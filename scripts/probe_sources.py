"""Second probe: table metadata, pacing and archive contents for the confirmed sources."""

from __future__ import annotations

import io
import json
import os
import re
import time
from pathlib import Path

import py7zr
import requests

OUT = Path(os.environ.get("PROBE_OUTDIR", ".probe"))
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "tonne-mile-demand-model-probe/0.2 (research; github actions)"})
results: list[dict] = []


def rec(name: str, **kw) -> None:
    kw["name"] = name
    results.append(kw)
    print(f"[{kw.get('status')}] {name} {kw.get('elapsed_s')}s {kw.get('note', '')}")


def get(
    name: str, url: str, params=None, save: bool = True, timeout: int = 60, **extra
) -> requests.Response | None:
    t0 = time.time()
    try:
        r = S.get(url, params=params, timeout=timeout)
        el = round(time.time() - t0, 2)
        fname = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:80] + ".bin"
        if save:
            (OUT / fname).write_bytes(r.content[:400_000])
        rec(
            name,
            status=r.status_code,
            elapsed_s=el,
            url=r.url,
            size=len(r.content),
            content_type=r.headers.get("Content-Type"),
            headers={
                k: v
                for k, v in r.headers.items()
                if k.lower() in ("retry-after", "x-ratelimit-limit", "x-ratelimit-remaining")
            },
            **extra,
        )
        return r
    except Exception as exc:  # noqa: BLE001
        rec(name, status=None, elapsed_s=round(time.time() - t0, 2), error=f"{type(exc).__name__}: {exc}")
        return None


# ------------------------------------------------------------- ASUB PxWeb (monthly BDI)
PX = "https://pxweb.asub.ax/PXWeb/api/v1/en/Statistik"
get("asub_statistik_root", PX)
get("asub_tutrsj", f"{PX}/Statistik__TUTRSJ")
get("asub_sj", f"{PX}/Statistik__TUTRSJ__SJ")
get("asub_konj", f"{PX}/Statistik__TUTRSJ__SJ__Konjunkturstatistik%20f%C3%B6r%20sj%C3%B6farten")
TBL = f"{PX}/Statistik__TUTRSJ__SJ__Konjunkturstatistik%20f%C3%B6r%20sj%C3%B6farten/SJ102.px"
meta = get("asub_sj102_meta", TBL)
if meta is not None and meta.status_code == 200:
    try:
        m = meta.json()
        q = {
            "query": [
                {"code": v["code"], "selection": {"filter": "all", "values": ["*"]}} for v in m["variables"]
            ],
            "response": {"format": "json-stat2"},
        }
        t0 = time.time()
        r = S.post(TBL, json=q, timeout=60)
        (OUT / "asub_sj102_data_jsonstat2.bin").write_bytes(r.content[:400_000])
        rec(
            "asub_sj102_data_jsonstat2",
            status=r.status_code,
            elapsed_s=round(time.time() - t0, 2),
            size=len(r.content),
        )
        q["response"] = {"format": "csv"}
        r = S.post(TBL, json=q, timeout=60)
        (OUT / "asub_sj102_data_csv.bin").write_bytes(r.content[:400_000])
        rec("asub_sj102_data_csv", status=r.status_code, elapsed_s=0, size=len(r.content))
    except Exception as exc:  # noqa: BLE001
        rec("asub_sj102_data", status=None, error=str(exc))
# also the sibling tables in the same folder may hold other freight indices
get(
    "asub_sj101_meta", f"{PX}/Statistik__TUTRSJ__SJ__Konjunkturstatistik%20f%C3%B6r%20sj%C3%B6farten/SJ101.px"
)
get(
    "asub_sj103_meta", f"{PX}/Statistik__TUTRSJ__SJ__Konjunkturstatistik%20f%C3%B6r%20sj%C3%B6farten/SJ103.px"
)

# ------------------------------------------------------------- Comtrade: paced calls
CT = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"


def ct(name: str, params: dict, pause: float = 4.0) -> None:
    time.sleep(pause)
    r = get(name, CT, params=params)
    if r is not None and r.status_code == 200:
        try:
            j = r.json()
            results[-1]["count"] = j.get("count")
            results[-1]["note"] = f"count={j.get('count')}"
        except Exception:  # noqa: BLE001
            pass


months_2024 = ",".join(f"2024{m:02d}" for m in range(1, 13))
ct(
    "ct_aus_exports_2601_2024",
    {
        "reporterCode": 36,
        "period": months_2024,
        "cmdCode": "2601",
        "flowCode": "X",
        "partnerCode": "156,392,410,490,699,0",
        "partner2Code": 0,
        "customsCode": "C00",
        "motCode": 0,
    },
)
ct(
    "ct_aus_exports_coal_grain_2024_h1",
    {
        "reporterCode": 36,
        "period": ",".join(f"2024{m:02d}" for m in range(1, 7)),
        "cmdCode": "2701,1001,1003,1005,1007,1201",
        "flowCode": "X",
        "partnerCode": "156,392,410,490,699,704,360,608,0",
        "partner2Code": 0,
        "customsCode": "C00",
        "motCode": 0,
    },
)
ct(
    "ct_idn_exports_2701_2024",
    {
        "reporterCode": 360,
        "period": months_2024,
        "cmdCode": "2701",
        "flowCode": "X",
        "partnerCode": "156,699,392,410,608,704,458,490,764,0",
        "partner2Code": 0,
        "customsCode": "C00",
        "motCode": 0,
    },
)
ct(
    "ct_usa_exports_grains_2024_h1",
    {
        "reporterCode": 842,
        "period": ",".join(f"2024{m:02d}" for m in range(1, 7)),
        "cmdCode": "1001,1005,1007,1201,2701",
        "flowCode": "X",
        "partnerCode": "156,392,410,490,818,608,170,704,566,360,528,699,484,0",
        "partner2Code": 0,
        "customsCode": "C00",
        "motCode": 0,
    },
)
ct(
    "ct_ind_imports_2701_2024",
    {
        "reporterCode": 699,
        "period": months_2024,
        "cmdCode": "2701",
        "flowCode": "M",
        "partnerCode": "360,36,710,842,643,508,0",
        "partner2Code": 0,
        "customsCode": "C00",
        "motCode": 0,
    },
)
ct(
    "ct_cap_test_usa_all_partners",
    {
        "reporterCode": 842,
        "period": "202401",
        "cmdCode": "1201,1005",
        "flowCode": "X",
        "partnerCode": None,
        "partner2Code": 0,
        "customsCode": "C00",
        "motCode": 0,
    },
)
ct(
    "ct_chn_imports_latest_2025",
    {
        "reporterCode": 156,
        "period": ",".join(f"2025{m:02d}" for m in range(1, 13)),
        "cmdCode": "2601",
        "flowCode": "M",
        "partnerCode": "36,76,0",
        "partner2Code": 0,
        "customsCode": "C00",
        "motCode": 0,
    },
)
ct(
    "ct_annual_chn_2000s",
    {
        "reporterCode": 156,
        "period": "2000,2001,2002,2003,2004,2005,2006,2007,2008,2009",
        "cmdCode": "2601,2701",
        "flowCode": "M",
        "partnerCode": "36,76,710,699,360,0",
        "partner2Code": 0,
        "customsCode": "C00",
        "motCode": 0,
    },
)
# annual endpoint has a different freq code
time.sleep(4)
get(
    "ct_annual_chn_2000s_A",
    "https://comtradeapi.un.org/public/v1/preview/C/A/HS",
    params={
        "reporterCode": 156,
        "period": "2000,2001,2002,2003,2004,2005,2006,2007,2008,2009",
        "cmdCode": "2601,2701",
        "flowCode": "M",
        "partnerCode": "36,76,710,699,360,0",
        "partner2Code": 0,
        "customsCode": "C00",
        "motCode": 0,
    },
)
time.sleep(4)
get(
    "ct_da_chn_all_monthly", "https://comtradeapi.un.org/public/v1/getDa/C/M/HS", params={"reporterCode": 156}
)
time.sleep(4)
get(
    "ct_da_key_reporters_202512",
    "https://comtradeapi.un.org/public/v1/getDa/C/M/HS",
    params={
        "reporterCode": "36,76,360,710,842,124,804,32,170,699,392,410,792,818,528",
        "period": "202512,202601,202602,202603,202604,202605,202606,202607,202608",
    },
)

# ------------------------------------------------------------- UNCTAD 7z contents
for ds in ["US.MerchantFleet", "US.ShipBuilding", "US.ShipScrapping", "US.PortCalls", "US.SeaborneTrade"]:
    r = get(
        f"unctad_full_{ds}",
        f"https://unctadstat-api.unctad.org/bulkdownload/{ds}/{ds.replace('.', '_')}",
        save=False,
        timeout=180,
    )
    if r is None or r.status_code != 200:
        continue
    try:
        with py7zr.SevenZipFile(io.BytesIO(r.content), mode="r") as z:
            names = z.getnames()
            data = z.readall()
        info = {"archive_bytes": len(r.content), "members": names}
        for n, bio in data.items():
            b = bio.read()
            info[n] = {"bytes": len(b)}
            head = b[:3000].decode("utf-8-sig", "replace")
            (OUT / f"unctad_{ds}_{re.sub(r'[^A-Za-z0-9_.-]+', '_', n)}_head.txt").write_text(head)
            lines = b.decode("utf-8-sig", "replace").splitlines()
            info[n]["lines"] = len(lines)
            # tail too
            (OUT / f"unctad_{ds}_{re.sub(r'[^A-Za-z0-9_.-]+', '_', n)}_tail.txt").write_text(
                "\n".join(lines[-15:])
            )
        (OUT / f"unctad_{ds}_info.json").write_text(json.dumps(info, indent=1))
        print("  ", ds, info)
    except Exception as exc:  # noqa: BLE001
        rec(f"unctad_extract_{ds}", status=None, error=str(exc))

# ------------------------------------------------------------- PortWatch timings
PW = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
stats = json.dumps(
    [
        {"statisticType": "sum", "onStatisticField": "export_dry_bulk", "outStatisticFieldName": "exp_db"},
        {"statisticType": "sum", "onStatisticField": "import_dry_bulk", "outStatisticFieldName": "imp_db"},
        {
            "statisticType": "sum",
            "onStatisticField": "portcalls_dry_bulk",
            "outStatisticFieldName": "calls_db",
        },
    ]
)
get(
    "pw_month_by_iso3",
    f"{PW}/Daily_Ports_Data/FeatureServer/0/query",
    params={
        "where": "date >= DATE '2024-01-01' AND date < DATE '2024-02-01'",
        "groupByFieldsForStatistics": "ISO3",
        "outStatistics": stats,
        "f": "json",
    },
    timeout=120,
)
get(
    "pw_daily_global_2024",
    f"{PW}/Daily_Ports_Data/FeatureServer/0/query",
    params={
        "where": "date >= DATE '2024-01-01' AND date < DATE '2025-01-01'",
        "groupByFieldsForStatistics": "date",
        "outStatistics": stats,
        "orderByFields": "date",
        "f": "json",
    },
    timeout=120,
)
get(
    "pw_year_by_iso3_2024",
    f"{PW}/Daily_Ports_Data/FeatureServer/0/query",
    params={
        "where": "year = 2024",
        "groupByFieldsForStatistics": "ISO3,month",
        "outStatistics": stats,
        "f": "json",
    },
    timeout=180,
)
get(
    "pw_choke_minmax",
    f"{PW}/Daily_Chokepoints_Data/FeatureServer/0/query",
    params={
        "where": "1=1",
        "outStatistics": json.dumps(
            [
                {"statisticType": "min", "onStatisticField": "date", "outStatisticFieldName": "dmin"},
                {"statisticType": "max", "onStatisticField": "date", "outStatisticFieldName": "dmax"},
            ]
        ),
        "f": "json",
    },
)
get(
    "pw_choke_db_all",
    f"{PW}/PortWatch_chokepoints_database/FeatureServer/0/query",
    params={"where": "1=1", "outFields": "portid,portname,lat,lon,vessel_count_dry_bulk", "f": "json"},
)
get(
    "pw_choke_page",
    f"{PW}/Daily_Chokepoints_Data/FeatureServer/0/query",
    params={
        "where": "1=1",
        "outFields": "date,portid,n_dry_bulk,capacity_dry_bulk",
        "orderByFields": "portid,date",
        "resultOffset": 0,
        "resultRecordCount": 1000,
        "f": "json",
    },
)

# ------------------------------------------------------------- Comex Stat countries and a soybean query
CX = "https://api-comexstat.mdic.gov.br"
get("comex_countries_full", f"{CX}/tables/countries", params={"language": "en"})
t0 = time.time()
r = S.post(
    f"{CX}/general",
    params={"language": "en"},
    timeout=180,
    json={
        "flow": "export",
        "monthDetail": True,
        "period": {"from": "2010-01", "to": "2010-12"},
        "filters": [{"filter": "heading", "values": [2601, 1201, 1005]}],
        "details": ["country", "heading"],
        "metrics": ["metricKG", "metricFOB"],
    },
)
(OUT / "comex_2010_three_headings.bin").write_bytes(r.content[:400_000])
rec(
    "comex_2010_three_headings",
    status=r.status_code,
    elapsed_s=round(time.time() - t0, 2),
    size=len(r.content),
)
t0 = time.time()
r = S.post(
    f"{CX}/general",
    params={"language": "en"},
    timeout=180,
    json={
        "flow": "export",
        "monthDetail": True,
        "period": {"from": "2026-01", "to": "2026-09"},
        "filters": [{"filter": "heading", "values": [2601]}, {"filter": "country", "values": [160]}],
        "details": ["country", "heading"],
        "metrics": ["metricKG"],
    },
)
(OUT / "comex_2026_ironore_china.bin").write_bytes(r.content[:400_000])
rec(
    "comex_2026_ironore_china",
    status=r.status_code,
    elapsed_s=round(time.time() - t0, 2),
    size=len(r.content),
)

# ------------------------------------------------------------- World Port Index full file and World Bank CMO
r = get(
    "wpi_full",
    "https://msi.nga.mil/api/publications/download",
    params={"key": "16920959/SFH00000/UpdatedPub150.csv", "type": "download"},
    save=False,
    timeout=180,
)
if r is not None and r.status_code == 200:
    txt = r.content.decode("utf-8-sig", "replace").splitlines()
    results[-1]["lines"] = len(txt)
    hits = [
        l
        for l in txt
        if re.search(
            r"(?i)Hedland|Tubar|Qingdao|Rotterdam|Richards Bay|Newcastle|Hay Point|Santos|New Orleans|Paradip|Taboneo|Samarinda|Novorossiysk|Odesa|Rosario|Caofeidian|Kashima|Pohang|Gwangyang|Dampier|Saldanha|Sept|Vancouver|Prince Rupert|Puerto Bolivar|Mundra|Visakhapatnam|Taichung|Kaohsiung|Dunkerque|Hamburg|Kwinana|Geelong|Tanjung Bara|Balikpapan|Sohar|Teluk Rubiah|Lumut",
            l,
        )
    ]
    (OUT / "wpi_selected_rows.txt").write_text(txt[0] + "\n" + "\n".join(hits[:200]))
get(
    "wb_cmo_monthly_head",
    "https://thedocs.worldbank.org/en/doc/5d903e848db1d1b83e0ec8f744e55570-0350012021/related/CMO-Historical-Data-Monthly.xlsx",
    save=False,
)

(OUT / "results2.json").write_text(json.dumps(results, indent=1, default=str))
print("done", len(results))
