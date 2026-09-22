package ru.moyakorzina.probe;

import android.app.Activity;
import android.os.Bundle;
import android.text.method.ScrollingMovementMethod;
import android.view.Gravity;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * ПРОВЕРКА, А НЕ ПРИЛОЖЕНИЕ. Она отвечает на один вопрос, от которого зависит,
 * стоит ли вообще делать Android-приложение для «Моей корзины»:
 *
 *     отдаёт ли WebView куки входа в магазин — те самые, что помечены HttpOnly?
 *
 * ПОЧЕМУ ЭТОТ ВОПРОС ГЛАВНЫЙ. В вебе мы в него упёрлись. Открыть магазин обычной
 * вкладкой человек может, войти — тоже, но вход останется в ЕГО браузере: куки
 * входа у сетей помечены HttpOnly, и никакой скрипт страницы их не прочитает.
 * Значит наш сервер не сможет потом ни собрать цены, ни положить корзину. У
 * кабинета ФНС эта дорога работает только потому, что его ключ лежит в
 * localStorage, откуда закладка его и берёт.
 *
 * У приложения хранилище кук СВОЁ (android.webkit.CookieManager), и запрет
 * HttpOnly на него не распространяется — он про доступ из JavaScript страницы.
 * Это общеизвестно, но в «Моей корзине» за непроверенное знание уже платили:
 * оно выглядит убедительно и врёт в работе. Поэтому — проверка на живом
 * телефоне, до единой строчки настоящего приложения.
 *
 * ЧТО ОНА ПОКАЗЫВАЕТ. Два списка имён рядом: что видит приложение и что видит
 * сама страница. Разница между ними и есть HttpOnly-куки — то, ради чего всё
 * затевается. Плюс отдельной строкой: нашлась ли известная кука входа сети.
 *
 * ЗНАЧЕНИЙ КУК ЗДЕСЬ НЕ ПОКАЗЫВАЕТСЯ, только имена и длины. Значение куки входа
 * — это и есть вход: показать его на экране значит положить его на стол. Для
 * ответа на вопрос достаточно имени.
 *
 * ЧЕГО ЗДЕСЬ НЕТ И НЕ БУДЕТ. Проверка ничего никуда не отправляет — ни на наш
 * сервер, ни куда-либо ещё. Она не подменяет отпечаток браузера и не трогает
 * user-agent: WebView ходит собой, как и положено. Капчу и код из СМС вводит
 * человек — здесь он делает это родной клавиатурой телефона, ради чего вся
 * затея и начата.
 */
public class MainActivity extends Activity {

    /** Сети и то, по какой куке у них видно вход. */
    private static final String[][] CHAINS = {
            // имя, адрес, известная кука входа («?» — ещё не знаем, эта проверка и узнает)
            {"Магнит", "https://magnit.ru/", "mg_at"},
            {"Пятёрочка", "https://5ka.ru/", "?"},
            {"Самокат", "https://samokat.ru/", "?"},
            {"ВкусВилл", "https://vkusvill.ru/", "?"},
            {"Лента", "https://lenta.com/", "?"},
            {"Дикси", "https://dixy.ru/", "?"},
    };

    private WebView web;
    private TextView report;
    private String known = "mg_at";

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setFitsSystemWindows(true);

        root.addView(chainRow());

        web = new WebView(this);
        root.addView(web, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        setUpWeb();

        Button look = new Button(this);
        look.setText("Что в куках");
        look.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { tell(); }
        });
        root.addView(look);

        report = new TextView(this);
        report.setPadding(24, 16, 24, 24);
        report.setTextSize(13f);
        report.setLines(9);
        report.setMovementMethod(new ScrollingMovementMethod());
        report.setText("Войдите в магазин как обычно, потом нажмите «Что в куках».");
        root.addView(report);

        setContentView(root);
        open(0);
    }

    private HorizontalScrollView chainRow() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        for (int i = 0; i < CHAINS.length; i++) {
            final int which = i;
            Button b = new Button(this);
            b.setText(CHAINS[i][0]);
            b.setOnClickListener(new View.OnClickListener() {
                @Override public void onClick(View v) { open(which); }
            });
            row.addView(b);
        }
        HorizontalScrollView scroll = new HorizontalScrollView(this);
        scroll.addView(row);
        return scroll;
    }

    /**
     * Настройки WebView — только те, без которых витрина сети не работает вовсе.
     * User-agent НЕ трогаем: пусть сеть видит тот браузер, который к ней и пришёл.
     */
    private void setUpWeb() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);          // витрины всех шести — приложения на JS
        s.setDomStorageEnabled(true);          // без localStorage они не стартуют
        s.setDatabaseEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setSupportZoom(true);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);

        CookieManager jar = CookieManager.getInstance();
        jar.setAcceptCookie(true);
        jar.setAcceptThirdPartyCookies(web, true);   // вход у сетей часто идёт через свой поддомен

        // Свой WebViewClient, иначе переход по ссылке уедет в браузер телефона и
        // вход случится ТАМ — то есть мимо всей проверки.
        web.setWebViewClient(new WebViewClient());
    }

    private void open(int which) {
        known = CHAINS[which][2];
        report.setText("Открываю " + CHAINS[which][0] + ". Войдите как обычно, потом нажмите «Что в куках».");
        web.loadUrl(CHAINS[which][1]);
    }

    /** Имена кук из строки вида «a=1; b=2». Значения не берём. */
    private static Set<String> names(String jar) {
        Set<String> got = new LinkedHashSet<>();
        if (jar == null) {
            return got;
        }
        for (String piece : jar.split(";")) {
            int equals = piece.indexOf('=');
            String name = (equals > 0 ? piece.substring(0, equals) : piece).trim();
            if (!name.isEmpty()) {
                got.add(name);
            }
        }
        return got;
    }

    /** Сколько знаков в значении куки — по этому видно, пустая она или настоящая. */
    private static int lengthOf(String jar, String name) {
        if (jar == null) {
            return 0;
        }
        for (String piece : jar.split(";")) {
            String bit = piece.trim();
            if (bit.startsWith(name + "=")) {
                return bit.length() - name.length() - 1;
            }
        }
        return 0;
    }

    private void tell() {
        final String url = web.getUrl();
        if (url == null) {
            report.setText("Страница ещё не открыта.");
            return;
        }
        final String mine = CookieManager.getInstance().getCookie(url);

        // Что видит сама страница. Разница с нашим списком и есть HttpOnly.
        web.evaluateJavascript("document.cookie", new android.webkit.ValueCallback<String>() {
            @Override public void onReceiveValue(String value) {
                String page = value == null ? "" : value;
                if (page.startsWith("\"") && page.endsWith("\"") && page.length() >= 2) {
                    page = page.substring(1, page.length() - 1).replace("\\\"", "\"");
                }
                show(url, mine, page);
            }
        });
    }

    private void show(String url, String mine, String page) {
        Set<String> ours = names(mine);
        Set<String> theirs = names(page);

        List<String> onlyOurs = new ArrayList<>();
        for (String name : ours) {
            if (!theirs.contains(name)) {
                onlyOurs.add(name);
            }
        }

        StringBuilder out = new StringBuilder();
        out.append(url).append("\n\n");
        out.append("Приложение видит кук: ").append(ours.size()).append('\n');
        out.append("Страница видит кук: ").append(theirs.size()).append('\n');
        out.append("ТОЛЬКО приложению (HttpOnly): ").append(onlyOurs.size()).append('\n');
        if (!onlyOurs.isEmpty()) {
            out.append("  ").append(String.join(", ", onlyOurs)).append('\n');
        }

        out.append('\n');
        if ("?".equals(known)) {
            out.append("Кука входа этой сети нам ещё неизвестна.\n");
            out.append("Все имена: ").append(String.join(", ", ours)).append('\n');
            out.append("Войдите и нажмите ещё раз — новая в списке и есть искомая.\n");
        } else if (ours.contains(known)) {
            out.append("Кука входа «").append(known).append("» НАЙДЕНА, длина значения ")
               .append(lengthOf(mine, known)).append(".\n");
            out.append(theirs.contains(known)
                    ? "Страница её тоже видит — значит она не HttpOnly.\n"
                    : "Странице она не видна: HttpOnly. Ровно то, ради чего и затевалось.\n");
        } else {
            out.append("Куки входа «").append(known).append("» пока нет — похоже, вход ещё не пройден.\n");
            out.append("Все имена: ").append(String.join(", ", ours)).append('\n');
        }

        report.setText(out.toString());
        report.setGravity(Gravity.NO_GRAVITY);
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
