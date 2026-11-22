// cartapi.js — корзина сетей (Перекрёсток, Пятёрочка, Дикси, Магнит, METRO, Впрок) собственными запросами сети.
// Встроен в APK «Моей корзины» 1.24 (09.11.2026), Магнит — с 1.26, METRO и Впрок — живые batch-протоколы.
// Вызывается из MainActivity.java в скрытом WebView-окне фоновой сборки.

var KZ_CARTAPI = (function () {
  'use strict';

  /**
   * Чистая функция расчёта изменений корзины (без сети и побочных эффектов).
   *
   * @param {Object} live  {sku: упаковок} — что лежит в корзине сети сейчас
   * @param {Array}  lines [{sku, target}] — позиции наряда и целевое число упаковок
   * @param {Object} mine  {sku: упаковок} — память телефона (ledger), что он клал сам
   * @param {Object} want  {sku: упаковок} — сколько артикула хочет корзина человека
   * @param {boolean} drop снимать ли своё лишнее
   * @returns {{set: Array, kept: Array, drops: Array}}
   */
  function ops(live, lines, mine, want, drop) {
    live = live || {};
    lines = lines || [];
    mine = mine || {};
    want = want || {};
    drop = !!drop;

    var set = [];
    var kept = [];
    var drops = [];

    // Позиции наряда: повтор одного sku обрабатывается один раз, target берётся наибольший.
    var distinctTargets = {};
    var skuOrder = [];
    for (var i = 0; i < lines.length; i++) {
      var item = lines[i];
      if (!item || item.sku == null) continue;
      var skuStr = String(item.sku);
      var tgt = item.target != null ? (parseInt(item.target, 10) || 0) : 1;
      if (distinctTargets[skuStr] == null) {
        distinctTargets[skuStr] = tgt;
        skuOrder.push(skuStr);
      } else if (tgt > distinctTargets[skuStr]) {
        distinctTargets[skuStr] = tgt;
      }
    }

    for (var j = 0; j < skuOrder.length; j++) {
      var s = skuOrder[j];
      var target = distinctTargets[s];
      var have = parseInt(live[s], 10) || 0;
      if (have >= target) {
        // Лишнее сверх нашего мог положить сам человек — не уменьшаем
        kept.push(s);
      } else {
        set.push({ sku: s, from: have, to: target });
      }
    }

    // Снятие своего лишнего: человек положил 2, телефон 1, корзина хочет 0 — в сети останется 2.
    if (drop) {
      for (var mSku in mine) {
        if (!Object.prototype.hasOwnProperty.call(mine, mSku)) continue;
        var mVal = parseInt(mine[mSku], 10) || 0;
        if (mVal <= 0) continue;
        var w = parseInt(want[mSku], 10) || 0;
        var lHave = parseInt(live[mSku], 10) || 0;
        if (lHave > w) {
          var to = Math.max(w, lHave - mVal);
          if (to < lHave) {
            drops.push({ sku: mSku, from: lHave, to: to });
          }
        }
      }
    }

    return { set: set, kept: kept, drops: drops };
  }

  /** Активные прогоны по ключу: позволяет мгновенно оборвать устаревшую сборку при правке корзины. */
  var activeRuns = {};
  var currentRun = null;

  function makeAbortError() {
    var e = new Error('aborted');
    e.aborted = true;
    return e;
  }

  function checkAborted() {
    if (currentRun && currentRun.aborted) {
      throw makeAbortError();
    }
  }

  /**
   * Мгновенно прервать сборку по ключу (вызывается из MainActivity при новой правке корзины).
   * Обрывает текущий XHR и снимает ожидание ведра токенов.
   */
  function abort(key) {
    var k = String(key || '');
    var st = activeRuns[k];
    if (!st) return false;
    st.aborted = true;
    if (st.timer != null) {
      clearTimeout(st.timer);
      st.timer = null;
    }
    if (typeof st.cancelTimer === 'function') {
      var fn = st.cancelTimer;
      st.cancelTimer = null;
      fn();
    }
    if (st.xhr) {
      try { st.xhr.abort(); } catch (e) {}
      st.xhr = null;
    }
    return true;
  }

  /** Ожидание паузы между запросами в миллисекундах (отменяется при abort). */
  function waitMs(ms) {
    checkAborted();
    var st = currentRun;
    return new Promise(function (resolve, reject) {
      var t = setTimeout(function () {
        if (st) {
          st.timer = null;
          st.cancelTimer = null;
          if (st.aborted) {
            reject(makeAbortError());
            return;
          }
        }
        resolve();
      }, ms);
      if (st) {
        st.timer = t;
        st.cancelTimer = function () {
          reject(makeAbortError());
        };
      }
    });
  }

  /** Сколько запросов ушло и сколько ждали темпа за эту сборку: в итог и в журнал телефона. */
  var stats = { requests: 0, paced: 0 };

  /**
   * Ведро токенов на окно: темп не чаще connectors.rate_limit_rps.
   * Живёт в window.__kzPace между сборками, пока окно отдыхает на robots.txt.
   * Часы монотонные: на эмуляторе время отскакивало на 12 дней назад (09.11.2026),
   * и по Date.now() ведро уходило в минус на миллионы токенов.
   */
  function pace(pauseMs, burst) {
    checkAborted();
    if (typeof window === 'undefined') return Promise.resolve();
    var now = typeof performance !== 'undefined' ? performance.now() : Date.now();
    var p = window.__kzPace;
    // Очередь дальше минуты вперёд не бывает: это ведро чужих часов, начинаем заново.
    if (!p || p.at - now > 60000) {
      p = window.__kzPace = { t: burst, at: now };
    }
    p.t = Math.min(burst, p.t + (now - p.at) / pauseMs);
    p.at = now;
    var delay = 0;
    if (p.t < 1) {
      delay = (1 - p.t) * pauseMs;
      p.t = 0;
      p.at = now + delay;
    } else {
      p.t -= 1;
    }
    if (delay > 0) {
      stats.paced += delay;
      return waitMs(delay);
    }
    return Promise.resolve();
  }

  /**
   * Короткий кэш последней корзины в окне (15 с): при быстрой правке корзины человеком
   * сети с полным ответом на запись (Перекрёсток, Пятёрочка, Магнит, METRO, Впрок)
   * пропускают лишний стартовый GET и сразу шлют дельту — ответ записи всё равно вернёт
   * настоящую корзину и итог целиком.
   */
  var FRESH_CART_MS = 15000;

  function monoNow() {
    return typeof performance !== 'undefined' ? performance.now() : Date.now();
  }

  function getFreshCart(chain, input) {
    if (input && input.fast_delta === false) return null;
    if (typeof window === 'undefined' || !window.__kzLastCart) return null;
    var entry = window.__kzLastCart[chain];
    if (!entry) return null;
    var age = monoNow() - entry.at;
    if (age < 0 || age > FRESH_CART_MS) {
      delete window.__kzLastCart[chain];
      return null;
    }
    return entry;
  }

  function setFreshCart(chain, entry) {
    if (typeof window === 'undefined') return;
    window.__kzLastCart = window.__kzLastCart || {};
    entry.at = monoNow();
    window.__kzLastCart[chain] = entry;
  }

  function clearFreshCart(chain) {
    if (typeof window === 'undefined' || !window.__kzLastCart) return;
    delete window.__kzLastCart[chain];
  }

  /**
   * Запрос через XMLHttpRequest (сайты сетей оборачивают window.fetch).
   * Таймаут 15 с; при ошибке или обрыве статус 0; при abort() — немедленный выход.
   */
  function httpReq(method, url, headers, body, withCredentials) {
    checkAborted();
    stats.requests++;
    var st = currentRun;
    return new Promise(function (resolve, reject) {
      var t0 = Date.now();
      var xhr = new XMLHttpRequest();
      var settled = false;
      if (st) st.xhr = xhr;
      xhr.timeout = 15000;
      if (withCredentials) {
        xhr.withCredentials = true;
      }
      xhr.open(method, url, true);
      if (headers) {
        for (var k in headers) {
          if (Object.prototype.hasOwnProperty.call(headers, k)) {
            xhr.setRequestHeader(k, headers[k]);
          }
        }
      }
      function finish(status, text, wasAbort) {
        if (settled) return;
        settled = true;
        if (st && st.xhr === xhr) st.xhr = null;
        if (wasAbort || (st && st.aborted)) {
          reject(makeAbortError());
          return;
        }
        var ms = Date.now() - t0;
        var json = null;
        try {
          if (text) json = JSON.parse(text);
        } catch (e) {}
        resolve({ status: status, ms: ms, json: json, text: text || '' });
      }
      xhr.onload = function () {
        finish(xhr.status, xhr.responseText, false);
      };
      xhr.onerror = function () {
        finish(0, '', false);
      };
      xhr.ontimeout = function () {
        finish(0, '', false);
      };
      xhr.onabort = function () {
        finish(0, '', true);
      };
      try {
        xhr.send(body != null ? body : null);
      } catch (e) {
        finish(0, '', false);
      }
    });
  }

  /** Форматирование строки журнала запроса без хоста и параметров авторизации. */
  function formatCall(method, url, res, orderId) {
    var p = '';
    // Дикси пишет «/ajax/ajax.php add»: действие через пробел, адресом его не разбираем (иначе %20).
    if (/\s/.test(url)) return method + ' ' + url + ' ' + res.status + ' ' + res.ms + 'ms';
    try {
      p = new URL(url, (typeof location !== 'undefined' ? location.href : 'http://localhost')).pathname;
    } catch (e) {
      p = String(url || '').split('?')[0];
    }
    if (orderId) {
      p = p.split(orderId).join('{order}');
    }
    return method + ' ' + p + ' ' + res.status + ' ' + res.ms + 'ms';
  }

  /** Причина отказа сети: detail.message (Пятёрочка), error.message или error.code (Перекрёсток), message (Магнит). */
  function errText(res) {
    var j = res.json || {};
    var e = j.error || {};
    if (e.code === 'OUT_OF_STOCK') return 'нет в наличии';
    return (j.detail && j.detail.message) || e.message || e.code
      || (typeof j.message === 'string' && j.message) || res.text.slice(0, 80);
  }

  /**
   * Разбор ответа корзины Перекрёстка (/api/customer/1.4.1.0/basket).
   */
  function perekParse(json) {
    if (!json || !json.content) return null;
    var c = json.content;
    var itemsList = Array.isArray(c.items) ? c.items : [];
    var cart = {};
    var items = {};

    for (var i = 0; i < itemsList.length; i++) {
      var it = itemsList[i];
      if (!it || !it.product || !it.product.masterData) continue;
      var md = it.product.masterData;
      var sku = String(md.plu);
      var quantum = parseInt(md.quantum, 10) || 1000;
      var q = Math.round((parseFloat(it.amount) || 0) / quantum);
      var price = q ? ((parseFloat(it.cost) || 0) / q / 100) : 0;
      var gone = it.state !== 'ok';
      cart[sku] = q;
      items[sku] = {
        sku: sku,
        q: q,
        price: price,
        gone: gone,
        quantum: quantum
      };
    }

    var inv = c.invoice || {};
    var count = parseInt(inv.itemNumber, 10) || 0;
    var total = null;
    if (count > 0) {
      total = {
        amount: (parseFloat(inv.paymentAmount) || 0) / 100,
        goods: (parseFloat(inv.cost) || 0) / 100,
        base: (parseFloat(inv.grossCost) || 0) / 100,
        discount: (parseFloat(inv.discountValue) || 0) / 100,
        delivery: (parseFloat(inv.deliveryCost) || 0) / 100,
        count: count,
        path: '/api/customer/1.4.1.0/basket'
      };
    }

    return { cart: cart, items: items, total: total };
  }

  /**
   * Разбор ответа корзины Пятёрочки (/api/orders/v10/orders/<oid>/).
   */
  function pyatParse(json) {
    if (!json || !json.basket) return null;
    var b = json.basket;
    var itemsList = Array.isArray(b.items) ? b.items : [];
    var cart = {};
    var items = {};

    for (var i = 0; i < itemsList.length; i++) {
      var it = itemsList[i];
      if (!it || it.product_plu == null) continue;
      var sku = String(it.product_plu);
      var weighted = it.uom !== 'шт';
      var q = weighted ? 1 : Math.round(parseFloat(it.quantity) || 0);
      var price = q ? ((parseFloat(it.total) || 0) / q) : 0;
      var gone = it.unavailability != null;
      cart[sku] = q;
      items[sku] = {
        sku: sku,
        q: q,
        price: price,
        gone: gone,
        weighted: weighted
      };
    }

    var count = parseInt(b.items_count, 10) || 0;
    var total = null;
    if (count > 0) {
      var base = parseFloat(b.total_sum) || 0;
      var discount = parseFloat(b.total_discount) || 0;
      var goods = Math.round((base - discount) * 100) / 100;
      total = {
        amount: parseFloat(b.final_sum) || 0,
        goods: goods,
        base: base,
        discount: discount,
        delivery: parseFloat(b.delivery_cost) || 0,
        count: count,
        path: '/api/orders/v10/orders'
      };
    }

    return { cart: cart, items: items, total: total };
  }

  /**
   * Разбор ответа корзины Дикси (/ajax/ajax.php action:calculateDiscounts).
   */
  function dixyParse(json) {
    if (!json || !json.template_data) return null;
    var td = json.template_data;
    var itemsList = Array.isArray(td.items) ? td.items : [];
    var cart = {};
    var items = {};
    var goodsSum = 0;

    for (var i = 0; i < itemsList.length; i++) {
      var it = itemsList[i];
      if (!it || it.xml_id == null) continue;
      var sku = String(it.xml_id);
      var ratio = parseFloat(it.ratio) || 1;
      var itQty = parseFloat(it.quantity) || 0;
      var itPrice = parseFloat(it.priceSimple) || 0;
      var q = Math.round(itQty / ratio);
      var price = itPrice * ratio;
      var gone = it.canBuy === false;
      goodsSum += itPrice * itQty;
      cart[sku] = q;
      items[sku] = {
        sku: sku,
        id: it.id,
        q: q,
        price: price,
        gone: gone,
        ratio: ratio
      };
    }

    var count = parseInt(td.count, 10) || 0;
    var total = null;
    if (count > 0) {
      var base = parseFloat(td.baseBskSumm) || 0;
      var goods = Math.round(goodsSum * 100) / 100;
      var discount = Math.round((base - goods) * 100) / 100;
      total = {
        amount: parseFloat(td.toPay) || 0,
        goods: goods,
        base: base,
        discount: discount,
        delivery: 0,
        count: count,
        path: '/ajax/ajax.php calculateDiscounts'
      };
    }

    return { cart: cart, items: items, total: total };
  }

  /** Чтение куки по имени в браузере. */
  function getCookie(name) {
    if (typeof document === 'undefined' || !document.cookie) return null;
    var parts = document.cookie.split(';');
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i].trim();
      if (p.indexOf(name + '=') === 0) {
        return p.slice(name.length + 1);
      }
    }
    return null;
  }

  /**
   * Запуск сбора корзины запросами сети.
   *
   * @param {Object} input
   * {key, chain, lines: [{sku, target, url}], mine: {...}, want: {...}, drop: bool, pause_ms, burst}
   * @returns {Promise<Object>}
   */
  async function run(input) {
    var tStart = Date.now();
    var chain = input && input.chain;
    var pauseMs = Math.max(300, parseInt(input.pause_ms, 10) || 1000);
    var burst = Math.max(1, parseInt(input.burst, 10) || 3);
    var linesIn = Array.isArray(input.lines) ? input.lines : [];

    function makeFail(stage, status, why, lastCart) {
      var out = {
        ok: false,
        stage: stage,
        status: status || 0,
        why: why || '',
        ms: Date.now() - tStart
      };
      if (lastCart) out.cart = lastCart;
      return out;
    }

    var runners = {
      perekrestok: runPerekrestok,
      pyaterochka: runPyaterochka,
      dixy: runDixy,
      magnit: runMagnit,
      metro: runMetro,
      vprok: runVprok
    };
    if (!Object.prototype.hasOwnProperty.call(runners, chain)) {
      return makeFail('chain', 0, 'неизвестная сеть ' + chain);
    }

    stats = { requests: 0, paced: 0 };
    var runKey = input && input.key != null ? String(input.key) : '';
    var runState = { aborted: false, xhr: null, timer: null, cancelTimer: null };
    if (runKey) activeRuns[runKey] = runState;
    var prevRun = currentRun;
    currentRun = runState;
    var res;
    try {
      res = await runners[chain](input, linesIn, pauseMs, burst, tStart, makeFail);
    } catch (err) {
      clearFreshCart(chain);
      if (err && err.aborted) {
        res = makeFail('aborted', 0, 'корзина поменялась');
      } else {
        res = makeFail('js', 0, 'cartapi.js: ' + (err && err.message ? err.message : String(err)));
      }
    } finally {
      if (runKey && activeRuns[runKey] === runState) delete activeRuns[runKey];
      currentRun = prevRun;
    }
    if (!res || !res.ok) clearFreshCart(chain);
    res.requests = stats.requests;
    res.paced_ms = Math.round(stats.paced);
    return res;
  }

  // --- 1.5 Адаптер Перекрёстка ---
  async function runPerekrestok(input, linesIn, pauseMs, burst, tStart, makeFail) {
    var rawCookie = getCookie('session');
    if (!rawCookie) {
      return makeFail('auth', 0, 'Перекрёсток: нет входа на сайте сети');
    }
    var sess;
    try {
      sess = JSON.parse(decodeURIComponent(rawCookie));
    } catch (e) {
      return makeFail('auth', 0, 'Перекрёсток: нет входа на сайте сети');
    }
    var token = sess && sess.accessToken;
    var dev = (sess && sess.device && sess.device.uuid) || '';
    if (!token) {
      return makeFail('auth', 0, 'Перекрёсток: нет входа на сайте сети');
    }

    function headers(hasBody) {
      var h = {
        'Accept': 'application/json, text/plain, */*',
        'Auth': 'Bearer ' + token,
        'X-Device-Id': dev
      };
      if (hasBody) h['Content-Type'] = 'application/json';
      return h;
    }

    var linesRes = {};
    var opsLines = [];
    for (var i = 0; i < linesIn.length; i++) {
      var l = linesIn[i];
      var s = String(l.sku || '');
      if (!/^\d+$/.test(s)) {
        linesRes[s] = { retry: true };
      } else {
        opsLines.push(l);
      }
    }

    // Быстрая дельта: если корзина читалась < 15 с назад и есть что менять, пропускаем стартовый GET.
    var parsed = null;
    var fresh = getFreshCart('perekrestok', input);
    if (fresh && fresh.parsed) {
      var fastPlan = ops(fresh.parsed.cart, opsLines, input.mine, input.want, input.drop);
      if (fastPlan.set.length || fastPlan.drops.length) {
        parsed = fresh.parsed;
      }
    }

    if (!parsed) {
      await pace(pauseMs, burst);
      var initRes = await httpReq('GET', '/api/customer/1.4.1.0/basket', headers(false));
      if (initRes.status === 401) {
        clearFreshCart('perekrestok');
        return makeFail('auth', 401, 'Перекрёсток: 401');
      }
      if (initRes.status === 0) {
        return makeFail('net', 0, 'Перекрёсток: обрыв сети');
      }
      if (initRes.status >= 400 || !initRes.json) {
        return makeFail('read', initRes.status, 'Перекрёсток: корзина не читается');
      }

      parsed = perekParse(initRes.json);
      if (!parsed) {
        return makeFail('read', initRes.status, 'Перекрёсток: ответ корзины не распознан');
      }
    }

    var lastCart = parsed.cart;
    var lastTotal = parsed.total;
    var quanta = {};
    for (var k in parsed.items) {
      quanta[k] = parsed.items[k].quantum;
    }

    var plan = ops(lastCart, opsLines, input.mine, input.want, input.drop);

    // Kept позиции
    for (var ki = 0; ki < plan.kept.length; ki++) {
      var keptSku = plan.kept[ki];
      var pIt = parsed.items && parsed.items[keptSku];
      var isGone = pIt ? pIt.gone : false;
      linesRes[keptSku] = {
        ok: !isGone,
        how: 'kept',
        put: 0,
        q: lastCart[keptSku] || 0,
        price: pIt ? pIt.price : 0,
        gone: isGone,
        retry: false,
        why: isGone ? 'нет в наличии у сети' : '',
        calls: []
      };
    }

    // Выполнение set
    for (var si = 0; si < plan.set.length; si++) {
      var op = plan.set[si];
      var sku = op.sku;
      var from = op.from;
      var to = op.to;
      var calls = [];
      var qtm = quanta[sku] || 1000;

      if (to === 0) {
        await pace(pauseMs, burst);
        var dUrl = '/api/customer/1.4.1.0/basket/item/plu' + sku;
        var dRes = await httpReq('DELETE', dUrl, headers(false));
        calls.push(formatCall('DELETE', dUrl, dRes));
        if (dRes.status === 401) return makeFail('auth', 401, 'Перекрёсток: 401', lastCart);
        if (dRes.status === 0) return makeFail('net', 0, 'Перекрёсток: обрыв сети', lastCart);
        if (dRes.status >= 400) {
          var w = errText(dRes);
          linesRes[sku] = { ok: false, how: 'request', put: 0, q: from, price: 0, gone: false, retry: false, why: 'сеть ответила ' + dRes.status + ': ' + w, calls: calls };
          continue;
        }
        if (dRes.json) {
          parsed = perekParse(dRes.json) || parsed;
          lastCart = parsed.cart;
          lastTotal = parsed.total;
        }
        linesRes[sku] = { ok: true, how: 'request', put: 0, q: 0, price: 0, gone: false, retry: false, why: '', calls: calls };
      } else if (from === 0) {
        await pace(pauseMs, burst);
        var pUrl = '/api/customer/1.4.1.0/basket/plu' + sku + '/plus';
        var pRes = await httpReq('PUT', pUrl, headers(false));
        calls.push(formatCall('PUT', pUrl, pRes));
        if (pRes.status === 401) return makeFail('auth', 401, 'Перекрёсток: 401', lastCart);
        if (pRes.status === 0) return makeFail('net', 0, 'Перекрёсток: обрыв сети', lastCart);
        if (pRes.status >= 400) {
          var wp = errText(pRes);
          var outP = wp === 'нет в наличии';
          linesRes[sku] = { ok: false, how: 'request', put: 0, q: 0, price: 0, gone: outP, retry: false, why: outP ? 'нет в наличии у сети' : 'сеть ответила ' + pRes.status + ': ' + wp, calls: calls };
          continue;
        }
        if (pRes.json) {
          parsed = perekParse(pRes.json) || parsed;
          lastCart = parsed.cart;
          lastTotal = parsed.total;
          if (parsed.items && parsed.items[sku]) qtm = parsed.items[sku].quantum || qtm;
        }
        if (to > 1) {
          await pace(pauseMs, burst);
          var aUrl = '/api/customer/1.4.1.0/basket/plu' + sku + '/amount';
          var aRes = await httpReq('PUT', aUrl, headers(true), JSON.stringify({ amount: to * qtm }));
          calls.push(formatCall('PUT', aUrl, aRes));
          if (aRes.status === 401) return makeFail('auth', 401, 'Перекрёсток: 401', lastCart);
          if (aRes.status === 0) return makeFail('net', 0, 'Перекрёсток: обрыв сети', lastCart);
          if (aRes.status >= 400) {
            var wa = errText(aRes);
            linesRes[sku] = { ok: false, how: 'request', put: 1, q: 1, price: 0, gone: false, retry: false, why: 'сеть ответила ' + aRes.status + ': ' + wa, calls: calls };
            continue;
          }
          if (aRes.json) {
            parsed = perekParse(aRes.json) || parsed;
            lastCart = parsed.cart;
            lastTotal = parsed.total;
          }
        }
        var curPIt = parsed.items && parsed.items[sku];
        var curQ = lastCart[sku] || 0;
        var itGone = curPIt ? curPIt.gone : false;
        linesRes[sku] = {
          ok: !itGone,
          how: 'request',
          put: Math.max(0, curQ - from),
          q: curQ,
          price: curPIt ? curPIt.price : 0,
          gone: itGone,
          retry: false,
          why: itGone ? 'нет в наличии у сети' : '',
          calls: calls
        };
      } else {
        await pace(pauseMs, burst);
        var uUrl = '/api/customer/1.4.1.0/basket/plu' + sku + '/amount';
        var uRes = await httpReq('PUT', uUrl, headers(true), JSON.stringify({ amount: to * qtm }));
        calls.push(formatCall('PUT', uUrl, uRes));
        if (uRes.status === 401) return makeFail('auth', 401, 'Перекрёсток: 401', lastCart);
        if (uRes.status === 0) return makeFail('net', 0, 'Перекрёсток: обрыв сети', lastCart);
        if (uRes.status >= 400) {
          var wu = errText(uRes);
          linesRes[sku] = { ok: false, how: 'request', put: 0, q: from, price: 0, gone: false, retry: false, why: 'сеть ответила ' + uRes.status + ': ' + wu, calls: calls };
          continue;
        }
        if (uRes.json) {
          parsed = perekParse(uRes.json) || parsed;
          lastCart = parsed.cart;
          lastTotal = parsed.total;
        }
        var curP = parsed.items && parsed.items[sku];
        var curQ2 = lastCart[sku] || 0;
        var gone2 = curP ? curP.gone : false;
        linesRes[sku] = {
          ok: !gone2,
          how: 'request',
          put: Math.max(0, curQ2 - from),
          q: curQ2,
          price: curP ? curP.price : 0,
          gone: gone2,
          retry: false,
          why: gone2 ? 'нет в наличии у сети' : '',
          calls: calls
        };
      }
    }

    // Выполнение drops
    var dropped = 0;
    for (var di = 0; di < plan.drops.length; di++) {
      var dOp = plan.drops[di];
      var dSku = dOp.sku;
      var dFrom = dOp.from;
      var dTo = dOp.to;
      var dQtm = quanta[dSku] || 1000;
      await pace(pauseMs, burst);

      if (dTo === 0) {
        var dropDelUrl = '/api/customer/1.4.1.0/basket/item/plu' + dSku;
        var rDel = await httpReq('DELETE', dropDelUrl, headers(false));
        if (rDel.status === 401) return makeFail('auth', 401, 'Перекрёсток: 401', lastCart);
        if (rDel.status === 0) return makeFail('net', 0, 'Перекрёсток: обрыв сети', lastCart);
        if (rDel.status < 400) {
          dropped += (dFrom - dTo);
          if (rDel.json) {
            parsed = perekParse(rDel.json) || parsed;
            lastCart = parsed.cart;
            lastTotal = parsed.total;
          }
        }
      } else {
        var dropAmtUrl = '/api/customer/1.4.1.0/basket/plu' + dSku + '/amount';
        var rAmt = await httpReq('PUT', dropAmtUrl, headers(true), JSON.stringify({ amount: dTo * dQtm }));
        if (rAmt.status === 401) return makeFail('auth', 401, 'Перекрёсток: 401', lastCart);
        if (rAmt.status === 0) return makeFail('net', 0, 'Перекрёсток: обрыв сети', lastCart);
        if (rAmt.status < 400) {
          dropped += (dFrom - dTo);
          if (rAmt.json) {
            parsed = perekParse(rAmt.json) || parsed;
            lastCart = parsed.cart;
            lastTotal = parsed.total;
          }
        }
      }
    }

    setFreshCart('perekrestok', { parsed: parsed });
    return {
      ok: true,
      ms: Date.now() - tStart,
      lines: linesRes,
      cart: lastCart,
      dropped: dropped,
      total: lastTotal
    };
  }

  // --- 1.6 Адаптер Пятёрочки ---
  async function runPyaterochka(input, linesIn, pauseMs, burst, tStart, makeFail) {
    var rawCartStore = (typeof localStorage !== 'undefined' && localStorage['cart-store']) || '{}';
    var cs;
    try {
      cs = JSON.parse(rawCartStore);
    } catch (e) {
      cs = {};
    }
    var oid = cs && cs.orderId;
    var dev = (typeof localStorage !== 'undefined' && localStorage.deviceId) || '';
    if (!dev) {
      return makeFail('auth', 0, 'Пятёрочка: нет deviceId');
    }

    function headers(hasBody) {
      var h = {
        'X-PLATFORM': 'webapp',
        'X-APP-VERSION': '0.1.1.dev',
        'X-TENANT-ID': 'TC5',
        'x-capabilities': 'tenant=ts5;clientVersion=0.1.1;timezone=+0;OS=webapp/;teamId=x5m;',
        'X-DEVICE-ID': dev,
        'Accept': 'application/json'
      };
      if (hasBody) h['Content-Type'] = 'application/json';
      return h;
    }

    if (!oid) {
      // Сайт теряет orderId из cart-store, а корзина в сети жива (эмулятор, 09.11.2026):
      // берём действующую. Оплаченный или оформленный заказ не трогаем никогда.
      await pace(pauseMs, burst);
      var actRes = await httpReq('GET', 'https://5d.5ka.ru/api/orders/v3/orders/?in_action=true', headers(false), null, true);
      if (actRes.status === 0) {
        return makeFail('net', 0, 'Пятёрочка: обрыв сети');
      }
      var act = ((actRes.json && actRes.json.items) || []).filter(function (o) {
        return o && o.is_active && !o.payed_time && o.status === 0;
      })[0];
      oid = act && act.id;
    }
    if (!oid) {
      return makeFail('address', 0, 'Пятёрочка: в приложении сети не выбран адрес доставки');
    }

    var parsed = null;
    var fresh = getFreshCart('pyaterochka', input);
    if (fresh && fresh.parsed && fresh.oid === oid) {
      var prelimOps = [];
      for (var fi = 0; fi < linesIn.length; fi++) {
        var fs = String(linesIn[fi].sku || '');
        if (/^\d+$/.test(fs) && !(fresh.parsed.items && fresh.parsed.items[fs] && fresh.parsed.items[fs].weighted)) {
          prelimOps.push(linesIn[fi]);
        }
      }
      var fastPlan = ops(fresh.parsed.cart, prelimOps, input.mine, input.want, input.drop);
      if (fastPlan.set.length || fastPlan.drops.length) {
        parsed = fresh.parsed;
      }
    }

    if (!parsed) {
      await pace(pauseMs, burst);
      var initUrl = 'https://5d.5ka.ru/api/orders/v10/orders/' + encodeURIComponent(oid) + '/';
      var initRes = await httpReq('GET', initUrl, headers(false), null, true);
      if (initRes.status === 401) {
        clearFreshCart('pyaterochka');
        return makeFail('auth', 401, 'Пятёрочка: 401');
      }
      if (initRes.status === 0) {
        return makeFail('net', 0, 'Пятёрочка: обрыв сети');
      }
      if (initRes.status >= 400 || !initRes.json) {
        return makeFail('read', initRes.status, 'Пятёрочка: корзина не читается');
      }

      parsed = pyatParse(initRes.json);
      if (!parsed) {
        return makeFail('read', initRes.status, 'Пятёрочка: ответ корзины не распознан');
      }
    }

    var lastCart = parsed.cart;
    var lastTotal = parsed.total;

    var linesRes = {};
    var opsLines = [];
    for (var i = 0; i < linesIn.length; i++) {
      var l = linesIn[i];
      var s = String(l.sku || '');
      if (!/^\d+$/.test(s)) {
        linesRes[s] = { retry: true };
      } else if (parsed.items && parsed.items[s] && parsed.items[s].weighted) {
        // shortcut: весовые Пятёрочки кладём касаниями, пока не замерен их qty
        linesRes[s] = { retry: true };
      } else {
        opsLines.push(l);
      }
    }

    var plan = ops(lastCart, opsLines, input.mine, input.want, input.drop);

    // Kept позиции
    for (var ki = 0; ki < plan.kept.length; ki++) {
      var keptSku = plan.kept[ki];
      var pIt = parsed.items && parsed.items[keptSku];
      var isGone = pIt ? pIt.gone : false;
      linesRes[keptSku] = {
        ok: !isGone,
        how: 'kept',
        put: 0,
        q: lastCart[keptSku] || 0,
        price: pIt ? pIt.price : 0,
        gone: isGone,
        retry: false,
        why: isGone ? 'нет в наличии у сети' : '',
        calls: []
      };
    }

    // Выполнение set
    for (var si = 0; si < plan.set.length; si++) {
      var op = plan.set[si];
      var sku = op.sku;
      var from = op.from;
      var to = op.to;
      var calls = [];
      await pace(pauseMs, burst);

      if (to === 0) {
        var delUrl = 'https://5d.5ka.ru/api/orders/v8/orders/' + encodeURIComponent(oid) + '/item/' + encodeURIComponent(sku) + '/';
        var dRes = await httpReq('DELETE', delUrl, headers(false), null, true);
        calls.push(formatCall('DELETE', delUrl, dRes, oid));
        if (dRes.status === 401) return makeFail('auth', 401, 'Пятёрочка: 401', lastCart);
        if (dRes.status === 0) return makeFail('net', 0, 'Пятёрочка: обрыв сети', lastCart);
        if (dRes.status >= 400) {
          var wd = errText(dRes);
          linesRes[sku] = { ok: false, how: 'request', put: 0, q: from, price: 0, gone: false, retry: false, why: 'сеть ответила ' + dRes.status + ': ' + wd, calls: calls };
          continue;
        }
        if (dRes.json) {
          parsed = pyatParse(dRes.json) || parsed;
          lastCart = parsed.cart;
          lastTotal = parsed.total;
        }
        linesRes[sku] = { ok: true, how: 'request', put: 0, q: 0, price: 0, gone: false, retry: false, why: '', calls: calls };
      } else if (from === 0) {
        var postUrl = 'https://5d.5ka.ru/api/orders/v8/orders/' + encodeURIComponent(oid) + '/item/';
        var postBody = JSON.stringify({ plu: Number(sku), qty: to, uom: 'шт' });
        var pRes = await httpReq('POST', postUrl, headers(true), postBody, true);
        calls.push(formatCall('POST', postUrl, pRes, oid));
        if (pRes.status === 401) return makeFail('auth', 401, 'Пятёрочка: 401', lastCart);
        if (pRes.status === 0) return makeFail('net', 0, 'Пятёрочка: обрыв сети', lastCart);
        if (pRes.status >= 400) {
          var wp = errText(pRes);
          var outP = wp === 'нет в наличии';
          linesRes[sku] = { ok: false, how: 'request', put: 0, q: 0, price: 0, gone: outP, retry: false, why: outP ? 'нет в наличии у сети' : 'сеть ответила ' + pRes.status + ': ' + wp, calls: calls };
          continue;
        }
        if (pRes.json) {
          parsed = pyatParse(pRes.json) || parsed;
          lastCart = parsed.cart;
          lastTotal = parsed.total;
        }
        var curP = parsed.items && parsed.items[sku];
        var curQ = lastCart[sku] || 0;
        var itGone = curP ? curP.gone : false;
        linesRes[sku] = {
          ok: !itGone,
          how: 'request',
          put: Math.max(0, curQ - from),
          q: curQ,
          price: curP ? curP.price : 0,
          gone: itGone,
          retry: false,
          why: itGone ? 'нет в наличии у сети' : '',
          calls: calls
        };
      } else {
        var putUrl = 'https://5d.5ka.ru/api/orders/v8/orders/' + encodeURIComponent(oid) + '/item/' + encodeURIComponent(sku) + '/';
        var putBody = JSON.stringify({ qty: to });
        var putRes = await httpReq('PUT', putUrl, headers(true), putBody, true);
        calls.push(formatCall('PUT', putUrl, putRes, oid));
        if (putRes.status === 401) return makeFail('auth', 401, 'Пятёрочка: 401', lastCart);
        if (putRes.status === 0) return makeFail('net', 0, 'Пятёрочка: обрыв сети', lastCart);
        if (putRes.status >= 400) {
          var wu = errText(putRes);
          linesRes[sku] = { ok: false, how: 'request', put: 0, q: from, price: 0, gone: false, retry: false, why: 'сеть ответила ' + putRes.status + ': ' + wu, calls: calls };
          continue;
        }
        if (putRes.json) {
          parsed = pyatParse(putRes.json) || parsed;
          lastCart = parsed.cart;
          lastTotal = parsed.total;
        }
        var curP2 = parsed.items && parsed.items[sku];
        var curQ2 = lastCart[sku] || 0;
        var itGone2 = curP2 ? curP2.gone : false;
        linesRes[sku] = {
          ok: !itGone2,
          how: 'request',
          put: Math.max(0, curQ2 - from),
          q: curQ2,
          price: curP2 ? curP2.price : 0,
          gone: itGone2,
          retry: false,
          why: itGone2 ? 'нет в наличии у сети' : '',
          calls: calls
        };
      }
    }

    // Выполнение drops (весовые не трогаем)
    var dropped = 0;
    for (var di = 0; di < plan.drops.length; di++) {
      var dOp = plan.drops[di];
      var dSku = dOp.sku;
      var dFrom = dOp.from;
      var dTo = dOp.to;
      if (parsed.items && parsed.items[dSku] && parsed.items[dSku].weighted) continue;
      await pace(pauseMs, burst);

      if (dTo === 0) {
        var dropDelUrl = 'https://5d.5ka.ru/api/orders/v8/orders/' + encodeURIComponent(oid) + '/item/' + encodeURIComponent(dSku) + '/';
        var rDel = await httpReq('DELETE', dropDelUrl, headers(false), null, true);
        if (rDel.status === 401) return makeFail('auth', 401, 'Пятёрочка: 401', lastCart);
        if (rDel.status === 0) return makeFail('net', 0, 'Пятёрочка: обрыв сети', lastCart);
        if (rDel.status < 400) {
          dropped += (dFrom - dTo);
          if (rDel.json) {
            parsed = pyatParse(rDel.json) || parsed;
            lastCart = parsed.cart;
            lastTotal = parsed.total;
          }
        }
      } else {
        var dropPutUrl = 'https://5d.5ka.ru/api/orders/v8/orders/' + encodeURIComponent(oid) + '/item/' + encodeURIComponent(dSku) + '/';
        var rPut = await httpReq('PUT', dropPutUrl, headers(true), JSON.stringify({ qty: dTo }), true);
        if (rPut.status === 401) return makeFail('auth', 401, 'Пятёрочка: 401', lastCart);
        if (rPut.status === 0) return makeFail('net', 0, 'Пятёрочка: обрыв сети', lastCart);
        if (rPut.status < 400) {
          dropped += (dFrom - dTo);
          if (rPut.json) {
            parsed = pyatParse(rPut.json) || parsed;
            lastCart = parsed.cart;
            lastTotal = parsed.total;
          }
        }
      }
    }

    setFreshCart('pyaterochka', { parsed: parsed, oid: oid });
    return {
      ok: true,
      ms: Date.now() - tStart,
      lines: linesRes,
      cart: lastCart,
      dropped: dropped,
      total: lastTotal
    };
  }

  // --- 1.7 Адаптер Дикси ---
  function dixyFormData(action, quantity, id) {
    var fd = new FormData();
    fd.append('sender', 'basket');
    fd.append('action', action);
    fd.append('quantity', String(quantity != null ? quantity : 0));
    fd.append('id', String(id != null ? id : 0));
    fd.append('initiator', 'listingInitiator');
    fd.append('orderData', '');
    fd.append('promocode', '');
    return fd;
  }

  async function runDixy(input, linesIn, pauseMs, burst, tStart, makeFail) {
    // Магазин проверяем раз за жизнь окна: сброшенный магазин выдаст и чтение корзины (showMap).
    // sessionStorage, а не переменная страницы: окно между сборками заново открывает robots.txt.
    var storeOk = false;
    try { storeOk = sessionStorage['kz.dixy.store'] === '1'; } catch (e) {}
    if (!storeOk) {
      await pace(pauseMs, burst);
      var chkFd = new FormData();
      chkFd.append('action', 'isStoreDefined');
      var chkRes = await httpReq('POST', '/ajax/ajax.php', null, chkFd);
      if (chkRes.status === 0) {
        return makeFail('net', 0, 'Дикси: обрыв сети');
      }
      if (!chkRes.json || !chkRes.json.storeDefined) {
        return makeFail('address', chkRes.status, 'Дикси: на сайте сети не выбран магазин или адрес');
      }
      try { sessionStorage['kz.dixy.store'] = '1'; } catch (e) {}
    }

    await pace(pauseMs, burst);
    var initRes = await httpReq('POST', '/ajax/ajax.php', null, dixyFormData('calculateDiscounts', 0, 0));
    if (initRes.status === 0) {
      return makeFail('net', 0, 'Дикси: обрыв сети');
    }
    if (initRes.json && initRes.json.action === 'showMap') {
      try { sessionStorage.removeItem('kz.dixy.store'); } catch (e) {}
      return makeFail('address', initRes.status, 'Дикси: на сайте сети не выбран магазин или адрес');
    }

    var parsed = dixyParse(initRes.json);
    if (!parsed) {
      return makeFail('read', initRes.status, 'Дикси: корзина не читается');
    }

    var lastCart = parsed.cart;
    var lastTotal = parsed.total;

    // Кэш числовых id Дикси: объединяем localStorage, серверный кэш из наряда и текущую корзину
    var dixyIds = {};
    try {
      if (typeof localStorage !== 'undefined' && localStorage['kz.dixy.ids']) {
        dixyIds = JSON.parse(localStorage['kz.dixy.ids']);
      }
    } catch (e) {
      dixyIds = {};
    }

    var resolvedNids = {};
    if (input.dixy_nids && typeof input.dixy_nids === 'object') {
      for (var sk in input.dixy_nids) {
        if (!Object.prototype.hasOwnProperty.call(input.dixy_nids, sk)) continue;
        var sv = input.dixy_nids[sk];
        if (sv && sv.id && !dixyIds[sk]) {
          dixyIds[sk] = { id: String(sv.id), ratio: parseFloat(sv.ratio) || 1 };
        }
      }
    }
    for (var li = 0; li < linesIn.length; li++) {
      var ln = linesIn[li];
      var lSku = String(ln.sku || '');
      if (lSku && ln.nid && !dixyIds[lSku]) {
        dixyIds[lSku] = { id: String(ln.nid), ratio: parseFloat(ln.ratio) || 1 };
      }
    }

    for (var k in parsed.items) {
      var pEntry = { id: String(parsed.items[k].id), ratio: parsed.items[k].ratio || 1 };
      dixyIds[k] = pEntry;
      resolvedNids[k] = pEntry;
    }

    var linesRes = {};
    var opsLines = [];
    var changedCache = false;

    for (var i = 0; i < linesIn.length; i++) {
      var l = linesIn[i];
      var sku = String(l.sku || '');
      if (dixyIds[sku]) {
        resolvedNids[sku] = dixyIds[sku];
        opsLines.push(l);
        continue;
      }
      // Поиск на карточке товара
      var urlStr = l.url || '';
      var canFetch = false;
      try {
        if (urlStr && typeof location !== 'undefined') {
          var uObj = new URL(urlStr, location.origin);
          if (uObj.origin === location.origin) canFetch = true;
        }
      } catch (e) {}

      if (!canFetch) {
        linesRes[sku] = { retry: true };
        continue;
      }

      await pace(pauseMs, burst);
      var cardRes = await httpReq('GET', urlStr);
      var cCalls = [formatCall('GET', urlStr, cardRes)];
      if (cardRes.status === 0) return makeFail('net', 0, 'Дикси: обрыв сети', lastCart);

      var m = /data-bsk_data="(\d+)\s+([\d.]+)\s+([\d.]+)\s+(\S+)\s+([\d.]+)"/.exec(cardRes.text);
      if (!m) {
        // Отказ, а не повтор: телефон запомнит его на 30 мин и не станет тянуть карточку на
        // каждой правке (замер 09.11.2026: лишний запрос в каждой сборке Дикси). Сбой сервера — повтор.
        var busy = cardRes.status === 429 || cardRes.status >= 500;
        linesRes[sku] = busy ? { retry: true } : {
          ok: false, how: 'request', put: 0, q: 0, price: 0, gone: false, retry: false,
          why: 'карточка Дикси не отдала товар (' + cardRes.status + ')', calls: cCalls
        };
        continue;
      }

      var numId = m[1];
      var numRatio = parseFloat(m[2]) || 1;
      var stock = parseFloat(m[3]) || 0;
      if (stock === 0) {
        linesRes[sku] = {
          ok: false,
          how: 'request',
          put: 0,
          q: 0,
          price: 0,
          gone: true,
          retry: false,
          why: 'нет в наличии у сети',
          calls: cCalls
        };
      } else {
        dixyIds[sku] = { id: numId, ratio: numRatio };
        resolvedNids[sku] = { id: numId, ratio: numRatio };
        changedCache = true;
        opsLines.push(l);
      }
    }

    if (changedCache && typeof localStorage !== 'undefined') {
      try {
        localStorage['kz.dixy.ids'] = JSON.stringify(dixyIds);
      } catch (e) {}
    }

    var plan = ops(lastCart, opsLines, input.mine, input.want, input.drop);

    // Kept позиции
    for (var ki = 0; ki < plan.kept.length; ki++) {
      var keptSku = plan.kept[ki];
      var pIt = parsed.items && parsed.items[keptSku];
      var isGone = pIt ? pIt.gone : false;
      linesRes[keptSku] = {
        ok: !isGone,
        how: 'kept',
        put: 0,
        q: lastCart[keptSku] || 0,
        price: pIt ? pIt.price : 0,
        gone: isGone,
        retry: false,
        why: isGone ? 'нет в наличии у сети' : '',
        calls: []
      };
    }

    // Выполнение set
    for (var si = 0; si < plan.set.length; si++) {
      var op = plan.set[si];
      var sSku = op.sku;
      var from = op.from;
      var to = op.to;
      var info = dixyIds[sSku];
      if (!info) {
        linesRes[sSku] = { retry: true };
        continue;
      }
      var nid = info.id;
      var ratio = info.ratio || 1;
      var calls = [];
      await pace(pauseMs, burst);

      if (to === 0) {
        var rRes = await httpReq('POST', '/ajax/ajax.php', null, dixyFormData('remove', 0, nid));
        calls.push(formatCall('POST', '/ajax/ajax.php remove', rRes));
        if (rRes.status === 0) return makeFail('net', 0, 'Дикси: обрыв сети', lastCart);
        if (rRes.status >= 400) {
          linesRes[sSku] = { ok: false, how: 'request', put: 0, q: from, price: 0, gone: false, retry: false, why: 'сеть ответила ' + rRes.status + ': ' + rRes.text.slice(0, 80), calls: calls };
          continue;
        }
        linesRes[sSku] = { ok: true, how: 'request', put: 0, q: 0, price: 0, gone: false, retry: false, why: '', calls: calls };
      } else if (from === 0) {
        var addRes = await httpReq('POST', '/ajax/ajax.php', null, dixyFormData('add', 1, nid));
        calls.push(formatCall('POST', '/ajax/ajax.php add', addRes));
        if (addRes.status === 0) return makeFail('net', 0, 'Дикси: обрыв сети', lastCart);
        if (addRes.status >= 400) {
          linesRes[sSku] = { ok: false, how: 'request', put: 0, q: 0, price: 0, gone: false, retry: false, why: 'сеть ответила ' + addRes.status + ': ' + addRes.text.slice(0, 80), calls: calls };
          continue;
        }
        var lastWriteRes = addRes;
        if (to > 1) {
          await pace(pauseMs, burst);
          var upRes = await httpReq('POST', '/ajax/ajax.php', null, dixyFormData('update', to * ratio, nid));
          calls.push(formatCall('POST', '/ajax/ajax.php update', upRes));
          if (upRes.status === 0) return makeFail('net', 0, 'Дикси: обрыв сети', lastCart);
          if (upRes.status >= 400) {
            linesRes[sSku] = { ok: false, how: 'request', put: 1, q: 1, price: 0, gone: false, retry: false, why: 'сеть ответила ' + upRes.status + ': ' + upRes.text.slice(0, 80), calls: calls };
            continue;
          }
          lastWriteRes = upRes;
        }
        var cp = lastWriteRes.json && lastWriteRes.json.changedProducts && lastWriteRes.json.changedProducts[0];
        var cpQty = cp ? (parseFloat(cp.quantity) || 0) : 0;
        var cpAmt = cp ? (parseFloat(cp.amount) || 0) : 0;
        var bsLimit = cp && cp['bs-limit'] === true;
        if (bsLimit || cpQty < to * ratio) {
          var gave = Math.round(cpQty / ratio);
          var inStock = Math.round(cpAmt / ratio);
          linesRes[sSku] = {
            ok: false,
            how: 'request',
            put: Math.max(0, gave - from),
            q: gave,
            price: 0,
            gone: false,
            retry: false,
            why: 'сеть дала ' + gave + ' из ' + to + ': в наличии ' + inStock,
            calls: calls
          };
        } else {
          linesRes[sSku] = {
            ok: true,
            how: 'request',
            put: to - from,
            q: to,
            price: 0,
            gone: false,
            retry: false,
            why: '',
            calls: calls
          };
        }
      } else {
        var uRes = await httpReq('POST', '/ajax/ajax.php', null, dixyFormData('update', to * ratio, nid));
        calls.push(formatCall('POST', '/ajax/ajax.php update', uRes));
        if (uRes.status === 0) return makeFail('net', 0, 'Дикси: обрыв сети', lastCart);
        if (uRes.status >= 400) {
          linesRes[sSku] = { ok: false, how: 'request', put: 0, q: from, price: 0, gone: false, retry: false, why: 'сеть ответила ' + uRes.status + ': ' + uRes.text.slice(0, 80), calls: calls };
          continue;
        }
        var cp2 = uRes.json && uRes.json.changedProducts && uRes.json.changedProducts[0];
        var cpQty2 = cp2 ? (parseFloat(cp2.quantity) || 0) : 0;
        var cpAmt2 = cp2 ? (parseFloat(cp2.amount) || 0) : 0;
        var bsLimit2 = cp2 && cp2['bs-limit'] === true;
        if (bsLimit2 || cpQty2 < to * ratio) {
          var gave2 = Math.round(cpQty2 / ratio);
          var inStock2 = Math.round(cpAmt2 / ratio);
          linesRes[sSku] = {
            ok: false,
            how: 'request',
            put: Math.max(0, gave2 - from),
            q: gave2,
            price: 0,
            gone: false,
            retry: false,
            why: 'сеть дала ' + gave2 + ' из ' + to + ': в наличии ' + inStock2,
            calls: calls
          };
        } else {
          linesRes[sSku] = {
            ok: true,
            how: 'request',
            put: to - from,
            q: to,
            price: 0,
            gone: false,
            retry: false,
            why: '',
            calls: calls
          };
        }
      }
    }

    // Выполнение drops для Дикси
    var dropped = 0;
    for (var di = 0; di < plan.drops.length; di++) {
      var dOp = plan.drops[di];
      var dSku = dOp.sku;
      var dFrom = dOp.from;
      var dTo = dOp.to;
      var dInfo = dixyIds[dSku];
      if (!dInfo) continue;
      var dNid = dInfo.id;
      var dRatio = dInfo.ratio || 1;
      await pace(pauseMs, burst);

      if (dTo === 0) {
        var drRes = await httpReq('POST', '/ajax/ajax.php', null, dixyFormData('remove', 0, dNid));
        if (drRes.status === 0) return makeFail('net', 0, 'Дикси: обрыв сети', lastCart);
        if (drRes.status < 400) dropped += (dFrom - dTo);
      } else {
        var duRes = await httpReq('POST', '/ajax/ajax.php', null, dixyFormData('update', dTo * dRatio, dNid));
        if (duRes.status === 0) return makeFail('net', 0, 'Дикси: обрыв сети', lastCart);
        if (duRes.status < 400) dropped += (dFrom - dTo);
      }
    }

    // Финальное чтение корзины и итога — только если что-то меняли: иначе итог дало первое чтение.
    var finRes = { status: 0, json: null };
    if (plan.set.length || plan.drops.length) {
      await pace(pauseMs, burst);
      finRes = await httpReq('POST', '/ajax/ajax.php', null, dixyFormData('calculateDiscounts', 0, 0));
    }
    if (finRes.status && finRes.status < 400 && finRes.json) {
      var finParsed = dixyParse(finRes.json);
      if (finParsed) {
        lastCart = finParsed.cart;
        lastTotal = finParsed.total;
        for (var skuKey in linesRes) {
          if (!Object.prototype.hasOwnProperty.call(linesRes, skuKey)) continue;
          var itInfo = finParsed.items && finParsed.items[skuKey];
          if (itInfo) {
            linesRes[skuKey].price = itInfo.price;
            if (itInfo.gone) {
              linesRes[skuKey].gone = true;
              linesRes[skuKey].ok = false;
              linesRes[skuKey].why = 'нет в наличии у сети';
            }
          }
        }
      }
    }

    var dixyOut = {
      ok: true,
      ms: Date.now() - tStart,
      lines: linesRes,
      cart: lastCart,
      dropped: dropped,
      total: lastTotal
    };
    if (Object.keys(resolvedNids).length > 0) {
      dixyOut.dixy_nids = resolvedNids;
    }
    return dixyOut;
  }

  // --- Адаптер Магнита (APK 1.26) ---
  // Протокол — из кода сайта magnit.ru (09.11.2026) и замеров на эмуляторе 18.10.2026:
  // GET /webgate/v1/carts/lite — корзина (items[]: goodId, offerId, qnty);
  // PUT того же адреса {items:[{goodId, offerId, qnty}]} СТАВИТ число названным позициям,
  // прочие не трогает, в ответе корзина целиком (app/phonecart.py, QTY_SETS и FULL_CART);
  // DELETE того же адреса очищает корзину ЦЕЛИКОМ — его не зовём никогда.
  // Вход живёт в куке mg_at минуты три, сайт на 401 зовёт POST /_auth/refresh и повторяет.
  // Заголовки сайта (x-service, x-store-code, устройство, версия) — из образца, выученного
  // касанием (tpl.magnit): без него сборка уходит на касания и выучит его там.
  var MAGNIT_CART = '/webgate/v1/carts/lite';
  var MAGNIT_PREVIEW = '/webgate/v1/checkout/preview';
  var MAGNIT_OUT = /unavailable|out_?of_?stock|not_?available|sold_?out/i;

  /**
   * Корзина Магнита из ответа carts/lite (чтение и запись отдают её целиком). Весовой товар
   * лежит долями (0,4 кг) — такие артикулы в frac: упаковками их не поставить.
   */
  function magnitParse(json) {
    if (!json || !Array.isArray(json.items)) return null;
    var cart = {};
    var frac = {};
    for (var i = 0; i < json.items.length; i++) {
      var it = json.items[i];
      var id = it && (it.goodId != null ? it.goodId : it.offerId);
      if (id == null || id === '') continue;
      var sku = String(id);
      var q = parseFloat(it.qnty) || 0;
      if (q !== Math.round(q)) frac[sku] = true;
      cart[sku] = (cart[sku] || 0) + (q > 0 ? Math.max(1, Math.round(q)) : 0);
    }
    return { cart: cart, frac: frac };
  }

  /**
   * Итог и наличие из оформления Магнита (checkout/preview сверен со страницей корзины
   * 17.10.2026, app/phonecart.py API_TOTALS). Суммы в копейках; доставка — строки
   * summary.prices с «доставк» в названии, остальное сверх товаров телефон покажет сборкой.
   * Позиции, которых нет в магазине, лежат под ключами вроде unavailableItems или с available: false.
   * shortcut: цен позиций не берём — prices.totalFinalPrice строки не сверен (за строку или
   * за штуку); цены Магнита серверу и так даёт каталог.
   */
  function magnitPreview(json, skus) {
    var s = json && json.summary;
    if (!s || typeof s.totalFinalPrice !== 'number') return null;
    var delivery = 0;
    var rows = [].concat(s.prices || [], s.additionalPrices || []);
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      if (r && typeof r.value === 'number' && /доставк/i.test(String(r.title || ''))) delivery += r.value;
    }
    var base = parseFloat(s.totalBasePrice) || 0;
    var goods = parseFloat(s.itemsTotalSalePrice) || 0;
    var gone = {};
    (function walk(o, under, d) {
      if (!o || typeof o !== 'object' || d > 8) return;
      var id = o.goodId != null ? o.goodId : o.offerId;
      if (id != null && skus[String(id)] && o.available === false) gone[String(id)] = true;
      for (var k in o) {
        if (!Object.prototype.hasOwnProperty.call(o, k)) continue;
        var v = o[k];
        if (under && (typeof v === 'string' || typeof v === 'number') && skus[String(v)]) gone[String(v)] = true;
        else if (v && typeof v === 'object') walk(v, under || MAGNIT_OUT.test(k), d + 1);
      }
    })(json, false, 0);
    return {
      total: {
        amount: s.totalFinalPrice / 100,
        goods: goods / 100,
        base: base / 100,
        discount: Math.max(0, base - goods) / 100,
        delivery: delivery / 100,
        count: parseInt(s.itemsCount, 10) || 0,
        path: MAGNIT_PREVIEW
      },
      gone: gone
    };
  }

  async function runMagnit(input, linesIn, pauseMs, burst, tStart, makeFail) {
    var th = input.tpl && input.tpl.h;
    if (!th || typeof th !== 'object' || !Object.keys(th).length) {
      return makeFail('tpl', 0, 'Магнит: запрос сети ещё не выучен');
    }
    // Магазин и устройство — из свежих кук: человек мог сменить адрес после урока.
    var base = {};
    for (var hk in th) {
      if (!Object.prototype.hasOwnProperty.call(th, hk)) continue;
      var lk = hk.toLowerCase();
      if (/^(content-type|content-length|cookie|host|origin|referer|user-agent|accept-encoding|connection)$/.test(lk)) continue;
      base[lk] = String(th[hk]);
    }
    // Куки в кавычках JSON: shopCode=%22992301%22 (замер 20.09.2026, app/shopbrowser/point.py).
    function cookieText(name) {
      var v = getCookie(name);
      return v ? decodeURIComponent(v).replace(/^"|"$/g, '') : '';
    }
    var shop = cookieText('shopCode');
    if (shop) base['x-store-code'] = shop;
    var udi = cookieText('mg_udi');
    if (udi) base['x-device-id'] = udi;
    base['x-magnit-bff'] = '1';

    function headers(hasBody) {
      var h = {};
      for (var k in base) h[k] = base[k];
      if (hasBody) h['content-type'] = 'application/json';
      return h;
    }

    // Запрос с одним обновлением входа на 401 за сборку, как делает сам сайт.
    var refreshed = false;
    async function call(method, url, body) {
      await pace(pauseMs, burst);
      var res = await httpReq(method, url, headers(body != null), body);
      if (res.status === 401 && !refreshed) {
        refreshed = true;
        await pace(pauseMs, burst);
        var rf = await httpReq('POST', '/_auth/refresh', { 'x-magnit-bff': '1', 'content-type': 'application/json' });
        if (rf.status >= 200 && rf.status < 300 && rf.json && rf.json.authorized === true) {
          await pace(pauseMs, burst);
          res = await httpReq(method, url, headers(body != null), body);
        }
      }
      return res;
    }

    var parsed = null;
    var fresh = getFreshCart('magnit', input);
    if (fresh && fresh.parsed && fresh.shop === (base['x-store-code'] || '')) {
      var mPrelim = [];
      for (var mi = 0; mi < linesIn.length; mi++) {
        var ms = String(linesIn[mi].sku || '');
        if (/^\d+$/.test(ms) && !(fresh.parsed.frac && fresh.parsed.frac[ms])) {
          mPrelim.push(linesIn[mi]);
        }
      }
      var mFast = ops(fresh.parsed.cart, mPrelim, input.mine, input.want, input.drop);
      if (mFast.set.length || mFast.drops.length) {
        parsed = fresh.parsed;
      }
    }

    if (!parsed) {
      var initRes = await call('GET', MAGNIT_CART);
      if (initRes.status === 401) {
        clearFreshCart('magnit');
        return makeFail('auth', 401, 'Магнит: нет входа на сайте сети');
      }
      if (initRes.status === 0) return makeFail('net', 0, 'Магнит: обрыв сети');
      parsed = initRes.status < 400 ? magnitParse(initRes.json) : null;
      if (!parsed) return makeFail('read', initRes.status, 'Магнит: корзина не читается');
    }
    var lastCart = parsed.cart;

    var linesRes = {};
    var opsLines = [];
    for (var i = 0; i < linesIn.length; i++) {
      var s = String(linesIn[i].sku || '');
      // Весовой (лежит долями) — касаниями: запрос ставит число упаковок, а не вес.
      if (!/^\d+$/.test(s) || parsed.frac[s]) linesRes[s] = { retry: true };
      else opsLines.push(linesIn[i]);
    }
    var plan = ops(lastCart, opsLines, input.mine, input.want, input.drop);
    for (var ki = 0; ki < plan.kept.length; ki++) {
      var kSku = plan.kept[ki];
      linesRes[kSku] = { ok: true, how: 'kept', put: 0, q: lastCart[kSku] || 0, price: 0, gone: false, retry: false, why: '', calls: [] };
    }

    // Позиции и своё лишнее — одним запросом; снятие до нуля — отдельным: что qnty 0
    // убирает строку, не замерено, и его отказ не должен сорвать остальное. Весовое
    // своё лишнее снимается только целиком: долю упаковками не поставить.
    var inSet = {};
    for (var si = 0; si < plan.set.length; si++) inSet[plan.set[si].sku] = true;
    var drops = plan.drops.filter(function (d) { return !inSet[d.sku] && !(parsed.frac[d.sku] && d.to > 0); });
    var zeroOps = drops.filter(function (d) { return d.to === 0; });
    var setOps = plan.set.concat(drops.filter(function (d) { return d.to > 0; }));

    function body(list) {
      return JSON.stringify({ items: list.map(function (op) { return { goodId: op.sku, offerId: op.sku, qnty: op.to }; }) });
    }

    // Один PUT на список; ответ — вся корзина, не разобрали его — перечитываем.
    async function put(list) {
      var r = await call('PUT', MAGNIT_CART, body(list));
      var res = { status: r.status, why: '', calls: [formatCall('PUT', MAGNIT_CART, r)] };
      if (r.status < 200 || r.status >= 300) {
        res.why = r.status ? 'сеть ответила ' + r.status + ': ' + errText(r) : 'обрыв сети';
        return res;
      }
      var p = magnitParse(r.json);
      if (!p) {
        var again = await call('GET', MAGNIT_CART);
        p = again.status >= 200 && again.status < 300 ? magnitParse(again.json) : null;
      }
      if (p) lastCart = p.cart;
      else res.why = 'ответ на запись не распознан';
      return res;
    }

    // Запись не принята — образец устарел или сеть не пускает: сборка уходит на касания, они
    // и выучат запрос заново. По одной не перебираем: сайт сам шлёт корзину целиком.
    if (setOps.length) {
      var setRes = await put(setOps);
      if (setRes.why) {
        clearFreshCart('magnit');
        var stage = setRes.status === 401 ? 'auth' : (setRes.status ? 'write' : 'net');
        return makeFail(stage, setRes.status, 'Магнит: ' + setRes.why, lastCart);
      }
      for (var oi = 0; oi < plan.set.length; oi++) {
        var op = plan.set[oi];
        var q = lastCart[op.sku] || 0;
        var landed = q >= op.to;
        linesRes[op.sku] = {
          ok: landed, how: 'request', put: Math.max(0, q - op.from), q: q, price: 0, gone: false, retry: false,
          why: landed ? '' : (q > op.from ? 'сеть положила ' + q + ' из ' + op.to : 'сеть не положила'),
          calls: setRes.calls
        };
      }
    }
    if (zeroOps.length) await put(zeroOps);   // отказ снятия положенному не мешает
    var dropped = 0;
    for (var dj = 0; dj < drops.length; dj++) {
      dropped += Math.max(0, drops[dj].from - Math.max(drops[dj].to, lastCart[drops[dj].sku] || 0));
    }

    // Итог и наличие — из оформления; не ответило — без итога, позиции уже лежат.
    var total = null;
    var known = {};
    for (var ls in linesRes) known[ls] = true;
    for (var cs in lastCart) known[cs] = true;
    var prev = await call('GET', MAGNIT_PREVIEW);
    var pv = prev.status >= 200 && prev.status < 300 ? magnitPreview(prev.json, known) : null;
    if (pv) {
      total = pv.total;
      for (var gs in pv.gone) {
        var lr = linesRes[gs];
        if (lr && !lr.retry) {
          lr.ok = false;
          lr.gone = true;
          lr.why = 'нет в наличии у сети';
        }
      }
    }

    setFreshCart('magnit', { parsed: { cart: lastCart, frac: parsed.frac }, shop: base['x-store-code'] || '' });
    return {
      ok: true,
      ms: Date.now() - tStart,
      lines: linesRes,
      cart: lastCart,
      dropped: dropped,
      total: total,
      tpl: true
    };
  }

  // --- Адаптер METRO (GraphQL supergraph.metro-cc.ru/graphql) ---
  // Протокол проверен живьём на online.metro-cc.ru и supergraph.metro-cc.ru/graphql:
  // - CORS: Access-Control-Allow-Origin: https://online.metro-cc.ru, Credentials: true.
  // - query { orderDraft { ... } } — читает текущую корзину, цены и итоги в копейках.
  // - mutation saveOrderDraft(input: { products: [{ article: Int, quantity: Int }] })
  //   ставит количество перечисленным артикулам пачкой (1 запрос на всю корзину),
  //   неперечисленные позиции не трогает, quantity: 0 убирает позицию.
  var METRO_GQL = 'https://supergraph.metro-cc.ru/graphql';
  var METRO_FIELDS = 'metro_order_id store_id order_type '
    + 'totals { total_amount products_price products_count discount products_discount delivery_price } '
    + 'basket { products { id article name price cost quantity eshop_availability unit_type } } '
    + 'out_of_stock { article }';
  var METRO_READ_Q = 'query { orderDraft { ' + METRO_FIELDS + ' } }';
  var METRO_SAVE_M = 'mutation($input: OrderDraftInput!) { saveOrderDraft(input: $input) { ' + METRO_FIELDS + ' } }';

  /**
   * Разбор ответа корзины METRO (GraphQL orderDraft / saveOrderDraft).
   * Цены и суммы в ответе — в копейках (проверено живьём: 6290 = 62.90 ₽).
   */
  function metroParse(json) {
    if (!json || typeof json !== 'object') return null;
    if (Array.isArray(json.errors) && json.errors.length && (!json.data || (!json.data.orderDraft && !json.data.saveOrderDraft))) {
      return null;
    }
    var draft = null;
    if (json.data && Object.prototype.hasOwnProperty.call(json.data, 'saveOrderDraft')) {
      draft = json.data.saveOrderDraft;
    } else if (json.data && Object.prototype.hasOwnProperty.call(json.data, 'orderDraft')) {
      draft = json.data.orderDraft;
    } else if (json.basket || json.totals || json.metro_order_id) {
      draft = json;
    } else {
      return null;
    }
    if (!draft) {
      return { empty: true, storeId: null, orderType: null, cart: {}, items: {}, oos: {}, total: null };
    }
    var oos = {};
    var oosList = Array.isArray(draft.out_of_stock) ? draft.out_of_stock : [];
    for (var oi = 0; oi < oosList.length; oi++) {
      if (oosList[oi] && oosList[oi].article != null) oos[String(oosList[oi].article)] = true;
    }
    var products = (draft.basket && Array.isArray(draft.basket.products)) ? draft.basket.products : [];
    var cart = {};
    var items = {};
    for (var i = 0; i < products.length; i++) {
      var it = products[i];
      if (!it || it.article == null) continue;
      var sku = String(it.article);
      var q = Math.round(parseFloat(it.quantity) || 0);
      var costRub = (parseFloat(it.cost) || 0) / 100;
      var unitRub = (parseFloat(it.price) || 0) / 100;
      var price = q > 0 && costRub > 0 ? Math.round((costRub / q) * 100) / 100 : unitRub;
      var gone = (it.eshop_availability === false) || !!oos[sku];
      cart[sku] = q;
      items[sku] = {
        sku: sku,
        q: q,
        price: price,
        gone: gone,
        unitType: String(it.unit_type || '')
      };
    }
    var t = draft.totals || null;
    var count = t ? (parseInt(t.products_count, 10) || Object.keys(cart).length) : Object.keys(cart).length;
    var total = null;
    if (count > 0 && t) {
      var goods = (parseFloat(t.products_price) || 0) / 100;
      var prodDisc = (parseFloat(t.products_discount) || 0) / 100;
      var disc = (parseFloat(t.discount) || 0) / 100;
      if (disc <= 0 && prodDisc > 0) disc = prodDisc;
      var base = Math.round((goods + Math.max(prodDisc, disc)) * 100) / 100;
      total = {
        amount: (parseFloat(t.total_amount) || 0) / 100,
        goods: goods,
        base: base,
        discount: Math.round(disc * 100) / 100,
        delivery: (parseFloat(t.delivery_price) || 0) / 100,
        count: count,
        path: '/graphql saveOrderDraft'
      };
    }
    return {
      empty: false,
      storeId: draft.store_id != null ? String(draft.store_id) : null,
      orderType: draft.order_type ? String(draft.order_type) : null,
      cart: cart,
      items: items,
      oos: oos,
      total: total
    };
  }

  function metroGqlErr(res) {
    var j = res && res.json;
    if (j && Array.isArray(j.errors) && j.errors.length) {
      var first = j.errors[0] || {};
      var extErrs = first.extensions && Array.isArray(first.extensions.errors) ? first.extensions.errors : null;
      if (extErrs && extErrs[0]) {
        return { code: (extErrs[0].extensions && extErrs[0].extensions.code) || '', msg: extErrs[0].message || first.message || '' };
      }
      return { code: (first.extensions && first.extensions.code) || '', msg: first.message || '' };
    }
    return null;
  }

  async function runMetro(input, linesIn, pauseMs, burst, tStart, makeFail) {
    var hdrs = {
      'Accept': 'application/json',
      'Content-Type': 'application/json'
    };

    var linesRes = {};
    var opsLines = [];
    for (var i = 0; i < linesIn.length; i++) {
      var l = linesIn[i];
      var s = String(l.sku || '');
      if (!/^\d+$/.test(s)) linesRes[s] = { retry: true };
      else opsLines.push(l);
    }

    var parsed = null;
    var fresh = getFreshCart('metro', input);
    if (fresh && fresh.parsed) {
      var fastPlan = ops(fresh.parsed.cart, opsLines, input.mine, input.want, input.drop);
      if (fastPlan.set.length || fastPlan.drops.length) {
        parsed = fresh.parsed;
      }
    }

    if (!parsed) {
      await pace(pauseMs, burst);
      var initRes = await httpReq('POST', METRO_GQL, hdrs, JSON.stringify({ query: METRO_READ_Q }), true);
      if (initRes.status === 401) {
        clearFreshCart('metro');
        return makeFail('auth', 401, 'METRO: 401');
      }
      if (initRes.status === 0) return makeFail('net', 0, 'METRO: обрыв сети');
      if (initRes.status >= 400 || !initRes.json) {
        return makeFail('read', initRes.status, 'METRO: корзина не читается');
      }
      parsed = metroParse(initRes.json);
      if (!parsed) {
        var gErr = metroGqlErr(initRes);
        return makeFail('read', initRes.status, 'METRO: ' + (gErr ? gErr.msg : 'ответ корзины не распознан'));
      }
    }

    var lastCart = parsed.cart;
    var lastTotal = parsed.total;
    var plan = ops(lastCart, opsLines, input.mine, input.want, input.drop);

    for (var ki = 0; ki < plan.kept.length; ki++) {
      var kSku = plan.kept[ki];
      var pIt = parsed.items && parsed.items[kSku];
      var isGone = pIt ? pIt.gone : false;
      linesRes[kSku] = {
        ok: !isGone,
        how: 'kept',
        put: 0,
        q: lastCart[kSku] || 0,
        price: pIt ? pIt.price : 0,
        gone: isGone,
        retry: false,
        why: isGone ? 'нет в наличии у сети' : '',
        calls: []
      };
    }

    // Собираем все set и drops в один пакетный saveOrderDraft.
    var inSet = {};
    var batch = [];
    for (var si = 0; si < plan.set.length; si++) {
      var sOp = plan.set[si];
      inSet[sOp.sku] = true;
      batch.push({ article: Number(sOp.sku), quantity: sOp.to });
    }
    var activeDrops = [];
    for (var di = 0; di < plan.drops.length; di++) {
      var dOp = plan.drops[di];
      if (!inSet[dOp.sku]) {
        activeDrops.push(dOp);
        batch.push({ article: Number(dOp.sku), quantity: dOp.to });
      }
    }

    if (batch.length) {
      async function sendBatch(needInit) {
        var draftIn = { products: batch };
        if (needInit) {
          var tc = getCookie('metroStoreId');
          draftIn.store_id = String((tc && /^\d+$/.test(tc)) ? tc : (parsed.storeId || '10'));
          draftIn.order_type = parsed.orderType || 'PICKUP';
        }
        await pace(pauseMs, burst);
        return httpReq('POST', METRO_GQL, hdrs, JSON.stringify({
          query: METRO_SAVE_M,
          variables: { input: draftIn }
        }), true);
      }

      var saveRes = await sendBatch(parsed.empty);
      var saveErr = metroGqlErr(saveRes);
      if (saveErr && saveErr.code === 'order_type_empty' && !parsed.empty) {
        saveRes = await sendBatch(true);
        saveErr = metroGqlErr(saveRes);
      }
      var calls = [formatCall('POST', METRO_GQL, saveRes)];
      if (saveRes.status === 401) {
        clearFreshCart('metro');
        return makeFail('auth', 401, 'METRO: 401', lastCart);
      }
      if (saveRes.status === 0) {
        clearFreshCart('metro');
        return makeFail('net', 0, 'METRO: обрыв сети', lastCart);
      }
      var saved = (saveRes.status >= 200 && saveRes.status < 300) ? metroParse(saveRes.json) : null;
      if (!saved || saveErr) {
        clearFreshCart('metro');
        return makeFail('write', saveRes.status, 'METRO: ' + (saveErr ? saveErr.msg : errText(saveRes)), lastCart);
      }
      parsed = saved;
      lastCart = parsed.cart;
      lastTotal = parsed.total;

      for (var oi = 0; oi < plan.set.length; oi++) {
        var op = plan.set[oi];
        var it = parsed.items && parsed.items[op.sku];
        var q = lastCart[op.sku] || 0;
        var outStock = (it && it.gone) || (parsed.oos && !!parsed.oos[op.sku]);
        var landed = !outStock && q >= op.to;
        linesRes[op.sku] = {
          ok: landed,
          how: 'request',
          put: Math.max(0, q - op.from),
          q: q,
          price: it ? it.price : 0,
          gone: outStock,
          retry: false,
          why: outStock ? 'нет в наличии у сети' : (landed ? '' : (q > op.from ? 'сеть положила ' + q + ' из ' + op.to : 'сеть не положила')),
          calls: calls
        };
      }
    }

    var dropped = 0;
    for (var dj = 0; dj < activeDrops.length; dj++) {
      dropped += Math.max(0, activeDrops[dj].from - Math.max(activeDrops[dj].to, lastCart[activeDrops[dj].sku] || 0));
    }

    setFreshCart('metro', { parsed: parsed });
    return {
      ok: true,
      ms: Date.now() - tStart,
      lines: linesRes,
      cart: lastCart,
      dropped: dropped,
      total: lastTotal
    };
  }

  // --- Адаптер Впрока (www.vprok.ru BFF batch API) ---
  // Протокол проверен живьём на www.vprok.ru:
  // - GET /web/api/v1/checkout/dropDownV2 — текущая корзина (positions[], totalRub, totalDime).
  // - PUT /web/api/v1/checkout/positionV2 — пакетная запись { items: [{ productId, count }], lastMileTypeId }
  //   с заголовками x-api-context-bff-request: true, x-api-context-delivery-type-id, x-api-context-is-cart: false.
  //   Неперечисленные позиции не трогает, count: 0 убирает позицию, ответ возвращает всю корзину и totalRub.
  // - Живой замер показал 429 на частых сериях — держим паузу >= 1200 мс и burst = 1.
  var VPROK_GET = '/web/api/v1/checkout/dropDownV2';
  var VPROK_PUT = '/web/api/v1/checkout/positionV2';

  /**
   * Разбор ответа корзины Впрока: принимает и dropDownV2 (positions[]), и positionV2 (items[]).
   */
  function vprokParse(json) {
    if (!json || typeof json !== 'object') return null;
    if (json.error) return null;
    var list = Array.isArray(json.items) ? json.items : (Array.isArray(json.positions) ? json.positions : []);
    var cart = {};
    var items = {};
    var frac = {};
    var goodsSum = 0;
    var baseSum = 0;

    for (var i = 0; i < list.length; i++) {
      var it = list[i];
      if (!it) continue;
      var pid = it.productId != null ? it.productId : (it.product && it.product.id);
      if (pid == null) continue;
      var sku = String(pid);
      var rawCount = parseFloat(it.count != null ? it.count : it.amount) || 0;
      if (Math.abs(rawCount - Math.round(rawCount)) > 0.001) {
        frac[sku] = true;
      }
      var q = rawCount > 0 ? Math.max(1, Math.round(rawCount)) : 0;
      var price = parseFloat(it.price) || 0;
      var priceFull = parseFloat(it.priceFull != null ? it.priceFull : (it.priceOld != null ? it.priceOld : price)) || price;
      var gone = (it.isInStock === false) || (it.isNotAvailable === true);
      goodsSum += price * rawCount;
      baseSum += Math.max(priceFull, price) * rawCount;
      cart[sku] = q;
      items[sku] = {
        sku: sku,
        q: q,
        price: price,
        gone: gone
      };
    }

    var count = parseInt(json.count, 10) || Object.keys(cart).length;
    var total = null;
    if (count > 0) {
      var rub = parseFloat(json.totalRub) || 0;
      var dime = parseInt(String(json.totalDime || '0'), 10) || 0;
      var amount = Math.round((rub + dime / 100) * 100) / 100;
      var goods = goodsSum > 0 ? Math.round(goodsSum * 100) / 100 : amount;
      var base = baseSum > 0 ? Math.round(baseSum * 100) / 100 : goods;
      var discount = Math.max(0, Math.round((base - goods) * 100) / 100);
      total = {
        amount: amount > 0 ? amount : goods,
        goods: goods,
        base: base,
        discount: discount,
        delivery: 0,
        count: count,
        path: VPROK_PUT
      };
    }

    return { cart: cart, items: items, frac: frac, total: total };
  }

  async function runVprok(input, linesIn, pauseMs, burst, tStart, makeFail) {
    var vPause = Math.max(pauseMs, 1200);
    var vBurst = 1;
    var rawDeliv = getCookie('deliveryTypeId');
    var delivId = (rawDeliv && /^\d+$/.test(rawDeliv)) ? parseInt(rawDeliv, 10) : 1;

    function headers(hasBody) {
      var h = {
        'Accept': 'application/json, text/plain, */*',
        'X-Requested-With': 'XMLHttpRequest',
        'x-api-context-bff-request': 'true',
        'x-api-context-delivery-type-id': String(delivId),
        'x-api-context-is-cart': 'false'
      };
      if (hasBody) h['Content-Type'] = 'application/json';
      return h;
    }

    var parsed = null;
    var fresh = getFreshCart('vprok', input);
    if (fresh && fresh.parsed) {
      var vPrelim = [];
      for (var vi = 0; vi < linesIn.length; vi++) {
        var vs = String(linesIn[vi].sku || '');
        if (/^\d+$/.test(vs) && !(fresh.parsed.frac && fresh.parsed.frac[vs])) {
          vPrelim.push(linesIn[vi]);
        }
      }
      var vFast = ops(fresh.parsed.cart, vPrelim, input.mine, input.want, input.drop);
      if (vFast.set.length || vFast.drops.length) {
        parsed = fresh.parsed;
      }
    }

    if (!parsed) {
      await pace(vPause, vBurst);
      var initRes = await httpReq('GET', VPROK_GET, headers(false));
      if (initRes.status === 401) {
        clearFreshCart('vprok');
        return makeFail('auth', 401, 'Впрок: 401');
      }
      if (initRes.status === 429) {
        return makeFail('rate', 429, 'Впрок: 429 (пауза)');
      }
      if (initRes.status === 0) return makeFail('net', 0, 'Впрок: обрыв сети');
      if (initRes.status >= 400 || !initRes.json) {
        return makeFail('read', initRes.status, 'Впрок: корзина не читается');
      }
      parsed = vprokParse(initRes.json);
      if (!parsed) {
        return makeFail('read', initRes.status, 'Впрок: ответ корзины не распознан');
      }
    }

    var lastCart = parsed.cart;
    var lastTotal = parsed.total;
    var linesRes = {};
    var opsLines = [];
    for (var i = 0; i < linesIn.length; i++) {
      var s = String(linesIn[i].sku || '');
      if (!/^\d+$/.test(s) || (parsed.frac && parsed.frac[s])) linesRes[s] = { retry: true };
      else opsLines.push(linesIn[i]);
    }

    var plan = ops(lastCart, opsLines, input.mine, input.want, input.drop);

    for (var ki = 0; ki < plan.kept.length; ki++) {
      var kSku = plan.kept[ki];
      var pIt = parsed.items && parsed.items[kSku];
      var isGone = pIt ? pIt.gone : false;
      linesRes[kSku] = {
        ok: !isGone,
        how: 'kept',
        put: 0,
        q: lastCart[kSku] || 0,
        price: pIt ? pIt.price : 0,
        gone: isGone,
        retry: false,
        why: isGone ? 'нет в наличии у сети' : '',
        calls: []
      };
    }

    var inSet = {};
    var batch = [];
    for (var si = 0; si < plan.set.length; si++) {
      var sOp = plan.set[si];
      inSet[sOp.sku] = true;
      batch.push({ productId: Number(sOp.sku), count: sOp.to });
    }
    var activeDrops = [];
    for (var di = 0; di < plan.drops.length; di++) {
      var dOp = plan.drops[di];
      if (!inSet[dOp.sku] && !(parsed.frac && parsed.frac[dOp.sku] && dOp.to > 0)) {
        activeDrops.push(dOp);
        batch.push({ productId: Number(dOp.sku), count: dOp.to });
      }
    }

    if (batch.length) {
      await pace(vPause, vBurst);
      var putBody = JSON.stringify({ items: batch, lastMileTypeId: delivId });
      var putRes = await httpReq('PUT', VPROK_PUT, headers(true), putBody);
      var calls = [formatCall('PUT', VPROK_PUT, putRes)];
      if (putRes.status === 401) {
        clearFreshCart('vprok');
        return makeFail('auth', 401, 'Впрок: 401', lastCart);
      }
      if (putRes.status === 429) {
        clearFreshCart('vprok');
        return makeFail('rate', 429, 'Впрок: 429 (пауза)', lastCart);
      }
      if (putRes.status === 0) {
        clearFreshCart('vprok');
        return makeFail('net', 0, 'Впрок: обрыв сети', lastCart);
      }
      var saved = (putRes.status >= 200 && putRes.status < 300) ? vprokParse(putRes.json) : null;
      if (!saved) {
        clearFreshCart('vprok');
        return makeFail('write', putRes.status, 'Впрок: сеть ответила ' + putRes.status + ': ' + errText(putRes), lastCart);
      }
      parsed = saved;
      lastCart = parsed.cart;
      lastTotal = parsed.total;

      for (var oi = 0; oi < plan.set.length; oi++) {
        var op = plan.set[oi];
        var it = parsed.items && parsed.items[op.sku];
        var q = lastCart[op.sku] || 0;
        var outStock = it ? it.gone : false;
        var landed = !outStock && q >= op.to;
        linesRes[op.sku] = {
          ok: landed,
          how: 'request',
          put: Math.max(0, q - op.from),
          q: q,
          price: it ? it.price : 0,
          gone: outStock,
          retry: false,
          why: outStock ? 'нет в наличии у сети' : (landed ? '' : (q > op.from ? 'сеть положила ' + q + ' из ' + op.to : 'сеть не положила')),
          calls: calls
        };
      }
    }

    var dropped = 0;
    for (var dj = 0; dj < activeDrops.length; dj++) {
      dropped += Math.max(0, activeDrops[dj].from - Math.max(activeDrops[dj].to, lastCart[activeDrops[dj].sku] || 0));
    }

    setFreshCart('vprok', { parsed: parsed });
    return {
      ok: true,
      ms: Date.now() - tStart,
      lines: linesRes,
      cart: lastCart,
      dropped: dropped,
      total: lastTotal
    };
  }

  return {
    ops: ops,
    run: run,
    abort: abort,
    parse: {
      perekrestok: perekParse,
      pyaterochka: pyatParse,
      dixy: dixyParse,
      magnit: magnitParse,
      magnitPreview: magnitPreview,
      metro: metroParse,
      vprok: vprokParse
    }
  };
})();

if (typeof module !== 'undefined') {
  module.exports = KZ_CARTAPI;
}
