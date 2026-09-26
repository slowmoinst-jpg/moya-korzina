/* Букмарклет «Передать вход» — читаемый исходник.
 *
 * ЗАЧЕМ ОН НУЖЕН. Приложение держит браузер на своём сервере и входит в магазины
 * им. 20.09.2026 эта дорога упёрлась в стену, которой нечем помочь изнутри:
 * Магнит встретил вход проверкой Yandex SmartCaptcha, а после неё ответил
 * «Аккаунт заблокирован. Продолжите без него или войдите по-другому». Тот же
 * аккаунт с телефона владельца открывается как обычно. Разница одна — адрес, с
 * которого стучатся: у сервера он один на всё, и по нему же нас не пускают
 * Лента, Дикси, Пятёрочка и Самокат.
 *
 * Значит вход надо не добывать на сервере, а ПЕРЕНЕСТИ с телефона. Это возможно,
 * и это замерено: 17.09.2026 владелец вошёл в Магнит с телефона, и страница
 * видит ВСЕ двадцать одну куку magnit.ru, включая mg_at со значением в 648
 * знаков. То есть кука входа не HttpOnly, и скрипт на самой странице читает её
 * так же, как читает её код сети.
 *
 * ПОЧЕМУ В БУФЕР, А НЕ ЗАПРОСОМ К НАМ. Закладка работает на странице сети, то
 * есть на чужом источнике по https. Постучаться отсюда в нашу дверь нельзя
 * дважды: браузер запретит смешанное содержимое (https -> http) и потребует
 * разрешения CORS. Обе преграды настоящие, и обходить их пришлось бы ослаблением
 * двери. Буфер их просто не касается — той же дорогой ходит закладка ФНС.
 *
 * ЧТО УЕЗЖАЕТ И ЧЕГО ЗДЕСЬ НЕТ. Уезжают имена и значения кук ЭТОГО домена.
 * Пароля и кода из СМС среди них нет — их человек набрал в самой сети, и
 * страница их не хранит. Ничего никуда не отправляется: только буфер обмена,
 * дальше человек сам вставляет это в наше приложение.
 *
 * ИМЯ КУКИ ВХОДА ЗДЕСЬ НЕ ИЩЕТСЯ НАРОЧНО. Достоверно оно известно только у
 * Магнита (mg_at); у пяти остальных сетей в app/shopbrowser/signals.AUTH_COOKIE
 * пустые списки, и выдумывать его нельзя. Поэтому берётся банка целиком, а
 * разбирается она на нашей стороне (app/shopbrowser/handoff.py).
 */
(function () {
  /* Какая это сеть. Коды те же, что в приложении (app/shopbrowser/driver.py):
     разойдись они — приложение отвергнет передачу как «вход в другую сеть». */
  var SITES = [
    ["magnit.ru", "magnit"],
    ["5ka.ru", "pyaterochka"],
    ["samokat.ru", "samokat"],
    ["lenta.com", "lenta"],
    ["vkusvill.ru", "vkusvill"],
    ["dixy.ru", "dixy"],
    ["metro-cc.ru", "metro"],
    ["perekrestok.ru", "perekrestok"]
  ];

  function chainOf(host) {
    host = (host || "").toLowerCase();
    for (var i = 0; i < SITES.length; i++) {
      var home = SITES[i][0];
      if (host === home || host.slice(-(home.length + 1)) === "." + home) return SITES[i][1];
    }
    return "";
  }

  /* Куки страницы. document.cookie отдаёт их одной строкой «имя=значение; …»,
     и значение может содержать «=» — поэтому режем по ПЕРВОМУ знаку равенства,
     а не split("="): ключ mg_at заканчивается на «=» по правилам base64, и
     наивная разбивка отрезала бы ему хвост. */
  function jar() {
    var out = [];
    var raw = document.cookie || "";
    var parts = raw.split(";");
    for (var i = 0; i < parts.length; i++) {
      var piece = parts[i].trim();
      if (!piece) continue;
      var at = piece.indexOf("=");
      if (at < 1) continue;
      out.push({ name: piece.slice(0, at).trim(), value: piece.slice(at + 1) });
    }
    return out;
  }

  function say(text, bad) {
    var box = document.createElement("div");
    box.textContent = text;
    box.style.cssText = "position:fixed;left:12px;right:12px;bottom:16px;z-index:2147483647;" +
      "padding:14px 16px;border-radius:12px;font:15px/1.4 -apple-system,system-ui,sans-serif;" +
      "color:#fff;box-shadow:0 6px 24px rgba(0,0,0,.35);background:" +
      (bad ? "#b3261e" : "#1b5e20");
    document.body.appendChild(box);
    setTimeout(function () { box.remove(); }, 9000);
  }

  var APP = "__APP_URL__";

  function pack(s) {
    return btoa(unescape(encodeURIComponent(s)))
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  var chain = chainOf(location.hostname);
  if (!chain) {
    say("Это не магазин из «Моей корзины». Откройте сайт сети и нажмите закладку там.", true);
    return;
  }
  var cookies = jar();
  if (!cookies.length) {
    say("Страница не отдала ни одной куки — похоже, вы здесь не вошли.", true);
    return;
  }

  var text = JSON.stringify({
    store: chain,
    host: location.hostname,
    at: new Date().toISOString().slice(0, 19),
    cookies: cookies
  });

  if (APP.indexOf("http") === 0) {
    say("Вход найден (" + cookies.length + " кук). Подключаю к «Моей корзине»...");
    var target = APP + (APP.indexOf("?") < 0 ? "?" : "&") + "store=" + encodeURIComponent(chain) + "&vhod=" + encodeURIComponent(pack(text));
    location.replace(target);
    return;
  }

  /* Буфер обмена есть не везде: он требует https и разрешения, а на старых
     телефонах его нет вовсе. Запасной путь — окно с выделенным текстом: его
     человек скопирует сам, и это лучше, чем «не получилось». */
  function fallback() {
    var area = document.createElement("textarea");
    area.value = text;
    area.style.cssText = "position:fixed;left:5%;top:10%;width:90%;height:50%;z-index:2147483647;" +
      "font:12px monospace;padding:8px;border:2px solid #1b5e20;border-radius:8px;background:#fff;";
    document.body.appendChild(area);
    area.focus();
    area.select();
    say("Скопируйте выделенный текст и вставьте его в «Моей корзине», в кабинете этой сети.");
  }

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function () {
      say("Вход скопирован (" + cookies.length + " кук). Откройте «Мою корзину», " +
          "экран «Кабинеты» → эта сеть, и вставьте.");
    }, fallback);
  } else {
    fallback();
  }
})();
