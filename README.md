# 电力专业低压数据采集软件

3D 地球 + ArcGIS 卫星影像 + 本地瓦片缓存 + 自动从 Excel 提取电网设备点位 + 拓扑层级与挂接管理。

单机桌面工具（PySide6 + CesiumJS），仅监听本机回环地址，不对外提供服务。

## 📦 三个版本

本项目同时提供 **GUI 源码版** / **Windows EXE 版** / **Android APK 版**，三者功能一致，均以 GUI 版为准。

| 版本 | 载体 | 下载 |
|------|------|------|
| **GUI 源码版** | Python 源码 | 克隆本仓库后 `python main.py` |
| **Windows EXE 版** | 单文件 EXE（175 MB） | [Releases 页面](https://github.com/kalitang/GUI-925DHT/releases) 下载 `电力低压数据采集软件.exe` |
| **Android APK 版** | APK（67 MB） | [Releases 页面](https://github.com/kalitang/GUI-925DHT/releases) 下载 `电力低压数据采集软件.apk` |

- EXE 版免安装，双击即用；内置《低压客户模板.xls》。
- APK 版已用 v2 方案签名，Android 7.0+ 可直接安装；使用「导入压缩包」或「打开目录」加载数据。

> ⚠️ EXE / APK 体积较大，作为 **Release 附件**分发，不进 git 历史，保持仓库轻量。

## 🧰 户表批量工具

内置的《户表批量工具》把「户表」离线表格批处理整合进主程序，**三个版本均可用**（菜单：户表批量 → 打开户表批量工具 / `Ctrl+U`）。

| 功能 | 说明 |
|------|------|
| ① 生成客户档案 | 按台区数据表批量生成《低压客户模板》客户档案 |
| ② 批量修改安卓表 | 把数据表中的 `0杆` 改为台区名；可选保留「采集版副本」 |
| ③ 提取整理 PDF | 把台区 PDF 归集到根目录 `打印/` 目录（幂等） |
| ④ 准备采集软件数据 | 一键补齐采集软件所需数据目录（幂等） |

支持 **停止**（随时中断）、**清空日志**、**运行日志区**、**进度条**、**成功/失败/跳过计数**、**状态提示**。

- GUI / EXE 版：Windows Excel COM 自动化（`win32com`）
- APK 版：Java Apache POI 等价实现（移动端无 COM）

## 📄 版权

© 晨曦网络工作室 · 重庆亦静怡动电子商务有限公司

## 功能特性

### 数据与拓扑
- **自动设备类型识别**：扫描「柱上变安卓 / 柱上安卓 / 柱上公变」等导出的 `.xls`，按设备名自动分类
  - `0杆` → 杆塔，并在同位置生成变压器（名称取 A 列线路编号）
  - 含「计量 / 表箱 / 电表 / 表位」→ 计量箱
  - 含「杆」→ 杆塔
  - 纯数字 → 接入点（墙支架）
  - 其余 → 其他
- **同坐标多台区不丢失**：同一坐标若属于不同台区，是两个独立设备，全部保留
- **同坐标不同台区用不同颜色**：冲突簇内各台区分配不同颜色，**同一台区在所有簇里颜色一致**
  - 仅冲突簇参与着色，其余设备保持类型默认色（地图不会变成调色盘）
- **连线拓扑**（A→B→C/D）：变压器→杆塔→接入点→计量箱
  - 杆塔按命名规则链推导父级（`0杆`/`N杆`/`AN杆`/`A1-2-1杆` 等，含支线回退）
  - 接入点挂最近杆塔，计量箱挂最近接入点
  - **连线不跨台区**（硬约束）
- **层级树**：台区 → 变压器 → 杆塔 → 接入点 → 计量箱 → 未分配
- **手动改挂接**：右键节点改父设备，持久化到 `cache/tree_overrides.json`，地图连线实时同步
  - 保证每个设备有且仅有一条上游连线
- **供电覆盖范围**：每台区凸包 + 80m 缓冲 → GeoJSON 多边形，半透明渲染

### 地图与界面
- **3D 真实地球**：CesiumJS 渲染 WGS84 椭球，自带星空 + 太阳/月亮（跟随时区）
- **ArcGIS 卫星影像**：Esri World Imagery，最大缩放 19 级
- **本地瓦片缓存**：所有瓦片落盘 `cache/tiles/`，**断网后已缓存区域仍可显示**
- **真实比例图标**：6 种 SVG 设备图标按真实世界尺寸（米）渲染，随缩放动态变尺寸
- **性能自适应**：自动探测显卡 → 省资源 / 平衡 / 全效 三档
- **GUI**：左树状设备列表 + 右地图；类型筛选、搜索（含设备属性全文）、CSV 导出
- **IP 定位**：探测本机出口经纬度，地图显示定位标记 + 罗盘指向

## 运行环境

- Python 3.10+（推荐 3.11+）
- Windows / macOS / Linux
- 网络：首次浏览目标区域需联网拉瓦片；之后可离线使用已缓存区域

## 启动

### Windows

双击 `启动.bat`（或 `Start.bat` / `launch.vbs` 无黑窗启动）

或命令行：

```cmd
python main.py
```

首次启动会自动安装 PySide6 / xlrd / requests，然后：

1. 启动本地 HTTP 瓦片代理（`127.0.0.1:8765`）
2. 加载 Cesium 3D 地球
3. **自动识别数据目录并加载**（见下）

## 路径规则（无硬编码盘符）

所有路径由 `app_paths.py` 从程序自身位置反推，**整个程序目录可以移动到任意盘符**：

| 变量 | 含义 |
|------|------|
| `APP_ROOT` | `PowerCollector/` 程序根 |
| `WORKSPACE_ROOT` | 程序根的上一级（工作区） |
| `CACHE_DIR` / `TILE_CACHE_DIR` | `PowerCollector/cache/`、`cache/tiles/` |
| `WEB_DIR` | `PowerCollector/web/` |
| `DATA_ROOT` | `WORKSPACE_ROOT/_peek/` |

**数据目录自动识别顺序**：

1. 环境变量 `POWERCOLLECTOR_DATA` 指定的目录
2. `WORKSPACE_ROOT/_peek/`（含 `.xls`/`.xlsx` 时）
3. `WORKSPACE_ROOT` 下第一个含数据表的一层子目录
4. 都没有 → 提示用「打开目录」手动选择

## 数据格式

数据目录里放 `*.xls`（Android 导出）或 `*.xlsx`，每个文件 = 一个台区。

**墙支架** sheet 列定义（列 3=设备名称，列 4/5=经纬度）：

| 列 | 字段 | 说明 |
|----|------|------|
| 1 | 线路编号 | 台区名（同台区所有设备共享） |
| 2 | 线路名称 | 通常为 `.` |
| 3 | **设备名称** | 设备编号 / 杆塔名 |
| 4 | **经度** | WGS84 |
| 5 | **纬度** | WGS84 |
| 6 | 备注 | 可选 |
| 7 | 序号 | 台区内序号 |
| 8 | `*名称` | 设备业务名 |
| 9 | 资源ID | UUID |

经纬度列自动探测：默认 D/E，若非数字则扫全表找连续两位小数列。

## 设备类型与图标

| 类型 | 识别规则 | 图标 | 真实尺寸 |
|------|----------|------|----------|
| 柱上变（变压器） | `0杆` 所在位置 | `transformer.svg` | 1.2m × 1.2m |
| 接入点（墙支架） | C 列纯数字 | `access_point.svg` | 0.4m × 0.4m |
| 杆塔 | 含「杆」 | `pole.svg` | 0.4m 直径 |
| 计量箱 | 含「计量/表箱/电表/表位」 | `meter_box.svg` | 0.2m × 0.4m |
| 其他 | 兜底 | `other.svg` | 0.3m |

## 菜单与操作

- **文件** → 打开数据目录 / 从 ZIP 导入 / 清空瓦片缓存 / 退出
- **视图** → 聚焦所有设备
- **工具栏** → 聚焦全部 · 显示设备 · 显示名称 · 显示连线 · 覆盖范围 · 导出 CSV · 性能档位
- **设备列表** → 左键飞行到该节点；右键改挂接；搜索框按名称/台区/设备属性过滤
- **地图** → 左键拖动旋转/平移，滚轮缩放（≤19 级），右键倾斜，点击图标看详情

## 文件结构

```
PowerCollector/
├── app_paths.py           # 统一路径解析（零硬编码盘符）
├── main.py                # 主程序入口（PySide6 GUI）
├── hutool_core.py         # 户表批量工具核心（4 功能，Excel COM）
├── hutool_dialog.py       # 户表批量工具 GUI 对话框
├── excel_loader.py        # Excel 解析 + 设备分类
├── link_builder.py        # 连线拓扑构建（杆塔命名规则）
├── link_overrides.py      # 手动挂接 → 连线重算
├── tree_builder.py        # 台区层级树
├── pole_colors.py         # 同坐标多台区配色
├── coverage.py            # 台区供电覆盖范围
├── tile_proxy.py          # 本地瓦片 HTTP 代理 + 缓存 + 静态托管
├── build_apk.py           # Android APK 构建脚本（含前端 overlay 注入）
├── packaging.spec         # PyInstaller 打包配置
├── requirements.txt
├── 低压客户模板.xls        # 户表工具模板（打进 EXE / APK）
├── 启动.bat / Start.bat / launch.vbs / launch_hidden.cmd
├── web/
│   ├── index.html         # Cesium 前端（含杆塔着色、渲染串行锁、户表面板）
│   ├── Cesium.js          # CesiumJS 1.131
│   └── icons/             # 6 个 SVG 设备图标
├── apk/                   # Android 工程（Gradle）
│   ├── app/src/main/java/com/power/collector/hutool/   # 户表工具 Java 实现
│   └── app/libs/          # Apache POI 5.2.5（本地 jar，离线可构建）
├── assets/icons/          # 6 个 SVG 设备图标（打包用）
└── cache/                 # 运行时生成：tiles/、webengine/、tree_overrides.json
```

## 构建

### Windows EXE

```cmd
python -m PyInstaller --clean --noconfirm packaging.spec
```

产物：`dist/电力低压数据采集软件.exe`（单文件，含模板与户表工具）。

### Android APK

```cmd
python build_apk.py release
```

产物：`apk/app/build/outputs/apk/release/app-release.apk`（v2 签名，内置模板 + Java 版户表工具）。

> 构建需要 JDK 17、Android SDK、Gradle 8.7；APK 构建脚本会自动同步 `web/` 资产并注入移动端 overlay 与户表面板。

## 技术栈

- **Python 3.13+** + **PySide6 6.11**（GUI + QtWebEngine）
- **xlrd**（`.xls` 解析）/ **openpyxl**（`.xlsx` 解析）/ **requests**（HTTP）
- **CesiumJS 1.131**（3D 地球）
- **ArcGIS World Imagery**（免费卫星底图）

## 已知限制

- 首次浏览目标区域需联网拉瓦片（已缓存区域可离线）
- 瓦片并发下载时部分可能超时（8s 超时 + 2 次重试，失败回退透明占位图）
- 设备量极大（>10 万）时 Cesium 实体渲染会有压力

## 开发自检

```cmd
python _peek/selfcheck.py        # 38 项结构/逻辑自检（不依赖真实数据）
python _peek/verify_colors.py    # 真实数据的配色验证
```
