# -*- coding: utf-8 -*-
"""
折线图坐标轴范围测试（改进建议书 四）。

覆盖 FpsChart 的「空载乱码 / ms 视角 100ms 过高」修正：
  A1 公开 API 未被破坏（自检）
  A2 新建 + clear() → X=(0,100)、FPS Y=(0,144)
  A3 只有 1 帧 → 仍然固定范围（不进入动态缩放）
  A4 100 帧稳定 100fps → FPS Y 上限恰好 120（= max(120, 100×1.15)）
  A5 含 300fps 尖峰 → Y 上限 ≈ 345，且绝不是 [297,303] 这种窄区间
  A6 切 ms 视角 → Y=(0,35) 且两条参考虚线可见；切回 FPS → 两条都隐藏
  A7 ms 视角 50ms 大尖峰 → Y 上限 55；只含 ≤20ms → 仍为 (0,35)
  A8 X 轴下限永不为负（脏负 idx / 首帧 idx=0）

无显示器环境可跑：先设 QT_QPA_PLATFORM=offscreen 再 import pyqtgraph。
运行：python tests\\test_chart_axis.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyqtgraph as pg
from PyQt6 import QtWidgets

from stuttertriage.models import FrameRecord
from stuttertriage.ui import chart as C

fails = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        fails.append(name)


def approx(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


def frames_fps(fps_list, idx0=0):
    """按每帧帧率造 FrameRecord（frametime = 1000 / fps）。"""
    out = []
    for i, f in enumerate(fps_list):
        ft = 1000.0 / float(f)
        out.append(FrameRecord(idx0 + i, i * ft, 1700000000.0 + i * ft,
                               "game.exe", 1234, ft, hw_seq=i))
    return out


def frames_ms(ms_list, idx0=0):
    """按每帧帧耗时（ms）造 FrameRecord。"""
    out = []
    for i, ms in enumerate(ms_list):
        out.append(FrameRecord(idx0 + i, i * float(ms), 1700000000.0 + i * float(ms),
                               "game.exe", 1234, float(ms), hw_seq=i))
    return out


def ranges(chart):
    """(xmin, xmax, ymin, ymax) 实测视野。"""
    vb = chart.plot_widget.getPlotItem().getViewBox()
    r = vb.viewRange()
    return (r[0][0], r[0][1], r[1][0], r[1][1])


def fmt(r):
    return "X=(%.6f, %.6f) Y=(%.6f, %.6f)" % r


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
print("pyqtgraph %s / Qt offscreen" % pg.__version__)

print("=" * 72)
print("A1) 公开 API 未被破坏（既有实现不许删）")
print("=" * 72)
ch = C.FpsChart()
ch.resize(900, 400)
for attr in ("plot_widget", "curve", "avg_line", "scatter", "locked_line", "ms_ref_lines"):
    check("属性存在: %s" % attr, hasattr(ch, attr))
for meth in ("drop_points", "set_locked", "set_avg", "apply_chart_style",
             "_update_gradient", "_apply_axis_limits", "set_mode", "clear",
             "update_series", "apply_theme"):
    check("方法存在: %s" % meth, callable(getattr(ch, meth, None)))
check("frameClicked 信号存在", hasattr(ch, "frameClicked"))
check("MODE_FPS / MODE_MS 常量", C.MODE_FPS == "fps" and C.MODE_MS == "ms")

print()
print("=" * 72)
print("A2) 新建 + clear() → X=(0,100)、FPS Y=(0,144)")
print("=" * 72)
r = ranges(ch)
check("新建：X=(0,100)", approx(r[0], 0) and approx(r[1], 100), fmt(r))
check("新建：Y=(0,144)", approx(r[2], 0) and approx(r[3], 144), fmt(r))
check("新建：X 无小数乱码", r[0] == 0.0 and r[1] == 100.0, "x=%r" % (r[0:2],))
check("新建：Y 无小数乱码", r[2] == 0.0 and r[3] == 144.0, "y=%r" % (r[2:4],))

ch.update_series(frames_fps([100.0] * 60))
ch.clear()
r = ranges(ch)
check("clear()：X=(0,100)", approx(r[0], 0) and approx(r[1], 100), fmt(r))
check("clear()：Y=(0,144)", approx(r[2], 0) and approx(r[3], 144), fmt(r))
check("clear()：曲线已清空", len(ch._frames) == 0 and ch.drop_points() == [])
check("clear()：X 下限不为负", r[0] >= 0, fmt(r))

print()
print("=" * 72)
print("A3) 只有 1 帧 → 仍固定 (0,100)/(0,144)，不进入动态缩放")
print("=" * 72)
ch.update_series(frames_fps([60.0]))
r = ranges(ch)
check("单帧：X=(0,100)", r[0] == 0.0 and r[1] == 100.0, fmt(r))
check("单帧：Y=(0,144)", r[2] == 0.0 and r[3] == 144.0, fmt(r))

print()
print("=" * 72)
print("A4) 100 帧稳定 100fps → Y 上限恰好 120 = max(120, 100*1.15)")
print("=" * 72)
ch.clear()
ch.update_series(frames_fps([100.0] * 100))
r = ranges(ch)
check("X 下限 = 0", approx(r[0], 0.0), fmt(r))
check("X 上限 = 末帧 idx 99", approx(r[1], 99.0), fmt(r))
check("Y 下限 = 0（锁死）", approx(r[2], 0.0), fmt(r))
check("Y 上限 = 120", approx(r[3], 120.0), fmt(r))
check("Y 不是 [97,101] 窄带", (r[3] - r[2]) > 100.0, "span=%.3f" % (r[3] - r[2]))

print()
print("=" * 72)
print("A5) 含 300fps 尖峰 → Y 上限 ≈ 345 = 300*1.15（绝非窄区间）")
print("=" * 72)
ch.clear()
spiky = [100.0] * 60 + [300.0] * 5 + [100.0] * 60
ch.update_series(frames_fps(spiky))
r = ranges(ch)
check("Y 上限 ≈ 345", approx(r[3], 345.0, 1e-4), fmt(r))
check("Y 下限 = 0", approx(r[2], 0.0), fmt(r))
check("Y 不是 [297,303] 窄区间", (r[3] - r[2]) > 300.0, "span=%.3f" % (r[3] - r[2]))
# 峰值必须完整落在框内（平滑削峰也不能把尖峰裁出去）
check("峰值 300 在框内", 300.0 <= r[3], "top=%.3f" % r[3])

print()
print("=" * 72)
print("A6) 切 ms 视角 → Y=(0,35) + 两条参考虚线可见；切回 FPS → 全隐藏")
print("=" * 72)
ch.set_mode(C.MODE_MS)
r = ranges(ch)
check("ms：Y=(0,35)", r[2] == 0.0 and r[3] == 35.0, fmt(r))
check("ms：两条参考虚线可见", all(ln.isVisible() for ln in ch.ms_ref_lines),
      "visible=%s" % [ln.isVisible() for ln in ch.ms_ref_lines])
check("ms：参考虚线值 = 16.7 / 33.3",
      approx(ch.ms_ref_lines[0].value(), 16.7) and approx(ch.ms_ref_lines[1].value(), 33.3),
      "%s" % [ln.value() for ln in ch.ms_ref_lines])
ch.set_mode(C.MODE_FPS)
check("切回 FPS：两条参考虚线隐藏", not any(ln.isVisible() for ln in ch.ms_ref_lines),
      "visible=%s" % [ln.isVisible() for ln in ch.ms_ref_lines])
r = ranges(ch)
check("切回 FPS：Y 恢复 345", approx(r[3], 345.0, 1e-4), fmt(r))

print()
print("=" * 72)
print("A7) ms 视角：> 35ms 大尖峰才微扩（50ms → 55），≤20ms → 仍 (0,35)")
print("=" * 72)
ch.clear()
ch.set_mode(C.MODE_MS)
ch.clear()
check("ms + clear()：参考虚线也隐藏", not any(ln.isVisible() for ln in ch.ms_ref_lines),
      "visible=%s" % [ln.isVisible() for ln in ch.ms_ref_lines])
ch.update_series(frames_ms([10.0] * 80 + [50.0] * 5 + [10.0] * 80))
r = ranges(ch)
check("50ms 尖峰：Y 上限 = min(50*1.1, 60) = 55", approx(r[3], 55.0), fmt(r))
check("50ms 尖峰：Y 下限 = 0", approx(r[2], 0.0), fmt(r))
ch.update_series(frames_ms([10.0] * 60 + [20.0] * 5 + [10.0] * 60))
r = ranges(ch)
check("≤20ms：Y 仍锁死 (0,35)", r[2] == 0.0 and r[3] == 35.0, fmt(r))
ch.update_series(frames_ms([8.0] * 120))
r = ranges(ch)
check("稳定 8ms：Y 仍锁死 (0,35)", r[2] == 0.0 and r[3] == 35.0, fmt(r))
# 尖峰超过硬上限也只到 60
ch.update_series(frames_ms([10.0] * 40 + [120.0] * 5 + [10.0] * 40))
r = ranges(ch)
check("120ms 尖峰：Y 上限封顶 60", approx(r[3], 60.0), fmt(r))

print()
print("=" * 72)
print("A8) X 轴下限永不为负")
print("=" * 72)
ch.clear()
ch.set_mode(C.MODE_FPS)
ch.update_series(frames_fps([60.0] * 20, idx0=-5))
r = ranges(ch)
check("脏负 idx：X 下限 >= 0", r[0] >= 0.0, fmt(r))
check("脏负 idx：X 上限 = 14", approx(r[1], 14.0), fmt(r))
ch.update_series(frames_fps([60.0] * 20, idx0=0))
r = ranges(ch)
check("首帧 idx=0：X 下限 >= 0", r[0] >= 0.0, fmt(r))
ch.update_series(frames_fps([60.0] * 3000, idx0=0))
r = ranges(ch)
check("3000 帧：X 下限 >= 0 且上限 = 2999", r[0] >= 0.0 and approx(r[1], 2999.0), fmt(r))

print()
print("=" * 72)
print("A9) 主题切换后轴范围仍然正确 + 参考虚线颜色跟随主题")
print("=" * 72)
import stuttertriage.theme as TH

for key, th in TH.THEME_PRESETS.items():
    ch.apply_theme(th)
    r = ranges(ch)
    ok = r[0] >= 0.0 and approx(r[2], 0.0) and approx(r[3], 120.0)
    check("主题 %s 后轴范围仍为 (0,120)" % key, ok, fmt(r))
ch.set_mode(C.MODE_MS)
ch.apply_theme(TH.THEME_PRESETS["capframex"])
check("主题切换后 ms 参考虚线色 = 主题 dim",
      ch.ms_ref_lines[0].pen.color().name().lower() ==
      TH.THEME_PRESETS["capframex"].dim.lower(),
      ch.ms_ref_lines[0].pen.color().name())

print()
print("=" * 72)
print("A10) 手动缩放不被数据刷新覆盖（修「数轴无法放大」）")
print("=" * 72)
ch.set_mode(C.MODE_FPS)
ch.apply_theme(TH.THEME_PRESETS["capframex"])
ch.update_series(frames_fps([60.0] * 120), 60.0, 61.0)
vb = ch.plot_widget.getPlotItem().getViewBox()
check("初始为自动视野（未手动缩放）", ch._user_zoom is False)
vb.setXRange(20, 40, padding=0.0)
vb.setYRange(30, 70, padding=0.0)
vb.sigRangeChangedManually.emit([True, True])     # 等价于滚轮/拖拽
ch.update_series(frames_fps([60.0] * 140), 60.0, 61.0)
r = ranges(ch)
check("刷新后 X 视野保持手动范围", approx(r[0], 20, 1.0) and approx(r[1], 40, 1.0), fmt(r))
check("刷新后 Y 视野保持手动范围", approx(r[2], 30, 1.0) and approx(r[3], 70, 1.0), fmt(r))
ch.set_mode(C.MODE_MS)
r = ranges(ch)
check("切视角后恢复自动轴（Y 0~35）", approx(r[2], 0.0) and approx(r[3], 35.0), fmt(r))
ch.clear()
r = ranges(ch)
check("清空后 X 回到 0~100", approx(r[0], 0.0) and approx(r[1], 100.0), fmt(r))

ch.close()

print()
print("=" * 72)
if fails:
    print("FAILED CHECKS: %s" % fails)
    sys.exit(1)
print("ALL CHART AXIS TESTS PASSED")
print("=" * 72)
