package com.beamind.smartraz;

import android.content.Context;
import android.util.AtomicFile;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import java.util.function.Consumer;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/** Immutable version directories with a small, atomically replaced registry. */
final class ReaderPackStore {
    static final long MAX_TOTAL = 3L * 1024 * 1024 * 1024;
    static final int MAX_JSON = 4 * 1024 * 1024;
    private final File root;
    private final Context context;
    private final Map<String, JSONObject> staged = new HashMap<>();

    ReaderPackStore(Context context) throws IOException {
        this.context = context;
        root = new File(context.getFilesDir(), "reader-packs");
        if (!root.isDirectory() && !root.mkdirs()) throw new IOException("Cannot create resource directory");
    }

    String rootPath() { return root.getAbsolutePath(); }

    synchronized JSONObject registry() throws Exception {
        AtomicFile file = new AtomicFile(new File(root, "installed.json"));
        try (InputStream input = file.openRead()) {
            JSONObject value = new JSONObject(new String(readLimited(input, 32 * 1024 * 1024), StandardCharsets.UTF_8));
            if (value.getInt("schemaVersion") != 1 || value.getJSONArray("packs").length() > 26) throw new IOException("Invalid pack registry");
            return value;
        } catch (FileNotFoundException error) {
            return new JSONObject().put("schemaVersion", 1).put("packs", new JSONArray());
        }
    }

    private void save(JSONArray packs) throws Exception {
        AtomicFile file = new AtomicFile(new File(root, "installed.json"));
        FileOutputStream output = null;
        try {
            output = file.startWrite();
            output.write(new JSONObject().put("schemaVersion", 1).put("packs", packs).toString().getBytes(StandardCharsets.UTF_8));
            file.finishWrite(output);
        } catch (Exception error) { file.failWrite(output); throw error; }
    }

    synchronized void cleanup() throws Exception {
        Set<String> keep = new HashSet<>(staged.keySet());
        JSONArray packs = registry().getJSONArray("packs");
        for (int i = 0; i < packs.length(); i++) keep.add(packs.getJSONObject(i).getString("id"));
        File[] files = root.listFiles();
        if (files != null) for (File file : files) {
            if (file.isDirectory() && validId(file.getName()) && !keep.contains(file.getName())) delete(file);
        }
        // Process death may interrupt copying the selected ZIP before stage()
        // reaches its finally block. Only collect our own temporary archives.
        File cache = context.getCacheDir().getCanonicalFile();
        File[] temporary = cache.listFiles((dir, name) -> name.startsWith("reader-pack-") && name.endsWith(".zip"));
        if (temporary != null) for (File file : temporary) {
            if (file.isFile() && file.getCanonicalFile().getParentFile().equals(cache)) file.delete();
        }
    }

    synchronized JSONObject stage(InputStream source, Consumer<String> progress) throws Exception {
        File zip = File.createTempFile("reader-pack-", ".zip", context.getCacheDir());
        File destination = null;
        try {
            progress.accept("正在读取资源包…");
            try (OutputStream output = new BufferedOutputStream(new FileOutputStream(zip))) {
                copy(source, output, MAX_TOTAL + 16 * 1024 * 1024, null);
            }
            try (ZipFile archive = new ZipFile(zip)) {
                ZipEntry meta = archive.getEntry("pack.json");
                if (meta == null || meta.getSize() < 1 || meta.getSize() > MAX_JSON || meta.getMethod() != ZipEntry.STORED) throw new IOException("Invalid pack manifest");
                JSONObject pack;
                try (InputStream input = archive.getInputStream(meta)) {
                    pack = new JSONObject(new String(readLimited(input, MAX_JSON), StandardCharsets.UTF_8));
                }
                String level = pack.getJSONObject("level").getString("id"), version = pack.getString("version");
                if (!pack.getString("schemaVersion").equals("raz-reader-pack/1.0") || pack.getInt("readerVersion") != 1 ||
                    !level.matches("[A-Z]") || !version.matches("[0-9a-f]{16}")) throw new IOException("Incompatible pack");
                JSONArray files = pack.getJSONArray("files");
                if (files.length() < 1 || files.length() > 60000 || archive.size() != files.length() + 1) throw new IOException("Invalid inventory");
                Set<String> inventory = new HashSet<>();
                long total = 0;
                for (int i = 0; i < files.length(); i++) {
                    JSONObject file = files.getJSONObject(i);
                    String path = file.getString("path");
                    long size = file.getLong("size");
                    if (!path.matches("content/levels/" + level + "/books/[a-z0-9-]+/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+\\.(json|webp|mp3)") ||
                        path.contains("/_source/") || !inventory.add(path) || size < 1 ||
                        size > (path.endsWith(".json") ? MAX_JSON : 64 * 1024 * 1024) ||
                        !file.getString("sha256").matches("[0-9a-f]{64}")) throw new IOException("Invalid file metadata");
                    total += size;
                }
                if (total > MAX_TOTAL || total != pack.getLong("totalBytes") || root.getUsableSpace() < total + 16 * 1024 * 1024) throw new IOException("资源包过大或设备可用空间不足。");
                Set<String> names = new HashSet<>();
                Enumeration<? extends ZipEntry> entries = archive.entries();
                while (entries.hasMoreElements()) {
                    ZipEntry entry = entries.nextElement();
                    if (!names.add(entry.getName()) || entry.isDirectory() || entry.getMethod() != ZipEntry.STORED ||
                        (!entry.getName().equals("pack.json") && !inventory.contains(entry.getName()))) throw new IOException("Unexpected ZIP entry");
                }
                String id = level + "-" + version + "-" + UUID.randomUUID();
                destination = new File(root, id);
                if (!destination.mkdir()) throw new IOException("Cannot create staging directory");
                long complete = 0;
                for (int i = 0; i < files.length(); i++) {
                    JSONObject file = files.getJSONObject(i);
                    String path = file.getString("path");
                    ZipEntry entry = archive.getEntry(path);
                    if (entry == null || entry.getSize() != file.getLong("size") || entry.getCompressedSize() != entry.getSize()) throw new IOException("Resource size mismatch");
                    File target = new File(destination, path);
                    if (!target.getCanonicalPath().startsWith(destination.getCanonicalPath() + File.separator)) throw new IOException("Invalid resource path");
                    if (!target.getParentFile().isDirectory() && !target.getParentFile().mkdirs()) throw new IOException("Cannot create resource directory");
                    MessageDigest digest = MessageDigest.getInstance("SHA-256");
                    try (InputStream input = archive.getInputStream(entry);
                         OutputStream output = new BufferedOutputStream(new FileOutputStream(target))) {
                        if (copy(input, output, entry.getSize(), digest) != entry.getSize()) throw new IOException("Resource truncated");
                    }
                    StringBuilder hash = new StringBuilder();
                    for (byte value : digest.digest()) hash.append(String.format(Locale.ROOT, "%02x", value & 255));
                    if (!hash.toString().equals(file.getString("sha256"))) throw new IOException("资源包文件校验失败，原有资源未改变。");
                    complete += entry.getSize();
                    progress.accept("正在导入 " + level + " 级：" + (complete * 100 / total) + "%");
                }
                staged.put(id, pack);
                return new JSONObject().put("id", id).put("manifest", pack).put("root", rootPath());
            }
        } catch (Exception error) {
            if (destination != null) delete(destination);
            throw error;
        } finally { if (!zip.delete()) zip.deleteOnExit(); }
    }

    synchronized void commit(String id) throws Exception {
        JSONObject pack = staged.get(id);
        if (pack == null) throw new IOException("No staged resource pack");
        String level = pack.getJSONObject("level").getString("id");
        JSONArray current = registry().getJSONArray("packs"), next = new JSONArray();
        for (int i = 0; i < current.length(); i++) {
            JSONObject item = current.getJSONObject(i);
            if (!item.getJSONObject("manifest").getJSONObject("level").getString("id").equals(level)) next.put(item);
        }
        java.text.SimpleDateFormat format = new java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.ROOT);
        format.setTimeZone(TimeZone.getTimeZone("UTC"));
        next.put(new JSONObject().put("id", id).put("installedAt", format.format(new Date())).put("manifest", pack));
        save(next);
        staged.remove(id);
    }

    synchronized void discard(String id) throws Exception {
        if (staged.remove(id) != null && validId(id)) delete(new File(root, id));
    }

    synchronized void remove(String level) throws Exception {
        if (!level.matches("[A-Z]")) throw new IOException("Invalid level");
        JSONArray current = registry().getJSONArray("packs"), next = new JSONArray();
        for (int i = 0; i < current.length(); i++) {
            JSONObject item = current.getJSONObject(i);
            if (!item.getJSONObject("manifest").getJSONObject("level").getString("id").equals(level)) next.put(item);
        }
        save(next);
    }

    private static boolean validId(String id) { return id.matches("[a-zA-Z0-9][a-zA-Z0-9-]{0,119}"); }
    private static byte[] readLimited(InputStream input, int limit) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        copy(input, output, limit, null);
        return output.toByteArray();
    }
    private static long copy(InputStream input, OutputStream output, long limit, MessageDigest digest) throws IOException {
        byte[] buffer = new byte[64 * 1024];
        long total = 0;
        int count;
        while ((count = input.read(buffer)) != -1) {
            total += count;
            if (total > limit) throw new IOException("Resource exceeds size limit");
            if (digest != null) digest.update(buffer, 0, count);
            output.write(buffer, 0, count);
        }
        return total;
    }
    private void delete(File file) throws IOException {
        if (!file.getCanonicalPath().startsWith(root.getCanonicalPath() + File.separator)) throw new IOException("Refusing directory outside pack storage");
        File[] children = file.listFiles();
        if (children != null) for (File child : children) delete(child);
        if (file.exists() && !file.delete()) throw new IOException("Cannot remove resource file");
    }
}
