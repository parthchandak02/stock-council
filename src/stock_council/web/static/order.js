// Broker order: Buy/Sell, account, quantity × trigger-price lines -> a ready Gmail draft. Dad presses Send.
(function () {
  const box = document.getElementById('order');
  if (!box || !document.getElementById('lines')) return;
  const lines = document.getElementById('lines'), btn = document.getElementById('opengmail');
  const subj = document.getElementById('pv-subject'), body = document.getElementById('pv-body');
  const d = box.dataset, label = btn.innerHTML;

  function addLine(q = '', p = '') {
    lines.insertAdjacentHTML('beforeend',
      `<div class="line"><input type="number" min="1" step="1" inputmode="numeric" placeholder="50" class="q" value="${q}" aria-label="Quantity">
       <input type="number" min="0" step="0.05" inputmode="decimal" placeholder="900" class="p" value="${p}" aria-label="Trigger price">
       <button type="button" class="rm" aria-label="Remove line">×</button></div>`);
  }
  function state() {
    const side = box.querySelector('input[name=side]:checked').value;
    const account = box.querySelector('select[name=account]').value;
    const rows = [...lines.querySelectorAll('.line')].map(l => ({qty: l.querySelector('.q').value.trim(), price: l.querySelector('.p').value.trim()}))
      .filter(r => r.qty && r.price && +r.qty > 0 && +r.price > 0);
    const subject = `${d.name}..gtt ${side} ${account}`;
    const text = [`${d.name} (NSE: ${d.symbol})`, ...rows.map(r => `${r.qty}x ${r.price}`)].join('\n');
    return {side, account, rows, subject, text};
  }
  function render() {
    const s = state();
    subj.textContent = s.subject; body.textContent = s.text;
    btn.disabled = s.rows.length === 0;
    btn.innerHTML = label;   // editing after opening a draft: offer a fresh one
    box.classList.toggle('selling', s.side === 'sell');
  }
  // a half-filled order is kept on this device until it is sent to Gmail
  const KEY = 'order:' + d.isin;
  let draft = null;
  try { draft = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) {}
  if (draft && draft.rows && draft.rows.length) {
    draft.rows.forEach(r => addLine(r.qty, r.price));
    const side = box.querySelector(`input[name=side][value=${draft.side}]`); if (side) side.checked = true;
    const acct = box.querySelector('select[name=account]');
    if (acct && [...acct.options].some(o => o.value === draft.account)) { acct.value = draft.account; acct.dispatchEvent(new Event('change')); }
    for (let i = draft.rows.length; i < 3; i++) addLine();
  } else for (let i = 0; i < 3; i++) addLine();
  function keep() {
    const s = state();
    try { s.rows.length ? localStorage.setItem(KEY, JSON.stringify({side: s.side, account: s.account, rows: s.rows})) : localStorage.removeItem(KEY); } catch (e) {}
  }
  box.addEventListener('input', keep); box.addEventListener('change', keep);
  box.addEventListener('input', render); box.addEventListener('change', render);
  box.querySelector('.addline').addEventListener('click', () => { addLine(); render(); lines.lastElementChild.querySelector('.q').focus(); });
  lines.addEventListener('click', e => { if (e.target.closest('.rm')) { e.target.closest('.line').remove(); render(); } });
  render();

  btn.addEventListener('click', () => {
    const s = state();
    const q = new URLSearchParams({view: 'cm', fs: '1', to: d.to, su: s.subject, body: s.text});
    if (d.gmail) q.set('authuser', d.gmail);
    window.open('https://mail.google.com/mail/?' + q.toString(), '_blank', 'noopener');   // open first: no popup block
    fetch((window.B || '') + '/orders', {method: 'POST', headers: {'content-type': 'application/json'}, keepalive: true,
      body: JSON.stringify({run_id: +d.run, isin: d.isin, symbol: d.symbol, name: d.name, side: s.side,
                            account: s.account, lines: s.rows, subject: s.subject})});
    btn.innerHTML = 'Draft opened in Gmail ✓';
    try { localStorage.removeItem(KEY); } catch (e) {}
  });
})();
