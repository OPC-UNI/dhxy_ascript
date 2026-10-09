"""自定义网页控制窗口：任务启动/停止、页面设置与配置、独立吸附日志窗口。

ControlWindow 基于 WebWindow（pywebview），页面为 res/ui/control_window.html，
是应用主窗口；日志由独立的 LogWindow 吸附在主窗口右侧，仅在调试信息开关
开启时显示（主窗口移动/缩放时跟随）。先 prepare() 创建窗口，再 show() 进入
阻塞式 GUI 事件循环（webview.start 由主线程执行，配合 Runtime.wait 驻留
线程防程序自行退出）。

页面结构：
- 顶部：启动/停止按钮 + 重置按钮 + 任务状态显示 + 浅色/深色主题切换
- Tab 栏：任务设置（每任务一组：模式单选 + 次数步进）/ 通用设置
  （摄妖香、血蓝阈值、人物与召唤兽默认战斗动作，内含修罗/五倍设置
  分组，仅队长时生效）/ 高级设置（调试信息悬浮开关）

功能约定：
- 启动/停止：桥接调用 TaskManager 启停任务子进程（升级强杀保证线程全清）
- 重置：停止当前任务，并把模式恢复默认（五环）
- 关闭（X）：取消关闭并隐藏到托盘（_on_closing），任务继续运行；
  真正退出须先 close_window() 放行（_real_close），否则窗口永不销毁
- 调试信息开关：控制游戏窗口悬浮层（任务侧读取），同时控制日志窗口显隐；
  关闭日志窗口 = 自动关闭调试信息开关（页面勾选同步）

注意：页面不要自行声明 callPython 函数，使用框架注入的桥接
（window.callPython → call_internal 通道），否则注入会被跳过。
"""
import json
import threading
import webview
from pathlib import Path
from ascript.windows.system import R
from ascript.windows.system.key_value import KeyValue
from ascript.windows.ui import WebWindow
from src.log_window import LogWindow
from . import game  # 游戏窗口查找（修罗回程验证用；game 不反向依赖本模块，可顶层导入）

# ---- 任务模式（UI 单选项，KeyValue 持久化，记住上次选择） ----
TASK_MODES = ["五环", "客栈", "修罗", "五倍", "玄枢轶闻", "名望"]
_DEFAULT_MODE = TASK_MODES[0]  # 五环
_MODE_KEY = "task_mode"
# 是否使用摄妖香（UI 开关，KeyValue 持久化）
_USE_SHEYAOXIANG_KEY = "use_sheyaoxiang"
# 调试信息悬浮显示（UI 开关，KeyValue 持久化；游戏窗口上绘制任务/配置/进度）
_DEBUG_OVERLAY_KEY = "debug_overlay"
# UI 主题（浅色/深色，KeyValue 持久化；首次默认浅色）
_UI_THEME_KEY = "ui_theme"
UI_THEMES = ("dark", "light")


def load_ui_theme():
    """读取 UI 主题（首次默认浅色）。"""
    theme = KeyValue.get(_UI_THEME_KEY, "light")
    return theme if theme in UI_THEMES else "light"


def save_ui_theme(theme):
    """保存 UI 主题（浅色/深色），返回操作结果。"""
    if theme not in UI_THEMES:
        return {"status": "error", "msg": "INVALID_THEME"}
    KeyValue.save(_UI_THEME_KEY, theme)
    return {"status": "success"}


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
XIULUO_RACES = ["男人", "女人", "男鬼", "女鬼", "男仙", "女仙", "男魔", "女魔", "男巫", "女巫",
                "男龙", "女龙"]
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


# 五倍设置：最少人数（默认 5，范围 1~5）
WUBEI_MIN_DEFAULT = 5
WUBEI_MIN_RANGE = (1, 5)
_WUBEI_MIN_KEY = "wubei_min_members"


def load_wubei_min():
    """读取五倍最少人数（1~5，默认 5）。"""
    value = int(KeyValue.get(_WUBEI_MIN_KEY, WUBEI_MIN_DEFAULT))
    return min(WUBEI_MIN_RANGE[1], max(WUBEI_MIN_RANGE[0], value))


def save_wubei_min(value):
    """保存五倍最少人数（夹取 1~5），返回操作结果。"""
    value = min(WUBEI_MIN_RANGE[1], max(WUBEI_MIN_RANGE[0], int(value)))
    KeyValue.save(_WUBEI_MIN_KEY, value)
    return {"status": "success", "min_members": value}


# 五倍设置：是否自动抢盒子（默认关闭；仅非队长时生效，队长一定抢）
_WUBEI_HEZI_KEY = "wubei_auto_hezi"


def load_wubei_hezi():
    """读取五倍自动抢盒子开关（首次默认关闭）。"""
    return bool(KeyValue.get(_WUBEI_HEZI_KEY, False))


def save_wubei_hezi(on):
    """保存五倍自动抢盒子开关，返回操作结果。"""
    KeyValue.save(_WUBEI_HEZI_KEY, bool(on))
    return {"status": "success"}


# 修罗设置：是否自动抢盒子（福源广泽盒出现时自动点「我需要」，默认关闭）
_XIULUO_HEZI_KEY = "xiuluo_auto_hezi"


def load_xiuluo_hezi():
    """读取修罗自动抢盒子开关（首次默认关闭）。"""
    return bool(KeyValue.get(_XIULUO_HEZI_KEY, False))


def save_xiuluo_hezi(on):
    """保存修罗自动抢盒子开关，返回操作结果。"""
    KeyValue.save(_XIULUO_HEZI_KEY, bool(on))
    return {"status": "success"}


# ---- 修罗高性能模式（UI 开关，KeyValue 持久化，默认关闭；
#      开启后固定位置 UI（背包按钮/领取选项/看打选项）首次识别记录
#      坐标，后续直接按坐标点击 + 效果校验，失败回退识别路径——
#      位置漂移/窗口缩放时自动失效并自愈） ----
_XIULUO_HIGH_PERF_KEY = "xiuluo_high_perf"


def load_xiuluo_high_perf():
    """读取修罗高性能开关（首次默认关闭）。"""
    return bool(KeyValue.get(_XIULUO_HIGH_PERF_KEY, False))


def save_xiuluo_high_perf(on):
    """保存修罗高性能开关，返回操作结果。"""
    KeyValue.save(_XIULUO_HIGH_PERF_KEY, bool(on))
    return {"status": "success"}


# ---- 修罗检查队伍（UI 开关，KeyValue 持久化；关闭时领取前跳过全部
#      队伍检查，默认关闭——用户确认每轮回程后开面板检查耗时 5~7s） ----
_XIULUO_CHECK_TEAM_KEY = "xiuluo_check_team"


def load_xiuluo_check_team():
    """读取修罗检查队伍开关（首次默认关闭）。"""
    return bool(KeyValue.get(_XIULUO_CHECK_TEAM_KEY, False))


def save_xiuluo_check_team(on):
    """保存修罗检查队伍开关，返回操作结果。"""
    KeyValue.save(_XIULUO_CHECK_TEAM_KEY, bool(on))
    return {"status": "success"}


# ---- 修罗自动巫医（UI 开关+间隔，KeyValue 持久化；默认开启、每 5 轮
#      一次——每 N 轮领取后无条件前往巫医 NPC 医治，血蓝正常也照常） ----
_XIULUO_AUTO_WUYI_KEY = "xiuluo_auto_wuyi"
_XIULUO_WUYI_INTERVAL_KEY = "xiuluo_wuyi_interval"
_WUYI_INTERVAL_MIN = 1
_WUYI_INTERVAL_MAX = 90


def load_xiuluo_auto_wuyi():
    """读取修罗自动巫医开关（首次默认开启）。"""
    return bool(KeyValue.get(_XIULUO_AUTO_WUYI_KEY, True))


def save_xiuluo_auto_wuyi(on):
    """保存修罗自动巫医开关，返回操作结果。"""
    KeyValue.save(_XIULUO_AUTO_WUYI_KEY, bool(on))
    return {"status": "success"}


def load_xiuluo_wuyi_interval():
    """读取修罗自动巫医间隔（轮，首次默认 5）。"""
    try:
        value = int(KeyValue.get(_XIULUO_WUYI_INTERVAL_KEY, 5))
    except (TypeError, ValueError):
        value = 5
    return max(_WUYI_INTERVAL_MIN, min(_WUYI_INTERVAL_MAX, value))


def save_xiuluo_wuyi_interval(value):
    """保存修罗自动巫医间隔（1~90 轮），返回操作结果。"""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return {"status": "error", "msg": "INVALID_INTERVAL"}
    if not (_WUYI_INTERVAL_MIN <= value <= _WUYI_INTERVAL_MAX):
        return {"status": "error", "msg": "OUT_OF_RANGE"}
    KeyValue.save(_XIULUO_WUYI_INTERVAL_KEY, value)
    return {"status": "success", "interval": value}


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
    """应用主窗口：任务启动/停止、配置页面；日志由吸附的 LogWindow 显示。

    启停通过桥接调用 TaskManager（任务子进程管理），状态与日志由
    TaskManager 回调推送（sync_status / add_logs → 日志窗口）。
    """

    def __init__(self, tm, title="啄木鸟"):
        super().__init__(R.ui("control_window.html"))
        self._tm = tm
        self._title = title
        self._visible = True  # 启动时直接可见
        self._real_close = threading.Event()  # 置位后 X/销毁真正放行（退出路径）
        self._debug_on = load_debug_overlay()  # 调试信息开关：控制日志窗口显隐
        self._verifying = False  # 修罗回程验证进行中标记（防连点）
        self.log_win = LogWindow()
        self.log_win.set_debug_off_callback(self._on_log_window_closed)
        self.log_win.apply_theme(load_ui_theme())  # 初始主题与主面板一致
        self.expose(self.get_init_data)
        self.expose(self.save_task_mode)
        self.expose(self.save_task_count)
        self.expose(self.save_use_sheyaoxiang)
        self.expose(self.save_threshold)
        self.expose(self.save_battle_action)
        self.expose(self.save_xiuluo_min)
        self.expose(self.save_xiuluo_races)
        self.expose(self.save_xiuluo_hezi)
        self.expose(self.save_xiuluo_check_team)
        self.expose(self.save_xiuluo_auto_wuyi)
        self.expose(self.save_xiuluo_wuyi_interval)
        self.expose(self.save_xiuluo_high_perf)
        self.expose(self.save_wubei_min)
        self.expose(self.save_wubei_hezi)
        self.expose(self.save_debug_overlay)
        self.expose(self.save_ui_theme)
        self.expose(self.start_task)
        self.expose(self.stop_task)
        self.expose(self.reset_task)
        self.expose(self.verify_xiuluo_return)
        self.expose(self.verify_xiuluo_claim)
        self.expose(self.verify_xiuluo_wuyi)
        self.expose(self.verify_wubei_claim)
        self.expose(self.verify_wubei_return)
        # 任务状态/日志回流接线：TaskManager → 主窗口状态 + 日志窗口
        self._tm.set_callbacks(on_status=self.sync_status, on_log=self.add_logs)

    def get_init_data(self):
        """页面初始化数据：主题、任务模式、任务次数、摄妖香开关、各条
        阈值、战斗动作、修罗/五倍设置与调试信息开关。"""
        return {"theme": load_ui_theme(),
                "mode": load_task_mode(),
                "task_counts": load_task_counts(),
                "use_sheyaoxiang": load_use_sheyaoxiang(),
                "thresholds": load_thresholds(),
                "battle": load_battle_actions(),
                "xiuluo": load_xiuluo_settings(),
                "xiuluo_hezi": load_xiuluo_hezi(),
                "xiuluo_check_team": load_xiuluo_check_team(),
                "xiuluo_auto_wuyi": load_xiuluo_auto_wuyi(),
                "xiuluo_wuyi_interval": load_xiuluo_wuyi_interval(),
                "xiuluo_high_perf": load_xiuluo_high_perf(),
                "wubei": {"min_members": load_wubei_min(),
                          "hezi": load_wubei_hezi()},
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

    def save_xiuluo_hezi(self, on):
        """修罗设置变更：保存自动抢盒子开关状态。"""
        return save_xiuluo_hezi(on)

    def save_xiuluo_check_team(self, on):
        """修罗设置变更：保存检查队伍开关状态。"""
        return save_xiuluo_check_team(on)

    def save_xiuluo_auto_wuyi(self, on):
        """修罗设置变更：保存自动巫医开关状态。"""
        return save_xiuluo_auto_wuyi(on)

    def save_xiuluo_wuyi_interval(self, value):
        """修罗设置变更：保存自动巫医间隔（轮）。"""
        return save_xiuluo_wuyi_interval(value)

    def save_xiuluo_high_perf(self, on):
        """修罗设置变更：保存高性能模式开关状态。"""
        return save_xiuluo_high_perf(on)

    def save_wubei_min(self, value):
        """五倍设置变更：保存最少人数。"""
        return save_wubei_min(value)

    def save_wubei_hezi(self, on):
        """五倍设置变更：保存自动抢盒子开关状态。"""
        return save_wubei_hezi(on)

    def save_debug_overlay(self, on):
        """调试信息开关变更：保存开关状态，并同步日志窗口显隐。"""
        result = save_debug_overlay(on)
        if result.get("status") == "success":
            self._set_debug_on(bool(on))
        return result

    def save_ui_theme(self, theme):
        """主题切换：保存浅色/深色选择，并同步日志窗口主题。"""
        result = save_ui_theme(theme)
        if result.get("status") == "success":
            self.log_win.apply_theme(theme)
        return result

    def start_task(self):
        """启动按钮：启动任务子进程。"""
        return self._tm.start()

    def stop_task(self):
        """停止按钮：停止任务子进程（升级强杀，保证任务线程全清）。"""
        return self._tm.stop()

    def reset_task(self):
        """重置按钮：停止当前任务，并把模式恢复默认（五环）。"""
        result = self._tm.stop()
        if result.get("status") != "success":
            return result
        save_task_mode(_DEFAULT_MODE)
        return {"status": "success", "mode": load_task_mode()}

    def verify_xiuluo_return(self):
        """高级设置-修罗回程验证按钮：与实际返程流程一致，但不右键使用
        回城符——仅将鼠标移动到回城符上定位（不点击）后关闭背包，供人工
        确认整条链路（开背包 → 任务储物格 → 回城符识别）是否正确。

        验证在主进程后台线程执行（不占用任务子进程通道）；
        任务运行中拒绝验证，避免与任务子进程互抢游戏操作。
        """
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        # 负面条件：游戏窗口须在前台可见——被遮挡时截图正常但点击
        # 会落到遮挡窗口上（实测 VS Code 最大化遮挡导致开包点击无效）
        if not _game_window_clickable(win):
            return {"status": "error", "msg": "游戏窗口被其他窗口遮挡，请将游戏窗口置于前台后再验证"}
        self._verifying = True
        threading.Thread(target=self._run_verify, args=(win,),
                         daemon=True, name="xiuluo-verify").start()
        return {"status": "success",
                "msg": "验证已开始：开背包 → 任务储物格 → 定位回城符（不点击）→ 关背包"}

    def verify_xiuluo_claim(self):
        """高级设置-修罗领取验证按钮：与实际领取流程一致，但到绿色选项
        处仅移动鼠标定位不点击（点击即领取）——验证走位/按钮识别/对话框/选项
        识别整条链路，最后点击屏幕中间关闭对话框，不领取任务。
        """
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        if not _game_window_clickable(win):
            return {"status": "error", "msg": "游戏窗口被其他窗口遮挡，请将游戏窗口置于前台后再验证"}
        self._verifying = True
        threading.Thread(target=self._run_claim_verify, args=(win,),
                         daemon=True, name="xiuluo-claim-verify").start()
        return {"status": "success",
                "msg": "验证已开始：走位 → 领取按钮 → 对话框选项（仅定位不点击）→ 关对话框"}

    def verify_xiuluo_wuyi(self):
        """高级设置-修罗巫医验证按钮：与自动巫医同一链路（条件门槛跳过、
        血蓝状态仅报告），NPC 点击前先移动鼠标定位（无延时）；对话框处理复用战斗线程
        逻辑（点击 NPC 后由战斗线程点「全部医治和修复」）。
        """
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        if not _game_window_clickable(win):
            return {"status": "error", "msg": "游戏窗口被其他窗口遮挡，请将游戏窗口置于前台后再验证"}
        self._verifying = True
        threading.Thread(target=self._run_wuyi_verify, args=(win,),
                         daemon=True, name="xiuluo-wuyi-verify").start()
        return {"status": "success",
                "msg": "验证已开始：血蓝检查 → 走位到巫医 → NPC 定位 → 点击 → 对话框处理"}

    def _run_wuyi_verify(self, win):
        """巫医验证线程体：跑自动巫医链路（悬停代替直接点击），结果回推页面与日志窗。"""
        from . import xiuluo  # 延迟导入：xiuluo 反向导入本模块（避免循环）
        try:
            self.add_logs(["[验证] 开始验证修罗巫医（走位 → NPC 定位 → 点击 → 战斗线程处理对话框）..."])
            ok, info = xiuluo.verify_wuyi_flow(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_wuyi_verify_result(ok, info)
        self._verifying = False

    def _notify_wuyi_verify_result(self, ok, info):
        """巫医验证结果回推页面（高级设置-巫医验证行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onWuyiVerifyResult){{window.onWuyiVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def _run_claim_verify(self, win):
        """领取验证线程体：跑修罗领取链路（选项悬停代替点击），结果回推页面与日志窗。"""
        from . import xiuluo  # 延迟导入：xiuluo 反向导入本模块（避免循环）
        try:
            self.add_logs(["[验证] 开始验证修罗领取（选项仅定位不点击，最后关闭对话框）..."])
            ok, info = xiuluo.verify_claim_flow(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_claim_verify_result(ok, info)
        self._verifying = False

    def verify_wubei_claim(self):
        """高级设置-五倍领取验证按钮：与五倍领取流程一致，但到绿色选项
        处仅移动鼠标定位不点击（点击即领取）——验证走位/入口识别/对话框/
        选项识别整条链路，最后点击屏幕中间关闭对话框，不领取任务。
        """
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        if not _game_window_clickable(win):
            return {"status": "error", "msg": "游戏窗口被其他窗口遮挡，请将游戏窗口置于前台后再验证"}
        self._verifying = True
        threading.Thread(target=self._run_wubei_claim_verify, args=(win,),
                         daemon=True, name="wubei-claim-verify").start()
        return {"status": "success",
                "msg": "验证已开始：走位 → 领取入口 → 对话框选项（仅定位不点击）→ 关对话框"}

    def _run_wubei_claim_verify(self, win):
        """五倍领取验证线程体：跑五倍领取链路（选项悬停代替点击），结果回推页面与日志窗。"""
        from . import wubei  # 延迟导入：wubei 反向导入本模块（避免循环）
        try:
            self.add_logs(["[验证] 开始验证五倍领取（选项仅定位不点击，最后关闭对话框）..."])
            ok, info = wubei.verify_claim_flow(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_wubei_claim_verify_result(ok, info)
        self._verifying = False

    def _notify_wubei_claim_verify_result(self, ok, info):
        """五倍领取验证结果回推页面（高级设置-五倍领取验证行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onWubeiClaimVerifyResult){{window.onWubeiClaimVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_wubei_return(self):
        """高级设置-五倍回程验证按钮：与实际返程流程一致，但不右键使用
        回城符——仅将鼠标移动到回城符上定位（不点击）后关闭背包，供人工
        确认整条链路（开背包 → 任务储物格 → 回城符识别）是否正确。
        """
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        if not _game_window_clickable(win):
            return {"status": "error", "msg": "游戏窗口被其他窗口遮挡，请将游戏窗口置于前台后再验证"}
        self._verifying = True
        threading.Thread(target=self._run_wubei_return_verify, args=(win,),
                         daemon=True, name="wubei-return-verify").start()
        return {"status": "success",
                "msg": "验证已开始：开背包 → 任务储物格 → 定位回城符（不点击）→ 关背包"}

    def _run_wubei_return_verify(self, win):
        """五倍回程验证线程体：跑五倍返程链路（悬停代替点击），结果回推页面与日志窗。"""
        from . import wubei  # 延迟导入：wubei 反向导入本模块（避免循环）
        try:
            self.add_logs(["[验证] 开始验证五倍回程（不点击回城符，定位后关闭背包）..."])
            ok, info = wubei.verify_return_flow(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_wubei_return_verify_result(ok, info)
        self._verifying = False

    def _notify_wubei_return_verify_result(self, ok, info):
        """五倍回程验证结果回推页面（高级设置-五倍回程验证行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onWubeiReturnVerifyResult){{window.onWubeiReturnVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def _notify_claim_verify_result(self, ok, info):
        """领取验证结果回推页面（高级设置-领取验证行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onClaimVerifyResult){{window.onClaimVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def _run_verify(self, win):
        """验证线程体：跑修罗返程链路（悬停代替点击），结果回推页面与日志窗。"""
        from . import xiuluo  # 延迟导入：xiuluo 反向导入本模块（避免循环）
        try:
            self.add_logs(["[验证] 开始验证修罗回程（不点击回城符，定位后关闭背包）..."])
            ok, info = xiuluo.verify_return_flow(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_verify_result(ok, info)
        self._verifying = False

    def _notify_verify_result(self, ok, info):
        """验证结果回推页面（高级设置-验证行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onVerifyResult){{window.onVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def add_log(self, line):
        """单行日志推送到吸附日志窗口（主进程 [系统] 消息用）。"""
        self.add_logs([line])

    def add_logs(self, lines):
        """批量日志推送到吸附日志窗口（隐藏期间由 LogWindow 内部缓冲）。"""
        self.log_win.add_logs(lines)

    # ---- 日志窗口联动 ----

    def _set_debug_on(self, on):
        """更新调试信息开关状态并刷新日志窗口显隐。"""
        self._debug_on = bool(on)
        self._refresh_log_visibility()

    def _refresh_log_visibility(self):
        """日志窗口可见 = 调试信息开启 且 主窗口可见；显示前先吸附定位。"""
        visible = self._debug_on and self._visible
        if visible:
            self._dock_log()
        self.log_win.set_visible(visible)

    def _dock_log(self):
        """日志窗口吸附到主窗口右侧（等高、右缘对齐、顶部对齐）。"""
        if not (self.webview_instance and self.log_win.webview_instance):
            return
        try:
            mw = self.webview_instance
            lw = self.log_win.webview_instance
            lw.resize(260, mw.height)
            lw.move(mw.x + mw.width, mw.y)
        except Exception:
            pass

    def _on_log_window_closed(self):
        """日志窗口被 X 关闭：关闭调试信息开关并同步主页面勾选。"""
        save_debug_overlay(False)
        self._debug_on = False
        if self.webview_instance:
            try:
                self.webview_instance.evaluate_js(
                    "if(window.setDebugSwitch){window.setDebugSwitch(false);}")
            except Exception:
                pass

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
        self._refresh_log_visibility()

    def sync_status(self, running):
        """任务状态同步到页面（仅更新状态与按钮，不改窗口显隐）。"""
        if self.webview_instance:
            try:
                state = "true" if running else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.syncStatus){{window.syncStatus({state});}}")
            except Exception:
                pass

    def _on_closing(self, *_args):
        """用户点 X：取消关闭并隐藏到托盘（任务继续运行）。

        返回 False → pywebview closing 事件置 Cancel（winforms FormClosing 取消）；
        _real_close 置位时返回 True 放行（真正退出路径）。本处理器在 GUI
        STA 线程同步执行（should_lock=True），hide() 无需 Invoke。
        """
        if self._real_close.is_set():
            return True
        self._visible = False
        try:
            self.webview_instance.hide()
        except Exception:
            pass
        self._refresh_log_visibility()  # 主窗口隐藏时日志窗口一并隐藏
        return False

    def close_window(self):
        """真正退出：放行关闭并销毁全部窗口 → Application.Exit → webview.start 返回。"""
        self._real_close.set()
        self.log_win.close_window()
        if self.webview_instance:
            try:
                self.webview_instance.destroy()
            except Exception:
                pass

    def prepare(self, width=360, height=500):
        """创建主窗口与日志窗口（不启动事件循环，webview.start 统一执行）。

        日志窗口初始隐藏，主窗口 shown 后按调试信息开关决定是否吸附显示；
        主窗口移动/缩放/最小化/恢复时同步日志窗口位置与显隐。
        """
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
        win.events.closing += self._on_closing
        # 主窗口 shown 后首次吸附定位并应用日志窗口初始显隐
        win.events.shown += lambda: self._refresh_log_visibility()
        # 移动/缩放时跟随吸附（仅日志窗口可见时重定位）
        win.events.moved += self._on_main_moved
        win.events.resized += self._on_main_moved
        # 最小化隐藏日志窗口，恢复时按开关重新显示
        win.events.minimized += lambda: self.log_win.set_visible(False)
        win.events.restored += lambda: self._refresh_log_visibility()

        # 日志窗口：初始隐藏创建（与主窗口同一次 webview.start）
        self.log_win.prepare()

    def _on_main_moved(self):
        """主窗口移动/缩放：日志窗口可见时重新吸附。"""
        if self._debug_on and self._visible:
            self._dock_log()


def _game_window_clickable(win):
    """检查游戏窗口中心点是否被其他窗口遮挡（点击会落到遮挡窗口上）。

    截图不依赖前台（窗口 DC 直接读取），但鼠标点击是屏幕坐标——
    游戏被最大化窗口遮挡时点击全落到遮挡窗口上。沿父窗口链向上
    查找，能走到游戏窗口 hwnd 才认为可点击。
    注意：必须放在类定义之后（模块级），否则类方法会被吞进本函数体。
    """
    try:
        import win32gui
        cx = win.rect.left + win.rect.width // 2
        cy = win.rect.top + win.rect.height // 2
        hwnd = win32gui.WindowFromPoint((cx, cy))
        game_hwnd = win.hwnd
        while hwnd:
            if hwnd == game_hwnd:
                return True
            hwnd = win32gui.GetParent(hwnd)
        return False
    except Exception:
        return True  # 检查失败不阻塞验证（宁可放行）
