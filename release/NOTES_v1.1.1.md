# 电力低压数据采集软件 v1.1.1 更新说明

**发布日期**：2026-11-17
**版本**：v1.1.1（EXE 元数据 1.1.1.0 / APK versionName 1.1.1 / versionCode 3）
**工作室**：晨曦网络工作室 · 重庆亦静怡动电子商务有限公司

## 本次更新（v1.1.0 → v1.1.1）

主题：**低内存 / 老旧设备优化** —— 解决小内存设备卡顿、处理数据时"需等待或关闭"、
APK 版 IP 定位无法联网。所有功能保持不变，仅优化代码结构与资源管理。

### 🐛 修复

1. **APK 版 IP 定位无法联网**
   - 根因：前端只 fetch 单一 `ipwho.is`，移动端弱网 / DNS 失败 / 被拦截时直接定位失败
   - 修复：改为 **4 源自动重试** `ipwho.is → api.ip.sb → ipapi.co → ip-api.com`，
     任一成功立即返回；每个源 8 秒超时（AbortController），避免卡死
   - **离线缓存**：定位成功后存 `localStorage.pc_ip_loc`，下次启动直接用，
     弱网 / 无网时也能显示上次定位位置
   - 失败时给出**细分原因**（无网络 / 被拦截 / 全部源不可用），不再笼统提示

2. **处理数据时"需等待或关闭"（APK 端户表工具）**
   - 根因：Apache POI 全量 DOM 解析大 xlsx 时长时间无进度反馈，UI 冻结被系统判定 ANR
   - 修复：`HutoolCore.readSourceRecords` / `modifyAndroidXls` 加**逐行 / 逐格进度回调**
     （每 100-200 行 / 格回调一次），UI 实时刷新进度条 + "已读 N 行 / N 处"
   - 每个台区处理完调用 **`System.gc()`** 释放 POI Workbook，避免内存累积 OOM
   - `writeCustomerXls` 去掉 `new HSSFWorkbook() + copySheet`（整张模板复制），
     改为**直接打开模板**，省一次完整解析 + 减少峰值内存

### ⚡ 性能优化（低内存 / 老旧 / 办公设备）

3. **Cesium 3D 地球内存自动降级**
   - `detectPerfLevel()` 新增 **`navigator.deviceMemory` 检测**：
     设备内存 < 2 GB 强制省资源档，< 4 GB 平衡档
   - 新增**移动 GPU 识别**（Mali / Adreno / PowerVR → 省资源档）
   - 省资源档**瓦片最大 zoom 从 19 降到 14**（减少 32 倍瓦片请求与内存占用）
   - 三档性能配置均加 `maxTileLevel`，按档自动适配

4. **APK 瘦身（R8 混淆 + 资源压缩）**
   - gradle release 开启 `minifyEnabled true` + `shrinkResources true`
   - 新增 `proguard-rules.pro`：保留 Apache POI 反射创建的类
     （`WorkbookFactory` / `HSSFWorkbook` / `XSSFWorkbook` / `SXSSFWorkbook`），
     忽略 log4j / osgi / slf4j / junrar 缺失依赖（APK 运行时不需要）
   - **APK 体积 67.2 MB → 62.7 MB**（减少约 4.5 MB）

5. **前端 GPU 识别增强**
   - `_gpuName()` 新增 3D vendor/renderer 探测（移动端 GPU 识别更准确）

### 📦 版本信息

| 产物 | 版本 | 大小 |
|---|---|---|
| Windows EXE | FileVersion 1.1.1.0 | 175 MB |
| Android APK | versionName 1.1.1 / versionCode 3 | 62.7 MB |

### 下载

- **EXE**：https://github.com/kalitang/GUI-925DHT/releases/download/v1.1.1/v1.exe
- **APK**：https://github.com/kalitang/GUI-925DHT/releases/download/v1.1.1/v1.apk

> v1.1.1 为 v1.1.0 的小版本修订，下载新版即可直接替换使用。

---
**版权**：晨曦网络工作室 · 重庆亦静怡动电子商务有限公司
**源码**：https://github.com/kalitang/GUI-925DHT
