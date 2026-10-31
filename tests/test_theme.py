# -*- coding: utf-8 -*-
# 【v4.0 移植】原 _test_theme.py；仅把已废弃的 _update_capframex_series 调用
# 换成新图表的重绘入口 _restyle_chart()。
# -*- coding: utf-8 -*-
"""
主题 / 图表样式 / 快捷键自定义 的自动化测试。

覆盖：
  T1 主题预设完整性 + 全局写回
  T2 自定义背景色派生（深/浅底自动判断 + 对比度可用）
  T3 运行时主题热切换（UI 实际变色）
  T4 主题 / 图表样式持久化（配置文件往返）
  T5 快捷键解析 / 改键 / 持久化 / 重新注册（Windows 同 id 不能替换的坑）
  T6 CapFrameX 图表语义（移动平均 / Stuttering=MA×2.5 / Low FPS 水平线）
"""
import os
import sys
import json
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtGui import QKeySequence

import StutterTriage as FL

fails = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        fails.append(name)


def _free_combo(preferred):
    """
    挑一个当前系统**没有被占用**的组合键。

    【为什么需要】用户可能正开着 StutterTriage（它按配置注册了全局热键），
    或者其它软件占用了 Ctrl+Shift+P 之类常见组合 —— 固定使用某个组合
    会让测试在环境变化时假失败。这里用 RegisterHotKey 探测后立即释放。
    """
    import ctypes
    user32 = ctypes.windll.user32
    for combo in preferred:
        parsed = FL.parse_hotkey(combo)
        if parsed is None:
            continue
        mods, vk = parsed
        if user32.RegisterHotKey(None, 0x91AC, mods, vk):
            user32.UnregisterHotKey(None, 0x91AC)
            return combo
    return None



print("=" * 72)
print("T1) 主题预设完整性 + 全局写回")
print("=" * 72)
for key in ("capframex", "deepspace", "pureblack", "light"):
    check("预设存在: %s" % key, key in FL.THEME_PRESETS)
check("共 4 个预设 + 自定义", len(FL.THEME_PRESETS) == 4)

# CapFrameX 预设的取色必须与源码一致
cx = FL.THEME_PRESETS["capframex"]
check("CapFrameX 帧时间色 = #2197F3 (Constants.cs:9)", cx.accent == "#2197F3", cx.accent)
check("CapFrameX 卡片色 = #252526 (ColorbarView.xaml.cs:268)", cx.panel == "#252526", cx.panel)
check("CapFrameX 图表底 = #2d2d30 (ColorbarView.xaml.cs:271)", cx.chart_bg == "#2d2d30", cx.chart_bg)
check("CapFrameX 低 FPS 色 = #FFB400", cx.warn == "#FFB400", cx.warn)

# 写回测试
orig_bg = FL.BG
FL.apply_theme_to_globals(FL.THEME_LIGHT)
check("写回后 BG 变为浅色", FL.BG == FL.THEME_LIGHT.bg, FL.BG)
check("写回后 ACCENT 跟随", FL.ACCENT == FL.THEME_LIGHT.accent, FL.ACCENT)
FL.apply_theme_to_globals(FL.THEME_DEEP_SPACE)
check("可写回原主题", FL.BG == orig_bg or FL.BG == FL.THEME_DEEP_SPACE.bg, FL.BG)

print()
print("=" * 72)
print("T2) 自定义背景色派生")
print("=" * 72)
t_dark = FL.theme_from_custom_bg("#123456")
check("深色底自动判定", t_dark.is_dark is True)
check("深色底文字为浅色", t_dark.fg.lower() in ("#eef2f7",) or t_dark.fg.startswith("#e"),
      t_dark.fg)
check("深色底面板与背景不同", t_dark.panel != t_dark.bg, t_dark.panel)

t_light = FL.theme_from_custom_bg("#f0f0f0")
check("浅色底自动判定", t_light.is_dark is False)
check("浅色底文字为深色", t_light.fg.startswith("#1") or t_light.fg.startswith("#2"), t_light.fg)

t_bad = FL.theme_from_custom_bg("not-a-color")
check("非法颜色安全回落", t_bad is not None and t_bad.bg.startswith("#"), t_bad.bg)

# 对比度检查（WCAG 近似）
def lum(c):
    r = int(c[1:3], 16) / 255.0
    g = int(c[3:5], 16) / 255.0
    b = int(c[5:7], 16) / 255.0
    f = lambda x: x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

for key, th in FL.THEME_PRESETS.items():
    l1, l2 = lum(th.bg), lum(th.fg)
    ratio = (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)
    check("主题 %s 文字对比度 >= 4.5:1" % key, ratio >= 4.5, "ratio=%.1f" % ratio)

print()
print("=" * 72)
print("T3) 运行时主题热切换（UI 实际变色）")
print("=" * 72)
app = QtWidgets.QApplication(sys.argv)
win = FL.StutterTriageWindow()

for key, th in FL.THEME_PRESETS.items():
    win._apply_theme(th.copy(), persist=False)
    sheet = win.styleSheet()
    check("切换到 %s 后 QSS 含该主题背景" % key,
          ("background-color: %s" % th.bg) in sheet, th.bg)
    # 图表背景：ViewBox 的背景 rect item
    try:
        brush = win.plot_widget.plotItem.vb.background.brush()
        got = brush.color().name().lower()
        check("切换到 %s 后图表背景跟随" % key,
              got == th.chart_bg.lower(), "got=%s want=%s" % (got, th.chart_bg))
    except Exception as ex:
        check("切换到 %s 后图表背景跟随" % key, False, repr(ex))

# 回到默认
win._apply_theme(FL.THEME_DEEP_SPACE.copy(), persist=False)
check("主题切换后主循环仍可用", True)
win._refresh_ui()
check("切换后 _refresh_ui 不抛异常", True)

print()
print("=" * 72)
print("T4) 主题 / 图表样式持久化")
print("=" * 72)
cfg_path = os.path.join(os.path.dirname(os.path.abspath(FL.__file__)), "stuttertriage_config.json")
try:
    backup = open(cfg_path, encoding="utf-8").read()
except Exception:
    backup = None

win._apply_theme(FL.THEME_PURE_BLACK.copy())
win._on_chart_style_changed(FL.CHART_STYLE_HUD)
data = json.load(open(cfg_path, encoding="utf-8"))
check("theme 已持久化", data.get("theme") == "pureblack", data.get("theme"))
check("chart_style 已持久化", data.get("chart_style") == "hud", data.get("chart_style"))

# 改回
win._apply_theme(FL.THEME_CAPFRAMEX.copy())
win._on_chart_style_changed(FL.CHART_STYLE_CAPFRAMEX)
data = json.load(open(cfg_path, encoding="utf-8"))
check("可改回 capframex", data.get("theme") == "capframex", data.get("theme"))

print()
print("=" * 72)
print("T5) 快捷键自定义")
print("=" * 72)
check("QKeySequenceEdit 已导入", hasattr(QtWidgets, "QKeySequenceEdit"))
check("设置页有可视化改键框", hasattr(win, "edit_hotkey"))
check("改键框有当前值", bool(win.edit_hotkey.keySequence().toString()),
      repr(win.edit_hotkey.keySequence().toString()))

# 解析
for combo in ("Ctrl+F9", "Ctrl+Shift+M", "Alt+F1", "Ctrl+Alt+Delete"):
    parsed = FL.parse_hotkey(combo)
    check("解析 %s" % combo, parsed is not None, str(parsed))
check("非法组合被拒", FL.parse_hotkey("F9+F9") is None)
check("空串被拒", FL.parse_hotkey("") is None)

# 改键 → 重新注册（关键：Windows 同 id 不能替换）
# 组合键动态挑选：用户可能正开着 StutterTriage（占用了它配置里的热键），
# 或其它软件占用常见组合 —— 固定组合会让本测试假失败。
if win._hotkey:
    combo1 = _free_combo(("Ctrl+Shift+P", "Ctrl+Shift+F11", "Ctrl+Alt+F10",
                          "Ctrl+Shift+F12")) or "Ctrl+Shift+P"
    combo2 = _free_combo(("Alt+F8", "Alt+F10", "Alt+F11")) or "Alt+F8"
    win._hotkey.unregister()
    win.edit_hotkey.setKeySequence(QKeySequence(combo1))
    win._on_apply_hotkey_sequence()
    check("改键后 hotkey_text 已更新", win._hotkey.hotkey_text == combo1,
          win._hotkey.hotkey_text)
    check("改键后注册成功", win._hotkey.available, win._hotkey.error or "")
    data = json.load(open(cfg_path, encoding="utf-8"))
    check("改键已持久化", data.get("hotkey") == combo1, data.get("hotkey"))

    # 再改成另一个，验证「同 id 替换」的坑已修
    win._hotkey.unregister()
    win.edit_hotkey.setKeySequence(QKeySequence(combo2))
    win._on_apply_hotkey_sequence()
    check("连续第二次改键也成功", win._hotkey.available and win._hotkey.hotkey_text == combo2,
          "%s | %s" % (win._hotkey.hotkey_text, win._hotkey.error))

# 恢复默认
win._on_reset_hotkey()
check("恢复默认热键", win._hotkey.hotkey_text == "Ctrl+F9", win._hotkey.hotkey_text)

print()
print("=" * 72)
print("T6) CapFrameX 图表语义")
print("=" * 72)
check("Stuttering 系数 = 2.5 (CapFrameXConfiguration.cs:417)",
      FL.CX_STUTTERING_FACTOR == 2.5, str(FL.CX_STUTTERING_FACTOR))
check("Low FPS 阈值 = 30 FPS", FL.CX_LOW_FPS_THRESHOLD == 30.0, str(FL.CX_LOW_FPS_THRESHOLD))
check("移动平均色 = #8B6A60 (Constants.cs:7)",
      FL.CX_COLOR_MOVING_AVG == "#8B6A60", FL.CX_COLOR_MOVING_AVG)

# ---- v4.0：旧包络线 / 移动平均 / Low FPS 水平线已按改进建议书删除 ----
# 新图表契约：1 条主折线 + 1 条平均帧虚线 + 漏帧红点散点
win._restyle_chart()
_ch = win.fps_chart
check("主折线存在", _ch.curve is not None, "")
check("平均帧基准虚线存在", _ch.avg_line is not None, "")
check("漏帧红点散点存在", _ch.scatter is not None, "")
win._on_chart_style_changed(FL.CHART_STYLE_CAPFRAMEX)
check("CapFrameX 样式可切换（不抛异常）", True, "")
win._on_chart_style_changed(FL.CHART_STYLE_HUD)
check("HUD 渐变填充样式可切换（不抛异常）", True, "")
win._on_chart_style_changed(FL.CHART_STYLE_HUD)
win._on_chart_style_changed(FL.CHART_STYLE_CAPFRAMEX)

print()
print("=" * 72)
win.hw_engine.stop()
if win.pm_bridge:
    win.pm_bridge.stop(wait=False)
if backup is not None:
    try:
        open(cfg_path, "w", encoding="utf-8").write(backup)
    except Exception:
        pass

if fails:
    print("FAILED CHECKS: %s" % fails)
    sys.exit(1)
print("ALL THEME/HOTKEY/CHART TESTS PASSED")
print("=" * 72)
