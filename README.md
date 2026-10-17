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

Market signals. The physical balance moves a few percent a year while the index moves by tens of percent, so most of a typical move is missing from the identity. Two public World Bank prices are added after the four physical forces as separately named components, with the elasticity e unchanged: the Brent crude price over the same window and the iron ore price (CFR China) over the window ending three months earlier. Their coefficients come from a regression without intercept of the physical residual on the two price changes over every rolling twelve-month window (Newey-West errors), and whatever is left stays in the residual, so the identity still adds up exactly. An expanding-window test refits every coefficient each month on data that ended at least twelve months earlier and compares the forecasts with the physical model and with a persistence benchmark (the index's own twelve-month change three months earlier). The signals are market correlates of the commodity and industrial cycle, not physical forces, and were chosen after testing several candidates on the same sample.

Extensions tested and not adopted. Two further additions are scored every run with the same protocol (`src/tmdm/model/extensions.py`, output `data/processed/model_extensions.json`): minor bulks of the exporters already covered added to tonne-miles (a subset of world minor-bulk trade), and the OECD composite leading indicators for China and the G20 as a third signal, and for China in place of iron ore, at leads of zero, three and six months. A variant would be flagged for review if it raised the out-of-sample R squared by more than 0.05, a margin meant to cover the choice among leads; the headline specification is fixed in the code.

Long history. An annual version of the decomposition runs from 1980 (`src/tmdm/transform/long_history.py`, `src/tmdm/model/long_history.py`, section "Since 1980" on the site). Tonnes come from the annual UN Comtrade records of the same exporters for every partner (SITC Revision 2 for the years before HS, HS as reported afterwards), plus Russian and Mozambican coal from Indian and Japanese import records; exports to land neighbours and to countries without a dry bulk port are left out. Reporters enter the record in different years and the classification changes around 1990, so each year's change in tonnes and tonne-miles is measured on the reporters present in both years, within one classification, and the changes are chained. Route distances use the named ports where the monthly model defines them and otherwise the three ports of each country with the most dry bulk calls in the IMF PortWatch ports table. The freight index is the annual average of the Baltic Dry Index from 2011 (the start of the public ASUB table); before 2011 it is a clearly labelled proxy: the Kilian index of dry cargo freight rates (Federal Reserve Bank of Dallas), which is deflated by US consumer prices and detrended, converted back to nominal terms with the US CPI and calibrated on the months where both series exist (log BDI = a + b t + Kilian / 100 + log CPI). The fleet is the UNCTAD calendar-year average; port time enters only from 2019. The elasticity is estimated on annual changes, the price signals are fitted on annual price changes (iron ore over the twelve months to September), and an expanding-window test predicts each year from the years before it.

## Data sources

Every raw download is stored under `data/raw/<dataset>/` next to a `metadata.json` record with the source URL, retrieval timestamp, frequency, coverage, units, licence and known limitations.

| Series | Source | Notes |
| --- | --- | --- |
| Baltic Dry Index, monthly | Statistics and Research Aland (ASUB), PxWeb table SJ102, originator Baltic Exchange | Public statistical re-publication, 1985 = 1,000. The public table runs from 2011; the Baltic Exchange licenses the daily index commercially, so no free daily series is used. |
| Bilateral trade in iron ore (HS 2601), coal (2701) and lignite (2702), wheat (1001), barley (1003), maize (1005), sorghum (1007) and soybeans (1201); minor bulks for the extension: sugar (1701), soybean meal (2304), cement and clinker (2523), manganese ore (2602), bauxite (2606), petroleum coke (2713), potash (3104), wood chips and pellets (4401), ferrous scrap (7204) | UN Comtrade public preview API | Net weight in kilograms, monthly from 2010 (2012-12 for Indonesia), annual from 2000. Exporter-reported where timely (Australia, Indonesia, South Africa, United States, Canada, Ukraine, Argentina, Colombia); importer-reported for Russian and Mozambican origins (India, Japan). Chinese import records lag by more than a year and are used for cross-checks only. Substitution: some monthly records carry a value but no net weight (the United States before 2016). For those records tonnes are estimated as value divided by the reporter's annual unit value for the same partner and commodity from the annual Comtrade series, and flagged as a proxy; months in which most of a group's value has no usable weight are interpolated and flagged, or trimmed at the ends of the group's range. The affected groups and months are listed on the Methodology page of the site and in `data/processed/coverage.json` (minor-bulk groups under `minor_bulks`). |
| Brazilian exports by destination (HS 2601, 1201, 1005; minor bulks 1701, 2304, 2602, 2606, 4401) | Brazil Comex Stat API (MDIC) | Monthly from 1997, kilograms and FOB value; primary source for Brazil and a cross-check against Comtrade. |
| Port activity and chokepoint transits | IMF PortWatch (ArcGIS feature layers) | AIS-based daily estimates from 2019: dry bulk tonnage loaded and discharged by port, transits through 28 chokepoints. The ports table (dry bulk vessel calls per port) gives the ports used for minor-bulk distances: the three ports of each country with the most dry bulk calls, weighted by calls (for the United States and Canada, the three on the coast facing the partner). |
| Bulk carrier fleet, ships built and scrapped, port time, seaborne trade | UNCTADstat bulk download (7z CSV) | Fleet as of 1 January in thousand dwt from 1980; median time in port by ship type from 2018; seaborne trade by cargo type from 2000. |
| Port coordinates | US NGA World Port Index (Pub. 150) | Public domain. |
| Commodity prices (market signals) | World Bank Commodity Markets Outlook (Pink Sheet), monthly | The workbook address is read from the World Bank commodity markets page on every run. Brent crude and iron ore CFR China are the two market signals; coal, grain and gas prices are kept for context and tests. |
| Dry cargo freight rates since 1968 (long history) | Federal Reserve Bank of Dallas, Kilian index of global real economic activity (workbook igrea.xlsx) | Substitution: no public Baltic Dry Index series before 2011 was found, so this index of single-voyage dry cargo freight rates (real, detrended) is used, converted back to nominal terms and calibrated on 2011 onward, as a labelled proxy for the index before 2011. |
| US consumer prices (long history) | OECD Data Explorer (SDMX, Prices, United States CPI) | Monthly from 1960; used only to convert the Kilian index back to nominal terms. |
| Annual trade since 1980 (long history) | UN Comtrade public preview API, annual, SITC Revision 2 (1980 to 1995) and HS as reported (from 1988) | Exports of iron ore, coal and grains by the covered exporters to every partner, and Indian and Japanese coal imports from Russia, the USSR and Mozambique. Stored gzip-compressed under `data/raw/un_comtrade_long/`. |
| Composite leading indicators | OECD Data Explorer (SDMX, dataset DF_CLI) | Monthly, amplitude adjusted, long-term average 100; China and G20 used in the extension tests only. |

Sea distances are computed with the `searoute` package on a maritime network graph for four routing variants per route (shortest, avoiding Suez, avoiding Panama, avoiding both). Iron ore routes and coal routes outside the Americas avoid the Panama Canal (Capesize tonnage cannot use it); coal from the United States, Colombia and Canada, largely carried on Panamax ships, takes the canal. Routes that normally transit the Suez Canal are lengthened towards the Cape of Good Hope in proportion to the fall in dry bulk transits through Bab el-Mandeb observed by PortWatch, from November 2023 onward; grain and Panamax coal routes that use Panama are treated the same way with Panama Canal transits during the draught restrictions of 2023 to September 2024. Outside those windows no diversion is applied, and routes that serve a port inside the Red Sea are never diverted. The distance matrix and the route geometries are stored under `data/processed/`.

Sources that were investigated and not used: Stooq and other quote sites (bot protection or terms of use), US Census trade API (requires a key), ABS merchandise trade (values only, no quantities), UNCTAD ship deliveries and demolitions by ship type (not in the public tables), Egyptian customs records for Russian wheat (values without weights in most years, and Russia itself stopped reporting to Comtrade in 2022, so the Russia to Egypt grain route is not covered).

<!-- results:start -->
## Results

Panel: Dec 2012 to Sep 2025, 154 months, 271 routes. Built from data retrieved on 2026-10-16.

* Elasticity of the index to tonne-miles over effective fleet: 2.36 (Newey-West standard error 0.79); estimated on twelve-month changes, it explains 7% of the variance of twelve-month changes in the log index (Durbin-Watson 0.53).
* Over rolling twelve-month windows the four physical forces leave about 91% of a typical move unexplained (mean residual 0.39 log points against a typical move of 0.43) and have the sign of the move 66% of the time.
* Adding two public market-price signals (Brent crude over the same window, coefficient 0.49; iron ore three months earlier, 0.83) cuts the unexplained share to 55% in sample and from 94% to 62% out of sample (69 months, every coefficient refitted on data ending twelve months earlier). They are market correlates of the commodity cycle, shown as separate bars, not physical forces.
* Further additions were scored with the same protocol (headline out-of-sample R squared 0.62). Adding minor bulks (sugar, soybean meal, cement, manganese and bauxite ores, petroleum coke, potash, wood, scrap) for the 9 exporters already covered (about 195 million tonnes a year, a subset of world minor-bulk trade), about 9% more tonne-miles, gives 0.62 (unexplained share 62% out of sample against 62%). The OECD leading indicators, China and the G20 as a third signal and China in place of iron ore, at leads of zero, three and six months (9 variants), change the out-of-sample R squared by -0.39 to +0.03; the best variant (China leading indicator added, 6 months earlier) gains +0.03. None clears the margin of 0.05 set to cover the choice of lead, so the headline keeps the major bulks and the two prices.
* Between the first and last twelve months of the panel, tonnes on the covered routes grew +28%, tonne-miles +30% (average haul +1.4%) and the bulk carrier fleet +48% to 1,057 million dwt.
* Latest window (Sep 2024 to Sep 2025): the index moved -4 percent; contributions in log points were volume +0.05, haul -0.00, fleet -0.07, port time -0.01, oil signal -0.07, iron ore signal -0.14, residual +0.20 (total -0.04).
* The three largest routes carry 48% of covered tonne-miles. Validation: 20 checks passed, 6 warnings, 0 failures.

| Episode | Window | Total | Volume | Haul | Fleet | Port time | Oil signal | Iron ore signal | Residual |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Slide to the 2016 trough | Dec 2013 to Feb 2016 | -1.34 | -0.20 | +0.07 | -0.18 | -0.00 | -0.58 | -0.77 | +0.32 |
| Recovery from the trough | Feb 2016 to Aug 2018 | +1.14 | +0.54 | -0.04 | -0.16 | -0.00 | +0.39 | +0.21 | +0.21 |
| 2019 Brumadinho dam collapse | Jan 2019 to Sep 2019 | +0.35 | +0.09 | +0.07 | -0.06 | +0.01 | +0.01 | +0.31 | -0.08 |
| Pandemic trough | Dec 2019 to May 2020 | -1.05 | -0.15 | +0.00 | -0.04 | -0.01 | -0.38 | -0.09 | -0.39 |
| 2021 boom | May 2020 to Oct 2021 | +1.95 | +0.14 | +0.05 | -0.12 | +0.01 | +0.48 | +0.69 | +0.71 |
| Normalisation after the boom | Oct 2021 to Feb 2023 | -1.34 | -0.17 | -0.09 | -0.10 | -0.01 | +0.04 | -0.67 | -0.36 |
| Red Sea and Panama Canal disruptions | Oct 2023 to Jun 2024 | +0.28 | +0.05 | -0.03 | -0.05 | +0.00 | -0.03 | +0.09 | +0.25 |
| Latest twelve months | Sep 2024 to Sep 2025 | -0.04 | +0.05 | -0.00 | -0.07 | -0.01 | -0.07 | -0.14 | +0.20 |

Values are log points (0.10 is roughly a ten percent move). Endpoints are three-month averages. The oil and iron ore columns are market signals, not physical forces.
<!-- results:end -->

## Limitations

* Sailing speed, waiting time outside ports, ballast ratios and bunker prices are not observable from public data; their effect sits in the residual.
* The route set covers the major bulks between the main exporters and importers. Minor bulks are downloaded and tested as an extension (see Results) but are not in the headline series; short-haul regional trades, which matter for Supramax and Handysize rates, are not covered.
* Representative ports approximate real terminals; the error is a few percent of a voyage and constant over time, so it affects levels rather than changes.
* Customs months record declarations, not sailings. Three-month averaging at the endpoints and twelve-month windows reduce but do not remove this timing noise.
* Months a reporter did not publish, and months whose customs records carry values without weights, are filled by linear interpolation and flagged; where the reporter's annual records give a unit value, weightless months are estimated from value instead (flagged as a proxy). Some annual records report weights about a thousand times too small for their value (for example US coal and iron ore before 2006 and US grains in 2000 and 2001); a unit value check excludes them from the proxy and the validation report lists every case. The Methodology page and `coverage.json` list every affected group and month.
* Before 2011 the long-history freight index is a proxy built from the Kilian index of dry cargo freight rates, not the Baltic Dry Index; its calibration rests on the years since 2011, and the freight rates and weights behind it differ from the index. Annual averages smooth moves within a year.
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
