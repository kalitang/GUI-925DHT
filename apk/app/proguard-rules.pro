# ============================================================
# ProGuard / R8 规则（低内存优化：混淆 + 移除未用代码）
#
# 关键：Apache POI 的 WorkbookFactory.create() 用反射创建
# HSSFWorkbook/XSSFWorkbook 实现，必须保留这些类不被移除/混淆，
# 否则运行时报 ClassNotFoundException。
# ============================================================

# ---- 保留 Apache POI 核心（反射调用）----
-keep class org.apache.poi.hssf.** { *; }
-keep class org.apache.poi.xssf.** { *; }
-keep class org.apache.poi.ss.usermodel.** { *; }
-keep class org.apache.poi.openxml4j.** { *; }
-keep class org.apache.xmlbeans.** { *; }
-keep class org.apache.xmlbeans.XmlObject { *; }

# POI 依赖的 xmlbeans 反射
-dontwarn org.apache.xmlbeans.**
-dontwarn org.apache.poi.**

# ---- 保留本应用的 Java 桥（JS 反射调用 @JavascriptInterface）----
-keep class com.power.collector.** {
    @android.webkit.JavascriptInterface <methods>;
    *;
}
-keepclassmembers class com.power.collector.** {
    @android.webkit.JavascriptInterface <methods>;
}

# ---- 保留 Cesium 数据模型（JSON 反序列化用字段名）----
# Cesium 前端 JS 不混淆（assets 不走 R8），但 Java 侧 AndroidBridge
# 传递的数据结构若被 R8 处理需保留。实际 Java 侧只是传字符串/Map，
# 无需额外 keep。

# ---- 通用：保留枚举/序列化/反射 ----
-keepclassmembers enum * {
    public static **[] values();
    public static ** valueOf(java.lang.String);
}
-keepclassmembers class * implements java.io.Serializable {
    private static final java.lang.Class serialVersionUID;
    private static final java.lang.Class writeReplace;
    private static final java.lang.Class readResolve;
}

# ---- 保留日志（避免 R8 移除 Log 调用）----
-keep class android.util.Log { *; }

# ---- 允许优化 ----
-optimizationpasses 5
-keepattributes *Annotation*
-keepattributes Exceptions,InnerClasses,Signature
-keepattributes SourceFile,LineNumberTable
-repackageclasses 'com.power.collector'

# ---- R8 忽略 APK 不需要但被 POI 传递带进来的可选依赖 ----
# log4j/osgi 是 POI 的可选日志/服务发现依赖，APK 不用，忽略其缺失类
-dontwarn org.apache.logging.log4j.**
-dontwarn org.osgi.**
-dontwarn aQute.bnd.**
-dontwarn org.slf4j.**
-dontwarn org.apache.commons.logging.**
-dontwarn javax.servlet.**
-dontwarn com.github.junrar.**

# ---- Cesium 前端 assets（JS/HTML/WASM）不走 R8，但需保留 native 调用 ----
-keep class org.apache.commons.compress.** { *; }
-dontwarn org.apache.commons.compress.**
