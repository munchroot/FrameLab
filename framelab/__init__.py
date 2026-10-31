# -*- coding: utf-8 -*-
r"""FrameLab 1.0.4 —— 游戏掉帧研究工作台 (PresentMon + Hardware Probe Inside)

【v1.0.4】设置按钮瘦身 + 根治「FrameLab 自己卡住」：
  1. **删掉 v3.2 遗留的 5 个进阶硬件页签**（CPU 热力图 / 显存监视 / 内存换入 /
     着色器与引擎 / 事件流）—— 前四者与主界面 5 个导航页信息完全重复，
     且它们的表格刷新调用了一个模块拆分时**丢失的方法** `_sync_table`，
     面板一打开就每 tick 抛 AttributeError。现在 ⚙ 只留「设置 + 事件流」，
     齿轮菜单同步删掉「展开进阶硬件面板 / 打开 PresentMon 目录」两条老入口。
  2. **诊断重算节流**：`SnapshotDiffInspector.inspect()` 末尾的会话级归因是
     O(全部帧 × 卡顿帧)，且缓存键含帧数（实时采集时永不命中）。旧实现被
     「顿挫横幅 / 自动弹卡 / 锁定卡跟随」三条路径以 30Hz 反复触发，
     会话越长单次越贵，最终把 33ms 的 UI 定时器彻底淹没 —— 亦即用户报告的
     「FrameLab 会自己卡住」。现在横幅 ≥0.5s、弹卡与跟随刷新 ≥1s 各一次，
     归因算法本身一行未动（同样的判定、同样的结论，只是不再 30Hz 重算）。
  3. 顺手清掉 v3.2 只写不读的死字段（_display_* / _chart_mode / _env_cache /
     _metric_cache_ui / _table_cache / OVERVIEW_RENDER_CAP / ENV_*）与
     `CpuHeatmapGrid`（v1.0.1 起主界面已不用）、`heat_color/_text_on/HEAT_STOPS`。

【v1.0.3】浅色皮肤红色可读性 + CPU 页合并卡：
  1. `ui/plain.py` 的 LEVEL_COLOR / GRADE_COLOR 原来**写死深色主题色板**
     （#ff5d5d 在白底上对比度只有 2.4:1），改为只存**角色名**、取色时按当前
     主题解析（`level_color()/grade_color()`），并用 _ThemeMap 兼容旧取值写法；
  2. 浅色主题 warn/orange 加深（#9a5b00 / #c23c11），四套主题全色审计 ≥4.5:1；
  3. CPU 页把「两列 KV + 频率/功率折线 + 每线程折线」合并成一张 CpuFreqCard，
     每线程折线加粗到 2.2px + 渐变填充 + 斑马纹行（用户要求"折线明显点"）。

【v1.0.0 正式版】名称固定为 FrameLab（不再带前缀品牌），版本从 4.3 起跳为 1.0.0：
  1. **版面零抖动**：横幅/结论行改为固定高度 + 单行省略号（ElidedLabel），
     病历卡改为常驻占位（不再 show/hide），数据到来与顿挫弹卡都不再推挤卡牌；
  2. **文案瘦身**：删除重复说明句与装饰性灰字，能用符号/短词就不用句子；
  3. **CPU 页新增「频率 + 功率」双折线**（`ui/cpu_trend.py`），
     数据源三级：PDH 功率计 → LibreHardwareMonitor WMI → 估算（标注 ≈）；
  4. 热力块矩阵按「行数优先」均分列数（12 核 = 6×2，不再 11+1 落单）。

v3.2 版面重构 + 极客可视化 + 四大新探针（对齐「左侧导航 / 右侧展示」的现代分栏
与「数据必须可视化」的极客理念）：
  1. **左右分栏**：废除 v3.1 上中下堆叠，改为
     「左侧固定侧边栏（约 20%）+ 右侧主展示区（约 80%）」。
     LOGO / 引擎状态灯 / 追踪进程 / 开始暂停 / 模拟流 / CSV 导入 / 导出 /
     清理 ETW / 打点 / 数据源标签 / **全局唯一的 ⚙ 设置按钮**（v1.0.4 起只含
     设置与事件流两个页签）
     全部收进侧边栏；主区依次是诊断横幅 → 四张分位大卡 → 图表卡。
  2. **图表视觉降噪与坐标系改造**：
     · X 轴从「秒」改为**帧序号**（波形紧凑连续，暂停/切窗口的时间空洞不再拉白）；
     · Y 轴 `setLimits(yMin=0)` + `_clamp_y_nonneg` 双保险，**永不为负**；
     · **删除跟着波峰乱跳的红虚线**（v3.1 的 Stuttering MA 包络），
       只保留发光主曲线 + 1% Low 平滑包络 + 0.1% Low 包络 + 异常事件打点。
  3. **修复图表双模式切换 Bug**：「自由缩放」放大后切「全局概览」不再卡在放大视野
     —— 原因与修法见 `_on_chart_mode_changed`（先 disableAutoRange + updateAutoRange
     + setRange，最后才 enableAutoRange；单开自动范围会被随后的 setXRange 顺手关掉）。
  4. **极客热力图**（v1.0.4 随进阶面板移除，仅存档说明）：CPU 矩阵从「纯数字格子」
     改为热力块矩阵（`CpuHeatmapGrid`）—— 已被主界面 CPU 页的每线程折线 + 柱状条取代。
  5. **四大新底层探针**（补全 99% 卡顿归因）：
     · ①硬盘 I/O 资产流式加载：PDH `\Process(*)\IO Read Bytes/sec` > 50MB/s + 掉帧；
     · ②引擎 GC / 主线程锁死：帧时间尖峰 ≥ max(50ms, 基线×3) 且 MsGPUBusy 与
       MsCPUBusy **同时 < 20%** ⇒ 显卡和 CPU 都没在干活（纯 `classify_gc_lock` 纯函数）；
     · ③显卡撞墙降频：AMD ADL PMLog（`sensor1` = GFXCLK，见实测契约 8）或
       NVIDIA NVML 读核心频率，高 3D 负载下相对基线暴跌 > 15% → 温度墙/功耗墙；
     · ④网络同步卡顿：PDH `\Network Interface(*)\Bytes Received/sec`（剔除回环/虚拟
       网卡）接收速率骤降至基线 25% 以下 + 同时掉帧 → 丢包 / 服务器 Tick 延迟。
  6. **归因优先级重排**：`banner_verdict` 把因果链更上游的维度排前
     （GPU 频率 / 磁盘 I/O / 网络同步 → GPU 忙时比 / 显存 / 换页 / CPU / 着色器 / GC）。

v3.1 深度 UX 重构（对齐商业游戏加加类软件的「傻瓜、活泼 + 极客硬核」理念）：
  1. 底层容量：FrameStore 100000 帧 / 20000 快照（15 分钟以上对账不丢史），
     _drain_queue 预算 8ms → 16ms（吃满一帧渲染时间而不卡死事件循环）。
  2. 图表双模式：「自由缩放（极客）」滚轮/拖拽自由探索最近 2500 帧；
     「全局概览（体检）」禁用拖拽 + X 轴每 tick 强制铺满全部帧，
     整局曲线随游戏进行自动向左挤压（抽稀渲染保 30 FPS，尖峰打点帧永不丢）。
  3. 首屏卡片化：废除下半部 QSplitter + QTabWidget——
     顶部控制栏卡 / 诊断横幅卡（内嵌流畅度三色条）/ 四张分位大卡 /
     图表卡（含模式切换）；病灶诊断卡改为点击尖峰滑出的抽屉（✕ 收起）；
     CPU/内存/显存/着色器/事件流/设置全部收进「🚀 进阶硬件面板」独立非模态窗口。
  4. 视觉打磨：全部卡片 border-radius 8px + 细微边框 + QGraphicsDropShadowEffect
     悬浮阴影（图表卡除外——30 FPS 重绘子树叠加模糊特效得不偿失）；
     pyqtgraph 右键菜单递归汉化（View All / X Axis / Mouse Enabled → 中文）。

================================================================================
架构总览
================================================================================
    ┌──────────────────────┐   queue.Queue    ┌──────────────────────────┐
    │ PresentMonBridge 线程 │ ───────────────► │                          │
    │ (子进程解析 stdout CSV)│                  │   UI 线程 = 唯一写者     │
    ├──────────────────────┤   queue.Queue    │   FrameStore             │
    │ HardwareEngine 线程   │ ───────────────► │   BaselineTracker        │
    │ (PDH/DXGI/PSAPI 轮询) │                  │   SnapshotDiffInspector  │
    └──────────────────────┘                  └──────────────────────────┘
                                             │
                                          pyqtgraph HUD (30 FPS)

模块划分（文件内严格分区，可按需拆分为独立 .py）
    §1  主题常量 / 工具函数
    §2  底层 ctypes 探针（PdhSession / dxgi_adapters / GpuClockProbe /
        NameTable / per_core_max_mhz / CacheWatcher / _pagein_top）
    §3  数据模型（Event / HardwareSnapshot / FrameRecord / DiffRow）
    §4  ETW 会话管家（解决 error 1450 静默失败）
    §5  HardwareEngine 轮询线程（含 v3.2 的进程 I/O / 网络 / GPU 频率采集）
    §6  PresentMonBridge 采集线程（含离线模拟降级）
    §7  FrameStore / BaselineTracker / SnapshotDiffInspector（含 classify_gc_lock）
    §8  pyqtgraph + PyQt6 赛博电竞深色 HUD
    §8b 探针读数条（ProbeReadoutBar；CPU 热力块 CpuHeatmapGrid 已于 v1.0.4 移除）
    §9  入口

================================================================================
实测契约（2026-10-23 于本机 AMD RX 6650 XT / JustCause3.exe 场景实测闭环）
================================================================================
1) 真因不是参数错，而是 **ETW 会话耗尽**：
   PresentMon 进程被 kill/terminate 后，其 ETW session 必然残留在
   `logman query -ets` 中处于 Running；堆积到 4 个即触发
   `error: failed to start trace session: error code 1450.`，
   表现为 "Started/Stopped recording" 但 **0 行数据**（最隐蔽的静默失败）。
   → 本程序用唯一 session 名 + `--stop_existing_session` + `logman stop` 兜底，
     并在启动时清理上次残留的 FrameLab 会话。
2) `--timed` 不配 `--terminate_after_timed` **必定泄漏** ETW 会话。
3) `--dont_restart_as_admin` 是 **未识别选项** → rc=1 立即退出（绝不使用）。
4) `--set_circular_buffer_size` 只解决 "events were lost" 丢包，**不解决 1450**；
   清理干净 ETW 后 256/2048/65536 全部正常出帧。推荐 4096（CapFrameX 同值）。
5) 列契约（**必须按列名解析，不能按下标**，列数随 flag 变 18/19/21/26/32）：
   · 默认模式（不加 --v1/--v2）→ 含 MsBetweenPresents / MsCPUBusy / MsGPUBusy /
     MsGPULatency / MsGPUTime / TimeInSeconds   ← 本程序采用此模式
   · --v1_metrics → 列名全小写 ms*，无 MsCPUBusy/MsGPUBusy
   · --v2_metrics → FrameTime/CPUBusy/GPUBusy，无 MsBetweenPresents
6) 非管理员：自己账户的进程正常显示；`<unknown>` 全是 dwm.exe 等取不到 exe 路径的
   提权进程，**永远无法被 --process_name 命中** → 不做命令行过滤，改为采集后
   按 Application + ProcessID 自行过滤。
7) CSV 为 UTF-8 **with BOM** + CRLF → 必须 utf-8-sig 读取；NA 视为缺失而非 0。

================================================================================
v3.2 新探针实测契约（2026-10-24 于本机 AMD RX 6650 XT / Adrenalin 26.9.1 实测）
================================================================================
8) **AMD GPU 核心频率 = ADL PMLog sensor 1（GFXCLK）**，实测定论：
   · `atiadlxx.dll` 的 `ADL2_Main_Control_Create` + `ADL2_New_QueryPMLogData_Get`
     均 rc=0，**不需要管理员权限**，返回驱动侧实时值（非标称规格）。
   · 本机 12 个非零传感器；用 WebGL 重负载页面施压 25 秒做**空载 vs 满载对照**：
       sensor 1  : 空载 231/505/2413 (min/avg/max) → 满载 2382/2423/2468  **×4.80**
       sensor 19 : 空载   3/  8/  18             → 满载   20/ 37/ 53      **×4.41**
       sensor 23 : 空载  32/ 33/  34             → 满载   49/ 57/ 59      ×1.71
       其余（2/8/14/15/20/21/27/40/41）全部 ×1.00~1.27 = 与负载无关
     ⇒ **sensor 1 = 核心频率（MHz）**（空载低功耗档在数百 MHz 大幅抖动、
     满载稳定贴住 2.4GHz 上限，这正是 GFXCLK 的特征），
     **sensor 8 = 核心温度（℃）**（49→51，随负载缓升），
     sensor 14 = 风扇 RPM、sensor 15 = 风扇 %。sensor 19/23 未定名，不采用。
   · 坑①：ctypes 嵌套 Structure 必须在**模块级**定义（类体不是闭包作用域，
     嵌套类里引用外层类体的名字会 NameError；写字符串 "_Sensor" 会
     抛 "second item in _fields_ tuple must be a C type"）。
   · 坑②：`Main_Control_Create` 的 malloc 回调 CFUNCTYPE **必须强引用住**
     （被 GC 后驱动再调用即崩进程）；ADL context 必须复用、退出才 Destroy。
   · 频率读不到时**返回 None 而不是 0** —— 0MHz 会让降频判定算出 -100% 的假降频。
9) **PDH Process 对象实例名规则**（`\Process(*)\...` 实测 295 个实例）：
   · 实例名 = 进程名，**同名多进程会出现重复条目**（如 "DeepSeek Harness" 出现 11 次、
     "AmDRSServ"/"Agents Anywhere" 多次）；本机**未见 15 字符截断**
     （"FNPLicensingService64" 19 字符完整）——但 Windows 文档明确 Process
     对象名上限为 15 字符，故实现里对两种情形都做了兼容（见 _io_net 的三段回退匹配）。
   · `#n` 是**同名实例序号而非 PID** ⇒ 必须剥掉 `#...` 后按进程名聚合求和；
     把 `#n` 当 PID 会在 chrome/svchost 这类同名多进程上张冠李戴。
   · 速率计数器需两次 `PdhCollectQueryData`（间隔 ≥0.2s）才有有效值，首读为 0。
10) **PDH 网络计数器**（`\Network Interface(*)\Bytes Received/Sent/sec`）实测 rc=0，
    本机实例为 "Intel[R] Wi-Fi 6E AX210 160MHz" 与 "Realtek PCIe 2.5GbE Family
    Controller"；必须剔除 Loopback/isatap/Teredo/VPN TAP/Hyper-V vEthernet
    （回环永远在跑，计入会让「网络归零」判定失效）。

依赖：pip install PyQt6 pyqtgraph numpy

================================================================================
包结构（v4.0 拆分）
================================================================================
    FrameLab.py                 启动器（薄壳，保持 python FrameLab.py 可用）
    framelab/theme.py           主题系统：配色预设 / 热切换注册表 / 图表语义常量
    framelab/consts.py          诊断阈值常量（可被 framelab_config.json 覆盖）
    framelab/utils.py           日志 / 有界队列 / 格式化 / pyqtgraph 符号校验
    framelab/probes.py          底层 ctypes 探针（PDH / DXGI / ADL / 缓存监视）
    framelab/models.py          数据模型 Event / HardwareSnapshot / FrameRecord / DiffRow
    framelab/etw.py             ETW 会话管家（error 1450 静默失败的根治）
    framelab/engines/           HardwareEngine / PresentMonBridge 采集线程
    framelab/analysis.py        BaselineTracker / FrameStore / SnapshotDiffInspector 归因
    framelab/config.py          framelab_config.json 读写
    framelab/hotkeys.py         全局热键
    framelab/winadmin.py        管理员权限 / PresentMon 路径 / 数据源判定
    framelab/csvio.py           CSV 后台读取
    framelab/ui/widgets.py      通用可视化组件（单行省略标签 / 卡顿条 / 指标卡 / 读数条）
    framelab/ui/navtabs.py      左侧 5 张大号视图导航卡（图标 + 模块名 + 实时徽章）
    framelab/ui/gauge.py        GamePP 风格图形元件（胶囊条 / 圆环 / 能量柱 / 段位徽章…）
    framelab/ui/pages.py        QStackedWidget 的 5 个全屏页面（总览/CPU/显卡/内存/网络）
    framelab/ui/chart.py        实时 FPS 曲线（渐变填充 + 均值虚线 + 漏帧红点 + 锁定坐标轴）
    framelab/ui/diagnosis.py    极简卡顿病历卡 + 11 项专业对比表
    framelab/ui/plain.py        小白话术映射表（术语 -> 人话 + 解决建议）
    framelab/ui/main_window.py  主窗口装配（只做布局与接线）
    framelab/app.py             进程入口 main()
================================================================================
"""

__version__ = "1.0.4"
__all__ = ["__version__"]
