# -*- coding: utf-8 -*-
"""
v4.1 判定算法/阈值修正的纯逻辑单元测试（无 Qt）。

覆盖改进建议书 二 / 三 / 五 三条修正：
  1) 桌面误报「爆显存」：专用显存 <88% 或溢出量 <256MB 或增量 <64MB 一律不得 DANGER；
  2) 显卡待机低频（400MHz→300MHz）误报降频：3D 高频门控；
  3) 顿挫双门控：局部中位数基准 + 相对跌幅 ≥35% + 跳变 ≥8ms + 85FPS 绝对豁免；
  4) 显存进度条文本百分比 clamp（1008.40% Bug 的根因）；
  5) 局部中位数帧率的增量缓存正确性（一次性 vs 分批喂入必须完全一致）。

运行： python tests/test_algofix.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

import numpy as np                                              # noqa: E402

from framelab import consts                                     # noqa: E402
from framelab.analysis import FrameStore, SnapshotDiffInspector  # noqa: E402
from framelab.models import FrameRecord, HardwareSnapshot       # noqa: E402
from framelab import stats                                       # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("OK  " if cond else "FAIL", name, detail))
    if not cond:
        FAILS.append(name)


# ==============================================================================
# 工具：构造「平稳基线 + 单个异常帧」的 store
# ==============================================================================
TOTAL_VRAM = 12.0 * (1 << 30)      # 12GB 显存


def _snap(seq, ts, ded_ratio, nonlocal_mb, clk, load3d):
    s = HardwareSnapshot(seq=seq)
    s.ts = ts
    s.cpu_usage = [30.0] * 8
    s.cpu_freq = [5100.0] * 8
    s.cpu0_usage = 30.0
    s.cpu_total = 30.0
    s.gpu_eng = {"3D": load3d}
    s.gpu_clock = clk
    s.gpu_temp = 85
    s.vram_total = TOTAL_VRAM
    s.vram_dedicated = TOTAL_VRAM * ded_ratio
    s.vram_nonlocal = int(nonlocal_mb * 1048576)
    return s


def build_case(ded_ratio, base_nonlocal_mb, spike_nonlocal_mb,
               base_clk=2400.0, spike_clk=None, spike_load=70.0, spike_ft=16.7):
    """
    80 轮平稳快照/帧先把基线喂稳，再追加 1 个异常快照 + 1 个异常帧
    （异常快照时间更晚，所以异常帧必绑定到它）。
    """
    st = FrameStore()
    for i in range(80):
        st.add_snapshot(_snap(i + 1, 1000.0 + i * 0.05, ded_ratio, base_nonlocal_mb,
                              base_clk, 70.0))
    for i in range(80):
        st.add_frame(FrameRecord(i + 1, i * 0.05, 1000.0 + i * 0.05 + 0.01,
                                 "G.exe", 1, 16.7))
    st.update_baseline(list(st.frames))
    ts = 1000.0 + 80 * 0.05
    st.add_snapshot(_snap(81, ts, ded_ratio, spike_nonlocal_mb,
                          base_clk if spike_clk is None else spike_clk, spike_load))
    f = FrameRecord(81, 80 * 0.05, ts + 0.01, "G.exe", 1, spike_ft)
    st.add_frame(f)
    return st, f


def _row(store, frame, dim_prefix):
    for r in SnapshotDiffInspector.inspect(frame, store):
        if r.dim.startswith(dim_prefix):
            return r
    return None


# ==============================================================================
# 1. 爆显存判定（改进建议书 二）
# ==============================================================================
def test_vram_spill():
    print("== 1. 爆显存：专用显存满载率 + 非本地绝对值 + 增量 三重门控 ==")
    st, f = build_case(0.15, 0.0, 700.0)
    r = _row(st, f, "显存溢出")
    check("桌面只用了 15% 显存 + 非本地 700MB → 不得 DANGER",
          r.severity != "DANGER", "%s | %s" % (r.severity, r.verdict))
    check("桌面 15% 场景给出「显存容量充足」", "容量充足" in r.verdict, r.verdict)

    st, f = build_case(0.90, 0.0, 700.0)
    r = _row(st, f, "显存溢出")
    check("专用 90% + 非本地 700MB + 较基线 +700MB → DANGER",
          r.severity == "DANGER", "%s | %s" % (r.severity, r.verdict))
    check("DANGER 文案保留「较基线 +X MB」佐证", "较基线 +700 MB" in r.verdict, r.verdict)
    check("DANGER 文案保留「专用显存已用 X%」佐证", "专用显存已用 90%" in r.verdict, r.verdict)

    st, f = build_case(0.90, 0.0, 100.0)
    r = _row(st, f, "显存溢出")
    check("专用 90% + 非本地仅 100MB（<256MB）→ 不得 DANGER",
          r.severity != "DANGER", "%s | %s" % (r.severity, r.verdict))

    st, f = build_case(0.90, 700.0, 700.0)
    r = _row(st, f, "显存溢出")
    check("专用 90% + 非本地 700MB 但较基线增量 0 → 不得 DANGER",
          r.severity != "DANGER", "%s | %s" % (r.severity, r.verdict))

    st, f = build_case(0.95, 0.0, 10.0)
    r = _row(st, f, "显存溢出")
    check("专用 95% 但溢出量太小 → 只给 WARN「显存占用临界」",
          r.severity == "WARN" and "临界" in r.verdict, "%s | %s" % (r.severity, r.verdict))

    print("  -- 会话级 is_vram_spill 与诊断卡同一套门控 --")
    # 会话级统计只统计「帧耗时 ≥ 基线×1.8」的卡顿帧，所以这里让异常帧本身是卡顿帧
    st, f = build_case(0.15, 0.0, 700.0, spike_ft=33.0)
    check("会话级统计：桌面 15% 不算溢出", "显存溢出" not in st.session_correlations())
    st, f = build_case(0.90, 0.0, 700.0, spike_ft=33.0)
    corr = st.session_correlations()
    check("会话级统计：90% + 700MB 计入溢出诱因",
          "显存溢出" in corr, str(sorted(corr.keys())))
    st, f = build_case(0.90, 700.0, 700.0, spike_ft=33.0)
    # 【口径说明】is_vram_spill 是**按快照**判定的谓词（会话级相关性统计用），
    # 门控 = vram_total>0 + 专用占比 ≥88% + 非本地 ≥max(256MB, SPILL_THRESHOLD)；
    # 「较基线增量 ≥64MB」只有诊断卡第 4 行（单帧 vs 基线）才做。
    check("会话级 is_vram_spill 只看占比与绝对量（增量门控属单帧诊断卡）",
          "显存溢出" in st.session_correlations())
    st, f = build_case(0.87, 0.0, 700.0, spike_ft=33.0)
    check("会话级统计：专用占比 87%（<88%）不算溢出",
          "显存溢出" not in st.session_correlations())

    print("  -- 阈值常量登记（Config 热更新必须广播到 analysis）--")
    for name in ("VRAM_SPILL_MIN_RATIO", "VRAM_SPILL_MIN_MB", "VRAM_SPILL_DELTA_MB",
                 "GPU_DOWNCLOCK_MIN_BASE_MHZ", "GPU_DOWNCLOCK_MIN_3D_LOAD",
                 "GPU_CLK_DROP_RATIO"):
        check("%s 已登记 LIVE_CONST_NAMES" % name, name in consts.LIVE_CONST_NAMES)
    from framelab import analysis
    check("GPU_CLK_DROP_RATIO 已改为 0.20", abs(consts.GPU_CLK_DROP_RATIO - 0.20) < 1e-9,
          str(consts.GPU_CLK_DROP_RATIO))
    check("analysis 侧取到热更新副本",
          abs(analysis.GPU_CLK_DROP_RATIO - 0.20) < 1e-9
          and analysis.VRAM_SPILL_MIN_MB == 256.0
          and analysis.GPU_DOWNCLOCK_MIN_3D_LOAD == 60.0,
          "%.2f / %.0f / %.0f" % (analysis.GPU_CLK_DROP_RATIO,
                                  analysis.VRAM_SPILL_MIN_MB,
                                  analysis.GPU_DOWNCLOCK_MIN_3D_LOAD))


# ==============================================================================
# 2. 显卡待机低频误报降频（改进建议书 三）
# ==============================================================================
def test_gpu_downclock():
    print("== 2. 撞墙降频：3D 高频门控（基线 ≥1200MHz 且 3D 负载 ≥60% 且本帧卡顿）==")
    st, f = build_case(0.5, 0.0, 0.0, base_clk=400.0, spike_clk=300.0, spike_ft=33.0)
    r = _row(st, f, "GPU 核心频率")
    check("基线 400MHz（省电待机档）→ 绝不 DANGER", r.severity != "DANGER",
          "%s | %s" % (r.severity, r.verdict))
    check("省电待机档给出明确说明", "省电待机" in r.verdict, r.verdict)

    st, f = build_case(0.5, 0.0, 0.0, base_clk=2400.0, spike_clk=1800.0,
                       spike_load=20.0, spike_ft=33.0)
    r = _row(st, f, "GPU 核心频率")
    check("基线 2400MHz 但 3D 负载仅 20% → 不得 DANGER（正常节能降频）",
          r.severity != "DANGER", "%s | %s" % (r.severity, r.verdict))

    st, f = build_case(0.5, 0.0, 0.0, base_clk=2400.0, spike_clk=1800.0,
                       spike_load=70.0, spike_ft=33.0)
    r = _row(st, f, "GPU 核心频率")
    check("基线 2400MHz + 3D 70% + 1800MHz(-25%) + 本帧卡顿 → DANGER",
          r.severity == "DANGER", "%s | %s" % (r.severity, r.verdict))
    check("DANGER 文案给出撞墙降频结论", "撞墙降频" in r.verdict, r.verdict)

    st, f = build_case(0.5, 0.0, 0.0, base_clk=2400.0, spike_clk=1800.0,
                       spike_load=70.0, spike_ft=16.7)
    r = _row(st, f, "GPU 核心频率")
    check("同样降频但本帧不卡（16.7ms）→ 不得 DANGER", r.severity != "DANGER",
          "%s | %s" % (r.severity, r.verdict))

    st, f = build_case(0.5, 0.0, 0.0, base_clk=1100.0, spike_clk=700.0,
                       spike_load=70.0, spike_ft=33.0)
    r = _row(st, f, "GPU 核心频率")
    check("基线 1100MHz（<1200 门控）→ 归 NORMAL", r.severity == "NORMAL",
          "%s | %s" % (r.severity, r.verdict))

    st, f = build_case(0.5, 0.0, 0.0, base_clk=2400.0, spike_clk=2200.0,
                       spike_load=70.0, spike_ft=33.0)
    r = _row(st, f, "GPU 核心频率")
    check("高频高负载但只回落 8%（未达 20%）→ WARN 而非 DANGER",
          r.severity == "WARN", "%s | %s" % (r.severity, r.verdict))

    print("  -- gpu_3d_load_pct（analysis/hardware 共用口径）--")
    check("hw is None → 0.0", stats.gpu_3d_load_pct(None) == 0.0)
    s = HardwareSnapshot(seq=1)
    s.gpu_eng = {"3D": 0.72, "Copy": 0.10}
    check("0~1 的比例自动 ×100", abs(stats.gpu_3d_load_pct(s) - 72.0) < 1e-9,
          "%.1f" % stats.gpu_3d_load_pct(s))
    s.gpu_eng = {"Copy": 0.35, "VideoDecode": 0.10}
    check("无 3D 引擎时取所有引擎最大值", abs(stats.gpu_3d_load_pct(s) - 35.0) < 1e-9,
          "%.1f" % stats.gpu_3d_load_pct(s))
    s.gpu_eng = {}
    check("无引擎数据 → 0.0", stats.gpu_3d_load_pct(s) == 0.0)


# ==============================================================================
# 3. 顿挫双门控（改进建议书 五）
# ==============================================================================
def _frames(fts):
    return [FrameRecord(i + 1, i * 0.01, i * 0.01, "g.exe", 1, ft)
            for i, ft in enumerate(fts)]


def test_stutter():
    print("== 3. 顿挫：局部中位数基准 + A(≥35%) 且 B(≥8ms) 且非 C(≥85FPS 豁免) ==")
    print("   常量：STUTTER_WINDOW=%d REL=%.2f JUMP=%.1f EXEMPT=%.0f"
          % (stats.STUTTER_WINDOW, stats.STUTTER_REL_FACTOR,
             stats.STUTTER_FT_JUMP_MS, stats.STUTTER_EXEMPT_FPS))

    fts = [10.0] * 200
    fts[100] = 1000.0 / 98.0                      # 100 → 98 帧
    fl = stats.stutter_flags(_frames(fts))
    check("100fps 平稳中一帧 98fps → 0 个标记", int(fl.sum()) == 0, "n=%d" % fl.sum())

    fts = [1000.0 / 204.0] * 200
    fts[100] = 1000.0 / 101.0                     # 204 → 101 帧（9.9ms）
    fl = stats.stutter_flags(_frames(fts))
    check("204fps 掉到 101fps（9.9ms）→ 0 个标记（85FPS 绝对豁免）",
          int(fl.sum()) == 0, "n=%d" % fl.sum())

    fts = [10.0] * 200
    fts[100] = 20.0                               # 100 → 50 帧（20ms，跳变 10ms）
    fl = stats.stutter_flags(_frames(fts))
    check("100fps 掉到 50fps（跳变 10ms ≥8ms）→ 有标记",
          int(fl.sum()) == 1 and bool(fl[100]), "idx=%s" % np.flatnonzero(fl).tolist())
    check("is_stutter_frame 与 stutter_flags 一致",
          stats.is_stutter_frame(_frames(fts), 100) and
          not stats.is_stutter_frame(_frames(fts), 99))

    fts = [10.0] * 200
    fts[100] = 1000.0 / 90.0                      # 100 → 90 帧（跌幅 10%）
    fl = stats.stutter_flags(_frames(fts))
    check("100fps 掉到 90fps → 0 个标记（相对跌幅不足 35%）",
          int(fl.sum()) == 0, "n=%d" % fl.sum())

    fts = [10.0] * 200
    fts[100] = 1000.0 / 60.0                      # 100 → 60 帧（跳变仅 6.67ms）
    fl = stats.stutter_flags(_frames(fts))
    check("100fps 掉到 60fps 但跳变仅 6.67ms（<8ms）→ 0 个标记（双重门控）",
          int(fl.sum()) == 0, "n=%d" % fl.sum())

    fts = [1000.0 / 120.0] * 200
    fts[100] = 1000.0 / 45.0                      # 120 → 45 帧（22.2ms，跳变 13.9ms）
    fl = stats.stutter_flags(_frames(fts))
    check("120fps 掉到 45fps → 有标记",
          int(fl.sum()) == 1 and bool(fl[100]), "n=%d" % fl.sum())

    check("第 0 帧恒不算顿挫", not stats.stutter_flags(_frames([50.0] * 5))[0])

    print("  -- drop_flags 保留函数名/签名，内部改走严格口径 --")
    fts = [10.0] * 200
    fts[100] = 20.0
    fr = _frames(fts)
    check("drop_flags == stutter_flags",
          np.array_equal(stats.drop_flags(fr), stats.stutter_flags(fr)))
    check("count_severe_drop 直接统计新 flags",
          stats.count_severe_drop(stats.drop_flags(fr), stats._fps_of(np.array(fts))) == 1)

    print("  -- window_stats 新键（病历卡据此显隐）--")
    fts = [1000.0 / 120.0] * 300
    fts[299] = 20.0                               # 最后一帧顿挫
    st = stats.window_stats(_frames(fts))
    for k in ("avg_fps", "max_fps", "min_fps", "low1_fps", "low01_fps",
              "low01_degraded", "dropped", "dropped_pct", "grade", "grade_label",
              "grade_hint", "smooth_pct", "n", "window_n"):
        check("window_stats 保留旧键 %s" % k, k in st)
    check("window_stats 新增 flags/stutter_now/local_median_fps/stutter_idx/smooth",
          all(k in st for k in ("flags", "stutter_now", "local_median_fps",
                                "stutter_idx", "smooth")))
    check("flags 与本次处理的 recent 切片等长",
          len(st["flags"]) == st["window_n"] == len(st["flags"]))
    check("最后一帧顿挫 → stutter_now=True 且 smooth=False",
          st["stutter_now"] is True and st["smooth"] is False,
          "stutter_now=%s" % st["stutter_now"])
    check("stutter_idx = 最后一帧 idx", st["stutter_idx"] == 300, str(st["stutter_idx"]))
    check("local_median_fps ≈ 120", abs(st["local_median_fps"] - 120.0) < 1.0,
          "%.1f" % st["local_median_fps"])
    check("dropped 与 flags 计数一致", st["dropped"] == int(st["flags"].sum()))

    st2 = stats.window_stats(_frames([1000.0 / 120.0] * 300))
    check("全程丝滑 → stutter_now=False 且 smooth=True 且 dropped=0",
          st2["stutter_now"] is False and st2["smooth"] is True and st2["dropped"] == 0,
          "dropped=%d" % st2["dropped"])


# ==============================================================================
# 4. 显存进度条文本 / 百分比 clamp（1008.40% Bug 根因）
# ==============================================================================
def test_vram_bar_text():
    print("== 4. vram_bar_text：格式 + 百分比严格 clamp(0,100) ==")
    txt, pct = stats.vram_bar_text(5.2 * 1024, 12.0 * 1024)
    check("文本格式「已用 X / 总计 X (XX%)」",
          txt.startswith("已用 ") and " / 总计 " in txt and txt.endswith("%)"), txt)
    check("正常比例 ≈ 43%", abs(pct - 43.33) < 0.5, "%.2f" % pct)

    txt, pct = stats.vram_bar_text(12 * 1024, 1 * 1024)     # 脏数据：已用 12GB / 总计 1GB
    check("脏数据百分比被 clamp 到 <=100", pct <= 100.0, "%.2f" % pct)
    check("脏数据百分比 = 100", abs(pct - 100.0) < 1e-9, "%.2f" % pct)
    check("脏数据文本不含 >100 的百分数", "1008" not in txt and "1200%" not in txt, txt)

    txt, pct = stats.vram_bar_text(-100.0, 1024.0)
    check("负数占用被 clamp 到 0", pct == 0.0, "%.2f" % pct)

    txt, pct = stats.vram_bar_text(1024.0, 0.0)
    check("容量未知 → (\"容量未知\", 0.0)", txt == "容量未知" and pct == 0.0,
          "%s / %.1f" % (txt, pct))

    txt, pct = stats.vram_bar_text(700.0, 12.0 * 1024, nonlocal_mb=700.0)
    check("可附带非本地溢出量", "非本地" in txt and pct <= 100.0, txt)


# ==============================================================================
# 5. 局部中位数帧率的增量缓存正确性
# ==============================================================================
def test_median_cache():
    print("== 5. local_median_fps：增量缓存与全量重算结果必须完全一致 ==")
    rng = np.random.default_rng(20261026)
    fts = 8.0 + rng.random(500) * 5.0
    fts[250] = 55.0
    fr = _frames(fts)

    # A) 分 5 次按前缀喂入（走增量路径）
    stats._reset_median_cache()
    stats._MED_STAT.update({"full": 0, "incr": 0, "reuse": 0})
    inc = None
    for k in (100, 200, 300, 400, 500):
        inc = stats.local_median_fps(fr[:k], fts[:k])
    stat_inc = dict(stats._MED_STAT)

    # B) 一次性喂 500 帧（清缓存 → 走全量路径）
    stats._reset_median_cache()
    stats._MED_STAT.update({"full": 0, "incr": 0, "reuse": 0})
    one = stats.local_median_fps(fr, fts)
    stat_full = dict(stats._MED_STAT)

    check("分批喂入 == 一次性喂入（np.allclose）", np.allclose(inc, one),
          "maxdiff=%.3g" % float(np.max(np.abs(inc - one))))
    check("分批喂入确实走了增量（incr >= 3）", stat_inc["incr"] >= 3, str(stat_inc))
    check("一次性喂入只做 1 次全量重算", stat_full["full"] == 1, str(stat_full))

    # C) 窗口滑动：整段复用后与全量重算一致
    stats._reset_median_cache()
    gt = stats.local_median_fps(fr[100:], fts[100:])          # 全量基准
    stats._reset_median_cache()
    stats.local_median_fps(fr, fts)                            # 先建立 500 帧缓存
    stats._MED_STAT.update({"full": 0, "incr": 0, "reuse": 0})
    slid = stats.local_median_fps(fr[100:], fts[100:])
    check("窗口滑动复用 == 全量重算", np.allclose(slid, gt),
          "maxdiff=%.3g" % float(np.max(np.abs(slid - gt))))
    check("滑动走了整段复用（reuse >= 1）", stats._MED_STAT["reuse"] >= 1,
          str(stats._MED_STAT))

    # D) 定义自检：第 i 帧 = median(fps[max(0,i-w+1):i+1])
    med = stats.local_median_fps(fr, fts, window=120)
    ok = True
    for i in (0, 1, 50, 119, 120, 251, 499):
        fps = stats._fps_of(fts)
        want = float(np.median(fps[max(0, i - 120 + 1):i + 1]))
        if abs(med[i] - want) > 1e-9:
            ok = False
            break
    check("中位数窗口定义与文档一致", ok, "i=%d got=%.4f want=%.4f" % (i, med[i], want))
    check("返回值与输入等长", med.size == fts.size, "%d/%d" % (med.size, fts.size))

    stats._reset_median_cache()
    check("空输入安全", stats.local_median_fps([], np.zeros(0)).size == 0
          and int(stats.stutter_flags([]).sum()) == 0)


def main():
    test_vram_spill()
    print()
    test_gpu_downclock()
    print()
    test_stutter()
    print()
    test_vram_bar_text()
    print()
    test_median_cache()
    print()
    if FAILS:
        print("FAILED: %d -> %s" % (len(FAILS), FAILS))
        return 1
    print("ALL ALGO-FIX CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
