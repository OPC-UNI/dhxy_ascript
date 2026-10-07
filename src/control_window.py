"""自定义网页控制窗口：页面提供启动/重置/关闭三个控制按钮自动化任务。

ControlWindow 基于 WebWindow（pywebview），页面为 res/ui/control_window.html。
必须在 app.show()（webview.start）之前调用 prepare() 创建窗口，
与浮动控制面板共用同一次 GUI 事件循环。

页面结构：
- 顶部：启动/重置/关闭三个按钮 + 任务状态显示
- Tab 栏：任务设置（任务模式、任务次数）/ 通用设置（摄妖香、血蓝阈值、
  人物与召唤兽默认战斗动作，内含修罗设置分组：最少人数、必须有的种族，
  仅队长时生效）/ 高级设置（暂无）

功能约定：
- 启动：对当前选中的任务模式开始执行任务
- 重置：停止当前任务，并把模式恢复默认（五环）
- 关闭：仅隐藏控制窗口，程序继续后台运行（托盘菜单可再唤起）

注意：页面不要自行声明 callPython 函数，使用框架注入的桥接
（window.callPython → call_internal 通道），否则注入会被跳过。
"""
import webview
from pathlib import Path
from ascript.windows.system import R
from ascript.windows.system.key_value import KeyValue
from ascript.windows.ui import WebWindow

# ---- 任务模式（UI 单选项，KeyValue 持久化，记住上次选择） ----
TASK_MODES = ["五环", "客栈", "修罗", "五倍", "玄枢轶闻", "名望"]
_DEFAULT_MODE = TASK_MODES[0]  # 五环
_MODE_KEY = "task_mode"
# 是否使用摄妖香（UI 开关，KeyValue 持久化）
_USE_SHEYAOXIANG_KEY = "use_sheyaoxiang"
# 调试信息悬浮显示（UI 开关，KeyValue 持久化；游戏窗口上绘制任务/配置/进度）
_DEBUG_OVERLAY_KEY = "debug_overlay"


def load_task_mode():
    """读取上次选择的任务模式（首次默认五环）。"""
    mode = KeyValue.get(_MODE_KEY, _DEFAULT_MODE)
    return mode if mode in TASK_MODES else _DEFAULT_MODE


def save_task_mode(mode):
    """保存任务模式选择，返回操作结果。"""
    if mode not in TASK_MODES:
        return {"status": "error", "msg": "INVALID_MODE"}
    KeyValue.save(_MODE_KEY, mode)
    return {"status": "success"}


def load_use_sheyaoxiang():
    """读取是否使用摄妖香开关（首次默认开启）。"""
    return bool(KeyValue.get(_USE_SHEYAOXIANG_KEY, True))


def save_use_sheyaoxiang(on):
    """保存是否使用摄妖香开关，返回操作结果。"""
    KeyValue.save(_USE_SHEYAOXIANG_KEY, bool(on))
    return {"status": "success"}


def load_debug_overlay():
    """读取调试信息悬浮开关（首次默认关闭）。"""
    return bool(KeyValue.get(_DEBUG_OVERLAY_KEY, False))


def save_debug_overlay(on):
    """保存调试信息悬浮开关，返回操作结果。"""
    KeyValue.save(_DEBUG_OVERLAY_KEY, bool(on))
    return {"status": "success"}


# ---- 任务次数设置（每个任务模式独立，步进 1，下限 0；部分模式有上限） ----
TASK_COUNT_DEFAULTS = {"五环": 2, "客栈": 20, "修罗": 90, "五倍": 100, "玄枢轶闻": 5, "名望": 5}
# 上限约束：未列出的模式（修罗、五倍）无上限
TASK_COUNT_MAX = {"五环": 2, "客栈": 20, "玄枢轶闻": 5, "名望": 5}
_COUNT_KEYS = {mode: f"task_count_{mode}" for mode in TASK_MODES}


def _clamp_count(mode, value):
    """按模式夹取次数：下限 0，上限取 TASK_COUNT_MAX（未限定则无上限）。"""
    value = max(0, int(value))
    return min(value, TASK_COUNT_MAX[mode]) if mode in TASK_COUNT_MAX else value


def load_task_counts():
    """读取各任务模式的执行次数（首次使用各自默认值，并夹在上下限内）。"""
    return {mode: _clamp_count(mode, KeyValue.get(_COUNT_KEYS[mode],
                                                  TASK_COUNT_DEFAULTS.get(mode, 0)))
            for mode in TASK_MODES}


def save_task_count(mode, value):
    """保存某任务模式的执行次数（按模式上下限夹取），返回操作结果。"""
    if mode not in _COUNT_KEYS:
        return {"status": "error", "msg": "INVALID_MODE"}
    value = _clamp_count(mode, value)
    KeyValue.save(_COUNT_KEYS[mode], value)
    return {"status": "success", "count": value}


# ---- 血蓝条阈值设置（各条独立，步进 5%，默认 40%） ----
_THRESHOLD_KEYS = {
    "char_hp": "threshold_char_hp",
    "char_mp": "threshold_char_mp",
    "beast_hp": "threshold_beast_hp",
    "beast_mp": "threshold_beast_mp",
}
DEFAULT_THRESHOLD = 40


def load_thresholds():
    """读取各条血蓝阈值（百分比，默认 40）。"""
    return {name: KeyValue.get(key, DEFAULT_THRESHOLD)
            for name, key in _THRESHOLD_KEYS.items()}


def save_threshold(name, value):
    """保存某条血蓝阈值，返回操作结果。"""
    if name not in _THRESHOLD_KEYS:
        return {"status": "error", "msg": "INVALID"}
    value = int(value)
    if value < 5 or value > 95:
        return {"status": "error", "msg": "OUT_OF_RANGE"}
    KeyValue.save(_THRESHOLD_KEYS[name], value)
    return {"status": "success", "threshold": value}


# ---- 修罗设置（最少人数 + 必须有的种族，仅队长时生效，KeyValue 持久化） ----
XIULUO_MIN_DEFAULT = 5
XIULUO_MIN_RANGE = (3, 5)  # 最少人数范围：最小 3，最大 5
XIULUO_RACES = ["男人", "女人", "男鬼", "女鬼", "男仙", "女仙", "男魔", "女魔", "男巫", "女巫"]
XIULUO_RACES_DEFAULT = ["男人", "男鬼"]
_XIULUO_MIN_KEY = "xiuluo_min_members"
_XIULUO_RACES_KEY = "xiuluo_required_races"


def load_xiuluo_settings():
    """读取修罗设置：最少人数（3~5，默认 5）与必须有的种族（默认 男人+男鬼）。"""
    min_members = int(KeyValue.get(_XIULUO_MIN_KEY, XIULUO_MIN_DEFAULT))
    min_members = min(XIULUO_MIN_RANGE[1], max(XIULUO_MIN_RANGE[0], min_members))
    raw = str(KeyValue.get(_XIULUO_RACES_KEY, ",".join(XIULUO_RACES_DEFAULT)))
    races = [r for r in raw.replace("，", ",").split(",") if r in XIULUO_RACES]
    return {"min_members": min_members,
            "races": races or list(XIULUO_RACES_DEFAULT)}


def save_xiuluo_min(value):
    """保存修罗最少人数（夹取 3~5），返回操作结果。"""
    value = min(XIULUO_MIN_RANGE[1], max(XIULUO_MIN_RANGE[0], int(value)))
    KeyValue.save(_XIULUO_MIN_KEY, value)
    return {"status": "success", "min_members": value}


def save_xiuluo_races(races):
    """保存修罗必须有的种族（过滤非法项，按逗号串存储），返回操作结果。"""
    races = [r for r in (races or []) if r in XIULUO_RACES]
    KeyValue.save(_XIULUO_RACES_KEY, ",".join(races))
    return {"status": "success", "races": races}


# ---- 战斗设置（人物/召唤兽默认战斗动作，UI 单选项，KeyValue 持久化） ----
# 人物：技能面板 3 行 × 5 列共 15 格 + 攻击/防御/捕捉/逃跑/无
CHAR_BATTLE_ACTIONS = ([f"技能{r}-{c}" for r in range(1, 4) for c in range(1, 6)]
                       + ["攻击", "防御", "捕捉", "逃跑", "无"])
# 召唤兽：技能 5 格 + 攻击/防御/逃跑/无（召唤兽不能捕捉）
BEAST_BATTLE_ACTIONS = [f"技能{i}" for i in range(1, 6)] + ["攻击", "防御", "逃跑", "无"]
_BATTLE_KEYS = {"char": "battle_char_action", "beast": "battle_beast_action"}
_DEFAULT_BATTLE = {"char": "攻击", "beast": "攻击"}


def load_battle_actions():
    """读取人物与召唤兽的默认战斗动作（首次默认攻击）。"""
    result = {}
    for name, key in _BATTLE_KEYS.items():
        options = CHAR_BATTLE_ACTIONS if name == "char" else BEAST_BATTLE_ACTIONS
        value = KeyValue.get(key, _DEFAULT_BATTLE[name])
        result[name] = value if value in options else _DEFAULT_BATTLE[name]
    return result


def save_battle_action(target, value):
    """保存人物/召唤兽的默认战斗动作，返回操作结果。"""
    if target not in _BATTLE_KEYS:
        return {"status": "error", "msg": "INVALID_TARGET"}
    options = CHAR_BATTLE_ACTIONS if target == "char" else BEAST_BATTLE_ACTIONS
    if value not in options:
        return {"status": "error", "msg": "INVALID_VALUE"}
    KeyValue.save(_BATTLE_KEYS[target], value)
    return {"status": "success"}


class _ApiBridge:
    """JS 与 Python 之间的 API 桥接，与 WebWindow 内部实现保持一致。"""

    def __init__(self, funcs):
        self._funcs = funcs

    def call_internal(self, func_name, args):
        f = self._funcs.get(func_name)
        if not f:
            return None
        try:
            return f(*args)
        except Exception as e:
            print(f"Error calling {func_name}: {e}")
            return None


class ControlWindow(WebWindow):
    """独立网页控制窗口。

    开始/停止按钮通过桥接调用浮动面板（FloatScriptWindow）的启停逻辑，
    任务进程的创建、日志转发等仍由浮动面板统一管理。
    """

    def __init__(self, app, title="AScript 控制面板"):
        super().__init__(R.ui("control_window.html"))
        self._app = app
        self._title = title
        self._visible = True  # 启动时直接可见
        self.expose(self.get_init_data)
        self.expose(self.save_task_mode)
        self.expose(self.save_task_count)
        self.expose(self.save_use_sheyaoxiang)
        self.expose(self.save_threshold)
        self.expose(self.save_battle_action)
        self.expose(self.save_xiuluo_min)
        self.expose(self.save_xiuluo_races)
        self.expose(self.save_debug_overlay)
        self.expose(self.start_task)
        self.expose(self.reset_task)
        self.expose(self.close_window)

    def get_init_data(self):
        """页面初始化数据：任务模式、任务次数、摄妖香开关、各条阈值、
        战斗动作、修罗设置与调试信息开关。"""
        return {"mode": load_task_mode(),
                "task_counts": load_task_counts(),
                "use_sheyaoxiang": load_use_sheyaoxiang(),
                "thresholds": load_thresholds(),
                "battle": load_battle_actions(),
                "xiuluo": load_xiuluo_settings(),
                "debug_overlay": load_debug_overlay()}

    def save_battle_action(self, target, value):
        """战斗设置变更：保存人物/召唤兽默认战斗动作。"""
        return save_battle_action(target, value)

    def save_threshold(self, name, value):
        """阈值设置变更：保存某条血蓝阈值。"""
        return save_threshold(name, value)

    def save_task_mode(self, mode):
        """单选项变更：保存任务模式。"""
        return save_task_mode(mode)

    def save_task_count(self, mode, value):
        """任务次数变更：保存某模式的执行次数。"""
        return save_task_count(mode, value)

    def save_use_sheyaoxiang(self, on):
        """摄妖香开关变更：保存开关状态。"""
        return save_use_sheyaoxiang(on)

    def save_xiuluo_min(self, value):
        """修罗设置变更：保存最少人数。"""
        return save_xiuluo_min(value)

    def save_xiuluo_races(self, races):
        """修罗设置变更：保存必须有的种族列表。"""
        return save_xiuluo_races(races)

    def save_debug_overlay(self, on):
        """调试信息开关变更：保存悬浮显示开关状态。"""
        return save_debug_overlay(on)

    def start_task(self):
        """启动按钮：对当前选中的模式开始执行任务。"""
        return self._app.on_toggle_click(True)

    def reset_task(self):
        """重置按钮：停止当前任务，并把模式恢复默认（五环）。"""
        result = self._app.on_toggle_click(False)
        if result.get("status") != "success":
            return result
        save_task_mode(_DEFAULT_MODE)
        return {"status": "success", "mode": load_task_mode()}

    def close_window(self):
        """关闭按钮：仅隐藏控制窗口，程序继续后台运行。"""
        if self.webview_instance and self._visible:
            try:
                self.webview_instance.hide()
                self._visible = False
            except Exception:
                pass
        return {"status": "success"}

    def toggle_visible(self):
        """切换控制窗口显示/隐藏（供托盘菜单调用）。"""
        if not self.webview_instance:
            return
        try:
            if self._visible:
                self.webview_instance.hide()
                self._visible = False
            else:
                self.webview_instance.show()
                self._visible = True
        except Exception:
            pass

    def sync_status(self, running):
        """任务状态同步到页面（仅更新状态与按钮，不改窗口显隐）。"""
        if self.webview_instance:
            try:
                state = "true" if running else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.syncStatus){{window.syncStatus({state});}}")
            except Exception:
                pass

    def prepare(self, width=360, height=500):
        """创建窗口但不启动事件循环（webview.start 由 app.show() 统一执行）。"""
        file_url = Path(self.abs_path).as_uri()
        win = webview.create_window(
            title=self._title,
            url=file_url,
            js_api=_ApiBridge(self._exposed_functions),
            width=width,
            height=height,
        )
        if win is None:
            return
        self.webview_instance = win

        def on_loaded(*_args):
            self._inject("control window loaded")

        win.events.loaded += on_loaded
