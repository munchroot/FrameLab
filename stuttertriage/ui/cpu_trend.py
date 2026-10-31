# -*- coding: utf-8 -*-
"""
CPU「频率 + 功率」双折线（v1.0.0 · Page 1 热力块矩阵旁边）。

【为什么单独一个文件】这是新增的可视化原件，和 gauge.py 里那堆静态元件不同：
它要维护一份环形历史缓冲、按快照序号去重、自绘两条归一化折线。
拆出来以后改采样窗口 / 配色 / 曲线数量都不用碰页面装配代码。

【精简原则】刻意不做坐标轴与网格：
  · 左上角只留两个色块图例（频率 / 功率）与当前值；
  · 纵轴各自归一化（频率对历史峰值、功率对历史峰值），只表达「变化形状」；
  · 高度固定 104px，永不参与拉伸 —— 页面其他部分不会因为它上下跳。

【数据真实性】功率来自 `probes.CpuPowerProbe`：
  · 有 PDH 功率计 → 实测；
  · 没有（绝大多数台式机）→ 估算，界面上强制带 `≈` 前缀，绝不假装是实测。
"""
from collections import deque

from PyQt6 import QtCore, QtGui
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout,
                             QWidget)

from .. import theme as T
from .kv import KeyChip


def _c(col, alpha=255):
    c = QtGui.QColor(col)
    c.setAlpha(alpha)
    return c


def _fmt_ghz(mhz):
    try:
        v = float(mhz or 0.0)
    except Exception:
        return "--"
    if v <= 0:
        return "--"
    return "%.2f GHz" % (v / 1000.0)


class CpuTrendChart(QWidget):
    """
    CPU 均频 / 功率历史折线。

    push(seq, freq_avg_mhz, freq_max_mhz, power_w, power_est=False)
      · seq 是 HardwareSnapshot.seq：同一快照重复 push 会被忽略，
        所以页面刷新比硬件采样快也不会画出「锯齿重复点」。
    """

    HEIGHT = 76                # 图内已无文字，高度比 v1.0.0 更矮（省垂直空间）
    CAP = 180                  # 环形缓冲：约 180 个硬件快照（≈3 分钟）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._freq = deque(maxlen=self.CAP)
        self._fmax = deque(maxlen=self.CAP)
        self._power = deque(maxlen=self.CAP)
        self._seq = None
        self._est = False
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("CPU 均频与封装功率走势（同一时刻的快照只取一个点）")

    # ------------------------------------------------------------------ 数据
    def push(self, seq, freq_avg_mhz=0.0, freq_max_mhz=0.0, power_w=None, power_est=False):
        if seq is not None and seq == self._seq:
            return
        self._seq = seq
        try:
            f = float(freq_avg_mhz or 0.0)
        except Exception:
            f = 0.0
        try:
            fm = float(freq_max_mhz or 0.0)
        except Exception:
            fm = 0.0
        self._freq.append(f)
        self._fmax.append(fm)
        try:
            self._power.append(None if power_w is None else float(power_w))
        except Exception:
            self._power.append(None)
        self._est = bool(power_est)
        self.update()

    def clear(self):
        self._freq.clear()
        self._fmax.clear()
        self._power.clear()
        self._seq = None
        self.update()

    def samples(self):
        """自检用：返回 (频率点列, 功率点列)"""
        return (list(self._freq), list(self._power))

    def current(self):
        """给页面 KV 行用：当前频率(MHz) / 当前功率(W 或 None) / 峰值频率(MHz) / 是否估算"""
        cur_f = self._freq[-1] if self._freq else 0.0
        cur_p = self._power[-1] if self._power else None
        fmax = max([v for v in self._fmax if v] or [0.0])
        return (cur_f, cur_p, fmax, self._est)

    # ------------------------------------------------------------------ 绘制
    def _range(self, values):
        """每条线各自归一化：返回 (下界, 上界)；样本不足或几乎无波动时返回 None。"""
        vals = [v for v in values if v is not None and v > 0]
        if len(vals) < 2:
            return None
        lo, hi = min(vals), max(vals)
        if hi <= 0:
            return None
        if (hi - lo) / hi < 0.02:       # 波动 <2% 视为平坦，别把噪声放大成锯齿
            mid = (hi + lo) / 2.0
            lo, hi = mid * 0.98, mid * 1.02
        return lo, hi

    def _path(self, values, x0, x1, y0, y1, rng):
        """
        把一列数值画成折线。

        【v1.0.1】样本还没攒满 CAP 时，**横向铺满整个宽度**（不再把几十个点挤在
        右边一小条里）—— 刚开机时曲线本来就只有一小段，铺满更好读；
        攒满 CAP 之后回到「每点固定间距、时间向左推进」的正常形态。
        """
        if rng is None or len(values) < 2:
            return None
        lo, hi = rng
        span = max(1e-9, hi - lo)
        n = len(values)
        path = QtGui.QPainterPath()
        step = (x1 - x0) / float(max(1, n - 1))
        start = x0
        first = True
        for i, v in enumerate(values):
            if v is None or v <= 0:
                continue
            x = start + step * i
            t = max(0.0, min(1.0, (v - lo) / span))
            y = y1 - (y1 - y0) * t
            if first:
                path.moveTo(x, y)
                first = False
            else:
                path.lineTo(x, y)
        return path if not first else None

    def paintEvent(self, _ev):
        """
        只画两条折线 + 一条极淡基线，**不写任何文字**（v1.0.1 KV 规范第 3 条）。

        图例（频率 / 功率 / 峰值）全部由页面用 KVRow 放在图外，
        数值与颜色一一对应（青线 = 频率，橙线 = 功率），不再在图上重复一遍。
        """
        th = T.CURRENT_THEME
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()

        p.setPen(QtGui.QPen(_c(th.border), 1))
        # 【v1.0.3】本图现在放在「CPU 频率与功耗」合并卡**内部**，底色用 th.panel
        # （比卡片底 panel2 更暗/更白）形成"凹陷屏幕"的层次，不再和卡片同一个色阶。
        p.setBrush(_c(th.panel, 220))
        p.drawRoundedRect(QtCore.QRectF(0.5, 0.5, w - 1, h - 1), 6.0, 6.0)

        freq_line = _c(th.accent)
        pow_line = _c(th.orange)

        pad_x, pad_y = 10.0, 9.0
        x0, x1 = pad_x, w - pad_x
        y0, y1 = pad_y, h - pad_y

        path_f = self._path(list(self._freq), x0, x1, y0, y1, self._range(self._freq))
        path_p = self._path(list(self._power), x0, x1, y0, y1, self._range(self._power))

        p.setPen(QtGui.QPen(_c(th.border), 1, Qt.PenStyle.DotLine))
        p.drawLine(QtCore.QPointF(x0, y1), QtCore.QPointF(x1, y1))

        if path_f is not None:
            p.setPen(QtGui.QPen(freq_line, 1.7))
            p.drawPath(path_f)
        if path_p is not None:
            p.setPen(QtGui.QPen(pow_line, 1.4))
            p.drawPath(path_p)


class CpuThreadRow(QWidget):
    """
    单线程一行：`[C3]  ──折线──  4.20 GHz · 31%`

    · 左侧蓝灰胶囊 = 线程号（标签）
    · 中间折线 = 该线程频率历史（纵轴窗口由 CpuThreadTrends 统一算好，见 _Spark）
    · 右侧 = 当前频率 + 当前占用（数值，高亮色）

    【v1.0.3 让折线"明显点"（用户要求）】
      · 行高 22 → 28，火花线 16 → 24：一条线占的垂直空间更大，起伏看得清；
      · 线宽 1.4 → 2.2，并在曲线下方铺一层同色渐变（alpha 60）——线再细也"看得见"；
      · 奇数行加一层极淡斑马纹底，相邻线程的线不会糊在一起；
      · 行尾画一个当前值圆点，一眼定位"现在在哪"。
    """

    ROW_H = 28

    def __init__(self, index, parent=None):
        super().__init__(parent)
        self.index = index
        self.vals = deque(maxlen=180)
        self.max_mhz = 5000.0
        self.usage = 0.0
        self.window = None            # (lo, hi)：由列表统一给的纵轴窗口
        self.setFixedHeight(self.ROW_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 0, 0)
        lay.setSpacing(8)
        self.chip = KeyChip("C%d" % index, size=10)
        self.chip.setFixedWidth(36)
        lay.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignVCenter)
        self.spark = _Spark(self)
        lay.addWidget(self.spark, stretch=1)
        self.lbl = QLabel("—")
        self.lbl.setMinimumWidth(124)
        self.lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        self.restyle()

    def paintEvent(self, _ev):
        """极淡斑马纹底（奇数行）：把相邻线程的折线在视觉上分开"""
        th = T.CURRENT_THEME
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        if self.usage >= 85.0:
            base = _c(th.warn, 26)
        elif self.index % 2 == 1:
            base = _c(th.panel, 190)
        else:
            base = _c(th.panel, 90)
        p.setBrush(base)
        p.drawRoundedRect(QtCore.QRectF(0, 1, self.width(), self.height() - 2), 4.0, 4.0)

    def set_window(self, lo, hi):
        """纵轴窗口（MHz）；None = 回退到 0..max_mhz"""
        self.window = None if lo is None or hi is None or hi <= lo else (float(lo), float(hi))

    def push(self, mhz, usage=None):
        try:
            v = float(mhz or 0.0)
        except Exception:
            v = 0.0
        self.vals.append(v)
        if usage is not None:
            try:
                self.usage = float(usage or 0.0)
            except Exception:
                self.usage = 0.0
        self.spark.update()
        self.update()

    def set_scale(self, max_mhz):
        try:
            self.max_mhz = max(1.0, float(max_mhz or 5000.0))
        except Exception:
            self.max_mhz = 5000.0

    def line_color(self):
        th = T.CURRENT_THEME
        return _c(th.warn if self.usage >= 85.0 else th.accent)

    def refresh_text(self):
        th = T.CURRENT_THEME
        cur = self.vals[-1] if self.vals else 0.0
        self.lbl.setText(("%s · %.0f%%" % (_fmt_ghz(cur), self.usage)) if cur > 0 else "—")
        col = th.warn if self.usage >= 85.0 else (th.accent if cur > 0 else th.dim)
        self.lbl.setStyleSheet(
            "font-size: 12px; font-weight: 700; color: %s;" % col)

    def restyle(self, theme=None):
        th = theme or T.CURRENT_THEME
        self.chip.restyle(th)
        self.refresh_text()
        self.spark.update()
        self.update()


class _Spark(QWidget):
    """
    极简火花线：只有线，没有轴、没有文字。

    【v1.0.2 关键修正】以前纵轴固定用 `0..max_mhz` 归一化：4.3~4.6GHz 的波动
    只占画面高度的 6%，画出来**永远是六条直线**（用户截图里正是如此）。
    现在用行上给定的**自适应窗口**（跨线程共享中位数 + 最小/最大跨度约束）：
    曲线有起伏，"谁在睿频、谁掉下来了"才看得出来；窗口下限有 25% 峰值上限，
    某个空闲核掉到 800MHz 也不会把其它核压成直线。

    【v1.0.3】线宽 2.2 + 同色渐变填充 + 行尾圆点：用户要求"让各个线程的折线明显点"。
    """

    def __init__(self, row, parent=None):
        super().__init__(parent)
        self.row = row
        self.setFixedHeight(24)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _ev):
        th = T.CURRENT_THEME
        vals = list(self.row.vals)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        # 中线 + 底线：给"频率高低"一个视觉基准
        p.setPen(QtGui.QPen(_c(th.border), 1, Qt.PenStyle.DotLine))
        p.drawLine(QtCore.QPointF(0, h / 2.0), QtCore.QPointF(w, h / 2.0))
        p.drawLine(QtCore.QPointF(0, h - 1), QtCore.QPointF(w, h - 1))
        if len(vals) < 2:
            return
        win = getattr(self.row, "window", None)
        if win:
            lo, top = win
        else:
            lo, top = 0.0, max(1.0, self.row.max_mhz)
        span = max(1.0, top - lo)
        n = len(vals)
        step = w / float(max(1, n - 1))
        path = QtGui.QPainterPath()
        last_y = h - 2.0
        for i, v in enumerate(vals):
            t = max(0.0, min(1.0, ((v or 0.0) - lo) / span))
            y = (h - 3) - (h - 6) * t
            last_y = y
            if i == 0:
                path.moveTo(0.0, y)
            else:
                path.lineTo(i * step, y)
        col = self.row.line_color()
        # 曲线下方的同色渐变（alpha 60 → 0）：线细也不至于看不见
        fill = QtGui.QPainterPath(path)
        fill.lineTo(w, h)
        fill.lineTo(0.0, h)
        fill.closeSubpath()
        grad = QtGui.QLinearGradient(0, 0, 0, h)
        c0 = QtGui.QColor(col)
        c0.setAlpha(64)
        c1 = QtGui.QColor(col)
        c1.setAlpha(6)
        grad.setColorAt(0.0, c0)
        grad.setColorAt(1.0, c1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QBrush(grad))
        p.drawPath(fill)
        # 主线：2.2px
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QtGui.QPen(col, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)
        # 行尾当前值圆点
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(QtCore.QPointF(w - 2.5, last_y), 2.6, 2.6)


class CpuThreadTrends(QWidget):
    """
    「每个线程一条折线」的可滚动列表（v1.0.1 · CPU 页）。

    【为什么要滚动条】16 核 / 32 线程全画在一张图里会糊成一团；
    这里改成**一线程一行**，共用同一纵轴刻度，谁在睿频、谁在降频一眼可见。
    核多时列表高度超过视口 → 自动出现竖直滚动条（滚动查看，不压缩行高）。

    【v1.0.2】视口由 `setFixedHeight` 改成**可伸缩**（下线 138px）：
    页面变高时先长列表（多露出几行），而不是在列表上下留一大片空白 ——
    用户截图里「每线程频率」标题和第一行 C0 之间空了 70px 就是这么来的。

    set_cores(n) / push(seq, freqs, usages) / set_scale(max_mhz)
    """

    MIN_H = 138           # 视口最小高度（约 6 行可见，其余滚动）
    VIEW_H = MIN_H        # 兼容旧名字（测试/外部若引用过）

    def __init__(self, n_cores=0, parent=None):
        super().__init__(parent)
        self._seq = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(self.MIN_H)
        self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.inner = QWidget()
        self.inner.setObjectName("ThreadTrendInner")
        self.vbox = QVBoxLayout(self.inner)
        # 右侧留 10px：竖直滚动条紧贴数值会看着像"数字被压住"
        self.vbox.setContentsMargins(0, 0, 10, 0)
        self.vbox.setSpacing(1)
        self.vbox.addStretch(1)
        self.scroll.setWidget(self.inner)
        outer.addWidget(self.scroll)
        self.rows = []
        self.max_mhz = 5000.0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.set_cores(n_cores)

    # ------------------------------------------------------------------ 结构
    def set_cores(self, n):
        """
        调整线程行数 —— **增量式**：只增删行，不清空已有行的历史。

        【v1.0.1 踩坑】原来这里是「先全删再全建」：只要线程数变一次
        （例如先看到 16 核的合成数据、随后又收到 12 核的真实快照），
        所有历史点就被清空，折线永远只剩 1 个点、看起来像没画。
        """
        n = max(0, int(n or 0))
        if n == len(self.rows):
            return
        while len(self.rows) > n:
            row = self.rows.pop()
            self.vbox.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        while len(self.rows) < n:
            i = len(self.rows)
            row = CpuThreadRow(i)
            row.set_scale(self.max_mhz)
            self.vbox.insertWidget(self.vbox.count() - 1, row)
            self.rows.append(row)

    def set_scale(self, max_mhz):
        try:
            self.max_mhz = max(1.0, float(max_mhz or 5000.0))
        except Exception:
            self.max_mhz = 5000.0
        for r in self.rows:
            r.set_scale(self.max_mhz)

    # ------------------------------------------------------------------ 纵轴窗口
    def auto_window(self):
        """
        全线程共享的自适应纵轴窗口 (lo, hi)。

        规则：中位数 ± 跨度/2；跨度 = clamp(真实极差, 6% 满频, 25% 峰值)。
        · 4.3~4.6GHz 的窄幅波动 → 窗口就收窄到这一段，曲线占满整行高度；
        · 某个空闲核掉到 800MHz → 跨度封顶 25% 峰值，其余核仍然是斜的/弯的，
          而空闲核被裁在窗口下沿（位置最低 = 频率最低，语义正确）。
        """
        vals = [v for r in self.rows for v in r.vals if v > 0]
        if len(vals) < 2:
            return None
        vals.sort()
        med = vals[len(vals) // 2]
        full = max(1.0, self.max_mhz)
        floor_span = 0.06 * full
        span = max(vals[-1] - vals[0], floor_span)
        span = min(span, max(0.25 * vals[-1], floor_span))
        return (med - span / 2.0, med + span / 2.0)

    # ------------------------------------------------------------------ 数据
    def push(self, seq, freqs, usages=None):
        if seq is not None and seq == self._seq:
            return
        self._seq = seq
        freqs = list(freqs or [])
        usages = list(usages or [])
        if len(freqs) != len(self.rows):
            self.set_cores(len(freqs))
        for i, row in enumerate(self.rows):
            row.push(freqs[i] if i < len(freqs) else 0.0,
                     usages[i] if i < len(usages) else None)
        win = self.auto_window()
        for row in self.rows:
            if win:
                row.set_window(win[0], win[1])
            else:
                row.set_window(None, None)
            row.refresh_text()
    def samples(self, index=0):
        """自检用：取第 index 个线程的历史点"""
        if 0 <= index < len(self.rows):
            return list(self.rows[index].vals)
        return []

    def restyle(self, theme=None):
        th = theme or T.CURRENT_THEME
        self.inner.setStyleSheet(
            "QWidget#ThreadTrendInner { background: transparent; }")
        for r in self.rows:
            r.restyle(th)


__all__ = ["CpuTrendChart", "CpuThreadTrends", "CpuThreadRow"]
