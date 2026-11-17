package com.power.collector.importer;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 拓扑连线构建（Java 移植，逻辑与 Py 版 link_builder.py 逐条对齐）。
 *
 * 规则（用户 2026-09-28 手写笔记）：
 *   1) 按台区（line）分组，连线只允许本台区内
 *   2) 变压器 → 最近杆塔              (level=1, A→B)
 *   3) 杆塔   → 命名规则决定的前端杆塔  (level=1, B→B / A→B)
 *                命名解析失败 → 就近兜底（最近的其他杆塔）
 *   4) 接入点 → 最近杆塔              (level=2, B→C)
 *                每个接入点只连 1 个最近杆塔，但同一杆塔可被多个接入点共享
 *   5) 计量箱 → 最近接入点            (level=3, C→D)
 *
 * 命名分段解析示例：
 *   '0杆'→['0']  '1杆'→['1']  'A1杆'→['A','1']  'A1-1杆'→['A','1','1']
 *   'A1-A杆'→['A','1','A']  'A1-A-1杆'→['A','1','A','1']
 */
public final class LinkBuilder {

    /** 名称尾部的「杆 / 号杆 / 号电杆 / 号」需剥离后再解析（对齐 Py 版） */
    private static final Pattern NUMBER_TAIL_RE = Pattern.compile("(杆|号杆|号电杆|号)$");
    /** 多主线主干：A1 / B12 */
    private static final Pattern MAINLINE_RE = Pattern.compile("^([A-Z]+)(\\d+)$");
    /** 支线：主干 + dash + 段(≥1) */
    private static final Pattern BRANCH_RE =
            Pattern.compile("^((?:[A-Z]+\\d+|\\d+))((?:-[A-Z0-9]+)+)$");
    /** 纯数字段 */
    private static final Pattern DIGIT_RE = Pattern.compile("^\\d+$");
    /** 字母+数字 */
    private static final Pattern ALNUM_RE = Pattern.compile("^([A-Z]+)(\\d+)$");

    private LinkBuilder() {}

    /** 连线：parentId, childId, level */
    public static class Link {
        public final int parent, child, level;
        public Link(int p, int c, int l) { parent = p; child = c; level = l; }
    }

    /** 构建连线 */
    public static List<Link> build(List<Device> devices, double maxLinkDistM) {
        List<Link> links = new ArrayList<>();
        if (devices == null || devices.isEmpty()) return links;

        // 按台区分组 + 类型分桶
        Map<String, Map<String, List<Device>>> byLine = new LinkedHashMap<>();
        Map<Integer, String> lineOf = new HashMap<>();
        for (Device d : devices) {
            String line = (d.line == null || d.line.isEmpty()) ? "_unknown" : d.line;
            lineOf.put(d.id, line);
            byLine.computeIfAbsent(line, k -> new LinkedHashMap<>())
                  .computeIfAbsent(d.type, k -> new ArrayList<>())
                  .add(d);
        }

        Set<String> seenEdges = new HashSet<>();

        for (Map.Entry<String, Map<String, List<Device>>> le : byLine.entrySet()) {
            Map<String, List<Device>> byType = le.getValue();
            List<Device> transformers = byType.getOrDefault(Device.TYPE_TRANSFORMER, new ArrayList<>());
            List<Device> poles = new ArrayList<>(byType.getOrDefault(Device.TYPE_POLE, new ArrayList<>()));
            List<Device> aps = new ArrayList<>(byType.getOrDefault(Device.TYPE_ACCESS_POINT, new ArrayList<>()));
            List<Device> boxes = new ArrayList<>(byType.getOrDefault(Device.TYPE_METER_BOX, new ArrayList<>()));

            // 杆塔 segments 索引
            Map<Integer, List<String>> poleSegments = new HashMap<>();
            for (Device p : poles) {
                List<String> seg = parsePoleName(p.name);
                if (seg != null) poleSegments.put(p.id, seg);
            }
            // segments 元组 → 杆塔（同名冲突保留第一个）
            Map<String, Device> segToPole = new HashMap<>();
            for (Device p : poles) {
                List<String> seg = poleSegments.get(p.id);
                if (seg == null) continue;
                segToPole.putIfAbsent(String.join("", seg), p);
            }

            // 1) 变压器 → 最近杆塔 (level=1)
            //    注意方向：Py 版是 _add(tx, pole, 1) —— 变压器是【父】，杆塔是【子】
            for (Device tx : transformers) {
                Device pole = nearest(tx, poles, maxLinkDistM);
                if (pole != null) add(links, seenEdges, lineOf, tx, pole, 1);
            }

            // 2) 杆塔 → 命名规则决定的前端杆塔 (level=1)
            for (Device pole : poles) {
                Device parentPole = null;
                List<String> seg = poleSegments.get(pole.id);
                if (seg != null) {
                    List<String> parentSeg = parentSegments(seg);
                    if (parentSeg != null) {
                        // 候选链：完整父级 → 逐级剥 dash → 主干
                        List<List<String>> candidates = new ArrayList<>();
                        candidates.add(parentSeg);
                        List<String> cur = new ArrayList<>(parentSeg);
                        while (cur.size() > 2) {
                            List<String> nxt;
                            if (cur.size() >= 2 && isAlpha(cur.get(cur.size() - 2))
                                    && isDigit(cur.get(cur.size() - 1))) {
                                nxt = new ArrayList<>(cur.subList(0, cur.size() - 2));
                            } else {
                                nxt = new ArrayList<>(cur.subList(0, cur.size() - 1));
                            }
                            if (nxt.isEmpty() || candidates.contains(nxt)) break;
                            candidates.add(nxt);
                            cur = nxt;
                        }
                        for (List<String> cand : candidates) {
                            parentPole = segToPole.get(String.join("", cand));
                            if (parentPole != null) break;
                        }
                    }
                }
                if (parentPole == null) {
                    // 兜底：最近的其他杆塔
                    List<Device> others = new ArrayList<>();
                    for (Device p : poles) if (p.id != pole.id) others.add(p);
                    parentPole = nearest(pole, others, maxLinkDistM);
                }
                if (parentPole != null) add(links, seenEdges, lineOf, parentPole, pole, 1);
            }

            // 3) 接入点 → 最近杆塔 (level=2)
            if (!poles.isEmpty() && !aps.isEmpty()) {
                for (Device ap : aps) {
                    Device pole = nearest(ap, poles, maxLinkDistM);
                    if (pole != null) add(links, seenEdges, lineOf, pole, ap, 2);
                }
            }

            // 4) 计量箱 → 最近接入点 (level=3)
            if (!aps.isEmpty() && !boxes.isEmpty()) {
                for (Device box : boxes) {
                    Device ap = nearest(box, aps, maxLinkDistM);
                    if (ap != null) add(links, seenEdges, lineOf, ap, box, 3);
                }
            }
        }
        return links;
    }

    /** 添加连线（跨台区阻断 + 去重） */
    private static void add(List<Link> links, Set<String> seen, Map<Integer, String> lineOf,
                            Device parent, Device child, int level) {
        String lp = lineOf.get(parent.id);
        String lc = lineOf.get(child.id);
        if (lp != null && !lp.equals(lc)) return;    // 跨台区阻断
        String e = parent.id + "_" + child.id + "_" + level;
        if (seen.add(e)) links.add(new Link(parent.id, child.id, level));
    }

    /**
     * 最近设备（Haversine）。
     *
     * 【精确对齐 Py 版 link_builder._nearest】
     *   Py 版：best_d 初值 = max_dist_m + 1，只跟"当前最优"比较，
     *          最后直接 return best（不再跟 max_dist_m 比）。
     *   → 等价于：最近者距离 < (max_dist_m + 1) 即可返回，
     *     所以 500.1m 的候选在 max=500 时也会被选中。
     *   早期 Java 版误用 bestD <= maxDistM，导致这类边界连线丢失
     *   （交叉验证 42 用例中 1 例不一致，定位到此处）。
     */
    static Device nearest(Device from, List<Device> pool, double maxDistM) {
        Device best = null;
        double bestD = maxDistM + 1;
        for (Device d : pool) {
            if (d.id == from.id) continue;
            double dist = haversine(from.lon, from.lat, d.lon, d.lat);
            if (dist < bestD) { bestD = dist; best = d; }
        }
        return best;
    }

    static double haversine(double lon1, double lat1, double lon2, double lat2) {
        final double R = 6371000.0;
        double dLat = Math.toRadians(lat2 - lat1);
        double dLon = Math.toRadians(lon2 - lon1);
        double a = Math.sin(dLat / 2) * Math.sin(dLat / 2)
                 + Math.cos(Math.toRadians(lat1)) * Math.cos(Math.toRadians(lat2))
                 * Math.sin(dLon / 2) * Math.sin(dLon / 2);
        return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    // ------------------------------------------------------- 命名分段解析

    /** 解析杆塔名 → segments */
    static List<String> parsePoleName(String rawName) {
        if (rawName == null) return null;
        String n = rawName.trim();
        n = NUMBER_TAIL_RE.matcher(n).replaceFirst("");
        if (n.isEmpty()) return null;

        // 支线：主干 + dash + 段
        Matcher m = BRANCH_RE.matcher(n);
        if (m.matches()) {
            String trunk = m.group(1);
            String rest = m.group(2);
            String[] outerParts = rest.substring(1).split("-");
            List<String> all = new ArrayList<>(splitAlnum(trunk));
            for (String p : outerParts) {
                for (String ip : p.split("-")) all.addAll(splitAlnum(ip));
            }
            return all;
        }
        // 多主线主干 A1 / B12
        Matcher m2 = MAINLINE_RE.matcher(n);
        if (m2.matches()) return splitAlnum(m2.group(1) + m2.group(2));
        // 纯数字主干
        if (DIGIT_RE.matcher(n).matches()) return splitAlnum(n);
        return null;
    }

    /** 'A1'→['A','1']；'12'→['12']；'A'→['A'] */
    static List<String> splitAlnum(String s) {
        List<String> out = new ArrayList<>();
        if (s == null || s.isEmpty()) return out;
        Matcher m = ALNUM_RE.matcher(s);
        if (m.matches()) {
            out.add(m.group(1));
            out.add(m.group(2));
            return out;
        }
        out.add(s);
        return out;
    }

    /** 父级 segments（null = 母杆/无前端） */
    static List<String> parentSegments(List<String> seg) {
        if (seg == null || seg.isEmpty()) return null;

        // 单段
        if (seg.size() == 1) {
            String first = seg.get(0);
            if (isDigit(first)) {
                int n = Integer.parseInt(first);
                if (n == 1) return Arrays.asList("0");
                if (n >= 2) return Arrays.asList(String.valueOf(n - 1));
            }
            return null;
        }

        // 双段
        if (seg.size() == 2) {
            String first = seg.get(0), second = seg.get(1);
            // 字母+数字 → 字母主线
            if (isAlpha(first) && isDigit(second)) {
                int n = Integer.parseInt(second);
                if (n == 1) return Arrays.asList("0");
                if (n >= 2) return Arrays.asList(first, String.valueOf(n - 1));
                return null;
            }
            // 数字+数字 → 数字主干 + 数字支线
            if (isDigit(first) && isDigit(second)) {
                int n = Integer.parseInt(second);
                if (n == 1) return Arrays.asList(first);
                if (n >= 2) return Arrays.asList(first, String.valueOf(n - 1));
                return null;
            }
            return null;
        }

        // 多段（≥3）：支线
        String last = seg.get(seg.size() - 1);
        String secondLast = seg.get(seg.size() - 2);
        if (isAlpha(secondLast) && isDigit(last)) {
            int n = Integer.parseInt(last);
            if (n == 1) return new ArrayList<>(seg.subList(0, seg.size() - 2));
            List<String> r = new ArrayList<>(seg.subList(0, seg.size() - 1));
            r.add(String.valueOf(n - 1));
            return r;
        }
        if (isDigit(last)) {
            int n = Integer.parseInt(last);
            if (n == 1) return new ArrayList<>(seg.subList(0, seg.size() - 1));
            List<String> r = new ArrayList<>(seg.subList(0, seg.size() - 1));
            r.add(String.valueOf(n - 1));
            return r;
        }
        // 末段纯字母 → dash 数减 1
        return new ArrayList<>(seg.subList(0, seg.size() - 1));
    }

    static boolean isAlpha(String s) {
        if (s == null || s.isEmpty()) return false;
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z'))) return false;
        }
        return true;
    }

    static boolean isDigit(String s) {
        if (s == null || s.isEmpty()) return false;
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c < '0' || c > '9') return false;
        }
        return true;
    }
}
