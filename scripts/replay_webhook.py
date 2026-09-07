#!/usr/bin/env python
# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
"""Replay one signed delivery N times to demonstrate idempotent processing.

GitHub's "Recent Deliveries" tab lists every *attempt* it makes, so a redelivery
always appears there as a second row. That says nothing about this service: the
question is whether the local store recorded the delivery once or twice.

This script answers it directly. It signs a payload with WEBHOOK_SECRET, posts it
repeatedly under a single delivery id, and reports how many rows `/events` holds
for that id. The secret is read from the environment or .env and never printed.

Usage:
    python scripts/replay_webhook.py
    python scripts/replay_webhook.py --times 5 --event issues --action closed
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import pathlib
import sys
import uuid

import httpx

ENV_FILE = pathlib.Path(__file__).resolve().parents[1] / ".env"


def load_secret() -> str:
    """Read WEBHOOK_SECRET from the environment, falling back to .env."""
    secret = os.environ.get("WEBHOOK_SECRET")
    if secret:
        return secret

    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "WEBHOOK_SECRET":
                return value.strip().strip("'\"")

    sys.exit("WEBHOOK_SECRET is not set, and no value was found in .env")


def build_payload(event: str, action: str, issue_number: int) -> dict[str, object]:
    if event == "ping":
        return {"zen": "Non-blocking is better than blocking.", "hook_id": 1}
    payload: dict[str, object] = {
        "action": action,
        "issue": {"number": issue_number, "title": "replayed by scripts/replay_webhook.py"},
        "sender": {"login": "replay-script"},
    }
    if event == "issue_comment":
        payload["comment"] = {"id": 1, "body": "replayed"}
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url", default=os.environ.get("SERVICE_BASE_URL", "http://127.0.0.1:8000")
    )
    parser.add_argument("--event", default="issues", choices=["issues", "issue_comment", "ping"])
    parser.add_argument("--action", default="opened")
    parser.add_argument("--issue-number", type=int, default=9999)
    parser.add_argument(
        "--times", type=int, default=3, help="How many identical deliveries to send"
    )
    parser.add_argument(
        "--delivery-id",
        default=None,
        help="Reuse a specific delivery id (default: a fresh one per run)",
    )
    args = parser.parse_args()

    secret = load_secret()
    delivery_id = args.delivery_id or f"replay-{uuid.uuid4()}"
    raw = json.dumps(build_payload(args.event, args.action, args.issue_number)).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

    base = args.url.rstrip("/")
    print(f"service      : {base}")
    print(f"delivery id  : {delivery_id}")
    print(f"event/action : {args.event}/{args.action}")
    print()

    with httpx.Client(base_url=base, timeout=10.0) as client:
        try:
            client.get("/healthz").raise_for_status()
        except httpx.HTTPError as exc:
            sys.exit(f"no service answering at {base}: {exc}")

        for attempt in range(1, args.times + 1):
            response = client.post(
                "/webhook",
                content=raw,
                headers={
                    "X-GitHub-Event": args.event,
                    "X-GitHub-Delivery": delivery_id,
                    "X-Hub-Signature-256": signature,
                    "Content-Type": "application/json",
                },
            )
            print(f"  attempt {attempt}: HTTP {response.status_code}")

        events = client.get("/events", params={"limit": 200}).json()

    matching = [record for record in events if record["id"] == delivery_id]
    print()
    print(f"deliveries sent            : {args.times}")
    print(f"rows stored for that id    : {len(matching)}")

    if len(matching) == 1:
        print("\nPASS - repeated deliveries were deduplicated.")
        return 0
    print(f"\nFAIL - expected exactly 1 stored row, found {len(matching)}.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
