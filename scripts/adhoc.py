"""Ad hoc remote check (round 3): ASUB shipping market tables (slow crawl) and the Kilian index definition."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

OUT = Path(".adhoc")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) tonne-mile-demand-model/0.1 research"})
res: dict[str, object] = {}
api = "https://pxweb.asub.ax/PXWeb/api/v1/en/Statistik"
folder = "TUTRSJ/SJ/" + quote("Konjunkturstatistik för sjöfarten")


def get_json(url: str) -> object:
    for attempt in range(6):
        r = S.get(url, timeout=60)
        if r.status_code == 429 or "Too many requests" in r.text[:200]:
            time.sleep(20 * (attempt + 1))
            continue
        return r.json()
    return {"error": "rate limited"}


time.sleep(5)
items = get_json(f"{api}/{folder}")
res["folder"] = items
tables = []
if isinstance(items, list):
    for it in items:
        time.sleep(6)
        meta = get_json(f"{api}/{folder}/{quote(str(it.get('id')))}")
        entry = {"id": it.get("id"), "text": it.get("text"), "updated": it.get("updated")}
        if isinstance(meta, dict):
            entry["title"] = meta.get("title")
            for v in meta.get("variables", []):
                vals = v.get("valueTexts", [])
                entry[v.get("code", "?")] = [len(vals), vals[:3], vals[-3:]]
        tables.append(entry)
        print(entry["id"], entry.get("text"))
res["tables"] = tables

# Kilian index definition (Dallas Fed page text)
try:
    r = S.get("https://www.dallasfed.org/research/igrea", timeout=60)
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    i = text.find("Index of global real economic activity")
    res["dallasfed_igrea_text"] = text[i : i + 6000]
except Exception as exc:  # noqa: BLE001
    res["dallasfed_igrea_text"] = f"error {exc}"

(OUT / "long_history_probe3.json").write_text(json.dumps(res, indent=1))
print("done")
