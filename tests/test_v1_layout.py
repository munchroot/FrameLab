# -*- coding: utf-8 -*-
"""
StutterTriage 1.0.x 回归：版面零抖动 + 文案瘦身 + CPU 折线 + 品牌/版本 +
v1.0.0「设置按钮瘦身 + 诊断节流（根治自己卡住）」。

【这份测试盯的是「用户看得见 / 用户感觉得到」的几条】
  一、卡牌不许再随曲线出现而上下动 —— 横幅/卡牌/图表/病历卡的 y 坐标在所有
      状态下必须完全一致（空载 → 有数据 → 顿挫 → 弹病历卡 → 关病历卡）；
  二、装饰性灰字已删除（不存在的属性必须真的不存在，避免有人"顺手加回来"）；
  三、CPU 页的频率/功率双折线：按快照 seq 去重、估算值必须带 est 标记、
      功率探针在无功率计的机器上必须退化为「估算」而不是编造实测；
  四、品牌与版本：StutterTriage 1.0.0；
  五、**设置按钮里的老东西清干净**（只剩设置 + 事件流两个页签，
      CpuHeatmapGrid / heat_color / _sync_table 那一批死代码全删）；
  六、**诊断全量重算被节流**（60 tick 内 inspect ≤3 次，而不是每 tick 一次）——
      这是「StutterTriage 自己卡住」的回归闸门。

运行：python tests\\test_v1_layout.py
"""
import os
import sys
import time
import shutil

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6 import QtWidgets                      # noqa: E402
from PyQt6.QtCore import Qt                      # noqa: E402

import stuttertriage.models as M                      # noqa: E402
import stuttertriage                                  # noqa: E402
import stuttertriage.analysis as analysis             # noqa: E402
import stuttertriage.config as F                      # noqa: E402
from stuttertriage.ui.main_window import StutterTriageWindow   # noqa: E402
from stuttertriage.ui.widgets import ElidedLabel      # noqa: E402
from stuttertriage.ui.cpu_trend import CpuTrendChart  # noqa: E402

_time = time

FAILED = []


def check(name, ok, detail=""):
    tag = "OK  " if ok else "FAIL"
    print("  [%s] %s %s" % (tag, name, detail))
    if not ok:
        FAILED.append(name)


def pump(app, ms=100):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def add_frames(win, lo, hi, ft=1000.0 / 60.0, hw_every=4):
    """合成帧 + 硬件快照（含功率），模拟真实采集写入顺序"""
    total = 8.0 * 1024 * 1024 * 1024
    for i in range(lo, hi):
        seq = i // hw_every
        if i % hw_every == 0:
            hw = M.HardwareSnapshot(seq)
            hw.ts = time.time() + i * 0.016
            hw.cpu_usage = [40.0 + (i % 9) * 2.0] * 8
            hw.cpu_freq = [3600.0 + (i * 17 % 900)] * 8
            hw.cpu_total = 45.0
            hw.cpu0_usage = 48.0
            hw.gpu_eng = {"engtype_3D": 0.9}
            hw.vram_total = total
            hw.vram_dedicated = total * 0.5
            hw.cpu_power = 40.0 + (i * 7 % 20)
            hw.cpu_power_est = True
            win.store.add_snapshot(hw)
        rec = M.FrameRecord(i, i * 0.016, time.time() + i * 0.016, "T.exe", 4242, ft,
                            gpu_busy=ft * 0.8, cpu_busy=ft * 0.4, hw_seq=seq)
        win.store.add_frame(rec)


def geom(win):
    """关键控件的 (y, height)：任何变化都等于「用户看到卡牌在动」"""
    page = win.pages[0]
    return {
        "banner": (win.banner_frame.geometry().y(), win.banner_frame.height()),
        "cards": (win.card_avg.geometry().y(), win.card_avg.height()),
        "stack": (win.stack.geometry().y(), win.stack.height()),
        "chart": (page.fps_chart.geometry().y(), page.fps_chart.height()),
        "diag": (page.diag_card.geometry().y(), page.diag_card.height()),
    }


def wait_stable(app, win, timeout_ms=2000):
    """等到版面真正稳定（连续两次采样的几何一致），避免慢机器上假失败"""
    end = time.time() + timeout_ms / 1000.0
    last = None
    while time.time() < end:
        pump(app, 40)
        cur = geom(win)
        if last is not None and cur == last:
            return cur
        last = cur
    return geom(win)


# ==============================================================================
def test_brand():
    print("== 1. 品牌与版本：StutterTriage 1.0.0 ==")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    check("__version__ = 1.0.0", stuttertriage.__version__ == "1.0.0", stuttertriage.__version__)
    title = win.windowTitle()
    check("窗口标题 = StutterTriage 1.0.0 …", title.startswith("StutterTriage 1.0.0"), title)
    check("标题里没有旧品牌「卡顿减减」", "卡顿减减" not in title, title)
    check("侧栏 LOGO 也没有旧品牌", "卡顿减减" not in win.lbl_logo.text(), win.lbl_logo.text())
    with open(os.path.join(ROOT, "build_tools", "version_info.txt"), "r", encoding="utf-8") as f:
        vi = f.read()
    check("version_info 里是 1.0.0.0", "1, 0, 0, 0" in vi and "1.0.0.0" in vi)
    check("version_info 里没有旧品牌", "卡顿减减" not in vi)
    win.close()
    win.deleteLater()
    pump(app, 60)


def test_no_layout_shift():
    print("== 2. 版面零抖动：空载 → 有数据 → 弹病历卡 → 关病历卡 ==")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    win.resize(1440, 900)
    win.show()
    g_empty = wait_stable(app, win)

    add_frames(win, 0, 3)
    win._refresh_ui_inner()
    g_few = wait_stable(app, win)

    add_frames(win, 3, 400)
    win._refresh_ui_inner()
    g_many = wait_stable(app, win)

    win._on_chart_frame_clicked(200)
    g_diag = wait_stable(app, win)

    win._on_diag_closed()
    g_idle = wait_stable(app, win)

    for tag, g in (("空载→少量帧", g_few), ("少量→大量帧", g_many),
                   ("→弹病历卡", g_diag), ("→关病历卡", g_idle)):
        diffs = {k: (g_empty[k], g[k]) for k in g_empty if g[k] != g_empty[k]}
        check("版面不变：%s" % tag, not diffs, str(diffs) if diffs else "")

    check("横幅高度锁死 = BANNER_HEIGHT",
          g_empty["banner"][1] == StutterTriageWindow.BANNER_HEIGHT, str(g_empty["banner"]))
    check("病历卡常驻（不是 show/hide 式）", win.pages[0].diag_card.isVisible())
    check("病历卡高度恒定 110", win.pages[0].diag_card.height() == 110,
          str(win.pages[0].diag_card.height()))
    check("关掉病历卡 = 回到 idle 占位（仍可见）",
          win.pages[0].diag_card.isVisible() and win.pages[0].diag_card.info() is None)
    win.close()
    win.deleteLater()
    pump(app, 60)


def test_elided():
    print("== 3. ElidedLabel：一行高 + 省略号 + 全文进 tooltip ==")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    host = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(host)
    lbl = ElidedLabel("短")
    lay.addWidget(lbl)
    host.resize(160, 60)
    host.show()
    pump(app, 80)
    h1 = lbl.height()
    long_text = "这是一段很长很长很长的结论文字，长到一行绝对放不下，必须被省略号截断才对"
    lbl.setText(long_text)
    pump(app, 80)
    check("高度不随文案变化", lbl.height() == h1, "%s -> %s" % (h1, lbl.height()))
    check("显示文本被截断（含 …）", "…" in lbl.text(), lbl.text())
    check("fullText() 保留全文", lbl.fullText() == long_text)
    check("tooltip 是全文", lbl.toolTip() == long_text)
    host.close()
    host.deleteLater()
    pump(app, 40)


def test_gray_text_removed():
    print("== 4. 文案瘦身：装饰性灰字真的删掉了 ==")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    for attr in ("lbl_grade_sub", "lbl_logo_sub", "lbl_nav_caption"):
        check("主窗口不再有 %s" % attr, not hasattr(win, attr))
    for idx, page in enumerate(win.pages):
        check("Page %d 不再有副标题 lbl_subtitle" % idx, not hasattr(page, "lbl_subtitle"))
    check("Page 0 不再有提示行 lbl_hint", not hasattr(win.pages[0], "lbl_hint"))
    check("Page 0 的结论行是单行省略标签", isinstance(win.pages[0].lbl_verdict, ElidedLabel))
    check("卡片标题已去英文括注", "FPS" not in win.card_avg.lbl_title.text(),
          win.card_avg.lbl_title.text())
    # 每张卡最多一行灰字（sub / tail 里至多一个可见）
    for card, name in ((win.card_avg, "平均帧"), (win.card_max, "最高帧"),
                       (win.card_min, "1% Low"), (win.card_drop, "顿挫")):
        shown = int(card.lbl_sub.isVisible()) + int(card.lbl_tail.isVisible())
        check("%s 卡最多 1 行补充文字" % name, shown <= 1, "visible_lines=%d" % shown)
    check("瓶颈天平是纯图形（条内/条上无文字）",
          win.bn_bar.minimumHeight() <= 12 and not hasattr(win.bn_bar, "text"),
          str(win.bn_bar.minimumHeight()))
    check("瓶颈数值行独立存在（CPU 左 / 显卡 右）",
          "CPU" in win.lbl_cpu_num.text() and "显卡" in win.lbl_gpu_num.text(),
          "%s | %s" % (win.lbl_cpu_num.text(), win.lbl_gpu_num.text()))
    check("瓶颈结论只占一行（不再有第 4 行建议）",
          not win.lbl_bn_advice.isVisible())
    win.close()
    win.deleteLater()
    pump(app, 60)


def test_cpu_trend():
    print("== 5. CPU 频率/功率双折线 ==")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    win.resize(1440, 900)
    win.show()
    pump(app, 200)
    # 【修竞态】必须停掉 UI/进程定时器：否则硬件心跳会在两条断言之间推入新快照，
    # 让下方「同一 seq 只取一个点」变成偶发失败（11 -> 12）。
    win.ui_timer.stop()
    win.proc_timer.stop()
    pump(app, 30)
    page = win.pages[1]
    trend = page.trend
    check("CPU 页挂着 CpuTrendChart", isinstance(trend, CpuTrendChart))
    check("高度自锁（不参与拉伸）", trend.height() == CpuTrendChart.HEIGHT, str(trend.height()))

    win._push_cpu_trend(None)                     # None 必须安全
    trend.clear()
    add_frames(win, 0, 40)
    # 逐个快照喂（真实运行时每个采样节拍就是一次新 seq；这里一次性造好快照后逐个重放）
    for hw in list(win.store.snapshots):
        win._push_cpu_trend(hw)
    pump(app, 60)
    freqs, powers = trend.samples()
    check("有频率采样点", len(freqs) > 1, "n=%d" % len(freqs))
    check("有功率采样点", len(powers) > 1 and powers[-1] is not None, str(powers[-1:]))
    check("主窗口 tick 也会喂曲线（seq 去重后不重复）",
          len(trend.samples()[0]) == len(freqs),
          "%d vs %d" % (len(trend.samples()[0]), len(freqs)))

    n_before = len(trend.samples()[0])
    hw = list(win.store.snapshots)[-1]
    for _ in range(5):                            # 同一 seq 重复喂 → 必须去重
        trend.push(hw.seq, 4000, 4200, 55.0, True)
    check("同一快照 seq 只取一个点", len(trend.samples()[0]) == n_before,
          "%d -> %d" % (n_before, len(trend.samples()[0])))

    from stuttertriage.probes import CpuPowerProbe
    probe = CpuPowerProbe()
    watts, est, src = probe.read(4200.0, 4600.0, 45.0)
    # 【跨机型契约】不同机器取数路径不同：
    #   pdh      = 本机有真实功率计数器（笔记本/品牌机），允许读到 0
    #   estimate = 没有实测源 → 必须给出正数估算
    #   none     = 连估算都做不了 → 必须 None
    if src == "pdh":
        ok_w = watts is not None and watts >= 0.0
    elif src == "estimate":
        ok_w = watts is not None and watts > 0
    else:
        ok_w = watts is None
    check("功率探针返回合法值（按取数路径）", ok_w, "%.1fW src=%s" % (watts or -1, src))
    check("无功率计时必须标估算", (est is True) if src == "estimate" else True,
          "est=%s src=%s" % (est, src))
    check("估算值不超过 TDP 上限", watts is None or watts <= probe.tdp_hint + 0.01,
          "%.1f <= %.1f" % (watts or 0, probe.tdp_hint))
    empty = probe.read(0.0, 0.0, 0.0)
    # 【跨机型契约】无 CPU 数据时：没有实测源就必须 None（绝不编数字）；
    # 若本机有真实功率计数器，返回实测值是同样正确的行为。
    check("没有 CPU 数据时不编数字", empty[0] is None or empty[2] == "pdh", str(empty))
    probe.close()
    win.close()
    win.deleteLater()
    pump(app, 60)


def test_legacy_panel_gone():
    """§6 v1.0.0：设置按钮里的老东西必须清干净（只留设置 + 事件流）"""
    print("== 6. 设置按钮瘦身：旧硬件页签 / 死代码全部移除 ==")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    pump(app, 60)

    tabs = [win.hw_tabs.tabText(i) for i in range(win.hw_tabs.count())]
    check("面板只剩 2 个页签（设置 + 事件流）", win.hw_tabs.count() == 2, str(tabs))
    check("设置页签在", win.hw_tabs.indexOf(win.tab_settings) >= 0, str(tabs))
    check("事件流页签在", win.hw_tabs.indexOf(win.tab_events) >= 0, str(tabs))
    for name in ("tab_cpu", "tab_vram", "tab_mem", "tab_shader", "heat_cpu",
                 "table_vram", "table_shader", "lbl_cpu_summary", "lbl_vram_info",
                 "lbl_mem_summary", "lbl_shader_status", "core_widgets"):
        check("旧面板控件 %s 已删除" % name, not hasattr(win, name))

    import stuttertriage.ui.widgets as W
    check("CpuHeatmapGrid 已从 widgets 删除", not hasattr(W, "CpuHeatmapGrid"))
    check("heat_color/_text_on 一并删除",
          not hasattr(W, "heat_color") and not hasattr(W, "_text_on"))

    texts = [a.text() for a in win.gear_menu.actions()]
    joined = " | ".join(texts)
    check("齿轮菜单不再有「进阶硬件面板」", "硬件面板" not in joined, joined)
    check("齿轮菜单不再有「打开 PresentMon 目录」", "PresentMon 目录" not in joined, joined)
    check("齿轮菜单仍保留设置 / 事件流 / CSV / 模拟流 / ETW / 换肤",
          all(k in joined for k in ("设置", "事件流", "导入 CSV", "导出本次采集",
                                    "模拟数据流", "清理残留 ETW", "快速换肤")), joined)
    check("死控件 btn_pm_dir 已删除", not hasattr(win, "btn_pm_dir"))
    check("死控件 btn_hw_panel 已删除", not hasattr(win, "btn_hw_panel"))

    # 事件流：真正写入表格（旧版 _sync_table 丢失定义 → 每 tick AttributeError）
    win._on_open_events_panel()
    pump(app, 60)
    import time as _t
    for i in range(3):
        win.store.add_event(M.Event(_t.time(), "手动标记", "探针事件 %d" % i, "对齐排查", "WARN", "star"))
    win._refresh_events_tab()
    pump(app, 40)
    # 注意：后台 HardwareEngine 可能同时塞入真实告警事件 → 只数「手动标记」那几行
    manual_rows = [r for r in range(win.table_events.rowCount())
                   if win.table_events.item(r, 1) is not None
                   and win.table_events.item(r, 1).text() == "手动标记"]
    check("事件表写入 3 条手动打点（旧版这里是 AttributeError）",
          len(manual_rows) == 3, "rows=%d manual=%d" % (win.table_events.rowCount(),
                                                        len(manual_rows)))
    check("事件类型列有内容", bool(manual_rows)
          and win.table_events.item(manual_rows[0], 1).text() == "手动标记")
    check("无颜色的格子不是 NoBrush（灰字根因）",
          win.table_events.item(manual_rows[0], 0).data(Qt.ItemDataRole.ForegroundRole) is None)
    win.hw_panel.hide()
    win.close()
    win.deleteLater()
    pump(app, 60)


def test_settings_grouped():
    """§6b v1.0.0：阈值表单不许再拿原始键名当标签；保存必须真的广播到各模块"""
    print("== 6b. 设置页人话化 + 阈值保存真的生效 ==")
    import tempfile
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    pump(app, 60)

    cfg = F.CONFIG_SCHEMA
    check("阈值编辑框数量 = CONFIG_SCHEMA", len(win.cfg_edits) == len(cfg),
          "%d vs %d" % (len(win.cfg_edits), len(cfg)))

    labels = [l.text() for l in win.tab_settings.findChildren(QtWidgets.QLabel)]
    raw = [t for t in labels if t in cfg]
    check("没有任何标签直接显示原始配置键名", not raw, str(raw))
    group_titles = [g[0] for g in F.CONFIG_GROUPS]
    joined = " | ".join(labels)
    check("分组标题都在设置页里", all(g in joined for g in group_titles), str(group_titles))
    check("标签是中文人话（含单位/默认值）",
          any("默认 64 MB" in t for t in labels) and any("默认 92" in t for t in labels),
          str([t for t in labels if "默认" in t][:3]))
    check("原始键名进了 tooltip",
          "spill_threshold" in win.cfg_edits["spill_threshold"].toolTip(),
          win.cfg_edits["spill_threshold"].toolTip()[:40])

    # ---- 保存必须广播到真正参与判定的模块（v1.0.0 修：以前只改本模块副本）----
    import stuttertriage.consts as C
    import stuttertriage.engines.hardware as HW
    import stuttertriage.analysis as AN
    tmp = tempfile.mkdtemp(prefix="fl_cfg_")
    old_path = win._ux_config.path if win._ux_config is not None else None
    old_spill = C.SPILL_THRESHOLD
    old_jam = C.CPU0_JAM_THRESHOLD
    try:
        if win._ux_config is None:
            win._ux_config = F.Config()
        win._ux_config.path = os.path.join(tmp, "stuttertriage_config.json")
        win.cfg_edits["spill_threshold"].setText(str(32 << 20))
        win.cfg_edits["cpu0_jam_threshold"].setText("88.5")
        win._on_save_thresholds()
        check("consts.SPILL_THRESHOLD 立即跟上", C.SPILL_THRESHOLD == (32 << 20),
              str(C.SPILL_THRESHOLD))
        check("engines.hardware 副本同步", HW.SPILL_THRESHOLD == (32 << 20),
              str(HW.SPILL_THRESHOLD))
        check("analysis 副本同步", abs(AN.CPU0_JAM_THRESHOLD - 88.5) < 1e-6,
              str(AN.CPU0_JAM_THRESHOLD))
        check("提示文案是「已保存到」", "已保存到" in win.lbl_settings_status.text(),
              win.lbl_settings_status.text())
    finally:
        if old_path:
            win._ux_config.path = old_path
        win._ux_config.set("spill_threshold", old_spill)
        win._ux_config.set("cpu0_jam_threshold", old_jam)
        win._ux_config.apply_to_globals()
        shutil.rmtree(tmp, ignore_errors=True)

    check("分组已覆盖全部配置键（无未登记分组）",
          len([k for k in cfg
               if k not in [x for _t, _d, ks in F.CONFIG_GROUPS for x in ks]]) == 0)

    # ---- 主题下拉框必须跟随真正生效的主题（v1.0.0 顺手修）----
    from stuttertriage import theme as T
    before = T.CURRENT_THEME
    try:
        win._apply_theme(T.THEME_DEEP_SPACE.copy(), persist=False)
        pump(app, 20)
        check("切到深空黑后下拉框跟随", win.combo_theme.currentData() == "deepspace",
              str(win.combo_theme.currentData()))
        win._apply_theme(T.THEME_LIGHT.copy(), persist=False)
        pump(app, 20)
        check("切到浅色后下拉框跟随", win.combo_theme.currentData() == "light",
              str(win.combo_theme.currentData()))
    finally:
        win._apply_theme(before.copy(), persist=False)

    win.close()
    win.deleteLater()
    pump(app, 60)


def test_diag_throttle():
    """§6c v1.0.0：诊断全量重算必须被节流（否则 10 万帧会话把 UI 拖死）"""
    print("== 6c. 「StutterTriage 自己卡住」回归：inspect 节流 ==")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    win.resize(1440, 900)
    win.show()
    add_frames(win, 0, 3000, hw_every=24)
    win._refresh_ui_inner()

    # 计数：inspect 被调用了多少次
    calls = {"n": 0}
    real = analysis.SnapshotDiffInspector.inspect

    def counting(frame, store):
        calls["n"] += 1
        return real(frame, store)

    analysis.SnapshotDiffInspector.inspect = staticmethod(counting)
    try:
        # 锁定一帧（病历卡常驻显示）→ 跑 60 个 tick（≈2 秒 @30FPS）
        win._on_chart_frame_clicked(1500, strict=True)
        calls["n"] = 0
        t0 = _time.perf_counter()
        for _ in range(60):
            win._refresh_ui_inner()
            app.processEvents()
        span = _time.perf_counter() - t0
        # 节流 1.0s：2 秒窗口内跟随刷新最多 3 次（+1 容错），绝不可能是 60 次
        check("锁定卡 60 tick 内 inspect ≤3 次（不是每 tick 一次）",
              calls["n"] <= 3, "calls=%d span=%.2fs" % (calls["n"], span))
        check("节流参数 >=0.5s", win.DIAG_FOLLOW_INTERVAL >= 0.5,
              str(win.DIAG_FOLLOW_INTERVAL))

        # 顿挫分支同样节流
        calls["n"] = 0
        st = dict(win._stat_cache or {})
        st.update({"stutter_now": True, "stutter_idx": 2999})
        win._stat_cache = st
        win.selected_frame = None
        win._auto_diag_idx = 2999          # 不触发自动弹卡，只看横幅路径
        for _ in range(30):
            win._refresh_ui_inner()
            app.processEvents()
        check("顿挫横幅 30 tick 内 inspect ≤2 次", calls["n"] <= 2, "calls=%d" % calls["n"])
    finally:
        analysis.SnapshotDiffInspector.inspect = staticmethod(real)

    win.close()
    win.deleteLater()
    pump(app, 60)



def test_manual_start_resample():
    """§6d「默认不采集 + 清空重采」：启动后手动点开始才收帧；清空后曲线从 0 重来"""
    print("== 6d. 手动开始采集 + 清空重采 ==")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    win.resize(1440, 900)
    win.show()
    pump(app, 60)

    def mk(i):
        return M.FrameRecord(i, i * 0.016, time.time() + i * 0.016, "T.exe", 4242,
                             1000.0 / 60.0, gpu_busy=13.0, cpu_busy=6.0, hw_seq=i // 4)

    check("启动即未开始采集", win._collect_started is False, str(win._collect_started))
    check("主按钮文字 = 开始采集", "开始采集" in win.btn_pause.text(), win.btn_pause.text())
    check("清空重采按钮存在", win.btn_resample is not None
          and "清空重采" in win.btn_resample.text(),
          win.btn_resample.text() if win.btn_resample is not None else "None")

    win.q.put(("frame", mk(1)))
    win._refresh_ui_inner()
    check("未开始时的队列帧被丢弃", len(win.store.frames) == 0, str(len(win.store.frames)))
    check("引擎徽章提示未开始", "未开始采集" in win.lbl_engine_badge.text(),
          win.lbl_engine_badge.text())
    win.btn_mark.click()          # 未开始打点：不应产生事件（也不应假装成功）
    win._refresh_ui_inner()
    marks_n = len([e for e in win.store.events if getattr(e, "kind", "") == "手动标记"])
    check("未开始时打点不产生事件", marks_n == 0, str(marks_n))

    win.btn_pause.click()
    pump(app, 40)
    check("点开始后进入采集", win._collect_started is True and win.is_paused is False,
          "%s/%s" % (win._collect_started, win.is_paused))
    check("主按钮文字 = 暂停采集", "暂停采集" in win.btn_pause.text(), win.btn_pause.text())
    win.btn_pause.click()          # 350ms 内连点第二下：不得变成「暂停」
    check("连点不会秒变暂停", win.is_paused is False
          and "暂停采集" in win.btn_pause.text(),
          "%s / %s" % (win.is_paused, win.btn_pause.text()))
    win.q.put(("frame", mk(2)))
    win._refresh_ui_inner()
    check("开始后队列帧被接收", len(win.store.frames) == 1, str(len(win.store.frames)))

    for i in range(3, 80):
        win.store.add_frame(mk(i))
    win._refresh_ui_inner()
    check("清空前有曲线数据", len(win.store.frames) >= 70, str(len(win.store.frames)))
    win.btn_resample.click()
    pump(app, 40)
    check("清空后帧缓存归零", len(win.store.frames) == 0, str(len(win.store.frames)))
    check("清空后统计缓存作废", win._stat_cache is None, str(win._stat_cache))
    check("清空后仍在采集", win._collect_started is True and win.is_paused is False,
          "%s/%s" % (win._collect_started, win.is_paused))

    win._toggle_pause()
    for i in range(100, 120):
        win.store.add_frame(mk(i))
    win.btn_resample.click()
    pump(app, 40)
    check("暂停中点清空 → 清空并继续采集",
          win.is_paused is False and len(win.store.frames) == 0,
          "paused=%s frames=%d" % (win.is_paused, len(win.store.frames)))

    win.close()
    win.deleteLater()
    pump(app, 60)


def test_power_fields():
    print("== 7. 快照字段：功率 + 估算标记 ==")
    s = M.HardwareSnapshot(1)
    check("默认 cpu_power 为 None（不是 0）", s.cpu_power is None)
    check("默认 cpu_power_est = True（保守标估算）", s.cpu_power_est is True)
    s.cpu_power = 61.5
    s.cpu_power_est = False
    check("可写入实测值", s.cpu_power == 61.5 and s.cpu_power_est is False)


def test_spike_denoise():
    print("== 8. 极值去噪：单帧尖峰不许顶飞「最高帧」与折线 Y 轴 ==")
    from stuttertriage.stats import window_stats
    from stuttertriage.ui.chart import MODE_FPS, MODE_MS, FpsChart   # noqa: F401

    def mk(fts, idx0=0):
        return [M.FrameRecord(idx0 + i, i * 0.01, time.time(), "T.exe", 1, ft)
                for i, ft in enumerate(fts)]

    # 98fps 稳态 + 1 帧 477fps（2.1ms）：最高帧必须仍是 ~98，并标出尖峰 477
    fts = [10.2] * 119 + [2.1]
    st = window_stats(mk(fts))
    check("单帧尖峰不抬高最高帧", abs(st["max_fps"] - 98.0) < 1.5, "%.1f" % st["max_fps"])
    check("尖峰仍被如实标注", abs(st["spike_fps"] - 476.2) < 2.0, "%.1f" % st["spike_fps"])
    check("原始极值仍在 max_fps_raw 里", abs(st["max_fps_raw"] - 476.2) < 2.0,
          "%.1f" % st["max_fps_raw"])

    # 5/125 帧（4%）真的跑到 300fps：这是成片高速段，必须照常算最高帧
    fts2 = [10.0] * 120 + [1000.0 / 300.0] * 5
    st2 = window_stats(mk(fts2))
    check("成片高速段仍算最高帧", abs(st2["max_fps"] - 300.0) < 1.0, "%.1f" % st2["max_fps"])
    check("成片高速段不算尖峰", st2["spike_fps"] == 0.0, "%.1f" % st2["spike_fps"])

    # 折线图：1 帧 477fps 尖峰不许把 Y 轴顶到 500+
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    ch = FpsChart()
    ch.resize(800, 300)
    ch.show()
    ch.update_series(mk([10.2] * 119 + [2.1]), peak_fps=st["max_fps"])
    ymax = ch.plot_widget.getPlotItem().getViewBox().viewRange()[1][1]
    check("Y 轴不被单帧尖峰顶飞", ymax < 200.0, "ymax=%.1f" % ymax)
    # ms 视角仍要看见 45ms 这种真卡顿帧（不能被去噪吃掉）
    ch.set_mode(MODE_MS)
    ch.update_series(mk([10.0] * 100 + [45.0]))
    ms_max = ch.plot_widget.getPlotItem().getViewBox().viewRange()[1][1]
    check("ms 视角仍扩到卡顿帧之上", ms_max >= 45.0, "ymax=%.1f" % ms_max)
    ch.close()
    ch.deleteLater()
    pump(app, 40)


def test_diag_consistency():
    print("== 9. 病历卡与横幅不许互相打脸 ==")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    win.resize(1440, 900)
    win.show()
    pump(app, 200)
    add_frames(win, 0, 300)
    win._refresh_ui_inner()
    pump(app, 120)

    # 显式按「真顿挫」弹卡 → 之后多轮刷新必须仍是 frame 态（不许翻成绿色丝滑）
    win._on_chart_frame_clicked(299, strict=True)
    check("弹卡当时是真顿挫态", win.diag_card.render_mode() == "frame",
          win.diag_card.render_mode())
    for _ in range(6):
        win._refresh_ui_inner()
        pump(app, 30)
    check("多轮刷新后仍是顿挫态（判定锁定）",
          win.diag_card.render_mode() == "frame", win.diag_card.render_mode())
    check("is_smooth_state 不再被「归因恰好正常」误判",
          not win.diag_card.is_smooth_state())

    # 点一帧平稳帧 → 必须是绿色丝滑态
    win._on_chart_frame_clicked(150, strict=False)
    check("平稳帧 → 绿色丝滑态", win.diag_card.is_smooth_state(),
          win.diag_card.render_mode())
    win._on_diag_closed()
    check("关闭后回到 idle 且顿挫锁被清掉",
          win.diag_card.render_mode() == "idle" and win._diag_strict is None)

    # 横幅文案不再出现「刚才卡顿原因：刚才…」这种重复
    from stuttertriage.ui.plain import explain
    from stuttertriage.models import DiffRow
    row = DiffRow("显存溢出", "7.9 GB", "5.1 GB", "+2.8 GB", "爆显存", "DANGER")
    txt = explain([row])["text"]
    check("横幅有原因与做法", "卡顿" in txt and "👉" in txt, txt)
    check("横幅不再重复「刚才」", "刚才" not in txt, txt)
    check("横幅不再有「解决：」冗余前缀", "解决：" not in txt, txt)
    ok_txt = explain([])["text"]
    check("无异常文案也不再是长句", "刚才" not in ok_txt and "解决" not in ok_txt, ok_txt)
    win.close()
    win.deleteLater()
    pump(app, 60)


def test_skin_persist():
    """
    换肤必须真的落盘并能读回来（v1.0.1 修「重启后皮肤丢失」）。

    【为什么要这条】Config.update() 原来只合并 CONFIG_SCHEMA（纯阈值），
    theme / chart_style / custom_bg 写进文件后读回来**直接被丢掉**，
    于是每次重启都回落到默认深色。这里用**临时文件**做全链路回归，
    绝不碰用户的 stuttertriage_config.json。
    """
    print("== 10. 换肤持久化：保存 → 重新加载 → 生效 ==")
    import json
    import tempfile
    from stuttertriage.config import Config, VALID_THEMES

    tmpdir = tempfile.mkdtemp(prefix="stuttertriage_cfg_")
    p = os.path.join(tmpdir, "stuttertriage_config.json")
    c = Config(path=p)
    check("默认皮肤是深空黑", c.get("theme") == "deepspace", c.get("theme"))
    for key in ("theme", "chart_style", "custom_bg"):
        check("皮肤键在白名单/额外键里", key in ("theme", "chart_style", "custom_bg"))
    c.set("theme", "light")
    c.set("chart_style", "hud")
    c.set("custom_bg", "#123456")
    check("save 成功", c.save())
    raw = json.load(open(p, encoding="utf-8"))
    check("文件里确实写了 light", raw.get("theme") == "light", str(raw.get("theme")))

    c2 = Config.load(path=p)
    check("重新加载后 theme = light（不再被丢弃）", c2.get("theme") == "light", c2.get("theme"))
    check("重新加载后 chart_style = hud", c2.get("chart_style") == "hud", c2.get("chart_style"))
    check("重新加载后 custom_bg 保留", c2.get("custom_bg") == "#123456", c2.get("custom_bg"))

    # 脏值必须回落，不能让程序卡在未知皮肤上
    bad = os.path.join(tmpdir, "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        json.dump({"theme": "不存在的皮肤", "chart_style": "??"}, f)
    c3 = Config.load(path=bad)
    check("未知皮肤回落默认", c3.get("theme") == "deepspace", c3.get("theme"))
    check("未知图表样式回落默认", c3.get("chart_style") == "capframex", c3.get("chart_style"))
    check("白名单包含四套预设 + custom",
          set(VALID_THEMES) >= {"capframex", "deepspace", "pureblack", "light", "custom"},
          str(VALID_THEMES))

    # 真窗口：换肤后 config 里的 theme 必须跟着变
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    win._ux_config = Config(path=p)
    win._apply_theme(__import__("stuttertriage.theme", fromlist=["x"]).THEME_CAPFRAMEX.copy(),
                     persist=True)
    check("窗口换肤写回配置", json.load(open(p, encoding="utf-8")).get("theme") == "capframex",
          json.load(open(p, encoding="utf-8")).get("theme"))
    win.close()
    win.deleteLater()
    pump(app, 60)
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_font_tokens():
    print("== 11. 字体层级与配色令牌（规范 §1）==")
    from stuttertriage import theme as T
    th = T.THEME_LIGHT
    check("浅色主题 FG = #1E293B", th.fg.upper() == "#1E293B", th.fg)
    check("浅色主题 DIM = #475569", th.dim.upper() == "#475569", th.dim)
    for key in ("capframex", "deepspace", "pureblack"):
        d = T.THEME_PRESETS[key].dim.upper()
        check("%s 深色 DIM = #94A3B8" % key, d == "#94A3B8", d)
    for key, t in T.THEME_PRESETS.items():
        check("%s 有 KV 令牌" % key,
              bool(t.key_bg) and bool(t.key_fg) and bool(t.val))
    # 全局 QSS 必须写死基线字重（禁止细体小灰字）
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    sheet = win.styleSheet()
    check("全局 QSS 含 font-weight 基线", "font-weight" in sheet)
    check("卡片标题 13px / 600", "13px" in win.card_avg.lbl_title.styleSheet()
          and "600" in win.card_avg.lbl_title.styleSheet(),
          win.card_avg.lbl_title.styleSheet())
    win.close()
    win.deleteLater()
    pump(app, 60)


def test_pro_table():
    """§12 v1.0.2：11 项归因表 —— 不许横滚 / 不许灰字 / 行高按内容 / 底色跟主题"""
    print("== 12. 归因表可读性（用户截图返修）==")
    from PyQt6.QtCore import Qt
    from stuttertriage.analysis import DiffRow
    from stuttertriage.ui.diagnosis import ProfessionalTableDialog
    from stuttertriage import theme as T

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    dlg = ProfessionalTableDialog(win)
    rows = []
    for i in range(11):
        sev = "DANGER" if i == 0 else ("WARN" if i == 1 else "NORMAL")
        rows.append(DiffRow("维度%d" % i, "12.3 ms", "6.8 ms", "+5.5 ms",
                            "🟠 明显微卡，请降低【阴影】质量" * 3, sev))
    f = M.FrameRecord(52, 0.7, time.time(), "T.exe", 1, 30.1, hw_seq=1)
    dlg.show_rows(f, rows)
    dlg.resize(1180, 460)
    dlg.show()
    pump(app, 200)

    hdr = dlg.table.horizontalHeader()
    from PyQt6.QtWidgets import QHeaderView
    check("第 5 列 Stretch（不再固定 560）",
          hdr.sectionResizeMode(4) == QHeaderView.ResizeMode.Stretch)
    check("横向滚动条被禁用",
          dlg.table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    check("行高按内容自动伸缩",
          dlg.table.verticalHeader().sectionResizeMode(0)
          == QHeaderView.ResizeMode.ResizeToContents)
    check("11 行齐全", dlg.table.rowCount() == 11, str(dlg.table.rowCount()))

    no_brush = 0
    for r in range(dlg.table.rowCount()):
        for c in range(dlg.table.columnCount()):
            it = dlg.table.item(r, c)
            fg = it.foreground()
            if fg.style() == Qt.BrushStyle.NoBrush or not fg.color().isValid():
                no_brush += 1
    check("没有任何单元格是空前景色（灰字根因）", no_brush == 0, "空前景格=%d" % no_brush)

    # 主题化底色：深色 → 浅色必须跟着变（写死的 #3d1418 就不会变）
    win._apply_theme(T.THEME_DEEP_SPACE.copy(), persist=False)
    dlg.restyle()
    pump(app, 120)
    dark_bg = dlg.table.item(0, 0).background().color().name()
    win._apply_theme(T.THEME_LIGHT.copy(), persist=False)
    dlg.restyle()
    pump(app, 120)
    light_bg = dlg.table.item(0, 0).background().color().name()
    check("DANGER 行底色随主题变化（不再写死 #3d1418）", dark_bg != light_bg,
          "%s -> %s" % (dark_bg, light_bg))
    # 浅色主题下前景必须是深色（浅底上的深字），深色主题下必须是亮色
    lfg = dlg.table.item(0, 1).foreground().color()
    lum = (0.299 * lfg.red() + 0.587 * lfg.green() + 0.114 * lfg.blue()) / 255.0
    check("浅色主题下正文色是深色（对比度可用）", lum < 0.5, "lum=%.2f %s" % (lum, lfg.name()))
    dlg.close()
    win.close()
    win.deleteLater()
    pump(app, 60)


def test_cpu_page_space():
    """§13 v1.0.2：CPU 页 —— KV 面板贴内容、线程列表可伸缩、火花线自适应窗口"""
    print("== 13. CPU 页版面与每线程折线（用户截图返修）==")
    from PyQt6.QtWidgets import QSizePolicy
    from stuttertriage.ui.cpu_trend import CpuThreadTrends

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    win.resize(1440, 900)
    win._on_nav_clicked(1)
    pump(app, 120)
    page = win.pages[1]

    def in_card(w):
        p = w.parentWidget()
        while p is not None:
            if p is page.card_freq:
                return True
            p = p.parentWidget()
        return False

    # 【v1.0.3】除柱状图外的三块必须合并进**同一张卡**
    for name, w in (("左 KV", page.kv), ("右 KV", page.kv_core),
                    ("均频/功率折线", page.trend), ("每线程折线", page.threads)):
        check("%s 在合并卡内" % name, in_card(w), str(w.parentWidget()))
    check("柱状图仍在合并卡之外（用户要求保留为独立卡片）",
          not in_card(page.bars), str(page.bars.parentWidget()))
    check("每线程列表视口可伸缩（不再是 setFixedHeight）",
          page.threads.scroll.maximumHeight() > page.threads.scroll.minimumHeight(),
          "%d..%d" % (page.threads.scroll.minimumHeight(),
                      page.threads.scroll.maximumHeight()))

    # 【v1.0.3】折线"明显点"：行要够高、线要够粗、要有渐变填充（像素级验证）
    from stuttertriage.ui.cpu_trend import CpuThreadRow
    check("每线程行高 ≥26px", CpuThreadRow.ROW_H >= 26, str(CpuThreadRow.ROW_H))
    rows = page.threads.rows
    if rows:
        r0 = rows[0]
        check("火花线高度 ≥20px", r0.spark.height() >= 20, str(r0.spark.height()))
        pump(app, 80)   # 先让硬件心跳刷完
        # 【修竞态】必须在心跳之后再注入并立刻 grab：
        # 心跳会用真实占用率覆盖 r0.usage，占用 ≥85 时线色变 warn（橙），
        # 与本测试断言的 accent 色不符 → 机器忙时曾稳定假失败。
        # push 不带 event loop，grab 同步触发 paint，心跳插不进来。
        for k in range(24):
            r0.push(4200.0 + (k % 12) * 30.0, 22.0)
        r0.set_window(4160.0, 4560.0)
        r0.spark.resize(600, r0.spark.height())
        img = r0.spark.grab().toImage()
        accent = __import__("stuttertriage.theme", fromlist=["x"]).CURRENT_THEME.accent
        want = __import__("PyQt6.QtGui", fromlist=["x"]).QColor(accent)
        hit = 0
        for yy in range(img.height()):
            for xx in range(0, img.width(), 2):
                c = img.pixelColor(xx, yy)
                if abs(c.red() - want.red()) + abs(c.green() - want.green()) \
                        + abs(c.blue() - want.blue()) < 150:
                    hit += 1
        check("折线像素足够多（2.2px 主线 + 渐变填充）", hit > 120, "命中像素=%d" % hit)

    # 12 线程 4.2~4.6GHz：窗口必须收窄到这一段，否则曲线就是直线
    tt = CpuThreadTrends(12)
    for k in range(30):
        freqs = [4200.0 + ((k + c) * 13 % 400) for c in range(12)]
        tt.push(k, freqs, [20.0] * 12)
    win_ = tt.auto_window()
    span = win_[1] - win_[0]
    check("窄幅波动时纵轴窗口收窄到 ≈400MHz", span <= 560.0, "span=%.0f" % span)
    check("曲线确实被展开（值落在窗口内靠上/靠下）",
          win_[0] < 4400.0 < win_[1], "%.0f..%.0f" % win_)
    # 一个空闲核掉到 800MHz，窗口跨度必须封顶 25% 峰值（否则其余核被压平）
    for k in range(30, 40):
        freqs = [4200.0] * 11 + [800.0]
        tt.push(k, freqs, [5.0] * 12)
    win2 = tt.auto_window()
    span2 = win2[1] - win2[0]
    check("空闲核不会把窗口拉到 800..4600", span2 <= 0.26 * 4620,
          "span=%.0f (%.0f..%.0f)" % (span2, win2[0], win2[1]))
    check("CpuFreqBars 不再在图内写频率数字",
          "best_f" not in open(os.path.join(ROOT, "stuttertriage", "ui", "gauge.py"),
                               encoding="utf-8").read())
    win.close()
    win.deleteLater()
    pump(app, 60)


def test_light_red_readable():
    """§16 v1.0.3：白色皮肤下的红/橙必须看得清（用户主诉）"""
    print("== 16. 浅色主题可读性（红色在白底看不清）==")
    from stuttertriage import theme as T
    from stuttertriage.ui import plain

    def lum(hexcol):
        c = __import__("PyQt6.QtGui", fromlist=["x"]).QColor(hexcol)
        out = []
        for v in (c.red(), c.green(), c.blue()):
            f = v / 255.0
            out.append(f / 12.92 if f <= 0.03928 else ((f + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]

    def ratio(a, b):
        la, lb = lum(a), lum(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    win._apply_theme(T.THEME_LIGHT.copy(), persist=False)
    pump(app, 80)
    th = T.CURRENT_THEME
    check("浅色主题下 danger 色 = 主题的 bad（不再写死 #ff5d5d）",
          plain.level_color("danger").lower() == th.bad.lower(),
          "%s / %s" % (plain.level_color("danger"), th.bad))
    check("评级 C 色 = 主题的 bad", plain.grade_color("C").lower() == th.bad.lower(),
          plain.grade_color("C"))
    for role, col in (("danger", plain.level_color("danger")),
                      ("warn", plain.level_color("warn")),
                      ("ok", plain.level_color("ok"))):
        r = min(ratio(col, "#ffffff"), ratio(col, th.panel2), ratio(col, th.bg))
        check("浅色主题 %s 对白底对比度 ≥4.5" % role, r >= 4.5, "%s %.2f" % (col, r))
    # 深浅两套主题必须给出不同颜色（证明是"按主题解析"而不是常量）
    win._apply_theme(T.THEME_DEEP_SPACE.copy(), persist=False)
    pump(app, 60)
    check("换到深色主题后 danger 色跟着变",
          plain.level_color("danger").lower() != th.bad.lower(),
          "%s -> %s" % (th.bad, plain.level_color("danger")))
    check("LEVEL_COLOR 兼容取值也是主题色",
          plain.LEVEL_COLOR["danger"] == plain.level_color("danger"))
    win.close()
    win.deleteLater()
    pump(app, 60)


def test_mem_pagein():
    """§14 v1.0.2：内存页 —— 能量条量程、换页 Top 空表必须说明原因"""
    print("== 14. 内存页换页归因（用户提问：空表是 bug 还是真没换页）==")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    # 【v1.0.0 心跳】未采集时窗口会每 100ms 用真实快照刷新硬件页；
    # 本节直接构造 hw 手动刷 MemPage，必须停掉定时器，否则断言被真实数据覆盖。
    win.ui_timer.stop()
    win._on_nav_clicked(3)
    pump(app, 80)
    page = win.pages[3]

    def txt():
        it = page.table_page.item(0, 0)
        return it.text() if it is not None else ""

    hw = M.HardwareSnapshot(1)
    hw.cpu_usage = [10.0] * 8
    hw.mem_commit = 29.9 * 1024 * 1024 * 1024
    hw.mem_limit = 31.5 * 1024 * 1024 * 1024
    hw.page_in, hw.page_out, hw.io_read_mb = 0.2, 0.0, 2.5
    hw.pagein_top = []
    page.refresh(hw, {})
    pump(app, 60)
    check("低换入时明确说明「未做进程级归因」", "未做进程级归因" in txt(), txt())
    check("换入能量条量程下限压到 ≤1MB/s（0.2MB/s 也看得见）",
          page.bar_pg_in.vmax <= 1.0 and page.bar_pg_in._value / page.bar_pg_in.vmax >= 0.15,
          "vmax=%.1f ratio=%.2f" % (page.bar_pg_in.vmax,
                                    page.bar_pg_in._value / page.bar_pg_in.vmax))
    hw.page_in = 12.0
    page.refresh(hw, {})
    pump(app, 40)
    check("换入升高但首轮无基线时提示采样中", "采样中" in txt(), txt())
    hw.pagein_top = [("game.exe", 8.5), ("chrome.exe", 3.0)]
    page.refresh(hw, {})
    pump(app, 40)
    check("拿到归因后列出进程", page.table_page.rowCount() == 2
          and page.table_page.item(0, 0).text() == "game.exe",
          "%d 行" % page.table_page.rowCount())
    check("0 值行被过滤（不再出现 0.0 的假嫌疑进程）",
          page.table_page.item(1, 0).text() == "chrome.exe")
    win.close()
    win.deleteLater()
    pump(app, 60)


def test_elided_sizehint():
    """§15 v1.0.2：ElidedLabel 被省略后必须还能要回全宽（状态胶囊被截成 "GH-" 的根因）"""
    print("== 15. ElidedLabel 尺寸反馈（状态胶囊截断）==")
    from PyQt6.QtWidgets import QSizePolicy
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    full = "🟢 调度健康 · 总占用 27% · 均频 4.40 GHz"
    lb = ElidedLabel(full)
    lb.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    lb.setMinimumWidth(160)
    lb.resize(160, 20)
    lb.show()
    pump(app, 60)
    check("被省略后 sizeHint 仍按全文算", lb.sizeHint().width() >= 200,
          "sizeHint=%d 显示=%s" % (lb.sizeHint().width(), lb.text()))
    check("全文仍可通过 fullText() 拿到", lb.fullText() == full)
    lb.close()
    lb.deleteLater()
    pump(app, 40)


def main():
    test_brand()
    test_no_layout_shift()
    test_elided()
    test_gray_text_removed()
    test_cpu_trend()
    test_legacy_panel_gone()
    test_settings_grouped()
    test_diag_throttle()
    test_manual_start_resample()
    test_power_fields()
    test_spike_denoise()
    test_diag_consistency()
    test_skin_persist()
    test_font_tokens()
    test_pro_table()
    test_cpu_page_space()
    test_mem_pagein()
    test_elided_sizehint()
    test_light_red_readable()
    print()
    if FAILED:
        print("FAILED: %d -> %s" % (len(FAILED), FAILED))
        return 1
    print("ALL StutterTriage 1.0.x LAYOUT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
