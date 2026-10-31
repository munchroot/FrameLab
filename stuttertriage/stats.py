# -*- coding: utf-8 -*-
"""
帧率统计（纯函数，无 Qt 依赖，便于单元测试）。

呈现口径：
  · 平均帧   = 窗口内帧耗时均值换算的 FPS（等价 1000 / mean(frametime)）；
  · 最高帧   = 窗口内「第 k 大的瞬时帧率」，k = max(3, ceil(2% 窗口))：
               丢弃最多 2%（至少 3 帧）离群尖峰，避免单帧 477/2554 fps 这种
               PresentMon 加载期数据把「最高帧」和折线图 Y 轴一起带飞；
               被丢弃的原始极值仍以 `max_fps_raw` / `spike_fps` 返回，界面单独标注；
  · 最低帧   = 1% Low（帧耗时 P99 换算），避免单帧切屏 0 帧误导；
  · 丝滑度   = S / A / B / C 评级（评级门槛见 GRADE_RULES）。

【v4.1 顿挫口径 = 商业帧率软件的双重门控（改进建议书 §五）】
  · **对比基准**：严禁拿当前帧与全局最高帧（MAX FPS）对比 ——
    一律与**最近 STUTTER_WINDOW（120）帧的局部中位数帧率**对比；
  · 真顿挫（Stutter / 漏帧）必须**同时**满足：
      条件 A 相对跌幅 ≥ 35%：cur_fps <= local_median_fps * 0.65
      条件 B 单帧耗时跳变 ≥ 8.0 ms：cur_frametime - local_median_ft >= 8.0
      条件 C **绝对流畅豁免**：cur_fps >= 85.0（帧耗时 ≤ 11.7ms）一律豁免，
             绝不触发告警、绝不生成病历卡。
  · 第 0 帧恒不算顿挫（没有历史可比）。
  · 旧口径 DROP_FT_FACTOR(2.5×) / DROP_FPS_FACTOR(40%) **已废弃**：
    常量定义保留（避免外部引用报错），但不再参与任何判定。
"""
import bisect
import math

import numpy as np

# 注意：本模块**不**再用 utils.human_size —— 显存容量文案统一走 _gb_text（1 位小数 GB），
# 与改进建议书二.3 的格式逐字一致（human_size 是 2 位小数且会输出 KB/B）。

# ---- 漏帧判定参数 ----
DROP_FT_FACTOR = 2.5        # 【已废弃】旧口径：帧耗时 > 均值 × 2.5 记严重漏帧
DROP_FPS_FACTOR = 0.40      # 【已废弃】旧口径：相邻帧瞬跌 > 40% 记顿挫
STAT_WINDOW = 1000          # 统计窗口（最近 N 帧），与 FrameStore.METRIC_WINDOW 同量级

# ---- 严格顿挫（Stutter）双门控参数（改进建议书 §五）----
STUTTER_WINDOW = 120        # 局部中位数基准窗口（帧）
STUTTER_REL_FACTOR = 0.65   # 条件 A：相对局部中位数跌幅 ≥ 35%
STUTTER_FT_JUMP_MS = 8.0    # 条件 B：单帧耗时跳变下限（ms）
STUTTER_EXEMPT_FPS = 85.0   # 条件 C：绝对流畅豁免线（帧耗时 ≤ 11.7ms 一律不报）

# ---- S/A/B/C 评级门槛（平均帧下限, 漏帧占比上限 %）----
GRADE_RULES = (("S", 100.0, 0.5), ("A", 60.0, 2.0), ("B", 45.0, 5.0))

GRADE_LABEL = {
    "S": "电竞级丝滑",
    "A": "流畅游玩",
    "B": "尚可 · 偶有顿挫",
    "C": "建议降画质",
}

GRADE_HINT = {
    "S": "帧率与稳定性都是顶级水准，保持当前设置即可。",
    "A": "整体流畅，个别场景轻微顿挫，属于正常范围。",
    "B": "帧率偏低或顿挫偏多，可降低画质/分辨率改善。",
    "C": "卡顿明显，建议大幅降低画质或排查后台占用。",
}


def _fts(frames):
    if not frames:
        return np.zeros(0, dtype=np.float64)
    return np.fromiter((max(0.1, float(f.frametime)) for f in frames),
                       dtype=np.float64, count=len(frames))


def _fps_of(fts):
    """帧耗时数组 → 瞬时帧率数组（对 0 / 负数 / NaN 做下限保护，绝不产生 inf）"""
    fts = np.asarray(fts, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(fts > 0, 1000.0 / np.maximum(fts, 1e-9), 0.0)


# ==============================================================================
# 局部中位数帧率 —— **增量缓存**
# ------------------------------------------------------------------------------
# 调用频率：window_stats 每 ~100ms 一次、图表每 ~33ms 一次。
# 严禁每次都用 sliding_window_view 全量重算（1000 帧 × 120 窗口 = 12 万个元素，
# 每次调用都要重排一遍，实测是 UI 线程热点）。
#
# 缓存结构：{window: [fts(np.float64), med(np.float64)]}（按窗口分桶）
#   · 命中且「尾部追加 / 前缀不变」→ 只对新追加的帧算中位数（每帧 ~120 元素）；
#   · 命中且「窗口滑动」→ 位移后的中位数**整段复用**（i >= window-1 时，
#     新数组第 i 帧的窗口 == 缓存数组第 i+s 帧的窗口，值完全一致）；
#   · 都不命中 → 全量重算（并把缓存换成新数组）。
# 复用只依赖**数值相等**（不依赖对象身份），因此调用方换列表 / 换切片都安全。
# 缓存长度超过 _MED_CACHE_MAX 帧 → 直接丢弃重建，避免长会话内存无界增长。
# 线程约定：只由 UI 线程调用（与 FrameStore 的写入线程一致），故不加锁。
# ==============================================================================
_MED_CACHE = {}
_MED_CACHE_MAX = 200000      # 单桶缓存超过该帧数即丢弃重建
_MED_CAND = 8                # 滑动位移探测时最多验证的候选数
# 轻量计数：full=全量重算次数 / incr=尾部追加增量次数 / reuse=整段位移复用次数
# （仅用于单测断言「确实走了增量」，不参与业务逻辑）
_MED_STAT = {"full": 0, "incr": 0, "reuse": 0}


def _reset_median_cache():
    """清空局部中位数缓存（单测用；运行期无需调用）"""
    _MED_CACHE.clear()
    return _MED_CACHE


def _medians_range(fps, i0, i1, w, out):
    """
    把 [i0, i1) 的局部中位数写进 out（滑动窗口用**有序 list**维护）。

    逐个 np.median(...) 调用在 Python 层的开销约 12µs/帧（实测 120 帧头段要 1.65ms），
    而「bisect 维护有序窗口 + O(1) 取中位」整套只要 ~0.04ms —— 结果完全相同
    （偶数个元素时两者都是 (a+b)/2，二进制浮点下与 np.median 逐位一致）。
    """
    if i1 <= i0:
        return
    start = max(0, i0 - w + 1)
    s = sorted(fps[start:i0 + 1].tolist())
    n = fps.size
    for i in range(i0, i1):
        m = len(s)
        if m & 1:
            out[i] = s[m >> 1]
        else:
            out[i] = (s[(m >> 1) - 1] + s[m >> 1]) * 0.5
        j = i + 1
        if j >= n:
            break
        leave = j - w
        if leave >= 0:                      # 窗口已满 → 先移出 j-w
            v = float(fps[leave])
            k = bisect.bisect_left(s, v)
            if k < len(s) and s[k] == v:
                del s[k]
        bisect.insort(s, float(fps[j]))
    return out


def local_median_fps(frames, fts=None, window=STUTTER_WINDOW):
    """
    局部中位数帧率数组（与 frames / fts 等长）。

    第 i 帧的基准 = median(fps[max(0, i-window+1) : i+1])，即「最近 window 帧
    （含本帧）的帧率中位数」—— 这是商业帧率软件用来判顿挫的稳定基准，
    不会被单帧 MAX FPS 或整局峰值带偏。

    增量缓存见本模块顶部说明：前缀不变时只补算尾帧；窗口滑动时整段复用。
    """
    if fts is None:
        fts = _fts(frames)
    # copy=True：缓存持有自己的副本，调用方后续改动原数组不会污染缓存
    fts = np.array(fts, dtype=np.float64, copy=True)
    n = fts.size
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    w = int(window) if (window and int(window) > 0) else n
    fps = _fps_of(fts)
    med = np.empty(n, dtype=np.float64)

    ent = _MED_CACHE.get(w)
    if ent is not None and ent[0].size > _MED_CACHE_MAX:
        _MED_CACHE.pop(w, None)
        ent = None
    if ent is not None:
        c_fts, c_med = ent
        s = _find_shift(fts, c_fts)
        if s is not None:
            k = c_fts.size
            # 位移 0（同一起点、前缀不变）→ **整段**可复用，含数组左端截断的头部；
            # 位移 > 0（窗口滑动）→ 头部 [0, w-1) 的窗口在两侧坐标系里不同，必须重算，
            # 其余 i >= w-1 且 i+s < k 的部分与缓存逐元素同源，整段复用。
            lo = 0 if s == 0 else (w - 1)
            hi = min(n, k - s)
            if hi > lo:
                med[lo:hi] = c_med[s + lo:s + hi]
            if lo > 0:
                _medians_range(fps, 0, min(lo, n), w, med)
            tail = max(lo, hi)
            if tail < n:
                _medians_range(fps, tail, n, w, med)
            _MED_CACHE[w] = [fts, med]
            _MED_STAT["reuse" if s else "incr"] += 1
            return med

    _medians_range(fps, 0, n, w, med)
    _MED_CACHE[w] = [fts, med]
    _MED_STAT["full"] += 1
    return med


def _find_shift(new, cached):
    """
    探测 new 相对 cached 的位移 s：满足 new[i] == cached[s+i]（重叠区间内）。

    返回 s（>=0）或 None（无法对齐 → 调用方全量重算）。
    只做**数值相等**校验，任何一条命中路径都保证复用的中位数来源合法：
    重叠区间内逐元素相等 ⇒ 相同窗口算出的中位数必然相同。
    """
    n = new.size
    k = cached.size
    if n == 0 or k == 0:
        return None
    # ① 尾部追加（最常见：同一帧流持续增长）
    if n >= k and np.array_equal(new[:k], cached):
        return 0
    # ② 纯滑动（等长或变短，没有新增帧）
    if n <= k and np.array_equal(new, cached[k - n:]):
        return k - n
    # ③ 滑动 + 追加：用首元素在缓存里定位候选位移，最多验证 _MED_CAND 个
    cand = np.flatnonzero(cached == new[0])
    if cand.size == 0:
        return None
    for p in cand[:_MED_CAND]:
        s = int(p)
        L = k - s
        if L > n:
            L = n
        if L <= 0:
            continue
        if np.array_equal(new[:L], cached[s:s + L]):
            return s
    return None


def _stutter_from_med(fts, med):
    """
    由「帧耗时 + 局部中位数帧率」算出严格顿挫标记（A 且 B 且非 C）。

    单独拆出来是为了让 window_stats 与 stutter_flags / is_stutter_frame
    共用同一次中位数计算，避免重复开销。
    """
    fts = np.asarray(fts, dtype=np.float64)
    n = fts.size
    flags = np.zeros(n, dtype=bool)
    if n == 0:
        return flags
    fps = _fps_of(fts)
    with np.errstate(divide="ignore", invalid="ignore"):
        med_ft = np.where(med > 0, 1000.0 / np.maximum(med, 1e-9), np.inf)
    cond_a = fps <= (med * STUTTER_REL_FACTOR)             # 相对跌幅 ≥ 35%
    cond_b = (fts - med_ft) >= STUTTER_FT_JUMP_MS          # 单帧耗时跳变 ≥ 8ms
    cond_c = fps >= STUTTER_EXEMPT_FPS                     # 绝对流畅豁免
    flags = cond_a & cond_b & (~cond_c)
    flags[0] = False                                       # 第 0 帧无历史
    return flags


def stutter_flags(frames, fts=None, window=STUTTER_WINDOW):
    """逐帧严格顿挫标记（bool 数组）：条件 A 且 B 且非 C；第 0 帧恒为 False。"""
    if fts is None:
        fts = _fts(frames)
    fts = np.array(fts, dtype=np.float64, copy=True)
    if fts.size == 0:
        return np.zeros(0, dtype=bool)
    med = local_median_fps(frames, fts, window)
    return _stutter_from_med(fts, med)


def is_stutter_frame(frames, index, fts=None, window=STUTTER_WINDOW):
    """单帧判定：第 index 帧是否满足严格顿挫双门控（越界/第 0 帧 → False）。"""
    if index is None:
        return False
    if fts is None:
        fts = _fts(frames)
    fts = np.array(fts, dtype=np.float64, copy=True)
    try:
        index = int(index)
    except Exception:
        return False
    if index <= 0 or index >= fts.size:
        return False
    return bool(stutter_flags(frames, fts, window)[index])


def drop_flags(frames, fts=None):
    """
    逐帧漏帧标记（保留旧函数名与签名）。

    【v4.1】内部已改为直接返回 stutter_flags(...) 的结果：
    与全局均值/最高帧比较的旧口径（DROP_FT_FACTOR / DROP_FPS_FACTOR）已废弃，
    那两个常量仅为兼容外部引用而保留定义，不再参与判定。
    """
    return stutter_flags(frames, fts)


def count_severe_drop(flags, fps):
    """
    严重掉帧次数。

    新口径下 flags 本身已是「相对局部中位数跌幅 ≥35% 且单帧耗时跳变 ≥8ms」
    的双门控标记 —— 每一条都是严重顿挫，直接计数即可；
    fps 参数保留以兼容旧签名（不再参与筛选，避免把真顿挫又过滤掉）。
    """
    try:
        arr = np.asarray(flags, dtype=bool)
    except Exception:
        return 0
    return int(arr.sum())


def window_stats(frames, window=STAT_WINDOW):
    """
    返回四大帧数卡的原始数据。

    frames : 已过滤到目标进程的 FrameRecord 序列（时间升序）
    返回 dict（**保留原有全部键**）：
      avg_fps / max_fps / min_fps(瞬时最低) / low1_fps / low01_fps /
      low01_degraded / dropped / dropped_pct / grade / grade_label / grade_hint /
      smooth_pct / n / window_n
    以及 v4.1 新增键：
      flags            本次实际处理的 recent 切片等长的 bool 数组（严格顿挫标记）
      stutter_now      最后一帧是否真顿挫（病历卡/横幅据此决定显隐）
      local_median_fps 最后一帧的局部中位数帧率
      stutter_idx      最后一帧的 idx（>=0，便于快速定位）
      smooth           = not stutter_now
    """
    if not frames:
        return {"avg_fps": 0.0, "max_fps": 0.0, "max_fps_raw": 0.0, "spike_fps": 0.0,
                "min_fps": 0.0, "low1_fps": 0.0,
                "low01_fps": 0.0, "low01_degraded": True, "dropped": 0,
                "dropped_pct": 0.0, "grade": "C",
                "grade_label": GRADE_LABEL["C"], "grade_hint": GRADE_HINT["C"],
                "smooth_pct": 100.0, "n": 0, "window_n": 0,
                "flags": np.zeros(0, dtype=bool), "stutter_now": False,
                "local_median_fps": 0.0, "stutter_idx": 0, "smooth": True}
    recent = frames[-window:] if (window and len(frames) > window) else list(frames)
    fts = _fts(recent)
    fps = _fps_of(fts)
    avg_fps = float(np.mean(fps))
    # 【v1.0.1 极值去噪】原来的 max_fps = 全体最大，会被「只有 1~2 帧的离群尖峰」带飞：
    # 实测出现过 2554 / 477 帧这种单帧数据（PresentMon 在加载、切窗口时会吐极短帧），
    # 结果「最高帧」卡变成荒谬数字，折线图 Y 轴被顶到几百、真实曲线压成一条线。
    # 现在改成「第 k 大的值」，k = max(3, 2% 窗口)：允许丢掉最多 2%（至少 3 帧）极值，
    # 但 4% 以上帧真的跑到 300fps 的**成片高速段**仍然算最高帧（不会被误判成噪点）。
    n_fts = len(fts)
    k_th = max(3, int(math.ceil(0.02 * n_fts)))
    k_th = max(1, min(k_th, n_fts))
    max_fps = float(np.partition(fps, n_fts - k_th)[n_fts - k_th])
    max_fps_raw = float(np.max(fps))
    # 只有「极值明显高出稳健最高帧」才当作尖峰上报，随卡片的灰字一起显示，不隐藏数据
    spike_fps = max_fps_raw if max_fps_raw > max_fps * 1.25 + 1e-6 else 0.0
    min_fps = float(np.min(fps))
    p99 = float(np.percentile(fts, 99))
    low1 = 1000.0 / p99 if p99 > 0 else 0.0
    degraded = len(fts) < 1000
    p999 = p99 if degraded else float(np.percentile(fts, 99.9))
    low01 = 1000.0 / p999 if p999 > 0 else 0.0
    # 严格顿挫（与旧 2.5× / 40% 口径无关）
    med = local_median_fps(recent, fts, STUTTER_WINDOW)
    flags = _stutter_from_med(fts, med)
    dropped = int(flags.sum())
    dropped_pct = dropped / len(fts) * 100.0 if len(fts) else 0.0
    g = grade(avg_fps, dropped_pct, low1)
    stutter_now = bool(flags[-1]) if flags.size else False
    last = recent[-1]
    try:
        last_idx = int(getattr(last, "idx", len(recent) - 1))
    except Exception:
        last_idx = len(recent) - 1
    return {
        "avg_fps": avg_fps, "max_fps": max_fps, "min_fps": min_fps,
        "max_fps_raw": max_fps_raw, "spike_fps": spike_fps,
        "low1_fps": low1, "low01_fps": low01, "low01_degraded": degraded,
        "dropped": dropped, "dropped_pct": dropped_pct,
        "grade": g, "grade_label": GRADE_LABEL[g], "grade_hint": GRADE_HINT[g],
        "smooth_pct": 100.0 - dropped_pct, "n": len(frames), "window_n": len(fts),
        "flags": flags, "stutter_now": stutter_now,
        "local_median_fps": float(med[-1]) if med.size else 0.0,
        "stutter_idx": last_idx, "smooth": (not stutter_now),
    }


def grade(avg_fps, dropped_pct, low1_fps=None):
    """
    丝滑度评级：S / A / B / C。

    规则（满足平均帧门槛 **且** 漏帧占比不超标）：
        S : 平均帧 ≥ 100 且 漏帧 ≤ 0.5%
        A : 平均帧 ≥ 60  且 漏帧 ≤ 2%
        B : 平均帧 ≥ 45  且 漏帧 ≤ 5%
        C : 其余
    附加降级：1% Low 低于平均帧 45% 时最多只能给 A（抖动太大不配 S）。
    """
    g = "C"
    for letter, fps_min, drop_max in GRADE_RULES:
        if avg_fps >= fps_min and dropped_pct <= drop_max:
            g = letter
            break
    if g == "S" and low1_fps and avg_fps > 0 and low1_fps < avg_fps * 0.45:
        g = "A"
    return g


def fps_verdict(avg_fps):
    """平均帧底部微字评价（改进建议书 二.1 的三档文案）"""
    if avg_fps > 100:
        return "电竞级丝滑"
    if avg_fps >= 60:
        return "流畅游玩"
    return "建议降画质"


def dropped_verdict(dropped):
    """漏帧卡底部微字：0 次绿色「零漏帧」，>5 次红色告警"""
    if dropped <= 0:
        return ("零漏帧", "green")
    if dropped <= 5:
        return ("偶发顿挫", "warn")
    return ("顿挫频繁", "bad")


def gpu_3d_load_pct(hw):
    """
    硬件快照的 3D 引擎负载（%）。

    · 优先取 `hw.gpu_eng` 里键名含 `3d` 的引擎负载（AMD/NV 的 engtype_3D）；
      数值 0~1 视为比例并自动 ×100，>1 视为已是百分数；
    · 没有任何 3D 引擎时退回「所有引擎最大值」（至少能反映显卡有活干）；
    · hw is None / 无数据 → 0.0。
    """
    if hw is None:
        return 0.0
    eng = getattr(hw, "gpu_eng", None) or {}
    best3d = None
    best = 0.0
    for name, val in eng.items():
        try:
            v = float(val)
        except Exception:
            continue
        if not np.isfinite(v):
            continue
        v = v * 100.0 if v <= 1.0 else v
        v = max(0.0, min(100.0, v))
        if v > best:
            best = v
        if "3d" in str(name).lower():
            if best3d is None or v > best3d:
                best3d = v
    return float(best3d) if best3d is not None else float(best)


def _gb_text(mb):
    """
    MB → 容量文案：`X.X GB` / `NNN MB`。

    只用于**容量**展示，绝不参与百分比计算（这正是当初 `1008.40%` 的病根）。
    按改进建议书二.3 的字面格式，GB 保留 1 位小数。
    """
    try:
        mb = float(mb)
    except Exception:
        return "-"
    if not np.isfinite(mb):
        return "-"
    if mb >= 1024.0:
        return "%.1f GB" % (mb / 1024.0)
    return "%.0f MB" % mb


def vram_bar_text(dedicated_mb, total_mb, nonlocal_mb=None):
    """
    显存进度条文本 + 百分比（修复 `1008.40%` Bug 的根因）。

    返回 (text, pct)：
      · text 形如 `已用 5.2 GB / 总计 12.0 GB (43%)`（改进建议书二.3 的格式）；
      · pct = clamp(dedicated / total * 100, 0, 100) —— **绝不返回 >100**；
      · total_mb <= 0（容量未知）→ ("容量未知", 0.0)，绝不拿脏数据算百分比；
      · 传入 nonlocal_mb 时在末尾附上「非本地」溢出量，便于一眼看出是否溢出。
    """
    try:
        ded = float(dedicated_mb if dedicated_mb is not None else 0.0)
    except Exception:
        ded = 0.0
    try:
        tot = float(total_mb if total_mb is not None else 0.0)
    except Exception:
        tot = 0.0
    if not np.isfinite(ded) or ded < 0:
        ded = 0.0
    if not np.isfinite(tot):
        tot = 0.0
    if tot <= 0:
        return ("容量未知", 0.0)
    pct = max(0.0, min(100.0, ded / tot * 100.0))
    text = "已用 %s / 总计 %s (%.0f%%)" % (_gb_text(ded), _gb_text(tot), pct)
    if nonlocal_mb is not None:
        try:
            nl = float(nonlocal_mb)
        except Exception:
            nl = 0.0
        if np.isfinite(nl) and nl > 0:
            text += " · 非本地 %s" % _gb_text(nl)
    return (text, pct)


def bottleneck(hw, gpu_ratio_pct=None, cpu_pct=None):
    """
    CPU / 显卡瓶颈天平（改进建议书 四.2）。

    hw : 最近的 HardwareSnapshot（可为 None）
    gpu_ratio_pct : 由帧数据算出的 GPU 忙时比（%），优先于 hw.gpu_eng
    cpu_pct : 由帧数据/硬件快照得到的 CPU 总占用（%）

    返回 dict：{text, advice, gpu_pct, cpu_pct, side}
      side ∈ {"gpu", "cpu", "balanced", "unknown"}
    """
    gp = gpu_ratio_pct
    if gp is None:
        gp = _gpu_pct_from_snapshot(hw)
    cp = cpu_pct
    if cp is None:
        cp = float(getattr(hw, "cpu_total", 0.0) or 0.0)
    gp = max(0.0, min(100.0, float(gp or 0.0)))
    cp = max(0.0, min(100.0, float(cp or 0.0)))
    if gp < 1.0 and cp < 1.0:
        return {"side": "unknown", "gpu_pct": gp, "cpu_pct": cp,
                "text": "等数据…", "conclusion": "等数据…", "advice": ""}
    if gp >= 90.0 and cp < 70.0:
        return {"side": "gpu", "gpu_pct": gp, "cpu_pct": cp,
                "text": "显卡已满载 %.0f%% · CPU %.0f%%" % (gp, cp),
                "conclusion": "显卡满载", "advice": "降分辨率 / 降画质，或换显卡"}
    if cp >= 85.0 and gp < 80.0:
        return {"side": "cpu", "gpu_pct": gp, "cpu_pct": cp,
                "text": "CPU 瓶颈 %.0f%% · 显卡 %.0f%%" % (cp, gp),
                "conclusion": "CPU 瓶颈", "advice": "调低【同屏人数 / 视野 / 阴影】，或关后台"}
    if gp >= 80.0 and cp >= 80.0:
        return {"side": "gpu", "gpu_pct": gp, "cpu_pct": cp,
                "text": "显卡 %.0f%% · CPU %.0f%% 双满载" % (gp, cp),
                "conclusion": "双满载", "advice": "整体降一档画质最有效"}
    return {"side": "balanced", "gpu_pct": gp, "cpu_pct": cp,
            "text": "显卡 %.0f%% · CPU %.0f%%（都没满载）" % (gp, cp),
            "conclusion": "都没满载",
            "advice": "↑ 帧率上限 / 垂直同步，非硬件瓶颈"}


def _gpu_pct_from_snapshot(hw):
    """硬件快照的显卡负载估算（%）—— 与 gpu_3d_load_pct 同一套口径。"""
    return gpu_3d_load_pct(hw)


def smooth_series(x, y, window=3):
    """
    轻量平滑（移动平均）—— 防止毛刺扎眼（改进建议书 三.1）。

    只用于**绘制**，不参与任何统计与归因；window<=1 时原样返回。
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if window is None or window <= 1 or y.size < window:
        return x, y
    k = np.ones(int(window), dtype=np.float64) / float(window)
    ys = np.convolve(y, k, mode="same")
    # 两端用原值回填，避免卷积分界的衰减假象
    half = int(window) // 2
    if half > 0:
        ys[:half] = y[:half]
        ys[-half:] = y[-half:]
    return x, ys


def drop_points(frames, fts=None, flags=None):
    """
    漏帧打点坐标：返回 [(frame_index, fps_value), ...]（改进建议书 三.2）。

    只在真正判定为顿挫（严格双门控）的波谷处打点，避免满屏红点。
    """
    if not frames:
        return []
    fts = _fts(frames) if fts is None else np.asarray(fts, dtype=np.float64)
    if flags is None:
        flags = drop_flags(frames, fts)
    fps = _fps_of(fts)
    return [(int(frames[i].idx), float(fps[i])) for i in range(len(frames)) if flags[i]]
