# -*- coding: utf-8 -*-
"""
进程入口（原 FrameLab.py 的 main()，拆分后唯一改动：DIAG 原地重配 + 装配新窗口）。

保持既有行为：
  · 仅支持 Windows；
  · DPI 感知；
  · 结构化日志写到 ~/.framelab/framelab_diag.log（**原地 configure**，
    否则其它模块持有的 DIAG 引用会指向旧的禁用实例）；
  · Fusion 风格 + 四主题；
  · 退出时记录异常统计并关闭日志。
"""
import ctypes
import os
import sys

from PyQt6.QtWidgets import QApplication

from .ui.main_window import FrameLabWindow
from .utils import DIAG


def main():
    if os.name != "nt":
        print("[FrameLab] 仅支持 Windows 10 1709+ / Windows 11")
        return 1
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # 结构化日志：写到用户目录，避免污染程序目录 / 权限问题
    try:
        log_dir = os.path.join(os.path.expanduser("~"), ".framelab")
        DIAG.configure(os.path.join(log_dir, "framelab_diag.log"), enabled=True)
    except Exception:
        DIAG.configure(None, enabled=False)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    win = FrameLabWindow()
    win.show()
    try:
        return app.exec()
    finally:
        try:
            DIAG.note("exit", "FrameLab 退出，异常统计: %s" % DIAG.summary())
            DIAG.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
