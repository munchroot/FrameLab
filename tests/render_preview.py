# -*- coding: utf-8 -*-
"""
把当前 UI 渲染成 PNG（offscreen），用于人工目检 / 视觉回归留档。

运行： python tests/render_preview.py
输出： docs/preview_deepspace.png、docs/preview_light.png、docs/preview_ms_view.png

【注意】默认走**真实 windows 平台**渲染（offscreen 平台下 Qt 找不到字体目录，
会把整张图渲染成方框）。若确实要无头运行，可自行设 QT_QPA_PLATFORM=offscreen，
但预览图会缺字。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6 import QtWidgets                       # noqa: E402

import stuttertriage.models as M                       # noqa: E402


def pump(app, ms=120):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def feed(win, n=900):
    """合成一段「大体 90 帧、偶尔掉到 25 帧」的会话，供目检用"""
    for i in range(n):
        seq = i // 4
        if i % 4 == 0:
            hw = M.HardwareSnapshot(seq)
            hw.ts = time.time() + i * 0.011
            hw.cpu_usage = [12.0 + (i * 7 % 30), 58.0, 11.0, 44.0, 13.0, 51.0,
                            55.0, 34.0, 11.0, 23.0, 33.0, 24.0, 11.0, 51.0, 55.0, 50.0]
            hw.cpu_freq = [3700.0 + (i * 13 % 1300)] * 16
            hw.cpu_total = 35.0
            # v1.0.0：CPU 频率/功率双折线要看到起伏，合成快照也给一条功率曲线
            hw.cpu_power = 42.0 + (i * 7 % 26)
            hw.cpu_power_est = True
            hw.cpu0_usage = 12.0
            hw.mem_avail_mb = 7200.0
            hw.mem_commit = 18.0 * 1024 * 1024 * 1024
            hw.mem_limit = 32.0 * 1024 * 1024 * 1024
            hw.gpu_eng = {"engtype_3D": 0.93}
            hw.vram_dedicated = 6.4 * 1024 * 1024 * 1024
            hw.vram_total = 8.0 * 1024 * 1024 * 1024
            hw.vram_nonlocal = 0.0
            hw.io_read_mb = 14.0
            hw.io_top = [("JustCause3.exe", 14.0), ("steam.exe", 2.1), ("chrome.exe", 0.4)]
            hw.page_in = 0.3
            hw.page_out = 0.1
            hw.pagein_top = [("JustCause3.exe", 0.3), ("DSH NEXT.exe", 0.1)]
            hw.net_rx_mb = 0.25
            hw.net_tx_mb = 0.08
            hw.cache = {
                "DXCache": {"files": 1204, "mb": 186.2, "new_files": 12, "new_mb": 3.4},
                "D3DCache": {"files": 320, "mb": 44.1, "new_files": 2, "new_mb": 0.6},
            }
            hw.cache_new_files = 14
            hw.cache_new_mb = 4.0
            hw.gpu_clock = 2412.0
            hw.gpu_clock_max = 2600.0
            hw.gpu_temp = 63.0
            win.store.add_snapshot(hw)
        ft = 1000.0 / 90.0
        if i % 97 == 40:                     # 周期性顿挫
            ft = 1000.0 / 25.0
        rec = M.FrameRecord(i, i * 0.011, time.time() + i * 0.011, "JustCause3.exe", 4242,
                            ft, gpu_busy=ft * 0.85, cpu_busy=ft * 0.35, hw_seq=seq)
        win.store.add_frame(rec)


def seed_cpu_trend(win):
    """把整段合成快照灌进 CPU 折线（否则预览里曲线只有最后几个点）"""
    try:
        trend = win.pages[1].trend
        trend.clear()
        threads = win.pages[1].threads
        for hw in list(win.store.snapshots):
            freqs = [f for f in (hw.cpu_freq or []) if f]
            avg_f = (sum(freqs) / len(freqs)) if freqs else 0.0
            top_f = max(freqs) if freqs else 0.0
            trend.push(hw.seq, avg_f, top_f, getattr(hw, "cpu_power", None),
                       bool(getattr(hw, "cpu_power_est", True)))
            threads.push(hw.seq, hw.cpu_freq, hw.cpu_usage)
    except Exception as ex:
        print("seed_cpu_trend skipped:", ex)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    # offscreen 平台下 Qt 找不到自带字体目录，必须显式指定系统字体，
    # 否则整张图会渲染成一堆方框（真机 windows 平台不受影响）。
    try:
        from PyQt6.QtGui import QFont
        app.setFont(QFont("Microsoft YaHei UI", 9))
    except Exception:
        pass
    import StutterTriage as F
    from stuttertriage import theme as T

    win = F.StutterTriageWindow()
    win.resize(1440, 900)
    win.show()
    pump(app, 200)
    # 【手动开始采集】预览图要展示"采集进行中"的正常状态（否则引擎徽章
    # 会写「未开始采集」，与满屏曲线自相矛盾）
    win._begin_collection()
    feed(win)
    for _ in range(10):
        win._refresh_ui_inner()
        pump(app, 30)
    seed_cpu_trend(win)
    # 打开病历卡（真顿挫帧），方便一次性目检所有新组件
    pts = win.fps_chart.drop_points()
    if pts:
        win._on_chart_frame_clicked(int(pts[len(pts) // 2][0]))
    pump(app, 120)

    out = os.path.join(ROOT, "docs")
    os.makedirs(out, exist_ok=True)

    def shot(name):
        pump(app, 120)
        win.grab().save(os.path.join(out, name))
        print("SAVED", os.path.join(out, name))

    win._apply_theme(T.THEME_DEEP_SPACE.copy(), persist=False)
    pump(app, 60)
    shot("preview_deepspace.png")

    win._apply_theme(T.THEME_LIGHT.copy(), persist=False)
    pump(app, 60)
    shot("preview_light.png")

    win._apply_theme(T.THEME_DEEP_SPACE.copy(), persist=False)
    win._toggle_fps_mode()
    pump(app, 60)
    shot("preview_ms_view.png")
    win._toggle_fps_mode()
    pump(app, 40)

    # 5 张导航卡各截一张：验证「点击整块替换右侧主画布」
    for idx, name in ((1, "preview_page_cpu.png"), (2, "preview_page_gpu.png"),
                      (3, "preview_page_mem.png"), (4, "preview_page_net.png")):
        win._on_nav_clicked(idx)
        pump(app, 120)
        shot(name)
    win._on_nav_clicked(0)
    pump(app, 60)

    # ⚙ 设置页（暗色可读性视觉证据）：
    # 分别在深色/浅色主题下打开进阶面板的「⚙ 设置」标签截图。
    for key, name in (("deepspace", "preview_settings_dark.png"),
                      ("light", "preview_settings_light.png")):
        win._apply_theme(T.THEME_PRESETS[key].copy(), persist=False)
        pump(app, 80)
        win.hw_panel.show()
        win.hw_tabs.setCurrentWidget(win.tab_settings)
        pump(app, 200)
        win.hw_panel.grab().save(os.path.join(out, name))
        print("SAVED", os.path.join(out, name))
        win.hw_panel.hide()
        pump(app, 60)

    # 恢复深色主题再退出
    win._apply_theme(T.THEME_DEEP_SPACE.copy(), persist=False)
    pump(app, 40)
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
