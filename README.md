# FrameLab 1.0.4

Windows 游戏掉帧（卡顿）研究工作台：底层用 PresentMon + PDH/DXGI/ADL 采集，
把「帧率掉了」翻译成**玩家看得懂的一句话 + 一招解决建议**。

> **v1.0.4 = 设置按钮瘦身 + 根治「FrameLab 自己卡住」**：
> ① **卡死真因**：`SnapshotDiffInspector.inspect()` 结尾的会话级归因是
> O(全部帧 × 卡顿帧)，而它的缓存键含帧数 —— 实时采集时帧数每 tick 都在变，
> 缓存**永不命中**。旧实现被三条路径以 30Hz 反复触发（顿挫横幅 / 自动弹病历卡 /
> 锁定卡跟随刷新），会话越长单次越贵：**实测 10 万帧 + 14% 卡顿帧时
> 单 tick 中位数 39ms、p95 68ms**（33ms 的 UI 定时器被彻底淹没）；
> 现在横幅 ≥0.5s、弹卡与跟随 ≥1s 各算一次 → **中位数 3.2ms、p95 5.4ms（×12.4）**，
> 归因算法本身一行未动（同样判定、同样结论，只是不再 30Hz 重算）。
> ② **设置按钮里的老东西清干净**：⚙ 只留「设置 + 事件流」两个页签，
> 删掉 v3.2 遗留的 4 个重复硬件页签（CPU 热力图 / 显存监视 / 内存换入 /
> 着色器与引擎）—— 它们的表格刷新调用了一个模块拆分时**丢失的方法**
> `_sync_table`，打开面板就每 tick 抛 AttributeError；齿轮菜单同步删掉
> 「展开进阶硬件面板」「打开 PresentMon 目录」（设置页已有）与一个死控件。
> ③ **设置页人话化**：12 个原始英文键名（`spill_threshold` / `baseline_*` …）
> 换成中文名 + 单位 + 默认值、按「显存 / CPU / 着色器 / 采集 / 基线」分组，
> 原始键名进 tooltip；顺手修掉「保存当前阈值」只改本模块副本、
> **要重启才生效**的 bug（现在广播到 consts/engines/analysis/pages 全部模块）。
> ④ 删除 `CpuHeatmapGrid` 及 `heat_color/_text_on/HEAT_STOPS` 等死代码。
>
> 历史：v1.0.3 = 白底可读性（`LEVEL_COLOR` 写死深色色板 → 角色名解析）+ CPU 页
> 合并成一张卡 + 每线程折线加粗；v1.0.2 = 三张截图的 5 个「看着像坏了」问题；
> v1.0.1 = 修「换肤重启丢失」等 10 项 + 版式瘦身；
> v1.0.0 = 版面零抖动 + 文案瘦身 + CPU 频率/功率折线 + 正式更名；
> v4.1 = 第二份建议书（`gemini-code-1793074763581.md`）的 1 个布局缺陷 + 4 个算法误报；
> v4.2 = 暗色主题下「设置」看不清；v4.3 = 关闭后残留（窗口关不掉 / PresentMon 孤儿）。

---

## 1. 快速开始

```powershell
# 依赖（3 个，均为硬依赖）
python -m pip install -r requirements.txt      # PyQt6 / pyqtgraph / numpy

# 运行
python FrameLab.py
```

- 需要 Windows 10 1709+ / Windows 11。
- 未安装 PresentMon 也能用：程序自动降级为「模拟数据流」，并可用 `CSV 导入回放`
  分析 CapFrameX / PresentMon 导出的 CSV。
- 想采集真实帧数据：把 `PresentMon.exe` 放到本目录（或在 ⚙ 设置里指定路径）。
- 建议以管理员身份运行（部分进程的 ETW/性能计数器需要权限；不强制）。

## 2. 目录结构（每个模块只干一件事）

```
FrameLab.py                 启动器（薄壳）+ 旧顶层名字兼容导出
framelab/
  paths.py                  「程序所在目录」的单一来源（配置/工具/提权脚本路径）
  theme.py                  主题系统：4 套配色 + 运行时热切换注册表 + pg 菜单汉化
  consts.py                 诊断阈值常量（可被 framelab_config.json 覆盖，热更新广播）
  utils.py                  结构化日志 / 有界队列 / 格式化 / pg 符号校验
  probes.py                 底层 ctypes 探针：PDH、DXGI、AMD ADL、缓存目录监视、CPU 功率
  models.py                 Event / HardwareSnapshot / FrameRecord / DiffRow
  etw.py                    ETW 会话管家（根治 PresentMon error 1450 静默 0 帧）
  engines/hardware.py       HardwareEngine 轮询线程（CPU/GPU/显存/IO/网络/缓存/功率）
  engines/presentmon.py     PresentMon 定位与能力探测 + PresentMonBridge 采集线程
  analysis.py               BaselineTracker / FrameStore / SnapshotDiffInspector（11 维归因）
  config.py                 framelab_config.json 读写与回落
  hotkeys.py                全局热键（RegisterHotKey + 原生事件过滤器）
  winadmin.py               管理员权限 / PresentMon 指引 / 数据源判定 / 模拟水印
  csvio.py                  CSV 后台读取（不冻结界面）
  stats.py                  帧率统计口径：平均/最高/1% Low/顿挫/评级/瓶颈（纯函数）
  ui/base_window.py         从 v3.2 原样迁移的可复用窗口逻辑（设置页/CSV/ETW/热键…）
  ui/main_window.py         主窗口装配（左栏导航卡 + 右侧 QStackedWidget + 每帧刷新口径）
  ui/navtabs.py             左侧 5 张大号视图导航卡（图标 + 模块名 + 一行实时状态徽章）
  ui/pages.py               QStackedWidget 的 5 个全屏页面（总览/CPU/显卡/内存/网络）
  ui/gauge.py               GamePP 风格图形元件（胶囊条/圆环/能量柱/频率柱/显存大横条/段位徽章）
  ui/chart.py               实时 FPS 曲线（渐变填充 + 均值虚线 + 顿挫红点 + 锁死坐标轴）
  ui/cpu_trend.py           CPU 折线：均频/功率总览 + 每线程一条（可滚动）折线
  ui/diagnosis.py           极简卡顿病历卡（idle/红/绿三态，高度恒定）+ 11 项专业对照表窗口
  ui/plain.py               小白话术映射表（专业归因 → 人话 + 解决建议）
  ui/widgets.py             通用可视化组件（CPU 热力块矩阵 / ElidedLabel / 探针读数条 / 进度条）
  app.py                    进程入口 main()
docs/                       设计资料（引擎 API 契约、迁移期 UI 清单、界面预览图）
assets/                     打包用图标（framelab.ico 多尺寸 + 256px PNG 源图）
build_tools/                打包脚本与 PyInstaller 配置（build_exe.ps1 / FrameLab.spec / version_info.txt）
dist/                       打包产物（FrameLab.exe 单文件；旁边可放 framelab_config.json）
tests/                      14 个可执行测试脚本（见 §4）
```

## 3. 这个版本改了什么

### 3.0.4 v1.0.4：设置按钮瘦身 + 根治「FrameLab 自己卡住」

**① 「会自己卡住」的真因（真 Bug，已修）**

| 层 | 事实 |
|---|---|
| 现象 | 用一会儿（尤其游戏在顿挫、病历卡弹出后）界面越来越钝，最后像卡死；帧率曲线/数字停更 |
| 真凶 | `SnapshotDiffInspector.inspect()` 末尾调 `store.session_correlations()`：它要**遍历全部帧**挑出卡顿帧，再对每个卡顿帧跑 5 项归因检查 —— 而它的缓存键是 `(帧数, 末帧对象, 基线)`，实时采集时帧数**每 tick 都在变 → 缓存永不命中**。旧实现有三条路径以 30Hz 调它：顿挫横幅每 tick 重算、自动弹卡每 tick 一次、锁定卡跟随刷新每 tick 一次 |
| 量级 | **实测 10 万帧（≈28 分钟 @60fps）+ 14% 卡顿帧、病历卡打开**：单 tick 中位数 **35.8–39.2 ms**、p95 **61.3–68.4 ms** —— 而 UI 定时器是 33 ms，等于事件循环被彻底淹没（`tests/_probe_freeze.py` 可复现） |
| 修法 | 只节流**调用频率**，不动算法：顿挫横幅 ≥0.5 s 重算一次、自动弹卡 ≥1 s 至多一次、锁定卡跟随刷新 ≥1 s 一次；`_page_ctx` 复用主循环已取好的 `visible_frames`（原本每 tick 白复制一份 10 万帧列表） |
| 结果 | 同样场景：中位数 **3.2–3.3 ms**、p95 **4.7–5.4 ms** —— **×11–12.4**（两次实测），且判定口径与文案一字未改（回归 §6c：60 tick 内 inspect ≤3 次） |

**② 设置按钮里的「老东西」清干净**

- ⚙ 面板从 6 个页签砍到 **2 个**：`⚙ 设置` + `⚡ 事件流`。
- 删掉的 4 个 v3.2 遗留页签（CPU 热力图 / 显存监视 / 内存换入 / 着色器与引擎）与主界面
  5 个导航页信息**完全重复**；更要命的是它们的表格刷新调用了 `self._sync_table(...)`，
  而该方法在 v4.0 模块拆分时**丢了定义** —— 面板一打开、硬件数据一到，每 tick 抛一次
  `AttributeError`（被兜底吞掉，界面表现为"表格永远空着 + 顶部闪一下渲染异常"）。
- 齿轮菜单同步删掉 `🚀 展开进阶硬件面板`（已无必要）、`📁 打开 PresentMon 目录`
  （设置页里本来就有「打开目录」），以及一个 new 出来却从未加进菜单的死 `QPushButton`。
- 事件流改用 `pages._set_cells` 差量写入（顺带修掉「不带颜色的格子塞 NoBrush → 灰字」）。
- 死代码一并删除：`CpuHeatmapGrid`、`heat_color`、`_text_on`、`HEAT_STOPS`、
  `_display_*`、`_chart_mode*`、`_env_cache`、`_metric_cache_ui`、`_table_cache`、
  `OVERVIEW_RENDER_CAP`、`ENV_WINDOW_MAX/ENV_MAX_ANCHORS/_ALIGN`。

**③ 设置页人话化 + 「保存阈值却要重启才生效」的 bug**

- 12 个原始英文键名（`spill_threshold` / `cpu0_jam_threshold` / `baseline_max_spike_frac` …）
  原来是**直接当标签渲染**的；现在显示中文名 + 单位 + 默认值，按
  「显存归因 / CPU 归因 / 着色器归因 / 采集引擎 / 平稳基线·进阶」分组，原始键名与解释进 tooltip。
- **真 Bug**：`_on_save_thresholds / _on_reset_thresholds / _on_reload_config` 原来写的是
  `apply_to_globals(globals())` —— 只把值写回 base_window **自己那份副本**，而真正参与判定的
  consts / engines / analysis / pages 四份副本一动不动：界面提示「已保存」，实际阈值要重启才生效。
  现在改成不带参数（广播到所有已登记模块），并先广播再回填输入框。
- 回归：§6b 断言无原始键名标签、分组齐全、保存后 `consts`/`hardware`/`analysis` 三处副本同步。
- **顺手修**：「主题」下拉框不跟随 —— 点「应用」自定义背景色、或预览脚本/测试直接调
  `_apply_theme()` 时不会经过下拉框回调，页面已经是深色、设置页却还写着「浅色（白底）」。
  新增 `_sync_theme_combo()`：只同步显示，`blockSignals` 防回环（预览图里深色页现在正确显示「深空黑」）。

**④ 采集控制：手动开始 + 清空重采（追加）**

- **默认不采集**：启动后曲线保持空白，左侧大按钮是「▶ 开始采集」；在此之前后台产出
  的帧 / 快照 / 告警**一律丢弃**（`_discard_pending`），不会在点开始那一瞬间把启动期间
  攒下的历史一次性灌进曲线。
- **▶ 开始采集 / ⏸ 暂停采集 / ▶ 继续采集** 三态合一：暂停时数据继续累积，只是不刷新界面
  （原有语义不变）。**防连点**：开始后 350ms 内的重复点击不当作暂停 —— 窗口刚激活 /
  触摸板双击时一次点击可能被投递两次，否则会「开始→立刻暂停」，看起来像点了没反应。
- **🧹 清空重采**：丢掉已采到的帧 / 硬件快照 / 事件 / 基线（新增 `FrameStore.reset()`），
  同时清空曲线、四张大卡与统计/诊断缓存，并从此刻重新采样；**暂停中点击 = 清空并继续**。
- 从齿轮菜单切「🎲 模拟数据流」会自动进入采集态（否则选了模拟流量却看不到曲线）。
- 回归：`test_v1_layout.py` §6d（默认丢弃 / 点开始才接收 / 清空归零 / 暂停中清空恢复）；
  `smoke_ux_runtime` / `smoke_v4_runtime` 的打点用例改为先进入采集态（打点事件走队列）。

**⑤ 数轴现在真的能放大（追加修复）**

- 旧行为：`_apply_axis_limits()` 每次刷新都 `setXRange/setYRange`（1Hz~30Hz），
  用户滚轮/拖拽放大**下一帧就被拉回**；空图时更狠 —— 每 tick `clear()` 一次，
  连"按住不放"的缩放都被重设。README 旧文里把这写成「已知限制」。
- 现在：监听 `ViewBox.sigRangeChangedManually`（滚轮/拖拽/框选都会发），置
  `_user_zoom=True` 后**停止自动锁轴**，视野完全交给鼠标；切 FPS/ms 视角或
  「🧹 清空重采」才复位。空图也不再每 tick `clear()`。
- 回归：`test_chart_axis.py` A10（手动缩放后刷新不覆盖 / 切视角复位 / 清空回 0~100）。

**⑥ 未开始采集时也不再像「卡死」（追加修复）**

- 用户报「软件无刷新」时，实际常见状态是：还没点「▶ 开始采集」→ 帧曲线空、四张大卡
  `--`、CPU/显卡也 `--`，整屏静止像死机。现在未开始采集时仍放行**硬件快照**
  （`_drain_hardware_only`）：CPU / 显卡 / 内存 / 网络四页与导航徽章、顶部瓶颈条
  继续按 100ms 刷新；帧与告警仍完全丢弃，曲线保持空白。
- 等待态横幅写明原因：未开始 → 「点左侧「▶ 开始采集」开始采样」；已开始无帧 →
  「游戏在运行时仍无数据，请以管理员身份运行」。

### 3.0.3 v1.0.3：白底可读性 + CPU 页合并成一张卡

**① 红色在白色皮肤看不清（真 Bug，已修）**

| 层 | 事实 |
|---|---|
| 现象 | 切到「浅色（白底）」皮肤后，病历卡边框/正文、横幅结论、评级大字母的红色/橙色发虚 |
| 真凶 | `ui/plain.py` 的 `LEVEL_COLOR` / `GRADE_COLOR` 写死了 `#ff5d5d` / `#ffb02e` / `#2ee6a8` —— 那是**深色主题专用色板**；白底上 `#ff5d5d` 对比度只有 **2.4:1**（`#2ee6a8` 更是 1.6:1） |
| 修法 | 两张表只存**角色名**（`bad`/`warn`/`green`…），取色时按 `CURRENT_THEME` 解析；旧写法 `LEVEL_COLOR[k]` 通过 `_ThemeMap` 兼容层继续可用（每次访问都按当前主题解析）。浅色主题的 `warn` `#b26a00`→`#9a5b00`、`orange` `#d84315`→`#c23c11`，**全部语义色对白底 ≥4.5:1** |
| 回归 | `tests/test_v1_layout.py` §16：danger/warn/ok 对白底对比度 ≥4.5、换主题后颜色跟着变、`LEVEL_COLOR` 兼容取值一致 |

**② CPU 页：除柱状图外合并成一张卡**

- 原来是「两块 KV 面板 + 一张独立折线图 + 每线程列表」四段各自带底色，视觉上是散的；
  现在合成一张 `QFrame#CpuFreqCard`：**两列 KV（中间一条 1px 竖分隔线）→ 均频/功率折线（内嵌"凹陷屏幕"）
  → 每线程折线**，页面只剩两张卡（这张 + 下面的柱状图）。
- 高度分配 `card_freq : bars = 5 : 1`：页面变高时优先多露几行线程（本机 12 线程 900px 窗口可见 9 行）。
- 回归：§13 断言四块都在 `card_freq` 内、柱状图**不在**卡内。

**③ 每线程折线"明显点"**

| 项 | 之前 | 现在 |
|---|---|---|
| 行高 / 火花线高 | 22 / 16 px | **28 / 24 px** |
| 线宽 | 1.4 px | **2.2 px**（圆头圆角） |
| 填充 | 无 | 曲线下方同色渐变（alpha 64→6） |
| 端点 | 无 | 行尾当前值圆点 |
| 行区分 | 无 | 奇数行极淡斑马纹；占用 ≥85% 的行加琥珀底 |
| 数值字号 | 11px/600 | **12px/700** |

- 回归：§13 像素级验证 —— 折线+填充命中色值像素数 **>120**（实测 923）。

### 3.0.2 v1.0.2：三张截图返修（5 项）

**① 归因表：最后一行被横向滚动条压掉、第 5 列被截断**

| 层 | 事实 |
|---|---|
| 真凶 | 第 5 列 `Interactive + setColumnWidth(560)`，前 4 列 `ResizeToContents`：列宽之和超过窗口宽度 → 底部永远挂着横滚条，最后一行只剩 2px、长结论整段被裁 |
| 修法 | 第 5 列 `Stretch` + `setHorizontalScrollBarPolicy(AlwaysOff)`；垂直表头改 `ResizeToContents`（行高随换行内容自动长）；默认尺寸 1180×460 → 1240×600 |

**② 归因表一排看不清的灰字**

- 真因不是配色，而是 `item.setForeground(QtGui.QBrush())`：默认构造的 QBrush 是 **NoBrush**，
  等于给单元格塞了"没有颜色"的前景色，委托只能退化成 palette 色；
  同一个 bug 也在 `pages._set_cells()` 里 —— 两处都改成"没有指定颜色时清掉 ForegroundRole 数据"。
- 行底色不再写死 `#3d1418` / `#38240b`（那是深色主题专用，浅色主题下深字压深红底完全不可读），
  改由新增的 `theme.mix()` / `theme.contrast_on()` 从当前主题派生，并保证前景与底色亮度差 ≥0.28。
- 回归：`tests/test_v1_layout.py` §12（无空前景格、无横滚、底色随主题变化、浅色下正文为深色）。

**③ CPU 页大片空白（两张截图都有）**

- `KVPanel` 原来纵向是默认策略，`QVBoxLayout` 把页面剩余高度摊给它们 → 面板内容 3 行、下面空 150px；
  现在 CPU 页两个面板显式 `Maximum`（只占内容高度）。
- `CpuThreadTrends` 原来 `setFixedHeight(138)`，多出来的高度变成"标题与首行之间 70px 的空档"；
  现在视口 `MinimumHeight=138` + 可伸缩（页面长高先多露几行），柱状图拿剩下的。
- `CpuFreqBars` 柱顶那个 `4594` 数字删除（KV 规范第 3 条：图表内不放文字，最高核频由 KV 行表达）。

**④ 每线程折线"永远是直线"**

- 真因：纵轴固定按 `0..max_mhz` 归一化，4.3~4.6GHz 的波动只占画面高度的 6%；
  某个核空闲降频到 800MHz 时又反过来把其它核压平。
- 修法：`CpuThreadTrends.auto_window()` 给出**全线程共享**的自适应窗口 ——
  中位数 ± 跨度/2，跨度 = `clamp(真实极差, 6% 满频, 25% 峰值)`；
  窄幅波动铺满整行高度，空闲核被裁在窗口下沿（位置最低 = 频率最低，语义正确）。
- 回归：`tests/test_v1_layout.py` §13（窄幅窗口 ≤560MHz；含空闲核时跨度 ≤25% 峰值）。

**⑤ 内存页：换页 Top 空表的真相**

- **不是 bug，也不是"系统没换页"**：`HardwareEngine` 只在 `page_in > 2 MB/s` 时才做进程遍历
  （1200 次 `OpenProcess` + `GetProcessMemoryInfo`，省 CPU），`0.2 MB/s` 达不到门槛 → `pagein_top` 恒为空。
  实测本次采集：PDH 硬换入 `0.2 MB/s`，而同口径的进程缺页速率合计约 `98 MB/s`
  （`AMDRSServ 82`、`DSH NEXT 11`）—— 差 500 倍是因为 PSAPI 的 `PageFaultCount` **含软缺页**，
  与 PDH 的"硬换入"不是同一口径。
- 界面修法：空表写清"为什么空"（`换入 0.2 MB/s < 2 MB/s · 未做进程级归因`；
  已过门槛但首轮无差值基线时写"采样中"）；列名 `换入 MB/s` → `缺页 MB/s`（口径诚实）；
  `pagein_top` 里 `rate=0` 的假嫌疑进程被过滤掉。
- 能量条量程下限 `32/64 MB/s` → `1/4 MB/s`：`0.2 MB/s` 原来只填 0.6% 宽度，屏幕上就是一个 2px 的点。
- 回归：`tests/test_v1_layout.py` §14。

**附：状态胶囊被截成 `…均频 4.40 GH-`** —— `ElidedLabel` 的 `sizeHint()` 量的是**当前已省略的文本**，
一旦被截短就再也长不回去（布局拿不到"全文需要多宽"）。改为按 `fullText()` 计算，回归见 §15。

### 3.0.1 v1.0.1：修 bug + 版式大瘦身（10 项）

**① 皮肤重启丢失（真 Bug，已修）**

| 层 | 事实 |
|---|---|
| 现象 | 换肤后重启，皮肤回到默认深色 |
| 真凶 | `Config.update()` 只合并 `CONFIG_SCHEMA`（纯阈值）+ 三个硬编码键；`CONFIG_EXTRA_KEYS` 定义了却从未使用 → `theme` / `chart_style` / `custom_bg` **写进文件后读回来被直接丢弃** |
| 修法 | 按额外键统一处理，并对 theme/chart_style 做白名单校验（脏值回落默认，绝不让程序卡在黑屏/未知皮肤） |
| 回归 | `tests/test_v1_layout.py` §10：临时配置全链路（save → load → 窗口换肤写回），并验证脏值回落 |

**② 评级徽章只留一个大字母**：方框内 `丝滑度评级` / `建议降画质` 两行小字全删（结论由横幅承担）。

**③ 病历卡不再重复评级**：标题从 `#428 · 40.0 ms · A 级` 改为 `#428 · 40.0 ms`（等级只在徽章出现一次）。

**④ 删除 CPU 方块热力图**：CPU 页只保留下方各核频率柱状图（`CpuFreqBars`）。

**⑤ CPU 频率/功率折线 → 每线程一条 + 滚动条**

- 新增 `ui/cpu_trend.CpuThreadTrends`：**一行一个线程**（`[C3] ──折线── 4.20 GHz · 31%`），
  共用同一纵轴刻度，谁在睿频、谁在降频一眼可见；16 线程超过视口高度时**自动出现竖直滚动条**；
- 行数是**增量增删**（不许"先全删再全建"）：否则线程数一变（16 核合成数据 ↔ 12 核真实快照）
  历史点会被清空，折线永远只剩 1 个点、看着像没画；
- 上方保留「均频 / 功率 / 峰值」总览折线（图内不写字，标签数值都在 KV 行）。

**⑥ 全局字体层级与配色重构**

| 令牌 | 浅色主题 | 深色主题 |
|---|---|---|
| `FG`（主文字） | `#1E293B` 深岩石黑 | 各主题原值（`#f0f0f0` / `#e9effa` / `#f2f2f2`） |
| `DIM`（次级文字） | `#475569` 石板灰（禁止浅于 `#64748B`） | 统一 `#94A3B8` |
| `key_bg` / `key_fg` | `#d2d9e4` / `#444f5a` | 低明度蓝灰（对胶囊底对比度 ≥5:1） |
| `val`（数值） | `#0b1524` | `#ffffff` |

统一字重：全局 QSS 基线 `font-weight: 500`，标签胶囊/标题 600，
数值 `bold`；不再出现「细体小灰字 vs 粗体大字」混排。

**⑦ 「显卡与显存」页大瘦身**：圆环内只有 `XX%`，圆环下方一枚 `3D 负载` 胶囊；
右侧 **2 根条**（核心频率 / 显存占用），每根条**上方一行** KV（左标题 + 右数据），
条下再无粗体黑字行；第 3 根「共享显存」条**已删除**（数值并入「显存占用」行，消除与溢出胶囊的文字重叠）；
页面标题与状态胶囊合并到同一行。

**⑧ 横幅文案不再自相矛盾**：帧率 <30 或评级 C/D 时显示「· 帧率偏低」，不再拼「· 全程丝滑」。

**⑨ 瓶颈天平压成 3 行**：数值行（CPU 左 / 显卡 右）+ 天平条 + 一句结论；
重复的第 4 行（黑字复述百分比）与建议行删除（建议进 tooltip）。

**⑩ 四张核心指标卡极简**：`13px 石板灰 600 标题 + 28px 大数字 + 最多一行 10px 副文本`；
「顿挫」卡的两行小字合并为 `占比 1.1% · 频繁`；「显存 Top 5」小标题改 13px 深色粗体 + 6px 间距。

### 3.0 v1.0.0：版面零抖动 + 文案瘦身 + CPU 频率/功率折线 + 正式更名

**① 卡牌不再随曲线出现而上下动（主诉）**

实测三处「高度会变」的来源（`tests/test_v1_layout.py` 里逐条回归）：

| 根因 | 表现 | 修法 |
|---|---|---|
| 顶部横幅的结论行/瓶颈建议是 `wordWrap=True`，数据一到从 1 行变 2 行 | 横幅 92px → 100px，下面 **4 张卡整体被顶着往下跳** | 横幅 `setFixedHeight(96)`；内部全部换成 `ElidedLabel`（单行 + `…` + 全文进 tooltip） |
| 病历卡是 `show/hide` 式 | 弹卡时**折线图瞬间矮 110px**，关掉又长回来 | 病历卡改**常驻占位**：`idle` 中性态与真卡同高，`show_for/show_smooth/show_idle` 只换内容不换高度 |
| 4 张卡的副标题/尾注空一行就少一行 | 卡片 sizeHint 71↔83 抖动 | 造卡时按最高那张 `setFixedHeight`，空行 `setVisible(False)` |

回归断言：空载 → 3 帧 → 400 帧 → 弹病历卡 → 关病历卡，**横幅/卡牌/图表/病历卡的 (y, height) 必须完全一致**。

**② 文案瘦身：能符号就符号，能词就词**

| 删除的灰字 | 理由 |
|---|---|
| 侧栏「游戏卡顿一站式体检」「视图导航」 | 窗口标题/导航卡本身已经说明一切 |
| 横幅「平均 54 帧 · 最高 2554 · 1% Low 2 · 顿挫 39.46%」 | 与下面 4 张大卡完全重复 |
| 5 个页面的副标题（SUBTITLE）与 Page 0 的提示行 | 都是成句说明，一屏堆 4~5 段灰字 = 杂乱 |
| CPU 页「负载色标:」+「柱高=当前频率，虚线=全核最高频」 | 色块 + 两字词（空闲/轻载/中载/重载/拥堵）已足够 |
| 卡片标题里的英文括注（`平均帧 (AVG FPS)`） | 改成 `平均帧` / `最高帧` / `1% Low` / `顿挫` |
| 各页结论长句 | 改短句：`🟢 调度健康 · 总占用 35% · 均频 4.95 GHz` |

每张卡最多「标题 + 大字 + 一行补充」，页面结论行/建议行都是单行省略。

**③ CPU 页新增「频率 + 功率」双折线**（`ui/cpu_trend.py`）

- 位置：热力块矩阵与各核频率柱状条之间，**固定 104px**，不参与拉伸；
- 极简：无坐标轴、无网格；左上角一行图例 `频率 4.95 GHz` / `功率 ≈48 W`，右上角极淡 `峰值`；
- 两条线各自按自身历史区间归一化（波动 <2% 时按平坦处理，避免把噪声放大成锯齿）；
- 按 `HardwareSnapshot.seq` 去重 —— 页面刷新比采样快也不会画出重复点；
- 功率取数三级：PDH 功率计（`Power Meter` / `Energy Meter` / `Processor Power`）→ **估算**；
  没有功率计的台式机上按 `P ≈ P_idle + (P_max−P_idle)·(f/f_max)^1.3·(util/100)` 估算，
  `P_max` 由 CPU 型号粗估（U28 / H45 / HX55 / i5-75 / i7-95 / i9-125W）。
  **估算值一律显示成 `≈48 W`**，绝不伪装成实测（`cpu_power_est` 字段一路带到 UI）。

**④ 热力块矩阵按行均分**

旧逻辑「宽度塞得下多少列就排多少列」：12 核在宽屏上排成 **11 + 1**（最后一行只落单一个方块）。
新逻辑**行数优先**：先按最大列数算行数，再让列数向上均分 —— 12 核 6×2、16 核 8×2、25 核 9+9+7，
块宽同时可在 96~150px 之间撑满，不再留一条大空白。

### 3.1 布局重构：左栏导航卡 + 右侧五页整块替换

**痛点**：旧版在 280px 窄左栏里展开抽屉，图表被挤到极小、文字被截断（如 `1008.40%` 溢出）、
还逼出丑陋的滚动条。

**做法**：

| 位置 | 内容 |
|---|---|
| 左栏（**固定 240px**） | 只有 5 张大号导航卡，每张两行：`图标 + 模块名称` / `一行极简实时状态徽章`（📈 帧率总览 `当前 XX FPS · 丝滑`、🔥 CPU 状态 `总占用 XX% · X.XX GHz`、🎮 显卡与显存 `核心 XX% · 显存 XX%`、💾 内存与磁盘 `内存 XX% · 读盘 X.X MB/s`、🌐 网络与环境 `↓ XX KB/s · 着色器就绪`）。**栏内不再画任何进度条/圆环，也没有滚动条** |
| 右侧上半（常驻） | 顶部状态横幅（段位徽章 + 小白话术 + 瓶颈天平）+ 4 张核心帧数大卡 |
| 右侧下半（`QStackedWidget`） | Page 0 帧率总览（折线图 + 病历卡，默认）／Page 1 CPU 热力块矩阵 + **频率/功率双折线** + 各核频率柱状条 + 健康度大字／Page 2 3D 负载大圆环 + 核心频率温度 + 物理/共享显存大横条 + 显存 Top5 进程／Page 3 物理内存大圆环 + 换页速率 + 读盘能量条 + 读盘进程排行／Page 4 上下行吞吐 + 着色器缓存目录文件数与写入爆发监控 |

实现：`ui/navtabs.py` + `ui/pages.py`，`ui/accordion.py`（旧手风琴）已删除。

### 3.2 修掉 4 个算法误报 / 坐标轴 Bug

| # | 痛点 | 修正后的判定 |
|---|---|---|
| 二 | 桌面空载就一直红字「爆显存」 | **必须同时满足**：① `vram_total > 0` 且物理专用显存已用 **≥ 88%**（15%~50% 时哪怕非本地几百 MB 也属正常共享映射，严禁报）；② 非本地显存 **≥ 256 MB** 且较平稳基线暴增 **≥ 64 MB**；③ 进度条文案固定 `已用 X.X GB / 总计 X.X GB (XX%)`，百分比严格 `clamp(0,100)` |
| 三 | 显卡待机 400MHz→300MHz 被误判降频元凶 | 增加 3D 高频门控：基线频率必须 **≥ 1200 MHz**（< 1000MHz 一律视为省电待机，直接跳过）；当前 3D 负载 **≥ 60%**；当前频率比基线跌 **> 20%** 且该帧真的在掉帧，才判定「撞墙降频」 |
| 四 | 空载时 Y 轴 `90.809335`、X 轴 `-651.29189` 乱码；ms 视角默认 100ms 太高 | 无数据/少于 2 帧：关闭 autoRange，X 固定 `(0,100)`，FPS 固定 `(0,144)`，ms 固定 `(0,35)`；FPS 视角下限锁死 0、上限 `max(120, 峰值×1.15)`（绝不收缩成 `[97,101]`）；ms 视角默认 `0~35 ms` 并在 **16.7 / 33.3 ms** 画两条浅灰参考虚线，只有出现 >35ms 真尖峰才微扩到 `min(峰值×1.1, 60)` |
| 五 | 100→98 帧、204→101 帧被当成「暴跌」 | 基准改为**最近 120 帧的局部中位数帧率**（不再拿全局 MAX FPS 比）；真顿挫必须同时满足：**A** `cur_fps ≤ 局部中位数×0.65`（相对跌幅 ≥35%）、**B** `cur_frametime − 局部中位数耗时 ≥ 8.0 ms`、**C** 豁免权 `cur_fps ≥ 85`（耗时 ≤11.7ms 人眼无感，绝不告警、绝不出暴跌病历卡）；没触发时病历卡隐藏或显示绿色 `🟢 全程运行丝滑 · 帧率波动处于正常微小范围内` |

> **规范自洽性提醒**：条件 A 与 B 取交集后，100 帧基线下的真实门槛是
> `1000/(10+8) ≈ 55.6 帧`（B 比 A 更严）。这是「必须同时满足」的字面结果，
> 已在 `tests/smoke_v4_runtime.py` 里显式记录（`100→60` 帧的跳变只有 6.7ms，被 B 过滤）。

### 3.2.1 v4.2：暗色主题下「设置」看不清（已修）

**痛点**：切到深空黑/纯黑后，右上角 ⚙ 弹出的菜单几乎整块是浅灰底+浅色文字（完全看不清），
「⚙ 设置」页也有大片白底浅字的区域。

**根因**：全局 QSS 只写了 `QWidget { color: fg }`，但**没有**给
`QMenu` / `QDialog` / `QScrollArea` 视口设背景色，也没有设置 `QPalette`。
于是文字被强制成浅色，背景仍是 Fusion 默认调色板的浅灰 —— 浅字压浅底。

**修法**（两层兜底，任何未被 QSS 命中的原生表面都跟随主题）：
1. QSS 补齐 `QMenu`（含 item:selected / disabled / separator）、`QDialog` / `QMessageBox`、
   `QScrollArea > QWidget#qt_scrollarea_viewport` 的背景与文字色规则；
2. 新增 `theme.build_palette()` / `theme.apply_app_palette()`，把整套主题色写进
   **QApplication 的调色板**（含 Disabled 组），`_apply_global_stylesheet()` 时一并下发。

**验证**：`tests/test_dark_settings_readability.py`（真实 windows 平台）对 4 套主题
（深空黑 / 纯黑 / CapFrameX / 浅色）× 3 个表面（⚙ 菜单 / 设置页 / 弹窗）测
「主色背景 = 主题色」与「文字对比度 ≥ 4.5（WCAG AA）、说明文字 ≥ 3.0」，
全部通过；预览图 `docs/preview_settings_dark.png`。

### 3.2.2 v4.3：关闭后还有残留 / 窗口「跑到屏幕上方」（已修）

**现象**：关掉 FrameLab 后点 × 没反应；任务管理器里还挂着进程；屏幕上飘着一个对话框；
后台还有 `PresentMon-x64.exe` 在吃 CPU。

**根因（三条叠加）**：

1. 启动的「权限提醒」用 `QDialog.open()` —— Qt 会把它变成 **WindowModal**，
   主窗口的关闭消息（WM_CLOSE）被模态过滤器吞掉 → 点 × 等于没点，只能强杀；
2. 权限提醒 / 11 项专业表格 / 进阶硬件面板都是**顶层窗口**，只要还有一个可见，
   Qt 的「最后一个窗口关闭才退出」就不成立 → 进程留在后台；
3. PresentMon 是外部 exe，被强杀时 Python 侧 `terminate()` 没机会执行 →
   孤儿进程 + 残留 ETW 会话（攒够 4 个就触发 error 1450 静默 0 帧）。

**修法**：

| 层 | 改动 |
|---|---|
| 权限提醒 | 显式 `setWindowModality(NonModal)` + `show()`（不再用 `open()`），主窗口随时可关 |
| 关闭收尾 | `closeEvent` 新增 `_closing` 闸门 + `_close_other_windows()`：把**所有**其它顶层窗口一并关掉，收尾后再 `quit()` 兜底，保证事件循环一定结束 |
| 子进程 | 新增 `framelab/procguard.py`：用 Windows **Job Object**（`KILL_ON_JOB_CLOSE`）把 PresentMon 绑成「父进程死则子进程必死」——正常退出 / 强杀 / 崩溃都生效 |
| 位置 | 权限提醒与进阶面板首次显示时居中到主窗口（不再被 Windows 甩到屏幕左上角） |

**验证**：`tests/test_close_cleanup.py`（13 项，含真实 Job 生效验证）；
EXE 实测两个场景 —— ① 权限提醒开着时点主窗口 ×：进程退出、0 残留窗口、0 残留 PresentMon；
② 任务管理器强杀：PresentMon 随父进程一起结束。

### 3.3 由前一份建议书带来的能力（v4.0，未回退）

| 条目 | 落点 |
|---|---|
| 四大帧数指标卡 | `ui/gauge.FpsCard` + `stats.window_stats`：平均帧（含「电竞级丝滑/流畅游玩/建议降画质」微字）、最高帧、最低帧 = 1% Low、顿挫次数（0 次绿色、>5 次红色） |
| 只留一条纯净 FPS 曲线 | `ui/chart.FpsChart`：单主折线（半透明底部渐变填充）+ 平均帧水平虚线 + 顿挫红点，右上角 `[FPS / ms]` 切换；旧版 3 条包络线与英文坐标已删 |
| 段位徽章 + 小白话术 | `ui/gauge.GradeBadge`（S/A/B/C）+ `ui/plain.py`（显存爆了→调低贴图质量、硬盘狂读→换 SSD、着色器编译→无需操作、CPU 单核满载→调低同屏人数/阴影…） |
| 瓶颈天平 | `stats.bottleneck` + `ui/gauge.BottleneckBar`：一句话说明「显卡满载 / CPU 清闲」并给建议 |
| 点击红点弹 3 句话病历卡 | `ui/diagnosis.py`：① 掉了多少帧 ② 罪魁祸首 ③ 一招解决，旁边 `[查看 11 项专业极客数据 ▸]` 才打开完整表格 |

### 3.4 拆分为模块（v4.0）

拆分方式：按 AST 顶层节点**原样搬迁**（不重写逻辑），再用静态分析自动推导跨模块 import。

拆分后必须解决的两个「单体遗留」问题（已解决并被测试守护）：

1. **主题热切换**：`from .theme import ACCENT` 会复制当时的颜色 ⇒ 主题切换静默失效。
   现在模块导入时调用 `theme.bind_globals(globals())` 持有可变副本并登记，
   `set_theme()` 广播到所有已登记模块。
2. **阈值热更新**：同理，`consts.bind_globals()` + `Config.apply_to_globals()` 广播。

`tests/test_static_integrity.py` 会把这两条变成红线（禁止冻结式 import、禁止各个
模块各自解释「程序目录」）。

### 3.5 清理过时无用文件（v4.0）

已删除（**项目外留有回滚副本**，见 §7）：

| 类别 | 内容 |
|---|---|
| 过时应用 | `FrameLab.exe`（2026-10-14 由旧单体构建，10.1 MB） |
| 过时备份 | `FrameLab.py.bak_tk` / `.bak_v2_*` / `.bak_v3_*` / `.bak_v32_*` |
| 过时脚本 | `_test_*.py`（11 个）、`_probe_*.py`（4 个）、`_smoke_ux_runtime.py` |
| 构建/缓存产物 | `_pi/`（PyInstaller 环境，13.7 MB）、`__pycache__/`、`_probe_out/`（4.1 MB 旧抓包与截图）、`_v32_shots/` |
| v4.1 废弃模块 | `framelab/ui/accordion.py`（改为 `navtabs.py` + `pages.py`） |

## 4. 测试

```powershell
# 全部（14 个脚本，纯逻辑 + 静态不变量 + offscreen 运行时冒烟 + 暗色可读性 + 退出清理 + 版面零抖动）
Get-ChildItem tests\*.py | Where-Object { $_.Name -ne 'render_preview.py' -and $_.Name -notlike '_*' } | ForEach-Object { python $_.FullName }

# 单独跑
python tests\test_v1_layout.py            # 版面零抖动 / 折线去重 / 换肤持久化 / 归因表可读性 / 每线程窗口 / 换页归因 / 白底对比度 / **v1.0.4 面板瘦身+设置人话化+诊断节流（§1~§16）/ 手动开始采集+清空重采（§6d）**
python tests\test_algofix.py              # 3 个算法修复：爆显存门控 / 降频门控 / 顿挫双重门控（正+负用例）
python tests\test_chart_axis.py           # 坐标轴：空载 0~100 / FPS 0~144 / ms 0~35 + 参考虚线
python tests\smoke_v4_runtime.py          # 端到端：导航卡/五页/徽章/病历卡红绿两态/坐标轴/降频/显存门控
python tests\test_dark_settings_readability.py  # v4.2：暗色主题下 菜单/设置页/弹窗 对比度 ≥ WCAG AA（4 主题）
python tests\test_close_cleanup.py        # v4.3：关闭后无残留窗口 / 无孤儿 PresentMon（含 Job 联动杀验证）
python tests\test_static_integrity.py     # 拆分不变量：无漏 import / 无冻结常量 / 主题广播 / 路径来源
python tests\test_plain_and_stats.py      # 小白话术映射 + 帧率口径 + 评级 + 阈值广播
python tests\test_theme.py                # 主题/热键/图表样式契约
python tests\test_ux.py                   # 59 项 UX 契约（配置/数据源/水印/热键/黑名单/CSV/ETW/打点）
python tests\test_regression.py           # 阻断级缺陷回归（时间轴单位、导入失败复位、基线鲁棒性…）
python tests\test_lifecycle.py            # 生命周期：线程可 join、无 ETW 残留
python tests\smoke_ux_runtime.py          # 旧 UX 冒烟（导入/水印/导出列/关闭流程）
python tests\render_preview.py            # 渲染界面预览图到 docs/preview_*.png（需真实 windows 平台）
```

`render_preview.py` 生成的预览图：`docs/preview_deepspace.png`（总览·深空黑）、
`docs/preview_light.png`（总览·浅色）、`docs/preview_ms_view.png`（ms 视角）、
`docs/preview_page_cpu.png` / `_gpu.png` / `_mem.png` / `_net.png`（四张导航页）、
`docs/preview_settings_dark.png` / `_light.png`（⚙ 设置页：v1.0.4 中文分组表单 + 暗/浅两套对比度证据）。

## 5. 打包成 EXE（PyInstaller）

产物：`dist\FrameLab.exe`（单文件、免安装、便携）。

```powershell
# 一条命令：创建/复用构建环境 → 打包 → 输出 dist\FrameLab.exe
pwsh -File build_tools\build_exe.ps1

# 重建构建环境（PyInstaller 升级 / 环境损坏时）
pwsh -File build_tools\build_exe.ps1 -RebuildVenv
```

| 项 | 说明 |
|---|---|
| 构建环境 | 默认在**项目外**：`<项目>\..\_build_env\framelab_venv`（venv + `--system-site-packages`，复用系统已装的 PyQt6/pyqtgraph/numpy，只额外装 PyInstaller）。可用 `-Venv <路径>` 或环境变量 `FRAMELAB_BUILD_VENV` 覆盖 |
| 中间产物 | PyInstaller 的 work 目录也在项目外（`_build_env\work`），打完自动清掉，项目里只留 `dist\` |
| 体积 / 耗时 | 约 **50 MB**、**45~60 秒**（本机 1.0.4 实测：50.3 MB / 46 秒；Python 3.11 + PyQt6 6.11 + numpy 2.4 + pyqtgraph 0.14） |
| 启动耗时 | 单文件模式约 **4 秒**（每次启动要先把 50MB 解包到 `%TEMP%\_MEI*`）。若嫌慢，把 spec 里的 `EXE(...)` 改成 `COLLECT(...)` 出 onedir 版即可（文件夹形式，启动 <1 秒） |
| 图标 / 版本 | `assets\framelab.ico`（16~256 多尺寸，与深空黑主题同色）+ `build_tools\version_info.txt`（资源管理器「属性→详细信息」里显示 1.0.4.0） |
| 隐藏导入 | spec 里用 `collect_submodules("framelab")` 显式列出全部子模块，防止「函数内延迟导入」被静态分析漏掉 |

**便携式行为（重要）**：`framelab/paths.py` 已按 `sys.frozen` 分支处理 ——
打包后「程序目录」= **exe 所在目录**。所以：

- `framelab_config.json` 会生成/读取 **exe 旁边**那一份（不是项目目录里的）；
  想沿用源码里的设置，把 `framelab_config.json` 复制到 exe 同目录即可。
- PresentMon 查找顺序不变（exe 目录 → `PresentMon\` 子目录 → PATH → 设置里指定的路径）。
- 运行日志仍写在 `%USERPROFILE%\.framelab\framelab_diag.log`（不污染程序目录）。

**已验证**（本机实测）：从项目目录启动、复制到其他目录启动都正常；
窗口标题 `FrameLab 1.0.4 · 游戏卡顿一站式体检台`；界面/实时数据/图表全渲染；
配置沿用用户设置且未被改写；关闭后 **无残留 FrameLab / PresentMon 进程**。

> 提示：普通双击启动时，程序会在 700ms 后弹一次「以管理员身份运行可获得完整采集能力」的提醒
> （ETW 需要管理员）。**右键 exe → 以管理员身份运行** 就不会有这个提醒。

## 6. 配置与日志

| 文件 | 位置 | 说明 |
|---|---|---|
| `framelab_config.json` | 与 `FrameLab.py` 同目录 | 主题、图表样式、热键、PresentMon 路径 + 12 个诊断阈值；脏值一律回落默认，不会让程序起不来 |
| `framelab_diag.log` | `%USERPROFILE%\.framelab\` | 结构化异常日志（关键路径异常必留痕、同类限频） |

路径来源统一由 `framelab/paths.py` 提供（拆分后踩过的坑：`__file__` 变成包目录，
配置被写进 `framelab/` 导致用户旧配置失效）。

## 7. 迁移与回滚

删除前的全部旧文件都留有副本（项目目录**外**）：

```
F:\Desktop\不完美项目\_VRAMWatch_v3.2_backup\
    FrameLab_v3.2_monolith.py        原 8000 行单体（357 KB）
    FrameLabWindow_v3.2_ref.py       原 FrameLabWindow 单独摘录
    FrameLab.exe                     旧可执行文件
    FrameLab.py.bak_*                4 个历史备份
    _test_*.py / _probe_*.py / _smoke_ux_runtime.py
```

回滚方式：把 `FrameLab_v3.2_monolith.py` 复制回项目目录改名 `FrameLab.py`，
并临时把 `framelab/` 目录移走即可（旧单体不依赖包）。

## 8. 已知限制

- 仅 Windows；真实采集需要 PresentMon.exe 与（部分场景）管理员权限。
- 1% Low 用帧耗时 P99 换算；样本 < 1000 帧时 0.1% Low 会退化为 P99（界面会标注）。
- 最高帧取的是窗口内最高**瞬时**帧率，切屏/加载瞬间的极端值会拉高它，属预期口径。
- 「模拟数据」会在图表上打水印并在侧栏标注数据源，避免被误当真实采集。
- 折线图**默认**每帧重新锁定坐标轴范围（这是「严禁乱码/严禁窄区间」的代价）；
  一旦你手动滚轮/拖拽缩放，就会停止自动锁轴、保留你的视野，直到切换 FPS/ms 视角
  或「🧹 清空重采」才复位（v1.0.4 修复「数轴无法放大」）。
- 显卡频率门控依赖驱动上报（AMD ADL / NVIDIA NVML）；本机未上报时该行标注「不可判定」，
  不会给出假结论。
- **单文件 EXE 被强杀会残留 `%TEMP%\_MEI*`**（约 120 MB/次）——这是 PyInstaller 单文件模式的
  固有行为（解包目录靠正常退出时清理）。正常关闭不残留；强杀后可手动删掉 `%TEMP%\_MEI*`。
- PresentMon 与 ETW 会话已用 Job Object 兜底（v4.3）：FrameLab 一死它们必然跟着结束；
  但 `logman stop` 需要管理员权限，非管理员强杀后 ETW 会话可能残留 Running，
  下次启动会自动清理（`PresentMonBridge` 启动前 `cleanup_stale()`）。
