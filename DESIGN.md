# Design note

> **Authorship.** Written with Claude Code (Anthropic) under the direction of
> Anita Agasaveeran and Ameya Mathew, who specified the design decisions recorded
> here. See the Credits section of README.md for the full disclosure.

Four decisions shaped this service: how upstream failures are translated, how
pagination is preserved, how webhook deliveries are made idempotent, and which
security trade-offs were accepted. Each is summarised below with the reasoning and
the cost.

## Error mapping

Every GitHub call funnels through a single method, `GitHubClient._request`, so the
translation from upstream status to this service's error model is written once
rather than repeated per route. Handlers contain no `try`/`except` at all.

| GitHub | This service | Reasoning |
| --- | --- | --- |
| `401` | `401 unauthorized` | Message names `GITHUB_TOKEN` and the permission required, so the fix is obvious |
| `403` + `x-ratelimit-remaining: 0` | **`429 rate_limited`** | Quota exhaustion is a retryable condition, not an authorisation failure |
| `403` + `Retry-After` | **`429 rate_limited`** | Secondary (abuse) rate limit; the header wins over the reset epoch |
| `429` | `429 rate_limited` | Passed through, defaulting to `Retry-After: 60` |
| `403` (otherwise) | `403 forbidden` | A genuine permission problem |
| `404` | `404 not_found` | |
| `410`, `422` | `400 validation_error` | GitHub's `errors[]` is preserved under `details.upstream_errors` |
| `5xx`, timeout, connection error | `503 upstream_unavailable` | The caller's request was fine; the dependency was not |
| anything else | `502 upstream_error` | An explicit fallback beats an accidental `500` |

The distinction that matters most is the first `403` row. GitHub returns `403` — not
`429` — when the hourly quota is gone, and also returns `403` for permission
failures. Collapsing the two would tell a caller to fix their token when they
actually need to wait, or the reverse. `_is_rate_limited` separates them on two
signals: `x-ratelimit-remaining == 0`, or the presence of `Retry-After`.

Every failure, including local validation, renders one envelope:

```json
{ "error": { "code", "message", "details", "request_id" } }
```

FastAPI's default `422` for request-validation errors is deliberately remapped to
`400`, because the published contract promises `400` for invalid payloads and a
client should not have to handle two shapes for one condition.

**Trade-off.** Mapping `5xx` to `503` hides whether GitHub returned `500` or `502`.
The upstream status is kept in `details.upstream_status` for debugging, but the
top-level code is deliberately coarse: callers should retry on `503` regardless.

## Pagination

`page` and `per_page` are validated at the edge (`per_page ≤ 100`, both `≥ 1`) and
forwarded to GitHub unchanged, so paging behaviour matches the upstream API exactly.

GitHub's `Link` header points at `api.github.com`, which is useless to a client of
this service and leaks the upstream shape. The header is therefore parsed and
rewritten to point at `/issues`, carrying only `page` and `per_page`:

```
</issues?page=2&per_page=30>; rel="next", </issues?page=5&per_page=30>; rel="last"
```

The parser handles the cases a naive `split(",")` gets wrong: commas inside URLs
(`?labels=bug,ops`), unquoted `rel` values, extra link parameters, and segments with
no `rel` at all. `X-Page` and `X-Per-Page` are added so a client can confirm what it
actually received without re-parsing.

Pull requests are filtered out of listings, and a PR number returns `404` from
`GET`/`PATCH`. GitHub treats pull requests as issues on these endpoints; this
service does not, and silently returning one would break that promise.

**Trade-off.** Rewriting `Link` means a client cannot follow the link opaquely to
GitHub — which is the point, but it does couple the header to this service's route
layout. Total counts are not synthesised, because GitHub does not provide them
without an extra request.

## Webhook idempotency

GitHub retries deliveries, and an operator can redeliver by hand, so the same event
arrives more than once as a matter of course. Idempotency lives in the schema rather
than in application logic:

```sql
PRIMARY KEY (delivery_id, action)
```

with `INSERT OR IGNORE`. A repeat is a no-op that still returns `204`. There is no
read-then-write, so two concurrent deliveries of the same event cannot both decide
they are first. `record()` returns whether a row was actually inserted, and that
value only affects a log field.

The delivery id alone would be sufficient — GitHub reuses it across redeliveries —
but the action is part of the key as a cheap guard against two logically distinct
events sharing an id, and because the requirement names both.

Deliveries are acknowledged before any slow work: the insert is a single indexed
write, and the summary log is deferred to a `BackgroundTasks` callback that runs
after the response is sent. GitHub times out slow endpoints and retries them, so a
handler that blocks would manufacture the duplicates the dedupe key then has to
absorb.

**Trade-off.** SQLite and an in-process background task suit a single-instance
service. Multiple replicas behind a load balancer would need shared storage —
Postgres with the same composite key — and durable work would need a real queue.
The `EventStore` interface is small enough that swapping the backend touches one
file.

## Security trade-offs

**Signature verification.** The HMAC is computed over the raw request body, read as
bytes before any JSON parsing, because re-serialising parsed JSON changes the bytes
and would break verification. Comparison uses `hmac.compare_digest`; a
short-circuiting `==` leaks, through timing, how many leading characters of a forged
signature were correct. A test greps the function for `compare_digest` so a refactor
to `==` fails CI rather than silently weakening the check.

**Uniform failure messages.** Every signature rejection returns the same message
regardless of cause — absent header, malformed prefix, wrong digest, tampered body.
A message that distinguished them would let an attacker iterate towards a valid
forgery. The specific reason is available in structured logs, not in the response.

**Check ordering.** The signature is verified before the event type is examined, so
an unauthenticated caller cannot enumerate which events the service accepts.

**Secrets.** `GITHUB_TOKEN` and `WEBHOOK_SECRET` are `SecretStr`, so an accidental
`repr` of the settings object prints `**********`. Neither the secret nor the
received signature is ever logged, and `.env` is excluded from both git and the
Docker build context. The token is documented as a fine-grained PAT limited to one
repository with `Issues: read & write` and `Metadata: read` — the minimum the routes
require.

**Accepted risks.** `GET /events` is unauthenticated. It exposes delivery ids,
event names, and issue numbers — not payload bodies, which stay in the local store —
and it is a development aid. In a deployment reachable from the internet it would
need authentication or removal. The service also trusts its own network position:
there is no rate limiting on inbound requests, so a public deployment would want
that at the proxy. Finally, a reverse proxy in front of `/webhook` must not modify
the request body; the bundled Caddyfile passes it through unchanged, and any
substitute must do the same or every signature check will fail.
