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
  for (let i = 0; i < 3; i++) addLine();
  box.addEventListener('input', render); box.addEventListener('change', render);
  box.querySelector('.addline').addEventListener('click', () => { addLine(); render(); lines.lastElementChild.querySelector('.q').focus(); });
  lines.addEventListener('click', e => { if (e.target.closest('.rm')) { e.target.closest('.line').remove(); render(); } });
  render();

  btn.addEventListener('click', () => {
    const s = state();
    const q = new URLSearchParams({view: 'cm', fs: '1', to: d.to, su: s.subject, body: s.text});
    if (d.gmail) q.set('authuser', d.gmail);
    window.open('https://mail.google.com/mail/?' + q.toString(), '_blank', 'noopener');   // open first: no popup block
    fetch('/orders', {method: 'POST', headers: {'content-type': 'application/json'}, keepalive: true,
      body: JSON.stringify({run_id: +d.run, isin: d.isin, symbol: d.symbol, name: d.name, side: s.side,
                            account: s.account, lines: s.rows, subject: s.subject})});
    btn.innerHTML = 'Draft opened in Gmail ✓';
  });
})();
