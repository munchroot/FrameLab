# -*- coding: utf-8 -*-

"""HardwareEngine 轮询线程（CPU/GPU/显存/IO/网络/缓存）。"""

import time
import ctypes
import threading
import numpy as np
from ..consts import IO_STREAM_DANGER_MB, IO_STREAM_WARN_MB, PROC_EVENT_WHITELIST, _RE_ENGTYPE, _RE_LUID, _RE_PID
from ..models import Event, HardwareSnapshot
from ..probes import (CacheWatcher, CpuPowerProbe, GpuClockProbe, NameTable, PdhSession,
                      cpu_count, dxgi_adapters, per_core_max_mhz)
from ..utils import DIAG, human_size, pages_to_mb_per_s

# ---- 主题/阈值热更新绑定（本模块持有可变副本）----
from ..consts import bind_globals as _bind_const_globals
_bind_const_globals(globals())



# ==============================================================================
# §5  HardwareEngine —— 硬件轮询线程
# ==============================================================================
class HardwareEngine(threading.Thread):
    """
    PDH / DXGI / PSAPI 硬件探针轮询线程。
    通过 queue.Queue 向 UI 线程投递快照与事件，自身不触碰任何 UI 对象。
    """

    def __init__(self, interval, out_queue):
        super().__init__(daemon=True, name="HardwareEngine")
        self.interval = max(0.2, float(interval))
        self.out_q = out_queue
        self.names = NameTable()
        self._stop_evt = threading.Event()
        self._dxgi = dxgi_adapters()
        self._dxgi_by_key = {k: (n, t) for n, t, k in self._dxgi}
        self._n = cpu_count()
        self._max_mhz = per_core_max_mhz(self._n) or [3500] * self._n
        self._prev_freq = None
        self._prev_dominant_core = None
        self.s = PdhSession()
        self.cache_w = CacheWatcher()
        self._pf_prev = {}
        self._pf_t0 = 0.0
        self._pf_last = 0.0
        self._evt_cooldown = {}
        self._seq = 0
        # ---- v3.2 新探针状态 ----
        self.gpu_probe = GpuClockProbe()     # ADL / NVML 频率桥（懒加载）
        self.power_probe = CpuPowerProbe()   # CPU 封装功率（PDH 实测 → 估算兜底）
        self._clk_ema = None                 # GPU 频率 EMA 基线（撞墙判定用）
        self._clk_peak = 0                   # 本次会话观察到的峰值频率
        self._io_keep = 20                   # 每轮保留的 IO Top-K 进程（控内存）

    def stop(self):
        self._stop_evt.set()
        # 释放 ADL / NVML（不释放会让驱动的 ADL 引用计数泄漏）
        try:
            if self.gpu_probe is not None:
                self.gpu_probe.close()
        except Exception:
            pass
        try:
            if getattr(self, "power_probe", None) is not None:
                self.power_probe.close()
        except Exception:
            pass

    # ---------------- 主循环 ----------------
    def run(self):
        specs = [
            ("a_ded",   r"\GPU Adapter Memory(*)\Dedicated Usage"),
            ("a_sha",   r"\GPU Adapter Memory(*)\Shared Usage"),
            ("p_ded",   r"\GPU Process Memory(*)\Dedicated Usage"),
            ("p_sha",   r"\GPU Process Memory(*)\Shared Usage"),
            ("p_loc",   r"\GPU Process Memory(*)\Local Usage"),
            ("p_non",   r"\GPU Process Memory(*)\Non Local Usage"),
            ("eng_util", r"\GPU Engine(*)\Utilization Percentage"),
            ("cpu_time", r"\Processor Information(*)\% Processor Time"),
            ("cpu_perf", r"\Processor Information(*)\% Processor Performance"),
            ("m_avail", r"\Memory\Available MBytes"),
            ("m_commit", r"\Memory\Committed Bytes"),
            ("m_limit", r"\Memory\Commit Limit"),
            ("m_pin",   r"\Memory\Pages Input/sec"),
            ("m_pout",  r"\Memory\Pages Output/sec"),
            # ---- v3.2 新探针：进程级 I/O 与网络吞吐 ----
            # Process 对象的实例名形如 "chrome#1234"，同名多实例用 #n 序号区分，
            # 因此这里按「#前面的进程名」聚合，不能把 #n 当成 PID。
            ("p_io_rd",  r"\Process(*)\IO Read Bytes/sec"),
            ("p_io_wr",  r"\Process(*)\IO Write Bytes/sec"),
            ("p_io_oth", r"\Process(*)\IO Other Bytes/sec"),
            ("net_rx",   r"\Network Interface(*)\Bytes Received/sec"),
            ("n_tx",     r"\Network Interface(*)\Bytes Sent/sec"),
        ]
        if not self.s.open(specs):
            self._emit(time.time(), "系统", "PDH 计数器打开失败，硬件探针以降级模式运行",
                       advice="请确认系统性能计数器服务正常", severity="INFO")
        self.s.collect()
        self.cache_w.scan(force=True)

        while not self._stop_evt.is_set():
            t0 = time.time()
            try:
                self.s.collect()
                snap = self.assemble()
                self.out_q.put(("hw", snap))
            except Exception as ex:
                # 【评审】关键路径异常必须留痕，不能静默降级
                DIAG.report("HardwareEngine.run", ex)
                self._emit(time.time(), "系统", "硬件探针采样异常: %s" % ex,
                           advice="底层计数器偶发失败，可忽略", severity="INFO")
            dt = self.interval - (time.time() - t0)
            if dt > 0:
                self._stop_evt.wait(dt)

    def assemble(self):
        self._seq += 1
        snap = HardwareSnapshot(self._seq)
        now = time.time()
        snap.ts = now
        self._pending = []
        try:
            self._gpu(snap)
            self._cpu(snap)
            self._mem(snap)
            # ---- v3.2 新探针：磁盘 I/O + 网络 + GPU 频率 ----
            self._io_net(snap)
            self._clock(snap)
            cache_res = self.cache_w.scan()
            snap.cache = cache_res
            snap.cache_new_files = cache_res.get("_tot_new_files", 0)
            snap.cache_new_mb = cache_res.get("_tot_new_mb", 0.0)

            # 缺页归因（仅在换入确实发生时做一次昂贵的进程遍历）
            if snap.page_in > 2.0 and (now - self._pf_last > 1.5):
                self._pf_last = now
                snap.pagein_top = self._pagein_top()

            self._detect_anomalies(snap)
        except Exception as ex:
            # 探针绝不能把整个采集链带崩，但异常必须留痕
            DIAG.report("HardwareEngine.assemble", ex)
        return snap

    def _pagein_top(self):
        """页面缺页速率 Top 进程（PSAPI GetProcessMemoryInfo）"""
        k32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        n = 2048
        arr = (ctypes.c_ulong * n)()
        need = ctypes.c_ulong(0)
        if not k32.K32EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(need)):
            return []
        count = need.value // ctypes.sizeof(ctypes.c_ulong)

        class PMCEX(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong),
                        ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        pm = PMCEX()
        pm.cb = ctypes.sizeof(pm)
        now = time.time()
        dt = now - self._pf_t0 if self._pf_t0 else 0.0
        out = []
        for i in range(min(count, 1200)):
            pid = arr[i]
            if not pid:
                continue
            h = k32.OpenProcess(0x1000, False, pid)
            if not h:
                continue
            try:
                if not psapi.GetProcessMemoryInfo(h, ctypes.byref(pm), pm.cb):
                    continue
                faults = pm.PageFaultCount
                ws = pm.WorkingSetSize
            except Exception:
                continue
            finally:
                k32.CloseHandle(h)
            if ws < 20 << 20:
                continue
            prev = self._pf_prev.get(pid)
            rate = 0.0
            if prev is not None and dt > 0.2:
                rate = max(0, faults - prev) / dt
            self._pf_prev[pid] = faults
            out.append({"pid": pid, "name": self.names.get(pid), "rate": rate})
        self._pf_t0 = now
        out.sort(key=lambda r: -r["rate"])
        return [(r["name"], r["rate"] * 4096.0 / 1048576.0) for r in out[:5]]

    def _emit(self, now, kind, text, advice="", severity="WARN",
              cooldown_key=None, cooldown=6.0, symbol="o"):
        key = cooldown_key or (kind, text)
        last = self._evt_cooldown.get(key)
        if last is not None and now - last < cooldown:
            return
        self._evt_cooldown[key] = now
        if len(self._evt_cooldown) > 300:
            cutoff = now - 180
            self._evt_cooldown = {k: v for k, v in self._evt_cooldown.items() if v > cutoff}
        ev = Event(now, kind, text, advice, severity, symbol)
        self.out_q.put(("event", ev))

    # ---------------- 异常探测 ----------------
    def _detect_anomalies(self, s):
        now = s.ts

        # 1) CPU 单核降频
        if s.cpu_freq and self._prev_freq:
            drops = []
            for c in range(min(len(s.cpu_freq), len(self._prev_freq))):
                f0 = self._prev_freq[c]
                f1 = s.cpu_freq[c]
                if f0 > 500 and (f0 - f1) / f0 > FREQ_DROP_RATIO:
                    u = s.cpu_usage[c] if c < len(s.cpu_usage) else 0
                    if u > 5:
                        drops.append((c, f0, f1, u))
            if len(drops) >= 4:
                self._emit(now, "降频", "≥4 核同时大幅降频，判定为 Alt+Tab 切出窗口",
                           advice="系统检测到游戏切出，忽略此降频", severity="INFO",
                           cooldown_key=("alttab",), cooldown=6.0, symbol="t")
            else:
                for c, f0, f1, u in drops:
                    drop_pct = (f0 - f1) / f0 * 100
                    self._emit(now, "降频",
                               "CPU%d 骤降频 %d→%dMHz (-%.0f%%) 负载 %.0f%%" % (c, f0, f1, drop_pct, u),
                               advice="排查 CPU 温度墙过热 throttling 或主板供电 VRM 限制",
                               severity="WARN" if drop_pct < 30 else "DANGER",
                               cooldown_key=("drop", c), cooldown=8.0, symbol="v")
        self._prev_freq = list(s.cpu_freq) if s.cpu_freq else self._prev_freq

        # 2) CPU0 拥堵
        # 【口径统一】事件探测与诊断卡必须用同一套规则：
        # CPU0 满载 **且** 其他核心确实空闲，才是"拥堵"；
        # 若其他核心同样满载，那只是整机负载高，不是 CPU0 特有问题。
        if s.cpu0_usage >= CPU0_JAM_THRESHOLD and len(s.cpu_usage) > 1:
            others = s.cpu_usage[1:]
            avg_other = sum(others) / len(others) if others else 0.0
            if avg_other < CPU0_IDLE_OTHERS:
                self._emit(now, "CPU0拥堵",
                           "CPU0 占用率达 %.0f%% 严重拥堵（其他核心均值仅 %.0f%%）" % (s.cpu0_usage, avg_other),
                           advice="系统中断(DPC/ISR)或主线程卡死 0 号核，建议在 Process Lasso 取消勾选游戏 CPU0",
                           severity="DANGER", cooldown_key=("cpu0_jam",), cooldown=8.0, symbol="s")

        # 3) 线程跨核剧烈迁移 / 大小核异构异常
        if s.cpu_usage and len(s.cpu_usage) > 2:
            max_c = int(np.argmax(s.cpu_usage))
            max_u = s.cpu_usage[max_c]
            if max_u > 60.0:
                if self._prev_dominant_core is not None and self._prev_dominant_core != max_c:
                    old_c = self._prev_dominant_core
                    f_old = s.cpu_freq[old_c] if old_c < len(s.cpu_freq) else 0
                    f_new = s.cpu_freq[max_c] if max_c < len(s.cpu_freq) else 0
                    if abs(f_old - f_new) > MIGRATE_FREQ_GAP or s.core_variance > 450:
                        self._emit(now, "调度迁移",
                                   "主负载核剧烈迁移 CPU%d(%.0fMHz) → CPU%d(%.0fMHz)"
                                   % (old_c, f_old, max_c, f_new),
                                   advice="主线程跨核/掉入能效核，建议在电源计划锁定高性能或用 Process Lasso 绑定大核",
                                   severity="WARN", cooldown_key=("migration",), cooldown=10.0, symbol="x")
                self._prev_dominant_core = max_c

        # 4) 显存溢出到内存
        for r in s.procs:
            if r["nonlocal"] > SPILL_THRESHOLD and r["name"].lower() not in PROC_EVENT_WHITELIST:
                self._emit(now, "溢出",
                           "%s 显存溢出 %s 到系统内存" % (r["name"], human_size(r["nonlocal"])),
                           advice="显卡物理显存用尽，请在游戏画质中降低【贴图分辨率 / 材质质量】",
                           severity="DANGER", cooldown_key=("spill", r["pid"]), cooldown=15.0, symbol="o")

        # 5) 页面文件换入高峰
        pin = s.page_in
        if pin > 45.0:
            tops = [t for t in (s.pagein_top or []) if t[1] > 1.0]
            who = "、".join("%s(%.0fMB/s)" % (n, m) for n, m in tops[:3]) if tops else "后台程序"
            self._emit(now, "内存",
                       "页面文件换入 %.0f MB/s（内存爆满正在读回硬盘数据）· 嫌疑: %s" % (pin, who),
                       advice="物理内存耗尽，Windows 正倒腾虚拟内存，请关闭高占内存后台并加装物理内存",
                       severity="DANGER", cooldown_key=("memhot",), cooldown=12.0, symbol="p")

        # 6) 着色器缓存爆发写入
        for tag, d in (s.cache or {}).items():
            if isinstance(d, dict) and d.get("new_files", 0) >= CACHE_BURST_FILES:
                self._emit(now, "着色器",
                           "%s 着色器写入爆发 +%d 文件 (+%.1f MB)" % (tag, d["new_files"], d["new_mb"]),
                           advice="游戏正在即时编译着色器(Shader/PSO)，首次进新场景编译完成后将恢复流畅",
                           severity="WARN", cooldown_key=("shader", tag), cooldown=8.0, symbol="d")

        # ---------------- v3.2 新探针 1：硬盘 I/O 资产流式加载 ----------------
        # 判据：某个进程的磁盘读取速率瞬间飙升。真实游戏里这几乎只有两种来源——
        #   · 正在流式加载模型/贴图（Unity/UE 的 streaming）
        #   · 内存不足导致从 pagefile 换入（那一路由「内存」探针单独负责，会同时告警）
        # 因此这里只在「读取速率异常高」时提示，且不宣称一定是游戏本体
        # （非管理员下无法百分百确认是谁在读盘）。
        if s.io_read_mb >= IO_STREAM_WARN_MB:
            who = "、".join("%s %.0fMB/s" % (n, v) for n, v in (s.io_top or [])[:2])
            sev = "DANGER" if s.io_read_mb >= IO_STREAM_DANGER_MB else "WARN"
            self._emit(now, "磁盘I/O",
                       "进程磁盘读取 %.0f MB/s%s —— 正在从硬盘流式加载资产"
                       % (s.io_read_mb, ("（%s）" % who) if who else ""),
                       advice="硬盘成为加载瓶颈：把游戏安装到 NVMe 固态硬盘，"
                              "并避免与下载/解压等高 I/O 任务同时进行",
                       severity=sev, cooldown_key=("assetstream",), cooldown=12.0, symbol="d")

        # ---------------- v3.2 新探针 3：GPU 撞墙降频 ----------------
        # 【v4.1 修正·待机低频误报】PROBE 3 增加 3D 高频门控：
        #   · 基线频率必须 >= GPU_DOWNCLOCK_MIN_BASE_MHZ(1200)：低于 1000MHz
        #     一律视为显卡省电待机态（本机 375MHz 就是典型），直接跳过；
        #   · 当前 3D 引擎负载必须 >= GPU_DOWNCLOCK_MIN_3D_LOAD(60%)：负载低时的
        #     降频是正常节能，不是撞墙；
        #   · 阈值统一用新的 GPU_CLK_DROP_RATIO(0.20)。
        # 【措辞】事件探针拿不到帧数据，因此只写「若同时伴随帧时间升高，则为撞墙
        # 降频」，不宣称一定掉帧（帧级确证由诊断卡第 9 行走 DANGER 门控）。
        load3d = max(s.gpu_eng.values()) if s.gpu_eng else 0.0
        if (s.gpu_clock and self._clk_ema
                and self._clk_ema >= GPU_DOWNCLOCK_MIN_BASE_MHZ
                and load3d >= GPU_DOWNCLOCK_MIN_3D_LOAD):
            drop = (self._clk_ema - float(s.gpu_clock)) / self._clk_ema
            if drop > GPU_CLK_DROP_RATIO:
                temp_txt = (" · 核心温度 %.0f℃" % s.gpu_temp) if s.gpu_temp else ""
                self._emit(now, "GPU降频",
                           "GPU 核心频率 %.0f→%.0f MHz (-%.0f%%)%s，3D 负载仍有 %.0f%%"
                           % (self._clk_ema, s.gpu_clock, drop * 100.0, temp_txt, load3d),
                           advice="显卡触发温度墙 / 功耗墙强制降频：清灰改善风道、"
                                  "提高风扇曲线，或降低画质以压低功耗。"
                                  "若同时伴随帧时间升高，则为撞墙降频",
                           severity="DANGER", cooldown_key=("gputhrottle",), cooldown=15.0,
                           symbol="v")

    # ---------------- 分类采集 ----------------
    def _gpu(self, s):
        a_ded = self.s.array("a_ded")
        a_sha = self.s.array("a_sha")
        p_ded = self.s.array("p_ded")
        p_sha = self.s.array("p_sha")
        p_loc = self.s.array("p_loc")
        p_non = self.s.array("p_non")

        tot_dedicated = 0.0
        tot_capacity = 0.0
        tot_local = 0.0
        for inst, v in a_ded.items():
            m = _RE_LUID.search(inst)
            if not m:
                continue
            key = ((int(m.group(1), 16) & 0xFFFFFFFF) << 32 |
                   (int(m.group(2), 16) & 0xFFFFFFFF))
            sha = a_sha.get(inst, 0.0)
            if v <= 0 and sha <= 0:
                continue
            name, tot = self._dxgi_by_key.get(key, (None, 0))
            if not tot and v < 1 << 20:
                continue
            s.adapters[key] = {"name": name or ("GPU (LUID %08X)" % (key & 0xFFFFFFFF)),
                               "total": tot, "dedicated": v, "shared": sha}
            tot_dedicated += v
            if tot > tot_capacity:
                tot_capacity = tot

        s.vram_dedicated = tot_dedicated
        # 【评审】取不到容量时不能写死成 8GB —— 那会让"已用 0%"这种
        # 完全错误的比例显示出来。改用 0 表示"未知"，显示层会渲染为"—"。
        s.vram_total = tot_capacity if tot_capacity > 0 else 0.0

        eng = {}
        for inst, v in self.s.array("eng_util").items():
            if v <= 0:
                continue
            mt = _RE_ENGTYPE.search(inst)
            if mt:
                eng[mt.group(1)] = eng.get(mt.group(1), 0.0) + v
        s.gpu_eng = eng

        agg = {}
        for inst, v in p_loc.items():
            mp = _RE_PID.search(inst)
            if mp:
                agg.setdefault(int(mp.group(1)), {})["local"] = v
        for inst, v in p_ded.items():
            mp = _RE_PID.search(inst)
            if mp and int(mp.group(1)) in agg:
                agg[int(mp.group(1))]["ded"] = v
        for inst, v in p_non.items():
            mp = _RE_PID.search(inst)
            if mp and int(mp.group(1)) in agg:
                agg[int(mp.group(1))]["non"] = v
        for inst, v in p_sha.items():
            mp = _RE_PID.search(inst)
            if mp and int(mp.group(1)) in agg:
                agg[int(mp.group(1))]["sha"] = v

        tot_nonlocal = 0.0
        for pid, d in agg.items():
            loc = d.get("local", 0.0)
            ded = d.get("ded", 0.0)
            non = d.get("non", 0.0)
            tot_nonlocal += non
            tot_local += loc
            if ded < 1 << 20 and loc < 1 << 20:
                continue
            s.procs.append({"pid": pid, "name": self.names.get(pid),
                            "local": loc, "dedicated": ded,
                            "shared": d.get("sha", 0.0), "nonlocal": non,
                            "total": loc + non})
        s.vram_nonlocal = tot_nonlocal
        s.vram_local = tot_local
        s.procs.sort(key=lambda r: (-r["local"], -r["nonlocal"]))
        s.procs = s.procs[:20]

    def _cpu(self, s):
        t = self.s.array("cpu_time")
        perf = self.s.array("cpu_perf")
        usage = []
        freq = []
        for i in range(self._n):
            inst = "0,%d" % i
            u = t.get(inst)
            if u is None:
                u = t.get(str(i))
            usage.append(max(0.0, float(u or 0.0)))
            base = self._max_mhz[i] if i < len(self._max_mhz) else 3500
            pf = perf.get(inst)
            if pf is None:
                pf = perf.get(str(i))
            if pf and pf > 0:
                freq.append(base * (pf / 100.0))
            else:
                freq.append(float(base))

        s.cpu_usage = usage
        s.cpu_freq = freq
        s.cpu0_usage = usage[0] if usage else 0.0
        if usage:
            s.dominant_core = int(np.argmax(usage))
            s.core_variance = float(np.var(usage))
        tot = t.get("_Total")
        s.cpu_total = max(0.0, float(tot)) if tot is not None else (sum(usage) / len(usage) if usage else 0.0)

        # ---- v1.0.0：CPU 封装功率（只供界面画走势，不参与任何归因判定）----
        try:
            if self.power_probe is not None and freq:
                watts, est, _src = self.power_probe.read(
                    sum(freq) / len(freq), max(freq), s.cpu_total)
                s.cpu_power = watts
                s.cpu_power_est = bool(est)
        except Exception as ex:
            DIAG.report("HardwareEngine._cpu.power", ex)

    # ---------------- v3.2 新探针采集 ----------------
    # 排除的虚拟/回环网卡关键字：它们的吞吐与游戏同步无关，
    # 计入总速率会让「网络归零」判定彻底失效（回环永远在跑）。
    NET_EXCLUDE = ("loopback", "isatap", "teredo", "pseudo", "pseudo-interface",
                   "tap-", "tap ", "vpn", "vethernet", "virtual", "vmware",
                   "vbox", "hyper-v", "bluetooth", "wsl", "npcap", "km-test")

    def _io_net(self, s):
        """
        进程级磁盘 I/O + 网卡吞吐采集。

        【为什么按进程名聚合而不是按实例名】
        Process 性能对象的实例名是 `chrome#1234` 这种形式，其中 `#` 后是
        **同名实例的序号**（不是 PID），并且进程名会被截断到 15 字符。
        直接把 `#n` 当 PID 会在同名多进程（chrome、svchost）时张冠李戴，
        因此这里一律剥掉 `#...` 后缀、按进程名小写聚合求和。
        """
        try:
            rd = self.s.array("p_io_rd")
            wr = self.s.array("p_io_wr")
            oth = self.s.array("p_io_oth")
            agg = {}
            for src, slot in ((rd, 0), (wr, 1), (oth, 2)):
                for inst, v in src.items():
                    if not inst or inst.startswith("_"):
                        continue
                    name = inst.split("#")[0].strip().lower()
                    if not name or name == "idle":
                        continue
                    try:
                        val = float(v)
                    except Exception:
                        continue
                    if val < 0:
                        val = 0.0
                    cur = agg.get(name)
                    if cur is None:
                        cur = [0.0, 0.0, 0.0]
                        agg[name] = cur
                    cur[slot] += val
            if agg:
                # 只保留 I/O 最活跃的 Top-K（内存：20000 快照 × 20 项仍可控）
                items = sorted(agg.items(),
                               key=lambda kv: -(kv[1][0] + kv[1][1] + kv[1][2]))
                items = items[:self._io_keep]
                conv = {}
                for name, (a, b, c) in items:
                    conv[name] = (a / 1048576.0, c / 1048576.0, b / 1048576.0)
                s.io_by_name = conv
                best = max(items, key=lambda kv: kv[1][0])
                s.io_read_mb = best[1][0] / 1048576.0
                s.io_other_mb = best[1][2] / 1048576.0
                s.io_write_mb = best[1][1] / 1048576.0
                s.io_top = [(n, v[0] / 1048576.0) for n, v in
                            sorted(items, key=lambda kv: -kv[1][0])[:3]
                            if v[0] > 0]
        except Exception as ex:
            DIAG.report("HardwareEngine._io_net(io)", ex)

        try:
            rx = self.s.array("net_rx")
            tx = self.s.array("n_tx")
            tot_rx = 0.0
            tot_tx = 0.0
            detail = {}
            for inst, v in rx.items():
                low = inst.lower()
                if any(k in low for k in self.NET_EXCLUDE):
                    continue
                r = max(0.0, float(v or 0.0))
                t = max(0.0, float(tx.get(inst, 0.0) or 0.0))
                if r <= 0 and t <= 0:
                    continue
                tot_rx += r
                tot_tx += t
                detail[inst] = (r / 1048576.0, t / 1048576.0)
            s.net_rx_mb = tot_rx / 1048576.0
            s.net_tx_mb = tot_tx / 1048576.0
            s.net_adapters = detail
        except Exception as ex:
            DIAG.report("HardwareEngine._io_net(net)", ex)

    def _clock(self, s):
        """
        GPU 核心频率 / 温度（撞墙降频探针）。

        频率不可用时 `s.gpu_clock` 保持 None —— 诊断卡会明确写
        「本机未提供 GPU 频率」，而不是拿 0 MHz 去算一个 -100% 的假降频。
        """
        try:
            if self.gpu_probe is None:
                return
            clk, temp = self.gpu_probe.read()
            s.gpu_temp = temp
            if clk:
                clk = int(clk)
                if clk > self._clk_peak:
                    self._clk_peak = clk
                # EMA 基线：0.9/0.1，兼顾稳定性与响应速度（0.4s × ~10 轮 ≈ 4s 收敛）
                if self._clk_ema is None:
                    self._clk_ema = float(clk)
                else:
                    self._clk_ema = 0.9 * self._clk_ema + 0.1 * float(clk)
                s.gpu_clock = clk
            s.gpu_clock_max = self._clk_peak
        except Exception as ex:
            DIAG.report("HardwareEngine._clock", ex)

    def _mem(self, s):
        def one(key):
            a = self.s.array(key)
            return next(iter(a.values()), 0.0)
        s.mem_avail_mb = one("m_avail")
        s.mem_commit = one("m_commit")
        s.mem_limit = one("m_limit")
        s.page_in = pages_to_mb_per_s(one("m_pin"))
        s.page_out = pages_to_mb_per_s(one("m_pout"))
