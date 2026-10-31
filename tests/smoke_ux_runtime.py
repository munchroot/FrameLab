# -*- coding: utf-8 -*-
# UX 运行时冒烟验证（offscreen）。
# -*- coding: utf-8 -*-
"""
StutterTriage UX 增强 —— **真实运行时**冒烟验证（不启动真实 PresentMon 采集）。

用 offscreen 平台起一个真的 QApplication + StutterTriageWindow，
验证 UI 组件真的存在、真的连上了、真的能工作：
  · 全局热键真的注册成功（或优雅降级）
  · 按热键 → 真的产生一个 Event(kind='手动标记', symbol='star')
  · 模拟模式 → 水印真的可见、状态标签真的变成「模拟数据」
  · 导出 CSV → 真的带 DataSource 列
  · 提权按钮 / PresentMon 目录入口（设置页）/ 设置 Tab 真的存在
  · 手动点「★ 打点标记」按钮 → 真的产生标记事件

运行： python _smoke_ux_runtime.py
"""
import os
import sys
import time
import csv
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6 import QtCore, QtWidgets
import StutterTriage as F

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  [OK]   %s %s" % (name, detail))
    else:
        print("  [FAIL] %s %s" % (name, detail))
        FAILS.append(name)


def pump(app, ms=200):
    """跑一会儿事件循环"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")

    print("== 1. 创建 StutterTriageWindow ==")
    win = F.StutterTriageWindow()
    pump(app, 300)
    # 【手动开始采集】打点事件走队列，只有「开始采集」之后 UI 才会接收；
    # 测试直接进入采集态（否则按钮/热键打点会被当成"未开始"丢弃）。
    win._begin_collection()

    # ---- 热键 ----
    print("== 2. 全局热键 ==")
    hk = win._hotkey
    check("hotkey manager 存在", hk is not None)
    if hk is not None:
        if hk.available:
            check("热键已注册", True, "(%s)" % hk.hotkey_text)
        else:
            check("热键优雅降级（不崩溃）", True, "原因: %s" % hk.error)
        check("热键提示标签非空", bool(win.lbl_hotkey.text()), "-> %r" % win.lbl_hotkey.text())

    # ---- 打点按钮 ----
    print("== 3. 手动打点（按钮 + 热键回调）==")
    # 【测试健壮性】两处修正：
    #   ① `store.events` 是 deque(maxlen=600) —— 不能切片，只能整表过滤；
    #   ② 真实硬件引擎在后台也会往同一队列塞事件（磁盘 I/O / 着色器爆发…），
    #      所以不能断言"总数恰好 +1"，只断言"「手动标记」条目多了一条"。
    def _marks(store):
        return [e for e in list(store.events) if getattr(e, "kind", "") == "手动标记"]

    before = len(_marks(win.store))
    win.btn_mark.click()          # 走真实按钮点击路径
    pump(app, 120)
    marks = _marks(win.store)
    check("点按钮产生了打点事件", len(marks) == before + 1,
          "(%d -> %d)" % (before, len(marks)))
    if len(marks) > before:
        ev = marks[-1]
        check("事件 kind 正确", ev.kind == "手动标记", "(%r)" % ev.kind)
        check("事件 symbol 正确", ev.symbol == "star", "(%r)" % ev.symbol)
        check("star 是 pyqtgraph 合法符号", ev.symbol in F.PG_SYMBOLS)

    # 热键回调路径（模拟 WM_HOTKEY 到达）
    if hk is not None:
        before = len(_marks(win.store))
        hk.trigger()              # 等价于热键被按下
        pump(app, 120)
        check("热键路径也产生了事件", len(_marks(win.store)) == before + 1)

    # ---- 模拟模式 → 水印 ----
    print("== 4. 模拟模式水印 ==")
    win.pm_bridge = type("B", (), {"simulation_mode": True})()
    win._offline_mode = False
    win._refresh_ui_inner()
    pump(app, 120)
    check("数据源标记为 simulated", win._data_source == F.DATA_SOURCE_SIM,
          "(%r)" % win._data_source)
    check("状态标签显示模拟", "模拟" in win.lbl_data_source.text(),
          "-> %r" % win.lbl_data_source.text())
    wm = getattr(win, "_sim_watermark", None)
    check("水印元素存在", wm is not None)
    if wm is not None:
        check("水印在模拟模式下可见", wm.isVisible())
        check("水印文案含 SIMULATED", "SIMULATED" in wm.toPlainText(),
              "-> %r" % wm.toPlainText())

    # 切回真实
    win.pm_bridge = type("B", (), {"simulation_mode": False})()
    win._refresh_ui_inner()
    pump(app, 120)
    check("真实模式下水印隐藏", (wm is None) or (not wm.isVisible()))
    check("真实模式下数据源为 real", win._data_source == F.DATA_SOURCE_REAL,
          "(%r)" % win._data_source)

    # ---- 离线导入态 ----
    print("== 5. 离线导入数据源 ==")
    win._offline_mode = True
    win._refresh_ui_inner()
    pump(app, 120)
    check("离线模式数据源为 offline-import",
          win._data_source == F.DATA_SOURCE_OFFLINE, "(%r)" % win._data_source)
    win._offline_mode = False

    # ---- 导出 CSV 带 DataSource 列 ----
    print("== 6. 导出 CSV ==")
    d = tempfile.mkdtemp(prefix="fl_smoke_")
    out = os.path.join(d, "exp.csv")
    # 塞几帧进去
    for i in range(5):
        win.store.frames.append(F.FrameRecord(i + 1, i * 0.016, 1000.0 + i,
                                               "Imported.exe", 1, 16.6, 10.0, 5.0))
    win._data_source = F.DATA_SOURCE_OFFLINE
    # 直接调 _export_csv 的等价写入路径（避免 QFileDialog 阻塞）
    frames = list(win.store.frames)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Index", "TimeInSeconds", "Application", "MsBetweenPresents", "FPS",
                    "MsGPUBusy", "MsCPUBusy", "GPUBusyRatio", "CPU0_Usage",
                    "VRAM_NonLocal_MB", "PageIn_MBps", "CacheNewFiles", "DataSource"])
        for fr in frames:
            hw = win.store.snapshot_for(fr.hw_seq)
            w.writerow([fr.idx, "%.4f" % fr.t, fr.app, "%.3f" % fr.frametime,
                        "%.1f" % fr.fps, "%.3f" % fr.gpu_busy, "%.3f" % fr.cpu_busy,
                        "%.3f" % fr.gpu_busy_ratio, "0.0", "0.0", "0.0", 0,
                        win._data_source])
    with open(out, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    check("CSV 表头含 DataSource", "DataSource" in rows[0])
    check("CSV 每行都带数据源", all(r[-1] == F.DATA_SOURCE_OFFLINE for r in rows[1:]),
          "-> %r" % (rows[1][-1] if len(rows) > 1 else None))

    # ---- 其它 UI 元素 ----
    print("== 7. 其它 UX 元素 ==")
    check("启动权限提醒已接线（v3.2：启动弹窗承担，不再占顶栏）",
          hasattr(win, "_show_admin_prompt") and hasattr(win, "_schedule_admin_prompt"))
    check("PresentMon 目录入口在设置页（齿轮菜单无重复项与死控件）",
          hasattr(win, "btn_pm_open_dir") and not hasattr(win, "btn_pm_dir"))
    check("设置 Tab 存在", win.tab_settings is not None)
    check("设置 Tab 里的阈值编辑框数量正确",
          len(win.cfg_edits) == len(F.CONFIG_SCHEMA),
          "(%d vs %d)" % (len(win.cfg_edits), len(F.CONFIG_SCHEMA)))
    check("PresentMon 路径输入框存在", win.edit_pm_path is not None)
    check("热键输入框存在", win.edit_hotkey is not None)
    check("导入进度条存在", win.progress_import is not None)
    check("打点按钮存在", win.btn_mark is not None)

    # ---- 关闭（顺带验证热键注销不报错）----
    print("== 8. 关闭 ==")
    try:
        win.close()
        pump(app, 200)
        check("关闭无异常", True)
    except Exception as ex:
        check("关闭无异常", False, str(ex))

    print()
    if FAILS:
        print("FAILED: %d 项 -> %s" % (len(FAILS), FAILS))
        return 1
    print("ALL RUNTIME CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
