# -*- coding: utf-8 -*-

"""归因分析：BaselineTracker / FrameStore / SnapshotDiffInspector。"""

import bisect
from collections import deque
import numpy as np
from .consts import GC_LOW_UTIL, GC_SPIKE_ABS_MS, GC_SPIKE_RATIO, IO_STREAM_DANGER_MB, IO_STREAM_WARN_MB, NET_STALL_RATIO, NET_STALL_RX_KB, NON_GAME_PROCESSES, PROC_EVENT_WHITELIST
from .models import DiffRow, FrameRecord
from .stats import gpu_3d_load_pct
from .utils import fmt_delta, human_size

# ---- 主题/阈值热更新绑定（本模块持有可变副本）----
# 【工程约定】GPU_CLK_DROP_RATIO / VRAM_SPILL_* / GPU_DOWNCLOCK_* 已登记进
# consts.LIVE_CONST_NAMES，一律靠 bind_globals 绑定，**禁止** from ..consts import
# （导入即冻结，配置热更新会在本模块静默失效）。
from .consts import bind_globals as _bind_const_globals
_bind_const_globals(globals())


def _clamp_pct(v):
    """百分比显示统一夹紧到 0~100（杜绝 1008.40% 这类脏数据）。"""
    try:
        v = float(v)
    except Exception:
        return 0.0
    if not np.isfinite(v):
        return 0.0
    return max(0.0, min(100.0, v))



# ==============================================================================
# §7  FrameStore / BaselineTracker / SnapshotDiffInspector
# ==============================================================================
class BaselineTracker:
    """
    【平稳基线 (Baseline)】追踪器 —— 中位数法 + 尖峰剔除 + 冻结门控。

    设计依据（实测结论）：
    · **中位数比均值/P95 截断都更鲁棒**：尖峰帧占比达到 30% 时，
      中位数基线仍保持稳定（实测恒定），而「均值 + P95 剔除」会被顶穿。
    · **剔除尖峰用「相对中位数的倍数」而不是百分位**：百分位阈值在
      尖峰占比 ≥5% 时同样会失效。
    · **连续卡顿簇不能污染基线**：因此基线只从「平稳窗口」采样，
      并且当窗口内尖峰占比过高时**冻结**基线（不更新）。
    · 硬件侧基线取「平稳帧所绑定的、去重后的快照序列」的中位数，
      避免同一快照被重复计权。
    """

    def __init__(self, ema=None, window=None, spike_ratio=None, max_spike_frac=None):
        # 默认值取自模块级常量 —— 这样 stuttertriage_config.json 覆盖后
        # 新建的 BaselineTracker 会自动继承新阈值。
        self.ema = BASELINE_EMA if ema is None else ema
        self.window = BASELINE_WINDOW if window is None else window
        self.spike_ratio = BASELINE_SPIKE_RATIO if spike_ratio is None else spike_ratio
        self.max_spike_frac = (BASELINE_MAX_SPIKE_FRAC if max_spike_frac is None
                               else max_spike_frac)
        # 帧侧基线
        self.base_frametime = 16.67
        self.base_fps = 60.0
        self.base_gpu_busy_ratio = 0.0
        self.base_cpu_busy_ratio = 0.0
        self.base_smooth_frames = 0
        self.frozen = False
        # 硬件侧基线
        self.base_cpu0_usage = 0.0
        self.base_cpu_total = 0.0
        self.base_core_freq = []
        self.base_vram_nonlocal = 0.0
        self.base_vram_dedicated = 0.0
        self.base_vram_total = 0.0
        self.base_page_in = 0.0
        self.base_page_out = 0.0
        self.base_cache_files = 0.0
        self.base_cache_mb = 0.0
        # ---- v3.2 新探针基线 ----
        self.base_gpu_clock = 0.0        # GPU 核心频率基线（MHz），0 = 未知
        self.base_io_read_mb = 0.0       # 主进程磁盘读取基线（MB/s）
        self.base_net_rx_mb = 0.0        # 网卡接收基线（MB/s）
        self._primed = False

    def reset(self):
        self._primed = False
        self.frozen = False
        self.base_core_freq = []

    @staticmethod
    def _median(values, default=0.0):
        if not values:
            return default
        arr = np.asarray(values, dtype=np.float64)
        return float(np.median(arr))

    def update(self, frames, snap_lookup):
        """
        frames: 最近窗口内的帧列表（按时间升序）
        snap_lookup: callable(seq) -> HardwareSnapshot | None
        """
        if len(frames) < 20:
            return
        window = frames[-self.window:]
        fts = np.array([f.frametime for f in window], dtype=np.float64)

        # 1) 中位数作为「平稳中心」，用倍数关系剔除尖峰
        med_ft = float(np.median(fts))
        if med_ft <= 0:
            return
        thr = med_ft * self.spike_ratio
        smooth = [f for f in window if f.frametime <= thr]
        spike_frac = 1.0 - (len(smooth) / len(window))
        self.base_smooth_frames = len(smooth)

        # 2) 尖峰过多 → 冻结基线（当前处于卡顿簇中，不能让基线被带偏）
        if spike_frac > self.max_spike_frac and self._primed:
            self.frozen = True
            return
        self.frozen = False

        if len(smooth) < 10:
            smooth = window

        new_ft = self._median([f.frametime for f in smooth], med_ft)
        new_gpu_ratio = self._median([f.gpu_busy_ratio for f in smooth])
        new_cpu_ratio = self._median([f.cpu_busy_ratio for f in smooth])

        # 硬件侧：按快照 seq 去重后取中位数
        seqs = []
        seen = set()
        for f in smooth:
            if f.hw_seq >= 0 and f.hw_seq not in seen:
                seen.add(f.hw_seq)
                seqs.append(f.hw_seq)
        snaps = []
        for sq in seqs:
            s = snap_lookup(sq)
            if s is not None:
                snaps.append(s)

        new = {
            "cpu0": 0.0,
            "cpu_total": 0.0,
            "vram_nonlocal": 0.0,
            "vram_dedicated": 0.0,
            "vram_total": 0.0,
            "page_in": 0.0,
            "page_out": 0.0,
            "cache_files": 0.0,
            "cache_mb": 0.0,
            # ---- v3.2 新探针 ----
            "gpu_clock": 0.0,
            "io_read": 0.0,
            "net_rx": 0.0,
        }
        new_core_freq = []
        if snaps:
            new["cpu0"] = self._median([s.cpu0_usage for s in snaps])
            new["cpu_total"] = self._median([s.cpu_total for s in snaps])
            new["vram_nonlocal"] = self._median([s.vram_nonlocal for s in snaps])
            new["vram_dedicated"] = self._median([s.vram_dedicated for s in snaps])
            new["vram_total"] = self._median([s.vram_total for s in snaps])
            new["page_in"] = self._median([s.page_in for s in snaps])
            new["page_out"] = self._median([s.page_out for s in snaps])
            new["cache_files"] = self._median([s.cache_new_files for s in snaps])
            new["cache_mb"] = self._median([s.cache_new_mb for s in snaps])
            # 【注意】GPU 频率 / 网络速率要排除「不可用(0/None)」的样本，
            # 否则不支持频率读取的机器会把基线拉到 0，导致永远算不出降频。
            clk_vals = [s.gpu_clock for s in snaps if getattr(s, "gpu_clock", None)]
            new["gpu_clock"] = self._median(clk_vals) if clk_vals else 0.0
            new["io_read"] = self._median([getattr(s, "io_read_mb", 0.0) for s in snaps])
            new["net_rx"] = self._median([getattr(s, "net_rx_mb", 0.0) for s in snaps])
            ncore = max(len(s.cpu_freq) for s in snaps)
            core = []
            for i in range(ncore):
                vals = [s.cpu_freq[i] for s in snaps if i < len(s.cpu_freq)]
                core.append(self._median(vals))
            new_core_freq = core

        if not self._primed:
            self._primed = True
            self.base_frametime = new_ft
            self.base_gpu_busy_ratio = new_gpu_ratio
            self.base_cpu_busy_ratio = new_cpu_ratio
            self.base_cpu0_usage = new["cpu0"]
            self.base_cpu_total = new["cpu_total"]
            self.base_vram_nonlocal = new["vram_nonlocal"]
            self.base_vram_dedicated = new["vram_dedicated"]
            self.base_vram_total = new["vram_total"]
            self.base_page_in = new["page_in"]
            self.base_page_out = new["page_out"]
            self.base_cache_files = new["cache_files"]
            self.base_cache_mb = new["cache_mb"]
            self.base_gpu_clock = new["gpu_clock"]
            self.base_io_read_mb = new["io_read"]
            self.base_net_rx_mb = new["net_rx"]
            self.base_core_freq = new_core_freq
        else:
            a = self.ema
            self.base_frametime = a * self.base_frametime + (1 - a) * new_ft
            self.base_gpu_busy_ratio = a * self.base_gpu_busy_ratio + (1 - a) * new_gpu_ratio
            self.base_cpu_busy_ratio = a * self.base_cpu_busy_ratio + (1 - a) * new_cpu_ratio
            self.base_cpu0_usage = a * self.base_cpu0_usage + (1 - a) * new["cpu0"]
            self.base_cpu_total = a * self.base_cpu_total + (1 - a) * new["cpu_total"]
            self.base_vram_nonlocal = a * self.base_vram_nonlocal + (1 - a) * new["vram_nonlocal"]
            self.base_vram_dedicated = a * self.base_vram_dedicated + (1 - a) * new["vram_dedicated"]
            self.base_vram_total = a * self.base_vram_total + (1 - a) * new["vram_total"]
            self.base_page_in = a * self.base_page_in + (1 - a) * new["page_in"]
            self.base_page_out = a * self.base_page_out + (1 - a) * new["page_out"]
            self.base_cache_files = a * self.base_cache_files + (1 - a) * new["cache_files"]
            self.base_cache_mb = a * self.base_cache_mb + (1 - a) * new["cache_mb"]
            # 频率基线只在拿到有效样本时更新（0 = 本机不支持，保持原值）
            if new["gpu_clock"] > 0:
                if self.base_gpu_clock > 0:
                    self.base_gpu_clock = a * self.base_gpu_clock + (1 - a) * new["gpu_clock"]
                else:
                    self.base_gpu_clock = new["gpu_clock"]
            self.base_io_read_mb = a * self.base_io_read_mb + (1 - a) * new["io_read"]
            self.base_net_rx_mb = a * self.base_net_rx_mb + (1 - a) * new["net_rx"]
            if new_core_freq:
                if len(self.base_core_freq) == len(new_core_freq):
                    self.base_core_freq = [a * o + (1 - a) * n
                                           for o, n in zip(self.base_core_freq, new_core_freq)]
                else:
                    self.base_core_freq = new_core_freq
        self.base_fps = 1000.0 / self.base_frametime if self.base_frametime > 0 else 0.0


class FrameStore:
    """
    统一时序数据库（**仅由 UI 线程写入**，因此无需加锁）。

    · frames: 帧记录 deque
    · snapshots: 硬件快照（按 seq 排序），供帧做时间对齐
    · events: 掉帧诱因事件
    · cache_timeline: (ts, new_files, new_mb) —— 用于「该时刻前后 1 秒」着色器统计

    【性能】所有派生索引都在写入路径上**增量维护**，UI 线程读取时零全量扫描：
    · _by_app : app(小写) -> deque(帧)      锁定目标进程后直接取分片
    · _f_ts/_f_refs : 帧 wall_ts 有序索引    add_event 用 bisect O(log n) 定位最近帧
    · _ct_ts/_ct_files/_ct_mb : 着色器时间轴  shader_activity 用 bisect 定位 ±window
    · _metric_cache : 指标卡结果缓存         帧集合未变时直接复用
    """

    def __init__(self, max_frames=100000, max_snaps=20000):
        """
        【容量】max_frames 20000 → 100000 / max_snaps 4000 → 20000：
        20000 帧在 144 FPS 下只有 ~2.3 分钟，整局 15 分钟以上的对账
        会被环形队列过早覆盖，表现为「曲线断层」且旧尖峰无法点击回看。
        100000 帧 @ 144FPS ≈ 11.6 分钟，@ 60FPS ≈ 27.8 分钟，
        配合全局概览模式足够覆盖整局；内存开销约 +30MB（FrameRecord ~200B）。
        max_snaps 20000 @ 0.4s 采样 ≈ 2.2 小时硬件快照，与帧窗口对齐。
        """
        self.frames = deque(maxlen=max_frames)
        self.snapshots = deque(maxlen=max_snaps)
        self.events = deque(maxlen=600)
        self.cache_timeline = deque(maxlen=2000)
        self._snap_by_seq = {}
        self._snap_ts = []
        self._snap_seq = []
        self.baseline = BaselineTracker()
        self._stats_tick = 0
        # ---- 派生索引（增量维护，UI 线程零扫描）----
        self._by_app = {}          # app_key(小写) -> deque(该 app 的帧)
        self._f_ts = []            # 帧 wall_ts（升序）→ bisect 定位最近帧
        self._f_refs = []          # 与 _f_ts 平行的帧引用
        self._f_start = 0          # _f_ts/_f_refs 有效区起始下标（避免 pop(0) 的 O(n) 搬移）
        self._idx_n = 0            # 索引覆盖的帧数（与 len(self.frames) 不一致时重建）
        # ---- 着色器时间轴（bisect 定位 ±window 区间）----
        self._ct_ts = []
        self._ct_files = []
        self._ct_mb = []
        self._ct_start = 0         # 有效区起始下标（避免 list 前插删除的 O(n) 搬移）
        self._ct_sorted = True
        # ---- 指标缓存：(帧数, 末帧对象, 结果)----
        self._metric_cache = None
        # ---- 最近 1200 帧的 app 滚动计数（dominant_app 用，避免每 tick 扫 1200 帧）----
        self._app_win = deque()
        self._app_cnt = {}
        # ---- 会话级归因统计缓存：(帧数, 末帧对象, 基线, 结果)----
        self._corr_cache = None
        # ---- 帧时间 numpy 镜像（环形）：count_spikes 用，避免 Python 级遍历 20000 帧 ----
        self._ft_np = np.empty(max_frames, dtype=np.float64)
        self._ft_head = 0        # 环内最旧元素位置
        self._ft_n = 0           # 有效元素数（= len(self.frames)）

    def reset(self):
        """
        【清空重采】清空整段会话数据，回到「刚打开」的干净状态。

        帧 / 硬件快照 / 事件 / 着色器时间轴 / 基线 / 全部派生索引与缓存
        一次性归零；容量（maxlen）沿用当前值，避免清空后反而装得更少。
        UI 线程是唯一写者，所以可以直接重新初始化（无需加锁）。
        """
        max_frames = self.frames.maxlen or 100000
        max_snaps = self.snapshots.maxlen or 20000
        self.__init__(max_frames=max_frames, max_snaps=max_snaps)

    # ---------------- 写入 ----------------
    def add_snapshot(self, snap):
        self.snapshots.append(snap)
        self._snap_by_seq[snap.seq] = snap
        self._snap_ts.append(snap.ts)
        self._snap_seq.append(snap.seq)
        # 与 snapshots 同步修剪（deque 有 maxlen，索引表必须同步裁剪）
        while len(self._snap_ts) > len(self.snapshots):
            old_seq = self._snap_seq.pop(0)
            old_ts = self._snap_ts.pop(0)
            self._snap_by_seq.pop(old_seq, None)
        self.cache_timeline.append((snap.ts, snap.cache_new_files, snap.cache_new_mb))
        # 着色器时间轴（与 cache_timeline 同寿命，供 bisect 二分定位）
        if self._ct_ts and snap.ts < self._ct_ts[-1]:
            self._ct_sorted = False          # 乱序 → 退回线性扫描
        self._ct_ts.append(snap.ts)
        self._ct_files.append(snap.cache_new_files)
        self._ct_mb.append(snap.cache_new_mb)
        if len(self._ct_ts) - self._ct_start > 2000:
            self._ct_start += 1
            if self._ct_start >= 1024:
                del self._ct_ts[:self._ct_start]
                del self._ct_files[:self._ct_start]
                del self._ct_mb[:self._ct_start]
                self._ct_start = 0

    def add_event(self, ev):
        self.events.append(ev)
        # 绑定到时间上最接近的帧（而不是简单的最后一帧）
        if not self.frames:
            return
        self._ensure_index()
        # 【性能】二分查找定位最近帧 —— 原先是 reversed(self.frames) 逐帧比较（O(n)）
        best, bestd = self._nearest_frame(ev.ts)
        if best is not None and bestd is not None and bestd < 1.5:
            best.events.append(ev)
            try:
                ev.frame = best       # 反向引用：概览抽稀时保住打点帧
            except Exception:
                pass

    def add_frame(self, rec):
        # 时间对齐：绑定「不晚于本帧、且最接近」的那一轮硬件快照
        rec.hw_seq = self._bind_snapshot(rec.wall_ts)
        # 【健壮性】追加前先保证派生索引与 frames 对齐 ——
        # 离线导入（_import_csv）会直接 clear()/append() store.frames，
        # 若此时来了一帧实时帧，索引会追加到陈旧状态上而彻底错乱。
        self._ensure_index()
        frames = self.frames
        dropped = None
        if frames.maxlen is not None and len(frames) >= frames.maxlen:
            dropped = frames[0]          # deque 即将挤出的最旧帧
        frames.append(rec)
        # ---- 派生索引增量维护（O(1)）----
        self._f_ts.append(rec.wall_ts)
        self._f_refs.append(rec)
        if dropped is not None:
            # 用「起始下标」代替 pop(0)，避免每帧 O(n) 搬移
            self._f_start += 1
            if self._f_start >= 1024:
                del self._f_ts[:self._f_start]
                del self._f_refs[:self._f_start]
                self._f_start = 0
            key = self._app_key(dropped)
            shard = self._by_app.get(key)
            if shard is not None:
                if shard and shard[0] is dropped:
                    shard.popleft()
                else:
                    try:
                        shard.remove(dropped)
                    except ValueError:
                        pass
                if not shard:
                    self._by_app.pop(key, None)
        key = self._app_key(rec)
        shard = self._by_app.get(key)
        if shard is None:
            shard = deque()
            self._by_app[key] = shard
        shard.append(rec)
        # 帧时间 numpy 镜像（与 deque 同步）
        if dropped is not None:
            self._ft_n -= 1
            self._ft_head = (self._ft_head + 1) % self._ft_np.size
        if self._ft_n < self._ft_np.size:
            self._ft_np[(self._ft_head + self._ft_n) % self._ft_np.size] = rec.frametime
            self._ft_n += 1
        else:
            self._ft_np[self._ft_head] = rec.frametime
            self._ft_head = (self._ft_head + 1) % self._ft_np.size
        self._idx_n = len(frames)
        # 滚动 app 计数窗口（1200 帧）
        win = self._app_win
        cnt = self._app_cnt
        app = rec.app or ""
        win.append(app)
        cnt[app] = cnt.get(app, 0) + 1
        if len(win) > 1200:
            old = win.popleft()
            c = cnt.get(old, 0) - 1
            if c <= 0:
                cnt.pop(old, None)
            else:
                cnt[old] = c

    @staticmethod
    def _app_key(rec):
        return (rec.app or "").lower()

    def _ensure_index(self):
        """
        派生索引与 frames 的一致性自检。

        离线导入（_import_csv）等场景会直接操作 store.frames，
        此时索引会失配 —— 这里一次性重建（O(n)，仅失配时发生）。
        """
        frames = self.frames
        n = len(frames)
        if self._idx_n == n:
            if n == 0:
                return
            # 末帧对象一致 → 索引与 frames 同步（离线导入会换掉全部帧对象，
            # 因此这个身份校验足以发现「直接操作 store.frames」造成的失配）
            if self._f_refs and self._f_refs[-1] is frames[-1]:
                return
        self._rebuild_index()

    def _rebuild_index(self):
        self._f_ts = []
        self._f_refs = []
        self._f_start = 0
        self._by_app = {}
        self._app_win = deque()
        self._app_cnt = {}
        # 帧时间 numpy 镜像同步重建
        cap = self._ft_np.size
        self._ft_head = 0
        self._ft_n = 0
        for i, f in enumerate(self.frames):
            if i >= cap:
                break
            self._ft_np[i] = f.frametime
            self._ft_n += 1
        for f in self.frames:
            self._f_ts.append(f.wall_ts)
            self._f_refs.append(f)
            key = self._app_key(f)
            shard = self._by_app.get(key)
            if shard is None:
                shard = deque()
                self._by_app[key] = shard
            shard.append(f)
            app = f.app or ""
            self._app_win.append(app)
            self._app_cnt[app] = self._app_cnt.get(app, 0) + 1
            if len(self._app_win) > 1200:
                old = self._app_win.popleft()
                c = self._app_cnt.get(old, 0) - 1
                if c <= 0:
                    self._app_cnt.pop(old, None)
                else:
                    self._app_cnt[old] = c
        self._idx_n = len(self.frames)

    def _nearest_frame(self, ts):
        """用二分查找定位「时间上最接近」的帧 —— O(log n)，返回 (帧, |Δt|)"""
        lo = self._f_start
        hi = len(self._f_ts)
        if lo >= hi:
            return None, None
        i = bisect.bisect_left(self._f_ts, ts, lo, hi)
        best_i = -1
        best_d = None
        # 二分落点两侧各看 2 个，容忍轻微乱序
        for j in range(max(lo, i - 2), min(hi, i + 2)):
            d = abs(self._f_ts[j] - ts)
            if best_d is None or d < best_d:
                best_i = j
                best_d = d
        if best_i < 0:
            return None, None
        return self._f_refs[best_i], best_d

    def _bind_snapshot(self, wall_ts):
        """
        用二分查找在 _snap_ts 上定位「不晚于本帧」的最靠近快照。
        _snap_ts 与 _snap_seq / snapshots 严格同步，故索引可直接使用。
        """
        if not self._snap_ts:
            return -1
        i = bisect.bisect_right(self._snap_ts, wall_ts) - 1
        if i < 0:
            i = 0
        if i >= len(self._snap_seq):
            i = len(self._snap_seq) - 1
        return self._snap_seq[i]

    def snapshot_for(self, seq):
        return self._snap_by_seq.get(seq)

    # ---------------- 读取 ----------------
    def visible_frames(self, target=None):
        """
        按目标进程过滤后的帧（target=None 表示全部）。

        【性能】按进程分片索引：锁定目标后直接取该分片，
        避免每 tick 对 20000 帧做 Python 级列表推导（实测省 ~5ms/tick）。
        """
        self._ensure_index()
        if not target or target in ("all", "自动锁定主游戏"):
            return list(self.frames)
        shard = self._by_app.get(target.lower())
        if not shard:
            return []
        return list(shard)

    def dominant_app(self):
        """
        自动锁定主游戏：近期（1200 帧）帧数最多的进程。

        【评审】必须排除「伪游戏」进程（dwm.exe / msedge.exe / 显卡驱动 / 播放器等）
        —— 它们同样持续产帧，不排除就会篡夺主游戏地位并污染基线。
        <unknown> 不参与竞争。

        【性能】用增量滚动计数（_app_cnt），查询 O(应用数)；
        原实现每 tick 要遍历 1200 帧并重建字典。
        """
        self._ensure_index()
        cnt = self._app_cnt
        if not cnt:
            return None
        best = None
        best_n = 0
        for app, c in cnt.items():
            if not app or app == "<unknown>":
                continue
            if app.lower() in NON_GAME_PROCESSES:
                continue
            if c > best_n:
                best = app
                best_n = c
        return best

    METRIC_WINDOW = 1000          # 指标窗口（帧）；≥1000 才能给出真实的 P99.9

    def summary_metrics(self, frames=None):
        """
        分位指标卡片数据（CapFrameX 口径）。

        【性能】带缓存：帧集合未变化时直接复用上次结果，
        绝不每 tick 都重建 numpy 数组（UI 侧还会进一步降频调用）。
        """
        if frames is None:
            frames = list(self.frames)
        n = len(frames)
        cache = self._metric_cache
        if cache is not None and cache[0] == n and (n == 0 or cache[1] is frames[-1]):
            return cache[2]
        if n < 5:
            m = {"avg_fps": 0.0, "low1_fps": 0.0, "low01_fps": 0.0,
                 "smooth_pct": 100.0, "stutter_pct": 0.0, "severe_pct": 0.0,
                 "gpu_busy_pct": 0.0, "total_frames": n, "low01_degraded": True}
            self._metric_cache = (n, frames[-1] if n else None, m)
            return m
        recent = frames[-self.METRIC_WINDOW:]
        fts = np.fromiter((f.frametime for f in recent), dtype=np.float64, count=len(recent))
        avg_ft = float(np.mean(fts))
        avg_fps = 1000.0 / avg_ft if avg_ft > 0 else 0.0
        p99 = float(np.percentile(fts, 99))
        # 【红线】0.1% Low 需要足够样本：样本不足 1000 时 P99.9 会退化到极值，
        # 此时退化为 P99（并由 UI 标注），避免给出误导性的极端值。
        degraded = len(fts) < 1000
        p999 = p99 if degraded else float(np.percentile(fts, 99.9))
        low1 = 1000.0 / p99 if p99 > 0 else 0.0
        low01 = 1000.0 / p999 if p999 > 0 else 0.0
        # CapFrameX 风格三色划分
        smooth_cnt = int(np.sum(fts < avg_ft * 2.5))
        stutter_cnt = int(np.sum((fts >= avg_ft * 2.5) & (fts < avg_ft * 4.0)))
        severe_cnt = int(np.sum(fts >= avg_ft * 4.0))
        tot = len(fts)
        ratios = np.fromiter((f.gpu_busy_ratio for f in recent), dtype=np.float64, count=len(recent))
        m = {
            "avg_fps": avg_fps,
            "low1_fps": low1,
            "low01_fps": low01,
            "smooth_pct": smooth_cnt / tot * 100.0,
            "stutter_pct": stutter_cnt / tot * 100.0,
            "severe_pct": severe_cnt / tot * 100.0,
            "gpu_busy_pct": float(np.mean(ratios) * 100.0) if len(ratios) else 0.0,
            "total_frames": n,
            "low01_degraded": degraded,
        }
        self._metric_cache = (n, frames[-1], m)
        return m

    def shader_activity(self, ts, window=1.0):
        """
        该时刻前后 window 秒内的着色器新增文件数与写入量。

        【性能】原先每 tick 线性遍历全部 2000 条 cache_timeline；
        现在用有序时间轴二分定位区间（窗口内通常只有几条）。
        """
        if not self._ct_sorted or not self._ct_ts:
            # 兜底：时间轴乱序或为空 → 退回线性扫描
            files = 0
            mb = 0.0
            for (t, f, m) in self.cache_timeline:
                if abs(t - ts) <= window:
                    files += f
                    mb += m
            return files, mb
        lo = bisect.bisect_left(self._ct_ts, ts - window, self._ct_start)
        hi = bisect.bisect_right(self._ct_ts, ts + window, self._ct_start)
        if hi <= lo:
            return 0, 0.0
        return sum(self._ct_files[lo:hi]), sum(self._ct_mb[lo:hi])

    def update_baseline(self, frames):
        self.baseline.update(frames, self.snapshot_for)
        # 基线变了 → 会话级归因的判定阈值也变了 → 缓存失效
        self._corr_cache = None

    def count_spikes(self, threshold):
        """
        统计「帧耗时 ≥ threshold」的帧数。

        等价于 sum(1 for f in self.frames if f.frametime >= threshold)，
        但走 numpy 向量化比较（C 层循环），20000 帧约 20µs 而非 1.8ms。
        """
        self._ensure_index()
        n = self._ft_n
        if n <= 0:
            return 0
        arr = self._ft_np
        cap = arr.size
        head = self._ft_head
        end = head + n
        if end <= cap:
            return int(np.count_nonzero(arr[head:end] >= threshold))
        return (int(np.count_nonzero(arr[head:] >= threshold))
                + int(np.count_nonzero(arr[:end - cap] >= threshold)))

    def session_correlations(self, frames=None):
        """
        【会话级归因统计】—— 比单帧归因更可信。

        【性能】带缓存：帧集合与基线都没变时直接复用上次结果。
        原实现每 tick 要对「所有卡顿帧 × 5 个判定条件」全量重算
        （实测 2.5ms/tick，是 UI 线程第一大热点）。

        【评审·Claude 建议 11】0.4s 的硬件采样粒度根本对不上几十毫秒的卡顿，
        单帧"元凶判定"本质上只是相关性推测，措辞不该那么肯定。
        但会话级统计能给出真实置信度：「本次会话 37 次卡顿里，29 次与着色器
        爆发时间重合（78%）」—— 这是可验证的证据强度。

        返回：每个诱因的 {卡顿次数, 重合次数, 命中率, 置信度描述}
        """
        frames = frames if frames is not None else list(self.frames)
        if len(frames) < 30:
            return {}
        base_ft = self.baseline.base_frametime
        if base_ft <= 0:
            return {}
        # 缓存命中条件：帧集合未变（数量 + 末帧对象）且基线未变
        cache = self._corr_cache
        if cache is not None and cache[0] == len(frames) \
                and (cache[1] is frames[-1]) and cache[2] == base_ft:
            return cache[3]

        # 1) 找出所有卡顿帧（帧耗时 ≥ 1.8× 基线）
        spikes = [f for f in frames if f.frametime >= base_ft * 1.8]
        if not spikes:
            return {}
        total = len(spikes)

        # 2) 每个诱因的判定条件（与诊断卡保持一致的口径）
        def is_shader_burst(f):
            n, _mb = self.shader_activity(f.wall_ts, 1.0)
            return n >= CACHE_BURST_FILES

        def is_cpu0_jam(f):
            hw = self.snapshot_for(f.hw_seq)
            if hw is None:
                return False
            if hw.cpu0_usage < CPU0_JAM_THRESHOLD:
                return False
            others = hw.cpu_usage[1:] if len(hw.cpu_usage) > 1 else []
            avg_other = sum(others) / len(others) if others else 0.0
            return avg_other < CPU0_IDLE_OTHERS

        def is_vram_spill(f):
            """
            会话级「显存溢出」相关性判定（必须与诊断卡第 4 行同一套门控）。

            三条件同时成立才算溢出：
              ① 容量已知（vram_total > 0）；
              ② **物理专用显存真的接近满载**：dedicated/total >= VRAM_SPILL_MIN_RATIO(88%)；
              ③ 非本地溢出量确实大：取「Absolute 门槛(256MB)」与「可配置
                 SPILL_THRESHOLD(默认 64MB)」里**较严**的一个 —— 二者都代表
                 "溢出量真的大"，取较严者最能表达「同时接近满载且溢出量真的大」，
                 也避免用户把 SPILL_THRESHOLD 调小后又被 256MB 反超。
                 桌面空载只用了 15%~50% 显存时，①②直接不成立 → 绝不误报。
            """
            hw = self.snapshot_for(f.hw_seq)
            if hw is None or hw.vram_total <= 0:
                return False
            ratio = hw.vram_dedicated / hw.vram_total
            if ratio < VRAM_SPILL_MIN_RATIO:
                return False
            thr_bytes = max(VRAM_SPILL_MIN_MB * 1048576.0, SPILL_THRESHOLD)
            return hw.vram_nonlocal >= thr_bytes

        def is_mem_pressure(f):
            hw = self.snapshot_for(f.hw_seq)
            if hw is None:
                return False
            return hw.page_in > self.baseline.base_page_in + 40.0

        def is_gpu_starved(f):
            if f.gpu_busy <= 0:
                return False
            ratio = f.gpu_busy_ratio * 100.0
            base_ratio = self.baseline.base_gpu_busy_ratio * 100.0
            return (base_ratio - ratio) >= 35.0

        checks = [
            ("着色器编译爆发", is_shader_burst),
            ("CPU0 拥堵", is_cpu0_jam),
            ("显存溢出", is_vram_spill),
            ("内存换入压力", is_mem_pressure),
            ("GPU 饥饿", is_gpu_starved),
        ]
        out = {}
        for name, fn in checks:
            hit = 0
            for f in spikes:
                try:
                    if fn(f):
                        hit += 1
                except Exception:
                    pass
            if hit <= 0:
                continue
            rate = hit / float(total)
            if rate >= 0.6:
                conf = "高"
            elif rate >= 0.35:
                conf = "中"
            elif rate >= 0.15:
                conf = "低"
            else:
                conf = "极低（可能是巧合）"
            out[name] = {"hits": hit, "total": total, "rate": rate, "confidence": conf}
        self._corr_cache = (len(frames), frames[-1], base_ft, out)
        return out


class SnapshotDiffInspector:
    """
    【核心杀手锏】异常瞬间 vs 平稳基线 —— 病灶对比诊断卡生成器。

    列结构：监控维度 | 异常瞬间数值 (T) | 平稳期均值 (Baseline) | 变化幅度 (Delta) | 元凶判定
    """

    @staticmethod
    def inspect(frame: FrameRecord, store: FrameStore):
        base = store.baseline
        hw = store.snapshot_for(frame.hw_seq)
        rows = []

        # 【陈旧快照检测】硬件探针 0.4s 采一次，若绑定的快照已经太旧，
        # 其硬件数据可能与该帧无关 —— 此时应显示"不可判定"而不是给出
        # 一个看似精确的结论（这是评审指出的"过度自信"问题）。
        hw_age = None
        if hw is not None:
            hw_age = frame.wall_ts - hw.ts
        hw_stale = (hw_age is None) or (hw_age > MAX_HW_AGE)

        # ---------------- 1. 帧表现 ----------------
        cur_ft = frame.frametime
        base_ft = base.base_frametime
        ft_delta = (cur_ft - base_ft) / base_ft * 100 if base_ft > 0 else 0.0
        cur_fps = 1000.0 / cur_ft
        base_fps = 1000.0 / base_ft if base_ft > 0 else 0.0
        if cur_ft >= base_ft * 3.0:
            v, s = "🔴 恶性掉帧！画面出现肉眼可见的严重顿挫", "DANGER"
        elif cur_ft >= base_ft * 1.8:
            v, s = "🟠 明显微卡 (Stutter)，已打破操作平滑度", "WARN"
        else:
            v, s = "🟢 处于基线平稳容差范围内", "NORMAL"
        rows.append(DiffRow(
            "帧表现 (帧耗时 / 瞬时 FPS)",
            "%.1f ms (%.0f FPS)" % (cur_ft, cur_fps),
            "%.1f ms (%.0f FPS)" % (base_ft, base_fps),
            fmt_delta(cur_ft, base_ft, "ms"),
            v, s))

        # ---------------- 2. GPU / CPU 忙时比 ----------------
        cur_ratio = _clamp_pct(frame.gpu_busy_ratio * 100.0)
        base_ratio = _clamp_pct(base.base_gpu_busy_ratio * 100.0)
        ratio_delta = cur_ratio - base_ratio
        has_gpu = frame.gpu_busy > 0.0
        # 【相对基线判定】原实现用绝对阈值（>20ms 且 <35%），在 30FPS 锁帧 /
        # 垂直同步 / 限帧场景下会每帧误报；"忙时>92%" 在 GPU-bound 游戏里
        # 是常态却被标 WARN，还会盖住真正原因。
        # 现在统一改为「相对基线下降 X 个百分点」+「帧耗时明显高于基线」。
        starved = (base_ratio - cur_ratio) >= 35.0 and cur_ft >= base_ft * 1.8
        overloaded = (cur_ratio - base_ratio) >= 12.0 and cur_ft >= base_ft * 1.8
        if not has_gpu:
            v, s = ("⚪ 本次采集未拿到 GPU 忙时数据（PresentMon 需默认 metric 模式）"
                    "—— 无法判定显卡饥饿", "NORMAL")
        elif starved:
            v, s = ("🔴 GPU 严重饥饿空转（忙时仅 %.0f%%，较基线 %.0f%% 下降 %.0f pt）！"
                    "CPU 渲染提交线程卡死，未能及时向 GPU 投递 DrawCall"
                    % (cur_ratio, base_ratio, base_ratio - cur_ratio)), "DANGER"
        elif overloaded:
            v, s = ("🟠 显卡满载瓶颈（忙时 %.0f%%），此帧渲染压力过高"
                    "（着色器/光栅负荷满溢）" % cur_ratio), "WARN"
        else:
            v, s = "🟢 负载相对平衡（GPU 忙时 %.0f%%）" % cur_ratio, "NORMAL"
        rows.append(DiffRow(
            "GPU 忙时比 (显卡饥饿判定)",
            "%.1f%% (%.2f ms)" % (cur_ratio, frame.gpu_busy),
            "%.1f%%" % base_ratio,
            "%+.1f pt" % ratio_delta,
            v, s))

        # ---------------- 3. CPU 核心状态 ----------------
        if hw is not None and not hw_stale:
            cur_cpu0 = hw.cpu0_usage
            base_cpu0 = base.base_cpu0_usage
            # 找出相对基线跌得最狠的**有负载**的核心。
            # 【评审】空闲核心本来就会降频（省电策略），不排除空闲核的话
            # 每次都会误报成「VRM 供电墙 / 温度墙」。事件探测里本来就有
            # u>5 的保护，诊断卡里却漏了 —— 现在两处口径一致。
            worst = None
            for c, f in enumerate(hw.cpu_freq):
                b = base.base_core_freq[c] if c < len(base.base_core_freq) else 0.0
                if b <= 200:
                    continue
                usage = hw.cpu_usage[c] if c < len(hw.cpu_usage) else 0.0
                if usage <= 5.0:          # 空闲核降频是正常现象，不算异常
                    continue
                drop = (b - f) / b * 100.0
                if worst is None or drop > worst[3]:
                    worst = (c, f, b, drop)
            avg_freq = float(np.mean(hw.cpu_freq)) if hw.cpu_freq else 0.0
            # 显示用百分比统一 clamp（PDH 偶发脏值不得显示成 108%）
            cpu0_d = _clamp_pct(cur_cpu0)
            base_cpu0_d = _clamp_pct(base_cpu0)
            cpu_tot_d = _clamp_pct(hw.cpu_total)
            if cur_cpu0 >= CPU0_JAM_THRESHOLD:
                v, s = ("🔴 CPU0 满载拥堵 (%.0f%%)！系统中断或游戏主线程卡死在 0 号核，"
                        "建议在 Process Lasso 中取消勾选 CPU0" % cpu0_d), "DANGER"
            elif worst is not None and worst[3] >= 20.0:
                v, s = ("🟠 CPU%d 骤降频至 %.0fMHz（基线 %.0fMHz，-%.0f%%）！"
                        "触发主板 VRM 供电墙或 CPU 温度墙"
                        % (worst[0], worst[1], worst[2], _clamp_pct(worst[3]))), "WARN"
            else:
                v, s = ("🟢 CPU 调度平稳（总体 %.0f%%，CPU0 %.0f%%）"
                        % (cpu_tot_d, cpu0_d)), "NORMAL"
            if worst is not None and worst[3] >= 20.0:
                cur_txt = "CPU0 %.0f%% · CPU%d %.0fMHz" % (cpu0_d, worst[0], worst[1])
                base_txt = "CPU0 %.0f%% · CPU%d %.0fMHz" % (base_cpu0_d, worst[0], worst[2])
                delta_txt = fmt_delta(worst[1], worst[2], "MHz")
            else:
                cur_txt = "CPU0 %.0f%% · 均频 %.0fMHz" % (cpu0_d, avg_freq)
                base_txt = "CPU0 %.0f%%" % base_cpu0_d
                delta_txt = "%+.0f pt" % (cpu0_d - base_cpu0_d)
            rows.append(DiffRow(
                "CPU 核心状态 (降频 / CPU0 拥堵)",
                cur_txt, base_txt, delta_txt, v, s))
        else:
            rows.append(DiffRow(
                "CPU 核心状态 (降频 / CPU0 拥堵)",
                "无该时刻硬件快照" if hw is None else "硬件快照已过期 (%.1fs)" % hw_age,
                "—", "—",
                "⚪ %s，本行不可判定" % ("该帧没有绑定到硬件快照（离线导入数据无历史硬件记录）"
                                         if hw is None else
                                         "硬件探针采样间隔 0.4s，本帧对应的硬件状态已过期，"
                                         "给出结论会有误导性"), "NORMAL"))

        # ---------------- 4. 显存溢出状态 ----------------
        # 【v4.1 修正·桌面误报】必须**同时**满足四个条件才允许 DANGER：
        #   ① 容量已知 vram_total > 0；
        #   ② 物理专用显存占用率 >= 88%（桌面只用了 15%~50% 时，
        #      哪怕 nonlocal 有几百 MB 也只是系统正常共享映射，严禁报爆显存）；
        #   ③ 非本地显存绝对值 >= 256MB；
        #   ④ 非本地显存较平稳基线暴增 >= 64MB。
        # 否则：占用率 >= 94% 只给 WARN「显存占用临界」，其余给 NORMAL「容量充足」。
        if hw is not None and not hw_stale:
            cur_spill = hw.vram_nonlocal / 1048576.0
            base_spill = base.base_vram_nonlocal / 1048576.0
            spill_delta = cur_spill - base_spill
            vram_ratio = (hw.vram_dedicated / hw.vram_total) if hw.vram_total > 0 else 0.0
            vram_pct = _clamp_pct(vram_ratio * 100.0)
            base_ratio = (base.base_vram_dedicated / base.base_vram_total
                          if base.base_vram_total > 0 else 0.0)
            base_pct = _clamp_pct(base_ratio * 100.0)
            # 找出溢出最大的进程
            worst_proc = None
            for p in hw.procs:
                if p["name"].lower() in PROC_EVENT_WHITELIST:
                    continue
                if worst_proc is None or p["nonlocal"] > worst_proc["nonlocal"]:
                    worst_proc = p
            spill_hot = (hw.vram_total > 0
                         and vram_ratio >= VRAM_SPILL_MIN_RATIO
                         and cur_spill >= VRAM_SPILL_MIN_MB
                         and spill_delta >= VRAM_SPILL_DELTA_MB)
            if spill_hot:
                v, s = ("🔴 疑似显存溢出至系统内存：非本地占用 %.0f MB（较基线 +%.0f MB），"
                        "专用显存已用 %.0f%%（≥%.0f%% 接近满载）。"
                        "若同时伴随帧时间飙升，多为贴图/材质超预算，请降低【贴图质量】"
                        % (cur_spill, spill_delta, vram_pct,
                           VRAM_SPILL_MIN_RATIO * 100.0)), "DANGER"
            elif vram_ratio >= 0.94:
                v, s = ("🟠 显存占用临界 (%.0f%%)，即将触顶引发换出"
                        "（非本地 %.0f MB，较基线 +%.0f MB）"
                        % (vram_pct, cur_spill, spill_delta)), "WARN"
            else:
                v, s = ("🟢 显存容量充足（专用显存已用 %.0f%%，非本地 %.0f MB，"
                        "较基线 +%.0f MB）" % (vram_pct, cur_spill, spill_delta)), "NORMAL"
            extra = ""
            if worst_proc is not None and worst_proc["nonlocal"] > SPILL_THRESHOLD:
                extra = "（%s 溢出 %s）" % (worst_proc["name"], human_size(worst_proc["nonlocal"]))
            rows.append(DiffRow(
                "显存溢出 (溢出到系统内存)",
                "非本地 %.0f MB / 专用 %.0f%%%s" % (cur_spill, vram_pct, extra),
                "非本地 %.0f MB / 专用 %.0f%%" % (base_spill, base_pct),
                "%+.0f MB" % spill_delta,
                v, s))
        else:
            rows.append(DiffRow(
                "显存溢出 (溢出到系统内存)",
                "无该时刻硬件快照" if hw is None else "硬件快照已过期 (%.1fs)" % hw_age,
                "—", "—",
                "⚪ %s，本行不可判定" % ("离线数据无历史显存记录"
                                         if hw is None else
                                         "硬件快照已过期，给出结论会有误导性"), "NORMAL"))

        # ---------------- 5. 内存交换与嫌疑进程 ----------------
        if hw is not None and not hw_stale:
            cur_pin = hw.page_in
            base_pin = base.base_page_in
            pin_delta = cur_pin - base_pin
            suspects = hw.pagein_top[:3]
            who = "、".join("%s(%.0fMB/s)" % (n, m) for n, m in suspects) if suspects else "无明显缺页进程"
            # 【相对基线 + 措辞降调】Pages Input/sec 是"页面输入速率"，
            # 不等于"从硬盘读回"，且 PageFaultCount 含软缺页 ——
            # 因此排行靠前的往往只是"在大量申请内存"的进程。
            if pin_delta >= 40.0 and cur_pin >= base_pin + 40.0:
                v, s = ("🔴 页面输入速率骤增 %.0f MB/s（较基线 +%.0f），疑似物理内存吃紧导致"
                        "从 pagefile 读回数据。缺页活跃进程（参考，非确证）: %s"
                        % (cur_pin, pin_delta, who)), "DANGER"
            elif pin_delta >= 12.0:
                v, s = "🟠 存在内存换入压力 (%.0f MB/s，较基线 +%.0f)" % (cur_pin, pin_delta), "WARN"
            else:
                v, s = "🟢 内存充裕，页面输入速率平稳 (%.1f MB/s)" % cur_pin, "NORMAL"
            rows.append(DiffRow(
                "页面文件换入 & 缺页嫌疑进程",
                "%.1f MB/s · Top: %s" % (cur_pin, who),
                "%.1f MB/s" % base_pin,
                "%+.1f MB/s" % pin_delta,
                v, s))
        else:
            rows.append(DiffRow(
                "页面文件换入 & 缺页嫌疑进程",
                "无该时刻硬件快照" if hw is None else "硬件快照已过期 (%.1fs)" % hw_age,
                "—", "—",
                "⚪ %s，本行不可判定" % ("离线数据无历史内存记录"
                                         if hw is None else
                                         "硬件快照已过期，给出结论会有误导性"), "NORMAL"))

        # ---------------- 6. 着色器编译活动 ----------------
        files, mb = store.shader_activity(frame.wall_ts, window=1.0)
        base_cache = base.base_cache_files
        if files >= CACHE_BURST_FILES or mb >= 2.0:
            v, s = ("🔴 着色器编译爆发！前后 1 秒内新增 %d 个缓存文件 (+%.1f MB)，"
                    "引发 PSO 流水线停顿" % (files, mb)), "DANGER"
        elif files >= 2:
            v, s = "🟠 检测到即时编译活动 (+%d 个文件)" % files, "WARN"
        else:
            v, s = "🟢 无即时着色器编译写盘活动", "NORMAL"
        rows.append(DiffRow(
            "着色器编译活动 (±1 秒窗口)",
            "+%d 个文件 (+%.1f MB)" % (files, mb),
            "每轮约 %.1f 个" % base_cache,
            "%+d" % int(files - base_cache),
            v, s))

        # ---------------- 7. 磁盘 I/O 资产流式加载（v3.2 新探针 1）----------------
        # PresentMon 的 Application 名（如 JustCause3.exe）与 PDH \Process(*)
        # 的实例名口径不同，需要逐个回退匹配：
        #   ① 全名小写 → ② 去掉 .exe → ③ 截断到 15 字符（Process 对象实测截断）
        # 都不中才退回「本轮读盘最凶的进程」，并在文案里注明是近似值。
        if hw is not None and not hw_stale:
            io_by = getattr(hw, "io_by_name", {}) or {}
            app_key = (frame.app or "").lower()
            io_cur = None
            io_which = ""
            for cand in (app_key,
                         app_key[:-4] if app_key.endswith(".exe") else app_key,
                         app_key[:15]):
                if cand and cand in io_by:
                    io_cur = io_by[cand][0]
                    io_which = cand
                    break
            io_approx = io_cur is None
            if io_cur is None:
                io_cur = float(getattr(hw, "io_read_mb", 0.0) or 0.0)
                tops = getattr(hw, "io_top", None) or []
                if tops:
                    io_which = "%s（全场最繁忙）" % tops[0][0]
            base_io = base.base_io_read_mb
            if io_cur >= IO_STREAM_DANGER_MB:
                v, s = ("🔴 硬盘 I/O 达 %.0f MB/s（%s）：游戏正在从硬盘流式加载模型/贴图，"
                        "硬盘随机读取已成瓶颈 ⇒ 建议把游戏安装到 NVMe 固态硬盘，"
                        "并关闭同盘的下载/解压任务" % (io_cur, io_which or "进程未知")), "DANGER"
            elif io_cur >= IO_STREAM_WARN_MB:
                v, s = ("🟠 磁盘读取偏高 %.0f MB/s（%s），疑似场景资产流式加载"
                        % (io_cur, io_which or "进程未知")), "WARN"
            else:
                v, s = "🟢 磁盘读取平稳 (%.1f MB/s)，无资产流式加载压力" % io_cur, "NORMAL"
            rows.append(DiffRow(
                "磁盘 I/O 资产流式加载",
                "%.1f MB/s%s" % (io_cur, "（近似值）" if io_approx else ""),
                "%.1f MB/s" % base_io,
                fmt_delta(io_cur, base_io, "MB/s"),
                v, s))
        else:
            rows.append(DiffRow(
                "磁盘 I/O 资产流式加载",
                "无该时刻硬件快照" if hw is None else "硬件快照已过期 (%.1fs)" % hw_age,
                "—", "—",
                "⚪ 硬件快照不可用，本行不可判定", "NORMAL"))

        # ---------------- 8. 网络同步吞吐（v3.2 新探针 4）----------------
        if hw is not None and not hw_stale:
            rx_kb = float(getattr(hw, "net_rx_mb", 0.0) or 0.0) * 1024.0
            tx_kb = float(getattr(hw, "net_tx_mb", 0.0) or 0.0) * 1024.0
            base_rx_kb = base.base_net_rx_mb * 1024.0
            pnet = (getattr(hw, "io_by_name", {}) or {})
            app_key = (frame.app or "").lower()
            app_net = None
            for cand in (app_key, app_key[:-4] if app_key.endswith(".exe") else app_key,
                         app_key[:15]):
                if cand and cand in pnet:
                    app_net = pnet[cand][1] * 1024.0     # IO Other Bytes/sec → KB/s
                    break
            stall = (base_rx_kb >= NET_STALL_RX_KB
                     and rx_kb <= base_rx_kb * NET_STALL_RATIO
                     and base_ft > 0 and cur_ft >= base_ft * 1.8)
            if stall:
                v, s = ("🔴 网络接收速率从基线 %.0f KB/s 骤降到 %.0f KB/s，"
                        "同时出现掉帧 ⇒ 疑似丢包 / 服务器 Tick 延迟，"
                        "游戏引擎在等待服务器同步（多人模式常见顿挫）"
                        % (base_rx_kb, rx_kb)), "DANGER"
            elif base_rx_kb >= NET_STALL_RX_KB and rx_kb <= base_rx_kb * 0.5:
                v, s = ("🟠 网络接收速率明显下降 (%.0f KB/s，基线 %.0f)"
                        % (rx_kb, base_rx_kb)), "WARN"
            else:
                v, s = "🟢 网络吞吐正常 (↓%.0f KB/s)" % rx_kb, "NORMAL"
            app_txt = (" · 游戏进程 %.0f KB/s" % app_net) if app_net else ""
            rows.append(DiffRow(
                "网络同步吞吐 (多人 Tick)",
                "↓%.0f KB/s ↑%.0f KB/s%s" % (rx_kb, tx_kb, app_txt),
                "↓%.0f KB/s" % base_rx_kb,
                "%+.0f KB/s" % (rx_kb - base_rx_kb),
                v, s))
        else:
            rows.append(DiffRow(
                "网络同步吞吐 (多人 Tick)",
                "无该时刻硬件快照" if hw is None else "硬件快照已过期 (%.1fs)" % hw_age,
                "—", "—",
                "⚪ 硬件快照不可用，本行不可判定", "NORMAL"))

        # ---------------- 9. GPU 核心频率 / 撞墙降频（v3.2 新探针 3）----------------
        # 【v4.1 修正·待机低频误报】增加 3D 高频门控：只有显卡处于高负载 3D 游戏
        # 运行态才启用降频检测 ——
        #   · base_clk < 1000 → 任何低频都视为省电待机态，直接归 NORMAL，绝不 DANGER；
        #   · 必须 base_clk >= GPU_DOWNCLOCK_MIN_BASE_MHZ(1200) 且 3D 负载
        #     >= GPU_DOWNCLOCK_MIN_3D_LOAD(60%)；
        #   · DANGER 还要求跌幅 > GPU_CLK_DROP_RATIO(20%) **且本帧确实是卡顿帧**
        #     （沿用本文件既有口径：cur_ft >= base_ft * 1.8）。
        if hw is not None and not hw_stale:
            clk = getattr(hw, "gpu_clock", None)
            base_clk = base.base_gpu_clock
            temp = getattr(hw, "gpu_temp", None)
            load3d = gpu_3d_load_pct(hw)
            if not clk:
                v, s = ("⚪ 本机未提供 GPU 核心频率（需要 AMD ADL 或 NVIDIA NVML 桥接），"
                        "撞墙降频不可判定"), "NORMAL"
                cur_txt = "不可用"
                delta_txt = "—"
            else:
                clk = float(clk)
                drop_pct = ((base_clk - clk) / base_clk * 100.0) if base_clk > 0 else 0.0
                drop_pct = max(0.0, drop_pct)
                temp_txt = (" · %.0f℃" % temp) if temp else ""
                cur_txt = "%.0f MHz%s" % (clk, temp_txt)
                delta_txt = fmt_delta(clk, base_clk, "MHz") if base_clk > 100 else "—"
                # 3D 高频门控：两个条件同时成立才算「游戏运行态」
                game_load = (base_clk >= GPU_DOWNCLOCK_MIN_BASE_MHZ
                             and load3d >= GPU_DOWNCLOCK_MIN_3D_LOAD)
                frame_stutter = (base_ft > 0 and cur_ft >= base_ft * 1.8)
                if base_clk > 0 and base_clk < 1000:
                    v, s = ("🟢 显卡处于省电待机档（基线 %.0f MHz），不做降频判定"
                            % base_clk), "NORMAL"
                elif game_load and drop_pct > GPU_CLK_DROP_RATIO * 100.0 and frame_stutter:
                    v, s = ("🔴 GPU 核心频率撞墙降频：%.0f → %.0f MHz (-%.0f%%)%s，"
                            "3D 负载 %.0f%%，且本帧帧耗时 %.1fms ≥ 基线 %.1fms×1.8。"
                            "显卡触发温度墙/功耗墙，帧时间随之升高 ⇒ 清灰改善风道、"
                            "提高风扇曲线或降低画质功耗"
                            % (base_clk, clk, drop_pct, temp_txt, load3d,
                               cur_ft, base_ft)), "DANGER"
                elif game_load and drop_pct > 8.0:
                    v, s = ("🟠 GPU 频率小幅回落 %.0f→%.0f MHz (-%.0f%%)，"
                            "3D 负载 %.0f%%%s（本帧未达卡顿门槛，暂不判撞墙）"
                            % (base_clk, clk, drop_pct, load3d, temp_txt)), "WARN"
                else:
                    v, s = ("🟢 GPU 频率稳定 (%.0f MHz%s)，3D 负载 %.0f%%"
                            % (clk, temp_txt, load3d)), "NORMAL"
            rows.append(DiffRow(
                "GPU 核心频率 / 撞墙降频",
                cur_txt,
                ("%.0f MHz" % base_clk) if base_clk > 100 else "—",
                delta_txt,
                v, s))
        else:
            rows.append(DiffRow(
                "GPU 核心频率 / 撞墙降频",
                "无该时刻硬件快照" if hw is None else "硬件快照已过期 (%.1fs)" % hw_age,
                "—", "—",
                "⚪ 硬件快照不可用，本行不可判定", "NORMAL"))

        # ---------------- 10. 引擎 GC / 主线程锁死（v3.2 新探针 2）----------------
        gc = SnapshotDiffInspector.classify_gc_lock(
            cur_ft, frame.gpu_busy_ratio, frame.cpu_busy_ratio, base_ft)
        rows.append(DiffRow(
            "引擎 GC / 主线程锁死",
            gc["cur"], "尖峰时 GPU 与 CPU 均不应同时空闲", "—",
            gc["text"], gc["severity"]))

        # ---------------- 11. 会话级归因统计（置信度）----------------
        # 单帧归因本质是相关性推测；会话级统计给出可验证的证据强度。
        corr = store.session_correlations()
        spike_total = 0
        if base_ft > 0:
            # 用向量化实现（numpy 环形缓冲），比 Python 层遍历 20000 帧快约 90 倍
            spike_total = store.count_spikes(base_ft * 1.8)
        if corr:
            parts = []
            for name, c in sorted(corr.items(), key=lambda kv: -kv[1]["rate"]):
                parts.append("%s %d/%d (%.0f%%·置信度%s)"
                             % (name, c["hits"], c["total"], c["rate"] * 100, c["confidence"]))
            v, s = ("📊 会话级证据：%s" % "；".join(parts), "NORMAL")
        else:
            v, s = "📊 会话级证据：样本不足，暂无统计结论", "NORMAL"
        rows.append(DiffRow(
            "会话级归因置信度",
            "本次会话共 %d 次卡顿" % spike_total,
            "按诱因统计重合率",
            "—",
            v, s))

        return rows

    @staticmethod
    def classify_gc_lock(frametime, gpu_ratio, cpu_ratio, base_frametime):
        """
        「引擎底层 GC / 主线程锁死」判定（纯函数，无副作用，便于单测）。

        诊断依据（对应本轮 MD 任务三.2）：
          严重帧时间尖峰（≥ max(50ms, 基线×3)）**但同时** GPU 忙时比与
          CPU 忙时比都异常低（< 20%）⇒ 显卡和 CPU 渲染线程都没在干活，
          帧时间却被别的东西（GC、锁、同步等待）吃掉了。

        返回 dict：{hit, severity, text, cur, gpu_pct, cpu_pct}

        【防误报·关键】
          · GPU 忙时比在 PresentMon 的 NA 场景下会被解析成 0.0，
            那不是「GPU 空闲」而是「没有数据」。这里用「比例 ≤ 1e-4」
            判定为缺失，直接给出「不可判定」，避免把「无数据」误判成 GC。
          · 未达到尖峰门槛时只报当前利用率，不报 GC。
        """
        try:
            ft = float(frametime or 0.0)
        except Exception:
            ft = 0.0
        try:
            base_ft = float(base_frametime or 0.0)
        except Exception:
            base_ft = 0.0
        gp = max(0.0, float(gpu_ratio or 0.0)) * 100.0
        cp = max(0.0, float(cpu_ratio or 0.0)) * 100.0
        cur = "%.1f ms · GPU %.0f%% / CPU %.0f%%" % (ft, gp, cp)
        thr = max(GC_SPIKE_ABS_MS, base_ft * GC_SPIKE_RATIO) if base_ft > 0 else GC_SPIKE_ABS_MS
        if ft < thr:
            return {"hit": False, "severity": "NORMAL", "cur": cur, "gpu_pct": gp,
                    "cpu_pct": cp,
                    "text": "🟢 无严重尖峰（%.1fms < 阈值 %.0fms），GC 不成立" % (ft, thr)}
        # GPU 忙时数据缺失（PresentMon 默认 metric 才有 MsGPUBusy）
        if float(gpu_ratio or 0.0) <= 1e-4:
            return {"hit": False, "severity": "NORMAL", "cur": cur, "gpu_pct": gp,
                    "cpu_pct": cp,
                    "text": "⚪ 本帧缺少 GPU 忙时数据（需默认 metric 模式），GC 不可判定"}
        if gp < GC_LOW_UTIL and cp < GC_LOW_UTIL:
            return {"hit": True, "severity": "DANGER", "cur": cur, "gpu_pct": gp,
                    "cpu_pct": cp,
                    "text": ("🔴 引擎底层 GC / 主线程锁死：帧耗时 %.0fms 但 GPU 仅 %.0f%%、"
                             "CPU 仅 %.0f%% —— 显卡与 CPU 渲染线程都没在干活，"
                             "属于游戏软件层面的代码拥堵（GC 停顿 / 主线程锁 / 同步等待），"
                             "非硬件瓶颈" % (ft, gp, cp))}
        if gp >= 80.0:
            return {"hit": False, "severity": "WARN", "cur": cur, "gpu_pct": gp,
                    "cpu_pct": cp,
                    "text": "🟠 尖峰由显卡满载造成（GPU %.0f%），不是 GC" % gp}
        if cp >= 80.0:
            return {"hit": False, "severity": "WARN", "cur": cur, "gpu_pct": gp,
                    "cpu_pct": cp,
                    "text": "🟠 尖峰由 CPU 线程满载造成（CPU %.0f%%），不是 GC" % cp}
        return {"hit": False, "severity": "NORMAL", "cur": cur, "gpu_pct": gp,
                "cpu_pct": cp,
                "text": "🟢 尖峰期间 GPU/CPU 均有负载（%.0f%% / %.0f%%），非 GC 型卡顿"
                        % (gp, cp)}

    @staticmethod
    def banner_verdict(rows, frame):
        """
        顶部一句话元凶看板。

        【D6 修正】必须报「病因」而不是「症状」。
        「帧表现」那一行描述的是症状（掉帧了），不是病因 —— 所以这里
        显式把它排除在候选之外，优先挑真正指向硬件/系统根因的那一行。
        """
        # 病因维度优先级（越靠前越可能是真正的元凶）
        # v3.2 新增四个探针后，把「因果链最上游」的维度排在前面：
        # 撞墙降频 / 磁盘流式加载 / 网络同步 都能直接解释掉帧，
        # 而 GPU/CPU 忙时比更像是这些根因的"结果"。
        cause_keys = ("GPU 核心频率", "磁盘 I/O", "网络同步", "GPU 忙时比",
                      "显存溢出", "页面文件换入", "CPU 核心状态", "着色器编译",
                      "引擎 GC")
        for key in cause_keys:
            for r in rows:
                if r.dim.startswith(key) and r.severity in ("DANGER", "WARN"):
                    return r.verdict
        # 没有硬件侧结论时，退回症状描述
        for r in rows:
            if r.severity == "DANGER":
                return r.verdict
        for r in rows:
            if r.severity == "WARN":
                return r.verdict
        return "🟢 各项硬件指标均在平稳基线附近，无掉帧诱因"
