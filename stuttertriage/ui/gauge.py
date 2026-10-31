# -*- coding: utf-8 -*-
"""
GamePP 风格图形化元件（纯自绘，无图片资源）。

设计铁律（改进建议书 一）：**能用图形绝不用纯文字**。
  · 胶囊进度条 CapsuleBar  —— 绿→黄→红渐变，可尾部闪烁「溢出警告块」
  · 环形占比 RingGauge      —— 内存占用环形图
  · 迷你能量柱 MiniEnergyBar —— 读盘 / 换页速率
  · 双色速率条 NetRateBars  —— ↑ 上传 / ↓ 下载
  · 状态灯 StatusDot        —— 着色器编译指示灯（编译爆发时亮黄闪烁）
  · 段位徽章 GradeBadge     —— S / A / B / C
  · 瓶颈天平 BottleneckBar  —— CPU vs 显卡 双色对比条

颜色全部在 paintEvent 时从 theme.CURRENT_THEME 读取 ⇒ 主题热切换自动跟随。
"""
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QWidget

from .. import theme as T
from .kv import KVRow, KeyChip, value_color


def _c(color):
    return QtGui.QColor(color)


def _with_alpha(color, alpha):
    c = _c(color)
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


def _lerp(c1, c2, t):
    a, b = _c(c1), _c(c2)
    t = max(0.0, min(1.0, float(t)))
    return QtGui.QColor(int(a.red() + (b.red() - a.red()) * t),
                        int(a.green() + (b.green() - a.green()) * t),
                        int(a.blue() + (b.blue() - a.blue()) * t))


def load_gradient(t):
    """
    负载色标：0 → 绿，0.65 → 黄，1.0 → 红（绿=空闲，橙=高载，红=拥堵）。
    t 为 0~1 的占用率。
    """
    th = T.CURRENT_THEME
    t = max(0.0, min(1.0, float(t)))
    if t <= 0.65:
        return _lerp(th.green, th.warn, t / 0.65)
    return _lerp(th.warn, th.bad, (t - 0.65) / 0.35)


class _Blinker(QtCore.QObject):
    """共享闪烁节拍器：所有需要「闪烁」的元件共用一个 400ms 定时器。"""

    tick = QtCore.pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._on = True
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._flip)
        self._want = 0

    def want(self, need):
        """登记「需要闪烁」的元件数量；为 0 时停表省电。"""
        self._want = max(0, self._want + (1 if need else -1))
        if self._want > 0 and not self._timer.isActive():
            self._timer.start()
        elif self._want == 0 and self._timer.isActive():
            self._timer.stop()
            self._on = True
            self.tick.emit(True)

    def _flip(self):
        self._on = not self._on
        self.tick.emit(self._on)


_BLINKER = None


def blinker():
    """
    共享节拍器单例。

    【v1.0.5 修崩溃】测试/异常路径下 `_BLINKER` 的 C++ 对象可能已被销毁
    （如上一窗口 deleteLater 收尾），再访问会抛
    `RuntimeError: wrapped C/C++ object of type _Blinker has been deleted`。
    这里用 sip.isdeleted 检测并重建，保证 blinker() 永远返回活对象。
    """
    global _BLINKER
    if _BLINKER is not None:
        try:
            if sip.isdeleted(_BLINKER):
                _BLINKER = None
        except Exception:
            _BLINKER = None
    if _BLINKER is None:
        _BLINKER = _Blinker()
    return _BLINKER


class CapsuleBar(QWidget):
    """
    水平圆角胶囊进度条 —— **纯图形，内部不画任何文字**（v1.0.1 KV 规范第 3 条）。

    set_value(ratio, text="", warn_tail=False, tail_text="")
      ratio     0~1
      text      【已废弃】旧版画在条内的说明文字；现在信息一律由外部 KVRow 呈现，
                传进来也会被忽略（保留参数只为兼容旧调用）
      warn_tail True 时在尾部叠加闪烁红色警戒块（非本地显存溢出）
      tail_text 【已废弃】同上
    """

    def __init__(self, label="", parent=None):
        super().__init__(parent)
        self.label = label
        self._ratio = 0.0
        self._text = ""
        self._warn_tail = False
        self._tail_text = ""
        self._blink_on = True
        self._blink_reg = False
        self.setMinimumHeight(14)
        blinker().tick.connect(self._on_blink)

    def set_value(self, ratio, text="", warn_tail=False, tail_text=""):
        self._ratio = max(0.0, min(1.0, float(ratio or 0.0)))
        self._text = text or ""
        self._warn_tail = bool(warn_tail)
        self._tail_text = tail_text or ""
        if self._warn_tail != self._blink_reg:
            blinker().want(self._warn_tail)
            self._blink_reg = self._warn_tail
        self.update()

    def _on_blink(self, on):
        if self._warn_tail:
            self._blink_on = on
            self.update()

    def paintEvent(self, _ev):
        th = T.CURRENT_THEME
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        bar_h = min(12, max(8, h))
        y = (h - bar_h) / 2.0
        x0, x1 = 0.0, float(w)
        # 轨道
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c(th.panel2))
        p.drawRoundedRect(QtCore.QRectF(x0, y, x1 - x0, bar_h), bar_h / 2.0, bar_h / 2.0)
        # 填充（按负载渐变）
        fill_w = max(0.0, (x1 - x0) * self._ratio)
        if fill_w > 1.0:
            base = load_gradient(self._ratio)
            grad = QtGui.QLinearGradient(x0, 0, x0 + fill_w, 0)
            grad.setColorAt(0.0, _with_alpha(load_gradient(max(0.0, self._ratio - 0.5)).name(), 210))
            grad.setColorAt(1.0, base)
            p.setBrush(QtGui.QBrush(grad))
            p.drawRoundedRect(QtCore.QRectF(x0, y, fill_w, bar_h), bar_h / 2.0, bar_h / 2.0)
        # 尾部闪烁警戒块（只画色块，不写字）
        if self._warn_tail:
            bw = max(34.0, min(96.0, (x1 - x0) * 0.22))
            col = _c(th.bad) if self._blink_on else _with_alpha(th.bad, 60)
            p.setBrush(col)
            p.drawRoundedRect(QtCore.QRectF(x1 - bw, y - 2, bw, bar_h + 4), 6, 6)


class RingGauge(QWidget):
    """
    环形占比图 —— **圆环内部只允许出现数值**（v1.0.1 KV 规范第 3 条）。

    旧版在圆心画「百分比 + 小字说明（sub） + 环下 caption」，
    一圈套三层文字，正是要禁止的「标签包数据、数据踩注释」。
    现在：圆心只留数值；标签由页面用 KVRow 放在圆环**外部**；
    sub 通过 sub_text() 暴露给页面自己放进 KV 行。
    """

    def __init__(self, caption="", parent=None):
        super().__init__(parent)
        self.caption = caption          # 仅作为"这个圆环叫什么"的语义标签，不再绘制
        self._ratio = 0.0
        self._center = ""
        self._sub = ""
        self.setMinimumSize(96, 96)

    def set_value(self, ratio, center="", sub=""):
        self._ratio = max(0.0, min(1.0, float(ratio or 0.0)))
        self._center = center
        self._sub = sub
        self.update()

    def sub_text(self):
        """给页面用的副信息（例如「已用 18.4 GB」），由页面放进 KV 行"""
        return self._sub

    def paintEvent(self, _ev):
        th = T.CURRENT_THEME
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        side = min(self.width(), self.height())
        thick = max(8.0, side * 0.13)
        rect = QtCore.QRectF((self.width() - side) / 2 + thick / 2 + 2,
                             (self.height() - side) / 2 + thick / 2 + 2,
                             side - thick - 4, side - thick - 4)
        p.setPen(QtGui.QPen(_c(th.panel2), thick, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 0, 360 * 16)
        col = load_gradient(self._ratio)
        p.setPen(QtGui.QPen(col, thick, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 90 * 16, -int(360 * 16 * self._ratio))
        # 圆心：只有数值（高亮纯白 = 规范里的 Value 色）
        p.setPen(_c(th.val))
        f = p.font()
        f.setPointSizeF(max(9.0, side * 0.115))
        f.setBold(True)
        p.setFont(f)
        p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), self._center)


class MiniEnergyBar(QWidget):
    """
    迷你能量条（读盘 / 换入 / 换出）—— **横向胶囊 + 只有颜色，没有文字**。

    【v1.0.1 改横向】原来画成竖直柱：放在 1000px 宽的槽位里，
    柱子只有 26px 宽，左右两片空白，看起来像"三根头发丝"。
    改成横向后与页面的 KV 行一一对应（标签在左、数值在右、条在下面），
    信息密度和可读性都正常。

    set_value(value, vmax=None, warn=False)   （API 与旧版完全一致）
    """

    def __init__(self, caption="", unit="MB/s", vmax=64.0, color_role="accent", parent=None):
        super().__init__(parent)
        self.caption = caption
        self.unit = unit
        self.vmax = float(vmax)
        self.color_role = color_role
        self._value = 0.0
        self._warn = False
        self.setFixedHeight(8)
        self.setMinimumWidth(80)

    def set_value(self, value, vmax=None, warn=False):
        self._value = max(0.0, float(value or 0.0))
        if vmax:
            self.vmax = float(vmax)
        self._warn = bool(warn)
        self.update()

    def _color(self):
        th = T.CURRENT_THEME
        if getattr(self, "_warn", False):
            return _c(th.warn)
        return _c(getattr(th, self.color_role, th.accent))

    def paintEvent(self, _ev):
        th = T.CURRENT_THEME
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), float(self.height())
        h = max(6.0, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c(th.panel2))
        p.drawRoundedRect(QtCore.QRectF(0, 0, w, h), h / 2.0, h / 2.0)
        ratio = 0.0 if self.vmax <= 0 else max(0.0, min(1.0, self._value / self.vmax))
        if ratio > 0.005:
            col = self._color()
            grad = QtGui.QLinearGradient(0, 0, w * ratio, 0)
            grad.setColorAt(0.0, _with_alpha(col.name(), 190))
            grad.setColorAt(1.0, col)
            p.setBrush(QtGui.QBrush(grad))
            p.drawRoundedRect(QtCore.QRectF(0, 0, max(3.0, w * ratio), h), h / 2.0, h / 2.0)


class NetRateBars(QWidget):
    """
    上下双色迷你速率条 —— **只画两条轨道，不写箭头/名字/数值**（KV 规范第 3 条）。

    上行 = 紫（第一条）、下行 = 青（第二条）；对应的 KV 行在页面里。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._up = 0.0
        self._down = 0.0
        self.up_max = 1024.0
        self.down_max = 8192.0
        self.setMinimumHeight(46)
        self.setMinimumWidth(120)

    def set_rates(self, up_kbs, down_kbs):
        self._up = max(0.0, float(up_kbs or 0.0))
        self._down = max(0.0, float(down_kbs or 0.0))
        self.update()

    def paintEvent(self, _ev):
        th = T.CURRENT_THEME
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        rows = ((self._up, self.up_max, th.purple),
                (self._down, self.down_max, th.accent))
        # 行高按可用高度均分（控件被拉高时不要挤在顶部）
        row_h = max(20.0, (h - 6.0) / len(rows))
        y = (h - row_h * len(rows)) / 2.0
        bar_h = 10.0
        for val, vmax, col in rows:
            track = QtCore.QRectF(0, y + (min(row_h, 20.0) - bar_h) / 2.0, w, bar_h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_c(th.panel2))
            p.drawRoundedRect(track, bar_h / 2.0, bar_h / 2.0)
            ratio = 0.0 if vmax <= 0 else max(0.0, min(1.0, val / vmax))
            if ratio > 0.01:
                p.setBrush(_c(col))
                p.drawRoundedRect(QtCore.QRectF(track.x(), track.y(),
                                                track.width() * ratio, track.height()),
                                  bar_h / 2.0, bar_h / 2.0)
            y += row_h


class StatusDot(QWidget):
    """状态指示灯（着色器编译）：平时暗灰，活跃时亮黄闪烁。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._on = True
        self._reg = False
        self.setFixedSize(16, 16)
        blinker().tick.connect(self._on_blink)

    def set_active(self, active):
        active = bool(active)
        if active != self._reg:
            blinker().want(active)
            self._reg = active
        self._active = active
        self.update()

    def _on_blink(self, on):
        if self._active:
            self._on = on
            self.update()

    def paintEvent(self, _ev):
        th = T.CURRENT_THEME
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        if self._active:
            col = _c(th.warn) if self._on else _with_alpha(th.warn, 70)
            glow = _with_alpha(th.warn, 60 if self._on else 20)
        else:
            col = _c(th.border_hot)
            glow = QtGui.QColor(0, 0, 0, 0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(0, 0, 16, 16)
        p.setBrush(col)
        p.drawEllipse(3, 3, 10, 10)


class GradeBadge(QWidget):
    """
    丝滑度段位徽章：方框里**只有一个大字母**（S / A / B / C）。

    【v1.0.1】原来框内还塞了「丝滑度评级」+「建议降画质」两行小字，
    挤成一团还压边框；结论文字由顶部横幅承担，徽章只负责"等级"这一件事。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._letter = "-"
        self._label = "等待数据"
        self.setFixedSize(84, 74)
        self.setToolTip(self._label)

    def set_grade(self, letter, label=""):
        self._letter = letter or "-"
        self._label = label
        self.setToolTip(label)
        self.update()

    def paintEvent(self, _ev):
        th = T.CURRENT_THEME
        from .plain import GRADE_COLOR
        col = _c(GRADE_COLOR.get(self._letter, th.dim))
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.setPen(QtGui.QPen(col, 2))
        p.setBrush(_with_alpha(col.name(), 38))
        p.drawRoundedRect(QtCore.QRectF(1, 1, self.width() - 2, self.height() - 2), 10, 10)
        p.setPen(col)
        f = p.font()
        f.setPointSizeF(30)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QtCore.QRectF(0, 0, self.width(), self.height()),
                   int(Qt.AlignmentFlag.AlignCenter), self._letter)


class BottleneckBar(QWidget):
    """
    瓶颈天平：左 CPU（橙）/ 右显卡（青）双色对比条 + 中线。

    v1.0.1：**条上不写任何文字**（标签与百分比都交给横幅里的 KV 行），
    只保留「哪边更长」这个图形结论。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.gpu = 0.0
        self.cpu = 0.0
        self.side = "unknown"
        self.setMinimumHeight(10)
        self.setMaximumHeight(12)

    def set_state(self, gpu_pct, cpu_pct, side="balanced"):
        self.gpu = max(0.0, min(100.0, float(gpu_pct or 0.0)))
        self.cpu = max(0.0, min(100.0, float(cpu_pct or 0.0)))
        self.side = side
        self.update()

    def paintEvent(self, _ev):
        th = T.CURRENT_THEME
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        bar_h = float(h)
        y = 0.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c(th.panel2))
        p.drawRoundedRect(QtCore.QRectF(0, y, w, bar_h), 4, 4)
        # CPU 从左往右，GPU 从右往左，各占一半赛道
        p.setBrush(_c(th.orange) if self.side == "cpu" else _with_alpha(th.orange, 120))
        p.drawRoundedRect(QtCore.QRectF(0, y, w * 0.5 * self.cpu / 100.0, bar_h), 4, 4)
        gw = w * 0.5 * self.gpu / 100.0
        p.setBrush(_c(th.accent) if self.side == "gpu" else _with_alpha(th.accent, 120))
        p.drawRoundedRect(QtCore.QRectF(w - gw, y, gw, bar_h), 4, 4)
        p.setPen(_c(th.border_hot))
        p.drawLine(int(w / 2), int(y), int(w / 2), int(y + bar_h))


class CpuFreqBars(QWidget):
    """
    各核心频率柱状条（改进建议书 一.2 · Page 1 全屏页）。

    · 横轴 = 核心号（C0..Cn），纵轴 = 当前频率；
    · 柱色沿用负载色标（绿=空闲 / 黄=中载 / 红=拥堵），一眼看出哪个核在死扛；
    · 柱顶标注频率最高的那根（避免 25 根柱子全都是数字看不清）；
    · 底部一条虚线基线标出全核最高频，用来直观对比"谁睿频上去了、谁掉下来了"。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.freqs = []
        self.usages = []
        self.max_mhz = 0.0
        self.setMinimumHeight(150)

    def set_values(self, freqs, usages=None, max_mhz=None):
        try:
            self.freqs = [float(f or 0.0) for f in (freqs or [])]
        except Exception:
            self.freqs = []
        try:
            self.usages = [float(u or 0.0) for u in (usages or [])]
        except Exception:
            self.usages = []
        if max_mhz:
            try:
                self.max_mhz = max(self.max_mhz, float(max_mhz))
            except Exception:
                pass
        self.update()

    def _scale(self):
        top = max(list(self.freqs) + [self.max_mhz, 1.0])
        return top * 1.08

    def paintEvent(self, _ev):
        th = T.CURRENT_THEME
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c(th.panel))
        p.drawRect(self.rect())
        n = len(self.freqs)
        if n <= 0:
            p.setPen(_c(th.dim))
            f = p.font()
            f.setPointSizeF(9.0)
            p.setFont(f)
            p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), "等待 CPU 频率采样…")
            return
        pad_l, pad_r, pad_t, pad_b = 8.0, 8.0, 16.0, 20.0
        base_y = h - pad_b
        usable = max(1.0, base_y - pad_t)
        scale = self._scale()
        slot = max(3.0, (w - pad_l - pad_r) / float(n))
        bar_w = max(2.0, min(30.0, slot * 0.62))
        # 全核最高频参考虚线
        top_mhz = max(self.freqs) if self.freqs else 0.0
        if top_mhz > 0:
            ry = base_y - usable * (top_mhz / scale)
            p.setPen(QtGui.QPen(_c(th.border_hot), 1, Qt.PenStyle.DashLine))
            p.drawLine(QtCore.QPointF(pad_l, ry), QtCore.QPointF(w - pad_r, ry))
        p.setPen(_c(th.border_hot))
        p.drawLine(QtCore.QPointF(pad_l, base_y), QtCore.QPointF(w - pad_r, base_y))
        f_small = p.font()
        f_small.setPointSizeF(7.0)
        for i, mhz in enumerate(self.freqs):
            x = pad_l + i * slot + (slot - bar_w) / 2.0
            ratio = 0.0 if scale <= 0 else max(0.0, min(1.0, mhz / scale))
            bh = usable * ratio
            u = self.usages[i] if i < len(self.usages) else 0.0
            col = load_gradient(u / 100.0) if u > 0 else _c(th.accent)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_with_alpha(th.panel2, 255))
            p.drawRoundedRect(QtCore.QRectF(x, pad_t, bar_w, usable), 3.0, 3.0)
            if bh > 1.0:
                p.setBrush(col)
                p.drawRoundedRect(QtCore.QRectF(x, base_y - bh, bar_w, bh), 3.0, 3.0)
            # 核心号
            p.setPen(_c(th.dim))
            p.setFont(f_small)
            p.drawText(QtCore.QRectF(x + bar_w / 2.0 - 22.0, base_y + 3.0, 44.0, 14.0),
                       int(Qt.AlignmentFlag.AlignCenter), "C%d" % i)
            # 【v1.0.2】柱顶那个 "4594" 已删除：图内不放文字（KV 规范第 3 条），
            # 最高核频由页面 KV 行「最高核频」唯一表达；虚线仍在，作视觉参考。


class VramSplitBar(QWidget):
    """
    物理专用显存 vs 共享（非本地）显存 大横条（改进建议书 一.2 / 二.3）。

    【铁律】这里的百分比**只允许来自 clamp(0,100) 的占用量**：
    历史上曾把「非本地显存 MB 数」直接塞进百分比字段，界面上出现
    `1008.40%` 这种把 MB 当百分比的笑话 —— 本控件禁止任何未 clamp 的输入。

    set_split(dedicated_mb, total_mb, nonlocal_mb, level="ok")
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ded = 0.0
        self.tot = 0.0
        self.non = 0.0
        self.level = "ok"
        self.setMinimumHeight(28)

    def set_split(self, dedicated_mb, total_mb, nonlocal_mb=0.0, level="ok"):
        try:
            self.ded = max(0.0, float(dedicated_mb or 0.0))
        except Exception:
            self.ded = 0.0
        try:
            self.tot = max(0.0, float(total_mb or 0.0))
        except Exception:
            self.tot = 0.0
        try:
            self.non = max(0.0, float(nonlocal_mb or 0.0))
        except Exception:
            self.non = 0.0
        self.level = level or "ok"
        self.update()

    def pct(self):
        if self.tot <= 0.0:
            return 0.0
        return max(0.0, min(100.0, self.ded / self.tot * 100.0))

    def paintEvent(self, _ev):
        """
        纯图形：轨道 + 已用渐变 + 尾部共享显存斜纹块。

        【v1.0.1】条上方原有的「已用 6.4 GB / 总计 8.0 GB (80%)」与
        尾部块里的「共享 512 MB」文字全部移除 —— 进度条内外只允许数值，
        标签与数值改由页面 KV 行呈现（同一个信息不再重复两次）。
        """
        th = T.CURRENT_THEME
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        bar_h = 20.0
        y = (h - bar_h) / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c(th.panel2))
        p.drawRoundedRect(QtCore.QRectF(0, y, w, bar_h), bar_h / 2.0, bar_h / 2.0)
        ratio = self.pct() / 100.0
        fill = w * ratio
        if fill > 1.0:
            grad = QtGui.QLinearGradient(0, 0, fill, 0)
            grad.setColorAt(0.0, _with_alpha(th.green, 220))
            grad.setColorAt(0.6, _with_alpha(th.warn, 230))
            grad.setColorAt(1.0, _c(th.bad if ratio >= 0.88 else th.warn))
            p.setBrush(QtGui.QBrush(grad))
            p.drawRoundedRect(QtCore.QRectF(0, y, fill, bar_h), bar_h / 2.0, bar_h / 2.0)
        # 共享（非本地）显存：条尾部叠加半透明色块（只表意，不写字）
        if self.non > 0.5 and w > 8.0:
            sw = max(24.0, min(w * 0.30, w * 0.30))
            p.setBrush(_with_alpha(th.purple, 90))
            p.drawRoundedRect(QtCore.QRectF(w - sw, y, sw, bar_h), bar_h / 2.0, bar_h / 2.0)


def _mb_text(mb):
    """MB → 「512 MB / 2.0 GB」；只用于**容量**展示，绝不参与百分比计算。"""
    try:
        mb = float(mb)
    except Exception:
        return "-"
    if mb >= 1024.0:
        return "%.1f GB" % (mb / 1024.0)
    return "%.0f MB" % mb


def vram_text(dedicated_mb, total_mb, nonlocal_mb=None):
    """
    显存进度条文案（改进建议书 二.3）：`已用 X.X GB / 总计 X.X GB (XX%)`。

    规范要求进度条数值严格 `clamp(0, 100)` —— 这里返回的百分比一定在 [0,100]。
    优先复用 `stuttertriage.stats.vram_bar_text`（唯一权威实现，纯函数、可单测）；
    若该实现不可用则用本地等价实现兜底，保证界面永远不会出现 `1008.40%`。
    """
    try:
        from ..stats import vram_bar_text as _vbt
        text, pct = _vbt(dedicated_mb, total_mb, nonlocal_mb)
        return text, max(0.0, min(100.0, float(pct)))
    except Exception:
        pass
    try:
        tot = float(total_mb or 0.0)
        ded = float(dedicated_mb or 0.0)
    except Exception:
        return "容量未知", 0.0
    if tot <= 0.0:
        return "容量未知", 0.0
    pct = max(0.0, min(100.0, ded / tot * 100.0))
    return "已用 %s / 总计 %s (%.0f%%)" % (_mb_text(ded), _mb_text(tot), pct), pct


class FpsCard(QtWidgets.QFrame):
    """
    四大核心指标卡 —— **最多两层文字 + 一行副文本**（v1.0.1 规范 §4）。

        ┌──────────────────────────┐
        │ 平均帧                   │ ← 顶部标题：13px 石板灰 600
        │ 89                       │ ← 中部大数字：28px 强调色
        │ 占比 19.2% · 频繁        │ ← 底部最多 1 行短副文本（10px）
        └──────────────────────────┘

    副文本规则：`sub_text` 与 `tail_text` 会被合并到**同一行**，
    用 ` · ` 连接（例如「顿挫频繁」+「占比 19.2%」→「占比 19.2% · 频繁」）。
    """

    ROLE_COLOR = {"accent": "accent", "green": "green", "warn": "warn", "bad": "bad",
                  "purple": "purple", "orange": "orange", "fg": "fg", "val": "val"}

    def __init__(self, title, sub_label="", role="accent", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._title = title
        self._sub_label = sub_label          # 兼容旧调用；不再单独占一行
        self._value = "--"
        self._sub = ""
        self._role = role
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(3)

        self.lbl_title = QtWidgets.QLabel(title)          # 兼容旧属性名
        self.lbl_value = QtWidgets.QLabel("--")
        self.lbl_sub = QtWidgets.QLabel("")
        self.lbl_tail = self.lbl_sub                      # 旧名 → 同一行，绝不出现两行注释
        lay.addWidget(self.lbl_title)
        lay.addWidget(self.lbl_value)
        lay.addWidget(self.lbl_sub)
        lay.addStretch(1)
        self.lbl_sub.setVisible(False)
        self.restyle()

    def set_value(self, value_text, sub_text="", tail_text="", role=None):
        if role:
            self._role = role
        self._value = value_text
        parts = [p for p in (sub_text, tail_text) if p]
        self._sub = " · ".join(parts)
        self.lbl_value.setText(value_text)
        self.lbl_sub.setText(self._sub)
        self.lbl_sub.setVisible(bool(self._sub))
        self.restyle()

    def restyle(self, theme=None):
        th = theme or T.CURRENT_THEME
        col = getattr(th, self.ROLE_COLOR.get(self._role, "accent"), th.accent)
        self.setStyleSheet(
            "QFrame#Card { background-color: %s; border: 1px solid %s; border-radius: 8px; }"
            % (th.panel, th.border))
        # 标题：13px 石板灰 + 600 字重（规范 §4）
        self.lbl_title.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: %s;" % th.dim)
        self.lbl_value.setStyleSheet(
            "font-size: 28px; font-weight: bold; color: %s;" % col)
        self.lbl_sub.setStyleSheet(
            "font-size: 10px; font-weight: 500; color: %s;" % th.dim)
