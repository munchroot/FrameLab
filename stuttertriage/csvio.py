# -*- coding: utf-8 -*-

"""CSV 后台读取（不冻结界面）。"""

import os
import csv
import threading



# ==============================================================================
# §9.5.6  CSV 后台读取（不冻结界面）
# ==============================================================================
class CsvBackgroundReader:
    """
    在**后台线程**读取并解析大 CSV。

    为什么要它：`csv.DictReader` + `list(reader)` 在几十万行的文件上
    会连续占用几秒到几十秒，直接在 UI 线程里做会把窗口整个冻住
    （Windows 会画出「未响应」灰条，甚至被判定为无响应而杀掉进程）。

    用法：
        rd = CsvBackgroundReader(path, progress_cb=cb)
        rd.start()
        rd.wait()            # 阻塞当前线程（通常是 UI 线程配合 processEvents）
        rows = rd.rows

    进度回调 progress_cb(done, total) 在后台线程被调用，
    **调用方负责线程安全**（UI 里请用 signal 或只在主线程轮询 self.progress）。
    """

    def __init__(self, path, progress_cb=None, chunk_report=2000):
        self.path = path
        self.progress_cb = progress_cb
        self.chunk_report = max(1, int(chunk_report))
        self.rows = []
        self.fields = []
        self.error = ""
        self.done = False
        self.total_bytes = 0
        self._thread = None

    # ---------------- 运行 ----------------
    def start(self):
        self._thread = threading.Thread(target=self._run, name="CsvBackgroundReader", daemon=True)
        self._thread.start()
        return self

    def _run(self):
        try:
            self.total_bytes = os.path.getsize(self.path)
            with open(self.path, "r", encoding="utf-8-sig", newline="", errors="replace") as f:
                reader = csv.DictReader(f)
                self.fields = reader.fieldnames or []
                rows = []
                n = 0
                for row in reader:
                    rows.append(row)
                    n += 1
                    if n % self.chunk_report == 0 and self.progress_cb:
                        try:
                            self.progress_cb(n, self.total_bytes)
                        except Exception:
                            pass
                self.rows = rows
                if self.progress_cb:
                    try:
                        self.progress_cb(len(rows), self.total_bytes)
                    except Exception:
                        pass
        except Exception as ex:
            self.error = str(ex)
        finally:
            self.done = True

    def wait(self, timeout=None):
        """等待读取完成；返回是否成功"""
        if self._thread is not None:
            self._thread.join(timeout)
        return self.done and not self.error

    @property
    def ok(self):
        return self.done and not self.error and bool(self.rows)
