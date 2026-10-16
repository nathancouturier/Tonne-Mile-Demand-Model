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
* Effective supply factor: share of fleet time at sea. Dry bulk port calls observed by IMF PortWatch per bulk carrier (UNCTAD fleet count), multiplied by the UNCTAD median time in port per call, give port days per ship; the factor is the at-sea share relative to January 2019. Before PortWatch coverage the factor is one, and after the last published median port time (second half of 2023) that value is held; both are flagged. Speed is not observable from public data and stays in the residual.
* Residual: whatever the four forces do not account for. It is reported as a fifth component and never redistributed.

The attribution identity for two months (endpoints averaged over three months):

```
log(BDI_1 / BDI_0) = e [dlog tonnes + dlog haul] - e [dlog fleet + dlog supply factor] + residual
```

The elasticity e is estimated once by regressing the twelve-month change in the log index on the twelve-month change in the log of tonne-miles over effective fleet (Newey-West standard errors over twelve lags). Twelve-month changes remove the seasonal pattern and the slow trends that dominate the levels of both series. The regression intercept is a drift that the four forces do not explain; the decomposition leaves it in the residual. Robustness variants re-estimate e on monthly levels (with and without month dummies, reported for completeness because both series trend), on four-quarter changes of quarterly averages and on each half of the sample, and an unrestricted first-difference regression tests whether the four inputs share one elasticity. The components and the residual sum exactly to the observed change; the unit tests enforce this.

## Data sources

Every raw download is stored under `data/raw/<dataset>/` next to a `metadata.json` record with the source URL, retrieval timestamp, frequency, coverage, units, licence and known limitations.

| Series | Source | Notes |
| --- | --- | --- |
| Baltic Dry Index, monthly | Statistics and Research Aland (ASUB), PxWeb table SJ102, originator Baltic Exchange | Public statistical re-publication, 1985 = 1,000. The public table runs from 2011; the Baltic Exchange licenses the daily index commercially, so no free daily series is used. |
| Bilateral trade in iron ore (HS 2601), coal (2701) and lignite (2702), wheat (1001), barley (1003), maize (1005), sorghum (1007) and soybeans (1201) | UN Comtrade public preview API | Net weight in kilograms, monthly from 2010 (2012-12 for Indonesia), annual from 2000. Exporter-reported where timely (Australia, Indonesia, South Africa, United States, Canada, Ukraine, Argentina, Colombia); importer-reported for Russian and Mozambican origins (India, Japan). Chinese import records lag by more than a year and are used for cross-checks only. Substitution: some monthly records carry a value but no net weight (the United States before 2016). For those records tonnes are estimated as value divided by the reporter's annual unit value for the same partner and commodity from the annual Comtrade series, and flagged as a proxy; months in which most of a group's value has no usable weight are interpolated and flagged, or trimmed at the ends of the group's range. The affected groups and months are listed on the Methodology page of the site and in `data/processed/coverage.json`. |
| Brazilian exports by destination (HS 2601, 1201, 1005, 2304) | Brazil Comex Stat API (MDIC) | Monthly from 1997, kilograms and FOB value; primary source for Brazil and a cross-check against Comtrade. |
| Port activity and chokepoint transits | IMF PortWatch (ArcGIS feature layers) | AIS-based daily estimates from 2019: dry bulk tonnage loaded and discharged by port, transits through 28 chokepoints. |
| Bulk carrier fleet, ships built and scrapped, port time, seaborne trade | UNCTADstat bulk download (7z CSV) | Fleet as of 1 January in thousand dwt from 1980; median time in port by ship type from 2018; seaborne trade by cargo type from 2000. |
| Port coordinates | US NGA World Port Index (Pub. 150) | Public domain. |
| Commodity prices (context) | World Bank Commodity Markets Outlook, monthly | Pink Sheet workbook. |

Sea distances are computed with the `searoute` package on a maritime network graph for four routing variants per route (shortest, avoiding Suez, avoiding Panama, avoiding both). Iron ore routes and coal routes outside the Americas avoid the Panama Canal (Capesize tonnage cannot use it); coal from the United States, Colombia and Canada, largely carried on Panamax ships, takes the canal. Routes that normally transit the Suez Canal are lengthened towards the Cape of Good Hope in proportion to the fall in dry bulk transits through Bab el-Mandeb observed by PortWatch, from November 2023 onward; grain and Panamax coal routes that use Panama are treated the same way with Panama Canal transits during the draught restrictions of 2023 to September 2024. Outside those windows no diversion is applied, and routes that serve a port inside the Red Sea are never diverted. The distance matrix and the route geometries are stored under `data/processed/`.

Sources that were investigated and not used: Stooq and other quote sites (bot protection or terms of use), US Census trade API (requires a key), ABS merchandise trade (values only, no quantities), UNCTAD ship deliveries and demolitions by ship type (not in the public tables), Egyptian customs records for Russian wheat (values without weights in most years, and Russia itself stopped reporting to Comtrade in 2022, so the Russia to Egypt grain route is not covered).

<!-- results:start -->
## Results

Panel: Dec 2012 to Sep 2025, 154 months, 271 routes. Built from data retrieved on 2026-10-16.

* Elasticity of the index to tonne-miles over effective fleet: 2.36 (Newey-West standard error 0.79); estimated on twelve-month changes, it explains 7% of the variance of twelve-month changes in the log index (Durbin-Watson 0.53).
* Over rolling twelve-month windows the four measured forces have the same sign as the index move 66% of the time; the residual averages 0.39 log points against a typical move of 0.43, so about 91% of a typical move is not explained by measured demand and supply.
* Between the first and last twelve months of the panel, tonnes on the covered routes grew +28%, tonne-miles +30% (average haul +1.4%) and the bulk carrier fleet +48% to 1,057 million dwt.
* Latest window (Sep 2024 to Sep 2025): the index moved -4 percent; contributions in log points were volume +0.05, haul -0.00, fleet -0.07, port time -0.01, residual -0.01 (total -0.04).
* The three largest routes carry 48% of covered tonne-miles. Validation: 18 checks passed, 6 warnings, 1 failure.

| Episode | Window | Total | Volume | Haul | Fleet | Port time | Residual |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Slide to the 2016 trough | Dec 2013 to Feb 2016 | -1.34 | -0.20 | +0.07 | -0.18 | -0.00 | -1.04 |
| Recovery from the trough | Feb 2016 to Aug 2018 | +1.14 | +0.54 | -0.04 | -0.16 | -0.00 | +0.81 |
| 2019 Brumadinho dam collapse | Jan 2019 to Sep 2019 | +0.35 | +0.09 | +0.07 | -0.06 | +0.01 | +0.24 |
| Pandemic trough | Dec 2019 to May 2020 | -1.05 | -0.15 | +0.00 | -0.04 | -0.01 | -0.86 |
| 2021 boom | May 2020 to Oct 2021 | +1.95 | +0.14 | +0.05 | -0.12 | +0.01 | +1.88 |
| Normalisation after the boom | Oct 2021 to Feb 2023 | -1.34 | -0.17 | -0.09 | -0.10 | -0.01 | -0.98 |
| Red Sea and Panama Canal disruptions | Oct 2023 to Jun 2024 | +0.28 | +0.05 | -0.03 | -0.05 | +0.00 | +0.30 |
| Latest twelve months | Sep 2024 to Sep 2025 | -0.04 | +0.05 | -0.00 | -0.07 | -0.01 | -0.01 |

Values are log points (0.10 is roughly a ten percent move). Endpoints are three-month averages.
<!-- results:end -->

## Limitations

* Sailing speed, waiting time outside ports, ballast ratios and bunker prices are not observable from public data; their effect sits in the residual.
* The route set covers the major bulks between the main exporters and importers. Minor bulks and short-haul regional trades, which matter for Supramax and Handysize rates, are not in the tonne-mile series.
* Representative ports approximate real terminals; the error is a few percent of a voyage and constant over time, so it affects levels rather than changes.
* Customs months record declarations, not sailings. Three-month averaging at the endpoints and twelve-month windows reduce but do not remove this timing noise.
* Months a reporter did not publish, and months whose customs records carry values without weights, are filled by linear interpolation and flagged; where the reporter's annual records give a unit value, weightless months are estimated from value instead (flagged as a proxy). Some annual records report weights about a thousand times too small for their value (for example US coal and iron ore before 2006 and US grains in 2000 and 2001); a unit value check excludes them from the proxy and the validation report lists every case. The Methodology page and `coverage.json` list every affected group and month.
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
* `refresh.yml` runs every Tuesday (after the weekly PortWatch update) and on demand: it downloads the data (three parallel Comtrade shards because the public API enforces an hourly call quota), rebuilds the processed data, the site and the research note, commits them and publishes the site to the `gh-pages` branch. GitHub only evaluates schedules and manual dispatch for workflows on the default branch, so the weekly refresh starts once this branch is merged. On a feature branch, a push that changes `scripts/refresh_trigger.txt` downloads data, and a commit message containing `[build]` (or `[build only]` to skip the download) runs the build and publishes the site.

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
