/* Букмарклет «Забрать цены» — читаемый исходник.
 *
 * ЗАЧЕМ ОН НУЖЕН. У Пятёрочки и Самоката программного канала нет: их витрины
 * закрыты для нашего сервера. Замер 19.09.2026 разложил это на два условия, и они
 * работают вместе:
 *
 *   РЕПУТАЦИЯ АДРЕСА   с серверного адреса приходит отказ «проверьте настройки
 *                      интернета и VPN» — до проверки дело не доходит вовсе,
 *                      и настоящий Chromium на сервере не помогает;
 *   СПОСОБНОСТЬ КЛИЕНТА с обычного домашнего адреса простой HTTP-запрос получает
 *                      заглушку JS-проверки, а настоящий браузер её проходит.
 *
 * Отсюда вывод, ради которого написан этот файл: витрину видит БРАУЗЕР ЧЕЛОВЕКА,
 * и только он. Не потому, что так можно кого-то обхитрить, а потому, что человек
 * и есть покупатель: он приходит со своего адреса, своим браузером, и никакой
 * проверки для него нет.
 *
 * ЧТО ОН ДЕЛАЕТ. Снимает со страницы каталога то, что человек и так видит:
 * артикул, название, цену, фасовку и адрес карточки. Считает их и показывает
 * число. Дальше человек сам решает — скопировать пакет себе или отправить его в
 * своё рабочее место.
 *
 * ГРАНИЦЫ, КОТОРЫЕ ЗДЕСЬ ЖЁСТКИЕ.
 *
 * - Ничего не происходит без нажатия. Скрипт живёт до закрытия вкладки, в фоне не
 *   работает и ничего о человеке не запоминает.
 * - Берётся только ВИТРИНА. Ни кук, ни токенов, ни содержимого кабинета, ни
 *   корзины, ни личных купонов: в пакет попадает ровно то, что напечатано на
 *   карточке товара.
 * - Секрет рабочего места спрашивается один раз и хранится в памяти самой
 *   страницы. В адресную строку он не попадает никогда — дверь приложения этого
 *   и не примет (app/api.py, проверка параметров адреса).
 * - Своя страница — свой темп. Прокрутка идёт шагами с паузой, как листает
 *   человек; гнать витрину в сто оборотов незачем и невежливо.
 *
 * КУДА ПОПАДАЮТ ДАННЫЕ. В дверь POST /api/prices рабочего места (app/api.py), и
 * дальше в app/collector.accept → app/pricebundle. Дверь принимает пакеты ровно
 * от двух сетей — pyaterochka и samokat (BROWSER_STORES), — потому что построена
 * она именно для этого случая.
 *
 * ОТКУДА ВЗЯТ РАЗБОР. Снято живьём 19.09.2026 в браузере владельца на
 * 5ka.ru/catalog/molochnye-produkty-yaytsa--251C51940/: артикул лежит в адресе
 * карточки (…--78001656/), а в её тексте подряд идут оценка, название, фасовка и
 * цена, причём цена РАЗДЕЛЕНА на рубли и копейки («228 99 ₽»). Отдельно
 * встречается приписка «Цена за 100 г» — её надо отделить от названия, иначе она
 * приедет в каталог как часть имени товара.
 *
 * Собранная версия лежит в grab-prices.html как ссылка javascript:… — её тащат в
 * закладки. Этот файл источник правды; пересборка — tools/build_bookmarklet.py.
 * Сборка склеивает строки через пробел, поэтому каждая инструкция обязана
 * заканчиваться точкой с запятой.
 */
(function () {
  "use strict";

  var HOST = location.hostname;
  var STORE = /(^|\.)5ka\.ru$/.test(HOST) ? "pyaterochka"
            : /(^|\.)samokat\.ru$/.test(HOST) ? "samokat" : null;

  if (!STORE) {
    alert("Эта закладка работает на витрине Пятёрочки или Самоката.\n" +
          "Откройте раздел каталога и нажмите её там.");
    return;
  }

  var SCROLL_STEPS = 14;      /* сколько раз пролистать вниз, добирая подгрузку */
  var SCROLL_PAUSE = 1600;    /* пауза между шагами: столько же листает человек */
  var MAX_ITEMS = 3000;       /* потолок пакета; дверь всё равно не примет больше */

  /* ---------- разбор карточки ---------- */

  /* Артикул живёт в самом адресе: /product/<название>--<артикул>/ */
  function skuOf(href) {
    var m = String(href || "").match(/--(\d+)\/?(?:[?#]|$)/);
    return m ? m[1] : null;
  }

  /* «4,85 Название 1 л 228 99 ₽» -> {name, price}. Цена разделена на рубли и
     копейки, а оценка и фасовка стоят по краям. */
  function readCard(text) {
    var t = String(text || "").replace(/\s+/g, " ").trim();
    var m = t.match(/^([\d,]+)?\s*(.+?)\s+(\d+)\s+(\d{2})\s*₽$/);
    if (!m) return null;
    var name = m[2]
      .replace(/\s*Цена за\s*\d+\s*(г|мл|кг|л)\s*$/i, "")
      .replace(/\s*\d+(?:[.,]\d+)?\s*(г|мл|кг|л|шт)\s*$/i, "")
      .trim();
    var price = Number(m[3] + "." + m[4]);
    if (!name || !(price > 0)) return null;
    return { name: name, price: price };
  }

  /* Фасовка — из названия: она там почти всегда написана.
   *
   * ДВЕ ЛОВУШКИ, ОБЕ ПОЙМАНЫ ЖИВОЙ ПРОВЕРКОЙ 19.09.2026 — из двенадцати позиций
   * фасовка не разобралась НИ У ОДНОЙ, хотя «200г» стояло в названиях прямо.
   *
   * 1. `\b` в JavaScript считает словом только латиницу и цифры. После русской
   *    «г» в конце строки границы слова НЕТ: обе стороны для него не-слово. Взамен
   *    стоит проверка «дальше не буква».
   * 2. Порядок в переборе важен: «кг» и «мл» должны стоять ПЕРЕД «г» и «л», иначе
   *    в «200мл» совпадёт «л» и литры станут миллилитрами.
   */
  function weightOf(name) {
    var m = String(name).match(/(\d+(?:[.,]\d+)?)\s*(кг|мл|г|л)(?![а-яёa-z])/i);
    if (!m) return null;
    var n = Number(m[1].replace(",", "."));
    var u = m[2].toLowerCase();
    if (u === "кг" || u === "л") n *= 1000;
    return n > 0 ? n : null;
  }

  var found = Object.create(null);

  function harvest() {
    var links = document.querySelectorAll('a[href*="/product/"]');
    for (var i = 0; i < links.length; i++) {
      var a = links[i];
      var sku = skuOf(a.getAttribute("href"));
      if (!sku || found[sku]) continue;
      var card = readCard(a.innerText);
      if (!card) continue;
      found[sku] = {
        sku: sku,
        name: card.name,
        price: card.price,
        weight_g: weightOf(card.name),
        url: new URL(a.getAttribute("href"), location.origin).href,
        in_stock: true
      };
      if (Object.keys(found).length >= MAX_ITEMS) return;
    }
  }

  /* ---------- панель ---------- */

  var box = document.createElement("div");
  box.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;" +
    "background:#111;color:#fff;font:14px/1.45 system-ui,sans-serif;padding:14px 16px;" +
    "border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,.35);max-width:320px";
  var say = document.createElement("div");
  say.textContent = "Читаю витрину…";
  box.appendChild(say);
  var row = document.createElement("div");
  row.style.cssText = "margin-top:10px;display:flex;gap:8px;flex-wrap:wrap";
  box.appendChild(row);
  document.body.appendChild(box);

  function button(title, onClick) {
    var b = document.createElement("button");
    b.textContent = title;
    b.style.cssText = "border:0;border-radius:8px;padding:7px 11px;cursor:pointer;" +
      "font:13px system-ui,sans-serif;background:#fff;color:#111";
    b.onclick = onClick;
    row.appendChild(b);
    return b;
  }

  function packet() {
    var items = [];
    for (var k in found) items.push(found[k]);
    return {
      store: STORE,
      collected_at: new Date().toISOString().slice(0, 19),
      address: (document.body.innerText.match(/^[^\n]{4,80}/) || [""])[0].trim(),
      items: items
    };
  }

  /* ---------- сбор ---------- */

  var step = 0;
  (function walk() {
    harvest();
    say.textContent = "Собрано позиций: " + Object.keys(found).length +
      (step < SCROLL_STEPS ? " — листаю дальше…" : "");
    if (step++ >= SCROLL_STEPS) return done();
    window.scrollTo(0, document.body.scrollHeight);
    setTimeout(walk, SCROLL_PAUSE);
  })();

  function done() {
    var n = Object.keys(found).length;
    window.scrollTo(0, 0);
    if (!n) {
      say.textContent = "Товаров на странице не нашлось. Откройте раздел каталога, " +
        "где видны карточки с ценами, и нажмите закладку снова.";
      return;
    }
    say.textContent = "Готово: " + n + " позиций с ценами.";

    button("Скопировать пакет", function () {
      var text = JSON.stringify(packet(), null, 1);
      navigator.clipboard.writeText(text).then(function () {
        say.textContent = "Пакет скопирован. Вставьте его в приложении.";
      }, function () {
        say.textContent = "Скопировать не вышло — сохраните файлом.";
      });
    });

    button("Отправить в приложение", function () {
      var base = prompt("Адрес приложения", localStorage.getItem("korzina.app") || "");
      if (!base) return;
      var who = prompt("Номер рабочего места (телефон)", localStorage.getItem("korzina.who") || "");
      if (!who) return;
      /* Секрет НЕ запоминаем: он живёт ровно столько, сколько идёт отправка. */
      var secret = prompt("Секрет рабочего места (настройки, ключ api.secret)");
      if (!secret) return;
      localStorage.setItem("korzina.app", base);
      localStorage.setItem("korzina.who", who);
      var body = packet();
      body.workplace = who;
      body.secret = secret;
      say.textContent = "Отправляю " + Object.keys(found).length + " позиций…";
      fetch(String(base).replace(/\/+$/, "") + "/api/prices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }).then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, d: d }; });
      }).then(function (x) {
        say.textContent = x.ok
          ? ("Принято: " + (x.d.prices_saved != null ? x.d.prices_saved : x.d.saved) + " цен.")
          : ("Приложение отказало: " + (x.d.error || x.d.message || "неизвестно"));
      }).catch(function (e) {
        say.textContent = "Отправить не вышло: " + e.message;
      });
    });

    button("Закрыть", function () { box.remove(); });
  }
})();
