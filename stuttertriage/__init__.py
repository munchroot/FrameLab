# -*- coding: utf-8 -*-
r"""StutterTriage —— 游戏掉帧（卡顿）一站式体检台

底层采集：PresentMon（ETW 帧事件）+ PDH / DXGI / ADL / NVML（硬件探针）。
上层把「帧率掉了」翻译成玩家看得懂的一句话 + 一招解决建议。

模块职责：
  · engines/presentmon.py   PresentMon 进程生命周期、能力探测、CSV 流解析
  · engines/hardware.py     硬件心跳线程（CPU/GPU/内存/磁盘/网络的周期采样）
  · etw.py                  ETW 会话管家（避免残留会话导致静默 0 帧）
  · procguard.py            Job Object：父进程结束则子进程必死
  · probes.py               PDH / DXGI / ADL / NVML 的 ctypes 探针
  · analysis.py             帧存储、卡顿标记、基线、11 维归因
  · stats.py                纯统计函数（P99 / 1% Low / 滚动中位数）
  · ui/                     PyQt6 界面（主窗口、五个页面、图表、皮肤）
  · config.py               配置外部化（阈值、主题、热键）
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
