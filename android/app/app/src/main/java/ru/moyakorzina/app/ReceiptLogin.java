package ru.moyakorzina.app;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

public final class ReceiptLogin extends Activity {
    private final Handler handler = new Handler();
    private WebView web;
    private String owner;
    private boolean isolated, sawLogin, checking, explained;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        owner = getIntent().getStringExtra("owner");
        if (owner == null || !owner.matches("[0-9]{10,15}")) { finish(); return; }
        if (!ReceiptSync.bound(this, owner)) ReceiptSync.logout(this);
        web = new WebView(this);
        isolated = ReceiptSync.prepare(web, owner);
        web.setWebViewClient(new WebViewClient());
        setContentView(web);
        web.loadUrl(ReceiptSync.ORIGIN + "/");
        handler.post(poll);
    }

    private final Runnable poll = new Runnable() {
        @Override public void run() {
            if (isFinishing() || isDestroyed()) return;
            handler.postDelayed(this, 600);
            if (checking || !ReceiptSync.official(web.getUrl())) return;
            web.evaluateJavascript(ReceiptSync.SCREEN, value -> {
                if (isFinishing() || isDestroyed() || checking || !ReceiptSync.official(web.getUrl())) return;
                if ("\"login\"".equals(value)) sawLogin = true;
                if (!"\"receipts\"".equals(value)) return;
                if (!isolated && !sawLogin && !owner.equals(ReceiptSync.prefs(ReceiptLogin.this).getString("legacyOwner", ""))) {
                    if (!explained) Toast.makeText(ReceiptLogin.this,
                        "Выйдите из кабинета ФНС и войдите своим номером, чтобы связать его с этой корзиной.", Toast.LENGTH_LONG).show();
                    explained = true;
                    return;
                }
                checking = true;
                ReceiptSync.account(ReceiptLogin.this, (current, session) -> {
                    if (isFinishing() || isDestroyed()) return;
                    if (owner.equals(current)) {
                        ReceiptSync.connect(ReceiptLogin.this, owner, isolated);
                        finish();
                    } else checking = false;
                });
            });
        }
    };

    @Override public void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
