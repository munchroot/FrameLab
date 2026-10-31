# -*- coding: utf-8 -*-
"""
《小白话术映射表》—— 把 11 维专业归因翻译成玩家看得懂的一句话 + 一招解决建议。

改进建议书 四.1 的核心：横幅与病历卡**禁用** GC / PSO / Non-local / CPU0 等术语。
本模块是**纯映射层**，不改变任何判定结果：输入 SnapshotDiffInspector 产出的
DiffRow 列表（dim / severity / verdict），输出人话。

映射优先级与底层 cause_keys 保持一致：越上游的病因越优先。
"""
from .. import theme as T
from ..stats import GRADE_LABEL

# level → 主题色角色（由 UI 决定具体颜色，避免此处依赖主题）
LEVEL_DANGER = "danger"
LEVEL_WARN = "warn"
LEVEL_OK = "ok"
LEVEL_UNKNOWN = "unknown"

LEVEL_ICON = {LEVEL_DANGER: "🔴", LEVEL_WARN: "🟠", LEVEL_OK: "🟢", LEVEL_UNKNOWN: "⚪"}

# ---------------------------------------------------------------------------
# 【v1.0.3 修「红色在白色皮肤上看不清」（用户反馈）】
#   以前这里写死 `#ff5d5d / #ffb02e / #2ee6a8` —— 那是**深色主题专用色板**：
#   放到浅色（白底）主题上，`#ff5d5d` 与白底的对比度只有 ~2.4:1、
#   `#2ee6a8` 更是只有 ~1.6:1，于是病历卡边框、病历卡正文、横幅结论、
#   评级大字母全部"发虚看不清"。
#   现在这里只存**角色名**，颜色在取值时按 `CURRENT_THEME` 解析：
#   深色主题仍是亮红/亮橙，浅色主题自动变成深红 #c62828 / 深橙 #b26a00。
# ---------------------------------------------------------------------------
LEVEL_ROLE = {LEVEL_DANGER: "bad", LEVEL_WARN: "warn",
              LEVEL_OK: "green", LEVEL_UNKNOWN: "dim"}
GRADE_ROLE = {"S": "green", "A": "accent", "B": "warn", "C": "bad"}


def level_color(level, theme=None):
    """level → 当前主题的颜色（浅色主题下自动用深色，白底上清晰可读）"""
    th = theme or T.CURRENT_THEME
    return getattr(th, LEVEL_ROLE.get(level, "dim"), th.dim)


def grade_color(grade, theme=None):
    """评级字母 S/A/B/C → 当前主题的颜色"""
    th = theme or T.CURRENT_THEME
    return getattr(th, GRADE_ROLE.get(str(grade or "").strip().upper(), "dim"), th.dim)


class _ThemeMap(dict):
    """
    兼容层：老代码写 `LEVEL_COLOR[key]` / `GRADE_COLOR.get(key)` 取色。

    每次访问都**按当前主题解析**，所以换肤后自动跟着变；
    `dict` 里存的是角色名（"bad"/"warn"/…），便于自检与打印。
    """

    def __init__(self, roles, resolver):
        super().__init__(roles)
        self._resolve = resolver

    def __getitem__(self, key):
        return self._resolve(key)

    def get(self, key, default=None):
        if key not in self:
            return default if default is not None else self._resolve(key)
        return self._resolve(key)

    def values(self):
        return [self[k] for k in dict.keys(self)]

    def items(self):
        return [(k, self[k]) for k in dict.keys(self)]


LEVEL_COLOR = _ThemeMap(LEVEL_ROLE, level_color)
GRADE_COLOR = _ThemeMap(GRADE_ROLE, grade_color)

# ---------------------------------------------------------------------------
# 规则表：(维度前缀, 触发级别集合, 小白原因, 一招解决, 级别)
#   · 维度前缀与 SnapshotDiffInspector 的 DiffRow.dim 对应（startswith 匹配）
#   · 只收录**能给出可执行建议**的维度；其余走兜底文案
# ---------------------------------------------------------------------------
PLAIN_RULES = (
    # 【v4.1 约定】触发级别保持 {LEVEL_DANGER, LEVEL_WARN}：新算法下只有
    # 「专用显存 ≥88% 且非本地 ≥256MB 且较基线暴增 ≥64MB」才 DANGER（已罕见），
    # 占用 ≥94% 只给 WARN；NORMAL（显存容量充足）不再命中本规则。
    ("显存溢出", {LEVEL_DANGER, LEVEL_WARN},
     "显存爆了！游戏把贴图塞不进去了",
     "去游戏设置里把【贴图/纹理质量】调低一档", LEVEL_DANGER),
    ("磁盘 I/O", {LEVEL_DANGER, LEVEL_WARN},
     "硬盘加载地图太慢，游戏一直在等读盘",
     "关闭后台下载，或把游戏移到固态硬盘(SSD)", LEVEL_WARN),
    ("着色器编译", {LEVEL_DANGER, LEVEL_WARN},
     "游戏正在首次加载特效（着色器）",
     "无需操作，跑完一遍地图后会自动变流畅", LEVEL_WARN),
    ("CPU 核心状态", {LEVEL_DANGER, LEVEL_WARN},
     "CPU 单核忙不过来了（一个核在死扛）",
     "调低游戏里的【同屏人数/视野距离/阴影】", LEVEL_DANGER),
    ("页面文件换入", {LEVEL_DANGER, LEVEL_WARN},
     "内存不够用了，系统在拿硬盘当内存顶",
     "关掉后台程序，或把游戏内【内存/纹理】占用调低", LEVEL_WARN),
    ("网络同步", {LEVEL_DANGER, LEVEL_WARN},
     "网络卡了，服务器数据没跟上",
     "检查 wifi/网线，或换个延迟低的服务器", LEVEL_WARN),
    ("GPU 核心频率", {LEVEL_DANGER, LEVEL_WARN},
     "显卡撞到温度墙/功耗墙，自己降频了",
     "清灰散热、加强机箱风道，或把画质调低减轻显卡负担", LEVEL_WARN),
    ("GPU 忙时比", {LEVEL_DANGER, LEVEL_WARN},
     "显卡已经满载，帧率被显卡卡住了",
     "调低【分辨率/画质预设/光追】或换更强的显卡", LEVEL_WARN),
    ("引擎 GC", {LEVEL_DANGER, LEVEL_WARN},
     "游戏程序自己卡了一下（引擎内部停顿）",
     "属于游戏优化问题，可尝试关闭后台录制/叠加层", LEVEL_WARN),
)

# 兜底：症状本身（不是病因）
FALLBACK_SYMPTOM = ("帧率掉了一下", "把画质调低一档通常能缓解", LEVEL_WARN)


def row_level(row):
    """DiffRow.severity（NORMAL/WARN/DANGER）→ 本模块的 level"""
    sev = getattr(row, "severity", "NORMAL")
    if sev == "DANGER":
        return LEVEL_DANGER
    if sev == "WARN":
        return LEVEL_WARN
    return LEVEL_OK


def worst_rows(rows):
    """按规则表优先级挑出「最值得解释给玩家」的一行；没有则返回 None"""
    if not rows:
        return None
    for prefix, levels, _cause, _advice, _lvl in PLAIN_RULES:
        for r in rows:
            dim = getattr(r, "dim", "") or ""
            if dim.startswith(prefix) and row_level(r) in levels:
                return r
    for r in rows:
        if row_level(r) == LEVEL_DANGER:
            return r
    return None


def explain(rows):
    """
    把诊断行翻译成一整句人话。

    返回 dict：
      {level, color, icon, cause, advice, text, row}
      text 形如：🔴 刚才卡顿原因：显存爆了！ 👉 解决：把【贴图/纹理质量】调低一档。
    """
    row = worst_rows(rows)
    if row is None:
        return {"level": LEVEL_OK, "color": LEVEL_COLOR[LEVEL_OK], "icon": LEVEL_ICON[LEVEL_OK],
                "cause": "硬件各项指标都正常", "advice": "什么都不用改",
                "text": "🟢 运行很稳 · 硬件各项指标都正常",
                "row": None}
    prefix = getattr(row, "dim", "") or ""
    for p, levels, cause, advice, lvl in PLAIN_RULES:
        if prefix.startswith(p) and row_level(row) in levels:
            return _pack(lvl, cause, advice, row)
    cause, advice, lvl = FALLBACK_SYMPTOM
    return _pack(lvl if row_level(row) == LEVEL_DANGER else LEVEL_WARN, cause, advice, row)


def _pack(level, cause, advice, row=None):
    icon = LEVEL_ICON[level]
    return {"level": level, "color": LEVEL_COLOR[level], "icon": icon,
            "cause": cause, "advice": advice,
            # 【v1.0.1 文案】原前缀是「刚才卡顿原因：」，而兜底 cause 本身又是
            # 「刚才帧率掉了一下」→ 屏上出现「刚才卡顿原因：刚才帧率掉了一下」。
            # 统一改成「卡顿：<原因> 👉 <做法>」，去掉重复的「刚才」与「解决：」。
            "text": "%s 卡顿：%s 👉 %s。" % (icon, cause, advice),
            "row": row}


def banner_text(grade, avg_fps, rows, has_data=True, waiting_msg=""):
    """
    顶部横幅文案（改进建议书 四.1：徽章 + 大白话）。

    正常时：🟢 当前 87 帧 · 整体流畅（A 级）
    卡顿时：🔴 卡顿：…… 👉 ……
    """
    if not has_data:
        return "🟡 正在等待帧数据…%s" % (("（%s）" % waiting_msg) if waiting_msg else "")
    info = explain(rows)
    if info["level"] == LEVEL_OK:
        return "🟢 当前 %.0f 帧 · %s（%s 级）" % (
            avg_fps, GRADE_LABEL.get(grade, "运行中"), grade)
    return info["text"]


def mini_card(frame, prev_frame, rows, grade):
    """
    「极简卡顿病历卡」三块内容（改进建议书 四.3）。

    ① 当时掉了多少帧：从 X 帧 暴跌至 Y 帧
    ② 罪魁祸首是谁：显卡显存瞬间溢出 512MB / 硬盘读盘卡住 …
    ③ 一招解决建议：调低【纹理质量】或【抗锯齿】

    返回 dict：{drop_text, cause_text, advice_text, level, color, detail}
      · detail 为底层该行的专业表述（点开专业表格前的过渡信息）
    """
    ft = float(getattr(frame, "frametime", 0.0) or 0.0)
    fps = 1000.0 / ft if ft > 0 else 0.0
    prev_fps = None
    if prev_frame is not None:
        pft = float(getattr(prev_frame, "frametime", 0.0) or 0.0)
        prev_fps = 1000.0 / pft if pft > 0 else None
    if prev_fps and prev_fps > fps:
        drop_text = "从 %.0f 帧 暴跌至 %.0f 帧" % (prev_fps, fps)
    else:
        drop_text = "这一瞬间只剩 %.0f 帧" % fps

    info = explain(rows)
    cause = info["cause"]
    advice = info["advice"]
    row = info["row"]
    detail = ""
    if row is not None:
        cur = getattr(row, "cur", "") or ""
        delta = getattr(row, "delta", "") or ""
        detail = "%s：%s%s" % (getattr(row, "dim", ""), cur, ("（%s）" % delta) if delta else "")
        # 显存溢出场景补上具体溢出量，玩家更容易理解
        if (getattr(row, "dim", "") or "").startswith("显存溢出") and cur:
            cause = "显卡显存瞬间溢出（%s）" % cur
    return {"drop_text": drop_text, "cause_text": cause, "advice_text": advice,
            "level": info["level"], "color": info["color"], "detail": detail,
            "grade": grade, "fps": fps}
