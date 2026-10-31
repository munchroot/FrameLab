# -*- coding: utf-8 -*-
"""
子进程「跟父进程一起死」的保险（Windows Job Object）。

【解决什么】
PresentMon 是外部 exe，它的 ETW 会话是**全局资源**。一旦 StutterTriage 被强杀
（任务管理器结束进程 / 崩溃 / 断电重启前的强制关闭），Python 侧的
`terminate()` 根本没机会执行 —— 于是：
  · 屏幕上还留着一个孤儿对话框；
  · 后台还挂着一个 PresentMon-x64.exe 在吃 CPU；
  · ETW 会话残留 Running，攒够 4 个就触发 PresentMon error 1450 静默 0 帧。

Windows 的 Job Object 带 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`：
把子进程塞进这种 Job 之后，**只要持有 Job 句柄的进程结束（正常退出 / 强杀 / 崩溃），
系统会自动把 Job 里的所有进程一起干掉**。句柄由本进程持有、永不主动关闭，
所以这条规则等价于「StutterTriage 一死，PresentMon 必死」。

【安全性】
· 纯 best-effort：任何一步失败都返回 False，调用方忽略即可，绝不影响采集主流程；
· 只把**我们亲手 Popen 出来的**子进程加进 Job，不碰系统里其他进程；
· 64 位下句柄必须用 c_void_p（默认 restype 是 c_int 会被截断成 32 位）。
"""
import ctypes
import os
import sys
from ctypes import wintypes

# ---- Job Object 相关常量 ----
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JOB_HANDLE = None          # 模块级持有：进程退出时由系统自动关闭 → 触发联动杀
_K32 = None


def _k32():
    """惰性加载 kernel32 并声明正确的参数/返回类型（64 位句柄防截断）。"""
    global _K32
    if _K32 is not None:
        return _K32
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = ctypes.c_void_p
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.SetInformationJobObject.restype = wintypes.BOOL
    k.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                          ctypes.c_void_p, wintypes.DWORD]
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.AssignProcessToJobObject.restype = wintypes.BOOL
    k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.CloseHandle.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.IsProcessInJob.restype = wintypes.BOOL
    k.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.POINTER(wintypes.BOOL)]
    _K32 = k
    return k


def _ensure_job():
    """拿到（必要时创建）全局 Job 句柄；失败返回 None。"""
    global _JOB_HANDLE
    if _JOB_HANDLE:
        return _JOB_HANDLE
    if os.name != "nt":
        return None
    try:
        k = _k32()
        h = k.CreateJobObjectW(None, None)
        if not h:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k.SetInformationJobObject(ctypes.c_void_p(h),
                                       JobObjectExtendedLimitInformation,
                                       ctypes.byref(info),
                                       ctypes.sizeof(info))
        if not ok:
            k.CloseHandle(ctypes.c_void_p(h))
            return None
        _JOB_HANDLE = h          # 故意不关闭：进程退出时系统关闭它 → 联动终止子进程
        return h
    except Exception:
        return None


def bind_kill_on_exit(pid):
    """
    把 pid 绑进「随本进程退出一起终止」的 Job。

    返回 True 表示已绑定；False 表示本机不支持 / 权限不足 / 已在别的受限 Job 里
    （Windows 8+ 支持嵌套 Job，通常没问题）。**失败绝不能影响采集**。
    """
    if os.name != "nt":
        return False
    try:
        job = _ensure_job()
        if not job:
            return False
        k = _k32()
        hp = k.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, int(pid))
        if not hp:
            return False
        try:
            return bool(k.AssignProcessToJobObject(ctypes.c_void_p(job),
                                                   ctypes.c_void_p(hp)))
        finally:
            k.CloseHandle(ctypes.c_void_p(hp))
    except Exception:
        return False


def is_in_our_job(pid):
    """自检用：该 pid 是否已在本进程的 Job 里。"""
    if os.name != "nt":
        return False
    try:
        job = _JOB_HANDLE
        if not job:
            return False
        k = _k32()
        hp = k.OpenProcess(0x1000, False, int(pid))     # PROCESS_QUERY_LIMITED_INFORMATION
        if not hp:
            return False
        try:
            res = wintypes.BOOL(0)
            ok = k.IsProcessInJob(ctypes.c_void_p(hp), ctypes.c_void_p(job), ctypes.byref(res))
            return bool(ok and res.value)
        finally:
            k.CloseHandle(ctypes.c_void_p(hp))
    except Exception:
        return False


def job_active():
    """本进程是否已成功建好 Job（测试/日志用）。"""
    return bool(_JOB_HANDLE)
