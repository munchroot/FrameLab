# -*- coding: utf-8 -*-
r"""
StutterTriage 4.0 —— 启动器（薄壳）

原 3.2 版的 8000 行单体 `StutterTriage.py` 已按模块拆分到 `stuttertriage/` 包：
    stuttertriage/theme.py            主题 / 配色 / 图表语义常量 / pg 菜单汉化
    stuttertriage/consts.py           诊断阈值常量（可被 stuttertriage_config.json 覆盖）
    stuttertriage/utils.py            日志 / 有界队列 / 格式化 / 符号校验
    stuttertriage/probes.py           底层 ctypes 探针（PDH / DXGI / ADL / 缓存监视）
    stuttertriage/models.py           Event / HardwareSnapshot / FrameRecord / DiffRow
    stuttertriage/etw.py              ETW 会话管家（error 1450 根治）
    stuttertriage/engines/            HardwareEngine / PresentMonBridge 采集线程
    stuttertriage/analysis.py         BaselineTracker / FrameStore / SnapshotDiffInspector
    stuttertriage/config.py           stuttertriage_config.json 读写
    stuttertriage/hotkeys.py          全局热键
    stuttertriage/winadmin.py         管理员权限 / PresentMon 路径 / 数据源判定
    stuttertriage/csvio.py            CSV 后台读取
    stuttertriage/stats.py            帧率统计口径（平均/最高/1% Low/漏帧/评级/瓶颈）
    stuttertriage/ui/widgets.py       通用可视化组件（指标卡 / CPU 热力图 / 读数条）
    stuttertriage/ui/gauge.py         GamePP 风格图形元件（胶囊条 / 环形图 / 段位徽章…）
    stuttertriage/ui/accordion.py     左侧手风琴抽屉（CPU / 显卡显存 / 内存硬盘 / 网络引擎）
    stuttertriage/ui/fps_cards.py     （见 gauge.FpsCard / GradeBadge / BottleneckBar）
    stuttertriage/ui/chart.py         实时 FPS 曲线（渐变填充 + 均值虚线 + 漏帧红点）
    stuttertriage/ui/diagnosis.py     极简卡顿病历卡 + 11 项专业对照表
    stuttertriage/ui/plain.py         小白话术映射表
    stuttertriage/ui/base_window.py   从 3.2 原样迁移的可复用窗口逻辑（设置/CSV/ETW/热键…）
    stuttertriage/ui/main_window.py   新版主窗口（GamePP 风格版面 + 每帧刷新）
    stuttertriage/app.py              进程入口

本文件只做两件事：① 重新导出旧顶层名字（`import StutterTriage as F` 的老脚本/测试仍然可用）；
② 提供 `python StutterTriage.py` 入口。

依赖：pip install PyQt6 pyqtgraph numpy
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- 兼容导出
from stuttertriage import __version__                                     # noqa: F401
from stuttertriage.analysis import (BaselineTracker, FrameStore,          # noqa: F401
                               SnapshotDiffInspector)
from stuttertriage.config import (CONFIG_EXTRA_KEYS, CONFIG_FILENAME,     # noqa: F401
                             CONFIG_SCHEMA, DEFAULT_CONFIG, Config,
                             config_path, default_config_dict)
from stuttertriage.consts import *                                        # noqa: F401,F403
from stuttertriage.csvio import CsvBackgroundReader                       # noqa: F401
from stuttertriage.engines.hardware import HardwareEngine                 # noqa: F401
from stuttertriage.engines.presentmon import (PresentMonBridge,           # noqa: F401
                                         detect_presentmon_capabilities,
                                         find_presentmon_binary)
from stuttertriage.etw import EtwSessionManager                           # noqa: F401
from stuttertriage.hotkeys import (MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT,  # noqa: F401
                              MOD_WIN, WM_HOTKEY, GlobalHotkeyManager,
                              _HotkeyNativeFilter, format_hotkey, parse_hotkey)
from stuttertriage.models import DiffRow, Event, FrameRecord, HardwareSnapshot  # noqa: F401
from stuttertriage.probes import (CacheWatcher, GpuClockProbe, NameTable,  # noqa: F401
                             PdhSession, cpu_count, dxgi_adapters,
                             per_core_max_mhz)
from stuttertriage.stats import bottleneck, window_stats                   # noqa: F401
from stuttertriage.theme import *                                          # noqa: F401,F403
from stuttertriage.ui.main_window import StutterTriageWindow                    # noqa: F401
from stuttertriage.ui.widgets import (MetricCard, ProbeReadoutBar,     # noqa: F401
                                 StutterBarWidget)
from stuttertriage.utils import (PG_SYMBOLS, BoundedQueue, DIAG, DiagLog,  # noqa: F401
                            fmt_delta, human_size, is_na,
                            pages_to_mb_per_s, pg_symbol, safe_float)
from stuttertriage.winadmin import (detect_data_source, is_admin,          # noqa: F401
                               make_sim_watermark, open_dir_in_explorer,
                               presentmon_dir, presentmon_missing_hint,
                               relaunch_as_admin)

# 兼容层自身也登记为「主题色副本」：老脚本读 F.ACCENT / F.BG 会跟随主题切换
from stuttertriage.theme import bind_globals as _bind_theme_globals      # noqa: E402
_bind_theme_globals(globals())

# 主题常量与旧顶层名字保持同名可见（from stuttertriage.theme import * 已覆盖）


def main():
    from stuttertriage.app import main as _main
    return _main()


if __name__ == "__main__":
    sys.exit(main())
