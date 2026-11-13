"""角色/全局设置访问器（从 control_window 抽出，2026-11-13）。

本模块只做持久化读写：KeyValue（全局设置）与角色设置/状态文件。
UI 布局、窗口、桥接逻辑仍留在 control_window.py；任务/设备模块只依赖
本模块，不再间接加载 GUI 栈。

迁移约定：设置键名、默认值、夹取/迁移逻辑一律保持不变；control_window
在过渡期 re-export 本模块的名字以兼容旧 import。
"""
import time

from ascript.windows.system.key_value import KeyValue

from . import app_utils
from . import role_settings
from . import user_data

# ---- 任务模式（UI 多选项，角色设置持久化，记住上次选择） ----
TASK_MODES = ["五环", "客栈", "修罗", "五倍", "种族比武", "玄枢轶闻", "名望",
              "仅战斗"]
_DEFAULT_MODES = ["五环"]  # 首次默认选中五环（与原单选默认一致）
_MODES_KEY = "task_modes"           # 多选列表（新键）
_MODE_KEY_OLD = "task_mode"         # 旧单选键（自动迁移）
BATTLE_ONLY_MODE = "仅战斗"  # 特殊任务：与其它任务互斥，启动后仅运行战斗线程

# ---- 使用环境（高级设置-使用环境展示；只读信息） ----
ENV_THEME = "水墨"               # 已适配的游戏 UI 主题
ENV_ADAPT_RES = (1378, 831)      # 坐标标定的窗口分辨率（实测布局基准）
# 是否使用摄妖香（UI 开关，角色设置持久化）
_USE_SHEYAOXIANG_KEY = "use_sheyaoxiang"
# 调试信息悬浮显示（UI 开关，KeyValue 持久化；游戏窗口上绘制任务/配置/进度）
_DEBUG_OVERLAY_KEY = "debug_overlay"
# 「显示与调试」卡片解锁标记（发行版默认隐藏该卡片与诊断入口；在「系统页-
# 关于」连点版本号 6 次解锁。全局设置，KeyValue 持久化，不进配置方案）
_DEBUG_UI_KEY = "debug_ui_unlocked"
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
    _kv_save(_UI_THEME_KEY, theme)
    return {"status": "success"}


def _sanitize_modes(modes):
    """过滤非法任务模式并强制「仅战斗」互斥，保留输入顺序。

    返回顺序 = 保存/选择顺序（任务按列表顺序执行，面板拖动排序的
    顺序必须原样保留）；仅战斗与其它任务不可共存：混合列表（页面已
    禁用互斥，正常不出现，此处兜底旧数据/方案导入）按保留普通任务、
    剔除仅战斗处理。
    """
    modes = [m for m in modes if m in TASK_MODES]
    if BATTLE_ONLY_MODE in modes and len(modes) > 1:
        modes = [m for m in modes if m != BATTLE_ONLY_MODE]
    return modes


def load_selected_modes():
    """读取选中的任务模式列表（首次默认五环；旧单选键自动迁移）。

    按保存顺序返回（= 面板已选任务的拖动顺序，执行顺序与列表一致）；
    旧数据本就按 TASK_MODES 固定顺序保存，读取结果与旧版一致。
    """
    raw = role_settings.get(_MODES_KEY, None)
    if raw is None:
        old = role_settings.get(_MODE_KEY_OLD)
        raw = [old] if old in TASK_MODES else list(_DEFAULT_MODES)
    if not isinstance(raw, list):
        raw = []
    return _sanitize_modes(raw)


def _kv_save(key, value):
    """全局设置写入（主题/调试/更新等）：走 app_utils.kv_save_merged，
    锁内重读→改→写，避免旧快照整文件覆写。"""
    app_utils.kv_save_merged(key, value)


def _kv_remove(key):
    """KeyValue.remove 包装：移除前重读合并（同 _kv_save 理由）。"""
    app_utils.kv_remove_merged(key)

def save_selected_modes(modes):
    """保存任务模式多选列表（过滤非法项、强制「仅战斗」互斥并保留
    传入顺序 = 面板拖动顺序，允许空列表），返回操作结果。"""
    modes = _sanitize_modes(modes)
    role_settings.set_value(_MODES_KEY, modes)
    return {"status": "success", "modes": modes}


def load_use_sheyaoxiang():
    """读取是否使用摄妖香开关（首次默认开启）。"""
    return bool(role_settings.get(_USE_SHEYAOXIANG_KEY, True))


def save_use_sheyaoxiang(on):
    """保存是否使用摄妖香开关，返回操作结果。"""
    role_settings.set_value(_USE_SHEYAOXIANG_KEY, bool(on))
    return {"status": "success"}


def load_debug_overlay():
    """读取调试信息悬浮开关（首次默认关闭）。"""
    return bool(KeyValue.get(_DEBUG_OVERLAY_KEY, False))


def save_debug_overlay(on):
    """保存调试信息悬浮开关，返回操作结果。"""
    _kv_save(_DEBUG_OVERLAY_KEY, bool(on))
    return {"status": "success"}


def load_debug_ui_unlocked():
    """读取「显示与调试」卡片解锁标记（首次默认关闭）。

    发行版默认不展示该卡片（也就没有调试信息开关与诊断入口）；
    在「系统页-关于」连点版本号 6 次解锁。全局设置，不随配置方案切换。
    """
    return bool(KeyValue.get(_DEBUG_UI_KEY, False))


def save_debug_ui_unlocked(on):
    """保存「显示与调试」卡片解锁标记（全局，不进配置方案）。"""
    _kv_save(_DEBUG_UI_KEY, bool(on))
    return {"status": "success"}


# ---- 软件更新（系统页「关于」；config.json 持久化，覆盖升级保留） ----
_UPDATE_AUTO_CHECK_KEY = "update_auto_check"
_UPDATE_LAST_CHECK_KEY = "update_last_check_ts"
_UPDATE_SKIPPED_KEY = "update_skipped_version"
_UPDATE_FEED_KEY = "update_feed_url"


def load_update_auto_check():
    """读取自动检查更新开关（首次默认开启）。"""
    return bool(KeyValue.get(_UPDATE_AUTO_CHECK_KEY, True))


def save_update_auto_check(on):
    """保存自动检查更新开关，返回操作结果。"""
    _kv_save(_UPDATE_AUTO_CHECK_KEY, bool(on))
    return {"status": "success"}


def load_update_last_check():
    """上次成功检查时间戳（秒），用于 24 小时节流。"""
    try:
        return float(KeyValue.get(_UPDATE_LAST_CHECK_KEY, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def save_update_last_check(ts):
    _kv_save(_UPDATE_LAST_CHECK_KEY, float(ts))


def load_update_skipped_version():
    """用户选择跳过的版本（空串 = 未跳过）。"""
    return str(KeyValue.get(_UPDATE_SKIPPED_KEY, "") or "")


def save_update_skipped_version(version):
    _kv_save(_UPDATE_SKIPPED_KEY, str(version or ""))


def load_update_feed_url():
    """config.json 覆盖清单地址（测试/应急；默认空）。"""
    return str(KeyValue.get(_UPDATE_FEED_KEY, "") or "")


def _as_bool(value):
    """桥接参数归一化：兼容 true/false 布尔与 "true"/"false" 字符串。"""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


# ---- 任务次数设置（每个任务模式独立，步进 1，下限 0；部分模式有上限） ----
TASK_COUNT_DEFAULTS = {"五环": 2, "客栈": 20, "修罗": 90, "五倍": 100,
                       "种族比武": 10, "玄枢轶闻": 5, "名望": 5,
                       "仅战斗": 0}  # 仅战斗持续运行无次数概念（0=不限；页面无步进器，
                                     # 方案快照 _settings_spec 按 _COUNT_KEYS 展开需要该键）
# 上限约束：未列出的模式（修罗、五倍）无上限
TASK_COUNT_MAX = {"五环": 2, "客栈": 20, "种族比武": 100,
                  "玄枢轶闻": 5, "名望": 5}
_COUNT_KEYS = {mode: f"task_count_{mode}" for mode in TASK_MODES}


def _clamp_count(mode, value):
    """按模式夹取次数：下限 0，上限取 TASK_COUNT_MAX（未限定则无上限）。"""
    value = max(0, int(value))
    return min(value, TASK_COUNT_MAX[mode]) if mode in TASK_COUNT_MAX else value


def load_task_counts():
    """读取各任务模式的执行次数（首次使用各自默认值，并夹在上下限内）。"""
    return {mode: _clamp_count(mode, role_settings.get(_COUNT_KEYS[mode],
                                                  TASK_COUNT_DEFAULTS.get(mode, 0)))
            for mode in TASK_MODES}


def save_task_count(mode, value):
    """保存某任务模式的执行次数（按模式上下限夹取），返回操作结果。"""
    if mode not in _COUNT_KEYS:
        return {"status": "error", "msg": "INVALID_MODE"}
    value = _clamp_count(mode, value)
    role_settings.set_value(_COUNT_KEYS[mode], value)
    return {"status": "success", "count": value}


# ---- 任务总进度（页面进度条数据源；任务子进程写入进度后主进程
#      经 get_task_progress 实时读取） ----
_TASK_PROGRESS_KEY = "task_progress"  # 与 app_utils._TASK_PROGRESS_KEY 同键


# ---- 血蓝条阈值设置（各条独立，步进 5%，默认 50%） ----
_THRESHOLD_KEYS = {
    "char_hp": "threshold_char_hp",
    "char_mp": "threshold_char_mp",
    "beast_hp": "threshold_beast_hp",
    "beast_mp": "threshold_beast_mp",
}
DEFAULT_THRESHOLD = 50


def load_thresholds():
    """读取各条血蓝阈值（百分比，默认 50）。"""
    return {name: role_settings.get(key, DEFAULT_THRESHOLD)
            for name, key in _THRESHOLD_KEYS.items()}


def save_threshold(name, value):
    """保存某条血蓝阈值，返回操作结果。"""
    if name not in _THRESHOLD_KEYS:
        return {"status": "error", "msg": "INVALID"}
    value = int(value)
    if value < 5 or value > 95:
        return {"status": "error", "msg": "OUT_OF_RANGE"}
    role_settings.set_value(_THRESHOLD_KEYS[name], value)
    return {"status": "success", "threshold": value}


# ---- 缺人自动喊话公共部分（修罗/五倍/种族比武设置，角色设置持久化） ----
# 喊话内容可在任务设置里修改保存；{种族} 占位符仅修罗使用——喊话时
# 替换为必须种族（顿号连接，无必须种族时替换为「随意来人」）。
# 清空内容保存恢复默认（开关才是喊话的总开关，避免误清空静默不喊）。
SHOUT_TEXT_MAX_LEN = 60
XIULUO_SHOUT_TEXT_DEFAULT = "百次修罗来人，效率队，来{种族}"
WUBEI_SHOUT_TEXT_DEFAULT = "百次五倍来人，效率队"
ZHONGZU_SHOUT_TEXT_DEFAULT = "种族比武来人，效率队"


def _norm_shout_text(raw, default):
    """喊话内容规范化：去首尾空白、超长截断、空内容恢复默认。"""
    text = str(raw if raw is not None else "").strip()
    if not text:
        text = default
    return text[:SHOUT_TEXT_MAX_LEN]


# ---- 修罗设置（最少人数 + 必须有的种族，仅队长时生效，角色设置持久化） ----
XIULUO_MIN_DEFAULT = 5
XIULUO_MIN_RANGE = (3, 5)  # 最少人数范围：最小 3，最大 5
XIULUO_RACES = ["男人", "女人", "男鬼", "女鬼", "男仙", "女仙", "男魔", "女魔", "男巫", "女巫",
                "男龙", "女龙"]
XIULUO_RACES_DEFAULT = ["男人", "男鬼"]
_XIULUO_MIN_KEY = "xiuluo_min_members"
_XIULUO_RACES_KEY = "xiuluo_required_races"


def load_xiuluo_settings():
    """读取修罗设置：最少人数（3~5，默认 5）与必须有的种族。

    种族语义（2026-10-14 确认）：首次无配置用默认（男人+男鬼）；
    用户显式清空 = 无种族要求（空列表，领取前种族检查与缺人喊话
    均按无要求处理）——不再强制回退默认。
    """
    min_members = int(role_settings.get(_XIULUO_MIN_KEY, XIULUO_MIN_DEFAULT))
    min_members = min(XIULUO_MIN_RANGE[1], max(XIULUO_MIN_RANGE[0], min_members))
    raw = role_settings.get(_XIULUO_RACES_KEY, None)
    if raw is None:
        races = list(XIULUO_RACES_DEFAULT)
    else:
        races = [r for r in str(raw).replace("，", ",").split(",") if r in XIULUO_RACES]
    return {"min_members": min_members,
            "races": races}


def save_xiuluo_min(value):
    """保存修罗最少人数（夹取 3~5），返回操作结果。"""
    value = min(XIULUO_MIN_RANGE[1], max(XIULUO_MIN_RANGE[0], int(value)))
    role_settings.set_value(_XIULUO_MIN_KEY, value)
    return {"status": "success", "min_members": value}


def save_xiuluo_races(races):
    """保存修罗必须有的种族（过滤非法项，按逗号串存储），返回操作结果。"""
    races = [r for r in (races or []) if r in XIULUO_RACES]
    role_settings.set_value(_XIULUO_RACES_KEY, ",".join(races))
    return {"status": "success", "races": races}


# ---- 五环设置：活力不足继续任务（UI 开关，角色设置持久化，默认开启；
#      开启时活力值不足对话框点「不差那点小钱，快给我任务吧。」花钱
#      领取继续任务；关闭时点「那可不行，我一会再来。」结束任务） ----
_WUHUAN_VITALITY_CONTINUE_KEY = "wuhuan_vitality_continue"


def load_wuhuan_vitality_continue():
    """读取五环活力不足继续任务开关（首次默认开启）。"""
    return bool(role_settings.get(_WUHUAN_VITALITY_CONTINUE_KEY, True))


def save_wuhuan_vitality_continue(on):
    """保存五环活力不足继续任务开关，返回操作结果。"""
    role_settings.set_value(_WUHUAN_VITALITY_CONTINUE_KEY, bool(on))
    return {"status": "success"}


# 五倍设置：是否自动抢盒子（默认关闭；仅非队长时生效，队长一定抢）
_WUBEI_HEZI_KEY = "wubei_auto_hezi"


# ---- 五倍检查队伍（UI 开关，角色设置持久化；默认开启，关闭时
#      跳过全部队伍检查直接领取——可能不是队长，被拒也不补人） ----
_WUBEI_CHECK_TEAM_KEY = "wubei_check_team"


def load_wubei_check_team():
    """读取五倍检查队伍开关（首次默认开启）。"""
    return bool(role_settings.get(_WUBEI_CHECK_TEAM_KEY, True))


def save_wubei_check_team(on):
    """保存五倍检查队伍开关，返回操作结果。"""
    role_settings.set_value(_WUBEI_CHECK_TEAM_KEY, bool(on))
    return {"status": "success"}


# ---- 五倍自动创建队伍（UI 开关，角色设置持久化；默认开启。开启时
#      任务启动若无队伍自动创建——点组队按钮 → 菜单「创建队伍」，
#      创建成功后走正常队伍检查与补人流程；已有队伍无操作。
#      实现见 team.ensure_team_created） ----
_WUBEI_AUTO_CREATE_TEAM_KEY = "wubei_auto_create_team"


def load_wubei_auto_create_team():
    """读取五倍自动创建队伍开关（首次默认开启）。"""
    return bool(role_settings.get(_WUBEI_AUTO_CREATE_TEAM_KEY, True))


def save_wubei_auto_create_team(on):
    """保存五倍自动创建队伍开关，返回操作结果。"""
    role_settings.set_value(_WUBEI_AUTO_CREATE_TEAM_KEY, bool(on))
    return {"status": "success"}


# ---- 仅战斗自动归队（UI 开关，角色设置持久化，默认开启；仅战斗
#      模式不判定身份——死亡落轮回司自动点「归队」，队长身份由
#      maybe_rejoin 自动跳过。实现见 main._battle_only_loop） ----
_BATTLE_ONLY_AUTO_REJOIN_KEY = "battle_only_auto_rejoin"


def load_battle_only_auto_rejoin():
    """读取仅战斗自动归队开关（首次默认开启）。"""
    return bool(role_settings.get(_BATTLE_ONLY_AUTO_REJOIN_KEY, True))


def save_battle_only_auto_rejoin(on):
    """保存仅战斗自动归队开关，返回操作结果。"""
    role_settings.set_value(_BATTLE_ONLY_AUTO_REJOIN_KEY, bool(on))
    return {"status": "success"}


# ---- 五倍自动归队（UI 开关，角色设置持久化；默认开启。仅非队长
#      生效：战斗死亡落轮回司后自动打开队伍面板点「归队」传送回队长，
#      实现见 team.maybe_rejoin（归队按钮模板待用户提供）） ----
_WUBEI_AUTO_REJOIN_KEY = "wubei_auto_rejoin"


def load_wubei_auto_rejoin():
    """读取五倍自动归队开关（首次默认开启）。"""
    return bool(role_settings.get(_WUBEI_AUTO_REJOIN_KEY, True))


def save_wubei_auto_rejoin(on):
    """保存五倍自动归队开关，返回操作结果。"""
    role_settings.set_value(_WUBEI_AUTO_REJOIN_KEY, bool(on))
    return {"status": "success"}


# ---- 五倍缺人自动喊话（UI 开关 + 喊话内容，角色设置持久化；默认开启。
#      喊话内容可在五倍设置里修改保存，默认「百次五倍来人，效率队」
#      （2026-11-12 用户要求内容可设置） ----
_WUBEI_SHOUT_SWITCH_KEY = "wubei_shout"
_WUBEI_SHOUT_TEXT_KEY = "wubei_shout_text"


def load_wubei_shout_switch():
    """读取五倍缺人自动喊话开关（首次默认开启）。"""
    return bool(role_settings.get(_WUBEI_SHOUT_SWITCH_KEY, True))


def save_wubei_shout_switch(on):
    """保存五倍缺人自动喊话开关，返回操作结果。"""
    role_settings.set_value(_WUBEI_SHOUT_SWITCH_KEY, bool(on))
    return {"status": "success"}


def load_wubei_shout_text():
    """读取五倍喊话内容（默认「百次五倍来人，效率队」）。"""
    return _norm_shout_text(role_settings.get(_WUBEI_SHOUT_TEXT_KEY, None),
                            WUBEI_SHOUT_TEXT_DEFAULT)


def save_wubei_shout_text(text):
    """保存五倍喊话内容（去空白/截断，空内容恢复默认），返回生效内容。"""
    value = _norm_shout_text(text, WUBEI_SHOUT_TEXT_DEFAULT)
    role_settings.set_value(_WUBEI_SHOUT_TEXT_KEY, value)
    return {"status": "success", "text": value}


def load_wubei_hezi():
    """读取五倍自动抢盒子开关（首次默认关闭）。"""
    return bool(role_settings.get(_WUBEI_HEZI_KEY, False))


def save_wubei_hezi(on):
    """保存五倍自动抢盒子开关，返回操作结果。"""
    role_settings.set_value(_WUBEI_HEZI_KEY, bool(on))
    return {"status": "success"}


# 修罗设置：是否自动抢盒子（福源广泽盒出现时自动点「我需要」，默认关闭）
_XIULUO_HEZI_KEY = "xiuluo_auto_hezi"


def load_xiuluo_hezi():
    """读取修罗自动抢盒子开关（首次默认关闭）。"""
    return bool(role_settings.get(_XIULUO_HEZI_KEY, False))


def save_xiuluo_hezi(on):
    """保存修罗自动抢盒子开关，返回操作结果。"""
    role_settings.set_value(_XIULUO_HEZI_KEY, bool(on))
    return {"status": "success"}


# ---- 高性能模式（UI 开关，通用设置，角色设置持久化，默认开启；
#      开启后各任务模块固定位置 UI（按钮/对话框选项）首次识别记录
#      坐标，后续直接按坐标点击 + 效果校验，失败回退识别路径——
#      位置漂移/窗口缩放时自动失效并自愈；旧修罗键自动迁移） ----
_HIGH_PERF_KEY = "high_perf"
_XIULUO_HIGH_PERF_KEY_OLD = "xiuluo_high_perf"


def load_high_perf():
    """读取高性能开关（首次默认开启；旧修罗高性能键自动迁移到通用键）。"""
    if role_settings.get(_HIGH_PERF_KEY) is None \
            and role_settings.get(_XIULUO_HIGH_PERF_KEY_OLD) is not None:
        role_settings.set_value(_HIGH_PERF_KEY, bool(role_settings.get(_XIULUO_HIGH_PERF_KEY_OLD)))
    return bool(role_settings.get(_HIGH_PERF_KEY, True))


def save_high_perf(on):
    """保存高性能开关，返回操作结果。"""
    role_settings.set_value(_HIGH_PERF_KEY, bool(on))
    return {"status": "success"}


# ---- 战斗结束自动补血量蓝量（UI 开关，通用设置，角色设置持久化，
#      默认开启；关闭后战斗结束不再检测血蓝条、不自动右键回满——
#      battle.watch_loop 战后分支门控） ----
_AUTO_REFILL_KEY = "auto_refill"


def load_auto_refill():
    """读取战斗结束自动补血量蓝量开关（首次默认开启）。"""
    return bool(role_settings.get(_AUTO_REFILL_KEY, True))


def save_auto_refill(on):
    """保存战斗结束自动补血量蓝量开关，返回操作结果。"""
    role_settings.set_value(_AUTO_REFILL_KEY, bool(on))
    return {"status": "success"}


# ---- 启动清屏（UI 开关，通用设置，角色设置持久化，默认开启；
#      每次任务启动时执行一次：Alt+6 屏蔽非队伍玩家 + Alt+5 屏蔽摊位，
#      实现见 src/device/clear_screen.py） ----
# 注意：这是 role_settings 里的清屏开关；app_utils 的
# _CLEAR_SCREEN_STATE_KEY 是 state 文件里的清屏幂等状态，两者不是同一个键。
_CLEAR_SCREEN_KEY = "clear_screen"


def load_clear_screen():
    """读取启动清屏开关（首次默认开启）。"""
    return bool(role_settings.get(_CLEAR_SCREEN_KEY, True))


def save_clear_screen(on):
    """保存启动清屏开关，返回操作结果。"""
    role_settings.set_value(_CLEAR_SCREEN_KEY, bool(on))
    return {"status": "success"}


# ---- 静默操作（UI 开关，通用设置，角色设置持久化，默认开启；
#      开启后全部点击走窗口消息通道、鼠标移动变空操作，游戏窗口
#      可不前台——实现见 app_utils._SILENT_CLICK 与 silent_click.py；
#      关闭时保持原物理鼠标方案（各键盘/激活类操作恢复原行为）） ----
_SILENT_MODE_KEY = "silent_mode"


def load_silent_mode():
    """读取静默操作开关（首次默认开启）。"""
    return bool(role_settings.get(_SILENT_MODE_KEY, True))


def save_silent_mode(on):
    """保存静默操作开关并让主进程立即生效（验证/诊断流程），
    返回操作结果。任务子进程在启动时按配置置位。"""
    role_settings.set_value(_SILENT_MODE_KEY, bool(on))
    app_utils.set_silent_mode(bool(on))
    return {"status": "success"}


# ---- 修罗检查队伍（UI 开关，角色设置持久化；关闭时领取前跳过全部
#      队伍检查，默认开启（关闭时每轮回程后开面板检查 5~7s 的耗时
#      也一并跳过——用户可按需关闭）） ----
_XIULUO_CHECK_TEAM_KEY = "xiuluo_check_team"


def load_xiuluo_check_team():
    """读取修罗检查队伍开关（首次默认开启）。"""
    return bool(role_settings.get(_XIULUO_CHECK_TEAM_KEY, True))


def save_xiuluo_check_team(on):
    """保存修罗检查队伍开关，返回操作结果。"""
    role_settings.set_value(_XIULUO_CHECK_TEAM_KEY, bool(on))
    return {"status": "success"}


# ---- 修罗自动创建队伍（UI 开关，角色设置持久化；默认开启。开启时
#      任务启动若无队伍自动创建——点组队按钮 → 菜单「创建队伍」，
#      创建成功后走正常队伍检查与补人流程；已有队伍无操作。
#      实现见 team.ensure_team_created） ----
_XIULUO_AUTO_CREATE_TEAM_KEY = "xiuluo_auto_create_team"


def load_xiuluo_auto_create_team():
    """读取修罗自动创建队伍开关（首次默认开启）。"""
    return bool(role_settings.get(_XIULUO_AUTO_CREATE_TEAM_KEY, True))


def save_xiuluo_auto_create_team(on):
    """保存修罗自动创建队伍开关，返回操作结果。"""
    role_settings.set_value(_XIULUO_AUTO_CREATE_TEAM_KEY, bool(on))
    return {"status": "success"}


# ---- 修罗自动归队（UI 开关，角色设置持久化；默认开启。仅非队长
#      生效：战斗死亡落轮回司后自动打开队伍面板点「归队」传送回队长，
#      实现见 team.maybe_rejoin；与五倍同款） ----
_XIULUO_AUTO_REJOIN_KEY = "xiuluo_auto_rejoin"


def load_xiuluo_auto_rejoin():
    """读取修罗自动归队开关（首次默认开启）。"""
    return bool(role_settings.get(_XIULUO_AUTO_REJOIN_KEY, True))


def save_xiuluo_auto_rejoin(on):
    """保存修罗自动归队开关，返回操作结果。"""
    role_settings.set_value(_XIULUO_AUTO_REJOIN_KEY, bool(on))
    return {"status": "success"}


# ---- 修罗自动巫医（UI 开关+间隔，角色设置持久化；默认开启、每 5 轮
#      一次——每 N 轮领取后无条件前往巫医 NPC 医治，血蓝正常也照常） ----
# ---- 修罗缺人自动喊话（UI 开关 + 喊话内容，角色设置持久化；默认开启。
#      内容支持 {种族} 占位符：喊话时替换为必须种族，见
#      xiuluo._build_shout_text） ----
_XIULUO_SHOUT_SWITCH_KEY = "xiuluo_shout"
_XIULUO_SHOUT_TEXT_KEY = "xiuluo_shout_text"


def load_xiuluo_shout_switch():
    """读取修罗缺人自动喊话开关（首次默认开启）。"""
    return bool(role_settings.get(_XIULUO_SHOUT_SWITCH_KEY, True))


def save_xiuluo_shout_switch(on):
    """保存修罗缺人自动喊话开关，返回操作结果。"""
    role_settings.set_value(_XIULUO_SHOUT_SWITCH_KEY, bool(on))
    return {"status": "success"}


def load_xiuluo_shout_text():
    """读取修罗喊话内容（默认「百次修罗来人，效率队，来{种族}」）。"""
    return _norm_shout_text(role_settings.get(_XIULUO_SHOUT_TEXT_KEY, None),
                            XIULUO_SHOUT_TEXT_DEFAULT)


def save_xiuluo_shout_text(text):
    """保存修罗喊话内容（去空白/截断，空内容恢复默认），返回生效内容。"""
    value = _norm_shout_text(text, XIULUO_SHOUT_TEXT_DEFAULT)
    role_settings.set_value(_XIULUO_SHOUT_TEXT_KEY, value)
    return {"status": "success", "text": value}


_XIULUO_AUTO_WUYI_KEY = "xiuluo_auto_wuyi"
_XIULUO_WUYI_INTERVAL_KEY = "xiuluo_wuyi_interval"
_WUYI_INTERVAL_MIN = 1
_WUYI_INTERVAL_MAX = 90


def load_xiuluo_auto_wuyi():
    """读取修罗自动巫医开关（首次默认开启）。"""
    return bool(role_settings.get(_XIULUO_AUTO_WUYI_KEY, True))


def save_xiuluo_auto_wuyi(on):
    """保存修罗自动巫医开关，返回操作结果。"""
    role_settings.set_value(_XIULUO_AUTO_WUYI_KEY, bool(on))
    return {"status": "success"}


def load_xiuluo_wuyi_interval():
    """读取修罗自动巫医间隔（轮，首次默认 5）。"""
    try:
        value = int(role_settings.get(_XIULUO_WUYI_INTERVAL_KEY, 5))
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
    role_settings.set_value(_XIULUO_WUYI_INTERVAL_KEY, value)
    return {"status": "success", "interval": value}


_WUBEI_AUTO_WUYI_KEY = "wubei_auto_wuyi"
_WUBEI_WUYI_INTERVAL_KEY = "wubei_wuyi_interval"


def load_wubei_auto_wuyi():
    """读取五倍自动巫医开关（首次默认开启，与修罗一致）。"""
    return bool(role_settings.get(_WUBEI_AUTO_WUYI_KEY, True))


def save_wubei_auto_wuyi(on):
    """保存五倍自动巫医开关，返回操作结果。"""
    role_settings.set_value(_WUBEI_AUTO_WUYI_KEY, bool(on))
    return {"status": "success"}


def load_wubei_wuyi_interval():
    """读取五倍自动巫医间隔（轮，首次默认 5，与修罗一致）。"""
    try:
        value = int(role_settings.get(_WUBEI_WUYI_INTERVAL_KEY, 5))
    except (TypeError, ValueError):
        value = 5
    return max(_WUYI_INTERVAL_MIN, min(_WUYI_INTERVAL_MAX, value))


def save_wubei_wuyi_interval(value):
    """保存五倍自动巫医间隔（1~90 轮），返回操作结果。"""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return {"status": "error", "msg": "INVALID_INTERVAL"}
    if not (_WUYI_INTERVAL_MIN <= value <= _WUYI_INTERVAL_MAX):
        return {"status": "error", "msg": "OUT_OF_RANGE"}
    role_settings.set_value(_WUBEI_WUYI_INTERVAL_KEY, value)
    return {"status": "success", "interval": value}


# ---- 自动修理装备（UI 开关+间隔场次，角色设置持久化；默认开启、每 30
#      场战斗一次——领取任务后按今日累计场次判断，前往洛阳城修理。
#      实现在 src/device/repair.py，战斗线程预期窗口处理对话框） ----
_XIULUO_AUTO_REPAIR_KEY = "xiuluo_auto_repair"
_XIULUO_REPAIR_INTERVAL_KEY = "xiuluo_repair_interval"
_WUBEI_AUTO_REPAIR_KEY = "wubei_auto_repair"
_WUBEI_REPAIR_INTERVAL_KEY = "wubei_repair_interval"
_REPAIR_INTERVAL_MIN = 1
_REPAIR_INTERVAL_MAX = 999
_REPAIR_INTERVAL_DEFAULT = 30


def load_xiuluo_auto_repair():
    """读取修罗自动修理装备开关（首次默认开启）。"""
    return bool(role_settings.get(_XIULUO_AUTO_REPAIR_KEY, True))


def save_xiuluo_auto_repair(on):
    """保存修罗自动修理装备开关，返回操作结果。"""
    role_settings.set_value(_XIULUO_AUTO_REPAIR_KEY, bool(on))
    return {"status": "success"}


def load_xiuluo_repair_interval():
    """读取修罗自动修理间隔（场战斗，首次默认 30）。"""
    try:
        value = int(role_settings.get(_XIULUO_REPAIR_INTERVAL_KEY,
                                      _REPAIR_INTERVAL_DEFAULT))
    except (TypeError, ValueError):
        value = _REPAIR_INTERVAL_DEFAULT
    return max(_REPAIR_INTERVAL_MIN, min(_REPAIR_INTERVAL_MAX, value))


def save_xiuluo_repair_interval(value):
    """保存修罗自动修理间隔（1~999 场），返回操作结果。"""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return {"status": "error", "msg": "INVALID_INTERVAL"}
    if not (_REPAIR_INTERVAL_MIN <= value <= _REPAIR_INTERVAL_MAX):
        return {"status": "error", "msg": "OUT_OF_RANGE"}
    role_settings.set_value(_XIULUO_REPAIR_INTERVAL_KEY, value)
    return {"status": "success", "interval": value}


def load_wubei_auto_repair():
    """读取五倍自动修理装备开关（首次默认开启，与修罗一致）。"""
    return bool(role_settings.get(_WUBEI_AUTO_REPAIR_KEY, True))


def save_wubei_auto_repair(on):
    """保存五倍自动修理装备开关，返回操作结果。"""
    role_settings.set_value(_WUBEI_AUTO_REPAIR_KEY, bool(on))
    return {"status": "success"}


def load_wubei_repair_interval():
    """读取五倍自动修理间隔（场战斗，首次默认 30，与修罗一致）。"""
    try:
        value = int(role_settings.get(_WUBEI_REPAIR_INTERVAL_KEY,
                                      _REPAIR_INTERVAL_DEFAULT))
    except (TypeError, ValueError):
        value = _REPAIR_INTERVAL_DEFAULT
    return max(_REPAIR_INTERVAL_MIN, min(_REPAIR_INTERVAL_MAX, value))


def save_wubei_repair_interval(value):
    """保存五倍自动修理间隔（1~999 场），返回操作结果。"""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return {"status": "error", "msg": "INVALID_INTERVAL"}
    if not (_REPAIR_INTERVAL_MIN <= value <= _REPAIR_INTERVAL_MAX):
        return {"status": "error", "msg": "OUT_OF_RANGE"}
    role_settings.set_value(_WUBEI_REPAIR_INTERVAL_KEY, value)
    return {"status": "success", "interval": value}


# ---- 五倍是否领取奎木狼任务（UI 开关，角色设置持久化，默认开启；
#      关闭时领取到奎木狼任务自动重新领取替换，逻辑同锁妖网） ----
_WUBEI_KUIMULANG_KEY = "wubei_kuimulang"


def load_wubei_kuimulang_switch():
    """读取五倍领取奎木狼任务开关（首次默认开启）。"""
    return bool(role_settings.get(_WUBEI_KUIMULANG_KEY, True))


def save_wubei_kuimulang_switch(on):
    """保存五倍领取奎木狼任务开关，返回操作结果。"""
    role_settings.set_value(_WUBEI_KUIMULANG_KEY, bool(on))
    return {"status": "success"}


# ---- 组号专用（UI 开关，修罗/五倍/种族比武各一，角色设置持久化，默认开启；
#      一个窗口内多个登录 Tab 视为一组号：队长带队，队员 Tab 自动挂
#      战斗/巫医/补血蓝，实现见 src/device/tab_group.py。单号（已登录 Tab<2）
#      不激活，行为与关闭时完全一致） ----
_XIULUO_TAB_GROUP_KEY = "xiuluo_tab_group"


def load_xiuluo_tab_group():
    """读取修罗组号专用开关（首次默认开启）。"""
    return bool(role_settings.get(_XIULUO_TAB_GROUP_KEY, True))


def save_xiuluo_tab_group(on):
    """保存修罗组号专用开关，返回操作结果。"""
    role_settings.set_value(_XIULUO_TAB_GROUP_KEY, bool(on))
    return {"status": "success"}


_WUBEI_TAB_GROUP_KEY = "wubei_tab_group"


def load_wubei_tab_group():
    """读取五倍组号专用开关（首次默认开启）。"""
    return bool(role_settings.get(_WUBEI_TAB_GROUP_KEY, True))


def save_wubei_tab_group(on):
    """保存五倍组号专用开关，返回操作结果。"""
    role_settings.set_value(_WUBEI_TAB_GROUP_KEY, bool(on))
    return {"status": "success"}


# ---- 种族比武设置（UI 开关，角色设置持久化；队伍任务固定 5 人。
#      检查队伍/自动创建队伍/自动归队/缺人喊话/组号专用与修罗同款；
#      无最少人数设置、无必须种族（用户 2026-11-12 确认）；自动巫医
#      链路暂未实现（设置保留）、自动修理复用 repair.py） ----
_ZHONGZU_CHECK_TEAM_KEY = "zhongzu_check_team"


def load_zhongzu_check_team():
    """读取种族比武检查队伍开关（首次默认开启）。"""
    return bool(role_settings.get(_ZHONGZU_CHECK_TEAM_KEY, True))


def save_zhongzu_check_team(on):
    """保存种族比武检查队伍开关，返回操作结果。"""
    role_settings.set_value(_ZHONGZU_CHECK_TEAM_KEY, bool(on))
    return {"status": "success"}


_ZHONGZU_AUTO_CREATE_TEAM_KEY = "zhongzu_auto_create_team"


def load_zhongzu_auto_create_team():
    """读取种族比武自动创建队伍开关（首次默认开启）。"""
    return bool(role_settings.get(_ZHONGZU_AUTO_CREATE_TEAM_KEY, True))


def save_zhongzu_auto_create_team(on):
    """保存种族比武自动创建队伍开关，返回操作结果。"""
    role_settings.set_value(_ZHONGZU_AUTO_CREATE_TEAM_KEY, bool(on))
    return {"status": "success"}


_ZHONGZU_AUTO_REJOIN_KEY = "zhongzu_auto_rejoin"


def load_zhongzu_auto_rejoin():
    """读取种族比武自动归队开关（首次默认开启）。"""
    return bool(role_settings.get(_ZHONGZU_AUTO_REJOIN_KEY, True))


def save_zhongzu_auto_rejoin(on):
    """保存种族比武自动归队开关，返回操作结果。"""
    role_settings.set_value(_ZHONGZU_AUTO_REJOIN_KEY, bool(on))
    return {"status": "success"}


# 种族比武缺人自动喊话（UI 开关 + 喊话内容，角色设置持久化；默认开启，
# 2026-11-12 用户要求与修罗/五倍一致——缺人等待时周期性喊话招募，
# 仅队长生效）
_ZHONGZU_SHOUT_SWITCH_KEY = "zhongzu_shout"
_ZHONGZU_SHOUT_TEXT_KEY = "zhongzu_shout_text"


def load_zhongzu_shout_switch():
    """读取种族比武缺人自动喊话开关（首次默认开启）。"""
    return bool(role_settings.get(_ZHONGZU_SHOUT_SWITCH_KEY, True))


def save_zhongzu_shout_switch(on):
    """保存种族比武缺人自动喊话开关，返回操作结果。"""
    role_settings.set_value(_ZHONGZU_SHOUT_SWITCH_KEY, bool(on))
    return {"status": "success"}


def load_zhongzu_shout_text():
    """读取种族比武喊话内容（默认「种族比武来人，效率队」）。"""
    return _norm_shout_text(role_settings.get(_ZHONGZU_SHOUT_TEXT_KEY, None),
                            ZHONGZU_SHOUT_TEXT_DEFAULT)


def save_zhongzu_shout_text(text):
    """保存种族比武喊话内容（去空白/截断，空恢复默认），返回生效内容。"""
    value = _norm_shout_text(text, ZHONGZU_SHOUT_TEXT_DEFAULT)
    role_settings.set_value(_ZHONGZU_SHOUT_TEXT_KEY, value)
    return {"status": "success", "text": value}


_ZHONGZU_AUTO_WUYI_KEY = "zhongzu_auto_wuyi"
_ZHONGZU_WUYI_INTERVAL_KEY = "zhongzu_wuyi_interval"


def load_zhongzu_auto_wuyi():
    """读取种族比武自动巫医开关（首次默认开启；链路暂未实现）。"""
    return bool(role_settings.get(_ZHONGZU_AUTO_WUYI_KEY, True))


def save_zhongzu_auto_wuyi(on):
    """保存种族比武自动巫医开关，返回操作结果。"""
    role_settings.set_value(_ZHONGZU_AUTO_WUYI_KEY, bool(on))
    return {"status": "success"}


def load_zhongzu_wuyi_interval():
    """读取种族比武自动巫医间隔（轮，首次默认 5）。"""
    try:
        value = int(role_settings.get(_ZHONGZU_WUYI_INTERVAL_KEY, 5))
    except (TypeError, ValueError):
        value = 5
    return max(_WUYI_INTERVAL_MIN, min(_WUYI_INTERVAL_MAX, value))


def save_zhongzu_wuyi_interval(value):
    """保存种族比武自动巫医间隔（1~90 轮），返回操作结果。"""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return {"status": "error", "msg": "INVALID_INTERVAL"}
    if not (_WUYI_INTERVAL_MIN <= value <= _WUYI_INTERVAL_MAX):
        return {"status": "error", "msg": "OUT_OF_RANGE"}
    role_settings.set_value(_ZHONGZU_WUYI_INTERVAL_KEY, value)
    return {"status": "success", "interval": value}


_ZHONGZU_AUTO_REPAIR_KEY = "zhongzu_auto_repair"
_ZHONGZU_REPAIR_INTERVAL_KEY = "zhongzu_repair_interval"


def load_zhongzu_auto_repair():
    """读取种族比武自动修理装备开关（首次默认开启）。"""
    return bool(role_settings.get(_ZHONGZU_AUTO_REPAIR_KEY, True))


def save_zhongzu_auto_repair(on):
    """保存种族比武自动修理装备开关，返回操作结果。"""
    role_settings.set_value(_ZHONGZU_AUTO_REPAIR_KEY, bool(on))
    return {"status": "success"}


def load_zhongzu_repair_interval():
    """读取种族比武自动修理间隔（场战斗，首次默认 30）。"""
    try:
        value = int(role_settings.get(_ZHONGZU_REPAIR_INTERVAL_KEY, 30))
    except (TypeError, ValueError):
        value = 30
    return max(_REPAIR_INTERVAL_MIN, min(_REPAIR_INTERVAL_MAX, value))


def save_zhongzu_repair_interval(value):
    """保存种族比武自动修理间隔（1~999 场），返回操作结果。"""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return {"status": "error", "msg": "INVALID_INTERVAL"}
    if not (_REPAIR_INTERVAL_MIN <= value <= _REPAIR_INTERVAL_MAX):
        return {"status": "error", "msg": "OUT_OF_RANGE"}
    role_settings.set_value(_ZHONGZU_REPAIR_INTERVAL_KEY, value)
    return {"status": "success", "interval": value}


_ZHONGZU_TAB_GROUP_KEY = "zhongzu_tab_group"


def load_zhongzu_tab_group():
    """读取种族比武组号专用开关（首次默认开启）。"""
    return bool(role_settings.get(_ZHONGZU_TAB_GROUP_KEY, True))


def save_zhongzu_tab_group(on):
    """保存种族比武组号专用开关，返回操作结果。"""
    role_settings.set_value(_ZHONGZU_TAB_GROUP_KEY, bool(on))
    return {"status": "success"}


# ---- 战斗设置（人物/召唤兽默认战斗动作，UI 单选项，角色设置持久化） ----
# 人物：技能面板 3 行 × 5 列共 15 格 + 攻击/防御/捕捉/逃跑/无
CHAR_BATTLE_ACTIONS = ([f"技能{r}-{c}" for r in range(1, 4) for c in range(1, 6)]
                       + ["攻击", "防御", "捕捉", "逃跑", "无"])
# 召唤兽：技能 6 格 + 攻击/防御/逃跑/无（召唤兽不能捕捉）
BEAST_BATTLE_ACTIONS = [f"技能{i}" for i in range(1, 7)] + ["攻击", "防御", "逃跑", "无"]
_BATTLE_KEYS = {"char": "battle_char_action", "beast": "battle_beast_action"}
_DEFAULT_BATTLE = {"char": "技能3-5", "beast": "攻击"}


def load_battle_actions():
    """读取人物与召唤兽的默认战斗动作（首次默认攻击）。"""
    result = {}
    for name, key in _BATTLE_KEYS.items():
        options = CHAR_BATTLE_ACTIONS if name == "char" else BEAST_BATTLE_ACTIONS
        value = role_settings.get(key, _DEFAULT_BATTLE[name])
        result[name] = value if value in options else _DEFAULT_BATTLE[name]
    return result


def save_battle_action(target, value):
    """保存人物/召唤兽的默认战斗动作，返回操作结果。"""
    if target not in _BATTLE_KEYS:
        return {"status": "error", "msg": "INVALID_TARGET"}
    options = CHAR_BATTLE_ACTIONS if target == "char" else BEAST_BATTLE_ACTIONS
    if value not in options:
        return {"status": "error", "msg": "INVALID_VALUE"}
    role_settings.set_value(_BATTLE_KEYS[target], value)
    return {"status": "success"}


# ---- 组号成员战斗动作（按账号 ID 持久化，组号设置面板编辑） ----
# 存当前队长角色 data/<角色ID>/state.json：{账号ID: {"char":..., "beast":...}}
# 账号 ID 来自 Tab 标题「（ID：账号）」。只增改不删——队员换队长/队伍
# 变动后旧账号设置保留（用户 2026-11-07 需求）；2026-11-11 起按队长
# 角色隔离，切换队长账号后各自一套。
_GROUP_ACTIONS_KEY = "group_member_actions"
# 组号识别状态（任务进程启动检查写入，面板「组号」侧边栏入口显隐信号）：
# 面板昵称可读且确认到 ≥1 名在队伍中的队员 Tab = 是组号 → True；可读且
# 0 名 = 非组号（Tab ≥2 但队伍里没有这些账号的角色）→ False；读不到不写
# （保留上次状态，宽松）。主进程读此键 + 组号开关 + 当前窗口已登录
# Tab ≥2 决定入口显隐（用户 2026-11-07 需求：识别到是组号才展示）。
_GROUP_DETECTED_KEY = "group_detected"
# 最近一次队伍检查快照（任务进程启动检查 / 组号页「检查当前队伍」写入）：
# 组号页打开时自动展示上次确认的队长与在队伍中的队员（此前必须手动点
# 检查才出列表，2026-11-11 用户需求）。只存账号 ID/角色名与时间戳，
# 战斗动作不复制（展示时按账号 ID 读 group_member_actions 实时合并）。
_GROUP_LAST_SCAN_KEY = "group_last_scan"


def load_group_actions():
    """读取全部组号成员战斗动作 {账号ID: {"char":..., "beast":...}}。

    条目缺人物/召唤兽动作或值非法时丢弃（旧数据/手工改坏文件自愈）。
    单号模式不使用本数据；任务进程每次直读当前角色的 state.json。
    """
    data = app_utils.role_state_get(_GROUP_ACTIONS_KEY, None)
    if data is None and not user_data.is_migrated():
        data = KeyValue.get(_GROUP_ACTIONS_KEY, {}) or {}  # 旧版未迁移/单测兜底
    data = data if isinstance(data, dict) else {}
    result = {}
    for aid, entry in data.items():
        if not isinstance(entry, dict):
            continue
        char = entry.get("char")
        beast = entry.get("beast")
        if char not in CHAR_BATTLE_ACTIONS or beast not in BEAST_BATTLE_ACTIONS:
            continue
        result[str(aid)] = {"char": char, "beast": beast}
    return result


def save_group_actions(mapping):
    """保存组号成员战斗动作（合并写入，绝不删已有账号条目）。

    mapping：{账号ID: {"char":..., "beast":...}}（组号设置页当前展示
    的成员整表）。任一值非法即拒绝整单（UI 下拉只出合法值，此为双
    保险）。跨进程合并写（app_utils.kv_save_merged，KeyValue 整文件
    覆写时避免覆盖任务进程刚写入的其他键）。
    """
    if not isinstance(mapping, dict):
        return {"status": "error", "msg": "INVALID_MAPPING"}
    incoming = {}
    for aid, entry in mapping.items():
        if not isinstance(entry, dict):
            return {"status": "error", "msg": "INVALID_ENTRY"}
        char = entry.get("char")
        beast = entry.get("beast")
        if char not in CHAR_BATTLE_ACTIONS or beast not in BEAST_BATTLE_ACTIONS:
            return {"status": "error", "msg": "INVALID_VALUE"}
        incoming[str(aid)] = {"char": char, "beast": beast}
    cur = load_group_actions()
    cur.update(incoming)
    if not app_utils.role_state_set(_GROUP_ACTIONS_KEY, cur):
        return {"status": "error", "msg": "SAVE_FAILED"}
    return {"status": "success"}


def save_group_last_scan(data):
    """保存最近一次队伍检查快照（组号页打开自动展示的数据源）。

    data 为 tab_group.scan_group_members 的返回数据。只存队长与「确认
    在队伍中」的队员（账号 ID/角色名）——不在队伍的账号无论是否设置过
    动作都不进快照（用户 2026-11-11 要求）；战斗动作不复制，展示时按
    账号 ID 实时读 group_member_actions。panel_read=False 不覆盖（面板
    没读到不代表队伍变了，保留上次可用快照）。
    任务进程（启动检查）与主进程（手动检查）共用；跨进程合并写。
    返回快照时间戳（秒）或 None（未保存）。
    """
    if not isinstance(data, dict) or not data.get("panel_read"):
        return None
    leader = data.get("leader")
    leader = leader if isinstance(leader, dict) else {}
    snapshot = {
        "ts": time.time(),
        "leader": {"id": str(leader.get("id") or ""),
                   "name": leader.get("name") or ""},
        "members": [{"id": str(m.get("id") or ""), "name": m.get("name") or ""}
                    for m in data.get("members") or []
                    if isinstance(m, dict) and m.get("in_team") and m.get("id")],
    }
    if not app_utils.role_state_set(_GROUP_LAST_SCAN_KEY, snapshot):
        app_utils.log("[组号] 保存队伍检查快照失败", "WARN")
        return None
    return snapshot["ts"]


def load_group_last_scan():
    """读取最近一次队伍检查快照（当前角色 state.json，跨进程可见）。"""
    snap = app_utils.role_state_get(_GROUP_LAST_SCAN_KEY, None)
    if snap is None and not user_data.is_migrated():
        snap = app_utils.kv_read_fresh(_GROUP_LAST_SCAN_KEY, None)  # 旧版兜底
    return snap if isinstance(snap, dict) else None


def build_group_last_scan_payload():
    """组号页「打开自动展示上次检查」载荷（与手动检查同结构）。

    返回 {"status","ok","info","data"}；data 与 onGroupCheckResult 的
    data 同形（leader/members/panel_read/options + snapshot_ts），成员
    只含快照确认在队伍的队员，动作按账号 ID 实时合并。无快照 ok=False。
    """
    snap = load_group_last_scan()
    leader_snap = snap.get("leader") if isinstance(snap, dict) else None
    if not isinstance(leader_snap, dict) or not leader_snap.get("id"):
        return {"status": "success", "ok": False,
                "info": "暂无队伍检查记录，请点「检查当前队伍」", "data": None}
    saved = load_group_actions()
    lid = str(leader_snap.get("id") or "")
    lentry = saved.get(lid) or {}
    leader = {"id": lid, "name": leader_snap.get("name") or "",
              "char": lentry.get("char"), "beast": lentry.get("beast")}
    members = []
    for m in snap.get("members") or []:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "")
        if not mid:
            continue
        entry = saved.get(mid) or {}
        members.append({"id": mid, "name": m.get("name") or "",
                        "in_team": True,
                        "char": entry.get("char"), "beast": entry.get("beast")})
    ts = snap.get("ts")
    try:
        when = time.strftime("%m-%d %H:%M", time.localtime(float(ts))) \
            if ts else "未知时间"
    except (TypeError, ValueError, OSError):
        when = "未知时间"
    data = {"leader": leader, "members": members, "panel_read": True,
            "options": {"char": CHAR_BATTLE_ACTIONS,
                        "beast": BEAST_BATTLE_ACTIONS},
            "snapshot_ts": ts}
    info = (f"上次检查（{when}）：队长 {leader['name'] or leader['id']}，"
            f"队伍内队员 {len(members)} 名；队伍/账号变化时请重新检查")
    return {"status": "success", "ok": True, "info": info, "data": data}


# ---- 自动使用法门（UI 开关，角色设置持久化，默认关闭；开启后每场
#      战斗第 1 回合操作条出现时先点一次法门坐标再继续配置的战斗
#      操作——坐标标定见 battle.FAMEN_CLICK_POS） ----
_AUTO_FAMEN_KEY = "auto_famen"


def load_auto_famen():
    """读取自动使用法门开关（首次默认关闭）。"""
    return bool(role_settings.get(_AUTO_FAMEN_KEY, False))


def save_auto_famen(on):
    """保存自动使用法门开关，返回操作结果。"""
    role_settings.set_value(_AUTO_FAMEN_KEY, bool(on))
    return {"status": "success"}

