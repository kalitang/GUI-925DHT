package com.power.collector;

import android.app.Activity;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.provider.DocumentsContract;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import android.widget.Toast;

import com.power.collector.importer.ArchiveExtractor;
import com.power.collector.importer.ImportManager;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * WebView ↔ Java 桥。
 *
 * 暴露给 JS（window.PCBridge）：
 *   pickDataDir()      打开系统文件夹选择器（选数据目录）
 *   pickArchive()      打开系统文件选择器（选压缩包/Excel，支持 zip/7z/rar/tar/gz/bz2/xz）
 *   clearLoadedData()  清除已加载的台区数据（设备+连线+分布）
 *   clearTileCache()   清空瓦片缓存
 *   openAppDir()       打开应用数据目录
 *   hasImported()      是否有已导入数据（"1"/"0"）
 *   importSummary()    最近一次导入摘要（JSON）
 *   appDataPath()      应用私有数据目录绝对路径
 *
 * SAF 目录遍历用 DocumentsContract（纯 Android API，无第三方依赖）。
 */
public class AndroidBridge {

    private static final String TAG = "AndroidBridge";

    private final MainActivity act;
    private final WebView web;
    private final Handler main = new Handler(Looper.getMainLooper());

    /** 导入工作目录（解压 + 解析暂存区） */
    private File workRoot;
    /** 最近一次导入摘要（JSON 字符串） */
    private String lastSummary = "{}";

    public AndroidBridge(MainActivity act, WebView web) {
        this.act = act;
        this.web = web;
    }

    // =============================================================== JS 接口

    /** 选择数据目录（SAF） */
    @JavascriptInterface
    public void pickDataDir() {
        act.runOnUiThread(() -> {
            try {
                Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
                i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                        | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                        | Intent.FLAG_GRANT_PREFIX_URI_PERMISSION);
                act.startActivityForResult(i, MainActivity.REQ_PICK_DIR);
            } catch (Throwable t) {
                toast("无法打开目录选择器：" + t.getMessage());
            }
        });
    }

    /** 选择压缩包或 Excel（SAF，多选任意格式） */
    @JavascriptInterface
    public void pickArchive() {
        act.runOnUiThread(() -> {
            try {
                Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                i.addCategory(Intent.CATEGORY_OPENABLE);
                i.setType("*/*");
                i.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                act.startActivityForResult(i, MainActivity.REQ_PICK_ARCHIVE);
            } catch (Throwable t) {
                toast("无法打开文件选择器：" + t.getMessage());
            }
        });
    }

    /** 清除已加载的台区数据（设备 + 连线 + 分布） */
    @JavascriptInterface
    public void clearLoadedData() {
        act.runOnUiThread(() -> {
            try {
                File dataDir = new File(act.getFilesDir(), "data");
                int removed = 0;
                File[] fs = dataDir.listFiles();
                if (fs != null) {
                    for (File f : fs) {
                        if (f.isFile() && f.getName().endsWith(".json")) {
                            if (f.delete()) removed++;
                        }
                    }
                }
                callJs("window.PCApp && window.PCApp.onDataCleared && window.PCApp.onDataCleared("
                        + removed + ")");
                toast(removed > 0 ? "已清除已加载数据" : "当前没有已加载数据");
            } catch (Throwable t) {
                toast("清除失败：" + t.getMessage());
            }
        });
    }

    /** 清空瓦片缓存（运行时下载的瓦片，不动内置离线瓦片） */
    @JavascriptInterface
    public void clearTileCache() {
        act.runOnUiThread(() -> {
            try {
                int n = deleteAll(act.getCacheDir());
                toast("已清空瓦片缓存（" + n + " 项）");
            } catch (Throwable t) {
                toast("清理失败：" + t.getMessage());
            }
        });
    }

    /** 打开应用数据目录 */
    @JavascriptInterface
    public void openAppDir() {
        act.runOnUiThread(() -> {
            File dir = new File(act.getFilesDir(), "data");
            if (!dir.exists()) dir.mkdirs();
            try {
                Intent i = new Intent(Intent.ACTION_VIEW);
                i.setDataAndType(Uri.parse("file://" + dir.getAbsolutePath()),
                        "resource/folder");
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                act.startActivity(i);
            } catch (Throwable t) {
                android.content.ClipboardManager cm =
                        (android.content.ClipboardManager) act.getSystemService(
                                android.content.Context.CLIPBOARD_SERVICE);
                if (cm != null) {
                    cm.setPrimaryClip(android.content.ClipData.newPlainText(
                            "path", dir.getAbsolutePath()));
                }
                toast("数据目录：" + dir.getAbsolutePath() + "（已复制）");
            }
        });
    }

    /** 是否有已导入数据 */
    @JavascriptInterface
    public String hasImported() {
        File f = new File(new File(act.getFilesDir(), "data"), "devices.json");
        return (f.isFile() && f.length() > 0) ? "1" : "0";
    }

    /** 最近一次导入摘要 */
    @JavascriptInterface
    public String importSummary() {
        return lastSummary;
    }

    /** 应用私有数据目录绝对路径 */
    @JavascriptInterface
    public String appDataPath() {
        return new File(act.getFilesDir(), "data").getAbsolutePath();
    }

    // ============================================================ 内部流程

    /** SAF 回调：选中了目录 */
    public void onDirectoryPicked(Uri treeUri) {
        main.post(() -> {
            try {
                File base = new File(act.getCacheDir(),
                        "import_work/dir_" + System.currentTimeMillis());
                base.mkdirs();
                int n = copyTree(treeUri, base);
                if (n == 0) {
                    toast("所选目录里没有可读文件");
                    return;
                }
                toast("已读取 " + n + " 个文件，正在解析…");
                doImport(base, false);
            } catch (Throwable t) {
                Log.e(TAG, "pick dir failed", t);
                toast("导入失败：" + t.getMessage());
            }
        });
    }

    /** SAF 回调：选中了压缩包 / Excel */
    public void onArchivesPicked(List<Uri> uris) {
        main.post(() -> {
            try {
                if (workRoot == null) workRoot = new File(act.getCacheDir(), "import_work");
                if (workRoot.exists()) deleteAll(workRoot);
                workRoot.mkdirs();
                File unpack = new File(workRoot, "unpacked");
                unpack.mkdirs();
                File direct = new File(workRoot, "direct");
                direct.mkdirs();

                int extracted = 0;
                for (Uri u : uris) {
                    String name = queryDisplayName(u);
                    File tmp = new File(workRoot, sanitize(name));
                    if (!copyUriToFile(u, tmp)) continue;

                    String lower = name.toLowerCase();
                    boolean isArchive = lower.endsWith(".zip") || lower.endsWith(".7z")
                            || lower.endsWith(".rar") || lower.endsWith(".tar")
                            || lower.endsWith(".gz") || lower.endsWith(".bz2")
                            || lower.endsWith(".xz");
                    if (isArchive) {
                        ArchiveExtractor.Result ar = ArchiveExtractor.extract(tmp, unpack);
                        extracted += ar.files;
                        Log.i(TAG, "extract " + name + " fmt=" + ar.format
                                + " files=" + ar.files + " err=" + ar.error);
                        if (ar.error != null) toast(name + " 解压失败：" + ar.error);
                    } else {
                        // 直接是 Excel / csv
                        copyUriToFile(u, new File(direct, sanitize(name)));
                    }
                }

                File src = extracted > 0 ? unpack : direct;
                File[] srcFiles = src.listFiles();
                if (srcFiles == null || srcFiles.length == 0) {
                    toast("没有可导入的内容");
                    return;
                }
                toast(extracted > 0
                        ? "解压 " + extracted + " 个文件，正在解析…"
                        : "正在解析文件…");
                doImport(src, true);
            } catch (Throwable t) {
                Log.e(TAG, "pick archive failed", t);
                toast("导入失败：" + t.getMessage());
            }
        });
    }

    /** 执行导入并回调页面 */
    private void doImport(File srcDir, boolean temporary) {
        ImportManager.Result r = ImportManager.importFromDirectory(act, srcDir, temporary);
        JSONObject o = new JSONObject();
        try {
            o.put("ok", r.ok);
            o.put("message", r.message);
            o.put("devices", r.devices);
            o.put("links", r.links);
            o.put("filesScanned", r.filesScanned);
            o.put("filesParsed", r.filesParsed);
            o.put("taquCount", r.taquCount);
            o.put("format", r.format);
            JSONObject tc = new JSONObject();
            if (r.typeCounts != null) {
                for (Map.Entry<String, Integer> e : r.typeCounts.entrySet()) {
                    tc.put(e.getKey(), e.getValue());
                }
            }
            o.put("typeCounts", tc);
        } catch (Exception ignored) {}
        lastSummary = o.toString();
        toast(r.message);
        callJs("window.PCApp && window.PCApp.onImportDone && window.PCApp.onImportDone("
                + lastSummary + ")");
    }

    // ============================================================ SAF 遍历

    /**
     * 用 DocumentsContract 递归复制目录树到本地。
     * （不用 androidx.documentfile —— 少一个依赖）
     */
    private int copyTree(Uri treeUri, File dest) {
        return copyDir(treeUri, treeUri, dest);
    }

    private int copyDir(Uri treeUri, Uri docUri, File dest) {
        int n = 0;
        try {
            Uri childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(
                    treeUri, DocumentsContract.getTreeDocumentId(docUri));
            String[] cols = {
                    DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                    DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                    DocumentsContract.Document.COLUMN_MIME_TYPE,
            };
            try (Cursor c = act.getContentResolver().query(childrenUri, cols, null, null, null)) {
                if (c == null) return 0;
                while (c.moveToNext()) {
                    String docId = c.getString(0);
                    String name = c.getString(1);
                    String mime = c.getString(2);
                    if (docId == null) continue;
                    Uri child = DocumentsContract.buildDocumentUriUsingTree(treeUri, docId);
                    if (name == null || name.isEmpty()) name = "file";
                    if (DocumentsContract.Document.MIME_TYPE_DIR.equals(mime)) {
                        File d = new File(dest, sanitize(name));
                        if (!d.exists()) d.mkdirs();
                        n += copyDir(treeUri, child, d);
                    } else {
                        if (copyUriToFile(child, new File(dest, sanitize(name)))) n++;
                    }
                }
            }
        } catch (Throwable t) {
            Log.w(TAG, "copyDir failed: " + docUri, t);
        }
        return n;
    }

    /** 把 content:// Uri 复制到本地文件 */
    private boolean copyUriToFile(Uri uri, File dst) {
        try {
            File parent = dst.getParentFile();
            if (parent != null && !parent.exists()) parent.mkdirs();
            try (InputStream in = act.getContentResolver().openInputStream(uri)) {
                if (in == null) return false;
                try (OutputStream out = new FileOutputStream(dst)) {
                    byte[] buf = new byte[64 * 1024];
                    int k;
                    while ((k = in.read(buf)) > 0) out.write(buf, 0, k);
                }
            }
            return true;
        } catch (Throwable t) {
            Log.e(TAG, "copy failed: " + uri, t);
            return false;
        }
    }

    private String queryDisplayName(Uri uri) {
        try (Cursor c = act.getContentResolver().query(
                uri, new String[]{android.provider.OpenableColumns.DISPLAY_NAME},
                null, null, null)) {
            if (c != null && c.moveToFirst()) {
                int idx = c.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME);
                if (idx >= 0) {
                    String n = c.getString(idx);
                    if (n != null && !n.isEmpty()) return n;
                }
            }
        } catch (Throwable ignored) {}
        String p = uri.getLastPathSegment();
        return p == null ? "import.dat" : p;
    }

    /** 文件名清理（去路径分隔符等） */
    static String sanitize(String name) {
        if (name == null || name.isEmpty()) return "file";
        return name.replaceAll("[\\\\/:*?\"<>|]", "_");
    }

    private int deleteAll(File dir) {
        int n = 0;
        File[] fs = dir.listFiles();
        if (fs == null) return 0;
        for (File f : fs) {
            if (f.isDirectory()) n += deleteAll(f);
            if (f.delete()) n++;
        }
        return n;
    }

    private void callJs(String js) {
        main.post(() -> {
            try { web.evaluateJavascript(js, null); }
            catch (Throwable ignored) {}
        });
    }

    private void toast(String msg) {
        Toast.makeText(act, msg, Toast.LENGTH_LONG).show();
    }
}
