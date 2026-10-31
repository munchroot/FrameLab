# -*- coding: utf-8 -*-
"""
右侧主画布的 5 个页面 —— 改进建议书 一.2。

    废除「左栏窄抽屉里塞图表」的做法：左栏只留 5 张导航卡（navtabs.py），
    右侧主区域上半部分（顶部状态横幅 + 4 张核心帧数大卡）**常驻**，
    下半部分改成一个 QStackedWidget，点击导航卡**整块替换**：

      Page 0  📈 帧率总览   → 实时帧数折线图 + 底部卡顿病历卡（默认选中）
      Page 1  🔥 CPU 状态   → 频率/功率总览折线 + 每线程频率折线 + 各核频率柱状条
      Page 2  🎮 显卡与显存 → 3D 负载大仪表盘 + 核心频率/温度 + 物理/共享显存大横条 + Top5 进程表
      Page 3  💾 内存与磁盘 → 物理内存大圆环 + 换页速率 + 读盘能量条 + 进程排行
      Page 4  🌐 网络与环境 → 上下行吞吐 + 着色器缓存目录文件数与写入爆发监控

铁律：本模块**不做任何判定算法**，所有阈值与结论都来自既有底层探针
（HardwareSnapshot）与 `stuttertriage.stats`；这里只负责「把数字变成图形 + 大白话」。
"""
from PyQt6 import QtGui
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                             QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import theme as T
from ..consts import bind_globals as _bind_const_globals
from ..probes import cpu_count, per_core_max_mhz
from ..utils import human_size
from .chart import FpsChart
from .cpu_trend import CpuThreadTrends, CpuTrendChart
from .diagnosis import MiniDiagnosisCard
from .gauge import (CapsuleBar, CpuFreqBars, MiniEnergyBar, NetRateBars, RingGauge,
                    StatusDot, VramSplitBar, vram_text)
from .kv import KVGrid, KVPanel, KVRow, KeyChip
from .widgets import ElidedLabel

# CPU0 拥堵 / 着色器爆发阈值需随配置热更新
_bind_const_globals(globals())

# 三档级别 → 主题色角色（各页 verdict 共用）
_LEVEL_ROLE = {"ok": "green", "warn": "warn", "danger": "bad",
               "info": "accent", "unknown": "dim"}

# 进程级缺页归因的门槛（MB/s）：与 `engines/hardware.py` 里
# `if snap.page_in > 2.0` 保持一致 —— 低于它时引擎刻意跳过 1200 进程遍历，
# 于是 `pagein_top` 必然为空。这里只用来在界面上**解释为什么空**，不参与采集。
PAGEIN_TOP_GATE_MB = 2.0


def gpu_load_pct(hw, ctx=None):
    """
    「GPU 3D 负载」的统一口径：

      1. 优先 `stats.gpu_3d_load_pct(hw)`（引擎级 3D 占用，最贴近游戏真实负载）；
      2. 引擎数据缺失时退回 PresentMon 的 GPU 忙时比（ctx["gpu_pct"]）；
      3. 都没有 → 0.0。

    返回值恒定在 0~100。
    """
    val = 0.0
    try:
        from ..stats import gpu_3d_load_pct as _g3
        val = float(_g3(hw))
    except Exception:
        val = 0.0
    if val <= 0.0 and ctx:
        try:
            val = float(ctx.get("gpu_pct") or 0.0)
        except Exception:
            val = 0.0
    return max(0.0, min(100.0, val))


def _fmt_mhz(mhz):
    try:
        m = float(mhz)
    except Exception:
        return "—"
    if m <= 0:
        return "—"
    return "%.0f MHz" % m if m < 1000 else "%.2f GHz" % (m / 1000.0)


def _fmt_rate(kbs):
    try:
        v = float(kbs)
    except Exception:
        return "—"
    if v >= 1024.0:
        return "%.2f MB/s" % (v / 1024.0)
    return "%.0f KB/s" % v


def _mk_table(headers, widths=None, word_wrap=False):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(list(headers))
    hdr = t.horizontalHeader()
    for i in range(len(headers)):
        if widths and i < len(widths) and widths[i] is not None:
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            t.setColumnWidth(i, int(widths[i]))
        elif i == 0 or (widths and widths[i] is None and i == len(headers) - 1):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        else:
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.setWordWrap(bool(word_wrap))
    return t


def _set_cells(table, cells):
    """
    把 [(text, fg), ...] 增量同步进表格（避免每帧重建控件）。

    【v1.0.2 踩坑】以前 fg 为 None 时执行 `item.setForeground(QtGui.QBrush())`：
    默认构造的 QBrush 是 **NoBrush**，等于给这一格塞了一个"没有颜色"的前景色，
    委托只能退化成 palette 色 —— 深色主题下就是一片看不清的灰字。
    正确做法是把 ForegroundRole 数据清掉（None），让单元格回到主题默认前景色。
    """
    n = len(cells)
    if table.rowCount() != n:
        table.setRowCount(n)
    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            text, fg = (cell + (None,))[:2] if isinstance(cell, tuple) else (cell, None)
            item = table.item(r, c)
            if item is None:
                item = QTableWidgetItem()
                table.setItem(r, c, item)
            if item.text() != (text or ""):
                item.setText(text or "")
            item.setData(Qt.ItemDataRole.ForegroundRole,
                         QtGui.QColor(fg) if fg else None)


# ==============================================================================
# 页面基类
# ==============================================================================
class _Page(QWidget):
    """
    页面骨架：卡片头部（标题）+ 大字结论 + 一行建议 + 主体区。

    refresh(hw, ctx) 由主窗口按节拍调用；badge(hw, ctx) 返回左栏导航卡徽章文案。

    【v1.0.0】副标题（SUBTITLE）与图表提示行一律删除：那些都是成句的说明文字，
    灰字堆在一起只显得杂乱。结论行/建议行改成 ElidedLabel（单行 + 省略号 + tooltip），
    页面高度因此恒定，切数据时主体区不会被顶得上下跳。
    """

    TITLE = "页面"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageCard")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # 【v1.0.1 规范 §2】页面顶部只占一行：标题在左，状态胶囊在右。
        # 以前是「标题一行 + 结论一行 + 建议一行」三行堆叠，白占垂直空间。
        head = QHBoxLayout()
        head.setSpacing(10)
        self.lbl_title = QLabel(self.TITLE)
        self.lbl_title.setStyleSheet("font-size: 16px; font-weight: 600;")
        head.addWidget(self.lbl_title)
        head.addStretch(1)
        self.lbl_verdict = ElidedLabel("正在等待数据…")
        self.lbl_verdict.setStyleSheet("font-size: 12px; font-weight: 600;")
        # ElidedLabel 默认横向 Ignored（会被 stretch 压成 0 宽），状态胶囊必须保底可见
        self.lbl_verdict.setSizePolicy(QSizePolicy.Policy.Preferred,
                                       QSizePolicy.Policy.Fixed)
        self.lbl_verdict.setMinimumWidth(160)
        head.addWidget(self.lbl_verdict, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(head)

        self.lbl_advice = ElidedLabel("")
        self.lbl_advice.setStyleSheet("font-size: 11px; font-weight: 500;")
        root.addWidget(self.lbl_advice)

        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        root.addLayout(self.body, stretch=1)
        self._level = "unknown"
        self._verdict_text = ""
        self._advice_text = ""
        self._base_restyle()

    # ---------------- 供子类实现 ----------------
    def refresh(self, hw, ctx):
        """hw: HardwareSnapshot | None；ctx: 主窗口补充的实时上下文 dict"""

    def badge(self, hw, ctx):
        """左栏导航卡的一行极简状态徽章 → (text, level)"""
        return ("等待数据", "unknown")

    # ---------------- 通用 ----------------
    def set_verdict(self, text, level="ok", advice=""):
        text = text or ""
        if text != self._verdict_text:
            self._verdict_text = text
            self.lbl_verdict.setText(text)
        if (level or "ok") != self._level:
            self._level = level or "ok"
        self._paint_verdict()
        advice = advice or ""
        if advice != self._advice_text:
            self._advice_text = advice
            self.lbl_advice.setText(("▸ " + advice) if advice else "")

    def restyle(self):
        """主题热切换入口：子类覆写时必须先 super().restyle() 再刷自己的控件。"""
        self._base_restyle()

    def _paint_verdict(self):
        th = T.CURRENT_THEME
        col = QtGui.QColor(getattr(th, _LEVEL_ROLE.get(self._level, "fg"), th.fg))
        # 状态胶囊：同色系低透明度底 + 600 字重，避免"细体小灰字"
        self.lbl_verdict.setStyleSheet(
            "font-size: 12px; font-weight: 600; color: %s;"
            " background-color: rgba(%d,%d,%d,38);"
            " border-radius: 9px; padding: 2px 10px;"
            % (col.name(), col.red(), col.green(), col.blue()))

    def _base_restyle(self):
        th = T.CURRENT_THEME
        self.setStyleSheet(
            "QWidget#PageCard { background-color: %s; border: 1px solid %s;"
            " border-radius: 8px; }" % (th.panel, th.border))
        self.lbl_title.setStyleSheet(
            "font-size: 16px; font-weight: 600; color: %s;" % th.accent)
        self._paint_verdict()
        self.lbl_advice.setStyleSheet(
            "font-size: 11px; font-weight: 500; color: %s;" % th.dim)


# ==============================================================================
# Page 0：帧率总览（折线图 + 病历卡）
# ==============================================================================
class FpsOverviewPage(_Page):
    """Page 0：实时帧数折线图 + 底部卡顿病历卡（改进建议书 三 / 四.3 / 五.3）"""

    TITLE = "📈 帧率总览"

    def __init__(self, parent=None):
        super().__init__(parent)
        # 图表头部工具条（v1.0.0：删掉「实时帧数曲线 · 点击任意位置…」那行灰字，
        # 只留两个动作按钮；按钮本身就是最好的说明书）
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addStretch()
        self.btn_fps_mode = QPushButton("FPS / ms")
        self.btn_fps_mode.setObjectName("NavButton")
        self.btn_fps_mode.setCheckable(True)
        self.btn_fps_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fps_mode.setToolTip("切换纵轴：实时帧数 (FPS) ↔ 帧生成时间 (ms)")
        bar.addWidget(self.btn_fps_mode)
        self.btn_jump_drop = QPushButton("⤓ 最近漏帧")
        self.btn_jump_drop.setObjectName("NavButton")
        self.btn_jump_drop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_jump_drop.setToolTip("把锁定线跳到最近一次真顿挫")
        bar.addWidget(self.btn_jump_drop)
        self.body.addLayout(bar)

        self.fps_chart = FpsChart()
        self.fps_chart.setMinimumHeight(300)
        self.body.addWidget(self.fps_chart, stretch=1)

        self.diag_card = MiniDiagnosisCard()
        self.body.addWidget(self.diag_card)
        self.diag_card.show_idle()      # 常驻占位：高度恒定，点开/关闭都不推挤图表
        self.restyle()

    # 便捷别名（主窗口按旧名字接线，减少重复）
    @property
    def chart(self):
        return self.fps_chart

    def refresh(self, hw, ctx):
        """总览页头部结论：与顶部横幅同源（都读 stats 的严格顿挫口径）。"""
        avg = float(ctx.get("avg_fps") or 0.0)
        if avg <= 0:
            self.set_verdict("🟡 等待帧数据…", "unknown", "")
            return
        grade = ctx.get("grade") or "-"
        dropped = int(ctx.get("dropped") or 0)
        if ctx.get("stutter_now"):
            self.set_verdict(
                "🔴 刚才真顿挫 · 帧率砸穿局部中位数 65% 且耗时跳变 ≥8ms",
                "danger", "病历卡已给出病因与改法")
        else:
            self.set_verdict(
                "🟢 全程丝滑 · %.0f 帧 · %s 级 · 顿挫 %d 次" % (avg, grade, dropped),
                "ok", "")

    def badge(self, hw, ctx):
        avg = float(ctx.get("avg_fps") or 0.0)
        if avg <= 0:
            return ("-- FPS · 等数据", "unknown")
        return ("%.0f FPS · %s" % (avg, ctx.get("verdict") or "采集正常"), "ok")

    def restyle(self):
        super().restyle()


# ==============================================================================
# Page 1：CPU 状态
# ==============================================================================
class CpuPage(_Page):
    """
    Page 1：频率/功率总览折线 + **每线程一条折线（可滚动）** + 各核频率柱状条。

    【v1.0.1】
      · **删除方块热力图**（用户反馈：只保留下方柱状图就行）；
      · 新增「每个线程一条折线」的滚动列表：多核/多线程时用滚动条浏览，
        不用把 N 条线糊在同一张图里；
      · 频率/功率总览折线与柱状图保留（图内一律不写字，标签数值都在 KV 行）。
    """

    TITLE = "🔥 CPU 状态"

    def __init__(self, parent=None):
        super().__init__(parent)
        n = cpu_count()
        mx = per_core_max_mhz(n) or [4000]
        self._max_mhz = float(max(mx))

        # ---- 【v1.0.3 合并成一张卡】总览 KV + 均频/功率折线 + 每线程折线 ----
        # 用户要求：「除了下面的柱状图卡片，其他结合下，让各个线程的折线明显点」。
        # 原来这三块各自带底色、中间还夹着一条独立折线图，视觉上是散的；
        # 现在合成一张「CPU 频率与功耗」卡（两列 KV + 竖分隔线 + 趋势图 + 每线程折线），
        # 页面只留两张卡：上面这张 + 下面的柱状图卡。
        self.card_freq = QFrame()
        self.card_freq.setObjectName("CpuFreqCard")
        cl = QVBoxLayout(self.card_freq)
        cl.setContentsMargins(10, 8, 10, 8)
        cl.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.kv = KVGrid(spacing=4)
        self.row_freq = self.kv.add("均频", size=15, role="accent")
        self.row_power = self.kv.add("功率", size=13, role="orange")
        self.row_peak = self.kv.add("峰值", size=13, role="val")
        top.addWidget(self.kv, stretch=2)
        self.sep = QFrame()
        self.sep.setFrameShape(QFrame.Shape.VLine)
        self.sep.setFixedWidth(1)
        top.addWidget(self.sep)
        self.kv_core = KVGrid(spacing=4)
        self.row_cores = self.kv_core.add("线程数", size=13, role="val")
        self.row_top = self.kv_core.add("最高核频", size=13, role="val")
        self.row_total = self.kv_core.add("总占用", size=13, role="val")
        top.addWidget(self.kv_core, stretch=2)
        cl.addLayout(top)

        self.trend = CpuTrendChart()
        self.trend.setFixedHeight(CpuTrendChart.HEIGHT)
        cl.addWidget(self.trend)

        # ---- 每线程频率折线（一行一线程；核多时竖直滚动条）----
        self.lbl_threads = QLabel("每线程频率（滚动查看）")
        cl.addWidget(self.lbl_threads)
        self.threads = CpuThreadTrends(n)
        self.threads.set_scale(self._max_mhz)
        cl.addWidget(self.threads, stretch=1)

        self.body.addWidget(self.card_freq, stretch=5)

        self.bars = CpuFreqBars()
        self.bars.setMinimumHeight(150)
        self.body.addWidget(self.bars, stretch=1)
        self.restyle()

    def refresh(self, hw, ctx):
        if hw is None or not hw.cpu_usage:
            self.set_verdict("🟡 等 CPU 采样…", "unknown", "")
            return
        usages = list(hw.cpu_usage)
        freqs = list(hw.cpu_freq or [])
        if freqs:
            mx = max([f for f in freqs if f] or [0.0])
            if mx > self._max_mhz:
                self._max_mhz = mx
        self.threads.set_scale(self._max_mhz)
        self.bars.set_values(freqs, usages, self._max_mhz)

        total = float(getattr(hw, "cpu_total", 0.0) or 0.0)
        cpu0 = float(usages[0]) if usages else 0.0
        others = usages[1:] if len(usages) > 1 else []
        avg_other = (sum(others) / len(others)) if others else 0.0
        top_freq = max([f for f in freqs if f] or [0.0])
        avg_freq = (sum(freqs) / len(freqs)) if freqs else 0.0

        # 每线程折线（同一 seq 只取一个点）
        try:
            self.threads.push(getattr(hw, "seq", None), freqs, usages)
        except Exception:
            pass

        # 频率/功率走势：同一快照 seq 只取一个点（页面刷新比采样快也不会重画重复点）
        try:
            self.trend.push(getattr(hw, "seq", None), avg_freq, top_freq,
                            getattr(hw, "cpu_power", None),
                            bool(getattr(hw, "cpu_power_est", False)))
            cur_f, cur_p, peak_f, est = self.trend.current()
            self.row_freq.set_value(_fmt_mhz(avg_freq), "accent")
            if cur_p is None:
                self.row_power.set_value("不可用", "warn")
            else:
                self.row_power.set_value(("≈%.0f W" if est else "%.0f W") % cur_p, "orange")
            self.row_peak.set_value(_fmt_mhz(peak_f or top_freq))
            self.row_cores.set_value("%d 线程" % len(usages))
            self.row_top.set_value(_fmt_mhz(top_freq))
            self.row_total.set_value("%.0f%%" % total)
        except Exception:
            pass

        if cpu0 >= CPU0_JAM_THRESHOLD and avg_other < CPU0_IDLE_OTHERS:
            self.set_verdict(
                "🔴 C0 被卡死 · C0 %.0f%% / 其余均 %.0f%%" % (cpu0, avg_other),
                "danger",
                "调低【同屏人数 / 视野距离 / 阴影】，或用 Process Lasso 取消勾选 CPU0")
        elif total >= 90.0:
            self.set_verdict(
                "🟠 CPU 整体快满了 · %.0f%% · 均频 %s" % (total, _fmt_mhz(avg_freq)),
                "warn", "关掉后台程序，或把游戏画质降一档")
        elif cpu0 >= 85.0:
            self.set_verdict(
                "🟠 单核偏忙 · C0 %.0f%%（单线程瓶颈）" % cpu0,
                "warn", "调低【同屏人数 / 视野距离 / 物理计算】")
        else:
            self.set_verdict(
                "🟢 调度健康 · 总占用 %.0f%% · 均频 %s" % (total, _fmt_mhz(avg_freq)),
                "ok", "")

    def badge(self, hw, ctx):
        if hw is None or not hw.cpu_usage:
            return ("--% · -- GHz", "unknown")
        freqs = [f for f in (hw.cpu_freq or []) if f]
        return ("%.0f%% · %s" % (float(hw.cpu_total), _fmt_mhz(max(freqs) if freqs else 0)),
                "warn" if self._level in ("warn", "danger") else "ok")

    def restyle(self):
        super().restyle()
        th = T.CURRENT_THEME
        for kv in (getattr(self, "kv", None), getattr(self, "kv_core", None)):
            try:
                kv.restyle(th)
            except Exception:
                pass
        try:
            self.threads.restyle(th)
        except Exception:
            pass
        # 合并卡：整张卡一个底 + 一条细边框；里面的 KV / 折线 / 每线程行都不再各画一层底
        try:
            self.card_freq.setStyleSheet(
                "QFrame#CpuFreqCard { background-color: %s; border: 1px solid %s;"
                " border-radius: 8px; }" % (th.panel2, th.border))
            self.sep.setStyleSheet("background-color: %s; border: none;" % th.border)
        except Exception:
            pass
        self.lbl_threads.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: %s;" % th.fg)
        try:
            self.trend.update()
        except Exception:
            pass


# ==============================================================================
# Page 2：显卡与显存
# ==============================================================================
class GpuPage(_Page):
    """
    Page 2：3D 负载圆环 + KV 指标表 + 显存大横条 + Top5 进程表。

    【v1.0.1 KV 重构】圆环里只留数值，标签以胶囊形式放在圆环**外部**；
    右侧是一张「左标签 / 右数值」的 KV 表，条状图一律不带文字，
    且每个指标只出现一次（不再"条上写一遍、下面再写一遍"）。
    """

    TITLE = "🎮 显卡与显存"

    def __init__(self, parent=None):
        super().__init__(parent)
        top = QHBoxLayout()
        top.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(4)
        self.ring = RingGauge("3D 核心负载")
        self.ring.setMinimumSize(180, 180)
        self.ring.setMaximumWidth(230)
        left.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)
        self.chip_gpu = KeyChip("3D 负载")
        left.addWidget(self.chip_gpu, 0, Qt.AlignmentFlag.AlignHCenter)
        left.addStretch(1)
        top.addLayout(left, stretch=0)

        right = QVBoxLayout()
        right.setSpacing(6)
        # 【v1.0.1 规范 §2】右侧只留两根条，每根条**上方一行文字**（左标题 / 右数据），
        # 条下面不再有任何粗体黑字行；「共享显存」不再单独占一根条
        # （它的数值并进「显存占用」那一行，避免与溢出胶囊文字重叠）。
        self.kv = KVPanel(spacing=3)
        self.row_clock = self.kv.grid.add("核心频率", size=14, role="accent")
        self.bar_clock = CapsuleBar()
        self.bar_clock.setFixedHeight(10)
        self.kv.grid.add_widget(self.bar_clock, spacing_after=8)
        self.row_vram = self.kv.grid.add("显存占用", size=14, role="val")
        self.split = VramSplitBar()
        self.split.setFixedHeight(18)
        self.kv.grid.add_widget(self.split)
        right.addWidget(self.kv)
        right.addStretch(1)
        top.addLayout(right, stretch=1)
        self.body.addLayout(top)

        self.lbl_proc = QLabel("显存 Top 5")
        # 规范 §4：小标题改 13px 深色粗体，并与表格保持 6px 间距
        self.lbl_proc.setStyleSheet(
            "font-size: 13px; font-weight: 600; margin-bottom: 6px;")
        self.body.addWidget(self.lbl_proc)
        self.table = _mk_table(["进程名", "本地显存", "溢出到内存", "专用计数"],
                               widths=[None, 120, 120, 110])
        self.body.addWidget(self.table, stretch=1)
        self.restyle()

    def refresh(self, hw, ctx):
        if hw is None:
            self.set_verdict("🟡 等显卡采样…", "unknown", "")
            return
        eng = getattr(hw, "gpu_eng", {}) or {}
        gpu_pct = gpu_load_pct(hw, ctx)
        # 圆环：只给数值；下方一枚 12px 石板灰标签（不再另写「3D 核心负载」）
        self.ring.set_value(gpu_pct / 100.0, "%.0f%%" % gpu_pct)
        self.chip_gpu.setText("3D 负载" if eng else "忙时比")

        clk = getattr(hw, "gpu_clock", None)
        clk_max = float(getattr(hw, "gpu_clock_max", 0) or 0.0)
        temp = getattr(hw, "gpu_temp", None)
        if clk:
            ratio = (float(clk) / clk_max) if clk_max > 0 else 0.0
            txt = "%.0f MHz" % float(clk)
            if temp:
                txt += " · %.0f ℃" % float(temp)
            txt += (" (峰值 %s)" % _fmt_mhz(clk_max)) if clk_max > 0 else " (峰值待采集)"
            self.row_clock.set_value(txt, "accent")
            self.bar_clock.set_value(max(0.0, min(1.0, ratio)))
        else:
            self.row_clock.set_value("驱动未上报", "warn")
            self.bar_clock.set_value(0.0)

        ded = float(hw.vram_dedicated or 0.0) / 1048576.0
        tot = float(hw.vram_total or 0.0) / 1048576.0
        non = float(hw.vram_nonlocal or 0.0) / 1048576.0
        ratio = (ded / tot) if tot > 1.0 else 0.0
        _text, pct = vram_text(ded, tot, non)
        self.split.set_split(ded, tot, non, self._vram_level(ratio, non, tot))
        if tot > 1.0:
            vtxt = "%.1f GB / %.1f GB (%.0f%%)" % (ded / 1024.0, tot / 1024.0, pct)
            if non > 0:
                vtxt += " · 共享 %s" % _mb(non)
            self.row_vram.set_value(vtxt, "bad" if (ratio >= 0.88 and non >= 256.0) else "val")
        else:
            self.row_vram.set_value("容量未上报", "warn")

        if hw.procs:
            cells = []
            for p in hw.procs[:5]:
                hot = T.CURRENT_THEME.bad if p["nonlocal"] >= SPILL_THRESHOLD else None
                cells.append([
                    (p["name"], None),
                    (human_size(p["local"]), None),
                    (human_size(p["nonlocal"]) if p["nonlocal"] > 0 else "-", hot),
                    (human_size(p["dedicated"]), None),
                ])
            _set_cells(self.table, cells)

        # ---- 小白话术结论（严格按改进建议书 二 的门控，绝不因「非本地有值」就报爆显存）----
        if tot <= 1.0:
            self.set_verdict("🟡 显存容量未上报 · 3D 负载 %.0f%%" % gpu_pct,
                             "unknown", "管理员身份运行通常能读到")
        elif ratio >= 0.88 and non >= 256.0:
            self.set_verdict(
                "🔴 显存不够用了 · 已用 %.0f%% · 共享 %s" % (ratio * 100.0, _mb(non)),
                "danger", "把【贴图/纹理质量】调低一档")
        elif ratio >= 0.90:
            self.set_verdict("🟠 显存快满 · 已用 %.0f%%" % (ratio * 100.0),
                             "warn", "把【贴图质量】降一档")
        else:
            self.set_verdict("🟢 显卡与显存正常 · 3D %.0f%%" % gpu_pct, "ok", "")

    @staticmethod
    def _vram_level(ratio, non_mb, total_mb):
        if total_mb <= 1.0:
            return "unknown"
        if ratio >= 0.88 and non_mb >= 256.0:
            return "danger"
        if ratio >= 0.90:
            return "warn"
        return "ok"

    def badge(self, hw, ctx):
        if hw is None:
            return ("核心 --% · 显存 --%", "unknown")
        tot = float(hw.vram_total or 0.0)
        ratio = (float(hw.vram_dedicated or 0.0) / tot) if tot > 1.0 else 0.0
        pct = gpu_load_pct(hw, ctx)
        lvl = self._vram_level(ratio, float(hw.vram_nonlocal or 0.0) / 1048576.0, tot / 1048576.0)
        return ("核心 %.0f%% · 显存 %.0f%%" % (pct, min(100.0, ratio * 100.0)),
                "warn" if lvl in ("warn", "danger") else ("ok" if tot > 1.0 else "unknown"))

    def restyle(self):
        super().restyle()
        th = T.CURRENT_THEME
        try:
            self.chip_gpu.restyle(th)
            self.kv.restyle(th)
        except Exception:
            pass
        self.lbl_proc.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: %s; margin-bottom: 6px;" % th.fg)


def _mb(mb):
    try:
        v = float(mb)
    except Exception:
        return "—"
    return ("%.1f GB" % (v / 1024.0)) if v >= 1024.0 else ("%.0f MB" % v)


# ==============================================================================
# Page 3：内存与磁盘
# ==============================================================================
class MemPage(_Page):
    """
    Page 3：物理内存圆环 + KV 指标表 + 三条能量柱 + 进程排行。

    【v1.0.1 KV】圆环里只有百分比；「已用 / 换入 / 换出 / 读盘」全是 KV 行，
    数值颜色与该指标的能量柱同色（accent/purple/orange），所以柱子不再需要贴标签。
    """

    TITLE = "💾 内存与磁盘"

    def __init__(self, parent=None):
        super().__init__(parent)
        top = QHBoxLayout()
        top.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(4)
        self.ring = RingGauge("物理内存占用")
        self.ring.setMinimumSize(180, 180)
        self.ring.setMaximumWidth(230)
        left.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)
        self.chip_mem = KeyChip("物理内存")
        left.addWidget(self.chip_mem, 0, Qt.AlignmentFlag.AlignHCenter)
        left.addStretch(1)
        top.addLayout(left, stretch=0)

        right = QVBoxLayout()
        right.setSpacing(8)
        # 每个指标 = 「KV 行 + 一条同色横向能量条」，行与条一一对应（规范 §2 思路）
        self.kv = KVPanel(spacing=3)
        self.row_used = self.kv.grid.add("已用", size=14, role="accent")
        self.row_commit = self.kv.grid.add("提交 / 上限", size=12, role="val")
        self.row_pg_in = self.kv.grid.add("换入", size=12, role="purple")
        # 【v1.0.2 量程修正】下限从 32/64 MB/s 压到 1/4 MB/s：桌面待机换入常年在
        # 0.1~0.5 MB/s，原来只填满 0.6% 宽度 —— 屏幕上就是一个 2px 的小点，
        # 看着像控件坏了（用户截图即如此）。量程本就是相对的，下限要贴近实际量级。
        self.bar_pg_in = MiniEnergyBar("换入", "MB/s", 1.0, "purple")
        self.kv.grid.add_widget(self.bar_pg_in, spacing_after=5)
        self.row_pg_out = self.kv.grid.add("换出", size=12, role="orange")
        self.bar_pg_out = MiniEnergyBar("换出", "MB/s", 1.0, "orange")
        self.kv.grid.add_widget(self.bar_pg_out, spacing_after=5)
        self.row_io = self.kv.grid.add("读盘", size=12, role="accent")
        self.bar_io = MiniEnergyBar("读盘", "MB/s", 4.0, "accent")
        self.kv.grid.add_widget(self.bar_io)
        right.addWidget(self.kv)
        right.addStretch(1)
        top.addLayout(right, stretch=1)
        self.body.addLayout(top)

        self.lbl_pagein = KeyChip("换页 Top")
        self.lbl_pagein.setToolTip(
            "进程缺页速率（PSAPI PageFaultCount 差值 × 4KB，含软缺页）。\n"
            "与上方 PDH「换入」不是同一口径：这里的 MB/s 会明显偏大。\n"
            "硬换入 > %.0f MB/s 时才做进程遍历（省 CPU）。" % PAGEIN_TOP_GATE_MB)
        self.body.addWidget(self.lbl_pagein, 0, Qt.AlignmentFlag.AlignLeft)
        self.table_page = _mk_table(["进程名", "缺页 MB/s"], widths=[None, 140])
        self.table_page.setMaximumHeight(150)
        self.body.addWidget(self.table_page)

        self.lbl_io = KeyChip("读盘 Top")
        self.body.addWidget(self.lbl_io, 0, Qt.AlignmentFlag.AlignLeft)
        self.table_io = _mk_table(["进程名", "读取 MB/s"], widths=[None, 140])
        self.body.addWidget(self.table_io, stretch=1)
        self.restyle()

    def refresh(self, hw, ctx):
        if hw is None:
            self.set_verdict("🟡 等内存采样…", "unknown", "")
            return
        commit = float(getattr(hw, "mem_commit", 0.0) or 0.0) / 1048576.0
        limit = float(getattr(hw, "mem_limit", 0.0) or 0.0) / 1048576.0
        avail = float(getattr(hw, "mem_avail_mb", 0.0) or 0.0)
        ratio = max(0.0, min(1.0, commit / limit)) if limit > 1.0 else (
            (commit / (commit + avail)) if (commit + avail) > 0 else 0.0)
        # 圆环只放数值；「已用」交给下面的 KV 行
        self.ring.set_value(ratio, "%.0f%%" % (ratio * 100.0))
        self.row_used.set_value("%.1f GB" % (commit / 1024.0) if commit > 0 else "—")
        self.row_commit.set_value(
            ("%.1f / %.1f GB" % (commit / 1024.0, limit / 1024.0)) if limit > 1.0 else "—")

        io_read = float(getattr(hw, "io_read_mb", 0.0) or 0.0)
        page_in = float(getattr(hw, "page_in", 0.0) or 0.0)
        page_out = float(getattr(hw, "page_out", 0.0) or 0.0)
        self.bar_io.set_value(io_read, vmax=max(4.0, io_read * 1.6), warn=io_read > 50.0)
        self.bar_pg_in.set_value(page_in, vmax=max(1.0, page_in * 1.6), warn=page_in > 8.0)
        self.bar_pg_out.set_value(page_out, vmax=max(1.0, page_out * 1.6), warn=page_out > 8.0)
        self.row_io.set_value("%.1f MB/s" % io_read, "warn" if io_read > 50.0 else "accent")
        self.row_pg_in.set_value("%.1f MB/s" % page_in, "warn" if page_in > 8.0 else "purple")
        self.row_pg_out.set_value("%.1f MB/s" % page_out, "warn" if page_out > 8.0 else "orange")

        # 【v1.0.2】空表必须写清"**为什么**空"，否则和"采集坏了"长得一模一样。
        # 换入 < 2 MB/s 时 HardwareEngine 刻意跳过 1200 进程遍历（省 CPU），
        # pagein_top 本来就是空的；首次触发的那一轮也还没有差值基线（全 0）。
        th = T.CURRENT_THEME
        tops = [(n, m) for n, m in (getattr(hw, "pagein_top", []) or []) if m > 0][:6]
        if tops:
            _set_cells(self.table_page,
                       [[(n, None), ("%.1f" % m, None)] for n, m in tops])
        elif page_in > PAGEIN_TOP_GATE_MB:
            _set_cells(self.table_page, [[
                ("换入 %.1f MB/s · 进程级归因采样中（首轮无差值基线）" % page_in, th.dim),
                ("", None)]])
        else:
            _set_cells(self.table_page, [[
                ("换入 %.1f MB/s < %.0f MB/s · 未做进程级归因（省 CPU；换入升高会自动列出）"
                 % (page_in, PAGEIN_TOP_GATE_MB), th.dim),
                ("", None)]])
        ios = list(getattr(hw, "io_top", []) or [])[:8]
        _set_cells(self.table_io,
                   [[(n, None), ("%.1f" % v, T.CURRENT_THEME.bad if v > 50.0 else None)]
                    for n, v in ios])

        if page_in > 8.0:
            self.set_verdict(
                "🟠 内存不够 · 系统在读盘顶（换入 %.1f MB/s）" % page_in,
                "warn", "关掉高占内存的后台程序，或加内存")
        elif io_read > 50.0:
            self.set_verdict(
                "🟠 硬盘狂读 · %.0f MB/s" % io_read,
                "warn", "游戏放 SSD，避免同时下载/解压")
        elif ratio >= 0.90:
            self.set_verdict("🟠 物理内存快满 · %.0f%%" % (ratio * 100.0),
                             "warn", "关掉浏览器/录屏")
        else:
            self.set_verdict(
                "🟢 内存与磁盘健康 · %.0f%% · 读盘 %.1f · 换入 %.1f MB/s"
                % (ratio * 100.0, io_read, page_in),
                "ok", "")

    def badge(self, hw, ctx):
        if hw is None:
            return ("内存 --% · 读盘 -- MB/s", "unknown")
        commit = float(getattr(hw, "mem_commit", 0.0) or 0.0) / 1048576.0
        limit = float(getattr(hw, "mem_limit", 0.0) or 0.0) / 1048576.0
        avail = float(getattr(hw, "mem_avail_mb", 0.0) or 0.0)
        ratio = (commit / limit) if limit > 1.0 else (
            (commit / (commit + avail)) if (commit + avail) > 0 else 0.0)
        io_read = float(getattr(hw, "io_read_mb", 0.0) or 0.0)
        page_in = float(getattr(hw, "page_in", 0.0) or 0.0)
        lvl = "warn" if (page_in > 8.0 or io_read > 50.0 or ratio >= 0.90) else "ok"
        return ("内存 %.0f%% · 读盘 %.1f MB/s" % (ratio * 100.0, io_read), lvl)

    def restyle(self):
        super().restyle()
        th = T.CURRENT_THEME
        for w in (self.chip_mem, self.chip_gpu if hasattr(self, "chip_gpu") else self.chip_mem,
                  self.lbl_pagein, self.lbl_io):
            try:
                w.restyle(th)
            except Exception:
                pass
        try:
            self.kv.restyle(th)
        except Exception:
            pass


# ==============================================================================
# Page 4：网络与环境
# ==============================================================================
class NetPage(_Page):
    """
    Page 4：上下行吞吐条 + KV 指标表 + 着色器缓存表。

    【v1.0.1 KV】两条速率条只画轨道；「上传 / 下载」的标签与数值在 KV 行里，
    数值颜色与对应轨道一致（紫 = 上传，青 = 下载）。
    """

    TITLE = "🌐 网络与环境"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bars = NetRateBars()
        self.bars.setMinimumHeight(56)
        self.bars.setMaximumHeight(80)
        self.bars.up_max = 4096.0
        self.bars.down_max = 16384.0
        self.body.addWidget(self.bars)

        kvrow = QHBoxLayout()
        kvrow.setSpacing(18)
        self.kv = KVPanel()
        self.row_down = self.kv.grid.add("下载", size=15, role="accent")
        self.row_up = self.kv.grid.add("上传", size=12, role="purple")
        kvrow.addWidget(self.kv, stretch=1)

        status = KVPanel()
        self.row_shader = status.grid.add("着色器", size=13, role="green")
        self.row_cache = status.grid.add("缓存新增", size=12, role="val")
        kvrow.addWidget(status, stretch=1)
        self.body.addLayout(kvrow)

        self.dot = StatusDot()
        self.lbl_dot_cap = KeyChip("着色器状态")
        dotrow = QHBoxLayout()
        dotrow.setSpacing(6)
        dotrow.addWidget(self.lbl_dot_cap)
        dotrow.addWidget(self.dot)
        dotrow.addStretch()
        self.lbl_dot = ElidedLabel("就绪")
        self.lbl_dot.setStyleSheet("font-size: 11px;")
        dotrow.addWidget(self.lbl_dot)
        self.body.addLayout(dotrow)

        self.lbl_cache = KeyChip("着色器缓存目录")
        self.body.addWidget(self.lbl_cache, 0, Qt.AlignmentFlag.AlignLeft)
        self.table = _mk_table(["缓存目录", "文件总数", "总大小", "新增写入(本轮)"],
                               widths=[None, 110, 110, 150])
        self.body.addWidget(self.table, stretch=1)
        self.restyle()

    def refresh(self, hw, ctx):
        if hw is None:
            self.set_verdict("🟡 等网络采样…", "unknown", "")
            return
        rx = float(getattr(hw, "net_rx_mb", 0.0) or 0.0) * 1024.0
        tx = float(getattr(hw, "net_tx_mb", 0.0) or 0.0) * 1024.0
        self.bars.set_rates(tx, rx)
        self.row_down.set_value(_fmt_rate(rx), "accent")
        self.row_up.set_value(_fmt_rate(tx), "purple")
        new_files = int(getattr(hw, "cache_new_files", 0) or 0)
        new_mb = float(getattr(hw, "cache_new_mb", 0.0) or 0.0)
        compiling = new_files >= max(1, int(CACHE_BURST_FILES))
        self.dot.set_active(compiling)
        th = T.CURRENT_THEME
        self.row_shader.set_value("编译中" if compiling else "就绪",
                                  "warn" if compiling else "green")
        self.row_cache.set_value("+%d 文件 / %.1f MB" % (new_files, new_mb))
        self.lbl_dot.setText(("+%d 文件编译中" % new_files) if compiling else "就绪")
        self.lbl_dot.setStyleSheet(
            "font-size: 11px; color: %s;" % (th.warn if compiling else th.dim))

        cache = getattr(hw, "cache", {}) or {}
        cells = []
        for k in [k for k in cache.keys() if not str(k).startswith("_")]:
            d = cache[k]
            burst = th.warn if d.get("new_files", 0) >= CACHE_BURST_FILES else None
            cells.append([
                (k, None),
                (str(d.get("files", 0)), None),
                ("%.1f MB" % float(d.get("mb", 0.0)), None),
                ("+%d (%.1f MB)" % (d.get("new_files", 0), d.get("new_mb", 0.0))
                 if d.get("new_files") else "-", burst),
            ])
        _set_cells(self.table, cells)

        if compiling:
            self.set_verdict(
                "🟡 首次编译着色器 · 本轮 +%d 文件" % new_files,
                "warn", "跑完一遍地图后自动变流畅")
        elif rx <= 0.0 and tx <= 0.0:
            self.set_verdict("🟡 网络空闲 · 着色器就绪", "unknown", "")
        else:
            self.set_verdict(
                "🟢 网络稳定 · ↓ %s · ↑ %s" % (_fmt_rate(rx), _fmt_rate(tx)),
                "ok", "")

    def badge(self, hw, ctx):
        if hw is None:
            return ("↓ -- KB/s", "unknown")
        rx = float(getattr(hw, "net_rx_mb", 0.0) or 0.0) * 1024.0
        new_files = int(getattr(hw, "cache_new_files", 0) or 0)
        if new_files >= max(1, int(CACHE_BURST_FILES)):
            return ("↓ %s · 编译" % _fmt_rate(rx), "warn")
        return ("↓ %s" % _fmt_rate(rx), "ok")

    def restyle(self):
        super().restyle()
        th = T.CURRENT_THEME
        for w in (self.lbl_dot_cap, self.lbl_cache):
            try:
                w.restyle(th)
            except Exception:
                pass
        try:
            self.kv.restyle(th)
        except Exception:
            pass
        self.lbl_dot.setStyleSheet("font-size: 11px; color: %s;" % th.dim)


def build_pages():
    """按改进建议书顺序创建 5 个页面（0 号「帧率总览」默认选中）"""
    return [FpsOverviewPage(), CpuPage(), GpuPage(), MemPage(), NetPage()]
