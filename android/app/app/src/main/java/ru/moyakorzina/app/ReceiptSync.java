package ru.moyakorzina.app;

import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;
import android.webkit.CookieManager;
import android.webkit.WebView;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;
import org.json.JSONObject;

final class ReceiptSync {
    static final String ORIGIN = "https://lkdr.nalog.ru";
    static final int FIRST = 7510, PERIODIC = 7511, NEXT = 7512;

    static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences("receipt-sync", Context.MODE_PRIVATE);
    }

    static boolean official(String url) {
        if (url == null) return false;
        Uri u = Uri.parse(url), expected = Uri.parse(ORIGIN);
        return expected.getScheme().equals(u.getScheme()) && expected.getEncodedAuthority().equals(u.getEncodedAuthority());
    }

    static boolean prepare(WebView view, String owner) {
        boolean isolated = WebViewFeature.isFeatureSupported(WebViewFeature.MULTI_PROFILE);
        if (isolated) WebViewCompat.setProfile(view, "fns-" + owner);
        view.getSettings().setJavaScriptEnabled(true);
        view.getSettings().setDomStorageEnabled(true);
        view.getSettings().setAllowFileAccess(false);
        view.getSettings().setAllowContentAccess(false);
        return isolated;
    }

    // Inspect only top-level UI shown by the cabinet. An empty signed-in list is a valid account.
    static final String SCREEN = "(() => {const visible=e=>e.getClientRects().length>0;"
        + "const inputs=[...document.querySelectorAll('input')].filter(visible);"
        + "if(inputs.some(e=>e.type==='tel'||e.autocomplete==='one-time-code'||/номер телефона/i.test(e.placeholder)))return 'login';"
        + "if(inputs.some(e=>/поиск по продавцу/i.test(e.placeholder))"
        + "||[...document.querySelectorAll('div,span')].some(e=>!e.childElementCount&&visible(e)&&/^ИНН\\s+\\d{10,12}$/.test(e.textContent.trim())))return 'receipts';"
        + "return '';})()";

    interface Account { void ready(String owner, String session); }

    static void account(Context c, Account cb) {
        String session = CookieManager.getInstance().getCookie(MainActivity.SERVER);
        if (session == null || session.isEmpty()) { cb.ready("", ""); return; }
        MainActivity.requestApp(session, "/api/auth/me", null, (status, text) -> {
            String owner = "";
            try {
                JSONObject me = new JSONObject(text);
                if (status == 200 && me.optBoolean("authenticated") && !me.optBoolean("isDemo"))
                    owner = me.optString("phone");
            } catch (Exception ignored) {}
            cb.ready(owner.matches("[0-9]{10,15}") ? owner : "", session);
        });
    }

    static boolean bound(Context c, String owner) {
        return !owner.isEmpty() && owner.equals(prefs(c).getString("owner", ""));
    }

    static String status(Context c, String owner) {
        JSONObject state = new JSONObject();
        try {
            state.put("connected", bound(c, owner));
            if (bound(c, owner)) {
                state.put("updated", Long.toString(prefs(c).getLong("updated", 0)));
                String phase = prefs(c).getString("phase", "waiting");
                long attempt = prefs(c).getLong("attempt", 0);
                if ((phase.equals("running") || phase.equals("resuming")) && System.currentTimeMillis() - attempt > 5 * 60_000L) phase = "waiting";
                state.put("phase", phase);
                state.put("storedCount", prefs(c).getInt("storedCount", 0));
                state.put("position", prefs(c).getInt("position", 0));
                state.put("total", prefs(c).getInt("total", 0));
                state.put("advanced", prefs(c).getLong("advanced", 0));
                state.put("millisPerReceipt", prefs(c).getLong("millisPerReceipt", 0));
                state.put("checked", prefs(c).getLong("checked", 0));
            }
        } catch (Exception ignored) {}
        return state.toString();
    }

    static void progress(Context c, String owner, int position, int total, long average) {
        if (!bound(c, owner)) return;
        SharedPreferences.Editor edit = prefs(c).edit().putString("phase", "running").putLong("attempt", System.currentTimeMillis()).putInt("position", position).putInt("total", total)
            .putLong("advanced", System.currentTimeMillis());
        if (average > 0) edit.putLong("millisPerReceipt", average);
        edit.apply();
    }

    static void phase(Context c, String owner, String phase) {
        if (!bound(c, owner)) return;
        SharedPreferences.Editor edit = prefs(c).edit().putString("phase", phase);
        if (phase.equals("running") || phase.equals("resuming")) edit.putLong("attempt", System.currentTimeMillis());
        if (phase.equals("done")) edit.putLong("checked", System.currentTimeMillis());
        edit.apply();
    }

    static void resume(Context c) {
        String owner = prefs(c).getString("owner", "");
        if (owner.isEmpty()) return;
        JobScheduler jobs = c.getSystemService(JobScheduler.class);
        if (!prefs(c).getBoolean("archiveJob", false) || jobs.getPendingJob(PERIODIC) == null) {
            schedule(c);
        } else if (System.currentTimeMillis() - prefs(c).getLong("attempt", 0) >= 60 * 60_000L
                && jobs.getPendingJob(FIRST) == null && jobs.getPendingJob(NEXT) == null) {
            enqueue(c, FIRST);
        }
    }

    static void connect(Context c, String owner, boolean isolated) {
        SharedPreferences.Editor edit = prefs(c).edit().putString("owner", owner)
            .putString("phase", "waiting").remove("checked").remove("updated").remove("storedCount")
            .remove("position").remove("total").remove("advanced").remove("millisPerReceipt");
        if (!isolated) edit.putString("legacyOwner", owner);
        edit.apply();
        schedule(c);
    }

    private static void schedule(Context c) {
        JobScheduler jobs = c.getSystemService(JobScheduler.class);
        ComponentName component = new ComponentName(c, ReceiptJob.class);
        jobs.schedule(new JobInfo.Builder(PERIODIC, component).setPersisted(true)
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setPeriodic(60 * 60_000L).build());
        int result = enqueue(c, FIRST);

        prefs(c).edit().putBoolean("archiveJob", result == JobScheduler.RESULT_SUCCESS).apply();
    }

    static int enqueue(Context c, int id) {
        return c.getSystemService(JobScheduler.class).schedule(new JobInfo.Builder(id, new ComponentName(c, ReceiptJob.class))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setPersisted(true)
            .setMinimumLatency(0).setOverrideDeadline(1500).build());
    }

    static void logout(Context c) {
        prefs(c).edit().remove("owner").remove("archiveJob").remove("phase").remove("checked").apply();
        JobScheduler jobs = c.getSystemService(JobScheduler.class);
        prefs(c).edit().remove("position").remove("total").remove("advanced").remove("millisPerReceipt").apply();
        jobs.cancel(NEXT);
        jobs.cancel(FIRST);
        jobs.cancel(PERIODIC);
    }
}
