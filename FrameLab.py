# -*- coding: utf-8 -*-
r"""
FrameLab 4.0 —— 启动器（薄壳）

原 3.2 版的 8000 行单体 `FrameLab.py` 已按模块拆分到 `framelab/` 包：
    framelab/theme.py            主题 / 配色 / 图表语义常量 / pg 菜单汉化
    framelab/consts.py           诊断阈值常量（可被 framelab_config.json 覆盖）
    framelab/utils.py            日志 / 有界队列 / 格式化 / 符号校验
    framelab/probes.py           底层 ctypes 探针（PDH / DXGI / ADL / 缓存监视）
    framelab/models.py           Event / HardwareSnapshot / FrameRecord / DiffRow
    framelab/etw.py              ETW 会话管家（error 1450 根治）
    framelab/engines/            HardwareEngine / PresentMonBridge 采集线程
    framelab/analysis.py         BaselineTracker / FrameStore / SnapshotDiffInspector
    framelab/config.py           framelab_config.json 读写
    framelab/hotkeys.py          全局热键
    framelab/winadmin.py         管理员权限 / PresentMon 路径 / 数据源判定
    framelab/csvio.py            CSV 后台读取
    framelab/stats.py            帧率统计口径（平均/最高/1% Low/漏帧/评级/瓶颈）
    framelab/ui/widgets.py       通用可视化组件（指标卡 / CPU 热力图 / 读数条）
    framelab/ui/gauge.py         GamePP 风格图形元件（胶囊条 / 环形图 / 段位徽章…）
    framelab/ui/accordion.py     左侧手风琴抽屉（CPU / 显卡显存 / 内存硬盘 / 网络引擎）
    framelab/ui/fps_cards.py     （见 gauge.FpsCard / GradeBadge / BottleneckBar）
    framelab/ui/chart.py         实时 FPS 曲线（渐变填充 + 均值虚线 + 漏帧红点）
    framelab/ui/diagnosis.py     极简卡顿病历卡 + 11 项专业对照表
    framelab/ui/plain.py         小白话术映射表
    framelab/ui/base_window.py   从 3.2 原样迁移的可复用窗口逻辑（设置/CSV/ETW/热键…）
    framelab/ui/main_window.py   新版主窗口（GamePP 风格版面 + 每帧刷新）
    framelab/app.py              进程入口

本文件只做两件事：① 重新导出旧顶层名字（`import FrameLab as F` 的老脚本/测试仍然可用）；
② 提供 `python FrameLab.py` 入口。

依赖：pip install PyQt6 pyqtgraph numpy
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- 兼容导出
from framelab import __version__                                     # noqa: F401
from framelab.analysis import (BaselineTracker, FrameStore,          # noqa: F401
                               SnapshotDiffInspector)
from framelab.config import (CONFIG_EXTRA_KEYS, CONFIG_FILENAME,     # noqa: F401
                             CONFIG_SCHEMA, DEFAULT_CONFIG, Config,
                             config_path, default_config_dict)
from framelab.consts import *                                        # noqa: F401,F403
from framelab.csvio import CsvBackgroundReader                       # noqa: F401
from framelab.engines.hardware import HardwareEngine                 # noqa: F401
from framelab.engines.presentmon import (PresentMonBridge,           # noqa: F401
                                         detect_presentmon_capabilities,
                                         find_presentmon_binary)
from framelab.etw import EtwSessionManager                           # noqa: F401
from framelab.hotkeys import (MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT,  # noqa: F401
                              MOD_WIN, WM_HOTKEY, GlobalHotkeyManager,
                              _HotkeyNativeFilter, format_hotkey, parse_hotkey)
from framelab.models import DiffRow, Event, FrameRecord, HardwareSnapshot  # noqa: F401
from framelab.probes import (CacheWatcher, GpuClockProbe, NameTable,  # noqa: F401
                             PdhSession, cpu_count, dxgi_adapters,
                             per_core_max_mhz)
from framelab.stats import bottleneck, window_stats                   # noqa: F401
from framelab.theme import *                                          # noqa: F401,F403
from framelab.ui.main_window import FrameLabWindow                    # noqa: F401
from framelab.ui.widgets import (MetricCard, ProbeReadoutBar,     # noqa: F401
                                 StutterBarWidget)
from framelab.utils import (PG_SYMBOLS, BoundedQueue, DIAG, DiagLog,  # noqa: F401
                            fmt_delta, human_size, is_na,
                            pages_to_mb_per_s, pg_symbol, safe_float)
from framelab.winadmin import (detect_data_source, is_admin,          # noqa: F401
                               make_sim_watermark, open_dir_in_explorer,
                               presentmon_dir, presentmon_missing_hint,
                               relaunch_as_admin)

# 兼容层自身也登记为「主题色副本」：老脚本读 F.ACCENT / F.BG 会跟随主题切换
from framelab.theme import bind_globals as _bind_theme_globals      # noqa: E402
_bind_theme_globals(globals())

# 主题常量与旧顶层名字保持同名可见（from framelab.theme import * 已覆盖）


def main():
    from framelab.app import main as _main
    return _main()


if __name__ == "__main__":
    sys.exit(main())
