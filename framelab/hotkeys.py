# -*- coding: utf-8 -*-

"""全局热键（RegisterHotKey + 原生事件过滤器）。"""

import os
import ctypes
from PyQt6 import QtCore, QtWidgets



# ==============================================================================
# §9.5.2  全局热键「打点标记」
# ==============================================================================
WM_HOTKEY = 0x0312


MOD_ALT      = 0x0001


MOD_CONTROL  = 0x0002


MOD_SHIFT    = 0x0004


MOD_WIN      = 0x0008


MOD_NOREPEAT = 0x4000


_HOTKEY_MOD_NAMES = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "meta": MOD_WIN, "super": MOD_WIN,
}



# 特殊功能键 → 虚拟键码
_VK_SPECIAL = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "f13": 0x7C, "f14": 0x7D, "f15": 0x7E, "f16": 0x7F, "f17": 0x80,
    "f18": 0x81, "f19": 0x82, "f20": 0x83, "f21": 0x84, "f22": 0x85,
    "f23": 0x86, "f24": 0x87,
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B,
    "escape": 0x1B, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28,
    "left": 0x25, "right": 0x27,
}


def parse_hotkey(text):
    """
    把 "Ctrl+F9" 解析成 (modifiers, vk)。

    解析失败返回 None（调用方据此提示用户，不抛异常）。

    规则：
      · 修饰键（Ctrl/Alt/Shift/Win）可重复出现（Ctrl+Ctrl+F9 视为 Ctrl+F9）；
      · **主键只能出现一次** —— "F9+F9" 这类串是用户手误，必须判为非法，
        否则会静默取最后一个键，用户以为设的是 A 实际设的是 B。
    """
    if not text:
        return None
    parts = [p.strip().lower() for p in str(text).replace("-", "+").split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    key = None
    for p in parts:
        if p in _HOTKEY_MOD_NAMES:
            mods |= _HOTKEY_MOD_NAMES[p]
            continue
        if p in _VK_SPECIAL:
            if key is not None:
                return None        # 主键重复 → 非法
            key = _VK_SPECIAL[p]
            continue
        if len(p) == 1 and p.isalnum():
            if key is not None:
                return None        # 主键重复 → 非法
            key = ord(p.upper())
            continue
        return None
    if key is None:
        return None
    return (mods | MOD_NOREPEAT, key)


def format_hotkey(mods, vk):
    """把 (modifiers, vk) 反向格式化成人类可读文本"""
    names = []
    if mods & MOD_CONTROL:
        names.append("Ctrl")
    if mods & MOD_ALT:
        names.append("Alt")
    if mods & MOD_SHIFT:
        names.append("Shift")
    if mods & MOD_WIN:
        names.append("Win")
    for nm, code in _VK_SPECIAL.items():
        if code == vk:
            names.append(nm.upper() if len(nm) <= 2 else nm.capitalize())
            break
    else:
        if 0x30 <= vk <= 0x5A:
            names.append(chr(vk))
    return "+".join(names) if names else "?"


class _HotkeyNativeFilter(QtCore.QAbstractNativeEventFilter):
    """
    原生事件过滤器：捕获 WM_HOTKEY 消息。

    RegisterHotKey 把热键变成一条投递到**本窗口线程**的 WM_HOTKEY 消息，
    只有安装了 native event filter 才能在 Qt 事件循环里收到它。

    【PyQt6 API 契约 · 极易踩坑】
        nativeEventFilter(self, eventType: QByteArray, message: sip.voidptr)
            -> (bool, sip.voidptr)

    PyQt6 要求返回**二元组** `(handled, message)`；
    PyQt5 才是返回单个 bool。返回单个 bool 会抛
    "TypeError: invalid result from ...nativeEventFilter()"，
    而且这个过滤器是**每个原生事件**都会调用的（鼠标移动 / 定时器 / 窗口变化），
    一旦写错就会持续刷屏，把 Qt 的事件分发打进异常路径。
    所以这里必须返回 `(bool, message)`，且 message 原样传回（不要转 int、不要自造）。
    """

    def __init__(self, hotkey_id, callback):
        super().__init__()
        self.hotkey_id = hotkey_id
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        # 返回值一律是二元组；第二个元素原样回传 message
        try:
            msg = ctypes.cast(int(message), ctypes.POINTER(ctypes.wintypes.MSG)).contents
            if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
                try:
                    self.callback()
                except Exception:
                    pass
                return True, message
        except Exception:
            pass
        return False, message


_HOTKEY_ID_SEQ = [0x5A00]


def _next_hotkey_id():
    """生成一个新的全局热键 id（同一线程内必须用不同 id 才能替换注册）"""
    _HOTKEY_ID_SEQ[0] += 1
    if _HOTKEY_ID_SEQ[0] > 0x7FFF:
        _HOTKEY_ID_SEQ[0] = 0x5A00
    return _HOTKEY_ID_SEQ[0]


class GlobalHotkeyManager:
    """
    全局热键管理器（纯 ctypes RegisterHotKey，**不引入任何第三方依赖**）。

    为什么不用 `keyboard` 库：
      · `keyboard` 在 Windows 上需要管理员权限 / 挂钩全局键盘事件，
        很多杀软会误报，而且和 PyQt 的事件循环配合要另起线程。
      · RegisterHotKey 是系统原生机制：即使本程序在后台/无焦点，
        按下热键依然会收到 WM_HOTKEY。

    降级策略（**绝不让热键问题把程序搞崩**）：
      1. 非 Windows / 解析失败 / 注册被占用 → `registered=False`，
         界面显示「热键不可用，请使用界面按钮打点」。
      2. 注册成功但事件过滤器装不上 → 同样降级为界面按钮。
    """

    HOTKEY_ID = 0xB701

    def __init__(self, callback, hotkey_text="Ctrl+F9", hotkey_id=None):
        self.callback = callback
        self.hotkey_text = hotkey_text
        self.hotkey_id = self.HOTKEY_ID if hotkey_id is None else hotkey_id
        self.registered = False
        self.error = ""
        self.mods = 0
        self.vk = 0
        self._filter = None
        self._app = None

    # ---------------- 状态 ----------------
    @property
    def available(self):
        return self.registered

    def describe(self):
        """人类可读的状态描述（供 UI 显示）"""
        if self.registered:
            return "全局热键 %s 已注册" % self.hotkey_text
        if self.error:
            return "全局热键不可用（%s）—— 请使用界面「★ 打点标记」按钮" % self.error
        return "全局热键未注册 —— 请使用界面「★ 打点标记」按钮"

    # ---------------- 注册 / 注销 ----------------
    def register(self, app=None, hotkey_text=None):
        """
        注册全局热键。**永远不抛异常**：任何失败都只记录 error 并降级。

        返回 True 表示注册成功。
        """
        self.unregister()
        if hotkey_text:
            self.hotkey_text = hotkey_text
        self.error = ""
        # 【关键】每次注册换一个**全新 id**。
        # 实测：同一线程用同一 id 重新注册不同的组合键必然失败
        # （RegisterHotKey 返回 False，且先 UnregisterHotKey 也救不回来）——
        # 因为 Windows 以 (线程, id, mods, vk) 为注册键，同 id 只能存在一份。
        # 换新 id 后旧的那份由 UnregisterHotKey 释放，新组合即可注册成功。
        self.hotkey_id = _next_hotkey_id()
        if os.name != "nt":
            self.error = "仅支持 Windows"
            return False
        parsed = parse_hotkey(self.hotkey_text)
        if parsed is None:
            self.error = "热键格式无法解析: %s" % self.hotkey_text
            return False
        self.mods, self.vk = parsed
        try:
            user32 = ctypes.windll.user32
            user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                              ctypes.c_uint, ctypes.c_uint]
            user32.RegisterHotKey.restype = ctypes.c_bool
            ok = bool(user32.RegisterHotKey(None, int(self.hotkey_id),
                                            ctypes.c_uint(self.mods),
                                            ctypes.c_uint(self.vk)))
        except Exception as ex:
            self.error = "RegisterHotKey 调用失败: %s" % ex
            return False
        if not ok:
            # 最常见原因：该热键已被其它程序占用
            self.error = "热键 %s 已被其它程序占用" % self.hotkey_text
            return False
        # 注册成功 → 装事件过滤器
        try:
            self._app = app or QtWidgets.QApplication.instance()
            if self._app is None:
                self.error = "没有可用的 QApplication"
                self._unregister_native()
                return False
            self._filter = _HotkeyNativeFilter(self.hotkey_id, self.callback)
            self._app.installNativeEventFilter(self._filter)
        except Exception as ex:
            self.error = "事件过滤器安装失败: %s" % ex
            self._filter = None
            self._unregister_native()
            return False
        self.registered = True
        return True

    def _unregister_native(self):
        try:
            if os.name == "nt":
                ctypes.windll.user32.UnregisterHotKey(None, int(self.hotkey_id))
        except Exception:
            pass

    def unregister(self):
        """注销热键（幂等，重复调用不报错）"""
        try:
            if self._filter is not None and self._app is not None:
                self._app.removeNativeEventFilter(self._filter)
        except Exception:
            pass
        self._filter = None
        self._unregister_native()
        self.registered = False

    def trigger(self):
        """手动触发（界面按钮走这里，与热键共用同一条路径）"""
        try:
            self.callback()
        except Exception:
            pass
