// Today's timeline: last run on the left, next run on the right, now as a dot moving between them.
// Times always show in the market's own zone (India: IST, US: New York, ET), whatever zone the phone is in,
// and say so. The dot and the countdown move every 30 s while the page is open.
(function () {
  const box = document.getElementById('tl');
  if (!box) return;
  const d = box.dataset, tz = d.iana, zone = d.zone;
  const T = k => d[k] ? new Date(d[k]).getTime() : null;
  const last = T('last'), next = T('next');
  const ymd = t => new Intl.DateTimeFormat('en-CA', {timeZone: tz}).format(new Date(t));
  const time = t => new Intl.DateTimeFormat('en-US', {timeZone: tz, hour: 'numeric', minute: '2-digit'}).format(new Date(t));
  const day = (t, now) => {
    if (ymd(t) === ymd(now)) return 'Today';
    if (ymd(t) === ymd(now + 864e5)) return 'Tomorrow';
    if (ymd(t) === ymd(now - 864e5)) return 'Yesterday';
    return new Intl.DateTimeFormat('en-US', {timeZone: tz, weekday: 'short'}).format(new Date(t));
  };
  const when = (t, now) => `${day(t, now)} ${time(t)}\u00a0${zone}`.replace(/ (AM|PM)/, "\u00a0$1");
  const span = ms => { const m = Math.max(0, Math.round(ms / 60000)), h = Math.floor(m / 60), dd = Math.floor(h / 24);
    return dd >= 1 ? `${dd}d ${h % 24}h` : h ? `${h}h ${m % 60}m` : `${m}m`; };
  const q = s => box.querySelector(s);
  function paint() {
    const now = Date.now();
    const a = last || now - 864e5, b = next || now + 864e5;
    const p = Math.max(0, Math.min(100, (now - a) / (b - a) * 100));
    q('.tl-fill').style.width = p + '%';
    q('.tl-now').style.left = p + '%';
    q('.tl-now').title = 'Now ' + time(now) + ' ' + zone;
    if (last) q('.tl-lab.last span').textContent = when(last, now);
    if (next) {
      q('.tl-lab.next b').textContent = now >= next ? 'Next run' : `Next run · in ${span(next - now)}`;
      q('.tl-lab.next span').textContent = now >= next ? 'Starting now' : when(next, now);
    }
  }
  paint();
  setInterval(paint, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) paint(); });
})();
