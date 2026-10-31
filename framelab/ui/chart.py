# -*- coding: utf-8 -*-
"""
主折线图 —— 「只留一条纯净的实时 FPS 曲线」（改进建议书 三）。

清理掉旧版的 3 条包络线与满屏英文坐标，改为：
  · 默认纵轴 = 实时帧数 (FPS)：1000 / frametime，轻量平滑防毛刺；
    右上角一个小巧的 [FPS / ms] 开关给老手切换；
  · 一条带半透明底部渐变填充的主折线；
  · 一条淡淡的水平虚线标出当前「平均帧」基准；
  · 只在漏帧/顿挫的波谷处打醒目红色圆点（点击可看病因）；
  · 坐标轴不再交给 autoRange（空载时会冒出 -651.29189 / 90.809335 这类浮点乱码）：
    X/Y 范围一律按改进建议书 四 自己算、并用 padding=0.0 钉死，
    见 `_apply_axis_limits`；ms 视角另加 16.7ms / 33.3ms 两条参考虚线。
"""
import numpy as np
import pyqtgraph as pg

from PyQt6 import QtCore, QtGui
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from .. import theme as T
from ..theme import CHART_STYLE_CAPFRAMEX, CHART_STYLE_HUD
from ..stats import smooth_series, window_stats, drop_flags
from ..utils import pg_symbol

MODE_FPS = "fps"
MODE_MS = "ms"

MAX_RENDER_POINTS = 3000       # 单次最多渲染的帧数（超出走抽稀，保 30 FPS）
DROP_CLICK_PX = 18.0           # 点击命中红点的像素半径

# ---- 坐标轴范围基线（改进建议书 四：空载乱码 / ms 视角 100ms 过高）----
EMPTY_X_RANGE = (0.0, 100.0)   # 空载 / 单帧：X 锁死 0~100，杜绝 -651.29189 这类乱码
EMPTY_FPS_RANGE = (0.0, 144.0)  # 空载 / 单帧：FPS 视角锁死 0~144（高刷屏常见上限）
FPS_Y_FLOOR = 120.0            # FPS 上限永不低于 120（严禁收缩成 [97,101] 窄带）
FPS_Y_PAD = 1.15               # FPS 上限 = max(120, 峰值 × 1.15)
MS_Y_MAX = 35.0                # ms 视角默认锁死 0~35（16.7ms 上下留足起伏空间）
MS_Y_HARD_MAX = 60.0           # ms 视角即便有真实大尖峰也不越过 60
MIN_X_SPAN = 10.0              # X 轴最小跨度，防单点退化后被 padding 撑出小数
MS_REF_LINES = ((16.7, "16.7ms (60帧)"), (33.3, "33.3ms (30帧)"))


class FpsChart(QWidget):
    """实时帧率曲线（自带均值基准线与漏帧红点）"""

    frameClicked = pyqtSignal(int)      # FrameRecord.idx（-1 表示取消锁定）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = MODE_FPS
        self._frames = []
        self._drop_pts = []            # [(idx, y), ...]
        self._avg_fps = 0.0
        self._locked_idx = None
        self._fill_item = None
        self._gradient_item = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setObjectName("FpsPlot")
        self.plot_widget.setMouseEnabled(x=True, y=True)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.18)
        self.plot_widget.getPlotItem().setClipToView(True)
        try:
            self.plot_widget.getPlotItem().setDownsampling(auto=True, method="peak")
        except Exception:
            pass
        # 【为什么必须关掉 autoRange】pyqtgraph 自缩放是拿「数据范围 ± padding」反推视野的：
        # 空载（或只有 1 帧）时数据范围退化成一个点，反推出来的就是
        # X = -651.29189 / Y = 90.809335 这种浮点乱码。
        # 轴范围从此一律由 _apply_axis_limits 自己算、自己钉死。
        vb = self.plot_widget.getPlotItem().getViewBox()
        try:
            # 帧序号从 0 起、帧率/帧耗时不可能为负 —— 钳死下限，用户也拖不出负区间
            vb.setLimits(xMin=0, yMin=0)
        except Exception:
            pass
        try:
            vb.enableAutoRange(axis="xy", enable=False)
        except Exception:
            try:
                vb.disableAutoRange()
            except Exception:
                pass

        # 主折线（带半透明底部渐变填充）
        self.curve = self.plot_widget.plot(pen=pg.mkPen(color=T.CURRENT_THEME.accent, width=1.6))
        # 平均帧基准线（淡淡水平虚线）
        self.avg_line = pg.InfiniteLine(
            angle=0, movable=False,
            pen=pg.mkPen(T.CURRENT_THEME.warn, width=1.1, style=Qt.PenStyle.DashLine),
            label="平均 {value:.0f}", labelOpts={"position": 0.06, "color": T.CURRENT_THEME.warn,
                                                 "fill": pg.mkBrush(0, 0, 0, 0),
                                                 "movable": False})
        self.plot_widget.addItem(self.avg_line, ignoreBounds=True)
        # 漏帧红点
        self.scatter = pg.ScatterPlotItem(size=11, pen=pg.mkPen("#ffffff", width=1.0),
                                          brush=pg.mkBrush(T.CURRENT_THEME.bad), symbol="o")
        self.plot_widget.addItem(self.scatter)
        # 锁定竖线
        self.locked_line = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen(T.CURRENT_THEME.bad, width=1.6))
        self.locked_line.setVisible(False)
        self.plot_widget.addItem(self.locked_line, ignoreBounds=True)

        # 帧耗时视角的两条参考虚线（16.7ms = 60 帧 / 33.3ms = 30 帧）。
        # 【为什么固定值直接写进 label】InfiniteLine 的 label 只在构造时按 format
        # 格式化一次，之后 setValue 不会重刷文本；本需求是固定刻度值，正好无碍。
        # label 位置放右侧 0.94，避免压住左侧的曲线与「平均」虚线。
        self.ms_ref_lines = []
        for ref_val, ref_text in MS_REF_LINES:
            ln = pg.InfiniteLine(
                angle=0, movable=False, pos=ref_val,
                pen=pg.mkPen(T.CURRENT_THEME.dim, width=1, style=Qt.PenStyle.DashLine),
                label=ref_text,
                labelOpts={"position": 0.94, "color": T.CURRENT_THEME.dim,
                           "fill": pg.mkBrush(0, 0, 0, 0), "movable": False})
            ln.setVisible(False)        # 只在 ms 视角显示
            self.plot_widget.addItem(ln, ignoreBounds=True)
            self.ms_ref_lines.append(ln)

        self.plot_widget.getPlotItem().sigRangeChanged.connect(self._update_gradient)
        self.plot_widget.scene().sigMouseClicked.connect(self._on_click)
        # 【手动缩放】用户滚轮/拖拽后置 True → 刷新不再强行改轴范围（见 _apply_axis_limits）
        self._user_zoom = False
        try:
            self.plot_widget.getPlotItem().getViewBox().sigRangeChangedManually.connect(
                self._on_manual_range)
        except Exception:
            pass
        lay.addWidget(self.plot_widget, stretch=1)
        self._apply_axis_limits()       # 建图即钉死初始范围（空载 → 0~100 / 0~144）
        self.apply_theme(T.CURRENT_THEME)

    # ------------------------------------------------------------------ 数据
    def set_mode(self, mode):
        self.mode = MODE_MS if mode == MODE_MS else MODE_FPS
        self.plot_widget.setLabel("left", "帧耗时 (ms)" if self.mode == MODE_MS else "实时帧数 (FPS)",
                                  color=T.CURRENT_THEME.dim)
        self._user_zoom = False     # 换视角回到自动轴，手动缩放作废
        self._apply_axis_limits()   # 换视角立刻换轴（含参考虚线显隐、ms 锁 0~35）
        if self._frames:
            self.update_series(self._frames)

    def _series_peak(self, ms=False):
        """
        当前可见序列的峰值（决定 Y 轴上限）。

        【为什么取「稳健峰值」与「平滑峰值」的较大者】
        画出来的是 smooth_series 平滑后的曲线，轴上界要跟**眼睛看到的曲线**贴合；
        但移动平均会把孤立尖峰削掉（单帧 300fps 平均后只剩 ~166），只看平滑值
        的话真实尖峰会被裁到画面外。取两者较大者即可两头兼顾。

        【v1.0.1 为什么不用 np.max】PresentMon 在加载/切窗口时会吐极短帧
        （实测见过 2554 fps、477 fps），一个点就把 Y 轴顶到几百，
        真实曲线被压成贴着底边的一条直线 —— 用户看到的就是「图全是平的」。
        现在用「第 k 大」：k = max(3, 2% 点数)，允许丢掉最多 2%（至少 3 个）极值点；
        成片的高速段（例如 4% 帧跑到 300fps）仍然照常进轴。
        """
        frames = self._frames[-MAX_RENDER_POINTS:] if self._frames else []
        if not frames:
            return None
        xs = np.asarray([float(f.idx) for f in frames], dtype=np.float64)
        fts = np.asarray([max(0.1, float(f.frametime)) for f in frames], dtype=np.float64)
        n = int(fts.size)
        if ms:
            # ms 视角**故意用真最大值**：一条 45ms 的卡顿帧正是用户要看的东西，
            # 而且纵轴本来就被 MS_Y_HARD_MAX=60 封顶，不存在被带飞的问题。
            vals = fts
            peak = float(np.max(vals))
            try:
                peak = max(peak, float(np.max(smooth_series(xs, vals, 3)[1])))
            except Exception:
                pass
            return peak
        vals = 1000.0 / fts
        k = max(1, min(max(3, int(np.ceil(0.02 * n))), n))
        peak = float(np.partition(vals, n - k)[n - k])
        try:
            peak = max(peak, float(np.partition(smooth_series(xs, vals, 3)[1],
                                                n - k)[n - k]))
        except Exception:
            pass
        return peak

    def _set_ms_ref_visible(self, on):
        """16.7ms / 33.3ms 参考虚线只在「帧耗时」视角出现（切回 FPS 必须隐藏）"""
        for ln in self.ms_ref_lines:
            try:
                ln.setVisible(bool(on))
            except Exception:
                pass

    def _apply_axis_limits(self, max_ft=None, max_fps=None):
        """
        统一决定 X / Y 轴范围（改进建议书 四）。set_mode / clear / update_series /
        apply_theme 四个入口都走这里，保证切视角、清空、换主题后范围立刻正确。

        规则：
          · 数据 < 2 帧（含空载）：X 固定 (0, 100)；FPS 固定 (0, 144)，ms 固定 (0, 35)；
          · FPS 视角：Y 上限 max(120, 峰值 × 1.15)，下限锁死 0，
            **严禁**收缩成 [97, 101] 这种极窄区间；
          · ms 视角：默认锁死 (0, 35)，只有画面里真的出现 > 35ms 的尖峰，
            才按 min(max(35, 峰值 × 1.1), 60) 适度微扩。

        【手动缩放优先】用户一旦滚轮/拖拽过视图（_user_zoom=True），这里直接
        放行 —— 否则每 tick 一次 setXRange/setYRange 会把手动放大立刻抹掉，
        表现为「数轴怎么都放大不了」。清空数据 / 切视角时才复位。

        【为什么全程 padding=0.0】pyqtgraph 的 setRange 默认带 2% padding，
        会把整齐的 0~144 重新变成 -2.88~146.88 —— 小数乱码就是这么来的。
        """
        if getattr(self, "_user_zoom", False):
            return
        try:
            vb = self.plot_widget.getPlotItem().getViewBox()
        except Exception:
            return
        # 自缩放必须先关死：否则紧接着的 setData / 重绘又会把范围改回去
        try:
            vb.enableAutoRange(axis="xy", enable=False)
        except Exception:
            pass
        try:
            # 双保险：即使后续有人重置过 limits，这里也把负区间重新钳死
            vb.setLimits(xMin=0, yMin=0)
        except Exception:
            pass

        frames = self._frames[-MAX_RENDER_POINTS:] if self._frames else []
        sparse = len(frames) < 2        # 空载 / 单帧：一律固定范围，不进入动态缩放
        if sparse:
            x0, x1 = EMPTY_X_RANGE
        else:
            xs = np.asarray([float(f.idx) for f in frames], dtype=np.float64)
            # 帧序号理论上从 0 起，但脏数据可能带负值 —— 下限永远不许为负；
            # 上限至少留 MIN_X_SPAN，避免跨度退化成 0 后又靠 padding 撑出小数尾巴
            x0 = max(0.0, float(np.min(xs)))
            x1 = max(float(np.max(xs)), x0 + MIN_X_SPAN)

        if self.mode == MODE_MS:
            peak = max_ft
            if peak is None:
                peak = self._series_peak(ms=True)
            if (not sparse) and peak is not None and float(peak) > MS_Y_MAX:
                y0, y1 = 0.0, min(max(MS_Y_MAX, float(peak) * 1.1), MS_Y_HARD_MAX)
            else:
                y0, y1 = 0.0, MS_Y_MAX
        else:
            peak = max_fps
            if peak is None:
                peak = self._series_peak(ms=False)
            if (not sparse) and peak is not None and float(peak) > 0:
                y0, y1 = 0.0, max(FPS_Y_FLOOR, float(peak) * FPS_Y_PAD)
            else:
                y0, y1 = EMPTY_FPS_RANGE

        try:
            vb.setXRange(x0, x1, padding=0.0)
            vb.setYRange(y0, y1, padding=0.0)
        except Exception:
            pass
        # 参考虚线跟着视角走（清空数据时由 clear() 再显式藏一次）
        self._set_ms_ref_visible(self.mode == MODE_MS)

    def _on_manual_range(self, *args):
        """
        用户手动缩放/平移（滚轮、拖拽、框选）→ 进入「手动视野」模式。

        只置一个标志：之后 update_series / 刷新循环都不再改轴范围，
        直到 clear()（清空重采）或 set_mode()（FPS/ms 切换）。
        """
        self._user_zoom = True

    def update_series(self, frames, avg_fps=None, peak_fps=None):
        """
        喂入帧序列（时间升序）：画主曲线 + 均值线 + 漏帧红点。

        peak_fps : 可选的 Y 轴上限依据（主窗口传入 stats 的**稳健最高帧**）。
                   传了就用它，保证「最高帧卡」与「Y 轴上界」口径完全一致；
                   不传则退回本控件自己的稳健峰值（_series_peak）。
        """
        self._frames = list(frames)
        if not self._frames:
            self.curve.setData([], [])
            self.scatter.setData([])
            self._drop_pts = []
            self._apply_axis_limits()   # 空序列也要把轴收回固定范围（防止沿用旧视野）
            return
        disp = self._frames[-MAX_RENDER_POINTS:]
        xs = np.array([f.idx for f in disp], dtype=np.float64)
        fts = np.array([max(0.1, float(f.frametime)) for f in disp], dtype=np.float64)
        fps = 1000.0 / fts
        if self.mode == MODE_FPS:
            series = fps
            x, y = smooth_series(xs, fps, 3)
            self.curve.setData(x, y)
            self.plot_widget.setLabel("left", "实时帧数 (FPS)", color=T.CURRENT_THEME.dim)
        else:
            series = fts
            x, y = smooth_series(xs, fts, 3)
            self.curve.setData(x, y)
            self.plot_widget.setLabel("left", "帧耗时 (ms)", color=T.CURRENT_THEME.dim)
        # 轴范围跟着「刚画出来的这条曲线」走：
        #   · FPS 视角 → 稳健峰值（丢掉 2% 极值，避免单帧 477fps 顶飞整条曲线）
        #   · ms  视角 → 真最大值（卡顿尖峰正是要看的东西，且上限已封顶 60ms）
        if self.mode == MODE_FPS and peak_fps:
            peak = float(peak_fps)
        elif self.mode == MODE_MS:
            peak = float(max(np.max(y) if y.size else 0.0,
                             np.max(series) if series.size else 0.0))
        else:
            n = int(series.size)
            k = max(1, min(max(3, int(np.ceil(0.02 * n))), n)) if n else 1
            peak = float(max(np.partition(y, n - k)[n - k] if y.size else 0.0,
                             np.partition(series, n - k)[n - k] if n else 0.0))
        if self.mode == MODE_FPS:
            self._apply_axis_limits(max_fps=peak)
        else:
            self._apply_axis_limits(max_ft=peak)

        # 漏帧红点：只在波谷处打点
        flags = drop_flags(disp, fts)
        pts = []
        self._drop_pts = []
        for i in range(len(disp)):
            if not flags[i]:
                continue
            px, py = float(xs[i]), float(fps[i] if self.mode == MODE_FPS else fts[i])
            pts.append({"pos": (px, py), "brush": pg.mkBrush(T.CURRENT_THEME.bad),
                        "pen": pg.mkPen("#ffffff", width=1.0),
                        "symbol": pg_symbol("o"), "size": 11})
            self._drop_pts.append((px, py))
        self.scatter.setData(pts)

        if avg_fps is None:
            avg_fps = window_stats(self._frames)["avg_fps"]
        self.set_avg(avg_fps)
        self._update_gradient()
        self._refresh_locked()

    def set_avg(self, avg_fps):
        self._avg_fps = float(avg_fps or 0.0)
        if self._avg_fps > 0:
            val = self._avg_fps if self.mode == MODE_FPS else (1000.0 / self._avg_fps)
            self.avg_line.setValue(val)
            # 【坑】pyqtgraph 的 InfiniteLine(label="{value}") 只在构造时格式化一次，
            # 之后 setValue 不会刷新文字 —— 必须显式改写 label 文本，
            # 否则界面上会永远显示 "平均 0"。
            try:
                self.avg_line.label.setText("平均 %.0f" % self._avg_fps)
            except Exception:
                pass
            self.avg_line.setVisible(True)
        else:
            self.avg_line.setVisible(False)

    def clear(self):
        self._frames = []
        self._drop_pts = []
        self._user_zoom = False         # 清空会话 → 手动缩放复位，回到自动轴
        self.curve.setData([], [])
        self.scatter.setData([])
        self.avg_line.setVisible(False)
        self.set_locked(None)
        self._apply_axis_limits()       # 回到固定范围，别让空图残留上一次的小数视野
        self._set_ms_ref_visible(False)  # 清空后参考虚线也收起来（空图上不留两条灰线）

    def drop_points(self):
        return list(self._drop_pts)

    def set_locked(self, idx):
        self._locked_idx = idx
        self._refresh_locked()

    def _refresh_locked(self):
        if self._locked_idx is None:
            self.locked_line.setVisible(False)
            return
        self.locked_line.setValue(float(self._locked_idx))
        self.locked_line.setVisible(True)

    # ------------------------------------------------------------------ 交互
    def _on_click(self, evt):
        """点击：命中红点 → 选中该帧；否则选最近的一帧（都发 frameClicked）"""
        try:
            if evt.button() != Qt.MouseButton.LeftButton:
                return
        except Exception:
            pass
        vb = self.plot_widget.getPlotItem().getViewBox()
        scene_pos = evt.scenePos()
        if not self.plot_widget.sceneBoundingRect().contains(scene_pos):
            return
        try:
            view_pos = vb.mapSceneToView(scene_pos)
        except Exception:
            return
        if not self._frames:
            return
        # 命中判定：像素距离
        best_idx, best_d = None, None
        for (px, py) in self._drop_pts:
            try:
                sp = vb.mapViewToScene(QtCore.QPointF(px, py))
            except Exception:
                continue
            d = (sp.x() - scene_pos.x()) ** 2 + (sp.y() - scene_pos.y()) ** 2
            if best_d is None or d < best_d:
                best_d, best_px = d, px
        if best_d is not None and best_d <= DROP_CLICK_PX ** 2:
            best_idx = int(best_px)
        else:
            vx = view_pos.x()
            best_idx = min((f.idx for f in self._frames), key=lambda i: abs(i - vx))
        self.set_locked(best_idx)
        self.frameClicked.emit(int(best_idx))

    # ------------------------------------------------------------------ 外观
    def _update_gradient(self, *args):
        """半透明底部渐变填充（用 sceneBoundingRect 做纵向渐变）"""
        style = T.CURRENT_CHART_STYLE
        if style != CHART_STYLE_HUD:
            if self._fill_item is not None:
                try:
                    self._fill_item.setBrush(QtGui.QBrush(QtCore.Qt.BrushStyle.NoBrush))
                except Exception:
                    pass
            return
        if self._fill_item is None:
            try:
                base = self.plot_widget.plot(pen=None, brush=None)
                self._fill_item = pg.FillBetweenItem(self.curve, base)
                self._gradient_item = base
                self.plot_widget.addItem(self._fill_item, ignoreBounds=True)
                self.plot_widget.removeItem(base)
                self.plot_widget.addItem(base, ignoreBounds=True)
            except Exception:
                self._fill_item = None
                return
        try:
            rect = self.plot_widget.sceneBoundingRect()
            grad = QtGui.QLinearGradient(0, rect.top(), 0, rect.bottom())
            c = QtGui.QColor(T.CURRENT_THEME.accent)
            grad.setColorAt(0.0, QtGui.QColor(c.red(), c.green(), c.blue(), 0))
            c2 = QtGui.QColor(T.CURRENT_THEME.accent)
            c2.setAlpha(64)
            grad.setColorAt(1.0, c2)
            self._fill_item.setBrush(QtGui.QBrush(grad))
        except Exception:
            pass

    def apply_theme(self, theme):
        th = theme or T.CURRENT_THEME
        try:
            self.plot_widget.setBackground(th.chart_bg)
            vb = self.plot_widget.getPlotItem().getViewBox()
            try:
                vb.setBackgroundColor(th.chart_bg)
            except Exception:
                pass
            for axis_name in ("left", "bottom"):
                ax = self.plot_widget.getAxis(axis_name)
                ax.setPen(pg.mkPen(th.chart_text))
                ax.setTextPen(pg.mkPen(th.chart_text))
            self.plot_widget.showGrid(x=True, y=True, alpha=0.22,
                                      pen=pg.mkPen(th.chart_grid, width=1))
        except Exception:
            pass
        pen_color = "#2197F3" if T.CURRENT_CHART_STYLE == CHART_STYLE_CAPFRAMEX else th.accent
        try:
            self.curve.setPen(pg.mkPen(color=pen_color, width=1.6))
            self.avg_line.setPen(pg.mkPen(th.warn, width=1.1, style=Qt.PenStyle.DashLine))
            self.scatter.setBrush(pg.mkBrush(th.bad))
            self.locked_line.setPen(pg.mkPen(th.bad, width=1.6))
        except Exception:
            pass
        # 参考虚线的颜色不写死：跟着当前主题的暗色走（含 label 文字色）
        for ln in self.ms_ref_lines:
            try:
                ln.setPen(pg.mkPen(th.dim, width=1, style=Qt.PenStyle.DashLine))
                ln.label.setColor(th.dim)
            except Exception:
                pass
        self._apply_axis_limits()       # 换主题后轴范围立即回到正确档位
        self._update_gradient()

    def apply_chart_style(self, style):
        pen_color = "#2197F3" if style == CHART_STYLE_CAPFRAMEX else T.CURRENT_THEME.accent
        try:
            self.curve.setPen(pg.mkPen(color=pen_color, width=1.6))
        except Exception:
            pass
        self._update_gradient()
