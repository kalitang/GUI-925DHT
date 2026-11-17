package com.power.collector.importer;

import java.io.File;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * 压缩包解压验证程序（桌面 JVM 运行）。
 *
 * 读 _testdata/_pkgs/* → 解压 → 检查 xlsx/csv 是否存在 → 列出解压结果。
 */
public class ArchiveCheck {

    public static void main(String[] args) throws Exception {
        String dir = args.length > 0 ? args[0] : "_testdata/_pkgs";
        File d = new File(dir);
        File[] fs = d.listFiles();
        if (fs == null) {
            System.out.println("目录不存在: " + dir);
            return;
        }
        Arrays.sort(fs);

        int ok = 0, fail = 0;
        for (File f : fs) {
            if (!f.isFile()) continue;
            System.out.println("\n=== " + f.getName() + " ===");
            String fmt = ArchiveExtractor.detectFormat(f);
            System.out.println("  识别格式: " + fmt);

            File out = new File(d, "_out_" + f.getName().replace('.', '_'));
            if (out.exists()) deleteAll(out);
            out.mkdirs();

            ArchiveExtractor.Result r = ArchiveExtractor.extract(f, out);
            System.out.println("  解压结果: format=" + r.format
                    + " files=" + r.files + " bytes=" + r.bytes
                    + " error=" + r.error);

            // 递归列出
            List<String> names = new ArrayList<>();
            listAll(out, out, names);
            System.out.println("  解出 " + names.size() + " 项:");
            for (String n : names) System.out.println("    " + n);

            boolean hasXlsx = names.stream().anyMatch(n -> n.toLowerCase().endsWith(".xlsx"));
            boolean hasCsv = names.stream().anyMatch(n -> n.toLowerCase().endsWith(".csv"));
            boolean hasCn = names.stream().anyMatch(n -> n.contains("丰岩"));

            if (r.error == null && hasXlsx && hasCsv && hasCn) {
                System.out.println("  ✅ 解压正确（含 xlsx + csv + 中文文件名）");
                ok++;
            } else {
                System.out.println("  ❌ 解压结果不符（xlsx=" + hasXlsx
                        + " csv=" + hasCsv + " 中文名=" + hasCn + "）");
                fail++;
            }
        }
        System.out.println("\n通过 " + ok + " / 失败 " + fail);
    }

    static void listAll(File root, File dir, List<String> out) {
        File[] fs = dir.listFiles();
        if (fs == null) return;
        for (File f : fs) {
            if (f.isDirectory()) listAll(root, f, out);
            else {
                String rel = f.getAbsolutePath().substring(root.getAbsolutePath().length() + 1);
                out.add(rel.replace(File.separatorChar, '/'));
            }
        }
    }

    static void deleteAll(File dir) {
        File[] fs = dir.listFiles();
        if (fs == null) return;
        for (File f : fs) {
            if (f.isDirectory()) deleteAll(f);
            f.delete();
        }
    }
}
