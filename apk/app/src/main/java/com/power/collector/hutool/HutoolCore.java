package com.power.collector.hutool;

import android.util.Log;

import org.apache.poi.hssf.usermodel.HSSFWorkbook;
import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.CellType;
import org.apache.poi.ss.usermodel.DateUtil;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.Workbook;
import org.apache.poi.ss.usermodel.WorkbookFactory;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 户表批量工具 —— 核心业务逻辑（Android / Java 版）。
 *
 * 与《户表批量工具》GUI 版（E:\户表\户表\户表\hutool_core.py）逐条对齐：
 *   功能① 批量生成客户档案：读 .xlsx（计量箱台区录入接入点导入 + 同期用户）
 *                              → 按模板写 <文件夹名>用户.xls
 *   功能② 批量修改安卓表：*安卓.xls 中整格等于 "0杆" 的单元格 → 文件夹名
 *                              （可选先复制一份「0杆」原样副本到 _采集软件数据/<台区名>/）
 *   功能③ 提取整理 PDF：递归收集所有 PDF → 移到 <根>/打印/（同名加序号，绝不覆盖）
 *   功能④ 准备采集软件数据：复制 *安卓.xls → <根>/_采集软件数据/<台区名>/
 *
 * 关键一致点：
 *   · "0杆" 用整格精确相等（trim 后），不是 in —— 否则 A10杆 / B2-B10杆 / A0杆 会被误改
 *   · 已匹配记录在前，未匹配记录排在最后（每个台区各自成档，不跨台区混）
 *   · 编号类列以文本写入，避免 5.03E+21 或丢精度
 *   · 模板自带的示例数据与合并单元格说明行会被清除
 */
public class HutoolCore {

    private static final String TAG = "HutoolCore";

    public static final String SHEET_BOX = "计量箱台区录入接入点导入";
    public static final String SHEET_USER = "同期用户";

    // 模板列索引（0-based）
    private static final int C_TQBH = 0, C_TQMC = 1, C_DBNB = 2, C_KHBH = 3;
    private static final int C_KHMC = 4, C_YDZZ = 5, C_LXR = 6, C_LXSJ = 7, C_ZJBH = 8;
    private static final int DATA_START_ROW = 2;   // 1-based

    public static final String ANDROID_TARGET = "0杆";
    public static final String COLLECTOR_DIR = "_采集软件数据";
    public static final String PDF_SUBDIR = "打印";

    private final LogSink log;
    private volatile boolean stopped = false;

    /** 日志回调（主线程消费） */
    public interface LogSink {
        void log(String line);
    }

    public HutoolCore(LogSink log) {
        this.log = log;
    }

    public void requestStop() {
        stopped = true;
    }

    public boolean isStopped() {
        return stopped;
    }

    private boolean shouldStop() {
        return stopped;
    }

    // ------------------------------------------------------------------ 工具
    /** 单元格值 -> 字符串；null -> ""；浮点整数值去掉 .0 */
    public static String s(Cell c) {
        if (c == null) return "";
        CellType t = c.getCellType();
        if (t == CellType.STRING) return c.getStringCellValue().trim();
        if (t == CellType.NUMERIC) {
            if (DateUtil.isCellDateFormatted(c)) return c.getDateCellValue().toString();
            double d = c.getNumericCellValue();
            if (d == Math.floor(d) && !Double.isInfinite(d)) {
                return String.valueOf((long) d);
            }
            return String.valueOf(d);
        }
        if (t == CellType.BOOLEAN) return String.valueOf(c.getBooleanCellValue());
        if (t == CellType.FORMULA) {
            try { return c.getStringCellValue().trim(); } catch (Exception e) { return ""; }
        }
        return "";
    }

    /** 纯值（非单元格）转字符串 */
    public static String sv(Object v) {
        if (v == null) return "";
        if (v instanceof Double) {
            double d = (Double) v;
            if (d == Math.floor(d) && !Double.isInfinite(d)) return String.valueOf((long) d);
            return String.valueOf(d);
        }
        return String.valueOf(v).trim();
    }

    /**
     * 列出 root 下的子文件夹（按名称排序）。
     * 每项为 String[]{文件夹名, 目录绝对路径}。
     */
    public static List<String[]> listSubfolders(File root) {
        List<String[]> out = new ArrayList<>();
        if (root == null || !root.isDirectory()) return out;
        File[] kids = root.listFiles();
        if (kids == null) return out;
        Arrays.sort(kids, new Comparator<File>() {
            @Override public int compare(File a, File b) {
                return a.getName().compareTo(b.getName());
            }
        });
        for (File f : kids) {
            if (f.isDirectory()) out.add(new String[]{f.getName(), f.getAbsolutePath()});
        }
        return out;
    }

    /** 文件夹内第一个 .xlsx（跳过 Excel 临时文件 ~$） */
    public static File findXlsx(File folder) {
        File[] fs = folder.listFiles();
        if (fs == null) return null;
        Arrays.sort(fs, new Comparator<File>() {
            @Override public int compare(File a, File b) {
                return a.getName().compareTo(b.getName());
            }
        });
        for (File f : fs) {
            String n = f.getName();
            if (n.toLowerCase().endsWith(".xlsx") && !n.startsWith("~$")) return f;
        }
        return null;
    }

    /** 文件夹内文件名含「安卓」的 .xls */
    public static List<File> findAndroidXls(File folder) {
        List<File> hits = new ArrayList<>();
        File[] fs = folder.listFiles();
        if (fs == null) return hits;
        Arrays.sort(fs, new Comparator<File>() {
            @Override public int compare(File a, File b) {
                return a.getName().compareTo(b.getName());
            }
        });
        for (File f : fs) {
            if (f.getName().toLowerCase().endsWith(".xls") && f.getName().contains("安卓")) {
                hits.add(f);
            }
        }
        return hits;
    }

    /** 是否是「整格等于 0杆」（trim 后）。A10杆 / 10杆 / A0杆 / 0杆-1 都不算 */
    public static boolean isZeroGan(String value) {
        if (value == null) return false;
        return value.trim().equals(ANDROID_TARGET);
    }

    // ------------------------------------------------------------------ 功能①
    /** 一条客户档案记录 */
    public static class Rec {
        public String tq = "";      // 台区编号(必填) = 台区名称
        public String tqmc = "";    // 台区名称(必填) = 台区名称
        public String bm = "";      // 电能表编号
        public String khbh = "";    // 客户编号
        public String name = "";    // 客户名称
        public String addr = "";    // 用电地址
        public boolean matched = false;
    }

    /**
     * 从 .xlsx 读取记录。
     * 已匹配记录在前，未匹配的排在最后。
     */
    public static Map<String, Object> readSourceRecords(File xlsx) throws Exception {
        List<Rec> matched = new ArrayList<>();
        List<Rec> unmatched = new ArrayList<>();
        List<String> missing = new ArrayList<>();

        Workbook wb = null;
        try {
            FileInputStream fis = new FileInputStream(xlsx);
            wb = WorkbookFactory.create(fis);
            fis.close();

            // ---- 计量箱表（有表头，按表头名定位列）----
            Sheet ws = wb.getSheet(SHEET_BOX);
            if (ws == null) throw new IllegalStateException("找不到工作表「" + SHEET_BOX + "」");
            Row hdrRow = ws.getRow(0);
            List<String> hdr = new ArrayList<>();
            if (hdrRow != null) {
                for (int c = 0; c < hdrRow.getLastCellNum(); c++) {
                    hdr.add(s(hdrRow.getCell(c)));
                }
            }
            int iBm = colIndex(hdr, "电能表条形码");
            int iHh = colIndex(hdr, "户号");
            int iTq = colIndex(hdr, "台区名称");

            // ---- 同期用户（无表头：A=户号 B=表码 C=名称 D=地址）----
            Sheet ws2 = wb.getSheet(SHEET_USER);
            if (ws2 == null) throw new IllegalStateException("找不到工作表「" + SHEET_USER + "」");
            Map<String, String[]> userByBm = new HashMap<>();
            for (int r = 0; r <= ws2.getLastRowNum(); r++) {
                Row row = ws2.getRow(r);
                if (row == null) continue;
                String bm = s(row.getCell(1));
                if (bm.isEmpty()) continue;
                userByBm.put(bm, new String[]{
                        s(row.getCell(0)),   // 户号
                        s(row.getCell(2)),   // 客户名称
                        s(row.getCell(3))    // 用电地址
                });
            }

            // ---- 逐行生成记录 ----
            for (int r = 1; r <= ws.getLastRowNum(); r++) {
                Row row = ws.getRow(r);
                if (row == null) continue;
                String bm = s(row.getCell(iBm));
                if (bm.isEmpty() || bm.equals("电能表条形码")) continue;
                String hh = s(row.getCell(iHh));
                String tq = s(row.getCell(iTq));

                String[] u = userByBm.get(bm);
                if (u == null) {
                    // 未匹配：只保留电能表条形码，客户编号/名称/地址留空
                    Rec rec = new Rec();
                    rec.tq = tq; rec.tqmc = tq; rec.bm = bm;
                    rec.khbh = ""; rec.name = ""; rec.addr = "";
                    rec.matched = false;
                    unmatched.add(rec);
                    missing.add(bm);
                } else {
                    Rec rec = new Rec();
                    rec.tq = tq; rec.tqmc = tq; rec.bm = bm;
                    rec.khbh = (!hh.isEmpty()) ? hh : u[0];
                    rec.name = u[1];
                    rec.addr = u[2];
                    rec.matched = true;
                    matched.add(rec);
                }
            }
        } finally {
            closeQuietly(wb);
        }

        List<Rec> records = new ArrayList<>(matched);
        records.addAll(unmatched);

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("records", records);
        out.put("missing", missing);
        return out;
    }

    private static int colIndex(List<String> hdr, String name) {
        for (int i = 0; i < hdr.size(); i++) {
            if (name.equals(hdr.get(i))) return i;
        }
        throw new IllegalStateException("在「" + SHEET_BOX + "」中找不到列 " + name
                + "，实际表头: " + hdr);
    }

    /**
     * 按模板写客户档案（.xls / HSSF）。
     * 输出 <文件夹名>用户.xls
     */
    public static void writeCustomerXls(File tpl, File out, List<Rec> records) throws Exception {
        Workbook src = null;
        Workbook wb = null;
        File tmp = null;
        try {
            FileInputStream fis = new FileInputStream(tpl);
            src = WorkbookFactory.create(fis);
            fis.close();

            // 复制模板为可写工作簿（HSSF 保留原格式）
            Sheet srcSheet = src.getSheetAt(0);
            wb = new HSSFWorkbook();
            copySheet(srcSheet, wb);

            Sheet ws = wb.getSheetAt(0);

            // 清空数据区（含模板自带的示例数据与合并说明行）
            int lastRow = Math.max(ws.getLastRowNum() + 1, 25);
            for (int r = DATA_START_ROW - 1; r < lastRow; r++) {
                Row row = ws.getRow(r);
                if (row == null) continue;
                for (int c = 0; c < 9; c++) {
                    Cell cell = row.getCell(c);
                    if (cell != null) row.removeCell(cell);
                }
                // 取消合并（A20:I20 「以下5条为示例数据」）
                for (int c = 0; c < 9; c++) {
                    if (row.getCell(c) != null) {
                        // HSSF 无法直接 removeMergedRegion 单格，逐个移除整个区域
                        break;
                    }
                }
            }
            removeMergedRegions(ws);

            int n = records.size();
            int last = DATA_START_ROW + n - 1;

            for (int i = 0; i < n; i++) {
                Rec rec = records.get(i);
                int r = DATA_START_ROW - 1 + i;
                Row row = ws.getRow(r);
                if (row == null) row = ws.createRow(r);

                String[] vals = new String[]{
                        rec.tq,      // 台区编号(必填)
                        rec.tqmc,    // 台区名称(必填)
                        rec.bm,      // 电能表编号
                        rec.khbh,    // 客户编号
                        rec.name,    // 客户名称
                        rec.addr,    // 用电地址
                        rec.name,    // 联系人(必填) = 客户名称
                        "",          // 联系手机 留空
                        ""           // 证件编号 留空
                };
                for (int c = 0; c < vals.length; c++) {
                    Cell cell = row.createCell(c);
                    String v = vals[c];
                    // 编号类列（台区编号/台区名称/电能表编号/客户编号）以文本写入
                    if (c == C_TQBH || c == C_TQMC || c == C_DBNB || c == C_KHBH) {
                        if (v != null && !v.isEmpty()) {
                            cell.setCellValue(v);
                            cell.setCellType(CellType.STRING);
                        } else {
                            // 未匹配行：这些列真正留空
                            cell.setBlank();
                        }
                    } else {
                        if (v == null) v = "";
                        cell.setCellValue(v);
                    }
                }
            }

            // 写临时文件再改名（避免半写文件）
            tmp = new File(out.getParentFile(), "." + out.getName() + ".tmp");
            OutputStream os = new FileOutputStream(tmp);
            wb.write(os);
            os.flush();
            os.close();
            wb.close();
            wb = null;

            if (out.exists() && !out.delete()) {
                Log.w(TAG, "无法删除旧文件: " + out);
            }
            if (!tmp.renameTo(out)) {
                // rename 失败则退回复制
                copyFile(tmp, out);
                tmp.delete();
            }
            tmp = null;
        } finally {
            closeQuietly(wb);
            closeQuietly(src);
            if (tmp != null && tmp.exists()) tmp.delete();
        }
    }

    /** 把源 sheet 复制到目标 workbook（含基本样式） */
    private static void copySheet(Sheet src, Workbook dstWb) {
        Sheet dst = dstWb.createSheet(src.getSheetName());
        dst.setDefaultColumnWidth(src.getDefaultColumnWidth());
        for (int r = 0; r <= src.getLastRowNum(); r++) {
            Row sRow = src.getRow(r);
            if (sRow == null) continue;
            Row dRow = dst.createRow(r);
            dRow.setHeight(sRow.getHeight());
            for (int c = 0; c < sRow.getLastCellNum(); c++) {
                Cell sCell = sRow.getCell(c);
                if (sCell == null) continue;
                Cell dCell = dRow.createCell(c);
                CellType t = sCell.getCellType();
                if (t == CellType.STRING) {
                    dCell.setCellValue(sCell.getStringCellValue());
                } else if (t == CellType.NUMERIC) {
                    dCell.setCellValue(sCell.getNumericCellValue());
                } else if (t == CellType.BOOLEAN) {
                    dCell.setCellValue(sCell.getBooleanCellValue());
                } else if (t == CellType.FORMULA) {
                    dCell.setCellValue(sCell.getCellFormula());
                }
                dCell.setCellStyle(sCell.getCellStyle());
            }
        }
        for (int i = 0; i < src.getNumMergedRegions(); i++) {
            dst.addMergedRegion(src.getMergedRegion(i).copy());
        }
    }

    private static void removeMergedRegions(Sheet ws) {
        for (int i = ws.getNumMergedRegions() - 1; i >= 0; i--) {
            ws.removeMergedRegion(i);
        }
    }

    // ------------------------------------------------------------------ 功能②
    /**
     * 把 xls 中整格等于 "0杆" 的单元格改成 newValue，原地保存（格式不变）。
     * 返回修改处数。
     */
    public static int modifyAndroidXls(File xls, String newValue) throws Exception {
        Workbook wb = null;
        try {
            FileInputStream fis = new FileInputStream(xls);
            wb = WorkbookFactory.create(fis);
            fis.close();

            int total = 0;
            for (int si = 0; si < wb.getNumberOfSheets(); si++) {
                Sheet ws = wb.getSheetAt(si);
                for (int r = 0; r <= ws.getLastRowNum(); r++) {
                    Row row = ws.getRow(r);
                    if (row == null) continue;
                    for (int c = 0; c < row.getLastCellNum(); c++) {
                        Cell cell = row.getCell(c);
                        if (cell == null) continue;
                        if (isZeroGan(s(cell))) {
                            cell.setCellValue(newValue);
                            total++;
                        }
                    }
                }
            }
            if (total > 0) {
                File tmp = new File(xls.getParentFile(), "." + xls.getName() + ".tmp");
                OutputStream os = new FileOutputStream(tmp);
                wb.write(os);
                os.flush();
                os.close();
                wb.close();
                wb = null;
                if (xls.exists() && !xls.delete()) {
                    Log.w(TAG, "无法删除旧文件: " + xls);
                }
                if (!tmp.renameTo(xls)) {
                    copyFile(tmp, xls);
                    tmp.delete();
                }
            }
            return total;
        } finally {
            closeQuietly(wb);
        }
    }

    /** 复制安卓表为「采集版」：0杆 原样保留，集中放到 <根>/_采集软件数据/<台区名>/ */
    public static File makeCollectorCopy(File src, String folderName, File root) {
        try {
            File destDir = new File(new File(root, COLLECTOR_DIR), folderName);
            if (!destDir.exists() && !destDir.mkdirs()) return null;
            String base = src.getName();
            String stem = base.toLowerCase().endsWith(".xls")
                    ? base.substring(0, base.length() - 4) : base;
            File dst = new File(destDir, stem + ".xls");
            copyFile(src, dst);
            return dst;
        } catch (Exception e) {
            Log.w(TAG, "采集版副本失败: " + e);
            return null;
        }
    }

    // ------------------------------------------------------------------ 功能③
    /** 递归收集 root 及其子目录下的全部 PDF */
    public static List<File> collectPdfFiles(File root) {
        List<File> out = new ArrayList<>();
        collectPdfFiles(root, out);
        return out;
    }

    private static void collectPdfFiles(File dir, List<File> out) {
        File[] fs = dir.listFiles();
        if (fs == null) return;
        for (File f : fs) {
            if (f.isDirectory()) {
                // 跳过目标目录本身，避免重复处理
                if (f.getName().equals(PDF_SUBDIR)) continue;
                collectPdfFiles(f, out);
            } else if (f.getName().toLowerCase().endsWith(".pdf")) {
                out.add(f);
            }
        }
    }

    /** 把 PDF 移到 <root>/打印/；同名则加 (1)(2)… 序号，绝不覆盖 */
    public static File moveToPrintDir(File pdf, File printDir) {
        if (!printDir.exists()) printDir.mkdirs();
        String name = pdf.getName();
        File dst = new File(printDir, name);
        int i = 1;
        while (dst.exists()) {
            int dot = name.lastIndexOf('.');
            String stem = dot > 0 ? name.substring(0, dot) : name;
            String ext = dot > 0 ? name.substring(dot) : "";
            dst = new File(printDir, stem + "(" + i + ")" + ext);
            i++;
        }
        if (pdf.renameTo(dst)) return dst;
        // rename 失败（跨卷等）→ 复制+删除
        try {
            copyFile(pdf, dst);
            pdf.delete();
            return dst;
        } catch (Exception e) {
            Log.w(TAG, "移动 PDF 失败: " + e);
            return null;
        }
    }

    // ------------------------------------------------------------------ 功能④
    /** 复制 *安卓.xls 到 <root>/_采集软件数据/<台区名>/（幂等：已存在不重复复制） */
    public static File copyToCollectorDir(File src, String folderName, File root) {
        try {
            File destDir = new File(new File(root, COLLECTOR_DIR), folderName);
            if (!destDir.exists() && !destDir.mkdirs()) return null;
            File dst = new File(destDir, src.getName());
            if (dst.exists()) return dst;   // 幂等
            copyFile(src, dst);
            return dst;
        } catch (Exception e) {
            Log.w(TAG, "复制采集数据失败: " + e);
            return null;
        }
    }

    // ------------------------------------------------------------------ 通用
    public static void copyFile(File src, File dst) throws Exception {
        FileInputStream in = new FileInputStream(src);
        FileOutputStream out = new FileOutputStream(dst);
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        out.flush();
        out.close();
        in.close();
    }

    private static void closeQuietly(Workbook wb) {
        if (wb == null) return;
        try { wb.close(); } catch (Exception e) { /* ignore */ }
    }

    public boolean isStoppedNow() {
        return shouldStop();
    }
}
