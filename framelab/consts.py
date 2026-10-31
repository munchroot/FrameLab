# -*- coding: utf-8 -*-

"""诊断阈值与常量（可被 framelab_config.json 覆盖；bind_globals 让各模块热更新）。"""

import re

# ---- 主题/阈值热更新绑定（本模块持有可变副本）----
from .theme import bind_globals as _bind_theme_globals
_bind_theme_globals(globals())


# 诊断阈值
SPILL_THRESHOLD      = 64 << 20     # 非本地显存告警阈值 64MB


FREQ_DROP_RATIO      = 0.18         # 单核相邻采样跌幅 > 18% 记降频


CACHE_BURST_FILES    = 8            # 着色器缓存新增文件数爆发阈值


CPU0_JAM_THRESHOLD   = 92.0         # CPU0 占用率 ≥ 92% 记拥堵


CPU0_IDLE_OTHERS     = 70.0         # 其他核心均值低于此值才算「其他核空闲」


MIGRATE_FREQ_GAP     = 400.0        # 跨核迁移时两核基频差阈值 (MHz)


MAX_HW_AGE          = 1.2          # 硬件快照最大可用年龄(秒)，超过则诊断卡标注「不可判定」



# ---- v3.2 四大新探针阈值 ----------------------------------------------------
IO_STREAM_WARN_MB   = 20.0          # 进程磁盘读取 > 20MB/s → 资产流式加载预警


IO_STREAM_DANGER_MB = 50.0          # > 50MB/s → 硬盘 I/O 瓶颈（建议 NVMe）


GPU_CLK_DROP_RATIO  = 0.20          # GPU 核心频率相对基线暴跌 > 20% → 撞墙降频（v4.1：0.15 → 0.20）


# ---- v4.1 判定算法/阈值修正（改进建议书 二 / 三 / 五）-------------------------
# 【二】桌面误报「爆显存」：物理专用显存必须真的接近满载，且非本地显存
#       绝对值与增量**双超标**，才允许判定爆显存。
VRAM_SPILL_MIN_RATIO       = 0.88    # 专用显存占用率 ≥ 88% 才允许判定爆显存


VRAM_SPILL_MIN_MB          = 256.0   # 非本地显存绝对值门槛 (MB)


VRAM_SPILL_DELTA_MB        = 64.0    # 非本地显存较基线暴增门槛 (MB)


# 【三】显卡待机低频波动（400MHz→300MHz）误报降频：必须 3D 高频门控
GPU_DOWNCLOCK_MIN_BASE_MHZ = 1200.0  # 基线核心频率 < 此值 → 省电待机，跳过降频判定


GPU_DOWNCLOCK_MIN_3D_LOAD  = 60.0    # 3D 引擎负载 < 此值 → 正常节能，跳过降频判定


GC_LOW_UTIL         = 20.0          # GPU/CPU 忙时比同时 < 20% 视为「都没干活」


GC_SPIKE_ABS_MS     = 50.0          # GC/锁死判定的帧耗时绝对下限（ms）


GC_SPIKE_RATIO      = 3.0           # 或 ≥ 基线 × 3


NET_STALL_RX_KB     = 20.0          # 基线接收速率高于此值( KB/s )才做「归零」判定


NET_STALL_RATIO     = 0.25          # 接收速率跌到基线 25% 以下 → 疑似网络同步卡顿


PROC_EVENT_WHITELIST = {"amdrsserv.exe", "amdrsservice.exe"}



# 【评审】"伪游戏" 进程黑名单：桌面合成器 / 浏览器 / 驱动 / 播放器等
# 也会持续产帧，若不排除会篡夺"主游戏"地位并污染基线。
NON_GAME_PROCESSES = {
    "dwm.exe", "explorer.exe", "chrome.exe", "msedge.exe", "firefox.exe",
    "msedgewebview2.exe", "wallpaper32.exe", "steamwebhelper.exe", "discord.exe",
    "searchhost.exe", "textinputhost.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "amdow.exe", "nvcontainer.exe", "rtss.exe",
    "obs64.exe", "wechat.exe", "tim.exe", "vlc.exe", "potplayer.exe",
}



# PresentMon 会话名前缀（用于清理本工具的历史残留会话）
PM_SESSION_PREFIX = "FrameLab_PM_"


PM_BUFFER_SIZE    = 4096            # 仅缓解丢包，非 2 的幂会被 PresentMon 拒绝



# 基线追踪参数（模块级默认值，可被 framelab_config.json 覆盖）
BASELINE_WINDOW          = 240       # 基线采样窗口帧数


BASELINE_EMA             = 0.25      # 基线 EMA 系数


BASELINE_SPIKE_RATIO     = 2.2       # 尖峰判定倍数（相对中位数）


BASELINE_MAX_SPIKE_FRAC  = 0.35      # 尖峰占比超过此值则冻结基线


_RE_PID     = re.compile(r"pid_(\d+)")


_RE_LUID    = re.compile(r"luid_0x([0-9A-Fa-f]+)_0x([0-9A-Fa-f]+)")


_RE_ENGTYPE = re.compile(r"engtype_([A-Za-z0-9_]+)")




# ==============================================================================
# §9.5  实战 UX 增强层
# ------------------------------------------------------------------------------
# 本节是**独立新增**的功能区，与 §1~§9 的既有逻辑解耦：
#   §9.5.1  阈值参数外部化（framelab_config.json）
#   §9.5.2  全局热键「打点标记」（Windows RegisterHotKey + 原生事件过滤器）
#   §9.5.3  管理员权限检测 / 温和提权入口
#   §9.5.4  PresentMon 缺失获取指引 + 手动路径配置
#   §9.5.5  数据源标记（real / simulated / offline-import）与模拟水印
#   §9.5.6  CSV 后台读取（不冻结界面）
# ==============================================================================

# ---- 数据源三态 -------------------------------------------------------------
DATA_SOURCE_REAL    = "real"            # 真实采集（PresentMon 在线）


DATA_SOURCE_SIM     = "simulated"       # 模拟帧流（PresentMon 缺失/失败降级）


DATA_SOURCE_OFFLINE = "offline-import"  # 离线 CSV 回放


DATA_SOURCE_LABEL = {
    DATA_SOURCE_REAL:    "● 真实采集 (PresentMon)",
    DATA_SOURCE_SIM:    "● 模拟数据 · 非真实采集",
    DATA_SOURCE_OFFLINE: "● 离线导入 CSV",
}


DATA_SOURCE_COLOR = {
    DATA_SOURCE_REAL:    GREEN,
    DATA_SOURCE_SIM:    BAD,
    DATA_SOURCE_OFFLINE: ACCENT,
}


SIM_WATERMARK_TEXT = "SIMULATED DATA · 非真实采集"




# ==============================================================================
# §9.5.4  PresentMon 缺失获取指引
# ==============================================================================
PRESENTMON_GUIDE = (
    "未找到 PresentMon.exe —— 无法采集真实帧数据（当前为模拟模式）。\n\n"
    "【怎么拿】\n"
    "  (1) 官方发行版：Intel/GameTechDev PresentMon 的 GitHub Releases 页面，\n"
    "      下载 PresentMon-<版本>-x64.exe（Windows 免安装单文件）；\n"
    "  (2) 或者直接用 CapFrameX 自带的版本（CapFrameX 安装目录下的 PresentMon 文件夹）。\n\n"
    "【放哪里】\n"
    "  把 exe 放到本程序所在目录：\n"
    "    %s\n"
    "  程序会优先在同目录、PresentMon 子目录、PATH 里查找。\n\n"
    "【命名要求】\n"
    "  - 必须是 .exe，且名字为 PresentMon.exe（大小写不敏感）；\n"
    "  - 或用设置页的「PresentMon 路径」手动指定到任意位置的 PresentMon.exe。\n\n"
    "【拿不到也能用】\n"
    "  - 不想装 PresentMon：直接点「导入 CSV 回放」，用 CapFrameX / PresentMon\n"
    "    导出的 CSV 做离线分析（导出 CSV 里会带 DataSource 列标明来源）。"
)


# ==============================================================================
# 阈值热更新注册表（拆分模块后新增）
# ------------------------------------------------------------------------------
# 单体时代 Config.apply_to_globals() 直接改这一个模块的 globals，全体生效。
# 拆分后 `from .consts import SPILL_THRESHOLD` 只复制当时的值，
# 因此每个"用到阈值"的模块导入时调用 bind_globals(globals())：
#   · 把当前阈值写入自己的模块级名字；
#   · 登记自己，Config.apply_to_globals() 时一起刷新。
# ==============================================================================

LIVE_CONST_NAMES = ("SPILL_THRESHOLD", "FREQ_DROP_RATIO", "CACHE_BURST_FILES",
                    "CPU0_JAM_THRESHOLD", "CPU0_IDLE_OTHERS", "MIGRATE_FREQ_GAP",
                    "PM_BUFFER_SIZE", "BASELINE_WINDOW", "BASELINE_EMA",
                    "BASELINE_SPIKE_RATIO", "BASELINE_MAX_SPIKE_FRAC", "MAX_HW_AGE",
                    # ---- v4.1 判定算法阈值（必须登记，否则热更新广播不到模块）----
                    "GPU_CLK_DROP_RATIO", "VRAM_SPILL_MIN_RATIO", "VRAM_SPILL_MIN_MB",
                    "VRAM_SPILL_DELTA_MB", "GPU_DOWNCLOCK_MIN_BASE_MHZ",
                    "GPU_DOWNCLOCK_MIN_3D_LOAD")

_BOUND_GLOBALS = [globals()]


def bind_globals(g):
    """把当前阈值副本注入模块 g，并登记 g 以便配置热更新时刷新。"""
    for name in LIVE_CONST_NAMES:
        if name in globals():
            g[name] = globals()[name]
    if g not in _BOUND_GLOBALS:
        _BOUND_GLOBALS.append(g)
    return g


def bound_globals():
    return [g for g in _BOUND_GLOBALS]


def apply_values(values):
    """
    把已校验/已归一的阈值写回所有已登记模块（含本模块）。

    values: {全局变量名: 值}，例如 {"SPILL_THRESHOLD": 67108864, ...}
    """
    n = 0
    for g in _BOUND_GLOBALS:
        for name, val in values.items():
            if name in g:
                g[name] = val
                n += 1
    return n


def datasource_color(src):
    """
    数据源标识色 —— **实时**读当前主题。

    （原实现的 DATA_SOURCE_COLOR 字典在主题切换后不会更新，属历史遗留；
    这里同时保留字典以兼容旧调用，新代码请用本函数。）
    """
    from . import theme as _t
    return {
        DATA_SOURCE_REAL:    _t.GREEN,
        DATA_SOURCE_SIM:     _t.BAD,
        DATA_SOURCE_OFFLINE: _t.ACCENT,
    }.get(src, _t.DIM)
