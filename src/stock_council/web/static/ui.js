// Site-styled dropdowns: every <select> becomes a button + list that looks the same on every device.
// The real <select> stays (hidden) inside the form, so submitting and change listeners keep working.
(function () {
  const ICON = n => `<svg class="ic" width="16" height="16" aria-hidden="true"><use href="/static/icons.svg?v=1#${n}-b"/></svg>`;
  let open = null;

  function label(sel) {
    const o = sel.options[sel.selectedIndex];
    return o ? o.textContent.trim() : '';
  }

  function close() {
    if (!open) return;
    open.pop.hidden = true; open.btn.setAttribute('aria-expanded', 'false'); open.wrap.classList.remove('open');
    open = null;
  }

  function choose(sel, value) {
    sel.value = value;
    sel.dispatchEvent(new Event('input', {bubbles: true}));
    sel.dispatchEvent(new Event('change', {bubbles: true}));
  }

  function enhanceOne(sel) {
    if (sel.dataset.ui) return;
    sel.dataset.ui = '1';
    const wrap = document.createElement('span');
    wrap.className = 'sel ' + (sel.className || '');
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel);
    sel.tabIndex = -1; sel.setAttribute('aria-hidden', 'true');
    const btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'sel-btn';
    btn.setAttribute('aria-haspopup', 'listbox'); btn.setAttribute('aria-expanded', 'false');
    if (sel.getAttribute('aria-label')) btn.setAttribute('aria-label', sel.getAttribute('aria-label'));
    const pop = document.createElement('div');
    pop.className = 'sel-pop'; pop.hidden = true; pop.setAttribute('role', 'listbox');
    wrap.append(btn, pop);

    function paint() {
      const empty = sel.value === '' && sel.dataset.emptyIcon;
      btn.innerHTML = empty ? ICON(sel.dataset.emptyIcon) : `<span class="sel-txt">${label(sel)}</span>${ICON('chevdown')}`;
      btn.classList.toggle('icon-only', !!empty);
    }
    paint();
    sel.addEventListener('change', paint);

    function build(filter = '') {
      const f = filter.toLowerCase();
      let html = sel.options.length > 12 ? `<div class="sel-search">${ICON('search')}<input type="text" placeholder="Search" aria-label="Search options"></div>` : '';
      html += '<div class="sel-list">';
      let group = null;
      [...sel.options].forEach((o, i) => {
        const t = o.textContent.trim();
        if (f && !t.toLowerCase().includes(f)) return;
        const g = o.parentElement.tagName === 'OPTGROUP' ? o.parentElement.label : null;
        if (g && g !== group) { html += `<div class="sel-group">${g}</div>`; group = g; }
        html += `<div class="sel-opt${o.selected ? ' on' : ''}" role="option" data-i="${i}" aria-selected="${o.selected}">${t}${o.selected ? ICON('check') : ''}</div>`;
      });
      html += '</div>';
      const prev = pop.querySelector('.sel-search input');
      pop.innerHTML = html;
      const s = pop.querySelector('.sel-search input');
      if (s) {
        s.value = filter;
        s.addEventListener('input', () => { build(s.value); pop.querySelector('.sel-search input').focus(); });
        s.addEventListener('keydown', key);
      }
    }

    function active(dir) {
      const opts = [...pop.querySelectorAll('.sel-opt')];
      if (!opts.length) return;
      let i = opts.findIndex(o => o.classList.contains('act'));
      if (i < 0) i = opts.findIndex(o => o.classList.contains('on'));
      opts.forEach(o => o.classList.remove('act'));
      i = Math.max(0, Math.min(opts.length - 1, i + dir));
      opts[i].classList.add('act'); opts[i].scrollIntoView({block: 'nearest'});
    }

    function key(e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); active(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); active(-1); }
      else if (e.key === 'Enter') { e.preventDefault(); const a = pop.querySelector('.sel-opt.act') || pop.querySelector('.sel-opt'); if (a) pick(a); }
      else if (e.key === 'Escape') { close(); btn.focus(); }
      else if (e.key === 'Tab') close();
    }

    function pick(el) {
      choose(sel, sel.options[+el.dataset.i].value);
      close(); btn.focus();
    }

    btn.addEventListener('click', () => {
      if (open && open.wrap === wrap) return close();
      close();
      build();
      pop.hidden = false; wrap.classList.add('open'); btn.setAttribute('aria-expanded', 'true');
      // open upwards when there is no room below
      const r = btn.getBoundingClientRect();
      wrap.classList.toggle('up', window.innerHeight - r.bottom < 280 && r.top > 300);
      open = {wrap, btn, pop};
      const s = pop.querySelector('.sel-search input');
      (s || btn).focus();
      const on = pop.querySelector('.sel-opt.on'); if (on) on.scrollIntoView({block: 'nearest'});
    });
    btn.addEventListener('keydown', e => {
      if (['ArrowDown', 'ArrowUp'].includes(e.key) && !open) { e.preventDefault(); btn.click(); }
      else if (open && open.wrap === wrap) key(e);
    });
    pop.addEventListener('mousedown', e => e.preventDefault());
    pop.addEventListener('click', e => { const o = e.target.closest('.sel-opt'); if (o) pick(o); });
  }

  function enhance(root) { (root || document).querySelectorAll('select').forEach(enhanceOne); }
  document.addEventListener('click', e => { if (open && !open.wrap.contains(e.target)) close(); });
  window.addEventListener('resize', close);
  window.ui = {enhance};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => enhance());
  else enhance();
  document.addEventListener('live:updated', e => enhance(e.target));
})();
