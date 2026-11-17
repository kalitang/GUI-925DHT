package com.power.collector.importer;

import android.content.Context;
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 导入管理器：扫描目录 → 解析台区 Excel → 生成 devices.json + devices_bundle.json。
 *
 * 逻辑对齐 Py 版 excel_loader.load_dir() + link_builder.build_links()：
 *   1) 递归找文件名含「安卓」的 .xls/.xlsx
 *   2) 每个文件 = 一个台区；台区名优先取 A 列（线路编号）
 *   3) 跨文件去重 key = (lon, lat, name, type)
 *   4) 重新分配全局唯一 id (1..N)
 *   5) 按台区分组构建拓扑连线
 *   6) 写出 devices.json（设备）+ devices_bundle.json（设备+连线）
 */
public final class ImportManager {

    private static final String TAG = "ImportManager";
    /** 连线最大距离（与 Py 版一致） */
    private static final double MAX_LINK_DIST_M = 500.0;

    private ImportManager() {}

    /** 导入结果 */
    public static class Result {
        public boolean ok = false;
        public String message = "";
        public int devices = 0;
        public int links = 0;
        public int filesScanned = 0;
        public int filesParsed = 0;
        public int taquCount = 0;
        public Map<String, Integer> typeCounts;
        public String format = "";
        /** 生成的数据目录（devices.json 所在） */
        public File dataDir;
        /** 解压出的目录（若来自压缩包） */
        public File extractedDir;
    }

    /**
     * 从目录导入。
     *
     * @param ctx       上下文（用于拿 filesDir）；桌面测试可传 null（用 baseDir）
     * @param srcDir    数据目录（解压后的，或用户选择的目录）
     * @param extracted 是否是本次解压出来的临时目录（决定是否保留）
     */
    public static Result importFromDirectory(Context ctx, File srcDir, boolean extracted) {
        Result r = new Result();
        if (srcDir == null || !srcDir.isDirectory()) {
            r.message = "目录不存在";
            return r;
        }
        r.extractedDir = extracted ? srcDir : null;

        // 1) 找台区文件
        List<File> excelFiles = new ArrayList<>();
        List<File> archives = new ArrayList<>();
        scanDir(srcDir, excelFiles, archives);
        r.filesScanned = excelFiles.size() + archives.size();

        // 2) 没有 Excel 就找压缩包并解压
        if (excelFiles.isEmpty() && !archives.isEmpty()) {
            File unpackRoot = new File(srcDir, "_unpacked");
            unpackRoot.mkdirs();
            for (File a : archives) {
                ArchiveExtractor.Result ar = ArchiveExtractor.extract(a, unpackRoot);
                PCLog.i(TAG, "extract " + a.getName() + " fmt=" + ar.format
                        + " files=" + ar.files + " err=" + ar.error);
            }
            excelFiles.clear();
            archives.clear();
            scanDir(unpackRoot, excelFiles, archives);
        }

        if (excelFiles.isEmpty()) {
            r.message = "未找到台区文件（文件名需含「安卓」的 .xls/.xlsx）";
            return r;
        }

        // 3) 逐个解析
        List<Device> all = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (File f : excelFiles) {
            if (!ExcelParser.isTaquFileName(f.getName())) continue;
            String district = guessDistrict(f, srcDir);
            ExcelParser.FileResult fr = ExcelParser.parse(f, district);
            if (fr.error != null) {
                PCLog.w(TAG, "解析失败 " + f.getName() + ": " + fr.error);
                continue;
            }
            r.filesParsed++;
            for (Device d : fr.devices) {
                // 去重 key 含台区（line）：同坐标不同台区的设备各自保留，
                // 否则 APK 导入会把异台区设备吃掉 → 杆塔着色冲突簇消失
                String key = Math.round(d.lon * 1e6) + "_" + Math.round(d.lat * 1e6)
                           + "_" + d.line + "_" + d.name + "_" + d.type;
                if (!seen.add(key)) continue;
                all.add(d);
            }
        }

        if (all.isEmpty()) {
            r.message = "解析完成但没有有效设备（检查坐标是否为中国境内经纬度）";
            return r;
        }

        // 4) 重新分配全局唯一 id
        for (int i = 0; i < all.size(); i++) all.get(i).id = i + 1;

        // 5) 构建连线
        List<LinkBuilder.Link> links = LinkBuilder.build(all, MAX_LINK_DIST_M);

        // 6) 写出 JSON
        File dataDir = (ctx != null)
                ? new File(ctx.getFilesDir(), "data")
                : new File(srcDir.getParentFile(), "_out_data");
        if (!dataDir.exists() && !dataDir.mkdirs()) {
            r.message = "无法创建数据目录";
            return r;
        }
        try {
            writeDevices(dataDir, all);
            writeBundle(dataDir, all, links);
        } catch (Exception e) {
            r.message = "写入失败: " + e.getMessage();
            PCLog.e(TAG, "write failed", e);
            return r;
        }

        r.ok = true;
        r.devices = all.size();
        r.links = links.size();
        r.taquCount = countLines(all);
        r.typeCounts = ExcelParser.typeCounts(all);
        r.dataDir = dataDir;
        r.message = "导入成功：" + r.taquCount + " 个台区 / " + r.devices
                + " 个设备 / " + r.links + " 条连线";
        return r;
    }

    /** 递归扫描：分 Excel 与压缩包 */
    private static void scanDir(File dir, List<File> excel, List<File> archives) {
        File[] fs = dir.listFiles();
        if (fs == null) return;
        Arrays.sort(fs);
        for (File f : fs) {
            if (f.isDirectory()) {
                if (f.getName().startsWith(".")) continue;
                scanDir(f, excel, archives);
                continue;
            }
            String n = f.getName().toLowerCase();
            if (n.endsWith(".xls") || n.endsWith(".xlsx")) {
                if (ExcelParser.isTaquFileName(f.getName())) excel.add(f);
            } else if (n.endsWith(".zip") || n.endsWith(".7z") || n.endsWith(".rar")
                    || n.endsWith(".tar") || n.endsWith(".gz") || n.endsWith(".bz2")
                    || n.endsWith(".xz")) {
                archives.add(f);
            }
        }
    }

    /** 镇街名：相对路径第一段去掉数字前缀（对齐 Py 版 _guess_district） */
    static String guessDistrict(File f, File root) {
        try {
            String rel = f.getAbsolutePath().substring(root.getAbsolutePath().length())
                    .replace(File.separatorChar, '/');
            String[] parts = rel.split("/");
            if (parts.length >= 2) {
                Matcher m = Pattern.compile("^([^\\d]+)").matcher(parts[0]);
                if (m.find() && m.group(1).trim().length() > 0) return m.group(1).trim();
            }
        } catch (Exception ignored) {}
        String base = root.getName();
        return base.isEmpty() ? "未知" : base;
    }

    static int countLines(List<Device> devices) {
        Set<String> s = new HashSet<>();
        for (Device d : devices) s.add(d.line == null || d.line.isEmpty() ? "_unknown" : d.line);
        return s.size();
    }

    // ------------------------------------------------------------ 写 JSON

    static void writeDevices(File dataDir, List<Device> devices) throws Exception {
        File out = new File(dataDir, "devices.json");
        try (Writer w = new OutputStreamWriter(new FileOutputStream(out), StandardCharsets.UTF_8)) {
            w.write("[");
            for (int i = 0; i < devices.size(); i++) {
                if (i > 0) w.write(",");
                w.write(jsonDevice(devices.get(i)));
            }
            w.write("]");
        }
    }

    static void writeBundle(File dataDir, List<Device> devices,
                            List<LinkBuilder.Link> links) throws Exception {
        File out = new File(dataDir, "devices_bundle.json");
        try (Writer w = new OutputStreamWriter(new FileOutputStream(out), StandardCharsets.UTF_8)) {
            w.write("{\"devices\":[");
            for (int i = 0; i < devices.size(); i++) {
                if (i > 0) w.write(",");
                w.write(jsonDevice(devices.get(i)));
            }
            w.write("],\"links\":[");
            for (int i = 0; i < links.size(); i++) {
                if (i > 0) w.write(",");
                LinkBuilder.Link l = links.get(i);
                w.write("[" + l.parent + "," + l.child + "," + l.level + "]");
            }
            w.write("],\"meta\":{\"device_count\":" + devices.size()
                    + ",\"link_count\":" + links.size()
                    + ",\"source\":\"apk-import\"}}");
        }
    }

    static String jsonDevice(Device d) {
        StringBuilder sb = new StringBuilder(160);
        sb.append("{\"id\":").append(d.id)
          .append(",\"name\":").append(quote(d.name))
          .append(",\"line\":").append(quote(d.line))
          .append(",\"lon\":").append(d.lon)
          .append(",\"lat\":").append(d.lat)
          .append(",\"type\":").append(quote(d.type))
          .append(",\"district\":").append(quote(d.district))
          .append(",\"srcFile\":").append(quote(d.srcFile))
          .append(",\"props\":{");
        boolean first = true;
        for (Map.Entry<String, String> e : d.props.entrySet()) {
            if (!first) sb.append(",");
            first = false;
            sb.append(quote(e.getKey())).append(":").append(quote(e.getValue()));
        }
        sb.append("}}");
        return sb.toString();
    }

    static String quote(String s) {
        if (s == null) return "\"\"";
        StringBuilder sb = new StringBuilder(s.length() + 8);
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        sb.append('"');
        return sb.toString();
    }
}
