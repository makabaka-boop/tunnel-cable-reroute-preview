"""FastAPI 应用：隧道电缆绕孔预检（可选全站仪 → 施工坐标现场标定）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .calibration import fit_rigid_transform
from .geometry import Collision, CompoundIntrusionSegment, IntrusionInterval, analyze_path_full
from .schemas import (
    CalibrationOut,
    CircleOut,
    CollisionOut,
    CompoundIntrusionSegmentOut,
    CompoundPieceOut,
    IntervalPieceOut,
    IntrusionIntervalOut,
    PointOut,
    PrecheckRequest,
    PrecheckResponse,
)

app = FastAPI(title="隧道电缆绕孔预检器", version="1.0.0")

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
    """
    parts = [p for p in loc if p != "body"]
    key = ""
    for p in parts:
        if isinstance(p, int):
            key += f"[{p}]"
        else:
            key = f"{key}.{p}" if key else str(p)
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

    raw: List[Collision]
    intervals: List[IntrusionInterval]
    compounds: List[CompoundIntrusionSegment]
    raw, intervals, compounds = analyze_path_full(
        nodes=nodes,
        circles=circles,
        cable_radius=payload.cable_radius,
    )

    def rpt(p) -> PointOut:
        return PointOut(x=round3(p[0]), y=round3(p[1]))

    collisions = [
        CollisionOut(
            segment_index=c.segment_index,
            circle_index=c.circle_index,
            nearest=rpt(c.nearest),
            distance=round3(c.distance),
            expanded_radius=round3(c.expanded_radius),
            circle_center=rpt(circles[c.circle_index][0]),
            circle_radius=round3(circles[c.circle_index][1]),
            cable_radius=round3(payload.cable_radius),
        )
        for c in raw
    ]

    interval_views = [
        IntrusionIntervalOut(
            circle_index=iv.circle_index,
            entry_segment_index=iv.entry_segment_index,
            exit_segment_index=iv.exit_segment_index,
            entry=rpt(iv.entry_point),
            exit=rpt(iv.exit_point),
            start_mileage=round3(iv.start_mileage),
            end_mileage=round3(iv.end_mileage),
            length=round3(iv.length),
            pieces=[
                IntervalPieceOut(
                    segment_index=p.segment_index,
                    circle_index=p.circle_index,
                    entry=rpt(p.entry_point),
                    exit=rpt(p.exit_point),
                    start_mileage=round3(p.start_mileage),
                    end_mileage=round3(p.end_mileage),
                    length=round3(p.length),
                )
                for p in iv.pieces
            ],
        )
        for iv in intervals
    ]

    circle_views = [
        CircleOut(
            center=PointOut(x=round3(center[0]), y=round3(center[1])),
            radius=round3(r),
            expanded_radius=round3(r + payload.cable_radius),
        )
        for (center, r) in circles
    ]

    compound_views = [
        CompoundIntrusionSegmentOut(
            circle_indices=list(seg.circle_indices),
            start_mileage=round3(seg.start_mileage),
            end_mileage=round3(seg.end_mileage),
            start=rpt(seg.start_point),
            end=rpt(seg.end_point),
            start_inclusive=seg.start_inclusive,
            end_inclusive=seg.end_inclusive,
            length=round3(seg.length),
            pieces=[
                CompoundPieceOut(
                    segment_index=p.segment_index,
                    circle_indices=list(p.circle_indices),
                    entry=rpt(p.entry_point),
                    exit=rpt(p.exit_point),
                    start_mileage=round3(p.start_mileage),
                    end_mileage=round3(p.end_mileage),
                    length=round3(p.length),
                )
                for p in seg.pieces
            ],
        )
        for seg in compounds
    ]

    return PrecheckResponse(
        feasible=len(collisions) == 0,
        cable_radius=round3(payload.cable_radius),
        nodes=[PointOut(x=round3(x), y=round3(y)) for (x, y) in nodes],
        circles=circle_views,
        collision_count=len(collisions),
        first_collision=collisions[0] if collisions else None,
        collisions=collisions,
        intrusion_intervals=interval_views,
        compound_intrusion_segments=compound_views,
        calibration=calibration_view,
    )
