"""Render the built site with headless Chromium and save desktop and mobile screenshots.

Usage: python scripts/screenshots.py [site_dir] [out_dir]
The script serves the site directory over a local HTTP server so that fetch()
calls for the JSON data files behave as they do on GitHub Pages.
"""

from __future__ import annotations

import http.server
import socketserver
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

SITE = Path(sys.argv[1] if len(sys.argv) > 1 else "site").resolve()
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "screenshots").resolve()
OUT.mkdir(parents=True, exist_ok=True)
VIEWS = ["overview", "history", "decomposition", "routes", "fleet", "methodology", "downloads"]


class Quiet(http.server.SimpleHTTPRequestHandler):
    """Static file handler without request logging."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(SITE), **kwargs)  # type: ignore[arg-type]

    def log_message(self, *args: object) -> None:
        return


def main() -> None:
    """Serve the site and take screenshots of every view at two widths."""
    with socketserver.TCPServer(("127.0.0.1", 0), Quiet) as httpd:
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}/index.html"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
            for label, width, height in (("desktop", 1366, 900), ("mobile", 390, 844)):
                page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
                errors: list[str] = []
                page.on("pageerror", lambda e, errs=errors: errs.append(str(e)))
                page.on("console", lambda m, errs=errors: errs.append(m.text) if m.type == "error" else None)
                page.goto(base, wait_until="networkidle")
                page.wait_for_timeout(800)
                page.screenshot(path=str(OUT / f"{label}_headline.png"), full_page=True)
                for view in VIEWS:
                    page.goto(f"{base}#{view}", wait_until="networkidle")
                    page.wait_for_timeout(900)
                    page.screenshot(path=str(OUT / f"{label}_{view}.png"), full_page=True)
                if errors:
                    print(f"[{label}] console errors:")
                    for e in errors:
                        print("   ", e[:300])
                page.close()
            browser.close()
    print(f"screenshots written to {OUT}")


if __name__ == "__main__":
    main()
