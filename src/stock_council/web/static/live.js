// Refreshes the #live part of a page in place while work is in progress. Keeps open panels open
// and the scroll position where it is; follows a redirect (e.g. a finished one-stock check).
(function () {
  const el = document.getElementById('live');
  if (!el || !el.dataset.poll) return;
  async function tick() {
    try {
      const r = await fetch(location.href, {cache: 'no-store'});
      if (r.redirected && new URL(r.url).pathname !== location.pathname) { location.href = r.url; return; }
      const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
      const next = doc.getElementById('live');
      if (!next) return;
      const live = document.getElementById('live');
      const open = [...live.querySelectorAll('details[open][id]')].map(d => d.id);
      live.innerHTML = next.innerHTML;
      open.forEach(id => { const d = document.getElementById(id); if (d) d.open = true; });
      live.dispatchEvent(new Event('live:updated', {bubbles: true}));
      if (!next.dataset.poll) return;          // finished: final state shown, stop
    } catch (e) {}
    setTimeout(tick, 2500);
  }
  setTimeout(tick, 2500);
})();
