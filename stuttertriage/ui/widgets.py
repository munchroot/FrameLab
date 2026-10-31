# -*- coding: utf-8 -*-

"""通用可视化组件：单行省略标签 / 卡顿条 / 指标卡 / 探针读数条。"""

import math
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import Qt
from ..theme import MONO_FAMILY

# ---- 主题/阈值热更新绑定（本模块持有可变副本）----
from ..theme import bind_globals as _bind_theme_globals
_bind_theme_globals(globals())



class ElidedLabel(QLabel):
    """
    单行省略标签 —— v1.0.0「版面零抖动」的基石。

    【为什么要有这个东西】这些标签原来是 wordWrap=True 的普通 QLabel：
    文案一长就从 1 行变 2 行，顶部横幅高度 92px → 100px，下面的 4 张核心卡
    被顶着上下跳（用户主诉的「卡牌会随着曲线的出现而上下动」）。
    现在一律「固定一行高 + 超宽用 … 截断 + 全文进 tooltip」：
    高度恒等于一行，文本怎么变都不会推挤其它控件。

    用法与 QLabel 完全一致；需要拿完整文案时用 fullText()。
    """

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = ""
        self._tip_on = True
        self.setWordWrap(False)
        sp = QtWidgets.QSizePolicy
        # 横向可压可伸（Ignored = 不参与最小宽度计算），纵向恒一行高
        self.setSizePolicy(sp.Policy.Ignored, sp.Policy.Fixed)
        self.setText(text)

    # ------------------------------------------------------------------ 对外
    def fullText(self):
        return self._full

    def set_tooltip_enabled(self, on):
        self._tip_on = bool(on)
        self.setToolTip(self._full if self._tip_on else "")

    def setText(self, text):        # noqa: N802 —— 与 Qt 命名保持一致
        self._full = "" if text is None else str(text)
        if self._tip_on:
            self.setToolTip(self._full)
        self._apply_elide()

    # ------------------------------------------------------------------ 内部
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._apply_elide()

    def changeEvent(self, ev):
        super().changeEvent(ev)
        if ev.type() in (QtCore.QEvent.Type.FontChange, QtCore.QEvent.Type.StyleChange):
            self._apply_elide()

    def minimumSizeHint(self):
        return QtCore.QSize(0, super().minimumSizeHint().height())

    def sizeHint(self):
        """
        【v1.0.1 真 bug】QLabel.sizeHint() 量的是**当前显示的文本**：
        一旦被省略号截成 160px，sizeHint 就变成 160px，
        布局再也拿不到"全文需要多宽"，于是永久卡在 `🟢 调度健康 · 总占用 27% · 均频 4.40 GH-`。
        这里改成按 `_full` 全文计算，配合 Preferred 策略即可自动要回完整宽度；
        窗口真的不够宽时，布局仍会压到 minimumWidth(160) 再省略。
        """
        try:
            # +26 = QSS 里的 padding(2px 10px) + 余量：只按文字宽度算的话，
            # 带胶囊内边距的状态标签会在最后 1~2 个字上被省略号吃掉。
            w = self.fontMetrics().horizontalAdvance(self._full) + 26
        except Exception:
            w = super().sizeHint().width()
        return QtCore.QSize(w, super().sizeHint().height())

    def _apply_elide(self):
        w = self.width() - 2
        txt = self._full
        if w > 8 and txt:
            try:
                txt = self.fontMetrics().elidedText(txt, Qt.TextElideMode.ElideRight, w)
            except Exception:
                txt = self._full
        if txt != QLabel.text(self):
            QLabel.setText(self, txt)


# ==============================================================================
# §8  pyqtgraph + PyQt6 赛博电竞深色 HUD
# ==============================================================================
class StutterBarWidget(QWidget):
    """CapFrameX 风格三色流畅度占比胶囊条"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(12)
        self.smooth = 100.0
        self.stutter = 0.0
        self.severe = 0.0

    def update_ratios(self, smooth, stutter, severe):
        self.smooth = max(0.0, float(smooth))
        self.stutter = max(0.0, float(stutter))
        self.severe = max(0.0, float(severe))
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        radius = h / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(PANEL2))
        p.drawRoundedRect(0, 0, w, h, radius, radius)
        tot = self.smooth + self.stutter + self.severe
        if tot <= 0:
            return
        w_smooth = int(w * (self.smooth / tot))
        w_stutter = int(w * (self.stutter / tot))
        w_severe = max(0, w - w_smooth - w_stutter)
        x = 0
        if w_smooth > 0:
            p.setBrush(QtGui.QColor(GREEN))
            p.drawRoundedRect(x, 0, w_smooth, h, radius, radius)
            x += w_smooth
        if w_stutter > 0:
            p.setBrush(QtGui.QColor(WARN))
            p.drawRect(x, 0, w_stutter, h)
            x += w_stutter
        if w_severe > 0:
            p.setBrush(QtGui.QColor(BAD))
            p.drawRoundedRect(x, 0, w_severe, h, radius, radius)


class MetricCard(QFrame):
    """电竞 HUD 分位数据卡片（首屏四大卡之一：圆角 8px + 悬浮阴影 + 大数字）"""

    def __init__(self, title, unit="", highlight=False, parent=None):
        super().__init__(parent)
        self.setObjectName("MetricCard")
        self.setStyleSheet("""
            QFrame#MetricCard {
                background-color: %s;
                border: 1px solid %s;
                border-radius: 8px;
            }
            QFrame#MetricCard:hover { border-color: %s; }
        """ % (PANEL, BORDER, BORDER_HOT))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(2)
        self.lbl_title = QLabel(title)
        self.lbl_val = QLabel("--")
        self.lbl_sub = QLabel(unit)
        lay.addWidget(self.lbl_title)
        lay.addWidget(self.lbl_val)
        lay.addWidget(self.lbl_sub)
        self._highlight = highlight
        self._title = title
        self._unit = unit
        self.restyle()

    def restyle(self):
        """按当前主题刷新配色（主题切换时调用）"""
        try:
            t = CURRENT_THEME
            self.lbl_title.setStyleSheet(
                "color: %s; font-size: 12px; font-weight: bold;" % t.dim)
            # 首屏四大卡：数字放大到 26px，等宽字体保证跳动时不抖动
            self.lbl_val.setStyleSheet(
                "color: %s; font-size: 26px; font-weight: bold; font-family: %s;"
                % (t.accent if self._highlight else t.fg, MONO_FAMILY))
            self.lbl_sub.setStyleSheet("color: %s; font-size: 10px;" % t.dim)
            self.setStyleSheet(
                "QFrame#MetricCard { background-color: %s; border: 1px solid %s;"
                " border-radius: 8px; }" % (t.panel, t.border))
        except Exception:
            pass

    def set_value(self, val_str, sub_str=""):
        self.lbl_val.setText(val_str)
        if sub_str:
            self.lbl_sub.setText(sub_str)




# 【v1.0.0 清理】原 §8b「极客热力可视化：CPU 核心热力块矩阵」整段已删除：
#   · CpuHeatmapGrid   —— v1.0.1 起主界面 CPU 页就不再使用（用户要求"只留柱状图"），
#                         仅剩设置按钮里那个 v3.2 进阶面板的 CPU 页签在用；
#   · heat_color/_text_on/HEAT_STOPS —— 只服务于上面的热力块与它旁边的色标图例。
#   随进阶面板瘦身（只留「设置 + 事件流」）一并移除，避免死代码继续被误引用。


class ProbeReadoutBar(QWidget):
    """
    「极客模式」新探针读数条（抽屉内的极客面板下半部）。

    四个探针各一行：标签 + 数值 + 状态点。
    刻意做成纯 QWidget 手绘 / QLabel 组合的轻量条，避免卡片嵌套过深。
    """

    ROWS = (
        ("io",    "💿 磁盘资产流式读取"),
        ("net",   "🌐 网络吞吐 (接收)"),
        ("clk",   "🌡 GPU 核心频率"),
        ("gc",    "⏳ 引擎 GC / 主线程锁"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lbl = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        for key, caption in self.ROWS:
            row = QHBoxLayout()
            row.setSpacing(6)
            lab = QLabel(caption)
            lab.setStyleSheet("color: %s; font-size: 11px;" % DIM)
            lab.setMinimumWidth(150)
            val = QLabel("—")
            val.setStyleSheet("color: %s; font-size: 11px; font-family: %s;"
                              % (FG, MONO_FAMILY))
            row.addWidget(lab)
            row.addWidget(val, stretch=1)
            lay.addLayout(row)
            self.lbl[key] = val

    def set_row(self, key, text, severity="NORMAL"):
        """severity: NORMAL / WARN / DANGER（决定数值颜色）"""
        w = self.lbl.get(key)
        if w is None:
            return
        if w.text() == text and getattr(w, "_sev", None) == severity:
            return
        w.setText(text)
        w._sev = severity
        color = FG
        if severity == "DANGER":
            color = BAD
        elif severity == "WARN":
            color = WARN
        w.setStyleSheet("color: %s; font-size: 11px; font-family: %s;"
                        % (color, MONO_FAMILY))

    def restyle(self, theme=None):
        t = theme or CURRENT_THEME
        for key, _cap in self.ROWS:
            w = self.lbl.get(key)
            if w is not None:
                w._sev = None
                w.setStyleSheet("color: %s; font-size: 11px; font-family: %s;"
                                % (t.fg, MONO_FAMILY))
