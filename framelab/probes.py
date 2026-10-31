# -*- coding: utf-8 -*-

"""底层 ctypes 探针：PDH / DXGI / AMD ADL / NVML / 缓存目录监视。"""

import os
import time
import uuid
import ctypes
import struct
import threading



# ==============================================================================
# §2  底层 ctypes 探针（实战验证版本，完整保留）
# ==============================================================================
PDH_FMT_DOUBLE = 0x00000200


PDH_MORE_DATA = 0x800007D2


class PdhSession:
    """PDH 计数器会话。x64 下 PDH_FMT_COUNTERVALUE_ITEM_W 步长实测为 24 字节。"""

    def __init__(self):
        self.ok = False
        self.hq = None
        self.hc = {}
        try:
            self.pdh = ctypes.WinDLL("pdh")
        except Exception:
            self.pdh = None

    def open(self, specs):
        if not self.pdh:
            return False
        pdh = self.pdh
        pdh.PdhOpenQueryW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        pdh.PdhOpenQueryW.restype = ctypes.c_long
        pdh.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p,
                                              ctypes.POINTER(ctypes.c_void_p)]
        pdh.PdhAddEnglishCounterW.restype = ctypes.c_long
        pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        pdh.PdhCollectQueryData.restype = ctypes.c_long
        pdh.PdhGetFormattedCounterArrayW.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
        pdh.PdhGetFormattedCounterArrayW.restype = ctypes.c_long

        hq = ctypes.c_void_p()
        if pdh.PdhOpenQueryW(None, None, ctypes.byref(hq)) != 0:
            return False
        self.hq = hq
        for key, path in specs:
            hc = ctypes.c_void_p()
            if pdh.PdhAddEnglishCounterW(hq, path, None, ctypes.byref(hc)) == 0:
                self.hc[key] = hc
        pdh.PdhCollectQueryData(hq)
        self.ok = len(self.hc) > 0
        return self.ok

    def collect(self):
        if self.ok and self.hq:
            self.pdh.PdhCollectQueryData(self.hq)

    def array(self, key):
        """取一次格式化的计数器数组；名字从缓冲区字节副本解析（跨模块裸指针会 OSError）"""
        hc = self.hc.get(key)
        if not hc:
            return {}
        pdh = self.pdh
        size = ctypes.c_ulong(0)
        count = ctypes.c_ulong(0)
        rc = pdh.PdhGetFormattedCounterArrayW(hc, PDH_FMT_DOUBLE, ctypes.byref(size),
                                              ctypes.byref(count), None)
        # rc 是有符号 c_long，PDH_MORE_DATA 必须按位比较
        if (rc & 0xFFFFFFFF) != PDH_MORE_DATA or size.value == 0:
            return {}
        buf = ctypes.create_string_buffer(size.value)
        rc = pdh.PdhGetFormattedCounterArrayW(hc, PDH_FMT_DOUBLE, ctypes.byref(size),
                                              ctypes.byref(count), buf)
        if rc != 0:
            return {}
        raw = bytes(buf.raw[:size.value])
        base = ctypes.addressof(buf)
        out = {}
        for i in range(count.value):
            off = i * 24
            try:
                ptr0 = struct.unpack_from("<Q", raw, off)[0]
                val = struct.unpack_from("<d", raw, off + 16)[0]   # 值在偏移 16
            except struct.error:
                break
            rel = ptr0 - base
            name = ""
            if 0 <= rel < size.value - 2:
                try:
                    seg = raw[rel:rel + 136]
                    name = seg.decode("utf-16-le", errors="ignore").split("\x00")[0]
                except Exception:
                    name = ""
            if not name:
                name = "#%d" % i
            out[name] = val
        return out

    def close(self):
        try:
            if self.hq and self.pdh:
                self.pdh.PdhCloseQuery(self.hq)
        except Exception:
            pass
        self.ok = False


class _IUnknown(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(ctypes.c_void_p))]


def _vtbl(obj, index, restype, *argtypes):
    functype = ctypes.WINFUNCTYPE(restype, ctypes.POINTER(_IUnknown), *argtypes)
    return ctypes.cast(obj.contents.lpVtbl[index], functype)


def dxgi_adapters():
    """枚举 DXGI 物理适配器 -> [(名称, 专用显存字节, LUID key)]"""
    out = []

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", ctypes.c_ulong), ("HighPart", ctypes.c_long)]

    try:
        dxgi = ctypes.windll.dxgi
        CreateDXGIFactory1 = dxgi.CreateDXGIFactory1
        # 必须用 IID_IDXGIFactory2：本机对 IID_IDXGIFactory1 返回 E_NOINTERFACE
        iid_bytes = uuid.UUID("50c83a1c-e072-4c48-87b0-3630fa36a6d0").bytes_le
        CreateDXGIFactory1.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(_IUnknown))]
        CreateDXGIFactory1.restype = ctypes.c_long
        factory = ctypes.POINTER(_IUnknown)()
        hr = CreateDXGIFactory1(ctypes.cast(ctypes.create_string_buffer(iid_bytes), ctypes.c_void_p),
                                ctypes.byref(factory))
        if hr != 0:
            return out
        EnumAdapters = _vtbl(factory, 7, ctypes.c_long, ctypes.c_uint,
                             ctypes.POINTER(ctypes.POINTER(_IUnknown)))

        class ADAPTER_DESC(ctypes.Structure):
            _fields_ = [("Description", ctypes.c_wchar * 128),
                        ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                        ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint),
                        ("DedicatedVideoMemory", ctypes.c_size_t),
                        ("DedicatedSystemMemory", ctypes.c_size_t),
                        ("SharedSystemMemory", ctypes.c_size_t),
                        ("AdapterLuid", LUID), ("Flags", ctypes.c_uint)]

        i = 0
        while True:
            ad = ctypes.POINTER(_IUnknown)()
            if EnumAdapters(factory, i, ctypes.byref(ad)) != 0:
                break
            i += 1
            try:
                GetDesc = _vtbl(ad, 8, ctypes.c_long, ctypes.POINTER(ADAPTER_DESC))
                desc = ADAPTER_DESC()
                if GetDesc(ad, ctypes.byref(desc)) == 0 and desc.DedicatedVideoMemory > 0:
                    luid_key = ((desc.AdapterLuid.HighPart & 0xFFFFFFFF) << 32 |
                                (desc.AdapterLuid.LowPart & 0xFFFFFFFF))
                    out.append((desc.Description.strip(), desc.DedicatedVideoMemory, luid_key))
            except Exception:
                pass
            finally:
                try:
                    _vtbl(ad, 2, ctypes.c_ulong)(ad)
                except Exception:
                    pass
        try:
            _vtbl(factory, 2, ctypes.c_ulong)(factory)
        except Exception:
            pass
    except Exception:
        pass
    return out




# ------------------------------------------------------------------------------
# GPU 核心频率桥（撞墙降频探针的数据源）
# ------------------------------------------------------------------------------
# Windows 的性能计数器**没有** GPU 核心频率（\GPU Engine 只有利用率），
# 因此必须走厂商桥：
#   · AMD：atiadlxx.dll 的 ADL2_New_QueryPMLogData_Get（PMLog 传感器表）
#   · NVIDIA：nvml.dll 的 nvmlDeviceGetClockInfo
# 两者都**不要求管理员权限**，且返回的是驱动侧的实时值（不是标称规格）。
#
# 实测（本机 RX 6650 XT / Adrenalin 26.9.1）：
#   PMLog 返回 12 个非零传感器；空载时 sensor1≈375MHz（GFXCLK 低功耗档）、
#   sensor8≈49（温度 ℃）、sensor14≈1262（风扇 RPM）、sensor15≈38（风扇 %）。
#   索引定义来自 AMD 公开的 ADL 头文件（ADLSensorType 枚举），
#   若未来驱动改表，只需改下面这两个常量。
ADL_PMLOG_SENSOR_GFXCLK = 1          # ADLSensorType.PMLOG_CLK_GFXCLK


ADL_PMLOG_SENSOR_EDGE_TEMP = 8       # ADLSensorType.PMLOG_TEMPERATURE_EDGE


ADL_SENSOR_INVALID = 65535           # ADL 用 65535 表示「该传感器无效」


class _ADL_Sensor(ctypes.Structure):
    # ADLSingleSensorData { int supported; int value; }
    _fields_ = [("supported", ctypes.c_int), ("value", ctypes.c_int)]


class _ADL_PMLogData(ctypes.Structure):
    # ADLPMLogDataOutput { int size; ADLSingleSensorData sensors[256]; }
    # 【坑】嵌套结构体必须给真实类型对象，且**两个结构体都得在模块级**——
    # Python 的类体不是闭包作用域，嵌套类内部引用不到外层类体里定义的名字。
    _fields_ = [("size", ctypes.c_int), ("sensors", _ADL_Sensor * 256)]


class GpuClockProbe:
    """
    GPU 核心频率/温度采样器（AMD ADL 优先，NVIDIA NVML 兜底）。

    设计约束（踩坑总结）：
      · **ADL context 必须复用**：每次采样都 Main_Control_Create/Destroy
        会让驱动的 ADL 内部引用计数抖动，实测连续 500 次后驱动日志出现
        「ADL context leak」告警；这里懒加载一次、进程退出前才 Destroy。
      · **malloc 回调对象必须强引用住**：ctypes 的 CFUNCTYPE 回调一旦被 GC，
        驱动后续再调用它就会崩进程 —— 存到 self 上。
      · 任何一步失败都返回 None（而不是 0）：0 MHz 会被上层当成「真实频率 0」
        从而把降频判定彻底带偏。
      · 失败后进入 5 分钟冷却（COOLDOWN），避免在不支持的机器上每 0.4 秒
        重试一次 DLL 加载（那会让硬件轮询线程频繁抖动）。
    """

    COOLDOWN = 300.0

    def __init__(self):
        self.ok = False
        self.vendor = None            # "amd" / "nvidia" / None
        self.last_err = ""
        self._dll = None
        self._ctx = ctypes.c_void_p()
        self._cb = None               # malloc 回调（必须强引用）
        self._nvml = None
        self._nvml_handle = None
        self._dead_until = 0.0
        self._tried = False

    # ---------------- 初始化 ----------------
    def _init_amd(self):
        adl = ctypes.WinDLL("atiadlxx.dll")
        CB = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_int)

        # 【v1.0.5 修闪退·根因】ADL 约定：回调分配、驱动内部用 **free()** 释放。
        # 旧实现返回 create_string_buffer 的裸地址，两处必炸：
        #   ① buf 是局部变量 → 回调一返回就 GC，驱动继续读写 → use-after-free；
        #   ② 驱动拿 CRT free() 释放 Python 堆对象 → 堆元数据损坏。
        # 表现为**随机时间、随机操作**下闪退（事件日志 python.exe /
        # libffi-8.dll / 0xc0000409 BEX64 / 偏移 0x4d61）。
        # 修复：用与驱动同一 UCRT 的 malloc/free，内存生命周期完全交给驱动。
        _msv = ctypes.cdll.msvcrt
        _msv.malloc.argtypes = [ctypes.c_size_t]
        _msv.malloc.restype = ctypes.c_void_p

        def _malloc(size):
            try:
                return _msv.malloc(max(1, int(size)))
            except Exception:
                return None

        self._cb = CB(_malloc)
        create = adl.ADL2_Main_Control_Create
        create.argtypes = [CB, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        create.restype = ctypes.c_int
        ctx = ctypes.c_void_p()
        rc = create(self._cb, 1, ctypes.byref(ctx))
        if rc != 0:
            raise RuntimeError("ADL2_Main_Control_Create rc=%d" % rc)
        q = adl.ADL2_New_QueryPMLogData_Get
        q.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        q.restype = ctypes.c_int
        # 探测哪个适配器支持 PMLog（多卡机器上可能只有 0 号支持）
        self._dll = adl
        self._ctx = ctx
        self._adapter = 0
        probe = _ADL_PMLogData()
        probe.size = ctypes.sizeof(probe)
        rc = q(ctx, 0, ctypes.byref(probe))
        if rc != 0:
            raise RuntimeError("ADL2_New_QueryPMLogData_Get rc=%d" % rc)
        self.vendor = "amd"
        self.ok = True

    def _init_nvidia(self):
        nvml = ctypes.WinDLL("nvml.dll")
        nvml.nvmlInit_v2.restype = ctypes.c_int
        if nvml.nvmlInit_v2() != 0:
            raise RuntimeError("nvmlInit failed")
        handle = ctypes.c_void_p()
        if nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) != 0:
            raise RuntimeError("nvmlDeviceGetHandleByIndex failed")
        self._nvml = nvml
        self._nvml_handle = handle
        self.vendor = "nvidia"
        self.ok = True

    def _ensure(self):
        if self.ok:
            return True
        now = time.time()
        if self._dead_until and now < self._dead_until:
            return False
        if self._tried and not self.ok:
            # 只在冷却到期后重试一次
            pass
        self._tried = True
        for init in (self._init_amd, self._init_nvidia):
            try:
                init()
                self.last_err = ""
                return True
            except Exception as ex:
                self.last_err = "%s: %s" % (init.__name__, ex)
        self._dead_until = now + self.COOLDOWN
        return False

    # ---------------- 采样 ----------------
    def read(self):
        """
        → (clock_mhz, temp_c)；任一不可得返回 None。
        频率是**整数 MHz**；温度是 ℃（ADL 的 edge 温度本身就是整数 ℃）。
        """
        if not self._ensure():
            return None, None
        try:
            if self.vendor == "amd":
                d = _ADL_PMLogData()
                d.size = ctypes.sizeof(d)
                rc = self._dll.ADL2_New_QueryPMLogData_Get(
                    self._ctx, self._adapter, ctypes.byref(d))
                if rc != 0:
                    return None, None
                def _val(idx):
                    if 0 <= idx < 256:
                        sn = d.sensors[idx]
                        if sn.supported and sn.value not in (0, ADL_SENSOR_INVALID):
                            return sn.value
                    return None
                clock = _val(ADL_PMLOG_SENSOR_GFXCLK)
                temp = _val(ADL_PMLOG_SENSOR_EDGE_TEMP)
                # 合理性门控：GFXCLK 不会超过 6000MHz；温度不会是 0~5℃ 之外的离谱值
                if clock is not None and not (30 <= clock <= 6000):
                    clock = None
                if temp is not None and not (-40 <= temp <= 150):
                    temp = None
                return clock, temp
            if self.vendor == "nvidia":
                nvml = self._nvml
                clk = ctypes.c_uint(0)
                # 0 = NVML_CLOCK_GRAPHICS
                if nvml.nvmlDeviceGetClockInfo(self._nvml_handle, 0,
                                               ctypes.byref(clk)) == 0:
                    return int(clk.value), None
        except Exception as ex:
            self.last_err = str(ex)
            return None, None
        return None, None

    def close(self):
        try:
            if self.vendor == "amd" and self._dll is not None and self._ctx:
                self._dll.ADL2_Main_Control_Destroy.argtypes = [ctypes.c_void_p]
                self._dll.ADL2_Main_Control_Destroy(self._ctx)
        except Exception:
            pass
        try:
            if self.vendor == "nvidia" and self._nvml is not None:
                self._nvml.nvmlShutdown()
        except Exception:
            pass
        self.ok = False
        self._dll = None
        self._ctx = ctypes.c_void_p()
        self._cb = None


class NameTable:
    """
    PID → 进程名高速缓存。

    【线程安全 · 评审高危项】
    UI 线程（_refresh_processes）与硬件探针线程都会调用 get()。
    原实现在 get() 里**持锁**执行 _refresh()：K32EnumProcesses + 2048 次
    QueryFullProcessImageNameW 全在锁内完成 → 既卡住 UI（掉帧），
    又把硬件线程一起堵死（采样间隔被拉长，硬件对齐失真）。

    现在改为：
      · get() 只做「单 PID 查询」（OpenProcess 一次，微秒级），永不持锁遍历；
      · 全量刷新丢到后台守护线程，扫描期间**完全不持锁**，
        扫完再原子换表（self.map 整体替换，读者无锁可见）。
    """

    REFRESH_INTERVAL = 10.0

    def __init__(self):
        self.map = {}
        self.last = 0.0
        self.lock = threading.Lock()
        self._refreshing = False
        self.k32 = ctypes.windll.kernel32
        self.k32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong,
            ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
        self.k32.QueryFullProcessImageNameW.restype = ctypes.c_int

    def get(self, pid):
        with self.lock:
            name = self.map.get(pid)
        if name:
            return name
        # 单 PID 兜底查询：微秒级，不遍历、不持锁
        name = self._query(pid)
        if name:
            with self.lock:
                self.map.setdefault(pid, name)
            return name
        self._maybe_refresh()
        return "PID %d" % pid

    def _maybe_refresh(self):
        """过期时在后台线程做一次全量刷新（不阻塞调用方，且防重入）"""
        with self.lock:
            if self._refreshing:
                return
            if (time.time() - self.last) <= self.REFRESH_INTERVAL:
                return
            self._refreshing = True
            self.last = time.time()

        def worker():
            try:
                self._refresh()
            except Exception:
                pass
            finally:
                with self.lock:
                    self._refreshing = False

        threading.Thread(target=worker, name="NameTableRefresh", daemon=True).start()

    def _refresh(self):
        """全量扫描（后台线程执行；扫描期间不持锁）"""
        n = 4096
        arr = (ctypes.c_ulong * n)()
        need = ctypes.c_ulong(0)
        if not self.k32.K32EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(need)):
            return
        count = need.value // ctypes.sizeof(ctypes.c_ulong)
        m = {}
        for i in range(min(count, 2048)):
            pid = arr[i]
            if not pid:
                continue
            name = self._query(pid)
            if name:
                m[pid] = name
        if m:
            with self.lock:
                self.map = m

    def _query(self, pid):
        h = self.k32.OpenProcess(0x1000, False, pid)
        if not h:
            return None
        try:
            size = ctypes.c_ulong(1024)
            b = ctypes.create_unicode_buffer(size.value)
            if self.k32.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(size)):
                return os.path.basename(b.value) or None
            return None
        except Exception:
            return None
        finally:
            self.k32.CloseHandle(h)


class SYSTEM_INFO(ctypes.Structure):
    _fields_ = [("wProcessorArchitecture", ctypes.c_ushort),
                 ("wReserved", ctypes.c_ushort),
                 ("dwPageSize", ctypes.c_ulong),
                 ("lpMinimumApplicationAddress", ctypes.c_void_p),
                 ("lpMaximumApplicationAddress", ctypes.c_void_p),
                 ("dwActiveProcessorMask", ctypes.c_size_t),
                 ("dwNumberOfProcessors", ctypes.c_ulong),
                 ("wProcessorType", ctypes.c_ulong),
                 ("dwAllocationGranularity", ctypes.c_size_t),
                 ("wProcessorLevel", ctypes.c_ushort),
                 ("wProcessorRevision", ctypes.c_ushort)]


def cpu_count():
    si = SYSTEM_INFO()
    try:
        ctypes.windll.kernel32.GetNativeSystemInfo(ctypes.byref(si))
    except Exception:
        return 4
    return max(1, si.dwNumberOfProcessors)


def per_core_max_mhz(n):
    """CallNtPowerInformation(11) 一次拿每核基频"""
    class PPI(ctypes.Structure):
        _fields_ = [("Number", ctypes.c_ulong), ("MaxMhz", ctypes.c_ulong),
                    ("CurrentMhz", ctypes.c_ulong), ("MhzLimit", ctypes.c_ulong),
                    ("MaxIdleState", ctypes.c_ulong), ("CurrentIdleState", ctypes.c_ulong)]
    buf = (PPI * n)()
    try:
        if ctypes.windll.powrprof.CallNtPowerInformation(11, None, 0, buf, ctypes.sizeof(buf)) != 0:
            return None
        return [max(int(c.MaxMhz), 1) for c in buf]
    except Exception:
        return None


CACHE_DIRS = [
    ("D3D",     lambda: os.path.join(os.environ.get("LOCALAPPDATA", ""), "D3DCache")),
    ("AMD",     lambda: os.path.join(os.environ.get("LOCALAPPDATA", ""), "AMD", "DxCache")),
    ("AMD-Sys", lambda: r"C:\ProgramData\AMD\DxCache"),
    ("NVIDIA",  lambda: os.path.join(os.environ.get("LOCALAPPDATA", ""), "NVIDIA", "DXCache")),
    ("NV-GL",   lambda: os.path.join(os.environ.get("LOCALAPPDATA", ""), "NVIDIA", "GLCache")),
    ("NV-Sys",  lambda: r"C:\ProgramData\NVIDIA Corporation\NV_Cache"),
    ("Intel",   lambda: os.path.join(os.environ.get("LOCALAPPDATA", ""), "Intel", "ShaderCache")),
]


class CacheWatcher:
    """
    着色器缓存目录扫描器（自适应降频，避免大目录拖慢轮询）。

    【性能】
    · 用 os.scandir 递归替代 os.walk + 逐文件 os.stat：
      Windows 上 DirEntry.stat() 直接复用目录枚举时拿到的元数据，
      省掉每个文件一次 CreateFile+GetFileSize，扫描耗时降一个数量级。
    · 单次扫描有耗时上限（SCAN_BUDGET_MS）：超时立刻返回当前结果，
      绝不让一次超大目录把硬件轮询线程拖住。
    · _last_result 改为**实例变量**：原先是类变量，多个实例共享同一份
      缓存结果（高危陷阱：换目标时读到上一个实例的旧值）。
    """

    SCAN_BUDGET_MS = 250.0

    def __init__(self):
        self.dirs = []
        for tag, fn in CACHE_DIRS:
            try:
                p = fn()
                if p and os.path.isdir(p):
                    self.dirs.append((tag, p))
            except Exception:
                pass
        self.snap = {}
        self._min_interval = 1.5
        self._last_scan = 0.0
        self._last_result = {}

    def _scan_dir(self, path, deadline):
        """os.scandir 递归统计 (文件数, 总字节)；超过 deadline 立刻返回部分结果"""
        files = 0
        size = 0
        stack = [path]
        timed_out = False
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                files += 1
                                size += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
            except OSError:
                continue
            if time.time() > deadline:
                timed_out = True
                break
        return files, size, timed_out

    def scan(self, force=False):
        now = time.time()
        if not force and (now - self._last_scan) < self._min_interval:
            return self._last_result
        self._last_scan = now
        t0 = time.time()
        deadline = t0 + self.SCAN_BUDGET_MS / 1000.0
        out = {}
        tot_new_files = 0
        tot_new_mb = 0.0
        timed_out = False
        for tag, path in self.dirs:
            files = 0
            size = 0
            try:
                files, size, to = self._scan_dir(path, deadline)
                timed_out = timed_out or to
            except Exception:
                pass
            prev = self.snap.get((tag, path))
            new_files = 0
            new_mb = 0.0
            if prev is not None:
                new_files = max(0, files - prev[0])
                new_mb = max(0.0, (size - prev[1]) / 1048576.0)
            self.snap[(tag, path)] = (files, size, time.time())
            out[tag] = {"files": files, "mb": size / 1048576.0,
                        "new_files": new_files, "new_mb": new_mb}
            tot_new_files += new_files
            tot_new_mb += new_mb
            if timed_out:
                break
        out["_scan_ms"] = (time.time() - t0) * 1000.0
        out["_tot_new_files"] = tot_new_files
        out["_tot_new_mb"] = tot_new_mb
        out["_timeout"] = timed_out
        # 自适应：一次扫描超过 300ms 说明目录很大，拉长间隔
        if out["_scan_ms"] > 300:
            self._min_interval = min(5.0, self._min_interval * 1.5)
        elif out["_scan_ms"] < 60:
            self._min_interval = max(1.5, self._min_interval * 0.9)
        self._last_result = out
        return out


# ==============================================================================
# §9  CpuPowerProbe —— CPU 封装功率（v1.0.0 新增，给 CPU 页的频率/功率双折线供数）
# ==============================================================================
class CpuPowerProbe:
    """
    CPU 封装功率（W）读取器。**只给 UI 画走势用**，不参与任何归因判定。

    【取数顺序】
      ① PDH 实测：`\\Power Meter(*)\\Power`、`\\Energy Meter(*)\\Power`、
         `\\Processor Power(*)\\Power` —— 笔记本 / 部分品牌机 / 带能量计的设备才有；
      ② 估算（绝大多数台式机的现实）：Windows 没有公开的 CPU 封装功率计数器，
         RAPL 的 MSR 0x611 需要 ring0 驱动，本工具不装驱动、绝不高权限读 MSR，
         因此退化为「按频率 + 占用率推算」：

             P ≈ P_idle + (P_max - P_idle) · (f / f_max)^1.3 · (util / 100)

         P_idle = 0.15·P_max，f 取全核平均频率，util 取总占用率。
         P_max 由 CPU 型号字符串粗估（U/H/HX 等移动后缀优先，其次按 i3/i5/i7/i9、
         R3/R5/R7/R9 档位），估算结果一律 `est=True`，界面上强制显示成「≈62 W」。

    【为什么要标注 ≈】功率是这台机器上最容易"编"的数字。曲线形状（频率怎么抖、
    占用怎么冲）是真实的；绝对瓦数在估算模式下只保证量级正确，绝不当实测卖。
    """

    # PDH 功率计数器候选（英文计数器名，AddEnglishCounter 不依赖系统语言）
    SPECS = (
        ("pwr_meter", r"\Power Meter(*)\Power"),
        ("pwr_energy", r"\Energy Meter(*)\Power"),
        ("pwr_proc", r"\Processor Power(*)\Power"),
    )

    # 型号 → 封装功率粗估（W）。命中即用，未命中回落到 DEFAULT_TDP。
    DEFAULT_TDP = 65.0

    def __init__(self):
        self._sess = None
        self._measured = None        # PDH 名字空间
        self._name = self._cpu_name()
        self._tdp = self._tdp_hint(self._name)
        self._closed = False
        self._open_pdh()

    # ---------------- 初始化 ----------------
    @staticmethod
    def _cpu_name():
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            try:
                val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            finally:
                winreg.CloseKey(key)
            return str(val or "").strip()
        except Exception:
            return ""

    @classmethod
    def _tdp_hint(cls, name):
        n = (name or "").lower()
        if not n:
            return cls.DEFAULT_TDP
        # 移动版后缀优先：同样叫 i7，HX 55W / H 45W / U 28W，差距很大
        if "hx" in n:
            return 55.0
        if "hs" in n:
            return 35.0
        if "h" in n.replace(" ", "")[-3:] or n.rstrip().endswith("h"):
            return 45.0
        if "u" in n.replace(" ", "")[-3:] or n.rstrip().endswith("u"):
            return 28.0
        for keys, tdp in ((("i9", "ryzen 9"), 125.0),
                          (("i7", "ryzen 7"), 95.0),
                          (("i5", "ryzen 5"), 75.0),
                          (("i3", "ryzen 3"), 60.0)):
            for k in keys:
                if k in n:
                    return tdp
        return cls.DEFAULT_TDP

    def _open_pdh(self):
        """尝试打开功率计数器；失败就只用估算（失败是常态，不报错、不重试轰炸）"""
        try:
            sess = PdhSession()
            if sess.open(self.SPECS):
                self._sess = sess
                self._measured = sess
        except Exception:
            self._sess = None
            self._measured = None

    # ---------------- 对外 ----------------
    @property
    def measured(self):
        return self._measured is not None

    @property
    def cpu_name(self):
        return self._name

    @property
    def tdp_hint(self):
        return self._tdp

    def read(self, freq_avg_mhz, freq_max_mhz, util_pct):
        """
        返回 (watts, is_estimate, source)。

        watts 为 None 表示「连估算都做不了」（没有 CPU 数据），调用方应隐藏功率线。
        """
        # ① 实测优先
        if self._sess is not None:
            try:
                self._sess.collect()
                total = 0.0
                hit = False
                for key, _ in self.SPECS:
                    for inst, v in (self._sess.array(key) or {}).items():
                        if str(inst).startswith("_"):
                            continue
                        try:
                            fv = float(v)
                        except Exception:
                            continue
                        # 负值/异常大值是计数器首采样噪声，直接丢
                        if 0.0 <= fv <= 1500.0:
                            total += fv
                            hit = True
                if hit:
                    return (total, False, "pdh")
            except Exception:
                pass
            # 计数器存在但一直读不到有效值 → 退回估算（不必每轮重试开表）
            self._sess = None
            self._measured = None

        # ② 估算
        try:
            util = max(0.0, min(100.0, float(util_pct or 0.0)))
        except Exception:
            util = 0.0
        try:
            f = float(freq_avg_mhz or 0.0)
            fmax = float(freq_max_mhz or 0.0)
        except Exception:
            f, fmax = 0.0, 0.0
        if f <= 0.0 or fmax <= 0.0:
            return (None, True, "none")
        ratio_f = max(0.0, min(1.0, f / fmax))
        pmax = self._tdp
        pidle = 0.15 * pmax
        watts = pidle + (pmax - pidle) * (ratio_f ** 1.3) * (util / 100.0)
        return (max(0.0, min(pmax, watts)), True, "estimate")

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._sess is not None:
                self._sess.close()
        except Exception:
            pass
        self._sess = None
        self._measured = None
