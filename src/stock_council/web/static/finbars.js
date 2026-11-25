// Financials as small bar charts (Yearly / Quarterly): one chart per measure, one scale each (no dual
// axes). Hover, tap or focus a bar for its exact value and the change from the period before.
// The latest value is labelled directly. Bars grow in once; reduced motion turns that off.
window.finbars = function (box) {
  if (!box || box.dataset.ready) return;
  box.dataset.ready = '1';
  const D = JSON.parse(box.dataset.fin || '{}');
  const cur = box.dataset.cur || '', unit = box.dataset.unit || '';
  const body = box.querySelector('.fin-body'), tip = box.querySelector('.fin-tip');
  let view = (() => { try { return localStorage.getItem('fin:view') || 'year'; } catch (e) { return 'year'; } })();
  if (!(D[view === 'health' ? 'year' : view] || []).length) view = (D.year || []).length ? 'year' : 'quarter';

  const money = v => v == null ? '–' : (v < 0 ? '−' : '') + cur + Math.abs(v).toLocaleString(undefined, {maximumFractionDigits: Math.abs(v) >= 100 ? 0 : 1}) + (unit ? ' ' + unit : '');
  const KIND = {cash_ratio: v => Math.round(v) + '%', de: v => v.toFixed(2), cycle: v => Math.round(v) + ' days'};
  let fmt = money;
  const label = (k, t) => k !== 'quarter' ? 'FY' + t.slice(2, 4) : new Date(t + 'T00:00:00').toLocaleDateString(undefined, {month: 'short', year: '2-digit'});

  function bars(title, rows, key, ref) {
    fmt = KIND[key] || money;
    const W = 260, H = 120, T = 18, Bm = 20, pad = 6;
    const vals = rows.map(r => r[key]);
    const known = vals.filter(v => v != null);
    if (!known.length) return '';
    const max = Math.max(0, ...known), min = Math.min(0, ...known), span = (max - min) || 1;
    const y = v => T + (H - T - Bm) * (max - v) / span, zero = y(0);
    const n = rows.length, slot = (W - pad * 2) / n, bw = Math.min(28, slot * 0.62);
    let g = `<line x1="0" x2="${W}" y1="${zero}" y2="${zero}" class="fz"/>`;
    if (ref && ref.v >= min && ref.v <= max) g += `<line x1="0" x2="${W}" y1="${y(ref.v)}" y2="${y(ref.v)}" class="fref"/>`;
    rows.forEach((r, i) => {
      const v = r[key], x = pad + i * slot + (slot - bw) / 2;
      g += `<text x="${x + bw / 2}" y="${H - 4}" text-anchor="middle" class="fl">${label(view, r.t)}</text>`;
      if (v == null) return;
      const top = Math.min(y(v), zero), h = Math.max(1.5, Math.abs(zero - y(v))), neg = v < 0;
      const prev = i ? vals[i - 1] : null;
      const ch = prev != null && prev !== 0 && v != null ? (v - prev) / Math.abs(prev) * 100 : null;
      g += `<g class="fb${neg && !KIND[key] ? ' neg' : ''}${i === n - 1 ? ' last' : ''}" tabindex="0" data-tip="${label(view, r.t)}|${fmt(v)}|${ch == null ? '' : (ch >= 0 ? '+' : '−') + Math.abs(ch).toFixed(0) + '% vs before'}">
        <rect class="hit" x="${pad + i * slot}" y="${T - 14}" width="${slot}" height="${H - T + 14 - Bm}"/>
        <rect class="bar" x="${x}" y="${top}" width="${bw}" height="${h}" rx="3" style="transform-origin:${x + bw / 2}px ${zero}px"/></g>`;
      if (i === n - 1) g += `<text x="${x + bw / 2}" y="${neg ? zero - 5 : top - 5}" text-anchor="middle" class="fv">${unit ? fmt(v).replace(' ' + unit, '') : fmt(v)}</text>`;
    });
    return `<figure class="fchart"><figcaption>${title}${ref ? ` <span class="faint">· line at ${ref.label}</span>` : ''}</figcaption><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${title} by ${view === 'quarter' ? 'quarter' : 'year'}">${g}</svg></figure>`;
  }

  function render() {
    box.querySelectorAll('[data-v]').forEach(b => b.classList.toggle('on', b.dataset.v === view));
    const SETS = {
      year: [['Sales', 'sales'], ['Net profit', 'net_profit'], ['Operating cash flow', 'operating_cash_flow'], ['Free cash flow', 'free_cash_flow']],
      quarter: [['Sales', 'sales'], ['Net profit', 'net_profit']],
      health: [['Profit turning into cash', 'cash_ratio', {v: 80, label: '80% healthy'}], ['Debt / equity', 'de', {v: 1, label: '1.0'}],
               ['Cash cycle (days)', 'cycle', {v: 60, label: '60 d'}]],
    };
    const sets = SETS[view];
    const src = view === 'health' ? 'year' : view;
    const rows = (D[src] || []).filter(r => sets.some(([, k]) => r[k] != null)).slice().reverse();   // oldest first; empty periods dropped
    body.innerHTML = sets.map(([t, k, ref]) => bars(t, rows, k, ref)).join('') || '<p class="muted small">No figures for this company yet.</p>';
  }
  box.addEventListener('click', e => {
    const b = e.target.closest('[data-v]');
    if (b) { view = b.dataset.v; try { localStorage.setItem('fin:view', view); } catch (err) {} render(); }
  });
  function show(g) {
    const [p, v, c] = g.dataset.tip.split('|');
    tip.innerHTML = `<b>${v}</b><span>${p}${c ? ' · ' + c : ''}</span>`;
    tip.hidden = false;
    const r = g.querySelector('.bar').getBoundingClientRect(), o = box.getBoundingClientRect();
    tip.style.left = Math.min(o.width - tip.offsetWidth - 8, Math.max(8, r.left - o.left + r.width / 2 - tip.offsetWidth / 2)) + 'px';
    tip.style.top = (r.top - o.top - tip.offsetHeight - 8) + 'px';
  }
  box.addEventListener('pointerover', e => { const g = e.target.closest('.fb'); if (g) show(g); });
  box.addEventListener('focusin', e => { const g = e.target.closest('.fb'); if (g) show(g); });
  box.addEventListener('pointerleave', () => { tip.hidden = true; });
  box.addEventListener('focusout', () => { tip.hidden = true; });
  render();
};
window.finbars(document.getElementById('fin'));
