package ru.moyakorzina.app;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.HorizontalScrollView;
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

/**
 * Моя корзина — Android-приложение: телефон работает в магазинах от имени владельца.
 *
 * ЗАЧЕМ ОНО НУЖНО. Сервер не может войти в аккаунты магазинов: iframe запрещён у всех
 * сетей, серверный адрес заблокирован у Пятёрочки, Самоката, Ленты и Дикси, у Магнита
 * серверный вход ловит SmartCaptcha. Телефон владельца — обычный покупатель, и магазины
 * пускают его как любого покупателя.
 *
 * ЧТО ОНО ДЕЛАЕТ.
 *   «Моя корзина» — открывает наше приложение. Один раз входите своим номером: дальше
 *                   телефон ходит на сервер с этой сессией, и ни номер, ни секрет в
 *                   коде приложения не хранятся.
 *   Кнопка сети   — открывает сайт магазина. Входите как обычно: номер, код из СМС,
 *                   капча — сами, родной клавиатурой.
 *   «Сохранить вход»   — куки входа уезжают на сервер ТЕЛОМ запроса POST /api/handoff
 *                   (не в адресе: адрес оседает в журналах).
 *   «Сохранить акции»  — откройте в магазине страницу своих предложений («Мои скидки»,
 *                   «Персональные предложения»): приложение соберёт с неё карточки
 *                   акций и отправит на сервер (POST /api/store_accounts/sync). Сервер
 *                   учтёт их в цене — app/personal.py.
 *
 * АДРЕС СЕРВЕРА задаётся при сборке (-PkorzinaServer=https://… или переменная
 * KORZINA_SERVER), в коде его нет.
 *
 * ЧЕГО ЗДЕСЬ НЕТ. Пароли и коды из СМС не перехватываются и не хранятся. Значения кук
 * не показываются. Кнопку «заказать» приложение не нажимает никогда.
 */
public class MainActivity extends Activity {

    /** Название, код сети на сервере, стартовая страница. */
    private static final String[][] CHAINS = {
        {"Магнит",      "magnit",      "https://magnit.ru/"},
        {"Пятёрочка",   "pyaterochka", "https://5ka.ru/"},
        {"Самокат",     "samokat",     "https://samokat.ru/"},
        {"Лента",       "lenta",       "https://lenta.com/"},
        {"ВкусВилл",    "vkusvill",    "https://vkusvill.ru/"},
        {"Дикси",       "dixy",        "https://dixy.ru/"},
    };

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

    private WebView web;
    private TextView status;
    private ProgressBar progress;
    private Button saveLoginBtn;
    private Button saveOffersBtn;
    private int currentChain = -1;
    private final Handler handler = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);
        root.setFitsSystemWindows(true);

        root.addView(chainRow());

        status = new TextView(this);
        status.setPadding(dp(16), dp(8), dp(16), dp(8));
        status.setTextSize(14f);
        status.setTextColor(Color.DKGRAY);
        root.addView(status);

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        progress.setVisibility(View.GONE);
        root.addView(progress, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(3)));

        web = new WebView(this);
        root.addView(web, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        setUpWeb();

        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER);
        bar.setPadding(dp(8), dp(8), dp(8), dp(8));
        bar.setBackgroundColor(0xFFF5F5F5);

        saveLoginBtn = new Button(this);
        saveLoginBtn.setText("Сохранить вход");
        saveLoginBtn.setEnabled(false);
        saveLoginBtn.setOnClickListener(v -> saveLogin());
        bar.addView(saveLoginBtn, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        saveOffersBtn = new Button(this);
        saveOffersBtn.setText("Сохранить акции");
        saveOffersBtn.setEnabled(false);
        saveOffersBtn.setOnClickListener(v -> saveOffers());
        bar.addView(saveOffersBtn, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        root.addView(bar);
        setContentView(root);

        if (SERVER.isEmpty()) {
            say("Адрес сервера не задан при сборке (-PkorzinaServer).", true);
        } else {
            openOurApp();
        }
    }

    private HorizontalScrollView chainRow() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(dp(4), dp(4), dp(4), 0);

        Button ours = new Button(this);
        ours.setText("Моя корзина");
        ours.setTextSize(13f);
        ours.setOnClickListener(v -> openOurApp());
        row.addView(ours);

        for (int i = 0; i < CHAINS.length; i++) {
            final int idx = i;
            Button b = new Button(this);
            b.setText(CHAINS[i][0]);
            b.setTextSize(13f);
            b.setOnClickListener(v -> openChain(idx));
            row.addView(b);
        }
        HorizontalScrollView scroll = new HorizontalScrollView(this);
        scroll.setHorizontalScrollBarEnabled(false);
        scroll.addView(row);
        return scroll;
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

        CookieManager jar = CookieManager.getInstance();
        jar.setAcceptCookie(true);
        jar.setAcceptThirdPartyCookies(web, true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                boolean inChain = currentChain >= 0;
                saveLoginBtn.setEnabled(inChain);
                saveOffersBtn.setEnabled(inChain);
                if (inChain) say(CHAINS[currentChain][0] + " — " + shortenUrl(url), false);
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

    private void openOurApp() {
        currentChain = -1;
        saveLoginBtn.setEnabled(false);
        saveOffersBtn.setEnabled(false);
        say("Моя корзина", false);
        web.loadUrl(SERVER + "/");
    }

    private void openChain(int idx) {
        currentChain = idx;
        saveLoginBtn.setEnabled(false);
        saveOffersBtn.setEnabled(false);
        say("Открываю " + CHAINS[idx][0] + "…", false);
        web.loadUrl(CHAINS[idx][2]);
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
        saveLoginBtn.setEnabled(false);
        saveOffersBtn.setEnabled(false);
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
                    done("Сначала войдите в «Мою корзину» — кнопка слева вверху.", true);
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
        boolean inChain = currentChain >= 0;
        saveLoginBtn.setEnabled(inChain);
        saveOffersBtn.setEnabled(inChain);
    }

    // ── Вспомогательные ──
    private void say(String text, boolean bad) {
        status.setText(text);
        status.setTextColor(bad ? 0xFFB71C1C : Color.DKGRAY);
    }

    private static byte[] readAll(InputStream is) throws java.io.IOException {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = is.read(buf)) != -1) out.write(buf, 0, n);
        return out.toByteArray();
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
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }
}
