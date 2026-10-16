/* Tonne-Mile Demand Model: page logic. Loads the JSON written by the export
   stage, renders the headline view, and reveals one section at a time. */
(function () {
  "use strict";
  const C = window.Charts;
  const fmt = C.fmt;
  const D = {}; // loaded data
  const state = { view: null, rendered: new Set() };

  const COMPONENTS = [
    { key: "volume", label: "Cargo volume", color: () => C.palette().s1 },
    { key: "distance", label: "Average haul", color: () => C.palette().s2 },
    { key: "fleet", label: "Fleet growth", color: () => C.palette().s3 },
    { key: "effective_supply", label: "Effective supply (port time)", color: () => C.palette().s4 },
    { key: "residual", label: "Residual (unexplained)", color: () => C.palette().neutral },
  ];
  const COMMODITY_LABEL = { iron_ore: "Iron ore", coal: "Coal", grains: "Grains and soybeans" };
  const COMMODITY_COLOR = { iron_ore: () => C.palette().s1, coal: () => C.palette().s2, grains: () => C.palette().s3 };
  // "2019-01","2019-02","2019-05" -> "Jan 2019 to Feb 2019; May 2019"
  const ranges = (months) => {
    if (!months || !months.length) return "";
    const next = (m) => { const [y, mo] = m.split("-").map(Number); return mo === 12 ? `${y + 1}-01` : `${y}-${String(mo + 1).padStart(2, "0")}`; };
    const out = [];
    let start = months[0], prev = months[0];
    for (let i = 1; i <= months.length; i++) {
      const m = months[i];
      if (m !== undefined && m === next(prev)) { prev = m; continue; }
      out.push(start === prev ? fmt.month(start) : `${fmt.month(start)} to ${fmt.month(prev)}`);
      if (m !== undefined) { start = m; prev = m; }
    }
    return out.join("; ");
  };

  const $ = (sel) => document.querySelector(sel);
  const text = (sel, t) => { const n = $(sel); if (n) n.textContent = t; };
  const pctChange = (a, b) => (b && a ? (a / b - 1) : null);
  const signed = (v, digits) => (v >= 0 ? "+" : "") + d3.format(`.${digits}f`)(v);
  const mt = (v) => fmt.f1(v) + " Mt";

  async function loadJSON(name) {
    const r = await fetch(`data/${name}.json`, { cache: "no-cache" });
    if (!r.ok) throw new Error(`failed to load ${name}: ${r.status}`);
    return r.json();
  }

  function retrievedLine(ids) {
    const src = D.summary.sources.filter((s) => ids.includes(s.dataset_id));
    return src.map((s) => `${s.publisher.split(" (")[0]}, retrieved ${s.retrieved_at.slice(0, 10)}`).join("; ");
  }
  const sourceLine = (label, ids) => `Source: ${label}. ${retrievedLine(ids)}.`;

  /* ---------------------------------------------------------------- headline */
  function renderTiles() {
    const s = D.summary, la = s.latest_attribution, lt = s.latest;
    const tiles = $("#tiles");
    tiles.innerHTML = "";
    const chg = pctChange(lt.bdi, lt.bdi_12m_ago);
    const explained = la.total ? 1 - la.residual / la.total : null;
    const items = [
      { label: `Baltic Dry Index, ${fmt.month(lt.month)}`, value: fmt.int(lt.bdi), delta: `three-month average ${signed(la.total_pct, 0)} percent on a year earlier`, dir: la.total_pct, note: chg === null ? null : `single month ${signed(chg * 100, 0)} percent on ${fmt.month(lt.month).slice(0, 3)} a year earlier` },
      { label: "Covered trades, tonnes shipped", value: mt(lt.tonnes_mt), note: `${s.routes.count} routes, latest month` },
      { label: "Tonne-miles on covered routes", value: d3.format(",.0f")(lt.tonne_miles_bn) + " bn", note: `average haul ${fmt.int(lt.avg_haul_nm)} nautical miles` },
      { label: "Bulk carrier fleet", value: d3.format(",.0f")(lt.fleet_mdwt) + " Mdwt", note: "UNCTAD, interpolated to month" },
      { label: `Explained by the four forces, 12 months to ${fmt.month(la.end)}`, value: explained === null ? "n/a" : fmt.pctPlain(explained), note: explained !== null && explained > 1 ? `the four forces implied a larger move than the ${signed(la.total_pct, 0)} percent that occurred` : explained !== null && explained < 0 ? `the four forces pointed the other way; the index moved ${signed(la.total_pct, 0)} percent` : `index moved ${signed(la.total_pct, 0)} percent (three-month averages); elasticity ${d3.format(".1f")(la.elasticity)}` },
    ];
    items.forEach((it) => {
      const t = C.el("div", "tile");
      t.append(C.el("p", "label", it.label), C.el("p", "value", it.value));
      if (it.delta) t.append(C.el("p", "delta " + (it.dir > 0 ? "up" : it.dir < 0 ? "down" : ""), it.delta));
      if (it.note) t.append(C.el("p", "note", it.note));
      tiles.append(t);
    });
  }

  function renderHeadline() {
    const se = D.series, c12 = se.change12;
    const p = C.palette();
    const xs = c12.month.map(C.monthToDate);
    const actual = xs.map((x, i) => ({ x, y: c12.actual_pct[i] }));
    const implied = xs.map((x, i) => ({ x, y: c12.implied_pct[i] }));
    const n = c12.month.length - 1;
    const first = c12.month[0], last = c12.month[n];
    const move = (v, rise, fall) => `${v >= 0 ? rise : fall} ${fmt.int(Math.abs(v))} percent`;
    C.lineChart("#chart-headline", {
      title: `Over the twelve months to ${fmt.month(last)} the four forces pointed to a ${move(c12.implied_pct[n], "rise of", "fall of")} in the index; it ${move(c12.actual_pct[n], "rose", "fell")}`,
      subtitle: `Twelve-month change in the Baltic Dry Index against the change attributed to cargo volume, haul, fleet and port time at the estimated elasticity, percent, ${fmt.month(first)} to ${fmt.month(last)}. Endpoints are three-month averages, as in the decomposition. The shaded gap is the residual.`,
      source: sourceLine("Baltic Dry Index from Statistics and Research Aland (originator Baltic Exchange); attribution from this project's decomposition", ["asub_baltic_dry_index", "un_comtrade", "brazil_comexstat", "unctadstat_maritime"]),
      series: [
        { key: "actual", label: "Baltic Dry Index, 12-month change", color: p.accent, values: actual, format: fmt.pctSigned },
        { key: "implied", label: "Attributed to the four forces", color: p.fitted, dashed: true, values: implied, format: fmt.pctSigned },
      ],
      yFormat: "+.0f", yLabel: "Percent change over 12 months", area: true, height: 320, tableRows: 48,
    });
  }

  function renderToday() {
    const s = D.summary, pw = s.portwatch_latest_week, la = s.latest_attribution;
    const n = $("#today");
    n.innerHTML = "";
    n.append(C.el("h3", null, "What the data says today"));
    const parts = [];
    const comps = COMPONENTS.slice(0, 4).map((c) => ({ ...c, v: la[c.key] })).sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
    const lead = comps[0];
    parts.push(`Over the twelve months to ${fmt.month(la.end)} the index moved ${signed(la.total_pct, 0)} percent. The largest measured force was ${lead.label.toLowerCase()} (${fmt.lp(lead.v)} log points), followed by ${comps[1].label.toLowerCase()} (${fmt.lp(comps[1].v)}). The residual, the part the four forces do not explain, was ${fmt.lp(la.residual)} log points against a total move of ${fmt.lp(la.total)}.`);
    if (pw && pw.export_mt) {
      const yoy = pw.export_mt_year_ago ? pctChange(pw.export_mt, pw.export_mt_year_ago) : null;
      parts.push(`The most recent demand signal is AIS-based: in the week starting ${pw.week}, ports worldwide loaded ${mt(pw.export_mt)} of dry bulk cargo${yoy === null ? "" : `, ${fmt.pct0(yoy)} against the same week a year earlier`} (IMF PortWatch).`);
    }
    const cov = s.coverage || {};
    const groups = cov.route_groups || [];
    const lastGroup = groups.length ? groups.reduce((a, b) => (a.last_month <= b.last_month ? a : b)) : null;
    const bdiEnd = cov.freight_index_window ? cov.freight_index_window[1] : null;
    let reason = "";
    if (lastGroup && bdiEnd) {
      reason = bdiEnd <= lastGroup.last_month ? `because the public monthly freight index ends in ${fmt.month(bdiEnd)}` : `because the customs series for ${lastGroup.label || lastGroup.group_id} ends in ${fmt.month(lastGroup.last_month)}`;
    }
    if (s.comex_coverage) parts.push(`Brazilian customs data run to ${s.comex_coverage.split(" to ")[1].split(" (")[0]}; the balanced route panel ends in ${fmt.month(s.panel.last)}${reason ? " " + reason : ""}.`);
    parts.forEach((t) => n.append(C.el("p", null, t)));
    n.append(C.el("p", "asof", `Built ${s.built_at.replace("T", " ").replace("Z", " UTC")}. Panel ${fmt.month(s.panel.first)} to ${fmt.month(s.panel.last)}, ${s.panel.n_months} months.`));
    text("#meta-line", `Data through ${fmt.month(s.panel.last)} for the panel. Built ${s.built_at.slice(0, 10)}. All inputs are public; every figure links to a source and a retrieval date.`);
  }

  /* ---------------------------------------------------------------- overview */
  function renderOverview() {
    const se = D.series, ro = D.rolling, p = C.palette();
    const xs = se.month.map(C.monthToDate);
    const idx = (arr) => { const b = arr.find((v) => v !== null); return arr.map((v) => (v === null ? null : (v / b) * 100)); };
    const first = se.month[0], last = se.month[se.month.length - 1];
    const growth12 = (arr) => d3.mean(arr.slice(-12)) / d3.mean(arr.slice(0, 12)) - 1;
    text("#overview-lede", `Four series drive the decomposition: the freight index, tonnes on the covered routes, the tonne-miles they generate, and the bulk carrier fleet adjusted for port time. Indexed to ${fmt.month(first)} = 100 so their relative movements can be compared on one axis.`);
    C.lineChart("#chart-levels", {
      title: `Between the first and last twelve months of the panel, tonne-miles grew ${signed(growth12(se.tonne_miles_bn) * 100, 0)} percent and the fleet ${signed(growth12(se.fleet_mdwt) * 100, 0)} percent; the index swung far more`,
      subtitle: `Index, ${fmt.month(first)} = 100. Tonnes and tonne-miles cover the ${D.summary.routes.count} routes in this project; fleet is world bulk carrier deadweight; effective supply is the at-sea share factor.`,
      source: sourceLine("ASUB (index), UN Comtrade and Brazil Comex Stat (tonnes), this project (distances), UNCTADstat (fleet, port time)", ["asub_baltic_dry_index", "un_comtrade", "brazil_comexstat", "unctadstat_maritime"]),
      series: [
        { key: "bdi", label: "Baltic Dry Index", color: p.accent, values: xs.map((x, i) => ({ x, y: idx(se.bdi)[i] })) },
        { key: "tm", label: "Tonne-miles", color: p.s2, values: xs.map((x, i) => ({ x, y: idx(se.tonne_miles_bn)[i] })) },
        { key: "t", label: "Tonnes", color: p.s1, values: xs.map((x, i) => ({ x, y: idx(se.tonnes_mt)[i] })) },
        { key: "fleet", label: "Fleet (dwt)", color: p.s3, values: xs.map((x, i) => ({ x, y: idx(se.fleet_mdwt)[i] })) },
        { key: "eff", label: "Effective supply factor", color: p.s4, values: xs.map((x, i) => ({ x, y: idx(se.supply_factor)[i] })) },
      ],
      yScale: "log", yFormat: ",.0f", yLabel: "Index (log scale)", height: 340, valueFormat: fmt.f1,
    });
    const data = ro.month.map((m, i) => ({ x: C.monthToDate(m), label: `${fmt.month(ro.start[i])} to ${fmt.month(m)}`, volume: ro.volume[i], distance: ro.distance[i], fleet: ro.fleet[i], effective_supply: ro.effective_supply[i], residual: ro.residual[i], total: ro.total[i] }));
    const avgAbsResid = d3.mean(ro.residual, (v) => Math.abs(v));
    const avgAbsTotal = d3.mean(ro.total, (v) => Math.abs(v));
    const agree = d3.mean(ro.total.map((t, i) => (Math.sign(t - ro.residual[i]) === Math.sign(t) ? 1 : 0)));
    C.stackedBars("#chart-rolling", {
      title: `Rolling twelve-month moves: the four forces give the direction of ${fmt.pctPlain(agree)} of them, but the residual averages ${fmt.f2(avgAbsResid)} log points against a typical move of ${fmt.f2(avgAbsTotal)}`,
      subtitle: `Change in the log index over the preceding twelve months (line) and its attribution (bars), log points. Endpoints are three-month averages. Elasticity ${d3.format(".2f")(ro.elasticity)}.`,
      source: sourceLine("this project's decomposition of the series above", ["asub_baltic_dry_index", "un_comtrade", "brazil_comexstat", "unctadstat_maritime"]),
      components: COMPONENTS.map((c) => ({ key: c.key, label: c.label, color: c.color() })),
      total: { key: "total", label: "Total change in log index", color: p.ink },
      data, yLabel: "Log points", height: 340,
    });
    const notes = $("#overview-notes");
    notes.innerHTML = "";
    const el = D.model.elasticity.headline;
    notes.append(C.el("h3", null, "Reading the charts"));
    notes.append(C.el("p", null, `A log point of 0.10 is roughly a ten percent move. The elasticity of ${d3.format(".2f")(el.elasticity)} (standard error ${d3.format(".2f")(el.std_error)}, Newey-West) means that a one percent rise in tonne-miles relative to effective fleet has been associated with a ${d3.format(".1f")(el.elasticity)} percent rise in the index. The regression on twelve-month changes explains ${fmt.pctPlain(el.r_squared)} of the variance of the twelve-month change in the log index over ${el.n_obs} observations.`));
    notes.append(C.el("p", null, "Because freight rates respond to the balance between demand and supply, the cargo volume, haul, fleet and port-time effects are all scaled by the same elasticity. The residual collects everything else: speed changes, bunker prices, sentiment, contract timing and measurement error in the inputs."));
  }

  /* ---------------------------------------------------------------- decomposition */
  function attributeBetween(startM, endM) {
    // recompute from the panel series with 3-month trailing averages, mirroring the Python model
    const se = D.series, e = D.rolling.elasticity;
    const i0 = se.month.indexOf(startM), i1 = se.month.indexOf(endM);
    if (i0 < 2 || i1 <= i0) return null;
    const avg = (arr, i) => (arr[i] + arr[i - 1] + arr[i - 2]) / 3;
    const a = { index: avg(se.bdi, i0), t: avg(se.tonnes_mt, i0), tm: avg(se.tonne_miles_bn, i0), f: avg(se.fleet_mdwt, i0), s: avg(se.supply_factor, i0) };
    const b = { index: avg(se.bdi, i1), t: avg(se.tonnes_mt, i1), tm: avg(se.tonne_miles_bn, i1), f: avg(se.fleet_mdwt, i1), s: avg(se.supply_factor, i1) };
    const total = Math.log(b.index / a.index);
    const volume = e * Math.log(b.t / a.t);
    const distance = e * Math.log((b.tm / b.t) / (a.tm / a.t));
    const fleet = -e * Math.log(b.f / a.f);
    const eff = -e * Math.log(b.s / a.s);
    const residual = total - volume - distance - fleet - eff;
    return { total, volume, distance, fleet, effective_supply: eff, residual, index_start: a.index, index_end: b.index, elasticity: e };
  }

  function renderWaterfall(att, title, sub) {
    const p = C.palette();
    const share = (v) => (att.total ? v / att.total : NaN);
    const steps = [
      { label: `Index, ${fmt.month(att.start)}`, value: att.index_start, kind: "start", color: p.fitted },
      ...COMPONENTS.map((c) => ({ label: c.label, value: att[c.key], share: share(att[c.key]), kind: "step", color: c.color() })),
      { label: `Index, ${fmt.month(att.end)}`, value: att.index_end, kind: "end", color: p.accent },
    ];
    C.waterfall("#chart-waterfall", {
      title, subtitle: sub,
      source: sourceLine("this project's decomposition; endpoints are three-month averages ending in the months shown", ["asub_baltic_dry_index", "un_comtrade", "brazil_comexstat", "unctadstat_maritime"]),
      steps, yLabel: "Index points", height: 360,
    });
  }

  function describe(att) {
    const comps = COMPONENTS.slice(0, 4).map((c) => ({ ...c, v: att[c.key] }));
    const explained = att.total ? 1 - att.residual / att.total : null;
    const dir = att.total >= 0 ? "rose" : "fell";
    const biggest = comps.slice().sort((a, b) => Math.abs(b.v) - Math.abs(a.v))[0];
    const same = comps.filter((c) => Math.sign(c.v) === Math.sign(att.total) && c.v !== 0).map((c) => c.label.toLowerCase());
    let s = `The index ${dir} from ${fmt.int(att.index_start)} to ${fmt.int(att.index_end)} (${fmt.lp(att.total)} log points, ${signed((Math.exp(att.total) - 1) * 100, 0)} percent). `;
    s += `The largest measured contribution came from ${biggest.label.toLowerCase()} (${fmt.lp(biggest.v)}). `;
    s += same.length ? `Forces pushing in the same direction as the move: ${same.join(", ")}. ` : "No measured force pushed in the direction of the move. ";
    s += explained === null ? "" : `Together the four forces account for ${fmt.pctPlain(explained)} of the move; the residual is ${fmt.lp(att.residual)} log points.`;
    return s;
  }

  function renderDecomposition() {
    const eps = D.episodes, se = D.series;
    const sel = $("#episode-select"), s0 = $("#start-select"), s1 = $("#end-select");
    sel.innerHTML = ""; s0.innerHTML = ""; s1.innerHTML = "";
    const custom = C.el("option", null, "Custom months");
    custom.value = "custom";
    sel.append(custom);
    eps.forEach((e) => { const o = C.el("option", null, `${e.name} (${fmt.month(e.start)} to ${fmt.month(e.end)})`); o.value = e.episode_id; sel.append(o); });
    se.month.forEach((m, i) => {
      if (i < 2) return;
      const a = C.el("option", null, fmt.month(m)); a.value = m; s0.append(a);
      const b = C.el("option", null, fmt.month(m)); b.value = m; s1.append(b);
    });
    const show = (att, name, desc) => {
      renderWaterfall(att, name ? `${name}: ${describe(att).split(". ")[1]}` : `${fmt.month(att.start)} to ${fmt.month(att.end)}: ${describe(att).split(". ")[1]}`, `Attribution of the change in the Baltic Dry Index, ${fmt.month(att.start)} to ${fmt.month(att.end)}. Bars are contributions in log points converted to index points along the way; labels show log points.`);
      text("#episode-text", (desc ? desc + " " : "") + describe(att));
    };
    const pick = (id) => {
      const e = eps.find((x) => x.episode_id === id);
      if (!e) return;
      s0.value = e.start; s1.value = e.end;
      show(e, e.name, e.description);
    };
    sel.addEventListener("change", () => { if (sel.value !== "custom") pick(sel.value); });
    const customPick = () => {
      sel.value = "custom";
      const att = attributeBetween(s0.value, s1.value);
      if (!att) { text("#episode-text", "Choose an end month after the start month (both need two preceding months for the three-month average)."); return; }
      show({ ...att, start: s0.value, end: s1.value }, null, null);
    };
    s0.addEventListener("change", customPick);
    s1.addEventListener("change", customPick);
    const start = eps.find((e) => e.episode_id === "recent") || eps[eps.length - 1];
    sel.value = start.episode_id;
    pick(start.episode_id);
    const notes = $("#decomp-notes");
    notes.innerHTML = "";
    notes.append(C.el("h3", null, "All covered episodes"));
    const wrap = C.el("div", "table-wrap");
    C.dataTable(wrap, [
      { key: "name", label: "Episode" }, { key: "window", label: "Window" },
      { key: "total", label: "Total (log points)", num: true, format: fmt.lp },
      { key: "volume", label: "Volume", num: true, format: fmt.lp }, { key: "distance", label: "Haul", num: true, format: fmt.lp },
      { key: "fleet", label: "Fleet", num: true, format: fmt.lp }, { key: "effective_supply", label: "Port time", num: true, format: fmt.lp },
      { key: "residual", label: "Residual", num: true, format: fmt.lp }, { key: "explained_share", label: "Explained", num: true, format: (v) => (Number.isFinite(v) ? fmt.pctPlain(v) : "") },
    ], eps.map((e) => ({ ...e, window: `${fmt.month(e.start)} to ${fmt.month(e.end)}` })));
    notes.append(wrap);
    notes.append(C.el("p", null, "Episodes before the start of the monthly panel are not shown because the customs data that build the tonne-mile series begin later than the index. The explained share can exceed 100 percent when the residual has the opposite sign to the move. Episodes shorter than twelve months compare endpoints in different seasons; cargo volumes are seasonal, so their volume effect includes seasonal movement."));
  }

  /* ---------------------------------------------------------------- routes */
  async function renderRoutes() {
    const R = D.routes, p = C.palette();
    const land = topojson.feature(D.land, D.land.objects.land);
    const top = R.routes.slice(0, 40);
    const maxShare = d3.max(top, (r) => r.share_last12);
    text("#routes-lede", `${R.routes.length} routes covering ${fmt.f1(R.total_tonnes_last12_mt / 1000)} billion tonnes and ${d3.format(",.0f")(R.total_tonne_miles_last12_bn / 1000)} trillion tonne-miles in the twelve months to ${fmt.month(R.window[1])}. Line width is proportional to the route's share of tonne-miles; colour is the commodity.`);
    C.routeMap("#chart-map", {
      title: `The ${top.length} largest routes: Australia and Brazil to China dominate tonne-miles, with grain and coal trades fanning out from the Americas and Indonesia`,
      subtitle: `Routes drawn on the maritime network used for the distance matrix (main leg of each route). Twelve months to ${fmt.month(R.window[1])}.`,
      source: sourceLine("UN Comtrade and Brazil Comex Stat (volumes), NGA World Port Index (port positions), searoute maritime network (paths), Natural Earth (coastlines)", ["un_comtrade", "brazil_comexstat", "nga_world_port_index"]),
      land,
      legend: Object.keys(COMMODITY_LABEL).map((k) => ({ label: COMMODITY_LABEL[k], color: COMMODITY_COLOR[k]() })),
      routes: top.map((r) => ({ id: r.route_id, geometry: r.geometry, color: COMMODITY_COLOR[r.commodity](), width: 0.8 + 9 * Math.sqrt(r.share_last12 / maxShare), label: `${r.origin_name} to ${r.destination_name}, ${COMMODITY_LABEL[r.commodity].toLowerCase()}`,
        rows: [{ label: "Tonnes, last 12 months", value: mt(r.tonnes_last12_mt) }, { label: "Tonne-miles", value: d3.format(",.0f")(r.tonne_miles_last12_bn) + " bn" }, { label: "Share of covered tonne-miles", value: d3.format(".1%")(r.share_last12) }, { label: "Distance", value: fmt.int(r.distance_nm) + " nm" }, { label: "Source", value: r.source }] })),
      ports: R.ports,
      tableColumns: [{ key: "route", label: "Route" }, { key: "commodity", label: "Commodity" }, { key: "tonnes_last12_mt", label: "Tonnes (Mt)", num: true, format: fmt.f1 }, { key: "distance_nm", label: "Distance (nm)", num: true, format: fmt.int }, { key: "tonne_miles_last12_bn", label: "Tonne-miles (bn)", num: true, format: fmt.int }, { key: "share_last12", label: "Share", num: true, format: d3.format(".1%") }, { key: "source", label: "Source" }],
      tableRows: R.routes.map((r) => ({ ...r, route: `${r.origin_name} to ${r.destination_name}`, commodity: COMMODITY_LABEL[r.commodity] })),
    });
    const top15 = R.routes.slice(0, 15);
    C.hbars("#chart-route-shares", {
      title: `The top three routes carry ${fmt.pctPlain(d3.sum(R.routes.slice(0, 3), (r) => r.share_last12))} of covered tonne-miles; the top fifteen carry ${fmt.pctPlain(d3.sum(top15, (r) => r.share_last12))}`,
      subtitle: `Share of tonne-miles on covered routes, twelve months to ${fmt.month(R.window[1])}.`,
      source: sourceLine("UN Comtrade, Brazil Comex Stat, this project's distance matrix", ["un_comtrade", "brazil_comexstat"]),
      rows: top15.map((r) => ({ label: `${r.origin_name} to ${r.destination_name}, ${COMMODITY_LABEL[r.commodity].toLowerCase()}`, short: `${r.origin} to ${r.destination} ${r.commodity.replace("_", " ")}`, value: r.share_last12, color: COMMODITY_COLOR[r.commodity](), sub: `${mt(r.tonnes_last12_mt)} over ${fmt.int(r.distance_nm)} nm`, subLabel: "Volume and distance" })),
      legend: Object.keys(COMMODITY_LABEL).map((k) => ({ label: COMMODITY_LABEL[k], color: COMMODITY_COLOR[k](), rect: true })),
      valueLabel: "Share of tonne-miles", valueFormat: d3.format(".1%"), xFormat: ".0%", rowLabel: "Route", labelWidth: 290,
    });
    const M = R.monthly;
    const xs = M.month.map(C.monthToDate);
    const comms = Object.keys(M.by_commodity);
    const totalsLast = comms.map((k) => M.by_commodity[k][M.month.length - 1]);
    C.lineChart("#chart-commodity", {
      title: `Iron ore generates ${fmt.pctPlain(totalsLast[comms.indexOf("iron_ore")] / d3.sum(totalsLast))} of covered tonne-miles in ${fmt.month(M.month[M.month.length - 1])}`,
      subtitle: "Monthly tonne-miles on covered routes by commodity group, billions.",
      source: sourceLine("UN Comtrade, Brazil Comex Stat, this project's distance matrix", ["un_comtrade", "brazil_comexstat"]),
      series: comms.map((k) => ({ key: k, label: COMMODITY_LABEL[k], color: COMMODITY_COLOR[k](), values: xs.map((x, i) => ({ x, y: M.by_commodity[k][i] })) })),
      yZero: true, yLabel: "Billion tonne-miles per month", height: 300,
    });
    const RO = D.rolling;
    const hs = RO.month.map((m, i) => ({ x: C.monthToDate(m), label: `${fmt.month(RO.start[i])} to ${fmt.month(m)}`, mix: RO.haul_dlog_mix[i], rerouting: RO.haul_dlog_rerouting[i], total: RO.haul_dlog[i] })).filter((d) => d.total !== null);
    const pctOf = (v) => `${d3.format(".1f")((Math.exp(v) - 1) * 100)} percent`;
    const verbOf = (v) => (v >= 0 ? "added" : "took off");
    if (hs.length) {
      const lastH = hs[hs.length - 1];
      const peak = hs.reduce((a, b) => (Math.abs(b.rerouting) > Math.abs(a.rerouting) ? b : a));
      C.stackedBars("#chart-haul-split", {
        title: `Rerouting ${peak.rerouting >= 0 ? "lengthened" : "shortened"} the average haul by up to ${pctOf(Math.abs(peak.rerouting))} over twelve months (${peak.label}); in the latest window trade mix ${verbOf(lastH.mix)} ${pctOf(Math.abs(lastH.mix))} and rerouting ${verbOf(lastH.rerouting)} ${pctOf(Math.abs(lastH.rerouting))}`,
        subtitle: "Twelve-month log change in the average haul of covered cargo (line), split into trade mix (shares shifting between routes at fixed distances) and rerouting (distances changing at fixed shares, from the Red Sea and Panama diversions). Values are changes in the haul itself, before the elasticity is applied; 0.01 is about one percent.",
        source: sourceLine("UN Comtrade, Brazil Comex Stat, IMF PortWatch chokepoint transits, this project's distance matrix", ["un_comtrade", "brazil_comexstat", "imf_portwatch"]),
        components: [{ key: "mix", label: "Trade mix", color: p.s1 }, { key: "rerouting", label: "Rerouting", color: p.s2 }],
        total: { key: "total", label: "Change in average haul", color: p.accent },
        data: hs, yLabel: "Log change over 12 months", height: 300,
      });
    }
    const notes = $("#routes-notes");
    notes.innerHTML = "";
    notes.append(C.el("h3", null, "How routes are measured"));
    notes.append(C.el("p", null, "Each route is one origin economy, one destination economy and one commodity group, measured from the customs statistics of the reporter named in the table (the exporter wherever the exporter publishes timely monthly data, otherwise the importer). Distances are volume-weighted blends of representative loading and discharge terminals, routed on a maritime network. Iron ore routes and coal routes outside the Americas avoid the Panama Canal because Capesize tonnage cannot use it, while coal from the United States, Colombia and Canada, largely carried on Panamax ships, takes the canal; routes that normally transit the Suez Canal are lengthened towards the Cape of Good Hope in proportion to the fall in dry bulk transits through the Red Sea observed by IMF PortWatch (from November 2023), and routes that use the Panama Canal the same way during the draught restrictions of 2023 to September 2024. Routes serving a port inside the Red Sea are never diverted."));
    notes.append(C.el("p", null, "Routes between land neighbours (United States to Mexico, Russia and Mongolia to China, Argentina to Brazil and Chile, Ukraine to its European neighbours) are excluded because customs data do not separate sea from rail and truck. Minor bulks (bauxite, steel, cement, fertilisers, forest products) are not covered; the panel describes the major bulks that drive Capesize and Panamax demand."));
  }

  /* ---------------------------------------------------------------- fleet */
  function renderFleet() {
    const F = D.fleet, P = D.portwatch, p = C.palette();
    const A = F.annual;
    const lastY = A.year[A.year.length - 1];
    const years = A.year.map((y) => new Date(Date.UTC(y, 0, 1)));
    text("#fleet-lede", `World bulk carrier capacity reached ${d3.format(",.0f")(A.fleet_mdwt[A.fleet_mdwt.length - 1])} million deadweight tonnes on 1 January ${lastY}. Supply is the fleet adjusted for the share of time ships spend in port; speed is not observable from public data and stays in the residual.`);
    C.lineChart("#chart-fleet", {
      title: (() => {
        const g = A.fleet_growth_pct.map((v, i) => [A.year[i], v]).filter((x) => x[1] !== null && Number.isFinite(x[1]));
        const up = g.filter((x) => x[1] > 0).length;
        const last = g[g.length - 1], prev = g[g.length - 2];
        const trend = prev && last[1] < prev[1] ? "slowed to" : prev && last[1] > prev[1] ? "picked up to" : "was";
        const every = up === g.length ? `has grown every year since ${A.year[0]}` : `has grown in ${up} of the ${g.length} years since ${A.year[0]}`;
        return `The bulk carrier fleet ${every}; growth ${trend} ${d3.format(".1f")(last[1])} percent in the year to 1 January ${last[0]}`;
      })(),
      subtitle: "World bulk carrier fleet as of 1 January, million deadweight tonnes (left table also lists ship numbers, average age and annual growth).",
      source: sourceLine("UNCTADstat, merchant fleet by type of ship (Clarksons Research data)", ["unctadstat_maritime"]),
      series: [{ key: "fleet", label: "Bulk carrier fleet (Mdwt)", color: p.s3, values: years.map((x, i) => ({ x, y: A.fleet_mdwt[i] })) }],
      yZero: true, yLabel: "Million dwt", height: 280, xFormat: (d) => String(d.getUTCFullYear()), xLabel: "Year", tableRows: 60,
    });
    // add growth and age to the table of the fleet chart
    const fleetTable = $("#chart-fleet .chart-table");
    C.dataTable(fleetTable, [{ key: "year", label: "Year (1 January)" }, { key: "fleet_mdwt", label: "Fleet (Mdwt)", num: true, format: fmt.f1 }, { key: "fleet_growth_pct", label: "Growth (%)", num: true, format: (v) => (v === null ? "" : d3.format("+.1f")(v)) }, { key: "number_of_ships", label: "Ships", num: true, format: fmt.int }, { key: "average_age_years", label: "Average age (years)", num: true, format: fmt.f1 }, { key: "built_gt_m", label: "World GT built, all types (m)", num: true, format: fmt.f1 }, { key: "scrapped_gt_m", label: "World GT scrapped, all types (m)", num: true, format: fmt.f1 }], A.year.map((y, i) => ({ year: y, fleet_mdwt: A.fleet_mdwt[i], fleet_growth_pct: A.fleet_growth_pct[i], number_of_ships: A.number_of_ships[i], average_age_years: A.average_age_years[i], built_gt_m: A.built_gt_m[i], scrapped_gt_m: A.scrapped_gt_m[i] })));
    const S = F.supply_factor, PT = F.port_time;
    const sfx = S.month.map(C.monthToDate);
    const ptRows = PT.month.map((m, i) => ({ x: C.monthToDate(m), y: PT.median_port_days[i] }));
    C.lineChart("#chart-supply", {
      title: PT.median_port_days.length ? `Dry bulk ships spent a median ${fmt.f2(PT.median_port_days[PT.median_port_days.length - 1])} days per port call in the latest half-year, against ${fmt.f2(PT.median_port_days[0])} at the start of the series` : "Port time series not available",
      subtitle: "Median time in port per call for dry bulk carriers (days, semiannual observations placed at mid-period). The effective supply factor in the panel is the at-sea share derived from this series; see Methodology.",
      source: sourceLine("UNCTADstat, port call and performance statistics (MarineTraffic data)", ["unctadstat_maritime"]),
      series: [{ key: "pt", label: "Median time in port (days)", color: p.s4, values: ptRows, format: fmt.f2 }],
      yZero: true, yLabel: "Days per call", height: 260, tableRows: 40,
    });
    const CK = P.chokepoints_monthly;
    const ckx = CK.month.map(C.monthToDate);
    const names = ["Bab el-Mandeb Strait", "Suez Canal", "Cape of Good Hope", "Panama Canal"].filter((n) => CK[n]);
    const colors = [p.s2, p.s1, p.s3, p.s4];
    const bab = CK["Bab el-Mandeb Strait"] || [];
    const i2023 = CK.month.indexOf("2023-10"), iLast = CK.month.length - 1;
    C.lineChart("#chart-chokepoints", {
      title: bab.length && i2023 >= 0 ? `Dry bulk transits through Bab el-Mandeb were ${fmt.f1(bab[iLast])} a day in ${fmt.month(CK.month[iLast])} against ${fmt.f1(bab[i2023])} in October 2023, before the Red Sea attacks` : "Chokepoint transits",
      subtitle: "Dry bulk vessel transits per day through four chokepoints, monthly averages of daily AIS counts.",
      source: sourceLine("IMF PortWatch, daily chokepoint transits", ["imf_portwatch"]),
      series: names.map((n, i) => ({ key: n, label: n, color: colors[i], values: ckx.map((x, j) => ({ x, y: CK[n][j] })) })),
      yZero: true, yLabel: "Transits per day", height: 300,
    });
    const W = P.weekly;
    const wx = W.week.map((w) => new Date(w + "T00:00:00Z"));
    C.lineChart("#chart-portwatch", {
      title: `AIS-based port loadings of dry bulk cargo: ${mt(W.export_mt[W.export_mt.length - 1])} in the week of ${W.week[W.week.length - 1]}`,
      subtitle: "Weekly global dry bulk export (loaded) and import (discharged) tonnage estimated from vessel draught changes, million tonnes. Complete weeks only.",
      source: sourceLine("IMF PortWatch, daily port activity (all ports, dry bulk carriers)", ["imf_portwatch"]),
      series: [{ key: "exp", label: "Loaded (exports)", color: p.s1, values: wx.map((x, i) => ({ x, y: W.export_mt[i] })) }, { key: "imp", label: "Discharged (imports)", color: p.s2, values: wx.map((x, i) => ({ x, y: W.import_mt[i] })) }],
      yZero: true, yLabel: "Million tonnes per week", height: 280, xFormat: (d) => d3.utcFormat("%Y-%m-%d")(d), xLabel: "Week starting", tableRows: 52,
    });
    const notes = $("#fleet-notes");
    notes.innerHTML = "";
    notes.append(C.el("h3", null, "What supply means here"));
    notes.append(C.el("p", null, "Fleet supply is the world bulk carrier fleet in deadweight tonnes as of 1 January each year, interpolated between years at a constant growth rate; months after the last observation are extrapolated at the last annual rate and flagged in the downloads. Deliveries and demolitions by ship type are not published in the public UNCTAD tables, so the fleet change is shown net; world gross tonnage built and scrapped across all ship types is listed in the table for context."));
    const SE = D.series;
    const flagged = (key) => SE.month.filter((m, i) => SE[key][i]);
    const fx = flagged("fleet_extrapolated"), sx = flagged("supply_extrapolated");
    const flagText = [];
    if (fx.length) flagText.push(`the fleet is extrapolated for ${fx.length} panel month${fx.length === 1 ? "" : "s"} (${ranges(fx)})`);
    if (sx.length) flagText.push(`the effective supply factor uses an assumed or held value for ${sx.length} panel months (${ranges(sx)}): it is set to one before PortWatch port calls start in January 2019, and the UNCTAD median port time is held at its last published value (second half of 2023) afterwards`);
    if (flagText.length) notes.append(C.el("p", null, `Within the panel, ${flagText.join("; ")}. These months are flagged in the panel download.`));
    notes.append(C.el("p", null, "The effective supply factor converts port time into capacity: the dry bulk port calls observed by IMF PortWatch in a month, divided by the number of bulk carriers (UNCTAD), multiplied by the UNCTAD median days in port per call, give port days per ship; one minus port days over the days in the month is the at-sea share. The factor is the at-sea share relative to January 2019, so more congestion lowers effective supply and, through the elasticity, raises the implied index. Nothing in the public data measures sailing speed."));
  }

  /* ---------------------------------------------------------------- methodology */
  function renderMethodology() {
    const m = D.model.elasticity, dr = D.model.difference_regression, V = D.validation, S = D.sources;
    const prose = $("#method-prose");
    prose.innerHTML = "";
    const add = (tag, t, cls) => prose.append(C.el(tag, cls, t));
    add("h3", "The question");
    add("p", "Over a given period, how much of the move in the Baltic Dry Index is explained by cargo volumes, by average haul distance, by fleet growth and by effective supply effects such as port congestion, and how much is left unexplained?");
    add("h3", "Definitions");
    const dl = C.el("dl");
    [["Tonne-miles", "Sum over routes of tonnes shipped multiplied by the sailing distance in nautical miles between representative loading and discharge ports."], ["Average haul", "Total tonne-miles divided by total tonnes, so that the change in tonne-miles is exactly the change in tonnes plus the change in haul. The haul change is split further into trade mix (shares shifting at fixed distances) and rerouting (distances changing at fixed shares)."], ["Fleet", "World bulk carrier capacity in deadweight tonnes as of 1 January (UNCTAD), interpolated to months."], ["Effective supply factor", "Share of fleet time at sea: dry bulk port calls observed by IMF PortWatch per bulk carrier (UNCTAD fleet count), multiplied by the UNCTAD median time in port per call, gives port days per ship; the factor is the at-sea share relative to January 2019."], ["Residual", "The part of the change in the log index not accounted for by the four forces. It is reported, never redistributed."]].forEach(([k, v]) => { dl.append(C.el("dt", null, k), C.el("dd", null, v)); });
    prose.append(dl);
    add("h3", "The attribution identity");
    add("p", "For any two months, with endpoint values averaged over three months:", null);
    add("p", "log(BDI1 / BDI0) = e [ dlog(tonnes) + dlog(haul) ] minus e [ dlog(fleet) + dlog(supply factor) ] + residual", "formula");
    add("p", `The elasticity e is estimated once, by regressing the twelve-month change in the log index on the twelve-month change in the log of tonne-miles over effective fleet (${fmt.month(m.panel_window[0])} to ${fmt.month(m.panel_window[1])}, ${m.headline.n_obs} overlapping twelve-month changes) with Newey-West standard errors over twelve lags. Twelve-month changes remove the seasonal pattern and the slow trends that dominate the levels of both series. The regression intercept (${fmt.lp(m.headline.intercept)} log points a year) is a drift the four forces do not account for; the decomposition leaves it in the residual. Each contribution is e multiplied by the log change of its input; the four contributions and the residual sum exactly to the observed change.`);
    add("h3", "Cross-checks");
    add("p", `Robustness variants re-estimate the elasticity on monthly levels (with and without month dummies), on four-quarter changes of quarterly averages and on each half of the sample. The levels regressions are shown for completeness: both series trend and their residuals are strongly autocorrelated, so their coefficients are not reliable. An unrestricted regression of monthly changes in the log index on the four component changes, with lags for volume and haul, tests whether the data accept one common elasticity (Wald test of equal absolute coefficients with the signs implied by the identity).`);
    add("h3", "Reconciliation");
    add("p", "Where two sources measure the same flow (Brazilian exports in Comex Stat and in UN Comtrade; exporter and importer records for Australia to China, Brazil to China, Indonesia to China, United States to China and Australia to Japan) the series are compared on twelve-month rolling sums and the median gap is reported below. PortWatch dry bulk port loadings are compared with UNCTAD seaborne dry cargo as a level check.");
    // sources table
    const st = $("#sources-table");
    C.dataTable(st, [{ key: "title", label: "Dataset" }, { key: "publisher", label: "Publisher" }, { key: "frequency", label: "Frequency" }, { key: "coverage", label: "Coverage" }, { key: "units", label: "Units" }, { key: "retrieved_at", label: "Retrieved" }, { key: "licence", label: "Licence" }, { key: "link", label: "Link" }], S.map((s) => ({ ...s, retrieved_at: s.retrieved_at.replace("T", " ").replace("Z", " UTC"), link: s.source_url })));
    st.querySelectorAll("tbody tr").forEach((tr, i) => { const td = tr.lastElementChild; td.textContent = ""; const a = C.el("a", null, "source"); a.href = S[i].source_url; a.rel = "noopener"; td.append(a); });
    const lim = C.el("div", "prose");
    lim.append(C.el("h3", null, "Known limitations of the inputs"));
    const ul = C.el("ul");
    S.forEach((s) => (s.limitations || []).forEach((l) => ul.append(C.el("li", null, `${s.title.split(":")[0]}: ${l}`))));
    lim.append(ul);
    st.after(lim);
    // quantity gaps, proxies and interpolation per route group
    const cov = D.summary.coverage || {};
    const fs = cov.fill_summary || {};
    const groups = (cov.route_groups || []).filter((g) => fs[g.group_id]);
    if (groups.length) {
      const gap = C.el("div", "prose");
      gap.append(C.el("h3", null, "Where customs quantities are estimated or interpolated"));
      gap.append(C.el("p", null, "Customs offices publish values for every month but not always weights. Where a reporter's monthly records carry a value and no net weight, tonnes are estimated as the value divided by that reporter's annual unit value for the same partner and commodity from the annual UN Comtrade series (flagged as a proxy). A route-month in which more than 1 percent of the value still has no usable weight, a month in which most of a group's value has none, and a month the reporter did not publish are interpolated linearly between the neighbouring months; gaps at the ends of a group's range are trimmed rather than extrapolated. Shares below refer to the panel window. The table lists every affected group; the per-route flags are in the downloads."));
      const reasons = { proxy_unit_value: "proxy from annual unit value", interpolated_unpublished: "interpolated, month not published", interpolated_no_weight: "interpolated, weights missing" };
      const rows = groups.map((g) => {
        const f = fs[g.group_id];
        const detail = Object.entries(f.months || {}).map(([k, v]) => `${reasons[k] || k}: ${v.length} month${v.length === 1 ? "" : "s"} (${ranges(v)})`).join(". ");
        return { group: g.label || g.group_id, window: `${fmt.month(g.first_month)} to ${fmt.month(g.last_month)}`, proxy: f.proxy_tonnes_share, interp: f.interpolated_tonnes_share, detail: detail || "none" };
      }).filter((r) => r.proxy > 0 || r.interp > 0);
      const gt = C.el("div");
      gap.append(gt);
      C.dataTable(gt, [{ key: "group", label: "Route group" }, { key: "window", label: "Data available" }, { key: "proxy", label: "Proxy share of tonnes", num: true, format: d3.format(".1%") }, { key: "interp", label: "Interpolated share of tonnes", num: true, format: d3.format(".1%") }, { key: "detail", label: "Affected months" }], rows);
      lim.after(gap);
    }
    // validation table
    const vt = $("#validation-table");
    C.dataTable(vt, [{ key: "dataset", label: "Dataset" }, { key: "check", label: "Check" }, { key: "status", label: "Status" }, { key: "message", label: "Result" }], V.checks);
    vt.querySelectorAll("tbody tr").forEach((tr, i) => { const td = tr.children[2]; td.className = "status-" + V.checks[i].status; td.textContent = V.checks[i].status.toUpperCase(); });
    vt.before(C.el("p", "source", `${V.summary.pass} checks passed, ${V.summary.warn} warnings, ${V.summary.fail} failures. Generated ${V.generated_at.replace("T", " ").replace("Z", " UTC")}.`));
    // regression table
    const rt = $("#regression-table");
    const rows = [{ model: "Headline: twelve-month change in log index on twelve-month change in log(tonne-miles / effective fleet)", elasticity: m.headline.elasticity, se: m.headline.std_error, t: m.headline.t_stat, r2: m.headline.r_squared, n: m.headline.n_obs, dw: m.headline.durbin_watson }];
    const labels = { levels_with_month_dummies: "Monthly levels with month dummies", levels_plain: "Monthly levels", quarterly_four_quarter_changes: "Quarterly averages, four-quarter changes", first_half: "First half of the sample, twelve-month changes", second_half: "Second half of the sample, twelve-month changes" };
    Object.entries(m.robustness).forEach(([k, v]) => rows.push({ model: labels[k] || k, elasticity: v.elasticity, se: v.std_error, t: v.t_stat, r2: v.r_squared, n: v.n_obs, dw: v.durbin_watson }));
    C.dataTable(rt, [{ key: "model", label: "Specification" }, { key: "elasticity", label: "Elasticity", num: true, format: fmt.f2 }, { key: "se", label: "Std. error (HAC)", num: true, format: fmt.f2 }, { key: "t", label: "t", num: true, format: fmt.f1 }, { key: "r2", label: "R squared", num: true, format: fmt.f2 }, { key: "n", label: "Observations", num: true, format: fmt.int }, { key: "dw", label: "Durbin-Watson", num: true, format: fmt.f2 }], rows);
    if (dr && dr.coefficients) {
      const dp = C.el("div", "prose");
      dp.append(C.el("h3", null, "Unrestricted first-difference regression"));
      const co = dr.coefficients, se = dr.std_errors;
      const line = ["volume", "haul", "fleet", "supply_factor"].map((k) => `${k.replace("_", " ")}: ${d3.format("+.2f")(co[k])} (se ${d3.format(".2f")(se[k])})`).join("; ");
      dp.append(C.el("p", null, `Monthly change in the log index regressed on the contemporaneous and one-month lagged changes of the four inputs (${dr.n_obs} months, R squared ${fmt.f2(dr.r_squared)}, Durbin-Watson ${fmt.f2(dr.durbin_watson)}). Contemporaneous coefficients: ${line}. Wald test that the four coefficients share one elasticity with the signs of the identity: p = ${d3.format(".2f")(dr.common_elasticity_wald_pvalue)}.`));
      dp.append(C.el("p", null, "Monthly changes in freight rates are noisy and the inputs are measured with error (customs timing, estimated distances, interpolated fleet), so the monthly regression explains a small share of the variance; it is a check on signs and relative magnitudes, not the headline estimate."));
      rt.after(dp);
    }
    const L = $("#limitations-prose");
    L.innerHTML = "";
    L.append(C.el("h3", null, "What the model cannot explain"));
    [
      "Sailing speed, waiting time outside ports and ballast ratios are not observable from public data; their effect on effective supply sits in the residual together with bunker prices, sentiment and contract timing.",
      "The index is a composite of timecharter averages whose composition changed in 2018 (Handysize removed, weights 40/30/30 for Capesize, Panamax and Supramax); the series is used as published.",
      "Covered routes are the major bulks between the main exporters and importers. Minor bulks and short-haul regional trades, which matter for Supramax and Handysize rates, are not in the tonne-mile series.",
      "Representative ports approximate real loading and discharge terminals; the error is a few percent of a voyage and is constant over time, so it affects levels far more than changes.",
      "Customs months record declarations, not sailings; a shipment loaded late in a month can be declared in the next. Three-month averaging at the endpoints and twelve-month windows reduce but do not remove this timing noise.",
      "China publishes monthly trade detail to UN Comtrade with a lag of more than a year, so Chinese import records are used only for cross-checks; the panel relies on exporter-side data for flows into China.",
      "The freight index is taken from the Aland statistics office, which republishes the monthly series; it is updated less often than the demand data, so the balanced panel ends earlier than the volume series.",
    ].forEach((t) => L.append(C.el("p", null, t)));
  }

  /* ---------------------------------------------------------------- downloads */
  function renderDownloads() {
    const list = $("#downloads-list");
    list.innerHTML = "";
    const pdf = C.el("div", "download featured");
    const a = C.el("a", null, "Tonne-Mile-Demand-Model-Research-Note.pdf");
    a.href = "downloads/Tonne-Mile-Demand-Model-Research-Note.pdf";
    pdf.append(a, C.el("p", null, "Research note (PDF, 3 to 5 pages) generated from the same pipeline."));
    list.append(pdf);
    D.summary.downloads.forEach((d) => {
      const card = C.el("div", "download");
      const link = C.el("a", null, d.file);
      link.href = `downloads/${d.file}`;
      card.append(link, C.el("p", null, d.description), C.el("p", "size", `${d3.format(",.0f")(Math.max(1, d.bytes / 1024))} KB`));
      list.append(card);
    });
    const repo = C.el("div", "download");
    const r = C.el("a", null, "Repository: pipeline code, raw data and metadata records");
    r.href = "https://github.com/nathancouturier/Tonne-Mile-Demand-Model";
    repo.append(r, C.el("p", null, "Raw downloads, metadata (source URL, retrieval date, limitations) and the code for every stage."));
    list.append(repo);
  }

  /* ---------------------------------------------------------------- views */
  const RENDERERS = { overview: renderOverview, decomposition: renderDecomposition, routes: renderRoutes, fleet: renderFleet, methodology: renderMethodology, downloads: renderDownloads };

  async function showView(name, push) {
    const buttons = document.querySelectorAll(".view-buttons button");
    const panels = document.querySelectorAll(".panel");
    if (!RENDERERS[name]) name = null;
    buttons.forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === name)));
    panels.forEach((p) => { p.hidden = p.id !== `panel-${name}`; });
    state.view = name;
    if (name && !state.rendered.has(name)) {
      try { await RENDERERS[name](); state.rendered.add(name); } catch (e) { console.error(e); $(`#panel-${name}`).append(C.el("p", "empty-note", `This section could not be rendered: ${e.message}`)); }
    }
    if (push) history.replaceState(null, "", name ? `#${name}` : location.pathname);
    if (name) $(`#panel-${name}`).scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function main() {
    try {
      const names = ["summary", "series", "rolling", "episodes", "routes", "fleet", "portwatch", "model", "validation", "sources"];
      const loaded = await Promise.all(names.map(loadJSON));
      names.forEach((n, i) => { D[n] = loaded[i]; });
      D.land = await (await fetch("assets/vendor/land-110m.json")).json();
    } catch (e) {
      console.error(e);
      text("#meta-line", "The data files for this page could not be loaded. Run the pipeline (make all) to generate them.");
      return;
    }
    renderTiles();
    renderHeadline();
    renderToday();
    document.querySelectorAll(".view-buttons button").forEach((b) => b.addEventListener("click", () => showView(state.view === b.dataset.view ? null : b.dataset.view, true)));
    const hash = location.hash.replace("#", "");
    if (hash && RENDERERS[hash]) showView(hash, false);
    window.addEventListener("hashchange", () => { const h = location.hash.replace("#", ""); if (RENDERERS[h]) showView(h, false); });
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", () => { state.rendered.clear(); renderHeadline(); if (state.view) { const v = state.view; state.view = null; showView(v, false); } });
  }
  document.addEventListener("DOMContentLoaded", main);
})();
