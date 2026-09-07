# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran,
# who specified the requirements and design and reviewed the implementation.
# See the "Credits and authorship" section of README.md.
"""Capture the Swagger UI walkthrough screenshots used in the submission document.

Drives the running service's interactive docs with Playwright and writes one PNG
per interaction. The service must already be running and configured with real
GitHub credentials, because the walkthrough creates a genuine issue.

    pip install playwright && playwright install chromium
    python scripts/capture_ui_screenshots.py docs/screenshots

The cache MISS/HIT pair needs a cold cache for the first shot, so restart the
service before running this and the first list call will be a genuine miss.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

from playwright.sync_api import Locator, Page, sync_playwright

BASE = "http://127.0.0.1:8000"
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots")
OUT.mkdir(parents=True, exist_ok=True)


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
    classes = block.get_attribute("class") or ""
    if "is-open" not in classes:
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


def execute(page: Page, block: Locator) -> str:
    block.locator("button.execute").first.click()
    page.wait_for_timeout(2500)
    body = block.locator(".live-responses-table .response-col_description pre")
    return body.first.inner_text() if body.count() else ""


MAX_HEIGHT = 1500


def shot(block: Locator, name: str, max_height: int = MAX_HEIGHT) -> None:
    """Capture an operation block, trimmed so the static response catalogue below
    the live result does not turn every screenshot into an 8000px strip."""
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


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=2)
        page.goto(f"{BASE}/docs", wait_until="networkidle")
        page.wait_for_selector(".opblock", timeout=15000)
        page.wait_for_timeout(800)

        print("capturing overview")
        page.screenshot(path=str(OUT / "01-overview.png"), full_page=True)

        # ---- POST /issues -------------------------------------------------
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
        shot(block, "02-create-request.png")
        response = execute(page, block)
        shot(block, "03-create-response.png")

        match = re.search(r'"number"\s*:\s*(\d+)', response)
        number = match.group(1) if match else "1"
        print("  created issue #", number)

        # ---- GET /issues/{number} ----------------------------------------
        print("GET /issues/{number}")
        block = find_block(page, "GET", "/issues/{number}")
        open_block(page, block)
        try_it_out(page, block)
        set_param(page, block, "number", number)
        execute(page, block)
        shot(block, "04-get-issue.png")

        # ---- POST comment -------------------------------------------------
        print("POST /issues/{number}/comments")
        block = find_block(page, "POST", "/issues/{number}/comments")
        open_block(page, block)
        try_it_out(page, block)
        set_param(page, block, "number", number)
        set_body(page, block, {"body": "Reproduced on Safari 17.4 with a clean profile."})
        execute(page, block)
        shot(block, "05-create-comment.png")

        # ---- GET /issues, while the issue is still open -------------------
        # Listed before closing, so the screenshot shows real data rather than [].
        print("GET /issues (first call, cache MISS)")
        block = find_block(page, "GET", "/issues")
        open_block(page, block)
        try_it_out(page, block)
        execute(page, block)
        shot(block, "06-list-issues-miss.png")

        # A second identical call: the service replays its cached ETag, GitHub
        # answers 304, and the body is served from cache at no quota cost.
        print("GET /issues (second call, cache HIT)")
        execute(page, block)
        shot(block, "07-list-issues-hit.png")

        # ---- PATCH close --------------------------------------------------
        print("PATCH /issues/{number}")
        block = find_block(page, "PATCH", "/issues/{number}")
        open_block(page, block)
        try_it_out(page, block)
        set_param(page, block, "number", number)
        set_body(page, block, {"state": "closed"})
        execute(page, block)
        shot(block, "08-patch-close.png")

        # ---- validation error ---------------------------------------------
        print("POST /issues (invalid)")
        block = find_block(page, "POST", "/issues")
        open_block(page, block)
        set_body(page, block, {"body": "a body with no title"})
        execute(page, block)
        shot(block, "09-validation-error.png")

        # ---- events --------------------------------------------------------
        print("GET /events")
        block = find_block(page, "GET", "/events")
        open_block(page, block)
        try_it_out(page, block)
        execute(page, block)
        shot(block, "10-events.png")

        # ---- healthz -------------------------------------------------------
        print("GET /healthz")
        block = find_block(page, "GET", "/healthz")
        open_block(page, block)
        try_it_out(page, block)
        execute(page, block)
        shot(block, "11-healthz.png")

        # ---- schemas -------------------------------------------------------
        print("schemas")
        page.locator("section.models").scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        page.locator("section.models").screenshot(path=str(OUT / "12-schemas.png"))
        print("  captured 12-schemas.png")

        browser.close()

    print("\nissue number used:", number)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
