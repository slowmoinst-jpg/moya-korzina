package ru.moyakorzina.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * Зашифрованное хранилище токенов и сессий магазинов на устройстве владельца (KeyStore).
 *
 * АРХИТЕКТУРА 2.0 (Концепция 2.0 от 17.10.2026):
 * Токены, куки входа и сессии магазинов хранятся ИСКЛЮЧИТЕЛЬНО на устройстве в защищённом
 * аппаратном хранилище Android KeyStore (TEE / StrongBox) с шифрованием AES-256-GCM.
 * Секреты НЕ передаются на сервер — сервер остаётся мозгом (каталог, мэтчинг, расчёт наряда),
 * а все приватные запросы (add_to_cart, списание баллов, чтение корзины) исполняются
 * с устройства владельца под его домашним/мобильным IP.
 */
public class SecureVault {
    private static final String TAG = "SecureVault";
    private static final String ANDROID_KEYSTORE = "AndroidKeyStore";
    private static final String KEY_ALIAS = "korzina_tokens_master_key";
    private static final String TRANSFORMATION = "AES/GCM/NoPadding";
    private static final int GCM_TAG_LENGTH = 128;
    private static final int GCM_IV_LENGTH = 12;

    private final SharedPreferences prefs;

    public SecureVault(Context context) {
        this.prefs = context.getSharedPreferences("korzina-secure-vault", Context.MODE_PRIVATE);
        ensureMasterKey();
    }

    private synchronized void ensureMasterKey() {
        try {
            KeyStore keyStore = KeyStore.getInstance(ANDROID_KEYSTORE);
            keyStore.load(null);
            if (!keyStore.containsAlias(KEY_ALIAS)) {
                KeyGenerator keyGenerator = KeyGenerator.getInstance(
                    KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE
                );
                KeyGenParameterSpec spec = new KeyGenParameterSpec.Builder(
                    KEY_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build();
                keyGenerator.init(spec);
                keyGenerator.generateKey();
                Log.i(TAG, "Master key generated in AndroidKeyStore");
            }
        } catch (Exception e) {
            Log.e(TAG, "Failed to initialize AndroidKeyStore master key: " + e.getMessage(), e);
        }
    }

    private SecretKey getMasterKey() throws Exception {
        KeyStore keyStore = KeyStore.getInstance(ANDROID_KEYSTORE);
        keyStore.load(null);
        KeyStore.Entry entry = keyStore.getEntry(KEY_ALIAS, null);
        if (!(entry instanceof KeyStore.SecretKeyEntry)) {
            throw new IllegalStateException("KeyStore entry is not a SecretKeyEntry");
        }
        return ((KeyStore.SecretKeyEntry) entry).getSecretKey();
    }

    public synchronized void putEncrypted(String key, String plaintext) {
        if (plaintext == null) {
            prefs.edit().remove(key).apply();
            return;
        }
        try {
            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.ENCRYPT_MODE, getMasterKey());
            byte[] iv = cipher.getIV();
            byte[] ciphertext = cipher.doFinal(plaintext.getBytes(StandardCharsets.UTF_8));

            byte[] combined = new byte[iv.length + ciphertext.length];
            System.arraycopy(iv, 0, combined, 0, iv.length);
            System.arraycopy(ciphertext, 0, combined, iv.length, ciphertext.length);

            String encoded = Base64.encodeToString(combined, Base64.NO_WRAP);
            prefs.edit().putString(key, encoded).apply();
        } catch (Exception e) {
            Log.e(TAG, "Failed to encrypt key=" + key + ": " + e.getMessage(), e);
        }
    }

    public synchronized String getDecrypted(String key, String fallback) {
        String encoded = prefs.getString(key, null);
        if (encoded == null) return fallback;
        try {
            byte[] combined = Base64.decode(encoded, Base64.NO_WRAP);
            if (combined.length <= GCM_IV_LENGTH) return fallback;

            byte[] iv = new byte[GCM_IV_LENGTH];
            System.arraycopy(combined, 0, iv, 0, GCM_IV_LENGTH);

            byte[] ciphertext = new byte[combined.length - GCM_IV_LENGTH];
            System.arraycopy(combined, GCM_IV_LENGTH, ciphertext, 0, ciphertext.length);

            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            GCMParameterSpec spec = new GCMParameterSpec(GCM_TAG_LENGTH, iv);
            cipher.init(Cipher.DECRYPT_MODE, getMasterKey(), spec);

            byte[] plaintext = cipher.doFinal(ciphertext);
            return new String(plaintext, StandardCharsets.UTF_8);
        } catch (Exception e) {
            Log.e(TAG, "Failed to decrypt key=" + key + ": " + e.getMessage(), e);
            return fallback;
        }
    }

    // ── Сессии магазинов ──

    public void saveSession(String chain, JSONObject session) {
        if (chain == null) return;
        String key = "session." + chain.trim().toLowerCase();
        putEncrypted(key, session != null ? session.toString() : null);
    }

    public JSONObject getSession(String chain) {
        if (chain == null) return null;
        String key = "session." + chain.trim().toLowerCase();
        String jsonStr = getDecrypted(key, null);
        if (jsonStr == null) return null;
        try {
            return new JSONObject(jsonStr);
        } catch (Exception e) {
            return null;
        }
    }

    public boolean hasSession(String chain) {
        return getSession(chain) != null;
    }

    public void clearSession(String chain) {
        if (chain == null) return;
        saveSession(chain, null);
        putEncrypted("points." + chain.trim().toLowerCase(), null);
    }

    // ── Токены авторизации ──

    public void saveTokens(String chain, String accessToken, String refreshToken) {
        try {
            JSONObject session = getSession(chain);
            if (session == null) session = new JSONObject();
            JSONObject tokens = session.optJSONObject("tokens");
            if (tokens == null) tokens = new JSONObject();
            if (accessToken != null) tokens.put("access_token", accessToken);
            if (refreshToken != null) tokens.put("refresh_token", refreshToken);
            tokens.put("updated_at", System.currentTimeMillis());
            session.put("tokens", tokens);
            saveSession(chain, session);
        } catch (Exception e) {
            Log.e(TAG, "saveTokens error: " + e.getMessage());
        }
    }

    public JSONObject getTokens(String chain) {
        JSONObject session = getSession(chain);
        return session != null ? session.optJSONObject("tokens") : null;
    }

    // ── Баллы лояльности ──

    public void savePoints(String chain, double points, String cardNumber) {
        try {
            JSONObject obj = new JSONObject();
            obj.put("points", points);
            if (cardNumber != null) obj.put("card", cardNumber);
            obj.put("updated_at", System.currentTimeMillis());
            putEncrypted("points." + chain.trim().toLowerCase(), obj.toString());
        } catch (Exception e) {
            Log.e(TAG, "savePoints error: " + e.getMessage());
        }
    }

    public JSONObject getPoints(String chain) {
        String raw = getDecrypted("points." + chain.trim().toLowerCase(), null);
        if (raw == null) return null;
        try {
            return new JSONObject(raw);
        } catch (Exception e) {
            return null;
        }
    }

    // ── Импорт / экспорт кук для сети ──

    public void importCookies(String chain, String rawCookies, String host) {
        if (chain == null || rawCookies == null) return;
        try {
            JSONObject session = getSession(chain);
            if (session == null) session = new JSONObject();
            JSONArray list = new JSONArray();
            for (String piece : rawCookies.split(";")) {
                piece = piece.trim();
                int eq = piece.indexOf('=');
                if (eq < 1) continue;
                String name = piece.substring(0, eq).trim();
                String val = piece.substring(eq + 1).trim();
                list.put(new JSONObject().put("name", name).put("value", val));
            }
            session.put("cookies", list);
            session.put("host", host);
            session.put("updated_at", System.currentTimeMillis());
            saveSession(chain, session);
        } catch (Exception e) {
            Log.e(TAG, "importCookies error: " + e.getMessage());
        }
    }

    public String exportCookieHeader(String chain) {
        JSONObject session = getSession(chain);
        if (session == null) return "";
        JSONArray cookies = session.optJSONArray("cookies");
        if (cookies == null || cookies.length() == 0) return "";
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < cookies.length(); i++) {
            JSONObject c = cookies.optJSONObject(i);
            if (c == null) continue;
            String n = c.optString("name", "");
            String v = c.optString("value", "");
            if (n.isEmpty()) continue;
            if (sb.length() > 0) sb.append("; ");
            sb.append(n).append("=").append(v);
        }
        return sb.toString();
    }
}
