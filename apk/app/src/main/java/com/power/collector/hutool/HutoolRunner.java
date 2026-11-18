package com.power.collector.hutool;

import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import java.io.File;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 户表批量工具 —— 批处理调度 + JS 桥。
 *
 * 与 GUI 版（hutool_gui.py 的 on_generate / on_modify / on_collect_pdf /
 * on_prepare_collector）行为一致：
 *   · 确认弹窗在 JS 侧（前端）
 *   · 后台线程执行，log / progress / done 通过回调回主线程
 *   · 停止按钮可中断（should_stop 等价物）
 *   · busy 状态：任务运行中禁止再启动
 */
public class HutoolRunner {

    private static final String TAG = "HutoolRunner";

    /** 事件回调（全部在主线程触发） */
    public interface Listener {
        void onLog(String line);
        void onProgress(int done, int total);
        void onDone(String title, String summary, boolean error);
    }

    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService exec = Executors.newSingleThreadExecutor();
    private final AtomicBoolean busy = new AtomicBoolean(false);
    private final AtomicBoolean stopFlag = new AtomicBoolean(false);
    private volatile HutoolCore core;
    private volatile Listener listener;

    public void setListener(Listener l) {
        this.listener = l;
    }

    public boolean isBusy() {
        return busy.get();
    }

    /** 停止当前任务 */
    public void stop() {
        stopFlag.set(true);
        if (core != null) core.requestStop();
        post("正在停止…", null);
    }

    public boolean isStopped() {
        return stopFlag.get();
    }

    private void post(String line, Runnable extra) {
        main.post(new Runnable() {
            @Override public void run() {
                if (line != null && listener != null) listener.onLog(line);
                if (extra != null) extra.run();
            }
        });
    }

    private void postProgress(int done, int total) {
        main.post(new Runnable() {
            @Override public void run() {
                if (listener != null) listener.onProgress(done, total);
            }
        });
    }

    private void postDone(String title, String summary, boolean error) {
        main.post(new Runnable() {
            @Override public void run() {
                if (listener != null) listener.onDone(title, summary, error);
            }
        });
    }

    // ------------------------------------------------------------- 功能①
    public void generate(final File root, final File tpl, final boolean keepUnmatched) {
        if (!start("=== 功能① 批量生成客户档案 ===")) return;
        exec.submit(new Runnable() {
            @Override public void run() {
                try {
                    List<String[]> folders = HutoolCore.listSubfolders(root);
                    final int total = folders.size();
                    post("根目录: " + root.getAbsolutePath(), null);
                    post("模板  : " + (tpl == null ? "(未找到)" : tpl.getAbsolutePath()) + " (内置)", null);
                    post("子文件夹: " + total + " 个", null);
                    post("------------------------------------------------------------", null);

                    final int[] n = {0};
                    HutoolCore.LogSink sink = new HutoolCore.LogSink() {
                        @Override public void log(String line) {
                            post(line, null);
                            n[0]++;
                            postProgress(n[0], total);
                        }
                    };
                    postProgress(0, total);

                    int ok = 0, fail = 0;
                    for (String[] pair : folders) {
                        if (core.isStoppedNow()) {
                            post("[已停止]", null);
                            break;
                        }
                        String name = pair[0];
                        File folder = new File(pair[1]);
                        try {
                            File xlsx = HutoolCore.findXlsx(folder);
                            if (xlsx == null) {
                                fail++;
                                sink.log("[失败] " + name + " — 文件夹内没有 .xlsx 源文件");
                                continue;
                            }
                            // 低内存优化：传进度回调（每 200 行回调一次，避免 UI 冻结）
                            HutoolCore.RowProgressSink rowSink = new HutoolCore.RowProgressSink() {
                                @Override public void onRow(int done, int totalRows) {
                                    post("读取中 " + name + " … " + done + "/" + totalRows, null);
                                }
                            };
                            Map<String, Object> res = HutoolCore.readSourceRecords(xlsx, rowSink);
                            @SuppressWarnings("unchecked")
                            List<HutoolCore.Rec> recs = (List<HutoolCore.Rec>) res.get("records");
                            @SuppressWarnings("unchecked")
                            List<String> missing = (List<String>) res.get("missing");

                            if (!keepUnmatched) {
                                // 整条跳过未匹配记录
                                List<HutoolCore.Rec> keep = new java.util.ArrayList<>();
                                for (HutoolCore.Rec r : recs) if (r.matched) keep.add(r);
                                recs = keep;
                            }
                            if (recs.isEmpty()) {
                                fail++;
                                sink.log("[失败] " + name + " — 没有可写入的记录");
                                continue;
                            }
                            File out = new File(folder, name + "用户.xls");
                            HutoolCore.writeCustomerXls(tpl, out, recs);
                            // 低内存优化：每处理完一个台区提示 GC（释放上一轮 POI 对象）
                            System.gc();
                            ok++;
                            String extra = (!missing.isEmpty() && keepUnmatched)
                                    ? "（未匹配 " + missing.size() + " 条已留空写入）" : "";
                            sink.log("[成功] " + name + " — 写入 " + recs.size() + " 条" + extra);
                        } catch (Exception e) {
                            fail++;
                            sink.log("[失败] " + name + " — " + e.getMessage());
                        }
                    }
                    post("------------------------------------------------------------", null);
                    post("完成：成功 " + ok + " / 失败 " + fail + " / 共 " + total, null);
                    finish("生成完成", "成功 " + ok + " / 失败 " + fail + " / 共 " + total, false);
                } catch (Exception e) {
                    Log.e(TAG, "生成失败", e);
                    post("错误: " + e.getMessage(), null);
                    finish("生成失败", String.valueOf(e.getMessage()), true);
                }
            }
        });
    }

    // ------------------------------------------------------------- 功能②
    public void modify(final File root, final boolean keepCollectorCopy) {
        if (!start("=== 功能② 批量修改安卓表 ===")) return;
        exec.submit(new Runnable() {
            @Override public void run() {
                try {
                    List<String[]> folders = HutoolCore.listSubfolders(root);
                    final int total = folders.size();
                    post("根目录: " + root.getAbsolutePath(), null);
                    post("子文件夹: " + total + " 个", null);
                    post("------------------------------------------------------------", null);

                    final int[] n = {0};
                    HutoolCore.LogSink sink = new HutoolCore.LogSink() {
                        @Override public void log(String line) {
                            post(line, null);
                            n[0]++;
                            postProgress(n[0], total);
                        }
                    };
                    postProgress(0, total);

                    int ok = 0, fail = 0, skipped = 0;
                    for (String[] pair : folders) {
                        if (core.isStoppedNow()) {
                            post("[已停止]", null);
                            break;
                        }
                        String name = pair[0];
                        File folder = new File(pair[1]);
                        try {
                            List<File> files = HutoolCore.findAndroidXls(folder);
                            if (files.isEmpty()) {
                                skipped++;
                                sink.log("[跳过] " + name + " — 没有 *安卓.xls 文件");
                                continue;
                            }
                            int modified = 0;
                            int copies = 0;
                            for (File f : files) {
                                if (core.isStoppedNow()) break;
                                if (keepCollectorCopy) {
                                    if (HutoolCore.makeCollectorCopy(f, name, root) != null) copies++;
                                }
                                HutoolCore.CellProgressSink cellSink = new HutoolCore.CellProgressSink() {
                                    @Override public void onCell(int rowDone, int rowTotal, int sheetIdx, int sheetTotal) {
                                        if (rowTotal > 0 && rowDone % 100 == 0) {
                                            post("修改 " + f.getName() + " sheet" + (sheetIdx+1) + "/" + sheetTotal + " … " + rowDone + "/" + rowTotal, null);
                                        }
                                    }
                                };
                                modified += HutoolCore.modifyAndroidXls(f, name, cellSink);
                            }
                            System.gc();
                            if (modified == 0) {
                                skipped++;
                                sink.log("[跳过] " + name + " — 未找到值为 '0杆' 的单元格");
                                continue;
                            }
                            ok++;
                            String cp = keepCollectorCopy
                                    ? "，另存采集版 " + copies + " 份（0杆 保留原样）" : "";
                            sink.log("[成功] " + name + " — 修改 " + modified + " 处" + cp);
                        } catch (Exception e) {
                            fail++;
                            sink.log("[失败] " + name + " — " + e.getMessage());
                        }
                    }
                    post("------------------------------------------------------------", null);
                    post("完成：成功 " + ok + " / 失败 " + fail + " / 跳过 " + skipped + " / 共 " + total, null);
                    if (keepCollectorCopy) {
                        post("采集软件数据目录: " + new File(root, HutoolCore.COLLECTOR_DIR).getAbsolutePath(), null);
                    }
                    finish("修改完成", "成功 " + ok + " / 失败 " + fail + " / 跳过 " + skipped + " / 共 " + total, false);
                } catch (Exception e) {
                    Log.e(TAG, "修改失败", e);
                    post("错误: " + e.getMessage(), null);
                    finish("修改失败", String.valueOf(e.getMessage()), true);
                }
            }
        });
    }

    // ------------------------------------------------------------- 功能③
    public void collectPdf(final File root) {
        if (!start("=== 功能③ 提取整理 PDF ===")) return;
        exec.submit(new Runnable() {
            @Override public void run() {
                try {
                    File printDir = new File(root, HutoolCore.PDF_SUBDIR);
                    post("根目录: " + root.getAbsolutePath(), null);
                    post("目标目录: " + printDir.getAbsolutePath(), null);
                    post("------------------------------------------------------------", null);

                    List<File> pdfs = HutoolCore.collectPdfFiles(root);
                    final int total = pdfs.size();
                    postProgress(0, total);
                    int moved = 0, skipped = 0;
                    for (File pdf : pdfs) {
                        if (core.isStoppedNow()) {
                            post("[已停止]", null);
                            break;
                        }
                        try {
                            File dst = HutoolCore.moveToPrintDir(pdf, printDir);
                            if (dst == null) {
                                skipped++;
                                post("[失败] " + pdf.getName() + " — 移动失败", null);
                            } else {
                                moved++;
                                post("[移动] " + pdf.getName() + " -> " + HutoolCore.PDF_SUBDIR + "/" + dst.getName(), null);
                            }
                        } catch (Exception e) {
                            skipped++;
                            post("[失败] " + pdf.getName() + " — " + e.getMessage(), null);
                        }
                        postProgress(moved + skipped, total);
                    }
                    post("------------------------------------------------------------", null);
                    post("完成：移动 " + moved + " / 跳过 " + skipped + " / 共 " + total, null);
                    finish("PDF 整理完成", "移动 " + moved + " / 跳过 " + skipped + " / 共 " + total, false);
                } catch (Exception e) {
                    Log.e(TAG, "PDF 整理失败", e);
                    post("错误: " + e.getMessage(), null);
                    finish("PDF 整理失败", String.valueOf(e.getMessage()), true);
                }
            }
        });
    }

    // ------------------------------------------------------------- 功能④
    public void prepareCollector(final File root) {
        if (!start("=== 功能④ 准备采集软件数据 ===")) return;
        exec.submit(new Runnable() {
            @Override public void run() {
                try {
                    List<String[]> folders = HutoolCore.listSubfolders(root);
                    final int total = folders.size();
                    post("根目录: " + root.getAbsolutePath(), null);
                    post("目标目录: " + new File(root, HutoolCore.COLLECTOR_DIR).getAbsolutePath(), null);
                    post("子文件夹: " + total + " 个", null);
                    post("------------------------------------------------------------", null);

                    final int[] n = {0};
                    HutoolCore.LogSink sink = new HutoolCore.LogSink() {
                        @Override public void log(String line) {
                            post(line, null);
                            n[0]++;
                            postProgress(n[0], total);
                        }
                    };
                    postProgress(0, total);

                    int copied = 0;
                    for (String[] pair : folders) {
                        if (core.isStoppedNow()) {
                            post("[已停止]", null);
                            break;
                        }
                        String name = pair[0];
                        File folder = new File(pair[1]);
                        List<File> files = HutoolCore.findAndroidXls(folder);
                        if (files.isEmpty()) {
                            sink.log("[跳过] " + name + " — 没有 *安卓.xls");
                            continue;
                        }
                        int c = 0;
                        for (File f : files) {
                            if (core.isStoppedNow()) break;
                            if (HutoolCore.copyToCollectorDir(f, name, root) != null) c++;
                        }
                        copied += c;
                        sink.log("[复制] " + name + " — " + c + " 份");
                    }
                    post("------------------------------------------------------------", null);
                    post("完成：台区 " + total + " / 复制 " + copied + " 份", null);
                    finish("采集软件数据准备完成", "台区 " + total + " / 复制 " + copied + " 份", false);
                } catch (Exception e) {
                    Log.e(TAG, "准备失败", e);
                    post("错误: " + e.getMessage(), null);
                    finish("准备失败", String.valueOf(e.getMessage()), true);
                }
            }
        });
    }

    // ------------------------------------------------------------- 内部
    private boolean start(String banner) {
        if (!busy.compareAndSet(false, true)) {
            post("已有任务正在运行，请等待完成。", null);
            return false;
        }
        stopFlag.set(false);
        core = new HutoolCore(new HutoolCore.LogSink() {
            @Override public void log(String line) {
                post(line, null);
            }
        });
        post(banner, null);
        return true;
    }

    private void finish(String title, String summary, boolean error) {
        busy.set(false);
        postDone(title, summary, error);
    }
}
