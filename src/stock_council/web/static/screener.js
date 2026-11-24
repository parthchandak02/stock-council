// Screener: add/remove rules, pick stocks for the council, sort the results by any column.
(function () {
  const list = document.getElementById('rulelist'), tpl = document.getElementById('ruletpl');
  document.getElementById('addrule')?.addEventListener('click', () => {
    list.insertAdjacentHTML('beforeend', tpl.innerHTML);
    list.lastElementChild.querySelector('input').focus();
  });
  list?.addEventListener('click', e => {
    const b = e.target.closest('.rm'); if (b) b.closest('.rule').remove();
  });

  const form = document.getElementById('pickform'), btn = document.getElementById('askbtn');
  form?.addEventListener('change', () => {
    const boxes = [...form.querySelectorAll('input[name=pick]')], n = boxes.filter(b => b.checked).length;
    boxes.forEach(b => { b.disabled = !b.checked && n >= 10; });
    btn.disabled = n === 0;
    btn.querySelector('span').textContent = n ? `Ask the council (${n})` : 'Ask the council';
  });

  const table = document.getElementById('results');
  table?.querySelectorAll('th[data-k]').forEach((th, i) => {
    th.addEventListener('click', () => {
      const col = i + 1, desc = th.dataset.dir !== 'desc';
      table.querySelectorAll('th').forEach(t => delete t.dataset.dir);
      th.dataset.dir = desc ? 'desc' : 'asc';
      const rows = [...table.tBodies[0].rows];
      rows.sort((a, b) => {
        const x = a.cells[col].dataset.v, y = b.cells[col].dataset.v;
        if (x === '' || y === '') return x === '' ? 1 : -1;
        const nx = parseFloat(x), ny = parseFloat(y);
        const c = isNaN(nx) || isNaN(ny) ? x.localeCompare(y) : nx - ny;
        return desc ? -c : c;
      });
      rows.forEach(r => table.tBodies[0].appendChild(r));
    });
  });
})();
