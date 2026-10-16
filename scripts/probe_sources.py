"""Probe candidate public data sources from a GitHub Actions runner.

Each probe records status, content type, size, elapsed time and a text sample.
Results are written to PROBE_OUTDIR (default .probe) and committed by the workflow.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

OUT = Path(os.environ.get("PROBE_OUTDIR", ".probe"))
OUT.mkdir(parents=True, exist_ok=True)
SAMPLE_BYTES = 250_000
UA = "tonne-mile-demand-model-probe/0.1 (research; github actions)"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept": "*/*"})

results: list[dict] = []


def probe(name: str, url: str, method: str = "GET", json_body=None, headers=None,
          params=None, timeout: int = 90, save: bool = True) -> dict:
    rec = {"name": name, "url": url, "method": method, "params": params, "json_body": json_body}
    t0 = time.time()
    try:
        r = S.request(method, url, json=json_body, headers=headers, params=params,
                      timeout=timeout, stream=True)
        rec["status"] = r.status_code
        rec["final_url"] = r.url
        rec["content_type"] = r.headers.get("Content-Type")
        rec["content_length_header"] = r.headers.get("Content-Length")
        rec["headers"] = {k: v for k, v in r.headers.items()
                          if k.lower().startswith(("x-rate", "ratelimit", "retry", "content-disp"))}
        buf = b""
        for chunk in r.iter_content(65536):
            buf += chunk
            if len(buf) >= SAMPLE_BYTES:
                break
        r.close()
        rec["sample_bytes"] = len(buf)
        fname = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:80]
        if save:
            (OUT / f"{fname}.bin").write_bytes(buf)
            rec["file"] = f"{fname}.bin"
        try:
            text = buf.decode("utf-8", errors="replace")
        except Exception:
            text = ""
        rec["text_head"] = text[:1500]
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"
    rec["elapsed_s"] = round(time.time() - t0, 2)
    results.append(rec)
    print(f"[{rec.get('status', 'ERR')}] {name} ({rec['elapsed_s']}s) {rec.get('content_type')}")
    return rec


# ---------------------------------------------------------------- BDI sources
for sym in ["bdi", "^bdi", "bdiy", "bdi.b", "bdiy.us", "bci", "bpi", "bsi", "bhsi", "^bci"]:
    probe(f"stooq_{sym}_d", "https://stooq.com/q/d/l/", params={"s": sym, "i": "d"})
probe("stooq_bdi_m", "https://stooq.com/q/d/l/", params={"s": "bdi", "i": "m"})
probe("stooq_bdi_page", "https://stooq.com/q/d/", params={"s": "bdi"})
probe("stooq_search_baltic", "https://stooq.com/q/", params={"s": "baltic"})
probe("te_guest_bdiy", "https://api.tradingeconomics.com/markets/historical/bdiy:ind",
      params={"c": "guest:guest", "f": "json"})
probe("te_guest_bdiy_d1", "https://api.tradingeconomics.com/markets/historical/bdiy:ind",
      params={"c": "guest:guest", "f": "json", "d1": "2010-01-01"})
probe("yahoo_bdiy", "https://query1.finance.yahoo.com/v8/finance/chart/%5EBDIY",
      params={"range": "1mo", "interval": "1d"})
probe("asub_pxweb_root", "https://pxweb.asub.ax/PXWeb/api/v1/en/")
probe("asub_saved_query", "https://pxweb.asub.ax/PXWeb/sq/e7984fda-9577-4142-a153-354ebba72d0e")
probe("stockmarketdata_bdi", "https://stock-marketdata.com/bdi.html")
probe("marketsinsider_bdi", "https://markets.businessinsider.com/index/baltic_dry")
probe("balticexchange_dry", "https://www.balticexchange.com/en/data-services/market-information0/dry-services.html")
probe("mendeley_subindices", "https://data.mendeley.com/datasets/t76ckh2ygg")
probe("investing_bdi_page", "https://www.investing.com/indices/baltic-dry-historical-data")

# PxWeb crawl for the Baltic Dry table at Statistics and Research Aland (ASUB)
def pxweb_crawl(base: str, depth: int = 4) -> list[str]:
    found = []
    stack = [("", 0)]
    seen = set()
    while stack:
        path, d = stack.pop()
        if path in seen or d > depth:
            continue
        seen.add(path)
        try:
            r = S.get(base + path, timeout=60)
            if r.status_code != 200:
                continue
            items = r.json()
        except Exception:
            continue
        if isinstance(items, dict):
            continue
        for it in items:
            iid = it.get("id", "")
            text = it.get("text", "")
            typ = it.get("type", "")
            if re.search(r"baltic|frakt|freight|shipping|sj[oö]fart", text, re.I):
                found.append(f"{typ} {path}/{iid} :: {text}")
            if typ == "l" and d < depth:
                stack.append((f"{path}/{iid}", d + 1))
        time.sleep(0.2)
    return found

try:
    hits = pxweb_crawl("https://pxweb.asub.ax/PXWeb/api/v1/en")
    (OUT / "asub_pxweb_hits.txt").write_text("\n".join(hits))
    print("ASUB hits:", len(hits))
except Exception as exc:  # noqa: BLE001
    (OUT / "asub_pxweb_hits.txt").write_text(f"error {exc}")

# ---------------------------------------------------------------- IMF PortWatch
PW = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
probe("pw_services_list", PW, params={"f": "pjson"})
for layer in ["Daily_Ports_Data", "Daily_Chokepoints_Data", "PortWatch_ports_database",
              "PortWatch_chokepoints_database", "Daily_Trade_Data"]:
    probe(f"pw_{layer}_meta", f"{PW}/{layer}/FeatureServer/0", params={"f": "pjson"})
    probe(f"pw_{layer}_count", f"{PW}/{layer}/FeatureServer/0/query",
          params={"where": "1=1", "returnCountOnly": "true", "f": "json"})
    probe(f"pw_{layer}_sample", f"{PW}/{layer}/FeatureServer/0/query",
          params={"where": "1=1", "outFields": "*", "resultRecordCount": 3, "f": "json"})
probe("pw_ports_stats_month", f"{PW}/Daily_Ports_Data/FeatureServer/0/query",
      params={"where": "date >= DATE '2024-01-01' AND date < DATE '2024-02-01'",
              "outStatistics": json.dumps([
                  {"statisticType": "sum", "onStatisticField": "export_dry_bulk", "outStatisticFieldName": "exp_db"},
                  {"statisticType": "sum", "onStatisticField": "import_dry_bulk", "outStatisticFieldName": "imp_db"},
                  {"statisticType": "sum", "onStatisticField": "portcalls_dry_bulk", "outStatisticFieldName": "calls_db"},
                  {"statisticType": "count", "onStatisticField": "portid", "outStatisticFieldName": "n"}]),
              "f": "json"})
probe("pw_ports_stats_month_by_port", f"{PW}/Daily_Ports_Data/FeatureServer/0/query",
      params={"where": "date >= DATE '2024-01-01' AND date < DATE '2024-02-01'",
              "groupByFieldsForStatistics": "portid",
              "outStatistics": json.dumps([
                  {"statisticType": "sum", "onStatisticField": "export_dry_bulk", "outStatisticFieldName": "exp_db"},
                  {"statisticType": "sum", "onStatisticField": "import_dry_bulk", "outStatisticFieldName": "imp_db"}]),
              "resultRecordCount": 5, "f": "json"})
probe("pw_choke_sample_bab", f"{PW}/Daily_Chokepoints_Data/FeatureServer/0/query",
      params={"where": "portid='chokepoint2'", "outFields": "*", "resultRecordCount": 5,
              "orderByFields": "date DESC", "f": "json"})
probe("pw_ports_minmax_date", f"{PW}/Daily_Ports_Data/FeatureServer/0/query",
      params={"where": "1=1", "outStatistics": json.dumps([
          {"statisticType": "min", "onStatisticField": "date", "outStatisticFieldName": "dmin"},
          {"statisticType": "max", "onStatisticField": "date", "outStatisticFieldName": "dmax"}]), "f": "json"})
probe("pw_portwatch_home", "https://portwatch.imf.org/")
probe("pw_faq", "https://portwatch.imf.org/pages/faqs")

# ---------------------------------------------------------------- UN Comtrade (public preview)
CT = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"
probe("comtrade_cn_ironore_au_202401", CT, params={"reporterCode": 156, "period": "202401", "cmdCode": "2601",
                                                  "flowCode": "M", "partnerCode": 36, "includeDesc": "true"})
probe("comtrade_cn_ironore_multi", CT, params={"reporterCode": 156, "period": "202401,202402,202403,202404,202405,202406",
                                              "cmdCode": "2601,2701", "flowCode": "M", "partnerCode": "36,76,710,124,360,643",
                                              "partner2Code": 0, "customsCode": "C00", "motCode": 0})
probe("comtrade_cn_ironore_12m", CT, params={"reporterCode": 156, "period": ",".join(f"2023{m:02d}" for m in range(1, 13)),
                                            "cmdCode": "2601", "flowCode": "M", "partnerCode": "36,76,710,124,643,356,586,0",
                                            "partner2Code": 0, "customsCode": "C00", "motCode": 0})
probe("comtrade_br_ironore_exports_2023", CT, params={"reporterCode": 76, "period": ",".join(f"2023{m:02d}" for m in range(1, 13)),
                                                     "cmdCode": "2601", "flowCode": "X", "partnerCode": "156,392,410,528,0",
                                                     "partner2Code": 0, "customsCode": "C00", "motCode": 0})
probe("comtrade_count", CT, params={"reporterCode": 156, "period": "202401", "cmdCode": "2601", "flowCode": "M",
                                   "countOnly": "true"})
probe("comtrade_da_cn_2024", "https://comtradeapi.un.org/public/v1/getDa/C/M/HS",
      params={"reporterCode": 156, "period": ",".join(f"2024{m:02d}" for m in range(1, 13))})
probe("comtrade_da_all_202506", "https://comtradeapi.un.org/public/v1/getDa/C/M/HS", params={"period": "202506"})
probe("comtrade_annual_cn_2008", "https://comtradeapi.un.org/public/v1/preview/C/A/HS",
      params={"reporterCode": 156, "period": "2008,2009,2010", "cmdCode": "2601", "flowCode": "M",
              "partnerCode": "36,76,356,710,0", "partner2Code": 0, "customsCode": "C00", "motCode": 0})
probe("comtrade_ref_reporters", "https://comtradeapi.un.org/files/v1/app/reference/Reporters.json")
probe("comtrade_ref_partners", "https://comtradeapi.un.org/files/v1/app/reference/partnerAreas.json")
# rate limit test: 5 quick calls
for i in range(5):
    probe(f"comtrade_rate_{i}", CT, params={"reporterCode": 392, "period": f"20240{i+1}", "cmdCode": "2601",
                                            "flowCode": "M", "partnerCode": "36,76"}, save=False)

# ---------------------------------------------------------------- UNCTADstat
for ds in ["US.MerchantFleet", "US.ShipBuilding", "US.ShipScrapping", "US.SeaborneTrade", "US.FleetBeneficialOwners",
           "US.PortCalls", "US.LSCI"]:
    probe(f"unctad_bulk_{ds}", f"https://unctadstat-api.unctad.org/bulkdownload/{ds}/{ds.replace('.', '_')}")
probe("unctad_7zip_fleet", "https://unctadstat.unctad.org/7zip/US_MerchantFleet.csv.7z")
probe("unctad_7zip_shipbuilding", "https://unctadstat.unctad.org/7zip/US_ShipBuilding.csv.7z")
probe("unctad_7zip_scrapping", "https://unctadstat.unctad.org/7zip/US_ShipScrapping.csv.7z")
probe("unctad_7zip_seaborne", "https://unctadstat.unctad.org/7zip/US_SeaborneTrade.csv.7z")
probe("unctad_api_root", "https://unctadstat-api.unctad.org/")
probe("unctad_api_swagger", "https://unctadstat-api.unctad.org/swagger/index.html")
probe("unctad_dataviewer_fleet", "https://unctadstat.unctad.org/datacentre/dataviewer/US.MerchantFleet")
probe("unctad_datacentre", "https://unctadstat.unctad.org/datacentre/")

# ---------------------------------------------------------------- Brazil Comex Stat
CX = "https://api-comexstat.mdic.gov.br"
probe("comex_filters", f"{CX}/general/filters", params={"language": "en"})
probe("comex_details", f"{CX}/general/details", params={"language": "en"})
probe("comex_countries", f"{CX}/tables/countries", params={"language": "en"})
probe("comex_last_update", f"{CX}/general/dates/updated", params={"language": "en"})
probe("comex_post_ironore_cn", f"{CX}/general", method="POST", params={"language": "en"},
      json_body={"flow": "export", "monthDetail": True, "period": {"from": "2023-01", "to": "2023-12"},
                 "filters": [{"filter": "heading", "values": [2601]}, {"filter": "country", "values": [160]}],
                 "details": ["country", "heading"], "metrics": ["metricFOB", "metricKG"]})
probe("comex_post_ironore_all", f"{CX}/general", method="POST", params={"language": "en"},
      json_body={"flow": "export", "monthDetail": True, "period": {"from": "2023-01", "to": "2023-12"},
                 "filters": [{"filter": "heading", "values": [2601]}],
                 "details": ["country"], "metrics": ["metricKG"]})
probe("comex_bulk_head", "https://balanca.economia.gov.br/balanca/bd/comexstat-bd/ncm/EXP_2024.csv", method="HEAD")
probe("comex_tables_ncm_sh", "https://balanca.economia.gov.br/balanca/bd/tabelas/NCM_SH.csv")

# ---------------------------------------------------------------- Australia ABS
probe("abs_dataflows", "https://data.api.abs.gov.au/rest/dataflow/ABS", params={"detail": "allstubs"})
probe("abs_merch_exp_structure", "https://data.api.abs.gov.au/rest/dataflow/ABS/MERCH_EXP",
      params={"references": "all", "detail": "referencepartial"})
probe("abs_merch_exp_sample", "https://data.api.abs.gov.au/rest/data/ABS,MERCH_EXP,1.0.0/all",
      params={"startPeriod": "2024-01", "endPeriod": "2024-01", "format": "csv", "dimensionAtObservation": "AllDimensions"})

# ---------------------------------------------------------------- US Census trade API
probe("census_exports_hs_1201_cty", "https://api.census.gov/data/timeseries/intltrade/exports/hs",
      params={"get": "CTY_CODE,CTY_NAME,QTY_1_MO,UNIT_QY1,ALL_VAL_MO", "E_COMMODITY": "1201", "time": "2024-01"})
probe("census_exports_hs_2701_cty", "https://api.census.gov/data/timeseries/intltrade/exports/hs",
      params={"get": "CTY_CODE,CTY_NAME,QTY_1_MO,UNIT_QY1,ALL_VAL_MO", "E_COMMODITY": "2701", "time": "2024-01"})
probe("census_exports_hs_1201_series", "https://api.census.gov/data/timeseries/intltrade/exports/hs",
      params={"get": "CTY_CODE,CTY_NAME,QTY_1_MO,UNIT_QY1", "E_COMMODITY": "1201", "CTY_CODE": "5700",
              "time": "from 2013-01 to 2024-12"})
probe("census_variables", "https://api.census.gov/data/timeseries/intltrade/exports/hs/variables.json")

# ---------------------------------------------------------------- Ports and distances
probe("nga_wpi_csv", "https://msi.nga.mil/api/publications/download",
      params={"key": "16920959/SFH00000/UpdatedPub150.csv", "type": "download"})
probe("nga_wpi_page", "https://msi.nga.mil/Publications/WPI")

# ---------------------------------------------------------------- Other cross-checks
probe("usgs_ironore_mcs2025", "https://pubs.usgs.gov/periodicals/mcs2025/mcs2025-iron-ore.pdf", method="HEAD")
probe("usgs_ironore_mcs2026", "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-iron-ore.pdf", method="HEAD")
probe("wb_api_test", "https://api.worldbank.org/v2/country/CHN/indicator/NY.GDP.MKTP.CD", params={"format": "json", "per_page": 3})
probe("eia_coal_exports_t7", "https://www.eia.gov/coal/production/quarterly/xls/t7p01p1.xlsx", method="HEAD")
probe("iea_coal", "https://www.iea.org/data-and-statistics/data-product/coal-information", method="HEAD")
probe("unctad_rmt_2024", "https://unctad.org/system/files/official-document/rmt2024_en.pdf", method="HEAD")
probe("unctad_rmt_2025", "https://unctad.org/system/files/official-document/rmt2025_en.pdf", method="HEAD")

# ---------------------------------------------------------------- Write summary
(OUT / "results.json").write_text(json.dumps(results, indent=1, default=str))
lines = ["# Probe summary", ""]
for r in results:
    lines.append(f"## {r['name']}")
    lines.append(f"- url: {r['url']}")
    if r.get("params"):
        lines.append(f"- params: {json.dumps(r['params'])[:600]}")
    lines.append(f"- status: {r.get('status')} error: {r.get('error')}")
    lines.append(f"- content_type: {r.get('content_type')} length: {r.get('content_length_header')} sample: {r.get('sample_bytes')} elapsed: {r.get('elapsed_s')}s")
    if r.get("headers"):
        lines.append(f"- headers: {r['headers']}")
    head = (r.get("text_head") or "").replace("\r", "")
    lines.append("```")
    lines.append(head[:1200])
    lines.append("```")
    lines.append("")
(OUT / "summary.md").write_text("\n".join(lines))
print("done", len(results))
