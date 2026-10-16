"""Ad hoc remote check: lignite (HS 2702) volumes and Egyptian annual weights in UN Comtrade."""

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
    # lignite (HS 2702): how much do Indonesia, China and India report, and with weights?
    "idn_x_2702_2024": (
        "A",
        {
            "reporterCode": 360,
            "period": "2024",
            "cmdCode": "2701,2702",
            "flowCode": "X",
            "partnerCode": "0,156,699,392,608",
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
        },
    ),
    "idn_x_2702_2019": (
        "A",
        {
            "reporterCode": 360,
            "period": "2019",
            "cmdCode": "2701,2702",
            "flowCode": "X",
            "partnerCode": "0,156,699,392,608",
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
        },
    ),
    "chn_m_2702_2024": (
        "A",
        {
            "reporterCode": 156,
            "period": "2024",
            "cmdCode": "2701,2702",
            "flowCode": "M",
            "partnerCode": "0,360,36,643",
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
        },
    ),
    "ind_m_2702_2024": (
        "A",
        {
            "reporterCode": 699,
            "period": "2024",
            "cmdCode": "2701,2702",
            "flowCode": "M",
            "partnerCode": "0,360,36,643,508",
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
        },
    ),
    "idn_x_2702_m_202401": (
        "M",
        {
            "reporterCode": 360,
            "period": "202401",
            "cmdCode": "2701,2702",
            "flowCode": "X",
            "partnerCode": "0,156,699",
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
        },
    ),
    # Egypt annual: do annual import records carry weights?
    "egy_m_annual_2015": (
        "A",
        {
            "reporterCode": 818,
            "period": "2015",
            "cmdCode": "1001,1003,1005,1201",
            "flowCode": "M",
            "partnerCode": "0,643,804,826",
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
        },
    ),
    "egy_m_annual_2019": (
        "A",
        {
            "reporterCode": 818,
            "period": "2019",
            "cmdCode": "1001,1003,1005,1201",
            "flowCode": "M",
            "partnerCode": "0,643,804,826",
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
        },
    ),
    # Russia as a reporter: when do its exports to Egypt stop?
    "rus_x_1001_2019": (
        "A",
        {
            "reporterCode": 643,
            "period": "2019",
            "cmdCode": "1001",
            "flowCode": "X",
            "partnerCode": "0,818",
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
        },
    ),
    "rus_x_1001_2022": (
        "A",
        {
            "reporterCode": 643,
            "period": "2022",
            "cmdCode": "1001",
            "flowCode": "X",
            "partnerCode": "0,818",
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
(OUT / "comtrade_lignite_egypt_check.json").write_text(json.dumps(results, indent=1))
