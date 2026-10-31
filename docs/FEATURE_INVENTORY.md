# FrameLab UI 层 功能 / 接线 清单（重写前侦察）

> **状态（v4.1 已完成）**：本文是重写**之前**的对照清单，用于保证「不丢行为」。
> 其中描述 v3.2 版面的部分（左侧栏按钮堆砌、四张分位卡、双模式图表、
> 11 行抽屉等）已被《改进建议书》要求的新版面取代；**行为契约**部分
> （设置页 / CSV 导入导出 / ETW 二次确认 / 提权提醒 / 热键 / 数据源三态 /
> 主题热切换 / 关闭流程）已原样迁移到 `framelab/ui/base_window.py`，
> 并由 `tests/` 下的 10 个脚本守护。旧代码已按「清理过时文件」删除，

>
> **v4.1 版面二次重构**：左栏 280px 手风琴抽屉 → **240px 五张大号视图导航卡**
> （`ui/navtabs.py`）；右侧下半区 → **QStackedWidget 五页整块替换**
> （`ui/pages.py`：帧率总览 / CPU / 显卡与显存 / 内存与磁盘 / 网络与环境）；
> `ui/accordion.py` 已删除。同时修正 4 处算法/坐标轴误报，见 `README.md` §3.2。

目标文件：`framelab/` 包（`analysis.py` / `stats.py` / `ui/` 等）
用途：UI 层（`FrameLabWindow` 4143–7305 + 控件 3731–4142 + 引导 8041–8078）即将拆分重写；本文件是"不丢行为"的对照清单。数据/引擎层保留不动。

## 0. 侦察方法与证据等级

| 标记 | 含义 |
|---|---|
| `L####` | 行号，已逐行读取并核对 |
| `[IN-RANGE]` | 来自授权范围 350–570 / 3731–4142 / 4143–7305 / 7831–8078 |
| `[BOUNDARY]` | **超出授权范围但为完成第 5/6/9 项而读取**：3272–3734（`SnapshotDiffInspector`）、7305–7543（`Config` / `CONFIG_SCHEMA` / 数据源标签 / 热键解析区） |
| `[OUT-OF-RANGE]` | 仅由 grep 取得"符号 + 行号"，**未读函数体**；需要细节者标 `[UNCLEAR]` |
| `[UNCLEAR]` | 未能确证，附行号 |

未读全文（只有定义行号）的外部依赖：`Theme` 186/194/219、`_mix` 285、`theme_from_custom_bg` 298、`THEME_DEEP_SPACE` 247、`THEME_PRESETS` 277、`CURRENT_THEME` 346、`DiagLog` 570、`BoundedQueue` 686、`human_size` 643、`safe_float` 664、`is_na` 726、`fmt_delta` 733、`cpu_count` 1221、`per_core_max_mhz` 1230、`EtwSessionManager` 1477、`HardwareEngine` 1550、`PresentMonBridge` 2205、`BaselineTracker` 2580、`FrameStore` 2772、`parse_hotkey` 7597、`GlobalHotkeyManager` 7706。

**已确认不存在的东西**（grep 全文件，0 命中）：`QShortcut`、`QAction(`、`QMenuBar`/`menuBar`、`QSystemTrayIcon`（无托盘）、`QSettings`。全文件仅 3 处 `statusBar()`（L6605 / L6659 / L6706）。

---

## 1. 构造 / 析构

### 1.1 `FrameLabWindow.__init__`（4145–4205）逐步顺序

| # | 行号 | 动作 | 说明 / 契约 |
|---|---|---|---|
| 1 | 4146 | `super().__init__()` | `QMainWindow` |
| 2 | 4147 | `setWindowTitle("FrameLab 3.2 · 次世代掉帧研究工作台")` | |
| 3 | 4148–4149 | `resize(1440, 940)`；`setMinimumSize(1120, 760)` | 几何不落盘（无 QSettings） |
| 4 | 4151 | `self.store = FrameStore()` | 引擎层，重写须原样保留 |
| 5 | 4152 | `self.q = BoundedQueue(maxsize=60000)` | 生产者 → UI 唯一消费者 |
| 6 | 4153 | `self.is_paused = False` | 暂停 = 停止渲染（`_drain_queue` 仍跑） |
| 7 | 4154 | `self.target_app = "自动锁定主游戏"` | 字符串哨兵值，**不是空串** |
| 8 | 4155 | `self.selected_frame = None` | |
| 9 | 4156–4162 | `_display_frames=[]`、`_display_ts=float64[]`、`_display_idx=int64[]`、`_display_x=float64[]` | 三者与 `_display_frames` 一一对应；`_display_ts` 仅给包络/诊断 |
| 10 | 4163–4165 | `_auto_dom=None`、`_env_tick=0`、`_pending_status=(True, "正在初始化…")` | `_pending_status` 是 `(ok: bool, msg: str)` |
| 11 | 4168–4170 | `_chart_mode=CHART_MODE_FREE`、`_chart_mode_dirty=True`、`_zh_pg_menus_done=set()` | 后两者是"强制重建 / 防重复挂信号"标志 |
| 12 | 4173–4178 | `_data_source=DATA_SOURCE_REAL`、`_offline_mode=False`、`_mark_seq=0`、`_ux_config=None`、`_hotkey=None`、`_admin_prompt_shown=False` | |
| 13 | 4180–4183 | `_env_cache=None`、`_metric_cache_ui=None`、`_table_cache={}`、`_style_cache={}` | 三个缓存是性能前提（见 §3） |
| 14 | 4186–4187 | `self.hw_engine = HardwareEngine(0.4, self.q)`；**`.start()`** | **0.4 s 采样；UI 尚未构建即开跑** |
| 15 | 4189–4190 | `self.pm_bridge=None`；`self._start_presentmon_bridge()` | → `PresentMonBridge(self.q, target_process=self.target_app)` L4213 + `.start()` L4214 |
| 16 | 4192 | `self._init_ui()` | 建全部控件（含 `hw_panel` / 设置页 / plot） |
| 17 | 4195 | `self._init_ux()` | 配置 → 主题 → 图表样式 → 热键 → 提权提醒 → PM 指引 |
| 18 | 4198–4200 | `ui_timer = QTimer(self)`，`timeout→_refresh_ui`，**`start(33)`** | 30 FPS |
| 19 | 4203–4205 | `proc_timer = QTimer(self)`，`timeout→_refresh_processes`，**`start(2500)`** | 2.5 s |
| — | 6559 | `QTimer.singleShot(700, self._show_admin_prompt)` | 仅非管理员时挂 |

**顺序铁律**
- L4186/4190 早于 `_init_ui()`：首个 tick 前队列可能已有 frame/hw/event/status，`_drain_queue` 必须先于一切 UI 读取（L5656）。
- `_init_ux()` 必须在 `_init_ui()` 之后：它写 `self.combo_theme`(L6487)、`self.combo_chart_style`(L6504)、`self.edit_hotkey`(L6525)、`self.lbl_hotkey`(L6537)，并调 `self._apply_theme(..., persist=False)`(L6485)——后者需要 `plot_widget` 与 `curve_*`(L4446–4468)、`lbl_legend_*`(L4594)、`self._settings_labels`(L6761，`_init_ui`→`_build_settings_tab` 建)。
- `_start_presentmon_bridge` 可重入：先 `stop()` 旧桥再新建（L4211–4214）。

### 1.2 `closeEvent`（7260–7302）顺序

| 序 | 行号 | 动作 |
|---|---|---|
| 0 | 7261–7266 | **热键注销 `self._hotkey.unregister()`——写在 docstring 之前**（L7267–7275 的 `"""..."""` 只是被丢弃的表达式语句，是已知格式异常，勿当成注释读） |
| 1 | 7277–7278 | `ui_timer.stop()`；`proc_timer.stop()` |
| 2 | 7283–7285 | `hw_panel.hide()` + `hw_panel.close()`（非模态独立窗口） |
| 3 | 7289–7292 | `hw_engine.stop()`；`is_alive()` → `join(timeout=2.0)` |
| 4 | 7296–7299 | `pm_bridge.stop(wait=True, timeout=2.0)`；`join(timeout=2.0)` |
| 5 | 7302 | `event.accept()` |

**缺口（重写时不要误以为已实现）**：L7274 docstring 声称"后台清理 ETW 会话"，但**无对应代码**；"BridgeStop*" 辅助线程、`CsvBackgroundReader` 线程均未 join；**退出时不保存配置**（无 `_ux_config.save()`）；无 `self.store` 清理。`DIAG.note/close` 在 `main()` 的 `finally`（L8070–8072）。

---

## 2. 用户可见功能 ↔ 处理器（全量）

### 2.1 侧边栏按钮（`_init_ui`，4705–4805）

| 属性名 | 显示文案（verbatim） | tooltip（verbatim） | handler |
|---|---|---|---|
| `btn_pause` | `⏸ 暂停采集` ↔ `▶ 继续采集` | `开始 / 暂停帧采集（不影响硬件探针）` | `_toggle_pause` L4747 / L6141 |
| `btn_sim` | `🎲 模拟流` | `无 PresentMon 时用高保真模拟帧流演示界面` | `_toggle_simulation` L4754 / L6168 |
| `btn_import` | `📂 CSV 导入` | `导入 PresentMon/CapFrameX 导出的 CSV 回放` | `_import_csv` L4756 / L6229 |
| `btn_export` | `💾 导出` | `把本次采集导出为 CSV` | `_export_csv` L4758 / L6417 |
| `btn_clean` | `🧹 清理 ETW` | `清理本工具残留的 ETW 会话（修复 error 1450 静默 0 帧）` | `_clean_etw_confirmed` L4760 / L6178 |
| `btn_mark` | `★ 打点标记` | `在当前时刻打一个手动标记（等同全局热键）` | `_on_manual_mark` L4767 / L6641 |
| `btn_settings_quick` | `⚙ 设置` | `主题 / 图表样式 / 阈值 / PresentMon 路径 / 热键（全局唯一设置入口）` | `_on_open_settings_panel` L4779 / L5603 |
| `btn_hw_panel` | `🚀 进阶硬件面板` ↔ `🚀 收起进阶硬件面板` | `CPU 热力图 / 显存 / 内存换入 / 着色器 / 事件流 —— 独立窗口` | `_on_toggle_hw_panel` L4783 / L5590 |
| `btn_pm_dir` | `打开 PresentMon 目录` | `定位并说明如何获取 PresentMon` | `_on_open_presentmon_dir` L4798 / L6698 |

次级按钮为 2×2 栅格（`QGridLayout`，L4751–4762，顺序 sim/import/export/clean）。
常量文案：`lbl_logo="◈ FrameLab 3.2"`(L4717)、`lbl_logo_sub="掉帧研究工作台"`(L4720)、`lbl_engine_badge="● 正在探测 PresentMon 引擎…"`(L4726)、`lbl_target_caption="追踪进程"`(L4734)、`progress_import`(L4800, 默认隐藏)、`lbl_hotkey`/`lbl_pm_hint`(L4789/4793)、对象名 `Sidebar`(L4709，宽 268–340)。

### 2.2 主展示区（4807–5083）

| 属性名 | 文案 / 参数 | handler |
|---|---|---|
| `banner_frame`(#BannerCard) + `banner_text` | `🟢 正在启动采集引擎…`(L4822) | 由 `_refresh_ui_inner` 写 |
| `card_stutter`(#StutterCard, 固定宽 320) + 标题 `流畅度时间分布 (CapFrameX 风格)`(L4832) + `stutter_bar` + `lbl_stutter_sub` `流畅 100% · 微卡 0% · 严重 0%`(L4835) | | `_refresh_ui_inner` L5786–5788 |
| `card_avg` | `MetricCard("AVG FPS", "帧率均值", highlight=True)` | |
| `card_low1` | `MetricCard("1% LOW FPS", "微卡下限 (P99)")` | |
| `card_low01` | `MetricCard("0.1% LOW FPS", "恶性卡顿下限 (P99.9)")` | |
| `card_bottle` | `MetricCard("GPU 忙时率", "渲染瓶颈倾向")` | |
| `card_chart`(#ChartCard) + `hint` | `帧时间 × 帧序号 · 左键点击尖峰锁定病灶`(L4865) | |
| `lbl_legend_1..5` | L4871–4875（`lbl_legend_5` 是保留空名，L4875） | `_update_legend` L4594 |
| `combo_chart_mode` | item `🖱 自由缩放 · 极客`/data `free`；`🌍 全局概览 · 体检`/data `overview`，L4884–4885 | `_on_chart_mode_changed` L4889 / L5309 |
| `plot_widget` | 见 §4 | |
| `diff_drawer`(#DrawerCard, 默认 `setVisible(False)` L4984) | 抽屉头 `lbl_diff_title`(`【病灶对比诊断卡】 点击图表波峰锁定对比` L4990)、`lbl_baseline_info`、`btn_reset`(`复位基准` L4997)、`btn_close_diff`(`✕ 收起`, tooltip `收起诊断抽屉（再次点击图表尖峰可重新展开）` L5002) | `_reset_inspect` L4998/5293；`_on_close_diff_drawer` L5003/5582 |
| `table_diff` | `QTableWidget(11, 5)` L5010（**11 行 × 5 列，不是 11 列**） | `_render_diff_card` L5921 |
| `geek_panel`(#GeekPanel, 宽 330–430) | `lbl_geek_title="🔥 极客热力图 · CPU 各核负载"`(L5034)、`heat_geek=CpuHeatmapGrid(cpu_count())`(L5038)、`lbl_geek_probe_title="🔬 新探针实时读数"`(L5041)、`probe_bar=ProbeReadoutBar()`(L5045) | `_refresh_geek_panel` L6069 |
| `hw_panel` | 顶层独立窗口 `Qt.WindowType.Window`，标题 `FrameLab · 进阶硬件面板`，resize(1100,720)，min(880,540)，`installEventFilter(self)` L5082 | |
| `hw_tabs` | 6 个页签（顺序即文字）：`🧠 CPU 热力图`、`🎮 显存监视`、`💾 内存换入`、`✨ 着色器与引擎`、`⚡ 事件流`、`⚙ 设置`（L5073–5079） | |
| `tab_cpu` | `lbl_cpu_summary`(L5147)、`heat_cpu`+`set_max_mhz(max(per_core_max_mhz(cpu_count()) or [4000]))`(L5152–5155)、色标 chips `空闲/轻载/中载/重载/拥堵`(L5167)、`块底部细条 = 频率（相对基频）`(L5177)、**`self.core_widgets = []`（兼容占位，L5184）** | `_refresh_hardware_tabs` |
| `tab_vram` | `lbl_vram_info`(L5191)、`table_vram` 表头 `["进程名","本地显存","溢出到内存","专用计数"]`(L5195) | |
| `tab_mem` | `lbl_mem_summary`(L5206)、`lbl_pagein_top`(L5209)、note `原理: 物理内存吃紧时，系统从 pagefile.sys 读回内存页即为「换入」，必掉帧。`(L5212) | |
| `tab_shader` | `lbl_shader_status`(L5222)、`table_shader` 表头 `["缓存目录","文件总数","总大小","新增写入(本轮)"]`(L5226) | |
| `tab_events` | `table_events` 表头 `["时刻","诱因类型","事件摘要与诊断建议"]`(L5238) | |
| `tab_settings` | `_build_settings_tab` L6748（见 2.5） | |

`_hdivider()` L5085 侧边栏分隔线。`showEvent` L5093 额外调 `_update_gradient()`。

### 2.3 图表交互（详 §4）

| 触发 | handler | 行为 |
|---|---|---|
| 鼠标移动 | `_on_mouse_moved` L5249 | 十字虚线 `v_line`/`h_line` 跟随（仅当 `sceneBoundingRect().contains(pos)`） |
| 左键点击 | `_on_mouse_clicked` L5256 | 二分找最近帧 → `selected_frame` + `locked_line.setPos(frame.idx)` + `_render_diff_card(frame)` + `_show_diff_drawer()` |
| 右键 | pyqtgraph 自带菜单 | 全中文，`_localize_pg_menus` L5472 + `PG_MENU_ZH` L412–466 |
| 滚轮/拖拽 | pyqtgraph | overview 模式被 `setMouseEnabled(x=False, y=False)` 禁掉（L5345） |

### 2.4 设置页（`_build_settings_tab`，6748–6929）

| 属性名 | 文案 | handler |
|---|---|---|
| 说明 | `阈值参数来自配置文件 %s（修改后点「保存当前阈值」生效）。` % `CONFIG_FILENAME`(L6773) | |
| `combo_theme` | items = `THEME_PRESETS`(name/key) + `自定义背景色…`/`custom`(L6785–6788) | `_on_theme_combo_changed` L6796 / L6932 |
| `combo_chart_style` | `CapFrameX 风格（水平阈值线 · 无填充）`/`capframex`；`赛博 HUD（渐变填充 · Low 包络线）`/`hud`(L6802–6803) | `_on_chart_style_combo_changed` L6810 / L6941 |
| `edit_custom_bg` | placeholder `#0a0e15 或 #ffffff`(L6818) | |
| `btn_pick_bg` | `选择颜色…` | `_on_pick_custom_bg` L6822 / L6950（`QColorDialog`，标题 `选择自定义背景色`） |
| `btn_apply_bg` | `应用` | `_on_apply_custom_bg` L6824 / L6957 |
| `cfg_edits` | 由 `CONFIG_SCHEMA` 生成的 key→`QLineEdit`（L6833–6849），label 文案 `"%s\n(%s)" % (key, desc)` | |
| `btn_cfg_save` | `保存当前阈值` | `_on_save_thresholds` L6854 / L7043 |
| `btn_cfg_reset` | `恢复默认` | `_on_reset_thresholds` L6856 / L7064 |
| `btn_cfg_reload` | `重新载入配置文件` | `_on_reload_config` L6858 / L7082 |
| `edit_pm_path` | placeholder `例如: C:\Tools\PresentMon.exe`(L6872) | |
| `btn_pm_browse` | `浏览…` | `_on_browse_presentmon` L6874 / L7114 |
| `btn_pm_open_dir` | `打开目录` | `_on_open_presentmon_dir` L6876 |
| PM 指引 | `presentmon_missing_hint()`(L6882，正文见 `PRESENTMON_GUIDE` L7877–7893) | |
| `edit_hotkey` | `QKeySequenceEdit`，初值 `_current_hotkey_text()`(L6896)；tooltip 区文案 L6891 | `editingFinished`+`keySequenceChanged` → `_on_hotkey_sequence_changed` L6899–6900 / L6985 |
| `btn_hk_apply` | `立即应用` | `_on_apply_hotkey_sequence` L6903 / L7002 |
| `btn_hk_reset` | `恢复默认` | `_on_reset_hotkey` L6905 / L7030（默认 `"Ctrl+F9"` L7031） |
| `lbl_hotkey_tip` | `提示：直接点击上方输入框，按下你想要的组合键（支持 Ctrl / Alt / Shift / Win + 任意键）。热键在游戏全屏时也能触发，用于随手标记卡顿瞬间。`(L6911–6913) | |
| `lbl_settings_status` | 空 | `_cfg_status` L7035 |

整页包在 `QScrollArea`（widgetResizable、NoFrame，L6924–6928）。所有带色 QLabel 经 `_track_setting_label(label, role)` L6714 登记进 `self._settings_labels`（L6761），role ∈ `accent` / `dim` / `dim_small`（`_style_setting_label` L6723）。

**死代码**：`_on_apply_hotkey` L7151 无任何调用点（grep 仅定义行），且 L7154 调 `self.edit_hotkey.text()` —— `QKeySequenceEdit` 无 `.text()`，一旦调用即抛异常。重写时删除或修好。

### 2.5 全局热键 / 打点

- 热键：`GlobalHotkeyManager(self._on_manual_mark, hk)` L6517（`hk = cfg.get("hotkey","Ctrl+F9")` L6516）→ `.register(app=QApplication.instance())` L6518；`_init_ux` 结束再 `edit_hotkey.setKeySequence(...)` 同步显示值 L6523–6529。
- 打点：`_on_manual_mark` L6641 → `Event(now, "手动标记", "手动打点 #%d（%s）", advice="此处的卡顿/异常可与该时刻对齐排查", severity="WARN", symbol="star")` L6652–6656 → `self.q.put(("event", ev))` L6657 → statusBar `★ 已打点标记 #%d @ %s`(L6660)。
- 标签文案：`全局热键: 不可用（请用 ★ 打点标记 按钮）`(L6540)、`全局热键: %s 打点标记`(L6544)、`全局热键不可用（%s）· 请用 ★ 打点标记 按钮`(L6547)。
- 接口面（[OUT-OF-RANGE]，定义 L7706）：`.register(app=None, hotkey_text=None)` L7749、`.unregister()` L7812、`.describe()` L7740、`.available`、`.error`、`.hotkey_text`。

### 2.6 管理员 / PresentMon / 数据源

| 功能 | 入口 | 实现要点 |
|---|---|---|
| 启动提权提醒 | `_schedule_admin_prompt` L6554 | `is_admin()` 为真则 `return`；否则 `QTimer.singleShot(700, ...)` |
| 提醒弹窗 | `_show_admin_prompt` L6563 | 标题 `FrameLab · 权限提醒`；正文 `建议以管理员身份运行 FrameLab`；informative L6581–6584；按钮 `🛡 以管理员身份重启`(Accept) / `继续以当前权限`(Reject, 默认)；`WA_DeleteOnClose`；`box.open()` **非模态**；引用存 `self._admin_dialog` L6593；`_admin_prompt_shown` 去重 L6572 |
| 提权重启 | `_on_admin_prompt_choice` L6597 | `relaunch_as_admin()` L7849（`ShellExecuteW(None,"runas",exe,params,os.getcwd(),1)`；返回 >32 为成功）→ statusBar `正在以管理员身份重启…` → `self.close()`；失败弹 `提权失败` L6610–6613 |
| PresentMon 指引 | `_update_presentmon_hint` L6617 | 优先配置路径 → `find_presentmon_binary()` → 文案 `PresentMon: %s`(绿) / `未找到 PresentMon.exe（模拟模式）—— `(WARN) |
| 打开目录 | `_on_open_presentmon_dir` L6698 | `open_dir_in_explorer(presentmon_dir())` L7906/7896；失败弹 `PresentMon` 对话框 |
| 手动指定 PM | `_on_browse_presentmon` L7114 → `_apply_presentmon_path` L7125 | 写配置 `presentmon_path`；若文件存在则**重启桥接**（旧桥在 `"BridgeStopPM"` 线程里 stop，L7139–7141） |
| 数据源三态 | `_update_data_source_banner` L6668 | `detect_data_source(self.pm_bridge, offline=self._offline_mode)` L7922（offline 优先 → `simulation_mode` → real）；写 `lbl_data_source` 文案/色；水印 `_sim_watermark.setVisible(src == DATA_SOURCE_SIM)` L6680；`_position_watermark` L6686 摆到 `viewRect().center()` |

数据源标签（[BOUNDARY] L7322–7332）：`● 真实采集 (PresentMon)` / `● 模拟数据 · 非真实采集` / `● 离线导入 CSV`；颜色 GREEN / BAD / ACCENT。水印文字 `SIMULATED DATA · 非真实采集`(L7334)，`pg.TextItem`、字号 26 加粗、`setZValue(1000)`、`ignoreBounds=True`（`make_sim_watermark` L7942）。

### 2.7 ETW 清理

1. `_clean_etw_confirmed` L6178：`live = self.pm_bridge.etw.session_name`(L6188)；`stale = [n for n in EtwSessionManager.list_sessions() if n.lower().startswith(PM_SESSION_PREFIX.lower()) and n != live]`(L6189–6191)；无残留 → 信息框 `ETW 会话清理` / `当前没有需要清理的残留 FrameLab ETW 会话。\n\n（本次操作不会影响正在进行的采集。）`(L6193–6196)；有残留 → `QMessageBox.question`，标题 `确认清理 ETW 残留`，正文由 L6198–6205 拼装（首行 `即将停止 %d 个残留的 FrameLab ETW 会话：`、逐条 `  - <name>`（最多 10 条，超出 `  ... 共 %d 个`）、`当前正在使用的会话 %s 会被自动跳过，不受影响。`、`停掉这些会话不会影响其它软件的 ETW 会话。`），默认按钮 No。
2. `_clean_etw` L6216：`EtwSessionManager().cleanup_stale(skip=live)` L6225 → 信息框 `已清理 %d 个残留 ETW 会话` / `当前没有残留的 FrameLab ETW 会话`(L6226–6227)。
3. 前缀常量 `PM_SESSION_PREFIX = "FrameLab_PM_"`(L542)。

### 2.8 CSV 导入 / 导出

**导入** `_import_csv` L6229–6415（一次长事务，有 3 个契约，注释 L6233–6238 明示）：
- 文件选择 `QFileDialog.getOpenFileName(self, "选择 CapFrameX / PresentMon CSV 文件", "", "CSV Files (*.csv)")` L6240。
- 后台读取 `_wait_csv_background` L7182 → `CsvBackgroundReader`（线程名 `"CsvBackgroundReader"`，L7999；`utf-8-sig`，`csv.DictReader`，每 2000 行回调）→ UI 用 `QApplication.processEvents(QEventLoopFlag.ExcludeUserInputEvents, 30)` 轮询 L7195；`progress_import` 显示且 `setRange(0,0)`，循环里 `setValue(rd.total_bytes)`(L7198)。
- 列名解析（大小写双写法）：帧时间 `MsBetweenPresents|msBetweenPresents|FrameTime`(L6265)；GPU `MsGPUBusy|msGPUActive|GPUBusy`(L6270)；CPU `MsCPUBusy|CPUBusy`(L6271)；`Application`(L6272)、`ProcessID`(L6273)；时间列 `TimeInSeconds|TimeInMs|CPUStartTime|CPUStartTimeInMs|CPUStartTimeQPCTimeInMs|CPUStartQPCTime|QPCTime`(L6278-6279)，单位用"中位差分 / 中位帧时间"自动判定：`0.5<ratio<2.0 → ×1/1000`（毫秒），`0.0002<ratio<0.002 → ×1`（秒），否则退化按帧累加（L6283–6319）。
- 校验：`math.isfinite(ft) and ft > 0`(L6333)；构造 `FrameRecord(idx, t, base_wall + t, app, pid, ft, gpu, cpu, hw_seq=-1)`(L6356)（`hw_seq=-1` 是"无硬件快照"的契约值，诊断卡据此显示不可判定）。
- 原子替换：先 `parsed` 校验非空（L6362），再 **stop bridge → 丢弃队列残留 frame → `store.frames.clear()` → `store.baseline.reset()` → 追加**（L6368–6386）；随后清 `selected_frame`、隐藏 `locked_line`/`diff_drawer`、`_chart_mode_dirty=True`(L6389–6395)、`_offline_mode=True`(L6398)。
- `finally` 必恢复 `is_paused=False` + 按钮文案（L6412–6415，注释标 `【D1】`）。
- 提示框：成功 `导入成功` + 长文 L6403–6408；失败 `导入失败`/`未解析出任何有效帧。\n（当前会话数据已保留，未做任何改动。）`、`读取失败`、`格式错误`。

**导出** `_export_csv` L6417–6443：`getSaveFileName(..., "framelab_record.csv", "CSV Files (*.csv)")`；表头（13 列，verbatim L6426–6429）：`Index, TimeInSeconds, Application, MsBetweenPresents, FPS, MsGPUBusy, MsCPUBusy, GPUBusyRatio, CPU0_Usage, VRAM_NonLocal_MB, PageIn_MBps, CacheNewFiles, DataSource`；每帧 `store.snapshot_for(fr.hw_seq)` 取硬件值（缺则 0）；`DataSource` 列写 `self._data_source`。文案 `导出成功`/`已成功导出 %d 帧对账数据！`、`导出失败`/`写入文件出错: %s`。
**没有 JSON 导出**（全文件无 `json.dump` 到用户文件；`json` 仅用于 `Config`）。

### 2.9 模拟流 / 进程选择 / 暂停 / 复位

- 模拟流 `_toggle_simulation` L6168：旧桥 `stop(wait=True,timeout=2.0)` 丢到 daemon 线程 `"BridgeStop"`(L6172)；新建 `PresentMonBridge(self.q)` 并**直接改属性** `self.pm_bridge.exe_path = "__FORCE_SIMULATION__"`(L6175) 后 start。**没有"退出模拟"的 UI 路径**；只有切进程(L6165 仅当桥死)或应用 PM 路径(L7141) 会重建真桥。
- 进程列表 `_refresh_processes` L7214（每 2.5 s）：`EnumWindows` + `IsWindowVisible` + `!IsIconic` 收集 pid（L7223–7236）→ 经 `self.hw_engine.names`(NameTable) 映射 → 只留 `.exe` 且去重 → **`sorted(names)[:80]`**，首项固定 `"自动锁定主游戏"`(L7249) → `blockSignals(True/False)` 包住清空/重填，末尾 `findText(cur)` 还原选中（L7246–7255）。窗口隐藏/最小化被排除；非游戏黑名单 `NON_GAME_PROCESSES`(L533) 在**引擎侧**生效，UI 不过滤。
- 目标切换 `_on_target_changed` L6145：**不重启 PresentMon**（注释 L6147–6154 解释原因），只设 `target_app`、`_auto_dom=None`、清 `selected_frame`、隐藏 `locked_line`/`diff_drawer`、`_chart_mode_dirty=True`；仅当 `pm_bridge is None or not is_alive()` 才重启。
- 自动锁定：`_refresh_ui_inner` L5674–5684 —— `target_app == "自动锁定主游戏"` 时 `dom = store.dominant_app()` 存 `_auto_dom`，然后 `eff_target` 交给 `store.visible_frames(eff_target)`。
- 暂停 `_toggle_pause` L6141：翻转 `is_paused` + 按钮文案；暂停期间 `_refresh_ui_inner` 在 L5670 早退（**但 `_drain_queue` 已执行**，数据继续入 store）。
- 复位 `_reset_inspect` L5293：清 `selected_frame`、隐藏 `locked_line`、`diff_drawer.hide()`、恢复标题、`store.baseline.reset()`。

---

## 3. 信号 / 槽 / 定时器 接线图

### 3.1 定时器与线程

| 名称 | 周期 | 连接 | 行号 |
|---|---|---|---|
| `ui_timer` | 33 ms（≈30 FPS） | `timeout → self._refresh_ui` | 4198–4200 |
| `proc_timer` | 2500 ms | `timeout → self._refresh_processes` | 4203–4205 |
| `QTimer.singleShot` | 700 ms 一次 | `→ self._show_admin_prompt` | 6559 |
| `HardwareEngine` | 0.4 s | 产出 `("hw", HardwareSnapshot)` | 4186–4187 / 1556 |
| `PresentMonBridge` | 事件驱动 | 产出 `("frame"|"event"|"status", payload)` | 4213–4214 |
| `CsvBackgroundReader` | 一次性 | `progress_cb → self._on_import_progress` | 7999 / 7192 |
| 一次性停止线程 | — | 名 `"BridgeStop"`(6173) / `"BridgeStopImport"`(6371) / `"BridgeStopPM"`(7140) | 均 daemon，不 join |

队列契约（`_drain_queue` L4226–4260）：`self.q.get_nowait()` 返回 `(kind, payload)`，`kind ∈ {"frame","hw","event","status"}`；分别 `store.add_frame` / `add_snapshot` / `add_event` / `self._pending_status = payload`（L4247–4257）。限额：`DRAIN_MAX_FRAMES=4000`、`DRAIN_MAX_SNAPS=200`、`DRAIN_MAX_EVENTS=400`、`DRAIN_BUDGET_MS=16.0`（L4216–4224），超限留待下一 tick；溢出丢帧数由 `self.q.take_dropped()` 在 L5660 读出并追加到状态文案。

### 3.2 显式 signal 连接表

| 发射者 | 信号 | 槽 | 行号 |
|---|---|---|---|
| `combo_procs` | `currentTextChanged` | `_on_target_changed` | 4740 |
| 侧边栏 8 按钮 | `clicked` | 见 §2.1 | 4747–4798 |
| `combo_chart_mode` | `currentIndexChanged` | `_on_chart_mode_changed` | 4889 |
| `plot_widget.getPlotItem()` | `sigRangeChanged` | `_clamp_y_nonneg`；`_update_gradient` | 4963 / 4964 |
| `plot_widget.scene()` | `sigMouseMoved` / `sigMouseClicked` | `_on_mouse_moved` / `_on_mouse_clicked` | 4966 / 4967 |
| `btn_reset` / `btn_close_diff` | `clicked` | `_reset_inspect` / `_on_close_diff_drawer` | 4998 / 5003 |
| `hw_panel` | `installEventFilter(self)` → `eventFilter`（Show/Hide/Close） | `_update_hw_panel_button` | 5082 / 5624 |
| pyqtgraph 菜单 | `aboutToShow` | `_zh_pg_menu_walk`（**仅首访连接**，防堆积） | 5540 |
| 管理员 box | `buttonClicked` | `lambda b: _on_admin_prompt_choice(b is btn_elev)` | 6590 |
| `combo_theme` | `currentIndexChanged` | `_on_theme_combo_changed` | 6796 |
| `combo_chart_style` | `currentIndexChanged` | `_on_chart_style_combo_changed` | 6810 |
| `edit_hotkey` | `editingFinished`, `keySequenceChanged` | `_on_hotkey_sequence_changed` | 6899 / 6900 |
| `ui_timer` / `proc_timer` | `timeout` | §3.1 | 4199 / 4204 |
| 设置页按钮 | `clicked` | §2.4 | 6822–6905 |

### 3.3 周期更新路径（`_tick` 类）

入口 `_refresh_ui` L5637（异常守卫：`DIAG.report` + `_ui_error_count`，前 3 次写 `banner_text`，L5644–5653）→ `_refresh_ui_inner` L5655：

| 序 | 行号 | 调用 | 频率 |
|---|---|---|---|
| 1 | 5656 | `self._drain_queue()` | 每 tick |
| 2 | 5658–5665 | 读 `_pending_status`、`q.take_dropped()`，写 `lbl_engine_badge` | 每 tick |
| 3 | 5668 | `_update_data_source_banner()`（→ `detect_data_source`、水印显隐、`_position_watermark`） | 每 tick |
| 4 | 5670–5671 | `if self.is_paused: return` | — |
| 5 | 5674–5684 | 自动锁定判定 + `store.visible_frames(eff_target)` | 每 tick |
| 6 | 5686–5689 | `len(frames) < 2` → 横幅 `🟡 正在等待帧数据…（%s）` 后 return | — |
| 7 | 5692–5694 | `_env_tick += 1`；`store.update_baseline(frames)` | **每 6 tick（≈200 ms）** |
| 8 | 5701–5719 | 曲线重建决策 + `curve_ft.setData` | free：每 tick；overview：`%2` 或 dirty 或空（L5702–5703） |
| 9 | 5729–5737 | `_compute_envelope` + `curve_lowenv/low01.setData` | **每 6 tick** 或 `_env_cache is None` |
| 10 | 5740–5760 | 事件打点 `scatter_events.setData(pts)` | 随曲线重建（在 rebuild 分支内） |
| 11 | 5767–5770 | overview：`_apply_x_range(frames[0].idx, frames[-1].idx)` | 每 tick |
| 12 | 5775–5788 | `store.summary_metrics(frames)` → 4 张卡 + `stutter_bar.update_ratios` + `lbl_stutter_sub` | **每 6 tick** 或 `_metric_cache_ui is None` |
| 13 | 5791–5802 | 横幅（卡顿/丝滑二态，见 §6） | 每 tick |
| 14 | 5807–5816 | 抽屉可见时：`_render_diff_card(last)`（未锁定）或只更新标题（已锁定）+ `lbl_baseline_info` | 每 tick（仅抽屉可见） |
| 15 | 5821–5822 | `if self.hw_panel.isVisible(): self._refresh_hardware_tabs()` | 每 tick（仅面板可见） |
| 16 | 5824–5825 | `if self.diff_drawer.isVisible(): self._refresh_geek_panel(frames)` | 每 tick（仅抽屉可见） |

其它周期性路径：`_refresh_processes`（2500 ms）、`_update_gradient`（`sigRangeChanged` 触发 + `showEvent` + 样式切换）、`_clamp_y_nonneg`（`sigRangeChanged`）、`_position_watermark`（模拟态每 tick）。

**缓存键语义**：`_env_tick % 6`（≈200 ms）是"重活节拍"；`_chart_mode_dirty` 是"数据集/模式突变"标志；`_env_cache`/`_metric_cache_ui` 为 `None` 时强制重算（首 tick）。重写若改 33 ms，`% 6` 必须同步换算。

---

## 4. 图表内部

### 4.1 建图（4894–4971）

| 行号 | 设置 |
|---|---|
| 4894 | `self.plot_widget = pg.PlotWidget()` |
| 4895 | `showGrid(x=True, y=True, alpha=0.18)` |
| 4896 | `setLabel("left", "Frametime (ms)", color=DIM)` |
| 4899 | `setLabel("bottom", "帧序号 (Frame Index)", color=DIM)` ← **X = 帧序号** |
| 4900 | `setMouseEnabled(x=True, y=True)` |
| 4901 | `getPlotItem().setClipToView(True)` |
| 4903 | `setDownsampling(auto=True, method="peak")` |
| 4912 | `getViewBox().setLimits(yMin=0)`（Y 下界锁 0） |
| 4915 | `enableAutoRange(axis="y", enable=True)` |
| 4970 | `setMinimumHeight(300)` |
| 4974 | `_localize_pg_menus()` |

坐标语义：`_display_x = _display_idx.astype(float64)`，`_display_idx = [f.idx for f in display]`（L5716–5717）；`_display_ts` 保留真实秒（L5713）仅供包络/诊断。free 模式只渲染 `frames[-2500:]`（L5711）；overview 用 `_decimate_for_overview`（L5709/5440，`OVERVIEW_RENDER_CAP = 12000` L5307，等距抽稀 + 保留末帧 + **所有带事件的帧必须保留**并 `sort(key=idx)`，因为 `_find_frame_near` 依赖有序数组，L5466）。

### 4.2 曲线 / 图元清单

| 属性 | 创建行 | pen / brush | 数据来源 | 状态 |
|---|---|---|---|---|
| `curve_ft` | 4917 | `ACCENT` w=1.4，`fillLevel=0.0`，brush `(25,211,255,26)`；`_restyle_chart` 按样式改（CapFrameX `CX_COLOR_FRAMETIME="#2197F3"` w=1.5 无填充 L4633–4634；HUD `t.accent` w=1.5 + brush `(25,211,255,26)` L4637–4638） | `setData(self._display_x, ys)` L5719，`ys=[f.frametime]` | 常显 |
| `curve_lowenv` | 4923 | `WARN` w=1.3 | `setData(xs, env1)` L5736 ← `_compute_envelope(ys, self._display_x)` L5731（滑窗 P99） | 常显 |
| `curve_low01` | 4925 | `BAD` w=1.1 `DotLine`（L4644 重设） | `setData(xs, env001)` L5737（P99.9） | 常显 |
| `curve_low1` | 4931 | `CX_COLOR_STUTTERING=(255,0,0,180)` w=1 | 无（仅 `_update_capframex_series` 会喂，而该方法无调用点） | **永远 hidden**（4937 / 4648） |
| `curve_ma` | 4933 | `CX_COLOR_MOVING_AVG="#8B6A60"` w=1 | 同上 | 永远 hidden |
| `curve_lowfps` | 4935 | `CX_COLOR_LOWFPS=(255,180,0,180)` w=1 | 同上 | 永远 hidden |
| `scatter_events` | 4943 | `ScatterPlotItem(size=9, pen=mkPen(None), antialias=True)`，`addItem` 4944 | `setData(pts)` L5760；`pts=[{"pos":(f.idx, f.frametime), "brush": color, "symbol": pg_symbol(ev.symbol), "size": 9}]` L5757 | 常显 |
| `_sim_watermark` | 4947 | `pg.TextItem`，`addItem(..., ignoreBounds=True)` 4949 | 可见性由 `_update_data_source_banner` 控制 6680 | 仅模拟态 |
| `v_line` / `h_line` | 4951 / 4953 | `InfiniteLine`，`DIM` w=0.8 `DashLine`，`movable=False`，`ignoreBounds=True` | `_on_mouse_moved` L5253–5254 | 跟随鼠标 |
| `locked_line` | 4955 | `InfiniteLine(angle=90)`，`BAD` w=2.0 | `setPos(frame.idx)` L5267 | 初始 `setVisible(False)` 4958 |
| `_fill_item` / `_gradient_item` | 4961–4962 | HUD 独有，惰性建：`base=plot(pen=None,brush=None)`、`FillBetweenItem(curve_ft, base)`、`addItem(ignoreBounds)`、`removeItem(base)`、再加回 base（L5112–5117）；brush=`QLinearGradient(sceneBoundingRect)` accent alpha 0→56（L5122–5129） | | CapFrameX 下改透明 brush 4652 |

事件打点配色（L5743–5756）：`kind ∈ ("溢出","CPU0拥堵","GPU降频","磁盘I/O") → BAD`；`"内存" → PURPLE`；`"手动标记" → "#ffd600"`；`"降频" → ORANGE`；`"着色器" → ACCENT`；`"网络" → PURPLE`；其余 `WARN`。符号经 `pg_symbol`（L562）白名单 `PG_SYMBOLS`（L558，非法符号会抛 `KeyError` 直接崩 UI）。

### 4.3 双模式与视口

- `_on_chart_mode_changed` L5309：`key = combo_chart_mode.itemData(index)`；非 `free/overview` 直接 return；`if key != prev: self._chart_mode_dirty = True`(L5340–5341)；overview → `setMouseEnabled(x=False,y=False)` + `_reset_view_to_global()`(L5345–5346)；free → `setMouseEnabled(x=True,y=True)` + `enableAutoRange(axis="x", enable=False)`(L5350–5353)；末尾 `if not self.is_paused: self._refresh_ui()`(L5357–5358，立刻重铺不等 tick)。
- `_reset_view_to_global` L5398：**顺序即契约** —— `disableAutoRange(XAxis)`(5413) → `updateAutoRange()`(5417) → `setXRange/setYRange`(5423) → `enableAutoRange("y")`(5426) → `enableAutoRange("x")`(5432) → 再 `_apply_x_range`(5436)。注释 L5399–5407 说明为何不能颠倒（`setRange` 会顺手关掉 autoRange）。
- `_apply_x_range(lo, hi, pad_ratio=0.01)` L5387：`pi.setXRange(lo-pad, hi+pad, padding=0)`；`hi<=lo` 兜底 `hi=lo+1`。
- `_clamp_y_nonneg` L5363：`_y_guard` 重入锁；`vb.viewRange()` 若 `y0 < -1e-6` → `setYRange(0.0, max(y1,1.0), padding=0)`；异常路径也要复位 `_y_guard=False`。
- `_decimate_for_overview(frames)` L5440：`n <= 12000` 原样返回；`step = max(1, n // 12000)`；补末帧；补事件帧；按 `idx` 排序。
- `_update_gradient(*args)` L5101：**仅 HUD 样式**（L5108 早退）；建/更新 `_fill_item` 渐变。

### 4.4 点击 / 悬停 / 右键汉化

- `_on_mouse_moved(pos)` L5249：`plot_widget.sceneBoundingRect().contains(pos)` → `plotItem.vb.mapSceneToView(pos)` → `v_line.setPos(x)`、`h_line.setPos(y)`。
- `_on_mouse_clicked(evt)` L5256：仅 `Qt.MouseButton.LeftButton`；命中场景矩形；`frame = _find_frame_near(pt.x())`；None 返回；否则三连：`locked_line.setPos(frame.idx)` + `_render_diff_card(frame)` + `_show_diff_drawer()`。
- `_find_frame_near(target_x)` L5273：`np.searchsorted(self._display_x, target_x)`（O(log n)），前后邻比较，返回 `FrameRecord`；`xs` 长度不匹配时回退用 `f.idx` 现算（L5284–5285）。
- 右键菜单汉化：`PG_MENU_ZH` L412–466（~50 条，verbatim 键值见文件）；`_localize_pg_menus` L5472（走 `getContextMenus(None)` + `vb.menu`）；`_zh_pg_menu_walk` L5506（`depth > 6` 停；`_zh_pg_menus_done` 记 `id(menu)`；**首访才 connect `aboutToShow`**，因 pyqtgraph 会运行时改 `Mouse Enabled/Disabled` 文本）；`_zh_pg_widget_texts` L5545（`QLabel`/`QCheckBox`/`QRadioButton`，覆写轴子菜单内嵌控件）。去 `&` + `strip()` 后精确匹配，未收录保持英文。

---

## 5. 诊断路径（尖峰 → 11 行诊断卡）

### 5.1 触发

两条独立路径：
1. **用户点击**：`_on_mouse_clicked`(L5256) → `_render_diff_card(frame)` + `_show_diff_drawer()`。抽屉展开即被 §3.3 第 14 项每 tick 驱动。
2. **横幅自动判定**：`_refresh_ui_inner` L5793 `last.frametime >= base_ft * 2.2 and base_ft > 0` → `rows = SnapshotDiffInspector.inspect(last, self.store)` L5794 → `verdict = SnapshotDiffInspector.banner_verdict(rows, last)` L5795。注意：`rows` 只用于取一句话结论，**不写入表格**。

未锁定时（`selected_frame is None`）抽屉每 tick 用 `last` 重算（L5813）→ "实时跟随"；锁定时只更标题（`【病灶对比诊断卡】 锁定时刻 T = %.2fs · 帧耗时 %.1fms` L5809–5811），表格内容冻结。

### 5.2 表格 = 11 行 × 5 列

`self.table_diff = QTableWidget(11, 5)` L5010；表头 verbatim（L5011–5013）：
`["监控维度", "异常瞬间数值 (T = 点击时刻)", "平稳期均值 (Baseline)", "变化幅度 (Delta)", "元凶判定与优化建议"]`。
列 0–3 `ResizeToContents`，列 4 `Interactive` 宽 520（L5015–5018）；`verticalHeader().setVisible(False)`、`NoEditTriggers`、`SelectRows`、`setWordWrap(True)`、行高 34（L5019–5023）。**无 itemClicked / 双击连接**（无"点表格打开"流程）。

### 5.3 行数据来源：`SnapshotDiffInspector.inspect(frame, store)`（L3280–3640，[BOUNDARY]）

| # | 维度文字（`DiffRow.dim`，verbatim） | 行号 | 读取的数据 |
|---|---|---|---|
| 1 | `帧表现 (帧耗时 / 瞬时 FPS)` | 3306 | `frame.frametime`、`store.baseline.base_frametime` |
| 2 | `GPU 忙时比 (显卡饥饿判定)` | 3336 | `frame.gpu_busy_ratio`、`frame.gpu_busy`、`base.base_gpu_busy_ratio` |
| 3 | `CPU 核心状态 (降频 / CPU0 拥堵)` | 3380 | `hw.cpu0_usage`、`hw.cpu_freq`、`hw.cpu_usage`、`hw.cpu_total`、`base.base_cpu0_usage`、`base.base_core_freq` |
| 4 | `显存溢出 (溢出到系统内存)` | 3421 | `hw.vram_nonlocal/dedicated/total`、`hw.procs[*]["nonlocal"]`、`base.base_vram_*`、`PROC_EVENT_WHITELIST` |
| 5 | `页面文件换入 & 缺页嫌疑进程` | 3454 | `hw.page_in`、`hw.pagein_top[:3]`、`base.base_page_in` |
| 6 | `着色器编译活动 (±1 秒窗口)` | 3479 | `store.shader_activity(frame.wall_ts, window=1.0)`、`base.base_cache_files` |
| 7 | `磁盘 I/O 资产流式加载` | 3519 | `hw.io_by_name`（按 app 全名→去 .exe→前 15 字符回退匹配）、`hw.io_read_mb`、`hw.io_top`、`base.base_io_read_mb` |
| 8 | `网络同步吞吐 (多人 Tick)` | 3559 | `hw.net_rx_mb`、`hw.net_tx_mb`、`hw.io_by_name[*][1]`、`base.base_net_rx_mb` |
| 9 | `GPU 核心频率 / 撞墙降频` | 3598 | `hw.gpu_clock`、`hw.gpu_temp`、`base.base_gpu_clock` |
| 10 | `引擎 GC / 主线程锁死` | 3614 | `classify_gc_lock(cur_ft, frame.gpu_busy_ratio, frame.cpu_busy_ratio, base_ft)` L3611 |
| 11 | `会话级归因置信度` | 3634 | `store.session_correlations()` L3620、`store.count_spikes(base_ft*1.8)` L3624 |

硬件数据统一来自 `hw = store.snapshot_for(frame.hw_seq)` L3282；陈旧判定 `hw_age = frame.wall_ts - hw.ts`，`hw_stale = (hw_age is None) or (hw_age > MAX_HW_AGE)`(L3288–3291，`MAX_HW_AGE=1.2` L518)；`hw is None`（离线导入 `hw_seq=-1`）或 stale 时第 3/4/5/7/8/9 行输出 `⚪ ...本行不可判定`（文案各不同）。

### 5.4 渲染

`_render_diff_card(frame)` L5921：`rows = inspect(frame, store)` → 标题 → 每行 5 个单元格元组 `(text, fg, bg, align)`：
- `DANGER` → `bg="#3d1418"`、delta 色 `BAD`、verdict 色 `BAD`
- `WARN` → `bg="#38240b"`、delta/verdict `WARN`
- 其余 → `bg=None`、delta `GREEN`、verdict `FG`
（L5928–5940）。列对齐固定：dim/verdict 用 `"L"`，其余 `"C"`；`_ALIGN` L5943。

`_sync_table(table, cells)` L5948：行数变化才 `setRowCount`；否则按 `_table_cache[table]` 逐格比对，只改变化项（text/对齐/fg/bg）；缓存 `_table_cache[table] = cells`。元组顺序是 `(text, fg, bg, align)`，`None` 表示"不设色/不设对齐"。

### 5.5 抽屉开合

`_show_diff_drawer` L5563：已可见直接 return；`setMaximumHeight(16777215)` 清动画残留 → `show()` → `raise_()`。**故意不做高度动画**（注释 L5566–5571：QPropertyAnimation 在离屏/暂停时可能把抽屉卡在 0 高度）。`_on_close_diff_drawer` L5582 仅 `hide()`，保留锁定线与数据。

---

## 6. 横幅 / 结论逻辑 + 全部中文话术（可复用为"白话映射层"）

### 6.1 横幅三态

| 条件 | 样式 | 文本（verbatim） | 行号 |
|---|---|---|---|
| `len(frames) < 2` | `_banner_qss(GREEN)` | `🟡 正在等待帧数据…（%s）` % msg | 5687–5688 |
| `last.frametime >= base_ft*2.2 and base_ft > 0` | `_banner_qss(BAD, BAD)` | `🔴 刚刚发生卡顿（帧耗时 %.1fms / %.0f FPS）—— 原因: %s` % (frametime, fps, verdict) | 5793–5798 |
| 其它 | `_banner_qss(GREEN)` | `🟢 游戏运行丝滑（当前 %.0f FPS · 均值 %.0f FPS）· 显存 / CPU / 内存健康` % (last.fps, m["avg_fps"]) | 5799–5802 |
| 初始值 | — | `🟢 正在启动采集引擎…` | 4822 |
| 渲染异常（前 3 次） | — | `⚠ 界面渲染异常（已忽略并继续运行）：%s` | 5651 |
| 队列溢出追加 | — | `（队列溢出丢弃 %d 帧）` | 5662 |
| 引擎状态行 | GREEN/WARN | `● 引擎状态: %s` | 5664 |
| 离线导入后 | ACCENT | `● 离线回放: 已加载 %d 帧 [%s]` | 6399 |

`_banner_qss(edge, border=None)` L5827：`QFrame#BannerCard { background-color: %PANEL; border: 1px solid (border or BORDER_HOT); border-left: 5px solid edge; border-radius: 8px; }`（依赖 `apply_theme_to_globals` 写回的模块常量）。

### 6.2 `banner_verdict(rows, frame)`（L3702–3728，[BOUNDARY]）——**严重度/优先级排序**

```
cause_keys = ("GPU 核心频率", "磁盘 I/O", "网络同步", "GPU 忙时比",
              "显存溢出", "页面文件换入", "CPU 核心状态", "着色器编译", "引擎 GC")   # L3714-3716
```
1. 依次遍历 `cause_keys`，在 `rows` 里找 **第一个** `r.dim.startswith(key) and r.severity in ("DANGER","WARN")`，返回其 `r.verdict`（L3717–3720）。
2. 兜底 1：任意 `severity == "DANGER"` 的行（L3722–3724）。
3. 兜底 2：任意 `severity == "WARN"` 的行（L3725–3727）。
4. 全 NORMAL：`🟢 各项硬件指标均在平稳基线附近，无掉帧诱因`（L3728）。

关键设计：`帧表现`（症状）**被显式排除**在病因候选外；四新探针（频率/磁盘/网络）排在因果链上游，`GPU 忙时比` 等"结果类"在后。`severity` 取值域仅 `DANGER` / `WARN` / `NORMAL`。

### 6.3 三色流畅度条

- 数据：`m = store.summary_metrics(frames)`（**每 6 tick**，L5775–5776），取 `m["smooth_pct"] / m["stutter_pct"] / m["severe_pct"]`。
- 调用：`self.stutter_bar.update_ratios(...)` L5786；副标题 `流畅 %.1f%% · 微卡 %.1f%% · 严重 %.1f%%` L5787–5788。
- 卡片标题 `流畅度时间分布 (CapFrameX 风格)` L4832；初始副标题 `流畅 100% · 微卡 0% · 严重 0%` L4835。
- 绘制语义见 §7.4（GREEN / WARN / BAD 三段，按总和归一化）。

### 6.4 指标卡文案（L5778–5785）

| 卡 | 主值 | 副值 |
|---|---|---|
| `card_avg` | `"%.1f" % m["avg_fps"]` | `"均耗时 %.1f ms" % (1000/avg_fps)`（avg_fps>0 时，否则空） |
| `card_low1` | `"%.1f" % m["low1_fps"]` | `"P99 稳定性"` |
| `card_low01` | `"%.1f" % m["low01_fps"]` | `m.get("low01_degraded") ? "严重卡顿底线 (P99 退化)" : "严重卡顿底线"` |
| `card_bottle` | `"%.0f%%" % m["gpu_busy_pct"]` | `gpu_busy_pct > 85 ? "GPU 瓶颈型" : "CPU/调度瓶颈型"` |

### 6.5 白话话术全表（verbatim，行号；`%` 为原文格式符）

**横幅 / 状态行**
| 文本 | 行号 |
|---|---|
| `● 正在探测 PresentMon 引擎…` | 4726 |
| `● 引擎状态: %s` | 5664 |
| `🟢 正在启动采集引擎…` | 4822 |
| `🟡 正在等待帧数据…（%s）` | 5688 |
| `🔴 刚刚发生卡顿（帧耗时 %.1fms / %.0f FPS）—— 原因: %s` | 5797 |
| `🟢 游戏运行丝滑（当前 %.0f FPS · 均值 %.0f FPS）· 显存 / CPU / 内存健康` | 5801 |
| `⚠ 界面渲染异常（已忽略并继续运行）：%s` | 5651 |
| `● 离线回放: 已加载 %d 帧 [%s]` | 6399 |
| `🟢 各项硬件指标均在平稳基线附近，无掉帧诱因` | 3728 |

**诊断行结论（`DiffRow.verdict`）**
| 文本 | 行号 | sev |
|---|---|---|
| `🔴 恶性掉帧！画面出现肉眼可见的严重顿挫` | 3300 | DANGER |
| `🟠 明显微卡 (Stutter)，已打破操作平滑度` | 3302 | WARN |
| `🟢 处于基线平稳容差范围内` | 3304 | NORMAL |
| `⚪ 本次采集未拿到 GPU 忙时数据（PresentMon 需默认 metric 模式）—— 无法判定显卡饥饿` | 3324–3325 | NORMAL |
| `🔴 GPU 严重饥饿空转（忙时仅 %.0f%%，较基线 %.0f%% 下降 %.0f pt）！CPU 渲染提交线程卡死，未能及时向 GPU 投递 DrawCall` | 3327–3329 | DANGER |
| `🟠 显卡满载瓶颈（忙时 %.0f%%），此帧渲染压力过高（着色器/光栅负荷满溢）` | 3331–3332 | WARN |
| `🟢 负载相对平衡（GPU 忙时 %.0f%%）` | 3334 | NORMAL |
| `🔴 CPU0 满载拥堵 (%.0f%%)！系统中断或游戏主线程卡死在 0 号核，建议在 Process Lasso 中取消勾选 CPU0` | 3363–3364 | DANGER |
| `🟠 CPU%d 骤降频至 %.0fMHz（基线 %.0fMHz，-%.0f%%）！触发主板 VRM 供电墙或 CPU 温度墙` | 3366–3368 | WARN |
| `🟢 CPU 调度平稳（总体 %.0f%%，CPU0 %.0f%%）` | 3370 | NORMAL |
| `⚪ %s，本行不可判定`（填 `该帧没有绑定到硬件快照（离线导入数据无历史硬件记录）` / `硬件探针采样间隔 0.4s，本帧对应的硬件状态已过期，给出结论会有误导性`） | 3387–3390 | NORMAL |
| `🔴 疑似显存溢出至系统内存：非本地占用 %.0f MB（较基线 +%.0f MB），本地显存已用 %.0f%%。若同时伴随帧时间飙升，多为贴图/材质超预算，请降低【贴图质量】` | 3410–3412 | DANGER |
| `🟠 显存占用临界 (%.0f%%)，即将触顶引发换出` | 3414 | WARN |
| `🟢 显存容量充足（已用 %.0f%%，非本地 %.0f MB）` | 3416 | NORMAL |
| `🔴 页面输入速率骤增 %.0f MB/s（较基线 +%.0f），疑似物理内存吃紧导致从 pagefile 读回数据。缺页活跃进程（参考，非确证）: %s` | 3446–3448 | DANGER |
| `🟠 存在内存换入压力 (%.0f MB/s，较基线 +%.0f)` | 3450 | WARN |
| `🟢 内存充裕，页面输入速率平稳 (%.1f MB/s)` | 3452 | NORMAL |
| `无明显缺页进程` | 3441 | — |
| `🔴 着色器编译爆发！前后 1 秒内新增 %d 个缓存文件 (+%.1f MB)，引发 PSO 流水线停顿` | 3472–3473 | DANGER |
| `🟠 检测到即时编译活动 (+%d 个文件)` | 3475 | WARN |
| `🟢 无即时着色器编译写盘活动` | 3477 | NORMAL |
| `🔴 硬盘 I/O 达 %.0f MB/s（%s）：游戏正在从硬盘流式加载模型/贴图，硬盘随机读取已成瓶颈 ⇒ 建议把游戏安装到 NVMe 固态硬盘，并关闭同盘的下载/解压任务` | 3510–3512 | DANGER |
| `🟠 磁盘读取偏高 %.0f MB/s（%s），疑似场景资产流式加载` | 3514–3515 | WARN |
| `🟢 磁盘读取平稳 (%.1f MB/s)，无资产流式加载压力` | 3517 | NORMAL |
| `（近似值）` / `（全场最繁忙）` / `进程未知` / `⚪ 硬件快照不可用，本行不可判定` | 3520 / 3507 / 3512,3515 / 3529 | — |
| `🔴 网络接收速率从基线 %.0f KB/s 骤降到 %.0f KB/s，同时出现掉帧 ⇒ 疑似丢包 / 服务器 Tick 延迟，游戏引擎在等待服务器同步（多人模式常见顿挫）` | 3548–3551 | DANGER |
| `🟠 网络接收速率明显下降 (%.0f KB/s，基线 %.0f)` | 3553–3554 | WARN |
| `🟢 网络吞吐正常 (↓%.0f KB/s)` | 3556 | NORMAL |
| `⚪ 本机未提供 GPU 核心频率（需要 AMD ADL 或 NVIDIA NVML 桥接），撞墙降频不可判定` | 3577–3578 | NORMAL |
| `🔴 GPU 核心频率撞墙降频：%.0f → %.0f MHz (-%.0f%%)%s。显卡触发温度墙/功耗墙，帧时间随之升高 ⇒ 清灰改善风道、提高风扇曲线或降低画质功耗` | 3588–3591 | DANGER |
| `🟠 GPU 频率小幅回落 %.0f→%.0f MHz (-%.0f%%)` | 3593–3594 | WARN |
| `🟢 GPU 频率稳定 (%.0f MHz%s)` | 3596 | NORMAL |
| `🟢 无严重尖峰（%.1fms < 阈值 %.0fms），GC 不成立` | 3675 | NORMAL |
| `⚪ 本帧缺少 GPU 忙时数据（需默认 metric 模式），GC 不可判定` | 3680 | NORMAL |
| `🔴 引擎底层 GC / 主线程锁死：帧耗时 %.0fms 但 GPU 仅 %.0f%%、CPU 仅 %.0f%% —— 显卡与 CPU 渲染线程都没在干活，属于游戏软件层面的代码拥堵（GC 停顿 / 主线程锁 / 同步等待），非硬件瓶颈` | 3684–3687 | DANGER |
| `🟠 尖峰由显卡满载造成（GPU %.0f%），不是 GC` | 3691 | WARN |
| `🟠 尖峰由 CPU 线程满载造成（CPU %.0f%%），不是 GC` | 3695 | WARN |
| `🟢 尖峰期间 GPU/CPU 均有负载（%.0f%% / %.0f%%），非 GC 型卡顿` | 3698 | NORMAL |
| `📊 会话级证据：%s`（部件 `%s %d/%d (%.0f%%·置信度%s)`） | 3630 / 3628 | NORMAL |
| `📊 会话级证据：样本不足，暂无统计结论` | 3632 | NORMAL |
| `本次会话共 %d 次卡顿` / `按诱因统计重合率` | 3635 / 3636 | — |
| 不可判定填充：`无该时刻硬件快照` / `硬件快照已过期 (%.1fs)` / `离线数据无历史显存记录` / `离线数据无历史内存记录` | 3385,3429,… / 3385 / 3431 / 3464 | — |

**极客探针条（`_refresh_geek_panel` L6069–6136）**
| 文本 | 行号 |
|---|---|
| `%.1f MB/s%s`（%s=`, Top: <名> %.1fMB/s` 或空） | 6101 / 6094 |
| `↓%.2f ↑%.2f MB/s · 进程IO %.2f MB/s` | 6107 |
| `%.0f MHz` + ` (峰值 %.0fMHz · %.0f%%)` + ` ← 撞墙降频` | 6115 / 6117 / 6121 |
| `不可用（未检测到 ADL / NVML 桥接）` | 6124 |
| `等待帧数据…` | 6134 |
| 探针行标签：`💿 磁盘资产流式读取` / `🌐 网络吞吐 (接收)` / `🌡 GPU 核心频率` / `⏳ 引擎 GC / 主线程锁` | 4090–4093 |
| 阈值：`io_mb >= 50 → DANGER`、`>= 20 → WARN`（用字面量 50.0/20.0，L6097–6100，与 `IO_STREAM_WARN_MB` 同值但**硬编码**）；`clk < base_clk*0.85 → DANGER`(L6119) | |

**硬件面板文字（`_refresh_hardware_tabs` L5989–6064）**
| 文本 | 行号 |
|---|---|
| `CPU 总体: %.0f%%   ·   CPU0: %.0f%%   ·   峰值: %.0f MHz` | 5996 |
| `专用显存: %s / %s (%.0f%%) · 非本地溢出: %s` | 6004 |
| `专用显存: %s / 未知 · 非本地溢出: %s` | 6010 |
| `物理内存交换: 换入 %.1f MB/s   ·   换出 %.1f MB/s` | 6025 |
| `缺页活跃嫌疑进程: %s`（`、` 连接 `%s(%.0fMB/s)`，取前 3） | 6028–6029 |
| `着色器新增: +%d 文件 (+%.1f MB)   ·   GPU 3D 利用率: %.1f%%` | 6032 |
| 事件流行 `%H:%M:%S` / `ev.kind` / `%s —— %s`（text, advice） | 6060–6062 |
| 表格空值 `-` / `+%d (%.1fMB)` / `%.1f MB` | 6020,6044,6043 |

---

## 7. 每个控件的对外契约

### 7.1 `StutterBarWidget(QWidget)` — L3735–3776
- `__init__(self, parent=None)` L3738：`setFixedHeight(12)`；初值 `smooth=100.0, stutter=0.0, severe=0.0`。
- `update_ratios(self, smooth, stutter, severe)` L3745：三个 float，`max(0.0, float(x))` 夹紧，不归一化、不校验总和，随后 `self.update()`。
- `paintEvent(self, event)` L3751：Antialiasing；圆角半径 `h/2`；先画 `PANEL2` 圆角底轨；`tot = smooth+stutter+severe`，`tot<=0` 直接 return（只剩底轨）；`w_smooth=int(w*smooth/tot)`、`w_stutter=int(w*stutter/tot)`、`w_severe=max(0, w-前两者)`（**余数归 severe**）；GREEN 圆角段 → WARN 直角段（`drawRect`，无圆角）→ BAD 圆角段。
- 无 `restyle`：颜色直接读模块级 `GREEN/WARN/BAD/PANEL2`，主题写回后自动跟随。

### 7.2 `MetricCard(QFrame)` — L3779–3827
- `__init__(self, title: str, unit: str = "", highlight: bool = False, parent=None)` L3782：`setObjectName("MetricCard")`；内联 QSS（`% PANEL/BORDER/BORDER_HOT`）；`QVBoxLayout` margins (16,10,16,10) spacing 2；子控件 `lbl_title`（title）、`lbl_val`（`"--"`）、`lbl_sub`（unit）；存 `_highlight` / `_title` / `_unit`；末尾 `restyle()`。
- `restyle(self)` L3807：用 `CURRENT_THEME` 重刷三标签（title 12px bold dim；val 26px bold `MONO_FAMILY`，`highlight → t.accent else t.fg`；sub 10px dim）与卡片 QSS（panel/border）——**注意 restyle 的卡片 QSS 不含 `:hover`**，与 `__init__` 的 QSS 不同（hover 边框在首次主题切换后丢失）。
- `set_value(self, val_str: str, sub_str: str = "")` L3824：`sub_str` 为空则保留原副标题。

### 7.3 `CpuHeatmapGrid(QWidget)` — L3902–4078
- 类常量 `BLOCK_W=96`、`BLOCK_H=46`、`GAP=6`（L3916–3918）。
- `__init__(self, n_cores=0, parent=None)` L3920：`n=max(0,int)`；`usage/freq=[0.0]*n`；`max_mhz=4000.0`；`_last_paint_usage=[None]*n`；主题字段 `_bg/_inner_bg/_border/_dim/_fg/_bad` 取自模块常量；`setMinimumHeight(BLOCK_H+12)`；SizePolicy Expanding×2。
- `set_values(self, usages, freqs=None)` L3941：`usages` 为每核占用率（**单位 %，0–100**，内部夹紧）；`n` 变化即重建数组；`freqs` 为 MHz 列表（截断到 n）。**重绘门控**：仅当某核占用率与上次绘制值差 `>= 0.5`（pt）时 `update()`（L3970）。**频率变化不参与门控** → 频率条可能滞后到下次占用率变化才重绘 `[UNCLEAR]`（注释声称 20MHz 门控，代码未见 L3945–3947）。
- `set_max_mhz(self, mhz)` L3981：`float`，仅 `> 100` 才生效（频率条 100% 基准）。
- `restyle(self, theme=None)` L3989：从 `theme or CURRENT_THEME` 取 panel/panel2/border/dim/fg/bad 后 `update()`。
- `cols()` / `rows()` L4009/4012（自检接口，测试脚本会读）；`sizeHint` L4015（`cols*(bw+GAP)+GAP` × `rows*(bh+GAP)+GAP`）；`minimumSizeHint` L4020（3 列 × 2 行）。
- `_grid()` L4001：`cols = max(1, (w-GAP)//(BLOCK_W+GAP))`，`rows = ceil(n/cols)`。
- `paintEvent` L4025：先铺 `_bg` 全矩形；`n<=0` → 居中 `等待 CPU 采样…`(L4034) 后 return；逐核 `divmod(i, cols)` 定位；`heat_color(u/100.0)` 圆角块 `QRectF(x, y, bw, bh-6)` r=4；文字色 `_text_on(col)`；左上 `"C%d"` (Segoe UI 8)；右侧 `"%.0f%%"` (Consolas 12 bold, AlignRight|AlignVCenter)；底部 4px 频率条：底 `_inner_bg`，前景 `#7fd1ff`（`ratio<0.6` 时 `#5b7fa8`），宽 `max(3.0, bw*ratio)`，`ratio = freq/max_mhz` 夹紧 0–1；末尾记 `_last_paint_usage[i]`。**PyQt6 坑**：`drawRoundedRect` 必须全用 `QRectF` + float，混用 int/float 会抛 TypeError 导致整块不画（L4049–4052）。
- 色标：`HEAT_STOPS` L3852–3859（0→(16,42,26)、0.30→(34,120,58)、0.55→(196,140,26)、0.70→(222,116,20)、0.85→(236,62,44)、1.00→(255,42,42)），`heat_color(ratio)` L3862 分段线性，`_text_on(color)` L3893 亮度阈值 `0.62` → `#0a0f14` / `#ffffff`。
- 用法：`heat_geek = CpuHeatmapGrid(cpu_count())` L5038 / `heat_cpu = CpuHeatmapGrid(cpu_count())` L5152 + `set_max_mhz(max(per_core_max_mhz(cpu_count()) or [4000]))` L5153–5155。

### 7.4 `ProbeReadoutBar(QWidget)` — L4081–4140
- `ROWS` L4089–4094：`(("io","💿 磁盘资产流式读取"), ("net","🌐 网络吞吐 (接收)"), ("clk","🌡 GPU 核心频率"), ("gc","⏳ 引擎 GC / 主线程锁"))`。
- `__init__(self, parent=None)` L4096：`QVBoxLayout` margins 0、spacing 3；每行 `QHBoxLayout`（label 固定 `setMinimumWidth(150)`，11px DIM；value `QLabel("—")` 11px `MONO_FAMILY`）；值控件存 `self.lbl[key]`。**无 paintEvent**（纯 QLabel 组合）。
- `set_row(self, key: str, text: str, severity: str = "NORMAL")` L4116：未知 key 静默 return；**幂等门控**（text 与 `w._sev` 都相同则跳过）；`severity ∈ {"NORMAL","WARN","DANGER"}` → `FG / WARN / BAD`；把 `severity` 存到 `QLabel._sev`（动态属性）。
- `restyle(self, theme=None)` L4133：把 4 个值标签改回 `t.fg` 并清 `_sev=None`（**丢弃 severity 着色**，下次 `set_row` 若文本未变则因 `_sev=None` 会重上色——依赖 `_sev` 语义）。
- 调用点：`_refresh_geek_panel` L6069（key `"io"/"net"/"clk"/"gc"`，见 §6.5）。

---

## 8. 主题流

### 8.1 全局流

```
CURRENT_THEME (L346, = THEME_DEEP_SPACE)
  → apply_theme_to_globals(theme, module_globals=None)  L349-375
      写回 BG/PANEL/PANEL2/BORDER/BORDER_HOT/FG/DIM/ACCENT/ACCENT_D/WARN/BAD/PURPLE/ORANGE/GREEN  L357-370
      pg.setConfigOptions(background=theme.chart_bg, foreground=theme.chart_text)  L372（try/except 吞掉）
  首次调用 L379 apply_theme_to_globals(CURRENT_THEME)
```
模块常量（L492–509）：`BG/PANEL/PANEL2/BORDER/BORDER_HOT/FG/DIM/ACCENT/ACCENT_D/WARN/BAD/PURPLE/ORANGE/GREEN/CYAN_DIM/FONT_FAMILY/MONO_FAMILY`。**大量控件直接读这些名字**（`setStyleSheet("color: %s" % DIM)` 之类），所以"写回同名模块变量"是零改动跟随主题的前提（注释 L350–355）。

`Theme` 数据类字段（由 L357–370 + L372 反推，定义 L186/194）：`bg, panel, panel2, border, border_hot, fg, dim, accent, accent_d, warn, bad, purple, orange, green, key, name, custom_bg, chart_bg, chart_text, chart_grid`，另有 `.copy()` L219。

### 8.2 `_apply_theme(theme, persist=True)` L4430–4484 顺序

1. `global CURRENT_THEME; CURRENT_THEME = theme`(L4439–4441)
2. `apply_theme_to_globals(theme, globals())`(L4442)
3. `self._apply_global_stylesheet()`(L4443) → L4265–4425：巨型 QSS，全用具名占位符 `%(bg)s…`（少写即 KeyError，注释 L4269）；覆盖 `QMainWindow/QWidget/QTableWidget/QHeaderView/QTabWidget/QTabBar/QPushButton/QComboBox/QLineEdit/QProgressBar/QToolTip/QScrollBar/QScrollArea` + 对象名 `Sidebar/GeekPanel/NavButton/NavPrimary/Card/BannerCard/ChartCard/DrawerCard/StutterCard/RocketButton/DrawerCloseButton/HwPanel`；末尾把同一 QSS 下发到 `hw_panel`（`for attr in ("hw_panel",)` L4419–4425）。
4. 图表重绘 L4445–4467：`plot_widget.setBackground(theme.chart_bg)`；`plotItem.vb.setBackgroundColor(...)`；`vb.background` 的 brush/pen 改色（因为 ViewBox 自带背景 rect）；`left`/`bottom` 轴 `setPen/setTextPen(chart_text)`；`showGrid(x,y,alpha=0.25, pen=chart_grid)`。
5. `self._restyle_chart()`(L4468) → L4613–4657：按 `CURRENT_CHART_STYLE` 设 `curve_ft` pen/brush（CapFrameX 走 `CX_COLOR_FRAMETIME`，HUD 走 `t.accent` + `_update_gradient()`）；`curve_lowenv` WARN 1.3；`curve_low01` BAD 1.1 DotLine；legacy 三线 `hide()`；CapFrameX 下 `_fill_item` brush 透明；最后 `_update_legend()`。
6. `self._restyle_static_labels()`(L4470) → L4486–4562：
   - 对 16 个标签用 `re.sub(r"color:\s*#[0-9a-fA-F]{6}", ...)` 替换 color（无 `color:` 则追加）：`lbl_engine_badge, banner_text, lbl_data_source, lbl_settings_status, lbl_baseline_info, lbl_diff_title, lbl_cpu_summary, lbl_vram_info, lbl_mem_summary, lbl_shader_status, lbl_stutter_sub, lbl_logo, lbl_logo_sub, lbl_target_caption, lbl_geek_title, lbl_geek_probe_title`（L4489–4507）。
   - 4 张 MetricCard `.restyle()`（L4524–4527）；`_set_style` 重刷 `card_topbar / card_chart / diff_drawer / banner_frame / card_stutter`（L4529–4544）。
   - `_restyle_settings_page()`(L4549)；`heat_geek`/`heat_cpu`.`restyle(t)`(L4554–4557)；`probe_bar.restyle(t)`(L4558–4560)。
7. 持久化 L4471–4480（见 §9）。
8. 异常 → `DIAG.report("_apply_theme", ex)`，返回 False；成功返回 True（L4481–4484）。

### 8.3 主题下拉框与图表样式下拉框

- 下拉框位置：**设置页**（`hw_tabs` 第 6 页 `⚙ 设置`），非侧边栏。`combo_theme` L6785–6797：items = `THEME_PRESETS` 的 `(th.name, key)` + `("自定义背景色…","custom")`；`findData(CURRENT_THEME.key)` 初始化选中。
- `_on_theme_combo_changed(index)` L6932 → `key = itemData(index)`；`_suppress_theme_event` 守卫（**该属性从未被赋值**，getattr 恒为 None → 守卫实际不生效 `[UNCLEAR]` L6937）→ `_on_theme_changed(key)` L4564：`custom` → `QColorDialog.getColor(QColor(CURRENT_THEME.custom_bg or "#101018"), self, "选择自定义背景色")` → `theme_from_custom_bg(color.name(), "自定义 (%s)" % color.name())`；否则 `THEME_PRESETS.get(key).copy()`（`.copy()` 必要，避免改到全局预设）。
- 图表样式：`CURRENT_CHART_STYLE` L384（默认 `CHART_STYLE_CAPFRAMEX="capframex"` L382）→ `combo_chart_style` L6801–6811 → `_on_chart_style_combo_changed` L6941 → `_on_chart_style_changed(key)` L4577：校验 key ∈ {capframex,hud} → 设全局 → `_restyle_chart()` → 写配置 `chart_style` → `_cfg_status("图表样式已切换为 %s" % ("CapFrameX 风格" if key==CHART_STYLE_CAPFRAMEX else "赛博 HUD"))`。
- 图例（`_update_legend` L4594–4611，verbatim）：CapFrameX `<span style='color:#2197F3'>■</span> 帧时间(ms)`；HUD `<span style='color:#19d3ff'>■</span> 瞬时帧时间(ms)`；`<span style='color:%s'>━</span> 1%% Low 平滑包络`(warn)；`<span style='color:%s'>┈</span> 0.1%% Low 包络`(bad)；`<span style='color:%s'>●</span> 异常事件打点`(bad)；统一 11px dim。

### 8.4 需要 re-theme 钩子的控件

| 控件/对象 | 钩子 |
|---|---|
| `MetricCard` × 4 | `restyle()`（L3807），由 `_restyle_static_labels` 调 |
| `CpuHeatmapGrid` (`heat_cpu`, `heat_geek`) | `restyle(theme)`（L3989） |
| `ProbeReadoutBar` (`probe_bar`) | `restyle(theme)`（L4133） |
| `StutterBarWidget` | 无钩子（paint 时读模块常量，自动跟随） |
| 设置页所有带色 QLabel | `_track_setting_label`/`_style_setting_label`/`_restyle_settings_page`（L6714/6723/6737） |
| `hw_panel`（独立窗口） | 重新整份 `setStyleSheet(qss)`（L4419–4425） |
| 硬编码 setStyleSheet 的标签 | `_restyle_static_labels` 的 16 元组（L4489–4507）+ 图例 `_update_legend` |
| QFrame 卡片 | `_set_style` 去重写样式（L4529–4544） |
| `plot_widget` / 轴 / 网格 / 曲线 | `_apply_theme` 内联 L4446–4465 + `_restyle_chart` |

---

## 9. 配置与持久化

| 项 | 值 | 行号 |
|---|---|---|
| 文件名 | `CONFIG_FILENAME = "framelab_config.json"` | 7341 |
| 位置 | `config_path()` = `os.path.dirname(os.path.abspath(__file__))` + 文件名 → **与 FrameLab.py 同目录**，非 `%APPDATA%` | 7405–7409 |
| 日志 | `~/.framelab/framelab_diag.log`（`DiagLog`，`main()` 内实例化；模块级默认 `DIAG = DiagLog(enabled=False)` L640） | 8056–8060 / 640 |
| 阈值键 → 全局量 | `CONFIG_SCHEMA`（12 项：[BOUNDARY] L7345–7370）：`spill_threshold→SPILL_THRESHOLD(int)`、`freq_drop_ratio→FREQ_DROP_RATIO(float)`、`cache_burst_files→CACHE_BURST_FILES(int)`、`cpu0_jam_threshold→CPU0_JAM_THRESHOLD(float)`、`cpu0_idle_others→CPU0_IDLE_OTHERS`、`migrate_freq_gap→MIGRATE_FREQ_GAP`、`pm_buffer_size→PM_BUFFER_SIZE(int,2 的幂)`、`baseline_window→BASELINE_WINDOW`、`baseline_ema→BASELINE_EMA`、`baseline_spike_ratio→BASELINE_SPIKE_RATIO`、`baseline_max_spike_frac→BASELINE_MAX_SPIKE_FRAC`、`max_hw_age→MAX_HW_AGE` | |
| 非阈值键 | `CONFIG_EXTRA_KEYS = ("hotkey","presentmon_path","blacklist_extra","theme","chart_style","custom_bg")` | 7373 |
| 默认值 | `DEFAULT_CONFIG` L7376–7395（`hotkey:"Ctrl+F9"`、`theme:"deepspace"`、`chart_style:"capframex"`、`custom_bg:""`） | |
| 原子写 | `Config.save()`：写 `<path>.tmp` → `os.replace`；`json.dump(indent=2, ensure_ascii=False, sort_keys=True)`；失败清理 tmp 并 return False | 7496–7515 |

**窗口读取的配置键**：`theme`(L4475 写 / L6478 读)、`custom_bg`(L4477 / L6480)、`chart_style`(L4587 / L6498)、`hotkey`(L6516, L6980, L7020, L7165)、`presentmon_path`(L6627 读 / L7130 写)、`blacklist_extra`(仅 `Config.update` 处理，窗口不读)；阈值键经 `cfg.apply_to_globals(globals())`(L6455, L7056, L7076, L7087) 与 `cfg.baseline_kwargs()`(L6458, L7104) 生效。

**加载流程 `_init_ux` L6450–6535**：`Config.load()` → `_ux_config` → `apply_to_globals` → **把新阈值同步到已存在的 `store.baseline` 实例**（`bl.ema/window/spike_ratio/max_spike_frac`，L6457–6466）→ 首次运行（`not cfg.existed`）落盘模板（L6467–6472）→ 恢复主题（`persist=False` 静默应用 + `combo_theme` 同步、`blockSignals` 包裹，L6477–6495）→ 恢复图表样式（同样 `blockSignals`，L6497–6512）→ 注册热键 → `_update_hotkey_label()` → 同步 `edit_hotkey` 显示值（L6523–6529）→ `_schedule_admin_prompt()` → `_update_presentmon_hint()`。

**写盘时机（全部即时，无退出保存）**：`_apply_theme(persist=True)` L4471–4478；`_on_chart_style_changed` L4587–4588；`_on_apply_hotkey_sequence` L7018–7021；`_on_save_thresholds` L7054；`_on_browse_presentmon→_apply_presentmon_path` L7130–7131；首次运行 L6470。`_on_reset_thresholds`(L7064) / `_on_reload_config`(L7082) **不落盘**（前者提示"尚未落盘"）。`closeEvent` **不保存**。

---

## 10. 重写时最容易踩碎的东西

### 10.1 跨方法引用的私有属性名（拆模块后必须仍是同一实例上的同名属性，或显式改造成接口）

- 图表：`plot_widget`, `plot_widget.plotItem.vb`, `curve_ft`, `curve_lowenv`, `curve_low01`, `curve_low1`, `curve_ma`, `curve_lowfps`, `scatter_events`, `v_line`, `h_line`, `locked_line`, `_fill_item`, `_gradient_item`, `_sim_watermark`, `_y_guard`, `_chart_mode`, `_chart_mode_dirty`, `_zh_pg_menus_done`.
- 数据窗口：`_display_frames`, `_display_ts`, `_display_idx`, `_display_x`, `_auto_dom`, `_env_tick`, `_env_cache`, `_metric_cache_ui`, `_table_cache`, `_style_cache`, `selected_frame`.
- 控件：`banner_frame`, `banner_text`, `card_stutter`, `stutter_bar`, `lbl_stutter_sub`, `card_avg/low1/low01/bottle`, `card_topbar`(仅 getattr 引用，**构建代码里未见赋值** `[UNCLEAR]` L4529), `card_chart`, `diff_drawer`, `lbl_diff_title`, `lbl_baseline_info`, `table_diff`, `geek_panel`, `heat_geek`, `heat_cpu`, `probe_bar`, `lbl_geek_title`, `lbl_geek_probe_title`, `lbl_cpu_summary`, `lbl_vram_info`, `lbl_mem_summary`, `lbl_pagein_top`, `lbl_shader_status`, `table_vram`, `table_shader`, `table_events`, `hw_panel`, `hw_tabs`, `tab_cpu/vram/mem/shader/events/settings`, `sidebar`, `combo_procs`, `combo_chart_mode`, `combo_theme`, `combo_chart_style`, `btn_*`（§2.1/2.4 全表）、`edit_custom_bg`, `edit_pm_path`, `edit_hotkey`, `cfg_edits`, `progress_import`, `lbl_hotkey`, `lbl_pm_hint`, `lbl_data_source`, `lbl_engine_badge`, `lbl_logo`, `lbl_logo_sub`, `lbl_target_caption`, `lbl_hotkey_tip`, `lbl_settings_status`, `lbl_legend_1..5`.
- 状态：`store`, `q`, `is_paused`, `target_app`, `_data_source`, `_offline_mode`, `_mark_seq`, `_ux_config`, `_hotkey`, `_admin_prompt_shown`, `_admin_dialog`, `_ui_error_count`, `_import_progress`（**只写不读，L7178/7189**）, `_pending_status`, `_settings_labels`, `_suppress_theme_event`（**从未赋值，L6937 的守卫是死代码**）, `core_widgets`（**故意保留的空列表，供旧脚本/外部引用，L5184**）, `lbl_legend_5`（**故意保留的空标签，L4875**）。

### 10.2 顺序 / 生命周期契约（"必须先调 X 再调 Y"）

1. `hw_engine.start()` + `_start_presentmon_bridge()`（L4187/4190）**早于** `_init_ui()`；任何 UI 构建期都不得依赖"队列为空"。
2. `_init_ui()` → `_init_ux()` → `ui_timer.start()` 三步顺序不可换（§1.1）。
3. `_init_ux` 内：`Config.load` → `apply_to_globals` → `baseline` 实例同步 → 主题 → 图表样式 → 热键注册 → 提权提醒 → PM 指引（后者读 `_ux_config`，L6627）。
4. `_drain_queue()` 必须先于 `_pending_status` 的读取（L5656 → L5658），否则状态行永远滞后一 tick。
5. `_apply_theme` 内：写回模块常量 → 全局 QSS → 图表 → 静态标签 → 持久化；`_restyle_chart` 依赖 `CURRENT_CHART_STYLE` 已最终确定。
6. `_reset_view_to_global` 的 6 步顺序（§4.3 L5399–5436）；`_clamp_y_nonneg` 的 `_y_guard` 必须 `finally` 复位。
7. `_on_chart_mode_changed`：先置 `_chart_mode_dirty`，再改鼠标/范围，最后（未暂停时）主动 `_refresh_ui()`。
8. CSV 导入：停桥（后台线程） → 丢弃队列残留 frame → `store.frames.clear()` → `baseline.reset()` → 追加；且 `finally` 必须恢复 `is_paused=False`。
9. 目标进程 / 导入后必须 `_chart_mode_dirty = True`（L6164 / L6395），否则曲线不重建。
10. `_render_diff_card` 必须在 `_show_diff_drawer` 之前或同帧（L5269→L5271）。
11. `combo_procs` 重填必须 `blockSignals`（L7247–7255），否则 2.5 s 一次重置 `selected_frame`/抽屉。
12. `combo_theme` / `combo_chart_style` 程序化 setCurrentIndex 必须 `blockSignals`（L6489/6506），否则启动即触发一次写盘。
13. 换桥必须"后台 stop + 立刻换新"（`"BridgeStop"`/`"BridgeStopImport"`/`"BridgeStopPM"`，L6172/6370/7139），不得同步 `stop(wait=True)`（会冻 UI）。
14. `target_app` 变更**不得**重启 PresentMon（L6147–6154）；仅桥死时重启（L6165）。

### 10.3 hasattr / getattr 守卫（说明何处允许"缺件不崩"）

`getattr(self,"hw_panel",None)` L4420/7283；`getattr(self,"_hotkey",None)` L6974/7263；`getattr(self,"heat_geek",None)` L6081；`getattr(self,"probe_bar",None)` L6087；`getattr(self,"_sim_watermark",None)` L6678/6689；`getattr(self,"_y_guard",False)` L5373；`getattr(self,"_ui_error_count",0)` L5648；`getattr(self,"_settings_labels",[])` L6745；`getattr(self,"core_widgets")`（外部）；`hasattr(card,"restyle")` L4526/4556/4559；硬件快照字段一律 `getattr(hw, "io_read_mb"/"io_top"/"net_rx_mb"/"net_tx_mb"/"io_other_mb"/"gpu_clock"/"gpu_clock_max"/"io_by_name", 默认)` L6092–6113；`getattr(ev,"frame",None)` L5460；`getattr(sys,"frozen",False)` L7860。

### 10.4 会静默改变行为的小东西（重写清单）

- `_sync_table` 的四元组顺序 `(text, fg, bg, align)` 与 `None` 语义（L5948–5978）；写错不会报错，只会安静地不上色。
- `pg_symbol()` 白名单（L558/562）：非法 symbol → `KeyError` 崩 UI。
- `attach_shadow` **禁止**用于含 `PlotWidget` 的图表卡（L469–487 注释、L5054–5056）；`plot_widget` 本身也不挂 effect。
- `CpuHeatmapGrid.paintEvent` 里 `drawRoundedRect` 全 `QRectF`（L4049–4052）。
- `StutterBarWidget` 余数归 severe、WARN 段无圆角（L3762–3776）。
- `MetricCard.restyle()` 的 QSS 丢 `:hover`（与 `__init__` 不一致）。
- `set_values` 只按占用率门控重绘、`old` 变量未使用（L3967）。
- `_on_import_progress` 与 `_import_progress` 只写不读（进度条真实反馈来自 `rd.total_bytes`，且 `setRange(0,0)` 下 `setValue` 无视觉意义）。
- `_on_apply_hotkey`（L7151）是死代码且会抛 `AttributeError`（`QKeySequenceEdit.text()`）。
- `_update_capframex_series` / `_compute_moving_average`（L5869/5899）无调用点 → `curve_ma/curve_low1/curve_lowfps` 永远无数据。
- 顶部/底部旧入口：`⚙ 设置` 现在**全局唯一**（v3.1 有两个，L4695 注释）；`card_topbar`（L4529 getattr）在构建代码中未见赋值 —— 若重写要保留主题重刷，需补上或删掉该引用。
- `statusBar()` 三处（L6605/6659/6706）依赖 `QMainWindow`；若新外壳不是 `QMainWindow` 会 AttributeError。
- `QApplication.setStyle("Fusion")` 在 `main()` L8063；`SetProcessDpiAwareness(1)` L8051 —— 影响全局 QSS 观感，勿丢。
- `app.exec()` 的 `finally` 里 `DIAG.note("exit", ...)` + `DIAG.close()`（L8070–8072）。

### 10.5 引擎层调用面（UI 依赖的引擎 API，重写后必须仍可用）

`FrameStore`：`.add_frame(rec)`、`.add_snapshot(snap)`、`.add_event(ev)`、`.frames`、`.snapshots`、`.events`、`.snapshot_for(seq)`、`.visible_frames(target)`、`.dominant_app()`、`.summary_metrics(frames)`（键：`avg_fps, low1_fps, low01_fps, low01_degraded, gpu_busy_pct, smooth_pct, stutter_pct, severe_pct`）、`.update_baseline(frames)`、`.shader_activity(ts, window)`、`.session_correlations()`、`.count_spikes(thr)`、`.baseline`。
`BaselineTracker`：`.reset()`、`.base_frametime`、`.base_fps`、`.base_smooth_frames`、`.base_gpu_clock`、`.base_gpu_busy_ratio`、`.base_cpu0_usage`、`.base_core_freq`、`.base_vram_nonlocal/dedicated/total`、`.base_page_in`、`.base_cache_files`、`.base_io_read_mb`、`.base_net_rx_mb`；可写字段 `.ema/.window/.spike_ratio/.max_spike_frac`。
`PresentMonBridge`：`.start()`、`.stop(wait,timeout)`、`.is_alive()`、`.target_process`、`.exe_path`、`.simulation_mode`、`.etw.session_name`。
`HardwareEngine`：`.start()`、`.stop()`、`.is_alive()`、`.join(timeout)`、`.names`（NameTable）。
`EtwSessionManager`：`list_sessions()`、`cleanup_stale(skip=)`。`BoundedQueue`：`put/get_nowait/take_dropped`。

## 11. v1.0.0 变更清单（正式更名 + 版面零抖动 + CPU 频率/功率折线）

| 变更 | 位置 | 说明 |
|---|---|---|
| 品牌/版本 | `framelab/__init__.py`、`ui/main_window.py`、`build_tools/version_info.txt` | 名称固定 `FrameLab`，版本 `1.0.0`；窗口标题 `FrameLab 1.0.0 · 游戏卡顿一站式体检台`；EXE 属性 `1.0.0.0` |
| 版面零抖动 | `ui/widgets.ElidedLabel` + `ui/main_window.BANNER_HEIGHT` + `ui/diagnosis.MiniDiagnosisCard.show_idle()` | 横幅固定 96px、所有动态文案单行省略；病历卡常驻 110px 占位；4 张卡高度锁死 |
| 文案瘦身 | `ui/main_window.py`、`ui/pages.py`、`ui/gauge.FpsCard`、`stats.bottleneck` | 删 `lbl_grade_sub` / `lbl_logo_sub` / `lbl_nav_caption` / `SUBTITLE` / `lbl_subtitle` / `lbl_hint`；卡片标题去英文括注；结论改短句 |
| CPU 频率/功率双折线 | `ui/cpu_trend.py`（新）+ `ui/pages.CpuPage.trend` + `main_window._push_cpu_trend` | 固定 104px、无坐标轴、双线各自归一化、按 `hw.seq` 去重；功率估算一律 `≈` |
| CPU 功率探针 | `probes.CpuPowerProbe`（新）+ `models.HardwareSnapshot.cpu_power/cpu_power_est` + `engines/hardware._cpu` | PDH 功率计优先，无则按 `P_idle+(P_max−P_idle)·(f/f_max)^1.3·util` 估算（TDP 由型号粗估） |
| 热力块均分 | `ui/widgets.CpuHeatmapGrid._grid` | 行数优先 → 12 核 6×2、16 核 8×2；块宽 96~150px 自适应撑满 |
| 瓶颈天平可读性 | `ui/gauge.BottleneckBar.paintEvent` | 数值标签移到轨道上方（原来灰字直接压在色条上看不清） |
| 回归测试 | `tests/test_v1_layout.py`（新） | 空载→有数据→弹/关病历卡的 (y,height) 必须完全一致；灰字属性必须真的删除；折线去重/估算标记；热力块行列 |

## 12. v1.0.1 变更清单（修 bug + 版式瘦身）

| 变更 | 位置 | 说明 |
|---|---|---|
| 换肤持久化 | `config.py` | `theme/chart_style/custom_bg` 之前写盘后读回被丢弃；现按额外键读取 + 白名单校验 |
| KV 排版体系 | `ui/kv.py`（新）、`ui/gauge.py`、`ui/pages.py` | 标签=蓝灰胶囊、数值=高亮白/强调色；条与圆环内部不放文字 |
| 字体与配色令牌 | `theme.py` | 浅色 `fg #1E293B` / `dim #475569`；深色 `dim #94A3B8`；`key_bg/key_fg/val`；全局字重 500 / 标题 600 |
| 评级徽章 | `ui/gauge.GradeBadge` | 只保留大字母，删除框内两行小字 |
| 病历卡去重 | `ui/diagnosis.py` | 标题不再重复「X 级」 |
| CPU 页 | `ui/pages.CpuPage`、`ui/cpu_trend.py` | 删方块热力图；新增每线程折线（固定视口 + 滚动条，增量增删行不丢历史） |
| GPU 页瘦身 | `ui/pages.GpuPage` | 3 条 → 2 条；每根条上方一行 KV；页头标题与状态合并一行 |
| 横幅修复 | `ui/main_window.py` | 低帧率不再写「全程丝滑」；瓶颈区压成 3 行（数值/条/结论） |
| 卡片极简 | `ui/gauge.FpsCard` | 13px 标题 + 28px 数字 + 最多一行副文本（两行小字合并） |
| 回归测试 | `tests/test_v1_layout.py` §10/§11 | 换肤持久化（含脏值回落）；字体令牌与全局字重 |

## 13. v1.0.2 变更清单（三张截图返修）

| 变更 | 位置 | 说明 |
|---|---|---|
| 归因表不再横滚 | `ui/diagnosis.py` | 第 5 列 `Stretch` + 关横滚 + 行高按内容；最后一行不再被滚动条压掉、长结论不再被截断 |
| 灰字根因 | `ui/diagnosis.py`、`ui/pages._set_cells` | 空 `QtGui.QBrush()` 前景 → 委托退化成 palette 色；改为显式前景 / 清掉 `ForegroundRole` |
| 主题化告警底色 | `theme.py` 新增 `mix/luminance/contrast_on` | 删除写死的 `#3d1418` / `#38240b`，浅色主题下也能读 |
| CPU 页空白 | `ui/pages.CpuPage`、`ui/cpu_trend.py` | KV 面板 `Maximum` 贴内容；线程列表视口可伸缩（138px 起） |
| 每线程折线自适应窗口 | `ui/cpu_trend.py` | 共享窗口（中位数 ± 跨度，跨度封顶 25% 峰值）；窄幅波动不再画成直线 |
| 图表内文字 | `ui/gauge.CpuFreqBars` | 删除柱顶 `4594` |
| 换页 Top 空表 | `ui/pages.MemPage` | 写明"换入 < 2MB/s 未做进程级归因"/"采样中"；列名改 `缺页 MB/s`（PSAPI 含软缺页）；过滤 0 值 |
| 能量条量程 | `ui/pages.MemPage` | 下限 32/64 → 1/4 MB/s（0.2MB/s 也能看见） |
| 状态胶囊截断 | `ui/widgets.ElidedLabel` | `sizeHint()` 按全文计算 |
| 回归测试 | `tests/test_v1_layout.py` §12~§15 | 归因表可读性 / CPU 页版面与窗口 / 换页归因 / ElidedLabel 尺寸反馈 |

## 14. v1.0.3 变更清单（白底可读性 + CPU 页合并）

| 变更 | 位置 | 说明 |
|---|---|---|
| 白底红/橙看不清 | `ui/plain.py`、`theme.py` | `LEVEL_COLOR`/`GRADE_COLOR` 由写死色板改为「角色名 + 按主题解析」，浅色 warn/orange 压深到 ≥4.5:1 |
| CPU 页合并成一张卡 | `ui/pages.CpuPage` | 新增 `QFrame#CpuFreqCard`：两列 KV + 竖分隔线 + 均频/功率折线 + 每线程折线；柱状图独立 |
| 每线程折线更明显 | `ui/cpu_trend.py` | 行高 28、火花线 24、线宽 2.2、渐变填充、行尾圆点、斑马纹；数值 12px/700 |
| 趋势图内嵌层次 | `ui/cpu_trend.CpuTrendChart` | 卡片内底色改用 `th.panel`，与卡片 `panel2` 形成内凹 |
| 回归测试 | `tests/test_v1_layout.py` §13/§16 | 合并卡结构 + 柱状图在卡外 + 折线像素级可见性；浅色主题对比度审计 |

## 15. v1.0.4 变更清单（设置面板瘦身 + 诊断节流）

| 变更 | 位置 | 说明 |
|---|---|---|
| 「FrameLab 自己卡住」根治 | `ui/main_window.py` | `inspect()` 会话级归因缓存键含帧数 → 实时采样永不命中；顿挫横幅 / 自动弹卡 / 锁定卡跟随三条 30Hz 路径改为 ≥0.5 s / ≥1 s / ≥1 s 节流（`DIAG_RENDER_INTERVAL`、`AUTO_POPUP_INTERVAL`、`DIAG_FOLLOW_INTERVAL`）；`_page_ctx` 复用 `visible_frames`。10 万帧 + 14% 卡顿帧：p50 35.8–39.2 ms → 3.2–3.3 ms（×11–12.4），算法与文案零改动 |
| 设置面板瘦身 | `ui/main_window.py`、`ui/base_window.py` | 6 页签 → 2 页签（⚙ 设置 / ⚡ 事件流）；删 4 个 v3.2 遗留页签（CPU 热力图 / 显存监视 / 内存换入 / 着色器与引擎）及其对已丢失的 `_sync_table` 的每 tick 调用；齿轮菜单删「展开进阶硬件面板」「打开 PresentMon 目录」与一个死控件 |
| 设置页人话化 | `config.py`、`ui/base_window.py` | `CONFIG_LABELS`（中文名 + 单位·默认）+ `CONFIG_GROUPS`（显存 / CPU / 着色器 / 采集 / 平稳基线·进阶）驱动表单；原始键名与解释进 tooltip；未登记键有兜底表单 |
| 阈值保存真生效 | `ui/base_window.py`、`config.py` | 保存 / 恢复默认 / 重新载入三处 `apply_to_globals(globals())` → `apply_to_globals()`（广播到 consts/engines/analysis/pages）；恢复默认改为先广播再回填输入框 |
| 死代码删除 | `ui/widgets.py` | `CpuHeatmapGrid`、`heat_color`、`_text_on`、`HEAT_STOPS` 移除 |
| 主题下拉框跟随 | `ui/base_window.py`、`ui/main_window.py` | 新增 `_sync_theme_combo()`：自定义背景色 / 预览脚本 / 测试直接 `_apply_theme()` 时，下拉框不再残留旧主题名（深色页面配「浅色（白底）」的错位） |
| 采集控制（追加） | `ui/base_window.py`、`ui/main_window.py`、`analysis.py` | 默认不采集（`_collect_started=False`），未开始时队列产出丢弃；主按钮三态「开始/暂停/继续」；新增「🧹 清空重采」→ `FrameStore.reset()` + 清曲线/卡片/缓存后重新采样（暂停中 = 清空并继续）；切模拟流自动进入采集态 |
| 数轴可缩放（追加） | `ui/chart.py`、`ui/main_window.py` | 监听 `sigRangeChangedManually` → `_user_zoom=True` 后停止每帧锁轴（切视角/清空才复位）；空图不再每 tick `clear()`；等待态横幅写明确原因。回归 `test_chart_axis.py` A10 |
| 未采集也有心跳（追加） | `ui/base_window.py`、`ui/main_window.py` | 未开始采集时 `_drain_hardware_only()` 只放行硬件快照（帧/告警丢弃）；硬件四页、导航徽章、顶部瓶颈条 100ms 刷新；`_had_frames` 控制卡片复位时机；`_toggle_pause` 350ms 防连点 |
| 回归测试 | `tests/test_v1_layout.py` §1/§6/§6b/§6c | 品牌 1.0.4；旧页签与死代码不存在；设置页无原始键名 + 分组齐全 + 保存后三处副本同步；60 tick 内 `inspect` ≤3 次 |