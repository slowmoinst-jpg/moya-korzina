package ru.moyakorzina.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.Gravity;
import android.view.InputDevice;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
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
import java.nio.charset.StandardCharsets;
import java.util.Iterator;
import java.util.Locale;

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
        "(function(hosts){"
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
      + "function note(m,u,code,t){try{if(!own(u))return;var path=tpl(new URL(u,location.href).pathname);"
      + " if(!CARTISH.test(path))return;if(window.__kzCalls.length<40)window.__kzCalls.push(m+' '+path+' '+code);"
      + " if(t)keep(path,t);}catch(e){}}"
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
     * касания (window.__kzLast): найти в теле поле с артикулом этой позиции и рядом
     * поле количества. Ответ — только имена полей и путь, без значений заголовков.
     */
    private static final String LEARN_JS =
        "(function(sku){try{var r=window.__kzLast;if(!r)return JSON.stringify({ok:false,why:'запроса корзины после касания не видно'});"
      + "var body;try{body=JSON.parse(r.b);}catch(e){return JSON.stringify({ok:false,why:'тело запроса не JSON'});}"
      + "var idPath=null,qtyPath=null,numeric=false;"
      + "function walk(o,path){if(idPath||o===null||typeof o!=='object')return;"
      + " for(var k in o){if(!Object.prototype.hasOwnProperty.call(o,k))continue;var v=o[k],p=path.concat([k]);"
      + "  if((typeof v==='string'||typeof v==='number')&&String(v)===String(sku)){idPath=p;numeric=(typeof v==='number');"
      + "   for(var q in o){if(/^(quantity|qty|count|amount|quantityinbasket|cnt)$/i.test(q)&&typeof o[q]==='number'){qtyPath=path.concat([q]);break;}}return;}"
      + "  if(v&&typeof v==='object')walk(v,p);}}"
      + "walk(body,[]);if(!idPath)return JSON.stringify({ok:false,why:'в запросе нет артикула '+sku});"
      + "window.__kzTpl={m:r.m,u:r.u,h:r.h,body:body,idPath:idPath,qtyPath:qtyPath,numeric:numeric};window.__kzRep={};"
      + "return JSON.stringify({ok:true,method:r.m,path:new URL(r.u,location.href).pathname,id:idPath.join('.'),qty:qtyPath?qtyPath.join('.'):null});"
      + "}catch(e){return JSON.stringify({ok:false,why:String(e).slice(0,100)});}})(%s)";

    /** Повторить выученный запрос для другой позиции: начать и вернуть сразу, ответ — в __kzRep. */
    private static final String REPLAY_JS =
        "(function(p){try{var t=window.__kzTpl;if(!t)return 'нет образца';"
      + "var b=JSON.parse(JSON.stringify(t.body));"
      + "function set(o,path,v){for(var i=0;i<path.length-1;i++)o=o[path[i]];o[path[path.length-1]]=v;}"
      + "set(b,t.idPath,t.numeric?Number(p.sku):String(p.sku));if(t.qtyPath)set(b,t.qtyPath,p.qty);"
      + "window.__kzRep=window.__kzRep||{};window.__kzRep[p.key]=null;"
      + "fetch(t.u,{method:t.m,headers:t.h,body:JSON.stringify(b),credentials:'include'})"
      + " .then(function(r){window.__kzRep[p.key]={ok:r.status>=200&&r.status<300,status:r.status,"
      + "  call:t.m+' '+new URL(t.u,location.href).pathname+' '+r.status};})"
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
      + "function label(e){return((e.getAttribute('aria-label')||'')+' '+(e.innerText||'')).replace(/\\s+/g,' ').trim();}"
      // Закреплённая полоса покупки (position fixed/sticky) — своя кнопка товара: у
      // Магнита на телефоне «94,99 ₽ [В корзину]» стоит внизу экрана поверх страницы
      // (замер владельца 18.10.2026). Каруселей закреплёнными не бывает, поэтому
      // такой полосе забор не нужен — только чтобы она была видна на экране.
      + "function pinned(e){for(var x=e;x&&x!==document.body;x=x.parentElement){var s=getComputedStyle(x).position;"
      + " if(s==='fixed'||s==='sticky')return true;}return false;}"
      + "function inside(e,below){var r=e.getBoundingClientRect();if(r.width<=0||r.height<=0)return false;"
      + " if(pinned(e))return r.bottom>0&&r.top<(window.innerHeight||100000);"
      + " var y=r.top+window.scrollY;if(below&&top!==null&&y<top)return false;return y<limit;}"
      + "if(p.gone&&!p.again&&h1){var g=new RegExp(p.gone,'i'),w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT),n;"
      + " while((n=w.nextNode())){var t=(n.nodeValue||'').replace(/\\s+/g,' ').trim().toLowerCase();"
      + "  if(!t||!g.test(t))continue;var el=n.parentElement;if(!el)continue;var r=el.getBoundingClientRect();"
      + "  if(r.width<=0||r.height<=0)continue;var y=r.top+window.scrollY;"
      + "  if((y>=top-200&&y<limit)||(pinned(el)&&r.top<(window.innerHeight||100000)))return JSON.stringify({gone:true});}}"
      + "var bySel=!p.again&&p.selectors&&p.selectors.length>0,nodes=[];"
      + "if(bySel)p.selectors.forEach(function(s){try{nodes=nodes.concat([].slice.call(document.querySelectorAll(s)));}catch(e){}});"
      + "var buttons=[].slice.call(document.querySelectorAll('button,[role=button]'));"
      + "if(!nodes.length){bySel=false;nodes=buttons;}"
      + "var pat=new RegExp(p.again?p.plus:p.add,'i'),best=null,bestY=Infinity;"
      + "function take(e){var y=e.getBoundingClientRect().top+window.scrollY;if(y<bestY){best=e;bestY=y;}}"
      + "nodes.forEach(function(e){if(e.disabled||!inside(e,!p.again))return;"
      + " if(!bySel){var a=(e.getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim(),t=(e.innerText||'').replace(/\\s+/g,' ').trim();"
      + "  if(!(pat.test(a)||pat.test(t)||pat.test((a+' '+t).trim())))return;}"
      + " take(e);});"
      + "if(!best&&!p.again)buttons.forEach(function(e){if(e.disabled||!inside(e,true))return;var l=label(e);"
      + " if(l.length>60||!/в корзину|добавить|купить/i.test(l)||/перейти|в корзине|оформ|избранн|сравн/i.test(l))return;take(e);});"
      + "if(!best){var near=[];buttons.forEach(function(e){if(near.length<6&&inside(e,true)){var l=label(e).slice(0,30);if(l)near.push(l);}});"
      + " return JSON.stringify({found:false,h1:!!h1,near:near});}"
      + "best.setAttribute(MARK,'1');best.scrollIntoView({block:'center',inline:'center'});"
      + "return JSON.stringify({found:true});"
      + "}catch(e){return JSON.stringify({found:false,error:String(e).slice(0,120)});}})(%s)";

    /**
     * Что сайт отправил после касания: артикулы в теле последнего запроса записи в
     * корзину (window.__kzLast). Есть поля артикула, но нашего среди них нет — значит
     * легло ДРУГОЕ: так сметана 25% чуть не сошла за 15% (17.10.2026).
     */
    private static final String VERIFY_JS =
        "(function(sku){try{var r=window.__kzLast;if(!r||typeof r.b!=='string')return JSON.stringify({known:false});"
      + "var body;try{body=JSON.parse(r.b);}catch(e){return JSON.stringify({known:false});}"
      + "var ids=[];(function walk(o){if(!o||typeof o!=='object')return;for(var k in o){if(!Object.prototype.hasOwnProperty.call(o,k))continue;"
      + " var v=o[k];if((typeof v==='string'||typeof v==='number')&&/^(product_?id|sku|article|goods_?id|item_?id|plu)$/i.test(k))ids.push(String(v));"
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
      + "var els=[].slice.call(document.querySelectorAll('a,button,[role=button],[role=link],[role=tab]')),best=null,bestS=9;"
      // Детали значка (svg и всё внутри) не в счёт: у «Корзины» нижнего меню Магнита
      // картинка из многих частей, и по общему числу потомков кнопка выглядела плиткой.
      + "function kids(e){return e.querySelectorAll('*').length-e.querySelectorAll('svg, svg *').length;}"
      + "for(var j=0;j<els.length;j++){var e=els[j];if(kids(e)>8)continue;"
      + " var al=(e.getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim(),tx=(e.innerText||'').replace(/\\s+/g,' ').trim();"
      + " var t=(al+' '+tx).trim();if(t.length>40||!/корзин/i.test(t)||ADD.test(al)||ADD.test(tx)||NEVER.test(t))continue;"
      + " var r=e.getBoundingClientRect();if(r.width<=0||r.height<=0||r.bottom<=0||r.top>=H)continue;"
      + " var s=/^корзина$/i.test(tx)?0:(/^корзина/i.test(tx)?1:2);if(s<bestS){best=e;bestS=s;}}"
      + "if(best){best.setAttribute(MARK,'1');return JSON.stringify({button:true});}"
      + "var links=[].slice.call(document.querySelectorAll('a[href]'));"
      + "for(var i=0;i<links.length;i++){var a=links[i];try{var u=new URL(a.getAttribute('href'),location.href);"
      + " if(!own(u.hostname)||!/(^|\\/)(cart|basket|korzina)(\\/|$)/i.test(u.pathname))continue;"
      + " if(kids(a)>8)continue;return JSON.stringify({href:u.href});}catch(e){}}"
      // Дороги не нашлось — для отчёта: что на странице похоже на корзину.
      + "var near=[];[].slice.call(document.querySelectorAll('a,button,[role=button]')).forEach(function(e){if(near.length>=5)return;"
      + " var l=((e.getAttribute('aria-label')||'')+' '+(e.innerText||'')).replace(/\\s+/g,' ').trim(),hr=e.getAttribute('href')||'';"
      + " if(/корзин|cart|basket/i.test(l+' '+hr))near.push((l||hr).slice(0,30));});"
      + "return JSON.stringify({near:near});}catch(e){return JSON.stringify({error:String(e).slice(0,120)});}})(%s)";

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
      + " ['discount',/(скидк|выгод|экономи|промокод)/i],['delivery',/доставк/i],"
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
    private static final int CART_SETTLE_MS = 3000;
    private static final int READY_POLL_MS = 600;
    private static final int READY_LIMIT_MS = 30000;

    private WebView web;
    private ProgressBar progress;
    private View chainPanel;
    private TextView chainStatus;
    private Button saveLoginBtn;
    private Button saveOffersBtn;
    private Button recordBtn;
    private Button cartBtn;
    private boolean recording = false;
    // volatile: читает его и мост, а он живёт на своём потоке (JavaBridge).
    private volatile int currentChain = -1;
    private CartRun cartRun;
    private final Handler handler = new Handler(Looper.getMainLooper());

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
     * мост молчит: ни адреса сервера, ни открытия страниц по чужой просьбе.
     */
    private final class KorzinaBridge {
        /** Кто на связи: сайт сверяет версию, прежде чем просить о новом. */
        @JavascriptInterface
        public String info() {
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
            if (currentChain >= 0) return false;
            for (int i = 0; i < CHAINS.length; i++) {
                if (CHAINS[i][1].equals(code)) {
                    final int idx = i;
                    handler.post(() -> openChain(idx));
                    return true;
                }
            }
            return false;
        }

        /** Вернуться на наш сайт. Путь только свой: мост чужих адресов не открывает. */
        @JavascriptInterface
        public void openApp(String path) {
            if (currentChain >= 0) return;
            final String target = (path != null && path.startsWith("/") && !path.contains("://"))
                ? path : "/";
            handler.post(() -> openOurApp(target));
        }
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        if (SERVER.isEmpty()) {
            TextView lost = new TextView(this);
            lost.setPadding(dp(24), dp(24), dp(24), dp(24));
            lost.setTextSize(16f);
            lost.setText("Адрес сервера не задан при сборке (-PkorzinaServer).");
            setContentView(lost);
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
        web = new WebView(this);
        root.addView(web, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        chainPanel = buildChainPanel();
        chainPanel.setVisibility(View.GONE);
        root.addView(chainPanel, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        setContentView(root);
        setUpWeb();
        openOurApp("/");
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

        cartBtn = new Button(this);
        cartBtn.setText("Собрать");
        cartBtn.setTextSize(12f);
        cartBtn.setOnClickListener(v -> toggleCart());
        row.addView(cartBtn, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.1f));

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

        CookieManager jar = CookieManager.getInstance();
        jar.setAcceptCookie(true);
        jar.setAcceptThirdPartyCookies(web, true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                // Режим следует за адресом, а не за тем, кто его открыл: человек
                // мог вернуться «назад» на наш сайт сам — и панель должна уйти
                // вместе со страницей сети, а не висеть поверх корзины.
                if (currentChain >= 0 && !isHostOf(currentChain, url)) {
                    exitChainMode();
                }
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
            public void onReceivedSslError(WebView view, android.webkit.SslErrorHandler h,
                                           android.net.http.SslError error) {
                // Сертификаты Минцифры у части российских магазинов — только для их сайтов.
                // Наш сервер и чужие адреса с битым сертификатом не открываются никогда:
                // там ездит сессия владельца.
                if (currentChain >= 0 && isChainHost(error.getUrl())) h.proceed();
                else h.cancel();
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int p) {
                progress.setVisibility(p < 100 ? View.VISIBLE : View.GONE);
                progress.setProgress(p);
            }
        });
    }

    // ── Режимы: наш сайт и страница сети ──
    private void openOurApp(String path) {
        exitChainMode();
        web.loadUrl(SERVER + path);
    }

    private void openChain(int idx) {
        currentChain = idx;
        stopRecording();
        chainPanel.setVisibility(View.VISIBLE);
        setChainButtons(true);
        say("Открываю " + CHAINS[idx][0] + "…", false);
        web.loadUrl(CHAINS[idx][2]);
    }

    /** Уйти со страницы сети обратно к сайту — по истории, если она ещё помнит нас. */
    private void leaveChain() {
        if (web.canGoBack()) {
            // Режим снимется сам в onPageFinished, когда адрес перестанет быть
            // адресом сети: два переключателя одного режима разъезжаются.
            web.goBack();
        } else {
            openOurApp("/");
        }
    }

    private void exitChainMode() {
        if (cartRun != null) stopRun("Сборка остановлена: вы ушли со страницы сети.");
        currentChain = -1;
        stopRecording();
        if (chainPanel != null) chainPanel.setVisibility(View.GONE);
    }

    private void setChainButtons(boolean on) {
        saveLoginBtn.setEnabled(on);
        saveOffersBtn.setEnabled(on);
        recordBtn.setEnabled(on);
        cartBtn.setEnabled(on);
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
     */
    private static final class CartRun {
        final int chain;
        final String code;
        final String name;
        JSONObject rules = new JSONObject();
        JSONArray lines = new JSONArray();
        double ourTotal = 0;
        int at = 0;        // какую позицию кладём
        int clicks = 1;    // сколько касаний у неё
        int put = 0;       // сколько уже сделано
        final JSONArray items = new JSONArray();
        boolean stopped = false;
        // Запрос сети «положить в корзину» выучен по первому касанию (LEARN_JS): дальше
        // позиции кладутся им, без загрузки карточек. Пробуем выучить один раз.
        boolean learned = false;
        boolean learnTried = false;
        String how = "tap";          // чем легла текущая позиция: касанием или запросом
        final long startedAt = SystemClock.uptimeMillis();
        String cartHint = "";        // дорога к корзине не нашлась: что похожее было на странице

        CartRun(int chain) {
            this.chain = chain;
            this.code = CHAINS[chain][1];
            this.name = CHAINS[chain][0];
        }
    }

    private boolean alive(CartRun r) {
        return r != null && r == cartRun && !r.stopped;
    }

    private void toggleCart() {
        if (currentChain < 0) return;
        if (cartRun != null) {
            stopRun("Сборка остановлена.");
            return;
        }
        final CartRun r = new CartRun(currentChain);
        cartRun = r;
        setChainButtons(false);
        cartBtn.setEnabled(true);
        cartBtn.setText("Стоп");
        say("Беру у сервера, что положить в " + r.name + "…", false);
        JSONObject body = new JSONObject();
        try { body.put("store", r.code); } catch (Exception ignored) { }
        call("/api/device/cartplan", body, (status, text) -> {
            if (!alive(r)) return;
            if (status != 200) {
                failRun(serverError(status, text));
                return;
            }
            try {
                JSONObject plan = new JSONObject(text);
                JSONObject rules = plan.optJSONObject("rules");
                JSONArray lines = plan.optJSONArray("lines");
                r.rules = rules != null ? rules : new JSONObject();
                r.lines = lines != null ? lines : new JSONArray();
                r.ourTotal = plan.optDouble("total", 0);
                if (r.lines.length() == 0) {
                    offerThisCard(r, plan.optString("note", ""));
                    return;
                }
                say(r.name + ": позиций " + r.lines.length() + ", начинаю", false);
                nextLine(r);
            } catch (Exception e) {
                failRun("Сервер ответил непонятно: " + e.getMessage());
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
                    failRun("Не получилось: " + e.getMessage());
                }
            })
            .setNegativeButton("Отмена", (d, w) -> failRun(why))
            .setOnCancelListener(d -> failRun(why))
            .show();
    }

    private void nextLine(final CartRun r) {
        if (!alive(r)) return;
        if (r.at >= r.lines.length()) {
            openCart(r);
            return;
        }
        JSONObject line = r.lines.optJSONObject(r.at);
        r.clicks = Math.max(1, line == null ? 1 : line.optInt("clicks", 1));
        r.put = 0;
        r.how = "tap";
        if (r.learned && line != null && !line.optString("sku", "").isEmpty()) {
            replayLine(r, line);
            return;
        }
        cardLine(r, line);
    }

    /** Позиция касаниями: открыть её карточку и нажать «В корзину». */
    private void cardLine(final CartRun r, JSONObject line) {
        if (!alive(r)) return;
        String url = line == null ? "" : line.optString("url", "");
        r.how = "tap";
        if (url.isEmpty() || !isHostOf(r.chain, url)) {
            lineDone(r, false, url.isEmpty() ? "у позиции нет адреса карточки"
                : "адрес карточки принадлежит не этой сети", new JSONArray());
            return;
        }
        say("Кладу " + (r.at + 1) + " из " + r.lines.length() + ": " + line.optString("name", ""), false);
        if (url.equals(web.getUrl())) {
            // Карточка уже открыта («Положить этот»): перезагрузка посреди касаний
            // только сбила бы её, а страница и так та самая.
            handler.postDelayed(() -> onCard(r), 300);
            return;
        }
        web.loadUrl(url);
        whenReady(r, url, null, SETTLE_MS, () -> onCard(r));
    }

    /** Где страница и в каком она виде: путь, readyState и есть ли заголовок h1. */
    private static final String STATE_JS =
        "(function(){return JSON.stringify({p:location.pathname,s:document.readyState,h:!!document.querySelector('h1')});})()";

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
     * READY_LIMIT_MS — идём дальше.
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
                web.evaluateJavascript(STATE_JS, value -> {
                    if (!alive(r)) return;
                    JSONObject st = asObject(value);
                    String path = trimSlash(st.optString("p", ""));
                    String state = st.optString("s", "");
                    long now = SystemClock.uptimeMillis();
                    boolean here = !path.isEmpty() && (target != null ? target.equals(path)
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
        web.evaluateJavascript(watchScript(r), null);
        tapAdd(r, false);
    }

    private void tapAdd(final CartRun r, final boolean again) {
        if (!alive(r)) return;
        web.evaluateJavascript(pickScript(r, again), value -> {
            if (!alive(r)) return;
            JSONObject got = asObject(value);
            if (got.optBoolean("gone")) {
                lineDone(r, false, "карточка говорит, что товара нет в наличии", new JSONArray());
                return;
            }
            if (!got.optBoolean("found")) {
                String why = r.put == 0
                    ? (got.optBoolean("h1") ? "на карточке не нашлось кнопки «в корзину»"
                                            : "страница не похожа на карточку товара: нет заголовка")
                    : "положено " + r.put + " из " + r.clicks + ": кнопки «ещё одну» не нашлось";
                // Какие кнопки были в блоке покупки — по ним видно, как сеть зовёт свою.
                JSONArray near = got.optJSONArray("near");
                if (near != null && near.length() > 0) {
                    StringBuilder seen = new StringBuilder();
                    for (int i = 0; i < near.length(); i++) {
                        seen.append(i > 0 ? ", " : "").append('«').append(near.optString(i)).append('»');
                    }
                    why += " (рядом: " + seen + ")";
                }
                checkLine(r, why);
                return;
            }
            // Прокрутке к кнопке нужно встать, прежде чем спрашивать, где кнопка.
            handler.postDelayed(() -> tapMarked(r), 450);
        });
    }

    private void tapMarked(final CartRun r) {
        if (!alive(r)) return;
        web.evaluateJavascript(COORDS_JS, value -> {
            if (!alive(r)) return;
            if (!tapAt(asObject(value))) {
                checkLine(r, r.put == 0 ? "кнопка «в корзину» ушла из-под пальца — карточка перерисовалась"
                                        : "положено " + r.put + " из " + r.clicks + ": карточка перерисовалась");
                return;
            }
            r.put++;
            int pause = r.rules.optInt("click_pause_ms", 700);
            if (r.put < r.clicks) handler.postDelayed(() -> tapAdd(r, true), pause + 500);
            else handler.postDelayed(() -> checkLine(r, ""), pause + 1200);
        });
    }

    /**
     * Что сеть сделала после касаний. Последнее слово — за её запросами к корзине:
     * все отказали — не легло; у Магнита ни одного запроса — тоже не легло (замер
     * 20.09.2026, правило SENDS_CART серверной передачи). Иначе судит число касаний.
     */
    private void checkLine(final CartRun r, final String failure) {
        if (!alive(r)) return;
        web.evaluateJavascript(CALLS_JS, value -> {
            if (!alive(r)) return;
            JSONArray calls = asObject(value).optJSONArray("calls");
            if (calls == null) calls = new JSONArray();
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
        web.evaluateJavascript(VERIFY_JS.replace("%s", JSONObject.quote(sku)), value -> {
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
        if (!alive(r)) return;
        JSONObject line = r.lines.optJSONObject(r.at);
        final String sku = line == null ? "" : line.optString("sku", "");
        final String how = r.how;
        try {
            r.items.put(new JSONObject()
                .put("name", line == null ? "" : line.optString("name", ""))
                .put("sku", sku)
                .put("ok", ok).put("why", why == null ? "" : why)
                .put("put", r.put).put("times", r.clicks).put("how", how)
                .put("calls", calls == null ? new JSONArray() : calls));
        } catch (Exception ignored) { }
        r.at++;
        r.how = "tap";
        // Первая позиция легла касанием — выучить по ней запрос сети и дальше класть им.
        if (ok && "tap".equals(how) && !r.learned && !r.learnTried && !sku.isEmpty()
                && r.at < r.lines.length()) {
            r.learnTried = true;
            learn(r, sku);
            return;
        }
        handler.postDelayed(() -> nextLine(r), pauseAfter(r));
    }

    /** Пауза до следующей позиции: запросом — темп запросов сервера, касаниями — темп покупателя. */
    private static int pauseAfter(CartRun r) {
        return r.learned ? Math.max(300, r.rules.optInt("request_pause_ms", 1000))
                         : Math.max(1300, r.rules.optInt("pause_ms", 1300));
    }

    /**
     * Выучить запрос сети по только что легшей позиции. Не вышло — не беда: остальные
     * позиции лягут касаниями, как раньше, а в строке состояния сказано, почему.
     */
    private void learn(final CartRun r, String sku) {
        web.evaluateJavascript(LEARN_JS.replace("%s", JSONObject.quote(sku)), value -> {
            if (!alive(r)) return;
            JSONObject got = asObject(value);
            if (got.optBoolean("ok")) {
                r.learned = true;
                say(r.name + ": дальше кладу запросом сети " + got.optString("method") + " "
                    + got.optString("path"), false);
            } else {
                say(r.name + ": запрос сети не выучен (" + got.optString("why", "?") + "), дальше касаниями", false);
            }
            handler.postDelayed(() -> nextLine(r), pauseAfter(r));
        });
    }

    /**
     * Позиция запросом сети: тот же запрос, что сайт отправил после касания, с артикулом
     * и количеством этой позиции. Отправляет его сама страница, со своей сессией.
     * Сеть не приняла — эта же позиция идёт касаниями, и дальше уже только касаниями.
     */
    private void replayLine(final CartRun r, final JSONObject line) {
        final String sku = line.optString("sku", "");
        say("Кладу " + (r.at + 1) + " из " + r.lines.length() + " запросом: " + line.optString("name", ""), false);
        final String key = "k" + r.at;
        JSONObject p = new JSONObject();
        try {
            p.put("sku", sku).put("qty", r.clicks).put("key", key);
        } catch (Exception ignored) { }
        web.evaluateJavascript(REPLAY_JS.replace("%s", p.toString()), started -> {
            if (!alive(r)) return;
            final long until = SystemClock.uptimeMillis() + 15000;
            handler.postDelayed(new Runnable() {
                @Override
                public void run() {
                    if (!alive(r)) return;
                    final Runnable again = this;
                    web.evaluateJavascript(REPLAY_POLL_JS.replace("%s", JSONObject.quote(key)), value -> {
                        if (!alive(r)) return;
                        JSONObject res = asObject(value);
                        if (res.length() == 0 && SystemClock.uptimeMillis() < until) {
                            handler.postDelayed(again, 300);
                            return;
                        }
                        if (res.optBoolean("ok")) {
                            r.put = r.clicks;
                            r.how = "request";
                            JSONArray calls = new JSONArray();
                            calls.put(res.optString("call", ""));
                            lineDone(r, true, "", calls);
                            return;
                        }
                        r.learned = false;
                        say(r.name + ": сеть не приняла запрос (" + res.optInt("status", 0)
                            + "), дальше касаниями", true);
                        cardLine(r, line);
                    });
                }
            }, 300);
        });
    }

    /** Открыть корзину сети: своей ссылкой со страницы, кнопкой «Корзина» или адресом из наряда. */
    private void openCart(final CartRun r) {
        if (!alive(r)) return;
        say(r.name + ": открываю корзину сети", false);
        web.evaluateJavascript(findCartScript(r), value -> {
            if (!alive(r)) return;
            JSONObject got = asObject(value);
            String href = got.optString("href", "");
            if (!href.isEmpty() && isHostOf(r.chain, href)) {
                web.loadUrl(href);
                whenReady(r, href, null, CART_SETTLE_MS, () -> readCart(r));
                return;
            }
            if (got.optBoolean("button")) {
                final String from = pathOf(web.getUrl());
                handler.postDelayed(() -> web.evaluateJavascript(COORDS_JS, at -> {
                    if (!alive(r)) return;
                    tapAt(asObject(at));
                    whenReady(r, null, from, CART_SETTLE_MS, () -> readCart(r));
                }), 450);
                return;
            }
            JSONArray near = got.optJSONArray("near");
            if (near != null && near.length() > 0) r.cartHint = near.toString();
            JSONArray pages = r.rules.optJSONArray("cart_pages");
            String page = pages != null && pages.length() > 0 ? pages.optString(0, "") : "";
            if (!page.isEmpty()) {
                web.loadUrl(page);
                whenReady(r, page, null, CART_SETTLE_MS, () -> readCart(r));
                return;
            }
            readCart(r);
        });
    }

    private void readCart(final CartRun r) {
        if (!alive(r)) return;
        // Сторож нужен и здесь: страница корзины пересчитывает её своими запросами.
        web.evaluateJavascript(watchScript(r), null);
        web.evaluateJavascript(READ_JS, value -> {
            if (!alive(r)) return;
            finishRun(r, asObject(value), "");
        });
    }

    private void stopRun(String why) {
        CartRun r = cartRun;
        if (r == null) return;
        r.stopped = true;
        finishRun(r, new JSONObject(), why);
    }

    private void failRun(String why) {
        cartRun = null;
        cartBtn.setText("Собрать");
        setChainButtons(currentChain >= 0);
        say(why, true);
    }

    /** Показать, что вышло, и отдать отчёт серверу. Корзину не трогаем: оформляет человек. */
    private void finishRun(final CartRun r, JSONObject cart, String stoppedWhy) {
        cartRun = null;
        cartBtn.setText("Собрать");
        setChainButtons(currentChain >= 0);

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

        long seconds = Math.round((SystemClock.uptimeMillis() - r.startedAt) / 1000.0);
        new AlertDialog.Builder(this)
            .setTitle(r.name + ": легло " + landed + " из " + r.items.length() + " за " + seconds + " с")
            .setMessage(msg.toString())
            .setPositiveButton("Готово", null)
            .show();
        say(r.name + ": легло " + landed + " из " + r.items.length() + ", отправляю отчёт", false);

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
        try {
            JSONObject body = new JSONObject()
                .put("store", r.code)
                .put("app_version", BuildConfig.VERSION_NAME)
                .put("items", r.items)
                .put("cart_page", cart.optString("page", ""))
                .put("our_total", r.ourTotal)
                .put("page_totals", pageTotals)
                .put("api_totals", api == null ? new JSONObject() : api);
            if (cart.has("count") && !cart.isNull("count")) body.put("cart_count", cart.optInt("count"));
            final String name = r.name;
            call("/api/device/cartreport", body, (status, text) -> {
                if (cartRun != null) return;   // уже идёт новая сборка — её строка важнее
                if (status == 200) say(name + ": отчёт на сервере", false);
                else say(name + ": отчёт не ушёл — " + serverError(status, text), true);
            });
        } catch (Exception ignored) { }
    }

    private String watchScript(CartRun r) {
        return WATCH_JS.replace("%s", hostsOf(r).toString());
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

    /**
     * Коснуться точки страницы так, как касается палец: событие экрана в WebView, а не
     * щелчок скрипта. Точки CSS переводятся в пиксели вида по ширине видимой области.
     */
    private boolean tapAt(JSONObject at) {
        if (!at.optBoolean("found")) return false;
        double vw = at.optDouble("vw", 0);
        if (vw <= 0 || web.getWidth() <= 0) return false;
        double k = web.getWidth() / vw;
        final float x = (float) (at.optDouble("x") * k);
        final float y = (float) (at.optDouble("y") * k);
        if (x < 0 || y < 0 || x > web.getWidth() || y > web.getHeight()) return false;
        final long down = SystemClock.uptimeMillis();
        MotionEvent press = MotionEvent.obtain(down, down, MotionEvent.ACTION_DOWN, x, y, 0);
        press.setSource(InputDevice.SOURCE_TOUCHSCREEN);
        web.dispatchTouchEvent(press);
        press.recycle();
        handler.postDelayed(() -> {
            MotionEvent release = MotionEvent.obtain(down, SystemClock.uptimeMillis(),
                                                     MotionEvent.ACTION_UP, x, y, 0);
            release.setSource(InputDevice.SOURCE_TOUCHSCREEN);
            web.dispatchTouchEvent(release);
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

    private static String money(double value) {
        return String.format(new Locale("ru"), "%,.2f ₽", value);
    }

    private interface Reply {
        void got(int status, String text);
    }

    /** POST на наш сервер с сессией «Моей корзины»; ответ — на главном потоке. */
    private void call(String path, JSONObject body, Reply reply) {
        final String session = CookieManager.getInstance().getCookie(SERVER);
        new Thread(() -> {
            int code = 0;
            String text = "";
            try {
                HttpURLConnection conn = (HttpURLConnection) new URL(SERVER + path).openConnection();
                conn.setRequestMethod("POST");
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(60000);
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
            JSONArray cookies = new JSONArray();
            for (String piece : raw.split(";")) {
                piece = piece.trim();
                int eq = piece.indexOf('=');
                if (eq < 1) continue;
                cookies.put(new JSONObject().put("name", piece.substring(0, eq).trim())
                                            .put("value", piece.substring(eq + 1)));
            }
            JSONObject body = new JSONObject()
                .put("store", chain).put("host", hostOf(url)).put("cookies", cookies);
            say("Сохраняю вход…", false);
            post("/api/handoff", body, CHAINS[currentChain][0] + " — вход сохранён");
        } catch (Exception e) {
            say("Не получилось собрать вход: " + e.getMessage(), true);
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
        // Режим снимет onPageFinished по адресу: вернулись на наш сайт — панель ушла.
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }
}
