package com.power.collector.importer;

/**
 * 轻量日志工具。
 *
 * 【为什么不用 android.util.Log】
 * 本类需要在桌面 JVM 上跑测试（ExcelParseCheck / LinkCrossCheck），
 * 而 android.util.Log 在桌面 JVM 不存在。反射调用太啰嗦，
 * 直接判断 Android 类是否可用最简单。
 */
final class PCLog {

    private static final boolean HAS_ANDROID;
    private static final Class<?> LOG_CLASS;

    static {
        Class<?> c = null;
        try {
            c = Class.forName("android.util.Log");
        } catch (Throwable ignored) {
        }
        LOG_CLASS = c;
        HAS_ANDROID = c != null;
    }

    private PCLog() {}

    static void e(String tag, String msg) {
        if (HAS_ANDROID) {
            try {
                LOG_CLASS.getMethod("e", String.class, String.class)
                          .invoke(null, tag, msg);
                return;
            } catch (Throwable ignored) {
            }
        }
        System.err.println("[E/" + tag + "] " + msg);
    }

    static void w(String tag, String msg) {
        if (HAS_ANDROID) {
            try {
                LOG_CLASS.getMethod("w", String.class, String.class)
                          .invoke(null, tag, msg);
                return;
            } catch (Throwable ignored) {
            }
        }
        System.err.println("[W/" + tag + "] " + msg);
    }

    static void i(String tag, String msg) {
        if (HAS_ANDROID) {
            try {
                LOG_CLASS.getMethod("i", String.class, String.class)
                          .invoke(null, tag, msg);
                return;
            } catch (Throwable ignored) {
            }
        }
        System.out.println("[I/" + tag + "] " + msg);
    }

    static void e(String tag, String msg, Throwable t) {
        if (HAS_ANDROID) {
            try {
                LOG_CLASS.getMethod("e", String.class, String.class, Throwable.class)
                          .invoke(null, tag, msg, t);
                return;
            } catch (Throwable ignored) {
            }
        }
        System.err.println("[E/" + tag + "] " + msg + " :: " + t);
        t.printStackTrace();
    }
}
