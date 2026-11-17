// 单机 APK 版：所有资源走应用内置 HttpServer。
//
// 重要：全部使用【相对路径】。
//   1) 相对路径天然同源，不会触发 Android 明文流量（cleartext）策略；
//   2) 页面本身由 http://127.0.0.1:<port>/web/index.html 加载，
//      相对路径会解析到同一个 127.0.0.1 端口，无需再写死端口；
//   3) APK 的 HttpServer 不实现 /proxy/<host>/<path> 通用 CDN 代理路由，
//      Cesium 静态资源由 CESIUM_BASE_URL="/" 直接映射到 /Workers /Assets 等。
window.PROXY_BASE = '/';
window.TILE_PROXY = '/tiles';
window.LAN_URL = './';
window.PC_LOCATION = null;
