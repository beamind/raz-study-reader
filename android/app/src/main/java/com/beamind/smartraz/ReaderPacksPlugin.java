package com.beamind.smartraz;

import android.app.Activity;
import android.content.Intent;
import androidx.activity.result.ActivityResult;
import com.getcapacitor.*;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.InputStream;

/** Import large files entirely on the native side; JS receives only metadata. */
@CapacitorPlugin(name = "ReaderPacks")
public class ReaderPacksPlugin extends Plugin {
    private ReaderPackStore store;
    private synchronized ReaderPackStore store() throws Exception {
        if (store == null) store = new ReaderPackStore(getContext());
        return store;
    }
    private interface Work { JSObject run() throws Exception; }
    private void execute(PluginCall call, Work work) {
        getBridge().execute(() -> {
            try { call.resolve(work.run()); }
            catch (Exception error) { call.reject("资源包处理失败：" + error.getMessage(), error); }
        });
    }
    @PluginMethod public void list(PluginCall call) {
        execute(call, () -> {
            JSObject response = new JSObject();
            response.put("registry", store().registry()); response.put("root", store().rootPath());
            return response;
        });
    }
    @PluginMethod public void cleanup(PluginCall call) { execute(call, () -> { store().cleanup(); return new JSObject(); }); }
    @PluginMethod public void commit(PluginCall call) { execute(call, () -> { store().commit(call.getString("id", "")); return new JSObject(); }); }
    @PluginMethod public void discard(PluginCall call) { execute(call, () -> { store().discard(call.getString("id", "")); return new JSObject(); }); }
    @PluginMethod public void remove(PluginCall call) { execute(call, () -> { store().remove(call.getString("level", "")); return new JSObject(); }); }
    @PluginMethod public void open(PluginCall call) {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE); intent.setType("*/*");
        startActivityForResult(call, intent, "opened");
    }
    @ActivityCallback private void opened(PluginCall call, ActivityResult result) {
        if (call == null) return;
        if (result.getResultCode() != Activity.RESULT_OK || result.getData() == null || result.getData().getData() == null) {
            JSObject response = new JSObject(); response.put("cancelled", true); call.resolve(response); return;
        }
        execute(call, () -> {
            try (InputStream input = getContext().getContentResolver().openInputStream(result.getData().getData())) {
                if (input == null) throw new java.io.IOException("无法读取所选文件");
                return JSObject.fromJSONObject(store().stage(input, message -> {
                    JSObject progress = new JSObject(); progress.put("message", message); notifyListeners("progress", progress);
                }));
            }
        });
    }
}
