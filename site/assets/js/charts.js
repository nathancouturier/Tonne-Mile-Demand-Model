/* Chart components built on D3: line charts with crosshair tooltips, stacked
   bars, waterfalls, horizontal bars and a route map. Every component draws
   into a .chart-card, fills its title, subtitle, source line and a data table
   twin, and re-renders on resize. No external dependencies beyond D3 and
   topojson-client, which are vendored. */
(function (global) {
  "use strict";

  const fmt = {
    int: d3.format(",.0f"),
    f1: d3.format(",.1f"),
    f2: d3.format(",.2f"),
    pct0: d3.format("+.0%"),
    pct1: d3.format("+.1%"),
    pctPlain: d3.format(".0%"),
    pctSigned: (v) => d3.format("+.0f")(v) + "%",
    lp: (v) => (v >= 0 ? "+" : "") + d3.format(".2f")(v),
    month: (m) => {
      const [y, mo] = String(m).split("-");
      const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
      return names[+mo - 1] + " " + y;
    },
    monthShort: (m) => {
      const [y, mo] = String(m).split("-");
      return mo === "01" ? y : "";
    },
  };

  function monthToDate(m) {
    const [y, mo] = String(m).split("-").map(Number);
    return new Date(Date.UTC(y, mo - 1, 1));
  }
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  const palette = () => ({
    s1: cssVar("--s1"), s2: cssVar("--s2"), s3: cssVar("--s3"), s4: cssVar("--s4"),
    s5: cssVar("--s5"), s6: cssVar("--s6"), s7: cssVar("--s7"), s8: cssVar("--s8"),
    neutral: cssVar("--neutral"), fitted: cssVar("--fitted"), accent: cssVar("--accent"), ink: cssVar("--ink"),
  });

  /* Card scaffold: ensures head/body/foot/table nodes exist and returns them. */
  function card(container, opts) {
    const root = typeof container === "string" ? document.querySelector(container) : container;
    root.innerHTML = "";
    const head = el("figcaption", "chart-head");
    const title = el("h3", "chart-title", opts.title || "");
    const sub = el("p", "chart-sub", opts.subtitle || "");
    head.append(title, sub);
    const legend = el("div", "legend");
    const body = el("div", "chart-body");
    const foot = el("div", "chart-foot");
    const source = el("p", "source", opts.source || "");
    const toggle = el("button", "table-toggle", "Show data table");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    foot.append(source, toggle);
    const table = el("div", "chart-table");
    table.hidden = true;
    root.append(head, legend, body, foot, table);
    toggle.addEventListener("click", () => {
      const open = table.hidden;
      table.hidden = !open;
      toggle.textContent = open ? "Hide data table" : "Show data table";
      toggle.setAttribute("aria-expanded", String(open));
    });
    return { root, title, sub, legend, body, source, table };
  }

  function dataTable(node, columns, rows, maxRows) {
    node.innerHTML = "";
    const t = el("table");
    const thead = el("thead");
    const tr = el("tr");
    columns.forEach((c) => {
      const th = el("th", c.num ? "num" : "", c.label);
      tr.append(th);
    });
    thead.append(tr);
    const tbody = el("tbody");
    const slice = maxRows ? rows.slice(-maxRows) : rows;
    slice.forEach((r) => {
      const trr = el("tr");
      columns.forEach((c) => {
        const v = r[c.key];
        const td = el("td", c.num ? "num" : "", v === null || v === undefined || Number.isNaN(v) ? "" : c.format ? c.format(v) : String(v));
        trr.append(td);
      });
      tbody.append(trr);
    });
    t.append(thead, tbody);
    node.append(t);
    if (maxRows && rows.length > maxRows) node.append(el("p", "source", `Showing the last ${maxRows} of ${rows.length} rows; the full series is in the downloads.`));
  }

  function legend(node, items) {
    node.innerHTML = "";
    items.forEach((it) => {
      const k = el("span", "key");
      const sw = el("span", "swatch" + (it.rect ? " rect" : "") + (it.dashed ? " dashed" : ""));
      sw.style.background = it.color;
      if (it.dashed) sw.style.color = it.color;
      k.append(sw, document.createTextNode(it.label));
      node.append(k);
    });
  }

  function tooltip(body) {
    const tt = el("div", "tooltip");
    tt.style.display = "none";
    body.append(tt);
    return {
      show(x, y, html) {
        tt.innerHTML = "";
        tt.append(html);
        tt.style.display = "block";
        const bw = body.clientWidth;
        const w = tt.offsetWidth;
        tt.style.left = Math.min(Math.max(0, x + 14), bw - w - 2) + "px";
        tt.style.top = Math.max(0, y - 10) + "px";
      },
      hide() { tt.style.display = "none"; },
    };
  }
  function ttContent(head, rows) {
    const frag = document.createDocumentFragment();
    frag.append(el("div", "tt-head", head));
    rows.forEach((r) => {
      const row = el("div", "tt-row");
      const left = el("span");
      if (r.color) {
        const key = el("span", "tt-key");
        key.style.background = r.color;
        left.append(key);
      }
      left.append(document.createTextNode(r.label));
      row.append(left, el("span", "tt-val", r.value));
      frag.append(row);
    });
    return frag;
  }

  function responsive(body, draw) {
    let raf = null;
    const ro = new ResizeObserver(() => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => draw(body.clientWidth));
    });
    ro.observe(body);
    draw(body.clientWidth);
  }

  /* Line chart. series: [{key, label, color, dashed, values:[{x: Date, y}]}], yScale: 'linear'|'log' */
  function lineChart(container, opts) {
    const c = card(container, opts);
    const p = palette();
    const series = opts.series;
    legend(c.legend, series.length > 1 ? series.map((s) => ({ label: s.label, color: s.color, dashed: s.dashed })) : []);
    const tt = tooltip(c.body);
    const height = opts.height || 300;
    const margin = { top: opts.yLabel ? 26 : 12, right: 18, bottom: 34, left: 56 };
    const allX = series.flatMap((s) => s.values.map((v) => v.x));
    const xDomain = d3.extent(allX);
    const draw = (width) => {
      c.body.querySelectorAll("svg").forEach((n) => n.remove());
      const w = Math.max(280, width);
      const svg = d3.select(c.body).insert("svg", ":first-child").attr("viewBox", `0 0 ${w} ${height}`).attr("role", "img").attr("aria-label", opts.title);
      const iw = w - margin.left - margin.right;
      const ih = height - margin.top - margin.bottom;
      const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);
      const x = d3.scaleUtc().domain(xDomain).range([0, iw]);
      const ys = series.flatMap((s) => s.values.map((v) => v.y)).filter((v) => v !== null && Number.isFinite(v));
      let y;
      if (opts.yScale === "log") {
        y = d3.scaleLog().domain([d3.min(ys) * 0.9, d3.max(ys) * 1.1]).range([ih, 0]).nice();
      } else {
        const lo = opts.yZero ? 0 : d3.min(ys);
        const hi = d3.max(ys);
        const pad = (hi - lo) * 0.08;
        y = d3.scaleLinear().domain([opts.yZero ? 0 : lo - pad, hi + pad]).range([ih, 0]).nice();
      }
      g.append("g").attr("class", "grid").call(d3.axisLeft(y).ticks(5).tickSize(-iw).tickFormat(""));
      g.append("g").attr("class", "axis").attr("transform", `translate(0,${ih})`).call(d3.axisBottom(x).ticks(Math.min(10, Math.floor(iw / 90))).tickSizeOuter(0));
      g.append("g").attr("class", "axis").call(d3.axisLeft(y).ticks(5, opts.yFormat || "~s").tickSizeOuter(0));
      if (opts.yLabel) svg.append("text").attr("class", "axis-label").attr("x", 4).attr("y", 10).text(opts.yLabel);
      if (opts.bands) {
        opts.bands.forEach((b) => {
          g.append("rect").attr("x", x(b.from)).attr("width", Math.max(0, x(b.to) - x(b.from))).attr("y", 0).attr("height", ih).attr("fill", p.neutral).attr("opacity", 0.08);
          g.append("text").attr("class", "annotation").attr("x", x(b.from) + 4).attr("y", 12).text(b.label);
        });
      }
      const line = d3.line().defined((d) => d.y !== null && Number.isFinite(d.y)).x((d) => x(d.x)).y((d) => y(d.y));
      if (opts.area && series.length === 2) {
        const a = series[0].values, b = series[1].values;
        const area = d3.area().defined((d, i) => d.y !== null && b[i] && b[i].y !== null).x((d) => x(d.x)).y0((d) => y(d.y)).y1((d, i) => y(b[i].y));
        g.append("path").datum(a).attr("fill", p.neutral).attr("opacity", 0.12).attr("d", area);
      }
      series.forEach((s) => {
        g.append("path").datum(s.values).attr("fill", "none").attr("stroke", s.color).attr("stroke-width", s.width || 2).attr("stroke-linejoin", "round").attr("stroke-linecap", "round").attr("stroke-dasharray", s.dashed ? "4 4" : null).attr("d", line);
      });
      // crosshair + tooltip
      const cross = g.append("line").attr("class", "crosshair").attr("y1", 0).attr("y2", ih).style("display", "none");
      const dots = g.append("g");
      const bisect = d3.bisector((d) => d.x).center;
      const overlay = g.append("rect").attr("width", iw).attr("height", ih).attr("fill", "transparent");
      const move = (ev) => {
        const [mx, my] = d3.pointer(ev, g.node());
        const x0 = x.invert(mx);
        const i = bisect(series[0].values, x0);
        const xi = series[0].values[i] && series[0].values[i].x;
        if (!xi) return;
        cross.style("display", null).attr("x1", x(xi)).attr("x2", x(xi));
        dots.selectAll("circle").remove();
        const rows = [];
        series.forEach((s) => {
          const d = s.values[i];
          if (d && d.y !== null && Number.isFinite(d.y)) {
            dots.append("circle").attr("cx", x(d.x)).attr("cy", y(d.y)).attr("r", 4).attr("fill", s.color).attr("stroke", cssVar("--surface")).attr("stroke-width", 2);
            rows.push({ label: s.label, value: (s.format || opts.valueFormat || fmt.f1)(d.y), color: s.color });
          }
        });
        tt.show(mx + margin.left, my, ttContent(opts.xFormat ? opts.xFormat(xi) : d3.utcFormat("%b %Y")(xi), rows));
      };
      overlay.on("pointermove", move).on("pointerleave", () => { cross.style("display", "none"); dots.selectAll("circle").remove(); tt.hide(); });
    };
    responsive(c.body, draw);
    // table
    const xs = series[0].values.map((v) => v.x);
    const rows = xs.map((xv, i) => {
      const r = { x: opts.xFormat ? opts.xFormat(xv) : d3.utcFormat("%Y-%m")(xv) };
      series.forEach((s) => { r[s.key] = s.values[i] ? s.values[i].y : null; });
      return r;
    });
    dataTable(c.table, [{ key: "x", label: opts.xLabel || "Month" }, ...series.map((s) => ({ key: s.key, label: s.label, num: true, format: s.format || opts.valueFormat || fmt.f1 }))], rows, opts.tableRows || 60);
    return c;
  }

  /* Stacked bars (positive and negative) with an optional total line. */
  function stackedBars(container, opts) {
    const c = card(container, opts);
    const p = palette();
    const comps = opts.components; // [{key,label,color}]
    legend(c.legend, [...comps.map((k) => ({ label: k.label, color: k.color, rect: true })), ...(opts.total ? [{ label: opts.total.label, color: opts.total.color }] : [])]);
    const tt = tooltip(c.body);
    const height = opts.height || 320;
    const margin = { top: opts.yLabel ? 26 : 12, right: 18, bottom: 34, left: 56 };
    const data = opts.data; // [{x: Date, label, comps..., total}]
    const draw = (width) => {
      c.body.querySelectorAll("svg").forEach((n) => n.remove());
      const w = Math.max(280, width);
      const svg = d3.select(c.body).insert("svg", ":first-child").attr("viewBox", `0 0 ${w} ${height}`).attr("role", "img").attr("aria-label", opts.title);
      const iw = w - margin.left - margin.right, ih = height - margin.top - margin.bottom;
      const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);
      const x = d3.scaleUtc().domain(d3.extent(data, (d) => d.x)).range([0, iw]);
      const pos = data.map((d) => comps.reduce((s, k) => s + Math.max(0, d[k.key] || 0), 0));
      const neg = data.map((d) => comps.reduce((s, k) => s + Math.min(0, d[k.key] || 0), 0));
      const tot = opts.total ? data.map((d) => d[opts.total.key]) : [];
      const yMax = d3.max([...pos, ...tot, 0.05]), yMin = d3.min([...neg, ...tot, -0.05]);
      const y = d3.scaleLinear().domain([yMin * 1.08, yMax * 1.08]).range([ih, 0]).nice();
      g.append("g").attr("class", "grid").call(d3.axisLeft(y).ticks(6).tickSize(-iw).tickFormat(""));
      g.append("g").attr("class", "axis").attr("transform", `translate(0,${ih})`).call(d3.axisBottom(x).ticks(Math.min(10, Math.floor(iw / 90))).tickSizeOuter(0));
      g.append("g").attr("class", "axis").call(d3.axisLeft(y).ticks(6).tickSizeOuter(0));
      if (opts.yLabel) svg.append("text").attr("class", "axis-label").attr("x", 4).attr("y", 10).text(opts.yLabel);
      g.append("line").attr("x1", 0).attr("x2", iw).attr("y1", y(0)).attr("y2", y(0)).attr("stroke", cssVar("--axis"));
      const bw = Math.max(1, (iw / data.length) * 0.8);
      data.forEach((d) => {
        let up = 0, down = 0;
        comps.forEach((k) => {
          const v = d[k.key] || 0;
          if (!v) return;
          const y0 = v > 0 ? up : down, y1 = y0 + v;
          g.append("rect").attr("x", x(d.x) - bw / 2).attr("width", bw).attr("y", Math.min(y(y0), y(y1))).attr("height", Math.max(0, Math.abs(y(y1) - y(y0)) - (bw > 4 ? 1 : 0))).attr("fill", k.color);
          if (v > 0) up = y1; else down = y1;
        });
      });
      if (opts.total) {
        const line = d3.line().x((d) => x(d.x)).y((d) => y(d[opts.total.key]));
        g.append("path").datum(data).attr("fill", "none").attr("stroke", opts.total.color).attr("stroke-width", 2).attr("d", line);
      }
      const bisect = d3.bisector((d) => d.x).center;
      const cross = g.append("line").attr("class", "crosshair").attr("y1", 0).attr("y2", ih).style("display", "none");
      g.append("rect").attr("width", iw).attr("height", ih).attr("fill", "transparent")
        .on("pointermove", (ev) => {
          const [mx, my] = d3.pointer(ev, g.node());
          const i = bisect(data, x.invert(mx));
          const d = data[i]; if (!d) return;
          cross.style("display", null).attr("x1", x(d.x)).attr("x2", x(d.x));
          const rows = comps.map((k) => ({ label: k.label, value: fmt.lp(d[k.key] || 0), color: k.color }));
          if (opts.total) rows.push({ label: opts.total.label, value: fmt.lp(d[opts.total.key]), color: opts.total.color });
          tt.show(mx + margin.left, my, ttContent(d.label, rows));
        }).on("pointerleave", () => { cross.style("display", "none"); tt.hide(); });
    };
    responsive(c.body, draw);
    dataTable(c.table, [{ key: "label", label: "Window end" }, ...comps.map((k) => ({ key: k.key, label: k.label, num: true, format: fmt.lp })), ...(opts.total ? [{ key: opts.total.key, label: opts.total.label, num: true, format: fmt.lp }] : [])], data, 60);
    return c;
  }

  /* Waterfall: steps [{label, value, color, kind:'start'|'step'|'end'}], values in log points; axis in index points via opts.toIndex */
  function waterfall(container, opts) {
    const c = card(container, opts);
    const p = palette();
    legend(c.legend, opts.steps.filter((s) => s.kind === "step").map((s) => ({ label: s.label, color: s.color, rect: true })));
    const tt = tooltip(c.body);
    const height = opts.height || 340;
    const margin = { top: 24, right: 18, bottom: 70, left: 60 };
    const steps = opts.steps;
    const draw = (width) => {
      c.body.querySelectorAll("svg").forEach((n) => n.remove());
      const w = Math.max(280, width);
      const svg = d3.select(c.body).insert("svg", ":first-child").attr("viewBox", `0 0 ${w} ${height}`).attr("role", "img").attr("aria-label", opts.title);
      const iw = w - margin.left - margin.right, ih = height - margin.top - margin.bottom;
      const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);
      const x = d3.scaleBand().domain(steps.map((s) => s.label)).range([0, iw]).padding(0.3);
      // cumulative levels in index points
      let level = steps[0].value; // start level (index points)
      const bars = steps.map((s, i) => {
        if (s.kind === "start") return { ...s, y0: 0, y1: s.value, level: s.value };
        if (s.kind === "end") return { ...s, y0: 0, y1: s.value, level: s.value };
        const next = level * Math.exp(s.value);
        const b = { ...s, y0: Math.min(level, next), y1: Math.max(level, next), from: level, to: next };
        level = next;
        return b;
      });
      const yMax = d3.max(bars, (b) => b.y1) * 1.1;
      const y = d3.scaleLinear().domain([0, yMax]).range([ih, 0]).nice();
      g.append("g").attr("class", "grid").call(d3.axisLeft(y).ticks(6).tickSize(-iw).tickFormat(""));
      g.append("g").attr("class", "axis").call(d3.axisLeft(y).ticks(6, "~s").tickSizeOuter(0));
      const xa = g.append("g").attr("class", "axis").attr("transform", `translate(0,${ih})`).call(d3.axisBottom(x).tickSizeOuter(0));
      xa.selectAll("text").attr("transform", "rotate(-20)").style("text-anchor", "end").attr("dy", "0.9em").attr("dx", "-0.3em");
      svg.append("text").attr("class", "axis-label").attr("x", 4).attr("y", 12).text(opts.yLabel || "Index points");
      bars.forEach((b, i) => {
        const h = Math.max(1.5, y(b.y0) - y(b.y1));
        g.append("rect").attr("x", x(b.label)).attr("width", x.bandwidth()).attr("y", y(b.y1)).attr("height", h).attr("fill", b.color).attr("rx", 2)
          .on("pointermove", (ev) => {
            const [mx, my] = d3.pointer(ev, g.node());
            const rows = b.kind === "step" ? [{ label: "Contribution (log points)", value: fmt.lp(b.value) }, { label: "Share of move", value: Number.isFinite(b.share) ? fmt.pct0(b.share) : "n/a" }, { label: "From", value: fmt.int(b.from) }, { label: "To", value: fmt.int(b.to) }] : [{ label: "Index level", value: fmt.int(b.value) }];
            tt.show(mx + margin.left, my, ttContent(b.label, rows));
          }).on("pointerleave", () => tt.hide());
        if (i < bars.length - 1) {
          const nextLevel = b.kind === "step" ? b.to : b.value;
          g.append("line").attr("x1", x(b.label) + x.bandwidth()).attr("x2", x(bars[i + 1].label)).attr("y1", y(nextLevel)).attr("y2", y(nextLevel)).attr("stroke", cssVar("--axis")).attr("stroke-dasharray", "2 3");
        }
        const label = b.kind === "step" ? fmt.lp(b.value) : fmt.int(b.value);
        g.append("text").attr("class", "annotation").attr("x", x(b.label) + x.bandwidth() / 2).attr("y", y(b.y1) - 5).attr("text-anchor", "middle").style("fill", cssVar("--ink")).text(label);
      });
    };
    responsive(c.body, draw);
    dataTable(c.table, [{ key: "label", label: "Step" }, { key: "value", label: "Value (log points, or index level for start and end)", num: true, format: (v) => d3.format(",.3f")(v) }, { key: "share", label: "Share of total move", num: true, format: (v) => (Number.isFinite(v) ? fmt.pct0(v) : "") }], steps.map((s) => ({ label: s.label, value: s.value, share: s.share })));
    return c;
  }

  /* Horizontal bars: rows [{label, value, color, sub}] */
  function hbars(container, opts) {
    const c = card(container, opts);
    const rows = opts.rows;
    const rowH = 26;
    const margin = { top: 6, right: 70, bottom: 26, left: 8 };
    const labelW = opts.labelWidth || 230;
    const height = margin.top + margin.bottom + rows.length * rowH;
    const tt = tooltip(c.body);
    if (opts.legend) legend(c.legend, opts.legend);
    const draw = (width) => {
      c.body.querySelectorAll("svg").forEach((n) => n.remove());
      const w = Math.max(300, width);
      const lw = w < 520 ? 130 : labelW;
      const svg = d3.select(c.body).insert("svg", ":first-child").attr("viewBox", `0 0 ${w} ${height}`).attr("role", "img").attr("aria-label", opts.title);
      const iw = w - margin.left - margin.right - lw;
      const g = svg.append("g").attr("transform", `translate(${margin.left + lw},${margin.top})`);
      const x = d3.scaleLinear().domain([0, d3.max(rows, (r) => r.value) * 1.05]).range([0, iw]);
      const y = d3.scaleBand().domain(rows.map((r) => r.label)).range([0, rows.length * rowH]).padding(0.25);
      g.append("g").attr("class", "axis").attr("transform", `translate(0,${rows.length * rowH})`).call(d3.axisBottom(x).ticks(5, opts.xFormat || "~s").tickSizeOuter(0));
      rows.forEach((r) => {
        g.append("rect").attr("x", 0).attr("y", y(r.label)).attr("width", Math.max(0, x(r.value))).attr("height", y.bandwidth()).attr("fill", r.color).attr("rx", 2)
          .on("pointermove", (ev) => { const [mx, my] = d3.pointer(ev, g.node()); tt.show(mx + margin.left + lw, my, ttContent(r.label, [{ label: opts.valueLabel || "Value", value: (opts.valueFormat || fmt.f1)(r.value) }, ...(r.sub ? [{ label: r.subLabel || "", value: r.sub }] : [])])); }).on("pointerleave", () => tt.hide());
        g.append("text").attr("class", "annotation").attr("x", -8).attr("y", y(r.label) + y.bandwidth() / 2 + 4).attr("text-anchor", "end").style("fill", cssVar("--ink")).text(w < 520 && r.short ? r.short : r.label);
        g.append("text").attr("class", "annotation").attr("x", x(r.value) + 6).attr("y", y(r.label) + y.bandwidth() / 2 + 4).text((opts.valueFormat || fmt.f1)(r.value));
      });
    };
    responsive(c.body, draw);
    dataTable(c.table, [{ key: "label", label: opts.rowLabel || "Row" }, { key: "value", label: opts.valueLabel || "Value", num: true, format: opts.valueFormat || fmt.f1 }, ...(opts.extraColumns || [])], rows);
    return c;
  }

  /* Route map with an equal-earth projection; routes [{id, geometry:[[lon,lat]], color, width, label, sub}] */
  function routeMap(container, opts) {
    const c = card(container, opts);
    const tt = tooltip(c.body);
    const land = opts.land;
    const routes = opts.routes;
    if (opts.legend) legend(c.legend, opts.legend);
    const draw = (width) => {
      c.body.querySelectorAll("svg").forEach((n) => n.remove());
      const w = Math.max(300, width);
      const h = Math.round(w * 0.52);
      const svg = d3.select(c.body).insert("svg", ":first-child").attr("viewBox", `0 0 ${w} ${h}`).attr("role", "img").attr("aria-label", opts.title);
      const proj = d3.geoEqualEarth().rotate([-150, 0]).fitExtent([[4, 4], [w - 4, h - 4]], { type: "Sphere" });
      const path = d3.geoPath(proj);
      svg.append("path").datum({ type: "Sphere" }).attr("fill", cssVar("--surface")).attr("stroke", cssVar("--rule")).attr("d", path);
      svg.append("path").datum(d3.geoGraticule10()).attr("class", "map-graticule").attr("d", path);
      svg.append("path").datum(land).attr("class", "map-land").attr("d", path);
      const rg = svg.append("g");
      routes.forEach((r) => {
        if (!r.geometry || r.geometry.length < 2) return;
        // split at the antimeridian by letting d3 handle a MultiLineString of segments
        const segs = [];
        let cur = [r.geometry[0]];
        for (let i = 1; i < r.geometry.length; i++) {
          const a = r.geometry[i - 1], b = r.geometry[i];
          if (Math.abs(a[0] - b[0]) > 180) { segs.push(cur); cur = [b]; } else cur.push(b);
        }
        segs.push(cur);
        rg.append("path").datum({ type: "MultiLineString", coordinates: segs }).attr("class", "map-route").attr("stroke", r.color).attr("stroke-width", r.width).attr("d", path)
          .on("pointermove", (ev) => { const [mx, my] = d3.pointer(ev, svg.node()); tt.show(mx, my, ttContent(r.label, r.rows || [])); })
          .on("pointerleave", () => tt.hide());
      });
      const pg = svg.append("g");
      (opts.ports || []).forEach((pt) => {
        const xy = proj([pt.lon, pt.lat]);
        if (!xy) return;
        pg.append("circle").attr("class", "map-port").attr("cx", xy[0]).attr("cy", xy[1]).attr("r", 2.6)
          .on("pointermove", (ev) => { const [mx, my] = d3.pointer(ev, svg.node()); tt.show(mx, my, ttContent(pt.name, [{ label: pt.country, value: "" }])); })
          .on("pointerleave", () => tt.hide());
      });
    };
    responsive(c.body, draw);
    dataTable(c.table, opts.tableColumns, opts.tableRows);
    return c;
  }

  global.Charts = { fmt, monthToDate, lineChart, stackedBars, waterfall, hbars, routeMap, card, dataTable, legend, palette, el };
})(window);
