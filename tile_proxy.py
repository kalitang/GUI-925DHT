"""本地瓦片 HTTP 代理 + 缓存 + 静态资源托管。

为什么需要：
  Cesium 在我们的 QWebEngineView 里加载 ArcGIS 卫星影像瓦片 + 自带的 workers/assets。
  把 webview 直接指向 file:// 会导致：
    1) 瓦片无法走本地代理（file:// 不能 XHR 到 http://）
    2) 跨域错误
  所以我们用一个本地 HTTP 服务同时托管：
    - /web/index.html  +  /web/Cesium.js  +  /web/Widgets.css + /web/proxy_info.js
    - /icons/*.svg  +  /assets/*.svg
    - /tiles/imagery/{z}/{x}/{y}  → ArcGIS 卫星（缓存）
    - /tiles/labels/{z}/{x}/{y}   → ArcGIS 注记（缓存）
    - /proxy/<host>/<path>        → 通用 CDN 代理（用于 Cesium workers 等静态资源）

启动方式：在主程序里起一个独立线程跑 ThreadingHTTPServer
"""
from __future__ import annotations
import os
import io
import re
import sys
import time
import hashlib
import logging
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer, HTTPServer
from urllib.parse import urlparse, parse_qs
import requests as req_lib

log = logging.getLogger("tile_proxy")

# ArcGIS 公开瓦片根（World_Imagery + World_Street_Map 注记）
ARCGIS_IMAGERY = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
ARCGIS_LABELS  = "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"

UA = "Mozilla/5.0 PowerCollector/1.0"


class TileCache:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self._hits = 0
        self._misses = 0

    def path_for(self, key: str) -> str:
        # 用 key 的 sha256 前两位做目录分片，避开单目录文件数过多
        h = hashlib.sha256(key.encode()).hexdigest()
        sub = os.path.join(self.root, h[:2])
        os.makedirs(sub, exist_ok=True)
        return os.path.join(sub, h + ".bin"), h

    def has(self, key: str) -> bool:
        p, _ = self.path_for(key)
        return os.path.exists(p) and os.path.getsize(p) > 0

    def read(self, key: str):
        p, _ = self.path_for(key)
        if os.path.exists(p):
            self._hits += 1
            with open(p, "rb") as f:
                return f.read()
        self._misses += 1
        return None

    def write(self, key: str, data: bytes):
        p, _ = self.path_for(key)
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)

    def stats(self):
        total = self._hits + self._misses
        return self._hits, self._misses, total


_cache: TileCache = None
_session = None


def _fetch(url: str, timeout=8, retries=2) -> tuple:
    """返回 (data, content_type)。失败返回 (None, None)。

    低内存设备调优：超时 30→8 秒，重试 3→2 次（避免后台线程堆积）。
    """
    global _session
    if _session is None:
        _session = req_lib.Session()
        _session.headers.update({"User-Agent": UA})
        # 只挂 1 次重试，避免和下面的循环叠加（之前总重试 3*3=9 次）
        from requests.adapters import HTTPAdapter
        try:
            from urllib3.util.retry import Retry
            retry = Retry(total=1, backoff_factor=0.3,
                          status_forcelist=(500, 502, 503, 504),
                          allowed_methods=frozenset(["GET"]),
                          raise_on_status=False)
            _session.mount("https://", HTTPAdapter(max_retries=retry))
        except Exception:
            pass
    for attempt in range(retries):
        try:
            r = _session.get(url, timeout=timeout)
            if r.status_code == 200 and r.content:
                return r.content, r.headers.get("Content-Type", "image/jpeg")
        except Exception as e:
            # 失败重试：丢弃旧连接避免 SSL EOF
            if attempt + 1 < retries:
                try:
                    _session.close()
                except Exception:
                    pass
                time.sleep(0.2)
            else:
                log.debug("fetch %s fail: %s", url, type(e).__name__)
    return None, None


# /tiles/imagery/{z}/{x}/{y} 或 /tiles/labels/{z}/{x}/{y}
_TILE_RE = re.compile(r"^/tiles/(imagery|labels)/(\d+)/(\d+)/(\d+)$")
# /web/*  → 静态托管（Cesium.js / Widgets.css / index.html / proxy_info.js）
# Cesium 资源目录用驼峰命名（Assets/Workers/Widgets/ThirdParty），需大小写不敏感
_STATIC_RE = re.compile(r"^/(?i:web|icons|assets|workers|widgets|thirdparty)/(.+)$")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # 静默默认访问日志；需要时打开
        log.debug(fmt, *args)

    def do_GET(self):
        u = urlparse(self.path)
        m = _TILE_RE.match(u.path)
        if m:
            self._serve_tile(m)
            return
        if _STATIC_RE.match(u.path):
            self._serve_static(u.path)
            return
        # 通用代理：任意 URL → 透传 + 缓存（供 Cesium 静态资源用）
        if u.path.startswith("/proxy/"):
            target = u.path[len("/proxy/"):]
            self._serve_proxy(target, parse_qs(u.query))
            return
        # 根路径 → 重定向到 /web/index.html
        if u.path in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/web/index.html")
            self.end_headers()
            return
        self.send_error(404, "bad path")

    def _serve_static(self, path: str):
        # 把 /web/foo → <APP_ROOT>/web/foo
        # /icons/foo 映射到 web/icons/foo（SVG 设备图标在 web/icons/ 下）
        # Cesium 1.131 资源路径大小写敏感（Assets/Workers/Widgets/ThirdParty 都是驼峰）
        # Windows 文件系统大小写不敏感，但 Python Path 比较按字面值 → 做大小写回退
        rel = path.lstrip("/")  # web/foo 或 icons/foo 或 Assets/foo
        lower_rel = rel.lower()
        if lower_rel.startswith("icons/"):
            rel = "web/" + rel
        elif lower_rel.startswith("assets/"):
            rel = "web/" + rel
        elif lower_rel.startswith("workers/"):
            rel = "web/" + rel
        elif lower_rel.startswith("widgets/"):
            rel = "web/" + rel
        elif lower_rel.startswith("thirdparty/"):
            rel = "web/" + rel
        full = (Path(__file__).resolve().parent / rel)
        # Windows 本身大小写不敏感，但若同目录有 Cesium.js/cesium.js 重名会拿错 → 这里 Path.exists 已经够用
        if not full.exists() or not full.is_file():
            # 诊断日志：打印缺失的具体路径（写到 stderr，PySide6 的 console 看不到这条，要靠 stdout）
            try:
                import sys as _sys
                print(f"[tile_proxy] 404 path={path!r} resolved={full!r}", file=_sys.stderr, flush=True)
            except Exception:
                pass
            # send_error 的 message 不能含非 latin-1 字符
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        # MIME
        ext = full.suffix.lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js":   "application/javascript; charset=utf-8",
            ".css":  "text/css; charset=utf-8",
            ".svg":  "image/svg+xml",
            ".png":  "image/png",
            ".jpg":  "image/jpeg",
            ".jpeg": "image/jpeg",
            ".json": "application/json; charset=utf-8",
            ".woff": "font/woff",
            ".woff2":"font/woff2",
        }.get(ext, "application/octet-stream")
        try:
            data = full.read_bytes()
        except Exception as e:
            self.send_error(500, str(e)); return
        self._send(200, data, ctype)

    def _serve_tile(self, m):
        layer, z, x, y = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        # 注记层已移除（lowmem 模式），只接受 imagery
        if layer != "imagery":
            self._send(404, b"layer disabled", "text/plain; charset=utf-8")
            return
        key = f"{layer}/{z}/{x}/{y}"
        data = _cache.read(key)
        if data is not None:
            self._send(200, data, "image/jpeg")
            return
        url = ARCGIS_IMAGERY.format(z=z, y=y, x=x)
        data, ctype = _fetch(url)
        if data is None:
            data = _TRANSPARENT_PNG
            ctype = "image/png"
        else:
            try:
                _cache.write(key, data)
            except Exception as e:
                log.warning("cache write err: %s", e)
        self._send(200, data, ctype or "image/jpeg")

    def _serve_proxy(self, target, _qs):
        # 通用 CDN 代理：用于 Cesium workers/assets/widgets 等静态资源
        if not target:
            self.send_error(404); return
        # 简单缓存：内存 + 文件
        cache_key = f"cdn::{target}"
        data = _cache.read(cache_key)
        if data is not None:
            # 推断 ctype
            ctype = "application/javascript" if target.endswith(".js") else \
                    "text/css" if target.endswith(".css") else \
                    "image/png" if target.endswith(".png") else \
                    "application/octet-stream"
            self._send(200, data, ctype)
            return
        # 透传
        url = "https://" + target
        data, ctype = _fetch(url, timeout=20)
        if data is None:
            self.send_error(502, "upstream failed"); return
        try:
            _cache.write(cache_key, data)
        except Exception as e:
            log.warning("proxy cache write err: %s", e)
        self._send(200, data, ctype or "application/octet-stream")

    def _send(self, code: int, body, ctype: str):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 瓦片是内容寻址的（SHA256 命名），可以长期缓存；
        # HTML/JS/CSS 是会变的代码，必须每次回源，否则改了前端用户还看到旧版
        if ctype.startswith(("text/html", "application/javascript", "text/css", "application/json")):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        else:
            self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


# 最小 1x1 透明 PNG（67 字节）
_TRANSPARENT_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c6300010000000500010d0a2db40000000049454e44ae"
    "426082")


class TileProxyServer:
    """瓦片代理 HTTP 服务。

    低内存设备默认用 HTTPServer（单线程），避免 ThreadingHTTPServer
    在 100 张瓦片并发时开 100 个线程导致内存耗尽。
    Cesium 浏览器本身就是单 connection 串行拉瓦片，单线程够用。

    仅监听本机回环地址（127.0.0.1）——本程序为单机桌面工具，
    不对局域网暴露服务。
    """

    def __init__(self, cache_root: str, port: int = 8765, use_threading: bool = False):
        global _cache
        _cache = TileCache(cache_root)
        self.port = port
        # 低内存设备默认 HTTPServer（单线程），线程安全模式才用 ThreadingHTTPServer
        cls = ThreadingHTTPServer if use_threading else HTTPServer
        # 仅本机回环（不对外暴露）
        self.server = cls(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        self.port = self.server.server_address[1]
        log.info("Tile proxy listening on 127.0.0.1:%s, cache=%s",
                 self.port, _cache.root)
        return self.port

    def stop(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass

    def stats(self):
        return _cache.stats() if _cache else (0, 0, 0)


def self_test(cache_root: str):
    s = TileProxyServer(cache_root, port=0)
    port = s.start()
    print(f"proxy on 127.0.0.1:{port}")
    r = req_lib.get(f"http://127.0.0.1:{port}/tiles/imagery/3/6/3", timeout=10)
    print("status:", r.status_code, "bytes:", len(r.content))
    h, m, t = s.stats()
    print(f"hits={h} misses={m} total={t}")
    s.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    self_test(sys.argv[1] if len(sys.argv) > 1 else "_test_cache")