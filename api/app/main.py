"""FastAPI 应用：隧道电缆绕孔预检（可选全站仪 → 施工坐标现场标定、一次性改线预览）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .calibration import fit_rigid_transform
from .geometry import AnalysisResult, analyze_path_result
from .reroute import (
    RerouteSpec,
    build_preview,
    check_junctions,
    validate_reroute,
)
from .schemas import (
    CalibrationOut,
    CircleOut,
    CircleRisksOut,
    CollisionOut,
    CompoundIntrusionSegmentOut,
    CompoundPieceOut,
    IntervalPieceOut,
    IntrusionIntervalOut,
    PointOut,
    PrecheckRequest,
    PrecheckResponse,
    RerouteEventOut,
    ReroutePreviewOut,
    RerouteRemainingOut,
    RerouteSummaryOut,
)

app = FastAPI(title="隧道电缆绕孔预检器", version="1.1.0")

# 前端通过 Vite dev server / 浏览器直连时需要 CORS；
# 容器内 nginx 同源代理 /api 也不受影响。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def round3(v: float) -> float:
    """四舍五入至三位小数（仅用于展示输出，计算内部始终双精度）。"""
    return round(float(v) + 0.0, 3)


def _loc_to_field(loc: tuple) -> str:
    """把 Pydantic 的错误定位转成前端可索引的字段键。

    例：('body', 'nodes', 0, 'x') -> 'nodes[0].x'

    嵌套模型校验器产生的定位可能是「已带点号的合并段」（如手动构造的
    'reroute.end_node_index'），先按点拆开，避免拼接出错误键名。
    """
    parts: List[object] = []
    for raw in (p for p in loc if p != "body"):
        if isinstance(raw, str) and "." in raw:
            for sub in raw.split("."):
                parts.append(sub)
        else:
            parts.append(raw)
    key = ""
    for p in parts:
        if isinstance(p, int):
            key += f"[{p}]"
        else:
            key = str(p) if key == "" else f"{key}.{p}"
    return key or "_root"


_MESSAGES = {
    "finite_number": "必须是有限数值（不能是 NaN 或无穷）",
    "greater_than": "半径必须为正数",
    "missing": "该字段缺失",
    "string_type": "必须是数值，不能是字符串",
    "int_from_float": "坐标必须是整数毫米",
    "bool_type": "不能是布尔值",
    "too_short": "列表项数量不足",
    "too_long": "列表项数量过多",
}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """转换为统一的字段级错误结构。任何校验失败都不产生预检结论。"""
    errors: Dict[str, str] = {}
    for err in exc.errors():
        field = _loc_to_field(tuple(err.get("loc", ())))
        etype = err.get("type", "")
        msg = err.get("msg", "输入不合法")
        if etype in _MESSAGES:
            text = _MESSAGES[etype]
        elif etype == "value_error":
            text = msg.replace("Value error, ", "").strip()
        else:
            text = msg
        errors.setdefault(field, text)
    return JSONResponse(
        status_code=422,
        content={"ok": False, "errors": errors},
    )


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


def _rpt(p) -> PointOut:
    return PointOut(x=round3(p[0]), y=round3(p[1]))


def _build_response(
    result: AnalysisResult,
    nodes: List[Tuple[float, float]],
    circles: List[Tuple[Tuple[float, float], float]],
    cable_radius: float,
    calibration_view: Optional[CalibrationOut],
) -> PrecheckResponse:
    """把一次未舍入分析结果组装为展示响应（三位小数仅发生在这里）。

    原线与候选改线共用本函数，确保两套结论来自完全相同的标定与几何规则、
    相同的排序与舍入口径。
    """
    collisions = [
        CollisionOut(
            segment_index=c.segment_index,
            circle_index=c.circle_index,
            nearest=_rpt(c.nearest),
            distance=round3(c.distance),
            expanded_radius=round3(c.expanded_radius),
            circle_center=_rpt(circles[c.circle_index][0]),
            circle_radius=round3(circles[c.circle_index][1]),
            cable_radius=round3(cable_radius),
        )
        for c in result.collisions
    ]

    interval_views = [
        IntrusionIntervalOut(
            circle_index=iv.circle_index,
            entry_segment_index=iv.entry_segment_index,
            exit_segment_index=iv.exit_segment_index,
            entry=_rpt(iv.entry_point),
            exit=_rpt(iv.exit_point),
            start_mileage=round3(iv.start_mileage),
            end_mileage=round3(iv.end_mileage),
            length=round3(iv.length),
            pieces=[
                IntervalPieceOut(
                    segment_index=p.segment_index,
                    circle_index=p.circle_index,
                    entry=_rpt(p.entry_point),
                    exit=_rpt(p.exit_point),
                    start_mileage=round3(p.start_mileage),
                    end_mileage=round3(p.end_mileage),
                    length=round3(p.length),
                )
                for p in iv.pieces
            ],
        )
        for iv in result.intervals
    ]

    circle_views = [
        CircleOut(
            center=PointOut(x=round3(center[0]), y=round3(center[1])),
            radius=round3(r),
            expanded_radius=round3(r + cable_radius),
        )
        for (center, r) in circles
    ]

    compound_views = [
        CompoundIntrusionSegmentOut(
            circle_indices=list(seg.circle_indices),
            start_mileage=round3(seg.start_mileage),
            end_mileage=round3(seg.end_mileage),
            start=_rpt(seg.start_point),
            end=_rpt(seg.end_point),
            start_inclusive=seg.start_inclusive,
            end_inclusive=seg.end_inclusive,
            length=round3(seg.length),
            pieces=[
                CompoundPieceOut(
                    segment_index=p.segment_index,
                    circle_indices=list(p.circle_indices),
                    entry=_rpt(p.entry_point),
                    exit=_rpt(p.exit_point),
                    start_mileage=round3(p.start_mileage),
                    end_mileage=round3(p.end_mileage),
                    length=round3(p.length),
                )
                for p in seg.pieces
            ],
        )
        for seg in result.compounds
    ]

    return PrecheckResponse(
        feasible=len(collisions) == 0,
        cable_radius=round3(cable_radius),
        nodes=[PointOut(x=round3(x), y=round3(y)) for (x, y) in nodes],
        circles=circle_views,
        collision_count=len(collisions),
        first_collision=collisions[0] if collisions else None,
        collisions=collisions,
        intrusion_intervals=interval_views,
        compound_intrusion_segments=compound_views,
        calibration=calibration_view,
    )


@app.post("/api/precheck", response_model=PrecheckResponse)
def precheck(payload: PrecheckRequest) -> PrecheckResponse:
    nodes = [(n.x, n.y) for n in payload.nodes]
    circles = [((c.x, c.y), c.radius) for c in payload.circles]

    # 可选现场标定：先以未舍入双精度拟合 survey → path 刚体变换，
    # 残差超阈值直接 422（不生成任何碰撞/侵入/复合侵入结论）；
    # 通过后只变换禁入圈圆心，半径、电缆路径与电缆半径语义不变，
    # 后续粗筛与精确几何链路完全复用，排序/区间拓扑/三位展示同源。
    calibration_view: Optional[CalibrationOut] = None
    if payload.calibration is not None:
        survey = [(p.x, p.y) for p in payload.calibration.survey_points]
        path = [(p.x, p.y) for p in payload.calibration.path_points]
        transform = fit_rigid_transform(survey, path)
        if transform.rms_error > payload.calibration.max_rms_error:
            raise RequestValidationError(
                errors=[
                    {
                        "loc": ("body", "calibration", "max_rms_error"),
                        "msg": (
                            "Value error, 标定残差 RMS "
                            f"{transform.rms_error:.6g} mm 超过阈值 "
                            f"{payload.calibration.max_rms_error:.6g} mm"
                        ),
                        "type": "value_error",
                    }
                ]
            )
        circles = [(transform.apply(center), r) for (center, r) in circles]
        (r00, r01), (r10, r11) = transform.rotation
        calibration_view = CalibrationOut(
            point_count=transform.point_count,
            rotation=[
                [round3(r00), round3(r01)],
                [round3(r10), round3(r11)],
            ],
            translation=PointOut(
                x=round3(transform.translation[0]),
                y=round3(transform.translation[1]),
            ),
            rms_error=round3(transform.rms_error),
        )

    # 改线规格的结构校验在 schemas 层已完成；这里只剩几何夹具复核
    # （端点衔接/相邻重合），正常路径下不会失败，失败同样以字段级 422 返回。
    reroute_spec: Optional[RerouteSpec] = None
    if payload.reroute is not None:
        spec, err = validate_reroute(
            node_count=len(nodes),
            start=payload.reroute.start_node_index,
            end=payload.reroute.end_node_index,
            replacement_nodes=[(p.x, p.y) for p in payload.reroute.replacement_nodes],
        )
        if err is not None:
            field, message = err
            raise RequestValidationError(
                errors=[{
                    "loc": ("body", "reroute", field),
                    "msg": f"Value error, {message}",
                    "type": "value_error",
                }]
            )
        assert spec is not None
        junction_err = check_junctions(nodes, spec)
        if junction_err is not None:
            field, message = junction_err
            # field 形如 "replacement_nodes[0]" → 定位到具体列表项
            loc_parts: List[Any] = ["body", "reroute"]
            if "[" in field:
                name, idx_part = field.split("[", 1)
                loc_parts.append(name)
                loc_parts.append(int(idx_part.rstrip("]")))
            else:
                loc_parts.append(field)
            raise RequestValidationError(
                errors=[{
                    "loc": tuple(loc_parts),
                    "msg": f"Value error, {message}",
                    "type": "value_error",
                }]
            )
        reroute_spec = spec

    original = analyze_path_result(
        nodes=nodes,
        circles=circles,
        cable_radius=payload.cable_radius,
    )
    response = _build_response(
        result=original,
        nodes=nodes,
        circles=circles,
        cable_radius=payload.cable_radius,
        calibration_view=calibration_view,
    )

    if reroute_spec is not None:
        preview = build_preview(
            nodes=nodes,
            circles=circles,
            cable_radius=payload.cable_radius,
            spec=reroute_spec,
        )
        # 候选线响应：同一套标定视图、同一批变换后禁入圈、同一舍入口径。
        candidate_view = _build_response(
            result=preview.candidate,
            nodes=list(preview.candidate_nodes),
            circles=circles,
            cable_radius=payload.cable_radius,
            calibration_view=calibration_view,
        )

        def _event_out(ev) -> RerouteEventOut:
            return RerouteEventOut(
                circle_index=ev.circle_index,
                segment_index=ev.segment_index,
                entry=_rpt(ev.entry_point),
                exit=_rpt(ev.exit_point),
                start_mileage=round3(ev.start_mileage),
                end_mileage=round3(ev.end_mileage),
                length=round3(ev.length),
            )

        risk_views = [
            CircleRisksOut(
                circle_index=cr.circle_index,
                status=cr.status,
                removed=[_event_out(ev) for ev in cr.removed],
                added=[_event_out(ev) for ev in cr.added],
                remaining=[
                    RerouteRemainingOut(
                        circle_index=rp.circle_index,
                        original_segment_index=rp.original_segment_index,
                        candidate_segment_index=rp.candidate_segment_index,
                        entry=_rpt(rp.entry_point),
                        exit=_rpt(rp.exit_point),
                        original_start_mileage=round3(rp.original_start_mileage),
                        original_end_mileage=round3(rp.original_end_mileage),
                        candidate_start_mileage=round3(rp.candidate_start_mileage),
                        candidate_end_mileage=round3(rp.candidate_end_mileage),
                        length=round3(rp.length),
                        mileage_shift=round3(rp.mileage_shift),
                    )
                    for rp in cr.remaining
                ],
            )
            for cr in preview.circle_risks
        ]

        status_counts = {
            "removed": 0, "added": 0, "remaining": 0,
            "reduced": 0, "increased": 0, "replaced": 0,
        }
        n_removed = n_added = n_remaining = 0
        for cr in preview.circle_risks:
            status_counts[cr.status] += 1
            n_removed += len(cr.removed)
            n_added += len(cr.added)
            n_remaining += len(cr.remaining)

        summary = RerouteSummaryOut(
            circle_count=len(preview.circle_risks),
            removed_event_count=n_removed,
            added_event_count=n_added,
            remaining_event_count=n_remaining,
            circles_removed=status_counts["removed"],
            circles_added=status_counts["added"],
            circles_remaining=status_counts["remaining"],
            circles_reduced=status_counts["reduced"],
            circles_increased=status_counts["increased"],
            circles_replaced=status_counts["replaced"],
            original_total_length=round3(original.total_length),
            candidate_total_length=round3(preview.candidate.total_length),
            mileage_shift=round3(preview.mileage_shift),
        )

        response.reroute_preview = ReroutePreviewOut(
            start_node_index=reroute_spec.start,
            end_node_index=reroute_spec.end,
            replacement_nodes=[_rpt(p) for p in reroute_spec.replacement_nodes],
            junction_start=_rpt(preview.junction_start),
            junction_end=_rpt(preview.junction_end),
            prefix_length=round3(preview.prefix_length),
            original_junction_end_mileage=round3(
                preview.original_junction_end_mileage
            ),
            candidate_junction_end_mileage=round3(
                preview.candidate_junction_end_mileage
            ),
            mileage_shift=round3(preview.mileage_shift),
            candidate=candidate_view,
            circle_risks=risk_views,
            summary=summary,
        )

    return response
