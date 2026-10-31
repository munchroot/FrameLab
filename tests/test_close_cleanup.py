# -*- coding: utf-8 -*-
"""
退出清理回归测试（v4.3）。

【修的 Bug】「关闭后后台还有残留、窗口跑到屏幕上方」
根因有两条：
  1. 启动的「权限提醒」用 `QDialog.open()` —— Qt 会把它变成 **WindowModal**，
     主窗口的 WM_CLOSE 被模态过滤器吞掉 → 用户点 X 没反应 →
     只能去任务管理器强杀 → 对话框留在屏幕上、PresentMon 变成孤儿进程；
  2. 权限提醒 / 11 项专业表格 / 进阶硬件面板都是**顶层窗口**，
     只要还有一个可见，Qt 的「最后一个窗口关闭才退出」不成立 → 进程留后台。

本测试锁定：
  A. 权限提醒必须是 NonModal（不能阻塞主窗口关闭）；
  B. 关闭主窗口时，所有其它顶层窗口被一起关掉；
  C. 关闭之后才触发的 700ms 提醒不能再弹（`_closing` 闸门）；
  D. PresentMon 被绑进「父进程死则子进程死」的 Job（procguard，真实进程验证）。

运行： python tests/test_close_cleanup.py
"""
import os
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6 import QtWidgets, QtCore        # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("OK  " if cond else "FAIL", name, detail))
    if not cond:
        FAILS.append(name)


def pump(app, ms=120):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def alive(w):
    """控件是否还活着（Qt 对象被 deleteLater 后访问会抛 RuntimeError）。"""
    try:
        return w is not None and w.isVisible()
    except RuntimeError:
        return False
    except Exception:
        return False


def visible_top_levels(win):
    app = QtWidgets.QApplication.instance()
    out = []
    for w in app.topLevelWidgets():
        try:
            if w is win or not w.isWindow() or not w.isVisible():
                continue
            out.append(w)
        except Exception:
            pass
    return out


def main():
    app = QtWidgets.QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    import FrameLab as F

    print("== A. 权限提醒必须是非模态（不阻塞主窗口关闭）==")
    win = F.FrameLabWindow()
    win.show()
    pump(app, 200)
    win._admin_prompt_shown = False
    win._show_admin_prompt()
    pump(app, 150)
    dlg = getattr(win, "_admin_dialog", None)
    check("权限提醒已弹出", dlg is not None and dlg.isVisible())
    if dlg is not None:
        check("模态 = NonModal（关键：open() 会变成 WindowModal）",
              dlg.windowModality() == QtCore.Qt.WindowModality.NonModal,
              str(dlg.windowModality()))
        # 回到主窗口不会再被拦：主窗口应仍然可用（非模态的标志）
        check("主窗口仍可交互（isEnabled）", win.isEnabled())
    check("提醒位置落在主窗口附近（不是被甩到屏幕角落）",
          dlg is not None and abs(dlg.x() - win.x()) < 1200,
          "dlg=(%s,%s) win=(%s,%s)" % (dlg.x() if dlg else -1, dlg.y() if dlg else -1,
                                       win.x(), win.y()))

    print("== B. 关闭主窗口时把其它顶层窗口一起关掉 ==")
    # 再造一个独立顶层窗口（模拟 11 项专业表格 / 进阶硬件面板）
    extra = QtWidgets.QDialog(win)
    extra.setWindowTitle("模拟专业表格")
    extra.resize(600, 400)
    extra.show()
    win.hw_panel.show()
    pump(app, 150)
    before = visible_top_levels(win)
    check("关闭前确实存在其它可见顶层窗口", len(before) >= 2, "count=%d" % len(before))

    win.close()
    pump(app, 300)
    after = visible_top_levels(win)
    check("关闭后无任何可见顶层窗口（否则进程不会退出）", len(after) == 0,
          "剩 %d 个：%s" % (len(after), [w.windowTitle() for w in after]))
    check("_closing 闸门已置位", bool(getattr(win, "_closing", False)))
    check("权限提醒已隐藏", not alive(dlg))

    print("== C. 关闭之后不能再弹提醒（否则成孤儿窗口）==")
    n_before = len(visible_top_levels(win))
    win._admin_prompt_shown = False          # 强行让条件成立
    win._show_admin_prompt()                 # 应被 _closing 挡住
    pump(app, 120)
    check("关闭后再调用 _show_admin_prompt 不产生新窗口",
          len(visible_top_levels(win)) == n_before,
          "before=%d after=%d" % (n_before, len(visible_top_levels(win))))

    print("== D. procguard：PresentMon 必须随父进程一起死 ==")
    from framelab import procguard
    check("procguard 可导入", hasattr(procguard, "bind_kill_on_exit"))
    if os.name == "nt":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            bound = procguard.bind_kill_on_exit(child.pid)
            check("bind_kill_on_exit 返回 True", bound)
            check("子进程确实在 Job 里（IsProcessInJob）",
                  procguard.is_in_our_job(child.pid))
            check("Job 句柄有效", procguard.job_active())
        finally:
            try:
                child.terminate()
                child.wait(timeout=5)
            except Exception:
                try:
                    child.kill()
                except Exception:
                    pass
    else:
        check("非 Windows 跳过 Job 检查", True)

    print()
    # 干净收尾：显式销毁窗口再退出，避免 PyQt6 在解释器收尾时
    # 「QApplication 先于窗口析构」触发 fastfail（0xC0000409），
    # 那会掩盖真实结果（检查项其实全过了）。
    try:
        win.close()
        win.deleteLater()
        pump(app, 150)
        app.processEvents()
    except Exception:
        pass

    if FAILS:
        print("FAILED: %d -> %s" % (len(FAILS), FAILS))
        return 1
    print("ALL CLOSE-CLEANUP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())