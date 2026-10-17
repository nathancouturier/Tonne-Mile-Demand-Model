"""Ad hoc remote check (round 2): a long public history of dry bulk freight rates."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

OUT = Path(".adhoc")
FILES = OUT / "long_history_files"
FILES.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) tonne-mile-demand-model/0.1 research"})
res: dict[str, object] = {}


def raw(name: str, url: str, params: dict | None = None, n: int = 3000, timeout: int = 90) -> requests.Response | None:
    try:
        r = S.get(url, params=params, timeout=timeout)
        res[name] = {"url": r.url, "status": r.status_code, "type": r.headers.get("content-type"), "head": r.text[:n]}
        print(name, r.status_code)
        return r
    except Exception as exc:  # noqa: BLE001
        res[name] = {"url": url, "error": str(exc)[:300]}
        return None


def save(name: str, url: str, params: dict | None = None) -> None:
    try:
        r = S.get(url, params=params, timeout=120, allow_redirects=True)
        cd = r.headers.get("content-disposition", "")
        m = re.search(r'filename\*?="?([^";]+)"?', cd)
        fname = f"{name}__{m.group(1) if m else 'file'}".replace("/", "_")[:120]
        (FILES / fname).write_bytes(r.content)
        res[f"save_{name}"] = {
            "url": r.url,
            "status": r.status_code,
            "type": r.headers.get("content-type"),
            "bytes": len(r.content),
            "file": fname,
            "head": r.content[:200].decode("latin-1"),
        }
        print("saved", name, r.status_code, len(r.content))
    except Exception as exc:  # noqa: BLE001
        res[f"save_{name}"] = {"url": url, "error": str(exc)[:300]}


# 1. ASUB: print the raw folder listings, then walk lists only
api = "https://pxweb.asub.ax/PXWeb/api/v1/en/Statistik"
raw("asub_root", api)
raw("asub_tutrsj", f"{api}/TUTRSJ")
raw("asub_sj", f"{api}/TUTRSJ/SJ")
tables = []


def walk(path: str, depth: int) -> None:
    if depth > 4:
        return
    try:
        items = S.get(f"{api}/{path}", timeout=60).json()
    except Exception as exc:  # noqa: BLE001
        tables.append({"path": path, "error": str(exc)[:200]})
        return
    if not isinstance(items, list):
        tables.append({"path": path, "not_a_list": str(items)[:300]})
        return
    for it in items:
        if not isinstance(it, dict):
            continue
        sub = f"{path}/{quote(str(it.get('id')))}"
        if it.get("type") == "l":
            walk(sub, depth + 1)
        elif it.get("type") == "t":
            entry = {"path": sub, "text": it.get("text")}
            try:
                meta = S.get(f"{api}/{sub}", timeout=60).json()
                for v in meta.get("variables", []):
                    vals = v.get("valueTexts", [])
                    entry[v.get("code", "?")] = [len(vals), vals[:2], vals[-2:]]
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)[:200]
            tables.append(entry)
            time.sleep(0.3)


for start in ("TUTRSJ/SJ", "TUTRSJ"):
    walk(start, 0)
res["asub_tables"] = tables

# 2. Kilian index files: Dallas Fed workbook and the author's Drive files
save("dallasfed_igrea", "https://www.dallasfed.org/-/media/Documents/research/igrea/igrea.xlsx")
raw("dallasfed_related", "https://www.dallasfed.org/research/igrea/related", n=4000)
for fid in (
    "169Nj256wQ27xOnuVEllfj_jbb7JNGrlQ",
    "1PK6L5dR5vR8JUI2IbJD32odQByHlPzNo",
    "1PmgTOlRoJG_0v-1imv-DlBxtzjtaP5Lo",
    "1fysBGNNB3Vx3mnOI02hBgQ4qMAMm3e0Q",
    "1taCyNal2BJHqfxiid5-nTrkBaQCbRUmC",
):
    save(f"kilian_{fid[:8]}", "https://drive.google.com/uc", {"export": "download", "id": fid})
r = raw("kilian_page", "https://sites.google.com/site/lkilian2019/research/data-sets", n=200)
if r is not None and r.ok:
    text = re.sub(r"<[^>]+>", " ", r.text)
    text = re.sub(r"\s+", " ", text)
    i = text.find("ata")
    res["kilian_page_text"] = text[:6000]

# 3. Open data repositories: Zenodo and figshare
for q in ('"Baltic Dry Index"', "Baltic Dry Index monthly", "BDI freight index"):
    raw(f"zenodo_{q[:12]}", "https://zenodo.org/api/records", {"q": q, "size": 25}, n=20000)
try:
    r = S.post(
        "https://api.figshare.com/v2/articles/search",
        json={"search_for": '"Baltic Dry Index"', "page_size": 50},
        timeout=60,
    )
    res["figshare"] = {
        "status": r.status_code,
        "items": [
            {k: a.get(k) for k in ("title", "doi", "url_public_html", "defined_type_name")}
            for a in (r.json() if r.ok else [])
        ][:50],
    }
except Exception as exc:  # noqa: BLE001
    res["figshare"] = {"error": str(exc)[:300]}

(OUT / "long_history_probe2.json").write_text(json.dumps(res, indent=1))
print("done")
