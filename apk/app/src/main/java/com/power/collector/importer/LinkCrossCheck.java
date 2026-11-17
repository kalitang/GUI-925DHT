package com.power.collector.importer;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 连线算法交叉验证程序（桌面 JVM 运行）。
 *
 * 读 link_cases.json，跑 Java 版 LinkBuilder，输出 link_java_out.json，
 * 再由 cross_link.py cmp 与 Py 版结果逐条比对。
 */
public class LinkCrossCheck {

    public static void main(String[] args) throws Exception {
        String base = args.length > 0 ? args[0] : ".";
        String casesFile = base + "/link_cases.json";
        String outFile = base + "/link_java_out.json";

        // ---- 极简 JSON 解析（只处理本测试用例的结构）----
        String raw = readAll(casesFile);
        List<Map<String, Object>> cases = parseCases(raw);

        List<Map<String, Object>> out = new ArrayList<>();

        for (Map<String, Object> c : cases) {
            int cid = ((Number) c.get("id")).intValue();
            List<Map<String, Object>> devMaps = (List<Map<String, Object>>) c.get("devices");

            List<Device> devs = new ArrayList<>();
            for (Map<String, Object> dm : devMaps) {
                Device d = new Device();
                d.id = ((Number) dm.get("id")).intValue();
                d.name = (String) dm.get("name");
                d.line = (String) dm.get("line");
                d.lon = ((Number) dm.get("lon")).doubleValue();
                d.lat = ((Number) dm.get("lat")).doubleValue();
                d.type = (String) dm.get("type");
                devs.add(d);
            }

            List<LinkBuilder.Link> links = LinkBuilder.build(devs, 500.0);
            links.sort((a, b) -> {
                if (a.parent != b.parent) return Integer.compare(a.parent, b.parent);
                if (a.child != b.child) return Integer.compare(a.child, b.child);
                return Integer.compare(a.level, b.level);
            });

            Map<String, Object> segs = new LinkedHashMap<>();
            Map<String, Object> parents = new LinkedHashMap<>();
            for (Device d : devs) {
                List<String> sg = LinkBuilder.parsePoleName(d.name);
                segs.put(d.name, sg == null ? new ArrayList<String>() : sg);
                List<String> ps = (sg == null) ? null : LinkBuilder.parentSegments(sg);
                parents.put(d.name, ps);
            }

            List<List<Integer>> linkArr = new ArrayList<>();
            for (LinkBuilder.Link l : links) {
                linkArr.add(java.util.Arrays.asList(l.parent, l.child, l.level));
            }

            Map<String, Object> rec = new LinkedHashMap<>();
            rec.put("id", cid);
            rec.put("segs", segs);
            rec.put("parents", parents);
            rec.put("links", linkArr);
            out.add(rec);
        }

        try (PrintWriter pw = new PrintWriter(new FileWriter(outFile,
                java.nio.charset.StandardCharsets.UTF_8))) {
            pw.println(toJson(out));
        }
        System.out.println("Java 版结果 → " + outFile + "（" + out.size() + " 个用例）");
    }

    // ------------------------------------------------------------ 迷你 JSON

    static String readAll(String path) throws Exception {
        StringBuilder sb = new StringBuilder();
        try (BufferedReader br = new BufferedReader(new FileReader(path,
                java.nio.charset.StandardCharsets.UTF_8))) {
            String line;
            while ((line = br.readLine()) != null) sb.append(line).append('\n');
        }
        return sb.toString();
    }

    /** 解析 cases 文件：[{id, devices:[{...}]}] */
    static List<Map<String, Object>> parseCases(String s) {
        List<Map<String, Object>> cases = new ArrayList<>();
        int i = 0;
        while (true) {
            int dIdx = s.indexOf("\"devices\"", i);
            if (dIdx < 0) break;
            int idIdx = s.lastIndexOf("\"id\"", dIdx);
            int cid = (int) parseNum(s, idIdx + 4);
            int arrStart = s.indexOf('[', dIdx);
            int arrEnd = matchBracket(s, arrStart);
            String arrTxt = s.substring(arrStart + 1, arrEnd);
            List<Map<String, Object>> devs = parseDeviceArray(arrTxt);
            Map<String, Object> cm = new LinkedHashMap<>();
            cm.put("id", cid);
            cm.put("devices", devs);
            cases.add(cm);
            i = arrEnd + 1;
        }
        return cases;
    }

    static List<Map<String, Object>> parseDeviceArray(String txt) {
        List<Map<String, Object>> out = new ArrayList<>();
        int i = 0;
        while (i < txt.length()) {
            int ob = txt.indexOf('{', i);
            if (ob < 0) break;
            int oe = matchBracket(txt, ob);
            String o = txt.substring(ob + 1, oe);
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("id", (int) parseNum(o, o.indexOf("\"id\"") + 4));
            m.put("name", parseStr(o, "\"name\""));
            m.put("line", parseStr(o, "\"line\""));
            m.put("lon", parseNum(o, o.indexOf("\"lon\"") + 5));
            m.put("lat", parseNum(o, o.indexOf("\"lat\"") + 5));
            m.put("type", parseStr(o, "\"type\""));
            out.add(m);
            i = oe + 1;
        }
        return out;
    }

    static double parseNum(String s, int from) {
        int i = s.indexOf(':', from);
        i++;
        while (i < s.length() && (s.charAt(i) == ' ' || s.charAt(i) == '"')) i++;
        int st = i;
        while (i < s.length() && (Character.isDigit(s.charAt(i)) || s.charAt(i) == '-'
                || s.charAt(i) == '.' || s.charAt(i) == 'e' || s.charAt(i) == 'E')) i++;
        return Double.parseDouble(s.substring(st, i));
    }

    static String parseStr(String s, String key) {
        int k = s.indexOf(key);
        if (k < 0) return "";
        int q1 = s.indexOf('"', k + key.length());
        int q2 = s.indexOf('"', q1 + 1);
        return s.substring(q1 + 1, q2);
    }

    /** 从 open bracket 开始，找到配对的 close bracket */
    static int matchBracket(String s, int start) {
        int depth = 0;
        boolean inStr = false;
        for (int i = start; i < s.length(); i++) {
            char c = s.charAt(i);
            if (inStr) {
                if (c == '\\') { i++; continue; }
                if (c == '"') inStr = false;
                continue;
            }
            if (c == '"') { inStr = true; continue; }
            if (c == '[' || c == '{') depth++;
            else if (c == ']' || c == '}') {
                depth--;
                if (depth == 0) return i;
            }
        }
        return -1;
    }

    /** 输出 JSON（够用即可，字符串转义基础处理） */
    static String toJson(Object o) {
        StringBuilder sb = new StringBuilder();
        json(sb, o, 0);
        return sb.toString();
    }

    @SuppressWarnings("unchecked")
    static void json(StringBuilder sb, Object o, int indent) {
        String pad = " ".repeat(indent);
        String pad2 = " ".repeat(indent + 1);
        if (o == null) { sb.append("null"); return; }
        if (o instanceof Map) {
            Map<String, Object> m = (Map<String, Object>) o;
            sb.append("{\n");
            int i = 0;
            for (Map.Entry<String, Object> e : m.entrySet()) {
                sb.append(pad2).append('"').append(esc(e.getKey())).append("\": ");
                json(sb, e.getValue(), indent + 1);
                if (++i < m.size()) sb.append(',');
                sb.append('\n');
            }
            sb.append(pad).append('}');
            return;
        }
        if (o instanceof List) {
            List<Object> l = (List<Object>) o;
            if (l.isEmpty()) { sb.append("[]"); return; }
            sb.append("[\n");
            for (int i = 0; i < l.size(); i++) {
                sb.append(pad2);
                json(sb, l.get(i), indent + 1);
                if (i < l.size() - 1) sb.append(',');
                sb.append('\n');
            }
            sb.append(pad).append(']');
            return;
        }
        if (o instanceof String) { sb.append('"').append(esc((String) o)).append('"'); return; }
        if (o instanceof Boolean) { sb.append(o); return; }
        if (o instanceof Number) {
            double d = ((Number) o).doubleValue();
            if (d == Math.rint(d) && !Double.isInfinite(d)) sb.append((long) d);
            else sb.append(d);
            return;
        }
        sb.append('"').append(esc(String.valueOf(o))).append('"');
    }

    static String esc(String s) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        return sb.toString();
    }
}
