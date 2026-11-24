package ru.moyakorzina.app;

import android.app.job.JobParameters;
import android.app.job.JobService;
import android.os.Handler;
import android.util.Base64;
import android.webkit.*;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.UUID;

public final class ReceiptJob extends JobService {
    private final Handler handler = new Handler();
    private JobParameters job;
    private WebView web;
    private WebMessagePort port;
    private String owner, session;
    private long started, sliceStart, lastStep, sampleMillis, reportedAt;
    private final Runnable stalled = () -> finish(true);
    private void watchProgress() {
        handler.removeCallbacks(stalled);
        handler.postDelayed(stalled, 90_000);
    }
    private void report(String phase, boolean force) {
        long now = android.os.SystemClock.elapsedRealtime();
        if (session == null || !ReceiptSync.bound(this, owner) || (!force && now - reportedAt < 30_000)) return;
        reportedAt = now;
        try {
            JSONObject state = new JSONObject().put("phase", phase)
                .put("position", ReceiptSync.prefs(this).getInt("position", 0))
                .put("total", ReceiptSync.prefs(this).getInt("total", 0));
            MainActivity.requestApp(session, "/api/fns/device-progress", state, (status, body) -> {});
        } catch (org.json.JSONException ignored) {}
    }
    private int priorPosition, samples;
    private boolean advancedCursor;
    private static final long SLICE_MS = 180_000;

    @Override public boolean onStartJob(JobParameters params) {
        if (job != null) return false;
        owner = ReceiptSync.prefs(this).getString("owner", "");
        if (owner.isEmpty()) return false;
        job = params;
        ReceiptSync.phase(this, owner, "running");
        started = android.os.SystemClock.elapsedRealtime();
        lastStep = 0; sampleMillis = 0; priorPosition = 0; samples = 0; advancedCursor = false;
        sliceStart = 0; reportedAt = 0; watchProgress();
        ReceiptSync.account(this, (current, cookie) -> {
            if (job != params) return;
            if (!owner.equals(current) || !ReceiptSync.bound(this, owner)) { finish(current.isEmpty(), "account"); return; }
            session = cookie;
            report("running", true);
            web = new WebView(this);
            ReceiptSync.prepare(web, owner);
            int width = (int)(360 * getResources().getDisplayMetrics().density);
            int height = width * 2;
            web.measure(android.view.View.MeasureSpec.makeMeasureSpec(width, android.view.View.MeasureSpec.EXACTLY),
                        android.view.View.MeasureSpec.makeMeasureSpec(height, android.view.View.MeasureSpec.EXACTLY));
            web.layout(0, 0, width, height);
            web.setWebViewClient(new WebViewClient() {
                @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                    if (r.isForMainFrame() && !ReceiptSync.official(r.getUrl().toString())) {
                        if (job == params) finish(false);
                        return true;
                    }
                    return false;
                }
                @Override public void onReceivedHttpError(WebView v, WebResourceRequest r, WebResourceResponse res) {
                    int status = res.getStatusCode();
                    if (job == params && ReceiptSync.official(r.getUrl().toString())
                            && (status == 401 || status == 403)) {
                        if (status == 401) expired(); else finish(false, "denied");
                    }
                }
                @Override public void onPageStarted(WebView v, String url, android.graphics.Bitmap icon) {
                    if (job == params && (port != null || !ReceiptSync.official(url))) finish(false);
                }
            });
            web.loadUrl(ReceiptSync.ORIGIN + "/");
            handler.post(poll);
        });
        return true;
    }

    private final Runnable poll = new Runnable() {
        @Override public void run() {
            WebView view = web;
            if (view == null || port != null) return;
            if (android.os.SystemClock.elapsedRealtime() - started > 45_000) { finish(true, "cabinet"); return; }
            handler.postDelayed(this, 800);
            if (!ReceiptSync.official(view.getUrl())) return;
            view.evaluateJavascript(ReceiptSync.SCREEN, v -> {
                if (view != web || port != null || !ReceiptSync.official(view.getUrl())) return;
                if ("\"login\"".equals(v)) expired();
                if ("\"receipts\"".equals(v)) startReading(view);
            });
        }
    };

    private void expired() {
        if (ReceiptSync.bound(this, owner)) ReceiptSync.logout(this);
        finish(false);
    }

    private void startReading(WebView view) {
        String script;
        try (InputStream in = getAssets().open("fns-screen.js"); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
            script = out.toString("UTF-8");
        } catch (Exception e) { finish(true); return; }
        WebMessagePort[] ports = view.createWebMessageChannel();
        port = ports[0];
        String nonce = UUID.randomUUID().toString();
        String cursor = ReceiptSync.prefs(this).getString("contents-cursor-" + owner, "{}");
        // Versions before 1.77 could mark a partial list complete without recording its size.
        try {
            JSONObject previous = new JSONObject(cursor);
            if (previous.optBoolean("complete") && !previous.has("total")) cursor = "{}";
        } catch (org.json.JSONException e) { cursor = "{}"; }
        ports[0].setWebMessageCallback(new WebMessagePort.WebMessageCallback() {
            @Override public void onMessage(WebMessagePort sender, WebMessage message) {
                if (web != view || sender != port) return;
                if (!ReceiptSync.bound(ReceiptJob.this, owner) || !ReceiptSync.official(view.getUrl())) { finish(false); return; }
                try {
                    JSONObject msg = new JSONObject(message.getData());
                    watchProgress();
                    switch (msg.getString("type")) {
                        case "progress":
                            int position = msg.getInt("position");
                            int total = msg.getInt("total");
                            long now = android.os.SystemClock.elapsedRealtime();
                            if (msg.optBoolean("resuming")) {
                                ReceiptSync.phase(ReceiptJob.this, owner, "resuming");
                                report("resuming", false);
                                sender.postMessage(new WebMessage("{}"));
                                return;
                            }
                            if (sliceStart == 0) sliceStart = now;
                            if (lastStep > 0 && position == priorPosition + 1) {
                                samples++; sampleMillis += now - lastStep;
                            }
                            priorPosition = position;
                            lastStep = now;
                            ReceiptSync.progress(ReceiptJob.this, owner, position, total, samples >= 6 ? sampleMillis / samples : 0);
                            report("running", false);
                            sender.postMessage(new WebMessage("{}"));
                            return;
                        case "save":
                            JSONObject bundle = new JSONObject().put("source", "lkdr").put("receipts",
                                new org.json.JSONArray().put(msg.getJSONObject("entry")));
                            byte[] json = bundle.toString().getBytes(StandardCharsets.UTF_8);
                            if (json.length > 2_000_000) { finish(true); return; }
                            JSONObject body = new JSONObject().put("content", Base64.encodeToString(json, Base64.NO_WRAP));
                            MainActivity.requestApp(session, "/api/receipts/file", body, (status, answer) -> {
                                if (sender != port) return;
                                try {
                                    JSONObject result = new JSONObject(answer);
                                    if (status == 400) {
                                        sender.postMessage(new WebMessage("{\"failed\":1}"));
                                        return;
                                    }
                                    if (status != 200 || !result.optBoolean("ok")) { finish(true, "upload"); return; }
                                    if (result.has("storedCount"))
                                        ReceiptSync.prefs(ReceiptJob.this).edit().putInt("storedCount", result.getInt("storedCount")).apply();
                                    if (result.optInt("receipts") + result.optInt("other") > 0)
                                        ReceiptSync.prefs(ReceiptJob.this).edit().putLong("updated", System.currentTimeMillis()).apply();
                                    sender.postMessage(new WebMessage(result.toString()));
                                } catch (Exception e) { finish(true); }
                            });
                            return;
                        case "cursor":
                            JSONObject checkpoint = msg.getJSONObject("state");
                            String name = "contents-cursor-" + owner;
                            if (checkpoint.optInt("offset") > 0 && !checkpoint.toString().equals(
                                    ReceiptSync.prefs(ReceiptJob.this).getString(name, ""))) advancedCursor = true;
                            long previousSweep = ReceiptSync.prefs(ReceiptJob.this).getLong("contents-sweep-" + owner, 0);
                            if (checkpoint.optBoolean("complete") && previousSweep == 0)
                                ReceiptSync.prefs(ReceiptJob.this).edit().putLong("contents-sweep-" + owner, System.currentTimeMillis()).apply();
                            if (checkpoint.optBoolean("complete") && previousSweep > 0 &&
                                System.currentTimeMillis() - previousSweep > 86400_000L) {
                                ReceiptSync.prefs(ReceiptJob.this).edit().remove(name)
                                    .putLong("contents-sweep-" + owner, System.currentTimeMillis()).apply();
                            } else ReceiptSync.prefs(ReceiptJob.this).edit().putString(name, checkpoint.toString()).apply();
                            if (advancedCursor && checkpoint.optInt("offset") > 0 &&
                                sliceStart > 0 && android.os.SystemClock.elapsedRealtime() - sliceStart >= SLICE_MS) {
                                int next = job.getJobId() == ReceiptSync.FIRST ? ReceiptSync.NEXT : ReceiptSync.FIRST;
                                int queued = ReceiptSync.enqueue(ReceiptJob.this, next);
                                finish(queued != android.app.job.JobScheduler.RESULT_SUCCESS, "waiting");
                                return;
                            }
                            sender.postMessage(new WebMessage("{}"));
                            return;
                        case "done":
                            boolean partial = msg.optBoolean("retry");
                            finish(partial, partial ? "partial" : "done");
                            return;
                        default: finish(true);
                    }
                } catch (Exception e) { finish(true); }
            }
        });
        String js = "(()=>{const origin=" + JSONObject.quote(ReceiptSync.ORIGIN) + ";if(location.origin!==origin)return false;"
            + "const start=e=>{if(location.origin!==origin||e.data!==" + JSONObject.quote(nonce)
            + "||!e.ports[0])return;removeEventListener('message',start);const p=e.ports[0];"
            + "const send=x=>new Promise(resolve=>{p.onmessage=e=>resolve(JSON.parse(e.data));p.postMessage(JSON.stringify(x))});"
            + "const state=JSON.parse(" + JSONObject.quote(cursor) + ");state.checkpoint=state=>send({type:'cursor',state});"
            + "(" + script + ")(entry=>send({type:'save',entry}),(position,visible,end,stage)=>send({type:'progress',position,total:end?visible:0,resuming:stage==='resume'}),()=>location.origin===origin,state)"
            + ".then(n=>p.postMessage(JSON.stringify({type:'done',retry:n>0})),()=>p.postMessage(JSON.stringify({type:'done',retry:true})));"
            + "};addEventListener('message',start);return true})()";
        view.evaluateJavascript(js, ok -> {
            if (web != view || port != ports[0]) return;
            if (!"true".equals(ok) || !ReceiptSync.official(view.getUrl())) { finish(true); return; }
            view.postWebMessage(new WebMessage(nonce, new WebMessagePort[]{ports[1]}), android.net.Uri.parse(ReceiptSync.ORIGIN));
        });
    }

    @Override public boolean onStopJob(JobParameters params) {
        if (job != null && params.getJobId() == job.getJobId()) {
            ReceiptSync.phase(this, owner, "waiting");
            report("waiting", true);
            close();
        }
        return true;
    }

    private void finish(boolean retry) {
        finish(retry, "retry");
    }

    private void finish(boolean retry, String phase) {
        JobParameters current = job;
        if (current == null) return;
        ReceiptSync.phase(this, owner, phase);
        report(phase, true);
        close();
        jobFinished(current, retry);
    }

    private void close() {
        job = null;
        handler.removeCallbacksAndMessages(null);
        if (port != null) { port.close(); port = null; }
        WebView view = web;
        web = null;
        if (view != null) { view.stopLoading(); view.destroy(); }
        session = null;
    }
}
