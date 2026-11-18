# EIAS v1.2.0 更新说明

**发布日期**：2026-11-18
**版本**：v1.2.0（EXE 元数据 1.2.0.0 / APK versionName 1.2.0 / versionCode 4）
**工作室**：晨曦网络工作室 · 重庆亦静怡动电子商务有限公司

## 本次更新（v1.1.1 → v1.2.0）

### 🆕 程序改名 EIAS

**电力低压数据采集软件** 正式更名为 **EIAS**（Electric Information Acquisition System
电力低压信息采集系统）。全量改名：

| 项 | 旧 | 新 |
|---|---|---|
| 程序名 | 电力低压数据采集软件 | **EIAS 电力低压信息采集** |
| 窗口标题 | 电力专业低压数据采集软件 | **EIAS 电力低压信息采集** |
| EXE 文件 | 电力低压数据采集软件.exe | **EIAS_v1.2.0.exe** |
| APK 文件 | 电力低压数据采集软件.apk | **EIAS_v1.2.0.apk** |
| APK 应用名 | 电力低压数据采集（乱码） | **EIAS** |
| EXE 元数据 | ProductName 电力低压数据采集软件 | **EIAS 电力低压信息采集** |
| Web 标题 | 电力低压数据采集 | **EIAS 电力低压信息采集** |

> 应用标识（Android applicationId `com.power.collector`、内部类名）保持不变，
> 已安装用户升级时不冲突。

### ⚙️ 版本管理自动化

- 新增 **`VERSION`** 文件统一管理版本号（当前 1.2.0）
- 新增 **`tools/bump_version.py`** 自动同步脚本：
  - `python tools/bump_version.py` → +1 修订（自动改 6 个文件）
  - `python tools/bump_version.py minor` / `major` / `1.5.0`
  - 每次修改/深化后运行一次，版本号全量同步（main.py / version_info.py /
    packaging.spec / build.gradle / web / VERSION）

### 📦 继承 v1.1.1 全部优化

本版本基于 v1.1.1 的低内存优化版（IP 多源重试 + POI 流式进度 + Cesium 降档
+ R8 瘦身），功能与性能保持不变。

### 📦 产物

| 产物 | 大小 |
|---|---|
| EIAS_v1.2.0.exe | 175 MB |
| EIAS_v1.2.0.apk | 62.7 MB |

### 下载

- **EXE**：https://github.com/kalitang/GUI-925DHT/releases/download/v1.2.0/EIAS_v1.2.0.exe
- **APK**：https://github.com/kalitang/GUI-925DHT/releases/download/v1.2.0/EIAS_v1.2.0.apk

---
**版权**：晨曦网络工作室 · 重庆亦静怡动电子商务有限公司
**源码**：https://github.com/kalitang/GUI-925DHT
