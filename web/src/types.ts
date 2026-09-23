export interface Point {
  x: number;
  y: number;
}

export interface Collision {
  segment_index: number;
  circle_index: number;
  nearest: Point;
  distance: number;
  expanded_radius: number;
  circle_center: Point;
  circle_radius: number;
  cable_radius: number;
}

export interface CircleView {
  center: Point;
  radius: number;
  expanded_radius: number;
}

export interface IntervalPiece {
  segment_index: number;
  circle_index: number;
  entry: Point;
  exit: Point;
  start_mileage: number;
  end_mileage: number;
  length: number;
}

export interface IntrusionInterval {
  circle_index: number;
  entry_segment_index: number;
  exit_segment_index: number;
  entry: Point;
  exit: Point;
  start_mileage: number;
  end_mileage: number;
  length: number;
  pieces: IntervalPiece[];
}

export interface CompoundPiece {
  segment_index: number;
  circle_indices: number[];
  entry: Point;
  exit: Point;
  start_mileage: number;
  end_mileage: number;
  length: number;
}

export interface CompoundIntrusionSegment {
  circle_indices: number[];
  start_mileage: number;
  end_mileage: number;
  start: Point;
  end: Point;
  start_inclusive: boolean;
  end_inclusive: boolean;
  length: number;
  pieces: CompoundPiece[];
}

export interface CalibrationResult {
  point_count: number;
  rotation: number[][]; // 2x2 行主序真旋转矩阵（det = +1）
  translation: Point;
  rms_error: number;
}

export interface CalibrationPayload {
  survey_points: Point[];
  path_points: Point[];
  max_rms_error: number;
}

// ---------------- 一次性改线预览 ----------------

export interface ReroutePayload {
  start_node_index: number;
  end_node_index: number;
  replacement_nodes: Point[];
}

export type CircleRiskStatus =
  | "remaining"
  | "removed"
  | "added"
  | "reduced"
  | "increased"
  | "replaced";

/** 消除 / 新增事件（原线或候选线各自视角）。 */
export interface RerouteEvent {
  circle_index: number;
  segment_index: number;
  entry: Point;
  exit: Point;
  start_mileage: number;
  end_mileage: number;
  length: number;
}

/** 仍存在事件：同一未舍入结构身份，双份里程（后缀带固定平移）。 */
export interface RerouteRemaining {
  circle_index: number;
  original_segment_index: number;
  candidate_segment_index: number;
  entry: Point;
  exit: Point;
  original_start_mileage: number;
  original_end_mileage: number;
  candidate_start_mileage: number;
  candidate_end_mileage: number;
  length: number;
  mileage_shift: number;
}

export interface CircleRisks {
  circle_index: number;
  status: CircleRiskStatus;
  removed: RerouteEvent[];
  added: RerouteEvent[];
  remaining: RerouteRemaining[];
}

export interface RerouteSummary {
  circle_count: number;
  removed_event_count: number;
  added_event_count: number;
  remaining_event_count: number;
  circles_removed: number;
  circles_added: number;
  circles_remaining: number;
  circles_reduced: number;
  circles_increased: number;
  circles_replaced: number;
  original_total_length: number;
  candidate_total_length: number;
  mileage_shift: number;
}

export interface ReroutePreview {
  start_node_index: number;
  end_node_index: number;
  replacement_nodes: Point[];
  junction_start: Point;
  junction_end: Point;
  prefix_length: number;
  original_junction_end_mileage: number;
  candidate_junction_end_mileage: number;
  mileage_shift: number;
  candidate: PrecheckResponse;
  circle_risks: CircleRisks[];
  summary: RerouteSummary;
}

export interface PrecheckResponse {
  feasible: boolean;
  cable_radius: number;
  nodes: Point[];
  circles: CircleView[];
  collision_count: number;
  first_collision: Collision | null;
  collisions: Collision[];
  intrusion_intervals: IntrusionInterval[];
  compound_intrusion_segments: CompoundIntrusionSegment[];
  // 请求带 calibration 时给出标定摘要；省略时为 null/缺省（逐项兼容）
  calibration?: CalibrationResult | null;
  // 请求带 reroute 时给出改线预览（顶层仍是原线结论）；省略时为 null
  reroute_preview?: ReroutePreview | null;
}

export interface PrecheckPayload {
  nodes: Point[];
  cable_radius: number;
  circles: Array<Point & { radius: number }>;
  calibration?: CalibrationPayload;
  reroute?: ReroutePayload;
}

export type FieldErrors = Record<string, string>;
