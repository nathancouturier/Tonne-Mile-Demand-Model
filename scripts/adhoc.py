"""Ad hoc remote check: do UN Comtrade six-digit coal lines for the United States carry net weights?"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

OUT = Path(".adhoc")
OUT.mkdir(exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "tonne-mile-demand-model/0.1 (research)"})
BASE = "https://comtradeapi.un.org/public/v1/preview/C/{freq}/HS"
results = {}
queries = {
    "usa_coal_hs6_201401": (
        "M",
        {
            "reporterCode": 842,
            "period": "201401",
            "cmdCode": "270111,270112,270119,270120",
            "flowCode": "X",
            "partnerCode": "76,528,156,392,699,826,0",
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
        },
    ),
    "usa_coal_hs6_201001": (
        "M",
        {
            "reporterCode": 842,
            "period": "201001",
            "cmdCode": "270111,270112,270119,270120",
            "flowCode": "X",
            "partnerCode": "76,528,156,392,699,826,0",
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
        },
    ),
    "usa_coal_hs4_201401_classic": (
        "M",
        {
            "reporterCode": 842,
            "period": "201401",
            "cmdCode": "2701",
            "flowCode": "X",
            "partnerCode": "76,528,0",
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
            "breakdownMode": "classic",
            "includeDesc": "true",
        },
    ),
    "usa_coal_annual_2008": (
        "A",
        {
            "reporterCode": 842,
            "period": "2008",
            "cmdCode": "2701,270112,270119",
            "flowCode": "X",
            "partnerCode": "0,76,528,156",
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
            "includeDesc": "true",
        },
    ),
    "usa_coal_annual_2004": (
        "A",
        {
            "reporterCode": 842,
            "period": "2004",
            "cmdCode": "2701,270112,270119",
            "flowCode": "X",
            "partnerCode": "0,76,528,156",
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
            "includeDesc": "true",
        },
    ),
    "usa_grains_hs4_201401": (
        "M",
        {
            "reporterCode": 842,
            "period": "201401",
            "cmdCode": "1001,1005,1201",
            "flowCode": "X",
            "partnerCode": "156,392,0",
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
            "includeDesc": "true",
        },
    ),
    "aus_coal_annual_2004": (
        "A",
        {
            "reporterCode": 36,
            "period": "2004",
            "cmdCode": "2701,2601",
            "flowCode": "X",
            "partnerCode": "0,392,156",
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
        },
    ),
}
for name, (freq, params) in queries.items():
    time.sleep(4)
    r = S.get(BASE.format(freq=freq), params=params, timeout=120)
    try:
        data = r.json()
    except ValueError:
        data = {"text": r.text[:500]}
    results[name] = {
        "status": r.status_code,
        "count": data.get("count"),
        "rows": [
            {
                k: row.get(k)
                for k in (
                    "period",
                    "cmdCode",
                    "partnerCode",
                    "netWgt",
                    "isNetWgtEstimated",
                    "qty",
                    "qtyUnitAbbr",
                    "altQty",
                    "altQtyUnitAbbr",
                    "primaryValue",
                    "isAggregate",
                    "isReported",
                )
            }
            for row in data.get("data", [])[:40]
        ],
    }
    print(name, r.status_code, data.get("count"))
(OUT / "comtrade_hs6_check.json").write_text(json.dumps(results, indent=1))
