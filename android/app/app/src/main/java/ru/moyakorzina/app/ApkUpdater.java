package ru.moyakorzina.app;

import android.app.Activity;
import android.app.PendingIntent;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.net.Uri;
import android.os.Build;
import android.os.SystemClock;
import android.provider.Settings;
import android.widget.Toast;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

import javax.net.ssl.HttpsURLConnection;

/** Загружает обновление со своего сервера прямо в системный установщик. */
final class ApkUpdater {
    private static final int INSTALL_PERMISSION = 501;
    private static final int MAX_APK_BYTES = 32 * 1024 * 1024;
    private static final String APK_PATH = "/static/moya-korzina.apk";

    private final Activity activity;
    private boolean downloading;
    private boolean waitingForPermission;

    ApkUpdater(Activity activity) {
        this.activity = activity;
    }

    void start(String server) {
        if (downloading || waitingForPermission) return;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                && !activity.getPackageManager().canRequestPackageInstalls()) {
            Intent settings = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.fromParts("package", activity.getPackageName(), null));
            try {
                waitingForPermission = true;
                activity.startActivityForResult(settings, INSTALL_PERMISSION);
            } catch (ActivityNotFoundException | SecurityException e) {
                waitingForPermission = false;
                tell("Не удалось открыть разрешение на установку");
            }
            return;
        }
        downloading = true;
        tell("Загружаем обновление");
        new Thread(() -> download(server), "apk-update").start();
    }

    boolean onActivityResult(int requestCode, String server) {
        if (requestCode != INSTALL_PERMISSION) return false;
        waitingForPermission = false;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                && activity.getPackageManager().canRequestPackageInstalls()) {
            start(server);
        } else {
            tell("Android не разрешил установку обновления");
        }
        return true;
    }

    private void download(String server) {
        PackageInstaller installer = activity.getPackageManager().getPackageInstaller();
        HttpsURLConnection connection = null;
        int sessionId = -1;
        boolean committed = false;
        try {
            connection = (HttpsURLConnection) new URL(server + APK_PATH).openConnection();
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(8000);
            connection.setReadTimeout(12000);
            if (connection.getResponseCode() != HttpURLConnection.HTTP_OK
                    || !"application/vnd.android.package-archive".equals(connection.getContentType())) {
                throw new IOException("Сервер не вернул APK");
            }
            int expectedSize = connection.getContentLength();
            if (expectedSize == 0 || expectedSize > MAX_APK_BYTES) throw new IOException("Размер APK");

            PackageInstaller.SessionParams params = new PackageInstaller.SessionParams(
                    PackageInstaller.SessionParams.MODE_FULL_INSTALL);
            params.setAppPackageName(activity.getPackageName());
            if (expectedSize > 0) params.setSize(expectedSize);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_REQUIRED);
            }
            sessionId = installer.createSession(params);
            try (PackageInstaller.Session session = installer.openSession(sessionId)) {
                stage(connection, session, expectedSize);
                Intent result = new Intent(activity, ApkUpdateResult.class);
                int flags = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ? PendingIntent.FLAG_MUTABLE : 0;
                PendingIntent callback = PendingIntent.getActivity(activity, sessionId, result, flags);
                session.commit(callback.getIntentSender());
                committed = true;
            }
        } catch (IOException | RuntimeException error) {
            activity.runOnUiThread(() -> tell("Не удалось загрузить обновление. Попробуйте ещё раз"));
        } finally {
            if (connection != null) connection.disconnect();
            if (!committed && sessionId >= 0) {
                try {
                    installer.abandonSession(sessionId);
                } catch (SecurityException | IllegalArgumentException ignored) { }
            }
            activity.runOnUiThread(() -> downloading = false);
        }
    }

    private static void stage(HttpsURLConnection connection, PackageInstaller.Session session,
                              int expectedSize) throws IOException {
        long until = SystemClock.elapsedRealtime() + 30_000;
        try (InputStream in = connection.getInputStream();
             OutputStream out = session.openWrite("base.apk", 0, expectedSize)) {
            int total = 0;
            byte[] buffer = new byte[8192];
            for (int n; (n = in.read(buffer)) != -1; ) {
                total += n;
                if (total > MAX_APK_BYTES || SystemClock.elapsedRealtime() >= until) {
                    throw new IOException("Превышен срок или размер загрузки APK");
                }
                out.write(buffer, 0, n);
            }
            if (total == 0 || (expectedSize >= 0 && total != expectedSize)) {
                throw new IOException("APK загружен не полностью");
            }
            session.fsync(out);
        }
    }

    private void tell(String text) {
        Toast.makeText(activity, text, Toast.LENGTH_LONG).show();
    }
}
