package com.power.collector.importer;

import org.apache.commons.compress.archivers.ArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream;
import org.apache.commons.compress.archivers.sevenz.SevenZArchiveEntry;
import org.apache.commons.compress.archivers.sevenz.SevenZFile;
import org.apache.commons.compress.compressors.gzip.GzipCompressorInputStream;
import org.apache.commons.compress.compressors.bzip2.BZip2CompressorInputStream;
import org.apache.commons.compress.compressors.xz.XZCompressorInputStream;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

import com.github.junrar.Junrar;

/**
 * 压缩包解压引擎。
 *
 * 支持格式（按文件头 magic 识别，不依赖扩展名）：
 *   ZIP  (PK\x03\x04)      - java.util.zip
 *   7Z   (7z\xBC\xAF)      - commons-compress SevenZFile
 *   RAR  (Rar!\x1a\x07)    - junrar
 *   TAR  (ustar)           - commons-compress TarArchiveInputStream
 *   GZIP (1f 8b)           - commons-compress GzipCompressorInputStream（单文件）
 *   BZIP2(BZh)             - commons-compress BZip2CompressorInputStream（单文件）
 *   XZ   (fd 37 7a 58 5a)  - commons-compress XZCompressorInputStream（单文件）
 *
 * 全部走"流式解压"，不落地中间文件，兼容 Android。
 */
public final class ArchiveExtractor {

    private static final String TAG = "ArchiveExtractor";
    /** 单个解压文件上限（防 zip 炸弹）：200 MB */
    private static final long MAX_ENTRY_BYTES = 200L * 1024 * 1024;
    /** 解压总上限：2 GB */
    private static final long MAX_TOTAL_BYTES = 2L * 1024 * 1024 * 1024;
    /** 条目路径最大深度 */
    private static final int MAX_PATH_DEPTH = 24;

    /** 解压结果 */
    public static class Result {
        public int files = 0;          // 解出的文件数
        public long bytes = 0;         // 解出总字节
        public String format = "?";     // 识别到的格式
        public String error = null;     // 非 null 表示失败
    }

    private ArchiveExtractor() {}

    /** 识别压缩格式（按 magic bytes） */
    public static String detectFormat(File f) {
        byte[] head = new byte[8];
        int n;
        try (InputStream in = new FileInputStream(f)) {
            n = in.read(head);
        } catch (IOException e) {
            return "unknown";
        }
        if (n < 2) return "unknown";
        // 【重要】byte 是有符号的！必须 & 0xFF 转成无符号再比较，
        // 否则 0x8b(139) 永远匹配不上 byte 的 -117。
        int b0 = head[0] & 0xFF;
        int b1 = head[1] & 0xFF;
        int b2 = n > 2 ? (head[2] & 0xFF) : 0;
        int b3 = n > 3 ? (head[3] & 0xFF) : 0;
        int b4 = n > 4 ? (head[4] & 0xFF) : 0;
        int b5 = n > 5 ? (head[5] & 0xFF) : 0;

        if (b0 == 'P' && b1 == 'K') return "zip";
        if (b0 == 0x37 && b1 == 'z') return "7z";
        if (n >= 7 && b0 == 'R' && b1 == 'a' && b2 == 'r'
                && b3 == '!' && b4 == 0x1a && b5 == 0x07) return "rar";
        if (n >= 4 && b0 == 0x1f && b1 == 0x8b) return "gzip";
        if (n >= 3 && b0 == 'B' && b1 == 'Z' && b2 == 'h') return "bzip2";
        if (n >= 6 && b0 == 0xFD && b1 == '7' && b2 == 'z'
                && b3 == 'X' && b4 == 'Z') return "xz";
        if (n >= 5) {
            // tar: "ustar" 在偏移 257
            try (InputStream in = new FileInputStream(f)) {
                long pos = 257;
                long skipped = 0;
                byte[] buf = new byte[512];
                while (skipped < pos) {
                    long s = in.skip(pos - skipped);
                    if (s <= 0) break;
                    skipped += s;
                }
                if (skipped == pos) {
                    int r = in.read(buf, 0, 5);
                    if (r == 5 && buf[0] == 'u' && buf[1] == 's' && buf[2] == 't'
                            && buf[3] == 'a' && buf[4] == 'r') return "tar";
                }
            } catch (IOException ignored) {}
        }
        return "unknown";
    }

    /** 解压到目标目录 */
    public static Result extract(File archive, File destDir) {
        Result r = new Result();
        if (!archive.isFile()) {
            r.error = "压缩包不存在: " + archive.getName();
            return r;
        }
        String fmt = detectFormat(archive);
        r.format = fmt;
        if (!destDir.exists() && !destDir.mkdirs()) {
            r.error = "无法创建目录: " + destDir.getAbsolutePath();
            return r;
        }
        try {
            switch (fmt) {
                case "zip":   extractZip(archive, destDir, r); break;
                case "7z":    extract7z(archive, destDir, r); break;
                case "rar":   extractRar(archive, destDir, r); break;
                case "tar":   extractTar(archive, destDir, r); break;
                case "gzip":  extractSingleStream(archive, destDir, r, "gz", true); break;
                case "bzip2": extractSingleStream(archive, destDir, r, "bz2", true); break;
                case "xz":    extractSingleStream(archive, destDir, r, "xz", true); break;
                default:
                    r.error = "不支持的压缩格式（无法识别文件头）";
            }
        } catch (Throwable t) {
            r.error = t.getClass().getSimpleName() + ": " + t.getMessage();
            PCLog.e(TAG, "extract failed: " + archive.getAbsolutePath(), t);
        }
        return r;
    }

    // ---------------------------------------------------------------- ZIP

    private static void extractZip(File src, File dest, Result res) throws IOException {
        try (ZipInputStream zis = new ZipInputStream(
                new BufferedInputStream(new FileInputStream(src)))) {
            ZipEntry e;
            while ((e = zis.getNextEntry()) != null) {
                if (e.isDirectory()) {
                    safeMkdir(resolve(dest, e.getName()));
                    zis.closeEntry();
                    continue;
                }
                File out = resolve(dest, e.getName());
                if (out == null) { zis.closeEntry(); continue; }
                long n = copyEntry(zis, out, res);
                if (n >= 0) { res.files++; res.bytes += n; }
                zis.closeEntry();
                if (res.bytes > MAX_TOTAL_BYTES) throw new IOException("解压总量超限");
            }
        }
    }

    // ---------------------------------------------------------------- 7Z

    private static void extract7z(File src, File dest, Result res) throws IOException {
        try (SevenZFile szf = new SevenZFile(src)) {
            SevenZArchiveEntry entry;
            while ((entry = szf.getNextEntry()) != null) {
                if (entry.isDirectory()) {
                    safeMkdir(resolve(dest, entry.getName()));
                    continue;
                }
                File out = resolve(dest, entry.getName());
                if (out == null) continue;
                // 【重要】7z 的 entry 名可能带多级目录，必须先建父目录
                // （copyEntry 里有这步，但 7z 分支自己写拷贝逻辑，没经过它）
                File parent = out.getParentFile();
                if (parent != null && !parent.exists()) parent.mkdirs();
                // 用 getInputStream 读当前条目（比直接 szf.read 更安全）
                try (InputStream in = szf.getInputStream(entry);
                     OutputStream os = new BufferedOutputStream(new FileOutputStream(out))) {
                    byte[] buf = new byte[64 * 1024];
                    long total = 0;
                    int r;
                    while ((r = in.read(buf)) > 0) {
                        total += r;
                        if (total > MAX_ENTRY_BYTES) throw new IOException("单个文件超限");
                        os.write(buf, 0, r);
                    }
                    res.files++;
                    res.bytes += total;
                }
                if (res.bytes > MAX_TOTAL_BYTES) throw new IOException("解压总量超限");
            }
        }
    }

    // ---------------------------------------------------------------- RAR

    private static void extractRar(File src, File dest, Result res) throws IOException {
        // junrar 7.5.x 提供静态 extract(File, File)，直接解到目标目录
        try {
            java.util.List<File> files = Junrar.extract(src, dest);
            if (files != null) {
                for (File f : files) {
                    if (f.isFile()) {
                        res.files++;
                        res.bytes += f.length();
                    }
                }
            }
            // 统计递归子目录里的文件
            res.files = countFiles(dest);
            res.bytes = totalBytes(dest);
        } catch (Exception e) {
            res.error = e.getClass().getSimpleName() + ": " + e.getMessage();
            PCLog.e(TAG, "rar extract failed: " + src.getAbsolutePath(), e);
        }
    }

    private static int countFiles(File dir) {
        int n = 0;
        File[] fs = dir.listFiles();
        if (fs == null) return 0;
        for (File f : fs) {
            if (f.isDirectory()) n += countFiles(f);
            else n++;
        }
        return n;
    }

    private static long totalBytes(File dir) {
        long n = 0;
        File[] fs = dir.listFiles();
        if (fs == null) return 0;
        for (File f : fs) {
            if (f.isDirectory()) n += totalBytes(f);
            else n += f.length();
        }
        return n;
    }

    // ---------------------------------------------------------------- TAR

    private static void extractTar(File src, File dest, Result res) throws IOException {
        try (TarArchiveInputStream tis = new TarArchiveInputStream(
                new BufferedInputStream(new FileInputStream(src)))) {
            ArchiveEntry e;
            while ((e = tis.getNextEntry()) != null) {
                if (e.isDirectory()) {
                    safeMkdir(resolve(dest, e.getName()));
                    continue;
                }
                File out = resolve(dest, e.getName());
                if (out == null) continue;
                long n = copyEntry(tis, out, res);
                if (n >= 0) { res.files++; res.bytes += n; }
                if (res.bytes > MAX_TOTAL_BYTES) throw new IOException("解压总量超限");
            }
        }
    }

    // ------------------------------------------------- 单文件压缩流 (gz/bz2/xz)
    //
    // 注意：.tar.gz / .tar.bz2 / .tar.xz 是「压缩流 + tar 归档」两层。
    // 这里先解压缩流到临时文件，再判断内容是不是 tar，是则继续解 tar。

    private static void extractSingleStream(File src, File dest, Result res,
                                            String ext, boolean stripExt) throws IOException {
        InputStream raw = new BufferedInputStream(new FileInputStream(src));
        InputStream dec;
        if ("gz".equals(ext))       dec = new GzipCompressorInputStream(raw);
        else if ("bz2".equals(ext)) dec = new BZip2CompressorInputStream(raw);
        else                        dec = new XZCompressorInputStream(raw);

        String name = src.getName();
        if (stripExt) {
            int dot = name.lastIndexOf('.');
            if (dot > 0) name = name.substring(0, dot);
            // .tar.gz → .tar.gz 已经是 .tar；.tar.bz2 → .tar.bz2
            // 去掉最后一个扩展名后，若还剩 .tar 说明里面是 tar
        }
        File out = resolve(dest, name);
        if (out == null) throw new IOException("非法文件名");
        long n = copyEntry(dec, out, res);
        if (n >= 0) { res.files++; res.bytes += n; }
        dec.close();

        // 两层：解出来的其实是 tar → 继续解
        if (isTarFile(out)) {
            File tarDir = new File(dest, name + "_unpacked");
            tarDir.mkdirs();
            ArchiveExtractor.Result tr = new ArchiveExtractor.Result();
            extractTar(out, tarDir, tr);
            if (tr.error == null && tr.files > 0) {
                // 删掉中间的 tar 文件，只留解出的内容
                out.delete();
                res.files += tr.files - 1;   // 减去中间那个 tar 本身
                res.bytes += tr.bytes;
            } else {
                PCLog.w(TAG, "tar-in-stream 二次解压失败: " + tr.error);
            }
        }
    }

    /** 判断文件是否是 tar（偏移 257 处有 "ustar"） */
    private static boolean isTarFile(File f) {
        if (!f.isFile() || f.length() < 512) return false;
        try (InputStream in = new FileInputStream(f)) {
            long skipped = 0;
            byte[] buf = new byte[512];
            while (skipped < 257) {
                long s = in.skip(257 - skipped);
                if (s <= 0) break;
                skipped += s;
            }
            if (skipped != 257) return false;
            int r = in.read(buf, 0, 5);
            return r == 5 && buf[0] == 'u' && buf[1] == 's' && buf[2] == 't'
                    && buf[3] == 'a' && buf[4] == 'r';
        } catch (IOException e) {
            return false;
        }
    }

    // ---------------------------------------------------------------- 工具

    /** 拷贝一个条目内容到文件，返回字节数；-1 表示被跳过 */
    private static long copyEntry(InputStream in, File out, Result res) throws IOException {
        File parent = out.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs()) return -1;
        long total = 0;
        try (OutputStream os = new BufferedOutputStream(new FileOutputStream(out))) {
            byte[] buf = new byte[64 * 1024];
            int r;
            while ((r = in.read(buf)) > 0) {
                total += r;
                if (total > MAX_ENTRY_BYTES) throw new IOException("单个文件超限: " + out.getName());
                os.write(buf, 0, r);
            }
        }
        return total;
    }

    /**
     * 安全地解析条目路径，防目录穿越（../）与绝对路径。
     * 返回 null 表示路径非法，应跳过。
     */
    private static File resolve(File destRoot, String entryName) {
        if (entryName == null || entryName.isEmpty()) return null;
        String name = entryName.replace('\\', '/');
        if (name.startsWith("/") || name.contains(":")) return null;   // 绝对路径 / 盘符
        // 逐段检查
        String[] parts = name.split("/");
        int depth = 0;
        StringBuilder sb = new StringBuilder();
        String last = null;
        for (String p : parts) {
            if (p.isEmpty() || ".".equals(p)) continue;
            if ("..".equals(p)) return null;                           // 目录穿越
            depth++;
            if (depth > MAX_PATH_DEPTH) return null;
            // 【重要】最后一段不加分隔符！
            // 否则 new File(dest, "a/b/") 会被当作目录，getParentFile() 变成
            // ".../b"，mkdirs 建不出来 → FileNotFoundException。
            if (sb.length() > 0) sb.append(File.separator);
            sb.append(p);
            last = p;
        }
        if (sb.length() == 0 || last == null) return null;
        File out = new File(destRoot, sb.toString());
        // 二次确认在 destRoot 之内
        try {
            String root = destRoot.getCanonicalPath();
            String child = out.getCanonicalPath();
            if (!child.equals(root) && !child.startsWith(root + File.separator)) return null;
        } catch (IOException e) {
            return null;
        }
        return out;
    }

    private static void safeMkdir(File f) {
        if (f != null) { try { f.mkdirs(); } catch (Exception ignored) {} }
    }
}
