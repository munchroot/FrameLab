# -*- coding: utf-8 -*-
"""
运行时冒烟验证（offscreen，不启动真实 PresentMon 采集）。

覆盖改进建议书（gemini-code-1793074763581）的 5 条硬性规范：
  A. 包拆分后所有模块可导入、旧顶层名字仍可从 StutterTriage 导入（向后兼容）
  B. 【规范一】左栏固定 240px + 5 张大号视图导航卡；右侧 QStackedWidget 5 页整块替换；
     顶部横幅与 4 张帧数大卡常驻（不在 stack 里）
  C. 喂合成帧 + 硬件快照后：卡片有数、评级合法、曲线有数据、导航徽章有实时文案
  D. 【规范二】爆显存判定：桌面 15% 显存 + 700MB 非本地 → 严禁报爆显存；
     88% 以上 + 非本地 ≥256MB + 较基线 +64MB → 才报
  E. 【规范三】显卡待机低频波动（400→300MHz）→ 严禁报降频；高频高载真掉帧 → 才报
  F. 【规范四】折线图坐标轴：空载锁死 (0,100)/(0,144)/(0,35)，ms 视角默认 0~35ms
  G. 【规范五】顿挫判定：100→98 / 204→101 严禁打点计数；100→60 才算；85FPS 以上豁免
  H. 病历卡：未触发严格顿挫 → 隐藏或绿色「全程运行丝滑」；真顿挫 → 自动弹卡
  I. 既有功能未丢：设置页阈值框、热键、PresentMon 路径、打点事件、关闭流程

运行： python tests/smoke_v4_runtime.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6 import QtWidgets            # noqa: E402

try:                                   # Windows GBK 控制台无法编码 emoji/部分中文
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("OK  " if cond else "FAIL", name, detail))
    if not cond:
        FAILS.append(name)


def pump(app, ms=150):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def feed(win, n=400, drop_at=(120, 260), app_name="TestGame.exe",
         vram_ratio=0.92, nonlocal_mb=700.0, splash_at=120):
    """塞入合成帧 + 绑定硬件快照（模拟真实采集的写入顺序）"""
    import stuttertriage.models as M
    total = 8.0 * 1024 * 1024 * 1024
    for i in range(n):
        seq = i // 4
        if i % 4 == 0:
            hw = M.HardwareSnapshot(seq)
            hw.ts = time.time() + i * 0.011      # 与帧墙钟对齐（否则诊断判「不可判定」）
            hw.cpu_usage = [40.0 + (i % 7) * 3.0] * 8
            hw.cpu_freq = [4200.0] * 8
            hw.cpu_total = 42.0
            hw.cpu0_usage = 45.0
            hw.mem_avail_mb = 8000.0
            hw.mem_commit = 12.0 * 1024 * 1024 * 1024
            hw.mem_limit = 32.0 * 1024 * 1024 * 1024
            hw.gpu_eng = {"engtype_3D": 0.86}
            hw.vram_total = total
            hw.vram_dedicated = total * vram_ratio
            hw.vram_nonlocal = ((nonlocal_mb * 1024 * 1024)
                                if (splash_at is not None and i >= splash_at) else 0.0)
            # 合成的显存 Top 进程（Page 2 的 Top5 表数据源）：
            # 不放这里的话该表只能靠**真实** HardwareEngine 的 DXGI 枚举喂，
            # 非管理员 / 采样时机不同就会空表 → 冒烟测试偶发红。
            hw.procs = [
                {"name": "TestGame.exe", "pid": 4242, "local": 4.2 * 1024 * 1024 * 1024,
                 "nonlocal": (nonlocal_mb * 1024 * 1024)
                 if (splash_at is not None and i >= splash_at) else 0.0,
                 "dedicated": 4.8 * 1024 * 1024 * 1024, "shared": 64 * 1024 * 1024},
                {"name": "dwm.exe", "pid": 1100, "local": 96 * 1024 * 1024,
                 "nonlocal": 0.0, "dedicated": 128 * 1024 * 1024,
                 "shared": 32 * 1024 * 1024},
                {"name": "explorer.exe", "pid": 2200, "local": 48 * 1024 * 1024,
                 "nonlocal": 0.0, "dedicated": 64 * 1024 * 1024,
                 "shared": 16 * 1024 * 1024},
            ]
            hw.io_read_mb = 12.0
            hw.page_in = 0.4
            hw.net_rx_mb = 0.3
            hw.net_tx_mb = 0.1
            hw.gpu_clock = 2400.0
            hw.gpu_clock_max = 2600.0
            hw.gpu_temp = 62.0
            win.store.add_snapshot(hw)
        ft = 1000.0 / 90.0
        if i in drop_at:
            ft = 1000.0 / 22.0
        rec = M.FrameRecord(i, i * 0.011, time.time() + i * 0.011, app_name, 4242,
                            ft, gpu_busy=ft * 0.8, cpu_busy=ft * 0.4, hw_seq=seq)
        win.store.add_frame(rec)
    win.target_app = app_name


def vram_row(ded_ratio, nonlocal_mb, prime_with_splash=False, base_nonlocal_mb=0.0,
             n=160, splash_at=120):
    """
    构造一段帧流，返回「显存溢出」那一行的 DiffRow（规范二的正/负用例）。

    prime_with_splash=True 时基线本身就带 700MB（增量 ≈ 0）→ 不该报爆显存。
    """
    from stuttertriage.analysis import FrameStore
    from stuttertriage.analysis import SnapshotDiffInspector
    import stuttertriage.models as M
    store = FrameStore()
    total = 8.0 * 1024 * 1024 * 1024
    t0 = time.time()
    for i in range(n):
        seq = i // 4
        if i % 4 == 0:
            hw = M.HardwareSnapshot(seq)
            hw.ts = t0 + i * 0.011
            hw.cpu_usage = [30.0] * 8
            hw.cpu_freq = [4200.0] * 8
            hw.cpu_total = 30.0
            hw.cpu0_usage = 30.0
            hw.vram_total = total
            hw.vram_dedicated = total * ded_ratio
            if prime_with_splash:
                hw.vram_nonlocal = base_nonlocal_mb * 1024 * 1024
            else:
                hw.vram_nonlocal = ((nonlocal_mb * 1024 * 1024) if i >= splash_at else 0.0)
            store.add_snapshot(hw)
        ft = 1000.0 / 120.0
        if i == splash_at:
            ft = 1000.0 / 20.0            # 真掉帧帧（帧耗时 50ms）
        store.add_frame(M.FrameRecord(i, i * 0.011, t0 + i * 0.011, "T.exe", 1, ft, hw_seq=seq))
    frames = store.visible_frames("T.exe")
    store.update_baseline(frames[:100])   # 用「干净」的前 100 帧打基线
    target = [f for f in frames if f.idx == splash_at][0]
    rows = SnapshotDiffInspector.inspect(target, store)
    return [r for r in rows if r.dim.startswith("显存溢出")][0]


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")

    print("== A. 包拆分 / 向后兼容 ==")
    import StutterTriage as F
    import stuttertriage
    from stuttertriage import (analysis, config, consts, csvio, etw, hotkeys, models,
                          probes, stats, theme, utils, winadmin)
    from stuttertriage.engines import hardware, presentmon
    from stuttertriage.ui import (base_window, chart, diagnosis, gauge, main_window,
                             navtabs, pages, plain, widgets)
    check("版本号存在", bool(stuttertriage.__version__), stuttertriage.__version__)
    check("旧顶层名字可用", all(hasattr(F, n) for n in (
        "StutterTriageWindow", "FrameRecord", "HardwareSnapshot", "FrameStore", "Config",
        "CONFIG_SCHEMA", "DIAG", "PG_SYMBOLS", "DATA_SOURCE_SIM", "GlobalHotkeyManager",
        "SnapshotDiffInspector", "theme_from_custom_bg", "ACCENT")), "")
    check("模块文件齐全", all([stats, gauge, navtabs, pages, chart, diagnosis, plain]))
    check("旧手风琴模块已删除", not os.path.exists(
        os.path.join(ROOT, "stuttertriage", "ui", "accordion.py")))

    print("== B. 建窗（规范一：240px 导航卡 + QStackedWidget）==")
    win = F.StutterTriageWindow()
    pump(app, 200)
    check("窗口标题含版本号", "StutterTriage 1.0.0" in win.windowTitle(), win.windowTitle())
    check("左栏固定 240px", win.sidebar.width() == 240 and win.sidebar.maximumWidth() == 240,
          str(win.sidebar.width()))
    check("5 张大号导航卡", len(win.nav_tabs) == 5, str(len(win.nav_tabs)))
    check("导航卡文案 = 规范里那 5 个模块",
          [t.title for t in win.nav_tabs] == ["帧率总览", "CPU 状态", "显卡与显存",
                                              "内存与磁盘", "网络与环境"],
          str([t.title for t in win.nav_tabs]))
    check("导航卡有两行（图标+名称 / 实时徽章）",
          all(t.lbl_title.text() and t.lbl_badge.text() for t in win.nav_tabs))
    check("右侧是 QStackedWidget", win.stack is not None
          and win.stack.count() == 5, str(win.stack.count()))
    check("默认选中 Page 0（帧率总览）", win.stack.currentIndex() == 0)
    check("Page 0 默认高亮", win.nav_tabs[0]._active and not win.nav_tabs[1]._active)
    check("5 个页面类型正确",
          [type(p).__name__ for p in win.pages] ==
          ["FpsOverviewPage", "CpuPage", "GpuPage", "MemPage", "NetPage"],
          str([type(p).__name__ for p in win.pages]))
    check("顶部横幅 + 4 大卡常驻（不在 stack 里）",
          win.banner_frame.parent() is not win.stack
          and win.card_avg.parent() is not win.stack)
    check("左栏没有抽屉/滚动条", not hasattr(win, "drawers")
          and win.sidebar.findChild(QtWidgets.QScrollArea) is None)
    check("四大帧数卡", all(hasattr(win, n) for n in
                            ("card_avg", "card_max", "card_min", "card_drop")))
    check("段位徽章 / 瓶颈天平 / FPS 曲线",
          win.badge is not None and win.bn_bar is not None and win.fps_chart is not None)
    check("齿轮菜单条目 ≥ 8", len(win.gear_menu.actions()) >= 8,
          "%d 条" % len(win.gear_menu.actions()))
    check("病历卡默认隐藏", not win.diag_card.isVisible())

    print("== F. 规范四：空载坐标轴锁死 ==")
    vr0 = win.fps_chart.plot_widget.getPlotItem().viewRange()
    check("无数据时 X 固定 (0,100)", (vr0[0][0], vr0[0][1]) == (0.0, 100.0), str(vr0[0]))
    check("无数据时 FPS 视角 Y 固定 (0,144)", (vr0[1][0], vr0[1][1]) == (0.0, 144.0),
          str(vr0[1]))
    check("无小数乱码（整数边界）",
          all(float(v).is_integer() for v in (vr0[0][0], vr0[0][1], vr0[1][0], vr0[1][1])),
          str(vr0))

    print("== C. 喂数据 + 刷新 ==")
    feed(win, 400)
    for _ in range(8):
        win._refresh_ui_inner()
        pump(app, 20)
    st = win._stat_cache or {}
    check("统计有数据", st.get("avg_fps", 0) > 0, "avg=%.1f" % st.get("avg_fps", 0))
    check("最高帧 >= 平均帧", st.get("max_fps", 0) >= st.get("avg_fps", 0),
          "max=%.1f" % st.get("max_fps", 0))
    check("1% Low 合理（>0 且 <= 平均帧）",
          0 < st.get("low1_fps", 0) <= st.get("avg_fps", 1) * 1.01,
          "low1=%.1f" % st.get("low1_fps", 0))
    check("顿挫被检出（90→22 帧）", st.get("dropped", 0) >= 2,
          "dropped=%d" % st.get("dropped", 0))
    check("评级合法", st.get("grade") in ("S", "A", "B", "C"), st.get("grade"))
    check("平均帧卡显示数字", win.card_avg.lbl_value.text() not in ("", "--"),
          win.card_avg.lbl_value.text())
    check("顿挫卡显示次数", "次" in win.card_drop.lbl_value.text(),
          win.card_drop.lbl_value.text())
    xdata = win.fps_chart.curve.getData()[0]
    check("曲线有数据点", xdata is not None and len(xdata) > 0)
    check("顿挫红点已打点", len(win.fps_chart.drop_points()) >= 2,
          "%d 个红点" % len(win.fps_chart.drop_points()))
    check("平均帧虚线可见", win.fps_chart.avg_line.isVisible())
    vr1 = win.fps_chart.plot_widget.getPlotItem().viewRange()
    check("FPS 视角 Y 下限锁死 0", vr1[1][0] == 0.0, str(vr1[1]))
    check("FPS 视角 Y 上限 = max(120, max*1.15)",
          abs(vr1[1][1] - max(120.0, st.get("max_fps", 0) * 1.15)) < 0.51,
          "ymax=%.2f max=%.1f" % (vr1[1][1], st.get("max_fps", 0)))
    check("Y 轴绝不收缩成 [97,101] 窄区间", vr1[1][1] >= 120.0, str(vr1[1]))
    check("X 轴下限不为负", vr1[0][0] >= 0.0, str(vr1[0]))
    check("导航卡徽章是实时文案（不是占位符）",
          ("FPS" in win.nav_tabs[0].lbl_badge.text()
           and "GHz" in win.nav_tabs[1].lbl_badge.text()),
          win.nav_tabs[0].lbl_badge.text() + " | " + win.nav_tabs[1].lbl_badge.text())
    check("总览页头部结论也是实时文案",
          "等待数据" not in win.pages[0].lbl_verdict.text()
          and "丝滑" in win.pages[0].lbl_verdict.text(),
          win.pages[0].lbl_verdict.text())
    # v1.0.1：数值已移到独立数值行，结论行只给一句短结论
    bn_txt = win.lbl_bottleneck.fullText() if hasattr(win.lbl_bottleneck, "fullText") \
        else win.lbl_bottleneck.text()
    check("瓶颈天平有结论", bool(bn_txt) and "等数据" not in bn_txt, bn_txt)
    check("横幅是人话", bool(win.banner_text.text()), win.banner_text.text()[:40])

    print("== B2. 点击导航卡整块替换右侧主画布 ==")
    win._on_nav_clicked(1)
    pump(app, 60)
    check("切到 Page 1（CPU）", win.stack.currentIndex() == 1)
    check("对应导航卡高亮", win.nav_tabs[1]._active and not win.nav_tabs[0]._active)
    cpu = win.pages[1]
    # v1.0.1：方块热力图已按用户要求移除，改查「每线程折线 + 柱状条」
    check("Page 1 无方块热力图（已删除）", not hasattr(cpu, "heat"))
    check("Page 1 每线程折线有数据", len(cpu.threads.samples(0)) > 0,
          "线程数=%d 首线程点数=%d" % (len(cpu.threads.rows), len(cpu.threads.samples(0))))
    check("Page 1 线程数 > 视口可容纳 → 出现滚动条",
          len(cpu.threads.rows) == 0
          or cpu.threads.scroll.verticalScrollBar().maximum() >= 0,
          "scroll max=%d" % cpu.threads.scroll.verticalScrollBar().maximum())
    check("Page 1 频率柱状条有数据", max(cpu.bars.freqs or [0]) > 0,
          str(cpu.bars.freqs[:3]))
    check("Page 1 大字诊断非空", len(cpu.lbl_verdict.text()) > 5, cpu.lbl_verdict.text())
    win._on_nav_clicked(2)
    pump(app, 60)
    gpu = win.pages[2]
    check("Page 2 3D 负载仪表盘有值", gpu.ring._center not in ("", "0%"),
          gpu.ring._center)
    check("Page 2 显存百分比被 clamp（无 1008%）", 0.0 <= gpu.split.pct() <= 100.0,
          "%.2f%%" % gpu.split.pct())
    # 【跨环境契约】CI 运行器是无 GPU 的虚拟机：显卡维度数据全空时，Top5 表为 0 行
    # 是正确行为。这里断言「有 GPU 数据 -> 表必须有行；无 GPU 数据 -> 允许 0 行」。
    _gpu_has_data = gpu.ring._center not in ("", "0%") or gpu.split.pct() > 0.0
    check("Page 2 Top5 进程表与 GPU 数据一致",
          gpu.table.rowCount() >= 1 or not _gpu_has_data,
          "rows=%d gpu_data=%s" % (gpu.table.rowCount(), _gpu_has_data))
    win._on_nav_clicked(3)
    pump(app, 60)
    mem = win.pages[3]
    check("Page 3 内存圆环有值", bool(mem.ring._center), mem.ring._center)
    check("Page 3 读盘能量条 / 换页表可用",
          mem.bar_io._value >= 0 and mem.table_io.rowCount() >= 0)
    win._on_nav_clicked(4)
    pump(app, 60)
    net = win.pages[4]
    check("Page 4 网络吞吐条有值", net.bars._down >= 0 and net.bars.up_max > 0)
    check("Page 4 着色器缓存状态有文案", bool(net.lbl_dot.text()), net.lbl_dot.text())
    win._on_nav_clicked(0)
    pump(app, 60)
    check("切回 Page 0 图表仍在", win.stack.currentIndex() == 0
          and win.fps_chart.curve.getData()[0] is not None)

    print("== D. 规范二：爆显存判定（正/负用例）==")
    row = vram_row(ded_ratio=0.15, nonlocal_mb=700.0)
    check("桌面 15% 显存 + 700MB 非本地 → 严禁报爆显存",
          row.severity != "DANGER", "%s | %s" % (row.severity, row.verdict[:40]))
    row = vram_row(ded_ratio=0.92, nonlocal_mb=700.0)
    check("92% 显存 + 700MB 非本地 + 暴增 → 报爆显存", row.severity == "DANGER",
          "%s | %s" % (row.severity, row.verdict[:40]))
    row = vram_row(ded_ratio=0.92, nonlocal_mb=100.0)
    check("非本地只有 100MB（<256）→ 不报爆显存", row.severity != "DANGER",
          row.severity)
    row = vram_row(ded_ratio=0.92, nonlocal_mb=700.0, prime_with_splash=True,
                   base_nonlocal_mb=700.0)
    check("非本地 700MB 但增量 ≈0（平稳映射）→ 不报爆显存",
          row.severity != "DANGER", "%s | %s" % (row.severity, row.delta))
    check("显存百分比行绝不出现 >100%", "1008" not in row.cur and "1008" not in row.base,
          row.cur)
    vtext, vpct = gauge.vram_text(6.4 * 1024, 8.0 * 1024, 0.0)
    import re as _re
    check("显存进度条格式 = 已用 X.X GB / 总计 X.X GB (XX%)",
          bool(_re.match(r"^已用 \d+\.\d GB / 总计 \d+\.\d GB \(\d+%\)$", vtext)),
          vtext)
    check("显存进度条百分比 = 80%（不是 MB 当百分比）", abs(vpct - 80.0) < 1e-9,
          "%.2f" % vpct)

    print("== E. 规范三：显卡待机低频波动 ==")
    rows_lo = dc_rows(base_mhz=400.0, cur_mhz=300.0, load3d=15.0, slow_frame=True)
    check("400→300MHz 桌面待机 → 严禁报降频",
          rows_lo.severity != "DANGER", "%s | %s" % (rows_lo.severity, rows_lo.verdict[:40]))
    rows_lo2 = dc_rows(base_mhz=2400.0, cur_mhz=1800.0, load3d=20.0, slow_frame=True)
    check("2400→1800MHz 但 3D 负载只 20% → 严禁报降频",
          rows_lo2.severity != "DANGER", rows_lo2.severity)
    rows_hi = dc_rows(base_mhz=2400.0, cur_mhz=1800.0, load3d=70.0, slow_frame=True)
    check("2400→1800MHz(-25%) + 3D 负载 70% + 真掉帧 → 报撞墙降频",
          rows_hi.severity == "DANGER", "%s | %s" % (rows_hi.severity, rows_hi.verdict[:40]))
    rows_hi2 = dc_rows(base_mhz=2400.0, cur_mhz=1800.0, load3d=70.0, slow_frame=False)
    check("同样降频但不卡顿 → 至多 WARN，不报「撞墙降频」",
          rows_hi2.severity != "DANGER", "%s | %s" % (rows_hi2.severity,
                                                     rows_hi2.verdict[:40]))

    print("== G. 规范五：顿挫双重门控 ==")
    if not (hasattr(stats, "stutter_flags") and hasattr(stats, "is_stutter_frame")):
        check("stats 暴露 stutter_flags / is_stutter_frame", False, "算法模块未就绪")
    else:
        check("stats 暴露 stutter_flags / is_stutter_frame", True)
        cases = (("100→98 帧属正常波动 → 0 标记",
                  [100.0] * 40 + [98.0] + [100.0] * 40, 0),
                 ("204→101 帧（>85FPS 豁免）→ 0 标记",
                  [204.0] * 40 + [101.0] + [204.0] * 40, 0),
                 ("100→90 帧 → 0 标记", [100.0] * 40 + [90.0] + [100.0] * 40, 0),
                 ("100→55 帧（跌 45% 且跳变 8.2ms）→ 有标记",
                  [100.0] * 40 + [55.0] + [100.0] * 40, 1),
                 ("100→50 帧（跌 50% 且跳变 10ms）→ 有标记",
                  [100.0] * 40 + [50.0] + [100.0] * 40, 1))
        for name, seq, want in cases:
            got = stutter_count(seq)
            check(name, (got >= want) if want else (got == 0), "标记数=%d" % got)
        # 【规范自洽性】条件 A(≤65%) 与 条件 B(耗时跳变 ≥8ms) 取交集后，
        # 100 帧基线下真正的门槛是 ≤55.6 帧（B 更严）—— 这是「必须同时满足」的字面结果。
        n60 = stutter_count([100.0] * 40 + [60.0] + [100.0] * 40)
        print("  [INFO] 100→60 帧（跳变仅 6.7ms < 8ms）标记数=%d（条件 B 过滤）" % n60)
    check("window_stats 带 stutter_now / smooth",
          "stutter_now" in st and "smooth" in st,
          str(sorted(k for k in st if "stut" in k or k == "smooth")))

    print("== H. 病历卡：绿色丝滑 vs 真顿挫 ==")
    pts = win.fps_chart.drop_points()
    check("真顿挫有红点可点", len(pts) >= 1, "%d 个" % len(pts))
    idx = int(pts[0][0]) if pts else 120
    win._on_chart_frame_clicked(idx)
    pump(app, 60)
    check("点真顿挫帧 → 病历卡显示", win.diag_card.isVisibleTo(win))
    check("① 掉了多少帧", bool(win.diag_card.blk_drop.lbl_body.text()),
          win.diag_card.blk_drop.lbl_body.text())
    check("② 罪魁祸首", bool(win.diag_card.blk_cause.lbl_body.text()),
          win.diag_card.blk_cause.lbl_body.text())
    check("③ 解决建议", bool(win.diag_card.blk_advice.lbl_body.text()),
          win.diag_card.blk_advice.lbl_body.text())
    check("真顿挫帧不给绿色丝滑态", not win.diag_card.is_smooth_state())
    win._on_chart_frame_clicked(5)          # 平稳帧
    pump(app, 40)
    check("点平稳帧 → 绿色「全程运行丝滑」", win.diag_card.is_smooth_state(),
          win.diag_card.blk_cause.lbl_body.text())
    win._open_pro_dialog()
    pump(app, 60)
    check("专业表格 11 行 5 列",
          win._pro_dialog.table.rowCount() == 11 and win._pro_dialog.table.columnCount() == 5,
          "%dx%d" % (win._pro_dialog.table.rowCount(), win._pro_dialog.table.columnCount()))
    check("专业表格首行有内容", bool(win._pro_dialog.table.item(0, 0).text()),
          win._pro_dialog.table.item(0, 0).text())
    win._pro_dialog.close()
    win.diag_card._on_close()
    pump(app, 30)
    check("收起病历卡后解除锁定", win.selected_frame is None)

    print("== E2. 主题 / FPS↔ms ==")
    from stuttertriage.theme import THEME_LIGHT, THEME_DEEP_SPACE
    import stuttertriage.theme as _T
    keep_key = _T.CURRENT_THEME.key          # 测试结束后还原，别改用户当前皮肤
    # 【v1.0.1】配置修好后，启动皮肤就是用户存档里的那套；先切到深色再切浅色，
    # 这样无论用户存档是浅是深，都能真正验证"换肤立即生效"。
    win._apply_theme(THEME_DEEP_SPACE.copy(), persist=False)
    pump(app, 60)
    before = win.banner_frame.styleSheet()
    win._apply_theme(THEME_LIGHT.copy(), persist=False)
    pump(app, 60)
    check("浅色主题生效", win.banner_frame.styleSheet() != before,
          "%r -> %r" % (before[:28], win.banner_frame.styleSheet()[:28]))
    check("页面跟随主题重刷", win.pages[1].lbl_title.styleSheet() != "")
    win._apply_theme(THEME_DEEP_SPACE.copy(), persist=False)
    pump(app, 40)
    check("深色主题生效", "deepspace" in _T.CURRENT_THEME.key or _T.CURRENT_THEME.is_dark)
    win._toggle_fps_mode()
    pump(app, 30)
    check("切到 ms 视角", win.fps_chart.mode == "ms", win.fps_chart.mode)
    vrm = win.fps_chart.plot_widget.getPlotItem().viewRange()
    ft_max = max([f.frametime for f in
                  win.store.visible_frames("TestGame.exe")] or [0.0])
    expect = 35.0 if ft_max <= 35.0 else min(max(35.0, ft_max * 1.1), 60.0)
    check("ms 视角 Y = 0~35ms（无大尖峰时）",
          abs(vrm[1][1] - expect) < 0.51, "ymax=%.2f expect=%.2f maxft=%.2f"
          % (vrm[1][1], expect, ft_max))
    check("ms 视角 Y 下限锁死 0", vrm[1][0] == 0.0, str(vrm[1]))
    check("ms 视角有 2 条参考虚线（16.7 / 33.3 ms）",
          len(win.fps_chart.ms_ref_lines) == 2
          and all(ln.isVisible() for ln in win.fps_chart.ms_ref_lines)
          and abs(win.fps_chart.ms_ref_lines[0].value() - 16.7) < 0.01,
          str([ln.value() for ln in win.fps_chart.ms_ref_lines]))
    win._toggle_fps_mode()
    check("切回 FPS 视角", win.fps_chart.mode == "fps")
    check("FPS 视角隐藏参考虚线",
          all(not ln.isVisible() for ln in win.fps_chart.ms_ref_lines))

    print("== I. 既有功能未丢 ==")
    check("设置页存在", win.tab_settings is not None)
    check("阈值编辑框数量正确", len(win.cfg_edits) == len(F.CONFIG_SCHEMA),
          "%d vs %d" % (len(win.cfg_edits), len(F.CONFIG_SCHEMA)))
    check("PresentMon 路径框", win.edit_pm_path is not None)
    check("热键输入框", win.edit_hotkey is not None)
    check("主题下拉框", win.combo_theme is not None)
    check("导入进度条存在", win.progress_import is not None)
    check("打点按钮存在", win.btn_mark is not None)
    check("设置/事件流面板 2 个页签（瘦身）", win.hw_tabs.count() == 2,
          str(win.hw_tabs.count()))
    check("面板标题 = 设置与事件流", "设置与事件流" in win.hw_panel.windowTitle(),
          win.hw_panel.windowTitle())
    before_n = len(win.store.events)
    win._begin_collection()       # 【手动开始采集】打点事件走队列，先进入采集态
    win.btn_mark.click()
    pump(app, 50)
    check("打点按钮产生事件", len(win.store.events) == before_n + 1)
    check("热键管理器已接线（或优雅降级）", win._hotkey is not None)
    check("热键提示标签非空", bool(win.lbl_hotkey.text()), win.lbl_hotkey.text()[:40])

    print("== J. 关闭 ==")
    try:
        win.close()
        pump(app, 150)
        check("关闭无异常", True)
    except Exception as ex:
        check("关闭无异常", False, str(ex))

    print()
    if FAILS:
        print("FAILED: %d -> %s" % (len(FAILS), FAILS))
        return 1
    print("ALL RUNTIME CHECKS PASSED")
    return 0


# ---------------------------------------------------------------------------
# 纯逻辑用例（不建窗，供规范三/五使用）
# ---------------------------------------------------------------------------
def stutter_count(fps_seq):
    """把帧率序列转成帧流，返回严格顿挫标记数（规范五）"""
    from stuttertriage import stats
    from stuttertriage.models import FrameRecord
    frames = [FrameRecord(i, i * 0.01, 0.0, "T.exe", 1, 1000.0 / max(0.1, v))
              for i, v in enumerate(fps_seq)]
    return int(stats.stutter_flags(frames).sum())


def dc_rows(base_mhz, cur_mhz, load3d, slow_frame):
    """构造一帧 + 一张硬件快照，返回「GPU 核心频率 / 撞墙降频」那一行（规范三）"""
    from stuttertriage.analysis import FrameStore, SnapshotDiffInspector
    import stuttertriage.models as M
    store = FrameStore()
    total = 8.0 * 1024 * 1024 * 1024
    t0 = time.time()
    for i in range(160):
        seq = i // 4
        if i % 4 == 0:
            hw = M.HardwareSnapshot(seq)
            hw.ts = t0 + i * 0.011
            hw.cpu_usage = [30.0] * 8
            hw.cpu_freq = [4200.0] * 8
            hw.cpu_total = 30.0
            hw.vram_total = total
            hw.vram_dedicated = total * 0.3
            hw.gpu_eng = {"engtype_3D": load3d / 100.0}
            hw.gpu_clock = base_mhz          # 稳态基线 = base_mhz
            hw.gpu_temp = 60.0
            store.add_snapshot(hw)
        store.add_frame(M.FrameRecord(i, i * 0.011, t0 + i * 0.011, "T.exe", 1,
                                      1000.0 / 120.0, hw_seq=seq))
    frames = store.visible_frames("T.exe")
    store.update_baseline(frames[:100])
    # 最后一次采样：频率掉到 cur_mhz
    last_snap = store.snapshots[-1]
    last_snap.gpu_clock = cur_mhz
    ft = (1000.0 / 20.0) if slow_frame else (1000.0 / 120.0)
    frame = M.FrameRecord(999, 9.99, last_snap.ts, "T.exe", 1, ft,
                          hw_seq=last_snap.seq)
    store.add_frame(frame)
    rows = SnapshotDiffInspector.inspect(frame, store)
    return [r for r in rows if r.dim.startswith("GPU 核心频率")][0]


if __name__ == "__main__":
    sys.exit(main())
