"""一次性改线预览：候选折线构造与「消除/新增/仍存在」风险比对（双精度）。

输入为**被替换的连续节点区间** ``[start_node_index, end_node_index]`` 与
接入两端的替代折点（内部拐点，整数毫米）。校验通过后候选折线为::

    nodes[0 : start]                      # 未改动前缀（逐节点完全相同）
    + [nodes[start]] + replacement_nodes  # 锚点 a → 替代折点 → 锚点 b
    + [nodes[end]]
    + nodes[end+1 : ]                     # 后缀（物理折线完全相同）

关键里程语义：

- **未改动前缀沿用原里程**：候选 cum 从 0 开始逐段累加，前缀节点坐标与
  段长与原线逐位相同，故候选 ``cum`` 前缀表与原线逐位相同（同一段长序列
  从 0.0 起做同样的 IEEE-754 加法，逐位一致，不依赖三位小数展示值）；
- **改线后缀按新路径长度重新累计**：锚点 b 的新里程
  ``= 前缀原里程 + 新路径 a→b 长度``，之后各段在其上继续累加，因此后缀
  事件里程与原线相差固定的里程平移 ``shift``。

事件同一性按**未舍入的 (原线段, 禁入圈输入序) 结构身份**判定，绝不使用
三位小数展示值，也不只凭坐标比较：

- 前缀：候选段 i ↔ 原线段 i（里程逐位相同）；
- 后缀：候选段 j ↔ 原线段 ``j - delta``（几何完全相同，里程相差 shift）；
- 区间内部：原线 ``(start, end)`` 段消失（→ 消除），候选替代段为新增
  （→ 新增），即使替代路径与某自交点同坐标也按不同事件处理。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .geometry import AnalysisResult, Point, analyze_path_result

# 单条「线段 × 禁入圈」闭交集事件的比对分类。
STATUS_REMAINING = "remaining"  # 仍存在：同一未舍入结构身份
STATUS_REMOVED = "removed"      # 消除：原线有、候选线无
STATUS_ADDED = "added"          # 新增：原线无、候选线有

# 禁入圈级汇总状态。
CIRCLE_STATUSES = (
    "remaining",   # 仅仍存在
    "removed",     # 仅消除
    "added",       # 仅新增
    "reduced",     # 有消除也有仍存在（无新增）
    "increased",   # 有新增也有仍存在（无消除）
    "replaced",    # 既有消除也有新增
)


@dataclass(frozen=True)
class RerouteSpec:
    """校验通过的改线规格。

    ``start``/``end`` 为保留锚点在原节点数组中的下标（start < end）；
    ``replacement_nodes`` 为两端锚点之间的内部折点（至少 1 个）。
    """

    start: int
    end: int
    replacement_nodes: Tuple[Point, ...]


@dataclass(frozen=True)
class RiskEvent:
    """按禁入圈归类的单个风险事件（来自精确 SegmentPiece，未舍入）。"""

    status: str                 # remaining / removed / added
    circle_index: int
    segment_index: int          # 该视角（原线或候选线）自身的线段下标
    entry_point: Point
    exit_point: Point
    start_mileage: float        # 该视角自身的累计里程
    end_mileage: float
    length: float


@dataclass(frozen=True)
class RemainingPair:
    """仍存在事件的原线 / 候选线双份信息（候选后缀里程带平移）。"""

    circle_index: int
    original_segment_index: int
    candidate_segment_index: int
    entry_point: Point
    exit_point: Point
    original_start_mileage: float
    original_end_mileage: float
    candidate_start_mileage: float
    candidate_end_mileage: float
    length: float
    mileage_shift: float


@dataclass(frozen=True)
class CircleRisks:
    """单个禁入圈的「消除/新增/仍存在」归类汇总（未舍入）。"""

    circle_index: int
    status: str
    removed: Tuple[RiskEvent, ...]
    added: Tuple[RiskEvent, ...]
    remaining: Tuple[RemainingPair, ...]


@dataclass(frozen=True)
class ReroutePreview:
    """改线预览的完整未舍入产物。"""

    spec: RerouteSpec
    candidate_nodes: Tuple[Point, ...]
    original: AnalysisResult
    candidate: AnalysisResult
    junction_start: Point              # 接入锚点 a（= 原/候选共有坐标）
    junction_end: Point                # 接入锚点 b
    prefix_length: float               # 原线锚点 a 里程（前缀逐位沿用）
    candidate_junction_end_mileage: float
    original_junction_end_mileage: float
    mileage_shift: float               # 后缀里程平移（候选 - 原线）
    circle_risks: Tuple[CircleRisks, ...]


def validate_reroute(
    node_count: int,
    start: int,
    end: int,
    replacement_nodes: Sequence[Point],
) -> Tuple[Optional[RerouteSpec], Optional[Tuple[str, str]]]:
    """校验端点衔接与折线有效性。

    返回 ``(spec, None)`` 或 ``(None, (field, message))``；
    ``field`` 形如 ``reroute.start_node_index``（reroute. 前缀由调用方保持）。
    校验项：

    - 下标为非负整数且 ``0 ≤ start < end ≤ node_count-1``（至少替换一条段；
      允许整段折线替换 start=0/end=末节点）；
    - 至少一个替代折点；
    - 端点衔接：替代首/末折点不得与锚点重合（禁止零长接入段）；
    - 替代折点之间相邻不重合；折线有效性与原规则一致（允许自交）。
    """
    n = node_count
    if not isinstance(start, int) or isinstance(start, bool) or start < 0:
        return None, ("start_node_index", "起始节点下标必须为非负整数")
    if not isinstance(end, int) or isinstance(end, bool) or end < 0:
        return None, ("end_node_index", "结束节点下标必须为非负整数")
    if start >= n:
        return None, ("start_node_index", f"起始节点下标超出节点范围（共 {n} 个节点）")
    if end >= n:
        return None, ("end_node_index", f"结束节点下标超出节点范围（共 {n} 个节点）")
    if start >= end:
        return None, (
            "end_node_index",
            "结束节点下标必须严格大于起始节点下标（至少替换一条连续线段）",
        )
    if len(replacement_nodes) == 0:
        return None, ("replacement_nodes", "替代折点至少需要一个")

    pts = [(float(p[0]), float(p[1])) for p in replacement_nodes]
    # 锚点 a → 第一个折点
    # 调用方（schemas）已保证 nodes 与折点坐标为整数毫米；这里仍按坐标比较。
    # 端点坐标在请求构造后取自同一整数输入，比较是精确的。
    return_spec = RerouteSpec(start=start, end=end, replacement_nodes=tuple(pts))
    return return_spec, None


def build_candidate_nodes(
    nodes: Sequence[Point],
    spec: RerouteSpec,
) -> List[Point]:
    """按规格拼接候选折线（端点衔接校验见 :func:`check_junctions`）。"""
    start, end = spec.start, spec.end
    return (
        list(nodes[: start + 1])
        + list(spec.replacement_nodes)
        + list(nodes[end:])
    )


def check_junctions(
    nodes: Sequence[Point],
    spec: RerouteSpec,
) -> Optional[Tuple[str, str]]:
    """端点衔接与新折线相邻重合检查（返回 (field, message) 或 None）。"""
    anchor_a = nodes[spec.start]
    anchor_b = nodes[spec.end]
    repl = spec.replacement_nodes
    if repl[0] == anchor_a:
        return (
            "replacement_nodes[0]",
            "首个替代折点与起始锚点重合，接入段为零长",
        )
    if repl[-1] == anchor_b:
        return (
            f"replacement_nodes[{len(repl) - 1}]",
            "末个替代折点与结束锚点重合，接入段为零长",
        )
    for i in range(len(repl) - 1):
        if repl[i] == repl[i + 1]:
            return (
                f"replacement_nodes[{i + 1}]",
                f"替代折点 #{i} 与 #{i + 1} 完全重合，禁止相邻重复节点",
            )
    return None


def _make_removed(piece) -> RiskEvent:
    return RiskEvent(
        status=STATUS_REMOVED,
        circle_index=piece.circle_index,
        segment_index=piece.segment_index,
        entry_point=piece.entry_point,
        exit_point=piece.exit_point,
        start_mileage=piece.start_mileage,
        end_mileage=piece.end_mileage,
        length=piece.length,
    )


def _make_added(piece) -> RiskEvent:
    return RiskEvent(
        status=STATUS_ADDED,
        circle_index=piece.circle_index,
        segment_index=piece.segment_index,
        entry_point=piece.entry_point,
        exit_point=piece.exit_point,
        start_mileage=piece.start_mileage,
        end_mileage=piece.end_mileage,
        length=piece.length,
    )


def build_preview(
    nodes: Sequence[Point],
    circles: Sequence[Tuple[Point, float]],
    cable_radius: float,
    spec: RerouteSpec,
) -> ReroutePreview:
    """用同一套标定后的输入与精确几何规则分别计算原线/候选线并归类风险。

    调用前必须已通过 :func:`validate_reroute` 与 :func:`check_junctions`。
    """
    start, end = spec.start, spec.end
    candidate_nodes = build_candidate_nodes(nodes, spec)

    # 原线与候选线：同一条 analyze_path_result 链路（粗筛/精确求交/
    # 跨拐点合并/复合段扫描规则完全一致）。
    original = analyze_path_result(nodes, circles, cable_radius)
    candidate = analyze_path_result(candidate_nodes, circles, cable_radius)

    # ---- 候选段 → 原线段的结构身份映射 ----
    n_repl = len(spec.replacement_nodes)
    # 候选折线：nodes[:start+1] + repl + nodes[end:]
    #   候选段 [0, start)        = 原线段 [0, start)（未改动前缀）；
    #   候选段 [start, start+n_repl]（含两条接入段）= 替代走向；
    #   候选段 start+n_repl+1 起 = 原线段 end 起（后缀，物理折线相同）。
    rep_hi = start + n_repl  # 最后一条替代段下标
    # 后缀：cand_seg - (start+n_repl+1) + end
    suffix_offset = end - (start + n_repl + 1)

    def mapped_original_segment(cand_seg: int) -> Optional[int]:
        if cand_seg < start:
            return cand_seg          # 前缀：段下标逐位相同
        if cand_seg > rep_hi:
            return cand_seg + suffix_offset  # 后缀：同一物理线段
        return None                  # 替代走向：新增

    # 以 (segment_index, circle_index) 为未舍入身份键建原线索引。
    orig_pieces: Dict[Tuple[int, int], object] = {
        (p.segment_index, p.circle_index): p for p in original.pieces
    }

    # 里程平移：后缀锚点 b 的新累计里程 - 原累计里程。
    prefix_length = original.cum[start]
    cand_b_mileage = candidate.cum[start + n_repl + 1]
    orig_b_mileage = original.cum[end]
    shift = cand_b_mileage - orig_b_mileage

    remaining_pairs: List[RemainingPair] = []
    removed_events: List[RiskEvent] = []
    added_events: List[RiskEvent] = []

    # ---- 候选视角：前缀/后缀的匹配（仍存在）与替代段的新增 ----
    for cp in candidate.pieces:
        mapped = mapped_original_segment(cp.segment_index)
        if mapped is None:
            added_events.append(_make_added(cp))
            continue
        op = orig_pieces.get((mapped, cp.circle_index))
        if op is None:
            # 映射到的原线段对该圈无侵入：风险是候选线上新出现的。
            added_events.append(_make_added(cp))
            continue
        # 结构身份相同（前缀几何逐位相同；后缀为同一物理折线）。
        # 未舍入校验：片段坐标来自同一批节点坐标上的同一套求交运算，逐位相等；
        # 前缀段里程逐位相等；后缀锚点里程为“前缀逐位里程 + 新路径长”，参数
        # 里程由各自 cum 表重算，内部点可能相差 1 ULP（大数累加），故这里只
        # 要求差为 1e-10 尺度内的同一固定平移——归类本身用的是精确结构身份，
        # 不读三位小数展示值。
        assert op.entry_point == cp.entry_point and op.exit_point == cp.exit_point
        if cp.segment_index < start:
            assert (
                op.start_mileage == cp.start_mileage
                and op.end_mileage == cp.end_mileage
            )
        else:
            scale = max(
                1.0,
                abs(shift),
                abs(cp.start_mileage),
                abs(op.start_mileage),
            )
            ulp_tol = 1e-10 * scale
            assert abs(cp.start_mileage - op.start_mileage - shift) <= ulp_tol
            assert abs(cp.end_mileage - op.end_mileage - shift) <= ulp_tol
        remaining_pairs.append(
            RemainingPair(
                circle_index=cp.circle_index,
                original_segment_index=mapped,
                candidate_segment_index=cp.segment_index,
                entry_point=op.entry_point,
                exit_point=op.exit_point,
                original_start_mileage=op.start_mileage,
                original_end_mileage=op.end_mileage,
                candidate_start_mileage=cp.start_mileage,
                candidate_end_mileage=cp.end_mileage,
                length=op.length,
                mileage_shift=cp.start_mileage - op.start_mileage,
            )
        )

    # ---- 原线视角：被删除段（消除）+ 前缀/后缀中候选不再命中的（消除）----
    matched_orig_keys = {(rp.original_segment_index, rp.circle_index)
                         for rp in remaining_pairs}
    for op in original.pieces:
        key = (op.segment_index, op.circle_index)
        if key in matched_orig_keys:
            continue
        if start <= op.segment_index < end:
            # 旧走向内部段（含原线段 start 自 anchor_a 的旧出发段、
            # 到 end-1 抵达 anchor_b 旧方向）：整段被移除。
            removed_events.append(_make_removed(op))
            continue
        # 前缀（seg < start）或后缀（seg >= end）：物理折线在候选线上逐段
        # 保留，同一套精确几何必然产生同一片段（候选视角已匹配为 remaining）；
        # 到这里说明段编号映射有误，直接失败而不是默默归为“消除”。
        raise AssertionError(
            f"保留段 {op.segment_index} × 圈 {op.circle_index} 未在候选线匹配"
        )

    # ---- 按禁入圈归类 ----
    circles_by_index: Dict[int, dict] = {}

    def bucket(ci: int) -> dict:
        return circles_by_index.setdefault(
            ci, {"removed": [], "added": [], "remaining": []}
        )

    for ev in removed_events:
        bucket(ev.circle_index)["removed"].append(ev)
    for ev in added_events:
        bucket(ev.circle_index)["added"].append(ev)
    for rp in remaining_pairs:
        bucket(rp.circle_index)["remaining"].append(rp)

    circle_risks: List[CircleRisks] = []
    for ci in sorted(circles_by_index):
        b = circles_by_index[ci]
        rem = tuple(sorted(b["removed"], key=lambda e: (e.segment_index, e.start_mileage)))
        add = tuple(sorted(b["added"], key=lambda e: (e.segment_index, e.start_mileage)))
        stay = tuple(sorted(
            b["remaining"],
            key=lambda r: (r.original_segment_index, r.original_start_mileage),
        ))
        has_removed = len(rem) > 0
        has_added = len(add) > 0
        has_remaining = len(stay) > 0
        if has_removed and has_added:
            status = "replaced"
        elif has_removed and has_remaining:
            status = "reduced"
        elif has_added and has_remaining:
            status = "increased"
        elif has_removed:
            status = STATUS_REMOVED
        elif has_added:
            status = STATUS_ADDED
        else:
            status = STATUS_REMAINING
        circle_risks.append(
            CircleRisks(
                circle_index=ci,
                status=status,
                removed=rem,
                added=add,
                remaining=stay,
            )
        )

    return ReroutePreview(
        spec=spec,
        candidate_nodes=tuple(candidate_nodes),
        original=original,
        candidate=candidate,
        junction_start=nodes[start],
        junction_end=nodes[end],
        prefix_length=prefix_length,
        candidate_junction_end_mileage=cand_b_mileage,
        original_junction_end_mileage=orig_b_mileage,
        mileage_shift=shift,
        circle_risks=tuple(circle_risks),
    )
