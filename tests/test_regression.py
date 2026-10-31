# -*- coding: utf-8 -*-
# 【v4.0 移植】原 _test_regression.py，未改逻辑（import StutterTriage 仍走兼容层）。
# -*- coding: utf-8 -*-
"""
回归测试：针对审计发现的两个阻断级缺陷 + 基线鲁棒性 + 时间轴单位。
"""
import csv
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PyQt6 import QtWidgets

import StutterTriage as FL

fails = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print("  [%s] %s %s" % (status, name, detail))
    if not cond:
        fails.append(name)


print("=" * 72)
print("R1) D2 回归：CSV 时间列单位自动判定")
print("=" * 72)
# 【v4.0】原实现依赖 _probe_out 里 2026-10 的真机抓包 CSV；
# 该目录已按「清理过时无用文件」删除，这里**现场合成**两份等价 CSV：
#   默认模式（TimeInSeconds = 毫秒）与 --v1 模式（TimeInSeconds = 秒）。
_FIXDIR = tempfile.mkdtemp(prefix="fl_reg_fixtures_")
p1 = os.path.join(_FIXDIR, "m_default_cxflags.csv")
p2 = os.path.join(_FIXDIR, "delta_v1.csv")


def _gen_fixture(path, n, ft_ms, time_in_ms, ft_col):
    t = 0.0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Application", "ProcessID", ft_col, "MsGPUBusy", "MsCPUBusy",
                    "TimeInSeconds"])
        for i in range(n):
            tv = t * 1000.0 if time_in_ms else t
            w.writerow(["JustCause3.exe", 100 + i, "%.4f" % ft_ms, "12.0", "6.0",
                        "%.4f" % tv])
            t += ft_ms / 1000.0


_gen_fixture(p1, 360, 16.67, True, "MsBetweenPresents")     # ≈6.0 s
_gen_fixture(p2, 720, 16.67, False, "msBetweenPresents")    # ≈12.0 s


def load_time_scale(path, ft_col, time_col, app_filter="JustCause3.exe"):
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8-sig", errors="replace")))
    rows = [r for r in rows if r.get("Application") == app_filter]
    diffs, fts = [], []
    for i in range(1, min(len(rows), 400)):
        a, b = rows[i].get(time_col), rows[i - 1].get(time_col)
        if a is None or b is None or FL.is_na(a) or FL.is_na(b):
            continue
        try:
            d = abs(float(a) - float(b))
        except Exception:
            continue
        if d <= 0:
            continue
        diffs.append(d)
        raw = rows[i].get(ft_col)
        if raw and not FL.is_na(raw):
            fts.append(float(raw))
    if not diffs or not fts:
        return None
    md = sorted(diffs)[len(diffs) // 2]
    mf = sorted(fts)[len(fts) // 2]
    ratio = md / mf if mf else 0
    if 0.5 < ratio < 2.0:
        return 1.0 / 1000.0
    if 0.0002 < ratio < 0.002:
        return 1.0
    return None


s1 = load_time_scale(p1, "MsBetweenPresents", "TimeInSeconds")
s2 = load_time_scale(p2, "msBetweenPresents", "TimeInSeconds")
print("   默认模式(TimeInSeconds=ms) scale =", s1)
print("   V1 模式(TimeInSeconds=s)  scale =", s2)
check("默认模式判定为毫秒", abs((s1 or 0) - 0.001) < 1e-9, "got=%r" % s1)
check("V1 模式判定为秒", abs((s2 or 0) - 1.0) < 1e-9, "got=%r" % s2)
for path, ftcol, expect_span in ((p1, "MsBetweenPresents", 6.0), (p2, "msBetweenPresents", 12.0)):
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8-sig", errors="replace")))
    rows = [r for r in rows if r.get("Application") == "JustCause3.exe"]
    scale = load_time_scale(path, ftcol, "TimeInSeconds")
    span = (float(rows[-1]["TimeInSeconds"]) - float(rows[0]["TimeInSeconds"])) * scale
    print("   %-28s span=%.2f s (期望 ~%.0f s)" % (os.path.basename(path), span, expect_span))
    check("%s 时间轴尺度正确" % os.path.basename(path), abs(span - expect_span) < expect_span * 0.35,
          "span=%.2f" % span)

print()
print("=" * 72)
print("R3) D1 回归：导入失败后 is_paused 必须复位（否则 UI 永久冻结）")
print("=" * 72)
app = QtWidgets.QApplication(sys.argv)
win = FL.StutterTriageWindow()
win._begin_collection()       # 【手动开始采集】先进入采集态，才谈得上"复位"
win.is_paused = True
win.btn_pause.setText("▶ 继续采集")


# 用一个不存在的文件触发「读取失败」分支；为避免无头环境下模态框阻塞，
# 这里把 QFileDialog 与 QMessageBox 的相关方法替换掉，只验证状态复位逻辑。
_orig_critical = QtWidgets.QMessageBox.critical
_orig_warning = QtWidgets.QMessageBox.warning
_orig_getopen = QtWidgets.QFileDialog.getOpenFileName
QtWidgets.QMessageBox.critical = staticmethod(lambda *a, **k: None)
QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: None)
QtWidgets.QFileDialog.getOpenFileName = staticmethod(
    lambda *a, **k: (os.path.join(_FIXDIR, "__does_not_exist__.csv"), ""))
try:
    win._import_csv()
except Exception:
    pass
QtWidgets.QMessageBox.critical = _orig_critical
QtWidgets.QMessageBox.warning = _orig_warning
QtWidgets.QFileDialog.getOpenFileName = _orig_getopen
print("   is_paused after failed import =", win.is_paused)
check("失败导入后 is_paused 复位为 False", win.is_paused is False)
check("按钮文本复位", "暂停采集" in win.btn_pause.text(), win.btn_pause.text())
win.hw_engine.stop()
if win.pm_bridge:
    win.pm_bridge.stop()

print()
print("=" * 72)
print("R4) D5 回归：基线鲁棒性（尖峰占比 30% 时基线不得被顶穿）")
print("=" * 72)
store = FL.FrameStore()
rng = np.random.default_rng(11)
frames = []
t = 0.0
for i in range(1200):
    ft = 16.67 + float(rng.normal(0, 0.3))
    # 30% 的帧是尖峰（连续卡顿簇）
    if i % 10 in (0, 1, 2):
        ft = 60.0
    frames.append((t, ft))
    t += ft / 1000.0
for i, (tt, ft) in enumerate(frames):
    snap = FL.HardwareSnapshot(seq=i + 1)
    snap.ts = 1000.0 + tt
    snap.cpu_usage = [30.0] * 8
    snap.cpu_freq = [5100.0] * 8
    snap.cpu0_usage = 30.0
    snap.cpu_total = 30.0
    snap.vram_total = 8.0 * (1 << 30)
    snap.vram_dedicated = 3.0 * (1 << 30)
    snap.vram_nonlocal = 0
    store.add_snapshot(snap)
for i, (tt, ft) in enumerate(frames):
    store.add_frame(FL.FrameRecord(i + 1, tt, 1000.0 + tt, "G.exe", 1, ft, ft * 0.9, ft * 0.4))
store.update_baseline(list(store.frames))
b = store.baseline
print("   尖峰占比 30%% 时基线 = %.2f ms (真值 16.67)  偏差 %+.1f%%"
      % (b.base_frametime, (b.base_frametime - 16.67) / 16.67 * 100))
check("基线偏差 < 15%", abs(b.base_frametime - 16.67) / 16.67 < 0.15,
      "base=%.2f" % b.base_frametime)

print()
print("=" * 72)
print("R5) D6 回归：顶部看板必须报病因而非症状")
print("=" * 72)
store2 = FL.FrameStore()
for i in range(300):
    snap = FL.HardwareSnapshot(seq=i + 1)
    snap.ts = 1000.0 + i * 0.016
    snap.cpu_usage = [30.0] * 8
    snap.cpu_freq = [5100.0] * 8
    snap.cpu0_usage = 30.0
    snap.cpu_total = 30.0
    snap.vram_total = 8.0 * (1 << 30)
    snap.vram_dedicated = 3.0 * (1 << 30)
    snap.vram_nonlocal = 0
    store2.add_snapshot(snap)
for i in range(300):
    ft = 58.0 if i == 250 else 16.7
    gpu = ft * 0.15 if i == 250 else ft * 0.92
    store2.add_frame(FL.FrameRecord(i + 1, i * 0.016, 1000.0 + i * 0.016, "G.exe", 1, ft, gpu, ft * 0.5))
store2.update_baseline(list(store2.frames))
worst = list(store2.frames)[250]
rows = FL.SnapshotDiffInspector.inspect(worst, store2)
verdict = FL.SnapshotDiffInspector.banner_verdict(rows, worst)
print("   看板结论:", verdict)
check("看板指向 GPU 饥饿（病因）而非「恶性掉帧」（症状）", "GPU" in verdict, verdict)

print()
print("=" * 72)
print("R6) 帧-硬件快照时间对齐（尖峰帧必须绑到尖峰快照）")
print("=" * 72)
store3 = FL.FrameStore()
# 快照与帧使用同一时间轴（快照 0.05s 间隔，帧落在两次快照之间），
# 这样「尖峰帧」必须绑定到「尖峰快照」。
SPIKE_SEQ = 121
for i in range(200):
    snap = FL.HardwareSnapshot(seq=i + 1)
    snap.ts = 1000.0 + i * 0.05
    spike = (i + 1 == SPIKE_SEQ)
    snap.cpu0_usage = 97.0 if spike else 30.0
    snap.vram_nonlocal = int(1.2 * (1 << 30)) if spike else 0
    store3.add_snapshot(snap)
for i in range(200):
    # 帧 i 发生在快照 i 之后、快照 i+1 之前 → 应绑定快照 i+1
    store3.add_frame(FL.FrameRecord(i + 1, i * 0.05, 1000.0 + i * 0.05 + 0.01, "G.exe", 1, 16.7))
sp = list(store3.frames)[SPIKE_SEQ - 1]
sn = store3.snapshot_for(sp.hw_seq)
check("尖峰帧绑定到尖峰快照", sn is not None and sn.cpu0_usage > 90,
      "hw_seq=%s cpu0=%s" % (sp.hw_seq, sn.cpu0_usage if sn else None))
check("所有帧都绑定到快照", all(f.hw_seq >= 0 for f in store3.frames))
# 不晚于本帧：帧 i 的 wall_ts 必须 >= 所绑定快照的 ts
bad = 0
for f in store3.frames:
    s = store3.snapshot_for(f.hw_seq)
    if s is None or s.ts > f.wall_ts + 1e-9:
        bad += 1
check("绑定快照时间不晚于帧时间", bad == 0, "violations=%d" % bad)

print()
print("=" * 72)
print("R7) 有界队列背压：队列满时生产者不得阻塞")
print("=" * 72)
bq = FL.BoundedQueue(maxsize=10)
for i in range(100):
    bq.put(("frame", i))
print("   dropped =", bq.take_dropped())
check("溢出被计数", bq.take_dropped() == 0)
items = []
try:
    while True:
        items.append(bq.get_nowait()[1])
except Exception:
    pass
check("队列未超上限且保留了最新数据", len(items) <= 10, "n=%d" % len(items))
check("保留了最新帧", items and items[-1] == 99, "last=%s" % (items[-1] if items else None))

print()
print("=" * 72)
print("R8) v4.1 回归：桌面空载误报「爆显存」必须消失 / 真溢出仍要 DANGER")
print("=" * 72)
TOTAL_VRAM = 12.0 * (1 << 30)


def _vram_store(ded_ratio, base_nonlocal_mb, spike_nonlocal_mb):
    """80 轮平稳快照把基线喂稳，再追加 1 个异常快照 + 1 个异常帧。"""
    st = FL.FrameStore()
    for i in range(80):
        s = FL.HardwareSnapshot(seq=i + 1)
        s.ts = 1000.0 + i * 0.05
        s.cpu_usage = [30.0] * 8
        s.cpu_freq = [5100.0] * 8
        s.cpu0_usage = 30.0
        s.cpu_total = 30.0
        s.gpu_eng = {"3D": 70.0}
        s.vram_total = TOTAL_VRAM
        s.vram_dedicated = TOTAL_VRAM * ded_ratio
        s.vram_nonlocal = int(base_nonlocal_mb * 1048576)
        st.add_snapshot(s)
    for i in range(80):
        st.add_frame(FL.FrameRecord(i + 1, i * 0.05, 1000.0 + i * 0.05 + 0.01, "G.exe", 1, 16.7))
    st.update_baseline(list(st.frames))
    ts = 1000.0 + 80 * 0.05
    s = FL.HardwareSnapshot(seq=81)
    s.ts = ts
    s.cpu_usage = [30.0] * 8
    s.cpu_freq = [5100.0] * 8
    s.cpu0_usage = 30.0
    s.cpu_total = 30.0
    s.gpu_eng = {"3D": 70.0}
    s.vram_total = TOTAL_VRAM
    s.vram_dedicated = TOTAL_VRAM * ded_ratio
    s.vram_nonlocal = int(spike_nonlocal_mb * 1048576)
    st.add_snapshot(s)
    f = FL.FrameRecord(81, 80 * 0.05, ts + 0.01, "G.exe", 1, 16.7)
    st.add_frame(f)
    return st, f


def _vram_row(store, frame):
    for r in FL.SnapshotDiffInspector.inspect(frame, store):
        if r.dim.startswith("显存溢出"):
            return r
    return None


_st, _f = _vram_store(0.15, 0.0, 700.0)
_r = _vram_row(_st, _f)
print("   桌面 15% 显存 + 非本地 700MB ->", _r.severity)
check("桌面只用了 15% 显存绝不再报爆显存", _r.severity != "DANGER", _r.verdict)

_st, _f = _vram_store(0.90, 0.0, 700.0)
_r = _vram_row(_st, _f)
print("   专用 90% + 非本地 700MB(+700 基线) ->", _r.severity)
check("真溢出（≥88% 且 ≥256MB 且增量 ≥64MB）仍判 DANGER",
      _r.severity == "DANGER", _r.verdict)

print()
print("=" * 72)
print("R9) v4.1 回归：顿挫双门控（局部中位数 + 85FPS 绝对豁免）")
print("=" * 72)
_noise = []
for i in range(200):
    _ft = 1000.0 / 100.0
    if i == 100:
        _ft = 1000.0 / 98.0
    _noise.append(FL.FrameRecord(i, i * 0.01, 0.0, "g.exe", 1, _ft))
_sn = FL.window_stats(_noise)
check("100→98 帧不算顿挫（噪声不得生成病历卡）", _sn["dropped"] == 0,
      "dropped=%d stutter_now=%s" % (_sn["dropped"], _sn["stutter_now"]))
check("window_stats 暴露 stutter_now / smooth / flags",
      _sn["stutter_now"] is False and _sn["smooth"] is True
      and len(_sn["flags"]) == _sn["window_n"],
      "flags=%d window_n=%d" % (len(_sn["flags"]), _sn["window_n"]))
_real = []
for i in range(200):
    _ft = 1000.0 / 100.0
    if i == 100:
        _ft = 20.0                     # 100 → 50 帧，帧耗时跳变 10ms
    _real.append(FL.FrameRecord(i, i * 0.01, 0.0, "g.exe", 1, _ft))
_sr = FL.window_stats(_real)
check("100→50 帧（跌幅+跳变双达标）判为顿挫", _sr["dropped"] == 1,
      "dropped=%d" % _sr["dropped"])

print()
print("=" * 72)
if fails:
    print("FAILED CHECKS: %s" % fails)
    sys.exit(1)
print("ALL REGRESSION TESTS PASSED")
print("=" * 72)
