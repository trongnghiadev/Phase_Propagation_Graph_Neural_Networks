"""Test quy tắc thuần Python của G1/G3/G4/G5 và Q5–Q7 (không cần Spark).

    python -X utf8 pipelines/spark/tests/test_features_rules.py

Phạm vi:
- G1: dựng cây MERIT, kiểm tra topology, cấp topo, luật tỉa, đường đi tới cửa sản.
- G3: thay `slope = 0`, kẹp vận tốc, τ hợp lệ, quy tắc ngày của đỉnh (`peak_day_offset`).
- G4: quy tắc gán trạm mưa và trọng số diện tích (tổng trọng số = 1).
- G5: quy tắc ngưỡng báo động theo thời gian, mức vượt, chia tập.
- Q5: khoảng cách giữa hai đoạn thẳng trên mặt cầu Trái Đất.
"""
from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import geometry as GEO  # noqa: E402
from features import rules as R  # noqa: E402


# ---------------------------------------------------------------------------
# Dữ liệu giả: cây lưu vực nhỏ có hội tụ
# ---------------------------------------------------------------------------
# Sơ đồ (số là COMID):
#
#        10 ── 20 ── 30        (nhánh tráy)
#         │
#   5 ── 1 ── 2 ── 3 ── 4     (nhánh phải)      4 = cửa sản (NextDownID = 0)
#
# Cạnh `up1..up4` trỏ tới nhánh CHẢY VÀO reach đó.
def _reach(comid, next_down=0, ups=(), order=3, slope=0.001, uparea=100.0, lengthkm=1.0):
    d = {"COMID": comid, "NextDownID": next_down, "order": order, "slope": slope,
         "uparea": uparea, "lengthkm": lengthkm, "maxup": len(ups)}
    for i, u in enumerate(ups, start=1):
        d[f"up{i}"] = u
    return d


REACHES = {
    4: _reach(4, 0, [3], order=1, uparea=1000.0, lengthkm=10.0),
    3: _reach(3, 4, [2], order=2, uparea=900.0, lengthkm=8.0),
    2: _reach(2, 3, [1, 10], order=3, uparea=800.0, lengthkm=6.0),
    1: _reach(1, 2, [5], order=4, uparea=700.0, lengthkm=5.0),
    10: _reach(10, 2, [20], order=4, uparea=600.0, lengthkm=4.0),
    20: _reach(20, 10, (), order=5, uparea=500.0, lengthkm=3.0),
    5: _reach(5, 1, (), order=5, uparea=400.0, lengthkm=2.0),
}


class TestTree(unittest.TestCase):
    """G1 — dựng cây và kiểm tra topology."""

    def test_build_tree(self):
        tree = R.build_tree(REACHES, 4)
        self.assertEqual(tree, {1, 2, 3, 4, 5, 10, 20})

    def test_build_tree_emptiness_on_unknown_root(self):
        self.assertEqual(R.build_tree(REACHES, 999), set())

    def test_check_tree_ok(self):
        tree = R.build_tree(REACHES, 4)
        self.assertEqual(R.check_tree(REACHES, tree), [])

    def test_check_tree_detects_self_loop(self):
        bad = dict(REACHES)
        bad[3] = _reach(3, 3, [2], order=2)
        tree = R.build_tree(bad, 4)
        errs = R.check_tree(bad, tree)
        self.assertTrue(any("chính nó" in e for e in errs), errs)

    def test_check_tree_detects_cycle(self):
        bad = dict(REACHES)
        # 20 → 10 → 20 tạo chu trình hướng xuôi (NextDownID), đồng thời giữ nguyên
        # quan hệ `up` để node 20 vẫn nằm trong cây.
        bad[10] = _reach(10, 20, [20], order=4)
        bad[20] = _reach(20, 10, (), order=5)
        tree = R.build_tree(bad, 4)
        self.assertIn(20, tree)
        errs = R.check_tree(bad, tree)
        self.assertTrue(errs, "phải phát hiện chu trình")

    def test_confluence_nodes(self):
        tree = R.build_tree(REACHES, 4)
        # nút 2 có hai nhánh chảy vào (1 và 10) → điểm hội tụ
        conf = R.confluence_nodes(REACHES, tree)
        self.assertIn(2, conf)

    def test_downstream_child(self):
        tree = R.build_tree(REACHES, 4)
        self.assertEqual(R.downstream_child(REACHES, 3, tree), 4)
        self.assertIsNone(R.downstream_child(REACHES, 4, tree))

    def test_path_to_root(self):
        tree = R.build_tree(REACHES, 4)
        self.assertEqual(R.path_to_root(REACHES, 20, tree), [20, 10, 2, 3, 4])
        self.assertEqual(R.path_to_root(REACHES, 4, tree), [4])


class TestTopoLevels(unittest.TestCase):
    """G1 — cấp topo theo hai hướng."""

    def setUp(self):
        self.tree = R.build_tree(REACHES, 4)
        self.levels = R.topo_levels(REACHES, self.tree, 4)

    def test_down_levels(self):
        d = self.levels["down"]
        self.assertEqual(d[4], 0)
        self.assertEqual(d[3], 1)
        self.assertEqual(d[2], 2)
        self.assertEqual(d[1], 3)
        self.assertEqual(d[5], 4)
        self.assertEqual(d[10], 3)
        self.assertEqual(d[20], 4)

    def test_up_levels_are_longest_path(self):
        """`up` = đường đi dài nhất từ lá tới nút.

        Nhánh phải dài 4 bước (5→1→2→3→4), nhánh trái 3 bước (20→10→2→3→4).
        Nút 2 phải nhận giá trị 2 (từ lá 5, đường dài nhất tới 2).
        """
        u = self.levels["up"]
        self.assertEqual(u[5], 0)
        self.assertEqual(u[20], 0)
        self.assertEqual(u[1], 1)
        self.assertEqual(u[10], 1)
        self.assertEqual(u[2], 2)
        self.assertEqual(u[3], 3)
        self.assertEqual(u[4], 4)

    def test_levels_cover_all_nodes(self):
        self.assertEqual(set(self.levels["down"]), self.tree)
        self.assertEqual(set(self.levels["up"]), self.tree)


class TestPruning(unittest.TestCase):
    """G1 — luật tỉa theo `uparea` + đường đi giữa trạm/hồ và cửa sản."""

    def setUp(self):
        self.tree = R.build_tree(REACHES, 4)

    def test_keeps_big_nodes(self):
        keep = R.keep_nodes(REACHES, self.tree, 500.0, set())
        # uparea ≥ 500: 4 (1000), 3 (900), 2 (800), 1 (700), 10 (600), 20 (500)
        self.assertTrue({4, 3, 2, 1, 10, 20} <= keep)
        self.assertIn(20, keep)   # uparea 500 → bằng ngưỡng thì giữ

    def test_small_node_pruned_unless_needed_for_up_edge(self):
        """Nút nhỏ bị tỉa, TRỪ khi nó là nhánh chảy vào của một nút còn lại.

        Node 5 (uparea 400) là nhánh chảy vào node 1 (uparea 700 ≥ 500) → phải giữ lại
        để cạnh `up` của node 1 tồn tại sau khi tỉa.
        """
        keep = R.keep_nodes(REACHES, self.tree, 500.0, set())
        self.assertIn(5, keep)     # giữ vì cần cho cạnh `up` của node 1

    def test_small_leaf_pruned(self):
        """Nút lá nhỏ bị tỉa hẳn khi không phục vụ cạnh `up` nào.

        Node 6 là lá (uparea 50) chảy vào node 3 — nhưng node 3 đã có nhánh chảy vào khác
        (node 2), nên không cần node 6.
        """
        reaches = dict(REACHES)
        reaches[3] = _reach(3, 4, [2, 6], order=2, uparea=900.0, lengthkm=8.0)
        reaches[6] = _reach(6, 3, (), order=6, uparea=50.0, lengthkm=1.0)
        tree = R.build_tree(reaches, 4)
        keep = R.keep_nodes(reaches, tree, 500.0, set())
        self.assertNotIn(6, keep)   # lá nhỏ, node 3 đã có nhánh chảy vào khác
        self.assertIn(3, keep)

    def test_keeps_station_path_to_outlet(self):
        """Trạm ở nút 20 (uparea nhỏ) → giữ cả đường đi 20→10→2→3→4."""
        keep = R.keep_nodes(REACHES, self.tree, 900.0, {20})
        self.assertTrue({20, 10, 2, 3, 4} <= keep)

    def test_keeps_reservoir_even_if_small(self):
        keep = R.keep_nodes(REACHES, self.tree, 10_000.0, {5})
        self.assertIn(5, keep)

    def test_connect_upstream_keeps_a_link(self):
        """Sau tỉa, mọi nút còn lại có nhánh chảy vào phải còn lại (để cạnh `up` tồn tại).

        Nút lá (không có nhánh chảy vào) không cần cạnh `up` nào.
        """
        keep = R.keep_nodes(REACHES, self.tree, 700.0, set())
        for n in keep:
            ups = [u for u in R.upstream_children(REACHES, n, self.tree) if u in keep]
            self.assertTrue(ups or not R.upstream_children(REACHES, n, self.tree),
                            f"nút {n} có nhánh chảy vào nhưng nhánh đó bị tỉa mất")


class TestSlopeFill(unittest.TestCase):
    """G3 — thay `slope = 0` bằng trung vị cùng `order`."""

    def test_median_of_same_order(self):
        reaches = {
            1: _reach(1, 0, (), order=1, slope=0.001),
            2: _reach(2, 0, (), order=1, slope=0.003),
            3: _reach(3, 0, (), order=1, slope=0.0),
        }
        out = R.fill_slope(reaches, {1, 2, 3})
        self.assertEqual(out[3][0], 0.0)            # giá trị gốc
        self.assertAlmostEqual(out[3][1], 0.002)    # trung vị của {0.001, 0.003}

    def test_keeps_positive_slope(self):
        reaches = {1: _reach(1, 0, (), order=1, slope=0.005)}
        out = R.fill_slope(reaches, {1})
        self.assertEqual(out[1], (0.005, 0.005))

    def test_falls_back_to_global_median(self):
        reaches = {
            1: _reach(1, 0, (), order=1, slope=0.0),
            2: _reach(2, 0, (), order=2, slope=0.004),
        }
        out = R.fill_slope(reaches, {1, 2})
        # nhóm order 1 không có slope > 0 → dùng trung vị toàn cây
        self.assertAlmostEqual(out[1][1], 0.004)


class TestPhysics(unittest.TestCase):
    """G3 — vật lý, kẹp vận tốc, τ."""

    def test_hydraulic_radius_is_a_depth_scale(self):
        """R phải là bán kính THỦY VỰC (vài mét), không phải bề rộng (hàng km)."""
        r_small = R.hydraulic_radius(100.0)
        r_big = R.hydraulic_radius(45_551.0)
        self.assertLess(r_small, 20.0)
        self.assertLess(r_big, 30.0)
        self.assertGreater(r_big, r_small)

    def test_manning_velocity_plausible(self):
        """Vận tốc Manning phải trong khoảng hợp lý của sông (0,3–5 m/s)."""
        for area in (100.0, 1_000.0, 10_000.0, 45_551.0):
            v = R.manning_velocity(R.hydraulic_radius(area), 0.0002)
            self.assertGreater(v, 0.3)
            self.assertLess(v, 5.0)

    def test_tau_down_clamped(self):
        """τ luôn trong [TAU_MIN_H, TAU_MAX_H] và không âm/NaN."""
        for area in (10.0, 100.0, 5_000.0):
            for slope in (0.0, 0.0002, 0.01):
                tau, c = R.tau_down(10.0, area, slope)
                self.assertGreaterEqual(tau, R.TAU_MIN_H)
                self.assertLessEqual(tau, R.TAU_MAX_H)
                self.assertFalse(math.isnan(tau))
                self.assertGreater(c, 0.0)

    def test_tau_up_clamped(self):
        for area in (10.0, 100.0, 5_000.0):
            for slope in (0.0, 0.0002, 0.01):
                tau, c = R.tau_up(10.0, area, slope)
                self.assertGreaterEqual(tau, R.TAU_MIN_H)
                self.assertLessEqual(tau, R.TAU_MAX_H)
                self.assertFalse(math.isnan(tau))
                self.assertGreater(c, 0.0)

    def test_celerity_bounds(self):
        for area in (10.0, 100.0, 45_551.0):
            for slope in (0.0, 0.0002, 0.05):
                _, c_d = R.tau_down(10.0, area, slope)
                _, c_u = R.tau_up(10.0, area, slope)
                self.assertGreaterEqual(c_d, R.FLOOD_C_MIN)
                self.assertLessEqual(c_d, R.FLOOD_C_MAX)
                self.assertGreaterEqual(c_u, R.SHALLOW_C_MIN)
                self.assertLessEqual(c_u, R.SHALLOW_C_MAX)

    def test_tau_up_uses_tidal_factor(self):
        """Sóng triều phải CHẬM hơn `sqrt(g·h)` (kênh chảy một phần)."""
        h = 3.0
        c = R.celerity_tidal(h, 0.0)
        self.assertLess(c, math.sqrt(R.G * h))
        self.assertGreater(c, 0.0)

    def test_flow_velocity_reduces_tidal_celerity(self):
        self.assertLess(R.celerity_tidal(3.0, 1.0), R.celerity_tidal(3.0, 0.0))

    def test_tau_zero_length(self):
        tau, c = R.tau_down(0.0, 100.0, 0.001)
        self.assertEqual(tau, R.TAU_MIN_H)


class TestDayOffset(unittest.TestCase):
    """G5 — quy tắc ngày của đỉnh (phải khớp với `pipelines/r/`)."""

    def test_same_day(self):
        self.assertEqual(R.peak_day_offset("2020-01-01", "2020-01-01"), 0)

    def test_next_day(self):
        self.assertEqual(R.peak_day_offset("2020-01-01", "2020-01-02"), 1)

    def test_before_day_is_same(self):
        self.assertEqual(R.peak_day_offset("2020-01-02", "2020-01-01"), 0)

    def test_constants(self):
        self.assertEqual(R.DAY_OFFSET_SAME, 0)
        self.assertEqual(R.DAY_OFFSET_NEXT, 1)


class TestRainWeights(unittest.TestCase):
    """G4 — gán trạm mưa và trọng số diện tích."""

    NODES = {
        1: {"coords": [[106.0, 10.0], [106.1, 10.0]], "bbox": [106.0, 10.0, 106.1, 10.0]},
        2: {"coords": [[106.5, 10.5], [106.6, 10.5]], "bbox": [106.5, 10.5, 106.6, 10.5]},
    }
    STATIONS = [
        {"code": "S1", "lon": 106.05, "lat": 10.0},
        {"code": "S2", "lon": 106.55, "lat": 10.5},
        {"code": "FAR", "lon": 107.5, "lat": 11.5},
    ]

    def test_assign_only_nearby(self):
        from features import rain_rules as g4
        assign = g4.assign_station_to_nodes(self.STATIONS, self.NODES, 40.0)
        self.assertEqual(set(assign), {1, 2})
        self.assertEqual([c for c, _ in assign[1]], ["S1"])
        self.assertEqual([c for c, _ in assign[2]], ["S2"])

    def test_radius_excludes_far_station(self):
        from features import rain_rules as g4
        assign = g4.assign_station_to_nodes(self.STATIONS, self.NODES, 5.0)
        self.assertEqual(set(assign), {1, 2})

    def test_weights_sum_to_one(self):
        from features import rain_rules as g4
        assign = g4.assign_station_to_nodes(self.STATIONS, self.NODES, 40.0)
        weights = g4.node_weights({1: 100.0, 2: 200.0}, assign)
        for comid, w in weights.items():
            self.assertAlmostEqual(sum(w.values()), 1.0, places=9)
            self.assertEqual(set(w), {c for c, _ in assign[comid]})

    def test_weights_are_area_shares(self):
        """w(n,s) = [uparea(n)/A(s)] chuẩn hoá theo tập trạm của nút, tổng = 1.

        S1 được gán cho nút 1 (uparea 100) và nút 3 (uparea 300) → A(S1) = 400.
        - nút 1: raw = 100/400 = 0,25 → chuẩn hoá (chỉ S1) → 1,0
        - nút 3: raw = 300/400 = 0,75 → chuẩn hoá (chỉ S1) → 1,0
        (mỗi nút chỉ có MỘT trạm nên trọng số = 1; khác biệt nằm ở tỷ lệ thô)
        """
        from features import rain_rules as g4
        nodes = dict(self.NODES)
        nodes[3] = {"coords": [[106.05, 10.0], [106.05, 10.1]],
                    "bbox": [106.05, 10.0, 106.05, 10.1]}
        assign = g4.assign_station_to_nodes(self.STATIONS, nodes, 40.0)
        weights = g4.node_weights({1: 100.0, 2: 200.0, 3: 300.0}, assign)
        self.assertAlmostEqual(weights[1]["S1"], 1.0)
        self.assertAlmostEqual(weights[3]["S1"], 1.0)
        self.assertAlmostEqual(sum(weights[1].values()), 1.0)
        self.assertAlmostEqual(sum(weights[3].values()), 1.0)

    def test_weights_split_between_stations(self):
        """Nút có 2 trạm → trọng số chia theo tỷ lệ uparea(n)/A(s), tổng vẫn = 1."""
        from features import rain_rules as g4
        nodes = dict(self.NODES)
        # nút 4 nằm giữa S1 và S2 → được cả hai trạm gán
        nodes[4] = {"coords": [[106.3, 10.25], [106.3, 10.3]],
                    "bbox": [106.3, 10.25, 106.3, 10.3]}
        assign = g4.assign_station_to_nodes(self.STATIONS, nodes, 40.0)
        weights = g4.node_weights({1: 100.0, 2: 200.0, 4: 400.0}, assign)
        w4 = weights[4]
        self.assertAlmostEqual(sum(w4.values()), 1.0)
        # A(S1) = uparea(1) + uparea(4) = 500 ; A(S2) = uparea(2) + uparea(4) = 600
        # raw(S1) = 400/500 = 0.8 ; raw(S2) = 400/600 = 0.6667 → chuẩn hoá
        self.assertAlmostEqual(w4["S1"], 0.8 / (0.8 + 400.0 / 600.0), places=9)
        self.assertAlmostEqual(w4["S2"], (400.0 / 600.0) / (0.8 + 400.0 / 600.0), places=9)

    def test_single_station_gets_full_weight(self):
        from features import rain_rules as g4
        assign = g4.assign_station_to_nodes(self.STATIONS, self.NODES, 40.0)
        weights = g4.node_weights({1: 100.0, 2: 200.0}, assign)
        self.assertAlmostEqual(weights[1]["S1"], 1.0)
        self.assertAlmostEqual(weights[2]["S2"], 1.0)

    def test_distance_point_line(self):
        from features import rain_rules as g4
        coords = [[106.0, 10.0], [106.1, 10.0]]
        # điểm ngay trên đường → ~0
        self.assertLess(g4.distance_point_line_m(106.05, 10.0, coords), 1.0)
        # điểm lệch 0,001 độ vĩ độ (~111 m) → khoảng 111 m
        d = g4.distance_point_line_m(106.05, 10.001, coords)
        self.assertTrue(100.0 < d < 120.0, d)

    def test_bbox_parsing(self):
        from features import rain_rules as g4
        self.assertEqual(g4.parse_bbox([1.0, 2.0, 3.0, 4.0]), [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(g4.parse_bbox(None), [])
        self.assertEqual(g4.parse_bbox("[1.0, 2.0, 3.0, 4.0]"), [1.0, 2.0, 3.0, 4.0])

    def test_parse_geometry_accepts_row(self):
        """Connector trả `geometry` lồng thành `Row` → phải parse được."""
        from features import rain_rules as g4

        class FakeRow(dict):
            """Giả lập `pyspark.sql.Row` (có `.asDict()`)."""

            def asDict(self, recursive=False):
                return dict(self)

        row = FakeRow({"type": "LineString", "coordinates": [[1.0, 2.0], [3.0, 4.0]]})
        self.assertEqual(g4.parse_geometry(row), [[1.0, 2.0], [3.0, 4.0]])
        self.assertEqual(g4.parse_bbox(FakeRow({"coordinates": [1.0, 2.0, 3.0, 4.0]})),
                         [1.0, 2.0, 3.0, 4.0])


class TestSpatialDistance(unittest.TestCase):
    """Q5 — khoảng cách giữa hai đoạn thẳng trên mặt cầu Trái Đất."""

    def test_identical_segments(self):
        a = (106.0, 10.0)
        b = (106.1, 10.0)
        self.assertLess(GEO.segment_segment_distance_m(a, b, a, b), 1.0)

    def test_parallel_offset(self):
        """Hai đoạn song song cách nhau 0,001 độ vĩ độ (~111 m)."""
        d = GEO.segment_segment_distance_m((106.0, 10.0), (106.1, 10.0),
                                          (106.0, 10.001), (106.1, 10.001))
        self.assertTrue(100.0 < d < 120.0, d)

    def test_crossing_segments(self):
        d = GEO.segment_segment_distance_m((106.0, 10.0), (106.1, 10.0),
                                          (106.05, 9.99), (106.05, 10.01))
        self.assertLess(d, 1.0)

    def test_bbox_gap(self):
        gap = GEO.bbox_gap_m([106.0, 10.0, 106.1, 10.0], [106.5, 10.5, 106.6, 10.5])
        self.assertGreater(gap, 40_000.0)
        self.assertEqual(GEO.bbox_gap_m([106.0, 10.0, 106.1, 10.0],
                                        [106.05, 9.99, 106.06, 10.01]), 0.0)

    def test_geometry_parsing(self):
        self.assertEqual(GEO.parse_geom({"type": "LineString", "coordinates": [[1, 2], [3, 4]]}),
                         [[1, 2], [3, 4]])
        self.assertEqual(GEO.parse_geom('{"type": "LineString", "coordinates": [[1, 2]]}'), [[1, 2]])
        self.assertEqual(GEO.parse_geom(None), [])

    def test_polyline_distance(self):
        """Khoảng cách đa tuyến = min trên các cặp đoạn con."""
        a = [[106.0, 10.0], [106.05, 10.0], [106.1, 10.0]]
        b = [[106.0, 10.002], [106.1, 10.002]]
        d = GEO.polyline_distance_m(a, b)
        self.assertTrue(200.0 < d < 240.0, d)


class TestProjectedGeometry(unittest.TestCase):
    """Q5 — hình học đã chiếu phẳng (bản tối ưu cho spatial join)."""
    LAT0 = math.radians(10.8)

    def _project(self, coords):
        return GEO.project_polyline(coords, self.LAT0)

    def test_project_scales_to_metres(self):
        """0,01 độ kinh độ ở vĩ độ 10,8° ≈ 1 047 m."""
        p = self._project([[106.0, 10.8], [106.01, 10.8]])
        dx = p[1][0] - p[0][0]
        self.assertTrue(1000.0 < dx < 1100.0, dx)

    def test_segment_boxes(self):
        p = self._project([[106.0, 10.0], [106.1, 10.0]])
        boxes = GEO.segment_boxes(p)
        self.assertEqual(len(boxes), 1)
        x1, y1, x2, y2 = boxes[0]
        self.assertLess(x1, x2)
        # đoạn nằm ngang → tung độ y không đổi
        self.assertAlmostEqual(y1, y2, places=9)

    def test_segment_boxes_diagonal(self):
        p = self._project([[106.0, 10.0], [106.1, 10.1]])
        x1, y1, x2, y2 = GEO.segment_boxes(p)[0]
        self.assertLess(x1, x2)
        self.assertLess(y1, y2)

    def test_distance_matches_unoptimised(self):
        """Bản có ngưỡng cắt phải cho CÙNG kết quả với bản đầy đủ."""
        a = [[106.0, 10.0], [106.05, 10.0], [106.1, 10.0]]
        b = [[106.0, 10.002], [106.1, 10.002]]
        pa, pb = self._project(a), self._project(b)
        full = GEO.projected_polyline_distance_m(pa, pb)
        cut = GEO.projected_polyline_distance_within(pa, pb,
                                                     GEO.segment_boxes(pa),
                                                     GEO.segment_boxes(pb), 500.0)
        self.assertAlmostEqual(full, cut, places=6)

    def test_cut_returns_above_limit_when_too_far(self):
        """Khi vượt ngưỡng, hàm trả giá trị > ngưỡng (không phải khoảng cách tuyệt đối)."""
        a = [[106.0, 10.0], [106.1, 10.0]]
        b = [[106.0, 10.05], [106.1, 10.05]]  # cách ~5,5 km
        pa, pb = self._project(a), self._project(b)
        cut = GEO.projected_polyline_distance_within(pa, pb,
                                                     GEO.segment_boxes(pa),
                                                     GEO.segment_boxes(pb), 250.0)
        self.assertGreater(cut, 250.0)

    def test_crossing_projected_segments(self):
        p1 = self._project([[106.0, 10.0], [106.1, 10.0]])
        p2 = self._project([[106.05, 9.99], [106.05, 10.01]])
        d = GEO.projected_segment_distance_m(p1[0], p1[1], p2[0], p2[1])
        self.assertLess(d, 1.0)


class TestBufferUnits(unittest.TestCase):
    """Q5 — ngưỡng khoảng cách tính bằng **mét** (chống lỗi nhân nhầm 1 000 lần).

    Lỗi đã xảy ra: `r_max * 1000.0` biến ngưỡng 250 m thành 250 000 m khiến mọi cặp
    ứng viên đều bị coi là khớp. Các test dưới đây khóa lại quy ước đơn vị.
    """

    # (osm_id, fclass, name, comid, order, distance_m, uparea)
    MATCHES = [
        ("A", "river", "Sông A", 100, 3, 40.0, 500.0),
        ("B", "stream", "Suối B", 200, 2, 90.0, 300.0),
        ("C", "stream", "Suối C", 300, 1, 249.9, 100.0),
        ("D", "canal", "Kênh D", 400, 1, 251.0, 50.0),
        ("E", "canal", "Kênh E", 500, 1, 6000.0, 20.0),
    ]

    def test_250m_buffer_excludes_far_pairs(self):
        rows = GEO.buffer_summary(self.MATCHES, [250.0], 5, 5)
        self.assertEqual(rows[0][1], 3, "chỉ 3 cặp ≤ 250 m")
        self.assertEqual(rows[0][6], round((40.0 + 90.0 + 249.9) / 3, 2))

    def test_50m_buffer(self):
        rows = GEO.buffer_summary(self.MATCHES, [50.0], 5, 5)
        self.assertEqual(rows[0][1], 1)
        self.assertEqual(rows[0][2], 1, "1 osm_id khớp")
        self.assertEqual(rows[0][3], 1, "1 comid khớp")

    def test_thresholds_are_monotonic(self):
        rows = GEO.buffer_summary(self.MATCHES, [50.0, 100.0, 250.0], 5, 5)
        counts = [r[1] for r in rows]
        self.assertEqual(counts, sorted(counts))
        self.assertEqual(counts, [1, 2, 3])

    def test_kilometre_buffers_are_excluded(self):
        """6 000 m > 250 m → không được tính vào ngưỡng 250 m."""
        rows = GEO.buffer_summary(self.MATCHES, [250.0], 5, 5)
        self.assertNotIn(6000.0, [row[6] for row in rows])


class TestSimplifyPolyline(unittest.TestCase):
    """Q5 — rút gọn Douglas–Peucker trước khi so sánh khoảng cách."""

    def test_straight_line_keeps_only_endpoints(self):
        line = [[106.0 + i * 0.001, 10.0] for i in range(20)]
        out = GEO.simplify_polyline(line, 0.0005)
        self.assertEqual(out, [line[0], line[-1]])

    def test_keeps_corner(self):
        """Góc vuôn phải được giữ lại."""
        line = [[106.0, 10.0], [106.05, 10.0], [106.05, 10.05]]
        out = GEO.simplify_polyline(line, 0.0005)
        self.assertIn([106.05, 10.0], out)

    def test_short_line_unchanged(self):
        line = [[106.0, 10.0], [106.01, 10.0]]
        self.assertEqual(GEO.simplify_polyline(line, 0.0005), line)

    def test_endpoints_preserved(self):
        line = [[106.0, 10.0], [106.001, 10.0001], [106.002, 10.0], [106.01, 10.01]]
        out = GEO.simplify_polyline(line, 0.0005)
        self.assertEqual(out[0], line[0])
        self.assertEqual(out[-1], line[-1])

    def test_error_within_tolerance(self):
        """Mọi điểm bị loại nằm trong sai số `tol` của đường gấp khúc còn lại."""
        import math as _math
        rng = [(106.0 + i * 0.0007, 10.0 + _math.sin(i) * 0.0004) for i in range(60)]
        tol = 0.0005
        out = GEO.simplify_polyline(rng, tol)
        self.assertLess(len(out), len(rng))
        # mỗi điểm gốc phải cách một đoạn nào đó của đường rút gọn không quá `tol`
        for p in rng:
            best = min(GEO.point_segment_distance_m(p, a, b)
                       for a, b in zip(out, out[1:]))
            self.assertLessEqual(best, tol + 1e-12)


class TestDecodeExtJson(unittest.TestCase):
    """Giải mã JSON mở rộng của MongoDB (`common/session.py`).

    Driver Java `toJson()` trả canonical extended JSON: BSON `int64` → `{"$numberLong": "…"}`.
    Nếu không giải mã thì COMID đọc từ MongoDB thành dict → so khớp khoá thất bại.
    """

    @classmethod
    def setUpClass(cls):
        # `common/session.py` import pyspark → nếu không có pyspark thì nạp trực tiếp
        # phần hàm thuần để test vẫn chạy được ngoài container.
        try:
            from common.session import decode_extjson, _parse_iso
        except ImportError:  # pragma: no cover - môi trường không có pyspark
            import ast
            import re as _re
            from datetime import datetime, timezone
            src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "common", "session.py"), encoding="utf-8").read()
            ns = {"re": _re, "datetime": datetime, "timezone": timezone, "Any": object}
            start = src.index("def _parse_iso")
            end = src.index("class JobLockError")
            exec(src[start:end], ns)
            decode_extjson = ns["decode_extjson"]
            _parse_iso = ns["_parse_iso"]
        cls.decode = staticmethod(decode_extjson)
        cls.parse_iso = staticmethod(_parse_iso)

    def test_number_long_thanh_int(self):
        """BSON int64 → int (không phải dict) — điều kiện để so khớp COMID."""
        out = self.decode({"$numberLong": "44042931"})
        self.assertIsInstance(out, int)
        self.assertEqual(out, 44042931)

    def test_number_int_va_double(self):
        self.assertEqual(self.decode({"$numberInt": "7"}), 7)
        self.assertAlmostEqual(self.decode({"$numberDouble": "1.5"}), 1.5)

    def test_oid_thanh_chuoi(self):
        self.assertEqual(self.decode({"$oid": "6b043798768c5f6ef38405a3"}),
                         "6b043798768c5f6ef38405a3")

    def test_date_thanh_datetime(self):
        from datetime import datetime, timezone
        out = self.decode({"$date": {"$numberLong": "1767225600000"}})
        self.assertIsInstance(out, datetime)
        self.assertEqual(out.tzinfo, timezone.utc)

    def test_date_iso_thanh_datetime(self):
        from datetime import datetime
        out = self.decode({"$date": "2026-11-23T13:30:46.21+00:00"})
        self.assertIsInstance(out, datetime)
        self.assertEqual(out.year, 2026)

    def test_parse_iso_nhieu_do_phan_thap_phan(self):
        """Python 3.10 không parse được phần thập phân 2 chữ số → phải chuẩn hoá."""
        from datetime import datetime
        for s in ("2026-11-23T13:30:46.21+00:00",
                  "2026-11-23T13:30:46.123456Z",
                  "2026-11-23T13:30:46.123Z",
                  "2026-11-23T13:30:46+00:00"):
            out = self.parse_iso(s)
            self.assertIsInstance(out, datetime)
            self.assertEqual(out.year, 2026)

    def test_dict_long_thuoc_ve(self):
        """Dict thường giữ nguyên cấu trúc, giá trị bên trong vẫn được giải mã."""
        out = self.decode({"src": {"$numberLong": "44042931"}, "dir": "down"})
        self.assertEqual(out["src"], 44042931)
        self.assertEqual(out["dir"], "down")

    def test_list_duoc_giai_ma(self):
        out = self.decode([{"$numberLong": "1"}, {"$numberLong": "2"}])
        self.assertEqual(out, [1, 2])

    def test_comid_thanh_chuoi_de_giu_dung_ten_nut_ao(self):
        """Nút ảo `SEA_VUNGTAU` là chuỗi → phải giữ nguyên chuỗi."""
        out = self.decode({"src": "SEA_VUNGTAU"})
        self.assertEqual(out["src"], "SEA_VUNGTAU")


if __name__ == "__main__":
    unittest.main(verbosity=2)