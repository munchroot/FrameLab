# -*- coding: utf-8 -*-
"""
拆分后的**架构不变量**测试（纯静态 + 少量导入期断言）。

为什么需要它：拆成多模块后最容易悄悄坏掉的两件事是
  ① 某个模块漏了 import（跑起来才 NameError，且往往在冷门分支里）；
  ② 有人写了 `from ..theme import ACCENT` / `from ..consts import SPILL_THRESHOLD`
     —— 导入即冻结，主题热切换 / 配置热更新在那些模块里**静默失效**。
本测试把这两条变成可执行的红线。

运行： python tests/test_static_integrity.py
"""
import ast
import builtins
import importlib
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

import framelab                                              # noqa: E402
from framelab import consts, theme                           # noqa: E402

PKG = os.path.dirname(os.path.abspath(framelab.__file__))

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("OK  " if cond else "FAIL", name, detail))
    if not cond:
        FAILS.append(name)


def iter_modules():
    for root, _dirs, files in os.walk(PKG):
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, PKG)[:-3].replace(os.sep, ".")
            if rel.endswith(".__init__"):
                rel = rel[:-len(".__init__")]
            dotted = "framelab" if rel in ("", "__init__") else "framelab." + rel
            yield dotted, p


def free_names(tree):
    loads, stores = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            (loads if isinstance(n.ctx, ast.Load) else stores).add(n.id)
        elif isinstance(n, ast.arg):
            stores.add(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stores.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            stores.add(n.name)
        elif isinstance(n, ast.Global):
            stores.update(n.names)
        elif isinstance(n, (ast.alias,)):
            stores.add((n.asname or n.name).split(".")[0])
    return loads - stores - set(dir(builtins))


def main():
    print("== 1. 每个模块都能编译且自由名字可解析（防漏 import）==")
    mods = list(iter_modules())
    check("模块数量 >= 20", len(mods) >= 20, "%d 个" % len(mods))
    for dotted, path in mods:
        src = io.open(path, encoding="utf-8").read()
        try:
            tree = ast.parse(src)
        except SyntaxError as ex:
            check("%s 语法正确" % dotted, False, str(ex))
            continue
        try:
            mod = importlib.import_module(dotted)
        except Exception as ex:
            check("%s 可导入" % dotted, False, "%s: %s" % (type(ex).__name__, ex))
            continue
        ns = vars(mod)
        missing = sorted(n for n in free_names(tree) if n not in ns)
        check("%s 无未定义全局名" % dotted, not missing, ", ".join(missing))

    print("== 2. 主题色 / 阈值常量禁止 import 冻结 ==")
    live_theme = ("BG", "PANEL", "PANEL2", "BORDER", "BORDER_HOT", "FG", "DIM",
                  "ACCENT", "ACCENT_D", "WARN", "BAD", "PURPLE", "ORANGE", "GREEN",
                  "CURRENT_THEME", "CURRENT_CHART_STYLE")
    live_const = tuple(consts.LIVE_CONST_NAMES)
    bad = []
    for dotted, path in mods:
        if dotted in ("framelab.theme", "framelab.consts"):
            continue
        src = io.open(path, encoding="utf-8").read()
        shim = dotted == "framelab"
        # 「安全」的判定：模块导入时把自己登记进主题注册表（bind_globals(globals())）
        bound_theme = ("bind_globals" in src and "globals()" in src) or shim
        for m in re.finditer(r"^from\s+(\.+[\w.]*theme)\s+import\s+\(?([^)\n]+)\)?", src, re.M):
            names = [x.strip() for x in m.group(2).split(",")]
            for n in names:
                if n in live_theme and not bound_theme:
                    bad.append("%s: from %s import %s（应改用 bind_globals）"
                               % (dotted, m.group(1), n))
        for m in re.finditer(r"^from\s+(\.+[\w.]*consts)\s+import\s+\(?([^)\n]+)\)?", src, re.M):
            names = [x.strip() for x in m.group(2).split(",")]
            for n in names:
                if n in live_const:
                    bad.append("%s: from %s import %s（阈值会热更新失效）"
                               % (dotted, m.group(1), n))
    check("无冻结式 import", not bad, " | ".join(bad))

    print("== 3. 主题热切换真的广播到所有 UI 模块 ==")
    from framelab.ui import base_window as _bw, chart as _chart, navtabs as _nav
    from framelab.ui import diagnosis as _dg, gauge as _gauge, main_window as _mw
    from framelab.ui import pages as _pages
    targets = [_bw, _bw, _mw, _gauge, _nav, _chart, _dg, _pages]
    names = ["base_window", "base_window", "main_window", "gauge", "navtabs", "chart",
             "diagnosis", "pages"]
    theme.apply_theme_to_globals(theme.THEME_LIGHT)
    miss = []
    for name, mod in zip(names, targets):
        # 有 ACCENT 副本的模块必须跟随；没有副本的必须是「实时读 T.CURRENT_THEME」
        if hasattr(mod, "ACCENT"):
            if mod.ACCENT != theme.THEME_LIGHT.accent:
                miss.append("%s.ACCENT 未跟随" % name)
            if hasattr(mod, "CURRENT_THEME") and mod.CURRENT_THEME.key != "light":
                miss.append("%s.CURRENT_THEME 未跟随" % name)
        elif hasattr(mod, "CURRENT_THEME"):
            if mod.CURRENT_THEME.key != "light":
                miss.append("%s.CURRENT_THEME 未跟随" % name)
        else:
            src = io.open(mod.__file__, encoding="utf-8").read()
            if "T.CURRENT_THEME" not in src and "theme.CURRENT_THEME" not in src:
                miss.append("%s 既无主题副本也不实时读主题" % name)
    theme.apply_theme_to_globals(theme.THEME_DEEP_SPACE)
    check("所有 UI 模块跟随主题", not miss, " | ".join(miss))

    print("== 4. 拆分后仍只有一个「程序目录」来源 ==")
    offenders = []
    for dotted, path in mods:
        if dotted in ("framelab.paths",):
            continue
        src = io.open(path, encoding="utf-8").read()
        for m in re.finditer(r"os\.path\.dirname\(os\.path\.abspath\(__file__\)\)", src):
            offenders.append(dotted)
            break
    check("路径都走 framelab.paths.app_dir()", not offenders, ", ".join(offenders))

    print("== 5. 顶层 shim 仍能导出旧名字 ==")
    import FrameLab as F
    legacy = ("FrameLabWindow", "FrameRecord", "HardwareSnapshot", "FrameStore", "Config",
              "CONFIG_SCHEMA", "DEFAULT_CONFIG", "DIAG", "DiagLog", "PG_SYMBOLS",
              "DATA_SOURCE_SIM", "GlobalHotkeyManager", "SnapshotDiffInspector",
              "theme_from_custom_bg", "apply_theme_to_globals", "ACCENT", "MOD_CONTROL",
              "PresentMonBridge", "HardwareEngine", "window_stats", "bottleneck")
    missing = [n for n in legacy if not hasattr(F, n)]
    check("旧顶层名字齐全", not missing, ", ".join(missing))
    check("config_path 指向项目根（不是包目录）",
          os.path.dirname(os.path.abspath(F.config_path())) == os.path.abspath(ROOT),
          F.config_path())

    print("== 6. 随包 PresentMon（EXE 拷给别人也能真实采集）==")
    pm_src = os.path.join(ROOT, "external", "PresentMon", "PresentMon-x64.exe")
    check("仓库内有 PresentMon 副本", os.path.isfile(pm_src), pm_src)
    spec_src = io.open(os.path.join(ROOT, "build_tools", "FrameLab.spec"),
                       encoding="utf-8").read()
    check("打包脚本把 PresentMon 打进 EXE",
          "PresentMon" in spec_src and "datas" in spec_src, "spec 未引用 PresentMon")
    from framelab import paths as P
    from framelab.engines.presentmon import find_presentmon_binary
    check("bundle_dir 在源码运行 = 项目根",
          os.path.normcase(P.bundle_dir()) == os.path.normcase(ROOT), P.bundle_dir())
    got = find_presentmon_binary()
    check("源码运行能找到 PresentMon", bool(got), str(got))
    # 模拟 PyInstaller 冻结态：sys.frozen=True + sys._MEIPASS=临时解包目录
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="fl_meipass_")
    os.makedirs(os.path.join(tmp, "PresentMon"))
    if os.path.isfile(pm_src):
        shutil.copy2(pm_src, os.path.join(tmp, "PresentMon", "PresentMon-x64.exe"))
    old_frozen = getattr(sys, "frozen", None)
    old_mei = getattr(sys, "_MEIPASS", None)
    try:
        sys.frozen = True
        sys._MEIPASS = tmp
        frozen_got = find_presentmon_binary()
    finally:
        if old_frozen is None:
            try:
                del sys.frozen
            except Exception:
                pass
        else:
            sys.frozen = old_frozen
        if old_mei is None:
            try:
                del sys._MEIPASS
            except Exception:
                pass
        else:
            sys._MEIPASS = old_mei
        shutil.rmtree(tmp, ignore_errors=True)
    check("冻结态优先使用 _MEIPASS 内置副本",
          bool(frozen_got) and os.path.normcase(frozen_got).startswith(
              os.path.normcase(tmp)),
          str(frozen_got))

    print()
    if FAILS:
        print("FAILED: %d -> %s" % (len(FAILS), FAILS))
        return 1
    print("ALL STATIC INTEGRITY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
