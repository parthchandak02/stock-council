// "Who owns it": 12 quarters of shareholding like Screener's, as a graph (lines or stacked) and a table.
// Pure public = public minus the named public holders you tick (all ticked by default).
(function () {
  const box = document.getElementById('holdings');
  if (!box) return;
  const body = box.querySelector('.hd-body');
  const B = window.B || '';
  const GROUPS = [
    {k: 'promoter', label: 'Promoters', color: 'var(--c-prom)'},
    {k: 'fii', label: 'FIIs', color: 'var(--c-fii)'},
    {k: 'dii', label: 'DIIs', color: 'var(--c-dii)'},
    {k: 'govt', label: 'Government', color: 'var(--c-govt)'},
    {k: 'pure', label: 'Pure public', color: 'var(--c-pure)'},
  ];
  const MON = {MAR: 'Mar', JUN: 'Jun', SEP: 'Sep', DEC: 'Dec'};
  let Q = [], named = {}, excluded = new Set(), hidden = new Set(), open = new Set(['public']);
  let view = 'chart', mode = 'lines', dims = {W: 640, L: 34, R: 10};

  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const f2 = v => v == null ? '–' : (+v).toFixed(2);
  const qLabel = q => `${MON[q.quarter.slice(3, 6)] || q.quarter.slice(3, 6)} ${q.quarter.slice(-4)}`;
  const title = s => s.toLowerCase().replace(/\b([a-z])/g, m => m.toUpperCase()).replace(/\b(Huf|Ltd|Pvt|Llp|Uti|Iepf)\b/g, m => m.toUpperCase());

  function prepare(quarters) {
    Q = quarters;
    named = {promoter: {}, fii: {}, dii: {}, govt: {}, public: {}};
    Q.forEach((q, i) => (q.holders || []).forEach(h => {
      const g = named[h.group] || (named[h.group] = {});
      (g[h.name] || (g[h.name] = Array(Q.length).fill(null)))[i] = h.pct;
    }));
    excluded = new Set(Object.keys(named.public));
  }
  const exclAt = i => [...excluded].reduce((s, n) => s + ((named.public[n] || [])[i] || 0), 0);
  const value = (k, i) => k === 'pure' ? (Q[i].public == null ? null : Math.max(0, Q[i].public - exclAt(i))) : Q[i][k];

  // ---------- graph ----------
  function chart() {
    const W = Math.max(300, Math.round(body.clientWidth || 640)), H = W < 500 ? 200 : 230, L = 34, R = 10, T = 10, Bm = 26;
    dims = {W, L, R};
    const n = Q.length, x = i => L + (n === 1 ? 0 : i * (W - L - R) / (n - 1));
    const shown = GROUPS.filter(g => !hidden.has(g.k));
    let ymax = 100;
    if (mode === 'lines') {
      const vals = shown.flatMap(g => Q.map((_, i) => value(g.k, i) || 0));
      ymax = Math.max(20, Math.ceil(Math.max(...vals, 0) * 1.08 / 20) * 20);   // 20, 40, 60, 80 or 100: round ticks
    }
    const y = v => T + (H - T - Bm) * (1 - v / ymax);
    let svg = `<svg viewBox="0 0 ${W} ${H}" class="hchart" role="img" aria-label="Shareholding by quarter">`;
    for (let k = 0; k <= 4; k++) {
      const v = ymax * k / 4, yy = y(v);
      svg += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" class="grid"/><text x="${L - 6}" y="${yy + 4}" class="ax" text-anchor="end">${Math.round(v)}</text>`;
    }
    const step = W < 420 ? 3 : n > 8 && W < 760 ? 2 : 1;
    Q.forEach((q, i) => {
      if ((n - 1 - i) % step) return;
      if (i !== n - 1 && x(n - 1) - x(i) < 52) return;   // keep the latest label readable
      const a = i === n - 1 ? 'end' : i === 0 ? 'start' : 'middle';
      svg += `<text x="${x(i)}" y="${H - 6}" class="ax" text-anchor="${a}">${qLabel(q).replace(' 20', " '")}</text>`;
    });
    if (mode === 'lines') {
      shown.forEach(g => {
        const pts = Q.map((_, i) => [x(i), value(g.k, i)]).filter(p => p[1] != null);
        svg += `<polyline points="${pts.map(p => `${p[0].toFixed(1)},${y(p[1]).toFixed(1)}`).join(' ')}" class="ln" style="stroke:${g.color}"/>`;
        svg += pts.map(p => `<circle cx="${p[0].toFixed(1)}" cy="${y(p[1]).toFixed(1)}" r="2.6" style="fill:${g.color}"/>`).join('');
      });
    } else {
      // stacked to 100%: the named public holders you subtract show as their own band
      const layers = [...GROUPS.slice(0, 4), {k: 'named', label: 'Named public', color: 'var(--c-named)'}, GROUPS[4]];
      let base = Q.map(() => 0);
      layers.forEach(g => {
        const v = Q.map((_, i) => (g.k === 'named' ? exclAt(i) : value(g.k, i)) || 0);
        const top = base.map((b, i) => b + v[i]);
        const d = 'M' + top.map((t, i) => `${x(i).toFixed(1)},${y(t).toFixed(1)}`).join(' L') + ' L' +
          base.map((b, i) => `${x(i).toFixed(1)},${y(b).toFixed(1)}`).reverse().join(' L') + ' Z';
        svg += `<path d="${d}" class="area" style="fill:${g.color}"/>`;
        base = top;
      });
    }
    svg += `<line class="hover" x1="0" x2="0" y1="${T}" y2="${H - Bm}" style="display:none"/></svg>`;
    const legend = (mode === 'lines' ? GROUPS : [...GROUPS.slice(0, 4), {k: 'named', label: 'Named public', color: 'var(--c-named)'}, GROUPS[4]])
      .map(g => `<button type="button" class="lg ${hidden.has(g.k) ? 'off' : ''}" data-k="${g.k}" ${mode === 'stacked' ? 'disabled' : ''}><i style="background:${g.color}"></i>${g.label}</button>`).join('');
    return `<div class="hc-top"><div class="seg sm"><button type="button" data-mode="lines" class="${mode === 'lines' ? 'on' : ''}">Lines</button><button type="button" data-mode="stacked" class="${mode === 'stacked' ? 'on' : ''}">Stacked</button></div>
      <div class="legend">${legend}</div></div><div class="hc-wrap">${svg}<div class="tip" hidden></div></div>
      <p class="muted small hc-note">Pure public subtracts ${excluded.size ? `${excluded.size} named holder${excluded.size > 1 ? 's' : ''}` : 'nothing'}; change it in the Table.</p>`;
  }

  // ---------- table ----------
  function table() {
    const head = `<tr><th></th>${Q.map(q => `<th>${qLabel(q)}</th>`).join('')}</tr>`;
    const row = (label, vals, cls = '', lead = '') => `<tr class="${cls}"><td>${lead}${label}</td>${vals.map(v => `<td>${v}</td>`).join('')}</tr>`;
    let rows = '';
    [['promoter', 'Promoters'], ['fii', 'FIIs'], ['dii', 'DIIs'], ['govt', 'Government'], ['public', 'Public']].forEach(([k, label]) => {
      const names = Object.keys(named[k] || {});
      const can = names.length > 0, isOpen = open.has(k);
      const lead = can ? `<button type="button" class="ex" data-g="${k}" aria-expanded="${isOpen}">${isOpen ? '−' : '+'}</button>` : '<span class="ex-sp"></span>';
      rows += row(label, Q.map((_, i) => `${f2(Q[i][k])}%`), 'grp' + (k === 'public' ? ' pub' : ''), lead);
      if (can && isOpen) names.sort((a, b) => Math.max(...named[k][b].map(v => v || 0)) - Math.max(...named[k][a].map(v => v || 0))).forEach(nm => {
        const cb = k === 'public' ? `<input type="checkbox" class="xc" data-n="${esc(nm)}" ${excluded.has(nm) ? 'checked' : ''} aria-label="Subtract ${esc(nm)}">` : '';
        rows += row(`<span class="nm" title="${esc(nm)}">${esc(title(nm))}</span>`, named[k][nm].map(v => v == null ? '' : f2(v)),
          'nmrow' + (k === 'public' && !excluded.has(nm) ? ' kept' : ''), cb);
      });
    });
    rows += row('<b>Pure public</b>', Q.map((_, i) => `<b>${f2(value('pure', i))}%</b>`), 'pure');
    rows += row('Shareholders', Q.map(q => q.shareholders ? q.shareholders.toLocaleString('en-IN') : '–'), 'cnt');
    return `<div class="tblwrap"><table class="tbl htbl">${head}${rows}</table></div>
      <p class="muted small hc-note">Ticked holders are subtracted from Public to give <b>Pure public</b>: suspense accounts, IEPF, large individual investors and companies holding over 1%.</p>`;
  }

  function render() {
    if (!Q.length) return;
    body.innerHTML = view === 'chart' ? chart() : table();
    box.querySelectorAll('.hd-top .seg button').forEach(b => b.classList.toggle('on', b.dataset.view === view));
  }

  box.addEventListener('click', e => {
    const v = e.target.closest('[data-view]'), m = e.target.closest('[data-mode]'), lg = e.target.closest('.lg'), ex = e.target.closest('.ex');
    if (v) { view = v.dataset.view; render(); }
    else if (m) { mode = m.dataset.mode; render(); }
    else if (lg && !lg.disabled) { hidden.has(lg.dataset.k) ? hidden.delete(lg.dataset.k) : hidden.add(lg.dataset.k); render(); }
    else if (ex) { open.has(ex.dataset.g) ? open.delete(ex.dataset.g) : open.add(ex.dataset.g); render(); }
  });
  box.addEventListener('change', e => {
    const c = e.target.closest('.xc');
    if (c) { c.checked ? excluded.add(c.dataset.n) : excluded.delete(c.dataset.n); render(); }
  });
  // hover / tap: values for the nearest quarter
  box.addEventListener('pointermove', e => {
    const svg = box.querySelector('.hchart'); if (!svg) return;
    const {W, L, R} = dims, span = W - L - R;
    const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) / r.width * W;
    if (px < L) return;
    const n = Q.length, i = Math.max(0, Math.min(n - 1, Math.round((px - L) / (span / Math.max(1, n - 1)))));
    const xx = L + (n === 1 ? 0 : i * span / (n - 1));
    const ln = svg.querySelector('.hover'); ln.setAttribute('x1', xx); ln.setAttribute('x2', xx); ln.style.display = '';
    const tip = box.querySelector('.tip');
    tip.innerHTML = `<b>${qLabel(Q[i])}</b>` + GROUPS.map(g => `<span><i style="background:${g.color}"></i>${g.label}<em>${f2(value(g.k, i))}%</em></span>`).join('');
    tip.hidden = false;
    const left = (xx / W) * r.width;
    tip.style.left = (left + 180 > r.width ? Math.max(0, left - 180) : left + 10) + 'px';
  });
  box.addEventListener('pointerleave', () => { const t = box.querySelector('.tip'); if (t) t.hidden = true; const l = box.querySelector('.hover'); if (l) l.style.display = 'none'; });

  let rt; window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(render, 150); });

  fetch(`${B}/holdings/${encodeURIComponent(box.dataset.symbol)}?bse=${encodeURIComponent(box.dataset.bse)}`)
    .then(r => r.json())
    .then(d => {
      if (!d.quarters || !d.quarters.length) { body.innerHTML = '<p class="muted small">No shareholding filings found for this company.</p>'; return; }
      prepare(d.quarters); render();
    })
    .catch(() => { body.innerHTML = '<p class="muted small">Couldn\'t load the shareholding filings right now.</p>'; });
})();
