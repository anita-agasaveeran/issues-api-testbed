# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran,
# who specified the requirements and design and reviewed the implementation.
# See the "Credits and authorship" section of README.md.
"""Capture the Swagger UI walkthrough screenshots used in the submission document.

Drives the running service's interactive docs with Playwright and writes one PNG
per interaction. The service must already be running and configured with real
GitHub credentials, because the walkthrough creates a genuine issue.

    pip install playwright && playwright install chromium
    docker restart issues-api          # cold ETag cache, so the first list is a MISS
    python scripts/capture_ui_screenshots.py docs/screenshots

Response figures capture the live response table only, so the body *and* the
response headers are both inside the frame — the headers carry Location, Link,
ETag and X-Cache, which several figures exist specifically to show.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import time

from playwright.sync_api import Locator, Page, sync_playwright

BASE = "http://127.0.0.1:8000"
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots")
OUT.mkdir(parents=True, exist_ok=True)

MAX_HEIGHT = 1500


def find_block(page: Page, method: str, path: str) -> Locator:
    blocks = page.locator(".opblock")
    for index in range(blocks.count()):
        block = blocks.nth(index)
        m = block.locator(".opblock-summary-method").inner_text().strip()
        p = block.locator(".opblock-summary-path").first.inner_text().strip()
        if m.upper() == method.upper() and p == path:
            return block
    raise LookupError(f"no operation for {method} {path}")


def open_block(page: Page, block: Locator) -> None:
    if "is-open" not in (block.get_attribute("class") or ""):
        block.locator(".opblock-summary").click()
        page.wait_for_timeout(400)


def try_it_out(page: Page, block: Locator) -> None:
    button = block.locator("button.try-out__btn")
    if button.count() and button.first.is_visible():
        button.first.click()
        page.wait_for_timeout(300)


def set_body(page: Page, block: Locator, payload: dict | None) -> None:
    area = block.locator("textarea.body-param__text")
    if area.count() and payload is not None:
        area.first.fill(json.dumps(payload, indent=2))
        page.wait_for_timeout(200)


def set_param(page: Page, block: Locator, name: str, value: str) -> None:
    field = block.locator(f"tr[data-param-name='{name}'] input").first
    if field.count():
        field.fill(value)
        page.wait_for_timeout(150)


def execute(page: Page, block: Locator, timeout_seconds: float = 20.0) -> str:
    """Click Execute and wait until the rendered response actually changes.

    Swagger UI re-renders the response table in place. Simply sleeping after the
    click can screenshot the *previous* response — which silently produced a
    mislabelled cache figure once, so the change is now waited for explicitly.
    """
    live = block.locator(".live-responses-table")
    before = live.inner_text() if live.count() else ""

    block.locator("button.execute").first.click()

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        page.wait_for_timeout(300)
        current = block.locator(".live-responses-table")
        if current.count():
            text = current.inner_text()
            if text and text != before:
                return text
    raise TimeoutError("response did not change after Execute")


def shot_block(block: Locator, name: str, max_height: int = MAX_HEIGHT) -> None:
    """Capture a whole operation, trimmed to keep the figure readable."""
    page = block.page
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(250)
    box = block.bounding_box()
    if box is None:
        block.screenshot(path=str(OUT / name))
    else:
        page.screenshot(
            path=str(OUT / name),
            full_page=True,
            clip={
                "x": box["x"],
                "y": box["y"],
                "width": box["width"],
                "height": min(box["height"], max_height),
            },
        )
    print("  captured", name)


def shot_response(block: Locator, name: str) -> None:
    """Capture just the live response: status, body, and headers together."""
    live = block.locator(".live-responses-table")
    live.scroll_into_view_if_needed()
    block.page.wait_for_timeout(400)
    live.screenshot(path=str(OUT / name))
    print("  captured", name)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=2)
        page.goto(f"{BASE}/docs", wait_until="networkidle")
        page.wait_for_selector(".opblock", timeout=15000)
        page.wait_for_timeout(800)

        print("overview")
        page.screenshot(path=str(OUT / "01-overview.png"), full_page=True)

        # ---- create -------------------------------------------------------
        print("POST /issues")
        block = find_block(page, "POST", "/issues")
        open_block(page, block)
        try_it_out(page, block)
        set_body(
            page,
            block,
            {
                "title": "Checkout button is dead on Safari",
                "body": "Clicking Pay does nothing. Reproduced on Safari 17.4.",
                "labels": ["bug"],
            },
        )
        shot_block(block, "02-create-request.png")
        response = execute(page, block)
        shot_response(block, "03-create-response.png")

        match = re.search(r'"number"\s*:\s*(\d+)', response)
        number = match.group(1) if match else "1"
        print("  created issue #", number)

        # ---- read ---------------------------------------------------------
        print("GET /issues/{number}")
        block = find_block(page, "GET", "/issues/{number}")
        open_block(page, block)
        try_it_out(page, block)
        set_param(page, block, "number", number)
        execute(page, block)
        shot_response(block, "04-get-issue.png")

        # ---- update title and body -----------------------------------------
        print("PATCH /issues/{number} (rename and edit body)")
        patch = find_block(page, "PATCH", "/issues/{number}")
        open_block(page, patch)
        try_it_out(page, patch)
        set_param(page, patch, "number", number)
        set_body(
            page,
            patch,
            {
                "title": "Checkout button unresponsive on Safari 17",
                "body": "Updated: also reproduces in a clean profile with no extensions.",
            },
        )
        execute(page, patch)
        shot_response(patch, "05-patch-title-body.png")

        # ---- comment -------------------------------------------------------
        print("POST /issues/{number}/comments")
        block = find_block(page, "POST", "/issues/{number}/comments")
        open_block(page, block)
        try_it_out(page, block)
        set_param(page, block, "number", number)
        set_body(page, block, {"body": "Reproduced on Safari 17.4 with a clean profile."})
        execute(page, block)
        shot_response(block, "06-create-comment.png")

        # ---- list comments --------------------------------------------------
        print("GET /issues/{number}/comments")
        block = find_block(page, "GET", "/issues/{number}/comments")
        open_block(page, block)
        try_it_out(page, block)
        set_param(page, block, "number", number)
        execute(page, block)
        shot_response(block, "07-list-comments.png")

        # ---- pagination: per_page=1 forces a Link header ---------------------
        print("GET /issues?per_page=1 (Link header)")
        listing = find_block(page, "GET", "/issues")
        open_block(page, listing)
        try_it_out(page, listing)
        set_param(page, listing, "per_page", "1")
        execute(page, listing)
        shot_response(listing, "08-pagination-link.png")

        # ---- conditional GET: cold cache, then warm --------------------------
        print("GET /issues (cache MISS)")
        set_param(page, listing, "per_page", "30")
        execute(page, listing)
        shot_response(listing, "09-list-issues-miss.png")

        print("GET /issues (cache HIT)")
        execute(page, listing)
        shot_response(listing, "10-list-issues-hit.png")

        # ---- close, then reopen ---------------------------------------------
        print("PATCH /issues/{number} (close)")
        set_body(page, patch, {"state": "closed"})
        execute(page, patch)
        shot_response(patch, "11-patch-close.png")

        print("PATCH /issues/{number} (reopen)")
        set_body(page, patch, {"state": "open"})
        execute(page, patch)
        shot_response(patch, "12-patch-reopen.png")

        # ---- errors -----------------------------------------------------------
        print("POST /issues (missing title -> 400)")
        block = find_block(page, "POST", "/issues")
        open_block(page, block)
        set_body(page, block, {"body": "a body with no title"})
        execute(page, block)
        shot_response(block, "13-validation-error.png")

        print("GET /issues/99999999 (-> 404)")
        block = find_block(page, "GET", "/issues/{number}")
        open_block(page, block)
        set_param(page, block, "number", "99999999")
        execute(page, block)
        shot_response(block, "14-not-found.png")

        # ---- events -----------------------------------------------------------
        print("GET /events")
        block = find_block(page, "GET", "/events")
        open_block(page, block)
        try_it_out(page, block)
        execute(page, block)
        shot_response(block, "15-events.png")

        # ---- health and schemas ------------------------------------------------
        print("GET /healthz")
        block = find_block(page, "GET", "/healthz")
        open_block(page, block)
        try_it_out(page, block)
        execute(page, block)
        shot_response(block, "16-healthz.png")

        print("schemas")
        page.locator("section.models").scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        page.locator("section.models").screenshot(path=str(OUT / "17-schemas.png"))
        print("  captured 17-schemas.png")

        browser.close()

    print("\nissue number used:", number)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
