# Tonne-Mile Demand Model

A decomposition of moves in the Baltic Dry Index into cargo volumes, average haul distance, fleet growth and effective supply effects, built only from public data and rebuilt automatically every week.

Live site: https://nathancouturier.github.io/Tonne-Mile-Demand-Model/

Research note (PDF): [docs/Tonne-Mile-Demand-Model-Research-Note.pdf](docs/Tonne-Mile-Demand-Model-Research-Note.pdf)

## The question

Dry bulk freight rates are set by the balance between demand for tonne-miles and the effective supply of ships. Over a given period, how much of the move in the Baltic Dry Index is explained by (a) cargo volumes, (b) average haul distance, (c) fleet growth and (d) effective supply effects such as port congestion, and how much is left unexplained?

## Method

Definitions used throughout:

* Tonne-miles: sum over routes of tonnes shipped multiplied by the sailing distance in nautical miles between representative loading and discharge ports.
* Average haul: tonne-miles divided by tonnes. By construction the change in log tonne-miles equals the change in log tonnes plus the change in log haul. The haul change is split further into trade mix (shares shifting at fixed distances) and rerouting (distances changing at fixed shares).
* Fleet: world bulk carrier capacity in deadweight tonnes as of 1 January (UNCTAD), interpolated log-linearly to months; months beyond the last observation are extrapolated at the last annual growth rate and flagged.
* Effective supply factor: share of fleet time at sea, derived from UNCTAD median time in port and port calls for dry bulk carriers, relative to its first observation. Speed is not observable from public data and stays in the residual.
* Residual: whatever the four forces do not account for. It is reported as a fifth component and never redistributed.

The attribution identity for two months (endpoints averaged over three months):

```
log(BDI_1 / BDI_0) = e [dlog tonnes + dlog haul] - e [dlog fleet + dlog supply factor] + residual
```

The elasticity e is estimated once by regressing the log index on the log of tonne-miles over effective fleet across the whole monthly panel (Newey-West standard errors). Robustness variants re-estimate it on twelve-month changes, quarterly averages and each half of the sample, and an unrestricted first-difference regression tests whether the four inputs share one elasticity. The components and the residual sum exactly to the observed change; the unit tests enforce this.

## Data sources

Every raw download is stored under `data/raw/<dataset>/` next to a `metadata.json` record with the source URL, retrieval timestamp, frequency, coverage, units, licence and known limitations.

| Series | Source | Notes |
| --- | --- | --- |
| Baltic Dry Index, monthly | Statistics and Research Aland (ASUB), PxWeb table SJ102, originator Baltic Exchange | Public statistical re-publication, 1985 = 1,000. The public table runs from 2011; the Baltic Exchange licenses the daily index commercially, so no free daily series is used. |
| Bilateral trade in iron ore (HS 2601), coal (2701), wheat (1001), barley (1003), maize (1005), sorghum (1007) and soybeans (1201) | UN Comtrade public preview API | Net weight in kilograms, monthly from 2010 (2012-12 for Indonesia), annual 2000 to 2009. Exporter-reported where timely (Australia, Indonesia, South Africa, United States, Canada, Ukraine, Argentina, Colombia); importer-reported for Russian and Mozambican origins (India, Japan, Egypt). Chinese import records lag by more than a year and are used for cross-checks only. |
| Brazilian exports by destination (HS 2601, 1201, 1005, 2304) | Brazil Comex Stat API (MDIC) | Monthly from 1997, kilograms and FOB value; primary source for Brazil and a cross-check against Comtrade. |
| Port activity and chokepoint transits | IMF PortWatch (ArcGIS feature layers) | AIS-based daily estimates from 2019: dry bulk tonnage loaded and discharged by port, transits through 28 chokepoints. |
| Bulk carrier fleet, ships built and scrapped, port time, seaborne trade | UNCTADstat bulk download (7z CSV) | Fleet as of 1 January in thousand dwt from 1980; median time in port by ship type from 2018; seaborne trade by cargo type from 2000. |
| Port coordinates | US NGA World Port Index (Pub. 150) | Public domain. |
| Commodity prices (context) | World Bank Commodity Markets Outlook, monthly | Pink Sheet workbook. |

Sea distances are computed with the `searoute` package on a maritime network graph for four routing variants per route (shortest, avoiding Suez, avoiding Panama, avoiding both). Iron ore and coal routes avoid the Panama Canal (Capesize tonnage cannot use it). Routes that normally transit the Suez Canal are lengthened towards the Cape of Good Hope in proportion to the fall in dry bulk transits through Bab el-Mandeb observed by PortWatch; grain routes that use Panama are treated the same way with Panama Canal transits. The distance matrix and the route geometries are stored under `data/processed/`.

Sources that were investigated and not used: Stooq and other quote sites (bot protection or terms of use), US Census trade API (requires a key), ABS merchandise trade (values only, no quantities), UNCTAD ship deliveries and demolitions by ship type (not in the public tables).

<!-- results:start -->
Results are written here by the pipeline (`tmdm report`) once the data has been downloaded.
<!-- results:end -->

## Limitations

* Sailing speed, waiting time outside ports, ballast ratios and bunker prices are not observable from public data; their effect sits in the residual.
* The route set covers the major bulks between the main exporters and importers. Minor bulks and short-haul regional trades, which matter for Supramax and Handysize rates, are not in the tonne-mile series.
* Representative ports approximate real terminals; the error is a few percent of a voyage and constant over time, so it affects levels rather than changes.
* Customs months record declarations, not sailings. Three-month averaging at the endpoints and twelve-month windows reduce but do not remove this timing noise.
* Months a reporter did not publish are filled by linear interpolation and flagged; the validation report lists them.
* The index composition changed in March 2018 (Handysize removed, weights 40/30/30 for Capesize, Panamax and Supramax). The series is used as published.
* The public monthly index is republished with a lag and updated less often than the demand data, so the balanced panel ends earlier than the volume series.

## How to rerun

Requirements: Python 3.11 or later and network access to the sources above (the GitHub Actions runners have it).

```
make setup          # create .venv and install the package with development tools
make all            # ingest, validate, transform, model, export, report
make test           # unit tests (distance routing, tonne-mile aggregation, decomposition adding-up)
make lint typecheck # ruff and mypy
make screenshots    # headless Chromium screenshots of the site at desktop and mobile widths
```

Stages can be run individually with `python -m tmdm.cli <stage>` (`ingest`, `validate`, `transform`, `model`, `export`, `report`). The ingest stage is incremental: raw files already present are kept and only recent months are refreshed. `TMDM_INGEST_ONLY` and `TMDM_INGEST_SKIP` restrict the sources; `TMDM_COMTRADE_SHARD=k/n` splits the UN Comtrade download across parallel jobs.

GitHub Actions:

* `ci.yml` runs lint, type checks and tests on every push.
* `refresh.yml` runs every Tuesday (after the weekly PortWatch update) and on demand: it downloads the data (three parallel Comtrade shards because the public API enforces an hourly call quota), rebuilds the processed data, the site and the research note, commits them and publishes the site to the `gh-pages` branch.

The random seed for any stochastic step is fixed in `src/tmdm/config.py`; the pipeline is deterministic given the raw data. Data versions are identified by the `retrieved_at` field of each metadata record and by the git history of `data/raw/`.

## Repository layout

```
src/tmdm/            package: ingest, validate, transform, model, export stages and the CLI
src/tmdm/reference.py commodities, partner codes, ports, loading and discharge blends, route groups
data/raw/            raw downloads with metadata records
data/processed/      every derived series (CSV and JSON)
site/                static site (HTML, CSS, vendored D3 and world atlas, generated JSON data)
docs/                research note PDF, figures, CV description, interview talking points
tests/               unit tests
.github/workflows/   CI, scheduled refresh and deployment
```

## Licence

Code is released under the MIT licence. Data files are redistributed under the terms of their publishers; see each `metadata.json`.
