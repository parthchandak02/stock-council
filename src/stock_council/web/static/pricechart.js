// Interactive price chart (TradingView Lightweight Charts, Apache-2.0, served from /static/vendor).
// Ranges 1M..Max, line or candles, 50/200-day averages, the market index for comparison, volume with
// spike days, the council's call day, and a crosshair readout of every value at the hovered day.
// Choices are remembered on this device. Up to 1Y is our own exchange data; 3Y+ comes from Yahoo.
(function () {
  const boxes = document.querySelectorAll('.pchart[data-isin]');
  if (!boxes.length || !window.LightweightCharts) return;
  const LC = window.LightweightCharts;
  const B = window.B || '';
  const store = {
    get(k, d) { try { const v = localStorage.getItem('pc:' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('pc:' + k, JSON.stringify(v)); } catch (e) {} },
  };
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const RANGES = [['1M', 21], ['3M', 63], ['6M', 126], ['YTD', 'ytd'], ['1Y', 'all'], ['3Y', '3y'], ['5Y', '5y'], ['Max', 'max']];

  boxes.forEach(box => init(box));

  function init(box) {
    const isin = box.dataset.isin, cur = box.dataset.cur || '', call = box.dataset.call || '';
    const el = box.querySelector('.pc'), tip = box.querySelector('.pc-tip'), read = box.querySelector('.pc-read');
    const state = {
      range: store.get('range', '6M'), type: store.get('type', 'line'),
      ma50: store.get('ma50', true), ma200: store.get('ma200', false), cmp: store.get('cmp', false),
    };
    const cache = {};
    let chart, main, vol, s50, s200, sIdx, data, bars = [], byTime = new Map();

    // ---------- controls ----------
    const ctrl = box.querySelector('.pc-ctrl');
    ctrl.innerHTML = `
      <div class="seg pc-range" role="tablist" aria-label="Time range">${RANGES.map(([k]) => `<button type="button" role="tab" data-r="${k}">${k}</button>`).join('')}</div>
      <div class="pc-opts">
        <div class="seg sm" aria-label="Chart type"><button type="button" data-t="line">Line</button><button type="button" data-t="candle">Candles</button></div>
        <button type="button" class="tgl" data-k="ma50" aria-pressed="false"><i class="ln s50"></i>50-day avg</button>
        <button type="button" class="tgl" data-k="ma200" aria-pressed="false"><i class="ln s200"></i>200-day avg</button>
        <button type="button" class="tgl" data-k="cmp" aria-pressed="false"><i class="ln sidx"></i>vs ${box.dataset.index || 'index'}</button>
      </div>`;
    function paintCtrl() {
      ctrl.querySelectorAll('[data-r]').forEach(b => { const on = b.dataset.r === state.range; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); });
      ctrl.querySelectorAll('[data-t]').forEach(b => b.classList.toggle('on', b.dataset.t === state.type));
      ctrl.querySelectorAll('[data-k]').forEach(b => b.setAttribute('aria-pressed', !!state[b.dataset.k]));
      ctrl.querySelector('[data-t="candle"]').disabled = state.cmp;   // comparison is in %, candles don't apply
    }
    ctrl.addEventListener('click', e => {
      const r = e.target.closest('[data-r]'), t = e.target.closest('[data-t]'), k = e.target.closest('[data-k]');
      if (r) { state.range = r.dataset.r; store.set('range', state.range); load(); }
      else if (t && !t.disabled) { state.type = t.dataset.t; store.set('type', state.type); draw(); }
      else if (k) { state[k.dataset.k] = !state[k.dataset.k]; store.set(k.dataset.k, state[k.dataset.k]); draw(); }
      paintCtrl();
    });
    paintCtrl();

    // ---------- chart ----------
    function theme() {
      return {
        layout: { background: { type: 'solid', color: 'transparent' }, textColor: css('--faint'), fontSize: 12,
                  fontFamily: getComputedStyle(document.body).getPropertyValue('--mono') || 'monospace', attributionLogo: false },
        grid: { vertLines: { visible: false }, horzLines: { color: css('--line') } },
        rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.24 } },
        timeScale: { borderVisible: false, rightOffset: 5, fixLeftEdge: true, lockVisibleTimeRangeOnResize: true },   // room for the last marker's label
        crosshair: { mode: LC.CrosshairMode.Magnet,
                     vertLine: { color: css('--mute'), width: 1, style: LC.LineStyle.Dashed, labelBackgroundColor: css('--raise') },
                     horzLine: { color: css('--mute'), width: 1, style: LC.LineStyle.Dashed, labelBackgroundColor: css('--raise') } },
        handleScroll: { vertTouchDrag: false },   // a vertical swipe scrolls the page, not the chart
      };
    }
    chart = LC.createChart(el, { autoSize: true, ...theme() });
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { chart.applyOptions(theme()); draw(); });

    async function fetchSpan(span) {
      if (cache[span]) return cache[span];
      const q = new URLSearchParams({span}); if (call) q.set('call', call);
      const r = await fetch(`${B}/chart/${encodeURIComponent(isin)}?${q}`);
      if (!r.ok) throw new Error('chart ' + r.status);
      return (cache[span] = await r.json());
    }
    async function load() {
      const span = ({'3Y': '3y', '5Y': '5y', 'Max': 'max'})[state.range] || '1y';
      box.classList.add('loading');
      try { data = await fetchSpan(span); }
      catch (e) { box.classList.remove('loading'); el.dataset.msg = 'Couldn\'t load prices right now.'; return; }
      box.classList.remove('loading');
      if (!data.bars || !data.bars.length) { el.dataset.msg = 'No price history for this range.'; return; }
      delete el.dataset.msg;
      bars = data.bars; byTime = new Map(bars.map((b, i) => [b.t, i]));
      box.querySelector('.pc-src').textContent = data.source || '';
      draw();
    }

    function startIndex() {
      const n = bars.length, r = RANGES.find(x => x[0] === state.range)[1];
      if (typeof r === 'number') return Math.max(0, n - r - 1);
      if (r === 'ytd') { const y = bars[n - 1].t.slice(0, 4); const i = bars.findIndex(b => b.t >= y + '-01-01'); return Math.max(0, i - 1); }
      return 0;
    }

    function draw() {
      if (!bars.length) return;
      [main, vol, s50, s200, sIdx].forEach(s => s && chart.removeSeries(s));
      main = vol = s50 = s200 = sIdx = null;
      const i0 = startIndex(), view = bars.slice(i0), cmp = state.cmp && data.index && data.index.points.length;
      const C = { price: css('--s-price'), ma50: css('--s-ma50'), ma200: css('--s-ma200'), idx: css('--s-index'),
                  up: css('--buy'), down: css('--sell'), vol: css('--line2'), spike: css('--hold') };
      const base = view[0].c;
      const pct = v => (v / base - 1) * 100;
      chart.applyOptions({ rightPriceScale: { mode: LC.PriceScaleMode.Normal } });
      const fmt = cmp ? { type: 'custom', formatter: v => (v > 0 ? '+' : '') + v.toFixed(1) + '%' } : { type: 'price', precision: base >= 1000 ? 0 : 2, minMove: base >= 1000 ? 1 : 0.01 };
      if (state.type === 'candle' && !cmp) {
        main = chart.addCandlestickSeries({ upColor: C.up, downColor: C.down, wickUpColor: C.up, wickDownColor: C.down, borderVisible: false, priceFormat: fmt, lastValueVisible: true, priceLineVisible: false });
        main.setData(view.map(b => ({ time: b.t, open: b.o, high: b.h, low: b.l, close: b.c })));
      } else {
        main = chart.addAreaSeries({ lineColor: C.price, lineWidth: 2, topColor: hexA(C.price, 0.22), bottomColor: hexA(C.price, 0), priceFormat: fmt, priceLineVisible: false });
        main.setData(view.map(b => ({ time: b.t, value: cmp ? pct(b.c) : b.c })));
      }
      vol = chart.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false });
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false });
      vol.setData(view.map((b, i) => ({ time: b.t, value: b.v, color: (b.x || 0) >= 4 ? hexA(C.spike, 0.9) : hexA(C.vol, 0.9) })));
      const line = (color, pts, style) => {
        const s = chart.addLineSeries({ color, lineWidth: 1.5, lineStyle: style || 0, priceFormat: fmt, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
        s.setData(pts); return s;
      };
      const from = view[0].t;
      const sel = arr => (arr || []).filter(p => p.t >= from);
      if (state.ma50) s50 = line(C.ma50, sel(data.ma50).map(p => ({ time: p.t, value: cmp ? pct(p.v) : p.v })));
      if (state.ma200) s200 = line(C.ma200, sel(data.ma200).map(p => ({ time: p.t, value: cmp ? pct(p.v) : p.v })), LC.LineStyle.Dashed);
      if (cmp) {
        const ip = sel(data.index.points), ib = ip.length ? ip[0].v : 1;
        sIdx = line(C.idx, ip.map(p => ({ time: p.t, value: (p.v / ib - 1) * 100 })), LC.LineStyle.SparseDotted);
      }
      // markers: the council's call day, and the last few volume spikes
      const marks = [];
      (data.spikes || []).filter(t => t >= from).slice(-12).forEach(t => marks.push({ time: t, position: 'belowBar', color: C.spike, shape: 'circle', size: 0.6 }));
      if (data.call && byTime.has(data.call) && data.call >= from) marks.push({ time: data.call, position: 'aboveBar', color: css('--ink'), shape: 'arrowDown', text: 'Call' });
      main.setMarkers(marks.sort((a, b) => a.time < b.time ? -1 : 1));
      chart.timeScale().fitContent();
      readout(null);
      box.querySelector('.pc-legend').innerHTML = [
        `<span><i class="ln" style="background:${C.price}"></i>${cmp ? 'This stock' : 'Price'}</span>`,
        state.ma50 ? `<span><i class="ln" style="background:${C.ma50}"></i>50-day avg</span>` : '',
        state.ma200 ? `<span><i class="ln dash" style="border-color:${C.ma200}"></i>200-day avg</span>` : '',
        cmp ? `<span><i class="ln dot" style="border-color:${C.idx}"></i>${data.index.name}</span>` : '',
        `<span><i class="sw" style="background:${C.spike}"></i>volume over 4× usual</span>`,
        data.call && data.call >= from ? '<span>▼ day of the call</span>' : ''].join('');
    }

    // ---------- readout ----------
    const fmtP = v => v == null ? '–' : cur + (v >= 1000 ? v.toLocaleString(undefined, {maximumFractionDigits: 0}) : v.toFixed(2));
    const fmtV = v => v >= 1e7 ? (v / 1e7).toFixed(2) + ' Cr' : v >= 1e5 && !cur.includes('$') ? (v / 1e5).toFixed(1) + ' L' : v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v.toLocaleString();
    const sign = x => (x > 0 ? '+' : x < 0 ? '−' : '') + Math.abs(x).toFixed(1) + '%';
    function readout(t) {
      const i0 = startIndex();
      const i = t && byTime.has(t) ? byTime.get(t) : bars.length - 1;
      const b = bars[i], first = bars[i0], prev = bars[i - 1];
      const ch = (b.c / first.c - 1) * 100;
      read.innerHTML = `<b>${fmtP(b.c)}</b><span class="${ch >= 0 ? 'pos' : 'neg'}">${sign(ch)}</span><span class="faint">${t ? 'from ' + nice(first.t) + ' to ' + nice(b.t) : 'over ' + state.range}</span>`;
      if (!t) { tip.hidden = true; return; }
      const val = (arr) => { const p = (arr || []).find(x => x.t === b.t); return p ? p.v : null; };
      const day = prev ? (b.c / prev.c - 1) * 100 : null;
      const rows = [
        ['Close', fmtP(b.c) + (day == null ? '' : ` <span class="${day >= 0 ? 'pos' : 'neg'}">${sign(day)}</span>`)],
        ['Open · High · Low', `${fmtP(b.o)} · ${fmtP(b.h)} · ${fmtP(b.l)}`],
        ['Volume', fmtV(b.v) + (b.x ? ` <span class="${b.x >= 4 ? 'warn' : 'faint'}">${b.x}× usual</span>` : '')],
      ];
      if (b.d != null) rows.push(['Delivered', b.d + '%']);
      if (state.ma50 && val(data.ma50) != null) rows.push(['<i class="ln s50"></i>50-day avg', fmtP(val(data.ma50))]);
      if (state.ma200 && val(data.ma200) != null) rows.push(['<i class="ln s200"></i>200-day avg', fmtP(val(data.ma200))]);
      if (state.cmp && data.index) {
        const ip = data.index.points, a = ip.find(p => p.t >= first.t), z = [...ip].reverse().find(p => p.t <= b.t);
        if (a && z) rows.push([`<i class="ln sidx"></i>${data.index.name}`, sign((z.v / a.v - 1) * 100)]);
      }
      tip.innerHTML = `<div class="pc-tip-h">${nice(b.t, true)}</div>` + rows.map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join('');
      tip.hidden = false;
    }
    const nice = (t, long) => new Date(t + 'T00:00:00').toLocaleDateString(undefined, long ? {weekday: 'short', day: 'numeric', month: 'short', year: 'numeric'} : {day: 'numeric', month: 'short', year: 'numeric'});
    chart.subscribeCrosshairMove(p => {
      if (!p || !p.time || !p.point || p.point.x < 0) { readout(null); return; }
      const t = typeof p.time === 'string' ? p.time : `${p.time.year}-${String(p.time.month).padStart(2, '0')}-${String(p.time.day).padStart(2, '0')}`;
      readout(t);
      const w = el.clientWidth, x = p.point.x;
      tip.style.left = (x > w / 2 ? Math.max(4, x - tip.offsetWidth - 14) : x + 14) + 'px';
    });
    load();
  }

  function hexA(c, a) {   // any CSS colour + alpha
    if (c.startsWith('#')) { const h = c.length === 4 ? c.replace(/#(.)(.)(.)/, '#$1$1$2$2$3$3') : c;
      const n = parseInt(h.slice(1, 7), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; }
    return c;
  }
})();
