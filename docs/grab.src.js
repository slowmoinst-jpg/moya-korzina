/* Букмарклет «Забрать покупки» — читаемый исходник.
 *
 * Зачем он нужен. Пока нет партнёрства с ФНС, историю покупок неоткуда взять,
 * кроме как из кабинета «Мои чеки онлайн». Приложение прочитать это окно не может
 * ни при каком браузере: содержимое чужого источника странице недоступно. А вот
 * скрипт, запущенный НА самой странице кабинета, — может, потому что для браузера
 * он часть этой страницы, как и её собственный код.
 *
 * ДВА РЕЖИМА.
 *
 * 1. «Забрать все чеки» — только в кабинете ФНС. Кабинет разговаривает со своим
 *    сервером обычными запросами: список чеков и отдельно фискальные данные по
 *    каждому. Букмарклет делает ровно те же запросы, что делает сам кабинет, когда
 *    человек листает список и открывает чек, — только подряд и без пауз на клики.
 *    На выходе один файл: все чеки с позициями плюс ОПИСЬ кабинета (ключ, дата,
 *    сумма по каждому чеку). Опись нужна, чтобы приложение могло честно сказать
 *    «у вас загружено 47 из 213», а не «загрузите что-нибудь ещё».
 *
 * 2. «Добавить эту страницу» — везде. Читает текст страницы или выделение и копит
 *    его. Это запасной путь: он не знает ничего об устройстве сайта, поэтому
 *    работает и там, где никакого API нет, — в письме, в личном кабинете магазина,
 *    на странице заказа. И он же выручит, если кабинет однажды поменяет запросы.
 *
 * ГРАНИЦЫ, КОТОРЫЕ ЗДЕСЬ ЖЁСТКИЕ.
 *
 * - Токен доступа остаётся в браузере. Он берётся из хранилища той же страницы и
 *   уходит только обратно в кабинет, тем же адресам, что и всегда. Ни на наш
 *   сервер, ни куда-либо ещё не отправляется ничего: файл человек сохраняет себе
 *   и сам решает, нести ли его в приложение.
 * - Запросы идут по одному, с паузой. Кабинет не наш, и выгребать его в сто
 *   потоков было бы свинством: PAUSE ниже — не украшение, а правило поведения.
 * - Ничего не происходит без нажатия. Скрипт живёт до закрытия вкладки, ничего не
 *   запоминает о входе и не работает в фоне.
 * - Уже выгруженные ключи помнятся в хранилище самого кабинета, чтобы повторный
 *   заход тянул только новое. Кнопка «Заново» это забывает.
 *
 * Собранная версия лежит в grab.html как ссылка javascript:… — её тащат в
 * закладки. Этот файл источник правды, править надо здесь, потом пересобрать
 * командой из tools/build_bookmarklet.py. Сборка склеивает строки через пробел,
 * поэтому каждая инструкция обязана заканчиваться точкой с запятой.
 */
(function () {
  "use strict";

  var KEY = "moya-korzina-buffer";
  var DONE = "moya-korzina-taken";
  var SEP = "\n\n— — — — —\n\n";

  /* Кабинет ФНС. Режим с запросами включается только здесь и больше нигде. */
  var FNS = /(^|\.)lkdr\.nalog\.ru$/i;
  var LIST = "/api/v1/receipt";
  var FISCAL = "/api/v1/receipt/fiscal_data";
  var PAUSE = 160;
  var PAGE = 100;
  var MAX_PAGES = 300;

  var stopped = false;
  var caught = "";

  /* Куски страницы, которые товаром быть не могут ни при каком раскладе. */
  var SKIP = /^(SCRIPT|STYLE|NOSCRIPT|SVG|NAV|HEADER|FOOTER|ASIDE|FORM|BUTTON|SELECT|CANVAS)$/;
  /* Теги, после которых начинается новая строка. */
  var BLOCK = /^(DIV|P|LI|TR|TABLE|SECTION|ARTICLE|H[1-6]|UL|OL|HR|DL|DD|DT|BLOCKQUOTE|PRE|MAIN)$/;
  /* Где обычно лежит содержательная часть. Порядок важен: берём самое узкое. */
  var MAIN = ["main", "[role=main]", "#content", ".content", "article"];
  /* Строки, которые в чеке встречаются, но позицией не являются. */
  var NOISE = new RegExp(
    "^(меню|войти|выйти|назад|закрыть|поиск|каталог|профиль|настройки|ещё|подробнее" +
    "|скачать|поделиться|печать|фильтр|сортировк|cookie|куки|реклама|наверх)", "i");

  /* Текст узла с переводами строк по блокам.
   *
   * Просится innerText, но он врёт на копии: у открепленного от документа узла
   * нет раскладки, и Chrome возвращает пустую строку. Именно на этом первая
   * версия букмарклета молча отдавала ноль строк. Поэтому обходим дерево сами —
   * так одинаково работает и на живой странице, и на копии.
   */
  function textOf(node) {
    var out = [];
    (function walk(n) {
      if (n.nodeType === 3) { out.push(n.nodeValue); return; }
      if (n.nodeType !== 1) return;
      var tag = n.tagName;
      if (SKIP.test(tag)) return;
      if (n.getAttribute && n.getAttribute("aria-hidden") === "true") return;
      if (tag === "BR") { out.push("\n"); return; }
      for (var c = n.firstChild; c; c = c.nextSibling) walk(c);
      if (tag === "TD" || tag === "TH") out.push("  ");
      else if (BLOCK.test(tag)) out.push("\n");
    })(node);
    return out.join("");
  }

  function pickRoot() {
    for (var i = 0; i < MAIN.length; i++) {
      var node = document.querySelector(MAIN[i]);
      if (node && textOf(node).trim().length > 200) return node;
    }
    return document.body;
  }

  function clean(text) {
    var seen = Object.create(null);
    return text
      .split("\n")
      .map(function (line) { return line.replace(/[ \t   ]+/g, " ").trim(); })
      .filter(function (line) {
        if (!line || line.length > 300) return false;
        if (NOISE.test(line)) return false;
        if (line.length < 24) {
          if (seen[line]) return false;
          seen[line] = true;
        }
        return true;
      })
      .join("\n");
  }

  /* Выделенное мышью, если оно есть. Это и есть «выбранные чеки». */
  function selected() {
    var sel = window.getSelection && window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return "";
    var holder = document.createElement("div");
    for (var i = 0; i < sel.rangeCount; i++) {
      holder.appendChild(sel.getRangeAt(i).cloneContents());
    }
    return clean(textOf(holder));
  }

  function grab() {
    return selected() || clean(textOf(pickRoot()));
  }

  /* --- накопитель текстового режима --- */
  function buffer() {
    try { return sessionStorage.getItem(KEY) || ""; } catch (e) { return window.__mkBuf || ""; }
  }
  function setBuffer(value) {
    try { sessionStorage.setItem(KEY, value); } catch (e) { window.__mkBuf = value; }
  }
  function count() {
    var b = buffer();
    return b ? b.split(SEP).length : 0;
  }

  /* --- память о выгруженном ---
   *
   * Лежит в хранилище самого кабинета, а не у нас: это заметка браузера себе,
   * чтобы второй заход не тянул заново две сотни чеков. На то, что уже попало в
   * приложение, она не влияет — там своя проверка по ключу чека.
   */
  function taken() {
    try { return JSON.parse(localStorage.getItem(DONE) || "{}") || {}; } catch (e) { return {}; }
  }
  function remember(keys) {
    var map = taken();
    keys.forEach(function (k) { map[k] = 1; });
    try { localStorage.setItem(DONE, JSON.stringify(map)); } catch (e) { /* переполнено — не беда */ }
  }
  function forget() {
    try { localStorage.removeItem(DONE); } catch (e) { /* нечего забывать */ }
  }

  /* --- доступ к кабинету --- */

  /* Заголовок живого запроса кабинета. Ключ в хранилище могут переименовать при
   * любом обновлении сайта, а заголовок настоящего запроса — не соврёт никогда.
   * Поэтому подслушиваем свой же кабинет: как только он сам сходит на сервер,
   * у нас есть рабочий пропуск.
   */
  function listen() {
    if (window.__mkListen) return;
    window.__mkListen = true;
    var set = XMLHttpRequest.prototype.setRequestHeader;
    XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
      if (String(name).toLowerCase() === "authorization" && value) caught = String(value);
      return set.apply(this, arguments);
    };
    var real = window.fetch;
    if (real) {
      window.fetch = function (input, init) {
        try {
          var h = (init && init.headers) || (input && input.headers);
          if (h) {
            var got = typeof h.get === "function" ? h.get("Authorization") : (h.Authorization || h.authorization);
            if (got) caught = String(got);
          }
        } catch (e) { /* чужой запрос нас не касается */ }
        return real.apply(this, arguments);
      };
    }
  }

  var TOKEN_KEYS = ["auth.token", "access.token", "accessToken", "token"];
  var JWT = /^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+$/;

  function token() {
    if (caught) return caught;
    var boxes = [];
    try { boxes.push(localStorage); } catch (e) { /* закрыто настройками */ }
    try { boxes.push(sessionStorage); } catch (e) { /* закрыто настройками */ }
    var i, j, box, value;
    for (i = 0; i < boxes.length; i++) {
      for (j = 0; j < TOKEN_KEYS.length; j++) {
        value = boxes[i].getItem(TOKEN_KEYS[j]);
        if (value && JWT.test(value)) return value;
      }
    }
    /* имя ключа поменялось — ищем что-нибудь, похожее на пропуск */
    for (i = 0; i < boxes.length; i++) {
      box = boxes[i];
      for (j = 0; j < box.length; j++) {
        value = box.getItem(box.key(j));
        if (value && JWT.test(value) && !/refresh/i.test(box.key(j))) return value;
      }
    }
    return "";
  }

  function wait(ms) {
    return new Promise(function (done) { setTimeout(done, ms); });
  }

  function api(path, body) {
    var pass = token();
    var headers = { "Content-Type": "application/json" };
    if (pass) headers.Authorization = /^Bearer /i.test(pass) ? pass : "Bearer " + pass;
    return fetch(path, {
      method: "POST",
      headers: headers,
      credentials: "include",
      body: JSON.stringify(body)
    }).then(function (r) {
      if (r.status === 401 || r.status === 403) {
        throw new Error("кабинет не узнал вход — обновите страницу кабинета и попробуйте снова");
      }
      if (!r.ok) throw new Error("кабинет ответил " + r.status);
      return r.json();
    });
  }

  /* Опись кабинета: все чеки, какие он показывает, без позиций. */
  function catalogue(onStep) {
    var all = [];
    var seen = Object.create(null);
    var offset = 0;
    var page = 0;
    function step() {
      if (stopped) return Promise.resolve(all);
      return api(LIST, { limit: PAGE, offset: offset, orderBy: "CREATED_DATE:DESC" }).then(function (data) {
        var got = (data && data.receipts) || [];
        var brands = (data && data.brands) || [];
        var fresh = 0;
        got.forEach(function (r) {
          if (!r || !r.key || seen[r.key]) return;
          seen[r.key] = 1;
          fresh += 1;
          var brand = brands.filter(function (b) { return b && b.id === r.brandId; })[0];
          all.push({
            key: r.key,
            date: r.createdDate || r.receiveDate || r.buyDate || null,
            store: r.brand || (brand && brand.name) || r.retailPlace || "",
            total: r.totalSum
          });
        });
        offset += got.length;
        page += 1;
        onStep(all.length);
        /* Три страховки от бесконечного листания: сервер сказал «хватит», страница
         * не принесла ничего нового, или мы уже перелистали больше разумного. */
        if (!data || !data.hasMore || !fresh || page >= MAX_PAGES) return all;
        return wait(PAUSE).then(step);
      });
    }
    return step();
  }

  /* Позиции по каждому чеку — по одному запросу, с паузой. */
  function fiscal(list, onStep) {
    var out = [];
    var failed = [];
    var i = 0;
    function step() {
      if (stopped || i >= list.length) return Promise.resolve({ receipts: out, failed: failed });
      var item = list[i];
      i += 1;
      return api(FISCAL, { key: item.key }).then(function (data) {
        out.push({
          key: item.key, date: item.date, store: item.store, total: item.total, fiscalData: data
        });
      }).catch(function (err) {
        failed.push({ key: item.key, reason: String(err && err.message || err) });
      }).then(function () {
        onStep(out.length, failed.length, list.length);
        return wait(PAUSE).then(step);
      });
    }
    return step();
  }

  function download(text, name) {
    var blob = new Blob([text], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }

  function stamp() {
    var d = new Date();
    function two(n) { return (n < 10 ? "0" : "") + n; }
    return d.getFullYear() + two(d.getMonth() + 1) + two(d.getDate()) + "-" + two(d.getHours()) + two(d.getMinutes());
  }

  function copy(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var area = document.createElement("textarea");
      area.value = text;
      area.style.cssText = "position:fixed;top:-1000px;opacity:0";
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      area.remove();
      ok ? resolve() : reject(new Error("копирование запрещено"));
    });
  }

  function showText(text) {
    /* Буфер обмена может быть закрыт настройками браузера — тогда показываем
     * текст, чтобы человек скопировал сам, а не остался ни с чем. */
    var area = document.createElement("textarea");
    area.value = text;
    area.style.cssText =
      "position:fixed;z-index:2147483647;inset:5vh 5vw;width:90vw;height:90vh;padding:16px;" +
      "font:14px/1.5 ui-monospace,Menlo,Consolas,monospace;border:2px solid #2A241E;border-radius:12px";
    document.body.appendChild(area);
    area.select();
    area.addEventListener("keydown", function (e) { if (e.key === "Escape") area.remove(); });
  }

  /* --- панель --- */
  var old = document.getElementById("mk-grab-panel");
  if (old) old.remove();

  var panel = document.createElement("div");
  panel.id = "mk-grab-panel";
  panel.style.cssText =
    "position:fixed;z-index:2147483647;left:50%;bottom:22px;transform:translateX(-50%);" +
    "display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:center;" +
    "max-width:min(94vw,680px);padding:12px 14px;border-radius:14px;background:#2A241E;color:#fff;" +
    "font:500 14px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;" +
    "box-shadow:0 12px 40px -14px rgba(0,0,0,.7)";

  function button(label, tone) {
    var b = document.createElement("button");
    b.textContent = label;
    b.style.cssText =
      "cursor:pointer;border:0;border-radius:9px;padding:9px 13px;font:inherit;font-weight:600;" +
      (tone === "main" ? "background:#BD6533;color:#fff;"
       : tone === "ghost" ? "background:transparent;color:#C9BFB2;padding:9px 8px;"
       : "background:#453D33;color:#fff;");
    return b;
  }

  var busy = false;

  var status = document.createElement("span");
  status.style.cssText = "color:#C9BFB2;font-weight:400";

  var here = FNS.test(location.hostname);
  var takeAuto = button("Забрать все чеки", "main");
  var again = button("Заново", "ghost");
  var stop = button("Стоп", "ghost");
  var add = button("Добавить эту страницу", here ? "" : "main");
  var takeAll = button("Скопировать всё");
  var clearBtn = button("Очистить", "ghost");
  var close = button("✕", "ghost");

  function refresh() {
    var n = count();
    if (!busy) status.textContent = n ? "в буфере " + n : (here ? "готов забрать чеки" : "буфер пуст");
    takeAll.style.display = n ? "" : "none";
    clearBtn.style.display = n ? "" : "none";
    takeAuto.style.display = here && !busy ? "" : "none";
    again.style.display = here && !busy && Object.keys(taken()).length ? "" : "none";
    stop.style.display = busy ? "" : "none";
    add.style.display = busy ? "none" : "";
  }

  function run(force) {
    busy = true;
    stopped = false;
    refresh();
    status.textContent = "читаю список чеков…";
    var known = force ? {} : taken();
    catalogue(function (n) {
      status.textContent = "в кабинете чеков: " + n + "…";
    }).then(function (all) {
      var need = all.filter(function (r) { return !known[r.key]; });
      if (!all.length) {
        throw new Error("кабинет не показал ни одного чека — войдите в него в этой же вкладке");
      }
      if (!need.length) {
        return { receipts: [], failed: [], all: all };
      }
      status.textContent = "беру позиции: 0 из " + need.length + "…";
      return fiscal(need, function (done, bad, total) {
        status.textContent = "беру позиции: " + done + " из " + total + (bad ? " · не вышло " + bad : "") + "…";
      }).then(function (res) {
        res.all = all;
        return res;
      });
    }).then(function (res) {
      var bundle = {
        source: "lkdr",
        takenAt: new Date().toISOString(),
        catalogue: res.all,
        receipts: res.receipts,
        failed: res.failed
      };
      var text = JSON.stringify(bundle);
      remember(res.receipts.map(function (r) { return r.key; }));
      busy = false;
      refresh();
      if (!res.receipts.length) {
        status.textContent = "новых чеков нет — все " + res.all.length + " уже забраны";
        return;
      }
      download(text, "moya-korzina-" + stamp() + ".json");
      status.textContent = "готово: чеков " + res.receipts.length + " из " + res.all.length +
        (res.failed.length ? " · не вышло " + res.failed.length : "") + " · файл сохранён";
    }).catch(function (err) {
      busy = false;
      refresh();
      status.textContent = String(err && err.message || err);
    });
  }

  takeAuto.onclick = function () { run(false); };
  again.onclick = function () { forget(); run(true); };
  stop.onclick = function () { stopped = true; status.textContent = "останавливаюсь…"; };

  add.onclick = function () {
    var text = grab();
    var lines = text.split("\n").filter(Boolean).length;
    if (lines < 3) {
      status.textContent = "здесь почти нет текста — откройте сам чек";
      return;
    }
    var had = buffer();
    setBuffer(had ? had + SEP + text : text);
    add.textContent = "Добавлено · " + lines + " строк";
    setTimeout(function () { add.textContent = "Добавить эту страницу"; }, 2000);
    refresh();
  };

  takeAll.onclick = function () {
    var all = buffer();
    if (!all) return;
    copy(all).then(function () {
      setBuffer("");
      refresh();
      status.textContent = "скопировано — вставьте в «Мою корзину»";
    }).catch(function () {
      showText(all);
      status.textContent = "браузер не дал скопировать — текст выделен, нажмите Ctrl+C";
    });
  };

  clearBtn.onclick = function () { setBuffer(""); refresh(); };
  close.onclick = function () { panel.remove(); };

  [takeAuto, again, stop, add, takeAll, clearBtn, status, close].forEach(function (el) {
    panel.appendChild(el);
  });
  document.body.appendChild(panel);
  if (here) listen();
  refresh();
})();
