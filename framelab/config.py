# -*- coding: utf-8 -*-

"""配置外部化：framelab_config.json 读写与阈值回落。"""

import os
import json
from .paths import app_dir



# ==============================================================================
# §9.5.1  阈值参数外部化 —— framelab_config.json
# ==============================================================================
CONFIG_FILENAME = "framelab_config.json"



# 阈值键 → (模块级全局变量名, 类型, 说明)
# apply_to_globals() 会把配置值写回这些模块级常量。
CONFIG_SCHEMA = {
    "spill_threshold":    ("SPILL_THRESHOLD",    int,
                            "非本地显存告警阈值 (字节)，默认 64MB"),
    "freq_drop_ratio":    ("FREQ_DROP_RATIO",    float,
                            "单核相邻采样跌幅超过该比例记降频，默认 0.18"),
    "cache_burst_files":  ("CACHE_BURST_FILES",  int,
                            "着色器缓存新增文件数爆发阈值，默认 8"),
    "cpu0_jam_threshold": ("CPU0_JAM_THRESHOLD", float,
                            "CPU0 占用率 >= 该值记拥堵 (%%)，默认 92.0"),
    "cpu0_idle_others":   ("CPU0_IDLE_OTHERS",   float,
                            "其他核均值低于该值才算「其他核空闲」，默认 70.0"),
    "migrate_freq_gap":   ("MIGRATE_FREQ_GAP",   float,
                            "跨核迁移两核基频差阈值 (MHz)，默认 400.0"),
    "pm_buffer_size":     ("PM_BUFFER_SIZE",     int,
                            "PresentMon 环形缓冲区大小，必须是 2 的幂，默认 4096"),
    "baseline_window":    ("BASELINE_WINDOW",    int,
                            "基线采样窗口帧数，默认 240"),
    "baseline_ema":       ("BASELINE_EMA",       float,
                            "基线 EMA 系数，默认 0.25"),
    "baseline_spike_ratio": ("BASELINE_SPIKE_RATIO", float,
                            "基线尖峰判定倍数（相对中位数），默认 2.2"),
    "baseline_max_spike_frac": ("BASELINE_MAX_SPIKE_FRAC", float,
                                "尖峰占比超过该值则冻结基线，默认 0.35"),
    "max_hw_age":         ("MAX_HW_AGE",         float,
                            "硬件快照最大可用年龄 (秒)，超过则诊断标注不可判定，默认 1.2"),
}



# 非阈值类配置（行为开关 / 路径 / 皮肤）
CONFIG_EXTRA_KEYS = ("hotkey", "presentmon_path", "blacklist_extra",
                      "theme", "chart_style", "custom_bg")


# ==============================================================================
# §9.5.1b  设置页的人话标签与分组（v1.0.4）
# ------------------------------------------------------------------------------
# 【为什么要有】设置页原先把 CONFIG_SCHEMA 的**原始键名**直接当标签渲染
# （`spill_threshold` / `cpu0_jam_threshold` / `baseline_max_spike_frac` …），
# 12 行英文标识符 + 括号说明糊成一大块 —— 属于 v3.2 时代只有作者看得懂的
# 「老东西」。现在只改**呈现**：中文名 + 单位在前、原始键名进 tooltip，并按
# 归因维度分组。键名 / 类型 / 取值范围 / 默认值 / 读写逻辑一律不变。
# ==============================================================================
CONFIG_LABELS = {
    "spill_threshold":         ("非本地显存告警阈值", "字节 · 默认 64 MB"),
    "freq_drop_ratio":         ("单核骤降频比例",     "0~1 · 默认 0.18"),
    "cache_burst_files":       ("着色器缓存爆发文件数", "个 · 默认 8"),
    "cpu0_jam_threshold":      ("CPU0 拥堵占用率",    "% · 默认 92"),
    "cpu0_idle_others":        ("其余核空闲均值上限",  "% · 默认 70"),
    "migrate_freq_gap":        ("跨核迁移基频差",      "MHz · 默认 400"),
    "pm_buffer_size":          ("PresentMon 环形缓冲", "帧 · 2 的幂 · 默认 4096"),
    "baseline_window":         ("基线采样窗口",        "帧 · 默认 240"),
    "baseline_ema":            ("基线 EMA 系数",       "0~1 · 默认 0.25"),
    "baseline_spike_ratio":    ("基线尖峰判定倍数",    "× · 默认 2.2"),
    "baseline_max_spike_frac": ("尖峰占比冻结门限",    "0~1 · 默认 0.35"),
    "max_hw_age":              ("硬件快照最大有效期",  "秒 · 默认 1.2"),
}

# 设置页分组：(组标题, 一句话说明, 键序列)
CONFIG_GROUPS = (
    ("显存归因", "非本地显存涨到多少就告警", ("spill_threshold",)),
    ("CPU 归因", "单核骤降 / CPU0 拥堵 / 跨核迁移",
     ("cpu0_jam_threshold", "cpu0_idle_others", "freq_drop_ratio", "migrate_freq_gap")),
    ("着色器归因", "缓存目录一轮新增多少文件算爆发", ("cache_burst_files",)),
    ("采集引擎", "PresentMon 缓冲与硬件快照有效期", ("pm_buffer_size", "max_hw_age")),
    ("平稳基线 · 进阶", "不懂就别改：改动会影响所有归因的相对基准",
     ("baseline_window", "baseline_ema", "baseline_spike_ratio",
      "baseline_max_spike_frac")),
)

# 皮肤键的白名单：脏值（手改坏了 / 旧版本残留）绝不能让程序起不来或卡在黑屏
VALID_THEMES = ("capframex", "deepspace", "pureblack", "light", "custom")
VALID_CHART_STYLES = ("capframex", "hud")


DEFAULT_CONFIG = {
    "spill_threshold": 64 << 20,
    "freq_drop_ratio": 0.18,
    "cache_burst_files": 8,
    "cpu0_jam_threshold": 92.0,
    "cpu0_idle_others": 70.0,
    "migrate_freq_gap": 400.0,
    "pm_buffer_size": 4096,
    "baseline_window": 240,
    "baseline_ema": 0.25,
    "baseline_spike_ratio": 2.2,
    "baseline_max_spike_frac": 0.35,
    "max_hw_age": 1.2,
    "hotkey": "Ctrl+F9",
    "presentmon_path": "",
    "blacklist_extra": [],
    "theme": "deepspace",
    "chart_style": "capframex",
    "custom_bg": "",
}


def default_config_dict():
    """返回一份**新的**默认配置副本（避免调用方污染 DEFAULT_CONFIG）"""
    cfg = dict(DEFAULT_CONFIG)
    cfg["blacklist_extra"] = list(DEFAULT_CONFIG["blacklist_extra"])
    return cfg


def config_path(base_dir=None):
    """配置文件路径：默认与启动器 FrameLab.py 同目录（v4：见 framelab.paths）"""
    if base_dir is None:
        base_dir = app_dir()
    return os.path.join(base_dir, CONFIG_FILENAME)


def _coerce(value, caster, fallback):
    """配置值类型转换；失败一律回落到默认值（绝不因脏配置让程序起不来）"""
    try:
        if caster is int:
            return int(value)
        if caster is float:
            return float(value)
        if caster is bool:
            return bool(value)
        return value
    except (TypeError, ValueError):
        return fallback


class Config:
    """
    阈值参数外部化容器。

    设计要点：
      · **不在 import 期读文件** —— 读文件放在显式 `load()` 里，
        这样单元测试可以自由地 load/save 到临时目录。
      · **脏配置绝不致命** —— 任何非法值都回落到默认值并记录 warning。
      · `apply_to_globals()` 是**显式**调用：把配置写回模块级常量，
        因为那些常量（SPILL_THRESHOLD 等）在别处被直接引用。
    """

    def __init__(self, data=None, path=None):
        self.path = path or config_path()
        self.data = default_config_dict()
        self.warnings = []
        self.existed = False
        if data:
            self.update(data)

    # ---------------- 读写 ----------------
    @classmethod
    def load(cls, path=None, base_dir=None):
        """读取配置文件；不存在则返回纯默认配置（existed=False）"""
        p = path or config_path(base_dir)
        obj = cls(path=p)
        if not os.path.isfile(p):
            return obj
        obj.existed = True
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                obj.warnings.append("配置文件根节点不是对象，已忽略")
                return obj
            obj.update(raw)
        except Exception as ex:
            obj.warnings.append("配置文件解析失败(%s)，已使用默认值" % ex)
        return obj

    def update(self, raw):
        """
        把外部字典合并进来（未知键忽略，非法值回落默认）。

        【v1.0.1 修「换肤重启丢失」】原来这里只遍历 CONFIG_SCHEMA（纯阈值），
        外加三个硬编码键（hotkey / presentmon_path / blacklist_extra）——
        `theme` / `chart_style` / `custom_bg` 虽然被 save() 老老实实写进了文件，
        读回来时却**直接丢掉**，于是每次重启都回落到默认深色皮肤。
        现在统一按 CONFIG_EXTRA_KEYS 处理，并对 theme/chart_style 做白名单校验。
        """
        if not isinstance(raw, dict):
            return self
        for key, (gname, caster, _desc) in CONFIG_SCHEMA.items():
            if key in raw:
                fallback = DEFAULT_CONFIG.get(key)
                val = _coerce(raw[key], caster, fallback)
                if val != raw[key] and not isinstance(val, bool):
                    self.warnings.append("配置项 %s 取值非法，已回落到默认值" % key)
                self.data[key] = val
        if "hotkey" in raw:
            self.data["hotkey"] = str(raw["hotkey"] or DEFAULT_CONFIG["hotkey"])
        if "presentmon_path" in raw:
            self.data["presentmon_path"] = str(raw["presentmon_path"] or "")
        if "blacklist_extra" in raw:
            extra = raw["blacklist_extra"]
            if isinstance(extra, (list, tuple)):
                self.data["blacklist_extra"] = [str(x).strip().lower() for x in extra if str(x).strip()]
            else:
                self.warnings.append("blacklist_extra 必须是数组，已忽略")
        # ---- 皮肤相关键：必须能读回来（否则「换肤重启丢失」）----
        if "theme" in raw:
            tkey = str(raw["theme"] or "")
            if tkey in VALID_THEMES:
                self.data["theme"] = tkey
            else:
                self.warnings.append("主题 %r 不认识，已回落默认" % tkey)
        if "chart_style" in raw:
            ckey = str(raw["chart_style"] or "")
            if ckey in VALID_CHART_STYLES:
                self.data["chart_style"] = ckey
            else:
                self.warnings.append("图表样式 %r 不认识，已回落默认" % ckey)
        if "custom_bg" in raw:
            self.data["custom_bg"] = str(raw["custom_bg"] or "")
        return self

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        return self

    def save(self, path=None):
        """写回配置文件（原子写：先写临时文件再替换，避免写一半损坏）"""
        p = path or self.path
        try:
            d = os.path.dirname(os.path.abspath(p))
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False, sort_keys=True)
                f.write("\n")
            os.replace(tmp, p)
            return True
        except Exception:
            try:
                if os.path.isfile(p + ".tmp"):
                    os.remove(p + ".tmp")
            except Exception:
                pass
            return False

    def reset_defaults(self):
        """恢复默认（保留当前 path）"""
        self.data = default_config_dict()
        return self

    # ---------------- 应用到模块级常量 ----------------
    def apply_to_globals(self, module_globals=None):
        """
        把配置写回模块级常量。

        返回被成功应用的键列表；非法值不写回（保持原值）。

        拆分模块后：`module_globals is None`（默认）时写回**所有已登记模块**
        （见 consts.bind_globals），而不是只写本模块的 globals；
        显式传入字典时保持旧行为，方便单元测试。
        """
        values = {}
        for key, (gname, caster, _desc) in CONFIG_SCHEMA.items():
            cur = DEFAULT_CONFIG.get(key)
            if module_globals is not None:
                if gname not in module_globals:
                    continue
                cur = module_globals.get(gname, cur)
            val = _coerce(self.data.get(key), caster, cur)
            if caster is int:
                val = max(1, val)
            if caster is float and gname in ("CPU0_JAM_THRESHOLD", "CPU0_IDLE_OTHERS"):
                val = max(0.0, min(100.0, val))
            if gname == "PM_BUFFER_SIZE":
                # PresentMon 只接受 2 的幂
                v = max(256, int(val))
                p = 1
                while p * 2 <= v:
                    p *= 2
                val = p
            values[gname] = val
        if module_globals is not None:
            applied = []
            for key, (gname, _caster, _desc) in CONFIG_SCHEMA.items():
                if gname in module_globals:
                    module_globals[gname] = values[gname]
                    applied.append(key)
            return applied
        from . import consts as _consts
        _consts.apply_values(values)
        return list(CONFIG_SCHEMA.keys())

    def baseline_kwargs(self):
        """BaselineTracker 的构造参数（由配置派生）"""
        return {
            "ema": float(self.data.get("baseline_ema", 0.25)),
            "window": int(self.data.get("baseline_window", 240)),
            "spike_ratio": float(self.data.get("baseline_spike_ratio", 2.2)),
            "max_spike_frac": float(self.data.get("baseline_max_spike_frac", 0.35)),
        }

    def to_json(self):
        return json.dumps(self.data, indent=2, ensure_ascii=False, sort_keys=True)
