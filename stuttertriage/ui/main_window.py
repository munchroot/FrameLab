# -*- coding: utf-8 -*-
"""
StutterTriage 主窗口（v4.1 · 「视图导航卡 + QStackedWidget」布局重构）。

只做「版面 + 每帧刷新口径 + 新可视化」三件事，**底层一行不改**：
  · 采集线程仍是 stuttertriage.engines.hardware.HardwareEngine /
    stuttertriage.engines.presentmon.PresentMonBridge；
  · 11 维归因仍是 stuttertriage.analysis.SnapshotDiffInspector（原封不动）；
  · 设置页 / CSV 导入导出 / ETW 清理 / 提权提醒 / 热键 等既有行为
    继承自 stuttertriage.ui.base_window.StutterTriageWindowBase。

改进建议书（gemini-code-1793074763581）的落点：
  一、布局 → 左栏固定 240px 只放 5 张大号导航卡（navtabs.py），
      右侧主区域上半（顶部横幅 + 4 张帧数大卡）常驻，
      下半改成 QStackedWidget 整块替换（pages.py 的 5 个全屏页面）。
  二、显存 → 只有「专用显存 ≥88% 且非本地 ≥256MB 且较基线 +64MB」才报爆显存。
  三、降频 → 基线 ≥1200MHz 且 3D 负载 ≥60% 且跌幅 >20% 且真掉帧才算撞墙降频。
  四、坐标轴 → 由 chart.FpsChart 内部锁死（空载 0~100 / FPS 0~144 / ms 0~35）。
  五、顿挫 → 以「最近 120 帧局部中位数」为基准的双重门控（stats.stutter_flags），
      85FPS 以上一律豁免；没触发时病历卡隐藏或显示绿色「全程运行丝滑」。
"""
import time

import numpy as np

from PyQt6.QtGui import QAction
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QProgressBar,
                             QPushButton, QStackedWidget, QTabWidget, QToolButton,
                             QVBoxLayout, QWidget)

from .. import theme as T
from ..analysis import SnapshotDiffInspector
from ..config import Config
from ..consts import (DATA_SOURCE_LABEL, DATA_SOURCE_REAL, DATA_SOURCE_SIM,
                      datasource_color)
from ..stats import bottleneck, dropped_verdict, fps_verdict, window_stats
from ..theme import (CURRENT_THEME, THEME_PRESETS, attach_shadow,
                     bind_globals as _bind_theme_globals)
from ..utils import DIAG
from ..winadmin import detect_data_source, make_sim_watermark
from .base_window import StutterTriageWindowBase
from .chart import MODE_FPS, MODE_MS
from .diagnosis import ProfessionalTableDialog
from .gauge import BottleneckBar, FpsCard, GradeBadge
from .navtabs import build_nav_tabs
from .pages import build_pages, gpu_load_pct
from .plain import GRADE_COLOR, explain
from .widgets import ElidedLabel
from .kv import KVGrid

try:                                    # 新算法统一入口（缺失时优雅降级为「不判定顿挫」）
    from ..stats import is_stutter_frame
except Exception:                       # pragma: no cover
    is_stutter_frame = None

# 本模块持有主题色可变副本（主题热切换时随 set_theme 刷新）
_bind_theme_globals(globals())


class StutterTriageWindow(StutterTriageWindowBase):
    """左栏 5 张导航卡 + 右侧 QStackedWidget 五页主画布"""

    FRAME_POLL = 6                   # 重活节拍（≈200ms）
    PAGE_POLL = 3                    # 页面/导航徽章刷新节拍（≈100ms）
    BADGE_POLL = 6                   # 左栏徽章节拍（≈200ms）
    BANNER_HEIGHT = 96               # 顶部横幅固定高度（版面零抖动的核心常量）

    # ================= 【v1.0.0 修「自己卡住」】诊断重算节流 =================
    # SnapshotDiffInspector.inspect() 末尾的会话级归因（session_correlations）
    # 是 O(全部帧 × 卡顿帧)，且缓存键包含帧数 —— 实时采集时帧数每 tick 都变，
    # 缓存永远不命中。旧实现里它被三条路径以 30Hz 反复触发：
    #   ① 顿挫期间每 tick 重算横幅结论；
    #   ② 自动弹卡每 tick 触发一次全量 inspect；
    #   ③ 病历卡弹出后 selected_frame 一直锁定 → 每 tick 跟随刷新再算一次。
    # 会话越长（最多 10 万帧）、卡顿帧越多，单次耗时从几 ms 涨到几百 ms，
    # 33ms 的 UI 定时器被彻底淹没 —— 用户看到的就是「StutterTriage 自己卡住」。
    # 处理：横幅结论 ≥0.5s 一次、自动弹卡 ≥1s 一次、锁定卡跟随 ≥1s 一次
    # （1Hz 的数字刷新肉眼无感，归因算法本身一行未动）。
    DIAG_RENDER_INTERVAL = 0.5       # 顿挫横幅全量重算最小间隔（秒）
    DIAG_FOLLOW_INTERVAL = 1.0       # 锁定帧病历卡跟随刷新最小间隔（秒）
    AUTO_POPUP_INTERVAL = 1.0        # 自动弹病历卡最小间隔（秒）

    # ================================================================
    # 版面
    # ================================================================
    def _init_ui(self):
        self.setWindowTitle("StutterTriage 1.0.0 · 游戏卡顿一站式体检台")
        self._apply_global_stylesheet()
        self._stat_cache = None
        self._last_rows = []
        self._pro_dialog = None
        self._diag_prev_frame = None
        self._auto_diag_idx = None        # 已自动弹过病历卡的顿挫帧（防闪烁）
        self._diag_strict = None          # 病历卡的顿挫判定（选中那一刻锁死，不随后续刷新翻转）
        self._fps_mode = MODE_FPS
        self._diag_render_ts = 0.0        # 上次全量诊断渲染时刻（节流用）
        self._auto_popup_ts = 0.0         # 上次自动弹卡时刻（节流用）

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        self._build_sidebar()
        root.addWidget(self.sidebar, stretch=0)
        attach_shadow(self.sidebar)

        self._build_main_area(root)
        self._build_hw_panel()
        self._build_gear_menu()
        self._apply_global_stylesheet()

    # ---------------------------------------------------------------- 左栏
    def _build_sidebar(self):
        """左栏固定 240px：只放 5 张大号导航卡，不再在栏内展开抽屉/画图形。"""
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(240)
        sb = QVBoxLayout(self.sidebar)
        sb.setContentsMargins(12, 10, 12, 10)
        sb.setSpacing(8)

        # ---- 顶部：LOGO + 极小齿轮（极客入口全收进这里）----
        top = QHBoxLayout()
        top.setSpacing(6)
        self.lbl_logo = QLabel("◈ StutterTriage")
        self.lbl_logo.setStyleSheet(
            "font-size: 17px; font-weight: bold; color: %s; letter-spacing: 1px;"
            % CURRENT_THEME.accent)
        top.addWidget(self.lbl_logo)
        top.addStretch()
        self.btn_gear = QToolButton()
        self.btn_gear.setObjectName("GearButton")
        self.btn_gear.setText("⚙")
        self.btn_gear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_gear.setToolTip("高级工具（CSV 导入/导出、清理 ETW、打点、硬件面板、设置…）")
        self.btn_gear.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        top.addWidget(self.btn_gear)
        sb.addLayout(top)

        self.lbl_engine_badge = ElidedLabel("● 正在探测引擎…")
        self.lbl_engine_badge.setStyleSheet(
            "color: %s; font-size: 10px; font-weight: bold;" % CURRENT_THEME.warn)
        sb.addWidget(self.lbl_engine_badge)
        sb.addWidget(self._hdivider())

        # ---- 选择游戏 + 大号开始/暂停 ----
        # 【v1.0.0 文案瘦身】原来的「选择游戏」灰字标题已删：下拉框本身就是选择器，
        # 只剩 2 个字反而更乱。这里直接贴一个极短标签「游戏」。
        self.lbl_target_caption = QLabel("游戏")
        self.lbl_target_caption.setStyleSheet(
            "color: %s; font-size: 11px; font-weight: bold;" % CURRENT_THEME.dim)
        sb.addWidget(self.lbl_target_caption)
        self.combo_procs = QComboBox()
        self.combo_procs.addItem("自动锁定主游戏")
        self.combo_procs.currentTextChanged.connect(self._on_target_changed)
        sb.addWidget(self.combo_procs)

        self.btn_pause = QPushButton("▶ 开始采集")
        self.btn_pause.setObjectName("NavPrimary")
        self.btn_pause.setMinimumHeight(40)
        self.btn_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pause.clicked.connect(self._toggle_pause)
        sb.addWidget(self.btn_pause)

        # 【清空重采】把已采到的曲线全部丢掉，从当前时刻重新开始
        self.btn_resample = QPushButton("🧹 清空重采")
        self.btn_resample.setObjectName("NavButton")
        self.btn_resample.setMinimumHeight(32)
        self.btn_resample.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_resample.setToolTip("清空已采到的帧/曲线数据，并从当前时刻重新开始采样")
        self.btn_resample.clicked.connect(self._on_resample_curves)
        sb.addWidget(self.btn_resample)
        sb.addWidget(self._hdivider())

        # ---- 5 张大号视图导航卡（改进建议书 一.1）----
        # 【v1.0.0 文案瘦身】删掉「视图导航」灰字标题：5 张带图标+徽章的导航卡
        # 本身就说明了一切，多一行字只是噪音。
        self.nav_tabs = build_nav_tabs()
        for t in self.nav_tabs:
            t.clicked.connect(self._on_nav_clicked)
            sb.addWidget(t)
        sb.addStretch()

        # ---- 底部状态区（保持既有属性名，行为零改动）----
        sb.addWidget(self._hdivider())
        self.lbl_data_source = QLabel(DATA_SOURCE_LABEL[DATA_SOURCE_REAL])
        self.lbl_data_source.setStyleSheet(
            "color: %s; font-size: 10px; font-weight: bold;" % CURRENT_THEME.green)
        sb.addWidget(self.lbl_data_source)

        self.btn_mark = QPushButton("★ 打点标记")
        self.btn_mark.setObjectName("NavButton")
        self.btn_mark.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mark.setToolTip("在当前时刻打一个手动标记（等同全局热键）")
        self.btn_mark.clicked.connect(self._on_manual_mark)
        sb.addWidget(self.btn_mark)

        self.lbl_hotkey = ElidedLabel("")
        self.lbl_hotkey.setStyleSheet("color: %s; font-size: 10px;" % CURRENT_THEME.dim)
        sb.addWidget(self.lbl_hotkey)

        self.lbl_pm_hint = ElidedLabel("")
        self.lbl_pm_hint.setStyleSheet("color: %s; font-size: 10px;" % CURRENT_THEME.warn)
        sb.addWidget(self.lbl_pm_hint)

        self.progress_import = QProgressBar()
        self.progress_import.setVisible(False)
        sb.addWidget(self.progress_import)

    # ---------------------------------------------------------------- 主区
    def _build_main_area(self, root):
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(10)
        root.addWidget(main, stretch=1)

        # 顶部状态横幅 + 4 张核心帧数大卡：**常驻**，切页不消失
        ml.addWidget(self._build_banner())
        ml.addLayout(self._build_fps_cards())

        # 下半区：QStackedWidget —— 点击导航卡整块替换
        self.pages = build_pages()
        self.stack = QStackedWidget()
        self.stack.setObjectName("MainStack")
        for p in self.pages:
            self.stack.addWidget(p)
        self.stack.setCurrentIndex(0)
        ml.addWidget(self.stack, stretch=1)
        self.nav_tabs[0].set_active(True)
        self._wire_page0()

    def _wire_page0(self):
        """Page 0（帧率总览）内部信号接线 + 兼容基类所需的旧属性名。"""
        page = self.pages[0]
        self.fps_chart = page.fps_chart
        self.plot_widget = self.fps_chart.plot_widget     # 兼容基类（主题/水印/菜单汉化）
        self.locked_line = self.fps_chart.locked_line
        self.diag_card = page.diag_card
        self.diff_drawer = self.diag_card                  # 兼容基类（hide/isVisible）
        self.btn_fps_mode = page.btn_fps_mode
        self.btn_jump_drop = page.btn_jump_drop

        self.fps_chart.frameClicked.connect(self._on_chart_frame_clicked)
        self.btn_fps_mode.clicked.connect(self._toggle_fps_mode)
        self.btn_jump_drop.clicked.connect(self._jump_to_last_drop)
        self.diag_card.detailRequested.connect(self._open_pro_dialog)
        self.diag_card.closed.connect(self._on_diag_closed)

        # 模拟数据水印（既有功能：防把降级模拟帧误当真实采集）
        self._sim_watermark = make_sim_watermark()
        if self._sim_watermark is not None:
            self.plot_widget.addItem(self._sim_watermark, ignoreBounds=True)

        self._localize_pg_menus()

    def _build_banner(self):
        """
        顶部横幅：段位徽章 + 大白话结论 + 瓶颈天平（改进建议书 四.1 / 四.2）。

        【v1.0.0 版面零抖动】横幅高度**锁死** BANNER_HEIGHT，内部所有文案一律
        走 ElidedLabel（单行 + 省略号 + tooltip），不再 wordWrap。
        以前这里是 wrap 标签：数据一到文案从 1 行变 2 行，横幅从 92px 长到 100px，
        下面 4 张卡被顶着上下跳 —— 这就是用户看到的「卡牌会动」。
        """
        self.banner_frame = QFrame()
        self.banner_frame.setObjectName("BannerCard")
        self.banner_frame.setFixedHeight(self.BANNER_HEIGHT)
        bl = QHBoxLayout(self.banner_frame)
        bl.setContentsMargins(12, 8, 14, 8)
        bl.setSpacing(14)

        self.badge = GradeBadge()
        bl.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)

        mid = QVBoxLayout()
        mid.setSpacing(3)
        mid.addStretch()
        self.banner_text = ElidedLabel("🟢 正在启动采集引擎…")
        self.banner_text.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: %s;" % CURRENT_THEME.fg)
        mid.addWidget(self.banner_text)
        mid.addStretch()
        bl.addLayout(mid, stretch=1)

        bn = QVBoxLayout()
        bn.setSpacing(3)
        bn.addStretch()
        # 【v1.0.1 规范 §3】瓶颈区压缩成 3 行：① 数值行（CPU 左 / 显卡 右）
        # ② 天平条 ③ 一句结论。原来第 4 行「显卡 0% · CPU 6%（都没满载）」与
        # 数值行完全重复，已删除；建议文字改为 tooltip，不再多占一行。
        numrow = QHBoxLayout()
        numrow.setSpacing(8)
        self.lbl_cpu_num = QLabel("CPU --%")
        self.lbl_gpu_num = QLabel("显卡 --%")
        for w in (self.lbl_cpu_num, self.lbl_gpu_num):
            w.setStyleSheet("font-size: 11px; font-weight: 600;")
        numrow.addWidget(self.lbl_cpu_num)
        numrow.addStretch(1)
        numrow.addWidget(self.lbl_gpu_num)
        bn.addLayout(numrow)
        self.bn_bar = BottleneckBar()
        self.bn_bar.setMinimumWidth(220)
        self.bn_bar.setMaximumWidth(340)
        bn.addWidget(self.bn_bar)
        self.lbl_bottleneck = ElidedLabel("等数据…")
        self.lbl_bottleneck.setStyleSheet(
            "font-size: 11px; font-weight: 600; color: %s;" % CURRENT_THEME.fg)
        self.lbl_bn_advice = ElidedLabel("")      # 兼容旧属性名：不再占版面，只进 tooltip
        self.lbl_bn_advice.setVisible(False)
        bn.addWidget(self.lbl_bottleneck)
        bn.addWidget(self.lbl_bn_advice)
        bn.addStretch()
        bl.addLayout(bn)
        attach_shadow(self.banner_frame)
        return self.banner_frame

    def _build_fps_cards(self):
        """四大帧数指标卡（改进建议书 二）—— 高度锁定，文案长短不再改变版面"""
        row = QHBoxLayout()
        row.setSpacing(10)
        self.card_avg = FpsCard("平均帧", sub_label="评级", role="accent")
        self.card_max = FpsCard("最高帧", sub_label="尖峰", role="green")
        self.card_min = FpsCard("1% Low", sub_label="0.1% Low", role="warn")
        self.card_drop = FpsCard("顿挫", sub_label="占比", role="green")
        cards = (self.card_avg, self.card_max, self.card_min, self.card_drop)
        # 【v1.0.0】用「造好时最高的那张卡」的自然高度当统一锁死高度：
        # 之后数字从 -- 变成 54、副标题从空变成「窗口 299 帧」都不会再改高度。
        lock_h = max([84] + [c.sizeHint().height() for c in cards])
        for c in cards:
            c.setFixedHeight(lock_h)
            row.addWidget(c, stretch=1)
            attach_shadow(c)
        return row

    # ---------------------------------------------------------------- 设置 / 事件流面板
    def _build_hw_panel(self):
        """
        设置与事件流面板（独立非模态窗口）。

        【v1.0.0 清理「设置按钮里的老东西」】删掉 v3.2 时代的 5 个硬件页签
        （CPU 热力图 / 显存监视 / 内存换入 / 着色器与引擎 / 事件流）：
          · CPU 热力图 —— v1.0.1 已按用户要求从主界面删除方块热力图；
          · 显存监视 / 内存换入 / 着色器与引擎 —— 与主界面 5 个导航页
            （CPU / 显卡与显存 / 内存与磁盘 / 网络与环境）信息完全重复，
            而且它们的表格刷新调用了一个在模块拆分时**丢失的方法**
            （`_sync_table`），打开面板后每 tick 抛一次 AttributeError。
        只保留真正必要、且别处没有的两块：
          · ⚙ 设置（主题 / 阈值 / 热键 / PresentMon 路径）；
          · ⚡ 事件流（引擎告警 + 手动打点的唯一历史视图）。
        """
        self.hw_panel = QWidget(self, Qt.WindowType.Window)
        self.hw_panel.setObjectName("HwPanel")
        self.hw_panel.setWindowTitle("StutterTriage · 设置与事件流")
        self.hw_panel.resize(820, 560)
        self.hw_panel.setMinimumSize(640, 420)
        hpl = QVBoxLayout(self.hw_panel)
        hpl.setContentsMargins(10, 10, 10, 10)
        self.hw_tabs = QTabWidget()
        self.tab_settings = self._build_settings_tab()
        self.hw_tabs.addTab(self.tab_settings, "⚙ 设置")
        self.tab_events = self._build_events_tab()
        self.hw_tabs.addTab(self.tab_events, "⚡ 事件流")
        hpl.addWidget(self.hw_tabs)
        self.hw_panel.hide()

    # ---------------------------------------------------------------- 齿轮菜单
    def _build_gear_menu(self):
        """
        齿轮菜单（⚙）：只保留真正有用的入口。

        【v1.0.0 清理】删掉两条老东西：
          · 🚀 展开进阶硬件面板 —— 新版面已把硬件信息全部搬到 5 个导航页；
          · 📁 打开 PresentMon 目录 —— 设置页里已有「打开目录」按钮，
            且原来这里还顺手 new 了一个从未加进菜单的 QPushButton（死控件）。
        """
        menu = QMenu(self)
        menu.setObjectName("GearMenu")

        self.act_settings = QAction("⚙ 设置（主题 / 阈值 / 热键 / PresentMon）", self)
        self.act_settings.triggered.connect(self._on_open_settings_panel)
        menu.addAction(self.act_settings)

        self.act_events = QAction("⚡ 事件流（告警与打点历史）", self)
        self.act_events.triggered.connect(self._on_open_events_panel)
        menu.addAction(self.act_events)

        menu.addSeparator()
        self.act_import = QAction("📂 导入 CSV 回放", self)
        self.act_import.triggered.connect(self._import_csv)
        menu.addAction(self.act_import)

        self.act_export = QAction("💾 导出本次采集 CSV", self)
        self.act_export.triggered.connect(self._export_csv)
        menu.addAction(self.act_export)

        self.act_sim = QAction("🎲 切到模拟数据流", self)
        self.act_sim.triggered.connect(self._toggle_simulation)
        menu.addAction(self.act_sim)

        self.act_clean = QAction("🧹 清理残留 ETW 会话", self)
        self.act_clean.triggered.connect(self._clean_etw_confirmed)
        menu.addAction(self.act_clean)

        menu.addSeparator()
        theme_menu = menu.addMenu("🎨 快速换肤")
        for key, th in THEME_PRESETS.items():
            act = QAction(th.name, self)
            act.triggered.connect(lambda _c=False, k=key: self._on_theme_changed(k))
            theme_menu.addAction(act)
        act_custom = QAction("自定义背景色…", self)
        act_custom.triggered.connect(lambda: self._on_theme_changed("custom"))
        theme_menu.addAction(act_custom)

        self.gear_menu = menu
        self.btn_gear.setMenu(menu)

    # ================================================================
    # 页面切换 / 导航徽章
    # ================================================================
    def _on_nav_clicked(self, index):
        """点击左栏导航卡 → 右侧主画布整块替换（改进建议书 一.2）。"""
        index = int(index)
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)
        for t in self.nav_tabs:
            t.set_active(t.index == index)
        if index > 0:
            # 立刻刷一次，避免切过去先看到上一轮的陈旧数字
            hw = self.store.snapshots[-1] if self.store.snapshots else None
            try:
                self.pages[index].refresh(hw, self._page_ctx(hw))
            except Exception as ex:
                DIAG.report("_on_nav_clicked.refresh", ex)

    def _on_resample_curves(self):
        """
        🧹 清空重采：丢掉已采到的曲线数据，并从此刻重新采样。

        只清「本次会话」的数据（帧 / 硬件快照 / 事件 / 基线），
        不碰阈值、配置、目标进程与窗口状态；清完立刻进入采集，
        所以「暂停中」点它 = 清空并继续。
        """
        try:
            self.store.reset()
        except Exception as ex:
            DIAG.report("_on_resample_curves.reset", ex)
        self._discard_pending()
        # 渲染侧缓存必须一并作废：否则清空后的第一帧仍会沿用旧的统计/结论
        self._stat_cache = None
        self._last_rows = []
        self._diag_prev_frame = None
        self._auto_diag_idx = None
        self._diag_render_ts = 0.0
        self._auto_popup_ts = 0.0
        self.selected_frame = None
        try:
            self.locked_line.setVisible(False)
        except Exception:
            pass
        try:
            self.fps_chart.clear()
        except Exception:
            pass
        try:
            self._reset_cards()
        except Exception:
            pass
        self._reset_diagnosis_card()
        # 曲线已归零 → 直接开始/继续采集（未开始 / 暂停 都拉起来）
        self._begin_collection()
        self._pending_status = (True, "曲线已清空，重新采样中")

    def _page_ctx(self, hw, frames=None):
        """
        页面刷新上下文（PresentMon 忙时比 / 平均帧 / 三档评价 / 顿挫状态）。

        【v1.0.0 性能】frames 可由调用方传入（主刷新循环已经取过一次
        `visible_frames`）—— 否则这里会**再复制一份**最多 10 万帧的列表，
        每 tick 白花几毫秒（会话越长越贵）。
        """
        ctx = {}
        try:
            if frames is None:
                frames = self.store.visible_frames(self._eff_target())
            recent = frames[-min(len(frames), 600):] if frames else []
            if recent:
                ctx["gpu_pct"] = float(np.mean([f.gpu_busy_ratio for f in recent])) * 100.0
        except Exception:
            pass
        st = self._stat_cache or {}
        avg = float(st.get("avg_fps") or 0.0)
        ctx["avg_fps"] = avg
        ctx["verdict"] = fps_verdict(avg) if avg > 0 else "等待数据"
        ctx["grade"] = st.get("grade", "-")
        ctx["dropped"] = st.get("dropped", 0)
        ctx["stutter_now"] = bool(st.get("stutter_now"))
        return ctx

    def _refresh_nav_badges(self, hw, ctx):
        """把 5 张导航卡的「一行极简实时状态徽章」刷成最新（改进建议书 一.1）。"""
        for tab, page in zip(self.nav_tabs, self.pages):
            try:
                text, level = page.badge(hw, ctx)
            except Exception:
                text, level = ("等待数据", "unknown")
            tab.set_badge(text, level)

    # ================================================================
    # 每帧刷新
    # ================================================================
    def _refresh_ui_inner(self):
        started = getattr(self, "_collect_started", True)
        if started:
            self._drain_queue()
        else:
            # 【手动开始采集】没点「▶ 开始采集」前不采集：帧/告警直接丢掉
            # （避免点开始时把启动期间的历史灌进曲线）；硬件快照放行，保持
            # CPU/显卡页有心跳，否则整屏「等数据…」像卡死。
            self._drain_hardware_only()

        ok, msg = self._pending_status
        if not started:
            msg = "未开始采集（点「▶ 开始采集」）"
        color = CURRENT_THEME.green if ok else CURRENT_THEME.warn
        dropped = self.q.take_dropped()
        if dropped:
            msg = "%s（队列溢出丢弃 %d 帧）" % (msg, dropped)
            color = CURRENT_THEME.warn
        self.lbl_engine_badge.setText("● 引擎: %s" % msg)
        self.lbl_engine_badge.setStyleSheet(
            "color: %s; font-size: 10px; font-weight: bold;" % color)

        self._update_data_source_banner()
        if self.is_paused:
            return

        # ---- 目标进程 ----
        if self.target_app == "自动锁定主游戏":
            dom = self.store.dominant_app()
            if dom and dom != self._auto_dom:
                self._auto_dom = dom
        else:
            self._auto_dom = None
        eff = self.target_app
        if eff == "自动锁定主游戏":
            eff = self._auto_dom
        frames = self.store.visible_frames(eff)
        page_idx = self.stack.currentIndex()

        if len(frames) < 2:
            if started:
                self.banner_text.setText(
                    "🟡 正在等待帧数据…（采集已开始；游戏在运行时仍无数据，请以管理员身份运行）")
            else:
                self.banner_text.setText("🟡 未开始采集 —— 点左侧「▶ 开始采集」开始采样")
            self._set_style(self.banner_frame, self._banner_qss(CURRENT_THEME.warn))
            # 只在「从有帧 → 无帧」时复位一次卡片；否则每 tick 复位会把下面
            # 刚写好的 CPU/显卡数字冲回「--%」（看起来又像不刷新）
            had = getattr(self, "_had_frames", False)
            self._had_frames = False
            if had:
                self._reset_cards()
            # 【别每 tick 清一次图】空图重复 clear() 会 30Hz 重设轴范围：
            # 既白耗重绘，又让用户的手动缩放「刚放大就被抹掉」。
            if page_idx == 0 and self.fps_chart._frames:
                self.fps_chart.clear()
            # 【无帧也有心跳】硬件探针一直在出快照：把硬件页 / 徽章 / 瓶颈条刷起来，
            # 否则整屏「等数据…」看起来就像软件卡死（用户报「无刷新」即此观感）。
            # 约 100ms 刷一次，和正常路径的 PAGE_POLL 同频。
            self._env_tick += 1
            hw0 = self.store.snapshots[-1] if self.store.snapshots else None
            if hw0 is not None and self._env_tick % self.PAGE_POLL == 0:
                ctx0 = {}
                for p in self.pages[1:]:
                    try:
                        p.refresh(hw0, ctx0)
                    except Exception as ex:
                        DIAG.report("_refresh_ui_inner.hw_only_page", ex)
                self._refresh_nav_badges(hw0, ctx0)
                try:
                    bn = bottleneck(hw0, None, getattr(hw0, "cpu_total", None))
                    self.bn_bar.set_state(bn["gpu_pct"], bn["cpu_pct"], bn["side"])
                    self.lbl_cpu_num.setText("CPU %.0f%%" % bn["cpu_pct"])
                    self.lbl_gpu_num.setText("显卡 %.0f%%" % bn["gpu_pct"])
                    self.lbl_bottleneck.setText(bn.get("conclusion") or bn["text"])
                    self.lbl_bottleneck.setToolTip(bn.get("advice") or "")
                    self.lbl_bn_advice.setText(bn.get("advice") or "")
                except Exception as ex:
                    DIAG.report("_refresh_ui_inner.hw_only_bn", ex)
            elif hw0 is None:
                self._refresh_nav_badges(None, {})
            return

        self._env_tick += 1
        self._had_frames = True
        if self._env_tick % self.FRAME_POLL == 0:
            self.store.update_baseline(frames)

        # ---- 统计（≈100ms 一次，避免每 tick 重算 numpy）----
        if self._stat_cache is None or self._env_tick % 3 == 0:
            self._stat_cache = window_stats(frames)
        st = self._stat_cache
        grade = st.get("grade", "C")

        # ---- 曲线（仅当前页面可见时才重画，切页回来下一 tick 即恢复）----
        if page_idx == 0:
            display = frames[-2500:]
            # 【v1.0.1】把「稳健最高帧」当 Y 轴依据传下去：卡片的数字与图上界同一个口径，
            # 也避免单帧 477/2554fps 的离群点把整条曲线压成一条直线。
            self.fps_chart.update_series(display, st.get("avg_fps", 0.0),
                                         peak_fps=st.get("max_fps"))
            if self._env_tick % self.FRAME_POLL == 0:
                self._position_watermark()

        # ---- 四大帧数卡（规范 §4：标题 + 大数字 + 最多一行短副文本）----
        # 平均帧卡不再重复"评级"文案（等级由横幅徽章统一表达，避免与病历卡重复）
        self.card_avg.set_value("%.0f" % st.get("avg_fps", 0.0))
        # 最高帧用稳健值；真的出现离群尖峰时在副文本里如实标注（不藏数据）
        spike = float(st.get("spike_fps", 0.0) or 0.0)
        self.card_max.set_value("%.0f" % st.get("max_fps", 0.0),
                                ("尖峰 %.0f" % spike) if spike > 0 else "",
                                role="green" if spike <= 0 else "accent")
        self.card_min.set_value("%.0f" % st.get("low1_fps", 0.0),
                                "0.1%% Low %.0f" % st.get("low01_fps", 0.0))
        dtext, dlevel = dropped_verdict(st.get("dropped", 0))
        role = {"green": "green", "warn": "warn", "bad": "bad"}[dlevel]
        # 顿挫卡：两行小字合并成一行 →「占比 19.2% · 频繁」
        self.card_drop.set_value("%d 次" % st.get("dropped", 0),
                                 "占比 %.1f%%" % st.get("dropped_pct", 0.0),
                                 tail_text=dtext.replace("顿挫", "").replace("漏帧", "") or dtext,
                                 role=role)

        # ---- 段位徽章 ----
        self.badge.set_grade(grade, st.get("grade_label", "等待数据"))
        # 【v1.0.0 文案瘦身】原来这里还有一行「平均 54 帧 · 最高 2554 · 1% Low 2 ·
        # 顿挫 39.46%」的灰字——和下面 4 张卡完全重复，已删除。

        last = frames[-1]
        hw = self.store.snapshot_for(last.hw_seq)
        ctx = self._page_ctx(hw, frames)
        self._push_cpu_trend(hw)

        # ---- 当前极客页面 / 总览页头部结论 / 硬件面板（图形化全尺寸刷新）----
        if self._env_tick % self.PAGE_POLL == 0:
            try:
                self.pages[page_idx].refresh(hw, ctx)
            except Exception as ex:
                DIAG.report("_refresh_ui_inner.page", ex)
        if self._env_tick % self.BADGE_POLL == 0:
            self._refresh_nav_badges(hw, ctx)

        # ---- 瓶颈天平（改进建议书 四.2）----
        bn = bottleneck(hw, ctx.get("gpu_pct"), getattr(hw, "cpu_total", None))
        self.bn_bar.set_state(bn["gpu_pct"], bn["cpu_pct"], bn["side"])
        self.lbl_cpu_num.setText("CPU %.0f%%" % bn["cpu_pct"])
        self.lbl_gpu_num.setText("显卡 %.0f%%" % bn["gpu_pct"])
        self.lbl_bottleneck.setText(bn.get("conclusion") or bn["text"])
        self.lbl_bottleneck.setToolTip(bn.get("advice") or "")
        self.lbl_bn_advice.setText(bn.get("advice") or "")

        # ---- 顶部横幅 + 病历卡：严格顿挫判定（改进建议书 五）----
        # 【v1.0.0 修「StutterTriage 自己卡住」】inspect() 结尾的会话级归因是
        # O(全部帧 × 卡顿帧)、且缓存键含帧数（实时采集时永不命中），旧实现
        # 在顿挫期间每 33ms 全量重算（自动弹卡还会再算一遍），会话一长就把
        # UI 线程拖死。现在：横幅 ≥0.5s 重算一次；自动弹卡 ≥1s 至多一次；
        # 节流窗口内沿用上一轮 rows（红色横幅保持不闪、不清空）。
        stutter_now = bool(st.get("stutter_now"))
        if stutter_now:
            frame = frames[-1]
            now = time.time()
            popped = False
            si = st.get("stutter_idx")
            # 真顿挫 → 自动弹病历卡（新顿挫帧 + 距上次自动弹卡 ≥1s）
            if (self.selected_frame is None and si is not None
                    and si != self._auto_diag_idx
                    and (now - self._auto_popup_ts) >= self.AUTO_POPUP_INTERVAL):
                self._auto_diag_idx = si
                self._auto_popup_ts = now
                # strict=True 是**显式**传入的：这次弹卡的原因就是「真顿挫」，
                # 不能让后续刷新把它翻成「丝滑」（与顶部红字打脸）
                self._on_chart_frame_clicked(int(si), strict=True)
                popped = True
            if popped:
                rows = self._last_rows          # 弹卡时刚算过，直接复用
            elif (now - self._diag_render_ts) >= self.DIAG_RENDER_INTERVAL:
                self._diag_render_ts = now
                rows = SnapshotDiffInspector.inspect(frame, self.store)
                self._last_rows = rows
            else:
                rows = None                     # 节流窗口内：横幅维持上一轮文案
            if rows is not None:
                info = explain(rows)
                self._set_style(self.banner_frame, self._banner_qss(info["color"]))
                self.banner_text.setText(info["text"])
        else:
            if self._env_tick % self.FRAME_POLL == 0:
                self._last_rows = []
            color = GRADE_COLOR.get(grade, CURRENT_THEME.green)
            self._set_style(self.banner_frame, self._banner_qss(color))
            # 【v1.0.1 修矛盾文案】低帧率（<30 帧 或 C 级）时严禁再拼「全程丝滑」——
            # 帧率低但没触发顿挫，正确说法是「帧率偏低」。
            avg_now = float(st.get("avg_fps", 0.0) or 0.0)
            low_fps = avg_now < 30.0 or grade in ("C", "D")
            if low_fps:
                self.banner_text.setText(
                    "🟠 当前 %.0f 帧 · %s（%s 级）· 帧率偏低"
                    % (avg_now, st.get("grade_label", "运行中"), grade))
            else:
                self.banner_text.setText(
                    "🟢 当前 %.0f 帧 · %s（%s 级）· 全程丝滑"
                    % (avg_now, st.get("grade_label", "运行中"), grade))
            # 【v1.0.0】没触发严格顿挫 → 病历卡回到 idle 占位（不再 hide：
            # hide 会让下面的折线图瞬间长高 110px，整页上下跳）
            if self.selected_frame is None:
                self.diag_card.show_idle()
            self._auto_diag_idx = None

        # ---- 病历卡（锁定帧时低频跟随刷新，1Hz 足够；专业表格同步）----
        if (self.selected_frame is not None and self.diag_card.isVisible()
                and (time.time() - self._diag_render_ts) >= self.DIAG_FOLLOW_INTERVAL):
            self._diag_render_ts = time.time()
            self._render_diagnosis(self.selected_frame, st, strict=self._diag_strict)

        # ---- 事件流面板（打开时才刷新；差量写入，代价可控）----
        if self.hw_panel.isVisible():
            self._refresh_events_tab()

    # ================================================================
    # 诊断
    # ================================================================
    def _update_data_source_banner(self):
        """数据源判定 + 模拟水印（覆盖基类：颜色改为**实时**读主题）"""
        try:
            src = detect_data_source(self.pm_bridge, offline=self._offline_mode)
            self._data_source = src
            color = datasource_color(src)
            self.lbl_data_source.setText(DATA_SOURCE_LABEL.get(src, src))
            self.lbl_data_source.setStyleSheet(
                "color: %s; font-size: 10px; font-weight: bold;" % color)
            wm = getattr(self, "_sim_watermark", None)
            if wm is not None:
                wm.setVisible(src == DATA_SOURCE_SIM)
                if src == DATA_SOURCE_SIM:
                    self._position_watermark()
        except Exception as ex:
            DIAG.report("_update_data_source_banner", ex)

    def _banner_qss(self, edge, border=None):
        th = CURRENT_THEME
        return ("QFrame#BannerCard { background-color: %s; border: 1px solid %s;"
                " border-left: 6px solid %s; border-radius: 8px; }"
                % (th.panel, border or th.border_hot, edge))

    def _push_cpu_trend(self, hw):
        """
        把硬件快照喂给 CPU 页的频率/功率双折线。

        【为什么放在主窗口而不是 CpuPage.refresh】refresh 只在「当前页正好是 CPU 页」
        时才被调用；如果只在页面内喂数据，用户切到 CPU 页时曲线永远是空的。
        这里每 tick 喂一次（trend.push 内部按 seq 去重），任何时刻切过去都有历史。
        """
        if hw is None:
            return
        try:
            freqs = [f for f in (getattr(hw, "cpu_freq", None) or []) if f]
            avg_f = (sum(freqs) / len(freqs)) if freqs else 0.0
            top_f = max(freqs) if freqs else 0.0
            self.pages[1].trend.push(getattr(hw, "seq", None), avg_f, top_f,
                                     getattr(hw, "cpu_power", None),
                                     bool(getattr(hw, "cpu_power_est", False)))
        except Exception as ex:
            DIAG.report("_push_cpu_trend", ex)

    def _reset_cards(self):
        for card in (self.card_avg, self.card_max, self.card_min, self.card_drop):
            card.set_value("--", "")
        self.badge.set_grade("-", "等待数据")
        self.lbl_bottleneck.setText("等数据…")
        self.lbl_bottleneck.setToolTip("")
        self.bn_bar.set_state(0.0, 0.0, "unknown")
        self.lbl_bn_advice.setText("")
        try:
            self.lbl_cpu_num.setText("CPU --%")
            self.lbl_gpu_num.setText("显卡 --%")
        except Exception:
            pass

    def _find_frame_by_idx(self, idx):
        frames = self.store.visible_frames(self._eff_target())
        for f in reversed(frames):
            if f.idx == idx:
                return f
        best = None
        for f in frames:
            if best is None or abs(f.idx - idx) < abs(best.idx - idx):
                best = f
        return best

    def _eff_target(self):
        eff = self.target_app
        if eff == "自动锁定主游戏":
            eff = self._auto_dom
        return eff

    def _prev_frame_of(self, frame):
        frames = self.store.visible_frames(self._eff_target())
        prev = None
        for f in frames:
            if f.idx >= frame.idx:
                break
            prev = f
        return prev

    def _frame_is_stutter(self, frame):
        """
        该帧是否**真的**构成顿挫（改进建议书 五的双重门控）。

        直接复用 `stats.is_stutter_frame`（最近 120 帧局部中位数 + 8ms 跳变 +
        85FPS 豁免），保证「顶部红字告警 / 折线红点 / 病历卡」三处口径完全一致。
        """
        if is_stutter_frame is None or frame is None:
            return False
        try:
            frames = self.store.visible_frames(self._eff_target())
            for pos in range(len(frames) - 1, -1, -1):
                if frames[pos].idx == frame.idx:
                    return bool(is_stutter_frame(frames, pos))
        except Exception as ex:
            DIAG.report("_frame_is_stutter", ex)
        return False

    def _on_chart_frame_clicked(self, idx, strict=None):
        """
        锁定某一帧并渲染病历卡。

        strict : 该帧是否真顿挫。**在点击那一刻定下来并记住**（`_diag_strict`）。
        【v1.0.1 修「结论与病历卡互相打脸」】以前每次刷新都用**当前**窗口重算
        `_frame_is_stutter(frame)`：局部中位数会随新帧漂移，几秒后同一帧可能从
        「真顿挫」翻成「全程丝滑」，于是顶部红字说顿挫、底部病历卡说丝滑。
        现在把判定**锁在选中那一刻**，之后只刷新数值不翻状态。
        """
        frame = self._find_frame_by_idx(idx)
        if frame is None:
            return
        self.selected_frame = frame
        self.fps_chart.set_locked(frame.idx)
        st = self._stat_cache or window_stats(self.store.visible_frames(self._eff_target()))
        self._diag_strict = (bool(strict) if strict is not None
                             else self._frame_is_stutter(frame))
        self._render_diagnosis(frame, st, strict=self._diag_strict)
        # 【v1.0.0】刚刚全量渲染过 → 记时，避免锁定卡的跟随刷新立刻再来一遍
        self._diag_render_ts = time.time()
        self.diag_card.setVisible(True)

    def _reset_diagnosis_card(self):
        """复位病历卡（目标进程切换 / CSV 导入 / 关闭）：同时清掉「顿挫锁」"""
        self._diag_strict = None
        super()._reset_diagnosis_card()

    def _render_diagnosis(self, frame, st, strict=None):
        try:
            rows = SnapshotDiffInspector.inspect(frame, self.store)
        except Exception as ex:
            DIAG.report("_render_diagnosis", ex)
            return
        self._last_rows = rows
        prev = self._prev_frame_of(frame)
        self._diag_prev_frame = prev
        if strict is None:
            strict = (self._diag_strict if self._diag_strict is not None
                      else self._frame_is_stutter(frame))
        if strict:
            self.diag_card.show_for(frame, prev, rows, (st or {}).get("grade", "C"))
        else:
            # 没触发严格顿挫 → 只给绿色「全程运行丝滑」，绝不给红色暴跌结论
            self.diag_card.show_smooth((st or {}).get("grade", "S"),
                                       (st or {}).get("avg_fps", 0.0),
                                       (st or {}).get("dropped", 0))
        if self._pro_dialog is not None and self._pro_dialog.isVisible():
            self._pro_dialog.show_rows(frame, rows)

    def _jump_to_last_drop(self):
        """定位最近一次真顿挫（没有则给绿色提示）"""
        pts = self.fps_chart.drop_points()
        if not pts:
            self.banner_text.setText("🟢 本次监控还没有出现真顿挫，全程丝滑～")
            return
        idx = int(pts[-1][0])
        self._on_chart_frame_clicked(idx)

    def _open_pro_dialog(self):
        if self.selected_frame is None:
            return
        if self._pro_dialog is None:
            self._pro_dialog = ProfessionalTableDialog(self)
        rows = self._last_rows
        if not rows:
            rows = SnapshotDiffInspector.inspect(self.selected_frame, self.store)
        self._pro_dialog.show_rows(self.selected_frame, rows)
        self._pro_dialog.restyle()
        self._pro_dialog.show()
        self._pro_dialog.raise_()
        self._pro_dialog.activateWindow()

    def _on_diag_closed(self):
        self.selected_frame = None
        self._diag_strict = None
        self.fps_chart.set_locked(None)
        # 立刻回到 idle 占位（不要等到下一个刷新节拍，否则点 ✕ 后卡片还停在旧内容）
        try:
            self.diag_card.show_idle()
        except Exception:
            pass

    # ================================================================
    # 交互 / 外观
    # ================================================================
    def _toggle_fps_mode(self):
        self._fps_mode = MODE_MS if self._fps_mode == MODE_FPS else MODE_FPS
        self.fps_chart.set_mode(self._fps_mode)
        self.btn_fps_mode.setText("ms 视角" if self._fps_mode == MODE_MS else "FPS 视角")

    def _apply_theme(self, theme, persist=True):
        """
        主题热切换（覆盖基类：必须走 theme.set_theme 才能刷新**所有模块**的颜色副本）
        """
        try:
            T.set_theme(theme)
            self._apply_global_stylesheet()
            self.fps_chart.apply_theme(theme)
            self._restyle_static_labels()
            self._restyle_chart()
            # v1.0.0：主题下拉框跟随（自定义背景色 / 预览脚本 / 测试等
            # 不走 combo 回调的路径，否则下拉框会残留旧主题名）
            self._sync_theme_combo(theme)
            if self._pro_dialog is not None:
                self._pro_dialog.restyle()
            if persist:
                if self._ux_config is None:
                    self._ux_config = Config()
                self._ux_config.set("theme", theme.key)
                if theme.key == "custom" and theme.custom_bg:
                    self._ux_config.set("custom_bg", theme.custom_bg)
                self._ux_config.save()
            return True
        except Exception as ex:
            DIAG.report("_apply_theme(v4)", ex)
            return False

    def _restyle_chart(self):
        self.fps_chart.apply_theme(CURRENT_THEME)
        self.fps_chart.apply_chart_style(T.CURRENT_CHART_STYLE)

    def _update_gradient(self, *args):
        self.fps_chart._update_gradient()

    def _restyle_static_labels(self):
        th = CURRENT_THEME
        for lbl, col in ((self.lbl_engine_badge, th.dim),
                         (self.banner_text, th.fg),
                         (self.lbl_bottleneck, th.fg),
                         (self.lbl_bn_advice, th.dim),
                         (self.lbl_data_source, th.green),
                         (self.lbl_hotkey, th.dim),
                         (self.lbl_pm_hint, th.warn),
                         (self.lbl_logo, th.accent),
                         (self.lbl_target_caption, th.dim),
                         (self.lbl_cpu_num, th.orange),
                         (self.lbl_gpu_num, th.accent)):
            try:
                sheet = lbl.styleSheet()
                if "color:" in sheet:
                    import re as _re
                    sheet = _re.sub(r"color:\s*#[0-9a-fA-F]{6}", "color: %s" % col, sheet)
                else:
                    sheet = sheet + "; color: %s;" % col
                lbl.setStyleSheet(sheet)
            except Exception:
                pass
        for card in (self.card_avg, self.card_max, self.card_min, self.card_drop):
            try:
                card.restyle()
            except Exception:
                pass
        for tab in self.nav_tabs:
            try:
                tab.restyle()
            except Exception:
                pass
        for page in self.pages:
            try:
                page.restyle()
            except Exception:
                pass
        try:
            self.diag_card.restyle()
            self.bn_bar.update()
        except Exception:
            pass
        try:
            self._restyle_settings_page()
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        try:
            self._restyle_chart()
        except Exception:
            pass

    def _init_ux(self):
        super()._init_ux()
        # 拆分后 Config 必须广播到**所有**持有阈值副本的模块（基类只写了自身 globals）
        try:
            if self._ux_config is not None:
                self._ux_config.apply_to_globals()
                self.store.baseline.ema = float(self._ux_config.get("baseline_ema", 0.25))
                self.store.baseline.window = int(self._ux_config.get("baseline_window", 240))
                self.store.baseline.spike_ratio = float(
                    self._ux_config.get("baseline_spike_ratio", 2.2))
                self.store.baseline.max_spike_frac = float(
                    self._ux_config.get("baseline_max_spike_frac", 0.35))
        except Exception as ex:
            DIAG.report("_init_ux(broadcast)", ex)
