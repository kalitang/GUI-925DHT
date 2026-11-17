package com.power.collector;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.JsResult;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ProgressBar;
import android.widget.Toast;

import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 单机 APK 壳。
 *
 * Android 不提供 com.sun.net.httpserver（JDK 模块被裁掉），所以这里用
 * ServerSocket 手写一个极简 HTTP/1.1 服务，把 APK assets 里的
 *   /web/*    CesiumJS + index.html
 *   /icons/*  设备 SVG 图标
 *   /data/*   设备数据 JSON（6588 设备 + 6541 连线）
 * 映射出去，让 Cesium 的 worker / 瓦片 / 图标 / 数据都能走标准 HTTP 加载。
 * 完全离线单机可用，不依赖任何外部服务器。
 *
 * 横屏锁定 + 全屏 + 触屏手势（缩放交给 Cesium 的 ScreenSpaceEventHandler）。
 */
public class MainActivity extends Activity {

    /** SAF：选择数据目录 */
    public static final int REQ_PICK_DIR = 1001;
    /** SAF：选择压缩包 / Excel */
    public static final int REQ_PICK_ARCHIVE = 1002;
    /** 运行时权限请求码（存储） */
    private static final int REQ_STORAGE_PERM = 1003;

    private WebView webview;
    private ProgressBar progressBar;
    private ServerSocket serverSocket;
    private final ExecutorService io = Executors.newFixedThreadPool(4);
    private final AtomicBoolean running = new AtomicBoolean(false);
    private int port = -1;
    private File assetsRoot;
    private File tileCacheRoot;
    private File bundledTileRoot;
    private boolean assetsCopied = false;
    /** 导入/清数据的 JS 桥 */
    private AndroidBridge bridge;
private com.power.collector.hutool.HutoolBridge hutoolBridge;

    /** 1x1 透明 PNG（网络失败时占位） */
    private static final byte[] TRANSPARENT_PNG = new byte[]{
            (byte) 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D,
            0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
            0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, (byte) 0xC4, (byte) 0x89,
            0x00, 0x00, 0x00, 0x0D, 0x49, 0x44, 0x41, 0x54, 0x78, (byte) 0x9C, 0x63,
            0x00, 0x01, 0x00, 0x00, 0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, (byte) 0xB4,
            0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, (byte) 0xAE, 0x42, 0x60, (byte) 0x82
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 强制横屏 + 全屏
        setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE);
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        getWindow().setFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN,
                WindowManager.LayoutParams.FLAG_FULLSCREEN);

        View root = View.inflate(this, R.layout.activity_main, null);
        setContentView(root);
        webview = root.findViewById(R.id.webview);
        progressBar = root.findViewById(R.id.progress);

        // ===== 运行时存储权限请求（真机导入必需）=====
        // 老机型(API≤32)：READ/WRITE_EXTERNAL_STORAGE 需动态申请；
        // API 30 且未授予 MANAGE_EXTERNAL_STORAGE → 引导用户去设置页；
        // API 33+：SAF 选目录不依赖存储权限（系统已限制），无需申请，直接放行。
        requestStoragePermissions();

        WebSettings s = webview.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);          // Cesium 用 localStorage 存性能档位
        s.setAllowContentAccess(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setUserAgentString(s.getUserAgentString() + " PowerCollectorAPK/1.0");
        s.setSupportZoom(false);              // 禁用 WebView 双击缩放，缩放手势交给 Cesium
        // 安全加固：全部资源走 http://127.0.0.1，不需要 file:// 访问
        s.setAllowFileAccess(false);
        s.setAllowFileAccessFromFileURLs(false);
        s.setAllowUniversalAccessFromFileURLs(false);
        // 允许 WebView 加载本地 HTTP 服务的跨源请求（Cesium worker 用 blob:/跨源）
        s.setBlockNetworkImage(false);
        s.setGeolocationEnabled(true);

        webview.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                // 本地服务 / http / https 全部交给 WebView；其它协议（geo: 等）走系统
                if ("http".equals(u.getScheme()) || "https".equals(u.getScheme())) return false;
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (Exception ignored) {}
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                progressBar.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        android.webkit.WebResourceError error) {
                // 只关心主文档失败（子资源失败由页面自行处理）
                if (!request.isForMainFrame()) return;
                CharSequence desc = error.getDescription();
                Toast.makeText(MainActivity.this,
                        "页面加载失败: " + (desc != null ? desc : error.getErrorCode()),
                        Toast.LENGTH_LONG).show();
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request,
                                            WebResourceResponse response) {
                if (!request.isForMainFrame()) return;
                Toast.makeText(MainActivity.this,
                        "HTTP " + response.getStatusCode() + " " + response.getReasonPhrase(),
                        Toast.LENGTH_LONG).show();
            }

            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view,
                                                             WebResourceRequest request) {
                return null;   // 不拦截，全部走内置 HttpServer
            }
        });

        webview.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                progressBar.setVisibility(newProgress < 100 ? View.VISIBLE : View.GONE);
            }

            @Override
            public boolean onJsAlert(WebView view, String url, String message, JsResult result) {
                Toast.makeText(MainActivity.this, message, Toast.LENGTH_LONG).show();
                result.confirm();
                return true;
            }
        });

        webview.setLongClickable(false);       // 长按不弹系统选择浮层（避免挡住地图操作）

        // 注册 JS 桥（导入数据 / 清除已加载数据 / 清瓦片缓存 / 打开数据目录）
        bridge = new AndroidBridge(this, webview);
        webview.addJavascriptInterface(bridge, "PCBridge");

        // 注册户表批量工具桥（与 GUI 版《户表批量工具》功能对齐）
        hutoolBridge = new com.power.collector.hutool.HutoolBridge(this);
        webview.addJavascriptInterface(hutoolBridge, "HutoolBridge");
        hutoolBridge.copyBundledTemplate();

        try {
            startLocalServer();
        } catch (Exception e) {
            Toast.makeText(this, "无法启动本地服务: " + e.getMessage(), Toast.LENGTH_LONG).show();
            return;
        }

        webview.loadUrl("http://127.0.0.1:" + port + "/web/index.html");
    }

    // ==================== 运行时存储权限 ====================
    // SAF（ACTION_OPEN_DOCUMENT_TREE）在 Android 11+ 不需要运行时存储权限，
    // 但 Android 6-10 部分 ROM 弹选择器 / 读 content:// 时会检查 READ/WRITE_EXTERNAL_STORAGE；
    // 真机"无法访问设备存储"通常是这些权限没申请。这里按 API 级别分级处理：
    //   - API < 23：权限自动授予（manifest 声明即可），直接放行
    //   - API 23-32：动态申请 READ/WRITE_EXTERNAL_STORAGE
    //   - API 33+：SAF 不依赖存储权限，直接放行（媒体权限管不到 Excel）
    // 申请失败 / 用户拒绝 → toast 提示用「导入压缩包」兜底（SAF 仍需可用）。
    private void requestStoragePermissions() {
        final int sdk = android.os.Build.VERSION.SDK_INT;
        if (sdk < 23) return;   // 老 API：manifest 声明即自动授予
        if (sdk >= 33) {
            // Android 13+：SAF 选目录系统层已处理，无需申请。仅检查 MANAGE_EXTERNAL_STORAGE
            // （API 30 才有的全设备权限，33+ 已弱化，不再强求）
            return;
        }
        // API 23-32：申请 READ/WRITE_EXTERNAL_STORAGE
        String[] need = new String[]{
                android.Manifest.permission.READ_EXTERNAL_STORAGE,
                android.Manifest.permission.WRITE_EXTERNAL_STORAGE,
        };
        for (String p : need) {
            if (checkSelfPermission(p) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                requestPermissions(need, REQ_STORAGE_PERM);
                android.util.Log.i("PowerCollector", "已申请存储权限 READ/WRITE_EXTERNAL_STORAGE");
                return;
            }
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] perms, int[] results) {
        super.onRequestPermissionsResult(requestCode, perms, results);
        if (requestCode != REQ_STORAGE_PERM) return;
        boolean all = true;
        for (int r : results) if (r != android.content.pm.PackageManager.PERMISSION_GRANTED) { all = false; break; }
        if (all) {
            android.util.Log.i("PowerCollector", "存储权限已授予");
        } else {
            // 拒绝：不阻塞 SAF（ACTION_OPEN_DOCUMENT_TREE 在多数 ROM 仍可弹选择器），
            // 但提示用户，若导入失败请重新授予「存储」权限
            Toast.makeText(this,
                    "未授予存储权限，导入可能失败。请在系统设置 → 应用 → PowerCollector → 权限中开启「存储」",
                    Toast.LENGTH_LONG).show();
        }
    }

    /** SAF 选择结果回调 */
    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (resultCode != RESULT_OK || data == null) return;
        // 户表批量工具：选择待处理根目录
        if (requestCode == com.power.collector.hutool.HutoolBridge.REQ_PICK_ROOT) {
            Uri tree = data.getData();
            if (tree != null && hutoolBridge != null) {
                try {
                    getContentResolver().takePersistableUriPermission(tree,
                            Intent.FLAG_GRANT_READ_URI_PERMISSION
                                    | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                } catch (Throwable ignored) { }
                hutoolBridge.onRootPicked(tree);
            }
            return;
        }
        if (bridge == null) return;
        try {
            if (requestCode == REQ_PICK_DIR) {
                Uri tree = data.getData();
                if (tree != null) {
                    // 持久化读权限（重启后仍可读）
                    try {
                        getContentResolver().takePersistableUriPermission(tree,
                                Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    } catch (Throwable ignored) {}
                    bridge.onDirectoryPicked(tree);
                }
            } else if (requestCode == REQ_PICK_ARCHIVE) {
                java.util.List<Uri> uris = new java.util.ArrayList<>();
                if (data.getClipData() != null) {
                    for (int i = 0; i < data.getClipData().getItemCount(); i++) {
                        uris.add(data.getClipData().getItemAt(i).getUri());
                    }
                } else if (data.getData() != null) {
                    uris.add(data.getData());
                }
                if (!uris.isEmpty()) bridge.onArchivesPicked(uris);
            }
        } catch (Exception e) {
            Toast.makeText(this, "处理选择结果失败: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    // ==================== 本地 HTTP 服务（手写 ServerSocket）====================

    private void startLocalServer() throws Exception {
        // assets → 可写目录（apk 内 assets 不能随机读）
        assetsRoot = new File(getFilesDir(), "webcache");
        assetsRoot.mkdirs();
        tileCacheRoot = new File(getCacheDir(), "tiles");
        tileCacheRoot.mkdirs();
        // 内置离线瓦片：直接从 assets 读，不复制（省磁盘/时间）
        bundledTileRoot = new File(getFilesDir(), "bundled_tiles");
        bundledTileRoot.mkdirs();
        copyBundledTiles();
        if (!assetsCopied) {
            copyAssetsRecursive("web");
            copyAssetsRecursive("icons");
            copyAssetsRecursive("data");
            assetsCopied = true;
        }

        serverSocket = new ServerSocket(0, 16, java.net.InetAddress.getByName("127.0.0.1"));
        port = serverSocket.getLocalPort();
        running.set(true);

        io.submit(() -> {
            while (running.get()) {
                try {
                    Socket client = serverSocket.accept();
                    io.submit(() -> handleClient(client));
                } catch (IOException e) {
                    if (running.get()) {
                        // accept 失败通常是 socket 被关闭
                        break;
                    }
                }
            }
        });
    }

    /** 处理单个 HTTP 请求：只支持 GET / HEAD，够 Cesium 用。 */
    private void handleClient(Socket client) {
        try (Socket sock = client) {
            sock.setSoTimeout(30000);
            InputStream in = sock.getInputStream();
            OutputStream rawOut = sock.getOutputStream();
            BufferedOutputStream out = new BufferedOutputStream(rawOut, 32 * 1024);

            // 读请求行 + 头
            String requestLine = readLine(in);
            if (requestLine == null || requestLine.isEmpty()) return;
            String[] parts = requestLine.split(" ");
            if (parts.length < 2) return;
            String method = parts[0];
            String rawPath = parts[1];

            // 读完剩余请求头
            while (true) {
                String line = readLine(in);
                if (line == null || line.isEmpty()) break;
            }

            // 解析路径（去掉 query）
            int q = rawPath.indexOf('?');
            String path = (q >= 0) ? rawPath.substring(0, q) : rawPath;
            try {
                path = java.net.URLDecoder.decode(path, "UTF-8");
            } catch (Exception ignored) {}

            if ("/".equals(path)) path = "/web/index.html";

            // --- 瓦片代理：/tiles/imagery/{z}/{x}/{y} → ArcGIS World Imagery ---
            // 在线拉取后落地缓存（app 私有目录），离线时回退缓存
            if (path.startsWith("/tiles/")) {
                handleTile(path, out, method);
                return;
            }

            // --- Cesium 静态资源：CESIUM_BASE_URL="/" 时请求 /Workers/... /Assets/...
            //     /Widgets/... /ThirdParty/... → 映射到 web/ 下同名目录 ---
            String lower = path.toLowerCase();
            if (lower.startsWith("/workers/") || lower.startsWith("/assets/")
                    || lower.startsWith("/widgets/") || lower.startsWith("/thirdparty/")) {
                File ces = new File(assetsRoot, "web" + path);
                if (!ces.isFile()) ces = new File(assetsRoot, path.substring(1));
                if (ces.isFile()) {
                    byte[] d = readAll(ces);
                    writeResponse(out, 200, "OK", contentType(ces.getName()), d, "HEAD".equals(method));
                    out.flush();
                    return;
                }
            }

            // --- 设备数据：/data/*.json ---
            //     优先读 filesDir/data（导入后可写，用户的实际数据），
            //     回退到 assets/data（内置数据，出厂时为空）
            if (path.startsWith("/data/")) {
                String name = path.substring("/data/".length());
                if (name.contains("/") || name.contains("..")) {
                    writeError(out, 403);
                    return;
                }
                File userData = new File(new File(getFilesDir(), "data"), name);
                File assetData = new File(assetsRoot, "data" + path);
                File pick = (userData.isFile() && userData.length() > 0) ? userData : assetData;
                if (pick.isFile()) {
                    byte[] d = readAll(pick);
                    writeResponse(out, 200, "OK", "application/json", d, "HEAD".equals(method));
                    out.flush();
                    return;
                }
                // 没有数据 → 返回空 JSON 数组（前端按"无数据"处理，不报错）
                byte[] empty = "[]".getBytes("UTF-8");
                writeResponse(out, 200, "OK", "application/json", empty, "HEAD".equals(method));
                out.flush();
                return;
            }

            File f = new File(assetsRoot, path.startsWith("/") ? path.substring(1) : path);
            // 防目录穿越
            try {
                if (!f.getCanonicalPath().startsWith(assetsRoot.getCanonicalPath())) {
                    writeError(out, 403);
                    return;
                }
            } catch (IOException ignored) {}

            if (!f.isFile()) {
                writeError(out, 404);
                return;
            }

            byte[] data = readAll(f);
            String ctype = contentType(f.getName());
            writeResponse(out, 200, "OK", ctype, data, "HEAD".equals(method));
            out.flush();

        } catch (Exception e) {
            // 单个连接失败不影响服务
        }
    }

    /** 瓦片代理：/tiles/imagery/{z}/{x}/{y} → 内置离线瓦片，miss 时联网拉取。 */
    private void handleTile(String path, OutputStream out, String method) throws IOException {
        // 解析 /tiles/imagery/{z}/{x}/{y}
        String[] seg = path.split("/");   // ["", "tiles", "imagery", z, x, y]
        if (seg.length < 6 || !"imagery".equals(seg[2])) {
            writeError(out, 404);
            return;
        }
        int z, x, y;
        try {
            z = Integer.parseInt(seg[3]);
            x = Integer.parseInt(seg[4]);
            y = Integer.parseInt(seg[5]);
        } catch (NumberFormatException e) {
            writeError(out, 400);
            return;
        }

        String key = "imagery/" + z + "/" + x + "/" + y;
        String h = sha256Hex(key);
        // 内置离线瓦片（assets/tiles/<h[:2]>/<h>.bin）—— 与桌面版 tile_proxy 同一套哈希规则
        File bundled = new File(bundledTileRoot, h.substring(0, 2) + "/" + h + ".bin");
        if (bundled.isFile() && bundled.length() > 0) {
            byte[] data = readAll(bundled);
            writeResponse(out, 200, "OK", "image/jpeg", data, "HEAD".equals(method));
            out.flush();
            return;
        }

        // 运行时缓存（app 私有目录）
        File cacheFile = new File(tileCacheRoot, key.replace('/', '_') + ".jpg");

        // 1) 运行时缓存命中
        if (cacheFile.isFile() && cacheFile.length() > 0) {
            byte[] data = readAll(cacheFile);
            writeResponse(out, 200, "OK", "image/jpeg", data, "HEAD".equals(method));
            out.flush();
            return;
        }

        // 2) 在线拉取（ArcGIS 公开卫星底图）
        byte[] data = null;
        try {
            java.net.URL url = new java.net.URL(
                    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/"
                            + z + "/" + y + "/" + x);
            java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(8000);
            conn.setReadTimeout(12000);
            conn.setRequestProperty("User-Agent", "PowerCollectorAPK/1.0");
            if (conn.getResponseCode() == 200) {
                try (InputStream in = conn.getInputStream()) {
                    java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
                    byte[] buf = new byte[16 * 1024];
                    int n;
                    while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
                    data = bos.toByteArray();
                }
                // 落地缓存
                try {
                    cacheFile.getParentFile().mkdirs();
                    File tmp = new File(cacheFile.getAbsolutePath() + ".tmp");
                    try (OutputStream fo = new java.io.FileOutputStream(tmp)) {
                        fo.write(data);
                    }
                    if (!tmp.renameTo(cacheFile)) tmp.delete();
                } catch (IOException ignored) {
                }
            }
        } catch (Exception e) {
            // 网络失败 → 返回透明 PNG
        }

        if (data == null || data.length == 0) {
            data = TRANSPARENT_PNG;
            writeResponse(out, 200, "OK", "image/png", data, "HEAD".equals(method));
        } else {
            writeResponse(out, 200, "OK", "image/jpeg", data, "HEAD".equals(method));
        }
        out.flush();
    }

    /** sha256 十六进制小写（与桌面版 tile_proxy 的 path_for 一致）。 */
    private static String sha256Hex(String s) {
        try {
            java.security.MessageDigest md = java.security.MessageDigest.getInstance("SHA-256");
            byte[] d = md.digest(s.getBytes("UTF-8"));
            StringBuilder sb = new StringBuilder(64);
            for (byte b : d) {
                String hx = Integer.toHexString(b & 0xff);
                if (hx.length() == 1) sb.append('0');
                sb.append(hx);
            }
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    private static String readLine(InputStream in) throws IOException {
        StringBuilder sb = new StringBuilder(128);
        int c;
        while ((c = in.read()) != -1) {
            if (c == '\r') continue;
            if (c == '\n') return sb.toString();
            sb.append((char) c);
            if (sb.length() > 8192) return sb.toString();
        }
        return sb.length() > 0 ? sb.toString() : null;
    }

    private static byte[] readAll(File f) throws IOException {
        try (InputStream in = new FileInputStream(f)) {
            java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream(
                    (int) Math.min(f.length(), Integer.MAX_VALUE));
            byte[] buf = new byte[32 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
            return bos.toByteArray();
        }
    }

    private static void writeResponse(OutputStream out, int code, String reason,
                                      String ctype, byte[] body, boolean headOnly) throws IOException {
        StringBuilder sb = new StringBuilder(256);
        sb.append("HTTP/1.1 ").append(code).append(' ').append(reason).append("\r\n");
        sb.append("Content-Type: ").append(ctype).append("\r\n");
        sb.append("Content-Length: ").append(body.length).append("\r\n");
        sb.append("Access-Control-Allow-Origin: *\r\n");
        sb.append("Cache-Control: max-age=3600\r\n");
        sb.append("Connection: close\r\n");
        sb.append("\r\n");
        out.write(sb.toString().getBytes("ISO-8859-1"));
        if (!headOnly) out.write(body);
    }

    private static void writeError(OutputStream out, int code) throws IOException {
        byte[] body = ("HTTP/1.1 " + code + " Error\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                .getBytes("ISO-8859-1");
        out.write(body);
    }

    private static String contentType(String name) {
        String n = name.toLowerCase();
        if (n.endsWith(".html")) return "text/html; charset=utf-8";
        if (n.endsWith(".js")) return "application/javascript; charset=utf-8";
        if (n.endsWith(".css")) return "text/css; charset=utf-8";
        if (n.endsWith(".svg")) return "image/svg+xml";
        if (n.endsWith(".json")) return "application/json; charset=utf-8";
        if (n.endsWith(".png")) return "image/png";
        if (n.endsWith(".jpg") || n.endsWith(".jpeg")) return "image/jpeg";
        if (n.endsWith(".gif")) return "image/gif";
        if (n.endsWith(".woff")) return "font/woff";
        if (n.endsWith(".woff2")) return "font/woff2";
        if (n.endsWith(".wasm")) return "application/wasm";
        if (n.endsWith(".txt")) return "text/plain; charset=utf-8";
        return "application/octet-stream";
    }

    /**
     * 把 assets/tiles/ 复制到 filesDir/bundled_tiles/（仅首次，之后跳过）。
     * 约 9000 张瓦片 / 十几 MB，一次性复制后即可被 HttpServer 随机读取。
     */
    private void copyBundledTiles() {
        File marker = new File(bundledTileRoot, ".done");
        if (marker.isFile()) return;
        String[] sub;
        try {
            sub = getAssets().list("tiles");
        } catch (IOException e) {
            sub = null;
        }
        if (sub == null) {
            // 没有内置瓦片（该 APK 未打包离线底图）→ 标记完成，避免每次启动都扫
            try {
                marker.createNewFile();
            } catch (IOException ignored) {
            }
            return;
        }
        long t0 = System.currentTimeMillis();
        int n = 0;
        for (String d : sub) {
            String[] files;
            try {
                files = getAssets().list("tiles/" + d);
            } catch (IOException e) {
                continue;
            }
            if (files == null || files.length == 0) continue;
            File dstDir = new File(bundledTileRoot, d);
            dstDir.mkdirs();
            for (String f : files) {
                File dst = new File(dstDir, f);
                if (dst.isFile() && dst.length() > 0) {
                    n++;
                    continue;
                }
                try (InputStream in = getAssets().open("tiles/" + d + "/" + f);
                     OutputStream os = new java.io.FileOutputStream(dst)) {
                    in.transferTo(os);
                    n++;
                } catch (IOException ignored) {
                }
            }
        }
        try {
            marker.createNewFile();
        } catch (IOException ignored) {
        }
        android.util.Log.i("PowerCollector", "内置瓦片就绪: " + n + " 张, "
                + (System.currentTimeMillis() - t0) + "ms");
    }

    /** 递归把 assets/<dir>/ 复制到 filesDir/webcache/<dir>/ */
    private void copyAssetsRecursive(String assetDir) throws Exception {
        File targetDir = new File(assetsRoot, assetDir);
        targetDir.mkdirs();
        String[] entries = getAssets().list(assetDir);
        if (entries == null) return;
        for (String name : entries) {
            String[] sub = getAssets().list(assetDir + "/" + name);
            if (sub != null && sub.length > 0) {
                copyAssetsRecursive(assetDir + "/" + name);
            } else {
                File dst = new File(targetDir, name);
                dst.getParentFile().mkdirs();
                try (InputStream in = getAssets().open(assetDir + "/" + name);
                     OutputStream out = new java.io.FileOutputStream(dst)) {
                    in.transferTo(out);
                }
            }
        }
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        running.set(false);
        try {
            if (serverSocket != null) serverSocket.close();
        } catch (IOException ignored) {}
        io.shutdownNow();
    }

    @Override
    public void onBackPressed() {
        if (webview != null && webview.canGoBack()) webview.goBack();
        else super.onBackPressed();
    }
}
