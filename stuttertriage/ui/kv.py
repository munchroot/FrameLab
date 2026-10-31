# -*- coding: utf-8 -*-
"""
KV（Key-Value）网格排版 —— v1.0.1 起全站统一的信息排版规范。

【三条铁律，别的地方都必须遵守】
  1. **颜色分工**：指标名（Key）永远是「低明度蓝灰胶囊」——底色 `theme.key_bg`、
     文字 `theme.key_fg`；数值（Value）永远是「高亮纯白」`theme.val`
     或主题强调色（accent / warn / bad / green / purple / orange）。
     任何"标签和数值同色"的旧写法都算违规。
  2. **禁止垂直堆砌**：不许「标题包住数据、数据下面再踩一行注释」的三明治。
     一个指标 = 一行：左边标签胶囊，右边数值，中间留白。
     需要第二项信息就再开一行，且每行都有自己的标签。
  3. **图表内不放文字**：圆环 / 进度条 / 能量柱内部和上方只允许出现**数值**，
     标签一律放到图表外部（就是本模块的 KV 行里），且同一个标签只出现一次。

用法：
    row = KVRow("平均帧", size=22, role="accent")
    row.set_value("98")
    grid.add_row(row)              # KVGrid 会自动对齐标签列宽
"""
from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .. import theme as T

# 数值可用的角色 → 主题字段（未命中就用 th.val）
_VALUE_ROLES = ("val", "accent", "warn", "bad", "green", "purple", "orange", "fg")


def value_color(theme=None, role="val"):
    """角色 → 实际颜色字符串（role 允许直接是 #rrggbb）"""
    th = theme or T.CURRENT_THEME
    if role and str(role).startswith("#"):
        return role
    if role in (None, "", "val"):
        return th.val
    return getattr(th, _VALUE_ITEMS.get(role, "val"), th.val)


_VALUE_ITEMS = {r: r for r in _VALUE_ROLES}


class KeyChip(QLabel):
    """
    指标名胶囊：低明度蓝灰底 + 同色系文字（绝不用数值的白）。

    【为什么是胶囊而不是纯文字】纯灰字在深色底上会被数值淹没；
    加一个低明度底块后，「标签区」和「数值区」在视觉上被切成两栏，
    扫视时眼睛先落数值、需要解释时再看标签。
    """

    def __init__(self, text="", parent=None, size=11):
        super().__init__(text, parent)
        self._size = int(size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.restyle()

    def setText(self, text):        # noqa: N802 —— Qt 命名
        super().setText(text)
        if not text:
            self.setVisible(False)
        else:
            self.setVisible(True)
            self.restyle()

    def restyle(self, theme=None):
        th = theme or T.CURRENT_THEME
        self.setStyleSheet(
            "QLabel { background-color: %s; color: %s;"
            " font-size: %dpx; font-weight: bold;"
            " border-radius: 4px; padding: 1px 6px; }"
            % (th.key_bg, th.key_fg, self._size))


class KVRow(QWidget):
    """
    一行 KV：`[标签胶囊]  …留白…  数值`

    KVRow(label, size=13, role="val", bold=True)
      · size  数值字号（px）
      · role  数值颜色角色：val(纯白) / accent / warn / bad / green / purple / orange
    set_value(text, role=None) 可临时改色（例如超阈值转红）。
    """

    def __init__(self, label="", size=13, role="val", bold=True, parent=None,
                 label_size=11, min_height=0):
        super().__init__(parent)
        self._size = int(size)
        self._bold = bool(bold)
        self._role = role
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.chip = KeyChip(label, size=label_size)
        lay.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        self.lbl_value = QLabel("—")
        self.lbl_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.lbl_value, 0, Qt.AlignmentFlag.AlignVCenter)
        if min_height:
            self.setMinimumHeight(int(min_height))
        self.restyle()

    # ------------------------------------------------------------------ 对外
    def set_label(self, text):
        self.chip.setText(text)

    def label_text(self):
        return self.chip.text()

    def set_value(self, text, role=None):
        if role is not None:
            self._role = role
        self.lbl_value.setText("—" if text is None else str(text))
        self._paint_value()

    def value_text(self):
        return self.lbl_value.text()

    def restyle(self, theme=None):
        th = theme or T.CURRENT_THEME
        self.chip.restyle(th)
        self._paint_value(th)

    def _paint_value(self, theme=None):
        th = theme or T.CURRENT_THEME
        col = value_color(th, self._role)
        self.lbl_value.setStyleSheet(
            "font-size: %dpx; font-weight: %s; color: %s;"
            % (self._size, "bold" if self._bold else "normal", col))


class KVGrid(QWidget):
    """
    若干 KVRow 的纵向容器（**行距统一、标签列不对齐也没关系** ——
    因为每行都是「胶囊在左、数值在右」，天然成网格；这里只负责行距与边距）。
    """

    def __init__(self, parent=None, spacing=4, margins=(0, 0, 0, 0)):
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(*margins)
        self._lay.setSpacing(int(spacing))
        self._rows = []

    def add_row(self, row):
        self._rows.append(row)
        self._lay.addWidget(row)
        return row

    def add(self, label, size=13, role="val", bold=True):
        return self.add_row(KVRow(label, size=size, role=role, bold=bold))

    def add_widget(self, w, spacing_after=0):
        """把图形（进度条/圆环/折线）插进 KV 列表里：标签行在上、图形在下"""
        self._lay.addWidget(w)
        if spacing_after:
            self._lay.addSpacing(int(spacing_after))
        return w

    def row(self, label):
        for r in self._rows:
            if r.label_text() == label:
                return r
        return None

    def rows(self):
        return list(self._rows)

    def restyle(self, theme=None):
        th = theme or T.CURRENT_THEME
        for r in self._rows:
            r.restyle(th)


class KVPanel(QFrame):
    """带主题底色的 KV 面板（页面里的「指标表」区块）"""

    def __init__(self, parent=None, spacing=5, margins=(10, 8, 10, 8)):
        super().__init__(parent)
        self.setObjectName("KvPanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*margins)
        lay.setSpacing(int(spacing))
        self.grid = KVGrid()
        lay.addWidget(self.grid)
        lay.addStretch(1)
        self.restyle()

    def restyle(self, theme=None):
        th = theme or T.CURRENT_THEME
        self.setStyleSheet(
            "QFrame#KvPanel { background-color: %s; border: 1px solid %s;"
            " border-radius: 8px; }" % (th.panel2, th.border))
        self.grid.restyle(th)


__all__ = ["KeyChip", "KVRow", "KVGrid", "KVPanel", "value_color"]