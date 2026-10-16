"""Ad hoc remote check: load the published GitHub Pages site in a headless browser and record what it serves."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import requests

OUT = Path(".adhoc")
OUT.mkdir(exist_ok=True)
URL = "https://nathancouturier.github.io/Tonne-Mile-Demand-Model/"
result: dict[str, object] = {"url": URL}
r = requests.get(URL, timeout=60)
result["status"] = r.status_code
result["bytes"] = len(r.content)
result["has_title"] = "Tonne-Mile Demand Model" in r.text
s = requests.get(URL + "data/summary.json", timeout=60)
result["summary_status"] = s.status_code
if s.status_code == 200:
    summary = s.json()
    result["built_at"] = summary.get("built_at")
    result["panel"] = summary.get("panel")
    result["latest"] = summary.get("latest")
print(json.dumps(result, indent=1))
(OUT / "live_site_check.json").write_text(json.dumps(result, indent=1))
if r.status_code != 200:
    sys.exit(0)
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "playwright"], check=True)
subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], check=True)
from playwright.sync_api import sync_playwright  # noqa: E402

errors: list[str] = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, width, height in (("desktop", 1366, 900), ("mobile", 390, 844)):
        page = browser.new_page(viewport={"width": width, "height": height})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(URL, wait_until="networkidle", timeout=120000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / f"live_{name}.png"), full_page=True)
        for view in ("overview", "decomposition", "routes", "fleet", "methodology", "downloads"):
            page.click(f"button[data-view={view}]")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(OUT / f"live_{name}_{view}.png"), full_page=True)
        page.close()
    browser.close()
result["console_errors"] = errors
(OUT / "live_site_check.json").write_text(json.dumps(result, indent=1))
print("console errors:", errors)
