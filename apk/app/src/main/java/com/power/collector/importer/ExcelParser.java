package com.power.collector.importer;

import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.CellType;
import org.apache.poi.ss.usermodel.DateUtil;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.Workbook;
import org.apache.poi.ss.usermodel.WorkbookFactory;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Excel 台区数据解析器。
 *
 * 规则（与 Py 版 excel_loader.py 完全一致，用户 2026-09-25/26 指定）：
 *   1. 文件名含「安卓」→ 一个文件 = 一个台区
 *   2. Sheet 优先「墙支架」，否则第一个有数据的 sheet
 *   3. 表头：A=线路编号 B=线路名称 C=设备名称 D=经度 E=纬度 F=备注
 *   4. 读 C 列分类：
 *        「0杆」→ 杆塔，且同位置额外生成变压器（名取 A 列）
 *        含 计量/表箱/电表/表位 → 计量箱
 *        含「杆」→ 杆塔
 *        纯数字 → 接入点（墙支架）
 *        否则 → 其他
 *   5. 坐标在 D/E 列；非数字则自动探测"两列连续的 7~8 位小数"
 *   6. 按 (lon,lat) 去重；粗略过滤非中国坐标
 */
public final class ExcelParser {

    private static final String TAG = "ExcelParser";

    private ExcelParser() {}

    /** 单个文件的解析结果 */
    public static class FileResult {
        public String district = "";
        public String fileName = "";
        public final List<Device> devices = new ArrayList<>();
        public int totalRows = 0;
        public int okCount = 0;
        public String error = null;
    }

    /** 判断文件名是否是要导入的台区文件（含「安卓」） */
    public static boolean isTaquFileName(String name) {
        if (name == null) return false;
        return name.contains("安卓");
    }

    /** 解析单个 Excel 文件 */
    public static FileResult parse(File xls, String district) {
        FileResult res = new FileResult();
        res.district = district == null ? "" : district;
        res.fileName = xls.getName();

        Workbook wb = null;
        try (InputStream in = new FileInputStream(xls)) {
            wb = WorkbookFactory.create(in);
            Sheet sheet = pickSheet(wb);
            if (sheet == null) {
                res.error = "没有数据 sheet";
                return res;
            }

            final int maxCol = maxCol(sheet);
            // 探测经纬度列：默认 D(3)/E(4)，若 R1 该两列非数字则自动找
            int lonCol = 3, latCol = 4;
            if (sheet.getLastRowNum() >= 1) {
                String d1 = cellStr(sheet.getRow(1), 3);
                String e1 = cellStr(sheet.getRow(1), 4);
                if (!isDecimal(d1) || !isDecimal(e1)) {
                    List<Integer> cand = new ArrayList<>();
                    for (int c = 0; c < maxCol; c++) {
                        String v = cellStr(sheet.getRow(1), c);
                        if (isDecimal(v)) cand.add(c);
                    }
                    if (cand.size() >= 2) {
                        lonCol = cand.get(0);
                        latCol = cand.get(1);
                    }
                }
            }

            Set<String> seen = new HashSet<>();
            int uid = 0;
            String fileLineFallback = lineNameFromFile(xls.getName());

            for (int r = 1; r <= sheet.getLastRowNum(); r++) {
                Row row = sheet.getRow(r);
                if (row == null) continue;
                res.totalRows++;
                String cVal = cellStr(row, 2).trim();
                if (cVal.isEmpty()) continue;

                String aVal = cellStr(row, 0).trim();
                String line = !aVal.isEmpty() ? aVal : fileLineFallback;

                String lonS = cellStr(row, lonCol).trim();
                String latS = cellStr(row, latCol).trim();
                double lon, lat;
                try {
                    lon = Double.parseDouble(lonS);
                    lat = Double.parseDouble(latS);
                } catch (NumberFormatException e) {
                    continue;
                }
                if (!(lon > 70 && lon < 140 && lat > 15 && lat < 55)) continue;

                String type;
                String txName = null;
                if ("0杆".equals(cVal)) {
                    type = Device.TYPE_POLE;
                    txName = aVal.trim();
                } else if (containsAny(cVal, "计量", "表箱", "电表", "表位")) {
                    type = Device.TYPE_METER_BOX;
                } else if (cVal.contains("杆")) {
                    type = Device.TYPE_POLE;
                } else if (isPureNumber(cVal)) {
                    type = Device.TYPE_ACCESS_POINT;
                } else {
                    type = Device.TYPE_OTHER;
                }

                uid++;
                String key = Math.round(lon * 1e6) + "_" + Math.round(lat * 1e6);
                if (seen.add(key)) {
                    Device d = new Device();
                    d.id = uid;
                    d.name = cVal;
                    d.line = line;
                    d.lon = lon;
                    d.lat = lat;
                    d.type = type;
                    d.district = res.district;
                    d.srcFile = res.fileName;
                    res.devices.add(d);
                    res.okCount++;
                }

                // 0杆 → 同位置额外生成变压器
                if (txName != null && !txName.isEmpty()) {
                    uid++;
                    String tkey = "TX_" + Math.round(lon * 1e6) + "_" + Math.round(lat * 1e6);
                    if (seen.add(tkey)) {
                        Device t = new Device();
                        t.id = uid;
                        t.name = txName;
                        t.line = line;
                        t.lon = lon;
                        t.lat = lat;
                        t.type = Device.TYPE_TRANSFORMER;
                        t.district = res.district;
                        t.srcFile = res.fileName;
                        t.props.put("via_0pole", cVal);
                        t.props.put("a_col_name", txName);
                        res.devices.add(t);
                        res.okCount++;
                    }
                }
            }
        } catch (Throwable t) {
            res.error = t.getClass().getSimpleName() + ": " + t.getMessage();
            PCLog.e(TAG, "parse failed: " + xls.getAbsolutePath(), t);
        } finally {
            if (wb != null) { try { wb.close(); } catch (Exception ignored) {} }
        }
        return res;
    }

    // ---------------------------------------------------------------- 工具

    private static Sheet pickSheet(Workbook wb) {
        Sheet s = wb.getSheet("墙支架");
        if (s != null) return s;
        for (int i = 0; i < wb.getNumberOfSheets(); i++) {
            Sheet sh = wb.getSheetAt(i);
            if (sh.getLastRowNum() > 0) return sh;
        }
        return null;
    }

    private static int maxCol(Sheet sheet) {
        int max = 0;
        for (Row r : sheet) { if (r.getLastCellNum() > max) max = r.getLastCellNum(); }
        return max;
    }

    /**
     * 取单元格字符串。
     *
     * 【不用 DataFormatter 的原因】POI 的 DataFormatter 依赖 java.beans.PropertyDescriptor，
     * 而 Android【不提供】java.beans.* —— 类加载时就会 NoClassDefFoundError。
     * 同样不用 FormulaEvaluator（它依赖 javax.xml.parsers 等，且更重）。
     * 这里只用 POI 最底层的 Cell API，避开所有 Android 缺失的 JDK 包。
     */
    private static String cellStr(Row row, int col) {
        if (row == null) return "";
        Cell c = row.getCell(col);
        if (c == null) return "";
        try {
            CellType t = c.getCellType();
            if (t == CellType.STRING) {
                String s = c.getStringCellValue();
                return s == null ? "" : s;
            }
            if (t == CellType.NUMERIC) {
                if (DateUtil.isCellDateFormatted(c)) {
                    // 日期：转成 yyyy-MM-dd HH:mm:ss
                    try {
                        return new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss")
                                .format(c.getDateCellValue());
                    } catch (Throwable ignored) {
                        return trimNum(c.getNumericCellValue());
                    }
                }
                return trimNum(c.getNumericCellValue());
            }
            if (t == CellType.BOOLEAN) {
                return c.getBooleanCellValue() ? "TRUE" : "FALSE";
            }
            if (t == CellType.BLANK) return "";
            if (t == CellType.FORMULA) {
                // 公式：取缓存结果类型（不求值，避免依赖 evaluator）
                try {
                    CellType ct = c.getCachedFormulaResultType();
                    if (ct == CellType.STRING) {
                        String s = c.getStringCellValue();
                        return s == null ? "" : s;
                    }
                    if (ct == CellType.NUMERIC) return trimNum(c.getNumericCellValue());
                    if (ct == CellType.BOOLEAN) return c.getBooleanCellValue() ? "TRUE" : "FALSE";
                } catch (Throwable ignored) {}
                return "";
            }
            // ERROR / 其它
            try {
                String s = c.toString();
                return s == null ? "" : s;
            } catch (Throwable ignored) {
                return "";
            }
        } catch (Throwable t) {
            return "";
        }
    }

    /** 数值转字符串，去掉浮点尾零（对齐 Py 版 str(float)） */
    private static String trimNum(double v) {
        if (v == Math.rint(v) && !Double.isInfinite(v)) {
            long l = (long) v;
            // 保留足够小数位，去掉尾零
            String s = String.valueOf(v);
            if (s.endsWith(".0")) s = String.valueOf(l);
            return s;
        }
        return String.valueOf(v);
    }

    /** 是否像"7~8 位小数"的数字（Py 版用 \d+\.\d+ 判定） */
    private static boolean isDecimal(String s) {
        if (s == null || s.isEmpty()) return false;
        int dot = s.indexOf('.');
        if (dot <= 0 || dot == s.length() - 1) return false;
        for (int i = 0; i < s.length(); i++) {
            char ch = s.charAt(i);
            if (i == dot) continue;
            if (ch < '0' || ch > '9') return false;
        }
        return true;
    }

    /** 是否纯数字（含小数） */
    private static boolean isPureNumber(String s) {
        if (s == null || s.isEmpty()) return false;
        boolean dot = false;
        for (int i = 0; i < s.length(); i++) {
            char ch = s.charAt(i);
            if (ch == '.') {
                if (dot) return false;
                dot = true;
                continue;
            }
            if (ch < '0' || ch > '9') return false;
        }
        return true;
    }

    private static boolean containsAny(String s, String... kws) {
        for (String k : kws) if (s.contains(k)) return true;
        return false;
    }

    /** 从文件名推导台区名（去「柱上变安卓」/「安卓」后缀） */
    static String lineNameFromFile(String fileName) {
        String base = fileName;
        int dot = base.lastIndexOf('.');
        if (dot > 0) base = base.substring(0, dot);
        if (base.endsWith("柱上变安卓")) {
            String s = base.substring(0, base.length() - "柱上变安卓".length());
            return s.isEmpty() ? base : s;
        }
        if (base.endsWith("安卓")) {
            String s = base.substring(0, base.length() - "安卓".length());
            return s.isEmpty() ? base : s;
        }
        return base;
    }

    /** 统计各类型设备数 */
    public static Map<String, Integer> typeCounts(List<Device> devices) {
        Map<String, Integer> m = new LinkedHashMap<>();
        for (Device d : devices) m.put(d.type, m.getOrDefault(d.type, 0) + 1);
        return m;
    }
}
