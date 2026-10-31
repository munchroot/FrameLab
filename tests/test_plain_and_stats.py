# -*- coding: utf-8 -*-
"""
纯逻辑单元测试（无 Qt）：小白话术映射表 + 帧率统计口径、阈值热更新广播。

运行： python tests/test_plain_and_stats.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

import numpy as np                                             # noqa: E402

from framelab import consts                                    # noqa: E402
from framelab.models import DiffRow, FrameRecord               # noqa: E402
from framelab.stats import (bottleneck, drop_flags, dropped_verdict, fps_verdict,
                            grade, local_median_fps, smooth_series, stutter_flags,
                            vram_bar_text, window_stats)          # noqa: E402
from framelab.ui import plain                                  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("OK  " if cond else "FAIL", name, detail))
    if not cond:
        FAILS.append(name)


def row(dim, verdict="x", sev="DANGER", cur="", delta=""):
    return DiffRow(dim, cur, "base", delta, verdict, sev)


def main():
    print("== 小白话术映射表（改进建议书 四.1 的 4 条硬性映射）==")
    cases = (
        ("显存溢出 (溢出到系统内存)", "贴图/纹理质量"),
        ("磁盘 I/O 资产流式加载", "固态硬盘(SSD)"),
        ("着色器编译活动 (±1 秒窗口)", "无需操作"),
        ("CPU 核心状态 (降频 / CPU0 拥堵)", "同屏人数"),
    )
    for dim, must in cases:
        info = plain.explain([row(dim)])
        check("%s → 含「%s」" % (dim.split()[0], must), must in info["text"], info["text"])

    print("== 优先级：越上游的病因越先解释 ==")
    rows = [row("页面文件换入 & 缺页嫌疑进程", sev="WARN"),
            row("显存溢出 (溢出到系统内存)")]
    info = plain.explain(rows)
    check("显存优先于换页", "显存" in info["text"], info["text"])
    rows2 = [row("GPU 忙时比 (显卡饥饿判定)", sev="WARN"),
             row("磁盘 I/O 资产流式加载")]
    check("磁盘 I/O 优先于 GPU 忙时比", "硬盘" in plain.explain(rows2)["text"])

    print("== 无异常时给安心文案 ==")
    ok = plain.explain([row("显存溢出 (溢出到系统内存)", sev="NORMAL"),
                        row("GPU 忙时比 (显卡饥饿判定)", sev="NORMAL")])
    check("全 NORMAL → 绿", ok["level"] == "ok", ok["text"])

    print("== 病历卡三块 ==")
    f1 = FrameRecord(10, 1.0, 0.0, "g.exe", 1, 45.0)
    f0 = FrameRecord(9, 0.98, 0.0, "g.exe", 1, 8.0)
    card = plain.mini_card(f1, f0, [row("显存溢出 (溢出到系统内存)", cur="非本地 512 MB")], "B")
    check("① 暴跌描述", "从 125 帧" in card["drop_text"] and "22 帧" in card["drop_text"],
          card["drop_text"])
    check("② 含具体溢出量", "512 MB" in card["cause_text"], card["cause_text"])
    check("③ 给出一招解决", "贴图" in card["advice_text"], card["advice_text"])

    print("== 帧率统计口径 ==")
    frames = []
    slow = (100, 101, 102, 103, 104)      # 2.5% 的帧暴跌 → 会被 P99 抓到
    for i in range(200):
        ft = 1000.0 / 120.0
        if i in slow:
            ft = 1000.0 / 20.0            # 单帧暴跌 → 一次顿挫
        frames.append(FrameRecord(i, i * 0.01, 0.0, "g.exe", 1, ft))
    st = window_stats(frames)
    check("平均帧 ≈ 120 附近（受暴跌帧拖累）", 90 < st["avg_fps"] < 125, "%.1f" % st["avg_fps"])
    check("最高帧 = 120", abs(st["max_fps"] - 120.0) < 0.6, "%.1f" % st["max_fps"])
    check("1% Low 远低于平均（暴跌被计入）", st["low1_fps"] < st["avg_fps"] * 0.5,
          "%.1f" % st["low1_fps"])
    # 【v4.1 新口径】漏帧 = 与「最近 120 帧局部中位数帧率」比：
    # 120fps 基准下 20fps 既满足相对跌幅 ≥35%，帧耗时跳变也 ≥8ms → 仍应被抓到。
    check("漏帧检出 5 次（严格双门控口径）", st["dropped"] == 5, str(st["dropped"]))
    check("漏帧占比 ≈ 2.5%", abs(st["dropped_pct"] - 2.5) < 0.6, "%.2f%%" % st["dropped_pct"])

    print("== v4.1 顿挫新口径：噪声不报、暴跌才报 ==")
    noise = []
    for i in range(200):
        ft = 1000.0 / 100.0
        if i == 100:
            ft = 1000.0 / 98.0            # 100 → 98：商业软件也不该报
        noise.append(FrameRecord(i, i * 0.01, 0.0, "g.exe", 1, ft))
    sn = window_stats(noise)
    check("100→98 帧不算顿挫", sn["dropped"] == 0, "n=%d" % sn["dropped"])
    check("window_stats 新增 stutter 家族键",
          sn["stutter_now"] is False and sn["smooth"] is True
          and len(sn["flags"]) == sn["window_n"]
          and 99 < sn["local_median_fps"] < 101,
          "med=%.1f" % sn["local_median_fps"])
    check("drop_flags 与 stutter_flags 同口径（旧 2.5×/40% 口径已废弃）",
          np.array_equal(drop_flags(noise), stutter_flags(noise)))
    check("local_median_fps 与输入等长", local_median_fps(noise).size == len(noise))
    check("vram_bar_text 百分比被 clamp", vram_bar_text(12 * 1024, 1024)[1] <= 100.0)
    check("VRAM/GD 新阈值已登记热更新",
          all(n in consts.LIVE_CONST_NAMES for n in
              ("VRAM_SPILL_MIN_RATIO", "VRAM_SPILL_MIN_MB", "VRAM_SPILL_DELTA_MB",
               "GPU_DOWNCLOCK_MIN_BASE_MHZ", "GPU_DOWNCLOCK_MIN_3D_LOAD",
               "GPU_CLK_DROP_RATIO"))
          and abs(consts.GPU_CLK_DROP_RATIO - 0.20) < 1e-9,
          str(consts.GPU_CLK_DROP_RATIO))

    print("== 评级与文案 ==")
    check("S：120 帧 0.3% 漏帧", grade(120, 0.3) == "S")
    check("A：90 帧 1% 漏帧", grade(90, 1.0) == "A")
    check("B：55 帧 3% 漏帧", grade(55, 3.0) == "B")
    check("C：40 帧 3% 漏帧", grade(40, 3.0) == "C")
    check("漏帧超 5% 降级到 C", grade(120, 6.0) == "C")
    check("1% Low 过低不给 S", grade(120, 0.1, low1_fps=40.0) == "A")
    check("平均帧文案三档", (fps_verdict(144), fps_verdict(75), fps_verdict(30))
          == ("电竞级丝滑", "流畅游玩", "建议降画质"))
    check("漏帧卡片文案", dropped_verdict(0)[0] == "零漏帧" and dropped_verdict(9)[1] == "bad")

    print("== 平滑不改变长度 / 不漏帧判断边界 ==")
    x, y = smooth_series([1, 2, 3, 4, 5], [1, 5, 1, 5, 1], 3)
    check("平滑后长度一致", len(x) == 5 and len(y) == 5)
    fl = drop_flags([FrameRecord(i, i, 0.0, "g", 1, 10.0) for i in range(10)])
    check("稳定帧无漏帧", not fl.any())

    print("== 瓶颈天平 ==")
    bn = bottleneck(None, 98.0, 35.0)
    check("显卡满载判定", bn["side"] == "gpu" and "显卡已满载" in bn["text"], bn["text"])
    bn2 = bottleneck(None, 40.0, 92.0)
    check("CPU 瓶颈判定", bn2["side"] == "cpu" and "CPU" in bn2["text"], bn2["text"])

    print("== 阈值热更新会广播到所有模块（拆分后的关键回归点）==")
    from framelab import analysis, config
    from framelab.engines import hardware
    cfg = config.Config()
    cfg.set("spill_threshold", 32 << 20)
    cfg.apply_to_globals()
    check("consts 自身更新", consts.SPILL_THRESHOLD == 32 << 20, str(consts.SPILL_THRESHOLD))
    check("engines.hardware 副本更新", hardware.SPILL_THRESHOLD == 32 << 20,
          str(hardware.SPILL_THRESHOLD))
    cfg.set("spill_threshold", 64 << 20)
    cfg.apply_to_globals()
    check("改回默认值同样生效", consts.SPILL_THRESHOLD == 64 << 20)

    print()
    if FAILS:
        print("FAILED: %d -> %s" % (len(FAILS), FAILS))
        return 1
    print("ALL PURE-LOGIC CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
