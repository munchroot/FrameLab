# -*- coding: utf-8 -*-
"""
「极简卡顿病历卡」+ 专业表格窗口（改进建议书 四.3）。

点击折线图上的漏帧红点后，**不再**直接弹出 11 行 5 列的可怕大表格，
而是先弹一张高度仅 ~110px 的病历卡，只有三个大图标色块：
    ① 当时掉了多少帧   ② 罪魁祸首是谁   ③ 一招解决建议
旁边一个小按钮「查看 11 项专业极客数据 ▸」，点了才打开完整对比表格。
"""
from PyQt6 import QtGui
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import theme as T
from .plain import LEVEL_COLOR, mini_card

CARD_HEIGHT = 110


class _Block(QFrame):
    """病历卡里的一个大图标色块（图标 + 标题 + 正文）"""

    def __init__(self, icon, caption, parent=None):
        super().__init__(parent)
        self.setObjectName("MiniBlock")
        self.icon = icon
        self.caption = caption
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(1)
        self.lbl_head = QLabel("%s %s" % (icon, caption))
        self.lbl_head.setStyleSheet("font-size: 11px; font-weight: bold;")
        self.lbl_body = QLabel("—")
        self.lbl_body.setWordWrap(True)
        self.lbl_body.setStyleSheet("font-size: 13px; font-weight: bold;")
        lay.addWidget(self.lbl_head)
        lay.addWidget(self.lbl_body)
        self.restyle("unknown")

    def set_text(self, text):
        self.lbl_body.setText(text)

    def restyle(self, level):
        th = T.CURRENT_THEME
        col = LEVEL_COLOR.get(level, th.dim)
        self.setStyleSheet(
            "QFrame#MiniBlock { background-color: %s; border: 1px solid %s;"
            " border-left: 4px solid %s; border-radius: 6px; }"
            % (th.panel2, th.border, col))
        self.lbl_head.setStyleSheet("font-size: 11px; font-weight: bold; color: %s;" % th.dim)
        self.lbl_body.setStyleSheet("font-size: 13px; font-weight: bold; color: %s;" % col)


class MiniDiagnosisCard(QFrame):
    """
    高度约 110px 的极简病历卡。

    show_for(frame, prev_frame, rows, grade) 填入内容；
    detailRequested 信号 → 由主窗口打开专业表格。

    【v1.0.0 版面零抖动】本卡从「按需 show/hide」改为**常驻占位**：
    没有锁定帧时显示中性的 idle 态（占位高度不变），有锁定帧时填内容。
    以前 hide 会让下面的折线图瞬间掉下 110px、弹卡又缩回去 —— 整页上下跳。
    """

    detailRequested = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MiniDiagCard")
        self.setFixedHeight(CARD_HEIGHT)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 6, 10, 6)
        root.setSpacing(4)

        head = QHBoxLayout()
        self.lbl_title = QLabel("【卡顿病历卡】")
        self.lbl_title.setStyleSheet("font-size: 12px; font-weight: bold;")
        head.addWidget(self.lbl_title)
        head.addStretch()
        self.btn_detail = QPushButton("11 项数据 ▸")
        self.btn_detail.setObjectName("MiniDetailButton")
        self.btn_detail.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_detail.clicked.connect(self.detailRequested.emit)
        head.addWidget(self.btn_detail)
        self.btn_close = QPushButton("✕")
        self.btn_close.setObjectName("MiniCloseButton")
        self.btn_close.setFixedWidth(28)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.clicked.connect(self._on_close)
        head.addWidget(self.btn_close)
        root.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.blk_drop = _Block("①", "掉了多少帧")
        self.blk_cause = _Block("②", "凶手是谁")
        self.blk_advice = _Block("③", "怎么改")
        for b in (self.blk_drop, self.blk_cause, self.blk_advice):
            row.addWidget(b, stretch=1)
        root.addLayout(row)
        self._info = None
        # 当前是哪种渲染（idle / smooth / frame）。
        # 【v1.0.1】以前 is_smooth_state() 只看 _info["level"]=="ok"：
        # 而 show_for() 渲染「真顿挫帧」时，如果 11 维归因全部正常，level 也是 ok，
        # 于是「病历卡是不是绿色丝滑态」被误判成 True（顶部却还在报顿挫）。
        self._mode = "idle"

    def _on_close(self):
        """✕ = 回到 idle 占位（不再 hide，避免整页高度跳变）"""
        self.show_idle()
        self.closed.emit()

    def show_idle(self):
        """
        中性占位态：没有锁定帧时显示的空壳，高度与真卡完全一致。

        【为什么必须有】以前这里是 hide()：折线图会瞬间长高 110px，
        用户点一下又缩回去，整个页面上下反复跳。
        """
        th = T.CURRENT_THEME
        self._info = None
        self._mode = "idle"
        self.lbl_title.setText("【卡顿病历卡】")
        self.lbl_title.setStyleSheet("font-size: 12px; font-weight: bold; color: %s;" % th.dim)
        self.blk_drop.set_text("—")
        self.blk_cause.set_text("—")
        self.blk_advice.set_text("—")
        for b in (self.blk_drop, self.blk_cause, self.blk_advice):
            b.restyle("unknown")
        self.btn_detail.setEnabled(False)
        self.setStyleSheet(
            "QFrame#MiniDiagCard { background-color: %s; border: 1px dashed %s;"
            " border-radius: 8px; }" % (th.panel, th.border))
        self.setVisible(True)

    def show_for(self, frame, prev_frame, rows, grade):
        info = mini_card(frame, prev_frame, rows, grade)
        self._info = info
        self._mode = "frame"
        self.blk_drop.set_text(info["drop_text"])
        self.blk_cause.set_text(info["cause_text"])
        self.blk_advice.set_text(info["advice_text"])
        level = info["level"]
        for b in (self.blk_drop, self.blk_cause, self.blk_advice):
            b.restyle(level)
        self.blk_drop.restyle("warn")
        self.btn_detail.setEnabled(True)
        th = T.CURRENT_THEME
        # 【v1.0.1 去重复】标题不再重复"X 级"：等级由顶部徽章统一表达，
        # 病历卡只说自己这一帧的事（第几帧 / 多少毫秒）。
        self.lbl_title.setText(
            "【卡顿病历卡】 #%s · %.1f ms"
            % (getattr(frame, "idx", "-"), float(getattr(frame, "frametime", 0.0) or 0.0)))
        self.lbl_title.setStyleSheet("font-size: 12px; font-weight: bold; color: %s;" % th.accent)
        self.setStyleSheet(
            "QFrame#MiniDiagCard { background-color: %s; border: 1px solid %s;"
            " border-top: 3px solid %s; border-radius: 8px; }"
            % (th.panel, th.border_hot, info["color"]))
        self.setVisible(True)

    def show_smooth(self, grade="S", avg_fps=0.0, dropped=0):
        """
        绿色「全程丝滑」态（改进建议书 五.3）：

        当没有触发严格顿挫条件（相对局部中位数跌幅 ≥35% 且帧耗时跳变 ≥8ms
        且当前帧率 <85FPS）时，底部病历卡不给红色暴跌结论，只报平安。
        """
        th = T.CURRENT_THEME
        self._mode = "smooth"
        self._info = {"level": "ok", "color": T.CURRENT_THEME.green,
                      "drop_text": "全程丝滑", "cause_text": "波动在正常范围内",
                      "advice_text": "无需操作", "grade": grade,
                      "fps": float(avg_fps or 0.0), "detail": ""}
        self.blk_drop.set_text("全程丝滑")
        self.blk_cause.set_text("波动在正常范围内")
        self.blk_advice.set_text("无需操作")
        for b in (self.blk_drop, self.blk_cause, self.blk_advice):
            b.restyle("ok")
        self.btn_detail.setEnabled(False)
        # 同样去掉"X 级"，避免与顶部徽章重复
        self.lbl_title.setText(
            "【卡顿病历卡】 平均 %.0f 帧 · 顿挫 %d 次"
            % (float(avg_fps or 0.0), int(dropped or 0)))
        self.lbl_title.setStyleSheet("font-size: 12px; font-weight: bold; color: %s;" % th.green)
        self.setStyleSheet(
            "QFrame#MiniDiagCard { background-color: %s; border: 1px solid %s;"
            " border-top: 3px solid %s; border-radius: 8px; }"
            % (th.panel, th.border, th.green))
        self.setVisible(True)

    def is_smooth_state(self):
        """
        是否处于「绿色全程丝滑」渲染态。

        【v1.0.1】以前判断 `_info["level"] == "ok"`：而 show_for() 渲染真顿挫帧时，
        若 11 维归因恰好全部正常，level 也是 ok → 被判成「丝滑态」，
        与顶部红字告警自相矛盾。现在按**渲染方式**判定。
        """
        return self._mode == "smooth"

    def render_mode(self):
        """idle / smooth / frame —— 供测试与主窗口自检"""
        return self._mode

    def info(self):
        return self._info

    def restyle(self):
        th = T.CURRENT_THEME
        self.lbl_title.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: %s;" % th.accent)


class ProfessionalTableDialog(QDialog):
    """
    完整的 11 行 5 列对比表格（原「病灶对比诊断卡」，点按钮才打开）。

    【v1.0.2 可读性返修（用户截图反馈）】
      1. 第 5 列原来是 `Interactive + setColumnWidth(560)`：列宽之和超过窗口宽度，
         于是底部永远挂着一条横向滚动条，**最后一行被它压掉、第 5 列被它截断**。
         改成「前 4 列 ResizeToContents + 第 5 列 Stretch + 关闭横滚」。
      2. 行高原来固定 34px，而第 5 列开着 wordWrap：换行后的第二行被直接裁掉。
         改成垂直表头 `ResizeToContents`，行高按内容自动长。
      3. 行底色原来写死 `#3d1418` / `#38240b`（深色主题专用），浅色主题下深色字
         压在深红底上几乎不可读；且正文列传的是**空 QBrush**（`QtGui.QBrush()`），
         委托只能回退到 palette —— 这就是"一片灰字"的来源。现在底色一律由
         `theme.mix()` 从当前主题派生，前景色一律显式给定并做对比度兜底。
    """

    _ALIGN = {
        "L": Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
        "C": Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter,
    }

    @staticmethod
    def _paint(sev):
        """严重度 → (行底色, 正文色, 语义色)；任何主题下都保证亮度差 ≥0.28"""
        th = T.CURRENT_THEME
        if sev == "DANGER":
            hot = th.bad
        elif sev == "WARN":
            hot = th.warn
        else:
            hot = th.green
        if sev in ("DANGER", "WARN"):
            bg = T.mix(th.panel2, hot, 0.20 if th.is_dark else 0.16)
        else:
            bg = T.mix(th.panel, th.panel2, 0.5)
        body = T.contrast_on(th.val, bg, need=0.34)
        return bg, body, T.contrast_on(hot, bg, need=0.28)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FrameLab · 11 项专业归因数据")
        self.resize(1240, 600)
        self.setMinimumSize(900, 380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        self.lbl_head = QLabel("锁定帧：—")
        self.lbl_head.setStyleSheet("font-size: 12px; font-weight: 600;")
        lay.addWidget(self.lbl_head)
        self.table = QTableWidget(11, 5)
        self.table.setHorizontalHeaderLabels(
            ["监控维度", "异常瞬间数值", "平稳期均值 (Baseline)", "变化幅度 (Delta)",
             "元凶判定与优化建议"])
        hdr = self.table.horizontalHeader()
        for i in range(4):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        # 最后一列吃掉剩余宽度 → 任何窗口尺寸下都不会再出现横向滚动条
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        hdr.setStretchLastSection(True)
        hdr.setHighlightSections(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.verticalHeader().setVisible(False)
        # 行高随内容自动伸缩（长结论换行后不再被裁成半行）
        self.table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setMinimumSectionSize(28)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.setWordWrap(True)
        lay.addWidget(self.table, stretch=1)
        self.lbl_tip = QLabel("提示：⚪ 表示该维度缺少硬件快照（离线回放或采集间隔过大），"
                              "不是「正常」。")
        self.lbl_tip.setWordWrap(True)
        lay.addWidget(self.lbl_tip)
        self.restyle()

    def show_rows(self, frame, rows):
        self.lbl_head.setText(
            "锁定帧 #%s · 时刻 T = %.2fs · 帧耗时 %.1f ms · 瞬时 %.0f FPS"
            % (getattr(frame, "idx", "-"), float(getattr(frame, "t", 0.0) or 0.0),
               float(getattr(frame, "frametime", 0.0) or 0.0),
               1000.0 / max(0.1, float(getattr(frame, "frametime", 0.1) or 0.1))))
        cells = []
        for row in rows:
            sev = getattr(row, "severity", "NORMAL")
            bg, body, hot = self._paint(sev)
            # NORMAL 行的「元凶判定」不再染成绿色语义色以外的灰：统一用正文色，保证可读
            vc = hot if sev in ("DANGER", "WARN") else body
            cells.append([
                (row.dim, body, bg, "L"),
                (row.cur, body, bg, "C"),
                (row.base, body, bg, "C"),
                (row.delta, hot, bg, "C"),
                (row.verdict, vc, bg, "L"),
            ])
        self._rows_cache = (frame, list(rows))
        self._sync(cells)

    def _sync(self, cells):
        """整表重写（11×5=55 个格子，开销可忽略；不再维护易错的新旧值缓存）"""
        table = self.table
        if table.rowCount() != len(cells):
            table.setRowCount(len(cells))
        for r, row in enumerate(cells):
            for c, (text, fg, bg, al) in enumerate(row):
                item = table.item(r, c)
                if item is None:
                    item = QTableWidgetItem()
                    table.setItem(r, c, item)
                item.setText("" if text is None else str(text))
                item.setTextAlignment(self._ALIGN[al])
                # 前景色**永远显式给定**：空 QBrush 会退化成 palette 色 → "一片灰字"
                item.setForeground(QtGui.QColor(fg) if fg else QtGui.QColor(T.CURRENT_THEME.val))
                item.setData(Qt.ItemDataRole.BackgroundRole,
                             QtGui.QColor(bg) if bg else None)

    def restyle(self):
        th = T.CURRENT_THEME
        self.setStyleSheet("QDialog { background-color: %s; }" % th.bg)
        self.lbl_head.setStyleSheet(
            "font-size: 12px; font-weight: 600; color: %s;" % th.accent)
        self.lbl_tip.setStyleSheet(
            "font-size: 11px; font-weight: 500; color: %s;" % th.dim)
        self.table.setStyleSheet(
            "QTableWidget { background-color: %s; color: %s;"
            " gridline-color: %s; border: 1px solid %s; border-radius: 6px;"
            " font-size: 12px; }"
            "QTableWidget::item { padding: 3px 8px; }"
            "QHeaderView::section { background-color: %s; color: %s;"
            " font-size: 12px; font-weight: 600; padding: 5px 8px;"
            " border: none; border-bottom: 1px solid %s; }"
            % (th.panel, th.val, th.border, th.border,
               th.panel2, th.fg, th.border))
        # 主题换了 → 行底色/前景色也要跟着重算（否则浅色主题下还是深色底）
        info = getattr(self, "_rows_cache", None)
        if info:
            self.show_rows(info[0], info[1])
