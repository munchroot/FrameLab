# -*- mode: python ; coding: utf-8 -*-
"""
FrameLab 的 PyInstaller 打包配置（单文件 EXE）。

用法（不要直接双击，走 build_tools/build_exe.ps1，它会把产物放到 dist/）：
    & "<venv>\Scripts\python.exe" -m PyInstaller --noconfirm --clean `
        --distpath "<project>\dist" --workpath "<build-tmp>" `
        "<project>\build_tools\FrameLab.spec"

要点：
  · 入口仍是 FrameLab.py（薄启动器），所以打包后行为与源码运行完全一致；
  · framelab/paths.py 已按 sys.frozen 处理「程序目录 = exe 所在目录」，
    因此 exe 旁边的 framelab_config.json 就是用户配置（便携式）；
  · 把 framelab 全部子模块显式列为 hiddenimports —— 项目里有若干
    「函数内延迟导入」（例如 vram_bar_text / build_palette 在用到时才 import），
    显式列出可避免个别子模块被静态分析漏掉；
  · 排除 tkinter / matplotlib / scipy 等完全用不到的重型依赖，控制体积。
"""
import os

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))          # <project>/build_tools
PROJ = os.path.dirname(SPEC_DIR)                           # <project>
ICON = os.path.join(PROJ, "assets", "framelab.ico")
VERSION_FILE = os.path.join(SPEC_DIR, "version_info.txt")

# ---- 隐藏导入：项目自身全部子模块 + pyqtgraph 的图形项 ----
hiddenimports = collect_submodules("framelab")
hiddenimports += [
    "pyqtgraph",
    "pyqtgraph.graphicsItems",
    "pyqtgraph.widgets",
]

# ---- 排除：本项目完全没有用到的重型/无关依赖 ----
excludes = [
    "tkinter",
    "unittest",
    "pydoc_data",
    "lib2to3",
    "test",
    "matplotlib",
    "scipy",
    "pandas",
    "PIL",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "sphinx",
    "setuptools",
    "pkg_resources",
]

# ---- 随包探针：PresentMon（v1.0.5）----
# 把 PresentMon 打进 EXE，解包到 `_MEIPASS/PresentMon/`。这样把 EXE 拷给别人、
# 对方机器没装 AMD/CapFrameX 时也能真实采集（否则 find_presentmon_binary()
# 找不到 → 桥接自动退化成模拟数据，界面显示「非真实采样」）。
# PresentMon 是 Intel 开源的 MIT 项目；这里内嵌的是官方发行版
# PresentMon-2.6.0-x64.exe（用户提供，D:\迅雷下载\），能力探测全绿
# （含 --v2_metrics / --set_circular_buffer_size / --qpc_time_ms 等）。
_PM_SRC = os.path.join(PROJ, "external", "PresentMon", "PresentMon-x64.exe")
datas = []
if os.path.isfile(_PM_SRC):
    datas.append((_PM_SRC, "PresentMon"))
else:  # 缺文件不阻断构建，但必须显式喊出来
    print("[FrameLab.spec] WARNING: external/PresentMon/PresentMon-x64.exe 不存在，"
          "打包后的 EXE 将没有内置 PresentMon")

a = Analysis(
    [os.path.join(PROJ, "FrameLab.py")],
    pathex=[PROJ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FrameLab",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # UPX 压缩容易触发杀软误报，关掉
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # GUI 程序：不弹黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON if os.path.isfile(ICON) else None,
    version=VERSION_FILE if os.path.isfile(VERSION_FILE) else None,
)
