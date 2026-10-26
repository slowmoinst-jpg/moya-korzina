package ru.moyakorzina.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.drawable.ColorDrawable;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.view.Gravity;
import android.view.InputDevice;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsetsController;
import android.webkit.ConsoleMessage;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;
import org.json.JSONTokener;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Моя корзина — Android-приложение: оболочка сайта и руки сервера на телефоне.
 *
 * ШАГ 1 ЕДИНОЙ АРХИТЕКТУРЫ (документ от 16.10.2026). До него приложение было
 * своим миром: ряд кнопок сетей вверху, нижняя панель действий, а сайт — одной
 * из вкладок. Теперь интерфейс один — веб-экраны сервера во весь экран, — а
 * приложение добавляет к ним ровно две вещи:
 *
 *   МОСТ window.Korzina. Сайт узнаёт, что открыт в телефоне (по метке в
 *   User-Agent и по самому мосту), и зовёт его: открыть страницу сети
 *   (openStore), вернуться на наш сайт (openApp), спросить версию (info).
 *   Кнопка «Войти» на «Кабинетах» работает через него.
 *
 *   ПАНЕЛЬ НА СТРАНИЦЕ СЕТИ. Когда в WebView открыт магазин, под страницей
 *   стоит маленькая панель: «Вход», «Акции», «Запись», «Собрать» — то, что
 *   серверу с этой страницы не сделать. На наших экранах панели нет: там
 *   командует сайт. Панель — переходная форма: на шаге 2 её заменит лист
 *   входа, который замечает вход сам, без кнопки.
 *
 *   «СОБРАТЬ» (1.4). Повторить корзину в этой сети руками телефона: сервер даёт
 *   наряд (POST /api/device/cartplan — позиции на карточках сети и правила кнопок
 *   серверной передачи), приложение открывает карточки и касается кнопки «В
 *   корзину» столько раз, сколько упаковок, затем открывает корзину сети, читает
 *   её итог (строки «Итого», «Скидка», «Доставка» и числа из ответов сайта) и
 *   отчитывается (POST /api/device/cartreport). Касания настоящие — события
 *   экрана, а не щелчки скрипта. Темп — из наряда, не чаще правил сервера.
 *
 *   САМА, В ФОНЕ (1.13). Пока человек смотрит корзину на нашем экране, приложение
 *   собирает её во всех сетях, где он вошёл: скрытое окно на сеть лежит под окном
 *   человека, сети идут параллельно, внутри сети — один поток. Страница корзины
 *   отдаёт корзину мостом (collect), и сборка идёт, только если корзина поменялась
 *   с прошлой сборки в этой сети. Поверх лежащего кладётся только недостающее: что
 *   телефон уже положил в сеть, он помнит. Ход и итог каждой сети уходят на сервер
 *   (cartprogress, cartreport), и наш экран показывает их строками сетей.
 *
 * ЧТО ОСТАЛОСЬ ПРЕЖНИМ. Вход в магазин делает человек — номер, код из СМС,
 * капча, родная клавиатура. Куки входа едут на сервер телом POST /api/handoff.
 * Пароли и коды не перехватываются, значения кук не показываются, кнопку
 * «заказать» приложение не нажимает никогда.
 *
 * АДРЕС СЕРВЕРА задаётся при сборке (-PkorzinaServer=https://… или переменная
 * KORZINA_SERVER), в коде его нет.
 */
public class MainActivity extends Activity {

    /**
     * Название, код сети на сервере, стартовая страница, свои адреса сети. Последнее —
     * для записи корзины: запросы к чужим адресам (счётчики, реклама) не записываются.
     */
    private static final String[][] CHAINS = {
        {"Магнит",      "magnit",      "https://magnit.ru/",   "magnit.ru"},
        {"Пятёрочка",   "pyaterochka", "https://5ka.ru/",      "5ka.ru,x5.ru"},
        {"Самокат",     "samokat",     "https://samokat.ru/",  "samokat.ru"},
        {"Лента",       "lenta",       "https://lenta.com/",   "lenta.com"},
        {"ВкусВилл",    "vkusvill",    "https://vkusvill.ru/", "vkusvill.ru"},
        {"Дикси",       "dixy",        "https://dixy.ru/",     "dixy.ru"},
        {"METRO",       "metro",       "https://online.metro-cc.ru/", "metro-cc.ru"},
        {"Перекрёсток", "perekrestok", "https://www.perekrestok.ru/", "perekrestok.ru"},
        // Fix Price добавлен 20.10.2026: сеть есть в единой базе и обходится сервером
        // (6 624 строки с ценой), но в приложении её не было — собрать её корзину в фоне
        // человек не мог. Домен именно fix-price.com (как у сборщика, замер 20.10.2026),
        // адрес товара — /catalog/<раздел>/p-<артикул>-<название>.
        {"FixPrice",    "fixprice",    "https://fix-price.com/", "fix-price.com"},
        {"Vprok",       "vprok",       "https://www.vprok.ru/",  "vprok.ru"},
        {"Монетка",     "monetka",     "https://monetka.ru/",    "monetka.ru"},
    };

    /**
     * Запись корзины: как сайт магазина кладёт товар в корзину и убирает его.
     *
     * ЗАЧЕМ. Корзину во все кабинеты, где человек вошёл, кладём запросами самих
     * магазинов (решение владельца 01.10.2026), а эти запросы под входом мы не видели
     * ни у одной сети. Снять их можно только здесь, на телефоне: сервер Пятёрочка и
     * Самокат не пускают даже через домашний выход (403, 15.10.2026).
     *
     * КАК. Скрипт оборачивает fetch и XMLHttpRequest страницы и записывает запросы К
     * СВОИМ адресам сети: все, кроме GET, и GET с корзиной в пути. Пишется СТРУКТУРА:
     * имена заголовков без значений, тело и ответ без телефонов, карт, адресов, имён.
     * Запросы входа (auth, login, otp, sms…) не пишутся вовсе. Сервер разбирает
     * запись ещё раз тем же правилом — app/cartrecord.py.
     *
     * Записанное копится в sessionStorage страницы и переживает её перезагрузку.
     */
    private static final String REC_JS =
        "(function(allow){"
      + "if(window.__kzRec){window.__kzRecStop=false;return 'on';}"
      + "window.__kzRec=true;window.__kzRecStop=false;"
      + "var SKIP=/auth|login|logout|otp|sms|verify|token|session|passw|register|sign|captcha|metric|analytic|track|collect|pixel|beacon|event/i;"
      + "var CARTISH=/cart|basket|korzin|order|checkout/i;"
      + "var HIDDEN={};('token auth session secret password pass passwd phone tel telephone msisdn email mail card pan address street house flat apartment entrance floor lat latitude lon lng longitude geo coords coordinates fio name firstname lastname surname user customer client device signature sign hash cookie birth birthday inn snils passport comment sms otp captcha ip').split(' ').forEach(function(w){HIDDEN[w]=1;});"
      + "var IDW={};('id sku article product plu good goods item code xml').split(' ').forEach(function(w){IDW[w]=1;});"
      + "function words(k){if(!k)return[];return String(k).replace(/([a-z0-9])([A-Z])/g,'$1 $2').toLowerCase().split(/[^a-z\\u0430-\\u044f\\u04510-9]+/).filter(Boolean);}"
      + "function any(ws,set){for(var i=0;i<ws.length;i++)if(set[ws[i]])return true;return false;}"
      + "function shape(v,k,d){"
      + " if(d>6)return '…';"
      + " if(v===null||typeof v==='boolean')return v;"
      + " var ws=words(k);"
      + " if(any(ws,HIDDEN))return '<скрыто>';"
      + " if(typeof v==='number')return(Math.abs(v)>=1e10&&!any(ws,IDW))?'<число>':v;"
      + " if(typeof v==='string'){"
      + "  if(/^\\+?\\d{11,}$/.test(v)&&!any(ws,IDW))return '<строка '+v.length+'>';"
      + "  return(v.length<=24&&/^[A-Za-z0-9_\\-.:]*$/.test(v))?v:'<строка '+v.length+'>';}"
      + " if(Array.isArray(v))return{'<список>':v.length,'пример':v.slice(0,2).map(function(x){return shape(x,k,d+1);})};"
      + " if(typeof v==='object'){var o={},n=0;for(var kk in v){if(!Object.prototype.hasOwnProperty.call(v,kk))continue;"
      + "  if(n++>=40){o['…']=Object.keys(v).length;break;}o[kk]=shape(v[kk],kk,d+1);}return o;}"
      + " return '<'+typeof v+'>';}"
      + "function own(u){try{var h=new URL(u,location.href).hostname;"
      + " return allow.some(function(d){return h===d||h.slice(-(d.length+1))==='.'+d;});}catch(e){return false;}}"
      + "function keep(m,u){if(!own(u))return false;var p='';try{p=new URL(u,location.href).pathname;}catch(e){return false;}"
      + " if(SKIP.test(p))return false;return m!=='GET'||CARTISH.test(p);}"
      + "function tpl(p){return String(p||'').split('/').map(function(s){"
      + " return(/^\\d{3,}$/.test(s)||/^[0-9a-fA-F-]{16,}$/.test(s)||/^[A-Za-z0-9_\\-]{20,}$/.test(s))?'{id}':s;}).join('/');}"
      + "function parse(t){if(t==null)return null;if(typeof t!=='string')return '<'+typeof t+'>';"
      + " try{return shape(JSON.parse(t),null,0);}catch(e){}"
      + " if(t.indexOf('=')>0&&t.indexOf(' ')<0){var o={};t.split('&').forEach(function(p){var i=p.indexOf('=');if(i<0)return;"
      + "  try{o[decodeURIComponent(p.slice(0,i))]=decodeURIComponent(p.slice(i+1).replace(/\\+/g,' '));}catch(e){}});return shape(o,null,0);}"
      + " return '<текст '+t.length+'>';}"
      + "function names(h){var out=[];try{if(!h)return out;"
      + " if(typeof Headers!=='undefined'&&h instanceof Headers)h.forEach(function(v,k){out.push(k);});"
      + " else if(Array.isArray(h))h.forEach(function(p){out.push(String(p[0]));});"
      + " else Object.keys(h).forEach(function(k){out.push(k);});}catch(e){}return out;}"
      + "function body(b){if(b==null)return null;if(typeof b==='string')return parse(b);"
      + " try{if(b instanceof URLSearchParams)return parse(b.toString());"
      + "  if(b instanceof FormData){var o={};b.forEach(function(v,k){o[k]=(typeof v==='string')?v:'<файл>';});return shape(o,null,0);}}catch(e){}"
      + " return '<тело>';}"
      + "function query(u){var q={};try{new URL(u,location.href).searchParams.forEach(function(v,k){q[k]=v;});}catch(e){}return shape(q,null,0);}"
      + "function save(r){if(window.__kzRecStop)return;try{var a=JSON.parse(sessionStorage.getItem('__kzRec')||'[]');"
      + " if(a.length<60){a.push(r);sessionStorage.setItem('__kzRec',JSON.stringify(a));}}catch(e){}}"
      + "function rec(via,m,u,h,b){var x=new URL(u,location.href);"
      + " return{via:via,method:m,host:x.hostname,path:tpl(x.pathname),query:query(u),headers:names(h),body:body(b),page:tpl(location.pathname)};}"
      + "var of=window.fetch;"
      + "if(of)window.fetch=function(input,init){"
      + " var u=(typeof input==='string')?input:((input&&input.url)||String(input||''));"
      + " var m=String((init&&init.method)||(input&&input.method)||'GET').toUpperCase();"
      + " var p=of.apply(this,arguments);"
      + " try{if(keep(m,u)){var r=rec('fetch',m,u,(init&&init.headers)||(input&&input.headers),init&&init.body);"
      + "  p.then(function(resp){r.status=resp.status;resp.clone().text().then(function(t){r.response=parse(t);save(r);},function(){save(r);});},"
      + "  function(){r.status=0;save(r);});}}catch(e){}"
      + " return p;};"
      + "var X=XMLHttpRequest.prototype,oOpen=X.open,oSend=X.send,oHead=X.setRequestHeader;"
      + "X.open=function(m,u){try{this.__kz={m:String(m||'GET').toUpperCase(),u:String(u||''),h:[]};}catch(e){}return oOpen.apply(this,arguments);};"
      + "X.setRequestHeader=function(k){try{if(this.__kz)this.__kz.h.push(String(k));}catch(e){}return oHead.apply(this,arguments);};"
      + "X.send=function(b){try{var x=this,k=x.__kz;if(k&&keep(k.m,k.u)){var r=rec('xhr',k.m,k.u,null,b);r.headers=k.h;"
      + " x.addEventListener('loadend',function(){r.status=x.status;try{"
      + "  r.response=(x.responseType===''||x.responseType==='text')?parse(String(x.responseText||'')):"
      + "  (x.responseType==='json'?shape(x.response,null,0):'<'+x.responseType+'>');}catch(e){}save(r);});}}catch(e){}"
      + " return oSend.apply(this,arguments);};"
      + "return 'on';"
      + "})(%s)";

    /** Начать запись: старое записанное на этой странице — в сторону. */
    private static final String REC_START_JS =
        "(function(){window.__kzRecStop=false;try{sessionStorage.removeItem('__kzRec');}catch(e){}return 1;})()";

    /** Закончить запись и забрать записанное. */
    private static final String REC_TAKE_JS =
        "(function(){window.__kzRecStop=true;var a='[]';"
      + "try{a=sessionStorage.getItem('__kzRec')||'[]';sessionStorage.removeItem('__kzRec');}catch(e){}return a;})()";

    private static final String SERVER = BuildConfig.KORZINA_SERVER.replaceAll("/+$", "");

    /**
     * Окно кабинета ФНС (Z5b плана 21.10.2026).
     *
     * Вход в кабинет закрыт капчей для программ, поэтому его проходит человек — здесь
     * же, в этом WebView. Приложение только забирает ключ его сессии: страница кабинета
     * исполняет /static/fns-grab.js, тот берёт ключ из хранилища и возвращает окно на
     * /phone/receipts?keys=…, где ключ принимает сервер (app/fns/sync.py).
     */
    private static final String FNS_URL = "https://lkdr.nalog.ru/";
    private static final String FNS_GRAB = "/static/fns-grab.js";
    private static final String FNS_LOG = "korzina:";
    private static final String FNS_ERR = "korzina!";

    /**
     * Сборщик карточек акций со страницы магазина. Разметка у сетей своя и меняется,
     * поэтому он не привязан к классам конкретного сайта: ищет небольшие блоки, где
     * есть скидка («-30%», «69,90 ₽»), и берёт из них название и срок («до 30.09»).
     * Лишнее отсеет сервер: купон без понятных цифр показывается, но в цену не идёт,
     * а к товару купон прикладывается только при очень похожем названии.
     */
    private static final String OFFERS_JS =
        "(function(){"
      // скидка — отдельной строкой: «-30%», «−15 ₽», «69,90 ₽». «2,5%» внутри названия — не скидка
      + "var val=/^[-\u2212\u2013]?\\s?(\\d{1,2}(?:[.,]\\d+)?\\s?%|\\d[\\d\\s\\u00a0]*(?:[.,]\\d{1,2})?\\s?(?:\u20bd|\u0440\u0443\u0431\\.?))$/i;"
      + "var sel='article,li,[class*=card],[class*=Card],[class*=offer],[class*=Offer],"
      + "[class*=coupon],[class*=Coupon],[class*=promo],[class*=Promo],[class*=discount],[class*=Discount]';"
      // куски текста — по самым вложенным элементам: «-30%» и «до 30.09» в соседних
      // <span> иначе слиплись бы в одну строку «-30%до 30.09»
      + "function lines(n){var out=[];[].slice.call(n.querySelectorAll('*')).concat([n]).forEach(function(e){"
      + " if(e.children.length)return; var t=(e.textContent||'').replace(/\\s+/g,' ').trim(); if(t)out.push(t);});"
      + " return out.length?out:(n.innerText||'').split('\\n').map(function(s){return s.trim();}).filter(Boolean);}"
      + "function hit(n){var t=(n.innerText||'').trim();"
      + " return t.length>0&&t.length<=300&&lines(n).some(function(l){return val.test(l);});}"
      + "var all=[].slice.call(document.querySelectorAll(sel)).filter(hit);"
      // самые внутренние: карточка, внутри которой нет другой карточки со скидкой
      + "var cards=all.filter(function(n){return !all.some(function(m){return m!==n&&n.contains(m);});});"
      + "var out=[],seen={},y=new Date().getFullYear();"
      + "cards.slice(0,200).forEach(function(n){"
      + " var ls=lines(n),value=ls.filter(function(l){return val.test(l);})[0];"
      + " var names=ls.filter(function(l){return !val.test(l)&&l.length>3&&l.length<=120&&!/^\u0434\u043e\\s/i.test(l);});"
      + " if(!names.length)return;"
      + " var title=names.reduce(function(a,b){return b.length>a.length?b:a;});"
      + " var e=(n.innerText||'').match(/\u0434\u043e\\s+(\\d{1,2})[.](\\d{1,2})(?:[.](\\d{2,4}))?/i),ends=null;"
      + " if(e){var yy=e[3]?(e[3].length==2?'20'+e[3]:e[3]):y;"
      + "  ends=yy+'-'+('0'+e[2]).slice(-2)+'-'+('0'+e[1]).slice(-2);}"
      + " var key=title+'|'+value; if(seen[key])return; seen[key]=1;"
      + " out.push({title:title,product:title,value:value.replace(/\\s+/g,' '),ends_at:ends});"
      + "});"
      + "return JSON.stringify(out);"
      + "})()";

    // ── Сборка корзины кнопками сайта (1.4) ──

    /**
     * Сторож запросов к корзине на карточке. Пишет «МЕТОД путь код» запросов к СВОИМ
     * адресам сети с корзиной в пути — по ним видно, приняла ли сеть нажатие. Из
     * ответов-JSON берёт только числа с полями вроде total, discount, delivery и
     * кладёт в sessionStorage: ответ на «положить» у многих сетей и есть пересчитанная
     * корзина. Поля со словами телефона, адреса, карты, имени не берутся вовсе.
     */
    private static final String WATCH_JS =
        "(function(hosts,full){"
      + "window.__kzFull=full||[];"
      + "if(window.__kzCartWatch)return 'on';window.__kzCartWatch=true;window.__kzCalls=[];"
      + "var KEYS=/total|sum|amount|cost|price|discount|delivery|bonus|point|saving|benefit|econom|promo|fee|pay|count|quantity|minimal/i;"
      + "var HID={};('token auth session secret password pass passwd phone tel telephone msisdn email mail card pan address street house flat apartment entrance floor lat latitude lon lng longitude geo coords coordinates fio name firstname lastname surname user customer client device signature sign hash cookie birth birthday inn snils passport comment sms otp captcha ip').split(' ').forEach(function(w){HID[w]=1;});"
      + "function hidden(k){var ws=String(k).replace(/([a-z0-9])([A-Z])/g,'$1 $2').toLowerCase().split(/[^a-z0-9\\u0430-\\u044f\\u0451]+/);"
      + " for(var i=0;i<ws.length;i++)if(ws[i]&&HID[ws[i]])return true;return false;}"
      + "function own(u){try{var h=new URL(u,location.href).hostname;"
      + " return hosts.some(function(d){return h===d||h.slice(-(d.length+1))==='.'+d;});}catch(e){return false;}}"
      + "var CARTISH=/cart|basket|korzin|order|checkout/i;"
      + "function tpl(p){return String(p||'').split('/').map(function(s){"
      + " return(/^\\d{3,}$/.test(s)||/^[0-9a-fA-F-]{16,}$/.test(s)||/^[A-Za-z0-9_\\-]{20,}$/.test(s))?'{id}':s;}).join('/');}"
      + "function nums(o,pre,d,out){if(d>5||!o||typeof o!=='object'||Array.isArray(o))return;"
      + " for(var k in o){if(!Object.prototype.hasOwnProperty.call(o,k)||hidden(k))continue;"
      + "  var v=o[k],p=pre?pre+'.'+k:k;"
      + "  if(typeof v==='number'&&isFinite(v)){if(KEYS.test(k)&&Object.keys(out).length<40)out[p]=v;}"
      + "  else if(typeof v==='string'&&/^-?\\d+(?:[.,]\\d+)?$/.test(v)&&KEYS.test(k)){if(Object.keys(out).length<40)out[p]=parseFloat(v.replace(',','.'));}"
      + "  else if(v&&typeof v==='object'&&!Array.isArray(v))nums(v,p,d+1,out);}}"
      + "function keep(path,t){try{var out={};nums(JSON.parse(t),'',0,out);if(!Object.keys(out).length)return;"
      + " var all=JSON.parse(sessionStorage.getItem('__kzNums')||'{}');delete all[path];all[path]=out;"
      + " var ks=Object.keys(all);if(ks.length>6)delete all[ks[0]];"
      + " sessionStorage.setItem('__kzNums',JSON.stringify(all));}catch(e){}}"
      // Ответ с корзиной ЦЕЛИКОМ (пути full из наряда, замер Магнита 18.10.2026): у каждой
      // позиции артикул и количество — window.__kzCart. Так телефон узнаёт, что лежит в
      // корзине сети на самом деле. Ни имён, ни лишних чисел: из чисел берём только
      // количество и ЦЕНУ ЗА УПАКОВКУ — цель требует получать цены корзиной в фоне, а у
      // Перекрёстка, Vprok и Монетки обход отдаёт только названия.
      + "function whole(path,t){try{var o=JSON.parse(t),items={},prices={},n=0;"
      + " (function walk(x,d){if(!x||typeof x!=='object'||d>8)return;"
      + "  if(Array.isArray(x)){for(var i=0;i<x.length;i++)walk(x[i],d+1);return;}"
      + "  var id=null,q=null,pr=null,total=null;for(var k in x){if(!Object.prototype.hasOwnProperty.call(x,k))continue;var v=x[k];"
      + "   if(id===null&&(typeof v==='string'||typeof v==='number')&&/^(goods?_?id|product_?id|sku|article|offer_?id|item_?id|plu)$/i.test(k))id=String(v);"
      + "   else if(q===null&&typeof v==='number'&&/^(qnty|quantity|qty|count|amount)$/i.test(k))q=v;"
      // Цена за упаковку. «price»/«pricePerUnit» — она и есть; «totalPrice»/«sum» — цена
      // строки целиком, из неё цену упаковки получим делением на количество.
      + "   else if(pr===null&&typeof v==='number'&&/^(price|price_?per_?unit|unit_?price|cost)$/i.test(k))pr=v;"
      + "   else if(total===null&&typeof v==='number'&&/^(total_?price|sum|amount_?price|total_?cost)$/i.test(k))total=v;}"
      + "  if(id!==null&&q!==null){items[id]=Math.max(items[id]||0,q);"
      + "   var one=null;if(pr!==null&&pr>0)one=pr;else if(total!==null&&total>0&&q>0)one=total/q;"
      + "   if(one!==null&&one>0&&one<1000000)prices[id]=Math.round(one*100)/100;n++;}"
      + "  for(var kk in x){if(Object.prototype.hasOwnProperty.call(x,kk)&&x[kk]&&typeof x[kk]==='object')walk(x[kk],d+1);}"
      + " })(o,0);if(n>0)window.__kzCart={at:Date.now(),path:path,items:items,prices:prices,n:n};}catch(e){}}"
      + "function note(m,u,code,t){try{if(!own(u))return;var path=tpl(new URL(u,location.href).pathname);"
      + " if(!CARTISH.test(path))return;if(window.__kzCalls.length<40)window.__kzCalls.push(m+' '+path+' '+code);"
      + " if(t){keep(path,t);if(code>=200&&code<300&&(window.__kzFull||[]).indexOf(path)>=0)whole(path,t);}}catch(e){}}"
      // Последний удачный запрос ЗАПИСИ в корзину — целиком, но только в памяти страницы
      // (window.__kzLast): по нему сборка повторит запрос сети для остальных позиций.
      // Наружу он не уходит никогда, на сервер едут лишь «метод путь код».
      + "function last(m,u,h,b,code,via){try{if(m==='GET'||!own(u)||code<200||code>=300||typeof b!=='string')return;"
      + " if(!CARTISH.test(new URL(u,location.href).pathname))return;window.__kzLast={m:m,u:u,h:h||{},b:b,via:via,at:Date.now()};}catch(e){}}"
      + "function heads(hh){var h={};try{if(!hh)return h;if(typeof Headers!=='undefined'&&hh instanceof Headers)hh.forEach(function(v,k){h[k]=v;});"
      + " else if(Array.isArray(hh))hh.forEach(function(p){h[p[0]]=p[1];});else Object.keys(hh).forEach(function(k){h[k]=hh[k];});}catch(e){}return h;}"
      + "var of=window.fetch;"
      + "if(of)window.fetch=function(input,init){"
      + " var u=(typeof input==='string')?input:((input&&input.url)||String(input||''));"
      + " var m=String((init&&init.method)||(input&&input.method)||'GET').toUpperCase();"
      + " var h=heads((init&&init.headers)||(input&&input.headers)),b=init?init.body:undefined;"
      + " var p=of.apply(this,arguments);"
      + " try{p.then(function(r){last(m,u,h,b,r.status,'fetch');try{r.clone().text().then(function(t){note(m,u,r.status,t);},function(){note(m,u,r.status,'');});}catch(e){}},"
      + "  function(){note(m,u,0,'');});}catch(e){}"
      + " return p;};"
      + "var X=XMLHttpRequest.prototype,oOpen=X.open,oSend=X.send,oHead=X.setRequestHeader;"
      + "X.open=function(m,u){try{this.__kzc={m:String(m||'GET').toUpperCase(),u:String(u||''),h:{}};}catch(e){}return oOpen.apply(this,arguments);};"
      + "X.setRequestHeader=function(k,v){try{if(this.__kzc)this.__kzc.h[k]=v;}catch(e){}return oHead.apply(this,arguments);};"
      + "X.send=function(b){try{var x=this,k=x.__kzc;if(k)x.addEventListener('loadend',function(){var t='';"
      + " try{t=(x.responseType===''||x.responseType==='text')?String(x.responseText||''):(x.responseType==='json'?JSON.stringify(x.response):'');}catch(e){}"
      + " last(k.m,k.u,k.h,b,x.status,'xhr');note(k.m,k.u,x.status,t);});}catch(e){}return oSend.apply(this,arguments);};"
      + "return 'on';"
      + "})(%s)";

    /**
     * Выучить запрос сети «положить в корзину» по тому, что сайт отправил после
     * касания (window.__kzLast): найти в теле ВСЕ поля с артикулом этой позиции и рядом
     * с первым — поле количества. Ответ — только имена полей и путь, без значений заголовков.
     *
     * ВСЕ, А НЕ ПЕРВОЕ. Замер на эмуляторе 18.10.2026: Магнит шлёт
     * {"items":[{"goodId":"…","offerId":"…","qnty":1}]} — артикул в двух полях. 1.10
     * подменяла только goodId, и позиции ложились «гибридами» с offerId первого товара:
     * сайт показывал их, но его «−» и крестик слали чужой offerId и строку не трогали.
     *
     * ПО «+» — ТОЛЬКО ОДНУ ПОЗИЦИЮ (one). «+» на карточке Магнита шлёт ВСЮ корзину с
     * итоговыми числами (замер 18.10.2026), а запрос сети ставит число каждой названной
     * позиции. Повтор такого образца переставил бы числа всем прочим позициям по снимку,
     * поэтому из списка остаётся только своя (trim). Делается это лишь там, где замерено,
     * что неназванные позиции запрос не трогает (qty_sets): иначе обрезка списка могла бы
     * убрать товары из корзины сети.
     */
    private static final String LEARN_JS =
        "(function(sku,one){try{var r=window.__kzLast;if(!r)return JSON.stringify({ok:false,why:'запроса корзины после касания не видно'});"
      + "var body;try{body=JSON.parse(r.b);}catch(e){return JSON.stringify({ok:false,why:'тело запроса не JSON'});}"
      + "var idPaths=[],qtyPath=null;"
      + "function walk(o,path){if(o===null||typeof o!=='object')return;"
      + " for(var k in o){if(!Object.prototype.hasOwnProperty.call(o,k))continue;var v=o[k],p=path.concat([k]);"
      + "  if((typeof v==='string'||typeof v==='number')&&String(v)===String(sku)){"
      + "   if(!idPaths.length)for(var q in o){if(/^(quantity|qty|qnty|count|amount|quantityinbasket|cnt)$/i.test(q)&&typeof o[q]==='number'){qtyPath=path.concat([q]);break;}}"
      + "   idPaths.push(p);continue;}"
      + "  if(v&&typeof v==='object')walk(v,p);}}"
      + "walk(body,[]);if(!idPaths.length)return JSON.stringify({ok:false,why:'в запросе нет артикула '+sku});"
      + "var trim=null;if(one){var o=body,p0=idPaths[0];for(var i=0;i<p0.length-1;i++){if(Array.isArray(o)&&o.length>1)trim={arr:p0.slice(0,i),idx:Number(p0[i])};o=o[p0[i]];}}"
      + "window.__kzTpl={m:r.m,u:r.u,h:r.h,body:body,idPaths:idPaths,qtyPath:qtyPath,trim:trim};window.__kzRep={};"
      + "return JSON.stringify({ok:true,method:r.m,path:new URL(r.u,location.href).pathname,trim:!!trim,"
      + " id:idPaths.map(function(p){return p.join('.');}).join(', '),qty:qtyPath?qtyPath.join('.'):null});"
      + "}catch(e){return JSON.stringify({ok:false,why:String(e).slice(0,100)});}})(%s)";

    /**
     * Повторить выученный запрос для другой позиции: начать и вернуть сразу, ответ — в
     * __kzRep. Артикул встаёт во все поля образца, каждое своего типа (строка или число).
     *
     * В ОТВЕТЕ ИЩЕТСЯ «НЕТ В НАЛИЧИИ». Артикул под ключом вроде unavailable,
     * outOfStock, soldOut в ответе сети значит gone: запрос сеть приняла, а товара у
     * неё нет. Так отвечает checkout/preview Магнита (unavailableItems, замер на
     * эмуляторе 18.10.2026). Его carts/lite, которым кладёт карточка, наличие не
     * выделяет — такие позиции ловит сверка со страницей корзины (CART_GONE_JS).
     */
    private static final String REPLAY_JS =
        "(function(p){try{var t=window.__kzTpl;if(!t)return 'нет образца';"
      + "var b=JSON.parse(JSON.stringify(t.body));"
      + "function get(o,path){for(var i=0;i<path.length;i++){if(o==null)return undefined;o=o[path[i]];}return o;}"
      + "function set(o,path,v){for(var i=0;i<path.length-1;i++)o=o[path[i]];o[path[path.length-1]]=v;}"
      + "(t.idPaths||[]).forEach(function(path){set(b,path,typeof get(t.body,path)==='number'?Number(p.sku):String(p.sku));});"
      + "if(t.qtyPath)set(b,t.qtyPath,p.qty);"
      + "if(t.trim){var arr=t.trim.arr.length?get(b,t.trim.arr):b;if(Array.isArray(arr)){var one=[arr[t.trim.idx]];if(t.trim.arr.length)set(b,t.trim.arr,one);else b=one;}}"
      + "var OUT=/unavailable|out_?of_?stock|not_?available|sold_?out/i;"
      + "function gone(o,under,d){if(!o||typeof o!=='object'||d>8)return false;"
      + " if(Array.isArray(o)){for(var i=0;i<o.length;i++)if(gone(o[i],under,d+1))return true;return false;}"
      + " for(var k in o){if(!Object.prototype.hasOwnProperty.call(o,k))continue;var v=o[k];"
      + "  if(under&&(typeof v==='string'||typeof v==='number')&&String(v)===String(p.sku))return true;"
      + "  if(v&&typeof v==='object'&&gone(v,under||OUT.test(k),d+1))return true;}"
      + " return false;}"
      + "window.__kzRep=window.__kzRep||{};window.__kzRep[p.key]=null;"
      + "fetch(t.u,{method:t.m,headers:t.h,body:JSON.stringify(b),credentials:'include'})"
      + " .then(function(r){var res={ok:r.status>=200&&r.status<300,status:r.status,"
      + "  call:t.m+' '+new URL(t.u,location.href).pathname+' '+r.status,gone:false};"
      + "  return r.text().then(function(x){try{res.gone=gone(JSON.parse(x),false,0);}catch(e){}window.__kzRep[p.key]=res;},"
      + "   function(){window.__kzRep[p.key]=res;});})"
      + " .catch(function(e){window.__kzRep[p.key]={ok:false,status:0,why:String(e).slice(0,80)};});"
      + "return 'started';}catch(e){return 'ошибка: '+String(e).slice(0,80);}})(%s)";

    /** Чем кончился повтор: объект ответа или null, пока ответа нет. */
    private static final String REPLAY_POLL_JS =
        "(function(k){var r=(window.__kzRep||{})[k];return r?JSON.stringify(r):'';})(%s)";

    /**
     * Кнопка «В корзину» ИМЕННО ЭТОГО товара — правило серверной передачи
     * (app/shopbrowser/cart.py, _PICK_JS): первая видимая ниже заголовка и не дальше
     * блока покупки, по признакам сети или по доступному имени из наряда. «Нет в
     * наличии» ищется только в блоке покупки. Найденная кнопка помечается и
     * прокручивается к середине экрана; касание делает приложение, не скрипт.
     *
     * ЗАБОР ПОД БЛОКОМ ПОКУПКИ. Замер владельца 17.10.2026 на Магните: сметаны 15%
     * в его магазине нет, и под карточкой сразу шли «Похожие» — их «В корзину»
     * попадала в окно 700 точек, и в корзину легла сметана 25%. Поэтому всё, что ниже
     * первого заголовка «Похожие», «Рекомендуем», «С этим покупают»…, — чужие товары,
     * и ни кнопки, ни «нет в наличии» оттуда не берутся.
     *
     * ВТОРОЙ ПРОХОД — НАДПИСЬ С ЦЕНОЙ. У Дикси кнопка товара подписана ценой и словами
     * вместе («94.99 ₽ В корзину»), строгое имя её не узнаёт. Второй проход ищет «в
     * корзину» внутри надписи, кроме «Перейти в корзину», «В корзине» и «Оформить».
     * Не нашлось — в ответе near: какие кнопки были в блоке покупки, для отчёта.
     *
     * ПЛИТКИ ЧУЖИХ ТОВАРОВ не в счёт и без забора (foreign): кнопка в плитке со ссылкой
     * на другую карточку — не наша, и её «нет в наличии» — тоже не наше.
     */
    private static final String PICK_JS =
        "(function(p){try{"
      + "var MARK='data-korzina-add',FENCE=/похож|рекоменд|с этим|покупают|понравит|аналог|вместе|смотрели|популярн|ещё товары|еще товары/i;"
      + "[].slice.call(document.querySelectorAll('['+MARK+']')).forEach(function(e){e.removeAttribute(MARK);});"
      + "var h1=document.querySelector('h1');"
      + "var top=h1?h1.getBoundingClientRect().top+window.scrollY:null;"
      + "var limit=(top!==null&&p.below)?top+p.below:Infinity;"
      + "if(top!==null)[].slice.call(document.querySelectorAll('h2,h3,h4,[role=heading]')).forEach(function(h){"
      + " var t=(h.innerText||'').replace(/\\s+/g,' ').trim();if(!t||t.length>40||!FENCE.test(t))return;"
      + " var y=h.getBoundingClientRect().top+window.scrollY;if(y>top&&y<limit)limit=y;});"
      + "function label(e){return((e.getAttribute('aria-label')||'')+' '+(e.getAttribute('title')||'')+' '+(e.innerText||'')).replace(/\\s+/g,' ').trim();}"
      // Закреплённая полоса покупки (position fixed/sticky) — своя кнопка товара: у
      // Магнита на телефоне «94,99 ₽ [В корзину]» стоит внизу экрана поверх страницы
      // (замер владельца 18.10.2026). Каруселей закреплёнными не бывает, поэтому
      // такой полосе забор не нужен — только чтобы она была видна на экране.
      + "function pinned(e){for(var x=e;x&&x!==document.body;x=x.parentElement){var s=getComputedStyle(x).position;"
      + " if(s==='fixed'||s==='sticky')return true;}return false;}"
      // Плитка чужого товара: в маленьком (до 300 знаков) предке кнопки есть ссылка на
      // ДРУГУЮ страницу сайта с картинкой или длинным названием, а заголовка карточки
      // в этом предке нет. Забор ловит только подписанные h2/h3 «Похожие». У Дикси
      // 18.10.2026 строка Алёнки дала ✓, а в корзине оказался второй Бабаевский —
      // похоже на кнопку плитки, которую забор не узнал.
      + "function foreign(e){var cur=location.pathname.replace(/\\/+$/,'');"
      + " for(var x=e,i=0;i<6&&x&&x!==document.body;i++,x=x.parentElement){if(h1&&x.contains(h1))return false;"
      + "  if((x.innerText||'').length>300)return false;var as=x.querySelectorAll('a[href]');"
      + "  for(var k=0;k<as.length;k++){try{var u=new URL(as[k].getAttribute('href'),location.href);"
      + "   var pth=u.pathname.replace(/\\/+$/,'');if(u.host!==location.host||!pth||pth===cur)continue;"
      + "   if(as[k].querySelector('img')||(as[k].innerText||'').replace(/\\s+/g,' ').trim().length>=20)return true;}catch(err){}}}"
      + " return false;}"
      + "function inside(e,below){var r=e.getBoundingClientRect();if(r.width<=0||r.height<=0)return false;"
      + " if(pinned(e))return r.bottom>0&&r.top<(window.innerHeight||100000);"
      + " if(foreign(e))return false;"
      + " var y=r.top+window.scrollY;if(below&&top!==null&&y<top)return false;return y<limit;}"
      + "if(p.gone&&!p.again&&h1){var g=new RegExp(p.gone,'i'),w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT),n;"
      + " while((n=w.nextNode())){var t=(n.nodeValue||'').replace(/\\s+/g,' ').trim().toLowerCase();"
      + "  if(!t||!g.test(t))continue;var el=n.parentElement;if(!el)continue;var r=el.getBoundingClientRect();"
      + "  if(r.width<=0||r.height<=0||(!pinned(el)&&foreign(el)))continue;var y=r.top+window.scrollY;"
      + "  if((y>=top-200&&y<limit)||(pinned(el)&&r.top<(window.innerHeight||100000)))return JSON.stringify({gone:true});}}"
      + "var bySel=!p.again&&p.selectors&&p.selectors.length>0,nodes=[];"
      + "if(bySel)p.selectors.forEach(function(s){try{nodes=nodes.concat([].slice.call(document.querySelectorAll(s)));}catch(e){}});"
      + "var buttons=[].slice.call(document.querySelectorAll('button,[role=button]'));"
      + "if(!nodes.length){bySel=false;nodes=buttons;}"
      + "var pat=new RegExp(p.again?p.plus:p.add,'i'),best=null,bestY=Infinity,wide=false;"
      + "function take(e){var y=e.getBoundingClientRect().top+window.scrollY;if(y<bestY){best=e;bestY=y;}}"
      + "nodes.forEach(function(e){if(e.disabled||!inside(e,!p.again))return;"
      + " if(!bySel){var a=(e.getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim(),t=(e.innerText||'').replace(/\\s+/g,' ').trim(),ti=(e.getAttribute('title')||'').replace(/\\s+/g,' ').trim();"
      + "  if(!(pat.test(a)||pat.test(t)||pat.test(ti)||pat.test((a+' '+ti+' '+t).trim())))return;}"
      + " take(e);});"
      + "if(!best&&!p.again)buttons.forEach(function(e){if(e.disabled||!inside(e,true))return;var l=label(e);"
      + " if(l.length>60||!/в корзину|добавить|купить/i.test(l)||/перейти|в корзине|оформ|избранн|сравн/i.test(l))return;wide=true;take(e);});"
      // Лежащий в корзине товар сайт рисует счётчиком «−  2 шт  +» вместо «В корзину». У
      // Магнита (замер 18.10.2026) его кнопки — одни значки, без подписи и aria-label, в
      // закреплённой полосе снизу (.pl-stepper). Счётчик узнаём по классу stepper в блоке
      // покупки или в закреплённой полосе: «+» — его последняя кнопка, число — в его тексте.
      + "function stepper(){var got=null;[].slice.call(document.querySelectorAll('[class*=stepper],[class*=Stepper]')).forEach(function(s){if(got)return;"
      + " var r=s.getBoundingClientRect();if(r.width<=0||r.height<=0||!(pinned(s)||inside(s,true)))return;"
      + " var bs=[].slice.call(s.querySelectorAll('button,[role=button]')).filter(function(e){var q=e.getBoundingClientRect();return !e.disabled&&q.width>0&&q.height>0;});"
      + " if(bs.length<2)return;var m=(s.innerText||'').replace(/\\s+/g,' ').match(/(\\d+(?:[.,]\\d+)?)/);"
      + " got={plus:bs[bs.length-1],count:m?parseFloat(m[1].replace(',','.')):1};});return got;}"
      + "if(!best&&p.again){var st=stepper();if(st)best=st.plus;}"
      // ТЕКСТ КАРТОЧКИ ДЛЯ ЦЕНЫ И НАЛИЧИЯ. Цену сеть печатает рядом с заголовком, а не в
      // нём одном (у Пятёрочки она бывает и в самом h1, у Самоката — отдельной строкой).
      // Сначала берём общий блок заголовка и найденной кнопки — это и есть карточка, без
      // «Похожих» и каруселей. Такого блока нет (кнопку не нашли) или он непомерно велик —
      // поднимаемся от заголовка на несколько предков, но не дальше 900 знаков: 600 из них
      // уходят в отчёт строки. Это чтение уже открытой карточки, запроса к сети нет. Текст
      // нужен и в отказе: «Больше нет» бывает на карточке без кнопки, и наличие — false.
      // Считаем ДО ветки отказа: им пользуются обе ветки, а var внутри if на успехе
      // остался бы undefined.
      + "var ct='';if(h1){var root=null;"
      + "if(best){var y=best;for(var i=0;i<10&&y&&y!==document.body;i++){"
      + "if(y.contains(h1)){root=y;break;}y=y.parentElement;}}"
      + "var t=((root||h1).innerText||'').replace(/\\s+/g,' ').trim(),x=h1;"
      + "if(!root||!t||t.length>900){"
      + "for(var i=0;i<4&&x&&x.parentElement;i++){x=x.parentElement;"
      + "var s=(x.innerText||'').replace(/\\s+/g,' ').trim();"
      + "if(s.length>t.length&&s.length<=900)t=s;}}ct=t.slice(0,600);}"
      // Не нашлось «В корзину», а в блоке покупки есть «+» или счётчик — товар уже лежит в
      // корзине сети (inCart, сколько — count). Класть его ещё раз «В корзину» нельзя.
      + "if(!best){var near=[],plus=new RegExp(p.plus,'i'),inCart=false,st0=!p.again?stepper():null;buttons.forEach(function(e){if(!inside(e,true))return;var l=label(e);"
      // Полоса выбора магазина («Доставка • Мигом <адрес>», «Самовывоз • Гипер <адрес>»)
      // в «рядом» не идёт: подпись уезжает в отчёт, а адреса телефон не шлёт.
      + " if(near.length<6&&l&&!/^(доставка|самовывоз)/i.test(l))near.push(l.slice(0,30));"
      + " if(!p.again&&!e.disabled&&l.length<=40&&(plus.test(l)||plus.test((e.getAttribute('aria-label')||'').trim())||plus.test((e.innerText||'').trim())))inCart=true;});"
      + " if(st0)inCart=true;"
      // Искали «+», а в блоке покупки «В корзину» — товара в корзине сети нет (addable),
      // хотя память телефона думала иначе: человек убрал его или оформил заказ.
      + " var addable=false;if(p.again){var ap=new RegExp(p.add,'i');buttons.forEach(function(e){if(addable||e.disabled||!inside(e,true))return;var l=label(e);"
      + "  if(l.length<=60&&(ap.test(l)||ap.test((e.innerText||'').trim()))&&!/перейти|в корзине|оформ/i.test(l))addable=true;});}"
      // ПУСТАЯ ОТДАЧА. Самокат, прогон 21.10.2026: прямой адрес карточки открылся, но
      // заголовок пуст, а текста на странице почти нет — это не «просит войти», а
      // страница не отдалась (пусто или бот-защита). Признак уезжает в отказ отдельно.
      + "var xx=(document.body?document.body.innerText:'').replace(/\\s+/g,' ').trim(),ti=(document.title||'').replace(/\\s+/g,' ').trim();"
      + " return JSON.stringify({found:false,h1:!!h1,title:(h1?(h1.innerText||''):'').replace(/\\s+/g,' ').trim().slice(0,200),text:ct,near:near,inCart:inCart,count:st0?st0.count:null,addable:addable,empty:(xx.length<40||ti==='')});}"
      // Закреплённую полосу не прокручиваем: она и так на экране, а прокрутка у Магнита
      // перерисовывает её, и помеченная кнопка исчезала из-под пальца (18.10.2026).
      + "var vis=[];buttons.forEach(function(e){if(vis.length>=3||e.disabled||!inside(e,true))return;var l=label(e);if(l)vis.push(l.slice(0,30));});"
      + "best.setAttribute(MARK,'1');if(!pinned(best))best.scrollIntoView({block:'center',inline:'center'});"
      + "return JSON.stringify({found:true,wide:wide,title:(h1?(h1.innerText||''):'').replace(/\\s+/g,' ').trim().slice(0,200),near:vis,text:ct});"
      + "}catch(e){return JSON.stringify({found:false,error:String(e).slice(0,120)});}})(%s)";

    /**
     * Что сайт отправил после касания: артикулы в теле последнего запроса записи в
     * корзину (window.__kzLast). Есть поля артикула, но нашего среди них нет — значит
     * легло ДРУГОЕ: так сметана 25% чуть не сошла за 15% (17.10.2026). Магнит зовёт
     * поля goodId и offerId (18.10.2026) — их сверка тоже видит.
     */
    private static final String VERIFY_JS =
        "(function(sku){try{var r=window.__kzLast;if(!r||typeof r.b!=='string')return JSON.stringify({known:false});"
      + "var body;try{body=JSON.parse(r.b);}catch(e){return JSON.stringify({known:false});}"
      + "var ids=[];(function walk(o){if(!o||typeof o!=='object')return;for(var k in o){if(!Object.prototype.hasOwnProperty.call(o,k))continue;"
      + " var v=o[k];if((typeof v==='string'||typeof v==='number')&&/^(product_?id|sku|article|goods?_?id|item_?id|offer_?id|plu)$/i.test(k))ids.push(String(v));"
      + " else if(v&&typeof v==='object')walk(v);}})(body);"
      + "return JSON.stringify({known:ids.length>0,match:ids.indexOf(String(sku))>=0,ids:ids.slice(0,3)});"
      + "}catch(e){return JSON.stringify({known:false});}})(%s)";

    /** Где на экране помеченная кнопка: середина в точках CSS и ширина видимой области. */
    private static final String COORDS_JS =
        "(function(){var e=document.querySelector('[data-korzina-add]');if(!e)return JSON.stringify({found:false});"
      + "var r=e.getBoundingClientRect();if(r.width<=0||r.height<=0)return JSON.stringify({found:false});"
      + "var vv=window.visualViewport,ox=vv?vv.offsetLeft:0,oy=vv?vv.offsetTop:0,vw=vv?vv.width:window.innerWidth;"
      + "return JSON.stringify({found:true,x:r.left+r.width/2-ox,y:r.top+r.height/2-oy,vw:vw});})()";

    /** Забрать записанные сторожем запросы к корзине и начать запись заново. */
    private static final String CALLS_JS =
        "(function(){var c=(window.__kzCalls||[]).slice(0,10);window.__kzCalls=[];return JSON.stringify({calls:c});})()";

    /**
     * Дорога к корзине сети со страницы. Первой — видимая кнопка или пункт меню
     * «Корзина»: её касается приложение, как человек. Замер владельца 17.10.2026 в 1.5:
     * у Магнита корзина открывается пунктом «Корзина» нижнего меню и живёт внутри
     * сайта без своего адреса (адрес остаётся magnit.ru/), а страницы /cart у него нет
     * (замер 20.09.2026). Без такой кнопки — своя ссылка с cart/basket/korzina в пути.
     * Кнопка «В корзину» товара сюда не попадает. И НИКОГДА — «Очистить корзину»,
     * «Удалить», «Оформить», «Оплатить»: на странице корзины они стоят рядом.
     */
    private static final String FIND_CART_JS =
        "(function(hosts){try{"
      + "function own(h){return hosts.some(function(d){return h===d||h.slice(-(d.length+1))==='.'+d;});}"
      + "var MARK='data-korzina-add',ADD=/^(в корзину|добавить в корзину|купить)$/i,H=window.innerHeight||100000;"
      + "var NEVER=/очист|удал|убра|оформ|оплат|clear|remove|delete|checkout/i;"
      + "[].slice.call(document.querySelectorAll('['+MARK+']')).forEach(function(e){e.removeAttribute(MARK);});"
      // Пункт меню тоже дорога: у Магнита с доставкой «Мигом» нижнее меню — это
      // li[role=menuitem], а кнопка «Перейти в корзину» нулевого размера (эмулятор, 18.10.2026).
      + "var els=[].slice.call(document.querySelectorAll('a,button,[role=button],[role=link],[role=tab],[role=menuitem]')),best=null,bestS=9;"
      // Детали значка (svg и всё внутри) не в счёт: у «Корзины» нижнего меню Магнита
      // картинка из многих частей, и по общему числу потомков кнопка выглядела плиткой.
      + "function kids(e){return e.querySelectorAll('*').length-e.querySelectorAll('svg, svg *').length;}"
      // Много потомков — плитка, только если и сама она большая. У пункта меню со
      // счётчиком на значке («Перейти в корзину Корзина 1») деталей больше восьми, и
      // 1.11 его пропускала: «дорогу к корзине не нашёл» (эмулятор владельца, 18.10.2026).
      + "function tile(e,r){return kids(e)>8&&(r.width>240||r.height>160);}"
      + "for(var j=0;j<els.length;j++){var e=els[j];"
      + " var al=(e.getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim(),tx=(e.innerText||'').replace(/\\s+/g,' ').trim();"
      + " var t=(al+' '+tx).trim();if(t.length>40||!/корзин/i.test(t)||ADD.test(al)||ADD.test(tx)||NEVER.test(t))continue;"
      + " var r=e.getBoundingClientRect();if(r.width<=0||r.height<=0||r.bottom<=0||r.top>=H||tile(e,r))continue;"
      + " var s=/^корзина$/i.test(tx)?0:(/^корзина/i.test(tx)?1:2);if(s<bestS){best=e;bestS=s;}}"
      + "if(best){best.setAttribute(MARK,'1');return JSON.stringify({button:true});}"
      + "var links=[].slice.call(document.querySelectorAll('a[href]'));"
      + "for(var i=0;i<links.length;i++){var a=links[i];try{var u=new URL(a.getAttribute('href'),location.href);"
      + " if(!own(u.hostname)||!/(^|\\/)(cart|basket|korzina)(\\/|$)/i.test(u.pathname))continue;"
      + " if(tile(a,a.getBoundingClientRect()))continue;return JSON.stringify({href:u.href});}catch(e){}}"
      // Дороги не нашлось — для отчёта: что на странице похоже на корзину.
      + "var near=[];[].slice.call(document.querySelectorAll('a,button,[role=button]')).forEach(function(e){if(near.length>=5)return;"
      + " var l=((e.getAttribute('aria-label')||'')+' '+(e.innerText||'')).replace(/\\s+/g,' ').trim(),hr=e.getAttribute('href')||'';"
      + " if(/корзин|cart|basket/i.test(l+' '+hr))near.push((l||hr).slice(0,30));});"
      + "return JSON.stringify({near:near});}catch(e){return JSON.stringify({error:String(e).slice(0,120)});}})(%s)";

    /**
     * Какие из положенных позиций корзина сети показывает «нет в наличии». Замер на
     * эмуляторе 18.10.2026: Алёнку Магнит принял запросом carts/lite с кодом 200 и в
     * ответе ничем её не выделил, а страница корзины поставила её в «Этих товаров
     * нет» с надписью «Нет в наличии» — сборка же написала «легло».
     *
     * От короткой надписи (как в правиле gone, не длиннее 25 знаков: баннер «Некоторые
     * товары закончились — проверьте корзину» не в счёт) поднимаемся к предкам, пока
     * в тексте не встретится имя ровно одной нашей позиции, — это её строка корзины.
     * Два имени сразу или текст длиннее 300 знаков — уже список, не строка: стоп.
     * Стоп и на строке списка без нашего имени (у неё есть соседка того же тега и
     * класса, а текста не меньше 30 знаков): это чужой товар человека, и его «нет в
     * наличии» не должно перейти на нашу позицию из соседней строки. Ответ — номера
     * имён из p.names.
     *
     * И ВИДНО ЛИ ПОЗИЦИЮ В КОРЗИНЕ ВООБЩЕ (seen): не меньше 70 % слов её имени есть
     * в тексте страницы. У Дикси 18.10.2026 Алёнка дала ✓, а в корзине её не было.
     * Имя из латиницы («shokolad alenka molochnyy 75g» — так наряд назвал находку
     * поиском сети) сверяется с текстом страницы, переложенным в латиницу.
     */
    private static final String CART_GONE_JS =
        "(function(p){try{var g=new RegExp(p.gone,'i');"
      + "function norm(s){return String(s||'').toLowerCase().replace(/ё/g,'е').replace(/[^a-zа-я0-9%]+/g,' ').trim();}"
      + "var TR={'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ж':'zh','з':'z','и':'i','й':'y','к':'k','л':'l',"
      + " 'м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh',"
      + " 'щ':'shch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya'};"
      + "function lat(s){return s.replace(/[а-я]/g,function(c){return TR[c]===undefined?c:TR[c];});}"
      + "function item(e){var c=typeof e.className==='string'?e.className:'';if(!c||!e.parentElement)return false;"
      + " for(var s=e.parentElement.firstElementChild;s;s=s.nextElementSibling)"
      + "  if(s!==e&&s.tagName===e.tagName&&s.className===c)return true;return false;}"
      + "var names=(p.names||[]).map(norm),latin=names.map(function(n){return !/[а-я]/.test(n);}),out=[];"
      + "function exact(x,k){var n=names[k];return n.length>=4&&(latin[k]?lat(x):x).indexOf(n)>=0;}"
      + "var w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT),m;"
      + "while((m=w.nextNode())){var t=norm(m.nodeValue);if(!t||t.length>25||!g.test(t))continue;"
      + " var el=m.parentElement;if(!el)continue;var r=el.getBoundingClientRect();if(r.width<=0||r.height<=0)continue;"
      + " for(var i=0;i<8&&el;i++,el=el.parentElement){var x=norm(el.innerText);if(x.length>300)break;"
      + "  var hits=[];names.forEach(function(n,k){if(exact(x,k))hits.push(k);});"
      + "  if(hits.length===1){if(out.indexOf(hits[0])<0)out.push(hits[0]);break;}"
      + "  if(hits.length>1||(x.length>=30&&item(el)))break;}}"
      + "var page=norm(document.body.innerText),pageLat=lat(page),seen=[];"
      + "names.forEach(function(n,k){var hay=latin[k]?pageLat:page;"
      + " var ws=n.split(' ').filter(function(x){return x.length>=3&&!/^\\d+$/.test(x);});"
      + " if(!ws.length){if(n.length>=4&&hay.indexOf(n)>=0)seen.push(k);return;}"
      + " if(ws.filter(function(x){return hay.indexOf(x)>=0;}).length/ws.length>=0.7)seen.push(k);});"
      + "return JSON.stringify({gone:out,seen:seen});}catch(e){return JSON.stringify({gone:[],seen:null,error:String(e).slice(0,80)});}})(%s)";

    /**
     * Итог корзины сети со страницы: строки «Итого», «Товары», «Скидка», «Доставка»,
     * «Сборка», «Баллы», «Минимальный заказ» с суммой в рублях в той же строке, плюс
     * счётчик корзины и числа из ответов сайта, собранные сторожем. Текст строки
     * (row) показывается только на телефоне, на сервер уходят вид, подпись и сумма.
     *
     * У Магнита строки «Итого» нет: сумма стоит на кнопке «К оформлению 1 600,4 ₽»,
     * а число товаров — подписью «11 товаров» (замер владельца 17.10.2026). Кнопка
     * оформления читается как итог, но не нажимается: заказ оформляет человек.
     */
    private static final String READ_JS =
        "(function(){try{"
      + "var KIND=[['total',/^(итого|итог|к оплате|всего|сумма заказа|общая сумма|стоимость заказа|сумма|к оформлению|перейти к оформлению|оформить заказ|оформить)(\\s|:|$)/i],"
      + " ['goods',/^(товары|стоимость товаров|сумма товаров|товаров на сумму|\\d+\\s+товар)/i],"
      + " ['discount',/(скидк|выгод|экономи|промокод|любимый продукт|разнообразное|абонемент)/i],['delivery',/доставк/i],"
      + " ['service',/(сервисн|сбор заказа|сборк|упаковк)/i],['points',/(бонус|балл|апельсинк|спасибо)/i],"
      + " ['min',/(минимальн|до бесплатн)/i]];"
      // Строка итога — одна строка: длиннее 60 знаков или с подписями двух видов сразу
      // («Промокод Товары на сумму…», «Доставка • адрес … Итого») — это уже блок, и сумма
      // в нём чужая. Замер владельца 17.10.2026: так в «доставку» попадал адрес и итог.
      + "var ANY=[/итог|к оплате|к оформлению|сумма заказа/i,/товар/i,/скидк|выгод|экономи|промокод/i,/доставк/i,"
      + " /сервисн|сборк|упаковк/i,/бонус|балл|апельсинк|спасибо/i,/минимальн/i];"
      + "function many(t){var n=0;for(var i=0;i<ANY.length;i++)if(ANY[i].test(t))n++;return n>1;}"
      + "var MONEY=/(\\d{1,3}(?:[\\s\\u00a0\\u202f]\\d{3})+(?:[.,]\\d{1,2})?|\\d+(?:[.,]\\d{1,2})?)\\s?(?:₽|руб)/gi;"
      + "function kind(t){for(var i=0;i<KIND.length;i++)if(KIND[i][1].test(t))return KIND[i][0];return null;}"
      + "function amount(s){return parseFloat(s.replace(/[\\s\\u00a0\\u202f]/g,'').replace(',','.'));}"
      + "var out=[],seen={},goods=null,els=document.querySelectorAll('body *');"
      + "for(var i=0;i<els.length&&out.length<20;i++){var e=els[i];if(e.children.length>3)continue;"
      + " var own=(e.innerText||'').replace(/\\s+/g,' ').trim();if(!own||own.length>40)continue;"
      + " var gm=own.match(/^(\\d+)\\s+товар/i);if(gm&&goods===null)goods=parseInt(gm[1],10);"
      + " var k=kind(own);if(!k)continue;var rc=e.getBoundingClientRect();if(rc.width<=0||rc.height<=0)continue;"
      + " var row=e,val=null,base=null,text='';"
      + " for(var up=0;up<4&&row;up++){text=(row.innerText||'').replace(/\\s+/g,' ').trim();if(text.length>60||many(text))break;"
      + "  var m=[],x;MONEY.lastIndex=0;while((x=MONEY.exec(text))!==null)m.push(x[1]);"
      + "  if(m.length){val=amount(m[m.length-1]);if(m.length>1)base=amount(m[0]);break;}"
      + "  if(k==='delivery'&&/бесплатн/i.test(text)){val=0;break;}"
      + "  row=row.parentElement;}"
      + " if(val===null||!isFinite(val))continue;var key=k+'|'+val;if(seen[key])continue;seen[key]=1;"
      + " out.push({kind:k,label:own,amount:val,base:(base!==null&&isFinite(base))?base:null,row:text.slice(0,120)});}"
      + "var count=null;[].slice.call(document.querySelectorAll('a,button,[role=button]')).forEach(function(e){"
      + " if(e.querySelectorAll('*').length-e.querySelectorAll('svg, svg *').length>8)return;"
      + " var t=((e.getAttribute('aria-label')||'')+' '+(e.innerText||'')).replace(/\\s+/g,' ').trim();"
      + " if(t.length>40||!/корзин/i.test(t))return;var m=t.match(/(\\d+)/);if(m){var n=parseInt(m[1],10);if(count===null||n>count)count=n;}});"
      + "var nums={};try{nums=JSON.parse(sessionStorage.getItem('__kzNums')||'{}');}catch(e){}"
      + "if(goods!==null)count=goods;"
      + "return JSON.stringify({totals:out,count:count,api:nums,page:location.pathname});"
      + "}catch(e){return JSON.stringify({error:String(e).slice(0,120)});}})()";

    /** Сколько дать странице сети улечься после загрузки, мс: витрины дорисовываются скриптом. */
    private static final int SETTLE_MS = 1500;
    /** Сколько ждать после нажатия «Соглашаюсь»: витрина перерисовывает карточку. */
    private static final int CONSENT_WAIT_MS = 1500;
    private static final int CART_SETTLE_MS = 3000;
    private static final int READY_POLL_MS = 600;
    private static final int READY_LIMIT_MS = 30000;
    /** Сколько раз ещё поискать кнопку «В корзину», если её пока нет, и через сколько. */
    private static final int PICK_TRIES = 3;
    /** Сколько ещё ждать запоздалый ответ сети на касание, если запросов не видно совсем. */
    private static final int CALLS_WAIT_MS = 2000;
    private static final int PICK_RETRY_MS = 1500;
    /** Сколько раз перечитать страницу корзины, если итога на ней ещё нет, и через сколько. */
    private static final int READ_TRIES = 5;
    private static final int READ_RETRY_MS = 2500;

    // ── Фоновая сборка (1.13) ──
    /** Через сколько после последней правки корзины начинать: «−» и «+» жмут подряд. */
    private static final int DEBOUNCE_MS = 2500;
    /** Не вышла сборка этой корзины — следующая попытка не раньше: сеть могла отказать. */
    private static final long RETRY_MS = 20L * 60 * 1000;
    /** Сколько времени считать завершённую сборку свежей: 6 часов. После этого корзина пересобирается. */
    private static final long FRESH_MS = 6L * 3600 * 1000;
    /** Пульс серверу не чаще: строку держит живой и сообщение раз в полторы секунды. */
    private static final int BEAT_MS = 1500;
    /** Столько карточек подряд без заголовка — и сеть, значит, показывает не товары. */
    private static final int BLANK_LIMIT = 2;
    /** Скрытое окно без дела дольше — убрать: память телефона не резиновая. */
    private static final int IDLE_MS = 10 * 60 * 1000;
    /** Приложение свёрнуто дольше — фоновые сборки на паузу; вернулся — продолжат. */
    private static final int AWAY_MS = 60 * 1000;
    /** Заказ на очистку истории не ждёт дольше: наш адрес может так и не открыться. */
    private static final int CLEAR_HISTORY_MS = 3000;
    private static final String TAG = "Korzina";

    private WebView web;
    /** Окна стопкой: скрытые окна сетей снизу, окно человека сверху и во весь размер. */
    private FrameLayout stage;
    /** Фоновые сборки: скрытое окно и сборка на сеть, по коду сети. Трогаются только с главного потока. */
    private final Map<String, Slot> slots = new HashMap<>();
    private SharedPreferences prefs;
    private SecureVault secureVault;

    private SecureVault vault() {
        if (secureVault == null) secureVault = new SecureVault(this);
        return secureVault;
    }
    /** Последняя корзина, которую страница отдала на сборку (мост collect). */
    private JSONObject lastCollect;
    /** Приложение свёрнуто дольше AWAY_MS: фоновые сборки стоят до возвращения. */
    private boolean away = false;
    private final Runnable goAway = this::pauseBackground;
    private ProgressBar progress;
    private View chainPanel;
    private TextView chainStatus;
    private Button saveLoginBtn;
    private Button saveOffersBtn;
    private Button recordBtn;
    private Button readBtn;
    private Button cartBtn;
    /** Окно кабинета ФНС: панель, её строка состояния и кнопка «Забрать чеки». */
    private View fnsPanel;
    private TextView fnsStatus;
    private Button fnsGrabBtn;
    /** Открыт кабинет ФНС через мост: панель и «назад» следуют за этим режимом. */
    private boolean fnsMode = false;
    private boolean fnsGrabbing = false;
    private boolean recording = false;
    // volatile: читает его и мост, а он живёт на своём потоке (JavaBridge).
    private volatile int currentChain = -1;
    private String chainLinesOverride = null;
    // Адрес окна человека для моста. WebView.getUrl() с API 18 проверяет поток и из
    // потока моста (JavaBridge) бросает RuntimeException, поэтому адрес запоминается
    // на главном потоке в колбэках WebViewClient, а мост читает только эти поля.
    private volatile String pageUrl = "";
    private volatile boolean pageOurs = false;
    // Один раз почистить историю, когда уходим с чужого окна (кабинет ФНС, страница
    // сети) на наш экран. Ставит её orderHistoryClear — только там, где дальше
    // гарантированно грузится наш адрес. Снимается она на первом нашем событии
    // страницы (onPageCommitVisible/onPageFinished), а если наш адрес так и не
    // пришёл — таймером: иначе заказ съест история следующего нашего экрана.
    // На каждой странице чистить нельзя: «назад» должен возвращать между экранами
    // приложения, а не в кабинет и App Store.
    private boolean clearHistoryOnce = false;
    /** Самостраховка заказа: наш адрес не открылся — флаг снимается сам. */
    private final Runnable clearHistoryReset = () -> clearHistoryOnce = false;
    private CartRun cartRun;
    private final Handler handler = new Handler(Looper.getMainLooper());

    /**
     * «Забрать чеки» нажали, а страница так и не вернулась: молчать нельзя —
     * говорим словами, что ключ не нашёлся, и разрешаем попробовать снова.
     */
    private final Runnable fnsStuck = () -> {
        if (!fnsMode || pageOurs) return;
        fnsGrabbing = false;
        setFnsButtons(true);
        sayFns("Не получилось забрать ключ — войдите в кабинет заново и нажмите «Забрать чеки».");
    };

    /**
     * Мост window.Korzina: им сайт зовёт телефон (шаг 1 единой архитектуры).
     *
     * Методы исполняются на ОТДЕЛЬНОМ потоке WebView (JavaBridge), поэтому всё,
     * что трогает экран, уезжает на главный через handler. Вызовы короткие и без
     * ответа о результате действия: открытие сети человек увидит сам, а действия
     * с отчётом («сохранить вход», акции, запись) пока делает панель — когда они
     * переедут на сайт, у моста появится обратный вызов.
     *
     * МОСТ ТОЛЬКО ДЛЯ НАШЕГО САЙТА. WebView один, и на странице сети window.Korzina
     * тоже виден — её скриптам и чужим счётчикам на ней. Поэтому, пока открыта сеть,
     * мост молчит: ни адреса сервера, ни открытия страниц по чужой просьбе. С 21.10.2026
     * это решается ещё и адресом окна (bridgeAllowed ниже), а не только режимом.
     */
    private final class KorzinaBridge {
        /** Кто на связи: сайт сверяет версию, прежде чем просить о новом. */
        @JavascriptInterface
        public String info() {
            if (!bridgeAllowed()) return "{}";
            if (currentChain >= 0) return "{}";
            try {
                return new JSONObject()
                    .put("version", BuildConfig.VERSION_NAME)
                    .put("server", SERVER)
                    .toString();
            } catch (Exception e) {
                return "{}";
            }
        }

        /** Открыть сайт сети в этом же WebView. Незнакомый код — ложь, и сайт решает сам. */
        @JavascriptInterface
        public boolean openStore(String code) {
            if (!bridgeAllowed()) return false;
            return openStore(code, null);
        }

        @JavascriptInterface
        public boolean openStore(String code, String linesJson) {
            if (!bridgeAllowed()) return false;
            if (currentChain >= 0) return false;
            for (int i = 0; i < CHAINS.length; i++) {
                if (CHAINS[i][1].equals(code)) {
                    final int idx = i;
                    final String lines = linesJson;
                    handler.post(() -> openChain(idx, lines));
                    return true;
                }
            }
            return false;
        }

        /** Вернуться на наш сайт. Путь только свой: мост чужих адресов не открывает. */
        @JavascriptInterface
        public void openApp(String path) {
            if (!bridgeAllowed()) return;
            if (currentChain >= 0) return;
            final String target = (path != null && path.startsWith("/") && !path.contains("://"))
                ? path : "/";
            handler.post(() -> openOurApp(target));
        }

        /**
         * Открыть кабинет ФНС внутри приложения (окно Z5b).
         *
         * Только наш сайт: чужой странице окно налоговой не нужно, а мост на ней и так
         * молчит (bridgeAllowed). Режим сети и кабинет ФНС одновременно не живут.
         */
        @JavascriptInterface
        public void openFns() {
            if (!bridgeAllowed()) return;
            if (currentChain >= 0) return;
            handler.post(() -> openFnsWindow());
        }

        /**
         * Корзина на экране (1.13): {basket, stamp, chains: [{code, login, cookies, sent}]}.
         * Приложение само решает, где собрать её в фоне (decide), и отвечает странице
         * {starting, skip}. Решение — на главном потоке, где живут сборки; мост ждёт его
         * недолго и, не дождавшись, отвечает пусто: страница всё равно спросит сервер.
         * Только нашему сайту: решает адрес окна человека, а не режим. Режим сети снимается
         * в onPageFinished, а страница корзины зовёт мост раньше, по DOMContentLoaded, —
         * и, вернувшись со страницы сети «←», человек остался бы без сборки (эмулятор,
         * 18.10.2026). Страница сети адрес окна своим сделать не может.
         */
        @JavascriptInterface
        public String collect(String json) {
            if (!bridgeAllowed()) return "{}";
            final JSONObject payload;
            try {
                payload = new JSONObject(json == null ? "{}" : json);
            } catch (Exception e) {
                return "{}";
            }
            final AtomicReference<String> answer = new AtomicReference<>("{}");
            final CountDownLatch ready = new CountDownLatch(1);
            handler.post(() -> {
                try {
                    if (pageOurs) answer.set(decide(payload).toString());
                } catch (Exception e) {
                    Log.w(TAG, "сборка в фоне не решилась", e);
                } finally {
                    ready.countDown();
                }
            });
            try {
                ready.await(1500, TimeUnit.MILLISECONDS);
            } catch (InterruptedException ignored) { }
            return answer.get();
        }

        /** Что собирается в фоне сейчас и что ждёт запуска: {going: [коды], soon: [коды]}. */
        @JavascriptInterface
        public String busy() {
            if (!bridgeAllowed()) return "{}";
            if (currentChain >= 0) return "{}";
            return busyNow;
        }
    }

    /**
     * Адрес — нашего сервера, а не сети и не чужой.
     *
     * Обычная проверка строки: ни WebView, ни его потоков не трогает, поэтому её
     * можно звать откуда угодно, в том числе с потока моста (JavaBridge).
     */
    private static boolean isOurs(String url) {
        return url != null && (url.equals(SERVER) || url.startsWith(SERVER + "/"));
    }

    /**
     * Пускать ли мост: только когда в окне НАШ адрес (21.10.2026, план Z0).
     *
     * Зачем отдельно от currentChain. Режим сети — это наше намерение, а не адрес:
     * он снимается в onPageFinished, то есть по факту загрузки, и по дороге
     * расходится с тем, что человек видит в окне. Адрес окна запоминается там, где
     * его безопасно спросить, — на главном потоке (pageUrl/pageOurs). WebView.getUrl()
     * здесь, на потоке моста, бросил бы RuntimeException «A WebView method was called
     * on thread 'JavaBridge'»: тогда молчал бы весь мост, а не только чужая страница.
     * В журнал идёт только хост: в query чужих адресов бывают токены.
     */
    private boolean bridgeAllowed() {
        // Два volatile-поля пишутся по очереди, поэтому одного pageOurs мало:
        // pageUrl уже новый, а флаг ещё старый — сверяем и адрес, но только строкой.
        if (pageOurs && isOurs(pageUrl)) return true;
        Log.w(TAG, "мост: чужая страница " + hostOf(pageUrl));
        return false;
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        // Белая заливка окна — чтобы при запуске не мигала тема платформы.
        getWindow().setBackgroundDrawable(new ColorDrawable(Color.WHITE));

        if (SERVER.isEmpty()) {
            TextView lost = new TextView(this);
            lost.setPadding(dp(24), dp(24), dp(24), dp(24));
            lost.setTextSize(16f);
            lost.setText("Адрес сервера не задан при сборке (-PkorzinaServer).");
            setContentView(lost);
            paintSystemBars();
            return;
        }

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);
        root.setFitsSystemWindows(true);

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        progress.setVisibility(View.GONE);
        root.addView(progress, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(3)));

        // Весь экран — сайт. Панель действий показывается только на странице сети:
        // на наших экранах командует сайт, там панели не место.
        //
        // ПАНЕЛЬ ПОД СТРАНИЦЕЙ, А НЕ ПОВЕРХ (1.5). В 1.4 она лежала поверх и закрывала
        // нижнее меню мобильного сайта сети — у Магнита там значок корзины, и владелец
        // 17.10.2026 не нашёл, как в неё попасть. Теперь страница кончается над панелью.
        //
        // СТОПКА ОКОН (1.13). Под окном человека лежат скрытые окна сетей — фоновая сборка.
        // Они того же размера и рисуются, как обычные: так касание доходит до страницы и
        // таймеры сайта не душатся. Человек видит только верхнее окно: у него белый фон.
        stage = new FrameLayout(this);
        root.addView(stage, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        web = new WebView(this);
        web.setBackgroundColor(Color.WHITE);
        stage.addView(web, new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        chainPanel = buildChainPanel();
        chainPanel.setVisibility(View.GONE);
        root.addView(chainPanel, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        // Панель кабинета ФНС — такая же, но своя: режимы разные и одновременно не живут.
        fnsPanel = buildFnsPanel();
        fnsPanel.setVisibility(View.GONE);
        root.addView(fnsPanel, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        setContentView(root);
        // ПОСЛЕ setContentView: WindowInsetsController живёт в оформлении окна, которое
        // создаётся вместе с содержимым. До него PhoneWindow.getInsetsController()
        // падал с NullPointerException (замер на эмуляторе 19.10.2026).
        paintSystemBars();
        setUpWeb();
        openOurApp("/");
    }

    /**
     * Строки состояния и навигации — под цвет белой страницы: тёмные значки на белом.
     * API 30+ — через WindowInsetsController (setSystemUiVisibility там устарел),
     * ниже — старыми флагами. Светлые значки полосы навигации есть только с API 26:
     * на 24–25 полоса остаётся тёмной, иначе белые кнопки пропали бы на белом.
     */
    private void paintSystemBars() {
        Window window = getWindow();
        window.setStatusBarColor(Color.WHITE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            window.setNavigationBarColor(Color.WHITE);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            WindowInsetsController bars = window.getInsetsController();
            if (bars != null) {
                int light = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                          | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
                bars.setSystemBarsAppearance(light, light);
            }
        } else {
            int flags = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                flags |= View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
            }
            window.getDecorView().setSystemUiVisibility(flags);
        }
    }

    /** Панель на странице сети: действия, которые серверу с этой страницы не сделать. */
    private View buildChainPanel() {
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(8), dp(6), dp(8), dp(8));
        panel.setBackgroundColor(0xF2F5F5F5);

        chainStatus = new TextView(this);
        chainStatus.setTextSize(13f);
        chainStatus.setTextColor(Color.DKGRAY);
        chainStatus.setSingleLine(true);
        chainStatus.setEllipsize(android.text.TextUtils.TruncateAt.END);
        panel.addView(chainStatus);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER);
        row.setPadding(0, dp(6), 0, 0);

        Button backBtn = new Button(this);
        backBtn.setText("←");
        backBtn.setTextSize(12f);
        backBtn.setOnClickListener(v -> leaveChain());
        row.addView(backBtn, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 0.6f));

        saveLoginBtn = new Button(this);
        saveLoginBtn.setText("Вход");
        saveLoginBtn.setTextSize(12f);
        saveLoginBtn.setOnClickListener(v -> saveLogin());
        row.addView(saveLoginBtn, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        saveOffersBtn = new Button(this);
        saveOffersBtn.setText("Акции");
        saveOffersBtn.setTextSize(12f);
        saveOffersBtn.setOnClickListener(v -> saveOffers());
        row.addView(saveOffersBtn, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        recordBtn = new Button(this);
        recordBtn.setText("Запись");
        recordBtn.setTextSize(12f);
        recordBtn.setOnClickListener(v -> toggleRecord());
        row.addView(recordBtn, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        // «Раздел» — прочитать карточки открытой страницы сети в единую базу. Нужна там,
        // где сервер витрину не видит (Пятёрочка, Самокат), а телефон видит.
        readBtn = new Button(this);
        readBtn.setText("Раздел");
        readBtn.setTextSize(12f);
        readBtn.setOnClickListener(v -> readSection());
        row.addView(readBtn, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        cartBtn = new Button(this);
        cartBtn.setText("Собрать");
        cartBtn.setTextSize(12f);
        cartBtn.setOnClickListener(v -> toggleCart());
        row.addView(cartBtn, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.1f));

        panel.addView(row);
        return panel;
    }

    /**
     * Панель кабинета ФНС: под чужой страницей, как панель сети под магазином.
     *
     * Здесь только то, чего страница налоговой сама не сделает: вернуться на наш экран
     * и забрать вход. Всё остальное — дело сайта кабинета и человека.
     */
    private View buildFnsPanel() {
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(8), dp(6), dp(8), dp(8));
        panel.setBackgroundColor(0xF2F5F5F5);

        fnsStatus = new TextView(this);
        fnsStatus.setTextSize(13f);
        fnsStatus.setTextColor(Color.DKGRAY);
        fnsStatus.setSingleLine(true);
        fnsStatus.setEllipsize(android.text.TextUtils.TruncateAt.END);
        panel.addView(fnsStatus);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER);
        row.setPadding(0, dp(6), 0, 0);

        Button backBtn = new Button(this);
        backBtn.setText("←");
        backBtn.setTextSize(12f);
        backBtn.setOnClickListener(v -> leaveFns());
        row.addView(backBtn, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 0.6f));

        fnsGrabBtn = new Button(this);
        fnsGrabBtn.setText("Забрать чеки");
        fnsGrabBtn.setTextSize(12f);
        fnsGrabBtn.setOnClickListener(v -> grabFns());
        row.addView(fnsGrabBtn, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.4f));

        panel.addView(row);
        return panel;
    }

    private void setUpWeb() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setSupportZoom(true);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);
        // Метка в конце User-Agent: сервер узнаёт телефон с первого запроса и
        // рисует кнопки моста сразу, а не после того, как встанет скрипт.
        // Вторая, независимая половина узнавания — сам мост ниже.
        s.setUserAgentString(s.getUserAgentString() + " KorzinaApp/" + BuildConfig.VERSION_NAME);
        web.addJavascriptInterface(new KorzinaBridge(), "Korzina");

        // ПОЛОС ПРОКРУТКИ НЕ ВИДНО (1.15). WebView рисует у правого края полосу — по ней
        // сразу видно, что перед человеком сайт, а не приложение. Листается страница
        // пальцем и без неё.
        web.setVerticalScrollBarEnabled(false);
        web.setHorizontalScrollBarEnabled(false);

        CookieManager jar = CookieManager.getInstance();
        jar.setAcceptCookie(true);
        jar.setAcceptThirdPartyCookies(web, true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                super.onPageStarted(view, url, favicon);
                // Адрес берём из колбэка, а не из view.getUrl(): документация
                // предупреждает, что в onPageStarted getUrl() может отдавать ещё
                // старую страницу, а страница корзины зовёт collect уже по
                // DOMContentLoaded. Поток здесь главный — WebView не трогаем.
                pageUrl = url == null ? "" : url;
                pageOurs = isOurs(pageUrl);
                followFnsPage();
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                pageUrl = url == null ? "" : url;
                pageOurs = isOurs(pageUrl);
                // Режим следует за адресом, а не за тем, кто его открыл: человек
                // мог вернуться «назад» на наш сайт сам — и панель должна уйти
                // вместе со страницей сети, а не висеть поверх корзины.
                if (currentChain >= 0 && !isHostOf(currentChain, url)) {
                    exitChainMode();
                }
                // Кабинет ФНС: вернулись на наш адрес — режим снят, панель ушла.
                if (fnsMode) {
                    followFnsPage();
                }
                // Ушли с чужого окна на наш экран — историю чужого окна за собой не
                // тащим. Заказ ставят leaveFns/leaveChain и снятие режимов (и это
                // может случиться выше, в followFnsPage/exitChainMode), а исполняется
                // он уже здесь, на нашем адресе. На каждой странице clearHistory не
                // зовём — иначе «назад» перестанет возвращать между экранами.
                clearHistoryIfOurs(view);
                if (fnsMode) return;
                if (currentChain < 0) return;
                chainPanel.setVisibility(View.VISIBLE);
                if (recording) {
                    // Новая страница — новый скрипт: обёртка живёт, пока жива страница.
                    // Записанное раньше лежит в sessionStorage и переживает перезагрузку.
                    view.evaluateJavascript(recScript(currentChain), null);
                    if (cartRun == null) say("Идёт запись — " + shortenUrl(url), false);
                } else if (cartRun == null) {
                    // Во время сборки строку состояния ведёт она: «кладу 3 из 9».
                    say(CHAINS[currentChain][0] + " — " + shortenUrl(url), false);
                }
            }

            @Override
            public void doUpdateVisitedHistory(WebView view, String url, boolean isReload) {
                super.doUpdateVisitedHistory(view, url, isReload);
                pageUrl = url == null ? "" : url;
                pageOurs = isOurs(pageUrl);
                followFnsPage();
            }

            @Override
            public void onPageCommitVisible(WebView view, String url) {
                super.onPageCommitVisible(view, url);
                pageUrl = url == null ? "" : url;
                pageOurs = isOurs(pageUrl);
                followFnsPage();
                // Страница уже наша и зафиксирована в истории: исполняем заказ здесь
                // же, не дожидаясь onPageFinished — из back-forward cache он может
                // не прийти, а флаг обязан сняться на первом нашем событии страницы.
                clearHistoryIfOurs(view);
            }

            @Override
            public void onReceivedSslError(WebView view, android.webkit.SslErrorHandler h,
                                           android.net.http.SslError error) {
                // Сертификаты Минцифры у части российских магазинов — только для их сайтов.
                // Наш сервер и чужие адреса с битым сертификатом не открываются никогда:
                // там ездит сессия владельца.
                if (currentChain >= 0 && isChainHost(error.getUrl())) h.proceed();
                else h.cancel();
            }

            @Override
            public boolean onRenderProcessGone(WebView view, android.webkit.RenderProcessGoneDetail detail) {
                // Процесс страниц один на все окна приложения и при нехватке памяти падает
                // разом. Не ответить здесь — упадёт и само приложение; отвечаем и заводим
                // окно человека заново.
                handler.post(() -> reviveMainView(view));
                return true;
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int p) {
                progress.setVisibility(p < 100 ? View.VISIBLE : View.GONE);
                progress.setProgress(p);
            }

            @Override
            public boolean onConsoleMessage(ConsoleMessage message) {
                // Сборщик кабинета ФНС говорит с человеком только через журнал страницы:
                // моста на чужом адресе нет. Показываем его строки на панели — иначе
                // «забираю чеки…» висело бы молча, что бы ни случилось.
                if (fnsMode && message != null) {
                    String text = message.message() == null ? "" : message.message();
                    if (text.startsWith(FNS_ERR)) {
                        // «Ключ не нашёлся» и прочие концы: работу можно начинать заново.
                        sayFns(text.substring(FNS_ERR.length()).trim());
                        handler.removeCallbacks(fnsStuck);
                        fnsGrabbing = false;
                        setFnsButtons(true);
                    } else if (text.startsWith(FNS_LOG)) {
                        sayFns(text.substring(FNS_LOG.length()).trim());
                    }
                }
                return super.onConsoleMessage(message);
            }
        });
    }

    // ── Режимы: наш сайт и страница сети ──
    private void openOurApp(String path) {
        exitChainMode();
        exitFnsMode();
        // Стартовый адрес запоминаем до loadUrl: getUrl() сразу после загрузки ещё
        // старый, а мост может спросить в ту же секунду. Дальше адрес ведут колбэки.
        pageUrl = SERVER + path;
        pageOurs = isOurs(pageUrl);
        web.loadUrl(SERVER + path);
    }

    private void openChain(int idx) {
        openChain(idx, null);
    }

    private void openChain(int idx, String linesJson) {
        currentChain = idx;
        chainLinesOverride = linesJson;
        stopRecording();
        chainPanel.setVisibility(View.VISIBLE);
        setChainButtons(true);
        say("Открываю " + CHAINS[idx][0] + "…", false);
        web.loadUrl(CHAINS[idx][2]);
    }

    /**
     * Заказать разовую очистку истории. Ставится только там, где следом
     * гарантированно грузится наш адрес. Заказ снимается на первом нашем событии
     * страницы, а если его не будет — таймером: флаг не должен пережить переход.
     */
    private void orderHistoryClear() {
        clearHistoryOnce = true;
        handler.removeCallbacks(clearHistoryReset);
        handler.postDelayed(clearHistoryReset, CLEAR_HISTORY_MS);
    }

    /**
     * Наш адрес открылся — исполнить заказ. Единственный clearHistory() в файле:
     * на каждой нашей странице историю не чистим, иначе «назад» перестанет
     * возвращать между экранами приложения.
     */
    private void clearHistoryIfOurs(WebView view) {
        if (!clearHistoryOnce || !pageOurs) return;
        view.clearHistory();
        clearHistoryOnce = false;
        handler.removeCallbacks(clearHistoryReset);
    }

    /** Уйти со страницы сети обратно к сайту — по истории, если она ещё помнит нас. */
    private void leaveChain() {
        if (web.canGoBack()) {
            // История уже помнит наш предыдущий экран — идём по ней и ничего не
            // чистим. Флаг здесь не взводим: goBack() может вернуть на страницу
            // сети (или на страницу из back-forward cache, откуда onPageFinished
            // не придёт), и тогда заказ исполнился бы на следующем нашем экране —
            // «назад» закрыл бы приложение.
            web.goBack();
        } else {
            // Истории нет — грузим наш адрес сами, заказ снимется уже на нём.
            orderHistoryClear();
            openOurApp("/");
        }
    }

    private void exitChainMode() {
        // Снятие режима сети — это и есть уход на наш экран. Заказ ставится, только
        // когда режим правда был: openOurApp зовёт этот метод и на обычных переходах
        // между нашими экранами, а там историю чистить нельзя.
        if (currentChain >= 0) orderHistoryClear();
        if (cartRun != null) stopRun("Сборка остановлена: вы ушли со страницы сети.");
        currentChain = -1;
        chainLinesOverride = null;
        stopRecording();
        if (chainPanel != null) chainPanel.setVisibility(View.GONE);
    }

    private void setChainButtons(boolean on) {
        saveLoginBtn.setEnabled(on);
        saveOffersBtn.setEnabled(on);
        recordBtn.setEnabled(on);
        readBtn.setEnabled(on);
        cartBtn.setEnabled(on);
    }

    // ── Кабинет ФНС: вход у человека, ключ — у приложения ──

    /** Открыть кабинет ФНС на весь экран. Вход и капчу человек проходит сам. */
    private void openFnsWindow() {
        exitChainMode();
        fnsMode = true;
        fnsGrabbing = false;
        pageUrl = FNS_URL;
        pageOurs = isOurs(pageUrl);
        showFnsPanel();
        setFnsButtons(true);
        sayFns("Войдите в кабинет ФНС сами — приложение капчу не проходит. Потом нажмите «Забрать чеки».");
        web.loadUrl(FNS_URL);
    }

    /** Уйти из кабинета на телефонный экран чеков: и по «←», и по кнопке «назад». */
    private void leaveFns() {
        // Кабинет и всё, куда с него уехали (вплоть до App Store), остаются позади:
        // «назад» с экрана чеков должен вести по нашему приложению, а не туда.
        // Заказ ставится до openOurApp: наш адрес загрузится гарантированно.
        orderHistoryClear();
        openOurApp("/phone/receipts");
    }

    /** Снять режим кабинета ФНС: панель уходит, таймер ожидания гаснет. */
    private void exitFnsMode() {
        // Режим снимается, когда окно вернулось на наш адрес (или мы уходим с кабинета):
        // его история нашему экрану не нужна. Без режима (openOurApp на обычном
        // переходе) заказа нет — иначе очистка сломала бы «назад» между нашими экранами.
        if (fnsMode) orderHistoryClear();
        fnsMode = false;
        fnsGrabbing = false;
        handler.removeCallbacks(fnsStuck);
        if (fnsPanel != null) fnsPanel.setVisibility(View.GONE);
    }

    /**
     * Панель видна ровно в одном случае: кабинет открыт через мост, а в окне — чужой
     * адрес. На нашем сайте её нет: там командует сайт.
     */
    private void showFnsPanel() {
        if (fnsPanel == null) return;
        fnsPanel.setVisibility(fnsMode && !pageOurs ? View.VISIBLE : View.GONE);
    }

    /** Режим ФНС следует за адресом окна, а не за намерением: вернулись — панель ушла. */
    private void followFnsPage() {
        if (!fnsMode) return;
        if (pageOurs) exitFnsMode();
        else showFnsPanel();
    }

    private void setFnsButtons(boolean on) {
        if (fnsGrabBtn != null) fnsGrabBtn.setEnabled(on);
    }

    private void sayFns(String text) {
        if (fnsStatus == null) return;
        fnsStatus.setText(text);
        Log.i(TAG, "кабинет ФНС: " + text);
    }

    /**
     * «Забрать чеки»: отдать странице адрес приложения и исполнить сборщик.
     *
     * Страница уходит в приложение САМА (location.replace внутри fns-grab.js), когда
     * найдёт ключ. Поэтому сразу после инъекции ничего не утверждаем: ждём возврата
     * адреса, а если его нет — через fnsStuck говорим словами. Ошибки сборщика
     * приходят строками журнала (onConsoleMessage) и тоже видны на панели.
     */
    private void grabFns() {
        if (!fnsMode || pageOurs || fnsGrabbing) return;
        fnsGrabbing = true;
        setFnsButtons(false);
        sayFns("Забираю чеки. Вход и капчу вы уже прошли — приложение вернётся на «Мои чеки».");
        // Адрес приложения — до сборщика: он берёт его из window.KORZINA_APP_URL.
        web.evaluateJavascript("window.KORZINA_APP_URL='" + SERVER + "/phone/receipts';", null);
        handler.removeCallbacks(fnsStuck);
        handler.postDelayed(fnsStuck, 25 * 1000);
        // GET, а не POST: статика сервера POST не принимает. call с null телом — GET.
        call(FNS_GRAB, null, (status, text) -> {
            if (!fnsMode) return;
            if (status != 200 || text == null || text.trim().isEmpty()) {
                fnsGrabbing = false;
                setFnsButtons(true);
                handler.removeCallbacks(fnsStuck);
                sayFns("Сборщик не отдался (код " + status + "). Попробуйте ещё раз.");
                return;
            }
            web.evaluateJavascript(text, null);
        });
    }

    // ── Запись корзины ──
    /** Скрипт записи со своими адресами этой сети. */
    private static String recScript(int idx) {
        JSONArray allow = new JSONArray();
        for (String host : CHAINS[idx][3].split(",")) allow.put(host.trim());
        return REC_JS.replace("%s", allow.toString());
    }

    /** Ушли со страницы сети — запись этой сети больше не наша: кнопка снова «Записать». */
    private void stopRecording() {
        recording = false;
        if (recordBtn != null) recordBtn.setText("Запись");
    }

    private void toggleRecord() {
        if (currentChain < 0) return;
        if (!recording) {
            recording = true;
            recordBtn.setText("Готово");
            web.evaluateJavascript(REC_START_JS, null);
            web.evaluateJavascript(recScript(currentChain), null);
            say("Идёт запись. Положите в корзину любой товар, потом уберите его и нажмите «Готово».",
                false);
            return;
        }
        final String chain = CHAINS[currentChain][1];
        final String name = CHAINS[currentChain][0];
        stopRecording();
        web.evaluateJavascript(REC_TAKE_JS, result -> {
            try {
                String json = (String) new JSONTokener(result).nextValue();
                JSONArray entries = new JSONArray(json == null ? "[]" : json);
                if (entries.length() == 0) {
                    say("Запросов корзины не видно. Войдите в " + name + ", нажмите «Записать», "
                        + "положите товар и уберите его, затем «Готово».", true);
                    return;
                }
                JSONObject body = new JSONObject()
                    .put("store", chain)
                    .put("app_version", BuildConfig.VERSION_NAME)
                    .put("entries", entries);
                say("Отправляю запись…", false);
                post("/api/cart_record", body,
                     name + " — записано запросов корзины: " + entries.length());
            } catch (Exception e) {
                say("Не получилось забрать запись: " + e.getMessage(), true);
            }
        });
    }

    // ── Сборка корзины кнопками сайта ──

    /**
     * Одна сборка в одной сети. Шаги идут цепочкой отложенных вызовов, и каждый
     * первым делом сверяется, что сборка всё ещё ЭТА и не остановлена: человек мог
     * нажать «Стоп» или уйти со страницы, пока карточка грузилась.
     *
     * ДВЕ РУКИ (1.13). «Собрать» на странице сети кладёт в окне человека, как раньше.
     * Фоновая сборка кладёт в скрытом окне своей сети (Slot), пока человек смотрит
     * корзину на нашем экране, — по сборке на сеть, сети параллельно. Поэтому каждый шаг
     * берёт окно у своей сборки (view), а не у экрана.
     */
    private static final class CartRun {
        final int chain;
        final String code;
        final String name;
        final WebView view;      // где кладём: окно человека или скрытое окно сети
        final Slot slot;         // фоновая сборка — её скрытое окно; null — «Собрать» человека
        JSONArray linesOverride = null; // Позиции для сборки доли сплита
        JSONObject rules = new JSONObject();
        JSONArray lines = new JSONArray();
        double ourTotal = 0;
        int basket = -1;         // какую корзину собираем и отпечаток её состава — из наряда
        String stamp = "";
        int at = 0;        // какую позицию кладём
        int clicks = 1;    // сколько касаний у неё: упаковок, которых не хватает
        int put = 0;       // сколько уже сделано
        int tries = 0;     // сколько раз ещё раз искали кнопку на этой карточке
        boolean cardAgain = false; // карточку уже перезагружали, когда кнопка не нашлась
        int have = 0;      // сколько упаковок этой позиции уже лежало в корзине сети
        int target = 1;    // сколько должно лежать
        int readTries = 0;            // сколько раз перечитывали страницу корзины без итога
        boolean cartPageTried = false; // открывали ли адрес корзины из наряда
        String cartFrom = null;        // путь страницы, с которой пошли в корзину
        final JSONArray items = new JSONArray();
        // Что лежит в корзине сети: артикул → упаковок. Начинается с памяти телефона (что
        // он сам клал сюда прежде) и поправляется ответами сети с корзиной целиком.
        JSONObject cart = new JSONObject();
        // Артикул → цена за упаковку, как её назвал ответ сети с корзиной. Цель требует
        // получать цены корзиной в фоне: у Перекрёстка, Vprok и Монетки обход отдаёт
        // только названия, и живая корзина — единственное место, где цена видна.
        // Ответа с ценами не было — карта пуста, и в отчёт цена не попадёт.
        JSONObject prices = new JSONObject();
        JSONObject names = new JSONObject();   // артикул → название: чтобы назвать лишнее
        // Артикулы, которые страница корзины сети уже показывала под именем наряда.
        final JSONObject shown = new JSONObject();
        final JSONObject want = new JSONObject();    // артикул → сколько должно лежать
        long cartAt = 0;   // когда сеть последний раз показала корзину целиком
        boolean stopped = false;
        // Запрос сети «положить в корзину» выучен по первому касанию (LEARN_JS): дальше
        // позиции кладутся им, без загрузки карточек. Пробуем выучить один раз.
        boolean learned = false;
        boolean learnTried = false;
        String how = "tap";          // чем легла текущая позиция: касанием или запросом
        final long startedAt = SystemClock.uptimeMillis();
        String cartHint = "";        // дорога к корзине не нашлась: что похожее было на странице
        String consent = "";         // на этой позиции закрыли баннер согласия — это видно в отчёте
        boolean consentRetry = false; // после баннера карточку уже проверили повторно — один раз
        double cardPrice = 0;         // цена за упаковку из текста карточки этой позиции
        boolean cardGone = false;     // карточка/её текст сказали «нет в наличии»
        boolean searchPause = false; // предыдущая строка упала на поиске/стене: нужна пауза
        int badged = 0;             // счётчик корзины до касания: после — сверяем, вырос ли
        boolean badgeChecked = false; // признак корзины после касания уже проверяли — один раз
        String now = "";             // что делается сейчас — строкой на панели или нашем экране
        int blank = 0;               // сколько карточек подряд открылись без заголовка
        long beatAt = 0;             // когда последний раз сказали серверу, как идёт
        boolean again = false;       // корзина сети оказалась пустой: один повтор целиком
        // Последний шаг наряда Ленты (Z9): серверная ссылка на готовую корзину.
        // Спрашиваем один раз за проход; завершение наряда от неё не зависит.
        boolean linkAsked = false;
        boolean linkDone = false;
        String linkNote = "";        // что телефон сказал про ссылку — для отчёта строки

        CartRun(int chain, WebView view, Slot slot) {
            this.chain = chain;
            this.code = CHAINS[chain][1];
            this.name = CHAINS[chain][0];
            this.view = view;
            this.slot = slot;
        }
    }

    /**
     * Скрытое окно сети и её фоновая сборка. Одна сеть — одно окно и не больше одной
     * сборки: внутри сети шаги идут друг за другом, одновременных запросов к ней нет.
     * Окно живёт между сборками (кэш сети тёплый) и уходит, если долго без дела.
     */
    private static final class Slot {
        final int chain;
        final String code;
        WebView view;
        CartRun run;
        String pending = "";     // какую корзину собрать, когда окно освободится
        Runnable soon;           // отложенный запуск: корзину, может быть, ещё правят
        Runnable sleep;          // убрать окно, если долго без дела
        int crashes = 0;

        Slot(int chain) {
            this.chain = chain;
            this.code = CHAINS[chain][1];
        }
    }

    private boolean alive(CartRun r) {
        if (r == null || r.stopped) return false;
        return r.slot == null ? r == cartRun : r.slot.run == r;
    }

    /** Ход сборки: у «Собрать» — на панели сети, у фоновой — на нашем экране, через сервер. */
    private void status(CartRun r, String text, boolean bad) {
        r.now = text;
        if (r.slot == null) say(text, bad);
        beat(r, false);
    }

    /**
     * Сказать серверу, как идёт сборка: строка «Сборки корзин» живёт этим пульсом, и пока
     * он идёт, сервер в эту сеть не кладёт. Не чаще BEAT_MS, кроме начала и конца.
     */
    private void beat(final CartRun r, boolean force) {
        long t = SystemClock.uptimeMillis();
        if (!force && t - r.beatAt < BEAT_MS) return;
        r.beatAt = t;
        JSONObject body = new JSONObject();
        try {
            body.put("store", r.code).put("planned", r.lines.length())
                .put("at", Math.min(r.at, r.lines.length())).put("now", r.now);
            if (r.basket >= 0) body.put("basket", r.basket);
            if (!r.stamp.isEmpty()) body.put("stamp", r.stamp);
        } catch (Exception ignored) { }
        final boolean first = force && r.at == 0;
        call("/api/device/cartprogress", body, (status, text) -> {
            // Сервер сам кладёт в эту сеть — две руки в одной корзине положили бы всё дважды.
            // Отступаем до следующей попытки (RETRY_MS) или правки корзины.
            if (status == 400 && r.slot != null && alive(r) && serverError(status, text).contains("кладёт сервер")) {
                Slot s = r.slot;
                stopBackground(s, true);
                remember(r, false);
                rest(s);
                return;
            }
            // Строка «идёт» уже на сервере — пусть наш экран спросит её сейчас, а не через опрос.
            if (first && r.slot != null) tellPage();
        });
    }

    /**
     * Что сейчас в фоне: какие сети собираются и какие ждут запуска. Страница корзины
     * спрашивает это мостом (busy), чтобы опрашивать сервер, пока сборка не кончится.
     * Пишется на главном потоке, читается потоком моста — поэтому готовой строкой.
     */
    private volatile String busyNow = "{\"going\":[],\"soon\":[]}";

    private void touchBusy() {
        JSONArray going = new JSONArray(), waiting = new JSONArray();
        for (Slot s : slots.values()) {
            if (s.run != null) going.put(s.code);
            else if (s.soon != null) waiting.put(s.code);
        }
        try {
            busyNow = new JSONObject().put("going", going).put("soon", waiting).toString();
        } catch (Exception ignored) { }
    }

    /** Наш экран корзины открыт — пусть сразу перечитает строки сетей (kzRunsChanged). */
    private void tellPage() {
        touchBusy();
        if (currentChain >= 0 || web == null || !isOurs(web.getUrl())) return;
        web.evaluateJavascript("window.kzRunsChanged&&window.kzRunsChanged()", null);
    }

    private void toggleCart() {
        if (currentChain < 0) return;
        if (cartRun != null) {
            stopRun("Сборка остановлена.");
            return;
        }
        // «Собрать» человека главнее фоновой: в эту сеть сейчас кладёт только он.
        Slot busy = slots.get(CHAINS[currentChain][1]);
        if (busy != null && busy.run != null) stopBackground(busy, true);
        final CartRun r = new CartRun(currentChain, web, null);
        if (chainLinesOverride != null && !chainLinesOverride.isEmpty()) {
            try {
                r.linesOverride = new JSONArray(chainLinesOverride);
            } catch (Exception ignored) { }
        }
        cartRun = r;
        setChainButtons(false);
        cartBtn.setEnabled(true);
        cartBtn.setText("Стоп");
        // Человек сам собирает в этой сети — значит, вошёл в неё: фоновая сборка пойдёт сюда тоже.
        memory().edit().putBoolean("in." + r.code, true).apply();
        say("Беру у сервера, что положить в " + r.name + "…", false);
        askPlan(r, () -> {
            if (r.lines.length() == 0) {
                offerThisCard(r, r.rules.optString("_note", ""));
                return;
            }
            loadLedger(r);
            say(r.name + ": позиций " + r.lines.length() + ", начинаю", false);
            beat(r, true);
            nextLine(r);
        });
    }

    /** Взять у сервера наряд: позиции этой сети, правила кнопок, корзину и её отпечаток. */
    private void askPlan(final CartRun r, final Runnable then) {
        JSONObject body = new JSONObject();
        try {
            body.put("store", r.code);
            if (r.basket >= 0) body.put("basket", r.basket);
            if (r.linesOverride != null && r.linesOverride.length() > 0) {
                body.put("lines", r.linesOverride);
            }
        } catch (Exception ignored) { }
        call("/api/device/cartplan", body, (status, text) -> {
            if (!alive(r)) return;
            if (status != 200) {
                failRun(r, serverError(status, text));
                return;
            }
            try {
                JSONObject plan = new JSONObject(text);
                JSONObject rules = plan.optJSONObject("rules");
                JSONArray lines = plan.optJSONArray("lines");
                r.rules = rules != null ? rules : new JSONObject();
                r.rules.put("_note", plan.optString("note", ""));
                r.lines = lines != null ? lines : new JSONArray();
                // Позиции, которых сеть не знает: сервер просит найти их поиском самой сети.
                // Там, где сервер к витрине не пробивается (Пятёрочка, Самокат — замер
                // 18.10.2026), это единственная дорога к настоящему артикулу. Строка ложится
                // в тот же список, но с пометкой search: первый шаг у неё — страница поиска.
                // Рядом — второй, короткий адрес поиска (search_alt), если сервер его дал:
                // когда первый запрос принесёт не тот товар, телефон попробует этот.
                JSONArray search = plan.optJSONArray("search");
                for (int i = 0; search != null && i < search.length(); i++) {
                    JSONObject s = search.optJSONObject(i);
                    String sUrl = s == null ? "" : s.optString("url", "");
                    String sName = s == null ? "" : s.optString("name", "");
                    if (sUrl.isEmpty() || sName.isEmpty()) continue;
                    r.lines.put(new JSONObject()
                        .put("name", sName).put("url", "").put("search", sUrl)
                        .put("search_alt", s.optString("search_alt", ""))
                        .put("clicks", 1).put("sku", "")
                        .put("product_id", s.optInt("product_id", 0)));
                }
                r.ourTotal = plan.optDouble("total", 0);
                if (plan.has("basket") && !plan.isNull("basket")) r.basket = plan.optInt("basket", r.basket);
                if (!plan.optString("stamp", "").isEmpty()) r.stamp = plan.optString("stamp", "");
                for (int i = 0; i < r.lines.length(); i++) {
                    JSONObject line = r.lines.optJSONObject(i);
                    String sku = line == null ? "" : line.optString("sku", "");
                    if (sku.isEmpty()) continue;
                    r.want.put(sku, r.want.optInt(sku, 0) + Math.max(1, line.optInt("clicks", 1)));
                    r.names.put(sku, line.optString("name", ""));
                }
                then.run();
            } catch (Exception e) {
                failRun(r, "Сервер ответил непонятно: " + e.getMessage());
            }
        });
    }

    /** Нашей корзины в этой сети нет — проверить сборку на открытой карточке. */
    private void offerThisCard(final CartRun r, String note) {
        String why = note == null || note.isEmpty() ? "у приложения нет карточек этой сети для вашей корзины" : note;
        new AlertDialog.Builder(this)
            .setTitle(r.name + ": класть нечего")
            .setMessage(why + "\n\nМожно проверить сборку на товаре, открытом сейчас: "
                + "приложение нажмёт на нём «В корзину» и прочитает итог корзины сети.")
            .setPositiveButton("Положить этот", (d, w) -> {
                if (!alive(r)) return;
                try {
                    JSONObject line = new JSONObject()
                        .put("name", "товар с открытой страницы")
                        .put("url", web.getUrl() == null ? "" : web.getUrl())
                        .put("clicks", 1).put("sku", "");
                    r.lines = new JSONArray().put(line);
                    nextLine(r);
                } catch (Exception e) {
                    failRun(r, "Не получилось: " + e.getMessage());
                }
            })
            .setNegativeButton("Отмена", (d, w) -> failRun(r, why))
            .setOnCancelListener(d -> failRun(r, why))
            .show();
    }

    private void nextLine(final CartRun r) {
        if (!alive(r)) return;
        if (r.at >= r.lines.length()) {
            // Строки наряда пройдены. У Ленты последний шаг — ссылка на готовую корзину,
            // собранную сервером (Z9): дальше завершение идёт как раньше, через openCart.
            lentaCartLink(r);
            return;
        }
        JSONObject line = r.lines.optJSONObject(r.at);
        final String sku = line == null ? "" : line.optString("sku", "");
        r.target = Math.max(1, line == null ? 1 : line.optInt("clicks", 1));
        // Фоновая сборка кладёт поверх лежащего только недостающее: иначе каждая правка
        // корзины удваивала бы корзину сети. «Собрать» человека кладёт всё, как раньше.
        r.have = r.slot != null && !sku.isEmpty() ? Math.max(0, r.cart.optInt(sku, 0)) : 0;
        r.put = 0;
        r.tries = 0;
        r.cardAgain = false;
        r.how = "tap";
        r.consent = "";
        r.consentRetry = false;
        r.cardPrice = 0;
        r.cardGone = false;
        r.searchPause = false;
        r.badged = 0;
        r.badgeChecked = false;
        if (r.target - r.have <= 0) {
            keptLine(r, line);
            return;
        }
        r.clicks = r.target - r.have;
        // Прибавка к уже лежащему — запросом, только если сеть им СТАВИТ количество
        // (qty_sets): иначе повтор прибавил бы целое число упаковок ещё раз.
        if (r.learned && !sku.isEmpty() && (r.have == 0 || r.rules.optBoolean("qty_sets"))) {
            replayLine(r, line);
            return;
        }
        cardLine(r, line);
    }

    /**
     * ПОСЛЕДНИЙ ШАГ НАРЯДА ЛЕНТЫ — ССЫЛКА НА ГОТОВУЮ КОРЗИНУ (Z9, 21.10.2026).
     *
     * ЗАЧЕМ. Витрина Ленты отвечает телефону 403 (замеры 20–21.10.2026), и строка наряда
     * в корзину не ложится. Сервер умеет собрать эту корзину своим MCP
     * (app/storefind.cart_link) и отдать ссылку: на ней те же позиции с количеством,
     * человеку остаётся нажать одну кнопку. Телефон открывает её последним шагом —
     * когда все строки уже пройдены, — и говорит в отчёте, что увидел: товары или стену.
     *
     * НЕОБЯЗАТЕЛЬНЫЙ ШАГ. Наряд не ждёт сервер: не ответил за CARTLINK_WAIT_MS — идём
     * читать корзину сети, как раньше. Любой отказ — пометка в отчёте, и только.
     */
    private void lentaCartLink(final CartRun r) {
        if (!alive(r)) return;
        if (!"lenta".equals(r.code) || r.linkAsked) {
            endOfRun(r);
            return;
        }
        r.linkAsked = true;
        // Позиции наряда как есть: имя, артикул, количество и фасовка. Сервер сам ищет
        // артикул Ленты по имени и берёт только точное совпадение (app/storefind.py).
        JSONObject body = new JSONObject();
        try {
            JSONArray items = new JSONArray();
            for (int i = 0; i < r.lines.length(); i++) {
                JSONObject line = r.lines.optJSONObject(i);
                if (line == null) continue;
                String name = line.optString("name", "");
                if (name.isEmpty()) continue;
                JSONObject one = new JSONObject().put("name", name);
                int productId = line.optInt("product_id", 0);
                if (productId > 0) one.put("product_id", productId);
                double qty = line.optDouble("qty", 0);
                if (qty > 0) one.put("qty", qty);
                double pack = line.optDouble("pack_g", 0);
                if (pack > 0) one.put("pack_g", pack);
                items.put(one);
            }
            body.put("store", r.code).put("items", items);
        } catch (Exception ignored) { }
        status(r, r.name + ": прошу сервер собрать ссылку на корзину", false);
        // Срок на всю ссылку: завершение наряда важнее последнего шага.
        final Runnable fallback = () -> endOfRun(r);
        handler.postDelayed(fallback, CARTLINK_WAIT_MS);
        call("/api/device/cartlink", body, (code, text) -> {
            handler.removeCallbacks(fallback);
            if (!alive(r) || r.linkDone) return;
            JSONObject answer = new JSONObject();
            try { answer = new JSONObject(text); } catch (Exception ignored) { }
            String url = answer.optString("url", "");
            if (code != 200 || !answer.optBoolean("ok") || url.isEmpty()) {
                String why = code == 200 ? answer.optString("why", "") : serverError(code, text);
                if (why.isEmpty()) why = answer.optString("note", "");
                if (why.isEmpty()) why = "сервер не дал ссылку";
                linkNote(r, "Лента: сервер не собрал ссылку — " + why);
                endOfRun(r);
                return;
            }
            openCartLink(r, url);
        });
    }

    /** Ссылку собрали: открыть её и посмотреть, пустил ли сайт с этого подключения. */
    private void openCartLink(final CartRun r, final String url) {
        if (!alive(r)) return;
        if (!isHostOf(r.chain, url)) {
            linkNote(r, "Лента: сервер не собрал ссылку — адрес не Ленты");
            endOfRun(r);
            return;
        }
        go(r, url);
        whenReady(r, url, null, SETTLE_MS, () -> {
            if (!alive(r)) return;
            // СТЕНА ПЕРВОЙ: 403 и «Доступ запрещён» — это не пустая корзина, и слова
            // страницы у них свои. Совсем без слов, но и без товаров — тот же итог.
            r.view.evaluateJavascript(WALL_JS, wallAnswer -> {
                if (!alive(r)) return;
                String wall = wallAnswer == null ? "" : wallAnswer.replace("\"", "").trim();
                if (!wall.isEmpty()) {
                    linkNote(r, CARTLINK_BLOCKED);
                    endOfRun(r);
                    return;
                }
                r.view.evaluateJavascript(CART_LINK_JS, shown -> {
                    if (!alive(r)) return;
                    boolean goods = shown != null && "1".equals(shown.replace("\"", "").trim());
                    linkNote(r, goods ? CARTLINK_VISIBLE : CARTLINK_BLOCKED);
                    endOfRun(r);
                });
            });
        });
    }

    /** Последний шаг наряда отработал: дальше — прежнее завершение, чтение корзины сети. */
    private void endOfRun(final CartRun r) {
        if (r == null || r.linkDone) return;
        r.linkDone = true;
        if (!alive(r)) return;
        openCart(r);
    }

    /**
     * Пометка последнего шага — в note последней строки отчёта: её видит сервер.
     * Строк ещё нет (наряд не дал ни одной) — пометка остаётся в r.linkNote.
     */
    private static void linkNote(CartRun r, String note) {
        if (r == null || note == null || note.isEmpty()) return;
        r.linkNote = note;
        JSONObject last = r.items.length() == 0 ? null : r.items.optJSONObject(r.items.length() - 1);
        if (last == null) return;
        String old = last.optString("note", "");
        try {
            last.put("note", old.isEmpty() ? note : old + "; " + note);
        } catch (Exception ignored) { }
    }

    /** Позиция уже лежит в корзине сети, сколько нужно: её не трогаем, и это не отказ. */
    private void keptLine(final CartRun r, JSONObject line) {
        try {
            r.items.put(new JSONObject()
                .put("name", line == null ? "" : line.optString("name", ""))
                .put("sku", line == null ? "" : line.optString("sku", ""))
                .put("ok", true).put("why", "").put("put", 0).put("times", r.target)
                .put("how", "kept").put("calls", new JSONArray()));
        } catch (Exception ignored) { }
        r.at++;
        status(r, "уже лежит: " + (line == null ? "" : line.optString("name", "")), false);
        handler.post(() -> nextLine(r));
    }

    /** Позиция касаниями: открыть её карточку и нажать «В корзину» (у лежащей — «+»). */
    private void cardLine(final CartRun r, JSONObject line) {
        if (!alive(r)) return;
        String search = line == null ? "" : line.optString("search", "");
        if (!search.isEmpty()) {
            searchLine(r, line, search);
            return;
        }
        String url = line == null ? "" : line.optString("url", "");
        r.how = "tap";
        if (url.isEmpty() || !isHostOf(r.chain, url)) {
            lineDone(r, false, url.isEmpty() ? "у позиции нет адреса карточки"
                : "адрес карточки принадлежит не этой сети", new JSONArray());
            return;
        }
        status(r, "кладу " + (r.at + 1) + " из " + r.lines.length() + ": " + line.optString("name", ""), false);
        final String open = r.view.getUrl() == null ? "" : r.view.getUrl();
        final String want = pathOf(url);
        if (url.equals(open) || (isHostOf(r.chain, open) && want != null && want.equals(pathOf(open)))) {
            // Карточка уже открыта («Положить этот» или сборка начата с неё): перезагрузка
            // только сбила бы её. Тот же путь с другими параметрами — та же карточка, а
            // whenReady принял бы уходящую страницу за пришедшую: путь у них один, и
            // вышло бы «нет заголовка» (замер на эмуляторе 18.10.2026, Магнит).
            handler.postDelayed(() -> onCard(r), 300);
            return;
        }
        go(r, url);
        whenReady(r, url, null, SETTLE_MS, () -> onCard(r));
    }

    /**
     * Найти позицию поиском самой сети. Там, где сервер к витрине не пробивается
     * (Пятёрочка и Самокат отвечают отказом и с сервера, и с домашнего адреса — замер
     * 18.10.2026), ищет телефон человека: он в этой сети обычный покупатель.
     *
     * Порядок: спросить сервер (у Магнита, Ленты и Дикси он ходит рабочими дорогами,
     * app/storefind.py) → открыть страницу поиска наряда → взять первую карточку →
     * сверить её название с названием позиции → дальше обычный путь карточки (касание
     * «В корзину», проверка, отчёт). Адрес найденной карточки уходит в отчёте, и сервер
     * заводит товар в единой базе — со следующего наряда эта сеть его знает.
     */
    private void searchLine(final CartRun r, final JSONObject line, final String searchUrl) {
        if (!alive(r)) return;
        if (!isHostOf(r.chain, searchUrl)) {
            lineDone(r, false, "адрес поиска принадлежит не этой сети", new JSONArray());
            return;
        }
        // СНАЧАЛА СЕРВЕР, ПОТОМ ВИТРИНА. Телефон упирается в стены сети: Лента и Дикси
        // отвечают 403, Магнит отдаёт пустую SPA (замеры 20–21.10.2026). Те же сети
        // сервер обходит рабочими дорогами (app/storefind.py), поэтому строку без
        // подтверждённого артикула сначала спрашиваем у него. Ответ точный — сразу
        // открываем найденную карточку; отказ — решаем по сети: Ленте страница поиска
        // всё равно не откроется, а Магнит и Дикси идут прежним резервом.
        if (serverFind(r.code)) {
            JSONObject body = new JSONObject();
            try {
                body.put("store", r.code);
                body.put("name", line.optString("name", ""));
                double pack = line.optDouble("pack_g", 0);
                if (pack > 0) body.put("pack_g", pack);
            } catch (Exception ignored) { }
            status(r, "спрашиваю сервер: " + line.optString("name", ""), false);
            call("/api/device/find", body, (code, text) -> {
                if (!alive(r)) return;
                JSONObject answer = new JSONObject();
                try { answer = new JSONObject(text); } catch (Exception ignored) { }
                JSONArray found = answer.optJSONArray("found");
                JSONObject first = found == null || found.length() == 0 ? null : found.optJSONObject(0);
                String url = first == null ? "" : first.optString("url", "");
                // АРТИКУЛ СЕТИ ИЗ ОТВЕТА СЕРВЕРА (21.10.2026). Строка наряда-поиска приходит
                // с пустым `sku`: сервер достаёт артикул из адреса карточки уже после отчёта
                // (app/catalog/fromurl.py). У Дикси старый формат `dixy.ru/product/<слаг>`
                // числа не несёт, и строка не заводится в каталоге — а `found[0].sku` ответа
                // этот артикул знает (app/storefind.py `_candidate`). Кладём его в строку,
                // `lineDone` уносит его в отчёт. Пустой не пишем: строку не портим.
                String foundSku = first == null ? "" : first.optString("sku", "");
                if (code == 200 && answer.optBoolean("ok") && !url.isEmpty()) {
                    try {
                        line.put("url", url).put("search", "");
                        if (!foundSku.isEmpty()) line.put("sku", foundSku);
                        // ИМЯ С КАРТОЧКИ СЕТИ (21.10.2026). `name` строки — НАЗВАНИЕ НАШЕЙ
                        // позиции из наряда, и в каталог сети оно попадать не должно: Дикси
                        // зовёт товар «Туалетная бумага Papia Балийский цветок», а у нас он
                        // «Papia Bali Flower» — база решила бы, что сеть называет его так же.
                        // Кладём имя найденной карточки отдельным полем; `name` не трогаем —
                        // он нужен отчёту и сверке карточки. Пустое не пишем.
                        String foundName = first.optString("name", "");
                        if (!foundName.isEmpty()) line.put("found_name", foundName);
                    } catch (Exception ignored) { }
                    noteLine(line, "нашёл через сервер (уверенность "
                        + String.format(new Locale("ru"), "%.2f", first.optDouble("confidence", 0)) + ")");
                    status(r, "нашёл на сервере: " + line.optString("name", ""), false);
                    // Дальше — обычная логика карточки: адрес, заголовок, кнопка, отчёт.
                    cardLine(r, line);
                    return;
                }
                String why = code == 200 ? serverWhy(answer)
                    : "сервер: " + serverError(code, text);
                if (code == 200 && answer.optBoolean("ok") && url.isEmpty()) {
                    why = "сервер: нашёл товар, но не дал адреса карточки";
                }
                if (code == 200 && "lenta".equals(r.code)) {
                    // У Ленты сервер — единственная рабочая дорога: страница поиска
                    // ответит 403, и открывать её значит показать человеку стену.
                    lineDone(r, false, why, new JSONArray());
                    return;
                }
                if (!why.contains("ищется телефоном") && !why.contains("серверного поиска нет")) {
                    noteLine(line, why);
                    try {
                        line.put("server_why", why);
                    } catch (Exception ignored) { }
                }
                status(r, "ищу в сети: " + line.optString("name", ""), false);
                go(r, searchUrl);
                whenReady(r, searchUrl, null, SETTLE_MS, () -> firstCard(r, line));
            });
            return;
        }
        status(r, "ищу в сети: " + line.optString("name", ""), false);
        go(r, searchUrl);
        whenReady(r, searchUrl, null, SETTLE_MS, () -> firstCard(r, line));
    }

    /**
     * Сети, у которых поиск умеет делать сервер (app/storefind.py): Магнит — webgate,
     * Лента — свой MCP, Дикси — движок подсказок. У Пятёрочки и Самоката серверного
     * поиска нет: их ищет только телефон, и звать find для них незачем.
     */
    private static final String[] SERVER_FIND = {"magnit", "lenta", "dixy"};

    private static boolean serverFind(String code) {
        for (String c : SERVER_FIND) {
            if (c.equals(code)) return true;
        }
        return false;
    }

    /**
     * Причина отказа сервера словами: готовая `hint` ответа, а без неё — `why` и
     * `difference` лучшего кандидата. Помечаем «сервер: …»: в отчёте видно, что это
     * сказала наша дверь, а не сеть.
     */
    private static String serverWhy(JSONObject answer) {
        // ГОТОВАЯ ФРАЗА СЕРВЕРА — ПЕРВОЙ. В `hint` сервер уже собрал из фактов то,
        // что человеку читать: «марка совпала, фасовка неизвестна; … — проверьте на
        // экране „Связи“» (app/storefind.py, _hint). Приклеивать к ней `why` и
        // `difference` — сказать одно и то же дважды. При точном совпадении сервер
        // отдаёт hint пустым, и работает прежняя склейка.
        String hint = answer == null ? "" : answer.optString("hint", "").trim();
        if (!hint.isEmpty()) return "сервер: " + hint;
        String why = answer == null ? "" : answer.optString("why", "").trim();
        String difference = "";
        JSONArray found = answer == null ? null : answer.optJSONArray("found");
        JSONObject best = found == null || found.length() == 0 ? null : found.optJSONObject(0);
        if (best != null) difference = best.optString("difference", "").trim();
        if (!difference.isEmpty() && !why.contains(difference)) {
            why = why.isEmpty() ? difference : why + " (отличие: " + difference + ")";
        }
        if (why.isEmpty()) why = "сервер не нашёл точного совпадения";
        return "сервер: " + why;
    }

    /** Первая карточка на странице поиска: адрес и текст. */
    private static final String FIRST_CARD_JS =
        "(function(){var a=document.querySelector('a[href*=\"/product\"]');"
        + "return a?JSON.stringify({u:a.getAttribute('href')||'',t:(a.innerText||'').slice(0,240)}):'';})()";

    /**
     * Сколько ждать карточки поиска: витрина рисует их скриптами, а не разметкой.
     * 20 с — один проход; не дождались — askCard делает ещё один такой же проход по
     * той же странице и только потом говорит «ничего не нашёл».
     */
    private static final int SEARCH_LIMIT_MS = 20000;

    /**
     * ПАУЗА МЕЖДУ ПОИСКАМИ. Живой прогон 21.10.2026 (APK 83 401): Магнит после
     * серии поисков (пять позиций по два запроса) показал стену «Выключите VPN.
     * И всё заработает» — похоже, сеть ограничивает частоту запросов. После
     * неудачи поиска (пустая выдача, стена, чужой товар) следующую позицию
     * начинаем не сразу, а через SEARCH_PAUSE_MS. Ждём через handler.postDelayed,
     * а не Thread.sleep: поток интерфейса не должен замирать.
     */
    private static final int SEARCH_PAUSE_MS = 1800;

    /**
     * Сколько наряд ждёт серверную ссылку на корзину Ленты (Z9). Последний шаг
     * необязательный: не ответил — наряд всё равно кончается чтением корзины сети.
     * 8 с — заметно меньше 15 с connect-таймаута телефона, и человек не ждёт зря.
     */
    private static final int CARTLINK_WAIT_MS = 8000;

    /** Что телефон говорит в отчёте о серверной ссылке Ленты — словами владельца. */
    private static final String CARTLINK_VISIBLE = "корзина собрана сервером, товары видны";
    private static final String CARTLINK_BLOCKED =
        "корзина собрана сервером, сайт не пустил с этого подключения";

    /**
     * ТОВАРЫ НА СЕРВЕРНОЙ ССЫЛКЕ (Z9). Лента собирает корзину ссылкой, и человеку
     * остаётся нажать на странице одну кнопку. С телефона эта страница иногда
     * открывается, иногда отвечает 403 (замеры 20–21.10.2026). Поэтому телефон не
     * верит, что «ссылка есть», а смотрит, что на ней: слова корзины, товара или
     * цену в рублях. Пусто — сайт страницу не отдал; стену ловит WALL_JS раньше.
     */
    private static final String CART_LINK_JS = "(function(){try{" +
        "var t=((document.body?document.body.innerText:'')+' '+(document.title||''));" +
        "return /корзин|товар|₽/i.test(t)?'1':'';}catch(e){return '';}})()";

    /**
     * Первая карточка поиска. Имя сверяем с названием позиции: у чужого товара кнопку не
     * жмём — иначе в корзину человека лёг бы не тот товар, а отчёт назвал бы его нашим.
     *
     * ЖДЁМ КАРТОЧКУ, А НЕ ЧИТАЕМ ОДИН РАЗ. Живой прогон 19.10.2026 (нажатие «Собрать»
     * на Магните): поиск открылся, а карточек в разметке не было ни одной — отчёт сказал
     * «поиск сети ничего не нашёл» по всем четырём позициям. При этом тот же адрес поиска
     * отвечает страницей в 40 КБ, где ссылок `/product/` нет вовсе даже на слово «молоко»:
     * витрина рисует выдачу своими скриптами. Одна проверка сразу после перехода их не
     * застаёт, поэтому спрашиваем раз в полсекунды, пока карточка не покажется.
     */
    private void firstCard(final CartRun r, JSONObject line) {
        askCard(r, line, SystemClock.uptimeMillis() + SEARCH_LIMIT_MS, false);
    }

    /**
     * СТЕНА ПО АДРЕСУ, А НЕ «НЕТ КНОПКИ». Замер 20.10.2026: с эмулятора Магнит показал
     * «Выключите VPN. И всё заработает», а отчёт писал «поиск сети ничего не нашёл» или
     * «на карточке не нашлось кнопки „в корзину"». По такому тексту человек не понимает,
     * что дело не в товаре и не в кнопке, а в том, что сеть не пускает этот адрес.
     */
    private static final String WALL_JS = "(function(){" +
        "var t=((document.body?document.body.innerText:'')+' '+(document.title||'')).toLowerCase();" +
        // 403 И «ДОСТУП ЗАПРЕЩЁН» — ЭТО НЕ ПУСТАЯ ВЫДАЧА. Живой прогон 21.10.2026:
        // Лента ответила «403 Error Forbidden Доступ к сайту lenta.com запрещен»,
        // Дикси — «Forbidden … Apache Server at dixy.ru Port 443», а на карточке —
        // «Не удалось загрузить сайт. Возможно, у вас включён ВПН». По этим словам
        // причина должна говорить про блокировку, а не про «ничего не нашёл».
        "if(/403\\s*error|error\\s*403|http 403|forbidden/.test(t))" +
        "  return 'blocked';" +
        "if(/доступ к сайту[\\s\\S]{0,80}запрещ/.test(t))" +
        "  return 'blocked';" +
        "if(/не удалось загрузить сайт|возможно, у вас включён впн|возможно, у вас включен впн/.test(t))" +
        "  return 'blocked';" +
        "if(/выключите vpn|включите vpn|отключите vpn|включён vpn|включен vpn/.test(t))" +
        "  return 'vpn';" +
        "if(/не робот|captcha|проверка безопасности|подтвердите, что вы/.test(t))" +
        "  return 'captcha';" +
        "if(/выберите адрес|введите адрес|адрес доставки|куда доставить/.test(t))" +
        "  return 'address';" +
        "if(/войдите|авторизуйтесь|вход в личный кабинет|личный кабинет/.test(t))" +
        "  return 'login';" +
        "return '';})()";

    /**
     * «ИСКАТЬ В КАТАЛОГЕ» ВМЕСТО ВЫДАЧИ. Живой прогон 21.10.2026 (APK 80 453): Магнит
     * открыл страницу поиска, а карточек на ней не было — витрина предложила «искать в
     * каталоге». Для человека это не пустой экран и не стена: сеть не нашла по запросу
     * и предлагает каталог. После попытки короткого search_alt причина должна называть
     * именно это, иначе человеку снова непонятно, почему товара нет.
     */
    private static final String CATALOG_JS = "(function(){" +
        "var t=(document.body?document.body.innerText:'').toLowerCase();" +
        "return /искать в каталоге|ничего не найдено|ничего не нашлось|нет подходящих товаров/.test(t)?'1':'';})()";

    /**
     * БАННЕР СОГЛАСИЯ ПОВЕРХ КАРТОЧКИ. Живой прогон 20.10.2026: у Самоката карточку
     * накрыл баннер, и сборка сказала «страница не похожа на карточку товара: нет
     * заголовка (рядом: «Соглашаюсь»)», хотя карточка была открыта. Перед проверками
     * карточки жмём первую видимую кнопку ровно из списка согласия. Слова сравниваем
     * по отдельности — innerText, aria-label, title, — регистр не важен, пробелы
     * нормализованы. «Оплатить», «Войти», «Подписаться», «Оформить» не трогаем: это
     * не согласие, а действие человека.
     */
    private static final String CONSENT_JS =
        "(function(){try{"
      + "var OK=['соглашаюсь','согласен','принять','принимаю','принять все','хорошо','понятно','разрешить','accept','i agree','allow all'];"
      + "var NO=/оплат|войти|вход|подпис|оформ/;"
      + "function norm(s){return String(s||'').replace(/\\s+/g,' ').trim().toLowerCase();}"
      + "function pick(e){var v=[norm(e.innerText),norm(e.getAttribute('aria-label')),norm(e.getAttribute('title'))];"
      + " for(var i=0;i<v.length;i++){if(!v[i]||v[i].length>40||NO.test(v[i]))continue;"
      + "  for(var k=0;k<OK.length;k++)if(v[i]===OK[k])return v[i];}return '';}"
      + "var els=[].slice.call(document.querySelectorAll('button,[role=button],a'));"
      + "for(var j=0;j<els.length;j++){var e=els[j];if(e.disabled)continue;"
      + " var r=e.getBoundingClientRect();if(r.width<=0||r.height<=0||r.bottom<=0||r.top>=(window.innerHeight||100000))continue;"
      + " var t=pick(e);if(!t)continue;e.click();return t;}"
      + "return '';}catch(e){return '';}})()";

    /**
     * ПРЕДЕЛ ПРИЧИНЫ ОТКАЗА. why уезжает на сервер, а он режет её до 200 знаков
     * (app/phonecart.py). Режем здесь же, чтобы дамп страницы не терялся на полпути
     * и чтобы в отчёте была ровно одна, уже короткая причина.
     */
    private static final int WHY_LIMIT = 200;

    /**
     * ЧТО ВИДИТ ТЕЛЕФОН НА СТРАНИЦЕ. Живой прогон 21.10.2026 (APK 78 693): причины уже
     * называют запрос («искали «Баранка самокат 300 г»») и закрывают баннер согласия,
     * но по ним не понять, что на странице Магнита, Ленты, Самоката и Дикси — пустая
     * выдача, стена, чужой экран или карточка, которую витрина ещё не дорисовала.
     *
     * Только заголовок (60), путь (60) и первые слова текста (120); пробелы и переносы
     * схлопнуты. Ни формы, ни куки, ни значения полей не читаются, никуда не шлётся:
     * это строка причины, а не сбор данных.
     */
    private static final String PAGE_JS =
        "(function(){try{"
      + "function sp(s){return String(s||'').replace(/\\s+/g,' ').trim();}"
      + "var t=sp(document.title),p=sp(location.pathname);"
      + "var x=sp(document.body?document.body.innerText:'');"
      + "return JSON.stringify({t:t.slice(0,60),p:p.slice(0,60),x:x.slice(0,120)});"
      + "}catch(e){return JSON.stringify({t:'',p:'',x:''});}})()";

    /**
     * Сказать словами, что сеть не пускает этот адрес.
     *
     * КОНТЕКСТ ВАЖЕН. «Выключите VPN» после серии поисков — это не про мобильный
     * интернет, а про то, что сеть ограничивает частоту запросов (живой прогон
     * 21.10.2026, Магнит: пять позиций по два запроса — и стена). В поиске и на
     * карточке так и говорим: «ограничивает частоту запросов — попробуйте позже».
     * Прежний текст про мобильный интернет остаётся предпроверке сети (blankWall):
     * там стена означает, что окно не пускают вовсе.
     *
     * `frequent` — проверка вызвана поиском или карточкой, а не предпроверкой сети.
     */
    private String wallText(String name, String wall, boolean frequent) {
        if (wall.contains("blocked")) {
            return name + ": сеть не пускает этот адрес (403) — попробуйте позже "
                + "или с другого подключения.";
        }
        if (wall.contains("address")) {
            return name + ": сеть просит выбрать адрес — выберите его в окне сети и нажмите «Собрать» снова.";
        }
        if (wall.contains("login")) {
            return name + ": сеть просит войти — войдите в окно сети и нажмите «Собрать» снова.";
        }
        if (wall.contains("vpn")) {
            return frequent
                ? name + ": сеть ограничивает частоту запросов — попробуйте позже."
                : name + ": сеть не пускает этот адрес — показывает «Выключите VPN». "
                    + "Включите мобильный интернет и попробуйте снова.";
        }
        return name + ": сеть просит пройти проверку — пройдите её в окне и попробуйте снова.";
    }

    /** Стена в предпроверке сети: прежний текст про мобильный интернет. */
    private String wallText(String name, String wall) {
        return wallText(name, wall, false);
    }

    /**
     * Дамп страницы в объект {t, p, x}. Зовётся ТОЛЬКО из причины отказа: успех страницу
     * не читает. Ничего не отправляет — это строка для отчёта.
     */
    private void pageDump(final CartRun r, final Consumer<JSONObject> cb) {
        if (!alive(r) || cb == null) return;
        r.view.evaluateJavascript(PAGE_JS, value -> {
            if (!alive(r)) return;
            cb.accept(asObject(value));
        });
    }

    /** Дамп словами: «заголовок» путь «первые слова». Пустая страница — пустая строка. */
    private static String pageWords(JSONObject dump) {
        if (dump == null) return "";
        String t = oneSpace(dump.optString("t", ""), 60);
        String p = oneSpace(dump.optString("p", ""), 60);
        String x = oneSpace(dump.optString("x", ""), 120);
        if (t.isEmpty() && p.isEmpty() && x.isEmpty()) return "";
        StringBuilder w = new StringBuilder();
        if (!t.isEmpty()) w.append('«').append(t).append('»');
        if (!p.isEmpty()) {
            if (w.length() > 0) w.append(' ');
            w.append(p);
        }
        if (!x.isEmpty()) {
            if (w.length() > 0) w.append(' ');
            w.append('«').append(x).append('»');
        }
        return w.toString();
    }

    /** Пробелы и переносы — в один пробел, и не длиннее max: строку читает человек. */
    private static String oneSpace(String text, int max) {
        String s = text == null ? "" : text.replaceAll("\\s+", " ").trim();
        if (s.length() > max) s = s.substring(0, max);
        return s;
    }

    /** Отказ поиска с дампом: «…: искали «…», на странице «t» p «x»». */
    private static String searchWhy(String why, JSONObject dump) {
        String page = pageWords(dump);
        String base = why == null ? "" : why;
        return fitWhy(page.isEmpty() ? base : base + ", на странице " + page);
    }

    /** Отказ карточки с дампом и кнопками рядом: «… (на странице …, рядом: «…»)». */
    private static String cardWhy(String why, JSONObject dump, JSONArray near) {
        StringBuilder out = new StringBuilder(why == null ? "" : why);
        String page = pageWords(dump);
        String round = quoted(near);
        if (!page.isEmpty()) {
            out.append(" (на странице ").append(page);
            if (!round.isEmpty()) out.append(", рядом: ").append(round);
            out.append(')');
        } else if (!round.isEmpty()) {
            out.append(" (рядом: ").append(round).append(')');
        }
        return fitWhy(out.toString());
    }

    /** Слова в кавычках через запятую — как их видно в отчёте. */
    private static String quoted(JSONArray words) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; words != null && i < words.length(); i++) {
            out.append(i > 0 ? ", " : "").append('«').append(words.optString(i)).append('»');
        }
        return out.toString();
    }

    /** Не длиннее WHY_LIMIT: сервер режет why до 200, и обрезанное видно многоточием. */
    private static String fitWhy(String why) {
        String w = why == null ? "" : why;
        if (w.length() <= WHY_LIMIT) return w;
        return w.substring(0, WHY_LIMIT - 1) + "…";
    }

    /** Проверить страницу на стену и назвать причину по-человечески, иначе — обычную. */
    private void checkLineOrWall(final CartRun r, String why) {
        if (!alive(r)) return;
        r.view.evaluateJavascript(WALL_JS, value -> {
            if (!alive(r)) return;
            String wall = value == null ? "" : value.replace("\"", "").trim();
            checkLine(r, wall.isEmpty() ? why : wallText(r.name, wall, true));
        });
    }

    /**
     * Предварительная проверка легла: подряд карточки без заголовка. Что на странице —
     * вход, проверка, стена или просто чужой экран: по дампу это видно, а отказ называет
     * шаг человека. Живой прогон 21.10.2026: Самокат отказал общим «показывает не карточки
     * товара — возможно, просит войти или проверку» и не показал ни строчки о странице.
     */
    private void blankWall(final CartRun r, final String plain, final JSONObject dump,
                           final boolean empty) {
        if (!alive(r)) return;
        r.view.evaluateJavascript(WALL_JS, value -> {
            if (!alive(r)) return;
            String wall = value == null ? "" : value.replace("\"", "").trim();
            String why;
            if (empty) {
                // Самокат, прогон 21.10.2026: прямой адрес карточки открылся, заголовок
                // пуст, текста меньше сорока знаков — страница не отдалась вовсе (пусто
                // или бот-защита). Это не «просит войти»: вход тут ничего не изменит.
                why = r.name + ": страница карточки не отдалась (пусто/бот-защита) — "
                    + "откройте её сами и проверьте.";
            } else if ("login".equals(wall) || "captcha".equals(wall)) {
                why = r.name + " просит войти или проверку — войдите в окне сети "
                    + "и нажмите «Собрать» снова.";
            } else if (!wall.isEmpty()) {
                why = wallText(r.name, wall);
            } else {
                why = plain;
            }
            String page = pageWords(dump);
            if (!page.isEmpty()) why = why + " (на странице " + page + ")";
            endBackground(r, "failed", fitWhy(why));
        });
    }

    private void askCard(final CartRun r, JSONObject line, final long until, final boolean retried) {
        if (!alive(r)) return;
        r.view.evaluateJavascript(FIRST_CARD_JS, value -> {
            if (!alive(r)) return;
            JSONObject card = asObject(value);
            String href = card.optString("u", "");
            if (href.isEmpty()) {
                if (SystemClock.uptimeMillis() < until) {
                    handler.postDelayed(() -> askCard(r, line, until, retried), READY_POLL_MS);
                    return;
                }
                // Один повторный проход по той же странице: витрина могла дорисовать выдачу
                // позже срока. Второй раз не повторяем, иначе ожидание станет бесконечным.
                if (!retried) {
                    status(r, "ищу ещё раз: " + line.optString("name", ""), false);
                    askCard(r, line, SystemClock.uptimeMillis() + SEARCH_LIMIT_MS, true);
                    return;
                }
                // Поиск идёт по странице сети: если страница — стена («Выключите VPN»,
                // 403), то «ничего не нашёл» неверно. Спрашиваем страницу, что она такое.
                r.view.evaluateJavascript(WALL_JS, wallAnswer -> {
                    if (!alive(r)) return;
                    String wall = wallAnswer == null ? "" : wallAnswer.replace("\"", "").trim();
                    if (!wall.isEmpty()) {
                        lineDone(r, false, wallText(r.name, wall, true), new JSONArray());
                        return;
                    }
                    // ПУСТАЯ ВЫДАЧА — ПРОБУЕМ КОРОТКИЙ ЗАПРОС. Живой прогон 21.10.2026:
                    // Магнит на полный запрос не нашёл ничего («— искать в каталоге»).
                    // Второй адрес (search_alt) пробуем ровно один раз: флаг живёт в
                    // строке наряда, а неудачу второго запроса назовёт notFoundWhy.
                    String alt = line.optString("search_alt", "");
                    if (!alt.isEmpty() && !line.optBoolean("search_alt_tried", false)
                            && isHostOf(r.chain, alt)) {
                        try {
                            line.put("search_alt_tried", true);
                        } catch (Exception ignored) { }
                        status(r, "ищу по короткому запросу: " + line.optString("name", ""), false);
                        go(r, alt);
                        whenReady(r, alt, null, SETTLE_MS, () -> firstCard(r, line));
                        return;
                    }
                    // «— ИСКАТЬ В КАТАЛОГЕ» — сеть не нашла по запросу и предлагает каталог.
                    // Это не пустая страница: причина должна называть именно это. Дамп
                    // покажет и остальное, что телефон видел на странице.
                    r.view.evaluateJavascript(CATALOG_JS, catAnswer -> {
                        if (!alive(r)) return;
                        String mark = catAnswer == null ? "" : catAnswer.replace("\"", "").trim();
                        final String base = mark.equals("1")
                            ? "сеть не нашла по запросу и предлагает каталог"
                            : "поиск сети ничего не нашёл за " + (SEARCH_LIMIT_MS / 1000) + " с";
                        pageDump(r, dump -> lineDone(r, false,
                            searchWhy(notFoundWhy(line, base), dump), new JSONArray()));
                    });
                });
                return;
            }
            String full = href.startsWith("http") ? href
                : (href.startsWith("/") ? CHAINS[r.chain][2].replaceAll("/+$", "") + href : "");
            if (full.isEmpty() || !isHostOf(r.chain, full)) {
                lineDone(r, false, "поиск сети вернул чужой адрес", new JSONArray());
                return;
            }
            if (!resembles(line.optString("name", ""), card.optString("t", ""))) {
                // Первый запрос принёс не тот товар: у строки наряда есть второй, короткий
                // адрес поиска. Пробуем его один раз — по нему сеть чаще понимает имя.
                String alt = line.optString("search_alt", "");
                if (!alt.isEmpty() && !line.optBoolean("search_alt_tried", false)
                        && isHostOf(r.chain, alt)) {
                    try {
                        line.put("search_alt_tried", true);
                    } catch (Exception ignored) { }
                    status(r, "ищу по короткому запросу: " + line.optString("name", ""), false);
                    go(r, alt);
                    whenReady(r, alt, null, SETTLE_MS, () -> firstCard(r, line));
                    return;
                }
                lineDone(r, false, "поиск сети нашёл не тот товар", new JSONArray());
                return;
            }
            try {
                line.put("url", full).put("search", "");
                if (line.optBoolean("search_alt_tried", false)) {
                    line.put("note", "нашёл по короткому запросу");
                }
            } catch (Exception ignored) { }
            cardLine(r, line);
        });
    }

    /** Похоже ли название позиции на текст карточки: общее слово от четырёх букв. */
    private static boolean resembles(String name, String text) {
        if (name.isEmpty() || text.isEmpty()) return false;
        String lower = text.toLowerCase();
        for (String word : name.toLowerCase().split("[^\\p{L}\\p{N}]+")) {
            if (word.length() >= 4 && lower.contains(word)) return true;
        }
        return false;
    }

    /**
     * СТРОГАЯ СВЕРКА НАЗВАНИЯ — ДОПОЛНЕНИЕ К resembles(), А НЕ ЗАМЕНА.
     *
     * Живой прогон 21.10.2026 (APK 83 401, Пятёрочка): «Сушка самокат со сгущенным
     * молоком 300 г» привела на ЧУЖОЙ товар «Сушки Невская сушка Простые 300г».
     * Вес совпал (300 г), поэтому проверка фасовки промолчала, а resembles()
     * пропустил по общему слову «сушка» — чужой товар лёг бы в корзину владельца.
     *
     * Значимое слово — от пяти букв, кроме общих продуктовых: «молоко», «сушка»,
     * «хлеб» и подобных. Сравниваем корни: «молоком» тоже общее слово, «сушка»
     * покрывает «сушки». В заголовке карточки должно найтись хотя бы одно значимое
     * слово, а если их два и больше — хотя бы два. Ни одного — карточка чужая.
     * Заглушка «товар с открытой страницы» — не товар наряда, её не сверяем.
     */
    private static final String[] COMMON_WORDS = {
        "молок", "сушк", "хлеб", "сыр", "бумаг", "вод", "чай", "кофе", "масл",
        "соль", "сахар", "мука", "мяс", "куриц", "рыб", "яйц", "кефир", "йогурт",
        "творог", "сметан", "сливк"
    };

    /** Слово — общее продуктовое: сравниваем по корню, а не целиком («молоком»). */
    private static boolean commonWord(String word) {
        for (String root : COMMON_WORDS) {
            if (word.startsWith(root)) return true;
        }
        return false;
    }

    /** Значимые слова названия: от пяти букв и не общие продуктовые, без повторов. */
    private static List<String> significantWords(String name) {
        List<String> out = new ArrayList<>();
        String lower = name == null ? "" : name.toLowerCase(Locale.ROOT).replace('ё', 'е');
        for (String word : lower.split("[^\\p{L}\\p{N}]+")) {
            if (word.length() < 5 || commonWord(word)) continue;
            if (!out.contains(word)) out.add(word);
        }
        return out;
    }

    /** Есть ли значимое слово в заголовке: целиком или по первым пяти буквам («сгущённое»). */
    private static boolean significantIn(String word, String card) {
        if (card.contains(word)) return true;
        return word.length() > 5 && card.contains(word.substring(0, 5));
    }

    /**
     * Причина отказа по названию или пустая строка, если карточка та же.
     * Возвращает «на карточке другой товар: в наряде «…», на карточке «…»».
     */
    private static String nameWhy(JSONObject line, String title) {
        if (line == null) return "";
        String name = line.optString("name", "");
        if (name.isEmpty() || "товар с открытой страницы".equals(name)) return "";
        String card = title == null ? "" : title.toLowerCase(Locale.ROOT).replace('ё', 'е');
        if (card.isEmpty()) return "";
        List<String> sig = significantWords(name);
        if (sig.isEmpty()) return "";
        // Одного значимого слова мало, если их два и больше: «самокат» без
        // «сгущенным» — уже другой товар.
        int need = sig.size() >= 2 ? 2 : 1;
        int have = 0;
        for (String word : sig) {
            if (significantIn(word, card)) have++;
        }
        if (have >= need) return "";
        return "на карточке другой товар: в наряде «" + name + "», на карточке «"
            + title + "»";
    }

    /**
     * ФАСОВКА В ЗАГОЛОВКЕ КАРТОЧКИ. Живой разбор 21.10.2026: у Магнита на нашей
     * полке стоит другая линейка того же бренда (Parmalat Natura Premium Comfort),
     * у Ленты — Parmalat Edge. Короткий запрос «Молоко Parmalat» приводит на любую
     * из них, а resembles() видит только общие слова — в корзину владельца лёг бы
     * другой товар, за который он заплатит. Поэтому перед касанием «В корзину»
     * сверяем вес упаковки из наряда (pack_g) с числом и единицей в заголовке.
     *
     * Порядок альтернатив важен: «кг» и «гр» стоят раньше «г», иначе «1 кг»
     * разобралось бы как 1 грамм. «1,8% 1 л» даёт литр, а не процент: число с «%»
     * единицей не сопровождается и в фасовку не идёт. «8 рулонов» фасовки не даёт
     * вовсе — рулоны не граммы.
     */
    private static final String PACK_RE =
        "(\\d+(?:[.,]\\d+)?)\\s*(кг|kg|гр|мл|ml|г|g|л|l)(?![а-яёa-z])";

    private static final Pattern PACK = Pattern.compile(
        PACK_RE, Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);

    /**
     * ДОПУСК ФАСОВКИ. Десятая часть — общее правило, но не шире 40 г: соседние
     * пачки молока отличаются на 50 г (950 и 1000), и 10 % от килограмма пропустили
     * бы чужую линейку. 30 г в заголовке — это округление или нетто, а не другая
     * упаковка (1000 и 1030 — одна).
     */
    private static final double PACK_PART = 0.10;
    private static final double PACK_NOISE_G = 40;

    /**
     * ЦЕНА В ТЕКСТЕ КАРТОЧКИ. У Пятёрочки, Самоката и Дикси телефон жмёт «В корзину»
     * прямо на карточке, ответа сети с корзиной нет — `r.prices` пуст, и раньше цена
     * не уходила в отчёт вовсе (живой прогон 21.10.2026: строка завелась с
     * price = NULL, in_stock = NULL). Подпись та же, что у серверной `_CARD_PRICE`
     * (app/phonecart.py): «205 00 ₽», «1 748,98 ₽», «299.99 руб». Название товара
     * кончается там, где она начинается.
     */
    private static final String CARD_PRICE_RE =
        "(\\d[\\d\\s]{0,8}(?:[.,]\\d{1,2})?)\\s*(?:₽|руб)";

    private static final Pattern CARD_PRICE = Pattern.compile(
        CARD_PRICE_RE, Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);

    /** Чем карточка говорит «товара нет». Слова витрин, а не догадка. */
    private static final String[] NO_STOCK_WORDS = {"нет в наличии", "больше нет", "отсутств"};

    /**
     * Цена за упаковку из текста карточки; 0 — цены не видно.
     *
     * «1 748,98 ₽» — 1748.98: пробел разделяет тысячи, запятая — копейки.
     * «205 00 ₽» — 205.00: точки нет, поэтому последняя группа ровно из двух цифр
     * читается как копейки, а не как тысячи (в «1 748» группа из трёх — тысячи).
     * «8 рулонов» цены не даёт: денежной подписи в тексте нет. Не умножаем ни на что:
     * цена — за упаковку, количество сервер знает из наряда.
     *
     * БЕРЁМ ПОСЛЕДНЮЮ ЦЕНУ. У товара со скидкой в карточке лежат две
     * («629 00 ₽ 299 99 ₽»), и платит человек вторую — то же правило, что у
     * CARDS_JS и tools/browser_cards.parse_text_card. Первая была бы ценой до скидки.
     */
    private static double cardPrice(String text) {
        if (text == null || text.isEmpty()) return 0;
        Matcher m = CARD_PRICE.matcher(text.replace('\u00a0', ' '));
        double price = 0;
        while (m.find()) {
            String raw = m.group(1).trim();
            if (raw.indexOf(',') >= 0 || raw.indexOf('.') >= 0) {
                raw = raw.replace(" ", "").replace(',', '.');
            } else {
                String[] parts = raw.split("\\s+");
                if (parts.length > 1 && parts[parts.length - 1].length() == 2) {
                    StringBuilder whole = new StringBuilder();
                    for (int i = 0; i < parts.length - 1; i++) whole.append(parts[i]);
                    raw = whole.append('.').append(parts[parts.length - 1]).toString();
                } else {
                    raw = raw.replace(" ", "");
                }
            }
            try {
                double value = Double.parseDouble(raw);
                if (value > 0) price = Math.round(value * 100) / 100.0;
            } catch (Exception ignored) { }
        }
        return price;
    }

    /** Сказала ли карточка, что товара нет: «Нет в наличии», «Больше нет», «отсутствует». */
    private static boolean cardGone(String text) {
        if (text == null || text.isEmpty()) return false;
        String low = text.toLowerCase(Locale.ROOT).replace('ё', 'е');
        for (String word : NO_STOCK_WORDS) {
            if (low.contains(word)) return true;
        }
        return false;
    }

    /** Граммы одной упаковки из строки: «950 г» → 950, «1 кг» → 1000, нет — 0. */
    private static double packGrams(String text) {
        if (text == null || text.isEmpty()) return 0;
        Matcher m = PACK.matcher(text.replace('\u00a0', ' '));
        String value = "";
        String unit = "";
        // Последнее число с единицей: в «Молоко 1,8% 1 л» процент идёт раньше объёма.
        while (m.find()) {
            value = m.group(1);
            unit = m.group(2);
        }
        if (value.isEmpty()) return 0;
        try {
            double number = Double.parseDouble(value.replace(',', '.'));
            String lower = unit.toLowerCase(Locale.ROOT);
            return ("кг".equals(lower) || "kg".equals(lower)
                || "л".equals(lower) || "l".equals(lower)) ? number * 1000 : number;
        } catch (Exception e) {
            return 0;
        }
    }

    /**
     * Сверять ли фасовку у этой строки. Заглушка «товар с открытой страницы» —
     * не товар наряда, веса у неё нет. Весовой товар (unit = kg) продаётся на
     * развес: фасовка на 100 г у него не пачка, и сравнение только мешало бы.
     */
    private static boolean packChecked(JSONObject line) {
        if (line == null) return false;
        if ("товар с открытой страницы".equals(line.optString("name", ""))) return false;
        if ("kg".equalsIgnoreCase(line.optString("unit", ""))) return false;
        return line.optDouble("pack_g", 0) > 0;
    }

    /** Допуск расхождения фасовки в граммах: десятая часть веса, но не шире 40 г. */
    private static double packTolerance(double want) {
        return Math.min(want * PACK_PART, PACK_NOISE_G);
    }

    /** Причина отказа по фасовке или пустая строка, если сошлась или не разобрана. */
    private static String packWhy(JSONObject line, String title) {
        if (line == null) return "";
        double want = line.optDouble("pack_g", 0);
        if (want <= 0) return "";
        double card = packGrams(title);
        if (card <= 0) return "";
        if (Math.abs(card - want) <= packTolerance(want)) return "";
        return "карточка другого веса/объёма: в наряде " + grams(want)
            + " г, на карточке " + grams(card) + " г";
    }

    /** Целые граммы для отчёта: 950.0 → 950, 1000.0 → 1000. */
    private static long grams(double value) {
        return Math.round(value);
    }

    /** Пометка к успешной строке: не повторять её, если карточка проверяется снова. */
    private static void noteLine(JSONObject line, String note) {
        if (line == null || note == null || note.isEmpty()) return;
        String old = line.optString("note", "");
        if (old.contains(note)) return;
        try {
            line.put("note", old.isEmpty() ? note : old + "; " + note);
        } catch (Exception ignored) { }
    }

    /**
     * «Поиск ничего не нашёл» с фактическим запросом. Живой прогон 20.10.2026: Магнит и
     * Лента дали этот отказ по всем позициям, и по отчёту нельзя было понять, что ушло
     * в сеть и пробовали ли второй адрес (search_alt). Теперь в причине видно и имя
     * позиции, и запрос из адреса поиска.
     */
    private static String notFoundWhy(JSONObject line, String base) {
        String name = line == null ? "" : line.optString("name", "");
        String asked = searchQuery(line == null ? "" : line.optString("search", ""));
        if (asked.isEmpty()) asked = name;
        StringBuilder why = new StringBuilder(base);
        if (!asked.isEmpty()) why.append(": искали «").append(asked).append('»');
        if (!asked.isEmpty() && !name.isEmpty() && !asked.equals(name)) {
            why.append(" (позиция «").append(name).append("»)");
        }
        if (line != null && line.optBoolean("search_alt_tried", false)) {
            // ФАКТИЧЕСКИЙ ВТОРОЙ ЗАПРОС. Живой прогон 21.10.2026: в отчёте «второй
            // запрос «…»» повторял ПЕРВЫЙ — при пустом разборе адреса alt подменялся
            // именем позиции, а имя и есть первый запрос. Теперь запрос берётся из
            // адреса search_alt; не разобрался — так и говорим, без подмены.
            String alt = searchQuery(line.optString("search_alt", ""));
            if (alt.isEmpty()) {
                why.append(", второй запрос тоже пуст");
            } else {
                why.append(", второй запрос «").append(alt).append("» тоже пуст");
            }
        }
        return why.toString();
    }

    /** Запрос, которым искали: параметр text/q/search/query/term/value из адреса поиска сети. */
    private static String searchQuery(String url) {
        if (url == null || url.isEmpty()) return "";
        try {
            String query = new URL(url).getQuery();
            if (query == null || query.isEmpty()) return "";
            for (String pair : query.split("&")) {
                int eq = pair.indexOf('=');
                String key = eq < 0 ? pair : pair.substring(0, eq);
                if (!"text".equals(key) && !"q".equals(key) && !"search".equals(key)
                        && !"query".equals(key) && !"term".equals(key)
                        && !"value".equals(key)) continue;
                return URLDecoder.decode(eq < 0 ? "" : pair.substring(eq + 1), "UTF-8");
            }
        } catch (Exception ignored) { }
        return "";
    }

    /**
     * Перейти по адресу, пометив уходящую страницу. Помета нужна whenReady: путь у
     * старой и новой страницы может совпасть (корзину открываем заново), и без неё
     * уходящая страница сошла бы за пришедшую.
     */
    private void go(CartRun r, String url) {
        r.view.evaluateJavascript("window.__kzStale=1", null);
        r.view.loadUrl(url);
    }

    /** Где страница и в каком она виде: путь, readyState, есть ли h1 и не уходящая ли она. */
    private static final String STATE_JS =
        "(function(){return JSON.stringify({p:location.pathname,s:document.readyState,h:!!document.querySelector('h1'),o:!!window.__kzStale});})()";

    /** Сколько ждать смены адреса после касания «Корзины»: дальше корзина — окно на месте. */
    private static final int SAME_PAGE_MS = 6000;

    /**
     * Дождаться страницы и дать ей улечься.
     *
     * ПО АДРЕСУ, А НЕ ПО onPageStarted. Замер на эмуляторе 17.10.2026 (Магнит): после
     * loadUrl прежняя страница живёт ещё около 3 с, и readyState «complete» у неё свой.
     * Сборка принимала её за новую и искала кнопку не там — «нет заголовка» у всех трёх
     * карточек. Теперь ждём, пока путь страницы станет тем, куда шли (want), а после
     * касания «Корзины» — пока он сменится с прежнего (from). Годна страница с
     * readyState «complete» или уже с заголовком h1: у Магнита заголовок есть через
     * ~5 с, а «complete» — только через ~11 из-за счётчиков. Не дождались за
     * READY_LIMIT_MS — идём дальше. Страница, помеченная уходящей (go), не годится.
     */
    private void whenReady(final CartRun r, final String want, final String from,
                           final int settle, final Runnable then) {
        final String target = pathOf(want);
        final String before = from == null ? null : trimSlash(from);
        final long started = SystemClock.uptimeMillis();
        final long until = started + READY_LIMIT_MS;
        handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                if (!alive(r)) return;
                final Runnable again = this;
                r.view.evaluateJavascript(STATE_JS, value -> {
                    if (!alive(r)) return;
                    JSONObject st = asObject(value);
                    String path = trimSlash(st.optString("p", ""));
                    String state = st.optString("s", "");
                    long now = SystemClock.uptimeMillis();
                    boolean here = !path.isEmpty() && !st.optBoolean("o") && (target != null ? target.equals(path)
                        : (before == null || !before.equals(path) || now - started > SAME_PAGE_MS));
                    boolean usable = "complete".equals(state)
                        || ("interactive".equals(state) && st.optBoolean("h"));
                    if ((here && usable) || now > until) handler.postDelayed(then, settle);
                    else handler.postDelayed(again, READY_POLL_MS);
                });
            }
        }, READY_POLL_MS);
    }

    private static String pathOf(String url) {
        if (url == null || url.isEmpty()) return null;
        try {
            return trimSlash(new URL(url).getPath());
        } catch (Exception e) {
            return null;
        }
    }

    private static String trimSlash(String path) {
        String p = path == null ? "" : path;
        while (p.length() > 1 && p.endsWith("/")) p = p.substring(0, p.length() - 1);
        return p;
    }

    private void onCard(final CartRun r) {
        if (!alive(r)) return;
        // Сначала согласие, потом карточка: у Самоката баннер накрывал её, и проверка
        // «нет заголовка» срабатывала на живой карточке (живой прогон 20.10.2026).
        dismissConsent(r, () -> {
            if (!alive(r)) return;
            if (!r.consent.isEmpty() && !r.consentRetry) {
                // Самокат, прогон 21.10.2026: баннер закрылся, а карточка ещё перерисовывалась,
                // и проверка сразу после согласия падала. Один повторный заход через
                // CONSENT_WAIT_MS; только его неудача становится отказом (с дампом страницы).
                r.consentRetry = true;
                handler.postDelayed(() -> onCard(r), CONSENT_WAIT_MS);
                return;
            }
            r.view.evaluateJavascript(watchScript(r), null);
            tapAdd(r, r.have > 0);
        });
    }

    /**
     * Закрыть баннер согласия, если он есть, и только потом проверять карточку. Нажатие
     * настоящее, глазами страницы (click), но выбор узкий: только слова согласия из
     * CONSENT_JS. Ждём CONSENT_WAIT_MS: после согласия витрина перерисовывает карточку.
     */
    private void dismissConsent(final CartRun r, final Runnable then) {
        if (!alive(r)) return;
        r.view.evaluateJavascript(CONSENT_JS, value -> {
            if (!alive(r)) return;
            String hit = value == null ? "" : value.replace("\"", "").trim();
            if (hit.isEmpty()) {
                then.run();
                return;
            }
            r.consent = "закрыл баннер согласия";
            status(r, r.consent, false);
            handler.postDelayed(then, CONSENT_WAIT_MS);
        });
    }

    private void tapAdd(final CartRun r, final boolean again) {
        if (!alive(r)) return;
        r.view.evaluateJavascript(pickScript(r, again), value -> {
            if (!alive(r)) return;
            JSONObject got = asObject(value);
            // ЦЕНА И НАЛИЧИЕ — С КАРТОЧКИ, А НЕ ИЗ ОТВЕТА КОРЗИНЫ. У Пятёрочки, Самоката и
            // Дикси ответа с корзиной нет, и `r.prices` пуст; цена видна только здесь.
            // Наличие «нет» тоже говорит карточка. Оба поля — на одну позицию и сбрасываются
            // в nextLine, чтобы не переехать на следующую.
            r.cardPrice = cardPrice(got.optString("text", ""));
            if (r.cardPrice <= 0) r.cardPrice = cardPrice(got.optString("title", ""));
            r.cardGone = got.optBoolean("gone")
                || cardGone(got.optString("text", "")) || cardGone(got.optString("title", ""));
            if (got.optBoolean("h1") || got.optBoolean("found")) r.blank = 0;
            if (got.optBoolean("gone")) {
                lineDone(r, false, "карточка говорит, что товара нет в наличии", new JSONArray());
                return;
            }
            if (!got.optBoolean("found")) {
                // СТРАНИЦА НЕ ОТДАЛАСЬ — НЕ «ПРОСИТ ВОЙТИ». Самокат, прогон 21.10.2026:
                // прямой адрес карточки открылся, но заголовка нет, текста меньше
                // сорока знаков — пустая отдача или бот-защита. Отличаем это от входа
                // по признаку empty из PICK_JS (текст короче 40 знаков или пустой title).
                final boolean emptyPage = r.put == 0 && got.optBoolean("empty");
                // Кнопка ещё не нарисована: через VPN компьютера у Магнита полоса покупки
                // с «В корзину» появлялась позже заголовка, и обе карточки ушли в «не
                // нашлось кнопки», хотя через 10 с она была на месте (эмулятор, 18.10.2026).
                // То же со счётчиком «−  1 шт  +» после первого касания: он встаёт на место
                // «В корзину» не сразу, и вторая упаковка шла в «кнопки „ещё одну“ не нашлось».
                if (r.tries < PICK_TRIES) {
                    r.tries++;
                    handler.postDelayed(() -> tapAdd(r, again), PICK_RETRY_MS);
                    return;
                }
                if (again && r.put == 0 && r.have > 0 && got.optBoolean("addable")) {
                    // Прибавляли к лежащему, а на карточке «В корзину»: в корзине сети этой
                    // позиции нет. Память — в сторону, кладём её целиком.
                    JSONObject line = r.lines.optJSONObject(r.at);
                    String sku = line == null ? "" : line.optString("sku", "");
                    if (!sku.isEmpty()) {
                        r.cart.remove(sku);
                        saveLedger(r);
                    }
                    r.have = 0;
                    r.clicks = r.target;
                    r.tries = 0;
                    tapAdd(r, false);
                    return;
                }
                if (!again && r.put == 0 && got.optBoolean("inCart")) {
                    // Вместо «В корзину» — «−  2 шт  +»: товар уже лежит в корзине сети.
                    // Класть его «В корзину» ещё раз — удвоить. Сколько лежит, сказал счётчик:
                    // хватает — позиция лежит; не хватает — прибавим его «+».
                    JSONObject line = r.lines.optJSONObject(r.at);
                    String sku = line == null ? "" : line.optString("sku", "");
                    r.have = Math.max(Math.max(1, r.have), packs(got.optDouble("count", 1)));
                    if (!sku.isEmpty()) {
                        try {
                            r.cart.put(sku, r.have);
                        } catch (Exception ignored) { }
                        saveLedger(r);
                    }
                    if (r.target - r.have <= 0 || r.slot == null) {
                        // У «Собрать» человека — как раньше: лежащее не трогаем.
                        keptLine(r, line);
                        return;
                    }
                    r.clicks = r.target - r.have;
                    r.tries = 0;
                    tapAdd(r, true);
                    return;
                }
                if (!got.optBoolean("h1") && r.put == 0 && r.slot != null && ++r.blank >= BLANK_LIMIT) {
                    // Две карточки подряд без заголовка — сеть показывает не товары: просит
                    // войти, проверку «я не робот» или «Выключите VPN». Это шаг человека.
                    // Живой прогон 21.10.2026: Самокат отказал общим «показывает не карточки
                    // товара» и не показал страницу — теперь причина берёт дамп и, если
                    // страница просит вход или проверку, говорит, что именно сделать.
                    final String plain = r.name + " показывает не карточки товара — "
                        + "возможно, просит войти или пройти проверку. Откройте его сами.";
                    pageDump(r, dump -> blankWall(r, plain, dump, emptyPage));
                    return;
                }
                // Карточка открылась, а кнопки за все попытки не нашлось: один раз
                // перезагружаем карточку и ищем снова. Витрины дорисовывают полосу покупки
                // позже заголовка; второй раз не перезагружаем — иначе отказ не наступит.
                if (!r.cardAgain && !again && r.put == 0 && got.optBoolean("h1")) {
                    JSONObject line = r.lines.optJSONObject(r.at);
                    String here = line == null ? "" : line.optString("url", "");
                    if (!here.isEmpty() && isHostOf(r.chain, here)) {
                        r.cardAgain = true;
                        r.tries = 0;
                        status(r, "кнопки нет — перечитываю карточку", false);
                        go(r, here);
                        whenReady(r, here, null, SETTLE_MS, () -> onCard(r));
                        return;
                    }
                }
                String why = r.put == 0
                    ? (got.optBoolean("h1") ? (again ? "на карточке не нашлось кнопки «+»"
                                                     : "на карточке не нашлось кнопки «в корзину»")
                                            : "страница не похожа на карточку товара: нет заголовка")
                    : "положено " + r.put + " из " + r.clicks + ": кнопки «ещё одну» не нашлось";
                // Пустая отдача карточки называется своим текстом, а не общим «нет
                // заголовка»: вход и проверку это не лечит. Дамп страницы — рядом.
                if (emptyPage) why = "страница карточки не отдалась (пусто/бот-защита)";
                // Какие кнопки были в блоке покупки — по ним видно, как сеть зовёт свою.
                JSONArray near = got.optJSONArray("near");
                if (r.put == 0) {
                    // Что именно видит телефон на карточке: без дампа «нет заголовка» и
                    // «нет кнопки» одинаковы у стены, пустой страницы и недорисованной карточки.
                    final String base = why;
                    final boolean blankPage = emptyPage;
                    pageDump(r, dump -> {
                        String text = cardWhy(base, dump, near);
                        if (blankPage) lineDone(r, false, text, new JSONArray());
                        else checkLineOrWall(r, text);
                    });
                    return;
                }
                if (near != null && near.length() > 0) why += " (рядом: " + quoted(near) + ")";
                checkLineOrWall(r, why);
                return;
            }
            // СЧЁТЧИК КОРЗИНЫ ДО ПЕРВОГО КАСАНИЯ. Если товар уже лежал, PICK_JS назвал
            // число («−  2 шт  +») — после нажатия оно должно вырасти; иначе 0. На
            // повторных касаниях той же позиции прежний счётчик не перезаписываем.
            if (r.put == 0) {
                r.badged = got.optBoolean("inCart") ? got.optInt("count", 0) : 0;
            }
            // СТРОГАЯ СВЕРКА НАЗВАНИЯ. Даже похожее название и совпавший вес не
            // гарантируют тот же товар: «Сушка самокат со сгущенным молоком» и «Сушки
            // Невская сушка Простые» — оба 300 г, общее слово «сушка». В заголовке
            // карточки должны найтись значимые слова наряда (одно, а если их два и
            // больше — два). Не нашлись — кнопку не жмём, причина с дампом страницы.
            if (r.put == 0) {
                JSONObject nameLine = r.lines.optJSONObject(r.at);
                String other = nameWhy(nameLine, got.optString("title", ""));
                if (!other.isEmpty()) {
                    final String nameBase = other;
                    pageDump(r, dump -> lineDone(r, false,
                        cardWhy(nameBase, dump, got.optJSONArray("near")), new JSONArray()));
                    return;
                }
            }
            // ФАСОВКА. Даже похожее название не гарантирует ту же линейку: у Магнита
            // Parmalat Natura Premium Comfort вместо Edge, у Ленты — Edge. Сверяем вес
            // упаковки с заголовком ДО касания: не сошлось — кнопку не жмём, строка
            // уходит в отчёт с дампом страницы. Нет веса на карточке — не блокируем,
            // но помечаем успешную строку. Проверяем только перед первым касанием:
            // вторая упаковка той же позиции идёт на ту же карточку.
            if (r.put == 0) {
                JSONObject packLine = r.lines.optJSONObject(r.at);
                if (packChecked(packLine)) {
                    String packTitle = got.optString("title", "");
                    String different = packWhy(packLine, packTitle);
                    if (!different.isEmpty()) {
                        final String packBase = different;
                        pageDump(r, dump -> lineDone(r, false,
                            cardWhy(packBase, dump, got.optJSONArray("near")), new JSONArray()));
                        return;
                    }
                    if (packGrams(packTitle) <= 0) {
                        noteLine(packLine, "вес на карточке не указан");
                    }
                }
            }
            if (got.optBoolean("wide")) {
                // Широкий поиск нашёл кнопку по общему слову («Купить», «Добавить»).
                // Такую жмём, только если заголовок карточки похож на название позиции:
                // иначе это чужой товар — плитка или рекомендация, — и в корзину лёг бы он.
                JSONObject line = r.lines.optJSONObject(r.at);
                String name = line == null ? "" : line.optString("name", "");
                String card = got.optString("title", "");
                if (!name.isEmpty() && !"товар с открытой страницы".equals(name)
                        && !resembles(name, card)) {
                    StringBuilder why = new StringBuilder("кнопка «в корзину» есть, но карточка не похожа на «")
                        .append(name).append("»");
                    // Что на карточке на самом деле: первые видимые кнопки для отчёта.
                    JSONArray vis = got.optJSONArray("near");
                    if (vis != null && vis.length() > 0) {
                        why.append(" (на карточке: ");
                        for (int i = 0; i < vis.length() && i < 3; i++) {
                            why.append(i > 0 ? ", " : "").append('«').append(vis.optString(i)).append('»');
                        }
                        why.append(')');
                    }
                    lineDone(r, false, why.toString(), new JSONArray());
                    return;
                }
            }
            // Прокрутке к кнопке нужно встать, прежде чем спрашивать, где кнопка.
            handler.postDelayed(() -> tapMarked(r), 450);
        });
    }

    private void tapMarked(final CartRun r) {
        if (!alive(r)) return;
        r.view.evaluateJavascript(COORDS_JS, value -> {
            if (!alive(r)) return;
            if (!tapAt(r.view, asObject(value))) {
                // Сайт перерисовал блок покупки между поиском кнопки и касанием — найти её снова.
                if (r.tries < PICK_TRIES) {
                    r.tries++;
                    handler.postDelayed(() -> tapAdd(r, r.put > 0 || r.have > 0), 600);
                    return;
                }
                checkLine(r, r.put == 0 ? "кнопка «в корзину» ушла из-под пальца — карточка перерисовалась"
                                        : "положено " + r.put + " из " + r.clicks + ": карточка перерисовалась");
                return;
            }
            r.put++;
            r.tries = 0;
            int pause = r.rules.optInt("click_pause_ms", 700);
            if (r.put < r.clicks) handler.postDelayed(() -> tapAdd(r, true), pause + 500);
            else handler.postDelayed(() -> checkLine(r, ""), pause + 1200);
        });
    }

    /**
     * ПРИЗНАК КОРЗИНЫ НА СТРАНИЦЕ. Живой прогон 21.10.2026 (Магнит, молоко neo):
     * «кнопка нажалась, а сеть не обратилась к своей корзине — не легло». Сеть может
     * принять запрос, но товар в корзину не положить. После «положено» один раз
     * смотрим бейдж/счётчик корзины в шапке и слова «в корзине»/«товар в корзине».
     * Счётчик до касания запомнен (r.badged): не вырос и слов нет — не легло.
     */
    private static final String CART_SIGN_JS =
        "(function(){try{"
      + "var t=(document.body?document.body.innerText:'').toLowerCase().replace(/ё/g,'е');"
      + "var on=/товар в корзине|уже в корзине|в корзине/.test(t);"
      + "var n=0,known=false;"
      + "var els=[].slice.call(document.querySelectorAll('[class*=badge],[class*=Badge],[class*=count],[class*=Count],[class*=cart],[class*=Cart],[aria-label*=\"корзин\" i],[data-testid*=cart]'));"
      + "for(var i=0;i<els.length;i++){var s=(els[i].innerText||'').replace(/\\s+/g,' ').trim();"
      + " if(!s||s.length>16)continue;var m=s.match(/(\\d{1,4})/);if(!m)continue;"
      + " var v=parseInt(m[1],10);if(v>n)n=v;known=true;}"
      + "return JSON.stringify({known:known,n:n,on:on});"
      + "}catch(e){return JSON.stringify({known:false,n:0,on:false});}})()";

    /** Один раз после касания: вырос ли признак корзины. Не вырос — отказ с дампом. */
    private void checkCartSign(final CartRun r, final JSONArray calls) {
        if (!alive(r)) return;
        r.view.evaluateJavascript(CART_SIGN_JS, value -> {
            if (!alive(r)) return;
            JSONObject sign = asObject(value);
            boolean known = sign.optBoolean("known");
            int n = sign.optInt("n", 0);
            boolean on = sign.optBoolean("on");
            if (known && !on && n <= r.badged) {
                final String base = "карточка открылась, кнопка нажата, но сеть не показала товар в корзине";
                pageDump(r, dump -> lineDone(r, false, cardWhy(base, dump, null), calls));
                return;
            }
            verifyLine(r, calls);
        });
    }

    /**
     * Что сеть сделала после касаний. Последнее слово — за её запросами к корзине:
     * все отказали — не легло; у Магнита ни одного запроса — тоже не легло (замер
     * 20.09.2026, правило SENDS_CART серверной передачи). Иначе судит число касаний.
     */
    private void checkLine(final CartRun r, final String failure) {
        checkLine(r, failure, new JSONArray(), false);
    }

    /**
     * `waited` — уже ждали запоздалый ответ сети. Через VPN ответ на касание приходит
     * дольше, чем мы ждём после него: «+» томатного сока лёг, а сборка сказала «сеть не
     * обратилась к своей корзине» (эмулятор, 18.10.2026). Поэтому, не увидев ни одного
     * запроса, спрашиваем ещё раз через CALLS_WAIT_MS.
     */
    private void checkLine(final CartRun r, final String failure, final JSONArray before, final boolean waited) {
        if (!alive(r)) return;
        r.view.evaluateJavascript(CALLS_JS, value -> {
            if (!alive(r)) return;
            JSONArray got = asObject(value).optJSONArray("calls");
            final JSONArray calls = new JSONArray();
            for (int i = 0; i < before.length(); i++) calls.put(before.opt(i));
            for (int i = 0; got != null && i < got.length(); i++) calls.put(got.opt(i));
            if (failure.isEmpty() && calls.length() == 0 && !waited && r.put > 0) {
                handler.postDelayed(() -> checkLine(r, failure, calls, true), CALLS_WAIT_MS);
                return;
            }
            boolean anyOk = false;
            int refused = 0;
            for (int i = 0; i < calls.length(); i++) {
                String c = calls.optString(i, "");
                int code = 0;
                try { code = Integer.parseInt(c.substring(c.lastIndexOf(' ') + 1)); } catch (Exception ignored) { }
                if (code >= 200 && code < 400) anyOk = true;
                else if (code >= 400 && refused == 0) refused = code;
            }
            boolean ok = failure.isEmpty() && r.put >= r.clicks;
            String why = failure;
            if (ok && !anyOk && refused > 0) {
                ok = false;
                why = "сеть отказала на запрос к корзине: " + refused;
            } else if (ok && calls.length() == 0 && r.rules.optBoolean("sends_cart")) {
                ok = false;
                why = "кнопка нажалась, а сеть не обратилась к своей корзине — не легло";
            }
            if (!ok) {
                lineDone(r, false, why, calls);
                return;
            }
            // Признак корзины — один раз на позицию, после «положено». Сеть могла
            // принять запрос, но не показать товар в корзине (Магнит neo, 21.10.2026).
            if (r.put > 0 && !r.badgeChecked) {
                r.badgeChecked = true;
                checkCartSign(r, calls);
                return;
            }
            verifyLine(r, calls);
        });
    }

    /**
     * Сверить, ЧТО легло: артикул в запросе записи сети против артикула наряда. Сеть
     * назвала другой — позиция не легла, а в корзине сети лишний товар, и человеку
     * сказано, что его убрать. Учиться по такому запросу нельзя.
     */
    private void verifyLine(final CartRun r, final JSONArray calls) {
        JSONObject line = r.lines.optJSONObject(r.at);
        final String sku = line == null ? "" : line.optString("sku", "");
        if (sku.isEmpty()) {
            lineDone(r, true, "", calls);
            return;
        }
        r.view.evaluateJavascript(VERIFY_JS.replace("%s", JSONObject.quote(sku)), value -> {
            if (!alive(r)) return;
            JSONObject got = asObject(value);
            if (got.optBoolean("known") && !got.optBoolean("match")) {
                JSONArray ids = got.optJSONArray("ids");
                String other = ids != null && ids.length() > 0 ? ids.optString(0) : "?";
                lineDone(r, false, "лёг другой товар (артикул " + other + " вместо " + sku
                    + ") — уберите его из корзины сети", calls);
                return;
            }
            lineDone(r, true, "", calls);
        });
    }

    private void lineDone(final CartRun r, boolean ok, String why, JSONArray calls) {
        // Не все касания удались («положено 1 из 2: кнопки „ещё одну“ не нашлось»), но
        // сделанные легли: память телефона должна их знать, иначе следующая сборка
        // положила бы эту упаковку ещё раз (ВкусВилл на эмуляторе, 18.10.2026). Не в счёт —
        // отказ сети, чужой товар и касание, на которое сеть не ответила своей корзиной.
        String w = why == null ? "" : why;
        boolean taken = ok || ("tap".equals(r.how) && r.put > 0 && !w.startsWith("сеть отказала")
            && !w.startsWith("лёг другой") && !w.startsWith("кнопка нажалась"));
        lineDone(r, ok, why, calls, taken);
    }

    /** `taken` — сеть приняла упаковки этой позиции (и та, что «нет в наличии», лежит в её корзине). */
    private void lineDone(final CartRun r, boolean ok, String why, JSONArray calls, boolean taken) {
        if (!alive(r)) return;
        JSONObject line = r.lines.optJSONObject(r.at);
        final String sku = line == null ? "" : line.optString("sku", "");
        final String how = r.how;
        // Баннер согласия, закрытый на карточке этой позиции, виден в отчёте: у успеха —
        // в note, у отказа — в why. Иначе человек не поймёт, почему карточка стала видна.
        String say = why == null ? "" : why;
        if (!ok && !r.consent.isEmpty()) say = r.consent + ": " + say;
        // ПРИЧИНА СЕРВЕРНОГО ПОИСКА В ОТКАЗЕ. Магнит и Дикси после отказа сервера идут
        // прежним резервом — страницей поиска, и её причина иначе скрыла бы, что сервер
        // уже смотрел и не нашёл. Ставим её первой: в отчёте она важнее слов страницы.
        String server = !ok && line != null ? line.optString("server_why", "") : "";
        if (!server.isEmpty() && !say.contains(server)) {
            say = say.isEmpty() ? server : server + "; " + say;
        }
        // Итоговая причина отказа — не длиннее WHY_LIMIT (сервер режет до 200): в предел
        // входит и отметка о баннере, иначе дамп страницы терялся бы уже на сервере.
        if (!ok) say = fitWhy(say);
        // ПАУЗА ПОСЛЕ НЕУДАЧИ ПОИСКА. Магнит, прогон 21.10.2026: после серии поисков
        // («пять позиций по два запроса») — стена «Выключите VPN», похоже на ограничение
        // частоты. Следующую строку начинаем через SEARCH_PAUSE_MS. Признак — search
        // ещё не пуст: удачный поиск очищает его перед карточкой, и отказ карточки
        // паузой не наказывается.
        r.searchPause = !ok && line != null && !line.optString("search", "").isEmpty();
        String note = ok && line != null ? line.optString("note", "") : "";
        if (ok && !r.consent.isEmpty()) note = note.isEmpty() ? r.consent : r.consent + "; " + note;
        try {
            JSONObject item = new JSONObject()
                .put("name", line == null ? "" : line.optString("name", ""))
                .put("sku", sku)
                // Адрес карточки: из него сервер достаёт настоящий артикул сети и заводит
                // товар в единой базе. Для Пятёрочки и Самоката это единственная дорога —
                // сервер к ним не пробивается, а телефон человека бывает на витрине.
                .put("url", line == null ? "" : line.optString("url", ""))
                .put("ok", ok).put("why", say)
                // Пометка строки: «нашёл по короткому запросу» — если карточка нашлась
                // вторым, коротким адресом поиска (search_alt). У неудачи причина в why.
                // Сюда же ложится «закрыл баннер согласия», если он был на карточке.
                .put("note", note)
                .put("put", r.put).put("times", r.clicks).put("how", how)
                .put("calls", calls == null ? new JSONArray() : calls);
            r.items.put(item);
            // ИМЯ С КАРТОЧКИ СЕТИ В ОТЧЁТ (21.10.2026). Отдельно от `name`: `name` — наше
            // название позиции из наряда, а сервер учит каталог сети по её собственному
            // имени (`found_name`). Нет имени — ключа нет, старые отчёты не меняются.
            String foundName = line == null ? "" : line.optString("found_name", "");
            if (!foundName.isEmpty()) item.put("found_name", foundName);
            // ЦЕНА ЗА УПАКОВКУ. Ответ сети с корзиной главнее: он называет цену именно
            // той позиции, что легла. Ответа не было (Пятёрочка, Самокат, Дикси) — цена
            // с карточки. Обе нулевые — поля нет: ноль не цена. На количество не умножаем,
            // сервер берёт цену за единицу.
            double one = r.prices.optDouble(sku, 0);
            if (one <= 0) one = r.cardPrice;
            if (one > 0) item.put("price", one);
            // НАЛИЧИЕ. Карточка сказала «нет» — false. Позиция легла — true. Иначе поля
            // нет: «не видно» — не «нет», и выдумывать наличие нельзя.
            if (r.cardGone) item.put("in_stock", false);
            else if (ok && r.put > 0) item.put("in_stock", true);
        } catch (Exception ignored) { }
        if (taken && !sku.isEmpty() && r.put > 0) {
            // Память телефона: сколько этой позиции теперь лежит в корзине сети. По ней
            // следующая сборка положит только недостающее. Касание прибавляет упаковку;
            // запрос сети, которая ставит количество (qty_sets), ставит итоговое число.
            boolean sets = "request".equals(how) && r.rules.optBoolean("qty_sets");
            try {
                r.cart.put(sku, sets ? r.target : r.cart.optInt(sku, 0) + r.put);
            } catch (Exception ignored) { }
            saveLedger(r);
        }
        r.at++;
        r.how = "tap";
        // Первая позиция легла касанием «В корзину» — выучить по ней запрос сети и дальше
        // класть им. По касанию «+» учимся только у сети, чей запрос ставит число
        // (qty_sets): у прочих «ещё одну» бывает другим запросом. Учимся в каждой сборке
        // заново: заголовки запроса (x-permissions-jwt у Магнита) живут недолго — образец
        // трёхминутной давности сеть отвергла кодом 500 (эмулятор, 18.10.2026).
        boolean sets = r.rules.optBoolean("qty_sets");
        if (ok && "tap".equals(how) && (r.have == 0 || sets) && !r.learned && !r.learnTried && !sku.isEmpty()
                && r.at < r.lines.length()) {
            r.learnTried = true;
            // У сети, что ставит число, образец — всегда одна своя позиция: после «+» в теле
            // вся корзина, и повтор с ней положил бы артикул дважды — Магнит ответил 500.
            learn(r, sku, sets);
            return;
        }
        syncCart(r, taken && r.put > 0 ? sku : "", () -> handler.postDelayed(() -> nextLine(r), pauseNext(r)));
    }

    /** Пауза до следующей позиции: запросом — темп запросов сервера, касаниями — темп покупателя. */
    private static int pauseAfter(CartRun r) {
        return r.learned ? Math.max(300, r.rules.optInt("request_pause_ms", 1000))
                         : Math.max(1300, r.rules.optInt("pause_ms", 1300));
    }

    /** Пауза до следующей строки; после неудачи поиска/стены — не меньше SEARCH_PAUSE_MS. */
    private static int pauseNext(CartRun r) {
        int pause = pauseAfter(r);
        return r.searchPause ? Math.max(pause, SEARCH_PAUSE_MS) : pause;
    }

    /**
     * Выучить запрос сети по только что легшей позиции. Не вышло — не беда: остальные
     * позиции лягут касаниями, как раньше, а в строке состояния сказано, почему.
     */
    private void learn(final CartRun r, final String sku, boolean one) {
        r.view.evaluateJavascript(LEARN_JS.replace("%s", JSONObject.quote(sku) + "," + one), value -> {
            if (!alive(r)) return;
            JSONObject got = asObject(value);
            if (got.optBoolean("ok")) {
                r.learned = true;
                status(r, r.name + ": дальше кладу запросом сети " + got.optString("method") + " "
                    + got.optString("path"), false);
            } else {
                status(r, r.name + ": запрос сети не выучен (" + got.optString("why", "?") + "), дальше касаниями", false);
            }
            syncCart(r, sku, () -> handler.postDelayed(() -> nextLine(r), pauseAfter(r)));
        });
    }

    /**
     * Позиция запросом сети: тот же запрос, что сайт отправил после касания, с артикулом
     * и количеством этой позиции. Отправляет его сама страница, со своей сессией.
     * Сеть не приняла — эта же позиция идёт касаниями, и дальше уже только касаниями.
     */
    private void replayLine(final CartRun r, final JSONObject line) {
        final String sku = line.optString("sku", "");
        status(r, "кладу " + (r.at + 1) + " из " + r.lines.length() + " запросом: " + line.optString("name", ""), false);
        final String key = "k" + r.at;
        // Сеть, что ставит количество, получает итоговое число; остальные — недостающее
        // (сюда они доходят только с пустого места, так что это одно и то же).
        final int qty = r.rules.optBoolean("qty_sets") ? r.target : r.clicks;
        JSONObject p = new JSONObject();
        try {
            p.put("sku", sku).put("qty", qty).put("key", key);
        } catch (Exception ignored) { }
        r.view.evaluateJavascript(REPLAY_JS.replace("%s", p.toString()), started -> {
            if (!alive(r)) return;
            final long until = SystemClock.uptimeMillis() + 15000;
            handler.postDelayed(new Runnable() {
                @Override
                public void run() {
                    if (!alive(r)) return;
                    final Runnable again = this;
                    r.view.evaluateJavascript(REPLAY_POLL_JS.replace("%s", JSONObject.quote(key)), value -> {
                        if (!alive(r)) return;
                        JSONObject res = asObject(value);
                        if (res.length() == 0 && SystemClock.uptimeMillis() < until) {
                            handler.postDelayed(again, 300);
                            return;
                        }
                        if (res.optBoolean("ok")) {
                            r.how = "request";
                            r.put = r.clicks;
                            JSONArray calls = new JSONArray();
                            calls.put(res.optString("call", ""));
                            if (res.optBoolean("gone")) {
                                // Запрос сеть приняла, а позицию положила в «нет в наличии».
                                lineDone(r, false, "нет в наличии у сети", calls, true);
                                return;
                            }
                            lineDone(r, true, "", calls);
                            return;
                        }
                        r.learned = false;
                        status(r, r.name + ": сеть не приняла запрос (" + res.optInt("status", 0)
                            + "), дальше касаниями", true);
                        cardLine(r, line);
                    });
                }
            }, 300);
        });
    }

    /**
     * Что лежит в корзине сети на самом деле — из её последнего ответа с корзиной
     * целиком (WATCH_JS, правило full_cart наряда). Человек мог оформить заказ или
     * убрать товар сам: тогда память телефона врёт, а ответ сети — нет. Знакомые нам
     * артикулы (из наряда и памяти) берут количество из ответа; которых в ответе нет —
     * в корзине сети их нет.
     *
     * Ответу верим, только если в нём есть позиция, которую сеть только что приняла
     * (`landed`): иначе это не та корзина или сеть переименовала поля, и «пусто» в нём
     * значило бы положить всё ещё раз.
     */
    private void syncCart(final CartRun r, final String landed, final Runnable then) {
        JSONArray full = r.rules.optJSONArray("full_cart");
        if (full == null || full.length() == 0) {
            then.run();
            return;
        }
        r.view.evaluateJavascript(CART_NOW_JS, value -> {
            if (!alive(r)) return;
            JSONObject got = asObject(value);
            JSONObject found = got.optJSONObject("items");
            long at = got.optLong("at", 0);
            if (found != null && at > r.cartAt && (landed.isEmpty() || found.has(landed))) {
                r.cartAt = at;
                try {
                    JSONObject next = new JSONObject();
                    Iterator<String> mine = r.cart.keys();
                    while (mine.hasNext()) {
                        String sku = mine.next();
                        next.put(sku, packs(found.optDouble(sku, 0)));
                    }
                    Iterator<String> wanted = r.want.keys();
                    while (wanted.hasNext()) {
                        String sku = wanted.next();
                        if (found.has(sku)) next.put(sku, packs(found.optDouble(sku, 0)));
                    }
                    r.cart = next;
                    // Цены из того же ответа: отчёт понесёт их на сервер, а тот положит в
                    // единую базу (app/catalog/refresh.note_device_prices). Нет цен в
                    // ответе — карта остаётся прежней, цена в отчёт не выдумывается.
                    JSONObject costs = got.optJSONObject("prices");
                    if (costs != null) {
                        Iterator<String> keys = costs.keys();
                        while (keys.hasNext()) {
                            String sku = keys.next();
                            double one = costs.optDouble(sku, 0);
                            if (one > 0) r.prices.put(sku, Math.round(one * 100) / 100.0);
                        }
                    }
                } catch (Exception ignored) { }
                saveLedger(r);
            }
            then.run();
        });
    }

    /** Количество из ответа сети — в упаковки: дробное у весового — это одна лежащая позиция. */
    private static int packs(double qty) {
        if (!(qty > 0)) return 0;
        return Math.max(1, (int) Math.round(qty));
    }

    /** Последний ответ сети с корзиной целиком, который видел сторож: {at, items: {артикул: количество}}. */
    private static final String CART_NOW_JS =
        "(function(){var c=window.__kzCart;return c?JSON.stringify(c):'';})()";

    /** Корзина сети пуста? Подписи пустой корзины — из наряда (rules.empty_cart). */
    private static final String EMPTY_JS =
        "(function(re){try{var t=(document.body&&document.body.innerText||'').toLowerCase().replace(/ё/g,'е');"
      + "return new RegExp(re,'i').test(t)?'1':'';}catch(e){return '';}})(%s)";

    /** Открыть корзину сети: своей ссылкой со страницы, кнопкой «Корзина» или адресом из наряда. */
    private void openCart(final CartRun r) {
        if (!alive(r)) return;
        status(r, r.name + ": открываю корзину сети", false);
        r.cartFrom = pathOf(r.view.getUrl());
        JSONArray pages = r.rules.optJSONArray("cart_pages");
        final String page = pages != null && pages.length() > 0 ? pages.optString(0, "") : "";
        if (r.slot != null && !page.isEmpty() && isHostOf(r.chain, page)) {
            // Скрытое окно идёт в корзину прямо по адресу: касаться в нём нечего, а адрес
            // корзины не зависит от того, какая страница открыта.
            go(r, page);
            whenReady(r, page, null, CART_SETTLE_MS, () -> readCart(r));
            return;
        }
        r.view.evaluateJavascript(findCartScript(r), value -> {
            if (!alive(r)) return;
            JSONObject got = asObject(value);
            String href = got.optString("href", "");
            if (!href.isEmpty() && isHostOf(r.chain, href)) {
                go(r, href);
                whenReady(r, href, null, CART_SETTLE_MS, () -> readCart(r));
                return;
            }
            if (got.optBoolean("button")) {
                final String from = pathOf(r.view.getUrl());
                handler.postDelayed(() -> r.view.evaluateJavascript(COORDS_JS, at -> {
                    if (!alive(r)) return;
                    tapAt(r.view, asObject(at));
                    whenReady(r, null, from, CART_SETTLE_MS, () -> readCart(r));
                }), 450);
                return;
            }
            JSONArray near = got.optJSONArray("near");
            if (near != null && near.length() > 0) r.cartHint = near.toString();
            if (!page.isEmpty()) {
                go(r, page);
                whenReady(r, page, null, CART_SETTLE_MS, () -> readCart(r));
                return;
            }
            readCart(r);
        });
    }

    private void readCart(final CartRun r) {
        if (!alive(r)) return;
        status(r, r.name + ": читаю итог корзины сети", false);
        // Сторож нужен и здесь: страница корзины пересчитывает её своими запросами.
        r.view.evaluateJavascript(watchScript(r), null);
        r.view.evaluateJavascript(READ_JS, value -> {
            if (!alive(r)) return;
            final JSONObject cart = asObject(value);
            if (!hasTotal(cart)) {
                String open = r.view.getUrl() == null ? "" : r.view.getUrl();
                // Всё ещё на странице, с которой шли в корзину, — касание её не открыло:
                // ждать дорисовки нечего, сразу адрес корзины (ниже).
                boolean stuck = r.cartFrom != null && r.cartFrom.equals(pathOf(open));
                // Итога нет — страница корзины ещё дорисовывается: у Магнита через VPN
                // эмулятора суммы пришли позже 9 с после заголовка (18.10.2026).
                if (!stuck && r.readTries < READ_TRIES) {
                    r.readTries++;
                    handler.postDelayed(() -> readCart(r), READ_RETRY_MS);
                    return;
                }
                // Касание «Корзины» её так и не открыло: у Дикси 18.10.2026 окно прочитало
                // страницу товара («Доставка, 99 руб.»). Тогда — адрес корзины из наряда.
                JSONArray pages = r.rules.optJSONArray("cart_pages");
                String page = pages != null && pages.length() > 0 ? pages.optString(0, "") : "";
                if (!r.cartPageTried && !page.isEmpty() && !page.equals(open)
                        && (pathOf(page) == null || !pathOf(page).equals(pathOf(open)))) {
                    r.cartPageTried = true;
                    r.readTries = 0;
                    go(r, page);
                    whenReady(r, page, null, CART_SETTLE_MS, () -> readCart(r));
                    return;
                }
                if (r.slot != null && !r.again && nothingPut(r)) {
                    // Итога так и нет, а класть было нечего: всё «уже лежало» по памяти
                    // телефона. Пустая корзина сети значит, что человек оформил заказ или
                    // очистил её, — память в сторону, и корзина ляжет заново целиком. Пустоту
                    // спрашиваем только после перечитываний: пока страница дорисовывается,
                    // «корзина пуста» мелькает и у полной корзины.
                    r.view.evaluateJavascript(EMPTY_JS.replace("%s", JSONObject.quote(
                            r.rules.optString("empty_cart", "корзина пуста"))), empty -> {
                        if (!alive(r)) return;
                        if (emptyAnswer(empty)) {
                            r.again = true;
                            r.cart = new JSONObject();
                            saveLedger(r);
                            while (r.items.length() > 0) r.items.remove(r.items.length() - 1);
                            r.at = 0;
                            r.readTries = 0;
                            r.cartPageTried = false;
                            // Проход начинается заново — последний шаг (ссылка Ленты)
                            // тоже должен повториться, иначе наряд завершится сразу.
                            r.linkAsked = false;
                            r.linkDone = false;
                            status(r, r.name + ": корзина сети пуста — кладу заново", false);
                            nextLine(r);
                            return;
                        }
                        markGone(r, false, () -> finishRun(r, cart, ""));
                    });
                    return;
                }
            }
            markGone(r, hasTotal(cart), () -> finishRun(r, cart, ""));
        });
    }

    /** Прочитан ли со страницы итог корзины: строка «Итого» или «К оформлению» (вид total). */
    private static boolean hasTotal(JSONObject cart) {
        JSONArray totals = cart.optJSONArray("totals");
        for (int i = 0; totals != null && i < totals.length(); i++) {
            JSONObject t = totals.optJSONObject(i);
            if (t != null && "total".equals(t.optString("kind"))) return true;
        }
        return false;
    }

    private static boolean emptyAnswer(String value) {
        try {
            Object v = new JSONTokener(value == null ? "null" : value).nextValue();
            return v instanceof String && "1".equals(v);
        } catch (Exception e) {
            return false;
        }
    }

    /** Ни одна позиция не положена сейчас, а хоть одна «уже лежала» — по памяти телефона. */
    private static boolean nothingPut(CartRun r) {
        boolean kept = false;
        for (int i = 0; i < r.items.length(); i++) {
            JSONObject it = r.items.optJSONObject(i);
            if (it == null) continue;
            if (it.optInt("put", 0) > 0) return false;
            if ("kept".equals(it.optString("how"))) kept = true;
        }
        return kept;
    }

    /**
     * Положенное, которое корзина сети показывает «нет в наличии», — не «легло»
     * (CART_GONE_JS). На странице корзины (onCart: итог прочитан) — и то, чего в ней
     * не видно, если хоть одну нашу позицию на странице видно: значит, имена сеть
     * пишет как в наряде, и пропавшее имя — пропавший товар.
     *
     * Не видно того, что «уже лежало» по памяти телефона, — память врёт: человек убрал
     * товар сам. Память о нём — в сторону, и следующая сборка положит его снова.
     */
    private void markGone(final CartRun r, final boolean onCart, final Runnable then) {
        final JSONArray names = new JSONArray();
        final JSONArray at = new JSONArray();
        for (int i = 0; i < r.items.length(); i++) {
            JSONObject it = r.items.optJSONObject(i);
            if (it == null || !it.optBoolean("ok")) continue;
            names.put(it.optString("name", ""));
            at.put(i);
        }
        if (names.length() == 0) {
            then.run();
            return;
        }
        JSONObject p = new JSONObject();
        try {
            p.put("gone", r.rules.optString("gone", "нет в наличии|закончил|раскупил")).put("names", names);
        } catch (Exception ignored) { }
        r.view.evaluateJavascript(CART_GONE_JS.replace("%s", p.toString()), value -> {
            if (!alive(r)) return;
            JSONObject got = asObject(value);
            JSONArray gone = got.optJSONArray("gone");
            for (int k = 0; gone != null && k < gone.length(); k++) {
                JSONObject it = r.items.optJSONObject(at.optInt(gone.optInt(k, -1), -1));
                if (it == null) continue;
                try {
                    it.put("ok", false).put("why", "корзина сети показывает: нет в наличии");
                } catch (Exception ignored) { }
            }
            JSONArray seen = got.optJSONArray("seen");
            if (onCart && seen != null) {
                for (int k = 0; k < at.length(); k++) {
                    boolean visible = false;
                    for (int s = 0; s < seen.length() && !visible; s++) visible = seen.optInt(s, -1) == k;
                    JSONObject it = r.items.optJSONObject(at.optInt(k, -1));
                    if (it == null || !it.optBoolean("ok")) continue;
                    String sku = it.optString("sku", "");
                    try {
                        if (visible) {
                            // Имя этой позиции сеть пишет на странице корзины так же, как наряд:
                            // в следующий раз её пропажа со страницы — правда, а не разнобой имён.
                            if (!sku.isEmpty()) r.shown.put(sku, true);
                            continue;
                        }
                        // Не видно: верим, если видно хоть одну нашу позицию (имена сеть пишет
                        // как наряд) или эту же позицию страница уже показывала прежде. Иначе
                        // все «уже лежавшие» сошли бы за легшие, когда человек оформил заказ, а в
                        // корзине сети остались только чужие товары.
                        boolean before = !sku.isEmpty() && r.shown.optBoolean(sku, false);
                        if (seen.length() == 0 && !before) continue;
                        it.put("ok", false).put("why", "в корзине сети его не видно");
                        if ("kept".equals(it.optString("how")) && !sku.isEmpty()) {
                            r.cart.remove(sku);
                            r.shown.remove(sku);
                        }
                    } catch (Exception ignored) { }
                }
                saveLedger(r);
            }
            if (!onCart) unseen(r);
            then.run();
        });
    }

    /**
     * Касание без единого доказательства — не «легло». Сеть не ответила своей корзиной
     * (сторож не видел ни одного её запроса), а страница корзины итога не показала:
     * так у ВкусВилла без входа — «В корзину» нажалось, а в корзине пусто и «Для
     * оформления заказа необходимо авторизоваться» (эмулятор, 18.10.2026). Память о
     * таком — в сторону: следующая сборка попробует снова.
     */
    private void unseen(CartRun r) {
        for (int i = 0; i < r.items.length(); i++) {
            JSONObject it = r.items.optJSONObject(i);
            if (it == null || !it.optBoolean("ok") || !"tap".equals(it.optString("how"))) continue;
            JSONArray calls = it.optJSONArray("calls");
            if (calls != null && calls.length() > 0) continue;
            try {
                it.put("ok", false).put("why", "не видно, что легло: сеть не ответила своей корзиной, "
                    + "а страница корзины итога не показала");
                String sku = it.optString("sku", "");
                if (!sku.isEmpty()) r.cart.remove(sku);
            } catch (Exception ignored) { }
        }
        saveLedger(r);
    }

    private void stopRun(String why) {
        CartRun r = cartRun;
        if (r == null) return;
        r.stopped = true;
        finishRun(r, new JSONObject(), why);
    }

    /** Сборка кончилась, не начавшись: наряд не пришёл, класть нечего, сеть увела со страницы. */
    private void failRun(CartRun r, String why) {
        if (r.slot != null) {
            endBackground(r, "failed", why);
            return;
        }
        cartRun = null;
        cartBtn.setText("Собрать");
        setChainButtons(currentChain >= 0);
        say(why, true);
    }

    /** Показать, что вышло, и отдать отчёт серверу. Корзину не трогаем: оформляет человек. */
    private void finishRun(final CartRun r, JSONObject cart, String stoppedWhy) {
        final boolean background = r.slot != null;
        if (!background) {
            cartRun = null;
            cartBtn.setText("Собрать");
            setChainButtons(currentChain >= 0);
        } else if (r.slot.run == r) {
            r.slot.run = null;
        }

        int landed = 0;
        StringBuilder msg = new StringBuilder();
        if (!stoppedWhy.isEmpty()) msg.append(stoppedWhy).append("\n\n");
        for (int i = 0; i < r.items.length(); i++) {
            JSONObject it = r.items.optJSONObject(i);
            if (it == null) continue;
            boolean ok = it.optBoolean("ok");
            if (ok) landed++;
            msg.append(ok ? "✓ " : "✗ ").append(it.optString("name", ""));
            if (ok && "request".equals(it.optString("how"))) msg.append(" · запросом сети");
            if (ok && "kept".equals(it.optString("how"))) msg.append(" · уже лежала");
            if (!ok) msg.append(" — ").append(it.optString("why", ""));
            msg.append('\n');
        }

        JSONArray totals = cart.optJSONArray("totals");
        JSONObject api = cart.optJSONObject("api");
        if (stoppedWhy.isEmpty()) {
            msg.append("\nКорзина сети:\n");
            if (totals == null || totals.length() == 0) {
                msg.append("строк итога на странице не нашлось\n");
                if (!r.cartHint.isEmpty()) {
                    msg.append("дорогу к корзине не нашёл, похожее на странице: ").append(r.cartHint).append('\n');
                }
            } else {
                for (int i = 0; i < totals.length(); i++) {
                    JSONObject t = totals.optJSONObject(i);
                    if (t != null) msg.append("• ").append(t.optString("row", "")).append('\n');
                }
            }
            if (cart.has("count") && !cart.isNull("count")) {
                msg.append("Счётчик корзины: ").append(cart.optInt("count")).append('\n');
            }
            if (api != null && api.length() > 0) {
                msg.append("\nИз ответов сайта:\n");
                Iterator<String> paths = api.keys();
                int shownPaths = 0;
                while (paths.hasNext() && shownPaths < 3) {
                    String path = paths.next();
                    JSONObject fields = api.optJSONObject(path);
                    if (fields == null) continue;
                    msg.append(path).append(": ");
                    Iterator<String> keys = fields.keys();
                    int shown = 0;
                    while (keys.hasNext() && shown < 8) {
                        String k = keys.next();
                        msg.append(shown > 0 ? ", " : "").append(k).append('=').append(fields.opt(k));
                        shown++;
                    }
                    msg.append('\n');
                    shownPaths++;
                }
            }
            if (cart.has("error")) msg.append("Чтение корзины: ").append(cart.optString("error")).append('\n');
        }
        msg.append("\nНаш расчёт по корзине: ").append(money(r.ourTotal));

        final long seconds = Math.round((SystemClock.uptimeMillis() - r.startedAt) / 1000.0);
        final int landedNow = landed;
        if (!background) {
            new AlertDialog.Builder(this)
                .setTitle(r.name + ": легло " + landed + " из " + r.items.length() + " за " + seconds + " с")
                .setMessage(msg.toString())
                .setPositiveButton("Готово", null)
                .show();
            say(r.name + ": легло " + landed + " из " + r.items.length() + ", отправляю отчёт", false);
        } else {
            r.now = "легло " + landed + " из " + r.items.length() + " за " + seconds + " с";
        }

        JSONArray pageTotals = new JSONArray();
        if (totals != null) {
            for (int i = 0; i < totals.length(); i++) {
                JSONObject t = totals.optJSONObject(i);
                if (t == null) continue;
                try {
                    JSONObject row = new JSONObject().put("kind", t.optString("kind"))
                        .put("label", t.optString("label")).put("amount", t.optDouble("amount"));
                    // «Товары на сумму 1 748,98 ₽ 1 561,4 ₽»: первая сумма — до скидок.
                    if (t.has("base") && !t.isNull("base")) row.put("base", t.optDouble("base"));
                    pageTotals.put(row);
                } catch (Exception ignored) { }
            }
        }
        if (stoppedWhy.isEmpty() && r.items.length() > 0) remember(r, true);
        try {
            JSONObject body = new JSONObject()
                .put("store", r.code)
                .put("app_version", BuildConfig.VERSION_NAME)
                .put("items", r.items)
                .put("cart_page", cart.optString("page", ""))
                .put("our_total", r.ourTotal)
                .put("page_totals", pageTotals)
                .put("api_totals", api == null ? new JSONObject() : api)
                .put("background", background)
                .put("seconds", seconds)
                .put("extra", extraOf(r));
            if (r.basket >= 0) body.put("basket", r.basket);
            if (!r.stamp.isEmpty()) body.put("stamp", r.stamp);
            if (cart.has("count") && !cart.isNull("count")) body.put("cart_count", cart.optInt("count"));
            final String name = r.name;
            call("/api/device/cartreport", body, (status, text) -> {
                if (background) {
                    if (status != 200) Log.w(TAG, name + ": отчёт не ушёл — " + serverError(status, text));
                    tellPage();
                    return;
                }
                if (cartRun != null) return;   // уже идёт новая сборка — её строка важнее
                if (status == 200) say(name + ": отчёт на сервере", false);
                else say(name + ": отчёт не ушёл — " + serverError(status, text), true);
            });
        } catch (Exception ignored) { }
        if (background) {
            Log.i(TAG, r.name + ": фоновая сборка — легло " + landedNow + " из " + r.items.length()
                + " за " + seconds + " с");
            rest(r.slot);
        }
    }

    /**
     * Что лежит в корзине сети сверх корзины человека: было в ней раньше, теперь нет или
     * меньше. Приложение этого не убирает — решает человек, — а называет в отчёте.
     */
    private static JSONArray extraOf(CartRun r) {
        JSONArray out = new JSONArray();
        Iterator<String> keys = r.cart.keys();
        while (keys.hasNext()) {
            String sku = keys.next();
            int more = r.cart.optInt(sku, 0) - r.want.optInt(sku, 0);
            String name = r.names.optString(sku, "");
            if (more <= 0 || name.isEmpty()) continue;
            try {
                out.put(new JSONObject().put("name", name).put("sku", sku).put("more", more));
            } catch (Exception ignored) { }
        }
        return out;
    }

    private String watchScript(CartRun r) {
        JSONArray full = r.rules.optJSONArray("full_cart");
        return WATCH_JS.replace("%s", hostsOf(r).toString() + "," + (full == null ? "[]" : full.toString()));
    }

    private String findCartScript(CartRun r) {
        return FIND_CART_JS.replace("%s", hostsOf(r).toString());
    }

    private static JSONArray hostsOf(CartRun r) {
        JSONArray hosts = r.rules.optJSONArray("hosts");
        if (hosts != null && hosts.length() > 0) return hosts;
        JSONArray own = new JSONArray();
        for (String host : CHAINS[r.chain][3].split(",")) own.put(host.trim());
        return own;
    }

    private static String pickScript(CartRun r, boolean again) {
        JSONObject p = new JSONObject();
        try {
            p.put("add", r.rules.optString("add_name", "^(?:в корзину|добавить в корзину|купить)$"))
             .put("plus", r.rules.optString("plus_name", "увелич|добавить ещё|плюс|^\\+$"))
             .put("gone", r.rules.optString("gone", "нет в наличии|закончил|раскупил"))
             .put("selectors", r.rules.optJSONArray("selectors") == null ? new JSONArray()
                                                                          : r.rules.optJSONArray("selectors"))
             .put("below", r.rules.optInt("below", 700))
             .put("again", again);
        } catch (Exception ignored) { }
        return PICK_JS.replace("%s", p.toString());
    }

    // ── Фоновая сборка: пока человек смотрит корзину ──

    /**
     * Что делать со сборкой в каждой сети, когда страница корзины отдала корзину (мост
     * collect). Ответ странице — {starting: [коды], skip: {код: почему нет}}.
     *
     * Собираем там, где человек вошёл: сервер знает сохранённый вход, или у телефона
     * в окне есть кука входа сети, или человек сам собирал здесь кнопкой «Собрать».
     * Без перемен в корзине (тот же отпечаток) повторно не собираем; сборка, которая не
     * вышла, повторяется не раньше RETRY_MS. Корзину, может быть, ещё правят, поэтому
     * запуск — через DEBOUNCE_MS после последней правки.
     */
    private JSONObject decide(JSONObject payload) {
        lastCollect = payload;
        JSONObject answer = new JSONObject();
        JSONArray starting = new JSONArray();
        JSONObject skip = new JSONObject();
        final String stamp = payload.optString("stamp", "");
        final int basket = payload.optInt("basket", -1);
        JSONArray chains = payload.optJSONArray("chains");
        for (int i = 0; chains != null && i < chains.length(); i++) {
            JSONObject c = chains.optJSONObject(i);
            if (c == null) continue;
            final String code = c.optString("code", "");
            final int idx = chainIndex(code);
            try {
                if (idx < 0) continue;
                if (stamp.isEmpty() || basket < 0) {
                    skip.put(code, "корзина пуста");
                    continue;
                }
                if (c.optBoolean("sent")) {
                    skip.put(code, "корзину туда уже положил сервер");
                    continue;
                }
                if (!entered(idx, c.optString("login", "connect"), c.optJSONArray("cookies"))) {
                    skip.put(code, "откройте сеть в приложении и войдите — тогда соберу сама");
                    continue;
                }
                Slot s = slots.get(code);
                if (s == null) {
                    s = new Slot(idx);
                    slots.put(code, s);
                }
                if (cartRun != null && cartRun.chain == idx) {
                    skip.put(code, "собираете сами");
                    continue;
                }
                if (s.run != null) {
                    if (!stamp.equals(s.run.stamp)) s.pending = stamp;   // доберём после этой сборки
                    starting.put(code);
                    continue;
                }
                JSONObject done = doneOf(code);
                long doneAt = done != null ? done.optLong("at", 0) : 0;
                boolean fresh = done != null && (System.currentTimeMillis() - doneAt < FRESH_MS);
                if (done != null && stamp.equals(done.optString("stamp")) && basket == done.optInt("basket", -2)
                        && ((done.optBoolean("ok") && fresh)
                            || (!done.optBoolean("ok") && System.currentTimeMillis() - doneAt < RETRY_MS))) {
                    skip.put(code, done.optBoolean("ok") ? "уже собрано для этой корзины (свежее)"
                                                         : "не вышло, попробую позже");
                    continue;
                }
                s.pending = stamp;
                soon(s);
                starting.put(code);
            } catch (Exception ignored) { }
        }
        try {
            answer.put("starting", starting).put("skip", skip);
        } catch (Exception ignored) { }
        return answer;
    }

    /** Вошёл ли человек в сеть — насколько видно серверу и самому телефону. */
    private boolean entered(int idx, String login, JSONArray cookies) {
        if (login == null || login.isEmpty()) return true;
        if (memory().getBoolean("in." + CHAINS[idx][1], false)) return true;
        if (cookies == null || cookies.length() == 0) return false;
        String jar = CookieManager.getInstance().getCookie(CHAINS[idx][2]);
        if (jar == null) return false;
        for (int i = 0; i < cookies.length(); i++) {
            String name = cookies.optString(i, "");
            if (!name.isEmpty() && (";" + jar.replace(" ", "")).contains(";" + name + "=")) return true;
        }
        return false;
    }

    /** Запустить сборку в сети через DEBOUNCE_MS: каждая новая правка корзины отодвигает запуск. */
    private void soon(final Slot s) {
        if (s.soon != null) handler.removeCallbacks(s.soon);
        s.soon = () -> startBackground(s);
        handler.postDelayed(s.soon, DEBOUNCE_MS);
        touchBusy();
    }

    private void startBackground(final Slot s) {
        s.soon = null;
        touchBusy();
        if (s.run != null || away || lastCollect == null || s.pending.isEmpty()) return;
        if (cartRun != null && cartRun.chain == s.chain) return;     // человек собирает сам
        // Темп «одна сеть за раз»: если другой скрытый слот сейчас собирает, откладываем на 5 с
        for (Slot other : slots.values()) {
            if (other != s && other.run != null) {
                s.soon = () -> startBackground(s);
                handler.postDelayed(s.soon, 5000);
                touchBusy();
                return;
            }
        }
        if (s.sleep != null) {
            handler.removeCallbacks(s.sleep);
            s.sleep = null;
        }
        if (s.view == null) s.view = hiddenView(s.chain);
        final CartRun r = new CartRun(s.chain, s.view, s);
        r.basket = lastCollect.optInt("basket", -1);
        r.stamp = s.pending;
        s.pending = "";
        s.run = r;
        touchBusy();
        r.now = "беру наряд у сервера";
        beat(r, true);
        askPlan(r, () -> begin(r));
    }

    /** Наряд пришёл: сначала новые позиции (первая учит запрос сети), потом прибавки, потом лежащие. */
    private void begin(final CartRun r) {
        if (r.lines.length() == 0) {
            String note = r.rules.optString("_note", "");
            endBackground(r, "empty", note.isEmpty() ? "позиции корзины этой сети незнакомы" : note);
            return;
        }
        loadLedger(r);
        JSONArray fresh = new JSONArray(), more = new JSONArray(), kept = new JSONArray();
        for (int i = 0; i < r.lines.length(); i++) {
            JSONObject line = r.lines.optJSONObject(i);
            if (line == null) continue;
            String sku = line.optString("sku", "");
            int have = sku.isEmpty() ? 0 : r.cart.optInt(sku, 0);
            int target = Math.max(1, line.optInt("clicks", 1));
            (have == 0 ? fresh : have < target ? more : kept).put(line);
        }
        JSONArray ordered = new JSONArray();
        for (JSONArray part : new JSONArray[]{fresh, more, kept}) {
            for (int i = 0; i < part.length(); i++) ordered.put(part.opt(i));
        }
        r.lines = ordered;
        status(r, "позиций " + r.lines.length() + ": новых " + fresh.length() + ", прибавить "
            + more.length() + ", уже лежат " + kept.length(), false);
        beat(r, true);
        nextLine(r);
    }

    /** Фоновая сборка кончилась без отчёта: класть нечего, наряд не пришёл, сеть не пустила. */
    private void endBackground(CartRun r, String state, String note) {
        Slot s = r.slot;
        if (s == null || s.run != r) return;
        r.stopped = true;
        s.run = null;
        r.now = note;
        JSONObject body = new JSONObject();
        try {
            body.put("store", r.code).put("state", state).put("note", note)
                .put("planned", r.lines.length()).put("at", r.at);
            if (r.basket >= 0) body.put("basket", r.basket);
            if (!r.stamp.isEmpty()) body.put("stamp", r.stamp);
        } catch (Exception ignored) { }
        call("/api/device/cartprogress", body, (status, text) -> tellPage());
        remember(r, "empty".equals(state));
        Log.i(TAG, r.name + ": фоновая сборка не пошла — " + note);
        rest(s);
    }

    /** Остановить фоновую сборку сети. `forHuman` — человек собирает сам: её корзина — его. */
    private void stopBackground(Slot s, boolean forHuman) {
        CartRun r = s.run;
        if (r == null) return;
        r.stopped = true;
        s.run = null;
        if (!forHuman && !r.stamp.isEmpty() && s.pending.isEmpty()) s.pending = r.stamp;
        if (s.view != null) s.view.loadUrl("about:blank");
        touchBusy();
    }

    /**
     * Сборка в сети кончилась: окно отдыхает (пустая страница — сайт сети не крутит
     * свои скрипты зря), корзина, поменявшаяся посреди сборки, собирается следом, а
     * долго пустующее окно уходит совсем.
     */
    private void rest(final Slot s) {
        if (s.run != null) return;
        touchBusy();
        if (!s.pending.isEmpty() && !away) {
            soon(s);
            return;
        }
        handler.postDelayed(() -> {
            if (s.run == null && s.view != null) s.view.loadUrl("about:blank");
        }, 1500);
        if (s.sleep != null) handler.removeCallbacks(s.sleep);
        s.sleep = () -> {
            s.sleep = null;
            if (s.run != null || s.view == null) return;
            stage.removeView(s.view);
            s.view.destroy();
            s.view = null;
        };
        handler.postDelayed(s.sleep, IDLE_MS);
    }

    /**
     * Скрытое окно сети: те же настройки и та же метка в User-Agent, что у окна человека,
     * но без моста window.Korzina — страницы сети его не видят. Окно лежит ПОД окном
     * человека во весь его размер: оно размечено и рисуется, поэтому касание в него
     * доходит и таймеры страницы не душатся, а человек его не видит.
     */
    private WebView hiddenView(final int idx) {
        final WebView v = new WebView(this);
        v.setBackgroundColor(Color.WHITE);
        v.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
        v.setFocusable(false);
        WebSettings s = v.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setUserAgentString(web.getSettings().getUserAgentString());
        CookieManager.getInstance().setAcceptThirdPartyCookies(v, true);
        v.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                if (!req.isForMainFrame()) return false;
                String url = req.getUrl().toString();
                if (isHostOf(idx, url)) return false;
                // Сеть уводит со своих адресов — на вход, проверку, оплату. Это шаг человека.
                handler.post(() -> leftChain(idx, hostOf(url)));
                return true;
            }

            @Override
            public void onReceivedSslError(WebView view, android.webkit.SslErrorHandler h,
                                           android.net.http.SslError error) {
                // Сертификаты Минцифры у части российских магазинов — только для адресов этой сети.
                if (isHostOf(idx, error.getUrl())) h.proceed();
                else h.cancel();
            }

            @Override
            public boolean onRenderProcessGone(WebView view, android.webkit.RenderProcessGoneDetail detail) {
                handler.post(() -> lostView(idx, view));
                return true;
            }
        });
        v.setWebChromeClient(new WebChromeClient() {
            // Окна, которые сайт сети спрашивает у человека, в скрытом окне человек не
            // видит. «Подтвердить» — НИКОГДА: так спрашивают «Удалить?» и «Очистить корзину?».
            @Override
            public boolean onJsAlert(WebView view, String url, String message, android.webkit.JsResult result) {
                result.confirm();
                return true;
            }

            @Override
            public boolean onJsConfirm(WebView view, String url, String message, android.webkit.JsResult result) {
                result.cancel();
                return true;
            }

            @Override
            public boolean onJsPrompt(WebView view, String url, String message, String value,
                                      android.webkit.JsPromptResult result) {
                result.cancel();
                return true;
            }

            @Override
            public boolean onJsBeforeUnload(WebView view, String url, String message, android.webkit.JsResult result) {
                result.confirm();
                return true;
            }

            @Override
            public void onGeolocationPermissionsShowPrompt(String origin,
                                                           android.webkit.GeolocationPermissions.Callback callback) {
                callback.invoke(origin, false, false);
            }
        });
        stage.addView(v, 0, new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        return v;
    }

    /** Скрытое окно сети ушло на чужой адрес — сборку не продолжаем, вход и проверку делает человек. */
    private void leftChain(int idx, String host) {
        Slot s = slots.get(CHAINS[idx][1]);
        if (s == null || s.run == null) return;
        endBackground(s.run, "failed", CHAINS[idx][0] + " уводит на " + host
            + " — похоже, просит войти или пройти проверку. Откройте сеть в приложении сами.");
    }

    /** Процесс страниц упал (память): окно сети больше не живое, сборку — заново один раз. */
    private void lostView(int idx, WebView view) {
        Slot s = slots.get(CHAINS[idx][1]);
        if (s == null || s.view != view) return;
        CartRun r = s.run;
        if (r != null) {
            r.stopped = true;
            s.run = null;
            if (s.crashes++ < 1 && !r.stamp.isEmpty()) s.pending = r.stamp;
        }
        stage.removeView(view);
        view.destroy();
        s.view = null;
        Log.w(TAG, CHAINS[idx][0] + ": скрытое окно сети упало");
        if (!s.pending.isEmpty() && !away) soon(s);
    }

    // ── Память телефона: что куда положено и какую корзину собирали ──

    private SharedPreferences memory() {
        if (prefs == null) prefs = getSharedPreferences("korzina-collect", MODE_PRIVATE);
        return prefs;
    }

    /** Что телефон клал в корзину этой сети раньше: артикул → упаковок, с названиями. */
    private void loadLedger(CartRun r) {
        try {
            JSONObject all = new JSONObject(memory().getString("ledger." + r.code, "{}"));
            Iterator<String> keys = all.keys();
            while (keys.hasNext()) {
                String sku = keys.next();
                JSONObject it = all.optJSONObject(sku);
                if (it == null) continue;
                if (it.optInt("q", 0) > 0) r.cart.put(sku, it.optInt("q", 0));
                if (!it.optString("n", "").isEmpty() && !r.names.has(sku)) r.names.put(sku, it.optString("n", ""));
                if (it.optInt("v", 0) == 1) r.shown.put(sku, true);
            }
        } catch (Exception ignored) { }
    }

    private void saveLedger(CartRun r) {
        try {
            JSONObject all = new JSONObject();
            Iterator<String> keys = r.cart.keys();
            while (keys.hasNext()) {
                String sku = keys.next();
                int q = r.cart.optInt(sku, 0);
                if (q <= 0) continue;
                JSONObject it = new JSONObject().put("q", q).put("n", r.names.optString(sku, ""));
                if (r.shown.optBoolean(sku, false)) it.put("v", 1);
                all.put(sku, it);
            }
            memory().edit().putString("ledger." + r.code, all.toString()).apply();
        } catch (Exception ignored) { }
    }

    /** Эту корзину в этой сети собрали (ok) или пытались: без перемен в корзине — не повторять. */
    private void remember(CartRun r, boolean ok) {
        if (r.stamp.isEmpty()) return;
        try {
            memory().edit().putString("done." + r.code, new JSONObject()
                .put("stamp", r.stamp).put("basket", r.basket).put("ok", ok)
                .put("at", System.currentTimeMillis()).toString()).apply();
        } catch (Exception ignored) { }
    }

    private JSONObject doneOf(String code) {
        try {
            String raw = memory().getString("done." + code, null);
            return raw == null ? null : new JSONObject(raw);
        } catch (Exception e) {
            return null;
        }
    }

    private static int chainIndex(String code) {
        for (int i = 0; i < CHAINS.length; i++) {
            if (CHAINS[i][1].equals(code)) return i;
        }
        return -1;
    }

    /**
     * Коснуться точки страницы так, как касается палец: событие экрана в окне, а не
     * щелчок скрипта. Точки CSS переводятся в пиксели окна по ширине видимой области.
     * Скрытое окно тоже размечено (лежит под окном человека во весь его размер), поэтому
     * касание доходит до страницы так же.
     */
    private boolean tapAt(final WebView view, JSONObject at) {
        if (!at.optBoolean("found")) return false;
        double vw = at.optDouble("vw", 0);
        if (vw <= 0 || view.getWidth() <= 0) return false;
        double k = view.getWidth() / vw;
        final float x = (float) (at.optDouble("x") * k);
        final float y = (float) (at.optDouble("y") * k);
        if (x < 0 || y < 0 || x > view.getWidth() || y > view.getHeight()) return false;
        final long down = SystemClock.uptimeMillis();
        MotionEvent press = MotionEvent.obtain(down, down, MotionEvent.ACTION_DOWN, x, y, 0);
        press.setSource(InputDevice.SOURCE_TOUCHSCREEN);
        view.dispatchTouchEvent(press);
        press.recycle();
        handler.postDelayed(() -> {
            MotionEvent release = MotionEvent.obtain(down, SystemClock.uptimeMillis(),
                                                     MotionEvent.ACTION_UP, x, y, 0);
            release.setSource(InputDevice.SOURCE_TOUCHSCREEN);
            view.dispatchTouchEvent(release);
            release.recycle();
        }, 80);
        return true;
    }

    private static JSONObject asObject(String result) {
        try {
            Object value = new JSONTokener(result == null ? "null" : result).nextValue();
            if (value instanceof String) return new JSONObject((String) value);
            if (value instanceof JSONObject) return (JSONObject) value;
        } catch (Exception ignored) { }
        return new JSONObject();
    }

    /** Длина окна в пикселях, как её видит страница: по ней решаем, докручена ли она. */
    private int pageHeight() {
        return web == null ? 0 : web.getHeight();
    }

    private static String money(double value) {
        return String.format(new Locale("ru"), "%,.2f ₽", value);
    }

    private interface Reply {
        void got(int status, String text);
    }

    /**
     * Запрос на наш сервер с сессией «Моей корзины»; ответ — на главном потоке.
     *
     * body == null — GET: так забирается своя статика (сборщик кабинета ФНС,
     * /static/fns-grab.js), которую сервер отдаёт только на GET.
     */
    private void call(String path, JSONObject body, Reply reply) {
        final boolean post = body != null;
        final String session = CookieManager.getInstance().getCookie(SERVER);
        new Thread(() -> {
            int code = 0;
            String text = "";
            try {
                HttpURLConnection conn = (HttpURLConnection) new URL(SERVER + path).openConnection();
                conn.setRequestMethod(post ? "POST" : "GET");
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(60000);
                conn.setInstanceFollowRedirects(false);
                if (post) {
                    conn.setDoOutput(true);
                    conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                }
                if (session != null) conn.setRequestProperty("Cookie", session);
                if (post) {
                    try (OutputStream os = conn.getOutputStream()) {
                        os.write(body.toString().getBytes(StandardCharsets.UTF_8));
                    }
                }
                code = conn.getResponseCode();
                InputStream is = code < 400 ? conn.getInputStream() : conn.getErrorStream();
                if (is != null) text = new String(readAll(is), StandardCharsets.UTF_8);
                conn.disconnect();
            } catch (Exception e) {
                text = "{\"error\":\"Сервер недоступен\"}";
            }
            final int status = code;
            final String answer = text;
            handler.post(() -> reply.got(status, answer));
        }).start();
    }

    private static String serverError(int status, String text) {
        if (status == 401 || status == 302 || status == 303) {
            return "Сначала войдите в «Мою корзину» своим номером.";
        }
        String msg = status == 0 ? "Сервер недоступен" : "Ошибка " + status;
        try { msg = new JSONObject(text).optString("error", msg); } catch (Exception ignored) { }
        return msg;
    }

    // ── Вход в магазин ──
    private void saveLogin() {
        if (currentChain < 0) return;
        final String chain = CHAINS[currentChain][1];
        String url = web.getUrl();
        String raw = url == null ? null : CookieManager.getInstance().getCookie(url);
        if (raw == null || raw.trim().isEmpty()) {
            say("Нет кук — похоже, вы ещё не вошли в магазин.", true);
            return;
        }
        try {
            // Концепция 2.0: сессии и токены шифруются в KeyStore на устройстве (AES-256-GCM)
            vault().importCookies(chain, raw, hostOf(url));
            memory().edit().putBoolean("in." + chain, true).apply();

            // Серверу сообщаем о факте подключения и способностях без передачи секретов
            JSONObject body = new JSONObject()
                .put("store", chain)
                .put("connected", true)
                .put("gives", new JSONArray().put("prices").put("cart"));

            say("Сохраняю вход в защищённое хранилище…", false);
            post("/api/device/connected", body,
                 CHAINS[currentChain][0] + " — вход защищённо сохранён в KeyStore");
            say(CHAINS[currentChain][0] + " — вход надёжно сохранён в KeyStore на устройстве", false);
        } catch (Exception e) {
            say("Не получилось сохранить вход: " + e.getMessage(), true);
        }
    }

    // ── Личные акции ──
    private void saveOffers() {
        if (currentChain < 0) return;
        final String chain = CHAINS[currentChain][1];
        final String name = CHAINS[currentChain][0];
        say("Собираю акции со страницы…", false);
        web.evaluateJavascript(OFFERS_JS, result -> {
            try {
                String json = (String) new JSONTokener(result).nextValue();
                JSONArray offers = new JSONArray(json == null ? "[]" : json);
                if (offers.length() == 0) {
                    say("На этой странице акций не нашлось. Откройте в " + name
                        + " раздел своих предложений и нажмите ещё раз.", true);
                    return;
                }
                JSONObject body = new JSONObject()
                    .put("store", chain).put("logged_in", true)
                    .put("gives", new JSONArray().put("coupons"))
                    .put("coupons", offers);
                post("/api/store_accounts/sync", body,
                     name + " — сохранено акций: " + offers.length());
            } catch (Exception e) {
                say("Не получилось разобрать страницу: " + e.getMessage(), true);
            }
        });
    }

    // ── Раздел сети: карточки со страницы — в единую базу ──

    /**
     * Сколько карточек берём со страницы за одно нажатие. Столько же принимает сервер
     * (app/phonecart.py, CARDS_LIMIT): больше он всё равно отбросит.
     */
    private static final int SECTION_LIMIT = 150;

    /**
     * Потолок одного нажатия «Раздел» и сколько раз прокручиваем страницу. Раздел сети
     * — это сотни товаров, и весь его за одно нажатие не вычитать: 300 карточек и
     * четырнадцать прокруток — это уже несколько экранов, дальше человек жмёт ещё раз.
     */
    private static final int SECTION_TOTAL = 300;
    private static final int SECTION_ROUNDS = 14;
    /**
     * Пауза между прокрутками, мс: витрина не должна получать запросы чаще человека.
     * Полторы секунды, а не меньше: замер на Пятёрочке 19.10.2026 — выдача поиска
     * прибавляла товары только на третьей прокрутке, через 1,6 с каждая (14 → 25).
     * С короткой паузой приложение решало, что товары кончились, и уходило с половиной.
     */
    private static final int SECTION_PAUSE_MS = 1500;
    /** Столько пустых прокруток подряд считаем концом раздела (одной мало, см. выше). */
    private static final int SECTION_QUIET = 2;

    /**
     * Карточки раздела: адрес, название, цена. Разметка у сетей разная, но карточка
     * всегда ссылается на товар, а её текст несёт название и цену.
     *
     * ЧТО ЗДЕСЬ ВАЖНО (замер 19.10.2026 на Пятёрочке, окно приложения):
     *  - цена берётся ПОСЛЕДНЯЯ в карточке: у товара со скидкой в тексте лежат две
     *    цены («629 00 ₽ 299 99 ₽»), и платит человек вторую. Первая — до скидки.
     *  - «629 00» — это 629 рублей 00 копеек, а не 62 900: пробел перед двумя
     *    последними цифрами и есть десятичный разделитель. Без этого правила цена
     *    уезжала в базу стократной.
     *  - из начала названия снимаются наклейки витрины: «-52%», «5% на 2 кг», «Нет
     *    оценок», «Комбо 4,84», оценка «4,95», «от 2 шт», «Хит»/«Новинка»/«Выгодно».
     *    Название уходит в единую базу как имя товара, и наклейка в нём — мусор, по
     *    которому потом не сопоставить. Процент ВНУТРИ названия («Маскарпоне 80%») —
     *    часть товара, и его не трогаем: снимаем только начало строки.
     *  - у карточки-картинки имени в ссылке нет — берём alt картинки; нет и его —
     *    карточку пропускаем. Выдуманное имя хуже пропуска.
     */
    private static final String CARDS_JS =
        "(function(){"
        + "function clean(raw){var t=(raw||'').replace(/\\s+/g,' ').trim();"
        + "var re=/(\\d[\\d\\s]{0,8}(?:[.,]\\d{1,2})?)\\s*(?:₽|руб)/gi,all=[],mm;"
        + "while((mm=re.exec(t))!==null){all.push(mm);}"
        + "var m=all.length?all[all.length-1]:null;"
        + "var name=all.length?t.slice(0,all[0].index):t;"
        + "for(var k=0;k<6;k++){var before=name;"
        + "name=name.replace(/^[-\\u2212]?\\d+([.,]\\d+)?\\s*%/,'')"
        + ".replace(/^%/,'')"
        + ".replace(/^на\\s+\\d+\\s*[а-яa-z]+/i,'')"
        + ".replace(/^от\\s+\\d+\\s*шт\\.?/i,'')"
        + ".replace(/^Нет оценок/i,'')"
        + ".replace(/^Комбо\\s+\\d[.,]\\d{1,2}/i,'')"
        + ".replace(/^\\d+[.,]\\d{1,2}/,'')"
        + ".replace(/^(Хит|Новинка|Выгодно)\\s*/i,'').trim();"
        + "if(name===before)break;}"
        + "var price=null;if(m){price=parseFloat(m[1].trim()"
        + ".replace(/\\s+(\\d{2})$/,'.$1').replace(/\\s/g,'').replace(',','.'));}"
        + "return {name:name.slice(0,120),price:price};}"
        + "var seen={},out=[],as=[].slice.call(document.querySelectorAll('a[href*=\"/product\"]'));"
        + "for(var i=0;i<as.length&&out.length<" + SECTION_LIMIT + ";i++){"
        + "var a=as[i],h=a.href||'';if(!h||seen[h])continue;seen[h]=1;"
        + "var got=clean(a.innerText);"
        + "if(!got.name){var im=a.querySelector('img[alt]');got=clean(im?(im.getAttribute('alt')||''):'');}"
        + "if(!got.name)continue;"
        + "out.push({url:h,name:got.name,price:got.price});}"
        + "return JSON.stringify({cards:out,y:Math.round(window.scrollY||0),"
        + "h:Math.round(document.body.scrollHeight||0)});})()";

    /**
     * Прочитать раздел сети, который человек открыл, и отдать карточки серверу.
     *
     * ЗАЧЕМ. Девять сетей сервер обходит сам, а Пятёрочку и Самокат витрина не пускает
     * ни с серверного адреса, ни с домашнего (замеры 18–19.10.2026): их каталога в
     * единой базе нет, и сопоставлять их товары не с чем. Окно человека эти витрины
     * видит — замер 19.10.2026: страница 5ka.ru отдала 28 карточек с ценами. Человек
     * открывает раздел и жмёт «Раздел»; сервер достаёт артикул из адреса карточки
     * (app/catalog/fromurl.py) и заводит товар (app/phonecart.accept_cards).
     *
     * ПОЧЕМУ ПРОКРУТКА. Витрина рисует товары по мере прокрутки: за один экран их
     * десятка три, а в разделе — сотни. Поэтому читаем не один экран, а идём вниз:
     * собрали → прокрутили → собрали снова, пока не перестанут появляться новые
     * карточки (две пустые пробы подряд) или пока не упрёмся в SECTION_TOTAL. Пауза
     * между прокрутками (SECTION_PAUSE_MS) — тот же темп, что у прочих наших шагов:
     * витрина не должна получать запросы чаще, чем человек за экраном.
     *
     * Почему кнопкой, а не само: обход разделов — это сотни страниц и переходы, и
     * делать это без ведома человека нельзя. Кнопка честно говорит, что ушло в базу.
     */
    private void readSection() {
        if (currentChain < 0) return;
        final String chain = CHAINS[currentChain][1];
        final String name = CHAINS[currentChain][0];
        if (cartRun != null) {
            say(name + ": идёт сборка — раздел прочитаю после неё", true);
            return;
        }
        say(name + ": читаю карточки раздела…", false);
        readRound(chain, name, new JSONArray(), new HashSet<>(), 0, 0);
    }

    /** Один заход чтения: собрать карточки, прокрутить страницу, собрать ещё. */
    private void readRound(String chain, String name, JSONArray all, Set<String> seen,
                           int round, int quiet) {
        web.evaluateJavascript(CARDS_JS, result -> {
            JSONObject state = asObject(result);
            JSONArray cards = state.optJSONArray("cards");
            if (cards == null) cards = new JSONArray();
            int added = 0;
            for (int i = 0; i < cards.length(); i++) {
                JSONObject card = cards.optJSONObject(i);
                if (card == null) continue;
                String url = card.optString("url", "");
                if (url.isEmpty() || seen.contains(url)) continue;
                seen.add(url);
                all.put(card);
                added++;
            }
            // Витрина дорисовывает товары не сразу: одна пустая проба ничего не значит.
            // Смотрим, докручена ли страница: пока внизу есть место, идём дальше даже
            // без новых карточек — замер 19.10.2026 на Пятёрочке: выдача выросла с 13
            // до 25 и ушла на вторую страницу уже после того, как приложение решило,
            // что раздел кончился.
            int y = state.optInt("y");
            int height = state.optInt("h");
            boolean more = height > 0 && y + pageHeight() < height - 80;
            boolean enough = all.length() >= SECTION_TOTAL || round >= SECTION_ROUNDS;
            if (enough || (!more && added == 0 && quiet + 1 >= SECTION_QUIET)) {
                if (all.length() == 0) {
                    explainNoCards(name);
                    return;
                }
                sendCards(chain, name, all);
                return;
            }
            web.evaluateJavascript(
                "window.scrollBy(0,Math.round(window.innerHeight*0.9));1", null);
            final int nextQuiet = added == 0 ? quiet + 1 : 0;
            handler.postDelayed(() -> readRound(chain, name, all, seen, round + 1, nextQuiet),
                                SECTION_PAUSE_MS);
        });
    }

    /**
     * ЧТО СТРАНИЦА ПРОСИТ, КОГДА КАРТОЧЕК НЕТ. Замер 20.10.2026 на живом приложении:
     * нажатие «Раздел» на Самокате ничего не дало — сайт вместо каталога показал
     * «Ввести адрес» с картой Москвы, и человек видел только «карточек нет». Он не знал,
     * что дело не в пустом разделе, а в том, что сеть просит выбрать адрес.
     */
    private static final String BLOCKER_JS = "(function(){" +
        "var t=(document.body?document.body.innerText:'').toLowerCase();" +
        "if(/ввести адрес|ваш город|укажите адрес|выберите адрес|куда доставить/.test(t))" +
        "  return 'address';" +
        "if(/не робот|captcha|проверка безопасности|подтвердите, что вы/.test(t))" +
        "  return 'captcha';" +
        "if(/войти|вход по|код из смс|авториз/.test(t)) return 'login';" +
        "return '';})()";

    /** Сказать словами, почему карточек нет: адрес, вход или проверка. */
    private void explainNoCards(String name) {
        web.evaluateJavascript(BLOCKER_JS, result -> {
            String why = result == null ? "" : result.replace("\"", "").trim();
            if (why.contains("address")) {
                say(name + ": сеть просит выбрать адрес — выберите его и нажмите «Раздел» снова", true);
            } else if (why.contains("login")) {
                say(name + ": сеть просит войти — нажмите «ВХОД», потом «Раздел» снова", true);
            } else if (why.contains("captcha")) {
                say(name + ": сеть показывает проверку — пройдите её в окне и нажмите «Раздел» снова", true);
            } else {
                say(name + ": карточек на странице нет — откройте раздел сети", true);
            }
        });
    }

    /** Отдать собранное серверу: пачками по SECTION_LIMIT, с общим итогом в строке. */
    private void sendCards(String chain, String name, JSONArray all) {        if (all.length() == 0) {
            say(name + ": карточек на странице нет — откройте раздел сети", true);
            return;
        }
        sendBatch(chain, name, all, 0, 0, 0, 0);
    }

    private void sendBatch(String chain, String name, JSONArray all, int from,
                           int withSku, int got, int learned) {
        JSONArray batch = new JSONArray();
        int i = from;
        for (; i < all.length() && batch.length() < SECTION_LIMIT; i++) batch.put(all.opt(i));
        final int next = i;
        try {
            JSONObject body = new JSONObject()
                .put("store", chain)
                .put("cards", batch)
                .put("page", web.getUrl() == null ? "" : web.getUrl());
            call("/api/device/cards", body, (status, text) -> {
                if (status != 200) {
                    say(name + ": раздел не ушёл — " + serverError(status, text), true);
                    return;
                }
                JSONObject a = asObject(text);
                int w = withSku + a.optInt("with_sku");
                int g = got + a.optInt("got");
                int l = learned + a.optInt("learned");
                if (next < all.length()) sendBatch(chain, name, all, next, w, g, l);
                else say(name + ": карточек " + w + " из " + g + ", новых в базе " + l, false);
            });
        } catch (Exception ignored) { }
    }

    /** POST на наш сервер с сессией «Моей корзины» из WebView. В фоне. */
    private void post(String path, JSONObject body, String okText) {
        setChainButtons(false);
        final String session = CookieManager.getInstance().getCookie(SERVER);
        new Thread(() -> {
            int code;
            String text = "";
            try {
                HttpURLConnection conn = (HttpURLConnection) new URL(SERVER + path).openConnection();
                conn.setRequestMethod("POST");
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(30000);
                conn.setDoOutput(true);
                conn.setInstanceFollowRedirects(false);
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                if (session != null) conn.setRequestProperty("Cookie", session);
                try (OutputStream os = conn.getOutputStream()) {
                    os.write(body.toString().getBytes(StandardCharsets.UTF_8));
                }
                code = conn.getResponseCode();
                InputStream is = code < 400 ? conn.getInputStream() : conn.getErrorStream();
                if (is != null) text = new String(readAll(is), StandardCharsets.UTF_8);
                conn.disconnect();
            } catch (Exception e) {
                final String why = e.getMessage();
                handler.post(() -> done("Сервер недоступен: " + why, true));
                return;
            }
            final int status = code;
            final String answer = text;
            handler.post(() -> {
                if (status == 200) {
                    done(okText, false);
                } else if (status == 401 || status == 302 || status == 303) {
                    done("Сначала войдите в «Мою корзину» своим номером.", true);
                } else {
                    String msg = "Ошибка " + status;
                    try { msg = new JSONObject(answer).optString("error", msg); } catch (Exception ignored) { }
                    done(msg, true);
                }
            });
        }).start();
    }

    private void done(String text, boolean bad) {
        say(text, bad);
        if (currentChain >= 0) setChainButtons(true);
    }

    // ── Вспомогательные ──
    private void say(String text, boolean bad) {
        if (chainStatus == null) return;
        chainStatus.setText(text);
        chainStatus.setTextColor(bad ? 0xFFB71C1C : Color.DKGRAY);
    }

    private static byte[] readAll(InputStream is) throws java.io.IOException {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = is.read(buf)) != -1) out.write(buf, 0, n);
        return out.toByteArray();
    }

    /** Адрес — из своих адресов ЭТОЙ сети, а не любой из списка: на странице
        Магнита не должно быть панели Пятёрочки. */
    private static boolean isHostOf(int idx, String url) {
        String host = hostOf(url);
        for (String own : CHAINS[idx][3].split(",")) {
            String d = own.trim();
            if (host.equals(d) || host.endsWith("." + d)) return true;
        }
        return false;
    }

    private static boolean isChainHost(String url) {
        String host = hostOf(url);
        for (String[] c : CHAINS) {
            String own = hostOf(c[2]).replaceFirst("^www\\.", "");
            if (host.equals(own) || host.endsWith("." + own)) return true;
        }
        return false;
    }

    private static String hostOf(String url) {
        try {
            return new URL(url).getHost();
        } catch (Exception e) {
            return "";
        }
    }

    private static String shortenUrl(String url) {
        if (url == null) return "";
        String s = url.replaceFirst("^https?://", "");
        int q = s.indexOf('?');
        if (q > 0) s = s.substring(0, q);
        return s.length() > 60 ? s.substring(0, 57) + "…" : s;
    }

    private int dp(int dp) {
        return (int) (dp * getResources().getDisplayMetrics().density + 0.5f);
    }

    @Override
    public void onBackPressed() {
        // В кабинете ФНС «назад» — не история налоговой, а наш экран чеков: человек
        // уходил за ключом, а не листать кабинет.
        if (fnsMode) {
            leaveFns();
            return;
        }
        // Идти назад некуда, а человек всё ещё в приложении (экран чеков после окна
        // ФНС, task-21): «назад» ведёт в корзину, а не закрывает приложение. Путь
        // берём из pageUrl — WebView.getUrl() здесь не нужен, адрес уже записан
        // колбэками на этом же главном потоке. С корзины и с её корня — обычный
        // выход: иначе «назад» возвращал бы в корзину вечно.
        if (web != null && !web.canGoBack() && pageOurs) {
            String path = pathOfPage();
            if (!"/phone/basket".equals(path) && !"/phone/".equals(path)) {
                openOurApp("/phone/basket");
                return;
            }
        }
        // Режим снимет onPageFinished по адресу: вернулись на наш сайт — панель ушла.
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    /** Путь страницы в окне без параметров и якоря — по адресу с главного потока. */
    private String pathOfPage() {
        try {
            String path = new URL(pageUrl).getPath();
            return path == null ? "" : path;
        } catch (Exception e) {
            return "";
        }
    }

    // ── Жизнь приложения: фоновая сборка идёт, только пока оно открыто ──

    @Override
    protected void onStart() {
        super.onStart();
        handler.removeCallbacks(goAway);
        if (!away) return;
        // Вернулись: остановленные сборки продолжаются с того, что уже легло.
        away = false;
        for (Slot s : slots.values()) {
            if (s.run == null && !s.pending.isEmpty()) soon(s);
        }
    }

    @Override
    protected void onStop() {
        super.onStop();
        // Человек отлучился (код из СМС, звонок) — минуту сборка ещё идёт, дальше стоит.
        handler.removeCallbacks(goAway);
        if (!slots.isEmpty()) handler.postDelayed(goAway, AWAY_MS);
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        for (Slot s : slots.values()) {
            if (s.run != null) s.run.stopped = true;
            s.run = null;
            if (s.view != null) {
                stage.removeView(s.view);
                s.view.destroy();
                s.view = null;
            }
        }
        super.onDestroy();
    }

    /** Приложение свёрнуто дольше AWAY_MS: фоновые сборки — на паузу до возвращения. */
    private void pauseBackground() {
        away = true;
        for (Slot s : slots.values()) {
            if (s.soon != null) {
                handler.removeCallbacks(s.soon);
                s.soon = null;
            }
            stopBackground(s, false);
        }
    }

    /**
     * Процесс страниц упал. Скрытые окна разберутся сами (lostView); окно человека
     * заводится заново — с мостом и нашим сайтом, а сборка на нём останавливается.
     */
    private void reviveMainView(WebView dead) {
        if (dead != web) return;
        if (cartRun != null) {
            cartRun.stopped = true;
            cartRun = null;
        }
        currentChain = -1;
        stage.removeView(dead);
        dead.destroy();
        web = new WebView(this);
        web.setBackgroundColor(Color.WHITE);
        stage.addView(web, new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setUpWeb();
        openOurApp("/");
    }
}

