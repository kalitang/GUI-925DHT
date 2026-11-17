"""下载 Cesium 1.131 全套静态资源到 web/，完全离线化。
Workers + Assets + Widgets.css + ThirdParty。
多源失败 fallback：jsdelivr → gcore → GitHub raw
"""
import os, sys, time, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
import urllib.error

# 目标目录：相对本文件位置推导（不硬编码盘符）
HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
WORKERS = os.path.join(WEB, "Workers")
ASSETS = os.path.join(WEB, "Assets")
THIRD = os.path.join(WEB, "ThirdParty")
WIDGETS = os.path.join(WEB, "Widgets")

VERSION = "1.131.0"
# 多源 fallback
CDN_BASES = [
    f"https://cdn.jsdelivr.net/npm/cesium@{VERSION}/Build/Cesium/",
    f"https://gcore.jsdelivr.net/npm/cesium@{VERSION}/Build/Cesium/",
    f"https://fastly.jsdelivr.net/npm/cesium@{VERSION}/Build/Cesium/",
]

# 已知的 Cesium 1.131 文件列表（从官方仓库获取，不依赖运行时探测）
# 实际上拉一个清单文件即可：jsdelivr 提供 /flat 端点返回目录下所有文件
LIST_URLS = [b + "Workers/" for b in CDN_BASES]

os.makedirs(WORKERS, exist_ok=True)
os.makedirs(ASSETS, exist_ok=True)
os.makedirs(THIRD, exist_ok=True)
os.makedirs(WIDGETS, exist_ok=True)

def get_full_list(path_prefix):
    """从 jsdelivr data API 拿完整文件清单（含子目录）"""
    url = f"https://data.jsdelivr.com/v1/package/npm/cesium@{VERSION}/flat"
    try:
        r = urllib.request.urlopen(url, timeout=20)
        import json
        d = json.loads(r.read())
        files = []
        for f in d.get('files', []):
            name = f['name']
            if name.startswith(path_prefix) and not name.endswith('/'):
                # 去掉 /Build/Cesium/ 前缀，转成相对路径
                rel = name[len('/Build/Cesium/'):]
                files.append(rel)
        return files
    except Exception as e:
        print(f"[flat fail] {url}: {e}")
        return []

def get_worker_list():
    """从 jsdelivr flat API 拿 Workers 文件清单"""
    files = get_full_list('/Build/Cesium/Workers/')
    if files:
        print(f"[list] {len(files)} workers from flat API")
    return files

def get_asset_list():
    """从 jsdelivr flat API 拿 Assets 文件清单（含子目录）"""
    files = get_full_list('/Build/Cesium/Assets/')
    if files:
        print(f"[list] {len(files)} assets from flat API")
    return files

def get_widgets_list():
    """从 jsdelivr flat API 拿 Widgets 文件清单"""
    files = get_full_list('/Build/Cesium/Widgets/')
    if files:
        print(f"[list] {len(files)} widgets from flat API")
    return files

def get_thirdparty_list():
    """从 jsdelivr flat API 拿 ThirdParty 文件清单"""
    files = get_full_list('/Build/Cesium/ThirdParty/')
    if files:
        print(f"[list] {len(files)} thirdparty from flat API")
    return files

def fetch(url, dest, timeout=30, retries=3):
    """下载单文件，3 次重试。返回是否成功 + 字节数"""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PowerCollector/1.0"})
            r = urllib.request.urlopen(req, timeout=timeout)
            data = r.read()
            if len(data) < 50 and r.status != 200:
                continue
            with open(dest, 'wb') as f:
                f.write(data)
            return True, len(data)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1 + attempt)
    return False, 0

def download_all(files, label):
    """并发下载 files（每项是相对路径，如 Workers/xxx.js）到 web/{path}"""
    todo = []
    for rel in files:
        local = os.path.join(WEB, rel)
        if os.path.exists(local) and os.path.getsize(local) > 50:
            continue
        todo.append(rel)
    if not todo:
        print(f"[{label}] all {len(files)} files already local ✓")
        return
    print(f"[{label}] downloading {len(todo)} files...")
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = {}
        for rel in todo:
            local = os.path.join(WEB, rel)
            os.makedirs(os.path.dirname(local), exist_ok=True)
            url_candidates = [b + rel for b in CDN_BASES]
            def task(_urls=url_candidates, _dest=local):
                for u in _urls:
                    s, n = fetch(u, _dest, timeout=15)
                    if s:
                        return True, n
                return False, 0
            futures[ex.submit(task)] = rel
        for fut in as_completed(futures):
            success, n = fut.result()
            rel = futures[fut]
            if success:
                ok += 1
            else:
                fail += 1
                print(f"  FAIL: {rel}")
    print(f"[{label}] done: {ok} ok, {fail} fail")

def download_single(rel, label):
    """下载单个文件（Widgets.css、ThirdParty 等）"""
    local = os.path.join(WEB, rel)
    if os.path.exists(local) and os.path.getsize(local) > 50:
        print(f"[{label}] already local: {rel}")
        return
    os.makedirs(os.path.dirname(local), exist_ok=True)
    url_candidates = [b + rel for b in CDN_BASES]
    for u in url_candidates:
        s, n = fetch(u, local)
        if s:
            print(f"[{label}] {rel} {n}B")
            return
    print(f"[{label}] FAIL: {rel}")

if __name__ == "__main__":
    # 1. Workers
    wfiles = get_worker_list()
    if wfiles:
        download_all(wfiles, "Workers")
    # 2. Assets（递归子目录）
    afiles = get_asset_list()
    if afiles:
        download_all(afiles, "Assets")
    # 3. Widgets（CSS）
    wgfiles = get_widgets_list()
    if wgfiles:
        download_all(wgfiles, "Widgets")
    # 4. ThirdParty（draco 解码器等）
    tpfiles = get_thirdparty_list()
    if tpfiles:
        download_all(tpfiles, "ThirdParty")
    # 统计
    def cnt(d):
        if not os.path.exists(d): return 0
        return sum(1 for _ in os.scandir(d) if _.is_file())
    def size_mb(d):
        if not os.path.exists(d): return 0
        return sum(os.path.getsize(os.path.join(r,f)) for r,_,fs in os.walk(d) for f in fs)/1024/1024
    print()
    print(f"Workers:    {cnt(WORKERS)} files, {size_mb(WORKERS):.1f} MB")
    print(f"Assets:     {cnt(ASSETS)} files, {size_mb(ASSETS):.1f} MB")
    print(f"Widgets:    {cnt(WIDGETS)} files, {size_mb(WIDGETS):.1f} MB")
    print(f"ThirdParty: {cnt(THIRD)} files, {size_mb(THIRD):.1f} MB")
    print(f"Total:      {sum(size_mb(d) for d in [WORKERS,ASSETS,WIDGETS,THIRD]):.1f} MB")
