/* Варианты корзины на экране «Корзина» (макет А, 14.11.2026). Данные всех вариантов лежат в data-vars ряда
   (app/web/phone/variants.py), на нажатие сервер не спрашиваем. «Заказ» — без меток: цены лучшего варианта,
   серое — то, чего нет нигде. Выбранное помним в sessionStorage, чтобы перезагрузка страницы его не сбросила.
   У серого товара — «Заменить»: шторка с готовыми заменами, что есть в наличии (app/web/phone/replace.py). */
(function () {
  var KEY = 'kz-var', KEYS = 'kz-var-key';
  var sel = 0;              /* номер выбранного варианта на табло, 0 — «Заказ» */
  var selKey = '';          /* и его сети: после замены итоги меняются, и места на табло могут встать иначе */
  var swapping = {};        /* товар → замену выбрали, цены ещё собираются: у строки кольцо вместо кнопки */
  var watching = false;

  var SWAP_ICON = '<svg class="i s" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7h11M15 4l3 3-3 3"></path><path d="M17 17H6M9 14l-3 3 3 3"></path></svg>';
  var SPIN_ICON = '<svg class="i s g-going" viewBox="0 0 24 24" aria-hidden="true"><circle class="trk" cx="12" cy="12" r="8"></circle><path d="M12 4a8 8 0 0 1 8 8"></path></svg>';

  function row() { return document.querySelector('.vrow'); }

  function data() {
    var r = row();
    try { return JSON.parse((r && r.getAttribute('data-vars')) || 'null'); } catch (e) { return null; }
  }

  function keyOf(v) { return (v.c || []).slice().sort().join(','); }

  function remember() {
    try {
      sessionStorage.setItem(KEY, String(sel));
      sessionStorage.setItem(KEYS, selKey);
    } catch (e) {}
  }

  function restore() {
    try {
      sel = parseInt(sessionStorage.getItem(KEY), 10) || 0;
      selKey = sessionStorage.getItem(KEYS) || '';
    } catch (e) { sel = 0; selKey = ''; }
  }

  /* Выбранный вариант узнаём по его сетям, а не по номеру. Нет такого на табло — «Заказ», выбор помним до следующего нажатия. */
  function pickOf(v) {
    var i;
    if (selKey) {
      for (i = 0; i < v.length; i++) { if (keyOf(v[i]) === selKey) { return i + 1; } }
      return 0;
    }
    return sel && sel <= v.length ? sel : 0;
  }

  /* Метку сети берём готовой из карточек варианта: свою вёрстку метки не заводим. */
  function mark(code) {
    var r = row(), src = r && r.querySelector('.vc .lg.s-' + code), node;
    if (src) { return src.cloneNode(true); }
    node = document.createElement('span');
    node.className = 'lg s-' + code;
    node.textContent = String(code || '?').charAt(0).toUpperCase();
    return node;
  }

  function cross(label) {
    var node = document.createElement('span');
    node.className = 'lg nx';
    node.setAttribute('role', 'img');
    node.setAttribute('aria-label', label);
    node.innerHTML = '<svg class="i s" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"></path></svg>';
    return node;
  }

  /* Одна метка в двух местах: колонка слева в списке и пилюля на фото в плитках, нужное показывает CSS. */
  function put(it, node) {
    var inList = document.createElement('span'), inTile = document.createElement('span'), img = it.querySelector('.img');
    inList.className = 'vm vm-l';
    inTile.className = 'vm vm-t';
    inList.appendChild(node.cloneNode(true));
    inTile.appendChild(node);
    it.insertBefore(inList, it.firstChild);
    if (img) { img.appendChild(inTile); }
  }

  function clear(it) {
    var old = it.querySelectorAll('.vm');
    for (var i = 0; i < old.length; i++) { old[i].parentNode.removeChild(old[i]); }
  }

  /* Серому — «Заменить» (пока цены собираются — «Ищем»); с выбранной заменой — подпись «если нет — …». */
  function swapUi(it, pid, grey, name) {
    var old = it.querySelectorAll('.sw, .fb'), i, b, s, nm = it.querySelector('.name');
    for (i = 0; i < old.length; i++) { old[i].parentNode.removeChild(old[i]); }
    it.classList.toggle('fbk', !!name);
    if (name && nm) {
      s = document.createElement('span');
      s.className = 'fb';
      s.textContent = 'если нет — ' + name;
      nm.parentNode.insertBefore(s, nm.nextSibling);
    }
    if (!grey) { delete swapping[pid]; return; }
    b = document.createElement('button');
    b.type = 'button';
    b.className = 'sw';
    b.setAttribute('data-swap', String(pid));
    b.disabled = !!swapping[pid];
    b.innerHTML = swapping[pid] ? SPIN_ICON + 'Ищем' : SWAP_ICON + 'Заменить';
    it.appendChild(b);
  }

  function one(it, cur, gone, eff, fb) {
    var m = /^it-(\d+)$/.exec(it.id || '');
    if (!m) { return; }
    var pid = m[1], a = cur && cur.a[pid], grey = !!cur && gone.indexOf(parseInt(pid, 10)) >= 0, pr = it.querySelector('.pr');
    it.classList.toggle('nx', grey);
    if (cur && pr) { pr.textContent = (grey || !a) ? '' : a[1]; }
    clear(it);
    /* Подпись «если нет — …» — только у строки, которую замена закрывает; серая её не получает: в этом варианте замены нет. */
    swapUi(it, parseInt(pid, 10), grey, grey ? '' : fb[pid]);
    if (!eff || !cur) { return; }
    if (grey) { put(it, cross('нет в этом варианте')); }
    else if (a) { put(it, mark(a[0])); }
  }

  /* Главная кнопка внизу: у выбранного варианта — «Заказать» с его суммой и тем же местом на «Где дешевле». */
  function cta(eff, cur) {
    var c = document.querySelector('[data-cta]'), sub;
    if (!c) { return; }
    if (c.getAttribute('data-orig') === null) { c.setAttribute('data-orig', c.innerHTML); }
    if (eff && cur) {
      c.setAttribute('href', c.getAttribute('data-base') + '?pick=' + cur.n);
      c.textContent = 'Заказать';
      sub = document.createElement('span');
      sub.className = 'sub num';
      sub.textContent = ' · ' + cur.t;
      c.appendChild(sub);
    } else {
      c.setAttribute('href', c.getAttribute('data-base'));
      c.innerHTML = c.getAttribute('data-orig');
    }
  }

  function apply() {
    var r = row(), d = data(), list = document.querySelector('.items'), v = (d && d.v) || [], best = null, i;
    if (!list) { return; }
    for (i = 0; i < v.length; i++) { if (v[i].best) { best = v[i]; } }
    /* Выбранного варианта может не быть (расчёт ещё идёт или вариантов стало меньше): тогда «Заказ», но выбор не теряем. */
    var eff = pickOf(v);
    var cur = eff ? v[eff - 1] : (best || v[0] || null);
    var gone = eff ? cur.x : ((d && d.z) || []);
    list.classList.toggle('has-var', eff > 0);
    if (r) {
      var tabs = r.querySelectorAll('[data-var]');
      for (i = 0; i < tabs.length; i++) {
        var on = parseInt(tabs[i].getAttribute('data-var'), 10) === eff;
        tabs[i].classList.toggle('pick', on);
        tabs[i].setAttribute('aria-selected', on ? 'true' : 'false');
      }
    }
    var its = list.querySelectorAll('.it');
    for (i = 0; i < its.length; i++) { one(its[i], cur, gone, eff, (d && d.f) || {}); }
    cta(eff, cur);
  }

  /* ---------- «Заменить» ---------- */
  function swapUrl(pid) {
    var r = row(), base = r && r.getAttribute('data-swap');
    return base ? base.replace(/0$/, String(pid)) : '';
  }

  /* Сети выбранного варианта: в них замена должна быть в наличии. «Заказ» — пусто, значит любые сети. */
  function codes() {
    var d = data(), v = (d && d.v) || [], at = pickOf(v);
    return at ? (v[at - 1].c || []).join(',') : '';
  }

  function closeSheet() {
    var host = document.querySelector('.shs-host');
    if (host) { host.parentNode.removeChild(host); }
    document.documentElement.classList.remove('shs-lock');
  }

  function openSheet(pid) {
    var url = swapUrl(pid);
    if (!url || !window.fetch) { return; }
    fetch(url + '?c=' + encodeURIComponent(codes()), { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) { throw new Error('HTTP ' + r.status); }
        return r.text();
      })
      .then(function (html) {
        var host = document.createElement('div');
        closeSheet();
        host.className = 'shs-host';
        host.setAttribute('data-pid', String(pid));
        host.innerHTML = html;
        document.body.appendChild(host);
        document.documentElement.classList.add('shs-lock');
      })
      .catch(function () {});
  }

  function oops(host, text) {
    var p = host.querySelector('.shs-err'), sheet = host.querySelector('.shs-sheet');
    if (!p && sheet) {
      p = document.createElement('p');
      p.className = 'shs-none shs-err';
      p.setAttribute('role', 'alert');
      sheet.appendChild(p);
    }
    if (p) { p.textContent = text; }
  }

  /* Пока сервер собирает цены замены, страница раз в три секунды спрашивает ряд заново (kzLiveAgain в basket.html). */
  function watch() {
    var tries = 0;
    if (watching) { return; }
    watching = true;
    (function again() {
      if (!Object.keys(swapping).length || ++tries > 15 || !window.kzLiveAgain) {
        swapping = {};
        watching = false;
        apply();
        return;
      }
      window.kzLiveAgain();
      setTimeout(again, 3000);
    })();
  }

  function pick(host, key) {
    var pid = host.getAttribute('data-pid'), form = new FormData(), url = swapUrl(pid);
    form.append('fb', key);
    form.append('c', codes());
    fetch(url, { method: 'POST', body: form, credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var box = document.getElementById('kz-runs');
        if (j.error) { oops(host, j.error); return; }
        /* Корзина с заменой — приложению: оно само решит, в каких сетях собрать (как после «+» и «−»). */
        if (j.collect && window.KorzinaDevice && KorzinaDevice.available && KorzinaDevice.available()) { KorzinaDevice.collect(j.collect); }
        if (j.collect && box) { box.setAttribute('data-collect', JSON.stringify(j.collect)); }
        closeSheet();
        if (!key) {                 /* замену сняли: искать нечего, ряд пересчитается и сам встанет */
          if (window.kzLiveAgain) { window.kzLiveAgain(); }
          return;
        }
        swapping[pid] = true;
        apply();
        watch();
      })
      .catch(function () { oops(host, 'Не вышло, попробуйте ещё раз.'); });
  }

  document.addEventListener('click', function (e) {
    var t = e.target, b;
    if (!t || !t.closest) { return; }
    b = t.closest('.vrow [data-var]');
    if (b) {
      var d = data(), v = (d && d.v) || [];
      sel = parseInt(b.getAttribute('data-var'), 10) || 0;
      selKey = sel && v[sel - 1] ? keyOf(v[sel - 1]) : '';
      remember();
      apply();
      return;
    }
    b = t.closest('.sw');
    if (b) {
      if (!b.disabled) { openSheet(b.getAttribute('data-swap')); }
      return;
    }
    b = t.closest('.shs-host [data-fb]');
    if (b) {
      pick(b.closest('.shs-host'), b.getAttribute('data-fb'));
      return;
    }
    if (t.closest('[data-shs-close]')) { closeSheet(); }
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeSheet(); }
  });

  /* Ряд заменяет сам себя, когда расчёт готов или пересчитан (kzLive в basket.html): после замены — заново. */
  window.kzVarApply = apply;
  restore();
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', apply); }
  else { apply(); }
})();
