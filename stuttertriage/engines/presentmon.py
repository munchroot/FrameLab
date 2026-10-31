# -*- coding: utf-8 -*-

"""PresentMon 定位/能力探测 + PresentMonBridge 采集线程。"""

import os
import csv
import glob
import time
import math
import shutil
import subprocess
import threading
from collections import deque
import numpy as np
from ..etw import EtwSessionManager
from ..models import FrameRecord
from ..utils import DIAG, is_na, safe_float

# ---- 主题/阈值热更新绑定（本模块持有可变副本）----
from ..consts import bind_globals as _bind_const_globals
from ..paths import app_dir, bundle_dir
_bind_const_globals(globals())



# ==============================================================================
# §6  PresentMonBridge —— 独立采集线程
# ==============================================================================
def _presentmon_search_roots():
    """候选搜索根目录（不写死任何具体版本号或盘符）"""
    script_dir = app_dir()
    roots = [
        script_dir,
        os.path.join(script_dir, "PresentMon"),
        os.path.join(script_dir, "tools"),
        os.path.join(script_dir, "external"),
        os.path.join(script_dir, "bin"),
    ]
    try:
        drives = []
        if os.name == "nt":
            import string
            for letter in string.ascii_uppercase:
                root = letter + ":\\"
                if os.path.isdir(root):
                    drives.append(root)
        else:
            drives = ["/"]
    except Exception:
        drives = []
    for d in drives:
        roots.append(os.path.join(d, "Program Files"))
        roots.append(os.path.join(d, "Program Files (x86)"))
        roots.append(os.path.join(d, "ProgramData"))
    home = os.path.expanduser("~")
    for sub in ("Desktop", "Downloads", "Tools", "Applications", ".local", "bin"):
        roots.append(os.path.join(home, sub))
    return roots


_PM_CAP_CACHE = {}


def detect_presentmon_capabilities(exe_path):
    """
    探测 PresentMon 二进制支持哪些命令行选项。

    【实测】本机同时存在两个 PresentMon：
      · CapFrameX 自带的 CX-PresentMon-2.6.1-x64.exe —— 支持 --v2_metrics /
        --set_circular_buffer_size / --qpc_time_ms
      · AMD Adrenalin 自带的 PresentMon-x64.exe —— **不支持**
        --v2_metrics 与 --set_circular_buffer_size（unrecognized option 会
        让 PresentMon 以 rc=1 直接退出，整条采集链静默失效）
    因此不能对所有二进制套用同一套参数，必须先探测能力再组装命令行。
    """
    if not exe_path:
        return set()
    key = os.path.abspath(exe_path)
    if key in _PM_CAP_CACHE:
        return _PM_CAP_CACHE[key]
    caps = set()
    try:
        out = subprocess.run([exe_path, "--help"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20,
                             creationflags=subprocess.CREATE_NO_WINDOW
                             if os.name == "nt" else 0)
        blob = ((out.stdout or "") + (out.stderr or "")).lower()
        for opt in ("--v2_metrics", "--v1_metrics", "--set_circular_buffer_size",
                    "--no_track_input", "--qpc_time_ms", "--session_name",
                    "--stop_existing_session", "--output_stdout", "--no_console_stats",
                    "--timed", "--terminate_after_timed", "--track_frame_type",
                    "--track_app_timing", "--write_display_metadata", "--track_pc_latency"):
            if opt in blob:
                caps.add(opt)
    except Exception:
        pass
    _PM_CAP_CACHE[key] = caps
    return caps


def find_presentmon_binary(explicit=None):
    """
    多路径探测 PresentMon.exe（完全可移植，不含任何硬编码盘符/版本号）。

    【评审】原实现把某个桌面路径写死，换台机器就找不到；版本号也写死。
    现在改为：显式指定 -> 环境变量 STUTTERTRIAGE_PRESENTMON -> 程序目录及其子目录
    -> **EXE 内置副本（PyInstaller `_MEIPASS/PresentMon`）** -> 各盘符常见安装
    位置（有限深度 glob） -> PATH。

    【v1.0.5】随包 PresentMon：EXE 里已经打进了 `PresentMon/PresentMon-x64.exe`，
    解包目录（bundle_dir）必须参与查找 —— 否则把 EXE 拷给别人、对方没装
    PresentMon 时会被误判为「找不到探针」，直接退化成模拟数据。
    """
    if explicit:
        return explicit if os.path.isfile(explicit) else None

    env = os.environ.get("STUTTERTRIAGE_PRESENTMON", "").strip()
    if env and os.path.isfile(env):
        return env

    script_dir = app_dir()
    bdir = bundle_dir()
    roots = [script_dir, os.path.join(script_dir, "PresentMon"),
             os.path.join(script_dir, "tools"), os.path.join(script_dir, "external"),
             os.path.join(script_dir, "bin"),
             bdir, os.path.join(bdir, "PresentMon")]
    seen = set()
    for root in roots:
        try:
            key = os.path.normcase(os.path.normpath(root))
        except Exception:
            key = root
        if key in seen:
            continue
        seen.add(key)
        try:
            if not os.path.isdir(root):
                continue
            for h in sorted(glob.glob(os.path.join(root, "*PresentMon*.exe"))):
                if os.path.isfile(h):
                    return h
        except Exception:
            pass

    for root in _presentmon_search_roots():
        try:
            if not os.path.isdir(root):
                continue
            for pat in ("*PresentMon*.exe", "*/*PresentMon*.exe",
                        "*/*/*PresentMon*.exe", "*/*/*/*PresentMon*.exe"):
                for h in sorted(glob.glob(os.path.join(root, pat))):
                    try:
                        if os.path.isfile(h) and os.path.getsize(h) > 100 * 1024:
                            return h
                    except Exception:
                        continue
        except Exception:
            pass

    p = shutil.which("PresentMon.exe") or shutil.which("PresentMon")
    if p:
        return p
    return None


class PresentMonBridge(threading.Thread):
    """
    静默 PresentMon 桥接线程。

    关键设计（全部来自实测结论）：
      · 使用**默认 metric 模式**（不加 --v1/--v2），该模式才同时含
        MsBetweenPresents / MsCPUBusy / MsGPUBusy / MsGPULatency / MsGPUTime。
      · **不加 --process_name**：非管理员下 `<unknown>` 进程永远无法被命令行命中，
        改为采集后按 Application + ProcessID 自行过滤。
      · **绝不使用 --dont_restart_as_admin**：该选项不被识别，会让 PresentMon
        以 rc=1 立即退出。
      · 使用唯一 session 名 + `--stop_existing_session` 自愈 + `logman stop` 兜底，
        杜绝 error 1450 导致的「静默 0 帧」。
      · `--set_circular_buffer_size 4096` 缓解 ETW 丢包。
    """

    def __init__(self, out_queue, target_process=None, exe_path=None):
        super().__init__(daemon=True, name="PresentMonBridge")
        self.out_q = out_queue
        self.target_process = target_process
        self.exe_path = exe_path
        self._stop_evt = threading.Event()
        self.proc = None
        self.etw = EtwSessionManager()
        self.simulation_mode = False
        self.frames_seen = 0
        self.last_error = ""
        self._qpc_base = None

    def stop(self, wait=True, timeout=3.0):
        """
        停止采集。

        【评审 P0】原实现在 UI 线程里直接 terminate() + wait(3)，
        会让界面最多卡住 3 秒；且 `logman stop` 最长可再耗 20 秒。
        现在默认 **不等待**（wait=False），由调用方在需要时显式等待；
        ETW 清理改为后台执行，绝不阻塞 UI。
        """
        self._stop_evt.set()
        p = self.proc
        if p:
            try:
                p.terminate()
            except Exception:
                pass
            if wait:
                try:
                    p.wait(timeout=timeout)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
                    try:
                        p.wait(timeout=1.0)
                    except Exception:
                        pass
        # 关键兜底：进程退出后 ETW session 必然残留，必须显式 stop。
        # 放到后台线程，避免 logman 阻塞 UI。
        try:
            threading.Thread(target=self.etw.dispose, name="EtwCleanup",
                             daemon=True).start()
        except Exception:
            pass
        # 采集线程回收（带超时，绝不无限等待）
        if wait and self.is_alive() and threading.current_thread() is not self:
            try:
                self.join(timeout=timeout)
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _status(self, ok, msg):
        self.out_q.put(("status", (ok, msg)))

    def _run_simulation(self):
        """无 PresentMon / 权限不足时的降级：生成高保真模拟帧流（硬件仍为真实探针）"""
        self.simulation_mode = True
        t0 = time.time()
        idx = 0
        base_ms = 8.33  # ~120 FPS
        rng = np.random.default_rng(20261023)
        while not self._stop_evt.is_set():
            idx += 1
            now = time.time()
            cycle = idx % 420
            if cycle == 150:        # 显存溢出型：GPU 忙时骤降（GPU 饥饿）
                ft = base_ms * 6.5
                gpu = ft * 0.12
                cpu = ft * 0.85
            elif cycle == 280:      # 着色器编译型：GPU 忙时占满
                ft = base_ms * 4.2
                gpu = ft * 0.97
                cpu = ft * 0.30
            elif cycle == 340:      # CPU0 拥堵型：GPU 空闲但 CPU 卡死
                ft = base_ms * 3.8
                gpu = ft * 0.20
                cpu = ft * 0.92
            else:
                ft = max(2.0, base_ms + float(rng.normal(0, 0.35)))
                gpu = ft * float(rng.uniform(0.86, 0.97))
                cpu = ft * float(rng.uniform(0.35, 0.62))
            rec = FrameRecord(idx, now - t0, now, "GameSimulation.exe", 9999, ft, gpu, cpu)
            self.frames_seen += 1
            self.out_q.put(("frame", rec))
            self._stop_evt.wait(ft / 1000.0)

    # ------------------------------------------------------------------
    def run(self):
        exe = self.exe_path
        if exe == "__FORCE_SIMULATION__":
            self._status(False, "已切换至模拟测试流模式（真实 PresentMon 采集已停止）")
            self._run_simulation()
            return
        exe = exe or find_presentmon_binary()
        if not exe or not os.path.isfile(exe):
            self._status(False, "未找到 PresentMon.exe —— 已切换至模拟测试流 + 纯硬件监控模式"
                                "（可导入真实 CSV 回放）")
            self._run_simulation()
            return

        # 启动前清理本工具的历史残留 ETW 会话 —— 直接消除 error 1450 静默失败
        try:
            stale = self.etw.cleanup_stale()
            if stale:
                self._status(True, "已清理 %d 个历史残留 ETW 会话" % len(stale))
        except Exception:
            pass

        # 【自适应】不同 PresentMon 版本支持的选项不同，
        # 传了不认识的选项会让它以 rc=1 直接退出（整个采集静默失效）。
        # 实测：AMD Adrenalin 自带的 PresentMon 不支持 --set_circular_buffer_size，
        # 而 CapFrameX 的 CX 构建支持 —— 因此必须先探测能力再组装命令行。
        caps = detect_presentmon_capabilities(exe)
        cmd = [exe]
        if "--stop_existing_session" in caps:
            cmd.append("--stop_existing_session")
        if "--session_name" in caps:
            cmd += ["--session_name", self.etw.session_name]
        if "--output_stdout" in caps:
            cmd.append("--output_stdout")
        if "--no_track_input" in caps:
            cmd.append("--no_track_input")
        if "--qpc_time_ms" in caps:
            cmd.append("--qpc_time_ms")
        if "--no_console_stats" in caps:
            cmd.append("--no_console_stats")
        if "--set_circular_buffer_size" in caps:
            cmd += ["--set_circular_buffer_size", str(PM_BUFFER_SIZE)]
        if not caps:
            # 探测不到 --help（例如二进制异常），退回最小可用命令组合
            cmd += ["--output_stdout", "--session_name", self.etw.session_name]

        # 【竞态修复】在真正拉起子进程**之前**再查一次停止标志。
        # 原实现存在窗口：stop() 先到 → run() 随后仍然 Popen 出 PresentMon，
        # 结果子进程既没被 terminate（self.proc 当时还是 None），
        # 又留下 ETW 会话 —— 实测 100 次启停会残留 7 个孤儿 PresentMon 进程
        # 与 3 个残留 ETW 会话。
        if self._stop_evt.is_set():
            return

        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as ex:
            self._status(False, "启动 PresentMon 失败: %s —— 已切换至模拟模式" % ex)
            self._run_simulation()
            return

        # 【v4.3】把 PresentMon 绑进「父进程死 → 子进程必死」的 Job：
        # 用户用任务管理器强杀 / 程序崩溃时 terminate() 根本没机会执行，
        # 以前会留下孤儿 PresentMon（后台吃 CPU）和残留 ETW 会话（→ error 1450）。
        # 纯 best-effort，失败不影响采集。
        try:
            from .. import procguard
            procguard.bind_kill_on_exit(self.proc.pid)
        except Exception:
            pass

        # Popen 之后立刻再查一次：stop() 可能正好落在 Popen 与下面之间
        if self._stop_evt.is_set():
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2.0)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            try:
                self.etw.dispose()
            except Exception:
                pass
            return

        self._status(True, "PresentMon 采集引擎就绪 [%s]" % os.path.basename(exe))
        self._parse_stream()

    # ------------------------------------------------------------------
    def _parse_stream(self):
        """
        解析 PresentMon stdout CSV 流。
        · 首行才是表头（前面可能有 warning/Started/Stopped 等杂行）
        · **按列名解析**，不按下标（列数随 flag 变 18/19/21/26/32）
        · NA 视为缺失，不能当 0（否则污染基线）
        """
        col = {}
        header_seen = False
        t0 = time.time()
        idx = 0
        lost_reported = False
        self._qpc_base = None
        self._wall_offset = None      # QPC → 墙钟 的偏移（滑动最小值法）
        self._stderr_lines = deque(maxlen=200)
        self._stderr_thread = None

        # ---------------------------------------------------------------
        # 【死锁修复】stderr 必须由独立线程持续消费。
        # 旧实现在主循环里调用阻塞式 readline()：stderr 管道写满后
        # PresentMon 会阻塞在写 stderr，从而停止写 stdout；
        # 而父线程正卡在 stdout.readline() 上 —— 双方互等，采集永久停摆。
        # ---------------------------------------------------------------
        def _stderr_reader():
            try:
                for ln in self.proc.stderr:
                    ln = (ln or "").strip()
                    if not ln:
                        continue
                    self._stderr_lines.append(ln)
                    low = ln.lower()
                    if "1450" in low:
                        self.last_error = "ETW 会话资源耗尽 (1450)"
                        self._status(False, "⚠ PresentMon 无法启动 ETW 会话 (error 1450)："
                                            "本机残留了过多 ETW 会话。已尝试清理，"
                                            "请关闭其它帧率采集工具后重试。")
                    elif "events were lost" in low:
                        if not lost_reported:
                            lost_reported = True
                            self._status(False, "⚠ 检测到 ETW 事件丢失（数据可能不完整），"
                                                "建议以管理员身份运行 StutterTriage")
                    elif "elevated privilege" in low:
                        self._status(True, "⚠ PresentMon 未以管理员权限运行："
                                           "仅能完整追踪当前账户的进程")
            except Exception:
                pass

        self._stderr_thread = threading.Thread(target=_stderr_reader,
                                               name="PMStderrReader", daemon=True)
        self._stderr_thread.start()

        while not self._stop_evt.is_set():
            line = self.proc.stdout.readline()
            if line == "":
                break
            line = line.strip()
            if not line:
                continue
            low = line.lower()
            if low.startswith(("warning", "error", "started recording", "stopped recording")):
                if low.startswith("error") and "1450" in low:
                    self.last_error = "ETW 1450"
                continue

            if not header_seen:
                if "," not in line:
                    continue
                headers = [c.strip() for c in line.split(",")]
                col = {name: i for i, name in enumerate(headers)}
                header_seen = True
                self._status(True, "PresentMon 已输出表头 (%d 列) —— 开始实时采集" % len(headers))
                continue

            # 【统一解析】与离线导入共用同一套字段识别 + 校验逻辑
            parts = next(csv.reader([line]), None)
            if not parts or len(parts) < 3:
                continue
            try:
                def get(name):
                    i = col.get(name)
                    if i is None or i >= len(parts):
                        return None
                    return parts[i]

                app = (get("Application") or "").strip() or "<unknown>"
                pid_s = (get("ProcessID") or "0").strip()
                pid = int(pid_s) if pid_s.isdigit() else 0

                ft_raw = get("MsBetweenPresents")
                if ft_raw is None:
                    ft_raw = get("msBetweenPresents")
                if ft_raw is None:
                    ft_raw = get("FrameTime")
                if ft_raw is None or is_na(ft_raw):
                    continue
                frametime = float(ft_raw)
                # 【有限值校验】NaN / inf / 非正值必须丢弃，
                # 否则会污染基线并产生荒谬的 FPS。
                if not math.isfinite(frametime) or frametime <= 0.0:
                    continue

                gpu_raw = get("MsGPUBusy")
                if gpu_raw is None:
                    gpu_raw = get("msGPUActive")
                if gpu_raw is None:
                    gpu_raw = get("GPUBusy")
                cpu_raw = get("MsCPUBusy")
                if cpu_raw is None:
                    cpu_raw = get("CPUBusy")
                gpu_busy = safe_float(gpu_raw, 0.0)
                cpu_busy = safe_float(cpu_raw, 0.0)

                idx += 1
                now = time.time()
                # 时间轴：优先用 PresentMon 自带的呈现时刻（--qpc_time_ms 下为
                # CPUStartQPCTimeInMs，单位毫秒），退化时才用墙钟。
                # 【实测】默认模式 TimeInSeconds 的单位是**毫秒**，V1 模式是**秒**，
                # 因此这里绝不使用 TimeInSeconds，直接用 QPC 列或墙钟。
                t_ms = None
                for qcol in ("CPUStartQPCTimeInMs", "CPUStartTimeInMs", "CPUStartQPCTime"):
                    raw = get(qcol)
                    if raw is not None and not is_na(raw):
                        t_ms = safe_float(raw, 0.0)
                        break
                if t_ms and t_ms > 0:
                    # QPC 列是绝对计数，需要用首帧做基准并换算成相对秒
                    if self._qpc_base is None:
                        self._qpc_base = t_ms
                    rec_t = (t_ms - self._qpc_base) / 1000.0
                    # 【时间对齐修正】管道有缓冲，Python 读到这一行时可能已过去
                    # 几百毫秒。用「滑动最小偏移」把 QPC 映射回真实墙钟：
                    # 偏移 = 墙钟 - QPC，最小值对应延迟最小的那一帧。
                    off = now - t_ms / 1000.0
                    if self._wall_offset is None or off < self._wall_offset:
                        self._wall_offset = off
                    wall_ts = self._wall_offset + t_ms / 1000.0
                else:
                    rec_t = now - t0
                    wall_ts = now
                rec = FrameRecord(idx, rec_t, wall_ts, app, pid, frametime, gpu_busy, cpu_busy)
                self.frames_seen += 1
                self.out_q.put(("frame", rec))
            except Exception as ex:
                # 单行解析失败是常态（脏数据），只计数不刷屏
                DIAG.report("PresentMonBridge.parse_line", ex)
                continue
        try:
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        # 收尾：无论正常退出还是异常，都必须清理 ETW 会话
        try:
            self.etw.dispose()
        except Exception:
            pass

        if self.frames_seen == 0 and not self._stop_evt.is_set():
            if self.last_error:
                self._status(False, "⚠ PresentMon 未能采集到任何帧（%s）—— 已切换至模拟模式" % self.last_error)
            else:
                self._status(False, "⚠ PresentMon 未采集到任何帧（可能是游戏未运行 / 无 DXGI Present / ETW 丢包）"
                                    "—— 已切换至模拟模式")
            self._run_simulation()
        elif self.frames_seen > 0 and not self._stop_evt.is_set():
            # 【状态可观测】PresentMon 中途退出时必须有明确提示，
            # 否则 UI 会一直显示"正在采集"却不再更新。
            err_tail = self._stderr_lines[-1] if self._stderr_lines else ""
            self._status(False, "⚠ PresentMon 意外退出（已采到 %d 帧）%s"
                             % (self.frames_seen, ("：" + err_tail) if err_tail else ""))
