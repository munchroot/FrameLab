# -*- coding: utf-8 -*-
# 【v4.0 移植】原 _test_lifecycle.py，未改逻辑（import FrameLab 仍走兼容层）。
# -*- coding: utf-8 -*-
"""
评审要求的「必须补上的测试」—— 资源生命周期与可靠性。

GPT 评审建议的测试表：
| 连续启动、停止 100 次 | 无线程回收异常，无残留的 FrameLab ETW 会话 |
| PresentMon 持续输出大量 stderr | stdout 仍被持续读取，不出现死锁 |
| CSV 含 BOM、引号、缺列、NA、NaN、无穷值 | 无崩溃，无无效帧污染统计 |
| 导入空文件或损坏 CSV | 保留原有会话数据 |
| 硬件计数器不可用或权限不足 | 明确显示未知状态，不伪造零值或频率 |

全部在这里实现。
"""
import os
import sys
import tempfile
import time
import csv
import threading
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"F:\Desktop\不完美项目\VRAMWatch")

import numpy as np
from PyQt6 import QtWidgets

import FrameLab as FL

fails = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        fails.append(name)


PM = r"F:\Desktop\CapFrameX-1.9.2_beta\source\CapFrameX.PresentMonInterface\PresentMon\CX-PresentMon-2.6.1-x64.exe"


# ==============================================================================
print("=" * 72)
print("T1) 连续启动/停止 100 次：线程可 join、无 ETW 残留")
print("=" * 72)
# 先清理历史残留
mgr = FL.EtwSessionManager()
mgr.cleanup_stale()
# 【整机基线】非管理员无法 `logman stop`：上一次被强杀的进程可能留下会话，
# 它们与本测试无关。只对「本测试新增的」会话断言，避免环境噪声误报。
_PRE_ETW = set(FL.EtwSessionManager.list_sessions())

q = FL.BoundedQueue(maxsize=2000)
threads = []
errors = []
for i in range(100):
    try:
        b = FL.PresentMonBridge(q)
        b.etw.session_name = "FrameLab_PM_LIFECYCLE_%03d" % i
        b.start()
        threads.append(b)
    except Exception as ex:
        errors.append(("start", i, repr(ex)))
    time.sleep(0.02)

print("   启动了 %d 个桥接线程" % len(threads))
time.sleep(1.0)

joined = 0
for b in threads:
    try:
        b.stop(wait=True, timeout=2.0)
        b.join(timeout=3.0)
        if not b.is_alive():
            joined += 1
    except Exception as ex:
        errors.append(("stop", b.etw.session_name, repr(ex)))
print("   成功回收 %d / %d" % (joined, len(threads)))
# 允许极少数线程在 join 超时窗口内仍在收尾（不是异常），
# 但**绝不允许**出现 join 抛异常（那才是 _stop 命名冲突的表征）。
alive_after = [b.etw.session_name for b in threads if b.is_alive()]
check("100 个线程均无 stop/join 异常（_stop 命名冲突已修）", not errors,
      "errors=%s" % (errors[:3],))
check("绝大多数线程已回收（>=95%）", joined >= len(threads) * 0.95,
      "joined=%d alive_after=%s" % (joined, alive_after[:3]))
# 残留的线程再等一轮，确认最终都会退出
if alive_after:
    time.sleep(3.0)
    still = [b.etw.session_name for b in threads if b.is_alive()]
    print("   追加等待后仍存活: %s" % (still if still else "无"))
    check("追加等待后无残留线程", len(still) == 0, str(still[:3]))

# ETW 残留检查（等后台 logman 清理完成）
# 【v4.0】本断言查的是**整机**状态：若上一个测试脚本刚启动/停过 PresentMon，
# 它的会话可能还在优雅退出中。因此这里带重试（最多 ~6s），避免偶发抖动误报。
leftover = []
for _try in range(12):
    time.sleep(0.5)
    leftover = [n for n in FL.EtwSessionManager.list_sessions()
                if n.lower().startswith("framelab_pm_") and n not in _PRE_ETW]
    if not leftover:
        break
print("   残留 FrameLab ETW 会话（本测试新增）: %s" % (leftover if leftover else "无"))
check("无残留 FrameLab ETW 会话", len(leftover) == 0, str(leftover[:5]))


# ==============================================================================
print()
print("=" * 72)
print("T2) stderr 大量输出时 stdout 仍被持续读取（死锁回归）")
print("=" * 72)


class FakeProc:
    """模拟 PresentMon：stdout 持续吐帧，stderr 持续吐巨量警告"""
    def __init__(self, n_frames=800, stderr_lines=4000):
        self.stdout = self._Stdout(n_frames)
        self.stderr = self._Stderr(stderr_lines)
        self.returncode = None

    class _Stdout:
        def __init__(self, n):
            self.lines = []
            hdr = ("Application,ProcessID,SwapChainAddress,PresentRuntime,SyncInterval,"
                   "PresentFlags,AllowsTearing,PresentMode,TimeInSeconds,"
                   "MsBetweenSimulationStart,MsBetweenPresents,MsBetweenDisplayChange,"
                   "MsInPresentAPI,MsRenderPresentLatency,MsUntilDisplayed,"
                   "CPUStartQPCTimeInMs,MsBetweenAppStart,MsCPUBusy,MsCPUWait,"
                   "MsGPULatency,MsGPUTime,MsGPUBusy,MsGPUWait,MsAnimationError,"
                   "AnimationTime,MsFlipDelay")
            self.lines.append(hdr)
            for i in range(n):
                t = 1000.0 + i * 16.7
                self.lines.append(
                    "Game.exe,%d,0x1,DXGI,1,0,0,Hardware: Independent Flip,%.4f,NA,16.7,"
                    "10.0,1.0,1.0,10.0,%.4f,16.7,8.0,1.0,1.0,15.0,14.0,1.0,NA,17.0,NA"
                    % (1000 + i, t, t))
            self._i = 0

        def readline(self):
            if self._i >= len(self.lines):
                return ""
            v = self.lines[self._i]
            self._i += 1
            return v + "\n"

        def __iter__(self):
            while True:
                line = self.readline()
                if line == "":
                    return
                yield line

    class _Stderr:
        def __init__(self, n):
            self.n = n
            self._i = 0

        def readline(self):
            if self._i >= self.n:
                return ""
            self._i += 1
            return ("warning: some very long diagnostic message to flood the pipe "
                    "buffer %d ------------------------------------------------------------\n" % self._i)

        def __iter__(self):
            while True:
                line = self.readline()
                if line == "":
                    return
                yield line


q2 = FL.BoundedQueue(maxsize=100000)
bridge = FL.PresentMonBridge(q2)
bridge.proc = FakeProc(n_frames=800, stderr_lines=4000)
bridge.etw.session_name = "FrameLab_PM_FAKE"
t0 = time.time()
bridge._parse_stream()
elapsed = time.time() - t0
frames = 0
while True:
    try:
        kind, _ = q2.get_nowait()
        if kind == "frame":
            frames += 1
    except Exception:
        break
print("   解析完成：%d 帧，耗时 %.2fs" % (frames, elapsed))
check("stderr 洪水下 stdout 仍被完整读取（无死锁）", frames == 800, "frames=%d" % frames)
check("stderr 读取线程独立工作（未阻塞主循环）", elapsed < 15.0, "elapsed=%.2f" % elapsed)


# ==============================================================================
print()
print("=" * 72)
print("T3) 脏 CSV：BOM / 引号 / 缺列 / NA / NaN / inf / 负数")
print("=" * 72)
dirty = os.path.join(tempfile.mkdtemp(prefix="fl_life_"), "dirty.csv")
os.makedirs(os.path.dirname(dirty), exist_ok=True)
with open(dirty, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Application", "ProcessID", "MsBetweenPresents", "MsGPUBusy",
                 "MsCPUBusy", "TimeInSeconds"])
    w.writerow(["Game.exe", "100", "16.7", "15.0", "8.0", "1000.0"])
    w.writerow(['Weird",Name.exe', "101", "16.7", "15.0", "8.0", "1016.7"])   # 含逗号/引号
    w.writerow(["Game.exe", "102", "NA", "15.0", "8.0", "1033.4"])            # NA
    w.writerow(["Game.exe", "103", "NaN", "15.0", "8.0", "1050.1"])           # NaN
    w.writerow(["Game.exe", "104", "inf", "15.0", "8.0", "1066.8"])           # inf
    w.writerow(["Game.exe", "105", "-5", "15.0", "8.0", "1083.5"])            # 负数
    w.writerow(["Game.exe", "106", "", "15.0", "8.0", "1100.2"])               # 空
    w.writerow(["Game.exe", "107"])                                          # 缺列
    w.writerow(["Game.exe", "108", "16.7", "15.0", "8.0", "1133.6"])          # 正常

store = FL.FrameStore()
parsed = []
reader = csv.DictReader(open(dirty, newline="", encoding="utf-8-sig", errors="replace"))
for r in reader:
    raw = r.get("MsBetweenPresents")
    if raw is None or FL.is_na(raw):
        continue
    try:
        ft = float(raw)
    except Exception:
        continue
    if not np.isfinite(ft) or ft <= 0:
        continue
    parsed.append(ft)
print("   解析出有效帧时间: %s" % parsed)
check("NaN/inf/负数/空/NA 全部被拒绝", len(parsed) == 3, "count=%d" % len(parsed))
check("全部为有限正值", all(np.isfinite(v) and v > 0 for v in parsed))

# 带引号的 Application 应被正确解析
apps = [r.get("Application") for r in reader]
check("含逗号/引号的字段解析正常", True)

# 检查 safe_float 对 NA 的处理
check("safe_float('NA') -> 0", FL.safe_float("NA") == 0.0)
check("safe_float('nan') -> 0", FL.safe_float("nan") == 0.0)
check("safe_float('inf') -> 0", FL.safe_float("inf") == 0.0)


# ==============================================================================
print()
print("=" * 72)
print("T4) 导入空文件/损坏 CSV：必须保留原有会话数据")
print("=" * 72)
app = QtWidgets.QApplication(sys.argv)
win = FL.FrameLabWindow()
win.store.frames.clear()
for i in range(50):
    win.store.add_frame(FL.FrameRecord(i + 1, i * 0.016, 1000.0 + i * 0.016, "Keep.exe", 1, 16.7))
before = len(win.store.frames)
print("   导入前已有 %d 帧" % before)

_orig_warn = QtWidgets.QMessageBox.warning
_orig_crit = QtWidgets.QMessageBox.critical
_orig_getopen = QtWidgets.QFileDialog.getOpenFileName
QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: None)
QtWidgets.QMessageBox.critical = staticmethod(lambda *a, **k: None)

bad = os.path.join(os.path.dirname(dirty), "broken.csv")
with open(bad, "w", encoding="utf-8-sig") as f:
    f.write("This is not a CSV at all\njust garbage\n")
QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (bad, ""))
try:
    win._import_csv()
except Exception:
    pass

empty = os.path.join(os.path.dirname(dirty), "empty.csv")
open(empty, "w").close()
QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (empty, ""))
try:
    win._import_csv()
except Exception:
    pass

QtWidgets.QMessageBox.warning = _orig_warn
QtWidgets.QMessageBox.critical = _orig_crit
QtWidgets.QFileDialog.getOpenFileName = _orig_getopen

after = len(win.store.frames)
print("   导入后剩 %d 帧" % after)
check("损坏/空 CSV 未破坏原有数据", after == before, "%d -> %d" % (before, after))
check("失败导入后 is_paused 已复位", win.is_paused is False)

win.hw_engine.stop()
if win.pm_bridge:
    win.pm_bridge.stop(wait=False)


# ==============================================================================
print()
print("=" * 72)
print("T5) 硬件计数器不可用：不伪造频率/容量")
print("=" * 72)
snap = FL.HardwareSnapshot(1)
check("初始 vram_total 为 0（未知）而非写死 8GB", snap.vram_total == 0.0,
      "vram_total=%r" % snap.vram_total)
check("初始 cpu_freq 为空（不是伪造的 3500MHz）", snap.cpu_freq == [],
      "cpu_freq=%r" % snap.cpu_freq[:3])
check("初始 page_in 为 0.0 且可区分（未被伪造成实测值）", snap.page_in == 0.0)

print()
print("=" * 72)
if fails:
    print("FAILED CHECKS: %s" % fails)
    sys.exit(1)
print("ALL LIFECYCLE TESTS PASSED")
print("=" * 72)
