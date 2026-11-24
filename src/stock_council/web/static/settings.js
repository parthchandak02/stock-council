// Settings save themselves: any change is sent (whole form) half a second after you stop typing.
(function () {
  const form = document.querySelector('form.settings'), badge = document.getElementById('savestate');
  if (!form || !badge) return;
  let t, busy = false, again = false;
  const show = (cls, txt) => { badge.className = 'chip big ' + cls; badge.lastChild.textContent = txt; };
  async function save() {
    if (busy) { again = true; return; }
    busy = true; show('none', 'Saving…');
    try {
      const r = await fetch(form.action, {method: 'POST', body: new FormData(form), headers: {'x-autosave': '1'}});
      show(r.ok ? 'good' : 'bad', r.ok ? 'Saved' : 'Not saved, try again');
    } catch (e) { show('bad', 'Offline, not saved'); }
    busy = false;
    if (again) { again = false; save(); }
  }
  const later = () => { clearTimeout(t); show('warn', 'Editing…'); t = setTimeout(save, 600); };
  form.addEventListener('input', later); form.addEventListener('change', later);
  form.addEventListener('click', e => { if (e.target.closest('.rm, #addmember')) later(); });
  window.addEventListener('beforeunload', e => { if (badge.textContent.includes('Editing') || busy) { save(); } });
})();
// Settings: council members can be added, removed and switched off; the switch writes 1/0 into a hidden field
// so every row always submits (a plain unchecked checkbox would send nothing and shift the rows).
(function () {
  const list = document.getElementById('memberlist'), tpl = document.getElementById('membertpl');
  if (!list) return;
  const opts = document.getElementById('catopts').innerHTML;   // the catalogue, sent once
  function fill(sel) {
    const v = sel.value;
    sel.innerHTML = (v ? '' : '<option value="">Pick a model</option>') + opts;
    if (v && ![...sel.options].some(o => o.value === v)) sel.insertAdjacentHTML('afterbegin', `<option value="${v}">${v}</option>`);
    sel.value = v;
    sel.dispatchEvent(new Event('change'));   // repaint the site dropdown
  }
  list.querySelectorAll('select[name=cm_model]').forEach(fill);
  list.addEventListener('change', e => {
    const sw = e.target.closest('.mon');
    if (sw) sw.previousElementSibling.value = sw.checked ? '1' : '0';
    const sel = e.target.closest('select[name=cm_model]');
    if (sel) {   // fill an empty name from the model, e.g. "mistralai/mistral-large-4-0" -> "Mistral"
      const name = sel.closest('.mrow').querySelector('.mname');
      if (!name.value.trim() && sel.value.includes('/')) name.value = sel.value.split('/')[1].split(/[-.:]/)[0].replace(/^./, c => c.toUpperCase());
    }
  });
  list.addEventListener('click', e => {
    const rm = e.target.closest('.rm');
    if (rm && list.querySelectorAll('.mrow').length > 1) rm.closest('.mrow').remove();
  });
  document.getElementById('addmember')?.addEventListener('click', () => {
    list.insertAdjacentHTML('beforeend', tpl.innerHTML);
    const row = list.lastElementChild;
    fill(row.querySelector('select[name=cm_model]'));
    window.ui && window.ui.enhance(row);
    row.querySelector('.mname').focus();
  });
})();
