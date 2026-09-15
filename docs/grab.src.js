/* Букмарклет «Забрать покупки» — читаемый исходник.
 *
 * Зачем он нужен. Пока нет партнёрства с ФНС, историю покупок неоткуда взять,
 * кроме как выделить страницу, скопировать и вставить. На странице «Мои чеки
 * онлайн» или в личном кабинете Ленты выделение цепляет меню, подвал и рекламу,
 * и разбирать это тяжело.
 *
 * Букмарклет делает то же самое, только аккуратнее, и умеет копить: человек
 * открывает чек, жмёт «Добавить», открывает следующий, снова жмёт — и в конце
 * забирает всё разом одной кнопкой.
 *
 * ЧТО ИМЕННО БЕРЁТСЯ. Если на странице что-то выделено мышью — берём выделение,
 * и только его. Это и есть ответ на «выделить нужные чеки»: никакого знания об
 * устройстве чужого сайта не требуется, работает везде одинаково. Ничего не
 * выделено — берём содержательную часть страницы и выкидываем навигацию.
 *
 * ЧЕГО ОН НЕ ДЕЛАЕТ, и это главное. Не запоминает вход, не трогает куки, ничего
 * никуда не отправляет и не работает без нажатия. Всё происходит в браузере
 * человека, на странице, которую он сам открыл. Наружу уходит только то, что он
 * потом сам вставит в приложение. Накопленное лежит в localStorage того же
 * сайта и стирается кнопкой «Очистить».
 *
 * Собранная версия лежит в grab.html как ссылка javascript:… — её тащат в
 * закладки. Этот файл источник правды, править надо здесь, потом пересобрать
 * командой из tools/build_bookmarklet.py.
 */
(function () {
  "use strict";

  var KEY = "moya-korzina-buffer";
  var SEP = "\n\n— — — — —\n\n";

  // Куски страницы, которые товаром быть не могут ни при каком раскладе.
  var SKIP = /^(SCRIPT|STYLE|NOSCRIPT|SVG|NAV|HEADER|FOOTER|ASIDE|FORM|BUTTON|SELECT|CANVAS)$/;
  // Теги, после которых начинается новая строка.
  var BLOCK = /^(DIV|P|LI|TR|TABLE|SECTION|ARTICLE|H[1-6]|UL|OL|HR|DL|DD|DT|BLOCKQUOTE|PRE|MAIN)$/;
  // Где обычно лежит содержательная часть. Порядок важен: берём самое узкое.
  var MAIN = ["main", "[role=main]", "#content", ".content", "article"];
  // Строки, которые в чеке встречаются, но позицией не являются.
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
      .map(function (line) { return line.replace(/[ \t   ]+/g, " ").trim(); })
      .filter(function (line) {
        if (!line || line.length > 300) return false;
        if (NOISE.test(line)) return false;
        // повторы меню и хлебных крошек: одна и та же короткая строка много раз
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

  // --- накопитель ---
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
    // Буфер обмена может быть закрыт настройками браузера — тогда показываем
    // текст, чтобы человек скопировал сам, а не остался ни с чем.
    var area = document.createElement("textarea");
    area.value = text;
    area.style.cssText =
      "position:fixed;z-index:2147483647;inset:5vh 5vw;width:90vw;height:90vh;padding:16px;" +
      "font:14px/1.5 ui-monospace,Menlo,Consolas,monospace;border:2px solid #2A241E;border-radius:12px";
    document.body.appendChild(area);
    area.select();
    area.addEventListener("keydown", function (e) { if (e.key === "Escape") area.remove(); });
  }

  // --- панель ---
  var old = document.getElementById("mk-grab-panel");
  if (old) old.remove();

  var panel = document.createElement("div");
  panel.id = "mk-grab-panel";
  panel.style.cssText =
    "position:fixed;z-index:2147483647;left:50%;bottom:22px;transform:translateX(-50%);" +
    "display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:center;" +
    "max-width:min(94vw,640px);padding:12px 14px;border-radius:14px;background:#2A241E;color:#fff;" +
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

  var status = document.createElement("span");
  status.style.cssText = "color:#C9BFB2;font-weight:400";
  function refresh() {
    var n = count();
    status.textContent = n ? "в буфере " + n : "буфер пуст";
    takeAll.style.display = n ? "" : "none";
    clearBtn.style.display = n ? "" : "none";
  }

  var add = button("Добавить эту страницу", "main");
  var takeAll = button("Скопировать всё");
  var clearBtn = button("Очистить", "ghost");
  var close = button("✕", "ghost");

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

  [add, takeAll, clearBtn, status, close].forEach(function (el) { panel.appendChild(el); });
  document.body.appendChild(panel);
  refresh();
})();
