// "New version ready": checks the server's build id every 2 minutes and whenever the tab comes back.
// Never reloads by itself (a half-typed broker order stays safe); "Later" hides it until the next deploy.
(function () {
  const mine = document.querySelector('meta[name=build]')?.content;
  const bar = document.getElementById('upd');
  if (!mine || !bar) return;
  let dismissed = null, last = 0;
  async function check() {
    if (Date.now() - last < 20000) return;
    last = Date.now();
    try {
      const r = await fetch((window.B || '') + '/version', {cache: 'no-store'});
      if (!r.ok) return;
      const {build} = await r.json();
      if (build && build !== mine && build !== dismissed) bar.hidden = false;
    } catch (e) {}
  }
  document.getElementById('upd-go').addEventListener('click', () => location.reload());
  document.getElementById('upd-x').addEventListener('click', async () => {
    bar.hidden = true;
    try { dismissed = (await (await fetch((window.B || '') + '/version', {cache: 'no-store'})).json()).build; } catch (e) {}
  });
  setInterval(check, 120000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
  window.addEventListener('focus', check);
})();
