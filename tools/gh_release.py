# -*- coding: utf-8 -*-
"""
发布 v1.1.0 Release 并上传 EXE / APK 附件。

前置条件：PAT 需具备该仓库的 Contents: Read and write 权限。
用法：
    set GH_TOKEN=<你的 fine-grained PAT>
    python _peek/gh_release.py v1.1.0 "dist\\电力低压数据采集软件.exe" "电力低压数据采集软件.apk"
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.error
import urllib.request

TOKEN = os.environ["GH_TOKEN"]
REPO = "kalitang/GUI-925DHT"
API = "https://api.github.com"

BODY = """## 电力专业低压数据采集软件 v1.1.0

3D 地球 + ArcGIS 卫星影像 + 本地瓦片缓存 + 自动从 Excel 提取电网设备点位 + 拓扑层级与挂接管理。

| 版本 | 附件 | 说明 |
|------|------|------|
| **Windows EXE** | `电力低压数据采集软件.exe`（175 MB） | 免安装，双击即用；内置《低压客户模板.xls》 |
| **Android APK** | `电力低压数据采集软件.apk`（67 MB） | v2 方案签名，Android 7.0+ 可直接安装 |
| **GUI 源码版** | 仓库源码 | `pip install -r requirements.txt` 后 `python main.py` |

### 户表批量工具（三版本均可用）

菜单：户表批量 → 打开户表批量工具（`Ctrl+U`）

| 功能 | 说明 |
|------|------|
| ① 生成客户档案 | 按台区数据表批量生成《低压客户模板》客户档案 |
| ② 批量修改安卓表 | 数据表中的 `0杆` 改为台区名；可选保留「采集版副本」 |
| ③ 提取整理 PDF | 台区 PDF 归集到根目录 `打印/`（幂等） |
| ④ 准备采集软件数据 | 一键补齐采集软件数据目录（幂等） |

支持 **停止** / **清空日志** / **运行日志区** / **进度条** / **成功失败跳过计数** / **状态提示**。

- GUI / EXE：Windows Excel COM 自动化（win32com）
- APK：Java Apache POI 等价实现（移动端无 COM）

### 使用注意

- EXE 仅监听本机回环地址，不对外提供服务。
- APK 使用「导入压缩包」或「打开目录」加载数据。
- APK 安装需允许存储权限（已 v2 签名）。

### 版权

© 晨曦网络工作室 · 重庆亦静怡动电子商务有限公司
"""


def api(method, url, payload=None, raw_body=None, content_type=None, expect_json=True):
    h = {
        "Authorization": f"Bearer {TOKEN}",
        "User-Agent": "hermes",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        h["Content-Type"] = "application/json; charset=utf-8"
    elif raw_body is not None:
        data = raw_body
        h["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=300) as r:
        out = r.read()
        return r.status, (json.loads(out.decode("utf-8")) if expect_json else out)


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "v1.1.0"
    assets = sys.argv[2:]

    rel = None
    st, lst = api("GET", f"{API}/repos/{REPO}/releases")
    for r in lst:
        if r.get("tag_name") == tag:
            rel = r
            break

    if rel is None:
        st, rel = api("POST", f"{API}/repos/{REPO}/releases", payload={
            "tag_name": tag,
            "target_commitish": "main",
            "name": "电力专业低压数据采集软件 v1.1.0",
            "body": BODY,
            "draft": False,
            "prerelease": False,
        })
        print(f"RELEASE_CREATED id={rel['id']}")
    else:
        print(f"RELEASE_EXISTS id={rel['id']}")
        # 更新标题与说明
        st, rel = api("PATCH", f"{API}/repos/{REPO}/releases/{rel['id']}", payload={
            "name": "电力专业低压数据采集软件 v1.1.0",
            "body": BODY,
            "draft": False,
            "prerelease": False,
        })
        print("  已更新标题与说明")

    upload_url = rel["upload_url"].split("{")[0]
    existing = {a["name"] for a in rel.get("assets", [])}

    for path in assets:
        name = os.path.basename(path)
        size = os.path.getsize(path)
        if name in existing:
            print(f"SKIP（已存在）{name}")
            continue
        print(f"UPLOAD {name}  {size/1048576:.1f} MB ...", flush=True)
        t0 = time.time()
        with open(path, "rb") as f:
            data = f.read()
        url = f"{upload_url}?name={urllib.parse.quote(name)}"
        st, _ = api("POST", url, raw_body=data,
                    content_type="application/octet-stream", expect_json=False)
        print(f"  -> HTTP {st}  {time.time()-t0:.0f}s")

    st, rel = api("GET", f"{API}/repos/{REPO}/releases/tags/{tag}")
    print("\n=== RELEASE 校验 ===")
    print(f"url   : {rel['html_url']}")
    print(f"tag   : {rel['tag_name']}  draft={rel['draft']}  prerelease={rel['prerelease']}")
    print(f"assets: {len(rel['assets'])}")
    for a in rel["assets"]:
        print(f"  - {a['name']}  {a['size']/1048576:.1f} MB")


if __name__ == "__main__":
    main()
