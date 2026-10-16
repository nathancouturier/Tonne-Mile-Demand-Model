"""Third probe: Aland PxWeb path, Comtrade single-period format, UNCTAD port calls, chokepoint dates."""

from __future__ import annotations

import io
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import py7zr
import requests

OUT = Path(os.environ.get("PROBE_OUTDIR", ".probe"))
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "tonne-mile-demand-model-probe/0.3 (research; github actions)"})
results: list[dict] = []


def rec(name: str, **kw) -> None:
    kw["name"] = name
    results.append(kw)
    print(f"[{kw.get('status')}] {name} {kw.get('elapsed_s')}s {kw.get('note', '')}")


def get(name: str, url: str, params=None, save: bool = True, timeout: int = 120, **extra):
    t0 = time.time()
    try:
        r = S.get(url, params=params, timeout=timeout)
        fname = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:80] + ".bin"
        if save:
            (OUT / fname).write_bytes(r.content[:600_000])
        rec(name, status=r.status_code, elapsed_s=round(time.time() - t0, 2), url=r.url, size=len(r.content), **extra)
        return r
    except Exception as exc:
        rec(name, status=None, elapsed_s=round(time.time() - t0, 2), error=f"{type(exc).__name__}: {exc}")
        return None


# ------------------------------------------------------------- ASUB PxWeb proper path
PX = "https://pxweb.asub.ax/PXWeb/api/v1/en/Statistik"
r = get("asub_l1", f"{PX}/TUTRSJ")
try:
    items = r.json()
    print("TUTRSJ items:", items)
    for it in items:
        if it.get("type") == "l":
            sub = get(f"asub_l2_{it['id']}", f"{PX}/TUTRSJ/{quote(it['id'])}")
            try:
                subitems = sub.json()
                print(f"  {it['id']} items:", subitems)
                for s2 in subitems:
                    if s2.get("type") == "l":
                        sub3 = get(
                            f"asub_l3_{it['id']}_{s2['id'][:20]}", f"{PX}/TUTRSJ/{quote(it['id'])}/{quote(s2['id'])}"
                        )
                        try:
                            print(f"    {s2['id']} items:", sub3.json())
                        except Exception:
                            pass
            except Exception:
                pass
except Exception as exc:
    print("asub crawl failed", exc)

TBL = f"{PX}/TUTRSJ/SJ/{quote('Konjunkturstatistik för sjöfarten')}/SJ102.px"
meta = get("asub_sj102_meta", TBL)
if meta is not None and meta.status_code == 200:
    m = meta.json()
    print("SJ102 meta:", json.dumps(m)[:1500])
    q = {
        "query": [{"code": v["code"], "selection": {"filter": "all", "values": ["*"]}} for v in m["variables"]],
        "response": {"format": "json-stat2"},
    }
    t0 = time.time()
    r = S.post(TBL, json=q, timeout=60)
    (OUT / "asub_sj102_jsonstat2.bin").write_bytes(r.content[:600_000])
    rec("asub_sj102_jsonstat2", status=r.status_code, elapsed_s=round(time.time() - t0, 2), size=len(r.content))
    q["response"] = {"format": "csv"}
    r = S.post(TBL, json=q, timeout=60)
    (OUT / "asub_sj102_csv.bin").write_bytes(r.content[:600_000])
    rec("asub_sj102_csv", status=r.status_code, elapsed_s=0, size=len(r.content))
    q["response"] = {"format": "json"}
    r = S.post(TBL, json=q, timeout=60)
    (OUT / "asub_sj102_json.bin").write_bytes(r.content[:600_000])
    rec("asub_sj102_json", status=r.status_code, elapsed_s=0, size=len(r.content))

# ------------------------------------------------------------- Comtrade: single period, many codes and partners
CT = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"
time.sleep(4)
r = get(
    "ct_aus_x_202401_single",
    CT,
    params={
        "reporterCode": 36,
        "period": "202401",
        "cmdCode": "2601,2701,1001,1003,1005,1007,1201",
        "flowCode": "X",
        "partnerCode": "156,392,410,490,699,704,360,608,458,764,0",
        "partner2Code": 0,
        "customsCode": "C00",
        "motCode": 0,
    },
)
if r is not None and r.status_code == 200:
    j = r.json()
    print("AUS single-period count:", j.get("count"), "sample:", json.dumps(j["data"][:2])[:800])
time.sleep(4)
r = get(
    "ct_usa_x_202401_single_desc",
    CT,
    params={
        "reporterCode": 842,
        "period": "202401",
        "cmdCode": "1001,1005,1007,1201,2701",
        "flowCode": "X",
        "partnerCode": "156,392,410,490,818,608,170,704,566,360,528,699,0",
        "partner2Code": 0,
        "customsCode": "C00",
        "motCode": 0,
        "includeDesc": "true",
    },
)
if r is not None and r.status_code == 200:
    j = r.json()
    print("USA single-period count:", j.get("count"))
time.sleep(4)
# first available monthly period per core reporter
get(
    "ct_da_core_2010",
    "https://comtradeapi.un.org/public/v1/getDa/C/M/HS",
    params={
        "reporterCode": "36,76,360,710,842,124,804,32,170,699,392,410,528,818,156",
        "period": "201001,201006,201101,201201",
    },
)
time.sleep(4)
get(
    "ct_da_core_2000s_annual",
    "https://comtradeapi.un.org/public/v1/getDa/C/A/HS",
    params={
        "reporterCode": "36,76,360,710,842,124,804,32,170,699,392,410,528,818,156",
        "period": "2000,2005,2008,2009",
    },
)
time.sleep(4)
r = get(
    "ct_annual_single_chn_2008",
    "https://comtradeapi.un.org/public/v1/preview/C/A/HS",
    params={
        "reporterCode": 156,
        "period": "2008",
        "cmdCode": "2601,2701",
        "flowCode": "M",
        "partnerCode": "36,76,710,699,360,0",
        "partner2Code": 0,
        "customsCode": "C00",
        "motCode": 0,
    },
)
if r is not None and r.status_code == 200:
    print("annual CHN 2008 count:", r.json().get("count"))

# ------------------------------------------------------------- UNCTAD port calls full header and labels
r = get(
    "unctad_portcalls_full",
    "https://unctadstat-api.unctad.org/bulkdownload/US.PortCalls/US_PortCalls",
    save=False,
    timeout=180,
)
if r is not None and r.status_code == 200:
    with py7zr.SevenZipFile(io.BytesIO(r.content), mode="r") as z:
        data = z.readall()
    for n, bio in data.items():
        txt = bio.read().decode("utf-8-sig", "replace")
        lines = txt.splitlines()
        (OUT / "unctad_portcalls_header.txt").write_text(lines[0])
        world = [ln for ln in lines if ln.startswith(("20", "19")) and ',0000,"World"' in ln]
        (OUT / "unctad_portcalls_world.txt").write_text("\n".join(world))
        print("portcalls header:", lines[0][:3000])
        print("portcalls world rows:", len(world))
        print("\n".join(world[:30]))
# merchant fleet world bulk carriers rows
r = get(
    "unctad_fleet_full",
    "https://unctadstat-api.unctad.org/bulkdownload/US.MerchantFleet/US_MerchantFleet",
    save=False,
    timeout=180,
)
if r is not None and r.status_code == 200:
    with py7zr.SevenZipFile(io.BytesIO(r.content), mode="r") as z:
        data = z.readall()
    for n, bio in data.items():
        lines = bio.read().decode("utf-8-sig", "replace").splitlines()
        world = [ln for ln in lines if ',0000,"World",2,"Bulk carriers"' in ln]
        (OUT / "unctad_fleet_world_bulk.txt").write_text(lines[0] + "\n" + "\n".join(world))
        print("fleet world bulk rows:", len(world), world[:2], world[-2:])
# seaborne trade world
r = get(
    "unctad_seaborne_full",
    "https://unctadstat-api.unctad.org/bulkdownload/US.SeaborneTrade/US_SeaborneTrade",
    save=False,
    timeout=180,
)
if r is not None and r.status_code == 200:
    with py7zr.SevenZipFile(io.BytesIO(r.content), mode="r") as z:
        data = z.readall()
    for n, bio in data.items():
        lines = bio.read().decode("utf-8-sig", "replace").splitlines()
        world = [ln for ln in lines if ',0000,"World",' in ln]
        (OUT / "unctad_seaborne_world.txt").write_text(lines[0] + "\n" + "\n".join(world))
        print("seaborne world rows:", len(world))
# other candidate dataset codes
for ds in [
    "US.ShipBuildingType",
    "US.ShipBuildingByType",
    "US.ShipScrappingType",
    "US.FleetAge",
    "US.ShipAge",
    "US.TonnageByType",
    "US.PortCallsArrivals",
    "US.PortCalls_S",
    "US.MerchantFleetOwnership",
]:
    get(
        f"unctad_try_{ds}",
        f"https://unctadstat-api.unctad.org/bulkdownload/{ds}/{ds.replace('.', '_')}",
        save=False,
        timeout=60,
    )

# ------------------------------------------------------------- PortWatch chokepoint dates
PW = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
r = get(
    "pw_choke_minmax2",
    f"{PW}/Daily_Chokepoints_Data/FeatureServer/0/query",
    params={
        "where": "1=1",
        "outStatistics": json.dumps(
            [
                {"statisticType": "min", "onStatisticField": "date", "outStatisticFieldName": "dmin"},
                {"statisticType": "max", "onStatisticField": "date", "outStatisticFieldName": "dmax"},
                {"statisticType": "count", "onStatisticField": "portid", "outStatisticFieldName": "n"},
            ]
        ),
        "f": "json",
    },
)
if r is not None:
    print("choke minmax:", r.json().get("features"))
r = get(
    "pw_choke_by_port_count",
    f"{PW}/Daily_Chokepoints_Data/FeatureServer/0/query",
    params={
        "where": "1=1",
        "groupByFieldsForStatistics": "portid,portname",
        "outStatistics": json.dumps(
            [
                {"statisticType": "min", "onStatisticField": "date", "outStatisticFieldName": "dmin"},
                {"statisticType": "count", "onStatisticField": "portid", "outStatisticFieldName": "n"},
            ]
        ),
        "f": "json",
    },
)
if r is not None:
    print("choke by port:", json.dumps([f["attributes"] for f in r.json().get("features", [])])[:3000])

(OUT / "results3.json").write_text(json.dumps(results, indent=1, default=str))
print("done", len(results))
