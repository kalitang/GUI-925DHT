package com.power.collector.importer;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 设备数据模型（与 web 端 devices.json 结构一致）。
 */
public class Device {
    public int id;
    public String name = "";
    public String line = "";       // 台区名（A 列线路编号）
    public double lon;
    public double lat;
    public String type = "";       // 接入点（墙支架）/ 杆塔 / 柱上变（变压器）/ 计量箱 / 其他
    public String district = "";   // 镇街
    public String srcFile = "";
    public Map<String, String> props = new LinkedHashMap<>();

    // 类型常量（与 excel_loader.py 一致）
    public static final String TYPE_ACCESS_POINT = "接入点（墙支架）";
    public static final String TYPE_POLE = "杆塔";
    public static final String TYPE_TRANSFORMER = "柱上变（变压器）";
    public static final String TYPE_METER_BOX = "计量箱";
    public static final String TYPE_OTHER = "其他";

    public boolean isValid() {
        return lon > 70 && lon < 140 && lat > 15 && lat < 55;
    }

    /** 转成 web 端 JSON 对象 */
    public Map<String, Object> toJson() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", id);
        m.put("name", name);
        m.put("line", line);
        m.put("lon", round6(lon));
        m.put("lat", round6(lat));
        m.put("type", type);
        m.put("district", district);
        m.put("srcFile", srcFile);
        m.put("props", props);
        return m;
    }

    static double round6(double v) {
        return Math.round(v * 1e6) / 1e6;
    }

    /** 设备集合 */
    public static class List2 extends ArrayList<Device> {
        public List2() { super(); }
    }
}
