# -*- coding: utf-8 -*-
"""
StutterTriageWindowBase —— 从 StutterTriage 3.2 单体工程原样迁移的可复用窗口逻辑。

本模块承载与版面无关的行为层：设置页 / 主题热切换 / QSS /
CSV 导入导出 / ETW 清理 / 提权提醒 / PresentMon 指引 / 热键接线 /
进程列表刷新 / 关闭流程。

【v1.0.0 清理】v3.2 的 4 个硬件页签构建器（CPU 热力图 / 显存监视 /
内存换入 / 着色器与引擎）连同只写不读的死字段一并删除 —— 它们的信息
在主界面 5 个导航页里已经全有了。本模块现在只保留：
  · `_build_settings_tab()` —— ⚙ 设置页（主题 / 阈值 / 热键 / PresentMon）
  · `_build_events_tab()`   —— ⚡ 事件流表（引擎告警 + 手动打点的唯一历史视图）

版面（_init_ui）与每帧刷新（_refresh_ui_inner）由子类
stuttertriage.ui.main_window.StutterTriageWindow 重新实现（GamePP 风格）。
"""

import os
import csv
import time
import math
import ctypes
import queue
import threading
import pyqtgraph as pg
from PyQt6 import QtCore, QtWidgets
from PyQt6.QtWidgets import (
    QColorDialog, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QTableWidget, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QKeySequence
from ..analysis import FrameStore
from ..config import (CONFIG_FILENAME, CONFIG_GROUPS, CONFIG_LABELS, CONFIG_SCHEMA,
                      Config, DEFAULT_CONFIG, _coerce)
from ..consts import DATA_SOURCE_COLOR, DATA_SOURCE_LABEL, DATA_SOURCE_REAL, DATA_SOURCE_SIM, PM_SESSION_PREFIX
from ..csvio import CsvBackgroundReader
from ..engines.hardware import HardwareEngine
from ..engines.presentmon import PresentMonBridge, find_presentmon_binary
from ..etw import EtwSessionManager
from ..hotkeys import GlobalHotkeyManager, parse_hotkey
from ..models import Event, FrameRecord
from ..theme import CHART_STYLE_CAPFRAMEX, CHART_STYLE_HUD, FONT_FAMILY, PG_MENU_ZH, THEME_DEEP_SPACE, THEME_PRESETS, _mix, apply_theme_to_globals, theme_from_custom_bg
from .pages import _set_cells
from ..utils import BoundedQueue, DIAG, is_na, safe_float
from ..winadmin import detect_data_source, is_admin, open_dir_in_explorer, presentmon_dir, presentmon_missing_hint, relaunch_as_admin
from ..theme import bind_globals as _bind_theme_globals
_bind_theme_globals(globals())
from ..consts import bind_globals as _bind_const_globals
_bind_const_globals(globals())


class StutterTriageWindowBase(QMainWindow):
    """旧 StutterTriage 3.2 主窗口中可复用的逻辑层（版面与每帧刷新由子类重写）。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("StutterTriage 3.2 · 次世代掉帧研究工作台")
        self.resize(1440, 940)
        self.setMinimumSize(1120, 760)

        self.store = FrameStore()
        self.q = BoundedQueue(maxsize=60000)
        self.is_paused = False
        # 【手动开始采集】进程序不再自动把曲线画起来：未点「▶ 开始采集」前，
        # 后台线程产出的一切（帧 / 快照 / 状态）都视为"还没开始"直接丢弃
        # （见 _refresh_ui_inner 的 started 分支与 _discard_pending）。
        self._collect_started = False
        self.target_app = "自动锁定主游戏"
        self.selected_frame = None
        self._auto_dom = None
        self._env_tick = 0
        self._pending_status = (True, "正在初始化…")

        # 【v1.0.0 清理】v3.2 遗留的 _display_frames/_display_ts/_display_idx/_display_x、
        # _chart_mode/_chart_mode_dirty、_env_cache/_metric_cache_ui/_table_cache 已删除：
        # 新主界面（QStackedWidget + FpsChart）自带渲染管线，这些字段只写不读。
        self._zh_pg_menus_done = set()           # 已汉化的 pyqtgraph 菜单（防重复挂 aboutToShow）

        # ---------- 【UX 新增】状态字段 ----------
        self._data_source = DATA_SOURCE_REAL   # real / simulated / offline-import
        self._offline_mode = False              # 离线 CSV 导入后为 True
        self._mark_seq = 0                     # 手动标记计数
        self._ux_config = None                 # Config 实例
        self._hotkey = None                    # GlobalHotkeyManager
        self._admin_prompt_shown = False       # 【v3.2】启动管理员提醒是否已弹过一次
        # 【v4.3】正在关闭：挡住 closeEvent 之后才触发的 singleShot 弹窗
        # （否则「关窗口 → 700ms 后权限提醒才弹出来」会留下一个孤儿对话框在屏幕上）
        self._closing = False
        self._admin_dialog = None
        self._placed_once = set()
        # ---- UI 侧缓存（避免每 tick 重复计算 / 重建控件）----
        self._style_cache = {}            # 控件 id → 上次样式串（避免重复 setStyleSheet）

        # ---- 后台线程 ----
        # 硬件探针一直跑（未采集时也显示 CPU/显卡心跳，见 _drain_hardware_only）
        self.hw_engine = HardwareEngine(0.4, self.q)
        self.hw_engine.start()

        # 【v1.0.5 延迟启动 PresentMon】以前这里一构造就拉起桥接：若当前没有
        # 游戏在画（或对方机器根本没装 PresentMon），PresentMon 采不到帧退出后
        # 桥接会**自动切模拟模式**，于是用户看到「非真实采样」。现在等用户点
        # 「▶ 开始采集」才启动（_begin_collection / _toggle_simulation）。
        self.pm_bridge = None

        self._init_ui()

        # 【UX 新增】配置 / 全局热键 / 提权提示 / PresentMon 指引
        self._init_ux()

        # 30 FPS UI 刷新
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._refresh_ui)
        self.ui_timer.start(33)

        # 进程列表刷新
        self.proc_timer = QTimer(self)
        self.proc_timer.timeout.connect(self._refresh_processes)
        self.proc_timer.start(2500)

    def _start_presentmon_bridge(self):
        if self.pm_bridge:
            self.pm_bridge.stop()
        self.pm_bridge = PresentMonBridge(self.q, target_process=self.target_app)
        self.pm_bridge.start()

    DRAIN_MAX_FRAMES = 4000        # 单次 tick 最多搬运的帧数

    DRAIN_MAX_SNAPS = 200          # 单次 tick 最多搬运的硬件快照数

    DRAIN_MAX_EVENTS = 400         # 单次 tick 最多搬运的事件数

    DRAIN_BUDGET_MS = 16.0         # 单次 tick 搬运的时间预算

    def _drain_queue(self):
        """
        把后台线程产出搬到 UI 线程（唯一写者模型）。

        【性能】单次 tick 有上限（帧 / 快照 / 事件条数 + 时间预算）：
        生产快于消费时（例如 ETW 一次性吐出上万帧），
        原实现会在一个 tick 里处理上万项，把 UI 卡死几十毫秒。
        超出的部分原样留在队列里，下一 tick 继续处理。
        """
        frames = 0
        snaps = 0
        events = 0
        t0 = time.perf_counter()
        try:
            while True:
                if frames >= self.DRAIN_MAX_FRAMES or snaps >= self.DRAIN_MAX_SNAPS \
                        or events >= self.DRAIN_MAX_EVENTS:
                    break
                if (time.perf_counter() - t0) * 1000.0 > self.DRAIN_BUDGET_MS:
                    break
                kind, payload = self.q.get_nowait()
                if kind == "frame":
                    self.store.add_frame(payload)
                    frames += 1
                elif kind == "hw":
                    self.store.add_snapshot(payload)
                    snaps += 1
                elif kind == "event":
                    self.store.add_event(payload)
                    events += 1
                elif kind == "status":
                    self._pending_status = payload
        except queue.Empty:
            pass
        return frames

    def _apply_global_stylesheet(self):
        """
        应用全局样式表（主题切换时重新调用即可）。

        使用具名占位符 %(name)s —— 少写/写错会直接 KeyError，而不是静默错位。

        【v3.1】独立非模态窗口（进阶硬件面板）**不会**继承主窗口的样式表，
        这里一并下发，否则面板下半部会露出 Fusion 默认的浅色底，
        在深色主题里非常刺眼。
        """
        qss = """
            QMainWindow { background-color: %(bg)s; }
            QWidget#HwPanel { background-color: %(bg)s; }
            /* 【v1.0.1 统一字重】全局基线 500：
               禁止「毫无力量的细体小灰字」与粗体大字混排。
               显式写死 font-weight 的控件（标题 600 / 数值 bold）优先级更高，不受影响。 */
            QWidget { color: %(fg)s; font-family: %(font)s; font-size: 12px;
                      font-weight: 500; }
            QLabel { font-weight: 500; }
            QLabel#KeyChipLabel { font-weight: 600; }
            QTableWidget {
                background-color: %(panel)s; gridline-color: %(border)s;
                border: 1px solid %(border)s; border-radius: 4px;
                selection-background-color: %(panel2)s;
            }
            QTableWidget::item { padding: 3px; }
            QHeaderView::section {
                background-color: %(panel2)s; color: %(dim)s; font-weight: bold;
                border: 1px solid %(border)s; padding: 4px;
            }
            QTabWidget::pane { border: 1px solid %(border)s; background: %(panel)s; top: -1px; }
            QTabBar::tab {
                background-color: %(panel2)s; color: %(dim)s; padding: 6px 14px; margin-right: 2px;
                border-top-left-radius: 4px; border-top-right-radius: 4px; font-weight: bold;
            }
            QTabBar::tab:selected {
                background-color: %(panel)s; color: %(accent)s; border-bottom: 2px solid %(accent)s;
            }
            QPushButton {
                background-color: %(panel2)s; border: 1px solid %(border)s; border-radius: 4px;
                padding: 5px 12px; font-weight: bold; color: %(fg)s;
            }
            QPushButton:hover { background-color: %(hover)s; border-color: %(accent)s; }
            QPushButton:pressed { background-color: %(accent_d)s; }
            QComboBox {
                background-color: %(panel2)s; border: 1px solid %(border)s; border-radius: 4px;
                padding: 4px 10px; color: %(fg)s;
            }
            QComboBox QAbstractItemView {
                background-color: %(panel2)s; color: %(fg)s; selection-background-color: %(accent_d)s;
            }
            QLineEdit {
                background-color: %(panel2)s; border: 1px solid %(border)s; border-radius: 4px;
                padding: 4px 8px; color: %(fg)s;
            }
            QProgressBar {
                background-color: %(panel2)s; border: 1px solid %(border)s;
                border-radius: 4px; text-align: center; color: %(fg)s;
            }
            QProgressBar::chunk { background-color: %(accent)s; }
            QToolTip {
                background-color: %(panel2)s; color: %(fg)s;
                border: 1px solid %(border)s; padding: 4px;
            }
            QScrollBar:vertical { background: %(bg)s; width: 10px; }
            QScrollBar::handle:vertical { background: %(border_hot)s; border-radius: 5px; }
            QScrollBar::add-line, QScrollBar::sub-line { height: 0px; }
            QScrollBar:horizontal { background: %(bg)s; height: 10px; }
            QScrollBar::handle:horizontal { background: %(border_hot)s; border-radius: 5px; }
            /* 设置页滚动区：背景透明，露出 Tab 面板底色（深/浅主题都不刺眼） */
            QScrollArea { background: transparent; border: none; }
            /* 【v4.2 修 Bug】视口（viewport）必须显式上色！
               旧写法只写了 `QScrollArea > QWidget > QWidget`（= 内容 widget），
               视口本身没被命中，于是它继续用 Fusion 默认调色板的**浅灰**：
               暗色主题下整个「设置」页就是一块白底，浅色文字压在上面完全看不清。 */
            QScrollArea > QWidget#qt_scrollarea_viewport { background-color: %(panel)s; }
            QScrollArea > QWidget#qt_scrollarea_viewport > QWidget { background: transparent; }

            /* ================================================================
               【v4.2 修 Bug：暗色主题下「设置」看不清】菜单 / 对话框
               ----------------------------------------------------------------
               原来只给 QWidget 设了 color，没给 QMenu/QDialog 设背景：
               文字被强制成浅色，背景却还是 Fusion 的浅灰 → 浅字压浅底，全白不可读。
               这里补齐背景 + 悬停/选中/禁用/分隔线，深浅主题都成对给色。
               ================================================================ */
            QMenu {
                background-color: %(panel2)s;
                color: %(fg)s;
                border: 1px solid %(border)s;
                padding: 4px;
            }
            QMenu::item {
                background-color: transparent;
                color: %(fg)s;
                padding: 6px 26px 6px 14px;
                border-radius: 4px;
            }
            QMenu::item:selected { background-color: %(accent_d)s; color: #ffffff; }
            QMenu::item:disabled { color: %(dim)s; }
            QMenu::separator { height: 1px; background: %(border)s; margin: 4px 8px; }
            QMenu::right-arrow { width: 12px; height: 12px; }
            QMenu::icon { padding-left: 8px; }

            QDialog, QMessageBox, QInputDialog { background-color: %(panel)s; color: %(fg)s; }
            QMessageBox QLabel { color: %(fg)s; background: transparent; }
            QMessageBox QPushButton { min-width: 76px; }

            /* ================================================================
               v3.2 左分栏：侧边栏 + 极客面板 + 导航按钮
               ================================================================ */
            QFrame#Sidebar {
                background-color: %(panel)s;
                border: 1px solid %(border)s;
                border-radius: 10px;
            }
            QFrame#GeekPanel {
                background-color: %(panel2)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
            }
            /* 侧边栏导航按钮：扁平、左对齐、悬停时强调色描边 */
            QPushButton#NavButton {
                background-color: %(panel2)s;
                border: 1px solid %(border)s;
                border-radius: 6px;
                padding: 5px 10px;
                text-align: left;
                font-weight: bold;
                color: %(fg)s;
            }
            QPushButton#NavButton:hover {
                background-color: %(hover)s;
                border-color: %(accent)s;
                color: %(accent)s;
            }
            QPushButton#NavButton:pressed { background-color: %(accent_d)s; color: #ffffff; }
            /* 侧边栏主按钮（开始/暂停）：始终强调色，视觉权重最高 */
            QPushButton#NavPrimary {
                background-color: %(accent_d)s;
                border: 1px solid %(accent)s;
                border-radius: 8px;
                padding: 8px 12px;
                text-align: center;
                font-size: 13px;
                font-weight: bold;
                color: #ffffff;
            }
            QPushButton#NavPrimary:hover { background-color: %(accent)s; }
            QPushButton#NavPrimary:pressed { background-color: %(panel2)s; color: %(accent)s; }

            /* ---- v3.1 卡片化：所有卡片统一圆角 8px + 细微边框 ---- */
            QFrame#Card, QFrame#BannerCard, QFrame#ChartCard, QFrame#DrawerCard {
                background-color: %(panel)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
            }
            QFrame#Card:hover, QFrame#ChartCard:hover { border-color: %(border_hot)s; }
            QFrame#StutterCard {
                background-color: %(panel2)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
            }

            /* ---- 「展开进阶硬件面板」大按钮（活泼风格） ---- */
            QPushButton#RocketButton {
                background-color: %(accent_d)s;
                color: #ffffff;
                border: 1px solid %(accent)s;
                border-radius: 8px;
                padding: 10px 26px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton#RocketButton:hover { background-color: %(accent)s; }
            QPushButton#RocketButton:pressed { background-color: %(panel2)s; color: %(accent)s; }

            /* ---- 抽屉「收起」按钮 ---- */
            QPushButton#DrawerCloseButton {
                background-color: %(panel2)s;
                color: %(fg)s;
                border: 1px solid %(border_hot)s;
                border-radius: 6px;
                padding: 3px 10px;
                font-weight: bold;
            }
            QPushButton#DrawerCloseButton:hover { border-color: %(bad)s; color: %(bad)s; }
        """ % {
            "bg": BG, "fg": FG, "font": FONT_FAMILY, "panel": PANEL, "panel2": PANEL2,
            "border": BORDER, "border_hot": BORDER_HOT, "dim": DIM,
            "accent": ACCENT, "accent_d": ACCENT_D, "warn": WARN, "bad": BAD,
            "hover": _mix(BG, ACCENT, 0.14),
        }
        self.setStyleSheet(qss)
        # 独立窗口（进阶硬件面板）同步同一套 QSS
        for attr in ("hw_panel",):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.setStyleSheet(qss)
                except Exception:
                    pass
        # QSS 覆盖不到的原生表面（菜单 / 对话框 / 滚动区视口）靠调色板兜底
        self._apply_app_palette()

    def _apply_app_palette(self):
        """
        把当前主题色写进 QApplication 的 QPalette（v4.2）。

        【为什么必须有】QSS 只能命中显式写出来的选择器。QMenu（⚙ 齿轮菜单、
        pyqtgraph 右键菜单）、QMessageBox（清理 ETW 的二次确认）这类
        **原生弹出层**不带 QSS 背景，会退回 Fusion 默认调色板（浅灰）——
        而文字颜色又被 `QWidget { color: fg }` 强制成浅色，结果就是
        「暗色主题下设置菜单全白、看不见」。把整套主题色交给调色板后，
        任何未被 QSS 命名的控件都是「深底浅字」成对出现。
        """
        try:
            from ..theme import apply_app_palette
            apply_app_palette(CURRENT_THEME, self)
            w = getattr(self, "hw_panel", None)
            if w is not None:
                try:
                    from ..theme import build_palette
                    w.setPalette(build_palette(CURRENT_THEME))
                except Exception:
                    pass
        except Exception as ex:
            DIAG.report("_apply_app_palette", ex)

    def _apply_theme(self, theme, persist=True):
        """
        切换主题：立即生效并（可选）写入配置文件。

        生效路径：
          ① apply_theme_to_globals() 把颜色写回模块级常量
          ② _apply_global_stylesheet() 重新应用 QSS
          ③ 图表曲线/网格/背景按新配色重绘
        """
        global CURRENT_THEME
        try:
            CURRENT_THEME = theme
            apply_theme_to_globals(theme, globals())
            self._apply_global_stylesheet()
            # 图表：背景 + 网格 + 坐标轴文字
            try:
                self.plot_widget.setBackground(theme.chart_bg)
                # pyqtgraph 的 PlotWidget.setBackground 只改 PlotItem 的背景色，
                # ViewBox 自己还有一个背景 rect item，不改的话画布仍是旧色。
                vb = self.plot_widget.plotItem.vb
                try:
                    vb.setBackgroundColor(theme.chart_bg)
                except Exception:
                    pass
                try:
                    rect = vb.background
                    rect.setBrush(pg.mkBrush(theme.chart_bg))
                    rect.setPen(pg.mkPen(theme.chart_bg))
                except Exception:
                    pass
                for axis_name in ("left", "bottom"):
                    axis = self.plot_widget.getAxis(axis_name)
                    axis.setPen(pg.mkPen(theme.chart_text))
                    axis.setTextPen(pg.mkPen(theme.chart_text))
                self.plot_widget.showGrid(x=True, y=True, alpha=0.25,
                                         pen=pg.mkPen(theme.chart_grid, width=1))
            except Exception:
                pass
            self._restyle_chart()
            # 顶栏/横幅等用 setStyleSheet 单独设色的控件也要跟着变
            self._restyle_static_labels()
            # 【v1.0.0】主题下拉框跟随当前主题（见 _sync_theme_combo）
            self._sync_theme_combo(theme)
            if persist:
                try:
                    if self._ux_config is None:
                        self._ux_config = Config()
                    self._ux_config.set("theme", theme.key)
                    if theme.key == "custom" and theme.custom_bg:
                        self._ux_config.set("custom_bg", theme.custom_bg)
                    self._ux_config.save()
                except Exception:
                    pass
            return True
        except Exception as ex:
            DIAG.report("_apply_theme", ex)
            return False

    def _sync_theme_combo(self, theme):
        """
        让设置页的「主题」下拉框显示当前真正生效的主题。

        【为什么要有】主题不只会从下拉框变：点「应用」自定义背景色、
        预览渲染脚本、热键/测试直接调 `_apply_theme()` —— 这些路径都不触发
        `currentIndexChanged`，下拉框会残留在旧主题名上（页面已经是深色，
        设置页却写着「浅色（白底）」）。这里只做显示同步，blockSignals
        防止回环触发一次无意义的换肤。
        """
        try:
            combo = getattr(self, "combo_theme", None)
            if combo is None:
                return
            idx = combo.findData(getattr(theme, "key", ""))
            if idx >= 0 and idx != combo.currentIndex():
                combo.blockSignals(True)
                combo.setCurrentIndex(idx)
                combo.blockSignals(False)
        except Exception:
            pass

    def _on_theme_changed(self, key):
        """设置页的主题下拉框回调"""
        if key == "custom":
            color = QColorDialog.getColor(QColor(CURRENT_THEME.custom_bg or "#101018"),
                                          self, "选择自定义背景色")
            if not color.isValid():
                return
            self._apply_theme(theme_from_custom_bg(color.name(), "自定义 (%s)" % color.name()))
        else:
            theme = THEME_PRESETS.get(key)
            if theme is not None:
                self._apply_theme(theme.copy())

    def _on_chart_style_changed(self, key):
        """设置页的图表样式下拉框回调"""
        global CURRENT_CHART_STYLE
        if key not in (CHART_STYLE_CAPFRAMEX, CHART_STYLE_HUD):
            return
        CURRENT_CHART_STYLE = key
        self._restyle_chart()
        try:
            if self._ux_config is None:
                self._ux_config = Config()
            self._ux_config.set("chart_style", key)
            self._ux_config.save()
        except Exception:
            pass
        self._cfg_status("图表样式已切换为 %s" %
                         ("CapFrameX 风格" if key == CHART_STYLE_CAPFRAMEX else "赛博 HUD"))

    def _nav_button(self, text, tooltip=""):
        """侧边栏通用按钮（统一最小高度 + 指针光标，避免逐个手写）"""
        b = QPushButton(text)
        b.setObjectName("NavButton")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(30)
        if tooltip:
            b.setToolTip(tooltip)
        return b

    def _hdivider(self):
        """侧边栏分隔线（1px 细线，比 addSpacing 更有结构感）"""
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: %s; border: none;" % BORDER)
        return line

    def _build_events_tab(self):
        """
        ⚡ 事件流：引擎告警 + 手动打点的唯一历史视图（v1.0.0 保留并修好）。

        【v1.0.0】旧版这里用 `self._sync_table(...)` 写入表格，而该方法在
        模块拆分时**丢了定义** —— 面板一打开、硬件数据一到就每 tick 抛
        AttributeError。现在统一改走 `pages._set_cells`（差量写入 + 正确的
        ForegroundRole 处理：不带颜色的格子必须把前景数据清成 None，
        否则会退回 palette 灰字）。
        """
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self.lbl_events_hint = QLabel(
            "最近 25 条诱因事件与手动打点（时间 / 类型 / 摘要与建议）。"
            "手动打点＝左侧「★ 打点标记」按钮或全局热键。")
        self.lbl_events_hint.setWordWrap(True)
        lay.addWidget(self.lbl_events_hint)
        self.table_events = QTableWidget(0, 3)
        self.table_events.setHorizontalHeaderLabels(["时刻", "诱因类型", "事件摘要与诊断建议"])
        self.table_events.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_events.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_events.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table_events.verticalHeader().setVisible(False)
        self.table_events.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_events.setWordWrap(True)
        lay.addWidget(self.table_events)
        try:
            self._track_setting_label(self.lbl_events_hint, "dim_small")
        except Exception:
            pass
        return w

    def _localize_pg_menus(self):
        """
        递归遍历 ViewBox 右键菜单并翻译为中文（View All / X Axis / …）。

        pyqtgraph 0.13/0.14 的 ViewBox.getContextMenus(None) 返回的是
        **QAction 列表**（= self.menu.actions()），不是 QMenu 列表——
        需要先取 action 再进子菜单。
        """
        try:
            vb = self.plot_widget.getPlotItem().getViewBox()
            actions = list(vb.getContextMenus(None))
        except Exception:
            actions = []
        try:
            # 顶层菜单本身（ViewBoxMenu）也翻译一遍
            menu = getattr(vb, "menu", None)
            if menu is not None:
                self._zh_pg_menu_walk(menu, depth=0)
        except Exception:
            pass
        for act in actions:
            try:
                if act.isSeparator():
                    continue
                key = str(act.text()).replace("&", "").strip()
                zh = PG_MENU_ZH.get(key)
                if zh:
                    act.setText(zh)
                sub = act.menu()
                if sub is not None:
                    self._zh_pg_menu_walk(sub, depth=1)
            except Exception:
                continue

    def _zh_pg_menu_walk(self, menu, depth=0):
        if menu is None or depth > 6:
            return
        try:
            title = str(menu.title()).replace("&", "").strip()
            if title and title in PG_MENU_ZH:
                menu.setTitle(PG_MENU_ZH[title])
        except Exception:
            pass
        first_visit = id(menu) not in self._zh_pg_menus_done
        if first_visit:
            self._zh_pg_menus_done.add(id(menu))
        try:
            for act in menu.actions():
                if act.isSeparator():
                    continue
                key = str(act.text()).replace("&", "").strip()
                zh = PG_MENU_ZH.get(key)
                if zh:
                    act.setText(zh)
                # QWidgetAction（轴子菜单里的联动/范围/复选框面板）→ 翻译内部控件
                try:
                    if isinstance(act, QtWidgets.QWidgetAction):
                        w = act.defaultWidget()
                        if w is not None:
                            self._zh_pg_widget_texts(w)
                except Exception:
                    pass
                sub = act.menu()
                if sub is not None:
                    self._zh_pg_menu_walk(sub, depth + 1)
            # pyqtgraph 会在运行中动态改某些 Action 文本（如 Mouse Enabled/Disabled），
            # 因此每次菜单弹出前重新翻译一遍；只在首访时挂信号，避免重复连接堆积。
            if first_visit:
                menu.aboutToShow.connect(
                    lambda m=menu, d=depth: self._zh_pg_menu_walk(m, d))
        except Exception:
            pass

    def _zh_pg_widget_texts(self, root):
        """翻译菜单内嵌面板（QWidgetAction）里的标签/复选框/单选框文案"""
        try:
            targets = root.findChildren(QtWidgets.QLabel)
            targets += root.findChildren(QtWidgets.QCheckBox)
            targets += root.findChildren(QtWidgets.QRadioButton)
        except Exception:
            return
        for w in targets:
            try:
                key = str(w.text()).replace("&", "").strip()
                zh = PG_MENU_ZH.get(key)
                if zh:
                    w.setText(zh)
            except Exception:
                continue

    def _on_open_settings_panel(self):
        """⚙ 设置按钮：打开面板并直接定位到「设置」页"""
        try:
            self._place_child_window(self.hw_panel)
            self.hw_panel.show()
            self.hw_panel.raise_()
            self.hw_panel.activateWindow()
            self.hw_tabs.setCurrentWidget(self.tab_settings)
        except Exception as ex:
            DIAG.report("_on_open_settings_panel", ex)

    def _on_open_events_panel(self):
        """⚡ 事件流按钮：打开面板并直接定位到「事件流」页"""
        try:
            self._place_child_window(self.hw_panel)
            self.hw_panel.show()
            self.hw_panel.raise_()
            self.hw_panel.activateWindow()
            self.hw_tabs.setCurrentWidget(self.tab_events)
            self._refresh_events_tab()
        except Exception as ex:
            DIAG.report("_on_open_events_panel", ex)

    def _refresh_ui(self):
        """
        UI 刷新入口。

        【评审】渲染层的异常若直接抛出，会中断 Qt 事件循环 —— 界面直接卡死
        且没有任何提示。这里用一层守卫兜住：记录日志 + 计数，界面继续活着。
        """
        try:
            self._refresh_ui_inner()
        except Exception as ex:
            DIAG.report("StutterTriageWindow._refresh_ui", ex)
            self._ui_error_count = getattr(self, "_ui_error_count", 0) + 1
            if self._ui_error_count <= 3:
                try:
                    self.banner_text.setText("⚠ 界面渲染异常（已忽略并继续运行）：%s" % ex)
                except Exception:
                    pass

    def _set_style(self, widget, css):
        """样式去重：内容没变就不重设（setStyleSheet 会触发整棵子树重绘，很贵）"""
        if widget is None:
            return
        if self._style_cache.get(widget) == css:
            return
        self._style_cache[widget] = css
        widget.setStyleSheet(css)

    def _refresh_events_tab(self):
        """
        ⚡ 事件流页签：最近 25 条事件（差量写入，不做全表重建）。

        【v1.0.0】从前这里叫 `_refresh_hardware_tabs`，会把 5 个旧页签
        （CPU 热力图 / 显存 / 内存 / 着色器 / 事件流）全部刷一遍，其中三处
        调用了**不存在的** `_sync_table` —— 面板一打开就每 tick 抛
        AttributeError（被 _refresh_ui 的兜底吞掉，界面看起来"没反应"）。
        现在只刷事件表，且用 `pages._set_cells` 做正确的差量写入。
        """
        evs = list(self.store.events)[-25:]
        if not evs:
            return
        cells = []
        for ev in reversed(evs):
            if ev.severity == "DANGER":
                kc = BAD
            elif ev.severity == "WARN":
                kc = WARN
            else:
                kc = None
            cells.append([
                (time.strftime("%H:%M:%S", time.localtime(ev.ts)), None),
                (ev.kind, kc),
                ("%s —— %s" % (ev.text, ev.advice), None),
            ])
        try:
            _set_cells(self.table_events, cells)
        except Exception as ex:
            DIAG.report("_refresh_events_tab", ex)

    def _toggle_pause(self):
        """
        开始 / 暂停 / 继续 三态合一。

        - 未开始（刚启动）：点一下 = 开始采集；
        - 采集中：点一下 = 暂停（数据继续累积，只是不刷新界面）；
        - 已暂停：点一下 = 继续。
        """
        if not getattr(self, "_collect_started", True):
            self._begin_collection()
            return
        # 【防连点】一次物理点击在「窗口刚激活 / 触摸板双击」时可能被投递两次：
        # 开始→立刻暂停，界面停在「继续采集」，用户看到的就是「点了开始却没刷新」。
        now = time.time()
        if now - getattr(self, "_last_pause_ts", 0.0) < 0.35:
            return
        self._last_pause_ts = now
        self.is_paused = not self.is_paused
        self.btn_pause.setText("▶ 继续采集" if self.is_paused else "⏸ 暂停采集")

    def _begin_collection(self):
        """
        从「未开始 / 已暂停」进入采集。

        先把启动/暂停期间后台攒下的队列内容丢掉 —— 否则点「开始采集」的
        那一瞬间，曲线会突然从 0 跳到几十秒前，看起来像"又自己采了"。
        """
        self._collect_started = True
        self.is_paused = False
        # 与 _toggle_pause 的防连点配对：刚开始后的 350ms 内再点不当作「暂停」
        self._last_pause_ts = time.time()
        # 桥接可能因为「离线导入 / 清理」被停掉：点开始要能真的采起来
        try:
            if self.pm_bridge is None or not self.pm_bridge.is_alive():
                self._start_presentmon_bridge()
        except Exception:
            pass
        self._discard_pending()
        self._pending_status = (True, "采集已开始")
        try:
            self.btn_pause.setText("⏸ 暂停采集")
        except Exception:
            pass

    def _discard_pending(self):
        """丢弃队列里尚未搬运的数据（返回丢弃条数；队列空时返回 0）"""
        n = 0
        try:
            while True:
                self.q.get_nowait()
                n += 1
        except queue.Empty:
            pass
        return n

    def _drain_hardware_only(self):
        """
        未开始采集时的「心跳」：只把硬件快照搬进 store，帧 / 告警 / 状态全部丢弃。

        为什么：曲线未开始时必须为空（用户明确要求），但 CPU / 显卡 / 内存 / 网络
        是持续监控项 —— 全丢会让整屏「等数据…」看起来像软件卡死。硬件快照不进
        帧会话、不影响归因，所以单独放行。
        """
        n = 0
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "hw":
                    self.store.add_snapshot(payload)
                    n += 1
        except queue.Empty:
            pass
        except Exception:
            pass
        return n

    def _on_target_changed(self, text):
        """
        【评审 P1】切换追踪进程**不应重启 PresentMon**。

        原实现在这里 stop 旧桥接 + 起新子进程，但：
          · PresentMon 命令行**本来就没有**进程过滤（`<unknown>` 进程永远
            无法被 --process_name 命中），过滤本来就是采集后在 UI 侧做的；
          · 重启会杀掉子进程、重建 ETW 会话 —— 正是 error 1450 的高发路径；
          · 还会丢失数秒数据、时间轴重置。
        现在只切换展示目标；只有桥接线程真的死了才重启。
        """
        self.target_app = text
        self._auto_dom = None
        self.selected_frame = None
        self.locked_line.setVisible(False)
        self._reset_diagnosis_card()
        # 只有已经开始采集时才重启桥接（未开始时切换目标不能偷偷拉起采集）
        if getattr(self, "_collect_started", False) and (
                self.pm_bridge is None or not self.pm_bridge.is_alive()):
            self._start_presentmon_bridge()

    def _reset_diagnosis_card(self):
        """
        把病历卡复位成 idle 占位。

        【v1.0.0】以前这里是 `self.diff_drawer.hide()`：卡片一隐藏，
        下方折线图立刻长高 110px，用户点一下/切换目标就整页上下跳。
        新卡是**常驻占位**（高度恒定），所以这里优先调 show_idle()，
        只有拿到旧版/第三方抽屉时才退回 hide()。
        """
        try:
            self.diff_drawer.show_idle()
        except Exception:
            try:
                self.diff_drawer.hide()
            except Exception:
                pass

    def _toggle_simulation(self):
        """切到模拟流：不阻塞 UI（停止动作放到后台）"""
        if self.pm_bridge:
            old = self.pm_bridge
            threading.Thread(target=lambda: old.stop(wait=True, timeout=2.0),
                             name="BridgeStop", daemon=True).start()
        self.pm_bridge = PresentMonBridge(self.q)
        self.pm_bridge.exe_path = "__FORCE_SIMULATION__"
        self.pm_bridge.start()
        # 【手动开始采集】主动选模拟流 = 立刻想看数据：顺手进入采集态
        try:
            self._begin_collection()
            self._pending_status = (True, "已切到模拟数据流")
        except Exception:
            pass

    def _clean_etw_confirmed(self):
        """
        【UX 新增】「清理 ETW 残留」的**二次确认**包装。

        为什么要确认：logman stop 会真的停掉 ETW trace session，
        如果用户以为只是「看一眼」，误点一下就会打断正在进行的采集。
        确认框里明确说明影响范围，并说明当前会话会被跳过。
        （_clean_etw 本体保持不变 —— 它已排除当前正在使用的会话名）
        """
        try:
            live = self.pm_bridge.etw.session_name if self.pm_bridge else None
            stale = [n for n in EtwSessionManager.list_sessions()
                     if n.lower().startswith(PM_SESSION_PREFIX.lower())
                     and n != live]
            if not stale:
                QMessageBox.information(
                    self, "ETW 会话清理",
                    "当前没有需要清理的残留 StutterTriage ETW 会话。\n\n"
                    "（本次操作不会影响正在进行的采集。）")
                return
            txt = ["即将停止 %d 个残留的 StutterTriage ETW 会话：" % len(stale)]
            txt.extend("  - " + n for n in stale[:10])
            if len(stale) > 10:
                txt.append("  ... 共 %d 个" % len(stale))
            txt.append("")
            if live:
                txt.append("当前正在使用的会话 %s 会被自动跳过，不受影响。" % live)
            txt.append("停掉这些会话不会影响其它软件的 ETW 会话。")
            r = QMessageBox.question(
                self, "确认清理 ETW 残留", "\n".join(txt),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        except Exception:
            pass
        self._clean_etw()

    def _clean_etw(self):
        """
        清理残留 ETW 会话。

        【评审】必须排除**当前正在使用的会话**，否则用户点一下
        就会把自己的采集干掉（帧流立刻断）。
        """
        live = self.pm_bridge.etw.session_name if self.pm_bridge else None
        mgr = EtwSessionManager()
        stopped = mgr.cleanup_stale(skip=live)
        msg = ("已清理 %d 个残留 ETW 会话" % len(stopped)) if stopped else "当前没有残留的 StutterTriage ETW 会话"
        QMessageBox.information(self, "ETW 会话清理", msg)

    def _import_csv(self):
        """
        离线回放：兼容 PresentMon / CapFrameX 全部 metric 模式（按列名解析）。

        【评审 P1】三点必须保证：
        ① **先解析到临时结构，校验成功后再原子替换** —— 原实现一上来就
           `self.store.frames.clear()`，文件损坏时会把当前会话数据全丢了。
        ② **导入期间必须停止实时采集** —— 否则实时帧会继续追加进同一个
           store，且实时帧的 t 从 0 开始，图线会彻底乱掉。
        ③ 帧时间必须做有限值校验（NaN/inf/非正一律丢弃）。
        """
        path, _ = QFileDialog.getOpenFileName(self, "选择 CapFrameX / PresentMon CSV 文件", "", "CSV Files (*.csv)")
        if not path:
            return
        try:
            # 【UX 新增】大 CSV 的「读取 + 解析」整体放到后台线程，
            # 否则 csv.DictReader 在几十万行文件上会把界面彻底冻住
            # （Windows 会画「未响应」灰条，甚至直接杀掉进程）。
            _bg = self._wait_csv_background(path)
            if _bg.error:
                QMessageBox.critical(self, "读取失败", "读取 CSV 出错: %s" % _bg.error)
                return
            with open(path, "r", encoding="utf-8-sig", newline="", errors="replace") as f:
                reader = csv.DictReader(f)
                fields = _bg.fields or (reader.fieldnames or [])
                if not fields:
                    QMessageBox.warning(self, "格式错误", "CSV 为空或无法解析表头。")
                    return
                fmap = {c.strip(): i for i, c in enumerate(fields)}

                def pick(*names):
                    for n in names:
                        if n in fmap:
                            return n
                    return None

                ft_col = pick("MsBetweenPresents", "msBetweenPresents", "FrameTime")
                if ft_col is None:
                    QMessageBox.warning(self, "格式错误",
                                        "未找到帧时间列（MsBetweenPresents / msBetweenPresents / FrameTime）。")
                    return
                gpu_col = pick("MsGPUBusy", "msGPUActive", "GPUBusy")
                cpu_col = pick("MsCPUBusy", "CPUBusy")
                app_col = pick("Application")
                pid_col = pick("ProcessID")
                # 【实测】时间列单位随 metric 模式变化：
                #   默认模式 TimeInSeconds = **毫秒**；V1 模式 TimeInSeconds = **秒**；
                #   TimeInMs / CPUStartTimeInMs = 毫秒；CPUStartTime = 秒。
                # 因此这里记录列名，稍后用「与帧间隔的比值」自动判定单位。
                time_col = pick("TimeInSeconds", "TimeInMs", "CPUStartTime",
                                "CPUStartTimeInMs", "CPUStartQPCTimeInMs", "CPUStartQPCTime", "QPCTime")

                rows = _bg.rows

            # ---- 自动判定时间列单位：取中位差分，与帧间隔中位数比对 ----
            time_scale = None      # 乘到原始值上得到「秒」
            if time_col:
                diffs = []
                ft_vals = []
                for i in range(1, min(len(rows), 400)):
                    a = rows[i].get(time_col)
                    b = rows[i - 1].get(time_col)
                    if a is None or b is None or is_na(a) or is_na(b):
                        continue
                    try:
                        d = abs(float(a) - float(b))
                    except Exception:
                        continue
                    if d <= 0:
                        continue
                    diffs.append(d)
                    raw = rows[i].get(ft_col)
                    if raw is not None and not is_na(raw):
                        try:
                            ft_vals.append(float(raw))
                        except Exception:
                            pass
                if diffs and ft_vals:
                    md = sorted(diffs)[len(diffs) // 2]
                    mf = sorted(ft_vals)[len(ft_vals) // 2]
                    if mf > 0:
                        ratio = md / mf
                        if 0.5 < ratio < 2.0:
                            time_scale = 1.0 / 1000.0     # 原始值是毫秒
                        elif 0.0002 < ratio < 0.002:
                            time_scale = 1.0               # 原始值是秒
                        else:
                            time_scale = None
                if time_scale is None:
                    # 无法判定 → 退化为「按帧间隔累加」，保证时间轴单调且尺度正确
                    time_col = None

            # ---- 解析到临时列表（失败不会破坏现有会话数据）----
            parsed = []
            idx = 0
            acc_t = 0.0
            base_wall = time.time()
            for r in rows:
                try:
                    raw = r.get(ft_col)
                    if raw is None or is_na(raw):
                        continue
                    ft = float(raw)
                    # 有限值校验：NaN / inf / 非正值一律丢弃
                    if not math.isfinite(ft) or ft <= 0.0:
                        continue
                    if time_col:
                        tv = r.get(time_col)
                        if tv is not None and not is_na(tv):
                            try:
                                tvf = float(tv)
                                t = tvf * time_scale if math.isfinite(tvf) else acc_t
                            except Exception:
                                t = acc_t
                        else:
                            t = acc_t
                    else:
                        t = acc_t
                    acc_t += ft / 1000.0
                    gpu = safe_float(r.get(gpu_col), 0.0) if gpu_col else 0.0
                    cpu = safe_float(r.get(cpu_col), 0.0) if cpu_col else 0.0
                    app = (r.get(app_col) or "Imported.exe").strip() if app_col else "Imported.exe"
                    pv = r.get(pid_col) if pid_col else None
                    pid = int(pv) if (pv or "").isdigit() else 0
                    idx += 1
                    # 离线数据的 wall_ts 用「导入时刻 + 相对时间」表示，
                    # 因此硬件快照无法历史对齐 —— 由诊断卡显式标注「不可判定」。
                    rec = FrameRecord(idx, t, base_wall + t, app, pid, ft, gpu, cpu, hw_seq=-1)
                    parsed.append(rec)
                except Exception:
                    continue

            # ---- 校验通过 → 原子替换 ----
            if not parsed:
                QMessageBox.warning(self, "导入失败",
                                    "未解析出任何有效帧。\n（当前会话数据已保留，未做任何改动。）")
                return

            # 导入期间必须停止实时采集，否则实时帧会混入同一批数据
            if self.pm_bridge:
                old = self.pm_bridge
                threading.Thread(target=lambda: old.stop(wait=True, timeout=2.0),
                                 name="BridgeStopImport", daemon=True).start()
                self.pm_bridge = None
            # 丢弃队列里残留的实时帧，避免与导入帧混杂
            drained = 0
            try:
                while True:
                    kind, _payload = self.q.get_nowait()
                    if kind == "frame":
                        drained += 1
            except Exception:
                pass

            self.store.frames.clear()
            self.store.baseline.reset()
            for rec in parsed:
                self.store.frames.append(rec)

            # 导入新数据集：清掉旧会话的选中态
            self.selected_frame = None
            self.locked_line.setVisible(False)
            self._reset_diagnosis_card()

            # 【UX 新增】离线导入 → 数据源标记为 offline-import
            self._offline_mode = True
            self.lbl_engine_badge.setText("● 离线回放: 已加载 %d 帧 [%s]"
                                          % (len(parsed), os.path.basename(path)))
            self.lbl_engine_badge.setStyleSheet("color: %s; font-size: 11px; font-weight: bold;" % ACCENT)
            QMessageBox.information(self, "导入成功",
                                    "已加载 %d 帧离线数据。\n\n注意：\n"
                                    "· 离线 CSV 没有历史硬件快照，因此诊断卡的"
                                    "「CPU / 显存 / 内存 / 着色器」四行会显示为不可判定，"
                                    "但「帧表现」与「GPU 忙时比」仍可正常对账。\n"
                                    "· 实时采集已停止（已丢弃 %d 帧残留实时帧）。"
                                    % (len(parsed), drained))
        except Exception as ex:
            QMessageBox.critical(self, "读取失败", "导入 CSV 出错: %s" % ex)
        finally:
            # 【D1】无论导入成功或失败，都必须恢复运行状态，
            # 否则 is_paused 永久为 True 会让整个界面彻底冻结。
            self.is_paused = False
            # 未开始采集时导入 CSV：按钮仍是「▶ 开始采集」（不能假称正在采集）
            self.btn_pause.setText("⏸ 暂停采集" if getattr(self, "_collect_started", True)
                                   else "▶ 开始采集")

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出当前帧与诊断数据", "stuttertriage_record.csv", "CSV Files (*.csv)")
        if not path:
            return
        try:
            frames = list(self.store.frames)
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                # 【UX 新增】DataSource 列：杜绝把模拟/离线数据误当真实采集
                w.writerow(["Index", "TimeInSeconds", "Application", "MsBetweenPresents", "FPS",
                            "MsGPUBusy", "MsCPUBusy", "GPUBusyRatio", "CPU0_Usage",
                            "VRAM_NonLocal_MB", "PageIn_MBps", "CacheNewFiles",
                            "DataSource"])
                for fr in frames:
                    hw = self.store.snapshot_for(fr.hw_seq)
                    w.writerow([
                        fr.idx, "%.4f" % fr.t, fr.app, "%.3f" % fr.frametime, "%.1f" % fr.fps,
                        "%.3f" % fr.gpu_busy, "%.3f" % fr.cpu_busy, "%.3f" % fr.gpu_busy_ratio,
                        "%.1f" % (hw.cpu0_usage if hw else 0.0),
                        "%.1f" % ((hw.vram_nonlocal / 1048576.0) if hw else 0.0),
                        "%.2f" % (hw.page_in if hw else 0.0),
                        (hw.cache_new_files if hw else 0),
                        self._data_source,
                    ])
            QMessageBox.information(self, "导出成功", "已成功导出 %d 帧对账数据！" % len(frames))
        except Exception as ex:
            QMessageBox.critical(self, "导出失败", "写入文件出错: %s" % ex)

    def _init_ux(self):
        """加载配置、注册全局热键、检测管理员权限、检查 PresentMon"""
        try:
            cfg = Config.load()
            self._ux_config = cfg
            cfg.apply_to_globals(globals())
            # 基线追踪器也用新阈值（已存在的实例要同步刷新）
            try:
                kw = cfg.baseline_kwargs()
                bl = getattr(self.store, "baseline", None)
                if bl is not None:
                    bl.ema = kw["ema"]
                    bl.window = kw["window"]
                    bl.spike_ratio = kw["spike_ratio"]
                    bl.max_spike_frac = kw["max_spike_frac"]
            except Exception:
                pass
            if not cfg.existed:
                # 首次运行：生成配置模板，方便用户直接改
                try:
                    cfg.save()
                except Exception:
                    pass
        except Exception:
            self._ux_config = Config()

        # 主题 / 图表样式（从配置恢复）
        try:
            tkey = cfg.get("theme", "deepspace") if self._ux_config else "deepspace"
            if tkey == "custom":
                cbg = cfg.get("custom_bg", "") if self._ux_config else ""
                theme = theme_from_custom_bg(cbg) if cbg else THEME_DEEP_SPACE.copy()
            else:
                theme = THEME_PRESETS.get(tkey, THEME_DEEP_SPACE).copy()
            # 先静默应用（不写回配置，避免每次启动都改文件）
            self._apply_theme(theme, persist=False)
            try:
                idx = self.combo_theme.findData(theme.key)
                if idx >= 0:
                    self.combo_theme.blockSignals(True)
                    self.combo_theme.setCurrentIndex(idx)
                    self.combo_theme.blockSignals(False)
            except Exception:
                pass
        except Exception as ex:
            DIAG.report("load_theme", ex)

        try:
            cstyle = cfg.get("chart_style", "capframex") if self._ux_config else "capframex"
            if cstyle in (CHART_STYLE_CAPFRAMEX, CHART_STYLE_HUD):
                global CURRENT_CHART_STYLE
                CURRENT_CHART_STYLE = cstyle
                self._restyle_chart()
                try:
                    idx = self.combo_chart_style.findData(cstyle)
                    if idx >= 0:
                        self.combo_chart_style.blockSignals(True)
                        self.combo_chart_style.setCurrentIndex(idx)
                        self.combo_chart_style.blockSignals(False)
                except Exception:
                    pass
        except Exception as ex:
            DIAG.report("load_chart_style", ex)

        # 全局热键（失败也不影响主流程）
        try:
            hk = cfg.get("hotkey", "Ctrl+F9") if self._ux_config else "Ctrl+F9"
            self._hotkey = GlobalHotkeyManager(self._on_manual_mark, hk)
            self._hotkey.register(app=QtWidgets.QApplication.instance())
        except Exception:
            self._hotkey = None
        self._update_hotkey_label()
        # 设置页里的可视化改键框同步当前生效值
        try:
            if self._hotkey is not None and self._hotkey.hotkey_text:
                self.edit_hotkey.blockSignals(True)
                self.edit_hotkey.setKeySequence(QKeySequence(self._hotkey.hotkey_text))
                self.edit_hotkey.blockSignals(False)
        except Exception:
            pass

        # 管理员权限（不再放顶栏：启动后弹一次温和提醒，不强制提权）
        self._schedule_admin_prompt()

        # PresentMon 缺失指引
        self._update_presentmon_hint()

    def _update_hotkey_label(self):
        try:
            if self._hotkey is None:
                self.lbl_hotkey.setText("全局热键: 不可用（请用 ★ 打点标记 按钮）")
                self.lbl_hotkey.setStyleSheet("color: %s; font-size: 10px;" % WARN)
                return
            if self._hotkey.available:
                self.lbl_hotkey.setText("全局热键: %s 打点标记" % self._hotkey.hotkey_text)
                self.lbl_hotkey.setStyleSheet("color: %s; font-size: 10px;" % GREEN)
            else:
                self.lbl_hotkey.setText("全局热键不可用（%s）· 请用 ★ 打点标记 按钮"
                                        % (self._hotkey.error or "未知原因"))
                self.lbl_hotkey.setStyleSheet("color: %s; font-size: 10px;" % WARN)
        except Exception:
            pass

    def _schedule_admin_prompt(self):
        """启动后延迟 700ms 弹一次管理员提醒（非阻塞，不打断采集引擎启动）"""
        try:
            if is_admin():
                return                      # 已是管理员：无需打扰
            QTimer.singleShot(700, self._show_admin_prompt)
        except Exception:
            pass

    def _show_admin_prompt(self):
        """
        启动提醒：建议以管理员身份运行。

        【设计】非模态（不阻塞事件循环）：
          · 采集线程 / 30 FPS 刷新照常运行；
          · 用户可以先不管它，继续看曲线。

        【v4.3 修 Bug：关不掉 + 残留在屏幕上】
        原来用 `QDialog.open()` —— Qt 会把对话框变成 **WindowModal**，
        于是主窗口的关闭（WM_CLOSE）被模态过滤器吞掉：用户点右上角 X 没反应，
        只能去任务管理器强杀；强杀后这个对话框还留在屏幕上（"跑到屏幕上方"），
        PresentMon 子进程也跟着变孤儿。
        现在改成：显式 NonModal + `show()`（普通显示，不接管模态），
        并且 `_closing` 时直接不再弹。
        """
        if getattr(self, "_closing", False):
            return
        if getattr(self, "_admin_prompt_shown", True):
            return
        self._admin_prompt_shown = True
        try:
            box = QMessageBox(self)
            box.setWindowTitle("StutterTriage · 权限提醒")
            box.setIcon(QMessageBox.Icon.Information)
            box.setText("建议以管理员身份运行 StutterTriage")
            box.setInformativeText(
                "非管理员模式下：\n"
                "  · 以提权方式运行的游戏进程无法被追踪（显示为 <unknown>）\n"
                "  · ETW 采集在高负载时可能丢包，曲线出现空洞\n\n"
                "当前权限仍可正常使用：模拟流 / CSV 回放 / 普通权限游戏都能工作。")
            btn_elev = box.addButton("🛡 以管理员身份重启",
                                     QMessageBox.ButtonRole.AcceptRole)
            btn_go = box.addButton("继续以当前权限", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(btn_go)
            box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            box.buttonClicked.connect(
                lambda b, e=btn_elev: self._on_admin_prompt_choice(b is e))
            # 关键：必须显式 NonModal + show()。open() 会强制 WindowModal。
            box.setWindowModality(Qt.WindowModality.NonModal)
            box.show()
            self._admin_dialog = box
            self._place_child_window(box)
        except Exception as ex:
            DIAG.report("_show_admin_prompt", ex)

    def _place_child_window(self, w, max_size=None):
        """
        把独立子窗口（权限提醒 / 进阶硬件面板）摆到主窗口上方居中 —— **只摆一次**。

        【为什么】这些窗口是 `Qt.WindowType.Window` 的**顶层窗口**，
        不指定位置时 Windows 会把它们甩到屏幕左上角 —— 看起来像"跑到屏幕上方"。
        用户手动挪过之后就不再干预（用 id() 记忆，避免每次 show 都拉回来）。
        """
        try:
            if w is None or not w.isWindow():
                return
            key = id(w)
            if key in self._placed_once:
                return
            if max_size:
                w.resize(min(w.width(), max_size[0]), min(w.height(), max_size[1]))
            scr = QtWidgets.QApplication.primaryScreen()
            avail = scr.availableGeometry() if scr is not None else None
            sz = w.size()
            if avail is not None:
                if sz.width() > avail.width() or sz.height() > avail.height():
                    w.resize(min(sz.width(), avail.width() - 40),
                             min(sz.height(), avail.height() - 40))
                    sz = w.size()
            g = self.frameGeometry()
            x = g.center().x() - sz.width() // 2
            y = g.top() + max(40, (g.height() - sz.height()) // 3)
            if avail is not None:
                x = max(avail.left() + 8, min(x, avail.right() - sz.width() - 8))
                y = max(avail.top() + 8, min(y, avail.bottom() - sz.height() - 8))
            w.move(x, y)
            self._placed_once.add(key)
        except Exception:
            pass

    def _close_other_windows(self):
        """
        关闭除主窗口外的所有顶层窗口（权限提醒 / 11 项专业表格 / 进阶硬件面板）。

        【v4.3 修 Bug】任何一个还可见的顶层窗口都会让 Qt 的
        「最后一个窗口关闭才退出」不成立 → 进程留在后台、窗口飘在屏幕上。
        """
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        for w in list(app.topLevelWidgets()):
            try:
                if w is None or w is self:
                    continue
                if not w.isWindow():
                    continue
                # 只 hide+close：带 WA_DeleteOnClose 的对话框会自己删除，
                # 这里再 deleteLater() 就成了重复删除（PyQt6 在解释器收尾时
                # 会因此 fastfail 0xC0000409）。close() 对普通窗口等于隐藏。
                w.hide()
                w.close()
            except Exception:
                pass
        self._admin_dialog = None

    def _on_admin_prompt_choice(self, elevate):
        """启动提醒里点「以管理员身份重启」→ 用户显式动作才提权（非强制）"""
        if not elevate:
            return
        try:
            ok = relaunch_as_admin()
            if ok:
                try:
                    self.statusBar().showMessage("正在以管理员身份重启…", 5000)
                except Exception:
                    pass
                self.close()
            else:
                QMessageBox.information(
                    self, "提权失败",
                    "未能拉起管理员进程（用户可能取消了 UAC 弹窗）。\n"
                    "继续以当前权限运行也不影响基本功能。")
        except Exception as ex:
            DIAG.report("_on_admin_prompt_choice", ex)

    def _update_presentmon_hint(self):
        """PresentMon 缺失时给出明确获取指引"""
        try:
            found = None
            try:
                found = find_presentmon_binary()
            except Exception:
                found = None
            cfg_path = ""
            if self._ux_config is not None:
                cfg_path = self._ux_config.get("presentmon_path", "") or ""
            if cfg_path and os.path.isfile(cfg_path):
                self.lbl_pm_hint.setText("PresentMon: %s" % os.path.basename(cfg_path))
                self.lbl_pm_hint.setStyleSheet("color: %s; font-size: 10px;" % GREEN)
            elif found:
                self.lbl_pm_hint.setText("PresentMon: %s" % os.path.basename(found))
                self.lbl_pm_hint.setStyleSheet("color: %s; font-size: 10px;" % GREEN)
            else:
                self.lbl_pm_hint.setText("未找到 PresentMon.exe（模拟模式）—— ")
                self.lbl_pm_hint.setStyleSheet("color: %s; font-size: 10px;" % WARN)
        except Exception:
            pass

    def _on_manual_mark(self):
        """
        手动打点（全局热键与界面按钮共用同一条路径）。

        生成 Event(kind="手动标记", symbol="star") 投入队列 →
        UI 线程的 _drain_queue 会把它交给 store.add_event →
        图表上出现金色星形标记，事件流表格出现该行。
        """
        try:
            # 【手动开始采集】未开始时打点不成立：帧流还没进来，标记会挂在空时间轴上
            # （而且事件走队列、未开始时会被丢弃）→ 直接提示，不假装"已打点"。
            if not getattr(self, "_collect_started", True):
                self.statusBar().showMessage("尚未开始采集：请先点「▶ 开始采集」再打点", 4000)
                return
            self._mark_seq += 1
            now = time.time()
            ev = Event(now, "手动标记",
                       "手动打点 #%d（%s）" % (self._mark_seq,
                                             time.strftime("%H:%M:%S", time.localtime(now))),
                       advice="此处的卡顿/异常可与该时刻对齐排查",
                       severity="WARN", symbol="star")
            self.q.put(("event", ev))
            try:
                self.statusBar().showMessage(
                    "★ 已打点标记 #%d @ %s" % (self._mark_seq,
                                               time.strftime("%H:%M:%S", time.localtime(now))), 4000)
            except Exception:
                pass
        except Exception:
            pass

    def _update_data_source_banner(self):
        """判定数据来源并同步：状态标签 / 图表水印"""
        try:
            src = detect_data_source(self.pm_bridge, offline=self._offline_mode)
            self._data_source = src
            color = DATA_SOURCE_COLOR.get(src, DIM)
            self.lbl_data_source.setText(DATA_SOURCE_LABEL.get(src, src))
            self.lbl_data_source.setStyleSheet(
                "color: %s; font-size: 11px; font-weight: bold;" % color)
            # 水印：只在模拟模式下显示
            wm = getattr(self, "_sim_watermark", None)
            if wm is not None:
                wm.setVisible(src == DATA_SOURCE_SIM)
                if src == DATA_SOURCE_SIM:
                    self._position_watermark()
        except Exception:
            pass

    def _position_watermark(self):
        """把水印摆在视口中央（跟随视图范围变化）"""
        try:
            wm = getattr(self, "_sim_watermark", None)
            if wm is None:
                return
            r = self.plot_widget.getViewBox().viewRect()
            wm.setPos(r.center())
        except Exception:
            pass

    def _on_open_presentmon_dir(self):
        """打开 PresentMon 目录（资源管理器），并提示缺失时的获取指引"""
        try:
            if not open_dir_in_explorer(presentmon_dir()):
                QMessageBox.information(self, "PresentMon",
                                        "无法自动打开目录，请手动前往：\n%s" % presentmon_dir())
            else:
                try:
                    self.statusBar().showMessage(
                        "已打开 PresentMon 目录：%s" % presentmon_dir(), 5000)
                except Exception:
                    pass
        except Exception:
            pass

    def _track_setting_label(self, label, role):
        """登记设置页里带主题色的标签，并立即按当前主题上色"""
        try:
            self._settings_labels.append((label, role))
        except Exception:
            pass
        self._style_setting_label(label, role)
        return label

    def _style_setting_label(self, label, role):
        """按角色给设置页标签上色（accent=小节标题 / dim=说明 / dim_small=脚注）"""
        try:
            t = CURRENT_THEME
            if role == "accent":
                label.setStyleSheet(
                    "color: %s; font-size: 11px; font-weight: bold;" % t.accent)
            elif role == "dim_small":
                label.setStyleSheet("color: %s; font-size: 10px;" % t.dim)
            else:
                label.setStyleSheet("color: %s; font-size: 11px;" % t.dim)
        except Exception:
            pass

    def _restyle_settings_page(self):
        """
        设置页跟随主题重刷。

        【为什么必须存在】设置页标签在构建时把 DIM/ACCENT 写死进了 QSS，
        切换到浅色主题后这些深色时代的颜色会留在白底上：
        深色 DIM(#8b98ad) 在白底上对比度不足，看起来「字发虚、看不清」。
        """
        for lbl, role in getattr(self, "_settings_labels", []):
            self._style_setting_label(lbl, role)

    def _build_settings_tab(self):
        """
        设置页：外观（主题/背景/图表样式）/ 阈值 / PresentMon 路径 / 热键。

        放在 QScrollArea 里 —— 右下面板较窄时控件会被压到看不见，
        加滚动条后无论面板多窄都能操作。

        【v3.2 主题跟随】本页大量 QLabel 用 setStyleSheet 硬编码主题色，
        这些颜色在**构建时**就被固化，切换主题后会残留在新背景上
        （深色主题的灰蓝 DIM 放到白底上几乎看不见）。
        因此所有带色标签统一登记到 self._settings_labels，
        主题切换时由 _restyle_settings_page() 按当前主题重刷。
        """
        self._settings_labels = []      # [(QLabel, role)] —— 供主题重刷

        outer = QWidget()
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        info = QLabel("阈值参数来自配置文件 %s（修改后点「保存当前阈值」生效）。"
                      % CONFIG_FILENAME)
        self._track_setting_label(info, "dim")
        lay.addWidget(info)

        # --- 外观：主题 / 自定义背景色 / 图表样式 ---
        look_title = QLabel("外观（主题 · 背景色 · 图表样式）")
        self._track_setting_label(look_title, "accent")
        lay.addWidget(look_title)

        look_row = QHBoxLayout()
        look_row.addWidget(QLabel("主题:"))
        self.combo_theme = QComboBox()
        for key, th in THEME_PRESETS.items():
            self.combo_theme.addItem(th.name, key)
        self.combo_theme.addItem("自定义背景色…", "custom")
        try:
            cur_theme_key = CURRENT_THEME.key
            idx = self.combo_theme.findData(cur_theme_key)
            if idx >= 0:
                self.combo_theme.setCurrentIndex(idx)
        except Exception:
            pass
        self.combo_theme.currentIndexChanged.connect(self._on_theme_combo_changed)
        look_row.addWidget(self.combo_theme)

        look_row.addSpacing(10)
        look_row.addWidget(QLabel("图表样式:"))
        self.combo_chart_style = QComboBox()
        self.combo_chart_style.addItem("CapFrameX 风格（水平阈值线 · 无填充）", CHART_STYLE_CAPFRAMEX)
        self.combo_chart_style.addItem("赛博 HUD（渐变填充 · Low 包络线）", CHART_STYLE_HUD)
        try:
            idx = self.combo_chart_style.findData(CURRENT_CHART_STYLE)
            if idx >= 0:
                self.combo_chart_style.setCurrentIndex(idx)
        except Exception:
            pass
        self.combo_chart_style.currentIndexChanged.connect(self._on_chart_style_combo_changed)
        look_row.addWidget(self.combo_chart_style)
        look_row.addStretch()
        lay.addLayout(look_row)

        bg_row = QHBoxLayout()
        bg_row.addWidget(QLabel("自定义背景:"))
        self.edit_custom_bg = QLineEdit()
        self.edit_custom_bg.setPlaceholderText("#0a0e15 或 #ffffff")
        self.edit_custom_bg.setMaximumWidth(140)
        bg_row.addWidget(self.edit_custom_bg)
        self.btn_pick_bg = QPushButton("选择颜色…")
        self.btn_pick_bg.clicked.connect(self._on_pick_custom_bg)
        self.btn_apply_bg = QPushButton("应用")
        self.btn_apply_bg.clicked.connect(self._on_apply_custom_bg)
        bg_row.addWidget(self.btn_pick_bg)
        bg_row.addWidget(self.btn_apply_bg)
        bg_row.addStretch()
        lay.addLayout(bg_row)

        # --- 阈值编辑表单（v1.0.0：中文人话标签 + 按归因维度分组）---
        # 【为什么要重构】原先把原始键名当标签（spill_threshold / baseline_max_spike_frac
        # …）12 行英文标识符糊成一块，只有作者看得懂。现在：
        #   · 标签 = 「中文名（单位·默认）」；原始键名 + 说明进 tooltip；
        #   · 按 显存 / CPU / 着色器 / 采集 / 基线 分组，每组一行小节标题；
        #   · self.cfg_edits[key] 的映射与保存/恢复/重载逻辑完全不变。
        self.cfg_edits = {}
        grouped_keys = [k for _t, _d, ks in CONFIG_GROUPS for k in ks]
        for gname_title, gdesc, keys in CONFIG_GROUPS:
            gtitle = QLabel(gname_title)
            self._track_setting_label(gtitle, "accent")
            lay.addWidget(gtitle)
            if gdesc:
                gnote = QLabel(gdesc)
                self._track_setting_label(gnote, "dim_small")
                gnote.setWordWrap(True)
                lay.addWidget(gnote)
            form = QGridLayout()
            form.setSpacing(4)
            form.setColumnStretch(1, 0)
            form.setColumnStretch(2, 1)
            row = 0
            for key in keys:
                schema = CONFIG_SCHEMA.get(key)
                if schema is None:
                    continue
                gname, caster, desc = schema
                label_text, unit = CONFIG_LABELS.get(key, (key, ""))
                lbl = QLabel("%s%s" % (label_text, ("（%s）" % unit) if unit else ""))
                self._track_setting_label(lbl, "dim_small")
                lbl.setWordWrap(True)
                lbl.setToolTip("%s\n%s\n配置键：%s" % (label_text, desc, key))
                edit = QLineEdit()
                try:
                    cur = globals().get(gname)
                except Exception:
                    cur = None
                edit.setText("" if cur is None else str(cur))
                edit.setMaximumWidth(160)
                edit.setToolTip(lbl.toolTip())
                form.addWidget(lbl, row, 0)
                form.addWidget(edit, row, 1)
                form.setColumnStretch(2, 1)
                self.cfg_edits[key] = edit
                row += 1
            lay.addLayout(form)
        # 兜底：万一 CONFIG_SCHEMA 新增了键却忘了登记分组，也要能改（绝不静默丢设置项）
        leftovers = [k for k in CONFIG_SCHEMA.keys() if k not in grouped_keys]
        if leftovers:
            form = QGridLayout()
            form.setSpacing(4)
            row = 0
            for key in leftovers:
                gname, _caster, desc = CONFIG_SCHEMA[key]
                lbl = QLabel(key)
                self._track_setting_label(lbl, "dim_small")
                lbl.setToolTip(desc)
                edit = QLineEdit()
                try:
                    cur = globals().get(gname)
                except Exception:
                    cur = None
                edit.setText("" if cur is None else str(cur))
                edit.setMaximumWidth(160)
                form.addWidget(lbl, row, 0)
                form.addWidget(edit, row, 1)
                self.cfg_edits[key] = edit
                row += 1
            lay.addLayout(form)

        btns = QHBoxLayout()
        self.btn_cfg_save = QPushButton("保存当前阈值")
        self.btn_cfg_save.clicked.connect(self._on_save_thresholds)
        self.btn_cfg_reset = QPushButton("恢复默认")
        self.btn_cfg_reset.clicked.connect(self._on_reset_thresholds)
        self.btn_cfg_reload = QPushButton("重新载入配置文件")
        self.btn_cfg_reload.clicked.connect(self._on_reload_config)
        btns.addWidget(self.btn_cfg_save)
        btns.addWidget(self.btn_cfg_reset)
        btns.addWidget(self.btn_cfg_reload)
        btns.addStretch()
        lay.addLayout(btns)

        # --- PresentMon 路径 ---
        lay.addSpacing(6)
        pm_title = QLabel("PresentMon.exe 路径（手动指定，留空则自动查找）")
        self._track_setting_label(pm_title, "accent")
        lay.addWidget(pm_title)
        pm_row = QHBoxLayout()
        self.edit_pm_path = QLineEdit()
        self.edit_pm_path.setPlaceholderText("例如: C:\Tools\PresentMon.exe")
        self.btn_pm_browse = QPushButton("浏览…")
        self.btn_pm_browse.clicked.connect(self._on_browse_presentmon)
        self.btn_pm_open_dir = QPushButton("打开目录")
        self.btn_pm_open_dir.clicked.connect(self._on_open_presentmon_dir)
        pm_row.addWidget(self.edit_pm_path, 1)
        pm_row.addWidget(self.btn_pm_browse)
        pm_row.addWidget(self.btn_pm_open_dir)
        lay.addLayout(pm_row)

        pm_hint = QLabel(presentmon_missing_hint())
        self._track_setting_label(pm_hint, "dim_small")
        pm_hint.setWordWrap(True)
        lay.addWidget(pm_hint)

        # --- 全局热键（可视化改键） ---
        lay.addSpacing(6)
        hk_title = QLabel("全局热键「打点标记」（点击输入框后直接按下想要的组合键）")
        self._track_setting_label(hk_title, "accent")
        lay.addWidget(hk_title)
        hk_row = QHBoxLayout()
        self.edit_hotkey = QKeySequenceEdit()
        self.edit_hotkey.setMaximumWidth(200)
        try:
            self.edit_hotkey.setKeySequence(QKeySequence(self._current_hotkey_text()))
        except Exception:
            pass
        self.edit_hotkey.editingFinished.connect(self._on_hotkey_sequence_changed)
        self.edit_hotkey.keySequenceChanged.connect(self._on_hotkey_sequence_changed)
        hk_row.addWidget(self.edit_hotkey)
        self.btn_hk_apply = QPushButton("立即应用")
        self.btn_hk_apply.clicked.connect(self._on_apply_hotkey_sequence)
        self.btn_hk_reset = QPushButton("恢复默认")
        self.btn_hk_reset.clicked.connect(self._on_reset_hotkey)
        hk_row.addWidget(self.btn_hk_apply)
        hk_row.addWidget(self.btn_hk_reset)
        hk_row.addStretch()
        lay.addLayout(hk_row)

        self.lbl_hotkey_tip = QLabel(
            "提示：直接点击上方输入框，按下你想要的组合键（支持 Ctrl / Alt / Shift / Win + 任意键）。"
            "热键在游戏全屏时也能触发，用于随手标记卡顿瞬间。")
        self._track_setting_label(self.lbl_hotkey_tip, "dim_small")
        self.lbl_hotkey_tip.setWordWrap(True)
        lay.addWidget(self.lbl_hotkey_tip)

        self.lbl_settings_status = QLabel("")
        self._track_setting_label(self.lbl_settings_status, "dim_small")
        lay.addWidget(self.lbl_settings_status)

        lay.addStretch()

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setWidget(w)
        # 【v4.2 修 Bug】视口**不要**自己刷底色：它默认会画 Fusion 调色板的浅灰，
        # 在暗色主题下整页变白底、文字看不清。关掉 autoFillBackground，
        # 让外层 Tab 面板的主题底色透上来（QSS 里另有同一条兜底规则）。
        try:
            vp = scroll.viewport()
            vp.setObjectName("qt_scrollarea_viewport")
            vp.setAutoFillBackground(False)
        except Exception:
            pass
        outer_lay.addWidget(scroll)
        return outer

    def _on_theme_combo_changed(self, index):
        try:
            key = self.combo_theme.itemData(index)
        except Exception:
            return
        if not key or key == getattr(self, "_suppress_theme_event", None):
            return
        self._on_theme_changed(key)

    def _on_chart_style_combo_changed(self, index):
        try:
            key = self.combo_chart_style.itemData(index)
        except Exception:
            return
        if not key:
            return
        self._on_chart_style_changed(key)

    def _on_pick_custom_bg(self):
        color = QColorDialog.getColor(
            QColor(self.edit_custom_bg.text().strip() or (CURRENT_THEME.custom_bg or "#101018")),
            self, "选择自定义背景色")
        if color.isValid():
            self.edit_custom_bg.setText(color.name())

    def _on_apply_custom_bg(self):
        raw = self.edit_custom_bg.text().strip()
        if not raw:
            self._cfg_status("请先填写颜色值，例如 #0a0e15", ok=False)
            return
        c = QColor(raw)
        if not c.isValid():
            self._cfg_status("颜色值无效：%s" % raw, ok=False)
            return
        self.edit_custom_bg.setText(c.name())
        theme = theme_from_custom_bg(c.name(), "自定义 (%s)" % c.name())
        self._apply_theme(theme)
        self._cfg_status("已应用自定义背景色 %s" % c.name())

    def _current_hotkey_text(self):
        """当前生效的热键文本（优先取运行中的管理器，其次取配置）"""
        try:
            if getattr(self, "_hotkey", None) is not None and self._hotkey.hotkey_text:
                return self._hotkey.hotkey_text
        except Exception:
            pass
        try:
            if self._ux_config is not None:
                return str(self._ux_config.data.get("hotkey") or "Ctrl+F9")
        except Exception:
            pass
        return "Ctrl+F9"

    def _on_hotkey_sequence_changed(self):
        """QKeySequenceEdit 内容变化 → 立即尝试注册"""
        try:
            seq = self.edit_hotkey.keySequence()
            text = seq.toString()
            if not text:
                return
            # 规范化：Qt 产出 "Ctrl+F9" 之类；有些版本会产出 "Ctrl+F9" 带空格
            text = text.replace(" ", "")
            if parse_hotkey(text) is None:
                self._cfg_status("无法识别的组合键：%s" % text, ok=False)
                return
            self.edit_hotkey.setKeySequence(QKeySequence(text))
            self._on_apply_hotkey_sequence()
        except Exception as ex:
            DIAG.report("_on_hotkey_sequence_changed", ex)

    def _on_apply_hotkey_sequence(self):
        """把可视化改键的结果应用到运行中的全局热键"""
        try:
            text = self.edit_hotkey.keySequence().toString().replace(" ", "")
            if not text:
                return
            if parse_hotkey(text) is None:
                self._cfg_status("无法识别的组合键：%s" % text, ok=False)
                return
            # 重新注册（失败会自动回退到旧热键）
            if self._hotkey is not None:
                ok = self._hotkey.register(QtWidgets.QApplication.instance(), text)
                if ok:
                    self._cfg_status("热键已更新为 %s" % text)
                    self.lbl_hotkey.setText("全局热键: %s 打点标记" % self._hotkey.hotkey_text)
                    try:
                        if self._ux_config is None:
                            self._ux_config = Config()
                        self._ux_config.set("hotkey", text)
                        self._ux_config.save()
                    except Exception:
                        pass
                else:
                    self._cfg_status("热键注册失败：%s（可能已被其它程序占用）"
                                     % (self._hotkey.error or text), ok=False)
        except Exception as ex:
            DIAG.report("_on_apply_hotkey_sequence", ex)

    def _on_reset_hotkey(self):
        default = "Ctrl+F9"
        self.edit_hotkey.setKeySequence(QKeySequence(default))
        self._on_apply_hotkey_sequence()

    def _cfg_status(self, msg, ok=True):
        try:
            self.lbl_settings_status.setText(msg)
            self.lbl_settings_status.setStyleSheet(
                "color: %s; font-size: 10px;" % (GREEN if ok else WARN))
        except Exception:
            pass

    def _on_save_thresholds(self):
        """把设置页里的阈值写回配置并立即生效"""
        try:
            if self._ux_config is None:
                self._ux_config = Config()
            for key, edit in self.cfg_edits.items():
                gname, caster, _d = CONFIG_SCHEMA[key]
                raw = edit.text().strip()
                if raw == "":
                    continue
                self._ux_config.set(key, _coerce(raw, caster, DEFAULT_CONFIG.get(key)))
            ok = self._ux_config.save()
            if ok:
                # 【v1.0.0 修 Bug】原来写的是 apply_to_globals(globals()) —— 只把值写回
                # **本模块自己**的常量副本，而真正参与判定的 consts / engines / analysis /
                # pages 四份副本一动不动：界面提示「已保存」，实际阈值要重启才生效。
                # 不带参数即广播到所有已登记模块（consts.apply_values）。
                self._ux_config.apply_to_globals()
                self._apply_runtime_thresholds()
                self._cfg_status("已保存到 %s" % os.path.basename(self._ux_config.path))
            else:
                self._cfg_status("保存失败（文件被占用或无权限）", ok=False)
        except Exception as ex:
            self._cfg_status("保存出错: %s" % ex, ok=False)

    def _on_reset_thresholds(self):
        """恢复默认阈值（先广播再回填输入框，保证框里显示的就是生效值）"""
        try:
            if self._ux_config is None:
                self._ux_config = Config()
            self._ux_config.reset_defaults()
            self._ux_config.apply_to_globals()          # v1.0.0：广播到全部模块
            self._apply_runtime_thresholds()
            for key, edit in self.cfg_edits.items():
                gname, _c, _d = CONFIG_SCHEMA[key]
                try:
                    edit.setText(str(globals().get(gname)))
                except Exception:
                    pass
            self._cfg_status("已恢复默认阈值（尚未落盘，点「保存当前阈值」写入文件）")
        except Exception as ex:
            self._cfg_status("恢复默认出错: %s" % ex, ok=False)

    def _on_reload_config(self):
        """从磁盘重新载入配置文件（v1.0.0：同样改成广播，否则等于没重载）"""
        try:
            cfg = Config.load()
            self._ux_config = cfg
            cfg.apply_to_globals()
            self._apply_runtime_thresholds()
            for key, edit in self.cfg_edits.items():
                gname, _c, _d = CONFIG_SCHEMA[key]
                try:
                    edit.setText(str(globals().get(gname)))
                except Exception:
                    pass
            self._cfg_status("已重新载入 %s" % os.path.basename(cfg.path))
        except Exception as ex:
            self._cfg_status("载入失败: %s" % ex, ok=False)

    def _apply_runtime_thresholds(self):
        """把新阈值同步到**已经存在**的运行时对象（基线追踪器等）"""
        try:
            if self._ux_config is None:
                return
            kw = self._ux_config.baseline_kwargs()
            bl = getattr(self.store, "baseline", None)
            if bl is not None:
                bl.ema = kw["ema"]
                bl.window = kw["window"]
                bl.spike_ratio = kw["spike_ratio"]
                bl.max_spike_frac = kw["max_spike_frac"]
        except Exception:
            pass

    def _on_browse_presentmon(self):
        try:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择 PresentMon.exe", presentmon_dir(), "可执行文件 (*.exe)")
            if not path:
                return
            self.edit_pm_path.setText(path)
            self._apply_presentmon_path(path)
        except Exception:
            pass

    def _apply_presentmon_path(self, path):
        """把手动指定的 PresentMon 路径写入配置并尝试重启采集"""
        try:
            if self._ux_config is None:
                self._ux_config = Config()
            self._ux_config.set("presentmon_path", path or "")
            self._ux_config.save()
            self.edit_pm_path.setText(path or "")
            self._update_presentmon_hint()
            if path and os.path.isfile(path):
                # 用新路径重启采集桥接 —— 但只在已开始采集时；否则等「开始采集」
                if not getattr(self, "_collect_started", False):
                    self._cfg_status("已保存 PresentMon 路径：%s（点「开始采集」后生效）" % path)
                    return
                try:
                    if self.pm_bridge is not None:
                        old = self.pm_bridge
                        threading.Thread(target=lambda: old.stop(wait=True, timeout=2.0),
                                         name="BridgeStopPM", daemon=True).start()
                    self.pm_bridge = PresentMonBridge(self.q, exe_path=path)
                    self.pm_bridge.start()
                    self._cfg_status("已应用 PresentMon 路径：%s" % path)
                except Exception as ex:
                    self._cfg_status("重启采集失败: %s" % ex, ok=False)
            else:
                self._cfg_status("路径无效：%s" % path, ok=False)
        except Exception as ex:
            self._cfg_status("应用路径出错: %s" % ex, ok=False)

    def _on_import_progress(self, done, total):
        """后台线程回调 —— 只更新数值，不碰 UI（UI 线程轮询读取）"""
        try:
            self._import_progress = (int(done), int(total))
        except Exception:
            pass

    def _wait_csv_background(self, path):
        """
        在后台线程读取 CSV，UI 线程用 processEvents 保持响应。

        返回 CsvBackgroundReader（已完成）；出错时 rows 为空、error 有值。
        """
        try:
            self._import_progress = (0, 0)
            self.progress_import.setVisible(True)
            self.progress_import.setRange(0, 0)     # 不确定进度
            rd = CsvBackgroundReader(path, progress_cb=self._on_import_progress)
            rd.start()
            while not rd.done:
                QtWidgets.QApplication.processEvents(
                    QtCore.QEventLoopFlag.ExcludeUserInputEvents, 30)
                try:
                    self.progress_import.setValue(rd.total_bytes or 0)
                except Exception:
                    pass
            self.progress_import.setVisible(False)
            return rd
        except Exception as ex:
            try:
                self.progress_import.setVisible(False)
            except Exception:
                pass
            rd = CsvBackgroundReader(path)
            rd.error = str(ex)
            rd.done = True
            return rd

    def _refresh_processes(self):
        """列出有窗口的进程，供用户挑选追踪目标"""
        try:
            names = []
            user32 = ctypes.windll.user32
            k32 = ctypes.windll.kernel32
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            handles = []

            def cb(hwnd, lparam):
                # 【评审】EnumWindows 会枚举到隐藏窗口 / 最小化窗口，
                # 它们不代表用户可见的进程，会污染下拉列表。
                if not user32.IsWindowVisible(hwnd):
                    return True
                if user32.IsIconic(hwnd):
                    return True
                pid = ctypes.c_ulong(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    handles.append(pid.value)
                return True

            user32.EnumWindows(WNDENUMPROC(cb), 0)
            nt = self.hw_engine.names
            seen = set()
            for pid in handles:
                if pid in seen:
                    continue
                seen.add(pid)
                nm = nt.get(pid)
                if nm and nm.lower().endswith(".exe") and nm not in names:
                    names.append(nm)
            cur = self.combo_procs.currentText()
            self.combo_procs.blockSignals(True)
            self.combo_procs.clear()
            self.combo_procs.addItem("自动锁定主游戏")
            for nm in sorted(names)[:80]:
                self.combo_procs.addItem(nm)
            idx = self.combo_procs.findText(cur)
            if idx >= 0:
                self.combo_procs.setCurrentIndex(idx)
            self.combo_procs.blockSignals(False)
        except Exception:
            pass

    def closeEvent(self, event):
        """
        退出收尾。

        【评审 P0】原实现只发停止信号，不 join 线程 —— 会留下僵尸线程与
        残留的 PresentMon 子进程 / ETW 会话。现在：
          ① 停止硬件线程并 join（有超时）
          ② 停止采集线程（terminate 子进程 + 回收）
          ③ 后台清理 ETW 会话（logman 最长 20s，不能阻塞 UI 退出）

        【v4.3 修 Bug：关不干净 / 屏幕残留】
        · `_closing` 置位：挡住「关闭之后才触发的 700ms 权限提醒」；
        · `_close_other_windows()`：权限提醒 / 11 项专业表格 / 进阶面板
          都是**顶层窗口**，只要还有一个可见，Qt 就不会退出 → 进程留后台；
        · 收尾后再 `quit()` 兜底，保证事件循环一定结束。
        """
        self._closing = True
        # 【UX 新增】注销全局热键（不注销会占用 Ctrl+F9）
        try:
            if getattr(self, "_hotkey", None) is not None:
                self._hotkey.unregister()
        except Exception:
            pass
        try:
            self.ui_timer.stop()
            self.proc_timer.stop()
        except Exception:
            pass
        # 【v3.1】收起进阶硬件面板 + 【v4.3】所有其它顶层窗口一起关掉
        try:
            if getattr(self, "hw_panel", None) is not None:
                self.hw_panel.hide()
                self.hw_panel.close()
        except Exception:
            pass
        self._close_other_windows()
        try:
            if self.hw_engine:
                self.hw_engine.stop()
                if self.hw_engine.is_alive():
                    self.hw_engine.join(timeout=2.0)
        except Exception:
            pass
        try:
            if self.pm_bridge:
                self.pm_bridge.stop(wait=True, timeout=2.0)
                if self.pm_bridge.is_alive():
                    self.pm_bridge.join(timeout=2.0)
        except Exception:
            pass
        event.accept()
        # 兜底：万一还有窗口赖着不关，也强制结束事件循环（进程不会留在后台）
        try:
            QTimer.singleShot(0, QtWidgets.QApplication.instance().quit)
        except Exception:
            pass
