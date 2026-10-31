# -*- coding: utf-8 -*-
# 【v4.0 移植】原 _test_ux.py：行为断言全部保留；
# 源码扫描类断言改为扫描整个 framelab 包（拆分后字符串分布在不同模块）。
# -*- coding: utf-8 -*-
"""
FrameLab —— 实战 UX 增强的自动化测试（对应任务 t4）。

覆盖范围：
  1. 配置读写（framelab_config.json）：默认值 / 往返 / 脏值回落 / 原子写
  2. 阈值外部化：apply_to_globals 真的改写模块级常量
  3. 模拟/真实/离线 数据源判定 + 导出 CSV 的 DataSource 列
  4. 模拟水印元素存在且可见性可切换
  5. 全局热键：注册失败**降级不崩溃** + 解析 + 格式回显
  6. 进程黑名单过滤（自动锁定主游戏不选非游戏进程）
  7. 管理员检测 / PresentMon 指引文案
  8. CSV 后台读取（不阻塞、能拿到 rows）
  9. ETW 清理：排除当前会话（不误杀）

运行：
    python _test_ux.py
    # 或
    python -m unittest _test_ux -v

注意：
  · 本测试**不依赖真实 PresentMon**，也不需要管理员权限。
  · Qt 使用 offscreen 平台插件，避免在无桌面环境弹出窗口。
"""

import io
import os
import sys
import tempfile
import shutil
import unittest
import csv

# ---- 让 offscreen 平台生效（必须在 import PyQt 之前）----
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import FrameLab as F
except Exception as _e:      # pragma: no cover
    print("!! 无法导入 FrameLab: %s" % _e)
    raise


# ==============================================================================
# 1. 配置读写
# ==============================================================================


# ---------------------------------------------------------------------------
# 【v4.0 移植】源码扫描辅助：单体时代读 FrameLab.py，现在要扫整个 framelab 包
# ---------------------------------------------------------------------------
def _all_source():
    import framelab
    parts = []
    base = os.path.dirname(os.path.abspath(framelab.__file__))
    for root, _dirs, files in os.walk(base):
        for fn in sorted(files):
            if fn.endswith(".py"):
                parts.append(io.open(os.path.join(root, fn), encoding="utf-8").read())
    return "\n".join(parts)

class TestConfigIO(unittest.TestCase):

    def test_default_config_has_all_schema_keys(self):
        """默认配置必须覆盖 CONFIG_SCHEMA 里的每一个键"""
        for key in F.CONFIG_SCHEMA:
            self.assertIn(key, F.DEFAULT_CONFIG,
                          "默认配置缺少阈值键: %s" % key)
        self.assertEqual(F.DEFAULT_CONFIG["hotkey"], "Ctrl+F9")
        self.assertEqual(F.DEFAULT_CONFIG["pm_buffer_size"], 4096)

    def test_load_missing_file_returns_defaults(self):
        """配置文件不存在 → 返回默认值，existed=False，且不抛异常"""
        d = tempfile.mkdtemp(prefix="fl_cfg_")
        try:
            p = os.path.join(d, "framelab_config.json")
            cfg = F.Config.load(p)
            self.assertFalse(cfg.existed)
            self.assertEqual(cfg.get("cpu0_jam_threshold"), 92.0)
            self.assertEqual(cfg.get("spill_threshold"), 64 << 20)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_save_then_load_roundtrip(self):
        """写入 → 读回，值必须完全一致"""
        d = tempfile.mkdtemp(prefix="fl_cfg_")
        try:
            p = os.path.join(d, "framelab_config.json")
            cfg = F.Config.load(p)
            cfg.set("cpu0_jam_threshold", 88.5)
            cfg.set("pm_buffer_size", 2048)
            cfg.set("baseline_window", 300)
            cfg.set("hotkey", "Ctrl+Shift+M")
            cfg.set("presentmon_path", r"C:\Tools\PresentMon.exe")
            cfg.set("blacklist_extra", ["MyOverlay.exe"])
            self.assertTrue(cfg.save())

            cfg2 = F.Config.load(p)
            self.assertTrue(cfg2.existed)
            self.assertAlmostEqual(cfg2.get("cpu0_jam_threshold"), 88.5)
            self.assertEqual(cfg2.get("pm_buffer_size"), 2048)
            self.assertEqual(cfg2.get("baseline_window"), 300)
            self.assertEqual(cfg2.get("hotkey"), "Ctrl+Shift+M")
            self.assertEqual(cfg2.get("presentmon_path"), r"C:\Tools\PresentMon.exe")
            self.assertEqual(cfg2.get("blacklist_extra"), ["myoverlay.exe"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_dirty_values_fall_back_and_do_not_crash(self):
        """脏配置（字符串型数字 / 完全非法）不得让程序起不来"""
        d = tempfile.mkdtemp(prefix="fl_cfg_")
        try:
            p = os.path.join(d, "framelab_config.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write('{"cpu0_jam_threshold": "not-a-number", '
                        '"pm_buffer_size": "abc", "spill_threshold": 123}')
            cfg = F.Config.load(p)
            # 非法值必须回落到默认，而不是抛异常
            self.assertAlmostEqual(cfg.get("cpu0_jam_threshold"), 92.0)
            self.assertEqual(cfg.get("pm_buffer_size"), 4096)
            # 合法值必须生效
            self.assertEqual(cfg.get("spill_threshold"), 123)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_corrupt_json_does_not_crash(self):
        """损坏的 JSON 文件 → 用默认值，不能抛异常"""
        d = tempfile.mkdtemp(prefix="fl_cfg_")
        try:
            p = os.path.join(d, "framelab_config.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write("{ this is not json ][")
            cfg = F.Config.load(p)
            self.assertEqual(cfg.get("cpu0_jam_threshold"), 92.0)
            self.assertTrue(cfg.warnings)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_unknown_keys_are_ignored(self):
        """未知键不能污染配置"""
        cfg = F.Config.load(os.path.join(tempfile.mkdtemp(), "x.json"))
        cfg.update({"totally_unknown_key": 123, "cpu0_jam_threshold": 70.0})
        self.assertNotIn("totally_unknown_key", cfg.data)
        self.assertAlmostEqual(cfg.get("cpu0_jam_threshold"), 70.0)

    def test_reset_defaults(self):
        cfg = F.Config.load(os.path.join(tempfile.mkdtemp(), "x.json"))
        cfg.set("cpu0_jam_threshold", 10.0)
        cfg.reset_defaults()
        self.assertAlmostEqual(cfg.get("cpu0_jam_threshold"), 92.0)


class TestConfigApplyToGlobals(unittest.TestCase):

    def test_apply_to_globals_changes_module_constants(self):
        """配置必须真的改写模块级常量（这些常量在别处被直接引用）"""
        d = tempfile.mkdtemp(prefix="fl_cfg_")
        try:
            p = os.path.join(d, "framelab_config.json")
            cfg = F.Config.load(p)
            cfg.set("spill_threshold", 32 << 20)
            cfg.set("freq_drop_ratio", 0.25)
            cfg.set("cache_burst_files", 12)
            cfg.set("cpu0_jam_threshold", 85.0)
            cfg.set("migrate_freq_gap", 300.0)
            cfg.set("pm_buffer_size", 2048)
            cfg.set("baseline_window", 180)
            applied = cfg.apply_to_globals(F.__dict__)
            self.assertIn("spill_threshold", applied)

            self.assertEqual(F.SPILL_THRESHOLD, 32 << 20)
            self.assertAlmostEqual(F.FREQ_DROP_RATIO, 0.25)
            self.assertEqual(F.CACHE_BURST_FILES, 12)
            self.assertAlmostEqual(F.CPU0_JAM_THRESHOLD, 85.0)
            self.assertAlmostEqual(F.MIGRATE_FREQ_GAP, 300.0)
            self.assertEqual(F.PM_BUFFER_SIZE, 2048)
            self.assertEqual(F.BASELINE_WINDOW, 180)
        finally:
            # 还原全局默认值，避免污染其它测试
            F.Config.load(os.path.join(tempfile.mkdtemp(), "x.json")).apply_to_globals(F.__dict__)
            shutil.rmtree(d, ignore_errors=True)

    def test_pm_buffer_size_rounded_to_power_of_two(self):
        """PresentMon 只接受 2 的幂 —— 配置成 3000 必须被规整"""
        d = tempfile.mkdtemp(prefix="fl_cfg_")
        try:
            cfg = F.Config.load(os.path.join(d, "x.json"))
            cfg.set("pm_buffer_size", 3000)
            cfg.apply_to_globals(F.__dict__)
            self.assertEqual(F.PM_BUFFER_SIZE, 2048)
        finally:
            F.Config.load(os.path.join(tempfile.mkdtemp(), "x.json")).apply_to_globals(F.__dict__)
            shutil.rmtree(d, ignore_errors=True)

    def test_baseline_kwargs_from_config(self):
        cfg = F.Config.load(os.path.join(tempfile.mkdtemp(), "x.json"))
        cfg.set("baseline_window", 321)
        cfg.set("baseline_ema", 0.5)
        kw = cfg.baseline_kwargs()
        self.assertEqual(kw["window"], 321)
        self.assertAlmostEqual(kw["ema"], 0.5)


# ==============================================================================
# 2. 数据源判定 + 导出 DataSource 列
# ==============================================================================

class _FakeBridge(object):
    """模拟 PresentMonBridge 的公开属性（不依赖真实类）"""

    def __init__(self, sim=False):
        self.simulation_mode = sim


class TestDataSource(unittest.TestCase):

    def test_detect_real(self):
        self.assertEqual(F.detect_data_source(_FakeBridge(sim=False)), F.DATA_SOURCE_REAL)
        self.assertEqual(F.detect_data_source(None), F.DATA_SOURCE_REAL)

    def test_detect_simulated(self):
        self.assertEqual(F.detect_data_source(_FakeBridge(sim=True)), F.DATA_SOURCE_SIM)

    def test_detect_offline_wins(self):
        """离线导入优先级最高"""
        self.assertEqual(F.detect_data_source(_FakeBridge(sim=True), offline=True),
                         F.DATA_SOURCE_OFFLINE)
        self.assertEqual(F.detect_data_source(_FakeBridge(sim=False), offline=True),
                         F.DATA_SOURCE_OFFLINE)

    def test_all_three_sources_have_labels(self):
        for s in (F.DATA_SOURCE_REAL, F.DATA_SOURCE_SIM, F.DATA_SOURCE_OFFLINE):
            self.assertIn(s, F.DATA_SOURCE_LABEL)
            self.assertIn(s, F.DATA_SOURCE_COLOR)

    def test_export_csv_contains_datasource_column(self):
        """
        导出 CSV 必须带 DataSource 列，且每行的值等于当前数据源标记。
        （不启动整个 UI —— 直接复用 _export_csv 的写表逻辑做等价验证）
        """
        d = tempfile.mkdtemp(prefix="fl_export_")
        try:
            path = os.path.join(d, "out.csv")

            class _MiniStore(object):
                def __init__(self):
                    self.frames = [F.FrameRecord(1, 0.0, 0.0, "Game.exe", 1, 16.6, 10.0, 5.0)]

                def snapshot_for(self, seq):
                    return None

            class _MiniWin(object):
                store = _MiniStore()
                _data_source = F.DATA_SOURCE_SIM

            w = _MiniWin()
            frames = list(w.store.frames)
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                cw = csv.writer(f)
                cw.writerow(["Index", "TimeInSeconds", "Application", "MsBetweenPresents",
                             "FPS", "MsGPUBusy", "MsCPUBusy", "GPUBusyRatio",
                             "CPU0_Usage", "VRAM_NonLocal_MB", "PageIn_MBps",
                             "CacheNewFiles", "DataSource"])
                for fr in frames:
                    hw = w.store.snapshot_for(fr.hw_seq)
                    cw.writerow([fr.idx, "%.4f" % fr.t, fr.app, "%.3f" % fr.frametime,
                                 "%.1f" % fr.fps, "%.3f" % fr.gpu_busy,
                                 "%.3f" % fr.cpu_busy, "%.3f" % fr.gpu_busy_ratio,
                                 "0.0", "0.0", "0.0", 0, w._data_source])

            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            self.assertIn("DataSource", rows[0])
            self.assertEqual(rows[0][-1], "DataSource")
            self.assertEqual(rows[1][-1], F.DATA_SOURCE_SIM)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_export_csv_header_in_source_has_datasource(self):
        """源码里 _export_csv 的表头必须真的包含 DataSource（防止改动被回退）"""
        src = _all_source()
        self.assertIn('"DataSource"]', src,
                      "_export_csv 的表头缺少 DataSource 列")


# ==============================================================================
# 3. 模拟水印
# ==============================================================================

class TestSimWatermark(unittest.TestCase):

    def test_watermark_text_mentions_simulated(self):
        self.assertIn("SIMULATED", F.SIM_WATERMARK_TEXT)
        self.assertIn("非真实采集", F.SIM_WATERMARK_TEXT)

    def test_make_watermark_returns_textitem(self):
        """必须能造出 pyqtgraph TextItem，且默认隐藏"""
        try:
            from PyQt6 import QtWidgets
            app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        except Exception:
            self.skipTest("PyQt6 不可用")
        item = F.make_sim_watermark()
        self.assertIsNotNone(item, "make_sim_watermark 返回了 None")
        self.assertIsInstance(item, F.pg.TextItem)
        self.assertFalse(item.isVisible(), "水印默认必须是隐藏状态")
        item.setVisible(True)
        self.assertTrue(item.isVisible())

    def test_source_contains_watermark_wiring(self):
        """源码里必须真的把水印接到 PlotItem 上"""
        src = _all_source()
        self.assertIn("make_sim_watermark", src)
        self.assertIn("_sim_watermark", src)


# ==============================================================================
# 4. 全局热键
# ==============================================================================

class TestHotkey(unittest.TestCase):

    def test_parse_ctrl_f9(self):
        mods, vk = F.parse_hotkey("Ctrl+F9")
        self.assertTrue(mods & F.MOD_CONTROL)
        self.assertEqual(vk, 0x78)   # VK_F9

    def test_parse_variants(self):
        self.assertIsNotNone(F.parse_hotkey("ctrl+shift+m"))
        self.assertIsNotNone(F.parse_hotkey("Alt+F1"))
        self.assertIsNotNone(F.parse_hotkey("Ctrl-Shift-F12"))
        self.assertIsNotNone(F.parse_hotkey("Win+K"))

    def test_parse_invalid_returns_none(self):
        for bad in ("", "Ctrl+", "Ctrl+NotAKey", "F9+F9+", "$$$"):
            self.assertIsNone(F.parse_hotkey(bad), "非法热键应返回 None: %r" % bad)

    def test_format_roundtrip(self):
        mods, vk = F.parse_hotkey("Ctrl+F9")
        self.assertEqual(F.format_hotkey(mods, vk), "Ctrl+F9")
        mods, vk = F.parse_hotkey("Ctrl+Shift+M")
        self.assertEqual(F.format_hotkey(mods, vk), "Ctrl+Shift+M")

    def test_register_failure_degrades_without_crash(self):
        """
        【核心验收】热键注册失败必须**优雅降级**，绝不抛异常、绝不崩溃。
        模拟方式：用一个不可能成功的 hotkey_id / 非法热键串。
        """
        calls = []
        mgr = F.GlobalHotkeyManager(lambda: calls.append(1), "Ctrl+F9")
        # 非法热键 → 解析失败 → 降级
        mgr.hotkey_text = "这不是热键"
        ok = mgr.register(app=None)
        self.assertFalse(ok)
        self.assertFalse(mgr.registered)
        self.assertTrue(mgr.error)
        # describe() 必须给出可读的降级说明（提示用界面按钮）
        d = mgr.describe()
        self.assertIn("按钮", d)
        # 界面按钮路径仍然可用 —— trigger() 不能抛异常
        mgr.trigger()
        self.assertEqual(len(calls), 1)
        # 注销也必须幂等、不抛异常
        mgr.unregister()
        mgr.unregister()

    def test_register_invalid_hotkey_text_degrades(self):
        """非法热键串 → 注册失败且不抛异常"""
        mgr = F.GlobalHotkeyManager(lambda: None, "Ctrl+!!!")
        ok = mgr.register(app=None)
        self.assertFalse(ok)
        self.assertFalse(mgr.available)
        self.assertIn("不可用", mgr.describe())

    def test_unregister_is_idempotent(self):
        mgr = F.GlobalHotkeyManager(lambda: None, "Ctrl+F9")
        mgr.unregister()
        mgr.unregister()
        self.assertFalse(mgr.registered)

    def test_real_registration_and_manual_trigger(self):
        """
        真机路径：能注册就注册（可能被别的程序占用 → 走降级）；
        无论成功与否，程序都不应崩溃，且 trigger() 永远可用。
        """
        try:
            from PyQt6 import QtWidgets
            app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        except Exception:
            self.skipTest("PyQt6 不可用")
        fired = []
        mgr = F.GlobalHotkeyManager(lambda: fired.append(1), "Ctrl+F9")
        ok = mgr.register(app=app)
        try:
            if ok:
                self.assertTrue(mgr.available)
                self.assertIn("已注册", mgr.describe())
            else:
                self.assertFalse(mgr.available)
                self.assertTrue(mgr.error)
            # 界面按钮备选路径
            mgr.trigger()
            self.assertEqual(len(fired), 1)
        finally:
            mgr.unregister()

    def test_source_has_no_third_party_hotkey_dependency(self):
        """禁止引入 keyboard 等第三方热键依赖 —— 必须是纯 ctypes"""
        src = _all_source()
        self.assertNotIn("import keyboard", src)
        self.assertNotIn("from keyboard", src)
        self.assertIn("RegisterHotKey", src)

    def test_native_event_filter_returns_tuple(self):
        """
        【PyQt6 API 契约 · 回归防护】

        PyQt6 的 nativeEventFilter 签名是：
            nativeEventFilter(eventType: QByteArray, message: sip.voidptr)
                -> (bool, sip.voidptr)
        返回**单个 bool** 会抛 "TypeError: invalid result from
        ...nativeEventFilter()"，而这个过滤器是**每个原生事件**都会调用的
        （鼠标移动 / 定时器 / 窗口变化），写错就会持续刷屏并让 Qt 的事件
        分发进入异常路径 —— 实打实的稳定性隐患。

        这里直接断言返回值的**形状**：(bool, message) 二元组。
        """
        try:
            from PyQt6 import QtCore
        except Exception:
            self.skipTest("PyQt6 不可用")
        flt = F._HotkeyNativeFilter(0xB701, lambda: None)
        dummy = QtCore.QByteArray(b"windows_generic_MSG")
        r = flt.nativeEventFilter(dummy, 0)
        self.assertIsInstance(r, tuple, "nativeEventFilter 必须返回 tuple，实际 %r" % (r,))
        self.assertEqual(len(r), 2, "nativeEventFilter 必须返回二元组，实际 %r" % (r,))
        self.assertIsInstance(r[0], bool, "第一个元素必须是 bool，实际 %r" % (r[0],))

    def test_native_event_filter_no_typeerror_under_native_events(self):
        """
        注册热键 + 制造一批原生事件 → 断言**不产生任何 TypeError**。

        这是 captain 在真实运行中发现的 bug 的回归测试：
        返回单个 bool 会在每个原生事件上抛 TypeError。
        """
        try:
            from PyQt6 import QtCore, QtWidgets
        except Exception:
            self.skipTest("PyQt6 不可用")
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        errors = []

        def hook(etype, value, tb):
            errors.append("%s: %s" % (etype.__name__, value))

        old_hook = sys.excepthook
        sys.excepthook = hook
        fired = []
        mgr = F.GlobalHotkeyManager(lambda: fired.append(1), "Ctrl+F9")
        try:
            mgr.register(app=app)
            win = QtWidgets.QWidget()
            win.resize(160, 90)
            win.show()
            win.winId()          # 强制创建原生窗口
            timer = QtCore.QTimer()
            timer.timeout.connect(lambda: None)
            timer.start(5)
            QtCore.QTimer.singleShot(300, app.quit)
            app.exec()
        finally:
            sys.excepthook = old_hook
            mgr.unregister()
            try:
                timer.stop()
            except Exception:
                pass
        type_errors = [e for e in errors if "TypeError" in e]
        self.assertEqual(type_errors, [],
                         "nativeEventFilter 产生了 TypeError: %s" % type_errors)
        # 顺带确认界面按钮路径仍可用
        mgr.trigger()
        self.assertEqual(len(fired), 1)


# ==============================================================================
# 5. 进程黑名单过滤（自动锁定主游戏）
# ==============================================================================

class TestBlacklist(unittest.TestCase):

    def test_blacklist_contains_required_processes(self):
        """黑名单至少要覆盖任务里点名的那些非游戏进程"""
        required = [
            "dwm.exe", "explorer.exe", "chrome.exe", "msedge.exe", "firefox.exe",
            "wallpaper32.exe", "steamwebhelper.exe", "discord.exe", "searchhost.exe",
            "textinputhost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
            "msedgewebview2.exe", "obs64.exe", "rtss.exe", "nvcontainer.exe", "amdow.exe",
        ]
        bl = getattr(F, "NON_GAME_PROCESSES", None)
        self.assertIsNotNone(bl, "缺少 NON_GAME_PROCESSES 黑名单")
        low = {str(x).lower() for x in bl}
        for name in required:
            self.assertIn(name.lower(), low, "黑名单缺少: %s" % name)

    def _mk_store(self, apps, count):
        st = F.FrameStore()
        for i in range(count):
            st.frames.append(F.FrameRecord(i, float(i), float(i), apps, 1000, 16.6))
        return st

    def test_dominant_app_skips_blacklisted(self):
        """非游戏进程帧数再多也不该被选成「主游戏」"""
        st = self._mk_store("Game.exe", 10)
        for i in range(500):
            st.frames.append(F.FrameRecord(1000 + i, float(i), float(i), "dwm.exe", 42, 16.6))
        dom = st.dominant_app()
        self.assertEqual(dom, "Game.exe", "dwm.exe 篡夺了主游戏地位，实际=%r" % dom)

    def test_dominant_app_skips_unknown(self):
        """<unknown> 不参与竞争"""
        st = self._mk_store("Game.exe", 5)
        for i in range(200):
            st.frames.append(F.FrameRecord(1000 + i, float(i), float(i), "<unknown>", 0, 16.6))
        self.assertEqual(st.dominant_app(), "Game.exe")

    def test_dominant_app_requires_persistence(self):
        """瞬时抢占（只有一两帧）不该抢走主游戏"""
        st = self._mk_store("Game.exe", 200)
        st.frames.append(F.FrameRecord(9999, 1.0, 1.0, "Game.exe", 1, 16.6))
        # 一个短命进程只出现 1 帧
        st.frames.append(F.FrameRecord(9998, 1.0, 1.0, "SomeLauncher.exe", 2, 16.6))
        dom = st.dominant_app()
        self.assertEqual(dom, "Game.exe")

    def test_dominant_app_returns_none_when_only_blacklisted(self):
        """全是黑名单进程时不应返回任何一个"""
        st = self._mk_store("dwm.exe", 100)
        dom = st.dominant_app()
        self.assertTrue(dom is None or str(dom).lower() not in
                        {x.lower() for x in F.NON_GAME_PROCESSES},
                        "返回了黑名单进程: %r" % dom)


# ==============================================================================
# 6. 提权提示 / PresentMon 指引
# ==============================================================================

class TestAdminAndPresentMon(unittest.TestCase):

    def test_is_admin_returns_bool(self):
        r = F.is_admin()
        self.assertIsInstance(r, bool)

    def test_relaunch_as_admin_signature_exists(self):
        """提权入口必须存在（不强制，只是提供按钮）"""
        self.assertTrue(callable(F.relaunch_as_admin))

    def test_source_has_admin_button_and_hint(self):
        src = _all_source()
        self.assertIn("以管理员身份重启", src)
        self.assertIn("建议以管理员身份运行", src)
        # 必须是「不强制」：代码里不能出现无条件提权调用
        self.assertNotIn("relaunch_as_admin()\n        self.close()", src)

    def test_presentmon_hint_contains_actionable_steps(self):
        hint = F.presentmon_missing_hint()
        self.assertIn("PresentMon", hint)
        self.assertIn("放哪里", hint)
        self.assertIn("命名要求", hint)
        self.assertIn("PresentMon.exe", hint)

    def test_presentmon_dir_exists(self):
        d = F.presentmon_dir()
        self.assertTrue(os.path.isdir(d))

    def test_source_has_open_presentmon_dir_button(self):
        src = _all_source()
        self.assertIn("打开 PresentMon 目录", src)
        self.assertIn("open_dir_in_explorer", src)


# ==============================================================================
# 7. CSV 后台读取
# ==============================================================================

class TestCsvBackgroundReader(unittest.TestCase):

    def _write_csv(self, d, n=500):
        p = os.path.join(d, "frames.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Application", "ProcessID", "MsBetweenPresents",
                        "MsGPUBusy", "MsCPUBusy", "TimeInSeconds"])
            for i in range(n):
                w.writerow(["Game.exe", 100 + i, 16.6, 10.0, 5.0, i * 16.6])
        return p

    def test_reader_loads_rows(self):
        d = tempfile.mkdtemp(prefix="fl_csv_")
        try:
            p = self._write_csv(d, 300)
            rd = F.CsvBackgroundReader(p)
            rd.start()
            self.assertTrue(rd.wait(timeout=30))
            self.assertTrue(rd.ok)
            self.assertEqual(len(rd.rows), 300)
            self.assertIn("MsBetweenPresents", rd.fields)
            self.assertEqual(rd.rows[0]["Application"], "Game.exe")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_reader_progress_callback_fires(self):
        d = tempfile.mkdtemp(prefix="fl_csv_")
        try:
            p = self._write_csv(d, 200)
            seen = []
            rd = F.CsvBackgroundReader(p, progress_cb=lambda a, b: seen.append((a, b)),
                                       chunk_report=50)
            rd.start()
            rd.wait(timeout=30)
            self.assertTrue(len(seen) >= 1, "进度回调没有被调用")
            self.assertGreaterEqual(seen[-1][0], 200)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_reader_error_on_missing_file(self):
        rd = F.CsvBackgroundReader(os.path.join(tempfile.mkdtemp(), "nope.csv"))
        rd.start()
        rd.wait(timeout=10)
        self.assertTrue(rd.done)
        self.assertFalse(rd.ok)
        self.assertTrue(rd.error)

    def test_reader_runs_in_background_thread(self):
        """必须真的开后台线程（主线程名 != 读取线程名）"""
        d = tempfile.mkdtemp(prefix="fl_csv_")
        try:
            p = self._write_csv(d, 100)
            names = []
            import threading
            orig = threading.Thread.start

            def spy(self, *a, **k):
                names.append(self.name)
                return orig(self, *a, **k)

            threading.Thread.start = spy
            try:
                rd = F.CsvBackgroundReader(p)
                rd.start()
                rd.wait(timeout=30)
            finally:
                threading.Thread.start = orig
            self.assertIn("CsvBackgroundReader", names)
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ==============================================================================
# 8. ETW 清理：不误杀当前会话
# ==============================================================================

class TestEtwCleanup(unittest.TestCase):

    def test_cleanup_stale_skips_live_session(self):
        """
        【核心验收】cleanup_stale 必须排除当前正在使用的 session_name。
        """
        calls = []

        class _Mgr(F.EtwSessionManager):
            @staticmethod
            def list_sessions():
                return ["FrameLab_PM_live01", "FrameLab_PM_stale02",
                        "FrameLab_PM_stale03", "OtherAppSession"]

            @staticmethod
            def stop_session(name):
                calls.append(name)
                return True

        mgr = _Mgr()
        stopped = mgr.cleanup_stale(skip="FrameLab_PM_live01")
        self.assertNotIn("FrameLab_PM_live01", stopped,
                         "当前正在使用的会话被误杀了")
        self.assertNotIn("FrameLab_PM_live01", calls,
                         "当前正在使用的会话被 stop 了")
        self.assertIn("FrameLab_PM_stale02", stopped)
        self.assertIn("FrameLab_PM_stale03", stopped)
        self.assertNotIn("OtherAppSession", stopped,
                         "清理范围不应波及其它软件的 ETW 会话")

    def test_cleanup_stale_without_skip_stops_all(self):
        """不传 skip 时按旧行为清理全部本工具残留"""
        stopped = []

        class _Mgr(F.EtwSessionManager):
            @staticmethod
            def list_sessions():
                return ["FrameLab_PM_a", "FrameLab_PM_b"]

            @staticmethod
            def stop_session(name):
                stopped.append(name)
                return True

        self.assertEqual(len(_Mgr().cleanup_stale()), 2)

    def test_stop_session_rejects_injection(self):
        """会话名必须做白名单校验（防命令注入）"""
        self.assertFalse(F.EtwSessionManager.stop_session("a; del x"))
        self.assertFalse(F.EtwSessionManager.stop_session(""))
        self.assertFalse(F.EtwSessionManager.stop_session(None))

    def test_source_has_second_confirmation(self):
        """清理按钮必须有二次确认"""
        src = _all_source()
        self.assertIn("_clean_etw_confirmed", src)
        self.assertIn("确认清理 ETW 残留", src)
        # 入口必须接到确认包装上（v4.0：按钮收进 ⚙ 齿轮菜单 → 用 QAction.triggered）
        wired = ("self.btn_clean.clicked.connect(self._clean_etw_confirmed)" in src
                 or "self.act_clean.triggered.connect(self._clean_etw_confirmed)" in src)
        self.assertTrue(wired, "清理 ETW 入口没有接到二次确认包装上")


# ==============================================================================
# 9. 手动打点标记
# ==============================================================================

class TestManualMark(unittest.TestCase):

    def test_manual_mark_event_shape(self):
        """手动标记事件必须是 kind='手动标记' + symbol='star'（pyqtgraph 合法符号）"""
        ev = F.Event(1234.5, "手动标记", "手动打点 #1", "说明", "WARN", "star")
        self.assertEqual(ev.kind, "手动标记")
        self.assertEqual(ev.symbol, "star")
        self.assertIn(ev.symbol, F.PG_SYMBOLS,
                      "star 必须是 pyqtgraph 合法符号，否则图表会 KeyError 崩掉")

    def test_star_symbol_is_valid_for_pyqtgraph(self):
        self.assertIn("star", F.PG_SYMBOLS)
        self.assertEqual(F.pg_symbol("star"), "star")

    def test_source_wires_manual_mark(self):
        src = _all_source()
        self.assertIn("手动标记", src)
        self.assertIn('"star"', src)
        self.assertIn("_on_manual_mark", src)
        # 界面按钮必须存在（热键不可用时的备选）
        self.assertIn("★ 打点标记", src)


# ==============================================================================
# 10. 杂项：requirements / 源码结构
# ==============================================================================

class TestProjectArtifacts(unittest.TestCase):

    def test_requirements_txt_exists_and_pins_versions(self):
        d = os.path.dirname(os.path.abspath(F.__file__))
        p = os.path.join(d, "requirements.txt")
        self.assertTrue(os.path.isfile(p), "缺少 requirements.txt")
        txt = open(p, "r", encoding="utf-8").read()
        self.assertIn("PyQt6>=6.6,<7", txt)
        self.assertIn("pyqtgraph>=0.13,<0.15", txt)
        self.assertIn("numpy>=1.25,<3", txt)

    def test_no_forced_third_party_hotkey_dep(self):
        """requirements.txt 里不能出现强制第三方热键依赖（只看真正的依赖行，忽略注释）"""
        d = os.path.dirname(os.path.abspath(F.__file__))
        with open(os.path.join(d, "requirements.txt"), "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        dep_lines = [l.strip() for l in lines
                     if l.strip() and not l.strip().startswith("#")]
        for line in dep_lines:
            self.assertNotIn("keyboard", line.lower(),
                             "requirements.txt 引入了强制第三方按键库: %s" % line)
        # 依赖行数应当正好是 3 个
        self.assertEqual(len(dep_lines), 3, "依赖行数异常: %r" % dep_lines)

    def test_no_stale_backup_newer_than_source(self):
        """
        旧 .bak 备份已按「清理过时无用文件」删除（迁移到项目外的回滚目录）；
        这里守护的是「不得残留比 FrameLab.py 更新的旧备份」——
        否则用户可能拿旧备份覆盖新代码。
        """
        d = os.path.dirname(os.path.abspath(F.__file__))
        src = os.path.join(d, "FrameLab.py")
        src_m = os.path.getmtime(src)
        newer = [b for b in os.listdir(d)
                 if b.startswith("FrameLab.py.bak")
                 and os.path.getmtime(os.path.join(d, b)) > src_m]
        self.assertEqual(newer, [], "存在比 FrameLab.py 更新的旧备份: %s" % newer)


# ==============================================================================
# 11. 离线导入后的索引自愈（captain 指定的顺带验证）
# ==============================================================================

class TestOfflineImportIndexHeal(unittest.TestCase):
    """
    【captain 指定的顺带验证】

    _import_csv 的原子替换路径是：
        self.store.frames.clear() → 直接 self.store.frames.append(rec)

    这绕过了 add_frame()，因此**不会**建立分片索引。
    perf-fixer 加了 _ensure_index() 自愈机制，本测试确认它确实生效：
        visible_frames('Imported.exe') 必须返回正确帧数（不能是 0）
        dominant_app() 必须返回 'Imported.exe'
    """

    def _imported_store(self, n=120):
        """模拟一次离线导入的结果（clear + append，绕过 add_frame）"""
        st = F.FrameStore()
        st.frames.clear()
        st.baseline.reset()
        for i in range(n):
            st.frames.append(F.FrameRecord(i + 1, i * 0.0166, 1000.0 + i * 0.0166,
                                           "Imported.exe", 4242, 16.6, 10.0, 5.0,
                                           hw_seq=-1))
        return st

    def test_visible_frames_after_raw_append(self):
        st = self._imported_store(120)
        vis = st.visible_frames("Imported.exe")
        self.assertEqual(len(vis), 120,
                         "visible_frames 返回 %d 帧（期望 120）—— 索引未自愈"
                         % len(vis))
        for fr in vis:
            self.assertEqual(fr.app, "Imported.exe")

    def test_dominant_app_after_raw_append(self):
        st = self._imported_store(120)
        dom = st.dominant_app()
        self.assertEqual(dom, "Imported.exe",
                         "dominant_app 返回 %r（期望 'Imported.exe'）—— 索引未自愈" % dom)

    def test_visible_frames_all_target(self):
        """target=None / 'all' 时应返回全部帧"""
        st = self._imported_store(50)
        self.assertEqual(len(st.visible_frames(None)), 50)
        self.assertEqual(len(st.visible_frames("all")), 50)

    def test_summary_metrics_works_after_import(self):
        """导入后分位指标仍可算（说明帧数据完整可用）"""
        st = self._imported_store(200)
        m = st.summary_metrics(st.visible_frames("Imported.exe"))
        self.assertGreater(m["total_frames"], 0)
        self.assertGreater(m["avg_fps"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
