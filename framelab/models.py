# -*- coding: utf-8 -*-

"""数据模型：Event / HardwareSnapshot / FrameRecord / DiffRow。"""

import time



# ==============================================================================
# §3  数据模型
# ==============================================================================
class Event:
    """掉帧诱因事件"""
    # frame: 反向引用绑定到的 FrameRecord（add_event 时写入）。
    # 全局概览模式抽稀渲染时用它保住「带打点的帧」，避免事件标记被抽稀吞掉。
    __slots__ = ("ts", "kind", "text", "advice", "severity", "symbol", "frame")

    def __init__(self, ts, kind, text, advice="", severity="WARN", symbol="o"):
        self.ts = ts
        self.kind = kind          # 降频/溢出/内存/着色器/CPU0拥堵/调度迁移
        self.text = text
        self.advice = advice
        self.severity = severity  # INFO / WARN / DANGER
        self.symbol = symbol
        self.frame = None


class HardwareSnapshot:
    """一轮硬件探针快照。seq 单调递增，用于与帧做时间对齐。"""
    __slots__ = (
        "seq", "ts", "adapters", "procs", "cpu_usage", "cpu_freq", "cpu_total",
        "cpu0_usage", "dominant_core", "core_variance",
        "mem_avail_mb", "mem_commit", "mem_limit", "page_in", "page_out",
        "pagein_top", "gpu_eng", "cache", "cache_new_files", "cache_new_mb",
        "vram_dedicated", "vram_total", "vram_nonlocal", "vram_local",
        # ---- v3.2 四大新探针字段 ----
        "io_read_mb", "io_other_mb", "io_write_mb", "io_top", "io_by_name",
        "net_rx_mb", "net_tx_mb", "net_adapters",
        "gpu_clock", "gpu_clock_max", "gpu_temp",
        # ---- v1.0.0：CPU 封装功率（W；None = 不可用）+ 是否估算值 ----
        "cpu_power", "cpu_power_est"
    )

    def __init__(self, seq=0):
        self.seq = seq
        self.ts = time.time()
        self.adapters = {}
        self.procs = []
        self.cpu_usage = []
        self.cpu_freq = []
        self.cpu_total = 0.0
        self.cpu0_usage = 0.0
        self.dominant_core = 0
        self.core_variance = 0.0
        self.mem_avail_mb = 0.0
        self.mem_commit = 0.0
        self.mem_limit = 0.0
        self.page_in = 0.0
        self.page_out = 0.0
        self.pagein_top = []
        self.gpu_eng = {}
        self.cache = {}
        self.cache_new_files = 0
        self.cache_new_mb = 0.0
        self.vram_dedicated = 0.0
        # 0 表示「容量未知」—— 不能写死 8GB 或 1 字节，
        # 否则显示层会算出荒谬的占用率。
        self.vram_total = 0.0
        self.vram_nonlocal = 0.0
        self.vram_local = 0.0
        # ---- v3.2 新探针 ----
        # 进程磁盘 I/O（MB/s）：io_read_mb 取「当前读盘最凶的进程」，
        # io_by_name 保留 Top-K 进程的 {进程名小写: (read_mb, other_mb)}，
        # 供归因时按 PresentMon 的 Application 名精确回查。
        self.io_read_mb = 0.0
        self.io_other_mb = 0.0
        self.io_write_mb = 0.0
        self.io_top = []          # [(进程名, read MB/s), ...]
        self.io_by_name = {}      # {name_lower: (read_mb, other_mb, write_mb)}
        # 网络吞吐（MB/s，已剔除回环/虚拟网卡）
        self.net_rx_mb = 0.0
        self.net_tx_mb = 0.0
        self.net_adapters = {}    # {网卡名: (rx_mb, tx_mb)}
        # GPU 核心频率（MHz，None = 不可用）与本次会话观察到的峰值
        self.gpu_clock = None
        self.gpu_clock_max = 0
        self.gpu_temp = None
        # CPU 封装功率（W）。None = 本机既无功率计也无法估算。
        # 【铁律】cpu_power_est=True 时界面必须显示成「≈62 W」，
        # 绝不把估算值当实测卖给用户。
        self.cpu_power = None
        self.cpu_power_est = True


class FrameRecord:
    """一帧。hw_seq 指向绑定的那一轮硬件快照（时间上最接近且不晚于本帧）。"""
    __slots__ = ("idx", "t", "wall_ts", "app", "pid", "frametime", "fps",
                 "gpu_busy", "cpu_busy", "gpu_busy_ratio", "cpu_busy_ratio",
                 "hw_seq", "events")

    def __init__(self, idx, t, wall_ts, app, pid, frametime,
                 gpu_busy=0.0, cpu_busy=0.0, hw_seq=-1):
        self.idx = idx
        self.t = t
        self.wall_ts = wall_ts
        self.app = app
        self.pid = pid
        self.frametime = max(0.1, float(frametime))
        self.fps = 1000.0 / self.frametime
        self.gpu_busy = max(0.0, float(gpu_busy))
        self.cpu_busy = max(0.0, float(cpu_busy))
        self.gpu_busy_ratio = (self.gpu_busy / self.frametime) if self.frametime > 0 else 0.0
        self.cpu_busy_ratio = (self.cpu_busy / self.frametime) if self.frametime > 0 else 0.0
        self.hw_seq = hw_seq
        self.events = []


class DiffRow:
    """病灶对比诊断卡的一行"""
    __slots__ = ("dim", "cur", "base", "delta", "verdict", "severity")

    def __init__(self, dim, cur, base, delta, verdict, severity="NORMAL"):
        self.dim = dim
        self.cur = cur
        self.base = base
        self.delta = delta
        self.verdict = verdict
        self.severity = severity
