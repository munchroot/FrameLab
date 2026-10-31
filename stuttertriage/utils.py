# -*- coding: utf-8 -*-

"""通用工具：结构化日志、有界队列、数值/单位格式化、pyqtgraph 符号校验。"""

import os
import time
import math
import queue
import threading
import traceback


# pyqtgraph ScatterPlotItem 的合法符号白名单（实测 0.14.0）：
# '+', '_', 'arrow_*', 'crosshair', 'd', 'h', 'o', 'p', 's', 'star', 't', 't1', 't2', 't3', 'x', '|'
# 传非法符号会直接抛 KeyError 让整个 UI 崩掉，因此这里强校验并降级。
PG_SYMBOLS = {"+", "_", "arrow_down", "arrow_left", "arrow_right", "arrow_up",
              "crosshair", "d", "h", "o", "p", "s", "star", "t", "t1", "t2", "t3", "x", "|"}


def pg_symbol(sym, fallback="o"):
    return sym if sym in PG_SYMBOLS else fallback




# ==============================================================================
# §1b  结构化日志（替代裸 except 的静默吞错）
# ==============================================================================
class DiagLog:
    """
    轻量结构化日志。

    【评审】"大量 except Exception: pass 会把真 bug 静默吞掉" —— 确实如此。
    这里的做法不是把每个 except 都改成具体异常类型（那会引入大量噪音），
    而是：**关键路径的异常必须留下可查的痕迹**，并且对重复错误限频，
    避免日志本身变成性能负担。

    · 关键路径（探针/采集/渲染）→ 写入文件 + 计数
    · 非关键路径（单个字段读取失败）→ 只计数不落盘
    """

    _MAX_KINDS = 200

    def __init__(self, path=None, enabled=True):
        self.enabled = enabled
        self.counts = {}
        self.path = path
        self._fh = None
        if enabled and path:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                self._fh = open(path, "a", encoding="utf-8", buffering=1)
            except Exception:
                self._fh = None

    def configure(self, path=None, enabled=True):
        """
        运行期重新指定日志文件（拆分模块后 app 用它替代 `global DIAG` 重新赋值）。

        各模块都是 `from .utils import DIAG` 拿到**同一个对象**，
        因此原地重配才有效；重新绑定 utils.DIAG 会让其它模块继续用旧实例。
        """
        self.close()
        self.enabled = bool(enabled)
        self.path = path
        self._fh = None
        if self.enabled and path:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                self._fh = open(path, "a", encoding="utf-8", buffering=1)
            except Exception:
                self._fh = None
        return self

    def report(self, where, exc, level="ERROR"):
        """记录一次异常（带限频）"""
        key = "%s|%s" % (where, type(exc).__name__)
        n = self.counts.get(key, 0) + 1
        self.counts[key] = n
        if not self.enabled:
            return
        # 同类错误只在第 1 次与每 50 次落盘，避免刷屏
        if n == 1 or n % 50 == 0:
            try:
                import traceback
                tb = "".join(traceback.format_exception_only(type(exc), exc)).strip()
                if self._fh:
                    self._fh.write("[%s] %s (%s) #%d\n" % (
                        time.strftime("%H:%M:%S"), where, tb, n))
                    self._fh.flush()
            except Exception:
                pass

    def note(self, where, msg):
        """记录一条非异常的重要观察"""
        if not self.enabled:
            return
        try:
            if self._fh:
                self._fh.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), where))
                self._fh.flush()
        except Exception:
            pass

    def summary(self):
        return dict(sorted(self.counts.items(), key=lambda kv: -kv[1]))

    def close(self):
        try:
            if self._fh:
                self._fh.close()
                self._fh = None
        except Exception:
            pass




# 全局日志实例（在 main() 里按需替换路径）
DIAG = DiagLog(enabled=False)


def human_size(n):
    """字节 → 人类可读"""
    try:
        n = float(n)
    except Exception:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024.0 or unit == "GB":
            return ("%.0f %s" % (n, unit)) if unit == "B" else ("%.2f %s" % (n, unit))
        n /= 1024.0
    return "%.2f GB" % n


def pages_to_mb_per_s(v):
    """4KB 物理页数 → MB/s"""
    try:
        return float(v) * 4096.0 / 1048576.0
    except Exception:
        return 0.0


def safe_float(text, default=0.0):
    """
    解析 PresentMon 数值；NA / 空 / 非法 / **非有限值** 一律返回 default。

    【评审·实测】原实现只挡了 NA/空/非法，float('nan') 与 float('inf')
    能直接通过 —— NaN 进入基线中位数会让基线整体变成 NaN，
    inf 会让百分比变成 inf%，这类"看起来有值"的脏数据最危险。
    """
    if text is None:
        return default
    t = str(text).strip()
    if t == "" or t.upper() == "NA" or t.upper() == "N/A":
        return default
    try:
        v = float(t)
    except Exception:
        return default
    if not math.isfinite(v):
        return default
    return v


class BoundedQueue:
    """
    有界队列 + 丢弃策略的包装。

    【红线】生产者绝不能因为队列满而阻塞：
    PresentMon 的 stdout 管道一旦没人读，子进程写满管道会卡住，
    进而让采集中断、ETW 会话泄漏。所以这里在队列满时**丢弃最旧的帧**
    （保留最新的），并统计丢弃数，由 UI 线程读取后提示。
    """

    def __init__(self, maxsize=60000):
        self._q = queue.Queue(maxsize=maxsize)
        self.dropped = 0
        self._lock = threading.Lock()

    def put(self, item):
        try:
            self._q.put_nowait(item)
            return True
        except queue.Full:
            with self._lock:
                self.dropped += 1
            # 丢弃最旧的一帧，给新数据腾位置
            try:
                self._q.get_nowait()
                self._q.put_nowait(item)
            except Exception:
                pass
            return False

    def get_nowait(self):
        return self._q.get_nowait()

    def take_dropped(self):
        with self._lock:
            n = self.dropped
            self.dropped = 0
            return n


def is_na(text):
    if text is None:
        return True
    t = str(text).strip()
    return t == "" or t.upper() == "NA" or t.upper() == "N/A"


def fmt_delta(cur, base, unit="", pct=True):
    """统一的 Delta 文案：带方向箭头与百分比"""
    if base is None:
        return "—"
    try:
        d = float(cur) - float(base)
    except Exception:
        return "—"
    if abs(d) < 1e-9:
        return "持平"
    arrow = "▲" if d > 0 else "▼"
    if pct and base:
        return "%s%+.1f%%" % (arrow, d / abs(base) * 100.0)
    return "%s%+.2f%s" % (arrow, d, unit)
