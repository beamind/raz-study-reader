package com.beamind.smartraz;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import androidx.activity.result.ActivityResult;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

/** User-selected JSON files through Android's Storage Access Framework. */
@CapacitorPlugin(name = "ReaderBackup")
public class ReaderBackupPlugin extends Plugin {
    private static final int MAX_BYTES = 20 * 1024 * 1024;

    @PluginMethod
    public void save(PluginCall call) {
        String data = call.getString("data");
        if (data == null || data.getBytes(StandardCharsets.UTF_8).length > MAX_BYTES) {
            call.reject("备份内容为空或超过 20 MB。");
            return;
        }
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("application/json");
        intent.putExtra(Intent.EXTRA_TITLE, call.getString("filename", "raz-learning.json"));
        startActivityForResult(call, intent, "saved");
    }

    @PluginMethod
    public void open(PluginCall call) {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        // Some file providers classify JSON as text/plain or octet-stream.
        intent.setType("*/*");
        startActivityForResult(call, intent, "opened");
    }

    private Uri selectedUri(PluginCall call, ActivityResult result) {
        if (result.getResultCode() != Activity.RESULT_OK || result.getData() == null || result.getData().getData() == null) {
            JSObject response = new JSObject();
            response.put("cancelled", true);
            call.resolve(response);
            return null;
        }
        return result.getData().getData();
    }

    @ActivityCallback
    private void saved(PluginCall call, ActivityResult result) {
        if (call == null) return;
        Uri uri = selectedUri(call, result);
        if (uri == null) return;
        getBridge().execute(() -> {
            try (OutputStream stream = getContext().getContentResolver().openOutputStream(uri, "wt")) {
                if (stream == null) throw new java.io.IOException("Cannot open output");
                stream.write(call.getString("data", "").getBytes(StandardCharsets.UTF_8));
                call.resolve();
            } catch (Exception error) { call.reject("备份文件未能保存，请重试。", error); }
        });
    }

    @ActivityCallback
    private void opened(PluginCall call, ActivityResult result) {
        if (call == null) return;
        Uri uri = selectedUri(call, result);
        if (uri == null) return;
        getBridge().execute(() -> {
            try (InputStream stream = getContext().getContentResolver().openInputStream(uri);
                 ByteArrayOutputStream output = new ByteArrayOutputStream()) {
                if (stream == null) throw new java.io.IOException("Cannot open input");
                byte[] buffer = new byte[8192];
                int count;
                while ((count = stream.read(buffer)) != -1) {
                    if (output.size() + count > MAX_BYTES) throw new java.io.IOException("Backup exceeds 20 MB");
                    output.write(buffer, 0, count);
                }
                JSObject response = new JSObject();
                response.put("data", new String(output.toByteArray(), StandardCharsets.UTF_8));
                call.resolve(response);
            } catch (Exception error) { call.reject("无法打开备份文件，请选择不超过 20 MB 的 JSON 备份。", error); }
        });
    }
}
