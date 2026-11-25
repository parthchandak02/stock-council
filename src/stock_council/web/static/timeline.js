// Today's timeline: last run, the market close, now, the next run, on one line. The "now" marker and the
// countdown move every 30 s while the page is open. Times show in the market's own time zone.
(function () {
  const box = document.getElementById('tl');
  if (!box) return;
  const d = box.dataset, tz = d.iana;
  const T = k => d[k] ? new Date(d[k]).getTime() : null;
  const last = T('last'), next = T('next'), close = T('close');
  const fmt = (t, withDay) => new Intl.DateTimeFormat(undefined, {timeZone: tz, hour: 'numeric', minute: '2-digit',
    ...(withDay ? {weekday: 'short'} : {})}).format(new Date(t));
  const sameDay = (a, b) => new Intl.DateTimeFormat('en-CA', {timeZone: tz}).format(new Date(a)) === new Intl.DateTimeFormat('en-CA', {timeZone: tz}).format(new Date(b));
  const span = ms => { const m = Math.max(0, Math.round(ms / 60000)), h = Math.floor(m / 60), dd = Math.floor(h / 24);
    return dd >= 1 ? `${dd} d ${h % 24} h` : h ? `${h} h ${m % 60} m` : `${m} m`; };
  const q = s => box.querySelector(s);
  function paint() {
    const now = Date.now();
    const a = last || now - 864e5, b = next || now + 864e5;
    const pos = t => Math.max(0, Math.min(100, (t - a) / (b - a) * 100));
    const p = pos(now);
    q('.tl-fill').style.width = p + '%';
    q('.tl-now').style.left = p + '%';
    const nl = q('.tl-lab.now');          // the full timeline has a "Now" label under the marker
    if (nl) {
      nl.querySelector('span').textContent = fmt(now, !sameDay(now, last || now));
      const W = box.querySelector('.tl-nowrow').clientWidth, half = nl.offsetWidth / 2;
      nl.style.left = Math.max(half, Math.min(W - half, p / 100 * W)) + 'px';
    }
    q('.tl-now').title = 'Now ' + fmt(now);
    const c = q('.tl-close');
    const showClose = close && close > a && close < b;
    if (showClose) { c.hidden = false; c.style.left = pos(close) + '%'; c.title = 'Market closes ' + fmt(close); }
    else c.hidden = true;
    const key = q('.tl-closekey'); if (key) key.hidden = !showClose;
    if (last) { const s = q('.tl-lab.last span'); s.textContent = (box.classList.contains('compact') ? 'Last ' : '') + fmt(last, true); if (d.picks) s.insertAdjacentHTML('beforeend', `<em>${d.picks}</em>`); }
    const n = q('.tl-lab.next span');
    if (next) { if (now >= next) n.textContent = 'starting now';
      else if (box.classList.contains('compact')) n.textContent = `Next ${fmt(next, !sameDay(now, next))} · in ${span(next - now)}`;
      else { n.textContent = fmt(next, !sameDay(now, next)); n.insertAdjacentHTML('beforeend', `<em>in ${span(next - now)}</em>`); } }
  }
  paint();
  setInterval(paint, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) paint(); });
})();
