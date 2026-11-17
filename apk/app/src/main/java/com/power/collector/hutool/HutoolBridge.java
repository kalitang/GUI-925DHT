package com.power.collector.hutool;

import android.app.Activity;
import android.content.ContentResolver;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.provider.DocumentsContract;
import android.util.Log;
import android.webkit.JavascriptInterface;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;

/**
 * 户表批量工具 —— Android JS 桥。
 *
 * 与 GUI 版对齐的接口：
 *   pickRoot()              选择待处理根目录（SAF）
 *   rootPath()              当前根目录绝对路径
 *   templatePath()          内置模板路径（assets/template/低压客户模板.xls → filesDir/tpl/）
 *   runGenerate()           功能① 批量生成客户档案
 *   runModify()             功能② 批量修改安卓表
 *   runCollectPdf()         功能③ 提取整理 PDF
 *   runPrepareCollector()   功能④ 准备采集软件数据
 *   stop()                  停止
 *   isBusy()                是否正在运行
 *
 * SAF 选中的目录会被复制到 cacheDir/hutool_work/ 下再操作 ——
 * Android 上直接改用户目录不可行，且 SAF 目录的写权限受限。
 * 注意：功能②/④ 产出的副本在 cache 里，用户需要自行导出（见前端提示）。
 */
public class HutoolBridge {

    private static final String TAG = "HutoolBridge";
    public static final int REQ_PICK_ROOT = 3001;

    private final Activity act;
    private final HutoolRunner runner = new HutoolRunner();
    private final List<String> pendingLog = new ArrayList<>();
    private File workRoot;
    private File selectedRoot;

    public HutoolBridge(Activity act) {
        this.act = act;
        runner.setListener(new HutoolRunner.Listener() {
            @Override public void onLog(String line) {
                pendingLog.add(line);
                // 页面拉取式：前端轮询 drainLogs()
            }
            @Override public void onProgress(int done, int total) {
                pendingProgress = new int[]{done, total};
            }
            @Override public void onDone(String title, String summary, boolean error) {
                pendingDone = new String[]{title, summary, error ? "1" : "0"};
            }
        });
    }

    private int[] pendingProgress = new int[]{0, 0};
    private String[] pendingDone;

    public HutoolRunner getRunner() {
        return runner;
    }

    // ---------------------------------------------------------------- 生命周期
    /** 把内置模板复制到 filesDir/tpl/（只做一次） */
    public void copyBundledTemplate() {
        try {
            File dstDir = new File(act.getFilesDir(), "tpl");
            if (!dstDir.exists() && !dstDir.mkdirs()) {
                Log.w(TAG, "无法创建模板目录");
                return;
            }
            File dst = new File(dstDir, "低压客户模板.xls");
            if (dst.isFile() && dst.length() > 0) return;
            InputStream in = act.getAssets().open("template/低压客户模板.xls");
            OutputStream out = new FileOutputStream(dst);
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            out.flush();
            out.close();
            in.close();
            Log.i(TAG, "模板已解压: " + dst.getAbsolutePath());
        } catch (Exception e) {
            Log.e(TAG, "模板解压失败", e);
        }
    }

    // ---------------------------------------------------------------- JS 接口
    @JavascriptInterface
    public void pickRoot() {
        try {
            Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
            i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                    | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                    | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
            act.startActivityForResult(i, REQ_PICK_ROOT);
        } catch (Throwable t) {
            Log.e(TAG, "pickRoot failed", t);
            toast("无法打开目录选择器");
        }
    }

    @JavascriptInterface
    public String rootPath() {
        return selectedRoot == null ? "" : selectedRoot.getAbsolutePath();
    }

    @JavascriptInterface
    public String templatePath() {
        File f = new File(new File(act.getFilesDir(), "tpl"), "低压客户模板.xls");
        return f.isFile() ? f.getAbsolutePath() : "";
    }

    @JavascriptInterface
    public void runGenerate() {
        File root = requireRoot();
        if (root == null) return;
        File tpl = new File(new File(act.getFilesDir(), "tpl"), "低压客户模板.xls");
        if (!tpl.isFile()) {
            toast("找不到内置模板");
            return;
        }
        runner.generate(root, tpl, true);
    }

    @JavascriptInterface
    public void runModify() {
        File root = requireRoot();
        if (root == null) return;
        runner.modify(root, true);
    }

    @JavascriptInterface
    public void runCollectPdf() {
        File root = requireRoot();
        if (root == null) return;
        runner.collectPdf(root);
    }

    @JavascriptInterface
    public void runPrepareCollector() {
        File root = requireRoot();
        if (root == null) return;
        runner.prepareCollector(root);
    }

    @JavascriptInterface
    public void stop() {
        runner.stop();
    }

    @JavascriptInterface
    public boolean isBusy() {
        return runner.isBusy();
    }

    /** 取走累积的日志（前端轮询） */
    @JavascriptInterface
    public String drainLogs() {
        if (pendingLog.isEmpty()) return "";
        StringBuilder sb = new StringBuilder();
        for (String s : pendingLog) sb.append(s).append('\n');
        pendingLog.clear();
        return sb.toString();
    }

    /** 取进度："done,total"，无则空串 */
    @JavascriptInterface
    public String progress() {
        if (pendingProgress == null) return "";
        return pendingProgress[0] + "," + pendingProgress[1];
    }

    /** 取完成事件："title|summary|error"，无则空串 */
    @JavascriptInterface
    public String doneEvent() {
        if (pendingDone == null) return "";
        String r = pendingDone[0] + "|" + pendingDone[1] + "|" + pendingDone[2];
        pendingDone = null;
        return r;
    }

    // ---------------------------------------------------------------- SAF 回调
    /** SAF 回调：用户选中了根目录 */
    public void onRootPicked(Uri treeUri) {
        act.runOnUiThread(new Runnable() {
            @Override public void run() {
                try {
                    if (workRoot == null) {
                        workRoot = new File(act.getCacheDir(), "hutool_work");
                    }
                    deleteAll(workRoot);
                    workRoot.mkdirs();
                    int n = copyTree(treeUri, workRoot);
                    if (n == 0) {
                        toast("所选目录里没有可读文件");
                        return;
                    }
                    selectedRoot = workRoot;
                    toast("已读取 " + n + " 个文件");
                } catch (Throwable t) {
                    Log.e(TAG, "onRootPicked failed", t);
                    toast("读取目录失败：" + t.getMessage());
                }
            }
        });
    }

    private File requireRoot() {
        if (selectedRoot == null || !selectedRoot.isDirectory()) {
            toast("请先选择待处理根目录");
            return null;
        }
        return selectedRoot;
    }

    // ---------------------------------------------------------------- 工具
    /**
     * 从 SAF treeUri 递归拷贝整个目录树到 dst。
     * 用原生 ContentResolver + DocumentsContract（API 21+，不依赖 androidx.documentfile）。
     */
    private int copyTree(Uri treeUri, File dst) {
        int count = 0;
        ContentResolver cr = act.getContentResolver();
        String rootDocId;
        try {
            rootDocId = android.provider.DocumentsContract.getTreeDocumentId(treeUri);
        } catch (Exception e) {
            Log.w(TAG, "getTreeDocumentId failed: " + treeUri, e);
            return 0;
        }
        try {
            count = copyDirSaf(cr, treeUri, rootDocId, dst);
        } catch (Throwable t) {
            Log.e(TAG, "copyTree failed", t);
        }
        return count;
    }

    /** 递归拷贝 SAF 子树。docId = 当前目录的 document id。 */
    private int copyDirSaf(ContentResolver cr, Uri treeUri, String docId, File dst) {
        int n = 0;
        Uri childUri = android.provider.DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, docId);
        Cursor cur;
        try {
            cur = cr.query(childUri, null, null, null, null);
            if (cur == null) return 0;
        } catch (Exception e) {
            Log.w(TAG, "query children failed: " + docId);
            return 0;
        }
        int nameCol = -1, docCol = -1, typeCol = -1;
        try {
            // SAF children 查询返回列（固定字符串名，不依赖类常量可见性）
            nameCol = cur.getColumnIndex("display_name");
            docCol = cur.getColumnIndex("_id");
            typeCol = cur.getColumnIndex("mime_type");
            if (nameCol < 0) nameCol = cur.getColumnIndex("name");
        } catch (Exception e) {
            cur.close();
            return 0;
        }
        try {
            while (cur.moveToNext()) {
                String name = cur.getString(nameCol);
                String childDoc = cur.getString(docCol);
                String mime = cur.getString(typeCol);
                if (name == null || name.isEmpty() || name.startsWith(".")) continue;
                boolean isDir = "vnd.android.document/directory".equals(mime);
                if (isDir) {
                    File sub = new File(dst, sanitize(name));
                    if (sub.mkdirs()) n += copyDirSaf(cr, treeUri, childDoc, sub);
                } else {
                    try {
                        File out = new File(dst, sanitize(name));
                        Uri fUri = android.provider.DocumentsContract.buildDocumentUriUsingTree(treeUri, childDoc);
                        InputStream in = cr.openInputStream(fUri);
                        if (in == null) continue;
                        OutputStream os = new FileOutputStream(out);
                        byte[] buf = new byte[8192];
                        int r;
                        while ((r = in.read(buf)) > 0) os.write(buf, 0, r);
                        os.flush();
                        os.close();
                        in.close();
                        n++;
                    } catch (Exception e) {
                        Log.w(TAG, "copy file failed: " + name);
                    }
                }
            }
        } finally {
            cur.close();
        }
        return n;
    }

    private static String sanitize(String name) {
        return name.replaceAll("[\\\\/:*?\"<>|]", "_");
    }

    private static void deleteAll(File f) {
        if (!f.exists()) return;
        if (f.isDirectory()) {
            File[] kids = f.listFiles();
            if (kids != null) for (File k : kids) deleteAll(k);
        }
        f.delete();
    }

    private void toast(String msg) {
        try {
            act.runOnUiThread(new Runnable() {
                @Override public void run() {
                    android.widget.Toast.makeText(act, msg,
                            android.widget.Toast.LENGTH_SHORT).show();
                }
            });
        } catch (Throwable ignored) { }
    }
}
