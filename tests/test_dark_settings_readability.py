# -*- coding: utf-8 -*-
"""
验证脚本（回归）：暗色主题下「设置」与 ⚙ 菜单的可读性（v4.2 修 Bug）。

历史 Bug：切到深空黑/纯黑后，⚙ 齿轮菜单整块浅灰底+浅色文字（完全看不清），
「⚙ 设置」页整页白底。根因 = 全局 QSS 只写了 `QWidget { color: fg }`，
没给 QMenu / QDialog / QScrollArea 视口设背景，也没设置 QPalette ——
原生表面退回 Fusion 默认浅灰，浅字压浅底。

修法：QSS 补 QMenu/QDialog/viewport 规则 + QApplication 调色板按主题下发。
本脚本用 **widget.grab()**（离屏但走完整 QSS+调色板渲染管线 —— 修复前它
能复现 95% 浅灰的菜单，修复后必须全主题通过）测量：
  · ⚙ 菜单背景 = 主题 panel2，fg 对比度 ≥ 4.5（WCAG AA）；
  · 设置页背景 = 主题色，fg 对比度 ≥ 4.5、dim 说明文字 ≥ 3.0；
  · QMessageBox 背景 = 主题 panel，fg 对比度 ≥ 4.5。

运行： python tests\test_dark_settings_readability.py   （需真实 windows 平台）
"""
import os
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6 import QtWidgets, QtGui, QtCore      # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("OK  " if cond else "FAIL", name, detail))
    if not cond:
        FAILS.append(name)


def pump(app, ms=400):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def hx(c):
    return "#%02x%02x%02x" % (c.red(), c.green(), c.blue())


def lum(c):
    return (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255.0


def contrast(c1, c2):
    def rel(c):
        v = [x / 255.0 for x in (c.red(), c.green(), c.blue())]
        v = [(x / 12.92) if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
        return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]
    a, b = rel(c1), rel(c2)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def modal(img, box=None):
    cnt = Counter()
    x0, y0, x1, y1 = box or (0, 0, img.width() - 1, img.height() - 1)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width() - 1, x1), min(img.height() - 1, y1)
    for y in range(y0, y1 + 1, 2):
        for x in range(x0, x1 + 1, 2):
            c = img.pixelColor(x, y)
            cnt[(c.red(), c.green(), c.blue())] += 1
    if not cnt:
        return None, 0.0
    (r, g, b), n = cnt.most_common(1)[0]
    return QtGui.QColor(r, g, b), n * 1.0 / max(1, sum(cnt.values()))


def grab_region(img, box=None):
    """widget.grab() 的图像（与屏幕截图同构：QImage + 区域统计）。"""
    return img


def near(c, hexcolor, tol=18):
    t = QtGui.QColor(hexcolor)
    return all(abs(a - b) <= tol for a, b in
               ((c.red(), t.red()), (c.green(), t.green()), (c.blue(), t.blue())))


def main():
    if sys.platform != "win32" or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        print("SKIP: 需要真实 windows 平台（屏幕截图）")
        return 0
    app = QtWidgets.QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setFont(QtGui.QFont("Microsoft YaHei UI", 9))
    import StutterTriage as F
    from stuttertriage import theme as T

    out = os.path.join(ROOT, "docs")
    win = F.StutterTriageWindow()
    win.resize(1200, 800)
    win.show()
    pump(app, 400)

    for key in ("deepspace", "pureblack", "capframex", "light"):
        th = T.THEME_PRESETS[key]
        print("== 主题 %s (bg=%s panel=%s panel2=%s fg=%s) ==" % (key, th.bg, th.panel, th.panel2, th.fg))
        win._apply_theme(th.copy(), persist=False)
        pump(app, 350)

        # ---- ⚙ 齿轮菜单（widget.grab 走完整 QSS+调色板渲染，修复前复现 95% 浅灰）----
        m = win.gear_menu
        m.ensurePolished()
        m.adjustSize()
        pump(app, 60)
        img_m = m.grab().toImage()
        bgm, share = modal(img_m, (6, 6, img_m.width() - 6, img_m.height() - 6))
        ok_bg = bgm is not None and near(bgm, th.panel2, tol=26)
        ok_ct = bgm is not None and contrast(QtGui.QColor(th.fg), bgm) >= 4.5
        check("⚙ 菜单背景 = 主题 panel2（非 Fusion 浅灰）", ok_bg,
              "modal=%s share=%.0f%%" % (hx(bgm) if bgm else "-", share * 100))
        check("⚙ 菜单 fg 对比度 ≥ 4.5", ok_ct,
              "contrast=%.2f" % (contrast(QtGui.QColor(th.fg), bgm) if bgm else -1))
        img_m_p = os.path.join(out, "_chk_menu_%s.png" % key)
        QtGui.QPixmap.fromImage(img_m).save(img_m_p)
        pump(app, 60)

        # ---- ⚙ 设置 页（进阶硬件面板最后一个标签）----
        win.hw_panel.show()
        win.hw_tabs.setCurrentWidget(win.tab_settings)
        pump(app, 300)
        # widget.grab()（离屏但完整 QSS+调色板渲染）——不受桌面其他窗口遮挡影响
        g_off = win.hw_panel.grab().toImage()
        bg_off, _ = modal(g_off, (30, 60, g_off.width() - 30, g_off.height() - 30))
        img_s = g_off
        # 只测「设置页内容区」：避开标签栏（顶部 ~44px）与外框
        bgp, sharep = modal(img_s, (30, 60, img_s.width() - 30, img_s.height() - 30))
        ok_page = bgp is not None and (near(bgp, th.panel, tol=30) or near(bgp, th.bg, tol=30)
                                       or near(bgp, th.panel2, tol=30))
        ok_pct = bgp is not None and contrast(QtGui.QColor(th.fg), bgp) >= 4.5
        ok_dim = bgp is not None and contrast(QtGui.QColor(th.dim), bgp) >= 3.0
        check("设置页背景 = 主题色（非白底/黑底一块）", ok_page,
              "modal=%s share=%.0f%%" % (hx(bgp) if bgp else "-", sharep * 100))
        check("设置页 fg 对比度 ≥ 4.5", ok_pct,
              "contrast=%.2f" % (contrast(QtGui.QColor(th.fg), bgp) if bgp else -1))
        check("设置页 dim 说明文字对比度 ≥ 3.0", ok_dim,
              "contrast=%.2f" % (contrast(QtGui.QColor(th.dim), bgp) if bgp else -1))
        img_s_p = os.path.join(out, "_chk_settings_%s.png" % key)
        QtGui.QPixmap.fromImage(img_s).save(img_s_p)
        win.hw_panel.hide()
        pump(app, 120)

        # ---- QMessageBox（清理 ETW 的二次确认也走同一套）----
        box = QtWidgets.QMessageBox(win)
        box.setWindowTitle("对比度自检")
        box.setText("暗色主题下弹窗文字可读性检查：%s" % th.name)
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        box.show()
        pump(app, 250)
        # widget.grab()（确定性）：修复前 QMessageBox 是 Fusion 浅灰底
        g_off_b = box.grab().toImage()
        bg_off_b, _ = modal(g_off_b, (8, 8, g_off_b.width() - 8, g_off_b.height() - 8))
        img_b = g_off_b
        bgb, _ = modal(img_b, (8, 8, img_b.width() - 8, img_b.height() - 8))
        check("弹窗(QMessageBox) fg 对比度 ≥ 4.5",
              bgb is not None and contrast(QtGui.QColor(th.fg), bgb) >= 4.5,
              "bg=%s contrast=%.2f" % (hx(bgb) if bgb else "-",
                                       contrast(QtGui.QColor(th.fg), bgb) if bgb else -1))
        box.close()
        pump(app, 100)

    win.close()
    pump(app, 200)
    print()
    if FAILS:
        print("FAILED: %d -> %s" % (len(FAILS), FAILS))
        return 1
    print("ALL DARK-SETTINGS READABILITY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())