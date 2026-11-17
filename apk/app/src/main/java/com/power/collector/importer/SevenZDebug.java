package com.power.collector.importer;

import java.io.File;
import java.io.InputStream;
import java.io.OutputStream;

/** 调试 7z 读取：打印每个 entry 的名字与流状态。 */
public class SevenZDebug {
    public static void main(String[] args) throws Exception {
        File f = new File(args.length > 0 ? args[0] : "_testdata/_pkgs/台区数据.7z");
        File out = new File("_testdata/_pkgs/_dbg7z");
        out.mkdirs();
        try (org.apache.commons.compress.archivers.sevenz.SevenZFile szf =
                     new org.apache.commons.compress.archivers.sevenz.SevenZFile(f)) {
            org.apache.commons.compress.archivers.sevenz.SevenZArchiveEntry e;
            while ((e = szf.getNextEntry()) != null) {
                System.out.println("entry: name=[" + e.getName() + "]"
                        + " isDir=" + e.isDirectory()
                        + " size=" + e.getSize()
                        + " hasStream=" + e.hasStream());
                if (e.isDirectory()) continue;
                File target = new File(out, e.getName());
                System.out.println("  target=" + target.getAbsolutePath()
                        + " parentExists=" + (target.getParentFile() != null
                            && target.getParentFile().exists()));
                try (InputStream in = szf.getInputStream(e)) {
                    System.out.println("  stream=" + in);
                    if (in == null) { System.out.println("  !! stream is null"); continue; }
                    byte[] buf = new byte[4096];
                    int n = in.read(buf);
                    System.out.println("  first read = " + n + " bytes");
                } catch (Throwable t) {
                    System.out.println("  read failed: " + t);
                }
            }
        } catch (Throwable t) {
            System.out.println("OPEN FAILED: " + t);
            t.printStackTrace();
        }
    }
}
