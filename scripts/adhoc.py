"""Ad hoc remote check: which extra public sources answer from a GitHub runner, and what PortWatch publishes."""

from __future__ import annotations

import json
from pathlib import Path

import requests

OUT = Path(".adhoc")
OUT.mkdir(exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "tonne-mile-demand-model/0.1 (research)"})
res: dict[str, object] = {}


def probe(name: str, url: str, params: dict | None = None, n: int = 600) -> None:
    try:
        r = S.get(url, params=params, timeout=60)
        res[name] = {"status": r.status_code, "type": r.headers.get("content-type"), "head": r.text[:n]}
    except Exception as exc:  # noqa: BLE001
        res[name] = {"error": str(exc)[:300]}
    print(name, res[name].get("status", res[name].get("error")))


# IMF PortWatch: every service in the folder and the fields of each layer
base = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
try:
    services = S.get(base, params={"f": "json"}, timeout=60).json().get("services", [])
    res["portwatch_services"] = [s["name"] for s in services]
    fields = {}
    for s in services:
        url = f"{base}/{s['name']}/{s['type']}/0"
        try:
            j = S.get(url, params={"f": "json"}, timeout=60).json()
            fields[s["name"]] = [f["name"] for f in j.get("fields", [])][:60]
        except Exception as exc:  # noqa: BLE001
            fields[s["name"]] = [f"error {exc}"]
    res["portwatch_fields"] = fields
    print("portwatch services:", len(services))
except Exception as exc:  # noqa: BLE001
    res["portwatch_services"] = f"error {exc}"

probe("fred_brent", "https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": "MCOILBRENTEU"})
probe("fred_iron_ore", "https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": "PIORECRUSDM"})
probe(
    "oecd_cli",
    "https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI,4.1/.M.LI...AA...H?startPeriod=2024-01&format=csvfilewithlabels",
)
probe("worldsteel_press", "https://worldsteel.org/media/press-releases/")
probe("kiel_trade_indicator", "https://www.ifw-kiel.de/topics/international-trade/kiel-trade-indicator/")
probe("unctad_bulk_list", "https://unctadstat-api.unctad.org/bulkdownload")
probe("china_nbs", "https://data.stats.gov.cn/english/")
(OUT / "sources_probe.json").write_text(json.dumps(res, indent=1))
