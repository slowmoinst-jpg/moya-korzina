/* «Забрать чеки» из кабинета ФНС — сборщик для окна внутри приложения.
 *
 * ЗАЧЕМ ОН НУЖЕН. В кабинет «Мои чеки онлайн» человек входит сам: на отправку кода
 * по СМС кабинет требует капчу (проверено 16.09.2026), и с сервера её не пройти.
 * Поэтому вход остаётся человеку, а ключ его сессии забирает этот файл — он
 * исполняется НА странице кабинета (MainActivity, evaluateJavascript), то есть
 * видит и хранилище, и запросы самой страницы, как её собственный код.
 *
 * ЧТО ОТДАЁТ. Один адрес: /phone/receipts?keys=<base64url>. Внутри — ключ доступа,
 * refresh-токен и идентификатор устройства, ровно то, что понимает
 * app/fns/sync.py (parse_keys/unpack_keys). Больше в адрес ничего не влезает, и это
 * правильно: опись и позиции забирает СЕРВЕР (app/fns/sync.py) — у него уже есть
 * пачки, запись в историю и продолжение после обрыва. Окно отдаёт ему вход.
 *
 * ПОЧЕМУ ЗДЕСЬ НЕТ ПАЧЕК ПОЗИЦИЙ. В закладке (docs/grab.src.js) они нужны: она
 * отдаёт человеку файл. Здесь файл некуда деть — WebView возвращается в приложение
 * ОДНИМ адресом, а тысяча чеков с позициями в адрес не влезает. Поэтому из ядра
 * закладки взято то, что относится ко входу: слушатель живых запросов кабинета,
 * поиск ключа в хранилище и упаковка. Плюс одна проба описи (/api/v1/receipt):
 * по ней видно, что вход живой, и мёртвый ключ не уедет в приложение молча.
 *
 * ГРАНИЦЫ. Ключ уходит только на адрес, который назвало приложение
 * (window.KORZINA_APP_URL), и только в адресе его же страницы. Ни на какой другой
 * сервер не отправляется ничего. Запросов ровно один, без пачек и без фона: кабинет
 * не наш. Без нажатия человека файл не исполняется — его инжектит кнопка
 * «Забрать чеки» на панели приложения.
 *
 * УСТОЙЧИВОСТЬ. Любой запрос может не ответить: у него есть срок (TIMEOUT), после
 * которого он считается молчащим. Проба при этом не держит возврат — ключ уходит
 * в приложение в любом случае, а причина остаётся короткой строкой в console.log
 * (её показывает панель приложения, см. MainActivity.onConsoleMessage).
 */
(function () {
  "use strict";

  var FNS = /(^|\.)lkdr\.nalog\.ru$/i;
  var LIST = "/api/v1/receipt";
  var TIMEOUT = 8000;       // один запрос дольше — считаем, что кабинет молчит
  var TOKEN_WAIT = 2400;    // ждём, пока страница сходит в кабинет и покажет ключ
  var LOG = "korzina: ";
  var FAIL = "korzina! ";

  var caught = "";

  function log(what) {
    try { console.log(LOG + what); } catch (e) { /* журнал закрыт — не беда */ }
  }

  /** Беда, после которой возврата не будет: панель приложения должна сказать словами. */
  function fail(what) {
    try { console.log(FAIL + what); } catch (e) { /* журнал закрыт — не беда */ }
  }

  function short(err) {
    return String((err && err.message) || err || "неизвестно").slice(0, 160);
  }

  function wait(ms) {
    return new Promise(function (done) { setTimeout(done, ms); });
  }

  /* --- доступ к кабинету ---
   *
   * Заголовок живого запроса кабинета. Ключ в хранилище могут переименовать при
   * любом обновлении сайта, а заголовок настоящего запроса не соврёт никогда.
   * Поэтому подслушиваем свой же кабинет: как только он сам сходит на сервер,
   * у нас есть рабочий пропуск. Тот же приём, что в docs/grab.src.js.
   */
  function listen() {
    if (window.__kzFnsListen) return;
    window.__kzFnsListen = true;
    var set = XMLHttpRequest.prototype.setRequestHeader;
    XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
      try {
        if (String(name).toLowerCase() === "authorization" && value) caught = String(value);
      } catch (e) { /* чужой заголовок нас не касается */ }
      return set.apply(this, arguments);
    };
    var real = window.fetch;
    if (real) {
      window.fetch = function (input, init) {
        try {
          var h = (init && init.headers) || (input && input.headers);
          if (h) {
            var got = typeof h.get === "function"
              ? h.get("Authorization")
              : (h.Authorization || h.authorization);
            if (got) caught = String(got);
          }
        } catch (e) { /* чужой запрос нас не касается */ }
        return real.apply(this, arguments);
      };
    }
  }

  var TOKEN_KEYS = ["auth.token", "access.token", "accessToken", "token"];
  var JWT = /^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+$/;

  function storages() {
    var boxes = [];
    try { boxes.push(localStorage); } catch (e) { /* закрыто настройками */ }
    try { boxes.push(sessionStorage); } catch (e) { /* закрыто настройками */ }
    return boxes;
  }

  function token() {
    if (caught) return caught;
    var boxes = storages(), i, j, box, value;
    for (i = 0; i < boxes.length; i++) {
      for (j = 0; j < TOKEN_KEYS.length; j++) {
        value = boxes[i].getItem(TOKEN_KEYS[j]);
        if (value && JWT.test(value)) return value;
      }
    }
    /* имя ключа поменялось — ищем что-нибудь похожее на пропуск */
    for (i = 0; i < boxes.length; i++) {
      box = boxes[i];
      for (j = 0; j < box.length; j++) {
        value = box.getItem(box.key(j));
        if (value && JWT.test(value) && !/refresh/i.test(box.key(j))) return value;
      }
    }
    return "";
  }

  /* Refresh-токен и идентификатор устройства нужны приложению, чтобы ходить в
   * кабинет самому: ключ доступа живёт недолго, refresh-токен обновляет его без
   * человека, а устройство кабинет помнит и по нему выдаёт новые ключи. */
  var REFRESH_KEYS = ["refresh.token", "refreshToken", "refresh"];

  function unquote(value) {
    return String(value || "").replace(/^"+|"+$/g, "");
  }

  function refreshToken() {
    var boxes = storages(), i, j, box, value;
    for (i = 0; i < boxes.length; i++) {
      for (j = 0; j < REFRESH_KEYS.length; j++) {
        value = unquote(boxes[i].getItem(REFRESH_KEYS[j]));
        if (value.length > 16) return value;
      }
    }
    for (i = 0; i < boxes.length; i++) {
      box = boxes[i];
      for (j = 0; j < box.length; j++) {
        if (/refresh/i.test(box.key(j))) {
          value = unquote(box.getItem(box.key(j)));
          if (value.length > 16) return value;
        }
      }
    }
    return "";
  }

  function deviceId() {
    var boxes = storages(), i, j, box, value;
    for (i = 0; i < boxes.length; i++) {
      box = boxes[i];
      for (j = 0; j < box.length; j++) {
        if (/deviceid/i.test(box.key(j))) {
          value = unquote(box.getItem(box.key(j)));
          if (value.length >= 8) return value;
        }
      }
    }
    return "";
  }

  /* --- запрос к кабинету ---
   *
   * Тот же запрос, что делает сам кабинет, когда человек листает чеки. Один и с
   * паузой, которую задаёт срок: кабинет не наш, выгребать его нельзя.
   */
  function probe(pass) {
    var headers = { "Content-Type": "application/json" };
    if (pass) headers.Authorization = /^Bearer /i.test(pass) ? pass : "Bearer " + pass;
    var stop = (typeof AbortController === "function") ? new AbortController() : null;
    var timer = null;
    var hang = new Promise(function (_, reject) {
      timer = setTimeout(function () {
        if (stop) { try { stop.abort(); } catch (e) { /* уже закрыт */ } }
        reject(new Error("кабинет не ответил за " + (TIMEOUT / 1000) + " с"));
      }, TIMEOUT);
    });
    var ask = fetch(LIST, {
      method: "POST",
      headers: headers,
      credentials: "include",
      body: JSON.stringify({ limit: 1, offset: 0, orderBy: "CREATED_DATE:DESC" }),
      signal: stop ? stop.signal : undefined
    });
    return Promise.race([ask, hang]).then(function (r) {
      clearTimeout(timer);
      if (r.status === 401 || r.status === 403) {
        throw new Error("кабинет не узнал вход — обновите страницу кабинета");
      }
      if (!r.ok) throw new Error("кабинет ответил " + r.status);
      return r.json();
    }, function (err) {
      clearTimeout(timer);
      throw err;
    });
  }

  /* --- возврат в приложение --- */

  /* Ключ — в адрес приложения, без пробелов и плюсов, чтобы доехал целым. */
  function pack(text) {
    return btoa(unescape(encodeURIComponent(text)))
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function send(pass) {
    var keys = JSON.stringify({
      source: "lkdr-keys",
      takenAt: new Date().toISOString(),
      token: String(pass).replace(/^Bearer /i, ""),
      refresh: refreshToken(),
      deviceId: deviceId()
    });
    var packed = pack(keys);
    log("вход найден — возвращаю в приложение");
    /* location.replace — чтобы страница кабинета с ключом не осталась в истории. */
    location.replace(window.KORZINA_APP_URL + "?keys=" + encodeURIComponent(packed));
  }

  function handover(pass) {
    /* Проба не должна держать возврат: не ответила за свой срок — уходим с ключом.
     * Живой ключ проверит и сервер (connect_with_keys), и обновит по refresh-токену. */
    var guard = wait(TIMEOUT + 1000);
    Promise.race([probe(pass), guard]).then(function () {
      send(pass);
    }, function (err) {
      log("проба описи не прошла: " + short(err));
      send(pass);
    });
  }

  function start() {
    var pass = token();
    if (pass) {
      handover(pass);
      return;
    }
    /* Кабинет мог ещё не сходить на сервер: коротко ждём и смотрим ещё раз. */
    var left = TOKEN_WAIT;
    (function again() {
      pass = token();
      if (pass) { handover(pass); return; }
      if (left <= 0) {
        fail("вход не найден — войдите в кабинет и нажмите «Забрать чеки» ещё раз");
        return;
      }
      left -= 300;
      wait(300).then(again);
    })();
  }

  if (!FNS.test(location.hostname)) {
    fail("это не кабинет ФНС — забирать нечего");
    return;
  }
  if (!window.KORZINA_APP_URL) {
    fail("приложение не назвало свой адрес — ключ некуда отдать");
    return;
  }

  listen();
  start();
})();
