// Start loading a page when the pointer rests on its link (or a finger touches it), so the click feels instant.
(function () {
  const done = new Set();
  function go(e) {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || a.target || a.hasAttribute('download') || a.hasAttribute('data-noprefetch') || a.origin !== location.origin) return;
    const u = a.href.split('#')[0];
    if (done.has(u) || u === location.href.split('#')[0] || /\.csv|\/static\//.test(u)) return;
    done.add(u);
    const l = document.createElement('link'); l.rel = 'prefetch'; l.href = u; document.head.appendChild(l);
  }
  let t;
  document.addEventListener('mouseover', e => { clearTimeout(t); t = setTimeout(() => go(e), 65); }, {passive: true});
  document.addEventListener('touchstart', go, {passive: true});
})();
