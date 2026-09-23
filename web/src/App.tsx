import { useRef, useState } from "react";
import { precheck, ValidationError } from "./api/client";
import { Scene } from "./components/Scene";
import {
  buildPayload,
  validateDraft,
  type CalibrationPairDraft,
  type CircleDraft,
  type FormDraft,
  type NodeDraft,
} from "./lib/validation";
import type {
  CompoundIntrusionSegment,
  FieldErrors,
  IntrusionInterval,
  PrecheckResponse,
} from "./types";
import "./App.css";

const err = (errors: FieldErrors, key: string) => errors[key];
const hasErr = (errors: FieldErrors, key: string) => Boolean(errors[key]);

function NumInput(props: {
  value: string;
  testid: string;
  ariaLabel: string;
  invalid?: boolean;
  onChange: (v: string) => void;
}) {
  return (
    <input
      className={`num-input${props.invalid ? " invalid" : ""}`}
      value={props.value}
      aria-label={props.ariaLabel}
      data-testid={props.testid}
      inputMode="decimal"
      onChange={(e) => props.onChange(e.target.value)}
    />
  );
}

const emptyPair = (): CalibrationPairDraft => ({
  surveyX: "",
  surveyY: "",
  pathX: "",
  pathY: "",
});

const initialDraft: FormDraft = {
  cableRadius: "5",
  nodes: [
    { x: "-100", y: "0" },
    { x: "100", y: "0" },
  ],
  circles: [{ x: "0", y: "15", radius: "10" }],
  calibrationEnabled: false,
  maxRmsError: "1",
  calibrationPairs: [emptyPair(), emptyPair()],
};

const fmt = (n: number) => String(n);

function IntervalRow({ iv, order }: { iv: IntrusionInterval; order: number }) {
  const sameSegment = iv.entry_segment_index === iv.exit_segment_index;
  return (
    <li className="interval-row" data-testid={`interval-${order}`}>
      <div className="interval-head">
        <span className="interval-order">#{order + 1}</span>
        <span className="interval-circle">禁入圈 #{iv.circle_index}</span>
        <span
          className={`interval-badge${iv.length === 0 ? " zero" : ""}`}
          data-testid={`interval-${order}-badge`}
        >
          {iv.length === 0 ? "相切零长点" : `侵入 ${fmt(iv.length)} mm`}
        </span>
      </div>
      <div className="interval-body">
        {sameSegment ? (
          <p>
            线段 #{iv.entry_segment_index} 内侵入：进入 ({fmt(iv.entry.x)}, {fmt(iv.entry.y)})
            → 离开 ({fmt(iv.exit.x)}, {fmt(iv.exit.y)})
          </p>
        ) : (
          <p>
            进入线段 #{iv.entry_segment_index}（{fmt(iv.entry.x)}, {fmt(iv.entry.y)}）→
            经 {iv.pieces.length} 个连续线段片段 → 离开线段 #{iv.exit_segment_index}（
            {fmt(iv.exit.x)}, {fmt(iv.exit.y)}）
          </p>
        )}
        <p className="interval-mileage">
          累计里程：{fmt(iv.start_mileage)} → {fmt(iv.end_mileage)} mm
          {sameSegment ? "" : `（跨拐点 ${iv.exit_segment_index - iv.entry_segment_index} 处）`}
        </p>
      </div>
    </li>
  );
}

const leftBracket = (inclusive: boolean) => (inclusive ? "[" : "(");
const rightBracket = (inclusive: boolean) => (inclusive ? "]" : ")");

function CompoundRow({ seg, order }: { seg: CompoundIntrusionSegment; order: number }) {
  const isZero = seg.start_mileage === seg.end_mileage;
  const segIds = seg.pieces.map((p) => p.segment_index);
  const sameSegment = segIds.length <= 1;
  return (
    <li className="interval-row compound-row" data-testid={`compound-${order}`}>
      <div className="interval-head">
        <span className="interval-order">#{order + 1}</span>
        <span
          className="interval-circle"
          data-testid={`compound-${order}-circles`}
        >
          {seg.circle_indices.map((c) => `禁入圈 #${c}`).join(" + ")}
        </span>
        <span
          className={`interval-badge${isZero ? " zero" : ""}`}
          data-testid={`compound-${order}-badge`}
        >
          {isZero ? "多圈相切零长点" : `复合侵入 ${fmt(seg.length)} mm`}
        </span>
      </div>
      <div className="interval-body">
        <p data-testid={`compound-${order}-endpoints`}>
          {sameSegment ? "线段" : "跨线段"} #{segIds.join(" → #")}：
          {leftBracket(seg.start_inclusive)}({fmt(seg.start.x)}, {fmt(seg.start.y)})
          {" → "}
          ({fmt(seg.end.x)}, {fmt(seg.end.y)}){rightBracket(seg.end_inclusive)}
        </p>
        <p
          className="interval-mileage"
          data-testid={`compound-${order}-mileage`}
        >
          累计里程：{leftBracket(seg.start_inclusive)}
          {fmt(seg.start_mileage)} → {fmt(seg.end_mileage)}
          {rightBracket(seg.end_inclusive)} mm
          {sameSegment ? "" : `（跨 ${segIds.length - 1} 处拐点）`}
        </p>
        <ol
          className="compound-pieces"
          data-testid={`compound-${order}-pieces`}
        >
          {seg.pieces.map((p) => (
            <li
              key={`${p.segment_index}-${p.circle_indices.join("-")}`}
              data-testid={`compound-${order}-piece-${p.segment_index}`}
            >
              线段 #{p.segment_index} · 圈 {p.circle_indices.join("/")}：
              ({fmt(p.entry.x)}, {fmt(p.entry.y)}) → ({fmt(p.exit.x)}, {fmt(p.exit.y)})
              ，里程 {fmt(p.start_mileage)} → {fmt(p.end_mileage)} mm
              {p.length === 0 ? "（零长点）" : `，长 ${fmt(p.length)} mm`}
            </li>
          ))}
        </ol>
      </div>
    </li>
  );
}

export function App() {
  const [draft, setDraft] = useState<FormDraft>(initialDraft);
  const [result, setResult] = useState<PrecheckResponse | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [networkError, setNetworkError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // 每次“新提交/校验失败/请求失败/重置”自增；在途旧响应回来时凭它作废，
  // 保证旧结论（含侵入区间）永远不会在新状态之后“复活”。
  const requestSeq = useRef(0);

  const updateNode = (i: number, patch: Partial<NodeDraft>) => {
    setDraft((d) => ({
      ...d,
      nodes: d.nodes.map((n, idx) => (idx === i ? { ...n, ...patch } : n)),
    }));
  };
  const updateCircle = (i: number, patch: Partial<CircleDraft>) => {
    setDraft((d) => ({
      ...d,
      circles: d.circles.map((c, idx) => (idx === i ? { ...c, ...patch } : c)),
    }));
  };
  const updatePair = (i: number, patch: Partial<CalibrationPairDraft>) => {
    setDraft((d) => ({
      ...d,
      calibrationPairs: d.calibrationPairs.map((p, idx) =>
        idx === i ? { ...p, ...patch } : p,
      ),
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    // 任何新的提交都先使旧请求失效并清除旧结论与错误。
    const seq = ++requestSeq.current;
    setResult(null);
    setErrors({});
    setNetworkError(null);

    const fieldErrors = validateDraft(draft);
    if (Object.keys(fieldErrors).length > 0) {
      setErrors(fieldErrors); // 非法输入：整次预检不保留旧结论
      return;
    }

    setLoading(true);
    try {
      const data = await precheck(buildPayload(draft));
      // 在途旧响应（用户已再次提交/重置/后发请求）不得覆盖新状态。
      if (seq !== requestSeq.current) return;
      setResult(data);
    } catch (e) {
      if (seq !== requestSeq.current) return;
      if (e instanceof ValidationError) {
        // 服务端字段级错误（与本地错误键一致）
        setErrors(e.errors);
      } else {
        setNetworkError((e as Error).message);
      }
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  };

  const handleReset = () => {
    requestSeq.current += 1; // 在途响应作废
    setDraft(initialDraft);
    setResult(null);
    setErrors({});
    setNetworkError(null);
    setLoading(false);
  };

  // 后端按 (线段下标, 禁入圈输入顺序) 确定首个碰撞；页面必须直接使用该结果，
  // 不能再按距离自行改排，否则“首个”和“其余”会与接口明细不一致。
  const first = result?.first_collision ?? null;
  const rest = result ? result.collisions.slice(1) : [];

  return (
    <div className="page">
      <header className="header">
        <h1>隧道电缆绕孔预检器</h1>
        <p className="subtitle">
          电缆折线绕开钻孔作业面时，按「孔半径 + 电缆半径」的扩张安全圈逐线段检测；
          距离 ≤ 扩张半径即碰撞（含相切），判定位置为圆心到线段的最近点。
        </p>
      </header>

      <main className="layout">
        <form className="panel form" onSubmit={handleSubmit} noValidate>
          <section>
            <h2>电缆半径（毫米，正数）</h2>
            <NumInput
              value={draft.cableRadius}
              testid="cable-radius"
              ariaLabel="电缆半径（毫米）"
              invalid={hasErr(errors, "cable_radius")}
              onChange={(v) => setDraft((d) => ({ ...d, cableRadius: v }))}
            />
            {err(errors, "cable_radius") && (
              <p className="field-error" data-testid="err-cable_radius">
                {err(errors, "cable_radius")}
              </p>
            )}
          </section>

          <section>
            <div className="row-head">
              <h2>路径节点（整数毫米，按输入顺序）</h2>
              <button
                type="button"
                className="btn small"
                data-testid="add-node"
                onClick={() =>
                  setDraft((d) => ({
                    ...d,
                    nodes: [...d.nodes, { x: "0", y: "0" }],
                  }))
                }
              >
                + 节点
              </button>
            </div>
            {hasErr(errors, "nodes") && (
              <p className="field-error" data-testid="err-nodes">
                {err(errors, "nodes")}
              </p>
            )}
            <ol className="rows" data-testid="node-list">
              {draft.nodes.map((node, i) => (
                <li key={i} className="row">
                  <span className="row-index">#{i}</span>
                  <NumInput
                    value={node.x}
                    testid={`node-${i}-x`}
                    ariaLabel={`节点 ${i} X 坐标`}
                    invalid={hasErr(errors, `nodes[${i}].x`)}
                    onChange={(v) => updateNode(i, { x: v })}
                  />
                  <NumInput
                    value={node.y}
                    testid={`node-${i}-y`}
                    ariaLabel={`节点 ${i} Y 坐标`}
                    invalid={hasErr(errors, `nodes[${i}].y`)}
                    onChange={(v) => updateNode(i, { y: v })}
                  />
                  <button
                    type="button"
                    className="btn ghost small"
                    aria-label={`删除节点 ${i}`}
                    data-testid={`remove-node-${i}`}
                    disabled={draft.nodes.length <= 1}
                    onClick={() =>
                      setDraft((d) => ({
                        ...d,
                        nodes: d.nodes.filter((_, idx) => idx !== i),
                      }))
                    }
                  >
                    删除
                  </button>
                  {(err(errors, `nodes[${i}].x`) || err(errors, `nodes[${i}].y`)) && (
                    <p className="field-error row-error">
                      {err(errors, `nodes[${i}].x`) ?? err(errors, `nodes[${i}].y`)}
                    </p>
                  )}
                </li>
              ))}
            </ol>
          </section>

          <section>
            <div className="row-head">
              <h2>禁入圈（圆心整数毫米，半径正数）</h2>
              <button
                type="button"
                className="btn small"
                data-testid="add-circle"
                onClick={() =>
                  setDraft((d) => ({
                    ...d,
                    circles: [...d.circles, { x: "0", y: "0", radius: "10" }],
                  }))
                }
              >
                + 禁入圈
              </button>
            </div>
            <ol className="rows" data-testid="circle-list">
              {draft.circles.map((c, i) => (
                <li key={i} className="row">
                  <span className="row-index">#{i}</span>
                  <NumInput
                    value={c.x}
                    testid={`circle-${i}-x`}
                    ariaLabel={`禁入圈 ${i} 圆心 X`}
                    invalid={hasErr(errors, `circles[${i}].x`)}
                    onChange={(v) => updateCircle(i, { x: v })}
                  />
                  <NumInput
                    value={c.y}
                    testid={`circle-${i}-y`}
                    ariaLabel={`禁入圈 ${i} 圆心 Y`}
                    invalid={hasErr(errors, `circles[${i}].y`)}
                    onChange={(v) => updateCircle(i, { y: v })}
                  />
                  <NumInput
                    value={c.radius}
                    testid={`circle-${i}-radius`}
                    ariaLabel={`禁入圈 ${i} 半径`}
                    invalid={hasErr(errors, `circles[${i}].radius`)}
                    onChange={(v) => updateCircle(i, { radius: v })}
                  />
                  <button
                    type="button"
                    className="btn ghost small"
                    aria-label={`删除禁入圈 ${i}`}
                    data-testid={`remove-circle-${i}`}
                    onClick={() =>
                      setDraft((d) => ({
                        ...d,
                        circles: d.circles.filter((_, idx) => idx !== i),
                      }))
                    }
                  >
                    删除
                  </button>
                  {(err(errors, `circles[${i}].x`) ||
                    err(errors, `circles[${i}].y`) ||
                    err(errors, `circles[${i}].radius`)) && (
                    <p className="field-error row-error">
                      {err(errors, `circles[${i}].x`) ??
                        err(errors, `circles[${i}].y`) ??
                        err(errors, `circles[${i}].radius`)}
                    </p>
                  )}
                </li>
              ))}
            </ol>
          </section>

          <section>
            <div className="row-head">
              <h2>现场标定（可选）：全站仪 → 施工坐标</h2>
              <label className="calibration-toggle">
                <input
                  type="checkbox"
                  data-testid="calibration-enabled"
                  checked={draft.calibrationEnabled}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, calibrationEnabled: e.target.checked }))
                  }
                />
                启用控制点标定
              </label>
            </div>
            {draft.calibrationEnabled && (
              <div className="calibration-editor" data-testid="calibration-editor">
                <p className="hint">
                  钻孔中心来自全站仪坐标系；录入 2～20 对现场控制点，后端按最小二乘
                  拟合「旋转 + 平移」刚体变换（不缩放、不镜像）后再做绕孔预检。
                </p>
                <div className="calibration-threshold">
                  <span className="row-index">残差阈值（毫米，正数）</span>
                  <NumInput
                    value={draft.maxRmsError}
                    testid="max-rms-error"
                    ariaLabel="标定残差阈值（毫米）"
                    invalid={hasErr(errors, "calibration.max_rms_error")}
                    onChange={(v) => setDraft((d) => ({ ...d, maxRmsError: v }))}
                  />
                </div>
                {err(errors, "calibration.max_rms_error") && (
                  <p className="field-error" data-testid="err-calibration.max_rms_error">
                    {err(errors, "calibration.max_rms_error")}
                  </p>
                )}
                {hasErr(errors, "calibration") && (
                  <p className="field-error" data-testid="err-calibration">
                    {err(errors, "calibration")}
                  </p>
                )}
                {(err(errors, "calibration.survey_points") ||
                  err(errors, "calibration.path_points")) && (
                  <p className="field-error" data-testid="err-calibration-points">
                    {err(errors, "calibration.survey_points") ??
                      err(errors, "calibration.path_points")}
                  </p>
                )}
                <ol className="rows" data-testid="calibration-pair-list">
                  {draft.calibrationPairs.map((p, i) => (
                    <li key={i} className="row pair-row">
                      <span className="row-index">#{i}</span>
                      <NumInput
                        value={p.surveyX}
                        testid={`pair-${i}-survey-x`}
                        ariaLabel={`控制点 ${i} 全站仪 X`}
                        invalid={hasErr(errors, `calibration.survey_points[${i}].x`)}
                        onChange={(v) => updatePair(i, { surveyX: v })}
                      />
                      <NumInput
                        value={p.surveyY}
                        testid={`pair-${i}-survey-y`}
                        ariaLabel={`控制点 ${i} 全站仪 Y`}
                        invalid={hasErr(errors, `calibration.survey_points[${i}].y`)}
                        onChange={(v) => updatePair(i, { surveyY: v })}
                      />
                      <NumInput
                        value={p.pathX}
                        testid={`pair-${i}-path-x`}
                        ariaLabel={`控制点 ${i} 施工 X`}
                        invalid={hasErr(errors, `calibration.path_points[${i}].x`)}
                        onChange={(v) => updatePair(i, { pathX: v })}
                      />
                      <NumInput
                        value={p.pathY}
                        testid={`pair-${i}-path-y`}
                        ariaLabel={`控制点 ${i} 施工 Y`}
                        invalid={hasErr(errors, `calibration.path_points[${i}].y`)}
                        onChange={(v) => updatePair(i, { pathY: v })}
                      />
                      <button
                        type="button"
                        className="btn ghost small"
                        aria-label={`删除控制点 ${i}`}
                        data-testid={`remove-pair-${i}`}
                        disabled={draft.calibrationPairs.length <= 2}
                        onClick={() =>
                          setDraft((d) => ({
                            ...d,
                            calibrationPairs: d.calibrationPairs.filter(
                              (_, idx) => idx !== i,
                            ),
                          }))
                        }
                      >
                        删除
                      </button>
                      {(err(errors, `calibration.survey_points[${i}].x`) ||
                        err(errors, `calibration.survey_points[${i}].y`) ||
                        err(errors, `calibration.path_points[${i}].x`) ||
                        err(errors, `calibration.path_points[${i}].y`)) && (
                        <p className="field-error row-error">
                          {err(errors, `calibration.survey_points[${i}].x`) ??
                            err(errors, `calibration.survey_points[${i}].y`) ??
                            err(errors, `calibration.path_points[${i}].x`) ??
                            err(errors, `calibration.path_points[${i}].y`)}
                        </p>
                      )}
                    </li>
                  ))}
                </ol>
                <div className="row-head pair-add">
                  <span className="hint">
                    每行一对：全站仪 X/Y ↔ 施工 X/Y（{draft.calibrationPairs.length}/20 对）
                  </span>
                  <button
                    type="button"
                    className="btn small"
                    data-testid="add-pair"
                    disabled={draft.calibrationPairs.length >= 20}
                    onClick={() =>
                      setDraft((d) => ({
                        ...d,
                        calibrationPairs: [...d.calibrationPairs, emptyPair()],
                      }))
                    }
                  >
                    + 控制点对
                  </button>
                </div>
              </div>
            )}
          </section>

          <div className="actions">
            <button type="submit" className="btn primary" data-testid="submit" disabled={loading}>
              {loading ? "预检中…" : "开始预检"}
            </button>
            <button type="button" className="btn ghost" data-testid="reset" onClick={handleReset}>
              重置示例
            </button>
          </div>

          {networkError && (
            <p className="field-error" data-testid="network-error">
              请求失败：{networkError}
            </p>
          )}
        </form>

        <section className="panel result">
          {!result && Object.keys(errors).length === 0 && !networkError && (
            <div className="placeholder" data-testid="placeholder">
              <p>填写路径节点、电缆半径与禁入圈后点击「开始预检」。</p>
              <p className="hint">判定值四舍五入显示至三位小数，计算内部使用双精度。</p>
            </div>
          )}

          {Object.keys(errors).length > 0 && (
            <div className="banner error" data-testid="banner-error">
              <strong>预检未执行：</strong>
              存在 {Object.keys(errors).length} 个字段错误，旧结论已清除。请修正红色字段后重试。
            </div>
          )}

          {result && result.calibration && (
            <div className="interval-panel calibration-panel" data-testid="calibration-panel">
              <h2 className="interval-title">
                标定结果（survey → path 刚体变换，{result.calibration.point_count} 对控制点）
              </h2>
              <div className="calibration-summary">
                <p data-testid="calibration-rms">
                  残差 RMS：{fmt(result.calibration.rms_error)} mm
                </p>
                <p data-testid="calibration-rotation">
                  旋转矩阵：[[{fmt(result.calibration.rotation[0][0])},{" "}
                  {fmt(result.calibration.rotation[0][1])}], [
                  {fmt(result.calibration.rotation[1][0])},{" "}
                  {fmt(result.calibration.rotation[1][1])}]]
                </p>
                <p data-testid="calibration-translation">
                  平移：({fmt(result.calibration.translation.x)},{" "}
                  {fmt(result.calibration.translation.y)}) mm
                </p>
              </div>
            </div>
          )}

          {result && result.feasible && (
            <div className="banner ok" data-testid="banner-ok">
              <strong>✅ 可敷设</strong>
              <span>所有线段均在扩张安全圈之外（相切亦视为碰撞）。</span>
            </div>
          )}

          {result && !result.feasible && first && (
            <div className="banner collision" data-testid="banner-collision">
              <strong>⛔ 不可敷设：{result.collision_count} 处碰撞</strong>
              <div className="first-detail" data-testid="first-collision-detail">
                首个碰撞：线段 #{first.segment_index} × 禁入圈 #{first.circle_index}
                ，判定位置（最近点）= ({first.nearest.x}, {first.nearest.y}) mm，
                圆心距离 = {first.distance} mm ≤ 扩张半径 {first.expanded_radius} mm。
              </div>
              {rest.length > 0 && (
                <details open className="rest-list">
                  <summary>其余 {rest.length} 处碰撞（升序）</summary>
                  <ul data-testid="rest-collisions">
                    {rest.map((c) => (
                      <li key={`${c.segment_index}-${c.circle_index}`}>
                        线段 #{c.segment_index} × 禁入圈 #{c.circle_index}：
                        判定位置 ({c.nearest.x}, {c.nearest.y})，距离 {c.distance} ≤{" "}
                        {c.expanded_radius}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}

          {result && !result.feasible && result.intrusion_intervals.length > 0 && (
            <div className="interval-panel" data-testid="interval-panel">
              <h2 className="interval-title">
                连续侵入区间（{result.intrusion_intervals.length} 个，按起始里程排序）
              </h2>
              <ol className="interval-list" data-testid="interval-list">
                {result.intrusion_intervals.map((iv, order) => (
                  <IntervalRow key={`${order}-${iv.circle_index}`} iv={iv} order={order} />
                ))}
              </ol>
            </div>
          )}

          {result && result.compound_intrusion_segments.length > 0 && (
            <div className="interval-panel" data-testid="compound-panel">
              <h2 className="interval-title">
                复合侵入段（{result.compound_intrusion_segments.length} 个，
                同时落入至少两个扩张圈，按起始里程与圈序排列）
              </h2>
              <ol className="interval-list" data-testid="compound-list">
                {result.compound_intrusion_segments.map((seg, order) => (
                  <CompoundRow
                    key={`${order}-${seg.circle_indices.join("-")}-${seg.start_mileage}`}
                    seg={seg}
                    order={order}
                  />
                ))}
              </ol>
            </div>
          )}

          {result && (
            <>
              <Scene result={result} />
              <p className="legend">
                <span className="lg lg-solid" /> 禁入圈（孔半径）
                <span className="lg lg-dashed" /> 扩张安全圈（孔半径+电缆半径）
                <span className="lg lg-intrusion" /> 连续侵入区间
                {result.compound_intrusion_segments.length > 0 && (
                  <>
                    <span className="lg lg-compound" /> 复合侵入段（≥2 圈）
                  </>
                )}
                <span className="lg lg-star" /> 首个碰撞判定位置
                <span className="lg lg-dot" /> 其余碰撞
              </p>
            </>
          )}
        </section>
      </main>
    </div>
  );
}
