import { describe, expect, it } from "vitest";
import { buildPayload, validateDraft, type FormDraft } from "./validation";

const okDraft: FormDraft = {
  cableRadius: "5",
  nodes: [
    { x: "0", y: "0" },
    { x: "100", y: "0" },
  ],
  circles: [{ x: "50", y: "30", radius: "10" }],
  calibrationEnabled: false,
  maxRmsError: "1",
  calibrationPairs: [
    { surveyX: "", surveyY: "", pathX: "", pathY: "" },
    { surveyX: "", surveyY: "", pathX: "", pathY: "" },
  ],
};

/** 启用标定的合法草稿：纯平移 survey = path + (1000, 2000)。 */
const calibratedDraft: FormDraft = {
  ...okDraft,
  calibrationEnabled: true,
  calibrationPairs: [
    { surveyX: "1000", surveyY: "2000", pathX: "0", pathY: "0" },
    { surveyX: "1100", surveyY: "2000", pathX: "100", pathY: "0" },
    { surveyX: "1000", surveyY: "2100", pathX: "0", pathY: "100" },
  ],
};

describe("录入校验 validateDraft（与后端字段键一致）", () => {
  it("合法录入无错误", () => {
    expect(validateDraft(okDraft)).toEqual({});
    expect(buildPayload(okDraft)).toEqual({
      cable_radius: 5,
      nodes: [
        { x: 0, y: 0 },
        { x: 100, y: 0 },
      ],
      circles: [{ x: 50, y: 30, radius: 10 }],
    });
  });

  it("节点不足报错", () => {
    const d: FormDraft = { ...okDraft, nodes: [{ x: "0", y: "0" }] };
    expect(validateDraft(d).nodes).toContain("至少");
  });

  it("非正电缆半径报错", () => {
    expect(validateDraft({ ...okDraft, cableRadius: "0" }).cable_radius).toBeTruthy();
    expect(validateDraft({ ...okDraft, cableRadius: "-2" }).cable_radius).toBeTruthy();
  });

  it("非整数毫米坐标报错，且拒绝 NaN / Infinity", () => {
    const cases = ["12.5", "abc", "NaN", "Infinity", "-Infinity", ""];
    for (const bad of cases) {
      const d: FormDraft = {
        ...okDraft,
        nodes: [
          { x: "0", y: "0" },
          { x: bad, y: "0" },
        ],
      };
      expect(validateDraft(d)["nodes[1].x"], `bad=${bad}`).toBeTruthy();
    }
  });

  it("相邻重复节点报错（挂字段级键）", () => {
    const d: FormDraft = {
      ...okDraft,
      nodes: [
        { x: "7", y: "7" },
        { x: "7", y: "7" },
      ],
    };
    const errors = validateDraft(d);
    expect(errors["nodes[1].x"]).toContain("重合");
  });

  it("禁入圈半径必须为有限正数", () => {
    const d: FormDraft = {
      ...okDraft,
      circles: [{ x: "0", y: "0", radius: "0" }],
    };
    expect(validateDraft(d)["circles[0].radius"]).toBeTruthy();
  });

  it("多个字段错误可同时收集", () => {
    const d: FormDraft = {
      cableRadius: "-1",
      nodes: [{ x: "x", y: "0" }],
      circles: [{ x: "1", y: "2", radius: "NaN" }],
      calibrationEnabled: false,
      maxRmsError: "1",
      calibrationPairs: [],
    };
    const errors = validateDraft(d);
    expect(Object.keys(errors).sort()).toEqual(
      ["cable_radius", "circles[0].radius", "nodes", "nodes[0].x"].sort(),
    );
  });
});

describe("现场标定校验（字段键与后端 calibration.* 一致）", () => {
  it("未启用标定：不校验控制点，载荷省略 calibration 键", () => {
    const d: FormDraft = {
      ...okDraft,
      calibrationPairs: [{ surveyX: "abc", surveyY: "", pathX: "", pathY: "" }],
    };
    expect(validateDraft(d)).toEqual({});
    expect("calibration" in buildPayload(d)).toBe(false);
  });

  it("合法标定：载荷带 calibration，坐标解析为数值", () => {
    expect(validateDraft(calibratedDraft)).toEqual({});
    expect(buildPayload(calibratedDraft).calibration).toEqual({
      survey_points: [
        { x: 1000, y: 2000 },
        { x: 1100, y: 2000 },
        { x: 1000, y: 2100 },
      ],
      path_points: [
        { x: 0, y: 0 },
        { x: 100, y: 0 },
        { x: 0, y: 100 },
      ],
      max_rms_error: 1,
    });
  });

  it("控制点对数需 2～20 对", () => {
    const one: FormDraft = {
      ...calibratedDraft,
      calibrationPairs: [calibratedDraft.calibrationPairs[0]],
    };
    expect(validateDraft(one).calibration).toContain("2～20 对");
    const many: FormDraft = {
      ...calibratedDraft,
      calibrationPairs: Array.from({ length: 21 }, (_, i) => ({
        surveyX: String(i),
        surveyY: "0",
        pathX: String(i),
        pathY: "0",
      })),
    };
    expect(validateDraft(many).calibration).toContain("2～20 对");
  });

  it("非有限控制点坐标挂在对应字段键", () => {
    const d: FormDraft = {
      ...calibratedDraft,
      calibrationPairs: [
        { surveyX: "NaN", surveyY: "0", pathX: "0", pathY: "0" },
        { surveyX: "1", surveyY: "Infinity", pathX: "1", pathY: "0" },
        { surveyX: "0", surveyY: "1", pathX: "", pathY: "1" },
      ],
    };
    const errors = validateDraft(d);
    expect(errors["calibration.survey_points[0].x"]).toBeTruthy();
    expect(errors["calibration.survey_points[1].y"]).toBeTruthy();
    expect(errors["calibration.path_points[2].x"]).toBeTruthy();
  });

  it("任一组控制点全部重合即退化", () => {
    const surveyDeg: FormDraft = {
      ...calibratedDraft,
      calibrationPairs: [
        { surveyX: "5", surveyY: "5", pathX: "0", pathY: "0" },
        { surveyX: "5", surveyY: "5", pathX: "1", pathY: "0" },
      ],
    };
    expect(validateDraft(surveyDeg)["calibration.survey_points"]).toContain("重合");
    const pathDeg: FormDraft = {
      ...calibratedDraft,
      calibrationPairs: [
        { surveyX: "0", surveyY: "0", pathX: "2", pathY: "2" },
        { surveyX: "1", surveyY: "0", pathX: "2", pathY: "2" },
      ],
    };
    expect(validateDraft(pathDeg)["calibration.path_points"]).toContain("重合");
  });

  it("残差阈值必须为有限正数", () => {
    for (const bad of ["0", "-1", "NaN", "Infinity", ""]) {
      const d: FormDraft = { ...calibratedDraft, maxRmsError: bad };
      expect(validateDraft(d)["calibration.max_rms_error"], `bad=${bad}`).toBeTruthy();
    }
  });
});
