# -*- coding: utf-8 -*-
"""
「程序所在目录」的单一事实来源。

拆分前 `os.path.dirname(os.path.abspath(__file__))` 就等于程序目录；
拆分后每个模块的 `__file__` 都在 `framelab/` 里，直接沿用会把
framelab_config.json / PresentMon 查找目录 / 提权脚本路径全都指到包目录里
（实测踩坑：配置被写到 framelab/framelab_config.json，用户的旧配置失效）。
因此统一走本模块：

    APP_DIR  = 与启动器 FrameLab.py 同级的目录（打包后 = exe 所在目录）
    PKG_DIR  = framelab 包目录（只放代码，不放用户数据）
"""
import os
import sys

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(PKG_DIR)


def app_dir():
    """用户数据 / 外部工具所在目录：源码运行 = 项目根，冻结打包 = exe 所在目录"""
    if getattr(sys, "frozen", False):
        try:
            return os.path.dirname(os.path.abspath(sys.executable))
        except Exception:
            pass
    return APP_DIR


def app_path(*parts):
    return os.path.join(app_dir(), *parts)


def bundle_dir():
    """
    随包资源目录：PyInstaller 单文件运行时 = `sys._MEIPASS`（解包临时目录）；
    源码运行 = 项目根。

    【为什么必须有】`app_dir()` 是 exe 所在目录，而打包进 EXE 的 PresentMon
    等资源会被解压到 `_MEIPASS` —— 两者不是一个地方。查找随包资源必须走这里，
    否则拷给别人运行时会「找不到 PresentMon」直接退化成模拟数据。
    """
    if getattr(sys, "frozen", False):
        try:
            base = getattr(sys, "_MEIPASS", "")
            if base and os.path.isdir(base):
                return base
        except Exception:
            pass
    return APP_DIR


def bundle_path(*parts):
    return os.path.join(bundle_dir(), *parts)


def launcher_path():
    """提权重启时要传给新进程的脚本路径（打包时返回 None）"""
    if getattr(sys, "frozen", False):
        return None
    cand = app_path("FrameLab.py")
    return cand if os.path.isfile(cand) else None
