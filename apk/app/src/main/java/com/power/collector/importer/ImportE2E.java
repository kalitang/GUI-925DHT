package com.power.collector.importer;

import java.io.File;
import java.util.Map;

/**
 * 端到端导入验证（桌面 JVM）：
 *   压缩包 → 解压 → 解析台区 → 构建连线 → 写 devices.json + devices_bundle.json
 */
public class ImportE2E {

    public static void main(String[] args) throws Exception {
        String pkgDir = args.length > 0 ? args[0] : "_testdata/_pkgs";
        File d = new File(pkgDir);
        File[] fs = d.listFiles();
        java.util.Arrays.sort(fs);

        int ok = 0, fail = 0;
        for (File f : fs) {
            if (!f.isFile()) continue;
            String n = f.getName().toLowerCase();
            if (!(n.endsWith(".zip") || n.endsWith(".7z") || n.endsWith(".tar")
                    || n.endsWith(".gz") || n.endsWith(".bz2") || n.endsWith(".xz"))) {
                continue;
            }
            System.out.println("\n=== " + f.getName() + " ===");

            // 1) 解压到独立目录
            File work = new File(d, "_e2e_" + f.getName().replace('.', '_'));
            deleteAll(work);
            work.mkdirs();
            ArchiveExtractor.Result ar = ArchiveExtractor.extract(f, work);
            if (ar.error != null) {
                System.out.println("  ❌ 解压失败: " + ar.error);
                fail++;
                continue;
            }
            System.out.println("  解压: " + ar.files + " 个文件");

            // 2) 导入
            ImportManager.Result ir = ImportManager.importFromDirectory(null, work, false);
            System.out.println("  导入: ok=" + ir.ok + " 设备=" + ir.devices
                    + " 连线=" + ir.links + " 台区=" + ir.taquCount
                    + " 文件=" + ir.filesParsed + "/" + ir.filesScanned);
            System.out.println("  消息: " + ir.message);
            if (ir.typeCounts != null) {
                System.out.println("  类型: " + ir.typeCounts);
            }

            // 3) 校验产物
            File outDir = new File(work.getParentFile(), "_out_data");
            File devJson = new File(outDir, "devices.json");
            File bundleJson = new File(outDir, "devices_bundle.json");
            boolean devOk = devJson.isFile() && devJson.length() > 100;
            boolean bunOk = bundleJson.isFile() && bundleJson.length() > 100;

            if (ir.ok && devOk && bunOk && ir.devices == 15) {
                System.out.println("  ✅ 通过：15 设备 + " + ir.links + " 连线，JSON 已生成");
                ok++;
            } else {
                System.out.println("  ❌ 失败（devices=" + ir.devices
                        + " devJson=" + devOk + " bundle=" + bunOk + "）");
                fail++;
            }
        }
        System.out.println("\n通过 " + ok + " / 失败 " + fail);
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
