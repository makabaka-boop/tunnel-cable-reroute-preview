import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { PrecheckResponse, ReroutePreview } from "./types";

/**
 * 一次性改线预览的前端核对（fetch 桩，不依赖真实 API）：
 * - 原线/候选线 SVG 与区间详情在同一快照内切换、视图一致；
 * - 消除/新增/仍存在风险摘要渲染；
 * - 取消预览、预览校验失败、在途预览乱序返回均不覆盖已保存原方案；
 * - 正式提交后旧预览作废。
 */

function makeCandidate(feasible: boolean): PrecheckResponse {
  return {
    feasible,
    cable_radius: 5,
    nodes: [
      { x: -100, y: 0 },
      { x: -50, y: 31 },
      { x: 50, y: 31 },
      { x: 100, y: 0 },
    ],
    circles: [{ center: { x: 0, y: 15 }, radius: 10, expanded_radius: 15 }],
    collision_count: feasible ? 0 : 1,
    first_collision: feasible
      ? null
      : {
          segment_index: 1,
          circle_index: 0,
          nearest: { x: 0, y: 31 },
          distance: 16,
          expanded_radius: 15,
          circle_center: { x: 0, y: 15 },
          circle_radius: 10,
          cable_radius: 5,
        },
    collisions: feasible
      ? []
      : [
          {
            segment_index: 1,
            circle_index: 0,
            nearest: { x: 0, y: 31 },
            distance: 16,
            expanded_radius: 15,
            circle_center: { x: 0, y: 15 },
            circle_radius: 10,
            cable_radius: 5,
          },
        ],
    intrusion_intervals: [],
    compound_intrusion_segments: [],
    calibration: null,
  };
}

function makeOriginal(): PrecheckResponse {
  return {
    feasible: false,
    cable_radius: 5,
    nodes: [
      { x: -100, y: 0 },
      { x: 100, y: 0 },
    ],
    circles: [{ center: { x: 0, y: 15 }, radius: 10, expanded_radius: 15 }],
    collision_count: 1,
    first_collision: {
      segment_index: 0,
      circle_index: 0,
      nearest: { x: 0, y: 0 },
      distance: 15,
      expanded_radius: 15,
      circle_center: { x: 0, y: 15 },
      circle_radius: 10,
      cable_radius: 5,
    },
    collisions: [
      {
        segment_index: 0,
        circle_index: 0,
        nearest: { x: 0, y: 0 },
        distance: 15,
        expanded_radius: 15,
        circle_center: { x: 0, y: 15 },
        circle_radius: 10,
        cable_radius: 5,
      },
    ],
    intrusion_intervals: [
      {
        circle_index: 0,
        entry_segment_index: 0,
        exit_segment_index: 0,
        entry: { x: 0, y: 0 },
        exit: { x: 0, y: 0 },
        start_mileage: 100,
        end_mileage: 100,
        length: 0,
        pieces: [
          {
            segment_index: 0,
            circle_index: 0,
            entry: { x: 0, y: 0 },
            exit: { x: 0, y: 0 },
            start_mileage: 100,
            end_mileage: 100,
            length: 0,
          },
        ],
      },
    ],
    compound_intrusion_segments: [],
    calibration: null,
  };
}

function makePreviewResponse(
  candidateFeasible = true,
  overrides: Partial<ReroutePreview> = {},
): PrecheckResponse {
  const original = makeOriginal();
  const preview: ReroutePreview = {
    start_node_index: 0,
    end_node_index: 1,
    replacement_nodes: [
      { x: -50, y: 31 },
      { x: 50, y: 31 },
    ],
    junction_start: { x: -100, y: 0 },
    junction_end: { x: 100, y: 0 },
    prefix_length: 0,
    original_junction_end_mileage: 200,
    candidate_junction_end_mileage: 222.8,
    mileage_shift: 22.8,
    candidate: makeCandidate(candidateFeasible),
    circle_risks: [
      {
        circle_index: 0,
        status: "removed",
        removed: [
          {
            circle_index: 0,
            segment_index: 0,
            entry: { x: 0, y: 0 },
            exit: { x: 0, y: 0 },
            start_mileage: 100,
            end_mileage: 100,
            length: 0,
          },
        ],
        added: [],
        remaining: [],
      },
    ],
    summary: {
      circle_count: 1,
      removed_event_count: 1,
      added_event_count: 0,
      remaining_event_count: 0,
      circles_removed: 1,
      circles_added: 0,
      circles_remaining: 0,
      circles_reduced: 0,
      circles_increased: 0,
      circles_replaced: 0,
      original_total_length: 200,
      candidate_total_length: 222.8,
      mileage_shift: 22.8,
    },
    ...overrides,
  };
  return { ...original, reroute_preview: preview };
}

function jsonResponse(data: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => data,
  };
}

async function enablePreview() {
  await userEvent.click(screen.getByTestId("reroute-enabled"));
}

describe("一次性改线预览", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("预览成功后在同一快照切换原线/候选线：SVG 与区间详情同步切换", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(makePreviewResponse(true))),
    );
    render(<App />);
    await enablePreview();
    await userEvent.click(screen.getByTestId("reroute-preview"));

    // 默认展示原线
    await waitFor(() =>
      expect(screen.getByTestId("reroute-toolbar")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument();
    expect(screen.getByTestId("interval-panel")).toBeInTheDocument();
    expect(screen.getByTestId("reroute-diff-panel")).toBeInTheDocument();
    // 风险摘要：圈0 已消除
    expect(screen.getByTestId("circle-risk-0-status").textContent).toContain("已消除");
    expect(
      screen.getByTestId("risk-removed-c0-s0").textContent,
    ).toContain("里程 100");

    // 原线 SVG：2 个节点
    let node1 = screen.queryByTestId("node-1");
    expect(node1?.tagName.toLowerCase()).toBe("circle");

    // 切到候选线：可敷设横幅、原线区间详情消失、SVG 变 4 个节点
    await userEvent.click(screen.getByTestId("view-candidate"));
    expect(screen.queryByTestId("banner-collision")).not.toBeInTheDocument();
    expect(screen.getByTestId("banner-ok").textContent).toContain("候选线可敷设");
    expect(screen.queryByTestId("interval-panel")).not.toBeInTheDocument();
    // 风险摘要只在原线视图
    expect(screen.queryByTestId("reroute-diff-panel")).not.toBeInTheDocument();
    // SVG 已换为候选节点（4 个）
    expect(screen.getByTestId("node-3")).toBeInTheDocument();
    expect(
      document.querySelector('[data-testid="intrusion-layer"]')?.children.length,
    ).toBe(0);

    // 切回原线：碰撞横幅与区间详情、风险摘要一起回来，SVG 恢复 2 节点
    await userEvent.click(screen.getByTestId("view-original"));
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument();
    expect(screen.getByTestId("interval-panel")).toBeInTheDocument();
    expect(screen.getByTestId("reroute-diff-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("node-3")).not.toBeInTheDocument();
    expect(screen.getByTestId("node-1")).toBeInTheDocument();
  });

  it("取消预览：工具栏消失，已保存原方案仍在，再取消不影响场景", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(makePreviewResponse(true))),
    );
    render(<App />);
    await enablePreview();
    await userEvent.click(screen.getByTestId("reroute-preview"));
    await waitFor(() =>
      expect(screen.getByTestId("reroute-diff-panel")).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByTestId("reroute-cancel"));
    expect(screen.queryByTestId("reroute-toolbar")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reroute-diff-panel")).not.toBeInTheDocument();
    // 已保存原方案结论仍在
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument();
    expect(screen.getByTestId("interval-panel")).toBeInTheDocument();
    expect(screen.getByTestId("scene")).toBeInTheDocument();
    // 无预览时不存在视图切换
    expect(screen.queryByTestId("view-candidate")).not.toBeInTheDocument();
  });

  it("预览本地校验失败：显示预览错误但不覆盖已保存原方案", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(makePreviewResponse(true)));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    // 先保存原方案（reroute 未启用，顶层结果）
    await userEvent.click(screen.getByTestId("submit"));
    await waitFor(() =>
      expect(screen.getByTestId("banner-collision")).toBeInTheDocument(),
    );

    await enablePreview();
    // 制造非法下标（start > end）
    await userEvent.clear(screen.getByTestId("reroute-start"));
    await userEvent.type(screen.getByTestId("reroute-start"), "1");
    await userEvent.clear(screen.getByTestId("reroute-end"));
    await userEvent.type(screen.getByTestId("reroute-end"), "0");
    const callsBefore = fetchMock.mock.calls.length;
    await userEvent.click(screen.getByTestId("reroute-preview"));

    // 没有发请求
    expect(fetchMock.mock.calls.length).toBe(callsBefore);
    expect(screen.getByTestId("reroute-banner-error")).toBeInTheDocument();
    // 已保存原方案仍在（工具栏未出现，场景仍是原线）
    expect(screen.queryByTestId("reroute-toolbar")).not.toBeInTheDocument();
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument();
    expect(screen.getByTestId("scene")).toBeInTheDocument();
  });

  it("预览请求 422：字段错误展示，已保存原方案不被覆盖", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(makePreviewResponse(true)))
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        json: async () => ({
          ok: false,
          errors: { "reroute.end_node_index": "结束锚点零长（模拟422）" },
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await enablePreview();
    await userEvent.click(screen.getByTestId("reroute-preview"));
    await waitFor(() =>
      expect(screen.getByTestId("reroute-diff-panel")).toBeInTheDocument(),
    );

    // 再次点预览（后端 422）：旧快照工具栏保留原方案，错误横幅出现
    await userEvent.click(screen.getByTestId("reroute-preview"));
    await waitFor(() =>
      expect(screen.getByTestId("reroute-banner-error")).toBeInTheDocument(),
    );
    expect(
      screen.getByTestId("err-reroute.end_node_index").textContent,
    ).toContain("零长");
    // 原方案视图仍可用
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument();
    expect(screen.getByTestId("scene")).toBeInTheDocument();
  });

  it("在途预览响应乱序：用户取消后迟到的旧响应不能恢复预览工具栏", async () => {
    const resolvers: Array<(v: unknown) => void> = [];
    const fetchMock = vi.fn(
      () =>
        new Promise((resolve) => {
          resolvers.push(resolve);
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await enablePreview();
    await userEvent.click(screen.getByTestId("reroute-preview"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // 在途期间取消预览
    await userEvent.click(screen.getByTestId("reroute-cancel"));
    expect(screen.queryByTestId("reroute-toolbar")).not.toBeInTheDocument();

    // 旧响应迟到：不能把预览工具栏/候选视图画回来
    resolvers[0](jsonResponse(makePreviewResponse(true)));
    await Promise.resolve();
    await Promise.resolve();
    expect(screen.queryByTestId("reroute-toolbar")).not.toBeInTheDocument();
    expect(screen.queryByTestId("view-candidate")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reroute-diff-panel")).not.toBeInTheDocument();
  });

  it("旧预览未返回时再次预览：先返回的旧响应不能覆盖新预览", async () => {
    const resolvers: Array<(v: unknown) => void> = [];
    const fetchMock = vi.fn(
      () =>
        new Promise((resolve) => {
          resolvers.push(resolve);
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await enablePreview();

    // 第一次预览（在途）
    await userEvent.click(screen.getByTestId("reroute-preview"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    // 第二次预览（在途，序号更新）
    await userEvent.click(screen.getByTestId("reroute-preview"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    // 第二次（新）先返回：候选可敷设
    resolvers[1](jsonResponse(makePreviewResponse(true)));
    await waitFor(() =>
      expect(screen.getByTestId("reroute-toolbar")).toBeInTheDocument(),
    );

    // 第一次（旧）后返回，其候选是碰撞态——不得覆盖新快照
    resolvers[0](jsonResponse(makePreviewResponse(false)));
    await Promise.resolve();
    await Promise.resolve();
    // 默认原线视图；切到候选线必须仍是新响应的可敷设
    await userEvent.click(screen.getByTestId("view-candidate"));
    expect(screen.getByTestId("banner-ok")).toBeInTheDocument();
    expect(screen.queryByTestId("banner-collision")).not.toBeInTheDocument();
  });

  it("正式提交（reroute 关闭）成功后旧预览作废，交互回到无预览状态", async () => {
    const withPreview = makePreviewResponse(true);
    // 正式提交：reroute 未启用 → 响应无 reroute_preview
    const plainOriginal = { ...makeOriginal() };
    delete (plainOriginal as Partial<PrecheckResponse>).reroute_preview;
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(withPreview))
      .mockResolvedValueOnce(jsonResponse(plainOriginal));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await enablePreview();
    await userEvent.click(screen.getByTestId("reroute-preview"));
    await waitFor(() =>
      expect(screen.getByTestId("reroute-diff-panel")).toBeInTheDocument(),
    );

    // 关闭改线并正式提交
    await userEvent.click(screen.getByTestId("reroute-enabled"));
    await userEvent.click(screen.getByTestId("submit"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("reroute-toolbar")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reroute-diff-panel")).not.toBeInTheDocument();
    // 原线结论正常
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument();
  });

  it("仍存在事件展示原线/候选双份里程与平移", async () => {
    const resp = makePreviewResponse(true, {
      circle_risks: [
        {
          circle_index: 0,
          status: "remaining",
          removed: [],
          added: [],
          remaining: [
            {
              circle_index: 0,
              original_segment_index: 2,
              candidate_segment_index: 3,
              entry: { x: 28, y: 0 },
              exit: { x: 30, y: 0 },
              original_start_mileage: 26,
              original_end_mileage: 28,
              candidate_start_mileage: 46.69,
              candidate_end_mileage: 48.69,
              length: 2,
              mileage_shift: 20.69,
            },
          ],
        },
      ],
      summary: {
        circle_count: 1,
        removed_event_count: 0,
        added_event_count: 0,
        remaining_event_count: 1,
        circles_removed: 0,
        circles_added: 0,
        circles_remaining: 1,
        circles_reduced: 0,
        circles_increased: 0,
        circles_replaced: 0,
        original_total_length: 200,
        candidate_total_length: 220.69,
        mileage_shift: 20.69,
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(resp)));
    render(<App />);
    await enablePreview();
    await userEvent.click(screen.getByTestId("reroute-preview"));
    await waitFor(() =>
      expect(screen.getByTestId("reroute-diff-panel")).toBeInTheDocument(),
    );
    const row = screen.getByTestId("risk-remaining-c0-s2").textContent ?? "";
    expect(row).toContain("原线段 #2");
    expect(row).toContain("候选段 #3");
    expect(row).toContain("26");
    expect(row).toContain("46.69");
    expect(row).toContain("后缀平移 20.69");
    expect(
      screen.getByTestId("circle-risk-0-status").textContent,
    ).toContain("仍存在");
    expect(screen.getByTestId("reroute-summary-events").textContent).toContain(
      "仍存在 1 处",
    );
  });
});
