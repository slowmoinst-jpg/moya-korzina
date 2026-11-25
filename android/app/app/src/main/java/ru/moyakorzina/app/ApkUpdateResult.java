package ru.moyakorzina.app;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.os.Build;
import android.os.Bundle;
import android.widget.Toast;

/** Системный установщик передаёт сюда запрос на подтверждение и результат. */
public final class ApkUpdateResult extends Activity {
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        Intent result = getIntent();
        int status = result.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
        if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            Intent confirm = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                    ? result.getParcelableExtra(Intent.EXTRA_INTENT, Intent.class)
                    : result.getParcelableExtra(Intent.EXTRA_INTENT);
            if (confirm != null) startActivity(confirm);
        } else if (status != PackageInstaller.STATUS_SUCCESS && status != PackageInstaller.STATUS_FAILURE_ABORTED) {
            Toast.makeText(this, "Android не установил обновление", Toast.LENGTH_LONG).show();
        }
        finish();
    }
}
