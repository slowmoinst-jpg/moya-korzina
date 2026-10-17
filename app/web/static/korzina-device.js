/* Мост между сайтом и приложением на телефоне (шаг 1 единой архитектуры).

   ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ, А НЕ ПРЯМЫЕ ВЫЗОВЫ window.Korzina ИЗ ШАБЛОНОВ.
   window.Korzina вставляет в страницу приложение (MainActivity, JavascriptInterface),
   и существует он только внутри него: в обычном браузере обращение к нему —
   ошибка скрипта. Вся проверка «а есть ли мост» живёт здесь один раз, а экраны
   спрашивают KorzinaDevice.available() и зовут методы, не зная устройства WebView.

   ЧТО УМЕЕТ МОСТ СЕГОДНЯ. Открыть страницу сети в приложении (openStore),
   вернуться на наш сайт (openApp) и сказать, что он есть и какой версии (info).
   Действия с ответом — «сохранить вход», акции, запись корзины — пока делает
   панель самого приложения на странице сети; когда они переедут сюда, вызовы
   получат ответ, и здесь появится приёмник результата.

   КОГДА МОСТ ВСТАЁТ. JavascriptInterface доступен к моменту исполнения defer-
   скриптов, но читать его мы всё равно лениво, в момент вызова: половина бед с
   мостами — это обращение к нему раньше вставки. */
window.KorzinaDevice = (function () {
  function bridge() {
    // Именно при вызове, а не при загрузке скрипта — см. комментарий выше.
    return (typeof window.Korzina !== 'undefined') ? window.Korzina : null;
  }

  function available() {
    return bridge() !== null;
  }

  /* Кто на связи: {version, server} или null вне приложения. Пригодится, когда
     сайт начнёт просить у телефона больше, чем умеет старая сборка. */
  function info() {
    var k = bridge();
    if (!k || typeof k.info !== 'function') { return null; }
    try { return JSON.parse(k.info()); } catch (e) { return null; }
  }

  /* Открыть сайт сети в приложении. Правда, если вызов ушёл в мост; ложь, если
     моста нет или сеть ему незнакома — тогда экран остаётся и решает сам. */
  function openStore(code) {
    var k = bridge();
    if (!k || typeof k.openStore !== 'function') { return false; }
    try { return !!k.openStore(String(code || '')); } catch (e) { return false; }
  }

  /* Вернуться на наш сайт. Путь всегда свой, начинается с «/» — мост чужие
     адреса не открывает, это проверяется и на его стороне. */
  function openApp(path) {
    var k = bridge();
    if (!k || typeof k.openApp !== 'function') { return false; }
    try { k.openApp(typeof path === 'string' && path.charAt(0) === '/' ? path : '/'); }
    catch (e) { return false; }
    return true;
  }

  return { available: available, info: info, openStore: openStore, openApp: openApp };
})();
