package com.power.collector.importer;

import java.io.File;
import java.io.FileReader;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Excel 解析验证程序（桌面 JVM 运行，不依赖 Android）。
 *
 * 读 _testdata/*.xls|*.xlsx → 解析 → 输出 JSON → 与 expected.json 比对。
 *
 * 用法: java ... ExcelParseCheck <testdata_dir>
 */
public class ExcelParseCheck {

    public static void main(String[] args) throws Exception {
        String dir = args.length > 0 ? args[0] : "_testdata";
        File d = new File(dir);

        File[] files = d.listFiles();
        if (files == null) {
            System.out.println("目录不存在: " + dir);
            return;
        }

        List<File> excels = new ArrayList<>();
        for (File f : files) {
            String n = f.getName().toLowerCase();
            if (f.isFile() && (n.endsWith(".xls") || n.endsWith(".xlsx"))
                    && ExcelParser.isTaquFileName(f.getName())) {
                excels.add(f);
            }
        }
        java.util.Collections.sort(excels);
        System.out.println("待解析文件: " + excels.size());

        for (File f : excels) {
            System.out.println("\n=== " + f.getName() + " ===");
            ExcelParser.FileResult fr = ExcelParser.parse(f, "邮亭镇");
            if (fr.error != null) {
                System.out.println("  ❌ 解析错误: " + fr.error);
                continue;
            }
            System.out.println("  总行数=" + fr.totalRows + "  有效设备=" + fr.okCount
                    + "  台区名(兜底)=" + ExcelParser.lineNameFromFile(f.getName()));
            Map<String, Integer> tc = ExcelParser.typeCounts(fr.devices);
            System.out.println("  类型统计: " + tc);

            StringBuilder sb = new StringBuilder();
            for (Device dev : fr.devices) {
                sb.append(String.format("    id=%-3d %-12s %-16s lon=%.6f lat=%.6f line=%s%n",
                        dev.id, dev.name, dev.type, dev.lon, dev.lat, dev.line));
            }
            System.out.print(sb);

            // 输出 JSON 供 Python 比对
            String outName = f.getName().replace('.', '_') + ".json";
            try (PrintWriter pw = new PrintWriter(new File(d, outName),
                    StandardCharsets.UTF_8)) {
                pw.println(toJson(fr.devices));
            }
        }
        System.out.println("\n完成");
    }

    static String toJson(List<Device> devices) {
        StringBuilder sb = new StringBuilder("[\n");
        for (int i = 0; i < devices.size(); i++) {
            Device d = devices.get(i);
            sb.append("  {");
            sb.append("\"id\":").append(d.id).append(",");
            sb.append("\"name\":\"").append(d.name).append("\",");
            sb.append("\"line\":\"").append(d.line).append("\",");
            sb.append("\"lon\":").append(d.lon).append(",");
            sb.append("\"lat\":").append(d.lat).append(",");
            sb.append("\"type\":\"").append(d.type).append("\"");
            sb.append("}");
            if (i < devices.size() - 1) sb.append(",");
            sb.append("\n");
        }
        sb.append("]");
        return sb.toString();
    }
}
