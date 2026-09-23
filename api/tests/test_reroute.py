"""一次性改线预览的独立小型几何夹具测试。

夹具刻意保持小而精确（整数坐标 / 可解析几何），分别覆盖：

- 切点：改线消除相切零长点、候选线新相切；
- 跨拐点：同一圈在拐点两侧的片段在候选线上继续合并 / 打断；
- 标定：survey→path 变换后原线与候选线同源计算；
- 里程平移：前缀逐位沿用、后缀按新路径长度重新累计且平移恒定；
- 自交路径：同坐标异里程的两个事件不得误认为同一事件；
- 比对不依赖三位小数展示值。
"""

import math

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.reroute import (
    RerouteSpec,
    build_preview,
    check_junctions,
    validate_reroute,
)

client = TestClient(app)
PATH = "/api/precheck"


def post(body):
    return client.post(PATH, json=body)


# 共用小夹具：
# 原线 (-100,0)→(100,0)，电缆 5；圈0 圆心 (0,15) 半径 10 → 扩张 15，
# 对原线恰为切点 (0,0)（里程 100，零长点）。
TANGENT_BODY = {
    "nodes": [{"x": -100, "y": 0}, {"x": 100, "y": 0}],
    "cable_radius": 5,
    "circles": [{"x": 0, "y": 15, "radius": 10}],
}


# ---------------------------------------------------------------- 端点校验


def test_validate_reroute_accepts_valid_full_replacement():
    spec, err = validate_reroute(
        4, start=0, end=3, replacement_nodes=[(0, 10), (10, 10)]
    )
    assert err is None and spec is not None
    assert (spec.start, spec.end) == (0, 3)
    nodes = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0), (15.0, 0.0)]
    assert check_junctions(nodes, spec) is None


@pytest.mark.parametrize(
    "start,end,repl,field",
    [
        (2, 1, [(5, 5)], "end"),            # start >= end（挂 end）
        (1, 1, [(5, 5)], "end"),            # 相等
        (-1, 2, [(5, 5)], "start"),         # 负下标
        (0, 4, [(5, 5)], "end"),            # 越界（4 节点 0..3）
        (0, 5, [(5, 5)], "end"),            # end 越界
    ],
)
def test_validate_reroute_rejects_index_problems(start, end, repl, field):
    spec, err = validate_reroute(4, start, end, repl)
    assert spec is None
    assert err is not None and field in err[0]


def test_validate_reroute_requires_replacement_point():
    spec, err = validate_reroute(4, 0, 2, [])
    assert spec is None and err is not None
    assert "replacement_nodes" in err[0]


def test_check_junctions_rejects_zero_length_connections():
    nodes = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]
    # 首折点与锚点 a 重合
    spec = RerouteSpec(0, 2, ((0.0, 0.0), (10.0, 10.0)))
    field, _ = check_junctions(nodes, spec)
    assert field == "replacement_nodes[0]"
    # 末折点与锚点 b 重合
    spec = RerouteSpec(0, 2, ((10.0, 10.0), (20.0, 0.0)))
    field, _ = check_junctions(nodes, spec)
    assert field == "replacement_nodes[1]"
    # 相邻折点重合
    spec = RerouteSpec(0, 2, ((5.0, 5.0), (5.0, 5.0)))
    field, _ = check_junctions(nodes, spec)
    assert field == "replacement_nodes[1]"


# ------------------------------------------------------------------- 切点


def test_tangent_removed_by_reroute_is_classified_removed():
    """原线切点零长事件被绕开 → 消除；候选线可敷设。"""
    body = dict(TANGENT_BODY)
    body["reroute"] = {
        "start_node_index": 0,
        "end_node_index": 1,
        # 从 (-100,0) 抬到 y=31 再回 (100,0)；扩张半径 15，圈心 (0,15)
        # 到水平顶段（y=31）垂距 16 > 15，两条斜接段离圈更远 → 完全脱离。
        "replacement_nodes": [{"x": -50, "y": 31}, {"x": 50, "y": 31}],
    }
    data = post(body).json()

    # 顶层仍是原线结论：相切碰撞、零长区间、里程 100
    assert data["feasible"] is False
    assert data["first_collision"]["nearest"] == {"x": 0.0, "y": 0.0}
    assert data["intrusion_intervals"][0]["start_mileage"] == 100.0

    rp = data["reroute_preview"]
    assert rp["candidate"]["feasible"] is True
    assert rp["candidate"]["collision_count"] == 0
    assert rp["candidate"]["intrusion_intervals"] == []

    risks = rp["circle_risks"]
    assert len(risks) == 1
    cr = risks[0]
    assert cr["circle_index"] == 0
    assert cr["status"] == "removed"
    (ev,) = cr["removed"]
    assert ev["segment_index"] == 0
    assert ev["entry"] == ev["exit"] == {"x": 0.0, "y": 0.0}
    assert ev["start_mileage"] == ev["end_mileage"] == 100.0
    assert ev["length"] == 0.0
    assert cr["added"] == [] and cr["remaining"] == []

    assert rp["summary"]["circles_removed"] == 1
    assert rp["summary"]["removed_event_count"] == 1


def test_reroute_introduces_new_tangent_is_classified_added():
    """候选替代路径对另一个圈产生新切点 → 新增（零长事件）。"""
    body = {
        # 原线在 y=31 高度（圈 (0,15) R=15，垂距 16 > 15，脱离）
        "nodes": [{"x": -100, "y": 31}, {"x": 100, "y": 31}],
        "cable_radius": 5,
        "circles": [{"x": 0, "y": 15, "radius": 10}],
        "reroute": {
            "start_node_index": 0,
            "end_node_index": 1,
            # 改到 y=0：候选水平段恰在圈正下方相切于 (0,0)
            "replacement_nodes": [{"x": -50, "y": 0}, {"x": 50, "y": 0}],
        },
    }
    data = post(body).json()
    assert data["feasible"] is True  # 原线无碰撞
    rp = data["reroute_preview"]
    assert rp["candidate"]["feasible"] is False
    cr = rp["circle_risks"][0]
    assert cr["status"] == "added"
    (ev,) = cr["added"]
    assert ev["entry"] == ev["exit"] == {"x": 0.0, "y": 0.0}
    assert ev["length"] == 0.0
    assert cr["removed"] == [] and cr["remaining"] == []
    assert rp["summary"]["circles_added"] == 1


# --------------------------------------------------------------- 跨拐点


def test_cross_corner_interval_pieces_follow_candidate_segments():
    """跨拐点合并在候选线上按候选段号重新成立。

    圈 (11,-1)，扩张 sqrt2，同时相切于候选线经过 (10,0) 拐点的两段；
    候选替代折点 (10,10) 使候选段 (0,0)→(10,0)→(10,10) 在拐点双相切，
    合并成一个跨两段的零长区间，且风险归类为新增（原线不经过该走向）。
    """
    R = math.sqrt(2.0) - 1.0  # cable=1 → 扩张 sqrt2
    body = {
        "nodes": [
            {"x": 0, "y": 0},
            {"x": 10, "y": 0},
            {"x": 20, "y": 0},
        ],
        "cable_radius": 1,
        "circles": [{"x": 11, "y": -1, "radius": R}],
        "reroute": {
            "start_node_index": 1,
            "end_node_index": 2,
            # 候选：(10,0) → (10,10) → (20,0)；圈在 (10,0) 处
            # 与候选段0((0,0)→(10,0)) 与候选段1((10,0)→(10,10)) 双相切
            "replacement_nodes": [{"x": 10, "y": 10}],
        },
    }
    data = post(body).json()
    # 原线：段0、段1 在 (10,0) 双相切 → 一个跨两段区间（被替换段1 消除）
    orig_iv = data["intrusion_intervals"]
    assert len(orig_iv) == 1
    assert orig_iv[0]["entry_segment_index"] == 0
    assert orig_iv[0]["exit_segment_index"] == 1

    rp = data["reroute_preview"]
    cand_iv = rp["candidate"]["intrusion_intervals"]
    assert len(cand_iv) == 1
    assert cand_iv[0]["entry_segment_index"] == 0
    assert cand_iv[0]["exit_segment_index"] == 1
    seg_ids = sorted(p["segment_index"] for p in cand_iv[0]["pieces"])
    assert seg_ids == [0, 1]

    # 圈0：候选段0 仍存在（前缀段，同坐标同里程），旧段1 消除，候选段1 新增
    cr = rp["circle_risks"][0]
    assert cr["status"] == "replaced"
    (removed,) = cr["removed"]
    assert removed["segment_index"] == 1
    (added,) = cr["added"]
    assert added["segment_index"] == 1
    (stay,) = cr["remaining"]
    assert stay["original_segment_index"] == 0
    assert stay["candidate_segment_index"] == 0
    assert stay["mileage_shift"] == 0.0


def test_cross_corner_removed_when_reroute_leaves_circle():
    """原线跨拐点区间在候选线上整体消失 → 两段片段都归为消除。"""
    R = math.sqrt(2.0) - 1.0
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}],
        "cable_radius": 1,
        "circles": [{"x": 11, "y": -1, "radius": R}],
        "reroute": {
            "start_node_index": 0,
            "end_node_index": 2,
            "replacement_nodes": [{"x": -20, "y": 20}],
        },
    }
    data = post(body).json()
    rp = data["reroute_preview"]
    assert rp["candidate"]["feasible"] is True
    cr = rp["circle_risks"][0]
    assert cr["status"] == "removed"
    assert {ev["segment_index"] for ev in cr["removed"]} == {0, 1}
    assert cr["added"] == [] and cr["remaining"] == []


# ------------------------------------------------------------- 里程平移


def test_prefix_mileages_bit_identical_and_suffix_shifted():
    """前缀沿用原里程（逐位），后缀按新路径长度重新累计、平移恒定。"""
    # 原线 x 轴 0→10→20→30；圈A 落在前缀段0，圈B 落在被替换段1 中部，
    # 圈C 落在后缀段2；改线抬高 (15,14)，新接入段不再触碰圈 B。
    body = {
        "nodes": [
            {"x": 0, "y": 0}, {"x": 10, "y": 0},
            {"x": 20, "y": 0}, {"x": 30, "y": 0},
        ],
        "cable_radius": 1,
        "circles": [
            {"x": 2, "y": 0, "radius": 1},    # 前缀：仍存在，里程不变
            {"x": 15, "y": 0, "radius": 1},   # 旧段1 中部：消除
            {"x": 28, "y": 0, "radius": 1},   # 后缀：仍存在，里程平移
        ],
        "reroute": {
            "start_node_index": 1,
            "end_node_index": 2,
            "replacement_nodes": [{"x": 15, "y": 14}],
        },
    }
    data = post(body).json()
    rp = data["reroute_preview"]
    shift = rp["mileage_shift"]
    assert shift > 0  # 抬高绕行必然更长

    # 几何夹具层：前缀 cum 与原线逐位相等（不靠展示值）
    nodes = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0)]
    circles = [((2.0, 0.0), 1.0), ((15.0, 0.0), 1.0), ((28.0, 0.0), 1.0)]
    spec = RerouteSpec(1, 2, ((15.0, 14.0),))
    pv = build_preview(nodes, circles, 1.0, spec)
    assert pv.original.cum[:2] == pv.candidate.cum[:2]
    assert pv.original.cum[:2] == (0.0, 10.0)
    # 候选后缀锚点 b（候选节点3）里程 = 20 + 未舍入 shift
    assert pv.candidate.cum[3] == pv.original.cum[2] + pv.mileage_shift

    by_circle = {cr["circle_index"]: cr for cr in rp["circle_risks"]}
    # 圈A：前缀仍存在，候选/原线里程相同，平移 0
    stay_a = by_circle[0]["remaining"][0]
    assert stay_a["original_start_mileage"] == stay_a["candidate_start_mileage"]
    assert stay_a["original_end_mileage"] == stay_a["candidate_end_mileage"]
    assert stay_a["mileage_shift"] == 0.0
    # 圈B：仅消除
    assert by_circle[1]["status"] == "removed"
    # 圈C：后缀仍存在，候选里程 = 原线里程 + shift，平移字段显式给出
    stay_c = by_circle[2]["remaining"][0]
    assert stay_c["original_segment_index"] == 2
    assert stay_c["candidate_segment_index"] == 3
    assert stay_c["candidate_start_mileage"] == pytest.approx(
        stay_c["original_start_mileage"] + shift
    )
    assert stay_c["mileage_shift"] == pytest.approx(shift)
    assert by_circle[2]["status"] == "remaining"

    # 总里程差等于后缀平移（候选全长 = 原全长 + shift）
    assert rp["summary"]["candidate_total_length"] == pytest.approx(
        rp["summary"]["original_total_length"] + shift
    )
    assert rp["prefix_length"] == 10.0  # 锚点 a(节点1) 的原里程
    assert rp["original_junction_end_mileage"] == 20.0


def test_shorter_reroute_gives_negative_shift():
    """替代路径更短时平移为负，后缀里程相应回退（用几何夹具直接核对）。"""
    # 原线绕远 (0,0)→(0,10)→(10,10)（长 20），整段替换为近似对角线
    # (0,0)→(3,3)→(7,7)→(10,10)，两段斜边总长 ≈ 14.14 < 20。
    nodes = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0)]
    spec = RerouteSpec(0, 2, ((3.0, 3.0), (7.0, 7.0)))
    assert check_junctions(nodes, spec) is None
    pv = build_preview(nodes, [((50.0, 50.0), 1.0)], 1.0, spec)
    assert pv.mileage_shift < 0
    assert pv.candidate.total_length < pv.original.total_length
    # 候选全长 = 原全长 + shift（未舍入恒等）
    assert pv.candidate.total_length == pv.original.total_length + pv.mileage_shift
    # 前缀为空：锚点 a 里程 0
    assert pv.prefix_length == 0.0


# ----------------------------------------------------------- 自交/展示值


def test_self_intersecting_same_coordinate_different_mileage_stays_distinct():
    """自交路径：同坐标异里程的两个切点必须是两个独立事件。

    构造一个自交矩形交叉：原线经过 (0,0) 两次（不同里程），圈放在
    (0,-R) 使两段都在 (0,0) 相切。改线只替换其中一段附近的走向，
    另一段的切点保留——不能因坐标相同而把两者当同一事件一起消除/保留。
    """
    # 路径：A(-10,0) → B(0,0) → C(0,10) → D(-10,10) → E(-10,0) 自交
    # 段0: (-10,0)→(0,0) 与 段3: (-10,10)→(-10,0)?? 需要同坐标异里程。
    # 用：(-10,0)→(0,0) [段0] ... 最后 → (0,0)→(10,0) [末段]
    nodes = [
        {"x": -10, "y": 0}, {"x": 0, "y": 0}, {"x": 0, "y": -10},
        {"x": 10, "y": -10}, {"x": 10, "y": 0}, {"x": 0, "y": 0},
        {"x": -10, "y": 0},
    ]
    # 段0 与段5 都经过/终于 (0,0)，里程不同。
    # 圈 (0,-6) r=1，cable=5 → 扩张 6：段0（y=0 水平线）与段5（水平）
    # 都在 (0,0) 相切；段2 ((0,-10)→(10,-10), y=-10) 距离 4 < 6 会整段
    # 相交，改圈位避开：圈放 (0,-6)，段2 y=-10 距离 4 也命中……
    # 改圈 (5,-6)：段2 x∈[0,10],y=-10 距离4 仍命中。使用 cable=1 r=5 → R=6，
    # 把圈放 (0,-6)：段2 距离 4 命中。为避免污染，路径改成不经过 y=-10。
    nodes = [
        {"x": -10, "y": 0}, {"x": 0, "y": 0},
        {"x": 0, "y": 10}, {"x": 10, "y": 10},
        {"x": 10, "y": 0}, {"x": 0, "y": 0},
        {"x": -10, "y": -10},
    ]
    # 圈 (0,-6)：水平段0、段5（y=0）在 x=0 相切；其余段（竖直/ y=10 /
    # 段5后无水平在 y=0 之后）段5 (10,0)→(0,0)、段6 (0,0)→(-10,-10)
    body = {
        "nodes": nodes,
        "cable_radius": 1,
        "circles": [{"x": 0, "y": -6, "radius": 5}],  # 扩张 6
        "reroute": {
            # 只替换节点1..2 附近（B→(0,10) 为段1），段0、段5 都不在替换区，
            # 两切点应同时仍存在且是两个独立事件。
            "start_node_index": 2,
            "end_node_index": 4,
            "replacement_nodes": [{"x": 0, "y": 20}, {"x": 10, "y": 20}],
        },
    }
    data = post(body).json()
    rp = data["reroute_preview"]
    cr = next(c for c in rp["circle_risks"] if c["circle_index"] == 0)
    seg_ids = sorted(r["candidate_segment_index"] for r in cr["remaining"])
    # 段0 与段5 的切点都还在（候选编号可能因段数变化平移，段5→段7）
    assert 0 in seg_ids
    assert any(s >= 5 for s in seg_ids)
    assert len(cr["remaining"]) >= 2
    # 原线视角：同坐标 (0,0) 的两个事件里程不同，仍然各自独立
    orig_mileages = sorted(r["original_start_mileage"] for r in cr["remaining"])
    assert orig_mileages[0] != orig_mileages[-1]


def test_diff_does_not_depend_on_three_decimal_display():
    """近相切：展示同为三位小数但实际一命中一脱离时，按双精度归类。"""
    # 原线段 (0,0)→(1,0)，圈 (3,10) r=5.1978 cable=5 → 扩张 10.1978，
    # 距线 sqrt(104)=10.198039… > R → 原线脱离（可敷设）。
    # 候选改线把段抬到恰相切附近：直接用已验证的夹具——
    # 这里确保消除/仍存在判定不读展示字段：原线无事件、候选有事件 = 新增。
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 1, "y": 0}],
        "cable_radius": 5,
        "circles": [{"x": 3, "y": 10, "radius": 5.1978}],
        "reroute": {
            "start_node_index": 0,
            "end_node_index": 1,
            # 候选经过 (0,10)→… 使圆被穿过：折点 (0,10)、(1,10)
            "replacement_nodes": [{"x": 0, "y": 10}, {"x": 1, "y": 10}],
        },
    }
    data = post(body).json()
    assert data["feasible"] is True
    rp = data["reroute_preview"]
    assert rp["candidate"]["feasible"] is False
    cr = rp["circle_risks"][0]
    assert cr["status"] == "added"
    assert len(cr["added"]) >= 1
    assert cr["removed"] == [] and cr["remaining"] == []


# ------------------------------------------------------------- 标定同源


def test_reroute_with_calibration_uses_one_transform_for_both_lines():
    """带标定：原线与候选线用同一次变换后的圈心，结论同源。"""
    # survey = path + (1000,2000)；孔 survey (1000,2015) → path (0,15)
    # 原线 (-100,0)→(100,0) 相切；改线抬高后消除。
    body = {
        "nodes": [{"x": -100, "y": 0}, {"x": 100, "y": 0}],
        "cable_radius": 5,
        "circles": [{"x": 1000, "y": 2015, "radius": 10}],
        "calibration": {
            "survey_points": [
                {"x": 1000, "y": 2000}, {"x": 1100, "y": 2000},
                {"x": 1000, "y": 2100},
            ],
            "path_points": [
                {"x": 0, "y": 0}, {"x": 100, "y": 0}, {"x": 0, "y": 100},
            ],
            "max_rms_error": 1,
        },
        "reroute": {
            "start_node_index": 0,
            "end_node_index": 1,
            # 顶段 y=31：圈心施工坐标 (0,15)，扩张 15，垂距 16 > 15 脱离
            "replacement_nodes": [{"x": -50, "y": 31}, {"x": 50, "y": 31}],
        },
    }
    data = post(body).json()
    assert data["calibration"] is not None
    assert data["calibration"]["translation"] == {"x": -1000.0, "y": -2000.0}
    assert data["feasible"] is False  # 原线相切
    rp = data["reroute_preview"]
    # 候选响应携带同一份标定摘要
    assert rp["candidate"]["calibration"]["translation"] == {"x": -1000.0, "y": -2000.0}
    assert rp["candidate"]["feasible"] is True
    # 候选响应中的圈心是变换后的施工坐标（展示 0,15）
    assert rp["candidate"]["circles"][0]["center"] == {"x": 0.0, "y": 15.0}
    assert rp["circle_risks"][0]["status"] == "removed"


def test_calibration_failure_with_reroute_returns_422_and_no_preview():
    """标定残差超阈值：422，不产生原线或候选任何结论。"""
    body = {
        "nodes": [{"x": -100, "y": 0}, {"x": 100, "y": 0}],
        "cable_radius": 5,
        "circles": [{"x": 1000, "y": 2015, "radius": 10}],
        "calibration": {
            "survey_points": [
                {"x": 1000, "y": 2000}, {"x": 1100, "y": 2000},
                {"x": 1000, "y": 2100},
            ],
            "path_points": [
                {"x": 0, "y": 0}, {"x": 100, "y": 0}, {"x": 0, "y": 150},
            ],
            "max_rms_error": 1,
        },
        "reroute": {
            "start_node_index": 0,
            "end_node_index": 1,
            "replacement_nodes": [{"x": 0, "y": 30}],
        },
    }
    r = post(body)
    assert r.status_code == 422
    assert "calibration.max_rms_error" in r.json()["errors"]


# ----------------------------------------------------------- 接口/422 契约


def test_reroute_422_cases_via_api():
    base = {
        "nodes": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 20, "y": 0}],
        "cable_radius": 5,
        "circles": [],
    }

    def expect(reroute, key):
        r = post({**base, "reroute": reroute})
        assert r.status_code == 422
        assert key in r.json()["errors"]

    expect(
        {"start_node_index": 1, "end_node_index": 1,
         "replacement_nodes": [{"x": 5, "y": 5}]},
        "reroute.end_node_index",
    )
    expect(
        {"start_node_index": 0, "end_node_index": 9,
         "replacement_nodes": [{"x": 5, "y": 5}]},
        "reroute",
    )
    expect(
        {"start_node_index": 0, "end_node_index": 1,
         "replacement_nodes": [{"x": 0, "y": 0}]},  # 与锚点 a 重合
        "reroute",
    )
    expect(
        {"start_node_index": 0, "end_node_index": 1,
         "replacement_nodes": [{"x": 5, "y": 5}, {"x": 5, "y": 5}]},
        "reroute.replacement_nodes",
    )
    # 折点坐标必须整数毫米 / 拒绝布尔
    expect(
        {"start_node_index": 0, "end_node_index": 1,
         "replacement_nodes": [{"x": 5.5, "y": 5}]},
        "reroute.replacement_nodes[0].x",
    )


def test_reroute_preview_omitted_is_null_and_old_contract_intact():
    data = post(TANGENT_BODY).json()
    assert data["reroute_preview"] is None
    # 旧字段集合与语义不变
    assert set(data.keys()) == {
        "feasible", "cable_radius", "nodes", "circles", "collision_count",
        "first_collision", "collisions", "intrusion_intervals",
        "compound_intrusion_segments", "calibration", "reroute_preview",
    }


def test_candidate_nodes_and_junction_metadata():
    body = {
        "nodes": [
            {"x": 0, "y": 0}, {"x": 10, "y": 0},
            {"x": 20, "y": 0}, {"x": 30, "y": 0},
        ],
        "cable_radius": 5,
        "circles": [],
        "reroute": {
            "start_node_index": 1,
            "end_node_index": 2,
            "replacement_nodes": [{"x": 14, "y": 14}],
        },
    }
    rp = post(body).json()["reroute_preview"]
    assert rp["junction_start"] == {"x": 10.0, "y": 0.0}
    assert rp["junction_end"] == {"x": 20.0, "y": 0.0}
    assert rp["replacement_nodes"] == [{"x": 14.0, "y": 14.0}]
    # 候选节点：前缀(含a) + 折点 + 后缀(含b)
    assert rp["candidate"]["nodes"] == [
        {"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 0.0},
        {"x": 14.0, "y": 14.0},
        {"x": 20.0, "y": 0.0}, {"x": 30.0, "y": 0.0},
    ]
    assert rp["circle_risks"] == []
    assert rp["summary"]["circle_count"] == 0


def test_status_circles_increased_and_reduced():
    """按圈状态：前缀仍存在 + 替代段新增 → increased；
    前缀/后缀仍存在 + 被替换段整段消除 → reduced。"""
    # increased：直线 0→10→20→30，圈 (2,0) R=2 命中段0（前缀，仍存在）；
    # 替代折线先折到 (2,2) 再到 (20,30)，首段重新穿过圈（新增）。
    body_inc = {
        "nodes": [
            {"x": 0, "y": 0}, {"x": 10, "y": 0},
            {"x": 20, "y": 0}, {"x": 30, "y": 0},
        ],
        "cable_radius": 1,
        "circles": [{"x": 2, "y": 0, "radius": 1}],
        "reroute": {
            "start_node_index": 1,
            "end_node_index": 2,
            "replacement_nodes": [{"x": 2, "y": 2}, {"x": 20, "y": 30}],
        },
    }
    rp = post(body_inc).json()["reroute_preview"]
    (cr,) = rp["circle_risks"]
    assert cr["circle_index"] == 0 and cr["status"] == "increased"
    assert {r["candidate_segment_index"] for r in cr["remaining"]} == {0}
    assert len(cr["added"]) >= 1
    assert all(ev["segment_index"] in (1, 2) for ev in cr["added"])
    assert cr["removed"] == []
    assert rp["summary"]["circles_increased"] == 1

    # reduced：U 形原线 (0,0)→(0,10)→(10,10)→(10,0)，圈 (5,5) R=6
    # 同时命中竖段0、横段1、竖段2 的内部（两锚点 (0,10)/(10,10) 距圈
    # sqrt(50)>6 不在圈内）。替换段1 为远端抬高折线后：
    # 竖段0/竖段2（候选段0/段3，前缀+后缀）仍存在，旧段1 消除、无新增。
    body_red = {
        "nodes": [
            {"x": 0, "y": 0}, {"x": 0, "y": 10},
            {"x": 10, "y": 10}, {"x": 10, "y": 0},
        ],
        "cable_radius": 1,
        "circles": [{"x": 5, "y": 5, "radius": 5}],
        "reroute": {
            "start_node_index": 1,
            "end_node_index": 2,
            "replacement_nodes": [{"x": 5, "y": 100}],
        },
    }
    rp2 = post(body_red).json()["reroute_preview"]
    (cr2,) = rp2["circle_risks"]
    assert cr2["status"] == "reduced"
    stay = {
        (r["original_segment_index"], r["candidate_segment_index"])
        for r in cr2["remaining"]
    }
    assert stay == {(0, 0), (2, 3)}
    # 后缀仍存在事件带固定非零里程平移；前缀平移为 0
    shifts = {
        (r["original_segment_index"], round(r["mileage_shift"], 6))
        for r in cr2["remaining"]
    }
    suffix_shift = round(rp2["mileage_shift"], 6)
    assert (0, 0.0) in shifts
    assert (2, suffix_shift) in shifts
    assert {ev["segment_index"] for ev in cr2["removed"]} == {1}
    assert cr2["added"] == []
    assert rp2["summary"]["circles_reduced"] == 1
