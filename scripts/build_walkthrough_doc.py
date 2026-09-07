# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran,
# who specified the requirements and design and reviewed the implementation.
# See the "Credits and authorship" section of README.md.
"""Build the UI interaction walkthrough (docs/UI-Interaction-Walkthrough.docx).

Assembles the screenshots written by scripts/capture_ui_screenshots.py into the
submission document. Run the capture first, then:

    pip install python-docx
    python scripts/build_walkthrough_doc.py

The document is deliberately not committed; only the scripts that produce it are.
"""

from __future__ import annotations

import pathlib
import struct

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"
OUT = ROOT / "docs" / "UI-Interaction-Walkthrough.docx"

CONTENT_WIDTH_IN = 6.5
MAX_FIGURE_HEIGHT_IN = 7.1

MONO = "Consolas"
ACCENT = RGBColor(0x1F, 0x49, 0x7D)
MUTED = RGBColor(0x59, 0x59, 0x59)

_figure_number = 0


def png_size(path: pathlib.Path) -> tuple[int, int]:
    return struct.unpack(">II", path.read_bytes()[16:24])


def shade(paragraph, fill: str) -> None:
    ppr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill)
    ppr.append(shd)


def rule(paragraph) -> None:
    ppr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:color"), "BFBFBF")
    borders.append(bottom)
    ppr.append(borders)


def box(paragraph) -> None:
    ppr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "dashed")
        el.set(qn("w:sz"), "8")
        el.set(qn("w:color"), "A6A6A6")
        borders.append(el)
    ppr.append(borders)


def code_block(doc: Document, text: str, *, size: float = 8) -> None:
    lines = [line.rstrip() for line in text.strip("\n").splitlines()]
    for index, line in enumerate(lines):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(2 if index == 0 else 0)
        p.paragraph_format.space_after = Pt(6 if index == len(lines) - 1 else 0)
        p.paragraph_format.left_indent = Inches(0.12)
        run = p.add_run(line if line else " ")
        run.font.name = MONO
        run.font.size = Pt(size)
        shade(p, "F2F2F2")


def figure(doc: Document, name: str, caption: str) -> None:
    global _figure_number
    _figure_number += 1

    path = SHOTS / name
    width_px, height_px = png_size(path)
    width_in = CONTENT_WIDTH_IN
    height_in = width_in * height_px / width_px
    if height_in > MAX_FIGURE_HEIGHT_IN:
        height_in = MAX_FIGURE_HEIGHT_IN
        width_in = height_in * width_px / height_px

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(3)
    p.add_run().add_picture(str(path), width=Inches(width_in), height=Inches(height_in))

    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(12)
    run = cap.add_run(f"Figure {_figure_number} — {caption}")
    run.italic = True
    run.font.size = Pt(9)
    run.font.color.rgb = MUTED


def placeholder(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(10)
    run = p.add_run(text)
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0xA0, 0x30, 0x30)
    run.bold = True
    box(p)


def body(doc: Document, text: str) -> None:
    p = doc.add_paragraph(text)
    p.paragraph_format.space_after = Pt(8)


def bullets(doc: Document, items: list[str]) -> None:
    for item in items:
        p = doc.add_paragraph(item, style="List Bullet")
        p.paragraph_format.space_after = Pt(3)


def table(doc: Document, headers: list[str], rows: list[list[str]], widths: list[float]) -> None:
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False

    for cell, header, width in zip(t.rows[0].cells, headers, widths, strict=True):
        cell.width = Inches(width)
        para = cell.paragraphs[0]
        run = para.add_run(header)
        run.bold = True
        run.font.size = Pt(9)
        shade(para, "DCE6F1")

    for row in rows:
        cells = t.add_row().cells
        for cell, value, width in zip(cells, row, widths, strict=True):
            cell.width = Inches(width)
            run = cell.paragraphs[0].add_run(value)
            run.font.size = Pt(8.5)

    doc.add_paragraph().paragraph_format.space_after = Pt(6)


def main() -> None:
    doc = Document()

    for section in doc.sections:
        section.page_width = Inches(8.5)
        section.page_height = Inches(11)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)
        section.top_margin = Inches(0.9)
        section.bottom_margin = Inches(0.9)

    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)

    # ---------------------------------------------------------------- title
    title = doc.add_paragraph()
    title.paragraph_format.space_after = Pt(2)
    run = title.add_run("GitHub Issues API Service")
    run.bold = True
    run.font.size = Pt(22)
    run.font.color.rgb = ACCENT

    sub = doc.add_paragraph()
    sub.paragraph_format.space_after = Pt(10)
    run = sub.add_run("UI Interaction Walkthrough")
    run.font.size = Pt(14)
    run.font.color.rgb = MUTED
    rule(sub)

    meta = doc.add_table(rows=0, cols=2)
    meta.autofit = False
    for label, value in [
        ("Student", "Anita Agasaveeran (anita.agasaveeran@sjsu.edu)"),
        ("Course", "MSSE Fall 2026 — CMPE 272"),
        ("Repository", "github.com/anita-agasaveeran/issues-api-testbed"),
        ("Service", "FastAPI wrapper over the GitHub REST API for Issues"),
        ("Interface shown", "Swagger UI (OpenAPI), served at http://localhost:8000/docs"),
        ("Environment", "Docker container, image issues-api-testbed:latest"),
        ("Date captured", "September 7, 2026"),
    ]:
        cells = meta.add_row().cells
        cells[0].width = Inches(1.6)
        cells[1].width = Inches(4.9)
        r = cells[0].paragraphs[0].add_run(label)
        r.bold = True
        r.font.size = Pt(9.5)
        r2 = cells[1].paragraphs[0].add_run(value)
        r2.font.size = Pt(9.5)

    doc.add_paragraph()

    # ------------------------------------------------------------ section 1
    doc.add_heading("1. What this document shows", level=1)
    body(
        doc,
        "This service exposes its own HTTP API for creating, reading, updating and "
        "commenting on GitHub issues, and separately ingests signed webhook "
        "deliveries from the same repository. Every screenshot below is a live "
        "interaction against a running instance backed by the real GitHub API — no "
        "response is mocked or edited.",
    )
    body(
        doc,
        "The interface exercised is the Swagger UI generated from the service's "
        "OpenAPI description. It is a real client: pressing Execute issues an HTTP "
        "request to the service, which calls GitHub and returns the projected "
        "result. The issue created in Section 4 is a genuine issue in the target "
        "repository, and the webhook deliveries in Section 13 were sent by GitHub "
        "in response to these very interactions.",
    )
    bullets(
        doc,
        [
            "Sections 4–10 walk the complete issue lifecycle: create, read, edit, "
            "comment, list comments, paginate, close, and reopen.",
            "Section 9 demonstrates the conditional-GET optimisation (extra credit).",
            "Sections 11–12 cover error handling and webhook signature verification, "
            "including tampered signatures and unsupported events.",
            "Sections 13–16 show real GitHub deliveries, idempotency, and the test runs.",
            "Section 17 maps each required submission artifact to its file in the repository.",
        ],
    )

    # ------------------------------------------------------------ section 2
    doc.add_heading("2. Environment and health check", level=1)
    body(
        doc,
        "The service was started from the Docker image built by the project's "
        "Dockerfile, running as an unprivileged user with the GitHub token supplied "
        "through environment variables. The health endpoint confirms which "
        "repository the instance is bound to and never contacts GitHub, so it "
        "consumes no API quota.",
    )
    code_block(
        doc,
        "$ docker build -t issues-api-testbed .\n"
        "$ docker run -d --name issues-api -p 8000:8000 --env-file .env \\\n"
        "    -e DATABASE_URL=sqlite:////data/events.db -v issues-api-events:/data \\\n"
        "    issues-api-testbed\n"
        "\n"
        "$ curl -s http://localhost:8000/healthz\n"
        '{"status":"ok","version":"0.1.0","repo":"anita-agasaveeran/issues-api-testbed"}',
    )
    figure(doc, "16-healthz.png", "GET /healthz executed from the Swagger UI.")

    # ------------------------------------------------------------ section 3
    doc.add_heading("3. The API surface", level=1)
    body(
        doc,
        "The Swagger UI lists every operation the service publishes: six issue and "
        "comment routes, the webhook receiver, the delivery log, and the health "
        "check. Each operation documents its success response together with the "
        "error responses it can return.",
    )
    figure(doc, "01-overview.png", "All nine operations, grouped by tag.")

    doc.add_page_break()

    # ------------------------------------------------------------ section 4
    doc.add_heading("4. Creating an issue", level=1)
    body(
        doc,
        "A POST to /issues takes a title, an optional body, and optional labels. "
        "The title is required and must contain non-whitespace text; unknown fields "
        "are rejected rather than silently ignored.",
    )
    figure(doc, "02-create-request.png", "The request body before execution.")
    body(
        doc,
        "The service responds 201 Created. The response headers carry "
        "location: /issues/19, pointing at the new resource on this service rather "
        "than on GitHub, and x-request-id, which appears on every log line for this "
        "request. The body is the issue projected into this API's own shape: labels "
        "are flattened from GitHub's label objects to plain strings, and the author "
        "is reduced to a login.",
    )
    figure(
        doc,
        "03-create-response.png",
        "201 Created. Note location: /issues/19 in the response headers.",
    )

    # ------------------------------------------------------------ section 5
    doc.add_heading("5. Retrieving the issue", level=1)
    body(
        doc,
        "Fetching the issue by number returns the same projection. A number that "
        "belongs to a pull request returns 404 here, even though GitHub's own issues "
        "endpoint would return it — this service is about issues only.",
    )
    figure(doc, "04-get-issue.png", "GET /issues/19 returns the created issue.")

    doc.add_page_break()

    # ------------------------------------------------------------ section 6
    doc.add_heading("6. Updating the title and body", level=1)
    body(
        doc,
        "PATCH accepts any combination of title, body, and state, and sends only the "
        "supplied fields upstream. Here both the title and the body are edited in a "
        "single request; note that updated_at has advanced while created_at has not.",
    )
    figure(
        doc,
        "05-patch-title-body.png",
        "PATCH /issues/19 renaming the issue and rewriting its body.",
    )

    # ------------------------------------------------------------ section 7
    doc.add_heading("7. Commenting, and listing comments", level=1)
    body(
        doc,
        "Posting a comment returns 201 with the comment id, author, and permalink, "
        "and a Location header. The issue_number field is echoed from the request "
        "path so a client does not have to correlate it separately.",
    )
    figure(doc, "06-create-comment.png", "POST /issues/19/comments returns 201 Created.")
    body(
        doc,
        "Listing the issue's comments returns the comment just created, with the "
        "same pagination headers as the issue listing.",
    )
    figure(doc, "07-list-comments.png", "GET /issues/19/comments returns the new comment.")

    doc.add_page_break()

    # ------------------------------------------------------------ section 8
    doc.add_heading("8. Pagination and the Link header", level=1)
    body(
        doc,
        "Requesting a single result per page forces GitHub to paginate, which lets "
        "the Link header be seen. The upstream header points at api.github.com and "
        "is rewritten to point at this service's own route, carrying only page and "
        "per_page:",
    )
    code_block(doc, 'link: </issues?page=2&per_page=1>; rel="next"', size=9)
    body(
        doc,
        "X-Page and X-Per-Page report the page actually served, so a client can "
        "confirm what it received without re-parsing the Link header.",
    )
    figure(
        doc,
        "08-pagination-link.png",
        "GET /issues?per_page=1. The rewritten link header names /issues, not api.github.com.",
    )

    # ------------------------------------------------------------ section 9
    doc.add_heading("9. Conditional GET and the ETag cache", level=1)
    body(
        doc,
        "The service caches the ETag of each distinct query and replays it upstream "
        "on the next identical request. The first call below is a cache miss: the "
        "service fetches from GitHub and stores the ETag it returns.",
    )
    figure(doc, "09-list-issues-miss.png", "First call: x-cache: MISS, with the ETag from GitHub.")
    body(
        doc,
        "The second, identical call sends that ETag as If-None-Match. GitHub replies "
        "304 Not Modified, which does not count against the API rate limit, and the "
        "service serves the cached body as a normal 200. The caller sees an "
        "unchanged response; the request cost no quota. This is the conditional-GET "
        "extra credit.",
    )
    figure(doc, "10-list-issues-hit.png", "Second call: identical body and status, x-cache: HIT.")

    doc.add_page_break()

    # ----------------------------------------------------------- section 10
    doc.add_heading("10. Closing and reopening", level=1)
    body(
        doc,
        "Closing the issue sets state to closed and populates closed_at. Sending an "
        "empty object is rejected as a 400 rather than producing a no-op round trip "
        "to GitHub.",
    )
    figure(doc, "11-patch-close.png", "PATCH with state: closed. closed_at is now populated.")
    body(doc, "Reopening restores state to open and clears closed_at back to null.")
    figure(doc, "12-patch-reopen.png", "PATCH with state: open. closed_at returns to null.")

    doc.add_page_break()

    # ----------------------------------------------------------- section 11
    doc.add_heading("11. Validation and error handling", level=1)
    body(
        doc,
        "Submitting a body with no title fails locally, before any call to GitHub, "
        "so an invalid request consumes no API quota. Every failure in this service "
        "uses one error envelope, so a client parses a single shape.",
    )
    figure(
        doc,
        "13-validation-error.png",
        "400 Bad Request with a machine-readable code, the offending field, and the request id.",
    )
    body(doc, "A number that does not exist returns 404 in the same envelope.")
    figure(doc, "14-not-found.png", "GET /issues/99999999 returns 404 not_found.")
    body(
        doc,
        "Errors originating at GitHub are translated rather than passed through. "
        "Every upstream call funnels through a single method, so the mapping is "
        "written once:",
    )
    table(
        doc,
        ["GitHub returns", "This service returns", "Why"],
        [
            ["401", "401 unauthorized", "Message names GITHUB_TOKEN and the permission needed"],
            [
                "403 + x-ratelimit-remaining: 0",
                "429 rate_limited",
                "Quota exhaustion is retryable, not an authorisation failure; "
                "Retry-After is derived from the reset time",
            ],
            [
                "403 + Retry-After",
                "429 rate_limited",
                "Secondary rate limit; the header wins over the reset epoch",
            ],
            ["403 otherwise", "403 forbidden", "A genuine permission problem"],
            ["404", "404 not_found", "Issue or repository not visible to the token"],
            ["410, 422", "400 validation_error", "GitHub's errors[] preserved in details"],
            [
                "5xx, timeout, connect error",
                "503 upstream_unavailable",
                "The request was fine; the dependency was not",
            ],
            ["anything else", "502 upstream_error", "Explicit fallback rather than a stray 500"],
        ],
        [1.5, 1.6, 3.4],
    )

    doc.add_page_break()

    # ----------------------------------------------------------- section 12
    doc.add_heading("12. Webhook signature verification", level=1)
    body(
        doc,
        "The webhook receiver verifies an HMAC-SHA256 signature computed over the "
        "raw request body, using a constant-time comparison. These headers are not "
        "declared parameters in the OpenAPI description, so the checks below were "
        "exercised from the command line against the same running container.",
    )
    code_block(
        doc,
        "1) Valid signature, known event -> 204 No Content\n"
        "   HTTP 204\n"
        "\n"
        "2) Same delivery id replayed (idempotent) -> 204, no new row\n"
        "   HTTP 204\n"
        "\n"
        "3) Tampered body, signature unchanged -> 401 Unauthorized\n"
        '   {"error":{"code":"unauthorized","message":"Missing or invalid webhook\n'
        '    signature.","details":{"reason":"signature_mismatch"},...}}\n'
        "\n"
        "4) Signature header absent -> 401 Unauthorized\n"
        '   {"error":{"code":"unauthorized","message":"Missing or invalid webhook\n'
        '    signature.","details":{"reason":"signature_header_absent"},...}}\n'
        "\n"
        "5) Valid signature, unsupported event -> 400 Bad Request\n"
        '   {"error":{"code":"validation_error","message":"Unsupported webhook event\n'
        '    \'push\'.","details":{"event":"push","supported":\n'
        '    ["issue_comment","issues","ping"]},...}}\n'
        "\n"
        "6) Ping event -> 204 No Content\n"
        "   HTTP 204\n"
        "\n"
        "7) Rows stored for delivery demo-sig-1 (sent twice):\n"
        "   1 row(s)",
    )
    body(
        doc,
        "Cases 3 and 4 return an identical message on purpose. A message that "
        "distinguished a missing header from a wrong digest would help an attacker "
        "iterate towards a valid forgery; the specific reason is available in "
        "structured logs, not in the response.",
    )

    # ----------------------------------------------------------- section 13
    doc.add_heading("13. Webhook deliveries received from GitHub", level=1)
    body(
        doc,
        "While these screenshots were being captured, the repository's webhook was "
        "live over a public Cloudflare tunnel. Creating the issue, editing it, "
        "commenting on it, closing it and reopening it each caused GitHub to send a "
        "signed delivery, which the service verified and recorded. The identifiers "
        "below are GitHub's own delivery ids, not values generated locally.",
    )
    figure(doc, "15-events.png", "GET /events showing deliveries triggered by the actions above.")
    body(
        doc,
        "Each row corresponds to an action taken earlier in this document. Raw "
        "payloads are retained in the local SQLite store but deliberately excluded "
        "from this response.",
    )

    doc.add_page_break()

    # ----------------------------------------------------------- section 14
    doc.add_heading("14. Webhook idempotency", level=1)
    body(
        doc,
        "GitHub retries deliveries and an operator can redeliver by hand, so the "
        "same event arrives more than once as a matter of course. The store keys on "
        "the delivery id and action together with INSERT OR IGNORE, so a repeat is a "
        "no-op that still acknowledges with 204. The project includes a script that "
        "proves this without depending on GitHub's retry behaviour.",
    )
    code_block(
        doc,
        "$ make replay\n"
        "service      : http://127.0.0.1:8000\n"
        "delivery id  : replay-a2c4ed81-63f5-4174-bff3-0f862ce887a4\n"
        "event/action : issues/opened\n"
        "\n"
        "  attempt 1: HTTP 204\n"
        "  attempt 2: HTTP 204\n"
        "  attempt 3: HTTP 204\n"
        "\n"
        "deliveries sent            : 3\n"
        "rows stored for that id    : 1\n"
        "\n"
        "PASS - repeated deliveries were deduplicated.",
    )
    body(
        doc,
        "The same behaviour was confirmed against a real GitHub redelivery. The "
        "repository's delivery log listed the original ping and its redelivery as "
        "two attempts, while the service's own /events endpoint held exactly one row "
        "for that delivery id — GitHub's list records attempts, which is not the "
        "same as this service storing duplicates.",
    )

    # ----------------------------------------------------------- section 15
    doc.add_heading("15. Reusable schemas", level=1)
    body(
        doc,
        "The contract defines reusable components rather than repeating shapes per "
        "route: Issue, Comment, EventRecord, and a shared Error envelope, plus the "
        "request models and enumerations.",
    )
    figure(doc, "17-schemas.png", "Schema components published by the service.")

    doc.add_page_break()

    # ----------------------------------------------------------- section 16
    doc.add_heading("16. Automated test results", level=1)
    body(
        doc,
        "The unit suite runs without credentials; the integration tests detect their "
        "absence and skip themselves, so the suite is green on a fresh clone and in "
        "continuous integration.",
    )
    code_block(
        doc,
        "$ make test\n"
        "202 passed, 14 deselected in 1.04s\n"
        "\n"
        "Name                      Stmts   Miss  Cover\n"
        "-------------------------------------------------\n"
        "app/cache.py                 43      0   100%\n"
        "app/config.py                39      0   100%\n"
        "app/github_client.py        135      6    96%\n"
        "app/pagination.py            51      1    98%\n"
        "app/routers/issues.py        86      0   100%\n"
        "app/routers/webhooks.py      42      0   100%\n"
        "app/schemas.py              124      5    96%\n"
        "app/store.py                 59      0   100%\n"
        "app/webhooks.py              47      0   100%\n"
        "-------------------------------------------------\n"
        "TOTAL                       789     22    97%\n"
        "\n"
        "$ make lint\n"
        "All checks passed!            (ruff)\n"
        "33 files already formatted    (ruff format)\n"
        "Success: no issues found in 15 source files   (mypy --strict)",
    )
    body(
        doc,
        "With credentials configured, the integration suite runs against the real "
        "GitHub API and creates genuine issues in the target repository:",
    )
    code_block(
        doc,
        "$ make test-integration\n"
        "test_create_then_fetch_the_issue                    PASSED\n"
        "test_update_title_and_body_then_close_and_reopen    PASSED\n"
        "test_create_a_comment_and_read_the_comment_list     PASSED\n"
        "test_labels_survive_the_round_trip                  PASSED\n"
        "test_filtering_by_label_returns_the_labelled_issue  PASSED\n"
        "test_listing_honours_state_and_pagination           PASSED\n"
        "test_conditional_get_returns_304_and_saves_quota    PASSED\n"
        "test_a_missing_issue_is_reported_as_404             PASSED\n"
        "test_validation_is_enforced_before_reaching_github  PASSED\n"
        "test_healthz_reports_the_configured_repository      PASSED\n"
        "=================== 10 passed in 19.59s ===================",
    )
    body(
        doc,
        "The webhook end-to-end suite runs separately, because it needs the service "
        "reachable from GitHub through a public tunnel. It creates a real issue, "
        "comments on it and closes it, then polls /events until the corresponding "
        "signed deliveries have been received and recorded:",
    )
    code_block(
        doc,
        "$ make test-webhook\n"
        "test_creating_an_issue_delivers_an_issues_event     PASSED\n"
        "test_commenting_delivers_an_issue_comment_event     PASSED\n"
        "test_closing_an_issue_delivers_a_closed_action      PASSED\n"
        "test_recorded_deliveries_have_unique_ids            PASSED\n"
        "=================== 4 passed in 13.81s ====================",
    )
    body(
        doc,
        "Coverage is 97% against an 80% gate. The suite includes negative cases "
        "throughout: tampered webhook signatures, invalid state filters, "
        "out-of-range pagination, empty PATCH bodies, and every GitHub error status "
        "mapped through a mocked transport, since a real rate limit cannot be "
        "provoked on demand.",
    )

    doc.add_page_break()

    # ----------------------------------------------------------- section 17
    doc.add_heading("17. Submission artifact checklist", level=1)
    body(
        doc,
        "This walkthrough shows the service running. The artifacts below are in the "
        "repository at github.com/anita-agasaveeran/issues-api-testbed and are what "
        "the assignment asks to be submitted.",
    )
    table(
        doc,
        ["Required artifact", "Location in the repository"],
        [
            ["OpenAPI 3.1 contract", "openapi.yaml (hand-authored, at the repository root)"],
            [
                "README with run instructions",
                "README.md — Docker and non-Docker, env var table, PAT scopes",
            ],
            ["API examples for each route", "README.md — curl and HTTPie for all nine routes"],
            ["Webhook setup and redelivery", "README.md — tunnel setup, and Redelivering an event"],
            ["Unit and integration tests", "tests/unit/ and tests/integration/"],
            ["Test fixtures", "tests/fixtures/ — captured GitHub issue, PR, comment, and webhooks"],
            ["Test runner script", "Makefile — make test, test-integration, test-webhook"],
            ["Design note", "DESIGN.md — error mapping, pagination, dedupe, security"],
            ["Dockerfile", "Dockerfile — two-stage build, non-root, healthcheck"],
            ["Compose with reverse proxy", "docker-compose.yaml and deploy/Caddyfile"],
            ["Dev container", ".devcontainer/devcontainer.json"],
            ["CI pipeline", ".github/workflows/ci.yml — lint, test, contract, image build"],
            ["Authorship comments", "Header in every source file; see README.md Credits"],
            ["Idempotency proof script", "scripts/replay_webhook.py (make replay)"],
            ["Screenshot capture script", "scripts/capture_ui_screenshots.py"],
        ],
        [2.3, 4.2],
    )
    body(
        doc,
        "The environment variables and the fine-grained token permissions the "
        "service requires — Issues: read and write, plus Metadata: read, scoped to "
        "this repository only — are documented in README.md.",
    )

    # ----------------------------------------------------------- section 18
    doc.add_heading("18. Optional GitHub-side screenshots", level=1)
    body(
        doc,
        "The assignment requires a document of UI interaction with screenshots, "
        "which Sections 2 through 15 provide. The additions below are optional "
        "supporting evidence from the GitHub web interface, which requires an "
        "authenticated browser session and so could not be captured "
        "programmatically. Include whichever are useful and delete the rest.",
    )
    placeholder(
        doc,
        "[ OPTIONAL ] Repository → Settings → Webhooks → Manage webhook → Settings "
        "tab. Show the Payload URL ending in /webhook, content type "
        "application/json, and the two selected events (Issues, Issue comments). "
        "Make sure the secret field stays masked.",
    )
    placeholder(
        doc,
        "[ OPTIONAL ] The Recent Deliveries tab, showing green checkmarks and a "
        "redelivery. This corroborates Section 14: GitHub lists two attempts while "
        "the service stores one row.",
    )
    placeholder(
        doc,
        "[ OPTIONAL ] The issue on github.com — issue #19 in issues-api-testbed — "
        "showing the edited title, the bug label, and the comment from Section 7.",
    )
    placeholder(
        doc,
        "[ OPTIONAL ] The Actions tab showing a green CI run (lint, unit tests, "
        "OpenAPI contract, Docker build).",
    )

    # ----------------------------------------------------------- section 19
    doc.add_heading("19. Authorship and AI assistance disclosure", level=1)
    body(doc, "Author of record: Anita Agasaveeran (anita.agasaveeran@sjsu.edu).")
    body(
        doc,
        "The implementation, test suite, OpenAPI contract, and supporting build and "
        "deployment files were written with Claude Code (Anthropic, Claude Opus 5) "
        "under my direction. I specified the requirements, chose the architecture "
        "and the step-by-step build order, reviewed the generated code, ran and "
        "validated the tests, performed the live GitHub and webhook verification, "
        "and am responsible for the submitted work. Every source file carries a "
        "header stating this, and the same disclosure appears in README.md and "
        "DESIGN.md.",
    )
    body(
        doc,
        "No project template, cookiecutter, or generated server stub was used; the "
        "repository was scaffolded from empty. The Mastodon.py client referenced in "
        "the assignment was not used and no code was copied from it. The GitHub REST "
        "API is called directly over HTTPS rather than through an SDK, so the "
        "client, its error mapping, and its pagination handling are project code. "
        "Third-party libraries — FastAPI, Pydantic, httpx, structlog, aiosqlite, "
        "pytest, respx, ruff, and mypy — are used as published, unmodified.",
    )
    body(
        doc,
        "The screenshots in this document were captured programmatically against "
        "the running service using Playwright; the script is in the repository at "
        "scripts/capture_ui_screenshots.py so the walkthrough can be reproduced.",
    )

    doc.save(str(OUT))
    print(f"wrote {OUT}")
    print(f"figures: {_figure_number}")
    print(f"size: {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
