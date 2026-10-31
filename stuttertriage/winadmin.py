# -*- coding: utf-8 -*-

"""管理员权限、PresentMon 路径指引、数据源判定、模拟水印。"""

import os
import sys
import ctypes
import pyqtgraph as pg
from PyQt6 import QtCore, QtGui
from .consts import DATA_SOURCE_OFFLINE, DATA_SOURCE_REAL, DATA_SOURCE_SIM, PRESENTMON_GUIDE, SIM_WATERMARK_TEXT
from .paths import app_dir, launcher_path



# ==============================================================================
# §9.5.3  管理员权限检测 / 温和提权入口
# ==============================================================================
def is_admin():
    """当前进程是否以管理员身份运行；检测失败一律返回 False（不误导用户）"""
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        pass
    try:
        return bool(ctypes.windll.kernel32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(app_path=None, script_path=None):
    """
    以管理员身份重启当前程序（ShellExecuteW + "runas"）。

    **不强制**：只提供按钮，用户点了才提权。
    返回 True 表示已成功拉起提权进程。
    """
    if os.name != "nt":
        return False
    exe = app_path or sys.executable
    params = ""
    if not getattr(sys, "frozen", False):
        # 以源码方式运行时，重启必须带上脚本路径
        script = script_path or launcher_path() or os.path.abspath(__file__)
        params = '"%s"' % script
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, os.getcwd(), 1)
        # ShellExecuteW 返回 > 32 表示成功
        return int(rc) > 32
    except Exception:
        return False


def presentmon_dir():
    """PresentMon 的默认查找目录（= 本程序所在目录，非包目录）"""
    return app_dir()


def presentmon_missing_hint():
    """PresentMon 缺失时的完整指引文案"""
    return PRESENTMON_GUIDE % presentmon_dir()


def open_dir_in_explorer(path=None):
    """用系统默认文件管理器打开目录（返回 True 表示已调起）"""
    try:
        d = path or presentmon_dir()
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        url = QtCore.QUrl.fromLocalFile(os.path.abspath(d))
        return bool(QtGui.QDesktopServices.openUrl(url))
    except Exception:
        return False




# ==============================================================================
# §9.5.5  数据源标记 + 模拟水印
# ==============================================================================
def detect_data_source(pm_bridge=None, offline=False):
    """
    判断当前数据来源：real / simulated / offline-import。

    - `offline=True` → 离线 CSV 导入（最高优先级）
    - `pm_bridge.simulation_mode` → 模拟降级
    - 否则 → 真实采集

    **不读取 PresentMonBridge 的内部实现**，只读它的公开属性。
    """
    if offline:
        return DATA_SOURCE_OFFLINE
    try:
        if pm_bridge is not None and getattr(pm_bridge, "simulation_mode", False):
            return DATA_SOURCE_SIM
    except Exception:
        pass
    return DATA_SOURCE_REAL


def make_sim_watermark(text=SIM_WATERMARK_TEXT):
    """
    构造模拟数据水印（pyqtgraph TextItem）。

    用 TextItem 而不是叠加 QLabel 的原因：
      - TextItem 直接活在 plot 的 scene 里，跟着视图缩放平移，不会脱节；
      - ignoreBounds=True → 不会把自动 Y 轴范围撑开；
      - setZValue(1000) → 永远压在曲线之上，一眼可见。
    """
    try:
        item = pg.TextItem(text=text, color=QtGui.QColor(255, 93, 93, 165),
                           anchor=(0.5, 0.5))
        f = QtGui.QFont("Segoe UI", 26)
        f.setBold(True)
        item.setFont(f)
        item.setZValue(1000)
        item.setVisible(False)
        return item
    except Exception:
        return None
