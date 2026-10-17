"""Ad hoc remote check: public sources for a long (multi-decade) history of freight, trade and prices."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote, urljoin

import requests

OUT = Path(".adhoc")
OUT.mkdir(exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) tonne-mile-demand-model/0.1 research"})
res: dict[str, object] = {}


def probe(
    name: str, url: str, params: dict | None = None, n: int = 1500, timeout: int = 90
) -> requests.Response | None:
    try:
        r = S.get(url, params=params, timeout=timeout)
        res[name] = {"url": r.url, "status": r.status_code, "type": r.headers.get("content-type"), "head": r.text[:n]}
        print(name, r.status_code)
        return r
    except Exception as exc:  # noqa: BLE001
        res[name] = {"url": url, "error": str(exc)[:300]}
        print(name, "error", str(exc)[:120])
        return None


def links(html: str, base: str, pattern: str) -> list[str]:
    out = []
    for href in re.findall(r'href="([^"]+)"', html):
        if re.search(pattern, href, re.I):
            out.append(urljoin(base, href))
    return sorted(set(out))[:40]


# 1. ASUB: every table under tourism, transport and shipping, with the range of its time variable
def asub_tree(lang: str) -> list[dict]:
    api = f"https://pxweb.asub.ax/PXWeb/api/v1/{lang}/Statistik"
    found: list[dict] = []

    def walk(path: str, depth: int) -> None:
        if depth > 4:
            return
        try:
            items = S.get(f"{api}/{path}", timeout=60).json()
        except Exception as exc:  # noqa: BLE001
            found.append({"path": path, "error": str(exc)[:200]})
            return
        for it in items:
            sub = f"{path}/{quote(it['id'])}"
            if it.get("type") == "l":
                walk(sub, depth + 1)
            elif it.get("type") == "t":
                entry = {"path": sub, "text": it.get("text"), "updated": it.get("updated")}
                try:
                    meta = S.get(f"{api}/{sub}", timeout=60).json()
                    for v in meta.get("variables", []):
                        vals = v.get("valueTexts", [])
                        entry[v.get("code", "?")] = [len(vals), vals[:2], vals[-2:]]
                except Exception as exc:  # noqa: BLE001
                    entry["error"] = str(exc)[:200]
                found.append(entry)
                time.sleep(0.3)

    walk("TUTRSJ", 0)
    return found


for lang in ("en", "sv"):
    try:
        res[f"asub_tree_{lang}"] = asub_tree(lang)
        print("asub", lang, len(res[f"asub_tree_{lang}"]))
    except Exception as exc:  # noqa: BLE001
        res[f"asub_tree_{lang}"] = f"error {exc}"

# 2. Long dry cargo freight series: Kilian index (FRED, Dallas Fed, author page), data repositories
probe("fred_igrea", "https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": "IGREA"}, timeout=150)
probe("fred_igrea_txt", "https://fred.stlouisfed.org/data/IGREA.txt", timeout=150)
r = probe("dallasfed_igrea", "https://www.dallasfed.org/research/igrea", n=600)
if r is not None and r.ok:
    res["dallasfed_igrea_links"] = links(r.text, r.url, r"\.(xlsx|xls|csv|txt)(\?|$)|igrea")
    for i, u in enumerate([x for x in res["dallasfed_igrea_links"] if re.search(r"\.(xlsx|xls|csv|txt)", x, re.I)][:3]):
        probe(f"dallasfed_igrea_file_{i}", u, n=400)
r = probe("kilian_site", "https://sites.google.com/site/lkilian2019/research/data-sets", n=600)
if r is not None and r.ok:
    res["kilian_site_links"] = links(r.text, r.url, r"\.(xlsx|xls|csv|txt|dat|m)(\?|$)|drive\.google|docs\.google")
probe(
    "dataverse_bdi",
    "https://dataverse.harvard.edu/api/search",
    {"q": '"Baltic Dry Index"', "type": "file", "per_page": 50},
    n=12000,
)
probe(
    "dataverse_bdi_datasets",
    "https://dataverse.harvard.edu/api/search",
    {"q": '"Baltic Dry Index"', "type": "dataset", "per_page": 30},
    n=12000,
)
probe("zenodo_bdi", "https://zenodo.org/api/records", {"q": '"Baltic Dry Index"', "size": 30}, n=15000)
probe("stooq_bdi", "https://stooq.com/q/d/l/", {"s": "bdi", "i": "m"}, n=400)
probe("nasdaq_lloyds_bdi", "https://data.nasdaq.com/api/v3/datasets/LLOYDS/BDI.csv", {"rows": 5}, n=400)

# 3. UN Comtrade: annual availability by classification for the covered reporters, and sample calls
REPORTERS = {"AUS": 36, "BRA": 76, "IDN": 360, "ZAF": 710, "SACU": 711, "USA": 842, "CAN": 124, "UKR": 804,
             "ARG": 32, "COL": 170, "IND": 699, "JPN": 392, "CHN": 156, "RUS": 643}  # fmt: skip
for cls in ("HS", "S1", "S2", "S3"):
    try:
        time.sleep(2)
        r = S.get(
            f"https://comtradeapi.un.org/public/v1/getDa/C/A/{cls}",
            params={"reporterCode": ",".join(str(c) for c in REPORTERS.values())},
            timeout=120,
        )
        rows = r.json().get("data", []) if r.ok else []
        years: dict[str, list[str]] = {}
        for row in rows:
            years.setdefault(str(row.get("reporterCode")), []).append(str(row.get("period")))
        res[f"comtrade_avail_A_{cls}"] = {
            "status": r.status_code,
            "by_reporter": {k: [min(v), max(v), len(v)] for k, v in years.items()},
        }
        print("comtrade avail", cls, r.status_code, len(rows))
    except Exception as exc:  # noqa: BLE001
        res[f"comtrade_avail_A_{cls}"] = {"error": str(exc)[:300]}
for name, cls, rep, period, cmd in (
    ("s2_aus_1985_iron_ore", "S2", 36, 1985, "281"),
    ("s2_aus_1985_coal", "S2", 36, 1985, "322"),
    ("s2_usa_1985_grain", "S2", 842, 1985, "041,044,2222"),
    ("s1_aus_1975_iron_ore", "S1", 36, 1975, "281"),
    ("hs_aus_1990_iron_ore", "HS", 36, 1990, "2601"),
    ("hs_bra_1992_iron_ore", "HS", 76, 1992, "2601"),
):
    time.sleep(2)
    try:
        r = S.get(
            f"https://comtradeapi.un.org/public/v1/preview/C/A/{cls}",
            params={"reporterCode": rep, "period": period, "cmdCode": cmd, "flowCode": "X"},
            timeout=120,
        )
        data = r.json().get("data", []) if r.ok else []
        sample = [
            {
                k: d.get(k)
                for k in ("cmdCode", "partnerCode", "netWgt", "qty", "qtyUnitAbbr", "primaryValue", "isAggregate")
            }
            for d in data[:8]
        ]
        world = [d for d in data if d.get("partnerCode") == 0]
        res[f"comtrade_{name}"] = {"status": r.status_code, "n": len(data), "world": world[:3], "sample": sample}
        print("comtrade", name, r.status_code, len(data))
    except Exception as exc:  # noqa: BLE001
        res[f"comtrade_{name}"] = {"error": str(exc)[:300]}

# 4. US consumer prices (to undo the deflation of a real freight index): OECD SDMX and BLS
probe(
    "oecd_cpi_usa",
    "https://sdmx.oecd.org/public/rest/data/OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0/USA.M.N.CPI.IX._T.N._Z",
    {"startPeriod": "1965-01", "format": "csvfilewithlabels"},
    n=1200,
)
probe(
    "bls_cpi_v1",
    "https://api.bls.gov/publicAPI/v1/timeseries/data/CUUR0000SA0",
    {"startyear": "1985", "endyear": "1994"},
    n=800,
)

(OUT / "long_history_probe.json").write_text(json.dumps(res, indent=1))
print("done")
