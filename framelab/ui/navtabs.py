# -*- coding: utf-8 -*-
"""
左侧「视图导航卡」—— 改进建议书 一.1。

**废除**旧的「左栏内部展开抽屉」做法：左栏固定 240px，只放 5 张大号导航卡，
每张卡只有两行：

    📈 帧率总览
    当前 142 FPS · 丝滑          ← 一行极简实时状态徽章

左栏**绝不再画进度条或圆环**（240px 宽画了必然溢出 + 逼出滚动条），
所有图形化可视化搬到右侧主画布的全屏页面里（pages.py）。

点击卡片 → `clicked(index)` 信号 → 主窗口把右侧 QStackedWidget 切到对应页面。
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout

from .. import theme as T

# 徽章级别 → 主题色角色
_LEVEL_ROLE = {"ok": "green", "warn": "warn", "danger": "bad",
               "info": "accent", "unknown": "dim"}

# 5 张导航卡的顺序与初始徽章（改进建议书 一.1 逐字对应）
NAV_SPECS = (
    ("📈", "帧率总览", "当前 -- FPS · 等待数据"),
    ("🔥", "CPU 状态", "总占用 --% · -- GHz"),
    ("🎮", "显卡与显存", "核心 --% · 显存 --%"),
    ("💾", "内存与磁盘", "内存 --% · 读盘 -- MB/s"),
    ("🌐", "网络与环境", "↓ -- KB/s · 着色器就绪"),
)


class NavTab(QFrame):
    """一张导航卡：`图标 + 模块名称` 一行，`实时状态徽章` 一行。"""

    clicked = pyqtSignal(int)

    def __init__(self, index, icon, title, badge="等待数据", parent=None):
        super().__init__(parent)
        self.setObjectName("NavTab")
        self.index = int(index)
        self.icon = icon
        self.title = title
        self._badge = badge or ""
        self._level = "unknown"
        self._active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(58)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 10, 8)
        lay.setSpacing(2)
        self.lbl_title = QLabel("")
        self.lbl_badge = QLabel("")
        self.lbl_badge.setWordWrap(False)
        lay.addWidget(self.lbl_title)
        lay.addWidget(self.lbl_badge)
        self.restyle()

    # ---------------- 状态 ----------------
    def set_badge(self, text, level="ok"):
        """实时状态徽章（level: ok / warn / danger / info / unknown）"""
        text = text or ""
        level = level or "ok"
        if text == self._badge and level == self._level:
            return
        self._badge = text
        self._level = level
        self._paint_badge()

    def set_active(self, on):
        on = bool(on)
        if on != self._active:
            self._active = on
            self.restyle()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mouseReleaseEvent(ev)

    # ---------------- 外观 ----------------
    def restyle(self):
        th = T.CURRENT_THEME
        if self._active:
            bg = T._mix(th.panel, th.accent, 0.18)
            edge = th.accent
            tcol = th.accent
        else:
            bg = th.panel2
            edge = th.border
            tcol = th.fg
        self.setStyleSheet(
            "QFrame#NavTab { background-color: %s; border: 1px solid %s;"
            " border-left: 4px solid %s; border-radius: 8px; }" % (bg, th.border, edge))
        self.lbl_title.setText("%s %s" % (self.icon, self.title))
        self.lbl_title.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: %s;" % tcol)
        self._paint_badge()

    def _paint_badge(self):
        th = T.CURRENT_THEME
        col = getattr(th, _LEVEL_ROLE.get(self._level, "dim"), th.dim)
        self.lbl_badge.setText(self._badge)
        self.lbl_badge.setStyleSheet(
            "font-size: 10px; font-weight: bold; color: %s;" % col)


def build_nav_tabs():
    """按改进建议书顺序创建 5 张导航卡（0 号「帧率总览」默认选中）"""
    return [NavTab(i, icon, title, badge)
            for i, (icon, title, badge) in enumerate(NAV_SPECS)]
