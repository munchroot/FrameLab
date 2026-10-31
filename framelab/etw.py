# -*- coding: utf-8 -*-

"""ETW 会话管家：清理残留会话（解决 PresentMon error 1450）。"""

import re
import uuid
import subprocess
from .consts import PM_SESSION_PREFIX



# ==============================================================================
# §4  ETW 会话管家
# ==============================================================================
class EtwSessionManager:
    """
    PresentMon 的 ETW session 是**全局资源**：
      · 进程被 kill / terminate 后 session 必然残留 Running
      · 残留堆积到 4 个即触发 "error code 1450"，采集静默返回 0 帧
      · `--stop_existing_session` 可自愈同名残留
    因此本类负责：唯一 session 名 / 启动前清理本工具历史残留 / 退出时 logman 兜底。
    """

    def __init__(self):
        self.session_name = PM_SESSION_PREFIX + uuid.uuid4().hex[:10]

    # ---------- logman 查询 ----------
    @staticmethod
    def list_sessions():
        """返回当前所有 ETW Trace 会话名"""
        names = []
        try:
            out = subprocess.run(["logman", "query", "-ets"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=15)
            for line in (out.stdout or "").splitlines():
                s = line.strip()
                if not s or s.startswith(("Data Collector", "----")):
                    continue
                if "Trace" in s or "Provider" in s:
                    parts = s.split()
                    if parts:
                        names.append(parts[0])
        except Exception:
            pass
        return names

    @staticmethod
    def stop_session(name):
        """logman stop <name> -ets —— 兜底清理，绝不能省"""
        try:
            # 会话名只允许字母数字与下划线，杜绝命令注入
            if not name or not re.match(r"^[A-Za-z0-9_.-]+$", str(name)):
                return False
            subprocess.run(["logman", "stop", str(name), "-ets"], capture_output=True,
                           text=True, timeout=20)
            return True
        except Exception:
            return False

    def cleanup_stale(self, skip=None):
        """
        启动时清理本工具前缀下的历史残留会话（避免 1450 静默失败）。

        skip: 需要跳过的会话名（通常是**当前正在使用**的那个，
              否则用户点一下「清理 ETW 残留」就会把自己的采集干掉）。
        """
        stopped = []
        for n in self.list_sessions():
            if skip and n == skip:
                continue
            if n.lower().startswith(PM_SESSION_PREFIX.lower()):
                if self.stop_session(n):
                    stopped.append(n)
        return stopped

    def dispose(self):
        """退出时兜底清理自己的会话"""
        self.stop_session(self.session_name)

    def alive(self):
        return self.session_name in self.list_sessions()
