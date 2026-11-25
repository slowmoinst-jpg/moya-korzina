// cartapi.js — корзина сетей (Перекрёсток, Пятёрочка, Дикси, Магнит, METRO, Самокат) собственными запросами сети.
// Встроен в APK «Моей корзины» 1.24 (09.11.2026), Магнит — с 1.26, METRO — с 1.27, Самокат — с 1.29
// (адаптер из 1.28 брал витрину по точке из кода и считал копейки сети рублями).
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

  /** Ожидание паузы между запросами в миллисекундах. */
  function waitMs(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  /**
   * Корзину поправили снова, пока шла эта сборка: приложение называет её ключ в window.__kzNewer
   * (MainActivity.fastChange, 1.46). Итог такой корзины уже никому не нужен.
   */
  function newer(input) {
    return typeof window !== 'undefined' && !!input.key && window.__kzNewer === input.key;
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
   * Запрос через XMLHttpRequest (сайты сетей оборачивают window.fetch).
   * Таймаут 15 с; при ошибке или обрыве статус 0.
   */
  var requestDeadline = 0;

  function httpReq(method, url, headers, body, withCredentials) {
    var left = requestDeadline ? requestDeadline - Date.now() : 15000;
    if (left <= 0) return Promise.resolve({ status: 0, ms: 0, json: null, text: '' });
    stats.requests++;
    return new Promise(function (resolve) {
      var t0 = Date.now();
      var xhr = new XMLHttpRequest();
      // An in-flight write can have reached the retailer. Let its normal response
      // reconcile the ledger, while preventing new requests after the deadline.
      xhr.timeout = method === 'GET' ? Math.min(15000, left) : 15000;
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
      function done(status, text) {
        var ms = Date.now() - t0;
        var json = null;
        try {
          if (text) json = JSON.parse(text);
        } catch (e) {}
        resolve({ status: status, ms: ms, json: json, text: text || '' });
      }
      xhr.onload = function () {
        done(xhr.status, xhr.responseText);
      };
      xhr.onerror = function () {
        done(0, '');
      };
      xhr.ontimeout = function () {
        done(0, '');
      };
      xhr.onabort = function () {
        done(0, '');
      };
      try {
        xhr.send(body != null ? body : null);
      } catch (e) {
        done(0, '');
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
    // Суммы у Дикси — строки с тысячами через пробел: «1 448.71» читалось как 1 (замер 11.11.2026).
    var num = function (v) { return parseFloat(String(v).replace(/\s/g, '')) || 0; };

    for (var i = 0; i < itemsList.length; i++) {
      var it = itemsList[i];
      if (!it || it.xml_id == null) continue;
      var sku = String(it.xml_id);
      var ratio = parseFloat(it.ratio) || 1;
      var itQty = parseFloat(it.quantity) || 0;
      var itPrice = num(it.priceSimple);
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
      var base = num(td.baseBskSumm);
      var goods = Math.round(goodsSum * 100) / 100;
      var discount = Math.round((base - goods) * 100) / 100;
      total = {
        amount: num(td.toPay),
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

    var runners = { perekrestok: runPerekrestok, pyaterochka: runPyaterochka, dixy: runDixy, magnit: runMagnit, metro: runMetro, samokat: runSamokat, vprok: runVprok };
    if (!Object.prototype.hasOwnProperty.call(runners, chain)) {
      return makeFail('chain', 0, 'неизвестная сеть ' + chain);
    }

    // Чтение корзины заранее (peek, 1.41) сборка ждёт: корзину она возьмёт из памяти, которую оно
    // оставит, а два чтения разом сети ни к чему.
    var w = typeof window !== 'undefined' ? window : {};
    var budget = Math.max(0, Math.min(5000, Number(input.budget_ms) || 0));
    if (!input.peek && w.__kzPeek) {
      if (budget) {
        let timer;
        try {
          await Promise.race([w.__kzPeek, new Promise(function (resolve) {
            timer = setTimeout(resolve, budget);
          })]);
        } finally {
          clearTimeout(timer);
        }
      } else {
        await w.__kzPeek;
      }
    }

    requestDeadline = budget ? tStart + budget : 0;
    if (requestDeadline && Date.now() >= requestDeadline) {
      return makeFail('deadline', 0, 'Сеть не успела подтвердить корзину за 5 секунд');
    }

    stats = { requests: 0, paced: 0 };
    var job = (async function () {
      try {
        return await runners[chain](input, linesIn, pauseMs, burst, tStart, makeFail);
      } catch (err) {
        return makeFail('js', 0, 'cartapi.js: ' + (err && err.message ? err.message : String(err)));
      }
    })();
    if (input.peek) w.__kzPeek = job;
    var res = await job;
    if (input.peek && w.__kzPeek === job) w.__kzPeek = null;
    // Чтение заранее не вышло (сеть отказала, нет входа, нет адреса): приложение не повторяет его,
    // пока метка свежа, — сеть, которая не пускает, лишними запросами не дёргаем.
    if (input.peek && !res.ok) w.__kzPeekFailAt = Date.now();
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

    await pace(pauseMs, burst);
    var initRes = await httpReq('GET', '/api/customer/1.4.1.0/basket', headers(false));
    if (initRes.status === 401) {
      return makeFail('auth', 401, 'Перекрёсток: 401');
    }
    if (initRes.status === 0) {
      return makeFail('net', 0, 'Перекрёсток: обрыв сети');
    }
    if (initRes.status >= 400 || !initRes.json) {
      return makeFail('read', initRes.status, 'Перекрёсток: корзина не читается');
    }

    var parsed = perekParse(initRes.json);
    if (!parsed) {
      return makeFail('read', initRes.status, 'Перекрёсток: ответ корзины не распознан');
    }

    var lastCart = parsed.cart;
    var lastTotal = parsed.total;
    var quanta = {};
    for (var k in parsed.items) {
      quanta[k] = parsed.items[k].quantum;
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
  var PYAT_ORDER = 'kz.5ka.order';
  var PYAT_ORDER_MS = 3600 * 1000;
  // Корзина, какой мы её видели в последнем ответе сети: правка по касанию идёт по ней одной записью (1.41).
  // Приложение обновляет эту память раз в 40 с, пока человек на экране корзины (peek в MainActivity).
  var PYAT_CART = 'kz.5ka.cart';
  var PYAT_CART_MS = 60 * 1000;
  // Адрес корзины сети дальше этого от адреса человека в приложении — другое место (как у Самоката).
  var PYAT_NEAR_M = 5000;

  /**
   * Точка, по которой Пятёрочка собирает корзину: адрес заказа ({lat, lon}) из ответа сети, а нет его — из
   * localStorage.cart-store (order.address, docs/design/store-apis.md). Не нашлось — null: сверять не с чем.
   */
  function pyatAddress(order, cs) {
    var cands = [order && order.address, cs && cs.order && cs.order.address];
    for (var i = 0; i < cands.length; i++) {
      var a = cands[i];
      var lat = a && parseFloat(a.lat != null ? a.lat : a.latitude);
      var lon = a && parseFloat(a.lon != null ? a.lon : a.longitude);
      if (lat && lon && isFinite(lat) && isFinite(lon)) return { lat: lat, lon: lon };
    }
    return null;
  }

  /** Корзина Пятёрочки — действующий неоплаченный заказ. Оформленный или оплаченный не трогаем никогда. */
  function pyatDraft(o) {
    return !!o && !!o.is_active && !o.payed_time && o.status === 0;
  }

  /** Ответ сети прямо говорит, что заказ уже не корзина: оплачен, оформлен или закрыт. Нет полей — не знаем. */
  function pyatPlaced(o) {
    return !!o && (!!o.payed_time || (o.status != null && o.status !== 0) || o.is_active === false);
  }

  async function runPyaterochka(input, linesIn, pauseMs, burst, tStart, makeFail) {
    var rawCartStore = (typeof localStorage !== 'undefined' && localStorage['cart-store']) || '{}';
    var cs;
    try {
      cs = JSON.parse(rawCartStore);
    } catch (e) {
      cs = {};
    }
    var oid = cs && cs.orderId;
    // Сайт свой номер заказа теряет, а поиск действующего — до двух секунд на каждую правку
    // (замер 11.11.2026: 1,5 с). Найденный номер помним сами.
    // shortcut: час верим запомненному; сменит человек адрес в Пятёрочке — до часа пишем в прежнюю
    // корзину. Нужен точнее — искать действующий заказ каждый раз.
    var known = false;
    if (!oid) {
      try {
        var keptOrder = JSON.parse((typeof localStorage !== 'undefined' && localStorage[PYAT_ORDER]) || 'null');
        if (keptOrder && keptOrder.id && Date.now() - keptOrder.at < PYAT_ORDER_MS) {
          oid = keptOrder.id;
          known = true;
        }
      } catch (e) { }
    }
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

    /**
     * «Обрыв» запроса Пятёрочки. fast — отказ защиты сети: ServicePipe отказывает протухшей куке на самый первый
     * запрос сборки, за доли секунды (на медленной связи — за секунды: предел 8 с при таймауте запроса 15 с) и при
     * живой связи. Таймаут, обрыв посреди записи (принятый запрос значит, что кука жива) и телефон без сети — не он:
     * только fast приложение лечит стиранием куки spsc (1.50). Время — самого запроса, без темпа и очереди.
     */
    function netFail(res, lastCart) {
      var out = makeFail('net', 0, 'Пятёрочка: обрыв сети (' + res.ms + ' мс)', lastCart);
      out.fast = stats.requests === 1 && res.ms < 8000 && (typeof navigator === 'undefined' || navigator.onLine !== false);
      return out;
    }

    // Правка по касанию не перечитывает корзину, которую мы видели меньше PYAT_CART_MS назад: чтение —
    // 0,85 с из 1,5 с правки, после простоя до 3 с (замеры 12–13.11.2026). Ответ записи — корзина
    // целиком, по нему и сверяемся; не сошлось с памятью — та же сборка с чтением (reread).
    // Память берём и сразу стираем: оборванная сборка её не оставит (чтение заранее прежнюю не трогает,
    // пока не добыло новую). since — когда человек вернулся в приложение или из окна сети либо окно
    // сети уводили класть касаниями: корзину там могли поменять или оформить в заказ, памяти старше
    // веры нет. Часы телефона отскакивают и назад, поэтому abs.
    var memo = null;
    try {
      var rawCart = localStorage[PYAT_CART];
      if (!input.peek) delete localStorage[PYAT_CART];
      var keptCart = JSON.parse(rawCart || 'null');
      if (input.touch && keptCart && keptCart.parsed && keptCart.oid === oid
          && keptCart.at >= (input.since || 0) && Math.abs(Date.now() - keptCart.at) < PYAT_CART_MS) {
        memo = keptCart.parsed;
      }
    } catch (e) { }
    // Что сборка успела по памяти, повтор с чтением учитывает (carry): положенное остаётся в отчёте
    // нашим, а запись, которой сеть только что отказала, он второй раз не шлёт.
    var carry = input.carry || { lines: {}, dropped: 0 };
    function reread(sku, from) {
      input.touch = false;
      input.carry = { lines: linesRes || {}, dropped: dropped || 0, sku: sku, from: from, oid: oid };
      return runPyaterochka(input, linesIn, pauseMs, burst, tStart, makeFail);
    }
    /** Заказ уже не корзина — памяти о его корзине веры нет (чтение заранее само её не стирает). */
    function forget() {
      try { delete localStorage[PYAT_CART]; } catch (e) { }
    }
    /** Отказ записи, за которым может стоять устаревшая память: позиции уже нет, она уже есть, заказ не тот. */
    function staleBy(status) {
      return status === 400 || status === 404 || status === 409 || status === 410;
    }
    // Давние отказы сборка перепроверяет сама (again): их новый отказ — не повод не верить памяти.
    var refused = {};
    for (var ri = 0; ri < linesIn.length; ri++) {
      if (linesIn[ri] && linesIn[ri].again) refused[String(linesIn[ri].sku)] = true;
    }

    var initUrl, initRes;
    for (;;) {
      if (memo) break;
      var found = false;
      if (!oid) {
        // Сайт теряет orderId из cart-store, а корзина в сети жива (эмулятор, 09.11.2026):
        // берём действующую. Оплаченный или оформленный заказ не трогаем никогда.
        await pace(pauseMs, burst);
        var actRes = await httpReq('GET', 'https://5d.5ka.ru/api/orders/v3/orders/?in_action=true', headers(false), null, true);
        if (actRes.status === 0) {
          return netFail(actRes);
        }
        if (actRes.status === 401) {
          return makeFail('auth', 401, 'Пятёрочка: 401');
        }
        // Отказ сети — не «адрес не выбран»: шаг человека тут не нужен.
        if (actRes.status >= 400 || !actRes.json) {
          return makeFail('read', actRes.status, 'Пятёрочка: список заказов не читается');
        }
        var act = (actRes.json.items || []).filter(pyatDraft)[0];
        oid = act && act.id;
        found = true;
      }
      if (!oid) {
        forget();
        return makeFail('address', 0, 'Пятёрочка: в приложении сети не выбран адрес доставки');
      }

      await pace(pauseMs, burst);
      initUrl = 'https://5d.5ka.ru/api/orders/v10/orders/' + encodeURIComponent(oid) + '/';
      initRes = await httpReq('GET', initUrl, headers(false), null, true);
      // Номер от сайта или из памяти мог устареть: заказ оформили, оплатили или его больше нет.
      // Такой не трогаем — ищем действующий. Прочие отказы сети — не про номер, его не забываем.
      var stale = initRes.status === 404 || initRes.status === 410
        || (initRes.status > 0 && initRes.status < 400 && !pyatDraft(initRes.json));
      if (stale) forget();
      if (found || !stale) break;
      oid = '';
      known = false;
      try { delete localStorage[PYAT_ORDER]; } catch (e) { }
    }
    // Перечитывание нашло другой заказ: что писали в прежний и в чём он отказал, к этому не относится.
    if (carry.oid && carry.oid !== oid) carry = { lines: {}, dropped: 0 };
    var parsed = memo;
    if (!parsed) {
      if (initRes.status === 401) {
        return makeFail('auth', 401, 'Пятёрочка: 401');
      }
      if (initRes.status === 0) {
        return netFail(initRes);
      }
      if (initRes.status >= 400 || !initRes.json) {
        return makeFail('read', initRes.status, 'Пятёрочка: корзина не читается');
      }
      // Только что найденный заказ тем временем оформили: это уже не корзина, писать в него нельзя.
      if (!pyatDraft(initRes.json)) {
        return makeFail('read', initRes.status, 'Пятёрочка: корзина сети уже оформлена в заказ');
      }

      parsed = pyatParse(initRes.json);
      if (!parsed) {
        return makeFail('read', initRes.status, 'Пятёрочка: ответ корзины не распознан');
      }
      if (!known && pyatDraft(initRes.json)) {
        try {
          localStorage[PYAT_ORDER] = JSON.stringify({ id: oid, at: Date.now() });
        } catch (e) { }
      }
    }

    // Адрес корзины против адреса человека (1.63). Сайт Пятёрочки сам берёт адрес по умолчанию («Тверская улица, 1»
    // в Москве), когда в окне его не выбирали или данные сайта стёрлись: так было у владельца 16.11.2026 при адресе в
    // Петербурге. Цены и наличие такой корзины — чужого магазина: не пишем в неё и честно говорим, что выбрать.
    // Сам адрес в причину не пишем (она уходит в журнал сервера), только расстояние.
    var home = input.place && input.place.lat && input.place.lon ? input.place : null;
    var there = home ? pyatAddress(initRes && initRes.json, cs) : null;
    if (home && there && samokatApart(there, home) > PYAT_NEAR_M) {
      forget();
      return makeFail('address', 0, 'Пятёрочка: в окне сети выбран адрес в ' + Math.round(samokatApart(there, home) / 1000)
        + ' км от адреса из приложения — откройте Пятёрочку и выберите свой адрес');
    }

    var lastCart = parsed.cart;
    var lastTotal = parsed.total;
    var fresh = !memo;          // parsed — ответ сети из этой сборки, а не память
    var hot = false;            // сеть отказывает не из-за памяти (403, 429, 5xx): лишним чтением её не дёргаем
    var same = false;           // давний отказ (again) снова получил 400: корзина та же, что в памяти (1.60)
    var stop;
    /** Ответ удачной записи — корзина целиком. null — идём дальше; иначе итог, с которым сборка выходит. */
    async function after(json) {
      var p = json && pyatParse(json);
      if (!p && !memo && !pyatPlaced(json)) {
        // Корзины в ответе нет, а шли с чтением: читаем её ещё раз, иначе отчёт остался бы по корзине до записи.
        await pace(pauseMs, burst);
        var seenRes = await httpReq('GET', initUrl, headers(false), null, true);
        // Тело отказа (403, 429) — не заказ: по нему не судим, оформлен ли он.
        json = seenRes.status > 0 && seenRes.status < 400 ? seenRes.json : null;
        p = json && pyatParse(json);
      }
      if (pyatPlaced(json)) {
        // Корзину оформили в заказ, пока мы шли по памяти или между чтением и записью: больше в него
        // не пишем, память и номер заказа забываем.
        try {
          delete localStorage[PYAT_CART];
          delete localStorage[PYAT_ORDER];
        } catch (e) { }
        return makeFail('read', 0, 'Пятёрочка: корзина сети уже оформлена в заказ', lastCart);
      }
      if (p) {
        parsed = p;
        lastCart = p.cart;
        lastTotal = p.total;
        fresh = true;
      } else if (memo) {
        return reread();        // шли по памяти, а корзины в ответе нет: состояние неизвестно
      } else {
        fresh = false;          // корзина после этой записи не прочиталась: запоминать её нельзя
      }
      return null;
    }

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
    // Свериться с сетью можно только по ответу записи: писать нечего — читаем, как раньше. Одни давние отказы
    // пишем по памяти без чтения: их новый 400 («Нельзя добавить товар») память не опровергает, а чтение перед ним —
    // секунда-две правки (эмулятор, 15.11.2026: тронутый батон, которого Пятёрочка не берёт, — 6,3 и 7,2 с вместо
    // 3–4 с; 1.60). Другой отказ давней позиции (404, 409, 403…) идёт той же дорогой, что у остальных.
    if (memo && !plan.set.length && !plan.drops.length) return reread();

    // Выполнение set
    var dropped = carry.dropped || 0;
    for (var si = 0; si < plan.set.length; si++) {
      var op = plan.set[si];
      var sku = op.sku;
      var from = op.from;
      var to = op.to;
      var calls = [];
      // Этой же записи сеть отказала мгновение назад, до перечитывания: второй раз не спрашиваем.
      if (carry.sku === sku && carry.from === from && carry.lines[sku]) {
        linesRes[sku] = carry.lines[sku];
        continue;
      }
      await pace(pauseMs, burst);

      if (to === 0) {
        var delUrl = 'https://5d.5ka.ru/api/orders/v8/orders/' + encodeURIComponent(oid) + '/item/' + encodeURIComponent(sku) + '/';
        var dRes = await httpReq('DELETE', delUrl, headers(false), null, true);
        calls.push(formatCall('DELETE', delUrl, dRes, oid));
        if (dRes.status === 401) return makeFail('auth', 401, 'Пятёрочка: 401', lastCart);
        if (dRes.status === 0) return netFail(dRes, lastCart);
        if (dRes.status >= 400) {
          var wd = errText(dRes);
          linesRes[sku] = { ok: false, how: 'request', put: 0, q: from, price: 0, gone: false, retry: false, why: 'сеть ответила ' + dRes.status + ': ' + wd, calls: calls };
          if (memo && !(refused[sku] && dRes.status === 400)) {
            if (staleBy(dRes.status)) return reread(sku, from);
            hot = true;
          } else if (memo) same = true;
          continue;
        }
        stop = await after(dRes.json);
        if (stop) return stop;
        linesRes[sku] = { ok: true, how: 'request', put: 0, q: 0, price: 0, gone: false, retry: false, why: '', calls: calls };
      } else if (from === 0) {
        var postUrl = 'https://5d.5ka.ru/api/orders/v8/orders/' + encodeURIComponent(oid) + '/item/';
        var postBody = JSON.stringify({ plu: Number(sku), qty: to, uom: 'шт' });
        var pRes = await httpReq('POST', postUrl, headers(true), postBody, true);
        calls.push(formatCall('POST', postUrl, pRes, oid));
        if (pRes.status === 401) return makeFail('auth', 401, 'Пятёрочка: 401', lastCart);
        if (pRes.status === 0) return netFail(pRes, lastCart);
        if (pRes.status >= 400) {
          var wp = errText(pRes);
          var outP = wp === 'нет в наличии';
          linesRes[sku] = { ok: false, how: 'request', put: 0, q: 0, price: 0, gone: outP, retry: false, why: outP ? 'нет в наличии у сети' : 'сеть ответила ' + pRes.status + ': ' + wp, calls: calls };
          if (memo && !(refused[sku] && pRes.status === 400)) {
            if (staleBy(pRes.status)) return reread(sku, from);
            hot = true;
          } else if (memo) same = true;
          continue;
        }
        stop = await after(pRes.json);
        if (stop) return stop;
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
        if (putRes.status === 0) return netFail(putRes, lastCart);
        if (putRes.status >= 400) {
          var wu = errText(putRes);
          linesRes[sku] = { ok: false, how: 'request', put: 0, q: from, price: 0, gone: false, retry: false, why: 'сеть ответила ' + putRes.status + ': ' + wu, calls: calls };
          if (memo && !(refused[sku] && putRes.status === 400)) {
            if (staleBy(putRes.status)) return reread(sku, from);
            hot = true;
          } else if (memo) same = true;
          continue;
        }
        stop = await after(putRes.json);
        if (stop) return stop;
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
        if (rDel.status === 0) return netFail(rDel, lastCart);
        if (rDel.status >= 400 && memo) {
          if (staleBy(rDel.status)) return reread();
          hot = true;
        }
        if (rDel.status < 400) {
          dropped += (dFrom - dTo);
          stop = await after(rDel.json);
          if (stop) return stop;
        }
      } else {
        var dropPutUrl = 'https://5d.5ka.ru/api/orders/v8/orders/' + encodeURIComponent(oid) + '/item/' + encodeURIComponent(dSku) + '/';
        var rPut = await httpReq('PUT', dropPutUrl, headers(true), JSON.stringify({ qty: dTo }), true);
        if (rPut.status === 401) return makeFail('auth', 401, 'Пятёрочка: 401', lastCart);
        if (rPut.status === 0) return netFail(rPut, lastCart);
        if (rPut.status >= 400 && memo) {
          if (staleBy(rPut.status)) return reread();
          hot = true;
        }
        if (rPut.status < 400) {
          dropped += (dFrom - dTo);
          stop = await after(rPut.json);
          if (stop) return stop;
        }
      }
    }

    // Записи не вышло (весовое лишнее не снимаем) — свериться не с чем: читаем. Сеть отказывает
    // (hot) — не читаем: отчёт по памяти, и память он не продлевает. Давний отказ снова (same) — тоже по памяти.
    if (memo && !fresh && !hot && !same) return reread();
    if (memo && fresh) {
      // По памяти позиция «лежит», а в ответе записи её нет или её меньше: читаем и докладываем.
      var miss = ops(lastCart, opsLines, input.mine, input.want, false).set;
      for (var mi = 0; mi < miss.length; mi++) {
        if (!linesRes[miss[mi].sku]) return reread();
      }
    }

    // Kept позиции — по самому свежему ответу сети
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

    // Положенное по памяти до перечитывания остаётся в отчёте нашим.
    for (var cSku in carry.lines) {
      var was = carry.lines[cSku];
      var nowRes = linesRes[cSku];
      if (was && was.put > 0 && nowRes && nowRes.how === 'kept') {
        nowRes.how = 'request';
        nowRes.put = was.put;
        nowRes.calls = was.calls;
      }
    }

    if (fresh) {
      try {
        localStorage[PYAT_CART] = JSON.stringify({ oid: oid, at: Date.now(), parsed: parsed });
      } catch (e) { }
    } else if (memo && same && !hot) {
      // Ничего не записалось: корзина та, что в памяти, и следующей правке читать её незачем. Срок прежний (at).
      try { localStorage[PYAT_CART] = rawCart; } catch (e) { }
    }

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

  var DIXY_STORE_MS = 6 * 3600 * 1000;
  // Корзина Дикси из последнего ответа сети — та же память, что у Пятёрочки (1.41): чтение корзины у
  // Дикси считает скидки и занимает от 0,3 до 1,4 с (замер 13.11.2026), а правка читала её дважды.
  var DIXY_CART = 'kz.dixy.cart';
  var DIXY_CART_MS = PYAT_CART_MS;

  async function runDixy(input, linesIn, pauseMs, burst, tStart, makeFail) {
    // Магазин проверяем раз в DIXY_STORE_MS: сброшенный магазин выдаст и чтение корзины (showMap).
    // localStorage, а не sessionStorage (1.35): после запуска приложения окно новое, и первая правка
    // платила лишним запросом и паузой темпа (замер 12.11.2026: 6 запросов, 4,4 с из отведённых 5).
    // Метка общая с окном человека на dixy.ru и переживает перезапуск приложения.
    var storeOk = false;
    try {
      var storeAge = Date.now() - Number(localStorage['kz.dixy.store'] || 0);
      storeOk = storeAge >= 0 && storeAge < DIXY_STORE_MS;
    } catch (e) {}
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
      try { localStorage['kz.dixy.store'] = String(Date.now()); } catch (e) {}
    }

    // Правка по касанию не читает корзину, которую мы видели меньше DIXY_CART_MS назад и не раньше since
    // (как у Пятёрочки): пишем по памяти и сверяемся чтением после записи. update и remove задают количество
    // целиком, add кладёт сверх лежащего — после него число ставим целиком, если сеть показала больше.
    // Чего память не знала в меньшую сторону (товар сняли, его стало меньше), покажет чтение, и сборка
    // пойдёт заново, уже с чтением (reread). Что человек за эту минуту доложил с другого устройства, запись
    // по памяти срежет до числа из нашей корзины. Память берём и сразу стираем: оборванная сборка её не
    // оставит (чтение заранее прежнюю не трогает, пока не добыло новую).
    var memo = null;
    try {
      var rawCart = localStorage[DIXY_CART];
      if (!input.peek) delete localStorage[DIXY_CART];
      var keptCart = JSON.parse(rawCart || 'null');
      if (input.touch && keptCart && keptCart.parsed && keptCart.at >= (input.since || 0)
          && Math.abs(Date.now() - keptCart.at) < DIXY_CART_MS) {
        memo = keptCart.parsed;
      }
    } catch (e) { }
    // shortcut: повтор с чтением не помнит первую попытку — положенное и снятое ею (put, dropped) в отчёте
    // «уже лежало», а запись, в которой сеть отказала или дала меньше, уйдёт второй раз; нужна точность —
    // переносить, как carry у Пятёрочки.
    var plainFail = makeFail;
    function reread() {
      input.touch = false;
      return runDixy(input, linesIn, pauseMs, burst, tStart, plainFail);
    }
    // Память-расчёт (1.46) — не ответ сети: отказ сборки по ней повторяется с чтением — после паузы темпа и
    // один раз (повтор памяти не берёт, его отказ — plainFail). По корзине-расчёту из отказа приложение
    // убавило бы «своё» на запись, которую сеть приняла и не выполнила, и товар остался бы в её корзине чужим.
    // Сборке старше 20 с повтор не даём: приложение ждёт сборку 45 с (CARTAPI_WAIT_MS) и потом пишет
    // касаниями. Запрос без ответа держит 15 с: после одного такого повтор успевает (начнётся к 21-й секунде,
    // его зависший запрос кончит сборку к 37-й), после двух — нет. Отказ старой сборки уходит без корзины,
    // «своё» приложение не трогает (1.47; в 1.46 порог был 10 с — зависший запрос оставался без повтора).
    if (memo && memo.reckoned) {
      makeFail = function (stage, status, why) {
        if (Math.abs(Date.now() - tStart) > 20000) return plainFail(stage, status, why);
        return waitMs(pauseMs).then(reread);
      };
    }

    var parsed = memo;
    if (!parsed) {
      await pace(pauseMs, burst);
      var initRes = await httpReq('POST', '/ajax/ajax.php', null, dixyFormData('calculateDiscounts', 0, 0));
      if (initRes.status === 0) {
        return makeFail('net', 0, 'Дикси: обрыв сети');
      }
      // Магазин сброшен или корзина не читается: и памяти о корзине веры нет (чтение заранее её не стирает).
      if (initRes.json && initRes.json.action === 'showMap') {
        try { delete localStorage['kz.dixy.store']; delete localStorage[DIXY_CART]; } catch (e) {}
        return makeFail('address', initRes.status, 'Дикси: на сайте сети не выбран магазин или адрес');
      }

      parsed = dixyParse(initRes.json);
      if (!parsed) {
        // Сброшенный магазин сеть может выдать и не словом showMap: в следующий раз спросим заново.
        try { delete localStorage['kz.dixy.store']; delete localStorage[DIXY_CART]; } catch (e) {}
        return makeFail('read', initRes.status, 'Дикси: корзина не читается');
      }
    }

    var lastCart = parsed.cart;
    var lastTotal = parsed.total;

    // Кэш числовых id Дикси (localStorage['kz.dixy.ids'])
    var dixyIds = {};
    try {
      if (typeof localStorage !== 'undefined' && localStorage['kz.dixy.ids']) {
        dixyIds = JSON.parse(localStorage['kz.dixy.ids']);
      }
    } catch (e) {
      dixyIds = {};
    }

    for (var k in parsed.items) {
      dixyIds[k] = { id: parsed.items[k].id, ratio: parsed.items[k].ratio };
    }

    var linesRes = {};
    var opsLines = [];
    var changedCache = false;

    for (var i = 0; i < linesIn.length; i++) {
      var l = linesIn[i];
      var sku = String(l.sku || '');
      if (dixyIds[sku]) {
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
    // По памяти писать нечего — и сверить её не с чем: читаем, как раньше.
    if (memo && !plan.set.length && !plan.drops.length) return reread();
    // Что эта сборка сама пробовала положить или снять: отказ сети по такой позиции — не расхождение с памятью.
    var tried = {};

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
      tried[sSku] = true;
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
        // Шли по памяти, а после add сеть показала больше, чем просили: товар уже лежал (память отстала),
        // и add лёг сверху — ставим число целиком.
        var added = addRes.json && addRes.json.changedProducts && addRes.json.changedProducts[0];
        if (to > 1 || (memo && added && (parseFloat(added.quantity) || 0) > to * ratio)) {
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
    var droppedTo = {};      // сколько осталось после снятия, которое сеть приняла: для корзины расчётом (1.46)
    for (var di = 0; di < plan.drops.length; di++) {
      var dOp = plan.drops[di];
      var dSku = dOp.sku;
      var dFrom = dOp.from;
      var dTo = dOp.to;
      tried[dSku] = true;
      var dInfo = dixyIds[dSku];
      if (!dInfo) continue;
      var dNid = dInfo.id;
      var dRatio = dInfo.ratio || 1;
      await pace(pauseMs, burst);

      if (dTo === 0) {
        var drRes = await httpReq('POST', '/ajax/ajax.php', null, dixyFormData('remove', 0, dNid));
        if (drRes.status === 0) return makeFail('net', 0, 'Дикси: обрыв сети', lastCart);
        if (drRes.status < 400) { dropped += (dFrom - dTo); droppedTo[dSku] = dTo; }
      } else {
        var duRes = await httpReq('POST', '/ajax/ajax.php', null, dixyFormData('update', dTo * dRatio, dNid));
        if (duRes.status === 0) return makeFail('net', 0, 'Дикси: обрыв сети', lastCart);
        if (duRes.status < 400) { dropped += (dFrom - dTo); droppedTo[dSku] = dTo; }
      }
    }

    // Сборку догнала следующая правка (1.46): итог этой корзины уже никому не нужен, а запрос за ним занял
    // бы место в темпе у записи следующей. Замер 13.11.2026 с телефона: 11 удалений подряд, раз в секунду —
    // каждая сборка тратила запрос на итог, очередь росла, и отчёт пришёл через 6,2 с после последнего
    // касания. Вместо чтения оставляем корзину расчётом: что лежало плюс записи, которые сеть приняла.
    // Следующая сборка пишет по ней и сверяется чтением после своих записей, как любая сборка по памяти.
    if ((plan.set.length || plan.drops.length) && newer(input)) {
      var willCart = {};
      var willItems = {};
      for (var wSku in lastCart) willCart[wSku] = lastCart[wSku];
      for (var tSku in tried) {
        var wrote = linesRes[tSku];
        if (wrote && wrote.how === 'request' && !wrote.retry) willCart[tSku] = wrote.q;
      }
      for (var oSku in droppedTo) willCart[oSku] = droppedTo[oSku];
      for (var cSku in willCart) {
        if (!(willCart[cSku] > 0)) { delete willCart[cSku]; continue; }
        var had = parsed.items && parsed.items[cSku];
        var known = dixyIds[cSku] || {};
        willItems[cSku] = {
          sku: cSku, id: had ? had.id : known.id, q: willCart[cSku], price: had ? had.price : 0,
          gone: had ? had.gone : false, ratio: had ? had.ratio : (known.ratio || 1)
        };
      }
      try {
        localStorage[DIXY_CART] = JSON.stringify({
          at: Date.now(), parsed: { cart: willCart, items: willItems, total: null, reckoned: true }
        });
      } catch (e) { }
      return { ok: true, stale: true, ms: Date.now() - tStart, lines: linesRes, cart: willCart, dropped: dropped, total: null };
    }

    // Финальное чтение корзины и итога — только если что-то меняли: иначе итог дало первое чтение.
    var finRes = { status: 0, json: null };
    if (plan.set.length || plan.drops.length) {
      await pace(pauseMs, burst);
      finRes = await httpReq('POST', '/ajax/ajax.php', null, dixyFormData('calculateDiscounts', 0, 0));
    }
    // Корзина из ответа сети, после которого эта сборка ничего не писала: её и запомним.
    var seen = (!memo && !plan.set.length && !plan.drops.length) ? parsed : null;
    if (finRes.status && finRes.status < 400 && finRes.json) {
      var finParsed = dixyParse(finRes.json);
      if (finParsed) {
        seen = finParsed;
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
    if (memo) {
      // Шли по памяти. Чтение после записи не вышло — итог неизвестен; вышло, а по нему осталась работа,
      // за которую эта сборка не бралась, — память разошлась с сетью. В обоих случаях заново, с чтением.
      if (!seen) return reread();
      var left = ops(lastCart, opsLines, input.mine, input.want, input.drop);
      var rest = left.set.concat(left.drops);
      for (var li = 0; li < rest.length; li++) {
        if (!tried[rest[li].sku]) return reread();
      }
    }
    if (seen) {
      try {
        localStorage[DIXY_CART] = JSON.stringify({ at: Date.now(), parsed: seen });
      } catch (e) { }
    }

    return {
      ok: true,
      ms: Date.now() - tStart,
      lines: linesRes,
      cart: lastCart,
      dropped: dropped,
      total: lastTotal
    };
  }

  // --- Адаптер Магнита (APK 1.26, с 1.28 без образца запроса) ---
  // Протокол — из кода сайта magnit.ru (09.11.2026) и замеров на эмуляторе 18.10 и 10.11.2026:
  // GET /webgate/v1/carts/lite — корзина (items[]: goodId, offerId, qnty);
  // PUT того же адреса {items:[{goodId, offerId, qnty}]} СТАВИТ число названным позициям,
  // прочие не трогает, в ответе корзина целиком (app/phonecart.py, QTY_SETS и FULL_CART);
  // DELETE того же адреса очищает корзину ЦЕЛИКОМ — его не зовём никогда.
  // GET /webgate/v1/checkout/preview — итог заказа. Расчёт сам правит корзину: чего в магазине нет,
  // получает qnty 0 (строка остаётся), ограниченное урезается до остатка (замер 13.11.2026).
  // Вход живёт в куке mg_at минуты три, сайт на 401 зовёт POST /_auth/refresh и повторяет.
  // Заголовки сайт собирает из своих кук, и мы так же: магазин — shopCode, устройство — mg_udi,
  // x-service — по типу магазина (x_shop_type), x-delivery-type — по nmg_dt. x-permissions-jwt
  // просит сеть приложить вход: без входа она отвечает 401, а не кладёт в корзину гостя.
  var MAGNIT_CART = '/webgate/v1/carts/lite';
  var MAGNIT_SERVICE = {
    MM: 'express', MM_MINI: 'express', MC: 'express', DARKSTORE: 'express', ZARYAD: 'express',
    GM: 'dostavka', MO: 'dostavka', ME: 'dostavka', DG: 'cosmetic', DG_DARKSTORE: 'cosmetic_darkstore'
  };
  var MAGNIT_DELIVERY = { DELIVERY_TYPE_PICKUP: 'pickup', DELIVERY_TYPE_DELIVERY: 'delivery' };
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
    // Куки в кавычках JSON: shopCode=%22992301%22 (замер 20.09.2026, app/shopbrowser/point.py).
    function cookieText(name) {
      var v = getCookie(name);
      return v ? decodeURIComponent(v).replace(/^"|"$/g, '') : '';
    }
    var shop = cookieText('shopCode');
    var kind = cookieText('x_shop_type');
    var how = cookieText('nmg_dt');
    // С 1.39 наряд несёт точку, по которой приложение считает цены (place): магазин у адреса человека,
    // его формат и доставку. Сайт без неё определяет город сам и может поставить самовывоз. Переносим
    // точку теми же тремя куками, что пишет сайт (app/shopbrowser/point.py), когда магазин сайта не у
    // адреса человека (нет в near) или выбран самовывоз там, где приложение считает доставку.
    var place = input.place && input.place.shop && input.place.type ? input.place : null;
    // С 1.55: где считаем доставку, свой выбор человека принимаем только из магазинов, которые её отдают (deliver).
    // У адреса владельца из 15 магазинов Магнита доставка у одного склада, остальные — самовывоз: кука сайта с
    // «Магнитом у дома» и доставкой экспрессом не является. Перечня нет (старый наряд) — как раньше, любой из near.
    var okShops = place ? ((place.delivery && place.deliver && place.deliver.length ? place.deliver : place.near) || []) : [];
    if (place && (!kind || !MAGNIT_DELIVERY[how] || okShops.indexOf(shop) < 0
                  || (place.delivery && how !== 'DELIVERY_TYPE_DELIVERY'))) {
      shop = String(place.shop);
      kind = String(place.type);
      how = place.delivery ? 'DELIVERY_TYPE_DELIVERY' : 'DELIVERY_TYPE_PICKUP';
      var till = '; path=/; expires=' + new Date(Date.now() + 365 * 864e5).toUTCString();
      document.cookie = 'shopCode=%22' + encodeURIComponent(shop) + '%22' + till;
      document.cookie = 'x_shop_type=' + encodeURIComponent(kind) + till;
      document.cookie = 'nmg_dt=' + how + till;
    }
    var delivery = MAGNIT_DELIVERY[how];
    // Без магазина или способа получения сайт и сам не кладёт, а открывает их выбор: шаг человека.
    if (!shop || !kind || !delivery) {
      return makeFail('address', 0, 'Магнит: выберите магазин и доставку или самовывоз на сайте сети');
    }
    if (!MAGNIT_SERVICE[kind]) return makeFail('read', 0, 'Магнит: тип магазина ' + kind + ' приложению незнаком');
    var base = {
      'x-magnit-bff': '1', 'x-client-name': 'magnit', 'x-device-platform': 'Web', 'x-permissions-jwt': 'true',
      'x-store-code': shop, 'x-service': MAGNIT_SERVICE[kind], 'x-delivery-type': delivery
    };
    var udi = cookieText('mg_udi');
    if (udi) base['x-device-id'] = udi;

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

    var initRes = await call('GET', MAGNIT_CART);
    if (initRes.status === 401) return makeFail('auth', 401, 'Магнит: вход истёк — войдите в магазин заново');
    if (initRes.status === 0) return makeFail('net', 0, 'Магнит: обрыв сети');
    var parsed = initRes.status < 400 ? magnitParse(initRes.json) : null;
    if (!parsed) return makeFail('read', initRes.status, 'Магнит: корзина не читается');
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

    // Запись не принята — сеть не пускает или сменила запрос: дальше решает приложение
    // (касаниями после отчёта). По одной не перебираем: сайт сам шлёт корзину целиком.
    if (setOps.length) {
      var setRes = await put(setOps);
      if (setRes.why) {
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
    var prevOk = prev.status >= 200 && prev.status < 300;
    var pv = prevOk ? magnitPreview(prev.json, known) : null;
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

    // Расчёт оформления сам правит корзину: позицию, которой в магазине нет, сеть оставляет строкой
    // с qnty 0, ограниченную урезает до остатка. Ответ на запись этого ещё не знает: 13.11.2026
    // отчёт был «легло 11 из 11» при четырёх нулях в корзине сети. Прибавляли — перечитываем
    // (после 20 с сборки уже нет: приложение ждёт адаптер 45 с, а запись к этому времени легла).
    // shortcut: лежавшее без прибавки не перечитываем — его пропажу покажет первое чтение следующей
    // сборки; число позиции есть и в ответе оформления (suborders[].items[].qnty), брать оттуда, если
    // четвёртый запрос станет мешать темпу.
    var after = plan.set.length && prevOk && Date.now() - tStart < 20000 ? await call('GET', MAGNIT_CART) : null;
    var left = after && after.status >= 200 && after.status < 300 ? magnitParse(after.json) : null;
    if (left) {
      lastCart = left.cart;
      // Ни одной позиции с количеством — не «всё раскупили», а пустой ответ или закрытый магазин:
      // позиции не легли, но наличие по такому ответу в базу не пишем.
      var filled = false;
      for (var lk in lastCart) if (lastCart[lk] > 0) filled = true;
      for (var ai = 0; ai < plan.set.length; ai++) {
        var was = plan.set[ai];
        var seen = linesRes[was.sku];
        var now = lastCart[was.sku] || 0;
        if (!seen || now >= was.to) continue;
        seen.q = now;
        seen.put = Math.max(0, now - was.from);
        if (!seen.ok) continue;   // причину уже назвали ответ на запись или оформление
        seen.ok = false;
        seen.gone = filled && now === 0;
        seen.why = !filled ? 'сеть обнулила корзину при расчёте заказа'
          : (now === 0 ? 'нет в наличии у сети' : 'сеть оставила ' + now + ' из ' + was.to);
        seen.calls = seen.calls.concat([formatCall('GET', MAGNIT_CART, after)]);
      }
    }

    return {
      ok: true,
      ms: Date.now() - tStart,
      lines: linesRes,
      cart: lastCart,
      dropped: dropped,
      total: total,
      // Сервис, формат, магазин и способ, по которым шли запросы (1.55): приложение пишет их в хронику.
      place: MAGNIT_SERVICE[kind] + '/' + kind + '/' + shop + '/' + delivery,
      tpl: false
    };
  }

  // --- METRO (APK 1.27) ---
  // Замер в эмуляторе 09.11.2026: корзина METRO — черновик заказа на supergraph.metro-cc.ru.
  // Сайт кладёт мутацией saveOrderDraft {products: [{article, quantity}]}: перечисленным ставит
  // число (0 убирает), остальные не трогает; читает запросом orderDraft. Заголовки — X-Config-Token
  // и x-store-id. Токен общий для всех посетителей (JWT до 2030 года), сайт пишет его в каждую свою
  // страницу; магазин — кука metroStoreId; вход — куки .metro-cc.ru. Без выбранного на сайте
  // самовывоза или доставки черновик не сохраняется (order_type_empty) — это шаг человека.
  var METRO_GRAPH = 'https://supergraph.metro-cc.ru/graphql';
  var METRO_TOKEN = 'kz.metro.token';
  var METRO_DRAFT = 'order_type basket { products { article quantity price } } out_of_stock { article } '
    + 'totals { total_amount products_price delivery_price products_count }';

  /**
   * Корзина, цены и итог из черновика METRO. Суммы в копейках: стейк за 1 339 ₽ — price 133900,
   * price — за штуку, products_price — товары (замер 09.11.2026). Весовое (доли) — в frac:
   * числом упаковок его не поставить.
   */
  function metroParse(draft) {
    if (!draft || typeof draft !== 'object') return null;
    var products = draft.basket && Array.isArray(draft.basket.products) ? draft.basket.products : [];
    var cart = {};
    var frac = {};
    var prices = {};
    for (var i = 0; i < products.length; i++) {
      var p = products[i];
      if (!p || p.article == null) continue;
      var sku = String(p.article);
      var q = parseFloat(p.quantity) || 0;
      if (q !== Math.round(q)) frac[sku] = true;
      cart[sku] = (cart[sku] || 0) + (q > 0 ? Math.max(1, Math.round(q)) : 0);
      if (typeof p.price === 'number' && p.price > 0) prices[sku] = p.price / 100;
    }
    var gone = {};
    var out = Array.isArray(draft.out_of_stock) ? draft.out_of_stock : [];
    for (var j = 0; j < out.length; j++) if (out[j] && out[j].article != null) gone[String(out[j].article)] = true;
    var t = draft.totals || {};
    var total = null;
    if (typeof t.total_amount === 'number') {
      total = { amount: t.total_amount / 100, goods: (parseFloat(t.products_price) || 0) / 100, base: 0, discount: 0,
                delivery: (parseFloat(t.delivery_price) || 0) / 100, count: parseInt(t.products_count, 10) || 0,
                path: '/graphql orderDraft' };
    }
    return { cart: cart, frac: frac, prices: prices, gone: gone, total: total, type: String(draft.order_type || '') };
  }

  /** Код и текст отказа supergraph: настоящая причина лежит во вложенном ответе магазина. */
  function metroError(json) {
    var e = json && Array.isArray(json.errors) ? json.errors[0] : null;
    if (!e) return null;
    var inner = e.extensions && Array.isArray(e.extensions.errors) ? e.extensions.errors[0] : null;
    return {
      code: String((inner && inner.extensions && inner.extensions.code) || (e.extensions && e.extensions.code) || ''),
      message: String((inner && inner.message) || e.message || '')
    };
  }

  /** Токен сайта: из памяти окна, а нет или истёк — из его страницы (404 — около 0,8 МБ, раз на годы). */
  async function metroToken() {
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem(METRO_TOKEN) || 'null'); } catch (e) { saved = null; }
    if (saved && saved.t && saved.exp * 1000 > Date.now() + 86400000) return saved.t;
    var page = await httpReq('GET', '/kz-metro-config', null, null, true);
    var m = /marketplaceConfigToken:"(eyJ[\w-]+\.[\w-]+\.[\w-]+)"/.exec(page.text || '');
    if (!m) return '';
    var exp = 0;
    try { exp = JSON.parse(atob(m[1].split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))).exp || 0; } catch (e) { exp = 0; }
    try {
      localStorage.setItem(METRO_TOKEN, JSON.stringify({ t: m[1], exp: exp || Date.now() / 1000 + 7 * 86400 }));
    } catch (e) {}
    return m[1];
  }

  async function runMetro(input, linesIn, pauseMs, burst, tStart, makeFail) {
    var store = getCookie('metroStoreId');
    if (!store) return makeFail('address', 0, 'METRO: на сайте сети не выбран магазин');
    var token = await metroToken();
    if (!token) return makeFail('read', 0, 'METRO: токен сайта не найден на его странице');

    async function gql(query, variables) {
      await pace(pauseMs, burst);
      return httpReq('POST', METRO_GRAPH, { 'content-type': 'application/json', 'x-config-token': token, 'x-store-id': store },
        JSON.stringify({ query: query, variables: variables || {} }), true);
    }
    function failOf(res, what) {
      var err = metroError(res.json);
      if (err && err.code === 'order_type_empty') {
        return makeFail('address', res.status, 'METRO: на сайте не выбран самовывоз или доставка');
      }
      if (!res.status) return makeFail('net', 0, 'METRO: обрыв сети');
      return makeFail(what, res.status, 'METRO: ' + (err ? err.message || err.code : 'сеть ответила ' + res.status));
    }

    var readRes = await gql('query kzOrderDraft { orderDraft { ' + METRO_DRAFT + ' } }');
    var data = readRes.json && readRes.json.data;
    if (readRes.status !== 200 || !data || metroError(readRes.json)) {
      // Сеть ответила отказом — токен могли отозвать: следующая сборка возьмёт новый со страницы.
      if (readRes.status) try { localStorage.removeItem(METRO_TOKEN); } catch (e) {}
      return failOf(readRes, 'read');
    }
    // Гостю без черновика сеть отвечает null — корзина пуста.
    var parsed = metroParse(data.orderDraft || {});
    var lastCart = parsed.cart;
    var gone = parsed.gone;
    var prices = parsed.prices;
    var total = parsed.total;

    var linesRes = {};
    var opsLines = [];
    for (var i = 0; i < linesIn.length; i++) {
      var s = String(linesIn[i].sku || '');
      if (!/^\d+$/.test(s) || parsed.frac[s]) linesRes[s] = { retry: true };
      else opsLines.push(linesIn[i]);
    }
    var plan = ops(lastCart, opsLines, input.mine, input.want, input.drop);
    for (var ki = 0; ki < plan.kept.length; ki++) {
      var kSku = plan.kept[ki];
      linesRes[kSku] = { ok: !gone[kSku], how: 'kept', put: 0, q: lastCart[kSku] || 0, price: prices[kSku] || 0, gone: !!gone[kSku], retry: false,
                         why: gone[kSku] ? 'нет в наличии у сети' : '', calls: [] };
    }

    // Позиции и своё лишнее (до нуля тоже) — одним запросом: сайт так и пишет, число ставится.
    var inSet = {};
    for (var si = 0; si < plan.set.length; si++) inSet[plan.set[si].sku] = true;
    var drops = plan.drops.filter(function (d) { return !inSet[d.sku] && !(parsed.frac[d.sku] && d.to > 0); });
    var writeOps = plan.set.concat(drops);
    var calls = [];
    if (writeOps.length) {
      var w = await gql('mutation kzSave($input: OrderDraftInput) { saveOrderDraft(input: $input) { ' + METRO_DRAFT + ' } }',
        { input: { products: writeOps.map(function (op) { return { article: Number(op.sku), quantity: op.to }; }) } });
      calls.push(formatCall('POST', METRO_GRAPH, w) + ' saveOrderDraft');
      var saved = w.json && w.json.data && w.json.data.saveOrderDraft;
      if (w.status !== 200 || !saved || metroError(w.json)) return failOf(w, 'write');
      var after = metroParse(saved);
      lastCart = after.cart;
      gone = after.gone;
      prices = after.prices;
      total = after.total || total;
    }
    for (var oi = 0; oi < plan.set.length; oi++) {
      var op = plan.set[oi];
      var q = lastCart[op.sku] || 0;
      var landed = q >= op.to && !gone[op.sku];
      linesRes[op.sku] = {
        ok: landed, how: 'request', put: Math.max(0, q - op.from), q: q, price: prices[op.sku] || 0, gone: !!gone[op.sku], retry: false,
        why: landed ? '' : (gone[op.sku] ? 'нет в наличии у сети' : q > op.from ? 'сеть положила ' + q + ' из ' + op.to : 'сеть не положила'),
        calls: calls
      };
    }
    var dropped = 0;
    for (var dj = 0; dj < drops.length; dj++) {
      dropped += Math.max(0, drops[dj].from - Math.max(drops[dj].to, lastCart[drops[dj].sku] || 0));
    }
    return { ok: true, ms: Date.now() - tStart, lines: linesRes, cart: lastCart, dropped: dropped, total: total, tpl: false };
  }

  /**
   * Разбор ответа корзины Самоката (POST /v2/carts/get и PUT /v2/carts/<cartId>). Вид ответа снят с
   * гостевой корзины 11.11.2026. Суммы в копейках. Позиция: {productId, product: {uuid, slug, name,
   * prices, quantity — остаток витрины}, price — за штуку, totalPrice, totalDiscountAmount — к оплате,
   * type, quantity}; сборы — services[].totalPrice. Ключ позиции — слаг (наш артикул), без слага — uuid.
   * Сбор FLEXIBLE_DELIVERY — доплата до минимального заказа: в перечне она есть, а в totalPrice не входит,
   * пока человек её не включил (читаем с buyout: false). Замер под входом владельца 12.11.2026:
   * товары 399 ₽ + упаковка 39 ₽ = итог 438 ₽, доплата 181 ₽ стоит рядом.
   */
  function samokatParse(json) {
    if (!json || !json.cart) return null;
    var c = json.cart;
    var products = Array.isArray(c.products) ? c.products : [];
    var cart = {};
    var items = {};
    var count = 0;
    var goods = 0;
    var base = 0;

    for (var i = 0; i < products.length; i++) {
      var p = products[i];
      if (!p) continue;
      var pr = p.product || {};
      var uuid = p.productId || pr.uuid || '';
      var key = pr.slug || uuid;
      if (!key) continue;
      var q = parseInt(p.quantity, 10) || 0;
      var unit = p.price != null ? p.price : (pr.prices && pr.prices.current);
      cart[key] = q;
      items[key] = {
        uuid: uuid,
        q: q,
        price: (parseFloat(unit) || 0) / 100,
        gone: typeof pr.quantity === 'number' && pr.quantity <= 0,
        left: typeof pr.quantity === 'number' ? pr.quantity : null,
        name: pr.name || ''
      };
      count += q;
      var full = parseFloat(p.totalPrice) || 0;
      base += full;
      goods += p.totalDiscountAmount != null ? (parseFloat(p.totalDiscountAmount) || 0) : full;
    }

    var fees = 0;
    var services = Array.isArray(c.services) ? c.services : [];
    for (var s = 0; s < services.length; s++) {
      // shortcut: корзина ниже минимального заказа идёт без доплаты, как в итоге сети; понадобится
      // показать её человеку — отдать отдельной строкой («min» у сервера).
      if (services[s] && services[s].type === 'FLEXIBLE_DELIVERY') continue;
      fees += parseFloat(services[s] && services[s].totalPrice) || 0;
    }

    var total = null;
    if (count > 0) {
      total = {
        amount: (parseFloat(c.totalPrice) || 0) / 100,
        goods: goods / 100,
        base: base / 100,
        discount: Math.max(0, base - goods) / 100,
        delivery: fees / 100,   // сборка, упаковка и доставка одной суммой: отдельных полей у отчёта нет
        count: count,
        path: '/v2/carts/get'
      };
    }

    return {
      cart: cart,
      items: items,
      total: total,
      cartId: c.cartId,
      version: c.version,
      promocode: c.promocode,
      raw: products
    };
  }

  // --- 1.10 Адаптер Самоката ---
  var SAMOKAT_API = 'https://api-web.samokat.ru';
  var SAMOKAT_SHOWCASE = 'kz.samokat.showcase';
  var SAMOKAT_SHOWCASE_MS = 3600 * 1000;
  var SAMOKAT_SESSION_MS = 5 * 60 * 1000;
  // Адрес профиля дальше этого от адреса человека в приложении — уже другое место (как у магазинов Магнита).
  var SAMOKAT_NEAR_M = 5000;

  /** Адрес, который человек выбрал в Самокате: кука SELECTED_ADDRESS_KEY — JSON сайта с lat и lon. */
  function samokatAddress() {
    try {
      var jar = typeof document !== 'undefined' ? String(document.cookie || '') : '';
      var m = jar.match(/(?:^|;\s*)SELECTED_ADDRESS_KEY=([^;]*)/);
      var a = m ? JSON.parse(decodeURIComponent(m[1])) : null;
      return a && a.lat && a.lon ? a : null;
    } catch (e) {
      return null;
    }
  }

  /** Метры между двумя точками {lat, lon}; на расстояниях города хватает плоской формулы. */
  function samokatApart(a, b) {
    var rad = Math.PI / 180;
    var dy = (a.lat - b.lat) * rad;
    var dx = (a.lon - b.lon) * rad * Math.cos((Number(a.lat) + Number(b.lat)) / 2 * rad);
    return 6371000 * Math.sqrt(dx * dx + dy * dy);
  }

  /**
   * Выбрать в Самокате адрес из профиля так, как это делает сайт: кука на год с его же перечнем полей
   * (бандл _app 2.88.1, модули 17423 и 19414), значение — как пишет js-cookie. Запросов сайт при этом не шлёт.
   */
  function samokatSelect(a) {
    var picked = {};
    ['id', 'region', 'city', 'settlement', 'street', 'place', 'livingArea', 'district', 'division', 'house', 'block']
      .forEach(function (k) { picked[k] = a[k]; });
    ['flat', 'entrance', 'intercom', 'floor', 'comment'].forEach(function (k) { picked[k] = a[k] == null ? '' : a[k]; });
    picked.lat = a.lat;
    picked.lon = a.lon;
    document.cookie = 'SELECTED_ADDRESS_KEY=' + encodeURIComponent(JSON.stringify(picked))
      .replace(/%(2[346BF]|3[AC-F]|40|5[BDE]|60|7[BCD])/g, decodeURIComponent)
      + '; path=/; expires=' + new Date(Date.now() + 365 * 864e5).toUTCString();
    return picked;
  }

  /** Корзина сети глазами наряда: артикул наряда — слаг, реже uuid; кладём оба ключа. */
  function samokatLive(parsed) {
    var live = {};
    for (var k in parsed.cart) {
      if (!Object.prototype.hasOwnProperty.call(parsed.cart, k)) continue;
      live[k] = parsed.cart[k];
      if (parsed.items[k].uuid) live[parsed.items[k].uuid] = parsed.cart[k];
    }
    return live;
  }

  /** Позиция корзины по артикулу наряда: по слагу, а нет — по uuid. */
  function samokatItem(parsed, sku) {
    if (parsed.items[sku]) return parsed.items[sku];
    for (var k in parsed.items) {
      if (parsed.items[k].uuid === sku) return parsed.items[k];
    }
    return null;
  }

  // Проверено на гостевой корзине 11.11.2026: запись проходит без заголовка x-creeper; на устаревшую
  // версию сеть отвечает 200 со status VERSION_MISMATCH и ничего не пишет. Под входом человека, с
  // набором, промокодом и баллами в корзине не проверено.
  async function runSamokat(input, linesIn, pauseMs, burst, tStart, makeFail) {
    var w = typeof window !== 'undefined' ? window : {};
    var calls = [];
    for (;;) {
      // 1. Сессия. Токен сайт даёт и гостю, а гостевая корзина человеку не нужна. Вход сайт узнаёт по
      // типу пользователя в токене (гость — ANONYMOUS); поле user сессии пусто и у вошедшего (замер
      // 12.11.2026). Пять минут токен живёт в памяти окна: запрос сессии — самый долгий в сборке (0,6 с).
      var kept = w.__kzSamokatSess;
      var remembered = !!kept && Date.now() - kept.at < SAMOKAT_SESSION_MS;
      var sess = remembered ? kept.sess : null;
      if (!sess) {
        await pace(pauseMs, burst);
        var sessRes = await httpReq('GET', '/api/auth/session', null, null, true);
        calls.push(formatCall('GET', '/api/auth/session', sessRes));
        if (sessRes.status !== 200 && sessRes.status !== 401) {
          return makeFail(sessRes.status === 0 || sessRes.status === 403 ? 'net' : 'read', sessRes.status,
            'Самокат: сессия не читается');
        }
        sess = sessRes.json;
        var who = '';
        try {
          who = JSON.parse(atob(sess.accessToken.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))).user.userType || '';
        } catch (e) { }
        if (sessRes.status === 401 || !who || who === 'ANONYMOUS') {
          return makeFail('auth', 401, 'Самокат: войдите в магазин под своим номером');
        }
        w.__kzSamokatSess = { sess: sess, at: Date.now() };
      }
      var out = await samokatCart(sess, calls, input, linesIn, pauseMs, burst, tStart, makeFail);
      if (out.ok || out.stage !== 'auth') return out;
      w.__kzSamokatSess = null;
      if (!remembered) return out;
      // Токен из памяти окна истёк — один раз заново, со свежей сессией.
    }
  }

  async function samokatCart(sess, calls, input, linesIn, pauseMs, burst, tStart, makeFail) {
    var w = typeof window !== 'undefined' ? window : {};
    function wall(res, what, lastCart) {
      if (res.status === 401) return makeFail('auth', 401, 'Самокат: вход истёк — войдите в магазин заново', lastCart);
      if (res.status === 0 || res.status === 403) return makeFail('net', res.status, 'Самокат: сеть не пустила (' + what + ')', lastCart);
      return makeFail('read', res.status, 'Самокат: ' + what + ' — ' + (errText(res) || ('HTTP ' + res.status)), lastCart);
    }

    var dev = '';
    try {
      dev = String((typeof localStorage !== 'undefined' && localStorage['X-Device-Uid']) || '').replace(/^"|"$/g, '');
    } catch (e) { }
    if (!dev) dev = w.__kzSamokatDev = w.__kzSamokatDev || ('kz' + Math.random().toString(36).slice(2, 14));
    function heads(hasBody) {
      var h = { 'authorization': 'Bearer ' + sess.accessToken, 'x-application-platform': 'mWeb', 'deviceid': dev };
      if (hasBody) h['content-type'] = 'application/json';
      return h;
    }

    // 2. Витрина — магазин по адресу, который выбран в Самокате (в 1.28 её брали по точке, зашитой в
    // код: корзина легла бы в чужой магазин). Помним час: запрос идёт до полутора секунд.
    // С 1.39 наряд несёт адрес человека из настроек приложения (place — его координаты): сайт сам
    // выбирает последний добавленный адрес, и у владельца это был адрес в другом городе (12.11.2026).
    // Раз в час сверяемся с профилем и выбираем в сети ближайший к человеку адрес.
    // shortcut: час верим витрине; сменит человек адрес в приложении сети — до часа кладём в прежний
    // магазин. Нужен точнее — спрашивать профиль каждый раз.
    var addr = samokatAddress();
    var home = input.place && input.place.lat && input.place.lon ? input.place : null;
    function keyOf(a) {
      return (a ? a.lat + ',' + a.lon : 'profile') + (home ? '|' + home.lat + ',' + home.lon : '');
    }
    var addrKey = keyOf(addr);
    var sid = '';
    var pick = false;   // адрес из профиля ещё надо выбрать в сети
    try {
      var keptShop = JSON.parse((typeof localStorage !== 'undefined' && localStorage[SAMOKAT_SHOWCASE]) || 'null');
      if (keptShop && keptShop.key === addrKey && Date.now() - keptShop.at < SAMOKAT_SHOWCASE_MS) sid = keptShop.sid;
    } catch (e) { }
    if (!sid) {
      if (home || !addr) {
        await pace(pauseMs, burst);
        var profRes = await httpReq('GET', SAMOKAT_API + '/users/profile', heads(false), null, true);
        calls.push(formatCall('GET', SAMOKAT_API + '/users/profile', profRes));
        if (profRes.status !== 200 || !profRes.json) return wall(profRes, 'профиль не читается');
        var known = (profRes.json.addresses || []).filter(function (a) { return a && a.lat && a.lon; });
        if (home) {
          known = known.filter(function (a) { return samokatApart(a, home) <= SAMOKAT_NEAR_M; });
          known.sort(function (a, b) { return samokatApart(a, home) - samokatApart(b, home); });
          if (!known.length) {
            return makeFail('address', 0, 'Самокат: в магазине нет адреса, который указан в приложении, — добавьте его на сайте сети');
          }
          if (!addr || addr.id !== known[0].id) {
            addr = known[0];
            addrKey = keyOf(addr);
            pick = true;
          }
        } else {
          // На этом устройстве адрес не выбран — берём последний из профиля, как сайт.
          known.sort(function (a, b) { return a.id > b.id ? -1 : 1; });
          addr = known[0] || null;
        }
      }
      if (!addr) return makeFail('address', 0, 'Самокат: в магазине не выбран адрес доставки');
      await pace(pauseMs, burst);
      var shopRes = await httpReq('GET', SAMOKAT_API + '/showcases/list?lat=' + encodeURIComponent(addr.lat)
        + '&lon=' + encodeURIComponent(addr.lon), heads(false), null, true);
      calls.push(formatCall('GET', SAMOKAT_API + '/showcases/list', shopRes));   // адрес в журнал не пишем
      if (shopRes.status !== 200) return wall(shopRes, 'витрина не читается');
      var shops = Array.isArray(shopRes.json) ? shopRes.json : [];
      var shop = null;
      var order = ['MINIMARKET', 'SUPERMARKET'];
      for (var oi = 0; oi < order.length && !shop; oi++) {
        for (var si = 0; si < shops.length && !shop; si++) {
          if (shops[si] && shops[si].type === order[oi]) shop = shops[si];
        }
      }
      sid = (shop || shops[0] || {}).showcaseId || '';
      if (!sid) {
        return makeFail('address', 0, 'Самокат: по ' + (pick ? 'адресу из приложения' : 'выбранному адресу') + ' сеть не доставляет');
      }
      // Выбираем адрес в сети, только когда она по нему доставляет: иначе человек остался бы на сайте
      // с адресом без магазина.
      if (pick) samokatSelect(addr);
      try {
        localStorage[SAMOKAT_SHOWCASE] = JSON.stringify({ key: addrKey, sid: sid, at: Date.now() });
      } catch (e) { }
    }

    // 3. Корзина
    async function readCart() {
      await pace(pauseMs, burst);
      var res = await httpReq('POST', SAMOKAT_API + '/v2/carts/get', heads(true), JSON.stringify({
        showcaseId: sid, buyout: false, offerCode: 'BASIC_OFFER', deliveryOption: 'DELIVERY', embedProductsToResponse: true
      }), true);
      calls.push(formatCall('POST', SAMOKAT_API + '/v2/carts/get', res));
      return res;
    }
    var cRes = await readCart();
    if (cRes.status !== 200 || !cRes.json || !cRes.json.cart) return wall(cRes, 'корзина не читается');
    var parsed = samokatParse(cRes.json);
    var before = samokatLive(parsed);
    var live = before;
    var plan = ops(live, linesIn, input.mine, input.want, input.drop);
    var firstPlan = plan;

    var uuids = w.__kzSamokatSlugs = w.__kzSamokatSlugs || {};
    var gone = {};

    // 4. Запись. Сеть принимает весь список разом: чужие позиции переносим как есть, а корзину с
    // набором, незнакомой позицией или промокодом незнакомого вида запросом не трогаем — пересобрать
    // их нечем, пропали бы. Такую корзину дособерут касания.
    for (var attempt = 0; plan.set.length || plan.drops.length; attempt++) {
      var list = [];
      var byId = {};
      for (var ri = 0; ri < parsed.raw.length; ri++) {
        var entry = parsed.raw[ri];
        var kind = entry && entry.type;
        // Товар, лежащий дважды, и количество не числом — тоже незнакомое: перезаписали бы не то.
        if (!entry || !entry.productId || byId[entry.productId] || typeof entry.quantity !== 'number'
            || (kind !== 'PRODUCT' && kind !== 'IMPULSE_PRODUCT' && kind !== 'CUTLERY')) {
          return makeFail('write', 0, 'Самокат: в корзине набор или незнакомая позиция, запросом её не правлю', live);
        }
        byId[entry.productId] = { productId: entry.productId, quantity: entry.quantity, type: kind };
        list.push(byId[entry.productId]);
      }
      if (parsed.promocode && typeof parsed.promocode !== 'string') {
        return makeFail('write', 0, 'Самокат: в корзине промокод незнакомого вида, запросом её не правлю', live);
      }

      var touched = false;
      for (var di = 0; di < plan.drops.length; di++) {
        var dropOp = plan.drops[di];
        var dropItem = samokatItem(parsed, dropOp.sku);
        if (dropItem && byId[dropItem.uuid]) {
          byId[dropItem.uuid].quantity = dropOp.to;
          touched = true;
        }
      }
      for (var wi = 0; wi < plan.set.length; wi++) {
        var op = plan.set[wi];
        var sku = String(op.sku);
        if (gone[sku]) continue;
        var inCart = samokatItem(parsed, sku);
        var uuid = (inCart && inCart.uuid) || uuids[sku] || '';
        if (!uuid) {
          await pace(pauseMs, burst);
          var prRes = await httpReq('GET', SAMOKAT_API + '/v2/showcases/' + encodeURIComponent(sid) + '/products/'
            + encodeURIComponent(sku), heads(false), null, true);
          calls.push(formatCall('GET', SAMOKAT_API + '/v2/showcases/{sid}/products/' + sku, prRes));
          if (prRes.status === 400 || prRes.status === 404) {
            gone[sku] = true;      // на этой витрине такого товара нет
            continue;
          }
          if (prRes.status !== 200 || !prRes.json || !prRes.json.uuid) return wall(prRes, 'товар не читается', live);
          if (typeof prRes.json.quantity === 'number' && prRes.json.quantity <= 0) {
            gone[sku] = true;      // остаток витрины — ноль
            continue;
          }
          uuid = uuids[sku] = prRes.json.uuid;
        }
        if (byId[uuid]) {
          byId[uuid].quantity = op.to;
        } else {
          byId[uuid] = { productId: uuid, quantity: op.to, type: 'PRODUCT' };
          list.push(byId[uuid]);
        }
        touched = true;
      }
      if (!touched) break;

      await pace(pauseMs, burst);
      var body = {
        version: parsed.version,
        products: list.filter(function (it) { return it.quantity > 0; }),
        showcaseId: sid,
        offerCode: 'BASIC_OFFER',
        embedProductsToResponse: true
      };
      if (parsed.promocode) body.promocode = parsed.promocode;
      var putRes = await httpReq('PUT', SAMOKAT_API + '/v2/carts/' + encodeURIComponent(parsed.cartId), heads(true),
        JSON.stringify(body), true);
      calls.push(formatCall('PUT', SAMOKAT_API + '/v2/carts/{cart}', putRes));
      if (putRes.status !== 200 || !putRes.json || !putRes.json.cart) return wall(putRes, 'запись не прошла', live);
      if (putRes.json.status === 'ACTUAL') {
        parsed = samokatParse(putRes.json);
        live = samokatLive(parsed);
        break;
      }
      // VERSION_MISMATCH: корзину тем временем поменяли (человек в приложении сети) — читаем и пишем ещё раз.
      if (attempt >= 1) {
        return makeFail('write', 200, 'Самокат: сеть не приняла запись (' + String(putRes.json.status || '').slice(0, 40) + ')', live);
      }
      cRes = await readCart();
      if (cRes.status !== 200 || !cRes.json || !cRes.json.cart) return wall(cRes, 'корзина не читается', live);
      parsed = samokatParse(cRes.json);
      live = samokatLive(parsed);
      plan = ops(live, linesIn, input.mine, input.want, input.drop);
      before = live;        // что человек успел положить сам, нам не засчитывается
      firstPlan = plan;
    }

    // 5. Итог по строкам наряда — по корзине после записи
    var linesRes = {};
    for (var li = 0; li < linesIn.length; li++) {
      var lineSku = String(linesIn[li].sku);
      var want = Math.max(1, parseInt(linesIn[li].target, 10) || 1);
      var was = before[lineSku] || 0;
      var q = live[lineSku] || 0;
      var info = samokatItem(parsed, lineSku) || {};
      var isGone = !!gone[lineSku] || !!info.gone;
      var landed = q >= want && !isGone;
      // Больше остатка витрины сеть не кладёт и об этом молчит (12.11.2026: просили 4, легло 3 при остатке 3).
      var short = info.left != null && info.left < want && q >= info.left;
      linesRes[lineSku] = {
        ok: landed,
        how: was >= want ? 'kept' : 'request',
        put: Math.max(0, q - was),
        q: q,
        price: info.price || 0,
        gone: isGone,
        retry: false,
        why: landed ? '' : (isGone ? 'нет в наличии у сети' : short ? 'у сети осталось ' + info.left
          : q > was ? 'сеть положила ' + q + ' из ' + want : 'сеть не положила'),
        calls: calls
      };
    }

    var dropped = 0;
    for (var dj = 0; dj < firstPlan.drops.length; dj++) {
      var dOp = firstPlan.drops[dj];
      dropped += Math.max(0, dOp.from - Math.max(dOp.to, live[dOp.sku] || 0));
    }

    return { ok: true, ms: Date.now() - tStart, lines: linesRes, cart: live, dropped: dropped, total: parsed.total, tpl: false };
  }

  // --- Впрок (vprok.ru), 1.62. Протокол снят в окне приложения на эмуляторе 16.11.2026.
  // Чтение: GET /web/api/v1/checkout/dropDownV2 (positions[]: count, price — за штуку, isNotAvailable, isFractional,
  // product.id; у пустой корзины positions нет). Запись: PUT /web/api/v1/checkout/positionV2 с телом
  // {items: [{productId, count, isPackage: false}], lastMileTypeId}: count — число целиком, 0 убирает, позиции идут
  // пачкой; ответ — вся корзина (items[]: productId, count, price, isInStock, isFractional). Заголовки как у сайта:
  // X-XSRF-TOKEN из куки XSRF-TOKEN (живёт около 18 минут), x-api-context-*; доставка — кука deliveryTypeId.
  // 429 со страницей «Слишком много запросов из вашей сети» сайт отвечает на запись с неизвестным ему артикулом
  // (замер 16.11.2026: две такие записи — 429, а чтение и допустимые записи сразу после них — 200, по четыре записи
  // за восемь секунд идут без отказов). На чтении 429 — стена защиты: адаптер отдаёт её приложению (stage wall).
  // Итог корзины с доставкой в ответах не лежит: total не отдаём (сервер возьмёт цены позиций).
  var VPROK_READ = '/web/api/v1/checkout/dropDownV2';
  var VPROK_WRITE = '/web/api/v1/checkout/positionV2';
  var VPROK_CART_MS = 60 * 1000;

  /** Корзина Впрока из ответа чтения или записи: {cart, frac, prices, gone}. Не корзина (страница защиты) — null. */
  function vprokParse(json) {
    if (!json || typeof json !== 'object' || typeof json.totalRub !== 'number') return null;
    var rows = Array.isArray(json.positions) ? json.positions : (Array.isArray(json.items) ? json.items : []);
    var cart = {};
    var frac = {};
    var prices = {};
    var gone = {};
    for (var i = 0; i < rows.length; i++) {
      var p = rows[i];
      if (!p) continue;
      var id = p.productId != null ? p.productId : (p.product && p.product.id);
      if (id == null) continue;
      var sku = String(id);
      var q = parseFloat(p.count) || 0;
      if (p.isFractional === true || q !== Math.round(q)) frac[sku] = true;
      cart[sku] = (cart[sku] || 0) + (q > 0 ? Math.max(1, Math.round(q)) : 0);
      if (typeof p.price === 'number' && p.price > 0) prices[sku] = p.price;
      if (p.isNotAvailable === true || p.isInStock === false) gone[sku] = true;
    }
    return { cart: cart, frac: frac, prices: prices, gone: gone };
  }

  async function runVprok(input, linesIn, pauseMs, burst, tStart, makeFail) {
    var w = typeof window !== 'undefined' ? window : {};
    var delivery = parseInt(getCookie('deliveryTypeId'), 10) || 8;
    function headers(hasBody) {
      var h = {
        'Accept': 'application/json, text/plain, */*',
        'x-api-context-bff-request': 'true',
        'x-api-context-delivery-type-id': String(delivery),
        'x-api-context-is-cart': 'false'
      };
      var xsrf = getCookie('XSRF-TOKEN');
      if (xsrf) {
        try { h['X-XSRF-TOKEN'] = decodeURIComponent(xsrf); } catch (e) {}
      }
      if (hasBody) h['Content-Type'] = 'application/json';
      return h;
    }
    function failOf(res, what, cart) {
      if (!res.status) return makeFail('net', 0, 'Впрок: обрыв сети', cart);
      if (res.status === 429) return makeFail('wall', 429, 'Впрок: защита сети просит подождать (429)', cart);
      if (res.status === 401 || res.status === 403) return makeFail('auth', res.status, 'Впрок: нет входа на сайте сети (' + res.status + ')', cart);
      return makeFail(what, res.status, 'Впрок: ' + (res.status === 200 ? 'ответ не корзина'
        : res.json ? errText(res) : 'сеть ответила ' + res.status), cart);
    }

    // Правка по касанию не читает корзину, которую мы видели меньше минуты назад и не раньше since: ответ записи —
    // вся корзина, по нему память и обновляется (как у Дикси и Пятёрочки). Память берём и стираем: оборванная
    // сборка её не оставит.
    var memo = w.__kzVprokCart;
    w.__kzVprokCart = null;
    var cur = null;
    if (input.touch && memo && memo.parsed && memo.at >= (input.since || 0) && Math.abs(Date.now() - memo.at) < VPROK_CART_MS) {
      cur = memo.parsed;
    }
    if (!cur) {
      await pace(pauseMs, burst);
      var readRes = await httpReq('GET', VPROK_READ, headers(false));
      cur = readRes.status === 200 ? vprokParse(readRes.json) : null;
      if (!cur) return failOf(readRes, 'read');
    }

    var linesRes = {};
    var opsLines = [];
    for (var i = 0; i < linesIn.length; i++) {
      var s = String(linesIn[i].sku || '');
      // Не артикул или товар на вес — числом упаковок его не поставить: касания.
      if (!/^\d+$/.test(s) || cur.frac[s]) linesRes[s] = { retry: true };
      else opsLines.push(linesIn[i]);
    }
    var plan = ops(cur.cart, opsLines, input.mine, input.want, input.drop);
    for (var ki = 0; ki < plan.kept.length; ki++) {
      var kSku = plan.kept[ki];
      linesRes[kSku] = { ok: !cur.gone[kSku], how: 'kept', put: 0, q: cur.cart[kSku] || 0, price: cur.prices[kSku] || 0, gone: !!cur.gone[kSku],
                         retry: false, why: cur.gone[kSku] ? 'нет в наличии у сети' : '', calls: [] };
    }

    // Позиции и своё лишнее (до нуля тоже) — одним запросом: сайт так и пишет, число ставится.
    var inSet = {};
    for (var si = 0; si < plan.set.length; si++) inSet[plan.set[si].sku] = true;
    var drops = plan.drops.filter(function (d) { return !inSet[d.sku] && !(cur.frac[d.sku] && d.to > 0); });
    var writeOps = plan.set.concat(drops);
    var calls = [];
    var refused = {};
    // Сайт отвечает 429 на запись, в которой есть товар, которого он не знает, и не пишет в ней ничего (замер
    // 16.11.2026; допустимые записи шли по четыре за восемь секунд без отказов — к частоте это не относится).
    // Такую запись пишем по одной позиции: негодная не мешает остальным и получает свой отказ.
    async function write(opList) {
      await pace(pauseMs, burst);
      var res = await httpReq('PUT', VPROK_WRITE, headers(true), JSON.stringify({
        items: opList.map(function (op) { return { productId: Number(op.sku), count: op.to, isPackage: false }; }),
        lastMileTypeId: delivery
      }));
      calls.push(formatCall('PUT', VPROK_WRITE, res));
      var next = res.status === 200 ? vprokParse(res.json) : null;
      if (next) {
        cur = next;
        return null;
      }
      if (res.status !== 429) return failOf(res, 'write', cur.cart);
      if (opList.length === 1) {
        refused[opList[0].sku] = 'Впрок не принял товар (429)';
        return null;
      }
      for (var wi = 0; wi < opList.length; wi++) {
        var bad = await write([opList[wi]]);
        if (bad) return bad;
      }
      return null;
    }
    var writeFail = writeOps.length ? await write(writeOps) : null;
    if (writeFail) return writeFail;
    for (var oi = 0; oi < plan.set.length; oi++) {
      var op = plan.set[oi];
      var q = cur.cart[op.sku] || 0;
      var landed = q >= op.to && !cur.gone[op.sku] && !refused[op.sku];
      linesRes[op.sku] = {
        ok: landed, how: 'request', put: Math.max(0, q - op.from), q: q, price: cur.prices[op.sku] || 0, gone: !!cur.gone[op.sku], retry: false,
        why: landed ? '' : (refused[op.sku] || (cur.gone[op.sku] ? 'нет в наличии у сети' : q > op.from ? 'сеть положила ' + q + ' из ' + op.to : 'сеть не положила')),
        calls: calls
      };
    }
    var dropped = 0;
    for (var dj = 0; dj < drops.length; dj++) {
      dropped += Math.max(0, drops[dj].from - Math.max(drops[dj].to, cur.cart[drops[dj].sku] || 0));
    }
    w.__kzVprokCart = { at: Date.now(), parsed: cur };
    return { ok: true, ms: Date.now() - tStart, lines: linesRes, cart: cur.cart, dropped: dropped, total: null, tpl: false };
  }

  return {
    ops: ops,
    run: run,
    parse: {
      perekrestok: perekParse,
      pyaterochka: pyatParse,
      dixy: dixyParse,
      magnit: magnitParse,
      magnitPreview: magnitPreview,
      metro: metroParse,
      samokat: samokatParse,
      vprok: vprokParse
    }
  };
})();

if (typeof module !== 'undefined') {
  module.exports = KZ_CARTAPI;
}
