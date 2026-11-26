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
  enhance();   // this script sits at the end of <body>, so every select already exists: swap them before first paint
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => enhance());
  document.addEventListener('live:updated', e => enhance(e.target));
})();

// Top-bar search: the icon opens a one-line "check any stock" box; Escape or a second tap closes it.
(function () {
  const btn = document.getElementById('srch-btn'), box = document.getElementById('srch');
  if (!btn || !box) return;
  const set = open => { box.hidden = !open; btn.setAttribute('aria-expanded', open); if (open) document.getElementById('srch-q').focus(); };
  btn.addEventListener('click', () => set(box.hidden));
  box.addEventListener('keydown', e => { if (e.key === 'Escape') { set(false); btn.focus(); } });
})();

// Search as you type: matching companies (ticker and name) in this market, then any in the other market.
// Picking one checks it straight away; Enter with nothing picked lets the server work out what was meant.
(function () {
  const input = document.getElementById('srch-q'), list = document.getElementById('sug');
  if (!input || !list) return;
  const form = input.form;
  let items = [], sel = -1, timer = 0, seq = 0;
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const close = () => { list.hidden = true; input.setAttribute('aria-expanded', 'false'); sel = -1; };
  const mark = () => list.querySelectorAll('li[data-i]').forEach(li => li.setAttribute('aria-selected', +li.dataset.i === sel));
  function go(it) {
    if (it.base === undefined) { input.value = it.symbol; form.submit(); return; }
    const f = document.createElement('form');           // a company in the other market: check it there
    f.method = 'post'; f.action = it.base + '/check';
    f.innerHTML = `<input type="hidden" name="symbol" value="${esc(it.symbol)}">`;
    document.body.appendChild(f); f.submit();
  }
  function render(d) {
    items = [...d.hits, ...d.other];
    if (!items.length) { close(); return; }
    let h = d.hits.map((x, i) => `<li role="option" data-i="${i}"><span class="nm"><b>${esc(x.name)}</b><span>${esc(x.symbol)}</span></span></li>`).join('');
    if (d.other.length) h += `<li class="sep" aria-hidden="true">In ${esc(d.other[0].market)}</li>` + d.other.map((x, j) =>
      `<li role="option" data-i="${d.hits.length + j}"><span class="nm"><b>${esc(x.name)}</b><span>${esc(x.symbol)}</span></span><img class="flag" src="/static/flag-${esc(x.code)}.svg" alt="${esc(x.market)}" width="18" height="12"></li>`).join('');
    list.innerHTML = h; list.hidden = false; input.setAttribute('aria-expanded', 'true'); sel = -1;
  }
  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { close(); return; }
    timer = setTimeout(async () => {
      const my = ++seq;
      try { const r = await fetch((window.B || '') + '/suggest?q=' + encodeURIComponent(q)); const d = await r.json(); if (my === seq) render(d); }
      catch (e) { close(); }
    }, 120);
  });
  input.addEventListener('keydown', e => {
    if (list.hidden) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = sel + 1 >= items.length ? -1 : sel + 1; mark(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = sel - 1 < -1 ? items.length - 1 : sel - 1; mark(); }
    else if (e.key === 'Enter' && sel >= 0) { e.preventDefault(); go(items[sel]); }
    else if (e.key === 'Escape') { close(); e.stopPropagation(); }
  });
  list.addEventListener('mousedown', e => e.preventDefault());   // keep focus in the box
  list.addEventListener('click', e => { const li = e.target.closest('li[data-i]'); if (li) go(items[+li.dataset.i]); });
  input.addEventListener('blur', () => setTimeout(close, 150));
})();

// Keep the search bar glued under the top bar, whose height depends on the screen.
(function () {
  const top = document.querySelector('header.top, .top');
  if (!top || !window.ResizeObserver) return;
  new ResizeObserver(() => document.documentElement.style.setProperty('--toph', top.offsetHeight + 'px')).observe(top);
})();

// Charts inside a fold are drawn for their real width once it opens.
document.addEventListener('toggle', e => { if (e.target.matches && e.target.matches('details.fold[open]')) window.dispatchEvent(new Event('resize')); }, true);
