package ru.moyakorzina.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.res.ColorStateList;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.Settings;
import android.text.SpannableString;
import android.text.Spanned;
import android.text.style.StyleSpan;
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
import android.webkit.ValueCallback;
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
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;
import org.json.JSONTokener;

import ru.moyakorzina.app.SignDrawable.Sign;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TimeZone;
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
     * Журнал приложения с хроникой (1.54). Всё, что приложение пишет в logcat, остаётся ещё и в кольце последних
     * строк; кольцо переживает смерть процесса (отдельный файл памяти телефона) и уходит на сервер с отчётом
     * Пятёрочки. Телефон владельца отладчиком не посмотреть: 14.11.2026 Пятёрочка пропустила пять нажатий подряд
     * (три другие сети уложились в 3,2 с), а журнал сервера не показал, что делало окно сети. Класс назван так же,
     * как android.util.Log, поэтому вызовы Log.i(TAG, …) по всему файлу остались как были.
     */
    private static final class Log {
        private static final int RING = 600;
        private static final int LINE = 200;
        private static final int SAVED_LINES = 200;
        private static final long SAVE_EVERY_MS = 4000;
        private static final String SAVED = "ring";
        private static final ArrayDeque<String> ring = new ArrayDeque<>();
        private static final SimpleDateFormat clock = new SimpleDateFormat("HH:mm:ss.SSS", Locale.US);
        private static SharedPreferences saved;
        private static long savedAt = 0;
        private static boolean attached = false;

        static int i(String tag, String msg) {
            keep(msg);
            return android.util.Log.i(tag, msg);
        }

        static int w(String tag, String msg) {
            keep(msg);
            return android.util.Log.w(tag, msg);
        }

        static int w(String tag, String msg, Throwable e) {
            keep(msg + " [" + e + "]");
            return android.util.Log.w(tag, msg, e);
        }

        static int e(String tag, String msg) {
            keep(msg);
            return android.util.Log.e(tag, msg);
        }

        /** Строка в кольцо — со временем телефона до миллисекунд. */
        static synchronized void keep(String msg) {
            try {
                long now = System.currentTimeMillis();
                String text = msg == null ? "" : msg.replace('\n', ' ');
                if (text.length() > LINE) text = text.substring(0, LINE);
                ring.addLast(clock.format(new Date(now)) + " " + text);
                while (ring.size() > RING) ring.removeFirst();
                if (saved != null && now - savedAt >= SAVE_EVERY_MS) save(now, false);
            } catch (RuntimeException ignored) {
                // хроника не должна ронять приложение
            }
        }

        /** Кольцо — в память: процесс могли убить посреди сборки, и хроника нужна именно тогда. */
        private static void save(long now, boolean sync) {
            savedAt = now;
            StringBuilder all = new StringBuilder();
            int skip = Math.max(0, ring.size() - SAVED_LINES), k = 0;
            for (String line : ring) {
                if (k++ >= skip) all.append(line).append('\n');
            }
            SharedPreferences.Editor edit = saved.edit().putString(SAVED, all.toString());
            if (sync) edit.commit();
            else edit.apply();
        }

        /**
         * При запуске: хроника прошлого запуска (если процесс убили — это то, что он успел) встаёт в начало кольца,
         * а вылет приложения пишется в него же перед тем, как система его закроет.
         */
        static synchronized void attach(SharedPreferences prefs) {
            if (attached) return;
            attached = true;
            saved = prefs;
            String old = prefs.getString(SAVED, "");
            if (!old.isEmpty()) {
                for (String line : old.split("\n")) {
                    if (!line.isEmpty()) ring.addLast(line);
                }
                ring.addLast(clock.format(new Date()) + " — приложение запущено заново, хроника выше от прошлого запуска —");
                while (ring.size() > RING) ring.removeFirst();
            }
            final Thread.UncaughtExceptionHandler was = Thread.getDefaultUncaughtExceptionHandler();
            Thread.setDefaultUncaughtExceptionHandler((thread, ex) -> {
                try {
                    StackTraceElement[] st = ex.getStackTrace();
                    StringBuilder top = new StringBuilder();
                    for (int n = 0; n < Math.min(4, st.length); n++) {
                        top.append(" <- ").append(st[n].getClassName().replaceAll("^.*\\.", "")).append('.')
                           .append(st[n].getMethodName()).append(':').append(st[n].getLineNumber());
                    }
                    keep("ВЫЛЕТ в потоке " + thread.getName() + ": " + ex + top);
                    synchronized (Log.class) {
                        save(System.currentTimeMillis(), true);
                    }
                } catch (Throwable ignored) { }
                if (was != null) was.uncaughtException(thread, ex);
            });
        }

        /**
         * Хроника для отчёта сети (1.54): первая строка — часы телефона и пояс, дальше до own последних строк про
         * эту сеть (её имя или код в строке) и до any последних любых, по порядку времени.
         */
        static synchronized JSONArray tail(String name, String code, int own, int any) {
            JSONArray out = new JSONArray();
            try {
                String[] lines = ring.toArray(new String[0]);
                boolean[] pick = new boolean[lines.length];
                int mine = 0;
                for (int k = lines.length - 1; k >= 0 && mine < own; k--) {
                    if (lines[k].contains(name) || lines[k].contains(code)) {
                        pick[k] = true;
                        mine++;
                    }
                }
                for (int k = lines.length - 1, n = 0; k >= 0 && n < any; k--, n++) pick[k] = true;
                long now = System.currentTimeMillis();
                int offset = TimeZone.getDefault().getOffset(now) / 60000;
                out.put(clock.format(new Date(now)) + " отчёт; часы телефона UTC" + (offset < 0 ? "-" : "+")
                        + String.format(Locale.US, "%02d:%02d", Math.abs(offset) / 60, Math.abs(offset) % 60)
                        + ", строк в кольце " + lines.length);
                for (int k = 0; k < lines.length; k++) {
                    if (pick[k]) out.put(lines[k]);
                }
            } catch (RuntimeException e) {
                out.put("хроника не собралась: " + e);       // отчёт без хроники лучше, чем без отчёта
            }
            return out;
        }
    }

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

    static final String SERVER = BuildConfig.KORZINA_SERVER.replaceAll("/+$", "");
    private final ApkUpdater apkUpdater = new ApkUpdater(this);

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

    /** Сколько ждать ответа сети с итогом корзины (api_total), мс. */
    private static final int API_WAIT_MS = 4000;
    private static final String API_TOTAL_JS =
        "(function(rule){try{var t=window.__kzTpl||{},h={};"
      + "Object.keys(t.h||{}).forEach(function(k){if(!/^content-(type|length)$/i.test(k))h[k]=t.h[k];});"
      + "var u=new URL(rule.path,t.u||location.href).href;window.__kzApi=null;"
      + "fetch(u,{method:'GET',headers:h,credentials:'include'}).then(function(r){"
      + " return r.text().then(function(x){var out={ok:r.status>=200&&r.status<300,status:r.status,fields:{}};"
      + "  try{var o=JSON.parse(x);['total','base','sale','count'].forEach(function(n){var p=rule[n];if(!p)return;"
      + "   var v=p.split('.').reduce(function(a,k){return a==null?undefined:a[k];},o);"
      + "   if(typeof v==='string'&&/^-?\\d+(?:[.,]\\d+)?$/.test(v))v=parseFloat(v.replace(',','.'));"
      + "   if(typeof v==='number'&&isFinite(v))out.fields[p]=v;});}catch(e){out.ok=false;}"
      + "  window.__kzApi=out;});})"
      + ".catch(function(e){window.__kzApi={ok:false,status:0};});"
      + "return 'started';}catch(e){return 'ошибка: '+String(e).slice(0,80);}})(%s)";
    private static final String API_POLL_JS =
        "(function(){var r=window.__kzApi;return r?JSON.stringify(r):'';})()";

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
     * Кнопка «−» на карточке своего товара (приложение 1.19): ею снимаем положенное самим
     * приложением. Только на той карточке, что открывали (путь или артикул в адресе), только
     * в зоне товара, как «В корзину», и никогда «+», «В корзину», «Очистить», «Оформить»,
     * «Оплатить». В степпере — кнопка по правилу minus, без подписи — первая, если она не «+».
     * Число в степпере уходит в ответ: без него касание нечем проверить, и снимать нельзя.
     */
    private static final String PICK_MINUS_JS =
        "(function(p){try{"
      + "var MARK='data-korzina-add',FENCE=/похож|рекоменд|с этим|покупают|понравит|аналог|вместе|смотрели|популярн|ещё товары|еще товары/i;"
      + "[].slice.call(document.querySelectorAll('['+MARK+']')).forEach(function(e){e.removeAttribute(MARK);});"
      + "function dec(x){try{return decodeURIComponent(x||'').replace(/\\/+$/,'');}catch(e){return x||'';}}"
      + "if(p.path&&dec(location.pathname)!==dec(p.path)&&!(p.sku&&p.sku.length>=4&&location.href.indexOf(p.sku)>=0))"
      + " return JSON.stringify({found:false,moved:true});"
      + "var h1=document.querySelector('h1');"
      + "var top=h1?h1.getBoundingClientRect().top+window.scrollY:null;"
      + "var limit=(top!==null&&p.below)?top+p.below:Infinity;"
      + "if(top!==null)[].slice.call(document.querySelectorAll('h2,h3,h4,[role=heading]')).forEach(function(h){"
      + " var t=(h.innerText||'').replace(/\\s+/g,' ').trim();if(!t||t.length>40||!FENCE.test(t))return;"
      + " var y=h.getBoundingClientRect().top+window.scrollY;if(y>top&&y<limit)limit=y;});"
      + "function pinned(e){for(var x=e;x&&x!==document.body;x=x.parentElement){var s=getComputedStyle(x).position;"
      + " if(s==='fixed'||s==='sticky')return true;}return false;}"
      + "function inside(e){var r=e.getBoundingClientRect();if(r.width<=0||r.height<=0)return false;"
      + " if(pinned(e))return r.bottom>0&&r.top<(window.innerHeight||100000);"
      + " var y=r.top+window.scrollY;if(top!==null&&y<top)return false;return y<limit;}"
      + "var NEVER=/очист|оформ|оплат|clear|checkout/i;"
      + "var pat=new RegExp(p.minus||'^[−–-]$|уменьш|minus|decrease|удал|убра|remove|delete','i');"
      + "var plus=new RegExp(p.plus||'увелич|добавить ещё|плюс|^[+]$','i');"
      + "var add=new RegExp(p.add||'^(?:в корзину|добавить в корзину|купить)$','i');"
      + "function label(e){return((e.getAttribute('aria-label')||'')+' '+(e.getAttribute('title')||'')+' '+(e.innerText||'')).replace(/\\s+/g,' ').trim();}"
      + "function bad(l){return NEVER.test(l)||plus.test(l)||add.test(l);}"
      + "function getCount(s){var inp=s.querySelector('input');"
      + " if(inp&&inp.value){var v=parseFloat(inp.value.replace(',','.'));if(!isNaN(v))return v;}"
      + " var m=(s.innerText||'').replace(/\\s+/g,' ').match(/(\\d+(?:[.,]\\d+)?)/);"
      + " return m?parseFloat(m[1].replace(',','.')):null;}"
      + "var best=null,count=null;"
      + "[].slice.call(document.querySelectorAll('[class*=stepper],[class*=Stepper]')).forEach(function(s){"
      + " if(best||!inside(s))return;"
      + " var bs=[].slice.call(s.querySelectorAll('button,[role=button]')).filter(function(e){"
      + "  var q=e.getBoundingClientRect();return !e.disabled&&q.width>0&&q.height>0;});"
      + " var m=bs.filter(function(e){var l=label(e);return pat.test(l)&&!bad(l);})[0];"
      + " if(!m&&bs.length>=2&&!bad(label(bs[0])))m=bs[0];"
      + " if(m){best=m;count=getCount(s);}});"
      // Без степпера — кнопка по правилу; число берём, только если рядом короткая подпись
      // вида «− 2 +»: в длинной первым числом была бы цена.
      + "if(!best){var buttons=[].slice.call(document.querySelectorAll('button,[role=button]'));"
      + " for(var i=0;i<buttons.length;i++){var b=buttons[i];if(b.disabled||!inside(b))continue;"
      + "  var l=label(b);if(bad(l)||!pat.test(l))continue;"
      + "  var box=b.parentElement||b;best=b;"
      + "  count=(box.innerText||'').replace(/\\s+/g,' ').trim().length<=16?getCount(box):null;break;}}"
      + "if(!best)return JSON.stringify({found:false});"
      + "best.setAttribute(MARK,'1');if(!pinned(best))best.scrollIntoView({block:'center',inline:'center'});"
      + "return JSON.stringify({found:true,count:count});"
      + "}catch(e){return JSON.stringify({found:false,error:String(e).slice(0,120)});}})(%s)";

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

    /** Сколько дать странице сети улечься после загрузки, мс: цель 5 с на сборку, ожидание частым опросом. */
    private static final int SETTLE_MS = 700;
    /** Сколько ждать после нажатия «Соглашаюсь»: витрина перерисовывает карточку. */
    private static final int CONSENT_WAIT_MS = 1500;
    private static final int CART_SETTLE_MS = 800;
    private static final int READY_POLL_MS = 250;
    private static final int READY_LIMIT_MS = 30000;
    /** Сколько раз ещё поискать кнопку «В корзину», если её пока нет, и через сколько. */
    private static final int PICK_TRIES = 3;
    /** Сколько ещё ждать запоздалый ответ сети на касание, если запросов не видно совсем. */
    private static final int CALLS_WAIT_MS = 2000;
    private static final int PICK_RETRY_MS = 1500;
    /** Сколько раз перечитать страницу корзины, если итога на ней ещё нет, и через сколько: частый опрос вместо длинных пауз. */
    private static final int READ_TRIES = 15;
    private static final int READ_RETRY_MS = 700;

    // ── Фоновая сборка (1.13) ──
    /** Через сколько после последней правки корзины начинать: 300 мс (правки подряд не теряются, устаревшую сборку сменяет новая). */
    private static final int DEBOUNCE_MS = 300;
    /** Хвост (поиск незнакомых) начинается, когда корзину столько не правили и экрана не касались (tailLater). */
    private static final int TAIL_QUIET_MS = 10000;
    /**
     * Сколько окна сетей с касаниями ждут, пока сети с запросами (cartapi.js) положат своё. Обычно
     * те кончают за 2–4 с; запас — на холодное окно и обновление входа (замер 10.11.2026: 5 с не
     * хватило, и витрины в соседних окнах растянули запросы до 30 с).
     */
    private static final int API_HEAD_MS = 12000;
    /** Не вышла сборка этой корзины — следующая попытка не раньше: сеть могла отказать. */
    private static final long RETRY_MS = 20L * 60 * 1000;
    /**
     * Позиция, которая недавно не легла в эту сеть, в фоновой сборке не повторяется 30 минут.
     * Поиск незнакомой, который ничего не нашёл, — FRESH_MS: столько же не повторяет его сервер.
     */
    private static final long FAIL_AGAIN_MS = 30L * 60 * 1000;
    /**
     * Узел корзин Пятёрочки не ответил и после обновления входа (403 её защиты, спрятанный CORS) —
     * столько её сборки идут сразу касаниями. Повтор запроса на каждой правке — 5–9 с впустую
     * и занятый общий процесс окон: соседние сети выходили к пределу 5 с (замер 11.11.2026: Дикси
     * 4,8 с вместо 1,9). Защита Пятёрочки обычно отходит за 2–4 мин.
     */
    private static final long API_WALL_MS = 3L * 60 * 1000;
    /**
     * Кука защиты Пятёрочки (ServicePipe), которую узел корзин перестаёт принимать, когда она протухла (1.50).
     * Без неё и с мусором в ней он отвечает за 60–350 мс статусом 0 — «обрыв сети» (стенд, 13.11.2026).
     */
    private static final String PYAT_COOKIE = "spsc";
    /**
     * Новую spsc и догрузившуюся главную после стирания ждём не дольше: на стенде (VPN, программная графика) это
     * 10–12 с, на телефоне быстрее. Узел принимает новую куку, только когда страница довела до конца запрос
     * защиты gatekeeper/evaluate: уход с главной, пока он в полёте (net::ERR_ABORTED, ~2,5 с после загрузки),
     * оставлял узел отказывающим (стенд, 13.11.2026).
     */
    private static final long HEAL_WAIT_MS = 20000;
    /** Запрос защиты завершился (Resource Timing страницы) — главная простояла ещё столько, и можно уходить. */
    private static final long HEAL_STABLE_MS = 500;
    /** Запроса защиты не видно (сайт поменялся, идёт из другого окна) — готова главная, простоявшая столько. */
    private static final long HEAL_LATE_MS = 8000;
    /**
     * Узел корзин Пятёрочки после отказавшего запроса (с протухшей или мусорной кукой) отказывает и запросам с
     * настоящей и с новой кукой около минуты (стенд, 13.11.2026, пять прогонов: отказ при 49–62 с после первого
     * отказа, приём при 62–83 с; новые отказы внутри паузы её не продлевали). Поэтому после отказа узлу молчим
     * столько: ни повтора, ни сборок, ни чтения заранее.
     */
    private static final long HEAL_QUIET_MS = 65000;
    /**
     * Лечить куку не чаще: узел, который и после этого не пускает, не дёргаем. Каждое лечение — стёртая кука и новая
     * загрузка главной у защиты: на стенде шесть лечений за час кончились закрытым адресом (14.11.2026), а закрытый
     * адрес телефона закрыл бы Пятёрочку и в браузере владельца. Протухшая кука лечится с первого раза.
     */
    private static final long HEAL_GAP_MS = 15L * 60 * 1000;
    /**
     * Защита показала вместо главной страницу «Проблемы со связью»: адрес закрыт, и стёртая кука его не откроет (стенд,
     * 14.11.2026, после шести лечений за час). К узлу корзин столько не ходим — ни лечения, ни запросов, ни чтения
     * заранее, ни подогрева главной (метка apiblock.<код> в часах телефона: переживает перезапуск и сон): каждый отказ
     * закрытому адресу копится. Касаний и поиска по сайту тоже нет (1.61, endBlocked): закрыт весь адрес, и фоновая
     * сборка кончается отказом с причиной — правка не пропадает молча.
     */
    private static final long HEAL_BLOCK_MS = 30L * 60 * 1000;
    /** Чтение корзины Впрока ответило 429 (1.62, стена защиты; на записи 429 значит «товар неизвестен»): адрес закрыт на столько. */
    private static final long ASK_WAIT_MS = 90L * 1000;
    /** Что отвечает HEALED_JS, когда окно показывает такую страницу. */
    private static final String HEAL_BLOCK_PAGE = "#block";
    /** Начало причины, с которой whenHealed кончает ожидание на такой странице: повтор запроса паузу не ждёт. */
    private static final String HEAL_BLOCKED = "блок защиты";
    /**
     * Кука spsc старше — узел корзин ей уже не верит, и первый же запрос получает отказ, а отказ запирает узел на минуту
     * (телефон владельца, 15.11.2026: после простоя в 25–45 мин первый запрос отказывал, и правка шла касаниями 58 с).
     * Такую куку лечим до запроса, пока отказа нет (aheadHeal, 1.57). Сколько кука живёт у узла, неизвестно: по телефону
     * больше 80 с (удача 14.11 в 23:42) и меньше 6 мин (отказы в 22:27 и 23:49) — берём 150 с, хроника 1.57 уточнит.
     */
    private static final long PYAT_STALE_MS = 150000;
    /** Заранее лечим не чаще: каждое лечение — стёртая кука и новая загрузка главной у защиты (см. HEAL_GAP_MS). */
    private static final long AHEAD_GAP_MS = 4L * 60 * 1000;
    private static final int AHEAD_PER_HOUR = 4;
    /** Сколько времени считать завершённую сборку свежей: 6 часов. После этого корзина пересобирается. */
    private static final long FRESH_MS = 6L * 3600 * 1000;
    /**
     * Наряд сети в памяти телефона годен столько, дальше — заново у сервера. Не короче FRESH_MS:
     * пока сборка свежая, сервер наряд не обновляет, и через полчаса (срок до 1.28) правка по
     * касанию шла долгой дорогой — корзина, ответ сервера, наряд (замер 10.11.2026).
     */
    private static final long PLAN_MS = 6L * 3600 * 1000;
    /** Пульс серверу не чаще: строку держит живой и сообщение раз в полторы секунды. */
    private static final int BEAT_MS = 1500;
    /**
     * Фоновая сборка идёт дольше — хроника уходит серверу сама, без отчёта (1.55): 15.11.2026 строки Магнита и
     * Пятёрочки провисели «идёт» две с лишним минуты, а отчёта, к которому хронику приложить, у такой сборки нет.
     * Одна сеть — не чаще TRACE_GAP_MS.
     */
    private static final long TRACE_AFTER_MS = 30000;
    private static final long TRACE_GAP_MS = 120000;
    private final Map<String, Long> traceSent = new HashMap<>();
    /**
     * Сборка без хода дольше RUN_IDLE_MS оборвана сторожем (1.56): окно сети могло замолчать, а сборка, которую никто не
     * кончает, держит сеть — правка, пришедшая за ней, ждёт «пишущую» (fastChange), и плитка крутится. Самый долгий законный
     * перерыв между шагами — пауза узла Пятёрочки около 65 с (HEAL_QUIET_MS), порог выше неё и ниже PHONE_STALE сервера.
     * shortcut: ход — это вызовы beat(); шаг, молчащий дольше порога, сторож оборвёт, тогда нужны пульсы по таймеру.
     */
    private static final long RUN_WATCH_MS = 10000;
    private static final long RUN_IDLE_MS = 100000;
    /** Окно сети падало давнее срока — это снова «первый раз»: собираем заново (lostView, 1.56). */
    private static final long CRASH_FORGET_MS = 5L * 60 * 1000;
    /** Столько карточек подряд без заголовка — и сеть, значит, показывает не товары. */
    private static final int BLANK_LIMIT = 2;
    /** Скрытое окно без дела дольше — убрать: память телефона не резиновая. */
    private static final int IDLE_MS = 10 * 60 * 1000;
    /** Столько без дела — и окно Пятёрочки идёт на её страницу за свежими куками защиты (warm). */
    private static final int KEEP_ALIVE_MS = 10 * 60 * 1000;
    /** Приложение свёрнуто дольше — фоновые сборки на паузу; вернулся — продолжат. */
    private static final int AWAY_MS = 60 * 1000;
    /** Заказ на очистку истории не ждёт дольше: наш адрес может так и не открыться. */
    private static final int CLEAR_HISTORY_MS = 3000;
    /** Код ответа системного выбора файла (onShowFileChooser → onActivityResult). */
    private static final int REQ_FILE = 1001;
    private static final String TAG = "Korzina";

    /** Ожидание ответа cartapi.js (APK 1.24): Перекрёсток, Пятёрочка, Дикси. */
    private static final long CARTAPI_WAIT_MS = 45000;
    /** Соединения с узлами корзин по касанию открываем не чаще: неиспользованное живёт около 10 с. */
    private static final long PRECONNECT_MS = 8000;
    /**
     * Наряд фоновой сборки не пришёл за это время — тот же запрос уходит второй раз (callTwice, 1.44). Обычный
     * наряд идёт 0,5–1,5 с; ждать дольше нельзя: вместе с запросами сети правка не уложится в 5 с.
     */
    private static final long HEDGE_MS = 1800;
    /**
     * Чтение корзины заранее (peek, 1.41). cartapi.js минуту помнит корзину из последнего ответа сети, и
     * правка по касанию не читает её заново: у Пятёрочки чтение — 0,85 с из 1,5 с правки, а после простоя
     * до 3 с (замеры 12–13.11.2026). Пока человек на экране корзины и трогал его не дольше PEEK_IDLE_MS
     * назад, память старше PEEK_AGE_MS обновляем сами: одно чтение, записи нет.
     * Сети с памятью: {код, ключ памяти в cartapi.js, «1» — читать и по расписанию}. У Дикси чтение корзины
     * считает скидки и занимает до 1,4 с (замер 13.11.2026): её читаем только по касанию и только когда
     * память не доживёт до правки — лишней нагрузки на сеть нет.
     */
    private static final String[][] PEEKS = {
        {"pyaterochka", "kz.5ka.cart", "1"},
        {"dixy", "kz.dixy.cart", ""},
    };
    private static final long PEEK_TICK_MS = 10000;
    private static final long PEEK_AGE_MS = 40000;
    /** По касанию читаем, только если память не доживёт до правки, которая придёт следом (она годна 60 с). */
    private static final long PEEK_TOUCH_AGE_MS = 55000;
    private static final long PEEK_IDLE_MS = 3L * 60 * 1000;
    private static final String CARTAPI_START =
        "(function(inp){window.__kzApi=window.__kzApi||{};window.__kzApi[inp.key]=null;KZ_CARTAPI.run(inp).then(function(r){window.__kzApi[inp.key]=r;},function(e){window.__kzApi[inp.key]={ok:false,stage:'js',why:String(e&&e.message||e).slice(0,200)};});return 'started';})(%s)";
    private static final String CARTAPI_POLL =
        "(function(k){var a=window.__kzApi;return a&&a[k]?JSON.stringify(a[k]):'';})(%s)";

    private static final long PLAN_PREFETCH_TTL_MS = 30_000L;
    private final Map<String, JSONObject> prefetchedPlans = new HashMap<>();
    private final Map<String, Long> prefetchedPlanAt = new HashMap<>();
    private final Set<String> prefetchInFlight = new HashSet<>();
    private final Map<String, List<Runnable>> prefetchWaiters = new HashMap<>();

    private String cartApiJsCached = null;

    private static String cartApiOrigin(String chain) {
        if ("perekrestok".equals(chain)) return "https://www.perekrestok.ru";
        if ("pyaterochka".equals(chain)) return "https://5ka.ru";
        if ("dixy".equals(chain)) return "https://dixy.ru";
        if ("magnit".equals(chain)) return "https://magnit.ru";
        if ("metro".equals(chain)) return "https://online.metro-cc.ru";
        if ("samokat".equals(chain)) return "https://samokat.ru";
        if ("vprok".equals(chain)) return "https://www.vprok.ru";
        return null;
    }

    /** Узел запросов корзины, если он не тот, где отдыхает окно сети (cartApiOrigin). */
    private static String cartApiHost(String chain) {
        if ("pyaterochka".equals(chain)) return "https://5d.5ka.ru";
        if ("samokat".equals(chain)) return "https://api-web.samokat.ru";
        if ("metro".equals(chain)) return "https://supergraph.metro-cc.ru";
        return null;
    }

    private synchronized String cartApiScript() {
        if (cartApiJsCached != null) return cartApiJsCached;
        try (InputStream is = getAssets().open("cartapi.js");
             ByteArrayOutputStream baos = new ByteArrayOutputStream()) {
            byte[] buf = new byte[4096];
            int n;
            while ((n = is.read(buf)) != -1) {
                baos.write(buf, 0, n);
            }
            cartApiJsCached = new String(baos.toByteArray(), StandardCharsets.UTF_8);
        } catch (Exception e) {
            Log.e(TAG, "Не удалось прочитать cartapi.js: " + e.getMessage());
            cartApiJsCached = "";
        }
        return cartApiJsCached;
    }

    private WebView web;
    /** Поле выбора файла на странице ждёт ответа; null — никто не ждёт. */
    private ValueCallback<Uri[]> fileUploadCallback;
    /** Окна стопкой: скрытые окна сетей снизу, окно человека сверху и во весь размер. */
    private FrameLayout stage;
    /** Фоновые сборки: скрытое окно и сборка на сеть, по коду сети. Трогаются только с главного потока. */
    private final Map<String, Slot> slots = new HashMap<>();
    /** Выученный образец запроса «положить в корзину» по коду сети: живёт в памяти между сборками. */
    private final Map<String, String> tpls = new HashMap<>();
    private SharedPreferences prefs;
    private SecureVault secureVault;

    private SecureVault vault() {
        if (secureVault == null) secureVault = new SecureVault(this);
        return secureVault;
    }

    /** Снять кавычки JSON-строки, если они есть. */
    private static String unquote(String val) {
        if (val == null) return "";
        val = val.trim();
        if (val.length() >= 2 && val.startsWith("\"") && val.endsWith("\"")) {
            try {
                Object obj = new JSONTokener(val).nextValue();
                if (obj instanceof String) return (String) obj;
            } catch (Exception ignored) { }
            return val.substring(1, val.length() - 1);
        }
        return val;
    }

    /**
     * Сохранённый образец запроса «положить в корзину» для сети (2.3).
     * Из памяти (tpls), иначе из SecureVault. Пусто, не JSON или старше 7 суток — null.
     */
    private JSONObject storedTpl(String code) {
        if (code == null || code.isEmpty()) return null;
        String raw = tpls.get(code);
        if (raw == null || raw.isEmpty()) {
            raw = vault().getDecrypted("tpl." + code, null);
        }
        if (raw == null || raw.trim().isEmpty()) return null;
        try {
            JSONObject obj = new JSONObject(raw);
            long at = obj.optLong("at", 0);
            long age = System.currentTimeMillis() - at;
            if (at <= 0 || age < 0 || age > 7L * 24 * 60 * 60 * 1000) {
                dropTpl(code);
                return null;
            }
            if (!tpls.containsKey(code)) {
                tpls.put(code, raw);
            }
            return obj;
        } catch (Exception e) {
            dropTpl(code);
            return null;
        }
    }

    /** Сбросить сохранённый образец запроса для сети (2.3). */
    private void dropTpl(String code) {
        if (code == null || code.isEmpty()) return;
        tpls.remove(code);
        vault().putEncrypted("tpl." + code, "");
    }

    /** Продлить срок годности образца в SecureVault при удачном повторе (2.5). */
    private void extendTpl(String code) {
        JSONObject obj = storedTpl(code);
        if (obj == null) return;
        try {
            obj.put("at", System.currentTimeMillis());
            String raw = obj.toString();
            tpls.put(code, raw);
            vault().putEncrypted("tpl." + code, raw);
        } catch (Exception ignored) { }
    }
    /** Последняя корзина, которую страница отдала на сборку (мост collect). */
    private JSONObject lastCollect;
    private long collectDeadline;
    /** Приложение свёрнуто дольше AWAY_MS: фоновые сборки стоят до возвращения. */
    private boolean away = false;
    private long preconnectedAt = 0;
    /** Когда экрана последний раз касались: хвост ждёт тишины и после касаний (1.36), время правки идёт отсюда (1.37). */
    private long touchedAt = 0;
    /** Когда человек вернулся в приложение или из окна сети (часы телефона): памяти корзины старше веры нет. */
    private long homeSince = 0;
    /** Запуск или возвращение: касаний ещё не было, а корзину Пятёрочки пора прочитать заранее (peek). */
    private long peekLive = 0;
    private boolean peekByTouch = false;
    private final Runnable peekLoop = this::peek;
    private final Runnable goAway = this::pauseBackground;
    private ProgressBar progress;
    private View chainPanel;
    private TextView chainStatus;
    private Button saveLoginBtn;
    private Button chainBackBtn;
    private View appErrorPanel;
    private String failedAppUrl = "";
    private Button saveOffersBtn;
    private Button recordBtn;
    private Button readBtn;
    private Button cartBtn;
    /**
     * Режим входа (1.26): окно сети открыто плиткой «Настройки» ради входа. На панели
     * «←» и одна кнопка — «Я вошёл», после входа «Готово»; вход приложение узнаёт само.
     */
    private Button loginDoneBtn;
    private boolean loginMode = false;
    private boolean loginSaved = false;
    /** Сколько раз «Войти» на сайте сети нажато за человека в этот вход (не больше LOGIN_TAPS). */
    private int loginTaps = 0;
    private final Runnable loginWatch = this::checkLogin;
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
        @JavascriptInterface
        public boolean updateApp() {
            if (!bridgeAllowed()) return false;
            if (!BuildConfig.DEBUG) return false;
            if (currentChain >= 0 || !SERVER.startsWith("https://")) return false;
            handler.post(() -> {
                if (bridgeAllowed() && currentChain < 0) apkUpdater.start(SERVER);
            });
            return true;
        }

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

        /**
         * Открыть вход в сеть (1.26): сайт сети в режиме входа — «Войти» на нём жмём за
         * человека, вход узнаём и сохраняем сами, на панели остаётся «Готово».
         */
        @JavascriptInterface
        public boolean login(String code) {
            if (!bridgeAllowed()) return false;
            if (currentChain >= 0) return false;
            for (int i = 0; i < CHAINS.length; i++) {
                if (CHAINS[i][1].equals(code)) {
                    final int idx = i;
                    handler.post(() -> openLogin(idx));
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
            handler.post(() -> {
                if (!pageOurs || currentChain >= 0) return;
                ReceiptSync.account(MainActivity.this, (owner, session) -> {
                    if (!pageOurs || isFinishing() || isDestroyed()) return;
                    if (owner.isEmpty()) {
                        Toast.makeText(MainActivity.this, "Для входа в ФНС нужна связь с корзиной", Toast.LENGTH_LONG).show();
                        return;
                    }
                    startActivity(new Intent(MainActivity.this, ReceiptLogin.class).putExtra("owner", owner));
                });
            });
        }

        @JavascriptInterface
        public String receiptStatus(String phone) {
            if (!bridgeAllowed()) return "{}";
            return ReceiptSync.status(MainActivity.this, phone);
        }

        @JavascriptInterface
        public void leaveReceiptProfile() {
            if (!bridgeAllowed()) return;
            ReceiptSync.logout(MainActivity.this);
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
            Log.i(TAG, "сборка: страница прислала корзину");
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

        /**
         * Правка корзины в миг касания (1.27): {basket, pid, verb, from, stamp} — отпечатки
         * корзины до и после правки. Их сервер положил в страницу заранее, и сборка не ждёт
         * его ответа на правку.
         */
        @JavascriptInterface
        public void change(String json) {
            if (!bridgeAllowed()) return;
            final JSONObject c;
            try {
                c = new JSONObject(json == null ? "{}" : json);
            } catch (Exception e) {
                return;
            }
            Log.i(TAG, "сборка: правка по касанию");
            handler.post(() -> {
                try {
                    if (pageOurs) fastChange(c);
                } catch (Exception e) {
                    Log.w(TAG, "правка по касанию не легла в сборку", e);
                }
            });
        }

        /** Что собирается в фоне сейчас и что ждёт запуска: {going: [коды], soon: [коды]}. */
        @JavascriptInterface
        public String busy() {
            if (!bridgeAllowed()) return "{}";
            if (currentChain >= 0) return "{}";
            return busyNow;
        }

        /** Заказ выбран: запомнить в order.current, доля в выбранных, уборка в остальных (1.19). */
        @JavascriptInterface
        public boolean ordered(String json) {
            if (!bridgeAllowed()) return false;
            if (json == null || json.trim().isEmpty()) return false;
            try {
                final JSONObject payload = new JSONObject(json);
                final String stamp = payload.optString("stamp", "");
                if (stamp.isEmpty()) return false;
                // Номер заказа: каждое «Заказать» — новая доля и уборка, даже по той же корзине.
                payload.put("at", System.currentTimeMillis());
                handler.post(() -> {
                    memory().edit().putString("order.current", payload.toString()).apply();
                    if (lastCollect != null) decide(lastCollect);
                });
                return true;
            } catch (Exception e) {
                return false;
            }
        }
    }

    /**
     * Адрес — нашего сервера, а не сети и не чужой.
     *
     * Обычная проверка строки: ни WebView, ни его потоков не трогает, поэтому её
     * можно звать откуда угодно, в том числе с потока моста (JavaBridge).
     */
    private static final String LOCAL_UI_PREFIX = "file:///android_asset/ui/";

    private static boolean isOurs(String url) {
        return url != null && (url.equals(SERVER)
            || url.startsWith(SERVER + "/")
            || url.startsWith(LOCAL_UI_PREFIX));
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
        try {
            Log.attach(getSharedPreferences("trace", MODE_PRIVATE));
        } catch (RuntimeException ignored) {
            // хроника не должна мешать запуску
        }
        Log.i(TAG, "приложение запущено, версия " + BuildConfig.VERSION_NAME);

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
        appErrorPanel = buildAppErrorPanel();
        appErrorPanel.setVisibility(View.GONE);
        stage.addView(appErrorPanel, new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        chainPanel = buildChainPanel();
        chainPanel.setVisibility(View.GONE);
        root.addView(chainPanel, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(root);
        // ПОСЛЕ setContentView: WindowInsetsController живёт в оформлении окна, которое
        // создаётся вместе с содержимым. До него PhoneWindow.getInsetsController()
        // падал с NullPointerException (замер на эмуляторе 19.10.2026).
        paintSystemBars();
        setUpWeb();
        openOurApp("/");
        ReceiptSync.resume(this);
        handler.postDelayed(this::noteLastExit, 4000);     // после старта страницы: сессия на сервер нужна кукой окна
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

    private View buildAppErrorPanel() {
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setGravity(Gravity.CENTER);
        panel.setPadding(dp(24), dp(24), dp(24), dp(24));
        panel.setBackgroundColor(0xFFF3F4F6);

        TextView message = new TextView(this);
        message.setText("Не удалось открыть «Мою корзину». Проверьте соединение.");
        message.setTextSize(16f);
        message.setTextColor(0xFF111214);
        message.setGravity(Gravity.CENTER);
        panel.addView(message);

        Button retry = new Button(this);
        retry.setText("Повторить");
        retry.setOnClickListener(v -> {
            if (!failedAppUrl.isEmpty()) web.loadUrl(failedAppUrl);
        });
        panel.addView(retry);
        return panel;
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
        // Знак слева (say), число табличными цифрами: оно меняется на ходу и не должно плясать.
        chainStatus.setGravity(Gravity.CENTER_VERTICAL);
        chainStatus.setCompoundDrawablePadding(dp(8));
        chainStatus.setFontFeatureSettings("tnum");
        panel.addView(chainStatus);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER);
        row.setPadding(0, dp(6), 0, 0);

        chainBackBtn = new Button(this);
        chainBackBtn.setText("←");
        chainBackBtn.setContentDescription("Вернуться в Мою корзину");
        chainBackBtn.setTextSize(12f);
        chainBackBtn.setOnClickListener(v -> leaveChain());
        row.addView(chainBackBtn, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 0.6f));

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

        // Только в режиме входа (1.26): «Я вошёл», после входа «Готово». Прочие кнопки тогда спрятаны.
        loginDoneBtn = new Button(this);
        loginDoneBtn.setTextSize(14f);
        loginDoneBtn.setVisibility(View.GONE);
        loginDoneBtn.setOnClickListener(v -> {
            if (loginSaved) finishLogin();
            else loginFound();
        });
        row.addView(loginDoneBtn, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 2f));

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
        // Версия в метке заморожена на 1.33 (с 1.34): ту же строку берут окна сетей, а защита Пятёрочки
        // привязывает к ней свои куки. Обновление меняло строку, и узел корзин отказывал, пока страница
        // сети не пройдёт проверку заново (замер 11.11.2026: первая правка после обновления — 10 с и
        // касания вместо запросов). Настоящую версию сервер читает из куки kz_app (app/web/device.py).
        s.setUserAgentString(s.getUserAgentString() + " KorzinaApp/1.33");
        web.addJavascriptInterface(new KorzinaBridge(), "Korzina");

        // ПОЛОС ПРОКРУТКИ НЕ ВИДНО (1.15). WebView рисует у правого края полосу — по ней
        // сразу видно, что перед человеком сайт, а не приложение. Листается страница
        // пальцем и без неё.
        web.setVerticalScrollBarEnabled(false);
        web.setHorizontalScrollBarEnabled(false);

        CookieManager jar = CookieManager.getInstance();
        jar.setAcceptCookie(true);
        jar.setAcceptThirdPartyCookies(web, true);
        if (!SERVER.isEmpty()) jar.setCookie(SERVER, "kz_app=" + BuildConfig.VERSION_NAME + "; Path=/; Max-Age=31536000");

        web.setWebViewClient(new WebViewClient() {
            // APK ships its own basket screens. Keep same-origin API requests and
            // the shop login pages on the network.
            @Override
            public android.webkit.WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
                if (!"GET".equals(req.getMethod()) || !req.getUrl().toString().startsWith(SERVER + "/")) return null;
                String path = req.getUrl().getEncodedPath();
                if (path == null) return null;
                String asset;
                String type;
                if (req.isForMainFrame() && path.matches("/|/login|/phone/(basket|settings|orders|cheaper|prices|baskets|receipts)")) {
                    asset = "index.html";
                    type = "text/html";
                } else if (path.matches("/(?:phone/)?assets/[A-Za-z0-9_-]+-[A-Za-z0-9_-]+\\.(js|css)")) {
                    asset = path.substring(path.indexOf("assets/"));
                    type = asset.endsWith(".js") ? "application/javascript" : "text/css";
                } else {
                    return null;
                }
                try {
                    return new android.webkit.WebResourceResponse(type, "UTF-8", getAssets().open("ui/" + asset));
                } catch (java.io.IOException unavailable) {
                    return null;
                }
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                if (req == null || req.getUrl() == null) return false;
                String scheme = req.getUrl().getScheme();
                if ("http".equalsIgnoreCase(scheme)
                        || "https".equalsIgnoreCase(scheme)
                        || "file".equalsIgnoreCase(scheme)) {
                    // Все веб-переходы (включая Магнит и редиректы входа) остаются строго внутри приложения.
                    return false;
                }
                // Блокируем intent://, magnit:// и прочие диплинки, чтобы сеть не выкидывала из приложения.
                return true;
            }

            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                super.onPageStarted(view, url, favicon);
                // Адрес берём из колбэка, а не из view.getUrl(): документация
                // предупреждает, что в onPageStarted getUrl() может отдавать ещё
                // старую страницу, а страница корзины зовёт collect уже по
                // DOMContentLoaded. Поток здесь главный — WebView не трогаем.
                pageUrl = url == null ? "" : url;
                pageOurs = isOurs(pageUrl);
                failedAppUrl = "";
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                pageUrl = url == null ? "" : url;
                pageOurs = isOurs(pageUrl);
                // Режим следует за адресом, а не за тем, кто его открыл: человек
                // мог вернуться «назад» на наш сайт сам — и панель должна уйти
                // вместе со страницей сети, а не висеть поверх корзины. Режим входа
                // держим и на чужом адресе: вход сети бывает на отдельном сервисе.
                if (currentChain >= 0 && !isHostOf(currentChain, url)
                        && (!loginMode || isOurs(url))) {
                    exitChainMode();
                }
                clearHistoryIfOurs(view);
                if (currentChain < 0) return;
                chainPanel.setVisibility(View.VISIBLE);
                if (recording) {
                    // Новая страница — новый скрипт: обёртка живёт, пока жива страница.
                    // Записанное раньше лежит в sessionStorage и переживает перезагрузку.
                    view.evaluateJavascript(recScript(currentChain), null);
                    if (cartRun == null) say(Sign.GOING, "", "запись · " + shortenUrl(url), "Идёт запись — " + shortenUrl(url));
                } else if (cartRun == null && !loginMode) {
                    // Во время сборки строку состояния ведёт она: «кладу 3 из 9», при входе — checkLogin.
                    say(CHAINS[currentChain][0] + " — " + shortenUrl(url), false);
                }
            }

            @Override
            public void doUpdateVisitedHistory(WebView view, String url, boolean isReload) {
                super.doUpdateVisitedHistory(view, url, isReload);
                pageUrl = url == null ? "" : url;
                pageOurs = isOurs(pageUrl);
            }

            @Override
            public void onPageCommitVisible(WebView view, String url) {
                super.onPageCommitVisible(view, url);
                pageUrl = url == null ? "" : url;
                pageOurs = isOurs(pageUrl);
                if (pageOurs && failedAppUrl.isEmpty()) appErrorPanel.setVisibility(View.GONE);
                // Страница уже наша и зафиксирована в истории: исполняем заказ здесь
                // же, не дожидаясь onPageFinished — из back-forward cache он может
                // не прийти, а флаг обязан сняться на первом нашем событии страницы.
                clearHistoryIfOurs(view);
            }

            @Override
            public void onReceivedError(WebView view, android.webkit.WebResourceRequest request,
                                        android.webkit.WebResourceError error) {
                String url = request.getUrl().toString();
                if (request.isForMainFrame() && isOurs(url) && url.equals(pageUrl)) {
                    failedAppUrl = url;
                    appErrorPanel.bringToFront();
                    appErrorPanel.setVisibility(View.VISIBLE);
                }
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
                return super.onConsoleMessage(message);
            }

            // Без этого поле выбора файла на «Мои чеки» (полная версия) в приложении молчит.
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> filePathCallback,
                                             FileChooserParams fileChooserParams) {
                if (fileUploadCallback != null) {
                    fileUploadCallback.onReceiveValue(null);
                }
                fileUploadCallback = filePathCallback;
                try {
                    // В accept у «Моих чеков» расширения (".pdf"), а createIntent берёт первое
                    // как MIME-тип, и список файлов выходил пустым.
                    Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
                    intent.addCategory(Intent.CATEGORY_OPENABLE);
                    intent.setType("*/*");
                    startActivityForResult(intent, REQ_FILE);
                    return true;
                } catch (ActivityNotFoundException e) {
                    if (fileUploadCallback != null) {
                        fileUploadCallback.onReceiveValue(null);
                        fileUploadCallback = null;
                    }
                    // Колбэк уже получил null, а false заставил бы WebView ответить второй раз
                    // (IllegalStateException).
                    return true;
                }
            }
        });

        // Без этого «Скачать» внутри приложения ничего не делает: WebView сам файлы не
        // качает. Скачивание отдаётся браузеру телефона, он же предложит поставить APK.
        web.setDownloadListener((url, userAgent, contentDisposition, mimetype, contentLength) -> {
            // Скачивания чужих страниц (сайты сетей и кабинет ФНС открываются в том же окне)
            // не открываем, как и до 1.18; большой data:-адрес ронял бы приложение.
            if (!isOurs(url)) return;
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
            } catch (ActivityNotFoundException e) {
                Toast.makeText(MainActivity.this, "Не удалось открыть скачивание",
                        Toast.LENGTH_SHORT).show();
            }
        });
    }

    // ── Режимы: наш сайт и страница сети ──
    private void openOurApp(String path) {
        exitChainMode();
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
        stopLogin();
        chainPanel.setVisibility(View.VISIBLE);
        setChainButtons(true);
        say(Sign.GOING, "", CHAINS[idx][0], "Открываю " + CHAINS[idx][0]);
        web.loadUrl(CHAINS[idx][2]);
    }

    /** Окно сети ради входа (1.26): строка-подсказка, «Войти» жмём за человека, вход ловит checkLogin. */
    private void openLogin(int idx) {
        openChain(idx, null);
        for (String[] p : LOGIN_PAGES) if (p[0].equals(CHAINS[idx][1])) web.loadUrl(p[1]);
        loginMode = true;
        showLoginPanel(true);
        say(Sign.ACT, "", "Войдите своим номером телефона");
        handler.postDelayed(loginWatch, LOGIN_CHECK_MS);
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
        if (currentChain >= 0) {
            openOurApp(loginMode ? "/phone/settings" : "/phone/basket");
            return;
        }
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
        // В окне сети корзину могли оформить в заказ: памяти корзины (cartapi.js) старше веры нет (1.41).
        if (currentChain >= 0) {
            homeSince = System.currentTimeMillis();
            for (Slot s : slots.values()) s.peekStartedAt = 0;
        }
        if (cartRun != null) stopRun("Сборка остановлена: вы ушли со страницы сети.");
        currentChain = -1;
        chainLinesOverride = null;
        stopRecording();
        stopLogin();
        if (chainPanel != null) chainPanel.setVisibility(View.GONE);
    }

    private void setChainButtons(boolean on) {
        saveLoginBtn.setEnabled(on);
        saveOffersBtn.setEnabled(on);
        recordBtn.setEnabled(on);
        readBtn.setEnabled(on);
        cartBtn.setEnabled(on);
    }

    /** Панель режима входа: «←» и «Я вошёл»; прочие кнопки сети спрятаны. */
    private void showLoginPanel(boolean on) {
        chainBackBtn.setText(on ? "В приложение" : "←");
        int rest = on ? View.GONE : View.VISIBLE;
        saveLoginBtn.setVisibility(rest);
        saveOffersBtn.setVisibility(rest);
        recordBtn.setVisibility(rest);
        readBtn.setVisibility(rest);
        cartBtn.setVisibility(rest);
        loginDoneBtn.setVisibility(on ? View.VISIBLE : View.GONE);
        loginDoneBtn.setText("Я вошёл");
        loginDoneBtn.setBackgroundTintList(null);
        chainStatus.setTextSize(on ? 15f : 13f);
        chainPanel.setBackgroundColor(0xF2F5F5F5);
    }

    private void stopLogin() {
        handler.removeCallbacks(loginWatch);
        loginMode = false;
        loginSaved = false;
        loginTaps = 0;
        if (loginDoneBtn != null) showLoginPanel(false);
    }

    // ── Кабинет ФНС: вход у человека, ключ — у приложения ──

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
            say(Sign.ACT, "", "Положите товар в корзину, уберите его и нажмите «Готово»",
                "Идёт запись. Положите в корзину любой товар, потом уберите его и нажмите «Готово».");
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
                    say(Sign.FAIL, "", "запросов корзины не видно",
                        "Запросов корзины не видно. Войдите в " + name + ", нажмите «Записать», "
                        + "положите товар и уберите его, затем «Готово».");
                    return;
                }
                JSONObject body = new JSONObject()
                    .put("store", chain)
                    .put("app_version", BuildConfig.VERSION_NAME)
                    .put("entries", entries);
                say(Sign.GOING, "", "запись", "Отправляю запись");
                post("/api/cart_record", body, String.valueOf(entries.length()), "запись",
                     name + " — записано запросов корзины: " + entries.length());
            } catch (Exception e) {
                say(Sign.FAIL, "", "запись не забралась: " + e.getMessage(), null);
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
        // Сборка после «Заказать» (1.19): номер заказа и отпечаток корзины. Сервер отвечает на
        // присланную долю своим отпечатком, а «собрано» должно помнить корзину и заказ —
        // иначе каждая страница с collect запускала бы эту сборку заново.
        long orderAt = 0;
        String orderStamp = "";
        JSONObject rules = new JSONObject();
        JSONArray lines = new JSONArray();
        JSONArray later = new JSONArray(); // фоновая сборка: строки поиска — после первого отчёта
        boolean tail = false;              // идёт поиск незнакомых после первого отчёта
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
        // Адрес карточки товара: для перехода при снятии лишнего касаниями (1.19).
        final JSONObject urls = new JSONObject();
        int droppedCount = 0;                  // сколько упаковок своего снято (1.19)
        // Артикулы, которые страница корзины сети уже показывала под именем наряда.
        final JSONObject shown = new JSONObject();
        final JSONObject want = new JSONObject();    // артикул → сколько должно лежать
        // Артикул → товар корзины (product_id): найденное поиском не снимаем, пока его ищут снова.
        final JSONObject pids = new JSONObject();
        // Товары корзины, у которых в наряде нет строки с артикулом: найденное для них не снимаем (1.43).
        final Set<Integer> noSku = new HashSet<>();
        // Артикул → вес наряда в граммах, с которым сверяли фасовку его карточки (1.49). У положенного
        // до 1.49 отметки нет.
        final JSONObject packOk = new JSONObject();
        // Артикулы товаров, найденных поиском сети и положенных раньше: в этой сборке идут знакомыми (ownFound).
        final Set<String> adopted = new HashSet<>();
        long cartAt = 0;   // когда сеть последний раз показала корзину целиком
        boolean stopped = false;
        // Запрос сети «положить в корзину» выучен по первому касанию (LEARN_JS): дальше
        // позиции кладутся им, без загрузки карточек. Пробуем выучить один раз.
        boolean learned = false;
        boolean learnTried = false;
        long callsUntil = 0;         // до какого времени ждать запросы сети опросом (1.3)
        boolean reusedTpl = false;   // образец запроса сети взят из хранилища между сборками (2.4)
        boolean extendedTpl = false; // срок образца продлён в этой сборке (2.5)
        boolean apiTried = false;  // итог уже спрашивали ответом сети (api_total)
        boolean cartRead = false; // страницу корзины в этой сборке читали
        boolean cartApiDone = false;      // путь запросами cartapi.js уже пробовали в этой сборке (APK 1.24)
        boolean cartApiRefreshed = false; // вход Перекрёстка уже обновляли (APK 1.24)
        String how = "tap";          // чем легла текущая позиция: касанием или запросом
        final long startedAt = SystemClock.uptimeMillis();
        long planAt = 0, apiAt = 0;  // когда пришёл наряд и пошли запросы сети: для замера в журнале
        long changedAt = 0;          // касание, с которого началась эта сборка (Slot.changedAt)
        int changedPid = 0;          // товар этого касания (Slot.changedPid)
        boolean apiOver = false;     // ответ cartapi.js пришёл: окна с касаниями больше не ждут
        String apiWhy = "";          // почему запросами сети не вышло (1.45): уходит в отчёт
        String healNote = "";        // как лечили куку защиты Пятёрочки (1.50): дописывается к причине отказа
        String apiKey = "";          // ключ идущего запуска cartapi.js: им правка говорит адаптеру, что сборку догнали (1.46)
        boolean fast = false;        // наряд не у сервера, а прежний с правкой по касанию (1.27)
        boolean healLogged = false;  // ожидание лечения куки уже записано в хронику (1.54): begin зовётся каждые 250 мс
        boolean wall = false;        // сеть не пустила (403, «Выключите VPN»): правка корзины не будит её
        String cartHint = "";        // дорога к корзине не нашлась: что похожее было на странице
        String consent = "";         // на этой позиции закрыли баннер согласия — это видно в отчёте
        boolean consentRetry = false; // после баннера карточку уже проверили повторно — один раз
        double cardPrice = 0;         // цена за упаковку из текста карточки этой позиции
        boolean cardGone = false;     // карточка/её текст сказали «нет в наличии»
        boolean searchPause = false; // предыдущая строка упала на поиске/стене: нужна пауза
        boolean lineWall = false;    // строку остановила стена сети, а не товар: отказ помнить недолго
        int badged = 0;             // счётчик корзины до касания: после — сверяем, вырос ли
        boolean badgeChecked = false; // признак корзины после касания уже проверяли — один раз
        String now = "";             // что делается сейчас — строкой на панели или нашем экране
        int blank = 0;               // сколько карточек подряд открылись без заголовка
        long beatAt = 0;             // когда последний раз сказали серверу, как идёт
        long actAt = SystemClock.uptimeMillis();   // когда сборка последний раз сделала шаг (beat, даже не ушедший): сторож RUN_IDLE_MS
        boolean again = false;       // корзина сети оказалась пустой: один повтор целиком
        // Последний шаг наряда Ленты (Z9): серверная ссылка на готовую корзину.
        // Спрашиваем один раз за проход; завершение наряда от неё не зависит.
        boolean linkAsked = false;
        boolean linkDone = false;
        String linkNote = "";        // что телефон сказал про ссылку — для отчёта строки

        long deadline = 0;

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
        CartRun last;            // последняя начатая сборка: хвост старой после неё не нужен
        String pending = "";     // какую корзину собрать, когда окно освободится
        Runnable soon;           // отложенный запуск: корзину, может быть, ещё правят
        Runnable sleep;          // убрать окно, если долго без дела
        Runnable keepAlive;      // поддержание кук ServicePipe (Пятёрочка) в актуальном состоянии
        long heldSince = 0;      // с какого времени окно ждёт сети с запросами (API_HEAD_MS)
        long changedAt = 0;      // когда человек тронул корзину: отчёт сети несёт время от касания (1.29)
        int changedPid = 0;      // какой товар тронул: его давний отказ перепроверяем первым
        long planAsked = 0;      // когда спросили наряд без сборки (fetchPlan)
        long robotsAt = 0;       // когда supersede повёл окно на robots.txt: новая сборка загрузку не повторяет
        long movedAt = 0;        // когда окно уводили на страницу сети (часы телефона): касания там меняют корзину
        long peekAskedAt = 0;    // когда окно последний раз спрашивали, пора ли читать корзину заранее
        long peekStartedAt = 0;  // когда чтение заранее последний раз ушло в сеть: не вышло — повтор не раньше PEEK_AGE_MS
        boolean peekOwed = false; // касание было, а окно сети не было готово: чтение заранее по нему ещё впереди
        long healAt = 0;         // когда последний раз лечили куку защиты Пятёрочки (1.50): чтение заранее — не чаще HEAL_GAP_MS
        long healUntil = 0;      // до какого времени окно занято лечением куки на главной странице или ждёт паузы узла после него
        long aheadAt = 0;        // когда последний раз лечили куку до запроса (1.57, аптайм)
        final long[] aheadLog = new long[AHEAD_PER_HOUR];   // когда лечили заранее: не больше AHEAD_PER_HOUR в час
        int aheadN = 0;
        String peekNote = "";    // чем кончилось лечение куки при чтении заранее (1.52): дописывается к причине отказа сборки
        long peekNoteAt = 0;     // когда (аптайм), чтобы давняя запись не сошла за свежую
        int crashes = 0;
        long crashAt = 0;        // когда окно падало последний раз (аптайм): через CRASH_FORGET_MS счёт падений заново

        Slot(int chain) {
            this.chain = chain;
            this.code = CHAINS[chain][1];
        }
    }

    private boolean alive(CartRun r) {
        if (r == null || r.stopped) return false;
        return r.slot == null ? r == cartRun : r.slot.run == r;
    }

    /**
     * Корзину уже поправили снова: эта фоновая сборка устарела. Журнал 07.11.2026 — Магнит
     * прошёл четыре сборки подряд (49 с), пока догнал последнюю правку.
     */
    private static boolean superseded(CartRun r) {
        return r.slot != null && !r.slot.pending.isEmpty() && !r.slot.pending.equals(r.stamp);
    }

    /** cartapi.js сейчас пишет в корзину сети: второй писатель в том же окне спутал бы ей счёт. */
    private static boolean writing(CartRun r) {
        return r.apiAt > 0 && !r.apiOver;
    }

    /**
     * Бросить устаревшую сборку и сразу начать новую. Отчёт не шлём: положенное уже в памяти
     * телефона (saveLedger), а поздний отчёт старого отпечатка перебил бы строку новой сборки.
     */
    private void supersede(CartRun r) {
        Slot s = r.slot;
        r.stopped = true;
        if (s.run == r) s.run = null;
        Log.i(TAG, r.name + ": корзина поменялась — собираю заново");
        // Окно сети с запросами — сразу на robots.txt (1.36): страницу поиска, брошенную хвостом, новая
        // сборка уводила только через 1,1 с, и всё это время она грузила общий поток окон (замер
        // 12.11.2026: правка при идущем хвосте — 4,6–5,6 с во всех сетях против 1,2–2,0 с без него).
        String origin = cartApiOrigin(s.code);
        if (origin != null && r.view != null && !healing(s) && !(origin + "/robots.txt").equals(r.view.getUrl())) {
            go(r, origin + "/robots.txt");
            s.robotsAt = SystemClock.uptimeMillis();
        }
        rest(s);
    }

    /**
     * Поискать незнакомые товары в сети после первого отчёта.
     * Пересчёт «Где дешевле» не ждёт поиска и сразу считает знакомые,
     * а второй отчёт уходит полным — со всеми найденными позициями.
     */
    private void startTail(final CartRun r) {
        Slot s = r.slot;
        if (s == null || r.stopped) {
            if (s != null) rest(s);
            return;
        }
        if (!s.pending.isEmpty() && !s.pending.equals(r.stamp)) {
            rest(s);
            return;
        }
        // Блок поставило лечение, пока хвост ждал тишины (tailLater): касания шли бы на закрытый адрес (1.61). Отчёт
        // головы уже на сервере, и отказ (endBlocked) затёр бы его строку — хвост просто не идёт.
        if (blocked(s.code)) {
            Log.i(TAG, r.name + ": хвост не иду — защита сети закрыла адрес");
            rest(s);
            return;
        }
        // Строки поиска — в конец наряда, а не вместо него: по r.lines снятие лишнего
        // (wantOf) знает, что оставить, а повтор при пустой корзине сети проходит всё.
        int from = r.lines.length();
        for (int i = 0; i < r.later.length(); i++) r.lines.put(r.later.opt(i));
        r.later = new JSONArray();
        r.at = from;
        r.tail = true;
        r.linkDone = false;
        r.apiTried = false;
        r.cartRead = false;
        s.run = r;
        status(r, "ищу в сети незнакомые: " + r.lines.length(), false);
        nextLine(r);
    }

    /**
     * Ход сборки: у «Собрать» — на панели сети, у фоновой — на нашем экране, через сервер.
     * Панель: кольцо, «сколько позиций прошло/сколько всего» (те же числа, что уходят серверу в
     * beat) и позиция в работе; фраза text на сервер уходит как была и читается с экрана целиком.
     */
    private void status(CartRun r, String text, boolean bad) {
        r.now = text;
        if (r.slot == null) {
            int total = r.lines.length();
            JSONObject line = r.lines.optJSONObject(r.at);
            String item = line == null ? "" : line.optString("name", "");
            boolean named = text.startsWith(r.name + ": ");
            String why = named ? text.substring(r.name.length() + 2) : text;
            say(bad ? Sign.FAIL : Sign.GOING, total > 0 ? Math.min(r.at, total) + "/" + total : "",
                bad ? why : item, named ? text : r.name + ": " + text);
        }
        beat(r, false);
    }

    /**
     * Сказать серверу, как идёт сборка: строка «Сборки корзин» живёт этим пульсом, и пока
     * он идёт, сервер в эту сеть не кладёт. Не чаще BEAT_MS, кроме начала и конца.
     */
    private void beat(final CartRun r, boolean force) {
        long t = SystemClock.uptimeMillis();
        r.actAt = t;
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
     * Через TRACE_AFTER_MS, если эта сборка всё ещё идёт (в том числе хвостом), хроника журнала уходит серверу
     * (/api/device/trace): что делали окна сетей, видно по ней, а не по молчанию строки.
     */
    private void watchTrace(final CartRun r) {
        handler.postDelayed(() -> {
            Slot s = r.slot;
            if (s == null || s.run != r || r.stopped) return;
            long now = SystemClock.uptimeMillis();
            Long was = traceSent.get(r.code);
            if (was != null && now - was < TRACE_GAP_MS) return;
            traceSent.put(r.code, now);
            Log.i(TAG, memoryNote());
            uploadTrace(r.code, "идёт " + (TRACE_AFTER_MS / 1000) + " с: " + r.now);
        }, TRACE_AFTER_MS);
    }

    /**
     * Сторож сборки (1.56): без хода дольше RUN_IDLE_MS — оборвать, чтобы сеть не стояла «идёт» и следующая правка шла.
     * Сервер узнаёт причину сразу, а не через PHONE_STALE молчания, и получает хронику.
     */
    private void watchRun(final CartRun r) {
        handler.postDelayed(() -> {
            Slot s = r.slot;
            if (s == null || s.run != r || r.stopped) return;
            long idle = SystemClock.uptimeMillis() - r.actAt;
            if (idle < RUN_IDLE_MS) {
                watchRun(r);
                return;
            }
            String why = r.name + ": сборка встала на «" + r.now + "» — без хода " + idle / 1000 + " с";
            Log.w(TAG, why);
            Log.i(TAG, memoryNote());
            uploadTrace(r.code, why);
            endBackground(r, "failed", why);
        }, RUN_WATCH_MS);
    }

    /**
     * Память телефона для хроники (1.56): процесс страниц один на все окна сетей и при нехватке памяти падает разом
     * (lostView), а систему при нехватке она может убить и приложение целиком.
     */
    private String memoryNote() {
        try {
            android.app.ActivityManager am = (android.app.ActivityManager) getSystemService(ACTIVITY_SERVICE);
            android.app.ActivityManager.MemoryInfo mi = new android.app.ActivityManager.MemoryInfo();
            am.getMemoryInfo(mi);
            int views = 0;
            for (Slot s : slots.values()) if (s.view != null) views++;
            return "память телефона: свободно " + mi.availMem / 1048576 + " МБ из " + mi.totalMem / 1048576
                    + ", порог " + mi.threshold / 1048576 + (mi.lowMemory ? ", МАЛО" : "") + "; окон сетей " + views;
        } catch (RuntimeException e) {
            return "память не прочиталась: " + e;
        }
    }

    /** Названия причин ApplicationExitInfo.getReason() по номерам (числами — чтобы не зависеть от версии SDK при сборке). */
    private static final String[] EXIT_WHY = {"неизвестно", "приложение вышло само", "убито сигналом", "МАЛО ПАМЯТИ",
        "ВЫЛЕТ", "ВЫЛЕТ в родном коде", "не отвечало (ANR)", "не запустилось", "сменили разрешение",
        "слишком много ресурсов", "закрыл человек (смахнул)", "остановлено в настройках", "умерла служба", "другая причина",
        "заморожено системой", "изменили пакет", "обновление приложения"};

    /**
     * Чем кончился прошлый процесс (1.56, Android 11+). 15.11.2026 строки Магнита и Дикси замолчали в одну секунду, и
     * по серверу было не понять: упал ли процесс страниц, убила ли приложение система или его закрыл человек. Причины
     * идут в хронику; кроме «вышло само», «закрыл человек», «остановлено в настройках» и обновления хроника с последними
     * строками прошлого процесса (они стоят в начале кольца, Log.attach) сразу уходит серверу.
     */
    private void noteLastExit() {
        if (Build.VERSION.SDK_INT < 30) return;
        try {
            android.app.ActivityManager am = (android.app.ActivityManager) getSystemService(ACTIVITY_SERVICE);
            List<android.app.ApplicationExitInfo> list = am.getHistoricalProcessExitReasons(getPackageName(), 0, 8);
            long seen = memory().getLong("exitSeen", 0);
            SimpleDateFormat when = new SimpleDateFormat("dd.MM HH:mm:ss", Locale.US);
            String news = "";
            long newest = 0;
            for (android.app.ApplicationExitInfo e : list) {
                // Окна сетей живут в своих изолированных процессах, и их обычный уход («isolated not needed») —
                // не выход приложения: только главный процесс.
                if (!getPackageName().equals(e.getProcessName())) continue;
                newest = Math.max(newest, e.getTimestamp());
                int reason = e.getReason();
                String why = reason >= 0 && reason < EXIT_WHY.length ? EXIT_WHY[reason] : "причина " + reason;
                Log.i(TAG, "прошлый процесс кончился: " + why + " в " + when.format(new Date(e.getTimestamp()))
                        + ", важность " + e.getImportance() + ", rss " + e.getRss() / 1024 + " МБ"
                        + (e.getDescription() == null ? "" : ", " + e.getDescription()));
                boolean human = reason == 1 || reason == 10 || reason == 11 || reason == 15 || reason == 16;
                if (news.isEmpty() && e.getTimestamp() > seen && !human) news = why;
            }
            if (newest > seen) memory().edit().putLong("exitSeen", newest).apply();
            if (!news.isEmpty()) uploadTrace("app", "прошлый процесс кончился: " + news);
        } catch (RuntimeException e) {
            Log.w(TAG, "причину прошлого выхода не прочитал: " + e);
        }
    }

    /** Система просит память (1.56): в хронике видно, не она ли убила окна сетей и приложение. */
    @Override
    public void onTrimMemory(int level) {
        super.onTrimMemory(level);
        if (level >= TRIM_MEMORY_RUNNING_LOW && level != TRIM_MEMORY_UI_HIDDEN) {
            Log.i(TAG, "система просит память, уровень " + level + "; " + memoryNote());
        }
    }

    /** Последние строки журнала (все сети) — серверу. Не вышло — не страшно: хроника не нужна для работы. */
    private void uploadTrace(String code, String reason) {
        try {
            JSONObject body = new JSONObject()
                .put("store", code)
                .put("app_version", BuildConfig.VERSION_NAME)
                .put("reason", reason)
                .put("trace", Log.tail("", "", 0, 159));    // с первой строкой (часы телефона) — столько, сколько хранит сервер
            call("/api/device/trace", body, (status, text) -> { });
        } catch (Exception ignored) { }
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
        say(Sign.GOING, "", r.name, "Беру у сервера, что положить в " + r.name);
        askPlan(r, () -> {
            if (r.lines.length() == 0) {
                offerThisCard(r, r.rules.optString("_note", ""));
                return;
            }
            loadLedger(r);
            say(Sign.GOING, "0/" + r.lines.length(), r.name, r.name + ": позиций " + r.lines.length() + ", начинаю");
            beat(r, true);
            nextLine(r);
        });
    }

    /** Взять у сервера наряд: позиции этой сети, правила кнопок, корзину и её отпечаток. */
    private static String planCacheKey(int basket, String stamp, String code) {
        return basket + ":" + (stamp == null ? "" : stamp) + ":" + (code == null ? "" : code);
    }

    /** Забрать наряды сразу для всех стартующих сетей одним POST /api/device/cartplans. */
    private void prefetchBatchPlans(final int basket, final String stamp, final List<String> codes) {
        if (basket < 0 || stamp == null || stamp.isEmpty() || codes == null || codes.size() < 2) return;
        final List<String> pendingCodes = new ArrayList<>();
        final List<String> pendingKeys = new ArrayList<>();
        long nowCheck = SystemClock.uptimeMillis();
        for (String c : codes) {
            String ck = planCacheKey(basket, stamp, c);
            Long cachedAt = prefetchedPlanAt.get(ck);
            if (cachedAt != null && (nowCheck - cachedAt) < PLAN_PREFETCH_TTL_MS && prefetchedPlans.containsKey(ck)) {
                continue;
            }
            if (prefetchInFlight.contains(ck)) continue;
            pendingCodes.add(c);
            pendingKeys.add(ck);
        }
        if (pendingCodes.size() < 2) return;
        JSONObject body = new JSONObject();
        try {
            JSONArray storesArr = new JSONArray();
            for (String c : pendingCodes) storesArr.put(c);
            body.put("stores", storesArr);
            body.put("background", true);
            body.put("app_version", BuildConfig.VERSION_NAME);
            body.put("basket", basket);
        } catch (Exception ignored) { return; }
        for (String ck : pendingKeys) prefetchInFlight.add(ck);
        call("/api/device/cartplans", body, (status, text) -> {
            try {
                if (status == 200) {
                    JSONObject resp = new JSONObject(text);
                    JSONObject plans = resp.optJSONObject("plans");
                    if (plans != null) {
                        long now = SystemClock.uptimeMillis();
                        Iterator<String> keys = plans.keys();
                        while (keys.hasNext()) {
                            String code = keys.next();
                            JSONObject p = plans.optJSONObject(code);
                            if (p != null && p.optBoolean("ok", false) && stamp.equals(p.optString("stamp"))) {
                                String ck = planCacheKey(basket, stamp, code);
                                prefetchedPlans.put(ck, p);
                                prefetchedPlanAt.put(ck, now);
                            }
                        }
                    }
                }
            } catch (Exception ignored) {
            } finally {
                for (String ck : pendingKeys) {
                    prefetchInFlight.remove(ck);
                    List<Runnable> waiters = prefetchWaiters.remove(ck);
                    if (waiters != null) {
                        for (Runnable w : waiters) {
                            try { w.run(); } catch (Exception ignored) { }
                        }
                    }
                }
            }
        });
    }

    /** Взять у сервера наряд: позиции этой сети, правила кнопок, корзину и её отпечаток. */
    private boolean useBatchPlan(final CartRun r, final Runnable then) {
        // Быстрый путь: если наряд уже предзагружен (или прямо сейчас летит) пакетом /api/device/cartplans и не нужен drop/override — без дублирующего POST!
        if (r.slot != null && r.linesOverride == null && r.basket >= 0 && !r.stamp.isEmpty()) {
            boolean needsDropLookup = false;
            Iterator<String> itCheck = r.cart.keys();
            while (itCheck.hasNext()) {
                if (r.urls.optString(itCheck.next(), "").isEmpty()) { needsDropLookup = true; break; }
            }
            if (!needsDropLookup) {
                final String ck = planCacheKey(r.basket, r.stamp, r.code);
                JSONObject cached = prefetchedPlans.remove(ck);
                Long at = prefetchedPlanAt.remove(ck);
                if (cached != null && at != null && (SystemClock.uptimeMillis() - at) < PLAN_PREFETCH_TTL_MS) {
                    r.planAt = SystemClock.uptimeMillis();
                    applyPlanJson(r, cached, then);
                    return true;
                }
                if (prefetchInFlight.contains(ck)) {
                    List<Runnable> waiters = prefetchWaiters.get(ck);
                    if (waiters == null) {
                        waiters = new ArrayList<>();
                        prefetchWaiters.put(ck, waiters);
                    }
                    waiters.add(() -> {
                        if (!alive(r)) return;
                        JSONObject p = prefetchedPlans.remove(ck);
                        Long pAt = prefetchedPlanAt.remove(ck);
                        if (p != null && pAt != null && (SystemClock.uptimeMillis() - pAt) < PLAN_PREFETCH_TTL_MS) {
                            r.planAt = SystemClock.uptimeMillis();
                            applyPlanJson(r, p, then);
                        } else {
                            askPlan(r, then);
                        }
                    });
                    return true;
                }
            }
        }
        return false;
    }

    private void askPlan(final CartRun r, final Runnable then) {
        loadLedger(r);
        if (useBatchPlan(r, then)) return;
        JSONObject body = new JSONObject();
        try {
            body.put("store", r.code);
            body.put("app_version", BuildConfig.VERSION_NAME);   // сервер включает запросы сети с нужной версии (1.29)
            if (r.slot != null) body.put("background", true);
            // Номер вопроса: повтор запроса (callTwice) несёт тот же, и сервер считает такой наряд один раз.
            if (r.slot != null) body.put("ask", SystemClock.uptimeMillis());
            if (r.basket >= 0) body.put("basket", r.basket);
            // Пустой список тоже уходит: после «Заказать» в невыбранной сети наряд пуст —
            // только снять своё. Без lines сервер отдал бы всю корзину, и она легла бы снова.
            if (r.linesOverride != null) body.put("lines", r.linesOverride);
            JSONArray dropSkus = new JSONArray();
            Iterator<String> it = r.cart.keys();
            while (it.hasNext()) {
                String sku = it.next();
                if (r.urls.optString(sku, "").isEmpty()) dropSkus.put(sku);
            }
            if (dropSkus.length() > 0) body.put("drop", dropSkus);
        } catch (Exception ignored) { }
        final Reply got = (status, text) -> {
            if (!alive(r)) return;
            r.planAt = SystemClock.uptimeMillis();
            if (status != 200) {
                failRun(r, serverError(status, text));
                return;
            }
            try {
                JSONObject plan = new JSONObject(text);
                applyPlan(r, plan);
                // Правка по касанию (1.27) выводит из этого наряда следующий, не спрашивая сервер.
                if (r.slot != null && r.linesOverride == null) {
                    memory().edit().putString("plan." + r.code, text)
                        .putLong("planAt." + r.code, System.currentTimeMillis()).apply();
                }
                then.run();
            } catch (Exception e) {
                failRun(r, "Сервер ответил непонятно: " + e.getMessage());
            }
        };
        // Фоновый наряд можно спросить дважды: по номеру вопроса (ask) сервер считает его один раз.
        if (r.slot != null) callTwice("/api/device/cartplan", body, got, () -> alive(r));
        else call("/api/device/cartplan", body, got);
    }

    private void applyPlanJson(CartRun r, JSONObject plan, Runnable then) {
        try {
                applyPlan(r, plan);
                // Правка по касанию (1.27) выводит из этого наряда следующий, не спрашивая сервер.
                if (r.slot != null && r.linesOverride == null) {
                    memory().edit().putString("plan." + r.code, plan.toString())
                        .putLong("planAt." + r.code, System.currentTimeMillis()).apply();
                }
                then.run();
            } catch (Exception e) {
                failRun(r, "Сервер ответил непонятно: " + e.getMessage());
            }
    }

    /** Разложить наряд по сборке: правила, позиции, снятие своего, поиск незнакомых, отпечаток. */
    private void applyPlan(final CartRun r, JSONObject plan) throws Exception {
        JSONObject rules = plan.optJSONObject("rules");
        JSONArray lines = plan.optJSONArray("lines");
        r.rules = rules != null ? rules : new JSONObject();
        r.rules.put("_note", plan.optString("note", ""));
        r.lines = lines != null ? lines : new JSONArray();
        JSONArray dropArr = plan.optJSONArray("drop");
        for (int i = 0; dropArr != null && i < dropArr.length(); i++) {
            JSONObject d = dropArr.optJSONObject(i);
            if (d == null) continue;
            String sku = d.optString("sku", "");
            String u = d.optString("url", "");
            String n = d.optString("name", "");
            if (!sku.isEmpty()) {
                if (!u.isEmpty()) r.urls.put(sku, u);
                if (!n.isEmpty() && !r.names.has(sku)) r.names.put(sku, n);
            }
        }
        // Позиции, которых сеть не знает: сервер просит найти их поиском самой сети.
        // Там, где сервер к витрине не пробивается (Пятёрочка, Самокат — замер
        // 18.10.2026), это единственная дорога к настоящему артикулу. Строка ложится
        // в тот же список, но с пометкой search: первый шаг у неё — страница поиска.
        // Рядом — второй, короткий адрес поиска (search_alt), если сервер его дал:
        // когда первый запрос принесёт не тот товар, телефон попробует этот.
        // И фасовка товара (pack_g, 1.48): packWhy сверяет её с карточкой до касания. Замер 13.11.2026,
        // Пятёрочка: «хлопья №3 350 г» легли карточкой «…№2 500 г» — веса у строки поиска не было.
        JSONArray search = plan.optJSONArray("search");
        for (int i = 0; search != null && i < search.length(); i++) {
            JSONObject s = search.optJSONObject(i);
            String sUrl = s == null ? "" : s.optString("url", "");
            String sName = s == null ? "" : s.optString("name", "");
            if (sUrl.isEmpty() || sName.isEmpty()) continue;
            JSONObject found = ownFound(r, s);
            if (found != null) {
                r.lines.put(found);
                continue;
            }
            (r.slot != null ? r.later : r.lines).put(new JSONObject()
                .put("name", sName).put("url", "").put("search", sUrl)
                .put("search_alt", s.optString("search_alt", ""))
                .put("clicks", 1).put("sku", "")
                .put("pack_g", s.optDouble("pack_g", 0))
                .put("product_id", s.optInt("product_id", 0)));
        }
        JSONArray searchRecent = plan.optJSONArray("search_recent");
        for (int i = 0; searchRecent != null && i < searchRecent.length(); i++) {
            JSONObject s = searchRecent.optJSONObject(i);
            if (s == null) continue;
            String sName = s.optString("name", "");
            if (sName.isEmpty()) continue;
            if (r.slot != null) {
                r.items.put(new JSONObject()
                    .put("name", sName).put("sku", "").put("url", "")
                    .put("ok", false)
                    .put("why", "поиском сети не нашлась недавно — поищу позже")
                    .put("put", 0).put("times", 0)
                    .put("how", "tap").put("calls", new JSONArray()));
            } else {
                String sUrl = s.optString("url", "");
                if (sUrl.isEmpty()) continue;
                r.lines.put(new JSONObject()
                    .put("name", sName).put("url", "").put("search", sUrl)
                    .put("search_alt", s.optString("search_alt", ""))
                    .put("clicks", 1).put("sku", "")
                    .put("pack_g", s.optDouble("pack_g", 0))
                    .put("product_id", s.optInt("product_id", 0)));
            }
        }
        // Товар в корзине есть, а строки с артикулом в наряде у него нет (ищется, искали недавно, сверх
        // предела поиска, сеть его не получит): найденное для него раньше снимать нельзя (searchedAgain).
        r.noSku.clear();
        JSONArray inBasket = plan.optJSONArray("pids");
        for (int i = 0; inBasket != null && i < inBasket.length(); i++) r.noSku.add(inBasket.optInt(i));
        r.ourTotal = plan.optDouble("total", 0);
        if (plan.has("basket") && !plan.isNull("basket")) r.basket = plan.optInt("basket", r.basket);
        if (!plan.optString("stamp", "").isEmpty()) r.stamp = plan.optString("stamp", "");
        for (int i = 0; i < r.lines.length(); i++) {
            JSONObject line = r.lines.optJSONObject(i);
            String sku = line == null ? "" : line.optString("sku", "");
            if (sku.isEmpty()) continue;
            r.want.put(sku, r.want.optInt(sku, 0) + Math.max(1, line.optInt("clicks", 1)));
            r.names.put(sku, line.optString("name", ""));
            if (line.optInt("product_id", 0) > 0) r.pids.put(sku, line.optInt("product_id", 0));
            r.noSku.remove(line.optInt("product_id", 0));
            JSONArray same = line.optJSONArray("product_ids");    // дубли на одном артикуле
            for (int k = 0; same != null && k < same.length(); k++) r.noSku.remove(same.optInt(k));
        }
    }

    /**
     * Строка товара, который это приложение уже нашло поиском сети и положило (1.49): артикул и адрес
     * карточки — из памяти телефона, дальше она идёт как знакомая. Иначе хвост после каждой правки
     * заново открывал поиск и карточку каждой лежащей позиции: десятки секунд касаний, после которых
     * сеть закрывает запросы (Пятёрочка, корзина из чека, 13.11.2026). В наряде у неё одна штука:
     * незнакомое сети кладётся по одной (changedPlan), настоящее число придёт со связью сервера.
     * Лишнее у неё не снимается, как и у строки поиска (searchedAgain): количество в сети человек
     * мог поправить сам.
     * null — искать, как раньше: сборку ведёт человек, своего артикула у товара нет или их два,
     * артикул занят строкой наряда, нет адреса карточки, фасовку карточки с этим весом не сверяли
     * (положенное до 1.49 один раз сверит хвост).
     */
    private JSONObject ownFound(CartRun r, JSONObject row) throws Exception {
        int pid = row == null ? 0 : row.optInt("product_id", 0);
        if (r.slot == null || pid <= 0) return null;
        String own = "";
        Iterator<String> skus = r.cart.keys();
        while (skus.hasNext()) {
            String sku = skus.next();
            if (r.cart.optInt(sku, 0) <= 0 || r.pids.optInt(sku, 0) != pid) continue;
            if (!own.isEmpty()) return null;
            own = sku;
        }
        String url = r.urls.optString(own, "");
        if (own.isEmpty() || url.isEmpty() || !isHostOf(r.chain, url)) return null;
        for (int i = 0; i < r.lines.length(); i++) {    // артикул занят строкой наряда: два товара на карточке
            JSONObject known = r.lines.optJSONObject(i);
            if (known != null && own.equals(known.optString("sku", ""))) return null;
        }
        double pack = row.optDouble("pack_g", 0);
        if (packChecked(row) && Math.abs(r.packOk.optDouble(own, 0) - pack) > packTolerance(pack)) return null;
        JSONObject line = new JSONObject()
            .put("name", row.optString("name", "")).put("url", url)
            .put("clicks", 1).put("sku", own)
            .put("product_id", pid);
        noteFail(r, line, "");      // прежние отказы поиска и запроса уже не про неё: позиция лежит
        noteFail(r, line, "", "apifail.");
        r.adopted.add(own);
        Log.i(TAG, r.name + ": найдено и положено раньше, искать не нужно: " + line.optString("name", ""));
        return line;
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
        if (superseded(r)) { supersede(r); return; }
        if (r.at >= r.lines.length()) {
            // Строки наряда пройдены. У Ленты последний шаг — ссылка на готовую корзину,
            // собранную сервером (Z9): дальше завершение идёт как раньше, через openCart.
            lentaCartLink(r);
            return;
        }
        JSONObject line = r.lines.optJSONObject(r.at);
        final String sku = line == null ? "" : line.optString("sku", "");
        if (line != null && !sku.isEmpty() && !line.optString("url", "").isEmpty()) {
            try { r.urls.put(sku, line.optString("url", "")); } catch (Exception ignored) { }
        }
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
        r.lineWall = false;
        r.badged = 0;
        r.badgeChecked = false;
        if (r.target - r.have <= 0) {
            keptLine(r, line);
            return;
        }
        // Фоновая сборка не повторяет позицию, которая недавно не легла в эту сеть: каждая
        // правка корзины ждала бы её отказа заново (Лента, Самокат, Пятёрочка — 20–50 с на
        // сборку, журнал 07.11.2026). «Собрать» человека пробует всё.
        if (r.slot != null) {
            String failed = failedRecently(r, line);
            if (!failed.isEmpty()) { laterLine(r, line, failed); return; }
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
     * В фоновой сборке шаг ссылки пропускается: кнопку на ссылке там никто не нажмёт.
     */
    private void lentaCartLink(final CartRun r) {
        if (!alive(r)) return;
        if (!"lenta".equals(r.code) || r.linkAsked || r.slot != null) {
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
        // Найденное прошлым поиском, пока его снова ищут, hasExcess не считает (searchedAgain).
        if (shouldDrop(r) && hasExcess(r)) {
            dropExcess(r, () -> openCart(r));
            return;
        }
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
            JSONObject item = new JSONObject()
                .put("name", line == null ? "" : line.optString("name", ""))
                .put("sku", line == null ? "" : line.optString("sku", ""))
                // Адрес и имя карточки, как у положенной позиции (1.48): по ним сервер даёт товару артикул
                // сети. Без них найденное поиском и уже лежащее искалось бы касаниями после каждой правки.
                .put("url", line == null ? "" : line.optString("url", ""))
                .put("ok", true).put("why", "").put("put", 0).put("times", r.target)
                .put("how", "kept").put("calls", new JSONArray());
            String foundName = line == null ? "" : line.optString("found_name", "");
            if (!foundName.isEmpty()) item.put("found_name", foundName);
            r.items.put(item);
        } catch (Exception ignored) { }
        r.at++;
        status(r, "уже лежит: " + (line == null ? "" : line.optString("name", "")), false);
        handler.post(() -> nextLine(r));
    }

    /** Ключ позиции в памяти отказов: единый товар, иначе артикул сети, иначе имя. */
    private static String lineKey(JSONObject line) {
        if (line == null) return "";
        int id = line.optInt("product_id", 0);
        if (id > 0) return "p" + id;
        String sku = line.optString("sku", "").trim();
        if (!sku.isEmpty()) return "s" + sku;
        String name = line.optString("name", "").trim();
        if (!name.isEmpty()) return "n" + name;
        return "";
    }

    /** Почему позиция не легла в эту сеть меньше FAIL_AGAIN_MS назад; "" — не отказывала. */
    private String failedRecently(CartRun r, JSONObject line) {
        return failedRecently(r, line, "failed.");
    }

    private String failedRecently(CartRun r, JSONObject line, String prefix) {
        return failedSince(r, line, prefix, false);
    }

    /** Почему позиция не легла в эту сеть когда-либо (легла — отказ забыт); "" — не отказывала. */
    private String failedBefore(CartRun r, JSONObject line, String prefix) {
        return failedSince(r, line, prefix, true);
    }

    private String failedSince(CartRun r, JSONObject line, String prefix, boolean ever) {
        try {
            if (r == null) return "";
            String key = lineKey(line);
            if (key.isEmpty()) return "";
            String raw = memory().getString((prefix != null ? prefix : "failed.") + r.code, "{}");
            JSONObject map = new JSONObject(raw);
            JSONObject rec = map.optJSONObject(key);
            if (rec != null) {
                long at = rec.optLong("at", 0);
                boolean notFound = !line.optString("search", "").isEmpty() && !rec.optBoolean("wall");
                if (ever || System.currentTimeMillis() - at < (notFound ? FRESH_MS : FAIL_AGAIN_MS)) {
                    String why = rec.optString("why", "");
                    return why.isEmpty() ? "не легла" : why;
                }
            }
        } catch (Exception ignored) { }
        return "";
    }

    /** Запомнить отказ позиции (why не пуст) или забыть его (легла). Помним 80 последних. */
    private void noteFail(CartRun r, JSONObject line, String why) {
        noteFail(r, line, why, "failed.");
    }

    private void noteFail(CartRun r, JSONObject line, String why, String prefix) {
        if (r == null) return;
        try {
            String key = lineKey(line);
            if (key.isEmpty()) return;
            String prefKey = (prefix != null ? prefix : "failed.") + r.code;
            String raw = memory().getString(prefKey, "{}");
            JSONObject map = new JSONObject(raw);
            if (why == null || why.isEmpty()) {
                if (!map.has(key)) return;
                map.remove(key);
            } else {
                JSONObject rec = new JSONObject();
                rec.put("at", System.currentTimeMillis());
                rec.put("why", oneSpace(why, 80));
                if (r.lineWall) rec.put("wall", true);
                map.put(key, rec);

                if (map.length() > 80) {
                    List<String> keys = new ArrayList<>();
                    Iterator<String> it = map.keys();
                    while (it.hasNext()) keys.add(it.next());
                    keys.sort((a, b) -> {
                        JSONObject recA = map.optJSONObject(a);
                        JSONObject recB = map.optJSONObject(b);
                        long atA = recA != null ? recA.optLong("at", 0) : 0;
                        long atB = recB != null ? recB.optLong("at", 0) : 0;
                        return Long.compare(atA, atB);
                    });
                    while (map.length() > 80 && !keys.isEmpty()) {
                        map.remove(keys.remove(0));
                    }
                }
            }
            memory().edit().putString(prefKey, map.toString()).apply();
        } catch (Exception ignored) { }
    }

    /** Позиция недавно не легла — в отчёт с прежней причиной, без попытки (FAIL_AGAIN_MS). */
    private void laterLine(final CartRun r, JSONObject line, String why) {
        laterItem(r, line, why, r.target);
        r.at++;
        status(r, "позже: " + (line == null ? "" : line.optString("name", "")), false);
        handler.post(() -> nextLine(r));
    }

    private static void laterItem(CartRun r, JSONObject line, String why, int times) {
        try {
            r.items.put(new JSONObject()
                .put("name", line == null ? "" : line.optString("name", ""))
                .put("sku", line == null ? "" : line.optString("sku", ""))
                .put("url", line == null ? "" : line.optString("url", ""))
                .put("ok", false)
                .put("why", fitWhy("не легла недавно, попробую позже: " + why))
                .put("put", 0).put("times", times)
                .put("how", "tap").put("calls", new JSONArray()));
        } catch (Exception ignored) { }
    }

    /**
     * Строки хвоста, которые недавно не легли, — сразу в отчёт. Хвост ради них шёл после каждой
     * правки: ничего не клал, а слал второй отчёт и занимал окно сети (замер 10.11.2026).
     */
    private void settleLater(CartRun r) {
        JSONArray due = new JSONArray();
        for (int i = 0; i < r.later.length(); i++) {
            JSONObject line = r.later.optJSONObject(i);
            if (line == null) continue;
            String failed = failedRecently(r, line);
            if (failed.isEmpty()) due.put(line);
            else laterItem(r, line, failed, Math.max(1, line.optInt("clicks", 1)));
        }
        r.later = due;
    }

    /**
     * Строки, которые запросом не легли, — в хвост, впереди поиска: касаниями после отчёта.
     * Сразу касаниями они держали отчёт сети минутами и грузили общий процесс окон, пока
     * корзину ещё правят (замер 10.11.2026: Пятёрочка после «обрыва сети» — 495 с касаний).
     */
    private static void toTail(CartRun r, JSONArray moved) {
        JSONArray kept = new JSONArray(), later = new JSONArray();
        for (int i = 0; i < moved.length(); i++) later.put(moved.opt(i));
        for (int i = 0; i < r.lines.length(); i++) {
            Object line = r.lines.opt(i);
            boolean gone = false;
            for (int k = 0; k < moved.length() && !gone; k++) gone = moved.opt(k) == line;
            if (!gone) kept.put(line);
        }
        for (int i = 0; i < r.later.length(); i++) later.put(r.later.opt(i));
        r.lines = kept;
        r.later = later;
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
        if (url.equals(open) || (isHostOf(r.chain, open) && want != null && samePath(want, pathOf(open)))) {
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
            r.lineWall = !wall.isEmpty();
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
            r.wall = wall.contains("blocked") || wall.contains("vpn");
            endBackground(r, "failed", fitWhy(why));
        });
    }

    private void askCard(final CartRun r, JSONObject line, final long until, final boolean retried) {
        if (!alive(r)) return;
        if (superseded(r)) { supersede(r); return; }
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
        // Память корзины (cartapi.js) старше этого мига не в счёт: на странице сети её меняют касаниями (1.41).
        // Возврат на robots.txt корзину не трогает — память, прочитанная заранее, остаётся годной.
        if (r.slot != null && !url.endsWith("/robots.txt")) r.slot.movedAt = System.currentTimeMillis();
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
                    boolean here = !path.isEmpty() && !st.optBoolean("o") && (target != null ? samePath(target, path)
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

    // Пути совпадают в точности или по коду карточки в хвосте вида «--4212867»: Пятёрочка
    // открывает /product/--4212867/ и переадресует на /product/<название>--4212867/ (08.11.2026).
    private static boolean samePath(String target, String path) {
        if (target == null || path == null) return false;
        if (target.equals(path)) return true;
        Pattern p = Pattern.compile("--(\\d+)$");
        Matcher mt = p.matcher(target);
        Matcher mp = p.matcher(path);
        return mt.find() && mp.find() && mt.group(1).equals(mp.group(1));
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
                    String sku = ownSku(r, line);
                    // Найденную поиском карточку числим своей, только если это тот же товар: иначе, убрав
                    // свой товар из корзины, приложение сняло бы чужой, который человек положил в сети сам.
                    if (line != null && line.optString("sku", "").isEmpty()
                            && !sameCard(line, got.optString("title", ""))) sku = "";
                    // И «лежит» не говорим, когда фасовка не та (1.48): в корзине сети другой товар. Замер
                    // 13.11.2026: вместо «хлопья №3 350 г» лежали «…№2 500 г», положенные сборкой до 1.48.
                    String different = strangerPack(line, got.optString("title", ""));
                    if (!different.isEmpty()) {
                        lineDone(r, false, different, new JSONArray());
                        return;
                    }
                    r.have = Math.max(Math.max(1, r.have), packs(got.optDouble("count", 1)));
                    if (!sku.isEmpty()) {
                        try {
                            r.cart.put(sku, r.have);
                            ownLine(r, line, sku);
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
            if (r.put < r.clicks) {
                handler.postDelayed(() -> tapAdd(r, true), pause + 500);
            } else {
                r.callsUntil = SystemClock.uptimeMillis() + pause + 1200 + CALLS_WAIT_MS;
                handler.postDelayed(() -> checkLine(r, ""), pause);
            }
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

    /**
     * Один раз после касания: вырос ли признак корзины. Не вырос — перепроверка через
     * 500 мс (счётчик сеть перерисовывает после ответа, а ждём его теперь опросом),
     * затем отказ с дампом.
     */
    private void checkCartSign(final CartRun r, final JSONArray calls, final boolean retried) {
        if (!alive(r)) return;
        r.view.evaluateJavascript(CART_SIGN_JS, value -> {
            if (!alive(r)) return;
            JSONObject sign = asObject(value);
            boolean known = sign.optBoolean("known");
            int n = sign.optInt("n", 0);
            boolean on = sign.optBoolean("on");
            if (known && !on && n <= r.badged) {
                if (!retried) {
                    handler.postDelayed(() -> checkCartSign(r, calls, true), 500);
                    return;
                }
                final String base = "карточка открылась, кнопка нажата, но сеть не показала товар в корзине";
                pageDump(r, dump -> lineDone(r, false, cardWhy(base, dump, null), calls));
                return;
            }
            verifyLine(r, calls);
        });
    }

    private void checkCartSign(final CartRun r, final JSONArray calls) {
        checkCartSign(r, calls, false);
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
     * Опрос ответов сети: пока calls пуст и callsUntil не вышел, перепроверяем каждые 250 мс.
     */
    private void checkLine(final CartRun r, final String failure, final JSONArray before, final boolean waited) {
        if (!alive(r)) return;
        r.view.evaluateJavascript(CALLS_JS, value -> {
            if (!alive(r)) return;
            JSONArray got = asObject(value).optJSONArray("calls");
            final JSONArray calls = new JSONArray();
            for (int i = 0; i < before.length(); i++) calls.put(before.opt(i));
            for (int i = 0; got != null && i < got.length(); i++) calls.put(got.opt(i));
            if (failure.isEmpty() && calls.length() == 0 && r.put > 0 && SystemClock.uptimeMillis() < r.callsUntil) {
                handler.postDelayed(() -> checkLine(r, failure, calls, false), 250);
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
        // Артикул найденного раньше взят из адреса карточки (ownFound): в запросе сети бывает другой номер.
        if (sku.isEmpty() || r.adopted.contains(sku)) {
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

    /**
     * Карточка — тот же товар, что строка наряда: название и фасовка, как перед касанием «В корзину».
     * Без заголовка судить не по чему: такую карточку своей не числим.
     */
    private static boolean sameCard(JSONObject line, String title) {
        return !title.isEmpty() && nameWhy(line, title).isEmpty()
            && (!packChecked(line) || packWhy(line, title).isEmpty());
    }

    /** У найденной поиском карточки фасовка не та, что в наряде: причина или пустая строка (1.48). */
    private static String strangerPack(JSONObject line, String title) {
        if (line == null || !line.optString("sku", "").isEmpty() || !packChecked(line)) return "";
        return packWhy(line, title);
    }

    /**
     * Артикул позиции для памяти телефона. У строки наряда он свой; у товара, найденного поиском самой
     * сети, его нет — он в адресе карточки, и правило то же, что у сервера (rules.sku_url,
     * app/catalog/fromurl.py). Без него память такую позицию не знала: товар убирали из корзины, а в
     * корзине сети он оставался (Магнит, 13.11.2026). В строку наряда найденный артикул не пишем: по
     * артикулу строки идёт сверка запроса сети (verifyLine), а в запросе у сети бывает другой номер.
     */
    private static String ownSku(CartRun r, JSONObject line) {
        if (line == null) return "";
        String sku = line.optString("sku", "");
        String rule = r.rules.optString("sku_url", ""), url = line.optString("url", "");
        if (!sku.isEmpty() || !line.has("search") || rule.isEmpty() || url.isEmpty()
                || !isHostOf(r.chain, url)) return sku;
        try {
            Matcher m = Pattern.compile(rule).matcher(url);
            if (!m.find()) return "";
            for (int g = 1; g <= m.groupCount(); g++) {    // у правила бывает две ветви (Дикси)
                String got = m.group(g);
                if (got != null && !got.isEmpty()) return got;
            }
        } catch (Exception ignored) { }
        return "";
    }

    /** Чей товар, как назван и где карточка: строка наряда приносит это сама, найденное поиском — нет. */
    private static void ownLine(CartRun r, JSONObject line, String sku) {
        try {
            if (line.optInt("product_id", 0) > 0) r.pids.put(sku, line.optInt("product_id", 0));
            if (!r.names.has(sku)) r.names.put(sku, line.optString("name", ""));
            String url = line.optString("url", "");
            if (!r.urls.has(sku) && !url.isEmpty()) r.urls.put(sku, url);
            // Сюда карточка приходит после сверки фасовки с весом наряда: перед касанием (onCard) или
            // лежащей (tapAdd).
            if (packChecked(line)) r.packOk.put(sku, line.optDouble("pack_g", 0));
        } catch (Exception ignored) { }
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
        noteFail(r, line, ok ? "" : say);
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
        final String own = ownSku(r, line);
        if (taken && !own.isEmpty() && r.put > 0) {
            // Память телефона: сколько этой позиции теперь лежит в корзине сети. По ней
            // следующая сборка положит только недостающее. Касание прибавляет упаковку;
            // запрос сети, которая ставит количество (qty_sets), ставит итоговое число.
            boolean sets = "request".equals(how) && r.rules.optBoolean("qty_sets");
            try {
                r.cart.put(own, sets ? r.target : r.cart.optInt(own, 0) + r.put);
                ownLine(r, line, own);
            } catch (Exception ignored) { }
            saveLedger(r);
        }
        r.at++;
        r.how = "tap";
        // Первая позиция легла касанием «В корзину» — выучить по ней запрос сети и дальше
        // класть им. По касанию «+» учимся только у сети, чей запрос ставит число
        // (qty_sets): у прочих «ещё одну» бывает другим запросом. Образец хранится
        // между сборками, а свежесть проверяет сам повтор (не принят — учимся заново).
        boolean sets = r.rules.optBoolean("qty_sets");
        if (ok && "tap".equals(how) && (r.have == 0 || sets) && !r.learned && !r.learnTried && !sku.isEmpty()
                && !r.adopted.contains(sku)) {      // артикул из адреса карточки — не образец запроса (ownFound)
            r.learnTried = true;
            // У сети, что ставит число, образец — всегда одна своя позиция: после «+» в теле
            // вся корзина, и повтор с ней положил бы артикул дважды — Магнит ответил 500.
            learn(r, sku, sets);
            return;
        }
        syncCart(r, taken && r.put > 0 ? own : "", () -> {
            if (r.at >= r.lines.length()) nextLine(r);
            else handler.postDelayed(() -> nextLine(r), pauseNext(r));
        });
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
            Runnable proceed = () -> syncCart(r, sku, () -> {
                if (r.at >= r.lines.length()) nextLine(r);
                else handler.postDelayed(() -> nextLine(r), pauseAfter(r));
            });
            if (got.optBoolean("ok")) {
                r.learned = true;
                status(r, r.name + ": дальше кладу запросом сети " + got.optString("method") + " "
                    + got.optString("path"), false);
                // Сохраняем выученный образец между сборками (2.2)
                r.view.evaluateJavascript("JSON.stringify({tpl:window.__kzTpl,origin:location.origin})", tplVal -> {
                    if (!alive(r)) return;
                    JSONObject payload = asObject(tplVal);
                    if (payload.has("tpl") && !payload.isNull("tpl")) {
                        try {
                            payload.put("at", System.currentTimeMillis());
                            String raw = payload.toString();
                            tpls.put(r.code, raw);
                            vault().putEncrypted("tpl." + r.code, raw);
                        } catch (Exception ignored) { }
                    }
                    proceed.run();
                });
                return;
            } else {
                status(r, r.name + ": запрос сети не выучен (" + got.optString("why", "?") + "), дальше касаниями", false);
                proceed.run();
            }
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
            String st = unquote(started);
            if (!"started".equals(st)) {
                if (r.reusedTpl) {
                    dropTpl(r.code);
                    r.reusedTpl = false;
                    r.learnTried = false;
                }
                r.learned = false;
                status(r, r.name + ": сеть не приняла запрос (" + st
                    + "), дальше касаниями", true);
                cardLine(r, line);
                return;
            }
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
                            handler.postDelayed(again, 100);
                            return;
                        }
                        if (res.optBoolean("ok")) {
                            if (r.reusedTpl && !r.extendedTpl) {
                                extendTpl(r.code);
                                r.extendedTpl = true;
                            }
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
                        if (r.reusedTpl) {
                            dropTpl(r.code);
                            r.reusedTpl = false;
                            r.learnTried = false;
                        }
                        r.learned = false;
                        status(r, r.name + ": сеть не приняла запрос (" + res.optInt("status", 0)
                            + "), дальше касаниями", true);
                        cardLine(r, line);
                    });
                }
            }, 100);
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

    /**
     * Итог корзины ответом самой сети (api_total наряда). Магнит: checkout/preview, поле
     * summary.totalFinalPrice сверено со страницей корзины 17.10.2026 (app/phonecart.py,
     * API_TOTALS). Страница корзины — 3–5 с, ответ — доли секунды. Запрос идёт из скрытого
     * окна с куками сети и заголовками образца (__kzTpl), значения наружу не уходят. Любой
     * сбой — прежний путь, страница корзины.
     */
    private void apiTotal(final CartRun r, final JSONObject rule) {
        if (!alive(r)) return;
        status(r, r.name + ": спрашиваю итог у сети", false);
        r.view.evaluateJavascript(API_TOTAL_JS.replace("%s", rule.toString()), started -> {
            if (!alive(r)) return;
            String st = unquote(started);
            if (!"started".equals(st)) {
                openCart(r);
                return;
            }
            final long until = SystemClock.uptimeMillis() + API_WAIT_MS;
            handler.postDelayed(new Runnable() {
                @Override
                public void run() {
                    if (!alive(r)) return;
                    final Runnable again = this;
                    r.view.evaluateJavascript(API_POLL_JS, value -> {
                        if (!alive(r)) return;
                        JSONObject res = asObject(value);
                        if (res.length() == 0 && SystemClock.uptimeMillis() < until) {
                            handler.postDelayed(again, 100);
                            return;
                        }
                        if (res.length() == 0 || !res.optBoolean("ok")) {
                            openCart(r);
                            return;
                        }
                        JSONObject fields = res.optJSONObject("fields");
                        String totalKey = rule.optString("total", "");
                        if (fields == null || totalKey.isEmpty() || !fields.has(totalKey)) {
                            openCart(r);
                            return;
                        }
                        try {
                            double scale = rule.optDouble("scale", 1.0);
                            double total = Math.round(fields.optDouble(totalKey) * scale * 100.0) / 100.0;
                            JSONObject cart = new JSONObject();
                            JSONArray totals = new JSONArray();
                            JSONObject totalRow = new JSONObject();
                            totalRow.put("kind", "total");
                            totalRow.put("label", "Итого");
                            totalRow.put("amount", total);
                            totalRow.put("row", "Итого " + money(total) + " (ответ сети)");
                            totals.put(totalRow);
                            cart.put("totals", totals);
                            JSONObject apiObj = new JSONObject();
                            String path = rule.optString("path", "");
                            apiObj.put(path, fields);
                            cart.put("api", apiObj);
                            cart.put("page", path);
                            String countKey = rule.optString("count", "");
                            if (!countKey.isEmpty() && fields.has(countKey)) {
                                cart.put("count", fields.optInt(countKey));
                            }
                            if (nothingPut(r) && cart.optInt("count", -1) == 0) {
                                openCart(r);
                                return;
                            }
                            finishRun(r, cart, "");
                        } catch (Exception e) {
                            openCart(r);
                        }
                    });
                }
            }, 100);
        });
    }

    /** Открыть корзину сети: своей ссылкой со страницы, кнопкой «Корзина» или адресом из наряда. */
    private void openCart(final CartRun r) {
        if (!alive(r)) return;
        if (superseded(r)) { supersede(r); return; }
        if (r.slot != null && !r.again) {
            if (!r.tail && r.later.length() > 0) { finishRun(r, new JSONObject(), ""); return; }
            // Итог ответом самой сети (api_total наряда, Магнит): без страницы корзины.
            JSONObject api = r.rules.optJSONObject("api_total");
            if (api != null && !r.apiTried) { r.apiTried = true; apiTotal(r, api); return; }
            // Легло не всё: итог сети «Где дешевле» тогда не берёт (app/netprice.py), а страница
            // корзины — 3–10 с. Пустую корзину это не прячет: класть что-то удалось.
            if (!nothingPut(r) && someFailed(r)) { finishRun(r, new JSONObject(), ""); return; }
            // Страница корзины этой сети две сборки подряд не показала итога — не открываем.
            // Каждая пятая сборка открывает: вдруг итог появился, и пустую корзину после
            // заказа тоже видно.
            if (memory().getInt("nototal." + r.code, 0) >= 2) {
                int skipped = memory().getInt("noread." + r.code, 0);
                if (skipped < 4) {
                    memory().edit().putInt("noread." + r.code, skipped + 1).apply();
                    finishRun(r, new JSONObject(), "");
                    return;
                }
                memory().edit().putInt("noread." + r.code, 0).apply();
            }
        }
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
        if (superseded(r)) { supersede(r); return; }
        r.cartRead = true;
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
                // Фоновая сборка сети без итога сюда доходит только каждой пятой (openCart) и
                // ждёт полностью.
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

    /** Итог корзины, прочитанный со страницы сети; 0 — не прочитан. */
    private static double totalOf(JSONObject cart) {
        JSONArray totals = cart.optJSONArray("totals");
        for (int i = 0; totals != null && i < totals.length(); i++) {
            JSONObject t = totals.optJSONObject(i);
            if (t != null && "total".equals(t.optString("kind")) && t.optDouble("amount", 0) > 0) {
                return t.optDouble("amount", 0);
            }
        }
        return 0;
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

    /** Хоть одна позиция этой сборки не легла (или отложена). */
    private static boolean someFailed(CartRun r) {
        if (r == null || r.items == null) return false;
        for (int i = 0; i < r.items.length(); i++) {
            JSONObject it = r.items.optJSONObject(i);
            if (it != null && !it.optBoolean("ok", false)) return true;
        }
        return false;
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
        say(Sign.FAIL, "", why);
    }

    /** Показать, что вышло, и отдать отчёт серверу. Корзину не трогаем: оформляет человек. */
    private void finishRun(final CartRun r, JSONObject cart, String stoppedWhy) {
        final boolean background = r.slot != null;
        if (background && !r.tail && stoppedWhy.isEmpty()) settleLater(r);
        final boolean early = background && !r.tail && r.later.length() > 0 && stoppedWhy.isEmpty();
        if (!background) {
            cartRun = null;
            cartBtn.setText("Собрать");
            setChainButtons(currentChain >= 0);
        } else if (r.slot.run == r) {
            r.slot.run = null;
        }

        // Счётчик фоновых сборок подряд без найденного итога на странице (1.5). Остановленная
        // сборка корзину не читала — она не в счёт.
        if (background && stoppedWhy.isEmpty() && r.cartRead) {
            String nototalKey = "nototal." + r.code;
            if (hasTotal(cart)) {
                memory().edit().putInt(nototalKey, 0).apply();
            } else {
                int count = memory().getInt(nototalKey, 0);
                memory().edit().putInt(nototalKey, count + 1).apply();
            }
        }

        // Не найден товар в корзине сети при повторе запроса — сброс образца (2.6)
        if (r.reusedTpl) {
            for (int i = 0; i < r.items.length(); i++) {
                JSONObject it = r.items.optJSONObject(i);
                if (it != null && "request".equals(it.optString("how")) && !it.optBoolean("ok")) {
                    String why = it.optString("why", "");
                    if (why.contains("в корзине сети его не видно")) {
                        dropTpl(r.code);
                        break;
                    }
                }
            }
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
        // От касания до отчёта: по этому числу сервер видит, уложилась ли правка в 5 с (1.29).
        // У отчёта хвоста своего касания нет; сборка без касания (запуск, чужая правка) его не несёт.
        final long lag = r.changedAt > 0 ? SystemClock.uptimeMillis() - r.changedAt : -1;
        r.changedAt = 0;
        final int landedNow = landed;
        // Панель: «готово» — всё легло, лишнего в корзине сети нет и сборку не останавливали, иначе «не всё».
        // Остановили — в знаменателе весь наряд, а не то, что успели. Сумма — только итог самой сети: наш
        // расчёт (r.ourTotal) на панели не показываем, он похож на итог сети (в диалоге он подписан).
        final int planned = r.items.length();
        final boolean stopped = !stoppedWhy.isEmpty();
        final int outOf = stopped ? r.lines.length() : planned;
        final boolean extra = extraOf(r).length() > 0;
        final Sign verdict = outOf == 0 ? Sign.NONE
            : !stopped && landed == outOf && !extra ? Sign.DONE : Sign.PART;
        final double netTotal = totalOf(cart);
        final String share = outOf == 0 ? "" : landed + "/" + outOf;
        final String sum = outOf == 0 ? r.name : verdict == Sign.DONE && netTotal > 0 ? rub(netTotal) : "";
        final String said = r.name + ": легло " + landed + " из " + outOf
            + (stopped ? ", остановлено" : extra ? ", в корзине сети лишнее" : "");
        if (!background) {
            new AlertDialog.Builder(this)
                .setTitle(r.name + ": легло " + landed + " из " + r.items.length() + " за " + seconds + " с")
                .setMessage(msg.toString())
                .setPositiveButton("Готово", null)
                .show();
            say(verdict, share, sum, said + ", отправляю отчёт");
        } else {
            r.now = early
                ? "легло " + landed + " из " + (r.items.length() + r.later.length()) + " за " + seconds + " с, ищу в сети ещё " + r.later.length()
                : "легло " + landed + " из " + r.items.length() + " за " + seconds + " с";
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
        // Пустой наряд (класть нечего, только снять своё) помним тоже (1.45): без этого сборка шла заново
        // с каждой страницей — телефон владельца, 13.11.2026: Лента 7 раз и Пятёрочка 4 раза за минуту.
        // Своё не снялось — «не вышло»: повтор не раньше RETRY_MS или после правки корзины.
        // shortcut: то, что снять нельзя вовсе, пробуем каждые RETRY_MS, пока корзина пуста; счётчик попыток — если увидим в журнале.
        if (!early && stoppedWhy.isEmpty()) remember(r, r.items.length() > 0 || !hasExcess(r));
        try {
            JSONArray reportItems = r.items;
            if (early) {
                reportItems = new JSONArray();
                for (int i = 0; i < r.items.length(); i++) reportItems.put(r.items.opt(i));
                for (int i = 0; i < r.later.length(); i++) {
                    JSONObject lat = r.later.optJSONObject(i);
                    String lName = lat != null ? lat.optString("name", "") : "";
                    reportItems.put(new JSONObject()
                        .put("name", lName)
                        .put("sku", "")
                        .put("url", "")
                        .put("ok", false)
                        .put("why", lat == null || lat.optString("search", "").isEmpty()
                            ? "запросом не легла, положу касаниями" : "ищу в сети после знакомых")
                        // Почему запросами не вышло (1.45) — пометкой: причину сервер разбирает по словам сети
                        // («войдите», «выберите магазин», «нет в наличии»), а пометку только хранит.
                        .put("note", fitWhy(r.apiWhy))
                        .put("put", 0)
                        .put("times", 0)
                        .put("how", "tap")
                        .put("calls", new JSONArray()));
                }
            }
            JSONObject body = new JSONObject()
                .put("store", r.code)
                .put("app_version", BuildConfig.VERSION_NAME)
                .put("items", reportItems)
                .put("cart_page", cart.optString("page", ""))
                .put("our_total", r.ourTotal)
                .put("page_totals", pageTotals)
                .put("api_totals", api == null ? new JSONObject() : api)
                .put("background", background)
                .put("seconds", seconds)
                .put("extra", extraOf(r));
            if (lag >= 0 && lag < 120000) body.put("lag_ms", lag);
            // Сколько сборка ждала наряд (1.44): в журнале правок видно, сервер это или дорога к нему.
            long planMs = r.planAt - r.startedAt;
            if (r.planAt > 0 && planMs >= 0 && planMs < 120000) body.put("plan_ms", planMs);
            if (r.basket >= 0) body.put("basket", r.basket);
            if (!r.stamp.isEmpty()) body.put("stamp", r.stamp);
            if (cart.has("count") && !cart.isNull("count")) body.put("cart_count", cart.optInt("count"));
            // Хроника приложения (1.54): что окно Пятёрочки решало до этого отчёта, в том числе на нажатиях, которых
            // отчёт не дал. Сервер хранит её отдельно (phonecart.traces).
            if ("pyaterochka".equals(r.code)) body.put("trace", Log.tail(r.name, r.code, 120, 30));
            final String name = r.name;
            call("/api/device/cartreport", body, (status, text) -> {
                // Отчёт дал товару артикул сети (bound, 1.48): сервер связал его с карточкой, которую
                // телефон нашёл и положил. Наряд в памяти ещё ищет товар касаниями — его срок вышел:
                // fetchPlan возьмёт новый, и следующую правку сеть примет запросом.
                try {
                    if (status == 200 && new JSONObject(text).optInt("bound", 0) > 0) {
                        memory().edit().remove("planAt." + r.code).apply();
                    }
                } catch (Exception ignored) { }
                if (background) {
                    if (status != 200) Log.w(TAG, name + ": отчёт не ушёл — " + serverError(status, text));
                    tellPage();
                    return;
                }
                // Уже идёт новая сборка или человек перешёл в другую сеть — строка там не наша.
                if (cartRun != null || currentChain != r.chain) return;
                if (status == 200) say(verdict, share, sum, said + ", отчёт на сервере");
                else say(Sign.FAIL, "", "отчёт не ушёл — " + serverError(status, text), null);
            });
        } catch (Exception ignored) { }
        if (background) {
            Log.i(TAG, r.name + ": фоновая сборка — легло " + landedNow + " из " + r.items.length()
                + " за " + seconds + " с" + (lag >= 0 ? ", от касания " + lag + " мс" : ""));
            if (early) tailLater(r); else rest(r.slot);
        }
    }

    /**
     * Хвост (поиск незнакомых) — только после TAIL_QUIET_MS без правок и касаний. Он грузит тяжёлые
     * страницы сети и держит её окно: правка ждала конца его шага, а соседние сети — общий
     * поток WebView (эмулятор, 09.11.2026: Дикси начала через 13 с после касания).
     */
    private void tailLater(final CartRun r) {
        final Slot s = r.slot;
        rest(s);
        // Дольше трёх TAIL_QUIET_MS касаний не ждём: листает «Где дешевле» — незнакомые ему тоже нужны.
        final long until = SystemClock.uptimeMillis() + 3L * TAIL_QUIET_MS;
        handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                if (s.run != null || s.last != r || s.view != r.view || !s.pending.isEmpty() || away) return;
                // Окно лечит куку защиты на главной странице (whenHealed): хвост увёл бы его оттуда (1.50).
                if (healing(s)) {
                    handler.postDelayed(this, 250);
                    return;
                }
                // Экрана касались меньше TAIL_QUIET_MS назад — человек ещё выбирает, правка близко (1.36):
                // хвост, начатый под палец, встречал её тяжёлой страницей в общем потоке окон.
                long now = SystemClock.uptimeMillis();
                long quiet = now - touchedAt;
                if (quiet < TAIL_QUIET_MS && now < until) handler.postDelayed(this, TAIL_QUIET_MS - quiet);
                else startTail(r);
            }
        }, TAIL_QUIET_MS);
    }

    /**
     * Что лежит в корзине сети сверх корзины человека: было в ней раньше, теперь нет или
     * меньше. Приложение этого не убирает — решает человек, — а называет в отчёте.
     */
    private JSONArray extraOf(CartRun r) {
        JSONArray out = new JSONArray();
        Iterator<String> keys = r.cart.keys();
        while (keys.hasNext()) {
            String sku = keys.next();
            int more = r.cart.optInt(sku, 0) - wantOf(r, sku);
            String name = r.names.optString(sku, "");
            // Найденное поиском сети для товара из корзины — не лишнее, хотя артикула в наряде у него нет.
            if (more <= 0 || name.isEmpty() || searchedAgain(r, sku)) continue;
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
        // Та же корзина с другим отпечатком — её правили; без «правки по касанию» так приходит новый товар.
        final boolean changed = lastCollect != null && !lastCollect.optString("stamp", "").isEmpty()
                && lastCollect.optInt("basket", -1) == payload.optInt("basket", -2)
                && !lastCollect.optString("stamp", "").equals(payload.optString("stamp", ""));
        if (lastCollect == null) peekLive = SystemClock.uptimeMillis();     // запуск: касаний ещё не было
        lastCollect = payload;
        int budget = payload.optInt("budget_ms", 0);
        collectDeadline = budget > 0 ? SystemClock.uptimeMillis() + Math.min(5000, budget) : 0;
        JSONObject answer = new JSONObject();
        JSONArray starting = new JSONArray();
        JSONObject skip = new JSONObject();
        final String stamp = payload.optString("stamp", "");
        final int basket = payload.optInt("basket", -1);
        JSONObject order = getOrderCurrent();
        if (order != null) {
            String orderStamp = order.optString("stamp", "");
            if (!stamp.equals(orderStamp)) {
                // Корзину правили — заказ забыть, сборка как обычно (1.19)
                memory().edit().remove("order.current").apply();
                order = null;
            }
        }
        JSONArray chains = payload.optJSONArray("chains");
        int warming = 0;
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
                // Окно сети с запросами открываем заранее (rest ставит его на robots.txt): первая
                // правка не ждёт загрузки окна, до 1,1 с (замер 09.11.2026). По одному, с шагом:
                // пять окон разом занимали главный поток на секунды, и касание человека ждало
                // (эмулятор под нагрузкой, 10.11.2026: «приложение не отвечает»).
                if (s.view == null && s.run == null && cartApiOrigin(code) != null) {
                    final Slot cold = s;
                    handler.postDelayed(() -> {
                        if (cold.view != null || cold.run != null || away) return;
                        cold.view = hiddenView(idx);
                        // Сразу на robots.txt: rest ждёт 1,5 с, и правка в первые секунды после запуска или
                        // возвращения заставала окно пустым (замер 11.11.2026: +0,8–2,2 с к правке).
                        cold.view.loadUrl(cartApiOrigin(code) + "/robots.txt");
                        rest(cold);
                    }, 300L * warming++);
                }
                if (cartRun != null && cartRun.chain == idx) {
                    skip.put(code, "собираете сами");
                    continue;
                }
                if (s.run != null) {
                    if (!stamp.equals(s.run.stamp)) {
                        s.pending = stamp;   // доберём после этой сборки
                        // Касания устаревшей корзины бросаем сразу (1.27): окна делят процесс страниц,
                        // и витрина в окне с касаниями держала запросы других сетей 10–19 с
                        // (замер 09.11.2026). Ждём только запись cartapi.js — она кончается за секунды.
                        // shortcut: касание в полёте может лечь мимо памяти телефона, и такую
                        // позицию никто не снимет; увидим лишнее в корзинах сетей — ждать конца шага.
                        if (!writing(s.run)) supersede(s.run);
                    }
                    starting.put(code);
                    continue;
                }
                JSONObject done = doneOf(code);
                long doneAt = done != null ? done.optLong("at", 0) : 0;
                boolean fresh = done != null && (System.currentTimeMillis() - doneAt < FRESH_MS);
                // Стену сети правка корзины не снимает, а проверка Qrator у Ленты полминуты грузит
                // общий процесс окон (замер 09.11.2026): до RETRY_MS сеть не будим.
                if (done != null && done.optBoolean("wall") && System.currentTimeMillis() - doneAt < RETRY_MS) {
                    skip.put(code, "сеть не пускала, попробую позже");
                    continue;
                }
                // После «Заказать» прежнее «собрано» не в счёт: доля и уборка — новая сборка (1.19).
                boolean sameOrder = order == null
                        || (done != null && done.optLong("order", -1) == order.optLong("at", -2));
                if (done != null && sameOrder && stamp.equals(done.optString("stamp")) && basket == done.optInt("basket", -2)
                        && ((done.optBoolean("ok") && fresh)
                            || (!done.optBoolean("ok") && System.currentTimeMillis() - doneAt < RETRY_MS))) {
                    // Собирать нечего, но наряд для правки по касанию должен лежать в памяти; после «Заказать»
                    // правка по касанию его не выводит.
                    if (order == null) fetchPlan(s, stamp, basket);
                    skip.put(code, done.optBoolean("ok") ? "уже собрано для этой корзины (свежее)"
                                                         : "не вышло, попробую позже");
                    continue;
                }
                if (order != null && stamp.equals(order.optString("stamp", ""))) {
                    JSONObject orderStores = order.optJSONObject("stores");
                    boolean inStores = orderStores != null && orderStores.has(code);
                    if (!inStores) {
                        // Сеть не из stores — только снятие своего (want = 0 для всего ledger)
                        // Сборку полной корзины поверх не запускать
                        JSONObject led = new JSONObject(memory().getString("ledger." + code, "{}"));
                        boolean hasAny = false;
                        Iterator<String> lk = led.keys();
                        while (lk.hasNext()) {
                            JSONObject it = led.optJSONObject(lk.next());
                            if (it != null && it.optInt("q", 0) > 0) { hasAny = true; break; }
                        }
                        if (!hasAny) {
                            skip.put(code, "в корзине сети нашего нет");
                            continue;
                        }
                    }
                }
                // Правку по касанию fastChange уже поставил на отсчёт: ответ сервера его не отодвигает (1.29).
                if (s.soon == null || !stamp.equals(s.pending)) {
                    s.pending = stamp;
                    soon(s);
                }
                starting.put(code);
            } catch (Exception ignored) { }
        }
        // Время правки без «правки по касанию» (новый товар идёт через сервер) — от последнего касания, если оно
        // не старше 10 с (1.37): отчёты таких правок шли без времени, и журнал сервера их не видел. У сети,
        // которая эту корзину не собирает и запуска не ждёт, время стираем: иначе оно ушло бы в отчёт
        // другой правки.
        Set<String> started = new HashSet<>();
        for (int i = 0; i < starting.length(); i++) started.add(starting.optString(i));
        if (order == null && started.size() >= 2) {
            prefetchBatchPlans(basket, stamp, new ArrayList<>(started));
        }
        long now = SystemClock.uptimeMillis();
        for (Slot s : slots.values()) {
            if (!started.contains(s.code)) {
                if (s.soon == null) s.changedAt = 0;
            } else if (changed && s.changedAt == 0 && now - touchedAt < 10000
                    && (s.run == null || !stamp.equals(s.run.stamp))) {
                s.changedAt = touchedAt;
            }
        }
        // Что решили по каждой сети с окном (1.54): в хронику, когда решение или корзина поменялись — collect
        // приходит с каждой правкой и с каждым возвращением на экран.
        for (Slot s : slots.values()) {
            String verdict = skip.has(s.code) ? "не собираю: " + skip.optString(s.code, "")
                    : started.contains(s.code) ? "собираю" + (s.run != null ? " (сборка уже идёт, доберу)" : "") : "";
            if (verdict.isEmpty()) continue;
            verdict += " [" + (stamp.length() > 6 ? stamp.substring(0, 6) : stamp) + "]";
            if (verdict.equals(lastVerdict.get(s.code))) continue;
            lastVerdict.put(s.code, verdict);
            Log.i(TAG, CHAINS[s.chain][0] + ": разбор корзины — " + verdict);
        }
        try {
            answer.put("starting", starting).put("skip", skip);
        } catch (Exception ignored) { }
        peekSoon(1500);     // окну сети — время открыться
        return answer;
    }

    /**
     * Наряд без сборки (1.33). Обновление приложения стирает planAt.* у всех сетей, наряд старше PLAN_MS
     * негоден — и первая правка шла за ним на сервер (замер 11.11.2026: Дикси 4,3–6,0 с вместо 1,6).
     * Берём его, пока корзину не правят. Сборки нет: сеть уже собрана, окно и запись в её корзину ни к
     * чему, а касание во время сборки оборвало бы её и пошло бы за нарядом второй раз.
     */
    private void fetchPlan(final Slot s, final String stamp, final int basket) {
        long now = SystemClock.uptimeMillis();
        // Не чаще раза в минуту: сервер мог не ответить, а collect приходит с каждой правкой.
        if (cartApiOrigin(s.code) == null || (s.planAsked != 0 && now - s.planAsked < 60000)
                || System.currentTimeMillis() - memory().getLong("planAt." + s.code, 0) <= PLAN_MS) return;
        s.planAsked = now;
        JSONObject body = new JSONObject();
        try {
            body.put("store", s.code).put("app_version", BuildConfig.VERSION_NAME)
                .put("background", true).put("basket", basket);
            // Как в askPlan: своё без адреса карточки. Адрес из наряда нужен, когда запросы откажут
            // и уменьшенное придётся снимать касаниями.
            JSONObject led = new JSONObject(memory().getString("ledger." + s.code, "{}"));
            JSONArray dropSkus = new JSONArray();
            for (Iterator<String> it = led.keys(); it.hasNext(); ) {
                String sku = it.next();
                JSONObject item = led.optJSONObject(sku);
                if (item != null && item.optInt("q", 0) > 0 && item.optString("u", "").isEmpty()) dropSkus.put(sku);
            }
            if (dropSkus.length() > 0) body.put("drop", dropSkus);
        } catch (Exception ignored) { }
        call("/api/device/cartplan", body, (status, text) -> {
            // Корзину уже поправили или сборка взяла наряд сама — этот затёр бы наряд новее.
            if (status != 200 || lastCollect == null || !stamp.equals(lastCollect.optString("stamp", ""))
                    || System.currentTimeMillis() - memory().getLong("planAt." + s.code, 0) <= PLAN_MS) return;
            try {
                if (!stamp.equals(new JSONObject(text).optString("stamp", "-"))) return;
            } catch (Exception e) {
                return;
            }
            memory().edit().putString("plan." + s.code, text)
                .putLong("planAt." + s.code, System.currentTimeMillis()).apply();
            Log.i(TAG, s.code + ": наряд взят заранее, без сборки");
        });
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

    /** Другая сеть с запросами (cartapi.js) вот-вот начнёт или ещё кладёт. */
    private boolean apiRunning(Slot self) {
        for (Slot o : slots.values()) {
            if (o == self || cartApiOrigin(o.code) == null) continue;
            if (o.run != null ? !o.run.tail && !o.run.apiOver : o.soon != null) return true;
        }
        return false;
    }

    /**
     * Соединения с узлами корзин открываем по касанию экрана (1.29): счётчик человек открывает раньше,
     * чем жмёт «+», и к первому запросу DNS и TLS уже позади. Замер 11.11.2026 на эмуляторе: первое
     * чтение корзины Пятёрочки после простоя — 0,7 с вместо 4,0 с. Запросов сети подсказка не шлёт.
     * Вторая, анонимная, — для предварительного запроса браузера (CORS): он ходит своим соединением.
     */
    private void preconnect() {
        long now = SystemClock.uptimeMillis();
        if (away || now - preconnectedAt < PRECONNECT_MS) return;
        if (chainPanel != null && chainPanel.getVisibility() == View.VISIBLE) return;   // человек в окне сети
        for (Slot s : slots.values()) {
            String origin = cartApiOrigin(s.code);
            if (origin == null || s.view == null || s.run != null) continue;
            preconnectedAt = now;
            String host = cartApiHost(s.code);
            s.view.evaluateJavascript("(function(o,h){if(location.origin!==o)return;"
                + "[[o,0],[h,0],[h,1]].forEach(function(p){if(!p[0])return;var l=document.createElement('link');"
                + "l.rel='preconnect';if(p[1])l.crossOrigin='anonymous';l.href=p[0];"
                + "(document.head||document.documentElement).appendChild(l);"
                + "setTimeout(function(){l.remove();},1000);});})("
                + JSONObject.quote(origin) + "," + JSONObject.quote(host == null ? "" : host) + ")", null);
        }
    }

    @Override
    public boolean dispatchTouchEvent(MotionEvent ev) {
        touchedAt = SystemClock.uptimeMillis();
        if (ev.getActionMasked() == MotionEvent.ACTION_DOWN) {
            preconnect();
            peekByTouch = true;
            peekSoon(0);
        }
        return super.dispatchTouchEvent(ev);
    }

    /** Прочитать корзину Пятёрочки заранее через delay мс; дальше peek заводит себя сам. */
    private void peekSoon(long delay) {
        handler.removeCallbacks(peekLoop);
        handler.postDelayed(peekLoop, delay);
    }

    private void peek() {
        final long now = SystemClock.uptimeMillis();
        boolean any = false;
        for (String[] p : PEEKS) any |= slots.get(p[0]) != null;
        // Человек не на экране корзины или давно его не трогал — сеть не тревожим: заведут касание и collect.
        // Касание при этом помним (peekByTouch): корзина, которую им открыли, прочитается сразу.
        if (!any || away || now - Math.max(touchedAt, peekLive) > PEEK_IDLE_MS
                || !pageOurs || !pageUrl.contains("/phone/basket")) return;
        peekSoon(PEEK_TICK_MS);
        final boolean byTouch = peekByTouch;
        peekByTouch = false;
        if (chainPanel != null && chainPanel.getVisibility() == View.VISIBLE) return;
        for (String[] p : PEEKS) {
            Slot s = slots.get(p[0]);
            if (s == null) continue;
            boolean touched = byTouch || s.peekOwed;
            s.peekOwed = false;
            if (touched || !p[2].isEmpty()) peekAt(s, p[1], touched, now);
        }
    }

    /** Прочитать корзину сети заранее, если её память (memoKey в cartapi.js) устарела; byTouch — по касанию. */
    private void peekAt(final Slot s, String memoKey, final boolean byTouch, long now) {
        if (now - s.peekAskedAt < 3000 || now - s.peekStartedAt < PEEK_AGE_MS || s.run != null || s.soon != null) return;
        // Сеть недавно не пускала (стена, отказ узла корзин): сборки её обходят, чтение заранее — тоже.
        JSONObject done = doneOf(s.code);
        long clock = System.currentTimeMillis();
        if (wallUp(s.code)
                || (done != null && done.optBoolean("wall") && clock - done.optLong("at", 0) < RETRY_MS)) return;
        if (s.view == null) {
            s.peekOwed = byTouch;       // касание помним до повтора: сеть без расписания иначе не прочтётся
            peekSoon(3000);     // окно сети ещё открывается (запуск приложения)
            return;
        }
        // Кука Пятёрочки старая: узел её не примет, а первый отказ запирает его на минуту — лечим до чтения (1.57).
        if (aheadDue(s)) {
            s.peekAskedAt = now;
            aheadHeal(s, "чтение заранее");
            return;
        }
        final JSONObject input = new JSONObject();
        try {
            // Темп — из наряда сети, как у сборки: ведро темпа у них общее. Наряда нет — правка пойдёт
            // через сервер, и секунду чтения там не выиграть.
            JSONObject rules = new JSONObject(memory().getString("plan." + s.code, "{}")).optJSONObject("rules");
            // Запросы сети сервер мог выключить (rules.api): тогда и заранее не читаем.
            if (rules == null || !rules.optBoolean("api", false)) return;
            input.put("key", "peek").put("chain", s.code).put("peek", true)
                 .put("lines", new JSONArray()).put("mine", new JSONObject()).put("want", new JSONObject())
                 .put("drop", false)
                 .put("pause_ms", Math.max(300, rules.optInt("request_pause_ms", 1000)))
                 .put("burst", rules.optInt("api_burst", 3));
        } catch (Exception e) {
            return;
        }
        s.peekAskedAt = now;
        final long age = byTouch ? PEEK_TOUCH_AGE_MS : PEEK_AGE_MS;
        // Только отдыхающее окно, без чтения в полёте и с памятью старше age или возвращения человека.
        // «0» — окно ещё не на отдыхе (открывается, греет куки на странице сети): заглянем через 3 с.
        s.view.evaluateJavascript("(function(r,key,since,age){"
                + "if(window.__kzPeek)return '';"
                // Прошлое чтение заранее не вышло (метка cartapi.js): сеть, которая не пускает, не дёргаем.
                + "if(window.__kzPeekFailAt&&Math.abs(Date.now()-window.__kzPeekFailAt)<" + API_WALL_MS + ")return '';"
                + "if(location.href!==r||window.__kzStale||document.readyState!=='complete')return '0';"
                + "try{var m=JSON.parse(localStorage[key]||'null');"
                + "if(m&&m.at>=since&&Math.abs(Date.now()-m.at)<age)return '';}catch(e){}"
                + "return '1';})(" + JSONObject.quote(cartApiOrigin(s.code) + "/robots.txt") + ","
                + JSONObject.quote(memoKey) + "," + Math.max(homeSince, s.movedAt) + "," + age + ")", val -> {
            String state = unquote(val);
            if ("0".equals(state) && !away) {
                s.peekOwed = byTouch;
                peekSoon(3000);
            }
            if (!"1".equals(state) || away || s.view == null || s.run != null || s.soon != null) return;
            String script = cartApiScript();
            if (script.isEmpty()) return;
            s.peekStartedAt = SystemClock.uptimeMillis();
            Log.i(TAG, CHAINS[s.chain][0] + ": читаю корзину заранее");
            s.view.evaluateJavascript(script + ";" + CARTAPI_START.replace("%s", input.toString()), null);
            if ("pyaterochka".equals(s.code)) handler.postDelayed(() -> peekCheck(s, 0), 1500);
        });
    }

    /**
     * Чтение Пятёрочки заранее не вышло быстрым «обрывом» при живой связи (fast в cartapi.js): это защита, а не
     * связь, она не принимает куку (healPyat). Лечим сразу, пока человек не успел правку: иначе первая правка
     * после запуска платила за это сама (1.50). Не чаще HEAL_GAP_MS. Узлу до конца HEAL_QUIET_MS не пишем: правка,
     * пришедшая за это время, ждёт паузу (healing), а не идёт касаниями по стене.
     * Результата ещё нет — чтение идёт (темп, холодное соединение): смотрим снова, до 8,5 с от его начала
     * (быстрый отказ — до 8 с).
     */
    private void peekCheck(final Slot s, final int tries) {
        if (s.view == null || s.run != null || s.soon != null || away) return;
        s.view.evaluateJavascript("(function(){var a=window.__kzApi&&window.__kzApi.peek;"
                + "return !a?'?':(a.ok===false&&a.stage==='net'&&a.fast===true?'1':'');})()", val -> {
            String state = unquote(val);
            if ("?".equals(state)) {
                if (tries < 14) handler.postDelayed(() -> peekCheck(s, tries + 1), 500);
                return;
            }
            Log.i(TAG, CHAINS[s.chain][0] + ": чтение заранее — " + ("1".equals(state) ? "быстрый отказ узла" : "без быстрого отказа")
                    + "; " + cookieNote(cartApiOrigin(s.code), PYAT_COOKIE) + "; кука освежена "
                    + agoSince(memory().getLong("warm." + s.code, 0)));
            if (!"1".equals(state) || s.view == null || s.run != null || s.soon != null || away) return;
            final long t0 = SystemClock.uptimeMillis();
            if (!healAllowed(s)) {
                // Узел отказывает, а лечить уже нельзя (лечили недавно или защита закрыла адрес): сборки и чтение заранее
                // его не дёргают, правка сразу идёт касаниями, а не после ещё одного отказа.
                notePeek(s, "лечили недавно, не лечу");
                memory().edit().putLong("apiwall." + s.code, System.currentTimeMillis()).apply();
                return;
            }
            final String origin = cartApiOrigin(s.code);
            dropCookie(origin, PYAT_COOKIE);
            if (hasCookie(origin, PYAT_COOKIE)) {
                // Стереть не вышло (кука другого пути или домена): новой главная не выдаст, а whenHealed принял бы старую
                // за выданную, и правка зря ждала бы паузу. Как при !healAllowed: стена, правка сразу касаниями.
                Log.i(TAG, CHAINS[s.chain][0] + ": чтение заранее не вышло, " + PYAT_COOKIE + " не стёрлась");
                notePeek(s, PYAT_COOKIE + " не стёрлась");
                memory().edit().putLong("apiwall." + s.code, System.currentTimeMillis()).apply();
                return;
            }
            // Узел молчит и перед новой кукой: до конца паузы ни сборок (cartApi), ни чтения заранее (peekAt).
            memory().edit().putLong("apiwall." + s.code, System.currentTimeMillis() - API_WALL_MS + HEAL_QUIET_MS).apply();
            Log.i(TAG, CHAINS[s.chain][0] + ": чтение заранее не вышло, " + PYAT_COOKIE + " стёрта, открываю главную");
            s.view.loadUrl(origin + "/");
            whenHealed(s, origin, miss -> {
                Log.i(TAG, CHAINS[s.chain][0] + ": " + (miss.isEmpty()
                        ? PYAT_COOKIE + " и главная готовы за " + (SystemClock.uptimeMillis() - t0) + " мс"
                        : PYAT_COOKIE + " не вылечена: " + miss));
                notePeek(s, PYAT_COOKIE + (miss.isEmpty() ? " стёрта, главная готова за " + (SystemClock.uptimeMillis() - t0) + " мс"
                        : " стёрта, не вылечена: " + miss));
                // Окно — на robots.txt и тогда, когда правка уже ждёт в begin: запросы на главной пропали бы вместе со
                // страницей, если защита её перезагрузит. Свёрнутое приложение страниц не грузит (вернётся — warm).
                if (!away && s.view != null) s.view.loadUrl(origin + "/robots.txt");
                holdQuiet(s, origin, t0, miss);
                // Главная только что открыта и выдала куку: подогрев (rest, warm) до срока не нужен — и не остался бы без
                // своих таймеров, пока окно держит пауза узла.
                if (hasCookie(origin, PYAT_COOKIE)) {
                    memory().edit().putLong("warm." + s.code, System.currentTimeMillis()).apply();
                }
                s.peekAskedAt = 0;
                s.peekStartedAt = 0;
                peekSoon(1500);     // читаем заново: корзина в памяти к первой правке
            });
        });
    }

    /**
     * Узел корзин Пятёрочки ответил «обрывом» (1.50). Это мгновенный отказ её защиты (ServicePipe): куку
     * spsc он перестаёт принимать, когда она протухла, а страница сети протухшую не заменяет — «обновляю вход»
     * главной страницей не помогало, и сеть уходила на три минуты в касания (телефон владельца, 13.11.2026).
     * Стёртую куку страница выдаёт заново, но узел принимает её лишь после того, как страница дошла до конца
     * (стенд: 8–10 с). Стираем куку в хранилище WebView (видны и HttpOnly), открываем главную, ждём новую куку
     * и готовую страницу (whenHealed) и повторяем запрос с robots.txt. Что вышло — в причину отказа (healNote):
     * не помогло, так журнал скажет, почему.
     */
    private void healPyat(final CartRun r, final String origin, final Runnable then) {
        final boolean had = hasCookie(origin, PYAT_COOKIE);
        dropCookie(origin, PYAT_COOKIE);
        final boolean gone = !hasCookie(origin, PYAT_COOKIE);
        final String was = "; " + PYAT_COOKIE + (had ? " была, " : " не было, ");
        final long t0 = SystemClock.uptimeMillis();
        Log.i(TAG, r.name + ": отказ узла корзин, стираю " + PYAT_COOKIE + " и открываю главную");
        // Узел молчит и перед новой кукой. Стена держит до конца паузы другие сборки (эту закрыли или свернули —
        // следующую) и чтение заранее; повтор этой сборки (then) стену не читает. Лечить снова — не раньше HEAL_GAP_MS.
        memory().edit().putLong("apiwall." + r.code, System.currentTimeMillis() - API_WALL_MS + HEAL_QUIET_MS).apply();
        r.slot.healAt = t0;
        beat(r, true);      // строка сборки живёт пульсом, а тишина до повтора — около минуты
        go(r, origin + "/");
        if (!gone) {        // стереть не вышло: прежний путь, главная и 4 с
            r.healNote = was + "не стёрлась";
            whenReady(r, "/", null, 4000, then);
            return;
        }
        whenHealed(r.slot, origin, miss -> {
            r.healNote = was + (miss.isEmpty() ? "стёрта, главная готова за " + (SystemClock.uptimeMillis() - t0) + " мс"
                                               : "стёрта, не вылечена: " + miss);
            Log.i(TAG, r.name + ": лечение" + r.healNote);
            // Пауза узла держится окном и когда сборку закрыли посреди лечения (новая правка): следующая сборка ждёт её
            // в begin, а не упирается в стену и не уходит касаниями.
            holdQuiet(r.slot, origin, t0, miss);
            if (!alive(r)) {        // сборку закрыли посреди лечения: окно на отдых, не оставлять на главной
                if (r.slot.run == null) rest(r.slot);
                return;
            }
            beat(r, true);
            // Запуск запросов на главной пропал бы вместе со страницей, когда защита её перезагрузит
            // (стенд, 13.11.2026: «сеть не ответила за 45 с», правка 52 с): назад на robots.txt, где окно отдыхает.
            // Повтор — не раньше HEAL_QUIET_MS после отказа: узел до тех пор отказывает и новой куке (стенд: отказы
            // до 62-й секунды). Новой куки нет (защита её не выдала) — паузу ждать незачем: повтор получит отказ, и
            // сеть пойдёт касаниями. Адрес закрыт защитой (HEAL_BLOCKED) — повторять запрос некуда: сразу отказ (apiGaveUp).
            go(r, origin + "/robots.txt");
            if (miss.startsWith(HEAL_BLOCKED)) {
                apiGaveUp(r, "узел корзин закрыт защитой сети" + r.healNote);
                uploadTrace(r.code, r.name + ": адрес закрыт защитой" + r.healNote);    // отчёта не будет, а хроника нужна
                return;
            }
            long wait = hasCookie(origin, PYAT_COOKIE)
                    ? Math.max(0, t0 + HEAL_QUIET_MS - SystemClock.uptimeMillis()) : 0;
            whenReady(r, null, null, (int) wait, then);
        });
    }

    /** Есть ли у origin кука name — по хранилищу WebView, а не по странице: видны и HttpOnly (1.50). */
    private static boolean hasCookie(String origin, String name) {
        String all = CookieManager.getInstance().getCookie(origin + "/");
        return all != null && ("; " + all).contains("; " + name + "=");
    }

    /**
     * Кука защиты для хроники (1.54): сколько в ней знаков, её очертание (цифра — d, буква — x, прочее как есть, не
     * больше 40 знаков) и возраст по метке времени в значении, если там такая есть. Значение никуда не пишется.
     * Зачем: сколько живёт кука на стороне узла, неизвестно, а от этого зависит, как часто её освежать.
     */
    private static String cookieNote(String origin, String name) {
        try {
            return cookieShape(origin, name);
        } catch (RuntimeException e) {
            return name + " не прочиталась: " + e;      // хроника не должна ронять сборку
        }
    }

    private static String cookieShape(String origin, String name) {
        String value = cookieValue(origin, name);
        if (value == null) return name + " нет";
        StringBuilder shape = new StringBuilder();
        for (int i = 0; i < value.length() && shape.length() < 40; i++) {
            char ch = value.charAt(i);
            shape.append(Character.isDigit(ch) ? 'd' : Character.isLetter(ch) ? 'x' : ch);
        }
        long mark = cookieMarkMs(value);
        String age = mark < 0 ? "" : ", метка " + (System.currentTimeMillis() - mark) / 1000 + " с назад";
        return name + " длина " + value.length() + " вид " + shape + age;
    }

    /** Значение куки name у origin по хранилищу WebView (видны и HttpOnly); нет куки — null. */
    private static String cookieValue(String origin, String name) {
        String all = origin == null ? null : CookieManager.getInstance().getCookie(origin + "/");
        if (all == null) return null;
        for (String part : all.split(";\\s*")) {
            if (part.startsWith(name + "=")) return part.substring(name.length() + 1);
        }
        return null;
    }

    /** Метка времени в значении куки (13 цифр — мс, 10 — секунды) в мс; её нет или она не похожа на время — -1. */
    private static long cookieMarkMs(String value) {
        Matcher m = Pattern.compile("\\d{10,13}").matcher(value);
        if (!m.find()) return -1;
        try {
            long mark = Long.parseLong(m.group());
            long ms = mark >= 100000000000L ? mark : mark * 1000;
            long gone = System.currentTimeMillis() - ms;
            return gone > -86400000L && gone < 90L * 86400000L ? ms : -1;
        } catch (NumberFormatException e) {
            return -1;
        }
    }

    /**
     * Сколько мс кука защиты Пятёрочки не освежалась (1.57): по метке времени в её значении, а нет метки — по последнему
     * удачному запросу или подогреву. Куки нет вовсе — Long.MAX_VALUE: узел без неё отказывает.
     */
    private long pyatCookieAgeMs(String origin, String code) {
        String value = cookieValue(origin, PYAT_COOKIE);
        if (value == null) return Long.MAX_VALUE;
        long now = System.currentTimeMillis();
        long mark = cookieMarkMs(value);
        if (mark >= 0) return Math.max(0, now - mark);
        long warm = memory().getLong("warm." + code, 0);
        return warm <= 0 ? Long.MAX_VALUE : Math.max(0, now - warm);
    }

    /**
     * Пора лечить куку Пятёрочки до запроса (1.57): она старше PYAT_STALE_MS, узел ей скоро не поверит, а первый отказ
     * запирает его на минуту. Не нужно, когда лечение уже идёт, узел недавно отказал (стена: пауза после отказа, её лечит
     * прежний путь) или защита закрыла адрес; и не чаще AHEAD_GAP_MS и AHEAD_PER_HOUR в час.
     */
    private boolean aheadDue(Slot s) {
        if (s == null || s.view == null || away || !"pyaterochka".equals(s.code)) return false;
        String origin = cartApiOrigin(s.code);
        if (origin == null || blocked(s.code) || wallUp(s.code) || healing(s)) return false;
        if (pyatCookieAgeMs(origin, s.code) <= PYAT_STALE_MS) return false;
        long now = SystemClock.uptimeMillis();
        if (s.aheadAt != 0 && now - s.aheadAt < AHEAD_GAP_MS) return false;
        int lastHour = 0;
        for (long t : s.aheadLog) if (t != 0 && now - t < 3600000L) lastHour++;
        return lastHour < s.aheadLog.length;
    }

    /**
     * Вылечить куку Пятёрочки до первого запроса (1.57): стереть, открыть главную, дождаться новой куки и конца запроса
     * защиты (whenHealed), вернуть окно на robots.txt и прочитать корзину заново. В отличие от лечения после отказа здесь
     * отказа не было, и паузы узла ждать не надо: запрос идёт сразу за лечением (begin ждёт его, пока healing). Лечение
     * после отказа это не заменяет и не отменяет: метку healAt не трогаем.
     */
    private void aheadHeal(final Slot s, final String why) {
        final String origin = cartApiOrigin(s.code);
        final String name = CHAINS[s.chain][0];
        final long t0 = SystemClock.uptimeMillis();
        s.aheadAt = t0;
        s.aheadLog[s.aheadN++ % s.aheadLog.length] = t0;
        if (s.keepAlive != null) {      // подогрев на очереди увёл бы окно на robots.txt посреди лечения
            handler.removeCallbacks(s.keepAlive);
            s.keepAlive = null;
        }
        Log.i(TAG, name + ": кука старая, лечу до запроса (" + why + "): " + cookieNote(origin, PYAT_COOKIE)
                + "; кука освежена " + agoSince(memory().getLong("warm." + s.code, 0)));
        dropCookie(origin, PYAT_COOKIE);
        if (hasCookie(origin, PYAT_COOKIE)) {
            Log.i(TAG, name + ": до запроса не вышло, " + PYAT_COOKIE + " не стёрлась");
            notePeek(s, "до запроса: " + PYAT_COOKIE + " не стёрлась");
            return;
        }
        final long healAt = s.healAt;
        s.view.loadUrl(origin + "/");
        whenHealed(s, origin, miss -> {
            s.healAt = healAt;
            String note = miss.isEmpty() ? PYAT_COOKIE + " вылечена до запроса за " + (SystemClock.uptimeMillis() - t0) + " мс"
                                         : PYAT_COOKIE + " до запроса не вылечена: " + miss;
            Log.i(TAG, name + ": " + note + "; " + cookieNote(origin, PYAT_COOKIE));
            notePeek(s, note);
            if (!away && s.view != null) s.view.loadUrl(origin + "/robots.txt");
            if (hasCookie(origin, PYAT_COOKIE)) memory().edit().putLong("warm." + s.code, System.currentTimeMillis()).apply();
            s.peekAskedAt = 0;
            s.peekStartedAt = 0;
            peekSoon(1000);
        });
    }

    /** Сколько прошло от метки в часах телефона, для хроники (1.54). */
    private static String agoSince(long wallMs) {
        return wallMs <= 0 ? "ни разу" : (System.currentTimeMillis() - wallMs) / 1000 + " с назад";
    }

    /**
     * Стереть куку name у origin: без атрибута Domain (кука только этого хоста) и с ним — на хосте и на каждом его
     * суффиксе в две и больше метки. Ключ куки — имя, домен и путь, а настоящая spsc живёт на .5ka.ru, не на узле
     * 5d.5ka.ru: «с доменом хоста» её не взять (1.52). Видны и HttpOnly (CookieManager).
     */
    private static void dropCookie(String origin, String name) {
        CookieManager cm = CookieManager.getInstance();
        cm.setCookie(origin + "/", name + "=; Max-Age=0; Path=/");
        for (String d = Uri.parse(origin).getHost(); d != null && d.indexOf('.') > 0; d = d.substring(d.indexOf('.') + 1)) {
            cm.setCookie(origin + "/", name + "=; Max-Age=0; Path=/; Domain=" + d + "; Secure");
        }
        cm.flush();
    }

    /**
     * Главная страница окна готова: догрузилась, не уходящая, с каркасом сайта (не страница проверки защиты и
     * не robots.txt), её запрос защиты gatekeeper/evaluate завершился (запись в Resource Timing появляется после
     * ответа) и она простояла HEAL_STABLE_MS; не видно запроса — HEAL_LATE_MS. Перезагрузка после проверки
     * обнуляет метку страницы (1.50). Не готова — коротко, что вместо неё: путь, readyState, «брошена» (go),
     * «без каркаса», «ждёт защиту». Страница «Проблемы со связью» — HEAL_BLOCK_PAGE: защита закрыла адрес (стенд,
     * 14.11.2026: без корня сайта #__next, с заголовком h1, поэтому через HEAL_LATE_MS она сошла бы за готовую). 15.11.2026
     * тот же блок пришёл другим видом: путь /xpvnsulc/, без h1, текст «дата Ваш IP … ID запроса …»; 1.57 его не узнала,
     * лечила и стучалась дальше (1.58: путь /xpvnsulc/ и «Ваш IP» без ограничения длины текста). Путь без текста
     * блоком сразу не считается: у Самоката /xpvnsulc/ — и проходящая проверка (20.10.2026); застрявшую на нём до конца
     * лечения страницу блоком называет whenHealed (1.61). Свой
     * экран ошибки сайта корень #__next имеет и блокировкой не считается; пробелы в тексте — любые, и неразрывные.
     */
    private static final String HEALED_JS =
        "(function(){var t=document.body?document.body.innerText:'';"
        + "if(!document.getElementById('__next')&&((t.length<1500&&/Проблемы\\s+со\\s+связью/.test(t))"
        + "||(location.pathname.indexOf('/xpvnsulc/')===0&&/Ваш\\s+IP/.test(t))))return '"
        + HEAL_BLOCK_PAGE + "';"
        + "var m=!!document.querySelector('#__next,main,h1,header');"
        + "if(document.readyState!=='complete'||window.__kzStale||location.pathname==='/robots.txt'||!m)"
        + "return location.pathname.slice(0,12)+' '+document.readyState+(window.__kzStale?' брошена':'')+(m?'':' без каркаса');"
        + "if(!window.__kzHealSeen)window.__kzHealSeen=Date.now();"
        + "var age=Date.now()-window.__kzHealSeen,g=false;"
        + "try{g=performance.getEntriesByType('resource').some(function(e){"
        + "return e.name.indexOf('/gatekeeper/')>=0&&e.responseEnd>0;});}catch(e){}"
        + "return ((g&&age>=" + HEAL_STABLE_MS + ")||age>=" + HEAL_LATE_MS + ")?'1':'ждёт защиту';})()";

    /** Лечить куку Пятёрочки можно не чаще HEAL_GAP_MS: лечили недавно, и не помогло — причина не в куке (1.50). */
    private static boolean healAllowed(Slot s) {
        return s.healAt == 0 || SystemClock.uptimeMillis() - s.healAt >= HEAL_GAP_MS;
    }

    /** Чем кончилось лечение куки при чтении заранее (1.52): до правки оно видно только в logcat телефона. */
    private static void notePeek(Slot s, String note) {
        s.peekNote = note;
        s.peekNoteAt = SystemClock.uptimeMillis();
    }

    /** Запись о лечении заранее — в причину отказа сборки, чтобы журнал отказов на сервере сказал, что делало окно. */
    private static String peekNote(Slot s) {
        return s == null || s.peekNote.isEmpty() ? ""
                : "; заранее, " + (SystemClock.uptimeMillis() - s.peekNoteAt) / 1000 + " с назад: " + s.peekNote;
    }

    /**
     * К узлу корзин сети не ходим: недавно отказал (стена apiwall.<код> держит API_WALL_MS, метку ставят отказ и лечение)
     * или защита закрыла адрес (blocked). Метка из «будущего» — часы телефона ушли назад — стену не держит и стирается:
     * иначе она ожила бы, когда часы дойдут до неё (1.50).
     */
    private boolean wallUp(String code) {
        long d = System.currentTimeMillis() - memory().getLong("apiwall." + code, 0);
        if (d < 0) memory().edit().remove("apiwall." + code).apply();
        return blocked(code) || (d >= 0 && d < API_WALL_MS);
    }

    /**
     * Защита закрыла адрес сети страницей «Проблемы со связью» (whenHealed): до метки apiblock.<код> узел корзин не
     * трогаем. Метка дальше HEAL_BLOCK_MS от «сейчас» (часы телефона ушли назад) блок не держит и стирается, как стена.
     */
    private boolean blocked(String code) {
        long until = memory().getLong("apiblock." + code, 0), now = System.currentTimeMillis();
        if (until - now > HEAL_BLOCK_MS) memory().edit().remove("apiblock." + code).apply();
        return until > now && until - now <= HEAL_BLOCK_MS;
    }

    /**
     * Кука выдана, а узел отказывает и ей до конца паузы (стенд: отказы до 62-й секунды): окно держим до неё, и правка,
     * пришедшая раньше, ждёт в begin и уходит запросом, а не касаниями по стене (1.50). +1 с: стена пишется в часах
     * телефона и кончается на миллисекунды позже t0 + HEAL_QUIET_MS. Закрытый защитой адрес паузой не лечится.
     */
    private static void holdQuiet(Slot s, String origin, long t0, String miss) {
        if (hasCookie(origin, PYAT_COOKIE) && !miss.startsWith(HEAL_BLOCKED)) {
            s.healUntil = Math.max(s.healUntil, t0 + HEAL_QUIET_MS + 1000);
        }
    }

    /** Идёт лечение куки защиты на главной странице окна: сборки его не уводят (whenHealed, 1.50). */
    private static boolean healing(Slot s) {
        return SystemClock.uptimeMillis() < s.healUntil;
    }

    /**
     * Ждёт после стёртой куки, пока у origin появится новая И главная страница окна дойдёт до конца вместе с
     * запросом защиты (HEALED_JS), но не дольше HEAL_WAIT_MS (1.50). done получает "" — готово, или то, чего не хватило,
     * и ровно один раз: ответом страницы или сторожем (ответ скрипта мог потеряться, когда защита перезагрузила страницу).
     * Окно на это время занято: сборки и возврат на robots.txt ждут (healing). Окно потеряли — лечить нечем.
     * Страница-блокировка защиты (HEALED_JS) кончает ожидание сразу: к узлу корзин не ходим HEAL_BLOCK_MS (blocked).
     */
    private void whenHealed(final Slot s, final String origin, final ValueCallback<String> done) {
        final long t0 = SystemClock.uptimeMillis();
        final long until = t0 + HEAL_WAIT_MS;
        final WebView v = s.view;
        final boolean[] over = {false};
        final String[] last = {""};     // чем страница отвечала в последний раз: сторожу нечем иначе объяснить молчание
        s.healAt = t0;
        s.healUntil = until + 1000;
        final ValueCallback<String> end = miss -> {
            if (over[0]) return;
            over[0] = true;
            s.healUntil = 0;
            done.onReceiveValue(miss);
        };
        // Страница проверки защиты не отпустила до конца ожидания — это блок (1.61): проходящая проверка уводит назад за
        // секунды (Самокат, 20.10.2026), а эмулятор 15.11.2026 простоял на /xpvnsulc/ всё лечение «loading без каркаса».
        // Сторож срабатывает и после конца лечения: метку блока пишем, только пока оно не кончилось.
        final ValueCallback<String> late = miss -> {
            if (!over[0]) end.onReceiveValue(last[0].startsWith("/xpvnsulc/")
                    ? healBlock(s, "проверка не отпустила за " + HEAL_WAIT_MS / 1000 + " с") : miss);
        };
        handler.postDelayed(() -> late.onReceiveValue(last[0].isEmpty() ? "страница не ответила" : "страница: " + last[0]),
                HEAL_WAIT_MS + 900);
        handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                if (over[0]) return;
                if (v == null || s.view != v) {
                    end.onReceiveValue("окно закрыто");
                    return;
                }
                // Следующий опрос — по таймеру, до ответа: ответ скрипта терялся, когда защита перезагружала страницу
                // (стенд, 14.11.2026: опрос кончался сторожем при куке, выданной за 6 с), и опрос вставал.
                handler.postDelayed(this, 500);
                v.evaluateJavascript(HEALED_JS, val -> {
                    if (over[0]) return;
                    String page = unquote(val);
                    last[0] = page;
                    if (HEAL_BLOCK_PAGE.equals(page)) {
                        end.onReceiveValue(healBlock(s, "«Проблемы со связью»"));
                        return;
                    }
                    boolean cookie = hasCookie(origin, PYAT_COOKIE);
                    boolean shown = "1".equals(page);
                    boolean ready = cookie && shown;
                    if (ready || SystemClock.uptimeMillis() >= until) {
                        late.onReceiveValue(ready ? "" : (cookie ? "" : "куки нет")
                                + (cookie || shown ? "" : ", ") + (shown ? "" : "страница: " + page));
                    }
                });
            }
        }, 500);
    }

    /**
     * Защита закрыла адрес: ни стёртая кука, ни пауза не помогут, а каждое лечение и каждый запрос копят отказы. К узлу
     * корзин не ходим HEAL_BLOCK_MS (часы телефона, wallUp); ответ — причина конца лечения для whenHealed.
     */
    private String healBlock(Slot s, String what) {
        memory().edit().putLong("apiblock." + s.code, System.currentTimeMillis() + HEAL_BLOCK_MS).apply();
        return HEAL_BLOCKED + " (" + what + "): к узлу корзин не хожу " + HEAL_BLOCK_MS / 60000 + " мин";
    }

    /** Запустить сборку в сети через DEBOUNCE_MS: каждая новая правка корзины отодвигает запуск. */
    private void soon(final Slot s) {
        if (s.soon != null) handler.removeCallbacks(s.soon);
        if (s.keepAlive != null) {
            handler.removeCallbacks(s.keepAlive);
            s.keepAlive = null;
        }
        s.soon = () -> startBackground(s);
        handler.postDelayed(s.soon, DEBOUNCE_MS);
        touchBusy();
    }

    private void startBackground(final Slot s) {
        s.soon = null;
        touchBusy();
        if (s.run != null || away || lastCollect == null || s.pending.isEmpty()) return;
        if (cartRun != null && cartRun.chain == s.chain) return;     // человек собирает сам
        // Окна делят один процесс страниц: витрина или проверка Qrator в окне с касаниями
        // растягивала запросы Дикси и Пятёрочки до 10–13 с (замер 09.11.2026). Сеть без запросов ждёт их уже
        // с нарядом (heldForApi, 1.59): Впрок без знакомых товаров ждал 5,45 с ради «не пошла».
        s.heldSince = 0;
        if (s.sleep != null) {
            handler.removeCallbacks(s.sleep);
            s.sleep = null;
        }
        // shortcut: окно сети без запросов создаётся сразу, и пустую сборку rest() паркует на robots.txt, пока другие
        // сети ждут ответа; задержит их замер холодного старта — создавать окно с нарядом (CartRun.view не final).
        if (s.view == null) s.view = hiddenView(s.chain);
        final CartRun r = new CartRun(s.chain, s.view, s);
        r.basket = lastCollect.optInt("basket", -1);
        r.stamp = s.pending;
        r.deadline = collectDeadline;
        r.changedAt = s.changedAt;
        r.changedPid = s.changedPid;
        s.changedAt = 0;
        s.changedPid = 0;
        s.pending = "";
        s.run = r;
        s.last = r;
        Log.i(TAG, r.name + ": сборка начата [" + (r.stamp.length() > 6 ? r.stamp.substring(0, 6) : r.stamp) + "]"
                + (r.changedAt > 0 ? ", от касания " + (SystemClock.uptimeMillis() - r.changedAt) + " мс" : ""));
        watchTrace(r);
        watchRun(r);
        // «Собрано» про другую корзину с началом новой сборки — уже неправда (1.45): оборвись она, и корзина,
        // вернувшаяся к прежнему отпечатку, считалась бы собранной до FRESH_MS с тем, что успело лечь.
        // Стену (wall) не трогаем: её срок считает decide.
        JSONObject was = doneOf(s.code);
        if (was != null && !was.optBoolean("wall") && !r.stamp.equals(was.optString("stamp"))) {
            memory().edit().remove("done." + s.code).apply();
        }

        // «Заказать» (1.19): если отпечаток равен order.stamp, собираем только долю или снимаем своё
        JSONObject order = getOrderCurrent();
        if (order != null && r.stamp.equals(order.optString("stamp", ""))) {
            JSONObject orderStores = order.optJSONObject("stores");
            if (orderStores != null && orderStores.has(s.code)) {
                r.linesOverride = orderStores.optJSONArray(s.code);
            } else {
                r.linesOverride = new JSONArray(); // не из stores — только снятие своего
            }
            r.orderAt = order.optLong("at", 0);
            r.orderStamp = r.stamp;
            r.deadline = 0;
        }

        if (r.deadline > 0) {
            handler.postAtTime(() -> {
                if (alive(r)) collectionBudgetExpired(r);
            }, r.deadline);
        }
        if (collectionBudgetExpired(r)) return;
        touchBusy();
        // Наряд этой корзины уже в памяти телефона — выведен из прежнего по касанию (1.27):
        // сервер на пути не стоит.
        JSONObject fast = r.linesOverride == null && cartApiOrigin(s.code) != null
                ? keptPlan(s.code, r.stamp, r.basket) : null;
        if (fast != null) {
            r.fast = true;
            loadLedger(r);
            try {
                applyPlan(r, fast);
            } catch (Exception e) {
                failRun(r, "наряд из памяти телефона не разобрался: " + e.getMessage());
                return;
            }
            r.planAt = SystemClock.uptimeMillis();
            r.now = "наряд из памяти телефона";
            beat(r, true);
            Log.i(TAG, r.name + ": наряд из памяти телефона, позиций " + r.lines.length());
            begin(r);
            return;
        }
        r.now = "беру наряд у сервера";
        beat(r, true);
        askPlan(r, () -> begin(r));
    }

    /**
     * Правка по касанию (1.27): сетям с запросами — наряд, выведенный из прежнего, и сборку сразу;
     * устаревшие касания любой сети — долой, чтобы не держали общий процесс окон.
     * Где наряд наверняка не вывести, ждём collect с ответом сервера, как до 1.27.
     */
    private void fastChange(JSONObject c) {
        final int basket = c.optInt("basket", -1);
        final String stamp = c.optString("stamp", "");
        if (lastCollect == null || basket < 0 || basket != lastCollect.optInt("basket", -2) || stamp.isEmpty()) return;
        JSONObject chains = new JSONObject();
        JSONArray list = lastCollect.optJSONArray("chains");
        for (int i = 0; list != null && i < list.length(); i++) {
            JSONObject ch = list.optJSONObject(i);
            if (ch != null) {
                try {
                    chains.put(ch.optString("code", ""), ch);
                } catch (Exception ignored) { }
            }
        }
        boolean order = getOrderCurrent() != null;
        // Время правки — от пальца, а не от обработчика страницы (1.37): при занятом потоке окон обработчик
        // опаздывал на 1,1 с, и отчёт называл 4,3–5,0 с там, где от касания прошло 5,6–6,2 с (12.11.2026).
        // Касание старше 10 с правку не начинало: тогда время идёт отсюда, как раньше.
        long at = SystemClock.uptimeMillis();
        if (at - touchedAt < 10000) at = touchedAt;
        for (Slot s : slots.values()) {
            s.changedAt = at;
            s.changedPid = c.optInt("pid", 0);
            JSONObject ch = chains.optJSONObject(s.code);
            JSONObject done = doneOf(s.code);
            boolean wall = done != null && done.optBoolean("wall")
                    && System.currentTimeMillis() - done.optLong("at", 0) < RETRY_MS;
            // Почему наряд не выведен — в хронику (1.54): 14.11.2026 Пятёрочка пропустила пять нажатий молча.
            String skip = ch == null ? "сервер сети не назвал" : ch.optBoolean("sent") ? "корзину туда положил сервер"
                    : order ? "идёт заказ" : wall ? "сеть не пускала, стена на 20 мин" : away ? "приложение свёрнуто"
                    : cartApiOrigin(s.code) == null ? "у сети нет запросов"
                    : (cartRun != null && cartRun.chain == s.chain) ? "человек собирает сам" : "";
            planWhy = "";
            JSONObject plan = skip.isEmpty() ? changedPlan(s.code, c) : null;
            if (plan == null && skip.isEmpty()) skip = "наряд не вывести: " + planWhy;
            CartRun run = s.run;
            boolean stale = run != null && !stamp.equals(run.stamp) && !writing(run);
            Log.i(TAG, CHAINS[s.chain][0] + ": правка «" + c.optString("verb", "") + "» товара " + c.optInt("pid", 0) + " — "
                    + (plan != null ? "наряд выведен" : skip) + "; сборка "
                    + (run == null ? "не идёт" : stamp.equals(run.stamp) ? "этой корзины" : stale ? "старой корзины, сменю" : "старой корзины, пишет"));
            if (plan != null) {
                memory().edit().putString("plan." + s.code, plan.toString()).apply();
                s.pending = stamp;
                if (stale) supersede(run);       // rest() запустит новую через DEBOUNCE_MS
                else if (run == null) soon(s);   // идущую запись cartapi.js доберёт rest() после неё
                // Идущей записи говорим, что её догнали (1.46): итог прежней корзины адаптер не читает, и
                // запрос за ним не занимает место в темпе у записи следующей сборки (cartapi.js, newer).
                // Только Дикси: адаптеры остальных сетей флаг не читают.
                else if ("dixy".equals(s.code) && !stamp.equals(run.stamp) && !run.apiKey.isEmpty()
                        && run.view != null) {
                    run.view.evaluateJavascript("window.__kzNewer=" + JSONObject.quote(run.apiKey), null);
                }
            } else if (stale) {
                // Без наряда новую сборку начнёт collect с ответом сервера; эти касания уже лишние.
                Log.i(TAG, run.name + ": корзина поменялась — касания старой бросаю");
                stopBackground(s, true);
                // Прежнее «собрано» забываем: добавил товар и тут же убрал — отпечаток совпал бы с ним,
                // collect пропустил бы сборку, и сеть осталась бы со строкой «собираю» и с тем, что
                // успела положить оборванная (замер 11.11.2026: Пятёрочка и Дикси без отчёта).
                memory().edit().remove("done." + s.code).apply();
            }
        }
    }

    /** Наряд сети из памяти телефона, если он про эту корзину и не старше PLAN_MS; иначе null. */
    private JSONObject keptPlan(String code, String stamp, int basket) {
        try {
            SharedPreferences m = memory();
            if (System.currentTimeMillis() - m.getLong("planAt." + code, 0) > PLAN_MS) return null;
            JSONObject plan = new JSONObject(m.getString("plan." + code, "{}"));
            return stamp.equals(plan.optString("stamp", "-")) && basket == plan.optInt("basket", -2) ? plan : null;
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * Наряд сети после правки по касанию — из наряда, который сервер дал для корзины до неё:
     * позиция товара плюс или минус упаковка либо долой, сумма и отпечаток — новые.
     * null, если так не вывести наверняка: наряд старше PLAN_MS или от другой корзины, товар
     * не штучный (упаковок не столько, сколько штук), слит с дублем по артикулу или его нет в наряде.
     */
    private JSONObject changedPlan(String code, JSONObject c) {
        try {
            SharedPreferences m = memory();
            if (System.currentTimeMillis() - m.getLong("planAt." + code, 0) > PLAN_MS) {
                return noPlan(m.getLong("planAt." + code, 0) == 0 ? "наряда в памяти нет" : "наряд в памяти старше 6 ч");
            }
            JSONObject plan = new JSONObject(m.getString("plan." + code, "{}"));
            String verb = c.optString("verb", "");
            int pid = c.optInt("pid", 0);
            if (pid <= 0) return noPlan("нет номера товара");
            if (plan.optInt("basket", -1) != c.optInt("basket", -2)) return noPlan("наряд от другой корзины");
            if (!c.optString("from", "").equals(plan.optString("stamp", "-"))) {
                return noPlan("наряд " + plan.optString("stamp", "-").replaceAll("^(.{0,6}).*$", "$1") + ", а правка от "
                        + c.optString("from", "").replaceAll("^(.{0,6}).*$", "$1"));
            }
            if (!("plus".equals(verb) || "minus".equals(verb) || "remove".equals(verb))) return noPlan("правка «" + verb + "» не плюс и не минус");
            boolean seen = false, out = false;
            double total = plan.optDouble("total", 0);
            JSONArray lines = plan.optJSONArray("lines"), kept = new JSONArray();
            for (int i = 0; lines != null && i < lines.length(); i++) {
                JSONObject line = lines.getJSONObject(i);
                JSONArray ids = line.optJSONArray("product_ids");    // дубли на одном артикуле
                for (int k = 0; ids != null && k < ids.length(); k++) {
                    if (ids.optInt(k) == pid) return noPlan("товар слит с дублем на одном артикуле");
                }
                if (line.optInt("product_id", 0) != pid) {
                    kept.put(line);
                    continue;
                }
                int clicks = line.optInt("clicks", 0);
                if (seen || clicks <= 0 || line.optDouble("qty", 0) != clicks) {
                    return noPlan(seen ? "товар в наряде дважды" : "товар не штучный: упаковок " + clicks + ", штук " + line.optDouble("qty", 0));
                }
                seen = true;
                int now = "plus".equals(verb) ? clicks + 1 : "minus".equals(verb) ? clicks - 1 : 0;
                double price = line.optDouble("price", 0), fresh = Math.round(price / clicks * now * 100) / 100.0;
                total += fresh - price;
                if (now <= 0) {
                    out = true;
                    continue;
                }
                JSONObject next = new JSONObject(line.toString()).put("clicks", now).put("qty", now);
                if (!line.isNull("price")) next.put("price", fresh);
                kept.put(next);
            }
            // Незнакомые сети товары ищутся и кладутся по одной: «+» и «−» наряд не меняют, «убрать» — не искать.
            for (String key : new String[]{"search", "search_recent"}) {
                JSONArray was = plan.optJSONArray(key);
                if (was == null) continue;
                JSONArray left = new JSONArray();
                for (int i = 0; i < was.length(); i++) {
                    JSONObject s = was.getJSONObject(i);
                    if (s.optInt("product_id", 0) != pid) {
                        left.put(s);
                        continue;
                    }
                    // Ушёл ли товар после «−», говорит страница (gone, с 12.11.2026: ушёл — назовёт «убрать»).
                    // Страница старше этого не сообщает: тогда ждём сервер, как до 1.37. И товар с запасным
                    // (swapped) — к серверу: в наряде вместо него строка запасного под другим номером.
                    planWhy = "товар из поиска сети: " + (seen ? "ещё и в строках наряда" : s.optBoolean("swapped")
                            ? "с запасным товаром" : "«−», а страница не сказала, ушёл ли товар");
                    if (seen || s.optBoolean("swapped")
                            || ("minus".equals(verb) && c.optBoolean("gone", true))) return null;
                    seen = true;
                    if (!"remove".equals(verb)) left.put(s);
                }
                plan.put(key, left);
            }
            // Товар из rest наряда сеть сейчас не получит (не знает, искать нечем, сервер его уже спросил):
            // его правка наряд не меняет.
            planWhy = "товара нет в наряде этой сети";
            JSONArray rest = plan.optJSONArray("rest");
            for (int i = 0; !seen && rest != null && i < rest.length(); i++) seen = rest.optInt(i) == pid;
            if (!seen) return null;
            JSONArray inBasket = plan.optJSONArray("pids");
            if (inBasket != null && (out || "remove".equals(verb))) {
                // Товара в корзине больше нет: найденное для него поиском сети теперь можно снимать (1.43).
                JSONArray left = new JSONArray();
                for (int i = 0; i < inBasket.length(); i++) if (inBasket.optInt(i) != pid) left.put(inBasket.optInt(i));
                plan.put("pids", left);
            }
            return plan.put("lines", kept).put("total", Math.round(total * 100) / 100.0)
                       .put("stamp", c.optString("stamp", ""));
        } catch (Exception e) {
            return noPlan("ошибка разбора наряда: " + e);
        }
    }

    /** Почему changedPlan не вывел наряд (1.54): fastChange кладёт это в хронику. */
    private String planWhy = "";

    /** Что collect решил по сети в прошлый раз (1.54): хроника пишет решение, когда оно поменялось. */
    private final Map<String, String> lastVerdict = new HashMap<>();

    /** Хроника: сборка ждёт конца лечения куки (1.54). begin зовётся каждые 250 мс — пишем начало и конец ожидания. */
    private void noteHeal(CartRun r) {
        boolean waiting = healing(r.slot);
        if (waiting && !r.healLogged) {
            r.healLogged = true;
            Log.i(TAG, r.name + ": жду конца лечения куки, окно занято ещё "
                    + Math.max(0, r.slot.healUntil - SystemClock.uptimeMillis()) + " мс");
        } else if (!waiting && r.healLogged) {
            r.healLogged = false;
            Log.i(TAG, r.name + ": лечение куки кончилось, иду запросами");
        }
    }

    private JSONObject noPlan(String why) {
        planWhy = why;
        return null;
    }

    /**
     * Подготовить скрытое окно к повтору сохранённого образца (2.4):
     * адрес окна должен быть на том же origin, что и образец запроса.
     */
    private void ensureTpl(final CartRun r, final Runnable then) {
        final JSONObject stored = storedTpl(r.code);
        if (stored == null || !alive(r)) {
            r.learned = false;
            r.learnTried = false;
            r.reusedTpl = false;
            then.run();
            return;
        }
        final String expectedOrigin = stored.optString("origin", "");
        final JSONObject tplObj = stored.optJSONObject("tpl");
        final String tpl = tplObj != null ? tplObj.toString() : stored.optString("tpl", "");
        if (expectedOrigin.isEmpty() || tpl.isEmpty()) {
            r.learned = false;
            r.learnTried = false;
            r.reusedTpl = false;
            then.run();
            return;
        }
        r.view.evaluateJavascript("location.origin", currentVal -> {
            if (!alive(r)) return;
            String curOrigin = unquote(currentVal);
            if (expectedOrigin.equals(curOrigin)) {
                r.view.evaluateJavascript(watchScript(r), null);
                r.view.evaluateJavascript("window.__kzTpl=" + tpl + ";window.__kzRep=window.__kzRep||{};", ignored -> then.run());
                return;
            }
            go(r, expectedOrigin + "/robots.txt");
            whenReady(r, null, null, 0, () -> {
                if (!alive(r)) return;
                r.view.evaluateJavascript("location.origin", afterVal -> {
                    if (!alive(r)) return;
                    String afterOrigin = unquote(afterVal);
                    if (expectedOrigin.equals(afterOrigin)) {
                        r.view.evaluateJavascript(watchScript(r), null);
                        r.view.evaluateJavascript("window.__kzTpl=" + tpl + ";window.__kzRep=window.__kzRep||{};", ignored -> then.run());
                    } else {
                        // Не совпал — в этой сборке образец не переиспользовать (dropTpl не звать)
                        r.learned = false;
                        r.learnTried = false;
                        r.reusedTpl = false;
                        then.run();
                    }
                });
            });
        });
    }

    /**
     * APK 1.24 (09.11.2026): сборка корзины собственными запросами сети (cartapi.js)
     * для Перекрёстка, Пятёрочки и Дикси, с 1.26 — и Магнита.
     */
    private void cartApi(final CartRun r) {
        if (!alive(r) || collectionBudgetExpired(r)) return;
        if (!alive(r)) return;
        r.cartApiDone = true;
        if (wallUp(r.code)) {
            apiGaveUp(r, (blocked(r.code) ? "защита сети закрыла адрес узла корзин" : "узел корзин сети недавно не отвечал") + peekNote(r.slot));
            return;
        }
        status(r, r.name + ": кладу запросами сети", false);
        beat(r, true);
        if ("pyaterochka".equals(r.code)) {
            Log.i(TAG, r.name + ": перед запросами " + cookieNote(cartApiOrigin(r.code), PYAT_COOKIE) + "; кука освежена "
                    + agoSince(memory().getLong("warm." + r.code, 0)) + ", лечили "
                    + (r.slot != null && r.slot.healAt > 0 ? (SystemClock.uptimeMillis() - r.slot.healAt) / 1000 + " с назад" : "ни разу с запуска"));
        }

        final JSONArray rest = new JSONArray();
        final List<JSONObject> skippedLines = new ArrayList<>();
        final List<String> skippedReasons = new ArrayList<>();
        final JSONArray linesInput = new JSONArray();
        final Map<String, JSONObject> inputLinesBySku = new HashMap<>();

        // Правка обязана уложиться в 5 с, а отказы старше FAIL_AGAIN_MS шли на перепроверку все разом и
        // съедали темп запросов (замер 11.11.2026: Дикси — 8 запросов, 10,8 с). Из отказавших позиций
        // сборка пробует одну: тронутую человеком — даже после недавнего отказа, иначе первую давнюю.
        JSONObject again = null;
        for (int i = 0; i < r.lines.length(); i++) {
            JSONObject line = r.lines.optJSONObject(i);
            if (line == null || line.optString("sku", "").isEmpty()
                    || failedBefore(r, line, "apifail.").isEmpty()) continue;
            boolean touched = line.optInt("product_id", 0) == r.changedPid;
            JSONArray ids = line.optJSONArray("product_ids");    // дубли на одном артикуле
            for (int k = 0; !touched && ids != null && k < ids.length(); k++) touched = ids.optInt(k) == r.changedPid;
            if (r.changedPid > 0 && touched) {
                again = line;
                break;
            }
            if (again == null && failedRecently(r, line, "apifail.").isEmpty()) again = line;
        }

        for (int i = 0; i < r.lines.length(); i++) {
            JSONObject line = r.lines.optJSONObject(i);
            if (line == null) continue;
            String sku = line.optString("sku", "");
            if (sku.isEmpty()) {
                rest.put(line);
                continue;
            }
            String failed = line == again ? "" : failedBefore(r, line, "apifail.");
            if (!failed.isEmpty()) {
                skippedLines.add(line);
                skippedReasons.add(failed);
                continue;
            }
            JSONObject itemIn = new JSONObject();
            try {
                itemIn.put("sku", sku);
                itemIn.put("target", Math.max(1, line.optInt("clicks", 1)));
                itemIn.put("url", line.optString("url", ""));
                itemIn.put("again", line == again);     // давний отказ: его новый отказ адаптеру не новость (1.41)
            } catch (Exception ignored) { }
            linesInput.put(itemIn);
            inputLinesBySku.put(sku, line);
        }

        final String origin = cartApiOrigin(r.code);
        if (origin == null || origin.isEmpty()) {
            begin(r);
            return;
        }

        // Только готовая robots.txt: страница сети, брошенная хвостом, могла ещё уходить по
        // loadUrl, и скрипт адаптера пропал бы вместе с ней (ждали бы CARTAPI_WAIT_MS).
        final String robots = origin + "/robots.txt";
        final long asked = SystemClock.uptimeMillis();
        r.view.evaluateJavascript("(location.href===" + JSONObject.quote(robots)
                + "&&!window.__kzStale&&document.readyState==='complete')?'1'"
                + ":location.host+location.pathname.slice(0,24)+' '+document.readyState+(window.__kzStale?' брошена':'')",
                currentVal -> {
            if (!alive(r)) return;
            String state = unquote(currentVal);
            if ("1".equals(state)) {
                runCartApiPayload(r, origin, rest, skippedLines, skippedReasons, linesInput, inputLinesBySku);
                return;
            }
            // В журнал — почему правка ждёт: окно застали не на отдыхе (замер 11.11.2026: 3,8 с у Пятёрочки).
            // supersede уже ведёт окно на robots.txt (1.36): загрузку заново не начинаем, ждём её.
            // 3 с — с запасом на загрузку robots.txt под нагрузкой; дольше — открываем сами, как раньше.
            // Сверяем и адрес: окно за это время могли увести на пустую страницу (stopBackground, lostView).
            boolean onTheWay = r.slot != null && SystemClock.uptimeMillis() - r.slot.robotsAt < 3000
                    && robots.equals(r.view.getUrl());
            Log.i(TAG, r.name + ": окно сети не на отдыхе (" + state + ", ответило за "
                    + (SystemClock.uptimeMillis() - asked) + " мс) — "
                    + (onTheWay ? "robots.txt уже открывается" : "открываю robots.txt"));
            if (!onTheWay) go(r, robots);
            whenReady(r, null, null, 0, () -> {
                if (!alive(r)) return;
                r.view.evaluateJavascript("location.origin", afterVal -> {
                    if (!alive(r)) return;
                    String afterOrigin = unquote(afterVal);
                    if (origin.equals(afterOrigin)) {
                        runCartApiPayload(r, origin, rest, skippedLines, skippedReasons, linesInput, inputLinesBySku);
                    } else {
                        apiGaveUp(r, "окно сети ушло на " + afterOrigin);
                    }
                });
            });
        });
    }

    /**
     * Запросами не вышло: отчёт об этом уходит сразу, касания — хвостом после него. Сразу
     * касаниями сеть держала отчёт минутами и грузила общий процесс окон, пока корзину ещё
     * правят (замер 10.11.2026: Пятёрочка после «обрыва сети» — 495 с касаний). Адрес закрыт
     * защитой — касаний нет совсем (endBlocked, 1.61).
     */
    private void apiGaveUp(CartRun r, String why) {
        r.apiOver = true;
        r.apiWhy = why;
        if (collectionBudgetExpired(r)) return;
        Log.i(TAG, r.name + ": запросом не вышло (" + why + ")" + (blocked(r.code) ? "" : ", касаниями — после отчёта"));
        if (endBlocked(r)) return;
        // Класть нечего, только снять своё: отчёта без позиций не бывает, идём прежней дорогой.
        if (r.lines.length() == 0 && r.later.length() == 0) {
            begin(r);
            return;
        }
        toTail(r, r.lines);
        finishRun(r, new JSONObject(), "");
    }

    /**
     * Адрес сети закрыт защитой (apiblock): карточки и поиск шли бы на тот же адрес и только копили отказы (эмулятор
     * 15.11.2026: три минуты касаний по /xpvnsulc/). Фоновая сборка кончается отказом с причиной; true — кончилась.
     * Сборку человека («Собрать», slot == null) не трогаем: он видит окно сети сам.
     */
    private boolean endBlocked(CartRun r) {
        if (r.slot == null || !blocked(r.code)) return false;
        long left = (memory().getLong("apiblock." + r.code, 0) - System.currentTimeMillis()) / 60000 + 1;
        endBackground(r, "failed", r.name + ": защита сети закрыла адрес, пауза ещё " + left + " мин");
        return true;
    }

    private void runCartApiPayload(final CartRun r, final String origin,
                                   final JSONArray rest,
                                   final List<JSONObject> skippedLines,
                                   final List<String> skippedReasons,
                                   final JSONArray linesInput,
                                   final Map<String, JSONObject> inputLinesBySku) {
        if (!alive(r)) return;

        final JSONObject input = new JSONObject();
        final String key = "a" + SystemClock.uptimeMillis();
        r.apiKey = key;
        try {
            input.put("key", key);
            input.put("chain", r.code);
            input.put("lines", linesInput);
            if (r.deadline > 0) input.put("budget_ms", Math.max(1, r.deadline - SystemClock.uptimeMillis()));
            JSONObject mine = new JSONObject();
            Iterator<String> skus = r.cart.keys();
            while (skus.hasNext()) {
                String s = skus.next();
                if (!searchedAgain(r, s)) mine.put(s, r.cart.optInt(s, 0));
            }
            input.put("mine", mine);
            input.put("want", new JSONObject(r.want.toString()));
            input.put("drop", shouldDrop(r));
            // Правка от касания (1.41): адаптеры Пятёрочки и Дикси не перечитывают корзину, которую видели
            // меньше минуты назад и не раньше, чем человек вернулся в приложение или из окна сети.
            input.put("touch", r.changedAt > 0);
            input.put("since", Math.max(homeSince, r.slot != null ? r.slot.movedAt : 0));
            // Точка человека из наряда (1.39): адрес для Самоката, магазин и доставка для Магнита.
            input.put("place", r.rules.optJSONObject("place"));
            int pauseMs = Math.max(300, r.rules.optInt("request_pause_ms", 1000));
            input.put("pause_ms", pauseMs);
            int burst = r.rules.optInt("api_burst", 3);
            input.put("burst", burst);
        } catch (Exception e) {
            apiGaveUp(r, "наряд не собрался: " + e.getMessage());
            return;
        }

        String script = cartApiScript();
        if (script.isEmpty()) {
            apiGaveUp(r, "в приложении нет cartapi.js");
            return;
        }

        if (collectionBudgetExpired(r)) return;
        String execCode = script + ";" + CARTAPI_START.replace("%s", input.toString());
        r.apiAt = SystemClock.uptimeMillis();
        r.apiOver = false;
        r.view.evaluateJavascript(execCode, startedVal -> {
            if (!alive(r)) return;
            String st = unquote(startedVal);
            if (!"started".equals(st)) {
                apiGaveUp(r, "скрипт запросов не запустился на странице сети");
                return;
            }

            final long until = SystemClock.uptimeMillis() + CARTAPI_WAIT_MS;
            final String pollCode = CARTAPI_POLL.replace("%s", JSONObject.quote(key));

            handler.postDelayed(new Runnable() {
                @Override
                public void run() {
                    if (!alive(r)) return;
                    final Runnable again = this;
                    r.view.evaluateJavascript(pollCode, pollVal -> {
                        if (!alive(r)) return;
                        JSONObject res = asObject(pollVal);
                        if (res.length() == 0 && SystemClock.uptimeMillis() < until) {
                            handler.postDelayed(again, 100);
                            return;
                        }
                        if (res.length() == 0) {
                            apiGaveUp(r, "сеть не ответила за " + CARTAPI_WAIT_MS / 1000 + " с");
                            return;
                        }

                        handleCartApiResult(r, origin, res, rest, skippedLines, skippedReasons, linesInput, inputLinesBySku);
                    });
                }
            }, 100);
        });
    }

    private void handleCartApiResult(final CartRun r, final String origin,
                                     final JSONObject res,
                                     final JSONArray rest,
                                     final List<JSONObject> skippedLines,
                                     final List<String> skippedReasons,
                                     final JSONArray linesInput,
                                     final Map<String, JSONObject> inputLinesBySku) {
        if (!alive(r)) return;
        r.apiOver = true;
        Log.i(TAG, r.name + ": ответ запросов сети: " + (res.optBoolean("ok", false) ? "ок"
                : "отказ, шаг «" + res.optString("stage", "") + "»" + (res.optBoolean("fast", false) ? ", быстрый" : ""))
                + "; запросов " + res.optInt("requests", 0) + ", всего " + res.optLong("ms", 0) + " мс, из них темп "
                + res.optLong("paced_ms", 0) + " мс"
                + ("pyaterochka".equals(r.code) ? "; " + cookieNote(cartApiOrigin(r.code), PYAT_COOKIE) : ""));
        // Сборку догнала правка: адаптер вернул корзину расчётом, без чтения сети (1.46, cartapi.js newer).
        final boolean reckoned = res.optBoolean("stale", false);

        JSONObject resCart = res.optJSONObject("cart");
        if (resCart != null) {
            Set<String> skus = new HashSet<>();
            Iterator<String> cartKeys = r.cart.keys();
            while (cartKeys.hasNext()) skus.add(cartKeys.next());
            Iterator<String> wantKeys = r.want.keys();
            while (wantKeys.hasNext()) skus.add(wantKeys.next());
            for (String s : skus) {
                int q = resCart.optInt(s, 0);
                // Расчёту своё убавлять не даём: запись, которую сеть приняла и не выполнила, осталась бы в
                // её корзине чужой. Убавит чтение сети, которым кончается серия правок.
                // shortcut: приложение закрыли посреди серии — своё завышено до следующей сборки с чтением.
                if (reckoned) q = Math.max(q, r.cart.optInt(s, 0));
                try {
                    r.cart.put(s, q);
                } catch (Exception ignored) { }
            }
            JSONObject resLines = res.optJSONObject("lines");
            if (resLines != null) {
                Iterator<String> lIt = resLines.keys();
                while (lIt.hasNext()) {
                    String s = lIt.next();
                    JSONObject lObj = resLines.optJSONObject(s);
                    if (lObj != null) {
                        double p = lObj.optDouble("price", 0);
                        if (p > 0) {
                            try {
                                r.prices.put(s, p);
                            } catch (Exception ignored) { }
                        }
                    }
                }
            }
            saveLedger(r);
        }

        boolean ok = res.optBoolean("ok", false);
        // Запрос прошёл — куки защиты сети живы: подогрев окна (warm) отсчитывается отсюда.
        if (ok) memory().edit().putLong("warm." + r.code, System.currentTimeMillis()).apply();
        if (!ok) {
            String stage = res.optString("stage", "");
            String why = res.optString("why", "");

            // Пятёрочка: «обрыв» — это 403 защиты сети, спрятанный CORS «*». Её куки обновляет
            // только скрипт своей страницы, robots.txt этого не умеет (эмулятор, 09.11.2026).
            boolean pyatNet = "pyaterochka".equals(r.code) && "net".equals(stage);
            // Куку лечим только на быстрый отказ защиты при живой связи (fast из cartapi.js): таймаут, обрыв посреди
            // записи и телефон без сети ею не лечатся. И не чаще HEAL_GAP_MS: лечили недавно, и не помогло — дальше
            // стена и касания.
            boolean pyatHeal = pyatNet && res.optBoolean("fast", false) && r.slot != null && healAllowed(r.slot);
            // Прочие «обрывы» (таймаут, обрыв посреди записи, телефон без сети) идут прежним входом: главная, 4 с, повтор.
            // Быстрый отказ, которого лечить уже нельзя (лечили недавно), сразу уходит в стену и касания.
            boolean pyatRefresh = "pyaterochka".equals(r.code)
                    && ("auth".equals(stage) || pyatHeal || (pyatNet && !res.optBoolean("fast", false)));
            if ((("auth".equals(stage) && "perekrestok".equals(r.code)) || pyatRefresh) && !r.cartApiRefreshed) {
                r.apiWhy = why;
                // Пока ждём страницу сети, запись завершена. Повтор начнётся только после проверки срока.
                if (collectionBudgetExpired(r)) return;
                r.cartApiRefreshed = true;
                memory().edit().putLong("warm." + r.code, System.currentTimeMillis()).apply();
                status(r, r.name + ": обновляю вход на сайте сети", false);
                final Runnable again = () -> {
                    if (!alive(r)) return;
                    // Правку, пришедшую, пока сборка ждала, старым нарядом не перебиваем: новая начнёт с нуля (1.50).
                    if (superseded(r)) {
                        supersede(r);
                        return;
                    }
                    beat(r, true);      // после паузы узла строка сборки не должна сойти за зависшую
                    runCartApiPayload(r, origin, rest, skippedLines, skippedReasons, linesInput, inputLinesBySku);
                };
                // «Обрыв» Пятёрочки — мгновенный отказ её защиты: протухшую куку главная не заменяет (1.50).
                if (pyatHeal) {
                    healPyat(r, origin, again);
                    return;
                }
                go(r, origin + "/");
                whenReady(r, "/", null, 4000, again);
                return;
            }

            if ("address".equals(stage)) {
                for (int i = 0; i < skippedLines.size(); i++) {
                    JSONObject sLine = skippedLines.get(i);
                    String sWhy = skippedReasons.get(i);
                    try {
                        r.items.put(new JSONObject()
                            .put("name", sLine != null ? sLine.optString("name", "") : "")
                            .put("sku", sLine != null ? sLine.optString("sku", "") : "")
                            .put("url", sLine != null ? sLine.optString("url", "") : "")
                            .put("ok", false)
                            .put("why", fitWhy("не легла недавно, попробую позже: " + sWhy))
                            .put("note", "")
                            .put("put", 0)
                            .put("times", sLine != null ? Math.max(1, sLine.optInt("clicks", 1)) : 1)
                            .put("how", "request")
                            .put("calls", new JSONArray()));
                    } catch (Exception ignored) { }
                }

                for (int i = 0; i < linesInput.length(); i++) {
                    JSONObject inLine = linesInput.optJSONObject(i);
                    String s = inLine != null ? inLine.optString("sku", "") : "";
                    JSONObject origLine = inputLinesBySku.get(s);
                    try {
                        r.items.put(new JSONObject()
                            .put("name", origLine != null ? origLine.optString("name", "") : "")
                            .put("sku", s)
                            .put("url", origLine != null ? origLine.optString("url", "") : "")
                            .put("ok", false)
                            .put("why", fitWhy(why))
                            .put("note", "")
                            .put("put", 0)
                            .put("times", origLine != null ? Math.max(1, origLine.optInt("clicks", 1)) : 1)
                            .put("how", "request")
                            .put("calls", new JSONArray()));
                    } catch (Exception ignored) { }
                }

                r.lines = rest;
                if (superseded(r)) { supersede(r); return; }
                if (rest.length() == 0) {
                    JSONObject emptyCart = new JSONObject();
                    try { emptyCart.put("totals", new JSONArray()); } catch (Exception ignored) { }
                    finishRun(r, emptyCart, "");
                } else {
                    r.at = 0;
                    begin(r);
                }
                return;
            }

            // Без входа Магнит отвечает 401, а Самокат отдаёт гостю токен с пустым user (1.29). Касания легли бы в корзину гостя: вход — шаг человека.
            if ("auth".equals(stage) && ("magnit".equals(r.code) || "samokat".equals(r.code))) {
                endBackground(r, "failed", why);
                return;
            }
            // Пятёрочка: страница сети открылась, а узел корзин и после неё не ответил — это защита сети,
            // API_WALL_MS сеть идёт касаниями. Прочим сетям метку не ставим: у них «обрыв» — обычно связь,
            // и после секундной потери интернета они три минуты шли бы касаниями.
            // shortcut: обрыв связи ровно на обновлении входа метит и Пятёрочку; проверять, что главная открылась, — если заметим.
            if (pyatNet) {
                memory().edit().putLong("apiwall." + r.code, System.currentTimeMillis()).apply();
                // Куку не лечили — журнал телефона скажет, почему (1.50): отказ не быстрый, повтор входа или лечили недавно.
                if (r.healNote.isEmpty()) {
                    r.healNote = "; куку не лечил: " + (!res.optBoolean("fast", false) ? "отказ не быстрый или связи нет"
                            : r.cartApiRefreshed ? "быстрый отказ после повтора входа" : "быстрый отказ, но лечили недавно")
                            + " (запросов " + res.optInt("requests", 0) + ", сборка " + res.optLong("ms", 0) + " мс)";
                }
            }
            // Защита сети просит подождать (429 на чтении, Впрок): адрес закрыт на ASK_WAIT_MS — до её конца ни запросов,
            // ни касаний (те же страницы шли бы на него же). endBlocked закончит фоновую сборку, сборка человека идёт касаниями.
            if ("wall".equals(stage)) {
                memory().edit().putLong("apiblock." + r.code, System.currentTimeMillis() + ASK_WAIT_MS).apply();
            }
            apiGaveUp(r, why + r.healNote + peekNote(r.slot));
            return;
        }

        for (int i = 0; i < skippedLines.size(); i++) {
            JSONObject sLine = skippedLines.get(i);
            String sWhy = skippedReasons.get(i);
            try {
                r.items.put(new JSONObject()
                    .put("name", sLine != null ? sLine.optString("name", "") : "")
                    .put("sku", sLine != null ? sLine.optString("sku", "") : "")
                    .put("url", sLine != null ? sLine.optString("url", "") : "")
                    .put("ok", false)
                    .put("why", fitWhy("не легла недавно, попробую позже: " + sWhy))
                    .put("note", "")
                    .put("put", 0)
                    .put("times", sLine != null ? Math.max(1, sLine.optInt("clicks", 1)) : 1)
                    .put("how", "request")
                    .put("calls", new JSONArray()));
            } catch (Exception ignored) { }
        }

        JSONObject resLines = res.optJSONObject("lines");
        int okCount = 0;
        int totalCount = 0;

        for (int i = 0; i < linesInput.length(); i++) {
            JSONObject inLine = linesInput.optJSONObject(i);
            String s = inLine != null ? inLine.optString("sku", "") : "";
            JSONObject origLine = inputLinesBySku.get(s);
            if (origLine == null) continue;
            totalCount++;

            JSONObject lRes = resLines != null ? resLines.optJSONObject(s) : null;
            if (lRes == null || lRes.optBoolean("retry", false)) {
                rest.put(origLine);
                continue;
            }

            boolean lOk = lRes.optBoolean("ok", false);
            String lWhy = lRes.optString("why", "");
            String lHow = lRes.optString("how", "request");
            int lPut = lRes.optInt("put", 0);
            int target = Math.max(1, origLine.optInt("clicks", 1));
            JSONArray calls = lRes.optJSONArray("calls");
            double price = lRes.optDouble("price", 0);
            boolean gone = lRes.optBoolean("gone", false);

            if (lOk) okCount++;

            try {
                JSONObject item = new JSONObject()
                    .put("name", origLine.optString("name", ""))
                    .put("sku", s)
                    .put("url", origLine.optString("url", ""))
                    .put("ok", lOk)
                    .put("why", fitWhy(lWhy))
                    .put("note", "")
                    .put("put", lPut)
                    .put("times", target)
                    .put("how", lHow)
                    .put("calls", calls != null ? calls : new JSONArray());

                String foundName = origLine.optString("found_name", "");
                if (!foundName.isEmpty()) item.put("found_name", foundName);
                if (price > 0) item.put("price", price);
                if (gone) {
                    item.put("in_stock", false);
                } else if (lOk && lPut > 0) {
                    item.put("in_stock", true);
                }
                r.items.put(item);
            } catch (Exception ignored) { }

            noteFail(r, origLine, lOk ? "" : lWhy, "apifail.");
        }

        r.droppedCount += res.optInt("dropped", 0);
        if (res.optBoolean("tpl", false)) extendTpl(r.code);

        JSONObject cartObj = new JSONObject();
        JSONArray totals = new JSONArray();
        JSONObject totalObj = res.optJSONObject("total");
        if (totalObj != null) {
            r.cartRead = true;
            double amount = totalObj.optDouble("amount", 0);
            double goods = totalObj.optDouble("goods", 0);
            double base = totalObj.optDouble("base", 0);
            double delivery = totalObj.optDouble("delivery", 0);
            int count = totalObj.optInt("count", 0);
            String path = totalObj.optString("path", "");

            try {
                JSONObject totalRow = new JSONObject();
                totalRow.put("kind", "total");
                totalRow.put("label", "Итого");
                totalRow.put("amount", amount);
                totalRow.put("row", "Итого " + money(amount) + " (ответ сети)");
                totals.put(totalRow);

                JSONObject goodsRow = new JSONObject();
                goodsRow.put("kind", "goods");
                goodsRow.put("label", "Товары");
                goodsRow.put("amount", goods);
                if (base > goods) goodsRow.put("base", base);
                goodsRow.put("row", "Товары " + money(goods) + " (ответ сети)");
                totals.put(goodsRow);

                if (delivery > 0) {
                    JSONObject delRow = new JSONObject();
                    delRow.put("kind", "delivery");
                    delRow.put("label", "Доставка");
                    delRow.put("amount", delivery);
                    delRow.put("row", "Доставка " + money(delivery) + " (ответ сети)");
                    totals.put(delRow);
                }

                double service = amount - goods - delivery;
                if (service > 0.005) {
                    JSONObject srvRow = new JSONObject();
                    srvRow.put("kind", "service");
                    srvRow.put("label", "Сборка");
                    srvRow.put("amount", Math.round(service * 100.0) / 100.0);
                    srvRow.put("row", "Сборка " + money(service) + " (ответ сети)");
                    totals.put(srvRow);
                }

                JSONObject apiMap = new JSONObject();
                JSONObject pathData = new JSONObject();
                pathData.put("amount", amount);
                pathData.put("goods", goods);
                pathData.put("base", base);
                pathData.put("discount", totalObj.optDouble("discount", 0));
                pathData.put("delivery", delivery);
                pathData.put("count", count);
                apiMap.put(path, pathData);

                cartObj.put("totals", totals);
                cartObj.put("api", apiMap);
                cartObj.put("page", path);
                cartObj.put("count", count);
            } catch (Exception ignored) { }
        } else {
            try { cartObj.put("totals", totals); } catch (Exception ignored) { }
        }

        if (reckoned && r.slot != null) {
            Log.i(TAG, r.name + ": сборку догнала правка — итог не читали (запросов " + res.optInt("requests", 0)
                + ", ждали темпа " + res.optLong("paced_ms", 0) + " мс)");
            // Корзина вернулась к отпечатку этой сборки («+» и тут же «−»), а итога у расчёта нет: её же ещё раз.
            if (r.slot.pending.isEmpty()) {
                r.slot.pending = r.stamp;
                r.slot.changedAt = r.changedAt;
                r.slot.changedPid = r.changedPid;
            }
            supersede(r);
            return;
        }
        if (superseded(r)) {
            supersede(r);
            return;
        }

        Log.i(TAG, r.name + ": запросами сети — легло " + okCount + " из " + totalCount
            + (res.optString("place").isEmpty() ? "" : " [" + res.optString("place") + "]")
            + " за " + res.optLong("ms", 0) + " мс, снято " + res.optInt("dropped", 0)
            + " (запросов " + res.optInt("requests", 0) + ", ждали темпа " + res.optLong("paced_ms", 0) + " мс;"
            + " от запуска: наряд " + (r.planAt - r.startedAt) + " мс, запросы с " + (r.apiAt - r.startedAt) + " мс)");

        toTail(r, rest);   // что запросом не кладётся (весовое): отчёт о положенном их не ждёт
        finishRun(r, cartObj, "");
    }

    private boolean collectionBudgetExpired(CartRun r) {
        if (r.slot == null || r.deadline == 0 || writing(r)
                || SystemClock.uptimeMillis() < r.deadline) return false;
        String note = "Сеть не успела подтвердить корзину за 5 секунд";
        if (!r.apiWhy.isEmpty()) note += ". Последний сбой запроса: " + r.apiWhy;
        endBackground(r, "failed", fitWhy(note));
        return true;
    }

    /** Наряд пришёл: сначала новые позиции (первая учит запрос сети), потом прибавки, потом лежащие. */
    private void begin(final CartRun r) {
        if (endBlocked(r)) return;      // и сеть без запросов (rules.api), и поиск хвостом шли бы на закрытый адрес
        loadLedger(r);      // до heldForApi: «снять своё» (hasExcess) считается по записи положенного
        if (heldForApi(r)) return;
        if (!alive(r) || collectionBudgetExpired(r)) return;
        if (r.lines.length() == 0 && r.later.length() > 0) {
            // Сеть не знает ни одного товара корзины (1.48): отчёт уходит сразу, поиск касаниями — хвостом
            // после него, как при знакомых товарах. Раньше поиск начинался под палец и держал отчёт до
            // своего конца: корзина из чека, Пятёрочка, 13.11.2026 — 96–135 с от касания. Своё лишнее
            // сначала снимет cartApi ниже, и отчёт уйдёт после него.
            boolean byApi = !r.tail && !r.cartApiDone && r.rules.optBoolean("api", false)
                    && cartApiOrigin(r.code) != null;
            if (byApi && !(shouldDrop(r) && hasExcess(r))) {
                finishRun(r, new JSONObject(), "");
                return;
            }
            if (!byApi) {
                r.lines = r.later;
                r.later = new JSONArray();
                r.tail = true;
            }
        }
        if (r.slot != null && !r.tail && !r.cartApiDone && r.rules.optBoolean("api", false)
                && cartApiOrigin(r.code) != null
                && (r.lines.length() > 0 || (shouldDrop(r) && hasExcess(r)))) {
            // Окно лечит куку защиты на главной странице (whenHealed): уведённое на robots.txt, оно оставило бы
            // узел корзин без новой куки. Ждём конца лечения, а после чтения заранее — и паузы узла (HEAL_QUIET_MS):
            // по стене правка ушла бы касаниями (десятки секунд и капча вместо одного запроса).
            // Кука Пятёрочки старая — первый запрос получил бы отказ, а отказ запирает узел на минуту: лечим до него (1.57).
            if (aheadDue(r.slot)) aheadHeal(r.slot, "перед сборкой");
            noteHeal(r);
            if (healing(r.slot)) {
                handler.postDelayed(() -> {
                    if (alive(r)) begin(r);
                }, 250);
                return;
            }
            cartApi(r);
            return;
        }
        if (r.lines.length() == 0) {
            if (shouldDrop(r) && hasExcess(r)) {
                if (r.slot != null && storedTpl(r.code) != null) {
                    r.learned = true;
                    r.learnTried = true;
                    r.reusedTpl = true;
                    ensureTpl(r, () -> endOfRun(r));
                    return;
                }
                endOfRun(r);   // класть нечего — снять своё и прочитать корзину сети
                return;
            }
            String note = r.rules.optString("_note", "");
            endBackground(r, "empty", note.isEmpty() ? "позиции корзины этой сети незнакомы" : note);
            return;
        }
        final Runnable proceed = () -> {
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
        };
        if (r.slot != null && storedTpl(r.code) != null) {
            r.learned = true;
            r.learnTried = true;
            r.reusedTpl = true;
            ensureTpl(r, proceed);
            return;
        }
        proceed.run();
    }

    /**
     * Касания — сети без запросов и касания после запросов (сеть отказала, позиции без артикула) — грузят
     * общий процесс окон и ждут до API_HEAD_MS, пока другие сети с запросами допишут. Замер 09.11.2026:
     * Магнит без образца ушёл на касания, и запрос Пятёрочки в соседнем окне не дождался ответа за 15 с
     * («обрыв сети»). Сборка, которой класть и снимать нечего, не ждёт (1.59).
     */
    private boolean heldForApi(final CartRun r) {
        Slot s = r.slot;
        boolean touches = r.cartApiDone || cartApiOrigin(r.code) == null;
        boolean work = r.lines.length() > 0 || r.later.length() > 0 || (shouldDrop(r) && hasExcess(r));
        if (s == null || !touches || !work || !apiRunning(s)) {
            if (s != null) s.heldSince = 0;
            return false;
        }
        long now = SystemClock.uptimeMillis();
        if (s.heldSince == 0) {
            s.heldSince = now;
            // Окно сети без запросов гасим, пока ждём: до первого касания пройдёт не меньше 250 мс, и пустая страница успеет встать.
            if (cartApiOrigin(r.code) == null) r.view.loadUrl("about:blank");
            status(r, "жду, пока допишут сети с запросами", false);
        }
        if (now - s.heldSince >= API_HEAD_MS) return false;
        handler.postDelayed(() -> {
            if (alive(r)) begin(r);
        }, 250);
        return true;
    }

    /** Фоновая сборка кончилась без отчёта: класть нечего, наряд не пришёл, сеть не пустила. */
    private void endBackground(CartRun r, String state, String note) {
        Slot s = r.slot;
        if (s == null || s.run != r) return;
        r.stopped = true;
        s.run = null;
        r.now = note;
        sendEnd(r, state, note);
        remember(r, "empty".equals(state));
        Log.i(TAG, r.name + ": фоновая сборка не пошла — " + note);
        rest(s);
    }

    /** Сказать серверу, что сборка кончилась без отчёта (state — failed или empty) и почему: строка не ждёт PHONE_STALE. */
    private void sendEnd(CartRun r, String state, String note) {
        JSONObject body = new JSONObject();
        try {
            body.put("store", r.code).put("state", state).put("note", note)
                .put("planned", r.lines.length()).put("at", r.at);
            if (r.basket >= 0) body.put("basket", r.basket);
            if (!r.stamp.isEmpty()) body.put("stamp", r.stamp);
        } catch (Exception ignored) { }
        call("/api/device/cartprogress", body, (status, text) -> tellPage());
    }

    /** Остановить фоновую сборку сети. `forHuman` — человек собирает сам: её корзина — его. */
    private void stopBackground(Slot s, boolean forHuman) {
        CartRun r = s.run;
        if (r == null) return;
        Log.i(TAG, r.name + ": сборку остановили" + (forHuman ? " (корзина поменялась или человек собирает сам)" : " (уход приложения)"));
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
        if (s.keepAlive != null) handler.removeCallbacks(s.keepAlive);
        s.keepAlive = null;
        if (!away && s.view != null && warmDue(s) == 0) {
            warm(s);
            return;
        }
        handler.postDelayed(() -> {
            if (s.run == null && s.view != null && !healing(s)) {
                // Окно с образцом запроса или адаптером отдыхает на robots.txt своей сети: скриптов
                // сети там нет, как на пустой странице, а следующей сборке не нужно заново открывать её
                // адрес (ensureTpl сверяет origin).
                String origin = cartApiOrigin(s.code);
                if (origin == null) {
                    JSONObject st = storedTpl(s.code);
                    origin = st != null ? st.optString("origin", "") : "";
                }
                if (origin != null && !origin.isEmpty()) {
                    // Окно уже там — не трогаем (1.29): перезагрузка стирала память окна (витрина Самоката,
                    // номер заказа Пятёрочки), а правка следом ждала, пока она кончится.
                    String robots = origin + "/robots.txt";
                    if (!robots.equals(s.view.getUrl())) s.view.loadUrl(robots);
                } else {
                    s.view.loadUrl("about:blank");
                }
            }
        }, 1500);
        if (s.sleep != null) handler.removeCallbacks(s.sleep);
        s.sleep = null;

        // Пятёрочка: держим куки ServicePipe свежими, чтобы не тратить 4 с на pyatRefresh при нажатии (1.28).
        long left = warmDue(s);
        if (!away && left < Long.MAX_VALUE) {
            s.keepAlive = () -> {
                s.keepAlive = null;
                if (!away && s.run == null && s.view != null && s.pending.isEmpty()) warm(s);
            };
            handler.postDelayed(s.keepAlive, left);
        }

        // Окно сети с запросами не убираем: на robots.txt скриптов сети нет, а открыть его заново —
        // секунда-две из пяти, отведённых на правку (1.28).
        if (cartApiOrigin(s.code) != null) return;
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
     * Через сколько мс окну пора на страницу сети за свежими куками защиты; сетям без этого — никогда, и закрытому
     * защитой адресу тоже (blocked): подогрев раз в KEEP_ALIVE_MS копил бы ему отказы.
     */
    private long warmDue(Slot s) {
        if (!"pyaterochka".equals(s.code) || blocked(s.code)) return Long.MAX_VALUE;
        long idle = System.currentTimeMillis() - memory().getLong("warm." + s.code, 0);
        return Math.max(0, Math.min(KEEP_ALIVE_MS, KEEP_ALIVE_MS - idle));
    }

    /**
     * Куки защиты Пятёрочки обновляет только скрипт её страницы (см. pyatRefresh). Заходим туда, пока
     * корзину не правят: при запуске, по возвращении и после KEEP_ALIVE_MS без дела. Свёрнутое
     * приложение сеть не трогает (1.29): в 1.28 главная грузилась каждые 10 минут, пока жив процесс.
     */
    private void warm(final Slot s) {
        if (healing(s)) return;     // окно и так на главной: лечит куку защиты (whenHealed)
        Log.i(TAG, CHAINS[s.chain][0] + ": подогрев — открываю главную; " + cookieNote(cartApiOrigin(s.code), PYAT_COOKIE)
                + "; прошлый подогрев " + agoSince(memory().getLong("warm." + s.code, 0)));
        memory().edit().putLong("warm." + s.code, System.currentTimeMillis()).apply();
        s.view.loadUrl(cartApiOrigin(s.code) + "/");
        s.keepAlive = () -> {
            s.keepAlive = null;
            Log.i(TAG, CHAINS[s.chain][0] + ": подогрев кончен; " + cookieNote(cartApiOrigin(s.code), PYAT_COOKIE));
            // Обратно на robots.txt сразу, без паузы rest: пока окно на главной, правка ждёт его загрузки.
            if (s.run == null && s.view != null) s.view.loadUrl(cartApiOrigin(s.code) + "/robots.txt");
            rest(s);
        };
        handler.postDelayed(s.keepAlive, 3500);
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
        boolean again = false;
        if (r != null) {
            r.stopped = true;
            s.run = null;
            // До 1.56 счёт падений не обнулялся: второе падение окна за всё время жизни приложения бросало сборку молча,
            // а строка сети крутилась «идёт» до PHONE_STALE.
            long now = SystemClock.uptimeMillis();
            if (now - s.crashAt > CRASH_FORGET_MS) s.crashes = 0;
            s.crashAt = now;
            again = s.crashes++ < 1 && !r.stamp.isEmpty();
            if (again) s.pending = r.stamp;
        }
        stage.removeView(view);
        view.destroy();
        s.view = null;
        Log.w(TAG, CHAINS[idx][0] + ": скрытое окно сети упало" + (r == null ? " (сборки не было)"
                : again ? ", соберу заново" : ", второй раз подряд — заново не собираю"));
        if (r != null) {
            // Процесс страниц один на все окна: падает он разом, и хроника у всех сетей одна — не чаще раза за TRACE_GAP_MS.
            Long was = traceSent.get("crash");
            long now = SystemClock.uptimeMillis();
            if (was == null || now - was >= TRACE_GAP_MS) {
                traceSent.put("crash", now);
                Log.i(TAG, memoryNote());
                uploadTrace(s.code, "окно сети упало посреди сборки: " + r.now);
            }
            if (!again) sendEnd(r, "failed", CHAINS[idx][0] + ": окно сети упало (нехватка памяти) — соберу при следующей правке");
        }
        if (!s.pending.isEmpty() && !away) soon(s);
    }

    // ── Память телефона: что куда положено и какую корзину собирали ──

    private SharedPreferences memory() {
        if (prefs == null) {
            prefs = getSharedPreferences("korzina-collect", MODE_PRIVATE);
            // Наряды в памяти несут правила сервера для прежней версии приложения (с какой версии сеть
            // кладётся запросами): после обновления берём их у сервера заново (1.29). Стена apiwall.* — тоже: она от
            // отказа прежней версии, а новую ставят, чтобы узел сети проверить заново (1.53; 14.11.2026 первые минуты
            // 1.51 прошли за стеной 1.49). Блок защиты (apiblock.*) остаётся: он про адрес сети, а не про версию.
            if (!BuildConfig.VERSION_NAME.equals(prefs.getString("planApp", ""))) {
                SharedPreferences.Editor e = prefs.edit().putString("planApp", BuildConfig.VERSION_NAME);
                for (String k : prefs.getAll().keySet()) if (k.startsWith("planAt.") || k.startsWith("apiwall.")) e.remove(k);
                e.apply();
            }
        }
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
                if (!it.optString("u", "").isEmpty() && !r.urls.has(sku)) r.urls.put(sku, it.optString("u", ""));
                if (it.optInt("p", 0) > 0 && !r.pids.has(sku)) r.pids.put(sku, it.optInt("p", 0));
                if (it.optDouble("k", 0) > 0) r.packOk.put(sku, it.optDouble("k", 0));
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
                String u = r.urls.optString(sku, "");
                if (!u.isEmpty()) it.put("u", u);
                if (r.pids.optInt(sku, 0) > 0) it.put("p", r.pids.optInt(sku, 0));
                if (r.packOk.optDouble(sku, 0) > 0) it.put("k", r.packOk.optDouble(sku, 0));
                all.put(sku, it);
            }
            memory().edit().putString("ledger." + r.code, all.toString()).apply();
        } catch (Exception ignored) { }
    }

    private JSONObject getOrderCurrent() {
        try {
            String raw = memory().getString("order.current", null);
            return raw != null ? new JSONObject(raw) : null;
        } catch (Exception e) {
            return null;
        }
    }

    /** Нужно ли снимать лишнее своё (1.19): фоновая сборка или после «Заказать». */
    private boolean shouldDrop(CartRun r) {
        if (r.slot != null) return true;
        if (r.linesOverride != null) return true;
        JSONObject order = getOrderCurrent();
        if (order != null && !r.stamp.isEmpty() && r.stamp.equals(order.optString("stamp", ""))) return true;
        return false;
    }

    // Строки наряда с артикулом — r.want: после запросов сети в r.lines остаётся только
    // недоложенное, и по одним строкам положенное запросами сочлось бы лишним (замер 09.11.2026).
    private int wantOf(CartRun r, String sku) {
        int want = r.want.optInt(sku, 0);
        for (int i = 0; i < r.lines.length(); i++) {
            JSONObject line = r.lines.optJSONObject(i);
            if (line != null && line.has("search") && sku.equals(line.optString("sku", ""))) {
                want += Math.max(0, line.optInt("clicks", 0));
            }
        }
        return want;
    }

    /**
     * Найденное прошлым поиском, пока строка его товара снова ищется: снялось бы, чтобы тут
     * же лечь снова. Память до 1.25 без product_id бережём, пока в наряде есть любой поиск.
     */
    private boolean searchedAgain(CartRun r, String sku) {
        // Найдено раньше и лежит (1.49): в наряде у товара одна штука, а сколько их в сети, решал человек.
        if (r.adopted.contains(sku)) return true;
        if (wantOf(r, sku) > 0) return false;
        int pid = r.pids.optInt(sku, 0);
        // Товар в корзине есть, артикула в наряде у него нет: строка поиска для защиты не нужна (1.43).
        if (pid > 0 && r.noSku.contains(pid)) return true;
        for (JSONArray list : new JSONArray[]{r.lines, r.later}) {
            for (int i = 0; i < list.length(); i++) {
                JSONObject line = list.optJSONObject(i);
                if (line == null || !line.optString("sku", "").isEmpty()) continue;
                if (pid == 0 || pid == line.optInt("product_id", 0)) return true;
            }
        }
        return false;
    }

    private boolean hasExcess(CartRun r) {
        Iterator<String> keys = r.cart.keys();
        while (keys.hasNext()) {
            String sku = keys.next();
            int q = r.cart.optInt(sku, 0);
            if (q <= 0) continue;
            if (q > wantOf(r, sku) && !searchedAgain(r, sku)) return true;
        }
        return false;
    }

    private static final class DropItem {
        final String sku;
        final int drop;
        final int want;
        String url;
        String name;

        DropItem(String sku, int drop, int want, String url, String name) {
            this.sku = sku;
            this.drop = drop;
            this.want = want;
            this.url = url;
            this.name = name;
        }
    }

    /** Снять своё: уменьшение и удаление из корзины сети (приложение 1.19). */
    private void dropExcess(final CartRun r, final Runnable then) {
        if (!alive(r)) return;
        final List<DropItem> queue = new ArrayList<>();
        Iterator<String> keys = r.cart.keys();
        while (keys.hasNext()) {
            String sku = keys.next();
            int q = r.cart.optInt(sku, 0);
            if (q <= 0) continue;
            int want = wantOf(r, sku);
            if (q > want && !searchedAgain(r, sku)) {
                queue.add(new DropItem(sku, q - want, want, r.urls.optString(sku, ""), r.names.optString(sku, "")));
            }
        }
        if (queue.isEmpty()) {
            then.run();
            return;
        }
        status(r, r.name + ": снимаю своё (" + queue.size() + " поз.)", false);
        processDropQueue(r, queue, 0, then);
    }

    private void processDropQueue(final CartRun r, final List<DropItem> queue, final int idx, final Runnable then) {
        if (!alive(r)) return;
        if (idx >= queue.size()) {
            if (r.droppedCount > 0) {
                String note = "убрано своё: " + r.droppedCount;
                r.linkNote = r.linkNote.isEmpty() ? note : (r.linkNote + "; " + note);
                Log.i(TAG, r.name + ": " + note);
            }
            then.run();
            return;
        }
        final DropItem item = queue.get(idx);
        final Runnable nextItem = () -> processDropQueue(r, queue, idx + 1, then);

        // Сеть с rules.qty_sets и выученным запросом — повтор запроса с количеством want
        if (r.rules.optBoolean("qty_sets") && r.learned) {
            replayDrop(r, item, ok -> {
                if (ok) {
                    own(r, item.sku, item.want);
                    r.droppedCount += item.drop;
                    Log.i(TAG, r.name + ": убрано своё запросом: " + item.sku + " (" + item.drop + " уп.)");
                    nextItem.run();
                } else {
                    r.learned = false;
                    status(r, r.name + ": отказ запроса снятия, дальше касаниями", true);
                    tapDropItem(r, item, nextItem);
                }
            });
            return;
        }
        tapDropItem(r, item, nextItem);
    }

    private void replayDrop(final CartRun r, final DropItem item, final ValueCallback<Boolean> callback) {
        final String key = "drop_" + item.sku;
        JSONObject p = new JSONObject();
        try {
            p.put("sku", item.sku).put("qty", item.want).put("key", key);
        } catch (Exception ignored) { }
        r.view.evaluateJavascript(REPLAY_JS.replace("%s", p.toString()), started -> {
            if (!alive(r)) { callback.onReceiveValue(false); return; }
            String st = unquote(started);
            if (!"started".equals(st)) {
                if (r.reusedTpl) {
                    dropTpl(r.code);
                    r.reusedTpl = false;
                    r.learnTried = false;
                }
                callback.onReceiveValue(false);
                return;
            }
            final long until = SystemClock.uptimeMillis() + 15000;
            handler.postDelayed(new Runnable() {
                @Override
                public void run() {
                    if (!alive(r)) { callback.onReceiveValue(false); return; }
                    final Runnable again = this;
                    r.view.evaluateJavascript(REPLAY_POLL_JS.replace("%s", JSONObject.quote(key)), value -> {
                        if (!alive(r)) { callback.onReceiveValue(false); return; }
                        JSONObject res = asObject(value);
                        if (res.length() == 0 && SystemClock.uptimeMillis() < until) {
                            handler.postDelayed(again, 100);
                            return;
                        }
                        boolean ok = res.optBoolean("ok");
                        if (ok) {
                            if (r.reusedTpl && !r.extendedTpl) {
                                extendTpl(r.code);
                                r.extendedTpl = true;
                            }
                        } else {
                            if (r.reusedTpl) {
                                dropTpl(r.code);
                                r.reusedTpl = false;
                                r.learnTried = false;
                            }
                        }
                        callback.onReceiveValue(ok);
                    });
                }
            }, 100);
        });
    }

    private void tapDropItem(final CartRun r, final DropItem item, final Runnable onDone) {
        if (item.url.isEmpty()) {
            Log.w(TAG, r.name + ": нет адреса карточки для снятия артикула " + item.sku + " — пропускаю");
            onDone.run();
            return;
        }
        status(r, r.name + ": открываю карточку для снятия: " + (item.name.isEmpty() ? item.sku : item.name), false);
        go(r, item.url);
        whenReady(r, item.url, null, SETTLE_MS, () -> {
            tapMinusLoop(r, item, item.drop, onDone);
        });
    }

    private void tapMinusLoop(final CartRun r, final DropItem item, final int remainingDrop, final Runnable onDone) {
        if (!alive(r)) return;
        if (remainingDrop <= 0) {
            onDone.run();
            return;
        }
        JSONObject p = new JSONObject();
        try {
            p.put("minus", r.rules.optString("minus_name", "^[−–-]$|уменьш|minus|decrease|удал|убра|remove|delete"));
            p.put("plus", r.rules.optString("plus_name", ""));
            p.put("add", r.rules.optString("add_name", ""));
            p.put("below", r.rules.optInt("below", 0));
            p.put("path", pathOf(item.url));
            p.put("sku", item.sku);
        } catch (Exception ignored) { }
        final String pick = PICK_MINUS_JS.replace("%s", p.toString());
        r.view.evaluateJavascript(pick, value -> {
            if (!alive(r)) return;
            JSONObject got = asObject(value);
            if (!got.optBoolean("found")) {
                Log.w(TAG, r.name + ": " + (got.optBoolean("moved") ? "открылась не та карточка" : "кнопка «−» не нашлась")
                    + " (" + item.sku + ") — не снимаю");
                onDone.run();
                return;
            }
            final double countBefore = got.isNull("count") ? -1 : got.optDouble("count", -1);
            // Без числа касание нечем проверить; на «нужно» и ниже своего снимать уже нечего.
            if (countBefore < 0 || countBefore <= item.want) {
                Log.i(TAG, r.name + ": на карточке " + item.sku + " число "
                    + (countBefore < 0 ? "не читается" : "уже " + countBefore) + " — не снимаю");
                if (countBefore >= 0) own(r, item.sku, Math.min(r.cart.optInt(item.sku, 0), (int) countBefore));
                onDone.run();
                return;
            }
            r.view.evaluateJavascript(COORDS_JS, atValue -> {
                if (!alive(r)) return;
                JSONObject at = asObject(atValue);
                if (!at.optBoolean("found")) {
                    onDone.run();
                    return;
                }
                tapAt(r.view, at);
                // Касание было — своего на одно меньше, даже если проверить нечем: так следующая
                // сборка не снимет лишнего, что положил человек.
                own(r, item.sku, r.cart.optInt(item.sku, 0) - 1);
                final int pause = Math.max(300, r.rules.optInt("click_pause_ms", 300));
                handler.postDelayed(() -> {
                    if (!alive(r)) return;
                    r.view.evaluateJavascript(pick, againVal -> {
                        if (!alive(r)) return;
                        JSONObject again = asObject(againVal);
                        double countAfter = !again.optBoolean("found") || again.isNull("count") ? -1 : again.optDouble("count", -1);
                        // Число уменьшилось, или степпер ушёл вместе с последней штукой.
                        boolean decreased = countAfter >= 0 ? countAfter < countBefore : countBefore <= 1;
                        if (!decreased) {
                            Log.i(TAG, r.name + ": число в виджете не уменьшилось на " + item.sku + " — останавливаюсь");
                            onDone.run();
                            return;
                        }
                        r.droppedCount++;
                        handler.postDelayed(() -> tapMinusLoop(r, item, remainingDrop - 1, onDone), pause);
                    });
                }, pause);
            });
        });
    }

    /** Своего этого артикула в корзине сети теперь n (0 — забыть совсем); память — в ledger. */
    private void own(CartRun r, String sku, int n) {
        if (n <= 0) {
            r.cart.remove(sku);
            r.urls.remove(sku);
            r.shown.remove(sku);
        } else {
            try { r.cart.put(sku, n); } catch (Exception ignored) { }
        }
        saveLedger(r);
    }

    /** Эту корзину в этой сети собрали (ok) или пытались: без перемен в корзине — не повторять. */
    private void remember(CartRun r, boolean ok) {
        if (r.stamp.isEmpty()) return;
        try {
            JSONObject done = new JSONObject()
                .put("stamp", r.orderAt > 0 ? r.orderStamp : r.stamp).put("basket", r.basket).put("ok", ok)
                .put("at", System.currentTimeMillis());
            if (r.orderAt > 0) done.put("order", r.orderAt);
            if (r.wall) done.put("wall", true);
            memory().edit().putString("done." + r.code, done.toString()).apply();
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

    interface Reply {
        void got(int status, String text);
    }

    /**
     * Запрос, который можно повторить без вреда (наряд: оба запроса несут один номер вопроса ask, и сервер
     * считает его один раз): не ответил за HEDGE_MS — уходит ещё раз, берём первый удачный ответ.
     * ЗАЧЕМ (замер 13.11.2026): наряды шести сетей уходят разом, часть — по новым соединениям,
     * и одно из них изредка открывается 3–17 с (VPN, потерянный пакет), пока соседние отвечают за 0,2 с:
     * Самокат ждал наряд 16,9 с. Второй запрос берёт соединение, которое к тому времени освободил сосед.
     */
    private void callTwice(final String path, final JSONObject body, final Reply reply,
                           final java.util.function.BooleanSupplier wanted) {
        final int[] flying = {1};
        final boolean[] done = {false};
        final Reply once = (status, text) -> {
            flying[0]--;
            if (done[0] || (status != 200 && flying[0] > 0)) return;     // отказал один — ждём второй
            done[0] = true;
            reply.got(status, text);
        };
        call(path, body, once);
        handler.postDelayed(() -> {
            if (done[0] || !wanted.getAsBoolean()) return;
            flying[0]++;
            Log.i(TAG, path + ": ответа нет " + HEDGE_MS + " мс — тот же запрос ещё раз");
            call(path, body, once);
        }, HEDGE_MS);
    }

    /**
     * Запрос на наш сервер с сессией «Моей корзины»; ответ — на главном потоке.
     *
     * body == null — GET: так забирается своя статика (сборщик кабинета ФНС,
     * /static/fns-grab.js), которую сервер отдаёт только на GET.
     */
    private void call(String path, JSONObject body, Reply reply) {
        requestApp(CookieManager.getInstance().getCookie(SERVER), path, body, reply);
    }

    static void requestApp(String session, String path, JSONObject body, Reply reply) {
        final boolean post = body != null;
        android.os.Handler handler = new android.os.Handler(android.os.Looper.getMainLooper());
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
    /** Сохранить вход открытой сети. Ложь — кук нет, сохранять нечего. */
    private boolean saveLogin() {
        if (currentChain < 0) return false;
        final String chain = CHAINS[currentChain][1];
        String url = web.getUrl();
        String raw = url == null ? null : CookieManager.getInstance().getCookie(url);
        if (raw == null || raw.trim().isEmpty()) {
            say(Sign.ACT, "", "Сначала войдите в магазин", "Нет кук — похоже, вы ещё не вошли в магазин.");
            return false;
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

            // Человеку из «Настроек» — что делать дальше, а не где лежат ключи.
            String ok = loginMode ? LOGIN_DONE_TEXT
                : CHAINS[currentChain][0] + " — вход защищённо сохранён в KeyStore";
            say(Sign.GOING, "", "вход", "Сохраняю вход в защищённое хранилище");
            post("/api/device/connected", body, "", CHAINS[currentChain][0], ok);
            say(Sign.DONE, "", CHAINS[currentChain][0], loginMode ? LOGIN_DONE_TEXT
                : CHAINS[currentChain][0] + " — вход надёжно сохранён в KeyStore на устройстве");
            return true;
        } catch (Exception e) {
            say(Sign.FAIL, "", "вход не сохранён: " + e.getMessage(), null);
            return false;
        }
    }

    // ── Режим входа (1.26): «Войти» за человека, вход узнаём сами, «Готово» ──
    private static final long LOGIN_CHECK_MS = 2000;
    private static final int LOGIN_TAPS = 4;
    private static final String LOGIN_DONE_TEXT = "✓ Вход сохранён — можно возвращаться";

    /**
     * Кука, которую сеть ставит только вошедшему (signals.AUTH_COOKIE на сервере). Имя
     * известно достоверно лишь у Магнита (замер 17.09.2026); у остальных сетей вход
     * узнаём по словам страницы — LOGIN_STATE_JS.
     */
    private static final String[][] LOGIN_COOKIES = {{"magnit", "mg_at"}};

    /** Сети без «Войти» на главной: вход открываем сразу с этой страницы (замер 09.11.2026). */
    private static final String[][] LOGIN_PAGES = {{"samokat", "https://samokat.ru/profile"}};

    /**
     * Что страница говорит о входе: in — вошёл, out — гость, vpn и check — стена сети.
     * Слова те же, что у сервера (app/shopbrowser/signals.py: STRONG_IN, ANONYMOUS, CHECK,
     * BLOCKED), и смотрим туда же — начало и конец текста, где шапка и нижняя панель.
     * Строже сервера в одном: «мои заказы» рядом с «войти» — меню гостя, а не вход;
     * не узнали вход — у человека остаётся «Я вошёл».
     */
    private static final String LOGIN_STATE_JS = "(function(){" +
        "var w=(document.body?document.body.innerText:'').toLowerCase().replace(/ё/g,'е');" +
        "var t=w.length<=3000?w:w.slice(0,1800)+' … '+w.slice(-1200);" +
        "if(!t.trim()) return '';" +
        "if(/выключите vpn|включите vpn|отключите vpn|включен vpn|не удалось загрузить сайт|" +
        "доступ к сайту[\\s\\S]{0,40}запрещ|403 error forbidden|не удалось открыть веб-страницу|" +
        "net::err_/.test(t)) return 'vpn';" +
        "if(/я не робот|подтвердите, что вы|проверка браузера|пройдите проверку|" +
        "разверните картинку|а не с ботом/.test(t)) return 'check';" +
        "var inside=/выйти|выход из аккаунта|здравствуйте,|мои заказы|мои покупки|мои купоны|" +
        "мой профиль|мои бонусы/.test(t);" +
        "var guest=/войти|вход в личный|зарегистрироваться|регистрация/.test(t);" +
        "return inside&&!guest?'in':guest?'out':'';})()";

    /**
     * Нажать за человека «Войти» на сайте сети. Видно поле телефона или пароля — форма
     * входа уже открыта, жать нечего ('form'). Иначе берём последний по документу видимый
     * элемент с подписью входа: во вложенной паре он самый глубокий (клик по обёртке до
     * кнопки не дошёл бы), а у Магнита «Войти» — в нижней панели. «Личный кабинет» —
     * только когда «Войти» нет: у Дикси он открывает меню, а не вход (замер 09.11.2026).
     * Не нашли (кнопка под ☰) — жмёт человек, подсказка та же.
     */
    private static final String LOGIN_TAP_JS = "(function(){" +
        "var f=document.querySelectorAll('input');" +
        "for(var j=0;j<f.length;j++){var x=f[j],q=x.getBoundingClientRect();" +
        "if(q.width<1||q.height<1) continue;" +
        "var m=(x.getAttribute('inputmode')||'')+' '+(x.getAttribute('autocomplete')||'');" +
        "var n=(x.placeholder||'')+' '+(x.name||'')+' '+(x.id||'')+' '+(x.getAttribute('aria-label')||'');" +
        "if(x.type==='tel'||x.type==='password'||/\\btel\\b/.test(m)||/phone|телефон/i.test(n)) return 'form';}" +
        "var want=/^(войти|вход|войти или зарегистрироваться|войти или создать профиль|войти по номеру телефона|" +
        "вход или регистрация|вход и регистрация)$/,lk=/^(вход в )?личный кабинет$/;" +
        "var all=document.querySelectorAll('a,button,[role=button],div,span'),hit=null,alt=null;" +
        "for(var i=0;i<all.length;i++){var e=all[i];" +
        "var s=(e.innerText||'').trim().replace(/\\s+/g,' ').toLowerCase().replace(/ё/g,'е');" +
        "if(s.length>40||!(want.test(s)||lk.test(s))) continue;" +
        "var r=e.getBoundingClientRect(); if(r.width<1||r.height<1) continue;" +
        "if(want.test(s)) hit=e; else alt=e;}" +
        "hit=hit||alt; if(!hit) return 'none';" +
        "hit.click(); return 'tap';})()";

    /**
     * Сайт сети не должен сам уйти «назад» на наш экран посреди входа. Самокат после отправки
     * номера зовёт router.back() (мобильная вёрстка, замер 12.11.2026): в браузере позади его же
     * страница, у нас — экран «Войти · Самокат», и поле кода из СМС пропадало через 2 с.
     * «Назад» из скрипта пропускаем, только когда позади есть запись того же сайта: перечень
     * Navigation API и index в нём считают только идущие подряд страницы этого сайта, наш экран
     * туда не входит. Кнопку «←» и «назад» телефона это не трогает: они идут мимо скрипта.
     * shortcut: без Navigation API (WebView старше 102) защиты нет; считать pushState самим,
     * если такие телефоны появятся.
     */
    private static final String LOGIN_BACK_JS = "(function(){" +
        "if(window.__kzBack) return; window.__kzBack=1;" +
        "var h=history,back=h.back,go=h.go;" +
        "function inside(n){var e=window.navigation&&navigation.currentEntry; return !e||e.index>=n;}" +
        "h.back=function(){if(inside(1)) back.call(h);};" +
        "h.go=function(n){if(!(n<0)||inside(-n)) go.call(h,n);};})()";

    /** Режим входа: раз в LOGIN_CHECK_MS спросить куку и страницу, вошёл ли человек. */
    private void checkLogin() {
        if (!loginMode || loginSaved || currentChain < 0) return;
        final int idx = currentChain;
        if (hasLoginCookie(idx)) {
            loginFound();
            return;
        }
        // Раньше нажатия «Войти»: защита должна стоять до того, как человек отправит номер.
        if (isHostOf(idx, pageUrl)) web.evaluateJavascript(LOGIN_BACK_JS, null);
        web.evaluateJavascript(LOGIN_STATE_JS, result -> {
            if (!loginMode || loginSaved || currentChain != idx) return;
            String state = result == null ? "" : result.replace("\"", "").trim();
            // Слова «выйти» на чужом сервисе входа ещё не значат, что сеть нас впустила.
            if ("in".equals(state) && isHostOf(idx, pageUrl)) {
                loginFound();
                return;
            }
            if ("vpn".equals(state)) say(Sign.ACT, "", "Сайт не пускает этот адрес — выключите VPN");
            else if ("check".equals(state)) say(Sign.ACT, "", "Пройдите проверку «я не робот»");
            else say(Sign.ACT, "", "Войдите своим номером телефона");
            // Кнопку входа сеть дорисовывает не сразу, а нажатие до готовности страницы
            // теряется (нижняя панель Магнита — через 7–20 с, замер 09.11.2026). Поэтому
            // жмём на каждой проверке, пока не откроется форма, но не больше LOGIN_TAPS раз.
            if ("out".equals(state) && loginTaps < LOGIN_TAPS && isHostOf(idx, pageUrl)) {
                web.evaluateJavascript(LOGIN_TAP_JS, r -> {
                    if ("\"form\"".equals(r)) loginTaps = LOGIN_TAPS;
                    else if ("\"tap\"".equals(r)) loginTaps++;
                });
            }
            handler.postDelayed(loginWatch, LOGIN_CHECK_MS);
        });
    }

    private boolean hasLoginCookie(int idx) {
        for (String[] c : LOGIN_COOKIES) {
            if (!c[0].equals(CHAINS[idx][1])) continue;
            String jar = CookieManager.getInstance().getCookie(CHAINS[idx][2]);
            return jar != null && (";" + jar.replace(" ", "")).contains(";" + c[1] + "=");
        }
        return false;
    }

    /** Вход есть (узнали сами или нажато «Я вошёл»): сохранить и сказать, что можно возвращаться. */
    private void loginFound() {
        if (loginSaved || currentChain < 0) return;
        loginSaved = true;              // say() красит строку зелёным, пока вход сохранён
        if (!saveLogin()) {
            loginSaved = false;
            return;
        }
        handler.removeCallbacks(loginWatch);
        chainPanel.setBackgroundColor(0xFFE8F5EC);  // --green-soft из phone.css
        loginDoneBtn.setText("Готово · к настройкам");
        loginDoneBtn.setBackgroundTintList(ColorStateList.valueOf(0xFFD4F24A));  // --lime: главное действие
    }

    /** «Готово»: в «Настройки», где сеть уже среди подключённых. */
    private void finishLogin() {
        if (currentChain < 0) return;
        openOurApp("/phone/settings?saved=connected&store=" + CHAINS[currentChain][1]);
    }

    // ── Личные акции ──
    private void saveOffers() {
        if (currentChain < 0) return;
        final String chain = CHAINS[currentChain][1];
        final String name = CHAINS[currentChain][0];
        say(Sign.GOING, "", "акции", "Собираю акции со страницы");
        web.evaluateJavascript(OFFERS_JS, result -> {
            try {
                String json = (String) new JSONTokener(result).nextValue();
                JSONArray offers = new JSONArray(json == null ? "[]" : json);
                if (offers.length() == 0) {
                    say(Sign.ACT, "", "Откройте раздел своих предложений и нажмите «Акции»",
                        "На этой странице акций не нашлось. Откройте в " + name
                        + " раздел своих предложений и нажмите ещё раз.");
                    return;
                }
                JSONObject body = new JSONObject()
                    .put("store", chain).put("logged_in", true)
                    .put("gives", new JSONArray().put("coupons"))
                    .put("coupons", offers);
                post("/api/store_accounts/sync", body, String.valueOf(offers.length()), "акции",
                     name + " — сохранено акций: " + offers.length());
            } catch (Exception e) {
                say(Sign.FAIL, "", "не разобрал страницу: " + e.getMessage(), null);
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
            say(Sign.GOING, "", "сборка", name + ": идёт сборка — раздел прочитаю после неё");
            return;
        }
        say(Sign.GOING, "", "раздел", name + ": читаю карточки раздела");
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
                say(Sign.ACT, "", "Выберите адрес в сети и нажмите «Раздел» снова",
                    name + ": сеть просит выбрать адрес — выберите его и нажмите «Раздел» снова");
            } else if (why.contains("login")) {
                say(Sign.ACT, "", "Войдите в сети и нажмите «Раздел» снова",
                    name + ": сеть просит войти — нажмите «Вход», потом «Раздел» снова");
            } else if (why.contains("captcha")) {
                say(Sign.ACT, "", "Пройдите проверку в окне и нажмите «Раздел» снова",
                    name + ": сеть показывает проверку — пройдите её в окне и нажмите «Раздел» снова");
            } else {
                say(Sign.ACT, "", "Откройте раздел сети и нажмите «Раздел»",
                    name + ": карточек на странице нет — откройте раздел сети");
            }
        });
    }

    /** Отдать собранное серверу: пачками по SECTION_LIMIT, с общим итогом в строке. */
    private void sendCards(String chain, String name, JSONArray all) {        if (all.length() == 0) {
            say(Sign.ACT, "", "Откройте раздел сети и нажмите «Раздел»",
                name + ": карточек на странице нет — откройте раздел сети");
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
                    say(Sign.FAIL, "", "раздел не ушёл — " + serverError(status, text), null);
                    return;
                }
                JSONObject a = asObject(text);
                int w = withSku + a.optInt("with_sku");
                int g = got + a.optInt("got");
                int l = learned + a.optInt("learned");
                if (next < all.length()) sendBatch(chain, name, all, next, w, g, l);
                else say(w < g || g == 0 ? Sign.PART : Sign.DONE, w + "/" + g, l > 0 ? "+" + l : "",
                         name + ": карточек " + w + " из " + g + ", новых в базе " + l);
            });
        } catch (Exception ignored) { }
    }

    /**
     * POST на наш сервер с сессией «Моей корзины» из WebView. В фоне. Принял — «готово» с числом и
     * контекстом (words — та же фраза для чтения с экрана), не принял — «не вышло» с причиной.
     */
    private void post(String path, JSONObject body, String number, String what, String words) {
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
                handler.post(() -> done(Sign.FAIL, "", "сервер недоступен: " + why, "Сервер недоступен: " + why));
                return;
            }
            final int status = code;
            final String answer = text;
            handler.post(() -> {
                if (status == 200) {
                    done(Sign.DONE, number, what, words);
                } else if (status == 401 || status == 302 || status == 303) {
                    done(Sign.FAIL, "", "Сначала войдите в «Мою корзину» своим номером.", null);
                } else {
                    String msg = "Ошибка " + status;
                    try { msg = new JSONObject(answer).optString("error", msg); } catch (Exception ignored) { }
                    done(Sign.FAIL, "", msg, null);
                }
            });
        }).start();
    }

    private void done(Sign sign, String number, String text, String words) {
        say(sign, number, text, words);
        if (currentChain >= 0) setChainButtons(true);
    }

    // ── Вспомогательные ──
    /** Строка без знака (имя и адрес страницы); bad — «не вышло» с причиной. */
    private void say(String text, boolean bad) {
        say(bad ? Sign.FAIL : Sign.NONE, "", text, text);
    }

    private void say(Sign sign, String number, String text) {
        say(sign, number, text, null);
    }

    /**
     * Строка панели сети на языке состояний (DESIGN.md): знак, число N/M и короткий контекст.
     * Слова остаются там, где человеку надо что-то сделать в чужом окне. Полная фраза для чтения
     * с экрана — words; без неё читается «состояние: число, контекст».
     */
    private void say(Sign sign, String number, String text, String words) {
        if (chainStatus == null) return;
        String line = number.isEmpty() ? text : text.isEmpty() ? number : number + " · " + text;
        SpannableString shown = new SpannableString(line);
        if (!number.isEmpty()) {
            shown.setSpan(new StyleSpan(Typeface.BOLD), 0, number.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        }
        chainStatus.setText(shown);
        chainStatus.setTextColor(sign == Sign.FAIL ? 0xFFB71C1C : sign == Sign.PART ? 0xFF8A5A00
            : sign == Sign.DONE || (loginSaved && sign != Sign.ACT) ? 0xFF17703A : Color.DKGRAY);
        chainStatus.setCompoundDrawablesRelativeWithIntrinsicBounds(new SignDrawable(sign, dp(20), animationsOn()), null, null, null);
        chainStatus.setContentDescription(words != null ? words
            : sign.words + (sign.words.isEmpty() || line.isEmpty() ? "" : ": ") + line);
    }

    /** Кольцо «идёт» — единственное движение; анимации в системе выключены — оно стоит. */
    private boolean animationsOn() {
        return Settings.Global.getFloat(getContentResolver(), Settings.Global.ANIMATOR_DURATION_SCALE, 1f) != 0f;
    }

    /** Целые рубли, узкий неразрывный пробел в разрядах и перед ₽ (DESIGN.md). */
    private static String rub(double value) {
        char thin = 0x202F;
        return String.format(Locale.US, "%,d", Math.round(value)).replace(',', thin) + thin + "₽";
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
        if (currentChain >= 0) {
            leaveChain();
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
        Log.i(TAG, "приложение на экране" + (away ? ", после ухода: сборки продолжатся" : ""));
        handler.removeCallbacks(goAway);
        // Пока приложение было не на экране, корзину в сети могли оформить в заказ: памяти корзины
        // (cartapi.js) старше этого мига веры нет, читаем заново (1.41).
        homeSince = System.currentTimeMillis();
        peekLive = SystemClock.uptimeMillis();
        for (Slot s : slots.values()) s.peekStartedAt = 0;
        peekSoon(500);
        if (!away) return;
        // Вернулись: остановленные сборки продолжаются с того, что уже легло.
        away = false;
        for (Slot s : slots.values()) {
            if (s.run == null && !s.pending.isEmpty()) soon(s);
            else if (s.run == null) rest(s);    // вернуть окну отсчёты: в фоне подогрев снят (1.29)
        }
    }

    @Override
    protected void onStop() {
        super.onStop();
        Log.i(TAG, "приложение ушло с экрана");
        // Человек отлучился (код из СМС, звонок) — минуту сборка ещё идёт, дальше стоит.
        handler.removeCallbacks(goAway);
        if (!slots.isEmpty()) handler.postDelayed(goAway, AWAY_MS);
    }

    // Ответ системного выбора файла (onShowFileChooser): отмена — null, и поле снова живое.
    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (BuildConfig.DEBUG && apkUpdater.onActivityResult(requestCode, SERVER)) return;
        if (requestCode == REQ_FILE) {
            if (fileUploadCallback != null) {
                fileUploadCallback.onReceiveValue(
                        WebChromeClient.FileChooserParams.parseResult(resultCode, data));
                fileUploadCallback = null;
            }
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
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
            if (s.run != null) Log.i(TAG, CHAINS[s.chain][0] + ": сборку остановил долгий уход приложения");
            s.changedAt = 0;     // правка, не собранная до ухода: время от касания включило бы всю отлучку
            if (s.soon != null) {
                handler.removeCallbacks(s.soon);
                s.soon = null;
            }
            if (s.keepAlive != null) {
                handler.removeCallbacks(s.keepAlive);
                s.keepAlive = null;
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

