#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键构建 PowerCollector APK。

用法：
    python build_apk.py            # 构建 debug APK（可直接安装）
    python build_apk.py release    # 构建 release（仍出 debug 签名）

步骤：
    1) 同步 assets（web/ + icons/ + data/devices.json）→ apk/app/src/main/assets/
    2) 注入移动端触屏增强（mobile_overlay.html → index.html）
    3) 重写 proxy_info.js 为 APK 单机版（127.0.0.1 动态端口）
    4) gradle assembleDebug

输出：
    PowerCollector/apk/app/build/outputs/apk/debug/app-debug.apk
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT   = Path(__file__).resolve().parent          # PowerCollector/
APK    = ROOT / 'apk'
PC_WEB = ROOT / 'web'
PC_ICONS = ROOT / 'icons'
DATA   = ROOT / 'data'

JAVA_HOME = Path(os.environ.get('JAVA_HOME', r'D:\jdk17extract\jdk-17.0.2'))
SDK       = Path(os.environ.get('ANDROID_HOME', r'C:\Android\Sdk'))
GRADLE    = Path(r'C:\Gradle\gradle-8.7\bin\gradle.bat')


def _find_java_home():
    """找一个可用的 JDK 17+：环境变量 → 已知路径 → PATH 上的 java。"""
    cands = []
    env = os.environ.get('JAVA_HOME')
    if env:
        cands.append(Path(env))
    cands += [
        Path(r'D:\jdk17extract\jdk-17.0.2'),
        Path(r'D:\jdk17'),
        Path(r'C:\Program Files\Java\jdk-19'),
        Path(r'C:\Program Files\Java\jdk-17'),
        Path(r'C:\Program Files\Eclipse Adoptium'),
        Path(r'C:\Program Files\Android\Android Studio\jbr'),
        Path(r'C:\Program Files\Microsoft\jdk-17'),
    ]
    for c in cands:
        if c.is_dir() and (c / 'bin' / 'java.exe').is_file():
            return c
        # Adoptium 目录下可能是 jdk-17.x.x
        if c.is_dir() and c.name.lower().startswith(('jdk', 'jbr', 'temurin')):
            subs = [d for d in c.iterdir() if d.is_dir() and (d / 'bin' / 'java.exe').is_file()]
            if subs:
                return subs[0]
    # 退回 PATH
    from shutil import which
    j = which('java')
    if j:
        return Path(j).parent.parent
    return JAVA_HOME


JAVA_HOME = _find_java_home()

def log(msg): print(f"[build_apk] {msg}", flush=True)
def fail(msg):
    print(f"[ERROR] {msg}", file=sys.stderr, flush=True)
    sys.exit(1)

# ---------------------------------------------------------------- assets
def sync_assets():
    assets = APK / 'app' / 'src' / 'main' / 'assets'
    for sub in ('web', 'icons', 'data'):
        p = assets / sub
        if p.exists(): shutil.rmtree(p)
    if not PC_WEB.exists(): fail(f"web 目录不存在：{PC_WEB}")
    if not PC_ICONS.exists(): fail(f"icons 目录不存在：{PC_ICONS}")

    # web/（排除运行期生成的 proxy_info.js，稍后重写）
    # 移动端专属块（MOBILE_OVERLAY / MOBILE_DATA_LOADER）由后续的
    # inject_mobile_overlay() 从 mobile_overlay.html 注入，这里不做手工合并。
    shutil.copytree(PC_WEB, assets / 'web')
    n = sum(1 for _ in (assets / 'web').rglob('*') if _.is_file())
    log(f"assets/web 同步完成（{n} 文件）")

    # icons/
    shutil.copytree(PC_ICONS, assets / 'icons')
    log(f"assets/icons 同步完成（{len(list((assets/'icons').glob('*')))} 文件）")

    # data/ —— 【出厂不内置任何演示数据】
    #
    # 用户要求：APK 出厂干净，无台区拓扑连线与分布信息。
    # 用户通过「导入压缩包 / 打开数据目录」自行导入，数据落在
    #   filesDir/data/  （MainActivity 的 /data/*.json 路由优先读这里）
    # assets/data 留空目录，页面读到空数组时按"暂无数据"处理。
    (assets / 'data').mkdir(parents=True, exist_ok=True)
    # 显式清掉可能残留的旧数据（升级安装时 assets 会被 sync_assets 重建）
    for name in ('devices.json', 'devices_bundle.json'):
        old = assets / 'data' / name
        if old.exists():
            old.unlink()
    log("data/ 保持为空（出厂不含演示数据，请用「导入压缩包」加载）")

    # 删除运行期生成的 proxy_info.js（下面重写）
    pi = assets / 'web' / 'proxy_info.js'
    if pi.exists(): pi.unlink()

# ---------------------------------------------------------------- APK 适配补丁
def patch_for_apk():
    """把 APK 专用改动打进 index.html。

    必须在 sync_assets 之后调用（sync_assets 会用 PC 版 web/ 覆盖 assets）。
    改动点：
      1) 设备数据加载改为【相对路径】/data/devices.json
         —— 绝对 http://127.0.0.1:port 会撞 Android 明文流量策略
      2) 去掉桌面版遗留的绝对地址 fetch
    """
    p = APK / 'app' / 'src' / 'main' / 'assets' / 'web' / 'index.html'
    s = p.read_text(encoding='utf-8')
    orig = s

    # 1) 绝对地址 → 相对路径
    s = re.sub(
        r"fetch\('http://127\.0\.0\.1:'\s*\+\s*port\s*\+\s*'/data/devices\.json'\)",
        "fetch('/data/devices.json')",
        s)

    # 2) 兜底：任何残留的 http://127.0.0.1 绝对 fetch → 相对
    s = re.sub(
        r"fetch\('http://127\.0\.0\.1:'\s*\+\s*\w+\s*\+\s*'([^']*)'\)",
        r"fetch('\1')",
        s)

    if s != orig:
        p.write_text(s, encoding='utf-8')
        log("index.html 已应用 APK 适配补丁（相对路径，去明文地址）")
    else:
        log("index.html APK 适配补丁：无改动（可能已是相对路径）")

    # 3) 校验：APK 资源里不应再有绝对 http://127.0.0.1 fetch
    bad = re.findall(r"fetch\('http://[^']+'", s)
    if bad:
        log(f"警告：仍存在绝对地址 fetch {bad[:3]}")

# ---------------------------------------------------------------- proxy_info.js
def write_proxy_info():
    p = APK / 'app' / 'src' / 'main' / 'assets' / 'web' / 'proxy_info.js'
    p.write_text(
        "// 单机 APK 版：所有资源走应用内置 HttpServer。\n"
        "//\n"
        "// 重要：全部使用【相对路径】。\n"
        "//   1) 相对路径天然同源，不会触发 Android 明文流量（cleartext）策略；\n"
        "//   2) 页面本身由 http://127.0.0.1:<port>/web/index.html 加载，\n"
        "//      相对路径会解析到同一个 127.0.0.1 端口，无需再写死端口；\n"
        "//   3) APK 的 HttpServer 不实现 /proxy/<host>/<path> 通用 CDN 代理路由，\n"
        "//      Cesium 静态资源由 CESIUM_BASE_URL=\"/\" 直接映射到 /Workers /Assets 等。\n"
        "window.PROXY_BASE = '/';\n"
        "window.TILE_PROXY = '/tiles';\n"
        "window.LAN_URL = './';\n"
        "window.PC_LOCATION = null;\n",
        encoding='utf-8')
    log("proxy_info.js 已重写为 APK 单机版（全相对路径，同源免明文拦截）")

# ---------------------------------------------------------------- 移动端增强注入
def inject_mobile_overlay():
    idx = APK / 'app' / 'src' / 'main' / 'assets' / 'web' / 'index.html'
    overlay = (APK / 'mobile_overlay.html').read_text(encoding='utf-8')
    html = idx.read_text(encoding='utf-8')
    marker = '<!-- MOBILE_OVERLAY -->'
    if marker in html:
        log("移动端增强已注入过，跳过")
        return
    # 插到 </body> 之前
    html = html.replace('</body>', f'{marker}\n{overlay}\n</body>')
    idx.write_text(html, encoding='utf-8')
    log("移动端触屏增强已注入 index.html（悬浮按钮 + 44px 触控目标 + 触屏手势）")

    # 注入数据自加载（APK 单机：从 assets/data/devices.json 读设备，替代 Python 推送）
    loader = '''
<!-- MOBILE_DATA_LOADER -->
<script>
// 单机 APK：直接从内置 assets 加载设备数据（Python 端推送在 APK 里不可用）
// 注意：用【相对路径】而非 http://127.0.0.1:port ——
//       绝对明文地址会撞 Android 9+ 的明文流量策略（net::ERR_CLEARTEXT_NOT_PERMITTED）
//
// 【2026-10-30】APK 出厂不带任何台区数据：
//   /data/devices.json 读不到 → 返回空数组 → 页面显示"暂无数据"引导用户导入。
(function bootData() {
  function showEmptyState() {
    var el = document.getElementById('loading');
    if (el) {
      el.style.display = 'block';
      el.style.zIndex = '900';
      el.style.background = 'rgba(12,20,32,0.92)';
      el.style.borderRadius = '10px';
      el.style.padding = '18px 22px';
      el.style.maxWidth = '78vw';
      el.style.lineHeight = '1.9';
      el.style.textAlign = 'center';
      el.innerHTML =
        '<div style="font-size:17px;color:#f1c40f;margin-bottom:8px">暂无台区数据</div>' +
        '<div style="font-size:13px;color:#cfd8e3">请用右下角按钮导入：</div>' +
        '<div style="font-size:13px;color:#2F9BFF;margin-top:6px">📦 导入压缩包（zip / 7z / rar 等）</div>' +
        '<div style="font-size:13px;color:#2F9BFF">📂 打开数据目录（选文件夹）</div>' +
        '<div style="font-size:11px;color:#7b8794;margin-top:10px">' +
        '台区文件：文件名含「安卓」的 .xls / .xlsx</div>';
    }
    if (window.__pcRefreshBars) window.__pcRefreshBars();
  }

  fetch('/data/devices.json')
    .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
    .then(function(list){
      list = Array.isArray(list) ? list : (list.devices || []);
      if (!list.length) { showEmptyState(); return; }
      // 同坐标不同台区着色（前端自算：APK 端无 Python，按 (lon,lat) 聚簇 + 台区分色）
      __pcAssignConflictColors(list);
      // 设备
      PowerAPI.loadDevices(list).then(function(n){
        document.getElementById('toolbar-count').textContent = n;
        // 轻量索引
        var idx = {};
        for (var i=0;i<list.length;i++) idx[list[i].id] = list[i];
        window.__setDeviceIndex(idx);
        // 相机对准设备区域：renderDevices 内部会在数据就绪后自动 fit
        // （这里不再重复调用 fitAll —— 早期调用会因 deviceData 未就绪而空转）
      });
      // 连线：devices_bundle.json 里带完整拓扑，单机版直接读
      fetch('/data/devices_bundle.json')
        .then(function(r){ return r.ok ? r.json() : null; })
        .then(function(bundle){
          if (!bundle) return;
          var links = bundle.links || bundle.linkData || [];
          if (!links.length) return;
          // bundle.links 可能是 [parentId, childId, level] 或 {p,c,l} 两种格式
          var norm = links.map(function(L){
            if (Array.isArray(L)) return [L[0], L[1], L[2] != null ? L[2] : 0];
            return [L.p != null ? L.p : L.parent, L.c != null ? L.c : L.child,
                    L.l != null ? L.l : (L.level != null ? L.level : 0)];
          }).filter(function(L){ return L[0] != null && L[1] != null; });
          try {
            window.PowerAPI.loadLinks(norm);
            console.log('[bootData] links loaded:', norm.length);
          } catch (e) { console.warn('loadLinks failed:', e); }
        })
        .catch(function(e){ console.warn('bundle load failed:', e); });
    })
    .catch(function(e){
      document.getElementById('loading').textContent =
        '设备数据加载失败：' + e.message;
      console.error('bootData failed:', e);
    });
})();
</script>
'''
    html = idx.read_text(encoding='utf-8')
    if '<!-- MOBILE_DATA_LOADER -->' in html:
        log("数据自加载已注入过，跳过")
        return
    html = html.replace(marker + '\n', marker + '\n' + loader, 1)
    idx.write_text(html, encoding='utf-8')
    log("设备数据自加载脚本已注入（APK 单机 boot 时 fetch assets/data/devices.json）")


def inject_hutool_overlay():
    """注入户表批量工具面板（与《户表批量工具》GUI 版功能对齐）。

    单独的 overlay 文件（apk/hutool_overlay.html），插到 </body> 之前。
    幂等：已注入过则跳过。
    """
    idx = APK / 'app' / 'src' / 'main' / 'assets' / 'web' / 'index.html'
    overlay_path = APK / 'hutool_overlay.html'
    if not overlay_path.exists():
        fail(f"找不到户表 overlay：{overlay_path}")
    overlay = overlay_path.read_text(encoding='utf-8')
    html = idx.read_text(encoding='utf-8')
    marker = '<!-- HUTOOL_OVERLAY -->'
    if marker in html:
        log("户表批量工具面板已注入过，跳过")
        return
    html = html.replace('</body>', f'{marker}\n{overlay}\n</body>')
    idx.write_text(html, encoding='utf-8')
    log("户表批量工具面板已注入 index.html（4 功能 + 停止 + 清空日志 + 日志 + 进度条）")

# ---------------------------------------------------------------- gradle
def gradle_build(variant='debug'):
    if not GRADLE.exists(): fail(f"Gradle 不存在：{GRADLE}")
    if not JAVA_HOME.exists(): fail(f"JAVA_HOME 无效：{JAVA_HOME}")
    if not SDK.exists(): fail(f"ANDROID_HOME 无效：{SDK}")

    env = os.environ.copy()
    env['JAVA_HOME'] = str(JAVA_HOME)
    env['ANDROID_HOME'] = str(SDK)
    env['ANDROID_SDK_ROOT'] = str(SDK)
    env['PATH'] = str(JAVA_HOME / 'bin') + os.pathsep + env.get('PATH', '')

    cmd = [str(GRADLE), '--no-daemon',
           f'assemble{variant.capitalize()}',
           f'-Dorg.gradle.java.home={JAVA_HOME}',
           f'-Dorg.gradle.android.sdk.dir={SDK}']
    log("构建命令：")
    log('  ' + ' '.join(cmd))
    t0 = time.time()
    # Gradle 在中文 Windows 上输出 GBK 字节流，必须按 errors='replace' 解码
    proc = subprocess.run(cmd, cwd=str(APK), env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding='utf-8', errors='replace')
    dt = time.time() - t0
    sys.stdout.write(proc.stdout or '')
    if proc.returncode != 0:
        tail = '\n'.join((proc.stdout or '').splitlines()[-60:])
        fail(f"Gradle 构建失败（exit={proc.returncode}，耗时 {dt:.0f}s）\n{tail}")
    out = APK / 'app' / 'build' / 'outputs' / 'apk' / variant
    apks = sorted(out.glob('*.apk')) if out.exists() else []
    if not apks: fail(f"找不到 APK 产物：{out}")
    return apks

def main():
    # 默认构建 release（用 key.properties 的免费自签名 + 版权信息）
    # 可手动传 debug 走系统 debug 签名（本地测试）
    variant = sys.argv[1] if len(sys.argv) > 1 else 'release'
    # 检查签名配置：release 构建需要 keystore（key.properties）
    if variant == 'release' and not (ROOT / 'release.keystore').exists():
        print(f"[build_apk] 警告：未找到 {ROOT / 'release.keystore'}，release 将回退 debug 签名")
    t0 = time.time()
    log(f"=== 构建 {variant} APK ===")
    sync_assets()
    write_proxy_info()
    inject_mobile_overlay()
    inject_hutool_overlay()
    patch_for_apk()      # 必须最后：把 APK 专用补丁打进 index.html
    apks = gradle_build(variant)
    for a in apks:
        log(f"✅ {a}  ({a.stat().st_size//1024//1024} MB)")
    # 按 VERSION 自动命名交付 APK：EIAS_v<ver>.apk
    try:
        import re as _re
        ver = next((l.strip() for l in (ROOT/'VERSION').read_text(encoding='utf-8').splitlines()
                    if l.strip() and not l.startswith('#') and _re.match(r'^\d+\.\d+\.\d+$', l.strip())), 'unknown')
        target = ROOT / f'EIAS_v{ver}.apk'
        if apks:
            import shutil
            shutil.copy2(apks[-1], target)
            log(f"📦 交付 APK：{target.name}  ({target.stat().st_size//1024//1024} MB)")
    except Exception as e:
        log(f"[warn] 交付命名失败（不影响 APK 生成）：{e}")
    log(f"全部完成，耗时 {time.time()-t0:.0f}s")

if __name__ == '__main__':
    main()
