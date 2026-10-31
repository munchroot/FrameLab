# -*- coding: utf-8 -*-

"""主题系统：配色预设、运行时热切换、图表语义常量、pyqtgraph 菜单汉化表、磁贴阴影。"""

import pyqtgraph as pg
from PyQt6 import QtGui, QtWidgets

pg.setConfigOptions(antialias=False, background="#0a0e15", foreground="#8b98ad")


def _mix(color, other, ratio):
    """把 color 与 other 按 ratio 混合（0=纯 color，1=纯 other）"""
    try:
        c = QtGui.QColor(color)
        o = QtGui.QColor(other)
        return "#%02x%02x%02x" % (
            int(c.red() * (1 - ratio) + o.red() * ratio),
            int(c.green() * (1 - ratio) + o.green() * ratio),
            int(c.blue() * (1 - ratio) + o.blue() * ratio))
    except Exception:
        return color


def mix(color, other, ratio):
    """
    公开版 `_mix`：给 UI 模块做**主题化混色**用（行底色 / 半透明强调底 / 边框）。

    【为什么必须公开】v1.0.1 里归因表用了写死的 `#3d1418` / `#38240b` 当告警行底色，
    浅色主题下依旧是深红/深棕，深色文字压在上面完全看不清。凡是"底色"都必须从
    当前主题的 panel2 与语义色混合出来，不能写死。
    """
    return _mix(color, other, ratio)


def luminance(color):
    """0.0~1.0 感知亮度（0.299R + 0.587G + 0.114B）—— 用来判断该往白还是往黑推。"""
    try:
        c = QtGui.QColor(color)
        return (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255.0
    except Exception:
        return 0.5


def contrast_on(color, bg, need=0.30, steps=3):
    """
    把 `color` 朝着「远离底色亮度」的方向推，直到与 `bg` 的亮度差 ≥ need。

    用于"深色底上的暗红字"这类低对比组合：保证任何主题下语义色都读得出来。
    """
    target = "#ffffff" if luminance(bg) < 0.5 else "#000000"
    out = color
    for _ in range(max(0, int(steps))):
        if abs(luminance(out) - luminance(bg)) >= need:
            break
        out = _mix(out, target, 0.30)
    return out


# KV 规范的基准蓝灰（标签胶囊的色相来源）：低明度、偏冷、与主题强调色同族
_KV_BLUE = "#4a6b96"



# ==============================================================================
# §1  主题系统（可运行时热切换）
# ==============================================================================
#
# 设计要点：
#   · 颜色不再是「写死的模块常量」，而是由**当前主题对象统一提供**。
#     主题切换时通过 apply_theme_to_globals() 把当前主题写回这些模块级名字，
#     于是所有既有的 setStyleSheet("... color: %s" % ACCENT) 调用无需改动，
#     就能自动跟随主题 —— 这是改动量最小的做法。
#   · 图表样式（CapFrameX / HUD）是**独立于配色**的另一个维度，
#     由 chart_style 控制，两者可自由组合。
#   · 用户可自定义背景色；自定义时由该背景色派生出整套面板/边框/文字色，
#     保证对比度可用，而不是简单地只改一个背景导致文字看不见。
class Theme:
    """一套配色。字段名与旧的模块常量一一对应，便于批量写回。"""

    __slots__ = ("key", "name", "bg", "panel", "panel2", "border", "border_hot",
                 "fg", "dim", "accent", "accent_d", "warn", "bad", "purple",
                 "orange", "green", "chart_bg", "chart_grid", "chart_text",
                 "is_dark", "custom_bg",
                 # 【v1.0.1 KV 配色规范】由已有颜色派生，不占构造参数
                 "key_bg", "key_fg", "val")

    def __init__(self, key, name, bg, panel, panel2, border, border_hot, fg, dim,
                 accent, accent_d, warn, bad, purple, orange, green,
                 chart_bg, chart_grid, chart_text, is_dark=True, custom_bg=None):
        self.key = key
        self.name = name
        self.bg = bg
        self.panel = panel
        self.panel2 = panel2
        self.border = border
        self.border_hot = border_hot
        self.fg = fg
        self.dim = dim
        self.accent = accent
        self.accent_d = accent_d
        self.warn = warn
        self.bad = bad
        self.purple = purple
        self.orange = orange
        self.green = green
        self.chart_bg = chart_bg
        self.chart_grid = chart_grid
        self.chart_text = chart_text
        self.is_dark = is_dark
        self.custom_bg = custom_bg

        # ---- KV 配色规范（v1.0.1）----
        # 指标名（Key）统一用**低明度蓝灰**胶囊底 + 同色系文字；
        # 数值（Value）统一用**高亮纯白**（浅色主题用最深墨色，保证对比度）+ 强调色。
        # 这样「标签」和「数值」在色相/明度上永远是两级，扫一眼就能分开。
        if is_dark:
            self.key_bg = _mix(panel2, _KV_BLUE, 0.30)      # 蓝灰底，低明度
            # 标签字用「蓝灰」色相但保持可读（对 key_bg 对比度 ≈5:1）：
            # 低明度体现在**胶囊底**上，文字再暗就看不清了，那就违背"可读"底线。
            self.key_fg = _mix(fg, _KV_BLUE, 0.45)
            self.val = "#ffffff"
        else:
            self.key_bg = _mix(panel2, _KV_BLUE, 0.16)      # 浅底上的淡蓝灰
            self.key_fg = _mix(dim, "#1b2733", 0.35)        # 深蓝灰字，压得住浅底
            self.val = "#0b1524"

    def copy(self):
        return Theme(self.key, self.name, self.bg, self.panel, self.panel2,
                    self.border, self.border_hot, self.fg, self.dim,
                    self.accent, self.accent_d, self.warn, self.bad, self.purple,
                    self.orange, self.green, self.chart_bg, self.chart_grid,
                    self.chart_text, self.is_dark, self.custom_bg)




# ---------------------------------------------------------------- 主题预设
# 「CapFrameX 暗色」严格对齐 CapFrameX 源码真实取值：
#   Constants.cs:9   FrametimeColor = FromRgb(34,151,243) = #2197F3（帧时间主曲线）
#   Constants.cs:11  GpuActiveTimeColor = FromRgb(254,201,27) = #FEC91B
#   Constants.cs:7   FrametimeMovingAverageColor = FromArgb(200,139,106,96) = #8B6A60 @78%
#   FrametimePlotBuilder.cs:137 CPU-Busy = FromRgb(100,149,237) = #6495ED
#   FrametimePlotBuilder.cs:156 Stuttering = FromAColor(180,Red) = #FF0000 @71% (Dash)
#   FrametimePlotBuilder.cs:166 Low FPS = FromAColor(180,RGB(255,180,0)) = #FFB400 @71% (LongDash)
#   FrametimeGraphDataContext.cs:90 网格线 = FromArgb(40,204,204,204)
#   ColorbarView.xaml.cs:271 图表画布背景 = #2d2d30
THEME_CAPFRAMEX = Theme(
    key="capframex", name="CapFrameX 暗色",
    bg="#1e1e1e", panel="#252526", panel2="#2d2d30",
    border="#3a3a3a", border_hot="#4f4f4f",
    fg="#f0f0f0", dim="#94A3B8",
    accent="#2197F3", accent_d="#1565c0",
    warn="#FFB400", bad="#FF5252", purple="#b39ddb", orange="#ff7043", green="#4caf50",
    chart_bg="#2d2d30", chart_grid="#3d3d3d", chart_text="#c8c8c8",
    is_dark=True)


THEME_DEEP_SPACE = Theme(
    key="deepspace", name="深空黑（赛博）",
    bg="#0a0e15", panel="#121826", panel2="#1a2438",
    border="#24304a", border_hot="#3b4f7a",
    fg="#e9effa", dim="#94A3B8",
    accent="#19d3ff", accent_d="#0f7ea6",
    warn="#ffb02e", bad="#ff5d5d", purple="#c792ea", orange="#ff7043", green="#2ee6a8",
    chart_bg="#0a0e15", chart_grid="#1c2740", chart_text="#94A3B8",
    is_dark=True)


THEME_PURE_BLACK = Theme(
    key="pureblack", name="纯黑（极简）",
    bg="#000000", panel="#0d0d0d", panel2="#151515",
    border="#262626", border_hot="#3d3d3d",
    fg="#f2f2f2", dim="#94A3B8",
    accent="#2197F3", accent_d="#1565c0",
    warn="#FFB400", bad="#FF5252", purple="#b39ddb", orange="#ff7043", green="#4caf50",
    chart_bg="#000000", chart_grid="#1f1f1f", chart_text="#94A3B8",
    is_dark=True)


THEME_LIGHT = Theme(
    key="light", name="浅色（白底）",
    bg="#f5f6f8", panel="#ffffff", panel2="#eceff3",
    border="#d3d8de", border_hot="#9aa6b2",
    # 【v1.0.1 字体层级】浅色主题下禁止浅灰虚字：
    #   fg  = #1E293B（深岩石黑，主文字）
    #   dim = #475569（沉稳石板灰，次级文字；不许比 #64748B 更浅）
    # 【v1.0.3 修「红色/橙色在白底看不清」】语义色一律按**白底**重新取：
    #   每个色 vs {white, #eceff3, #f5f6f8} 的 WCAG 对比度都要 ≥4.5:1
    #   （warn  #b26a00 最低只有 3.67 → #9a5b00 提到 4.70
    #     orange #d84315 最低只有 3.85 → #c23c11 提到 4.61）
    fg="#1E293B", dim="#475569",
    accent="#1565c0", accent_d="#0d47a1",
    warn="#9a5b00", bad="#c62828", purple="#6a3fa0", orange="#c23c11", green="#1b5e20",
    chart_bg="#ffffff", chart_grid="#dfe3e8", chart_text="#475569",
    is_dark=False)


THEME_PRESETS = {
    THEME_CAPFRAMEX.key: THEME_CAPFRAMEX,
    THEME_DEEP_SPACE.key: THEME_DEEP_SPACE,
    THEME_PURE_BLACK.key: THEME_PURE_BLACK,
    THEME_LIGHT.key: THEME_LIGHT,
}


def theme_from_custom_bg(bg, name="自定义"):
    """
    由用户自定义的背景色派生出一整套配色。

    不允许「只改背景」——那会让文字/面板全部失去对比度。
    这里按亮度自动判断深浅底，再派生出面板、边框、文字与图表配色。
    """
    try:
        c = QtGui.QColor(bg)
        if not c.isValid():
            raise ValueError("invalid color")
    except Exception:
        return THEME_DEEP_SPACE.copy()
    lum = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255.0
    dark = lum < 0.5
    if dark:
        panel = _mix(bg, "#ffffff", 0.06)
        panel2 = _mix(bg, "#ffffff", 0.12)
        border = _mix(bg, "#ffffff", 0.20)
        border_hot = _mix(bg, "#ffffff", 0.32)
        fg = "#eef2f7"
        dim = _mix(fg, bg, 0.42)
        chart_grid = _mix(bg, "#ffffff", 0.16)
        chart_text = dim
    else:
        panel = _mix(bg, "#000000", 0.04)
        panel2 = _mix(bg, "#000000", 0.09)
        border = _mix(bg, "#000000", 0.16)
        border_hot = _mix(bg, "#000000", 0.28)
        fg = "#16191d"
        dim = _mix(fg, bg, 0.42)
        chart_grid = _mix(bg, "#000000", 0.12)
        chart_text = dim
    return Theme(
        key="custom", name=name, bg=bg, panel=panel, panel2=panel2,
        border=border, border_hot=border_hot, fg=fg, dim=dim,
        accent="#19d3ff" if dark else "#1565c0",
        accent_d="#0f7ea6" if dark else "#0d47a1",
        warn="#ffb02e" if dark else "#9a5b00",
        bad="#ff5d5d" if dark else "#c62828",
        purple="#c792ea" if dark else "#6a3fa0",
        orange="#ff7043" if dark else "#c23c11",
        green="#2ee6a8" if dark else "#1b5e20",
        chart_bg=bg, chart_grid=chart_grid, chart_text=chart_text,
        is_dark=dark, custom_bg=bg)




# 当前主题（模块级单一事实来源）
CURRENT_THEME = THEME_DEEP_SPACE


def _write_colors(theme, g):
    """把一套配色写进给定命名空间（只写颜色，不动 CURRENT_THEME）"""
    g["BG"] = theme.bg
    g["PANEL"] = theme.panel
    g["PANEL2"] = theme.panel2
    g["BORDER"] = theme.border
    g["BORDER_HOT"] = theme.border_hot
    g["FG"] = theme.fg
    g["DIM"] = theme.dim
    g["ACCENT"] = theme.accent
    g["ACCENT_D"] = theme.accent_d
    g["WARN"] = theme.warn
    g["BAD"] = theme.bad
    g["PURPLE"] = theme.purple
    g["ORANGE"] = theme.orange
    g["GREEN"] = theme.green


def apply_theme_to_globals(theme, module_globals=None):
    """
    把主题写回模块级颜色常量。

    既有代码里大量 setStyleSheet("color: %s" % ACCENT) 这类写法，
    通过写回同名模块变量即可**零改动**地跟随主题。

    v4.0（拆分后）两种用法：
      · apply_theme_to_globals(theme)            —— 全局热切换：
        写回 theme 自身 + **所有已登记模块**（等价 set_theme，保持旧调用语义）
      · apply_theme_to_globals(theme, some_dict) —— 只写指定的命名空间
    """
    global CURRENT_THEME
    if module_globals is not None:
        _write_colors(theme, module_globals)
        try:
            pg.setConfigOptions(background=theme.chart_bg, foreground=theme.chart_text)
        except Exception:
            pass
        return theme
    CURRENT_THEME = theme
    _write_colors(theme, globals())
    for g in list(globals().get("_BOUND_GLOBALS", [])):
        _write_colors(theme, g)
        if "CURRENT_THEME" in g:
            g["CURRENT_THEME"] = theme
    try:
        pg.setConfigOptions(background=theme.chart_bg, foreground=theme.chart_text)
    except Exception:
        pass
    return theme


# ==============================================================================
# 调色板（QPalette）—— 让 QSS **覆盖不到**的原生表面也跟随主题
# ------------------------------------------------------------------------------
# 【v4.2 修 Bug：暗色主题下「设置」看不清】
# 现象：切到深空黑/纯黑后，点右上角 ⚙ 弹出的菜单几乎整块是浅灰底 + 浅色文字
#       （完全看不清）；「⚙ 设置」页整页是一块白底，未显式上色的标签也看不清。
# 根因：`_apply_global_stylesheet()` 里只写了 `QWidget { color: fg }`，
#       却**没有**给 QMenu / QDialog / QScrollArea 视口设背景色。
#       于是文字被强制成浅色，而背景仍是 Fusion 默认调色板的浅灰 —— 浅字压浅底。
# 处理：除在 QSS 里补 QMenu/QDialog/viewport 规则外，再把整套主题色写进
#       QApplication 的调色板，覆盖所有未被 QSS 显式命名的原生控件。
# ==============================================================================

def build_palette(theme=None):
    """
    按当前主题构造 `QPalette`（分组的深浅两套语义都照顾到）。

    · Window / Base 用背景与面板色（菜单、对话框、滚动区视口的底色）；
    · Text / WindowText / ButtonText 用前景色（保证与上面底色成对出现）；
    · Highlight 用强调深色 + 白色高亮文字（选中项在深浅主题下都够亮）；
    · Disabled 组单独给 dim，禁用态「灰但仍可读」，不会变成看不清的浅灰。
    """
    from PyQt6.QtGui import QColor, QPalette
    t = theme or CURRENT_THEME

    pal = QPalette()
    role = QPalette.ColorRole
    grp = QPalette.ColorGroup

    pal.setColor(role.Window, QColor(t.bg))
    pal.setColor(role.WindowText, QColor(t.fg))
    pal.setColor(role.Base, QColor(t.panel))
    pal.setColor(role.AlternateBase, QColor(t.panel2))
    pal.setColor(role.Text, QColor(t.fg))
    pal.setColor(role.Button, QColor(t.panel2))
    pal.setColor(role.ButtonText, QColor(t.fg))
    pal.setColor(role.ToolTipBase, QColor(t.panel2))
    pal.setColor(role.ToolTipText, QColor(t.fg))
    pal.setColor(role.Highlight, QColor(t.accent_d))
    pal.setColor(role.HighlightedText, QColor("#ffffff"))
    pal.setColor(role.Mid, QColor(t.border))
    pal.setColor(role.BrightText, QColor(t.bad))
    try:
        pal.setColor(role.PlaceholderText, QColor(t.dim))
    except Exception:
        pass

    # 禁用态：统一走 dim（不要用 Fusion 默认的"灰到看不见"）
    for r in (role.WindowText, role.Text, role.ButtonText):
        try:
            pal.setColor(grp.Disabled, r, QColor(t.dim))
        except Exception:
            pass
    for r, col in ((role.Base, t.panel), (role.Button, t.panel2), (role.Window, t.bg)):
        try:
            pal.setColor(grp.Disabled, r, QColor(col))
        except Exception:
            pass
    return pal


def apply_app_palette(theme=None, widget=None):
    """
    把主题调色板下发给 QApplication（以及可选的具体 widget）。

    返回 True/False 表示是否真的写进了 QApplication。
    """
    ok = False
    pal = build_palette(theme)
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.setPalette(pal)
            ok = True
    except Exception:
        pass
    if widget is not None:
        try:
            widget.setPalette(pal)
        except Exception:
            pass
    return ok
# ------------------------------------------------------------------------------
# 单体时代所有代码共享同一个模块 globals，apply_theme_to_globals 改一次全体生效。
# 拆成多模块后 `from .theme import ACCENT` 只会复制**当时的值**，主题切换即失效。
# 因此每个"用到颜色"的模块在导入时调用 bind_globals(globals())：
#   · 把当前主题色写入自己的模块级名字（保持 % ACCENT 这类老写法可用）；
#   · 把自己登记进注册表，set_theme() 时被重新刷新。
# ==============================================================================

_BOUND_GLOBALS = []

_LIVE_NAMES = ("BG", "PANEL", "PANEL2", "BORDER", "BORDER_HOT", "FG", "DIM",
               "ACCENT", "ACCENT_D", "WARN", "BAD", "PURPLE", "ORANGE", "GREEN",
               "CYAN_DIM", "FONT_FAMILY", "MONO_FAMILY",
               "CURRENT_THEME", "CURRENT_CHART_STYLE")


def bind_globals(g):
    """把当前主题色副本注入模块 g，并登记 g 以便后续主题切换时刷新。"""
    for name in _LIVE_NAMES:
        if name in globals():
            g[name] = globals()[name]
    if g not in _BOUND_GLOBALS:
        _BOUND_GLOBALS.append(g)
    return g


def bound_globals():
    """当前已登记的模块 globals 列表（只读用途）"""
    return list(_BOUND_GLOBALS)


def set_theme(theme):
    """
    切换当前主题（**唯一推荐入口**）：

      · 更新 CURRENT_THEME；
      · 把新配色写回所有已登记模块的模块级名字。
    返回新主题，便于链式调用。
    """
    global CURRENT_THEME
    CURRENT_THEME = theme
    for g in _BOUND_GLOBALS:
        apply_theme_to_globals(theme, g)
        if "CURRENT_THEME" in g:
            g["CURRENT_THEME"] = theme
    return theme


def set_chart_style(style):
    """切换图表样式（capframex / hud），并同步到所有已登记模块"""
    global CURRENT_CHART_STYLE
    CURRENT_CHART_STYLE = style
    for g in _BOUND_GLOBALS:
        if "CURRENT_CHART_STYLE" in g:
            g["CURRENT_CHART_STYLE"] = style
    return style


def theme_keys():
    return list(THEME_PRESETS.keys()) + ["custom"]


def resolve_theme(key, custom_bg=""):
    """按 key 取主题对象；key == 'custom' 时由自定义背景色派生"""
    if key == "custom":
        return theme_from_custom_bg(custom_bg or CURRENT_THEME.bg)
    return THEME_PRESETS.get(key, CURRENT_THEME)


# 初始主题写回模块常量（保持向后兼容：老代码继续能用 BG/ACCENT 等名字）
apply_theme_to_globals(CURRENT_THEME)



# 图表样式：'capframex'（CapFrameX 为主，默认）| 'hud'（赛博 HUD）
CHART_STYLE_CAPFRAMEX = "capframex"


CHART_STYLE_HUD = "hud"


CURRENT_CHART_STYLE = CHART_STYLE_CAPFRAMEX



# ---- CapFrameX 图表语义常量（全部取自 CapFrameX 源码） ----
CX_STUTTERING_FACTOR = 2.5      # CapFrameXConfiguration.cs:417 默认 StutteringFactor


CX_LOW_FPS_THRESHOLD = 30.0     # 默认 Low FPS 阈值（帧率），对应帧时间 1000/30 ms


CX_COLOR_FRAMETIME = "#2197F3"  # Constants.cs:9


CX_COLOR_MOVING_AVG = "#8B6A60"  # Constants.cs:7  @78%


CX_COLOR_STUTTERING = (255, 0, 0, 180)      # FrametimePlotBuilder.cs:156 @71% Dash


CX_COLOR_LOWFPS = (255, 180, 0, 180)        # FrametimePlotBuilder.cs:166 @71% LongDash


CX_COLOR_GPU_BUSY = "#FEC91B"   # Constants.cs:11


CX_COLOR_CPU_BUSY = "#6495ED"   # FrametimePlotBuilder.cs:137



# ---- 图表交互「双模式」--------------------------------------------------------
# free     : 自由缩放（极客模式，默认）—— 滚轮缩放 / 拖拽平移，只渲染最近 2500 帧
# overview : 全局概览（体检模式）—— 禁用鼠标拖拽，X 轴每次刷新强制铺满
#            FrameStore 里【所有帧】，整局曲线随时间自动向左挤压
CHART_MODE_FREE = "free"


CHART_MODE_OVERVIEW = "overview"



# ---- pyqtgraph 右键菜单汉化表 --------------------------------------------------
# 覆盖 pyqtgraph 0.13/0.14 的 ViewBox / PlotItem 菜单全部文案：
#   · ViewBoxMenu.py：View All / X axis / Y axis / Mouse Mode / 1 button / 3 button
#   · axisCtrlTemplate_generic.py（轴子菜单内的 QWidgetAction 控件）：
#     Link Axis: / Auto / Manual / Invert Axis / Mouse Enabled / Visible Data Only / Auto Pan Only
#   · PlotItem.ctrlMenu「Plot Options」内的复选框（Downsample / Peak / Mean …）
# 递归遍历 getContextMenus(None) 返回的 QAction、其子菜单、以及 QWidgetAction
# 默认控件里的 QLabel/QCheckBox/QRadioButton 文本，按「去掉 & 助记符后的文本」
# 精确匹配替换；未收录的条目保持英文原文（宁可不译，不可错译）。
PG_MENU_ZH = {
    # 顶层 / 轴子菜单
    "ViewBox options": "视图选项",
    "View All": "显示全部",
    "X Axis": "X 轴",
    "Y Axis": "Y 轴",
    "X axis": "X 轴",
    "Y axis": "Y 轴",
    "Manual": "手动",
    "Auto": "自动",
    "Auto Range": "自动范围",
    "Invert": "反转轴",
    "Invert Axis": "反转轴",
    "Link Axis:": "联动轴：",
    "Mouse Enabled": "启用鼠标",
    "Mouse Disabled": "禁用鼠标",
    "Visible Data Only": "仅可见数据",
    "Auto Pan Only": "仅自动跟随",
    # 鼠标模式
    "Mouse Mode": "鼠标模式",
    "1 button": "单键模式",
    "3 button": "三键模式",
    # Plot Options（绘图选项）
    "Plot Options": "绘图选项",
    "Average": "均值曲线",
    "Downsample": "降采样",
    "Auto Downsample": "自动降采样",
    "Clip to View": "裁剪到视图内",
    "Max Traces:": "最大轨迹数：",
    "Peak": "峰值",
    "Mean": "均值",
    "Subsample": "等距抽样",
    "Forget hidden traces": "遗忘隐藏轨迹",
    "Antialias": "抗锯齿",
    "Alpha": "透明度",
    "Opacity": "不透明度",
    "Log X": "X 对数轴",
    "Log Y": "Y 对数轴",
    "dy/dx": "dy/dx 导数",
    "Y vs. Y'": "Y 对 Y' 相图",
    "Power Spectrum (FFT)": "功率谱 (FFT)",
    "Subtract Mean": "减去均值",
    "Show X Grid": "显示 X 网格",
    "Show Y Grid": "显示 Y 网格",
    # 其它
    "Grid": "网格",
    "Transforms": "坐标变换",
    "Rotate": "旋转",
    "Scale": "比例",
    "Zoom": "缩放",
    "Link": "视图联动",
    "Export...": "导出…",
    "Window": "窗口",
    "Input": "输入",
}


def attach_shadow(widget, blur=22, dy=3, alpha=90):
    """
    给卡片挂上 QGraphicsDropShadowEffect，制造 Web 端卡片悬浮立体感。

    【注意】不要给包含 pyqtgraph PlotWidget 的图表卡挂阴影：
    图形特效会把整个子树重绘进像素缓冲再做模糊，图表以 30 FPS 重绘时
    每帧都要全量模糊一次（1400×600 画布 ≈ 数 ms/帧），得不偿失。
    因此阴影只用于静态卡片（顶栏 / 横幅 / 指标卡 / 抽屉）。
    """
    try:
        eff = QtWidgets.QGraphicsDropShadowEffect(widget)
        eff.setBlurRadius(blur)
        eff.setOffset(0, dy)
        eff.setColor(QtGui.QColor(0, 0, 0, alpha))
        widget.setGraphicsEffect(eff)
        return eff
    except Exception:
        return None



# ==============================================================================
# §1b 旧版模块级常量（保留名字，由主题写回）
# ==============================================================================
BG         = CURRENT_THEME.bg      # 主背景


PANEL      = CURRENT_THEME.panel   # 卡片容器


PANEL2     = CURRENT_THEME.panel2  # 悬浮/次级背景


BORDER     = CURRENT_THEME.border  # 描边


BORDER_HOT = CURRENT_THEME.border_hot


FG         = CURRENT_THEME.fg      # 主文字


DIM        = CURRENT_THEME.dim     # 注脚


ACCENT     = CURRENT_THEME.accent  # 帧时间主曲线


ACCENT_D   = CURRENT_THEME.accent_d


WARN       = CURRENT_THEME.warn    # 1% Low / 警告


BAD        = CURRENT_THEME.bad     # 0.1% Low / 严重卡顿


PURPLE     = CURRENT_THEME.purple  # 内存 / 缺页


ORANGE     = CURRENT_THEME.orange  # CPU0 拥堵


GREEN      = CURRENT_THEME.green   # 丝滑正常


CYAN_DIM   = "#0d5f78"


FONT_FAMILY = "Segoe UI, Microsoft YaHei UI, sans-serif"


MONO_FAMILY = "Consolas, Liberation Mono, Courier New, monospace"


# 本模块自身也纳入注册表：set_theme 会把新配色写回 theme 自己的 globals
if globals() not in _BOUND_GLOBALS:
    _BOUND_GLOBALS.append(globals())
