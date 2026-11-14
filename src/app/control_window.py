"""自定义网页控制窗口：任务启动/停止、页面设置与配置、独立吸附日志窗口。

ControlWindow 基于 WebWindow（pywebview），页面为 res/ui/control_window.html，
是应用主窗口；日志直接展示在面板内「运行日志」抽屉（2026-11 起不再使用
独立吸附日志窗）。先 prepare() 创建窗口，再 show() 进入阻塞式 GUI 事件
循环（webview.start 由主线程执行，配合 Runtime.wait 驻留线程防程序自行退出）。

页面结构（2026-11 重设计，见 docs/ui-redesign.md）：
- 顶栏：品牌 + 绑定角色 + 配置方案下拉（选中即切换）+ 主题切换 +
  启动/停止主按钮（F11 老板键提示）
- 侧边导航：运行看板（状态印章/任务队列/日志抽屉）/ 任务（选择+次数+
  各任务专属设置）/ 设置（通用+血蓝阈值+战斗动作）/ 系统（环境/授权/
  配置方案）/ 诊断（验证工具，调试信息开启时显示）
- 看板运行状态：任务子进程写悬浮层状态文件（结构化 status 字段），
  本窗口 get_runtime_status 直读，与调试信息开关无关

功能约定：
- 启动/停止：桥接调用 TaskManager 启停任务子进程（升级强杀保证线程全清）
- 关闭（X）：取消关闭并隐藏到托盘（_on_closing），任务继续运行；
  真正退出须先 close_window() 放行（_real_close），否则窗口永不销毁
- 调试信息开关：控制游戏窗口悬浮层（任务侧读取）与「诊断」导航显隐；
  「显示与调试」卡片发行版默认隐藏（诊断入口随之不展示），在「系统页-
  关于」连点版本号 6 次解锁；开关为全局设置（config.json），不进配置方案；
  日志统一展示在面板内「运行日志」抽屉（独立吸附日志窗已于 2026-11 移除）

注意：页面不要自行声明 callPython 函数，使用框架注入的桥接
（window.callPython → call_internal 通道），否则注入会被跳过。
"""
import json
import os
import threading
import time
import webbrowser
import webview
from pathlib import Path
from ascript.windows.hardware.keyboard import Keyboard
from ascript.windows.system import R
from ascript.windows.system.device import Device
from ascript.windows.ui import WebWindow
from ..device import game  # 游戏窗口查找（修罗回程验证用；game 不反向依赖本模块，可顶层导入）
from ..core import app_utils  # KeyValue 合并读写（kv_save_merged/kv_remove_merged）
from ..core import schemes  # 配置方案（多套设置快照切换 + 导入导出）
from ..core import user_data  # 角色数据目录（data/<角色ID>/）
from ..core import role_settings  # 当前角色生效设置（data/<角色ID>/settings.json）
from . import hotkey  # 老板键（启动/停止按钮文案展示热键名）
from . import updater  # 软件自动更新（清单/下载/覆盖升级）
from . import update_config  # 更新渠道与节流常量
from .. import __version__  # 版本号唯一来源（src/__init__.py，面板关于/侧边栏展示）


from ..device.binding import (_short_label_from_title, bound_label_from_binding,
                              bound_short_label)
from ..device.progress import (_fresh_progress_modes, _legacy_progress_char_key,
                              task_progress_summary)
from ..core.settings import (
    BATTLE_ONLY_MODE, BEAST_BATTLE_ACTIONS, CHAR_BATTLE_ACTIONS, DEFAULT_THRESHOLD,
    ENV_ADAPT_RES, ENV_THEME, SHOUT_TEXT_MAX_LEN, TASK_COUNT_DEFAULTS,
    TASK_COUNT_MAX, TASK_MODES, UI_THEMES, WUBEI_SHOUT_TEXT_DEFAULT,
    XIULUO_MIN_DEFAULT, XIULUO_MIN_RANGE, XIULUO_RACES, XIULUO_RACES_DEFAULT,
    XIULUO_SHOUT_TEXT_DEFAULT, ZHONGZU_SHOUT_TEXT_DEFAULT, _AUTO_FAMEN_KEY, _AUTO_REFILL_KEY,
    _BATTLE_KEYS, _BATTLE_ONLY_AUTO_REJOIN_KEY, _CLEAR_SCREEN_KEY, _COUNT_KEYS,
    _DEBUG_OVERLAY_KEY, _DEBUG_UI_KEY, _DEFAULT_BATTLE, _DEFAULT_MODES,
    _GROUP_ACTIONS_KEY, _GROUP_DETECTED_KEY, _GROUP_LAST_SCAN_KEY, _HIGH_PERF_KEY,
    _MODES_KEY, _MODE_KEY_OLD, _REPAIR_INTERVAL_DEFAULT, _REPAIR_INTERVAL_MAX,
    _REPAIR_INTERVAL_MIN, _SILENT_MODE_KEY, _TASK_PROGRESS_KEY, _THRESHOLD_KEYS,
    _UI_THEME_KEY, _UPDATE_AUTO_CHECK_KEY, _UPDATE_FEED_KEY, _UPDATE_LAST_CHECK_KEY,
    _UPDATE_SKIPPED_KEY, _USE_SHEYAOXIANG_KEY, _WUBEI_AUTO_CREATE_TEAM_KEY, _WUBEI_AUTO_REJOIN_KEY,
    _WUBEI_AUTO_REPAIR_KEY, _WUBEI_AUTO_WUYI_KEY, _WUBEI_CHECK_TEAM_KEY, _WUBEI_HEZI_KEY,
    _WUBEI_KUIMULANG_KEY, _WUBEI_REPAIR_INTERVAL_KEY, _WUBEI_SHOUT_SWITCH_KEY, _WUBEI_SHOUT_TEXT_KEY,
    _WUBEI_TAB_GROUP_KEY, _WUBEI_WUYI_INTERVAL_KEY, _WUHUAN_VITALITY_CONTINUE_KEY, _WUYI_INTERVAL_MAX,
    _WUYI_INTERVAL_MIN, _XIULUO_AUTO_CREATE_TEAM_KEY, _XIULUO_AUTO_REJOIN_KEY,
    _XIULUO_AUTO_KICK_AWAY_KEY, _XIULUO_KICK_AWAY_MINUTES_KEY, _XIULUO_AUTO_REPAIR_KEY,
    _XIULUO_AUTO_WUYI_KEY, _XIULUO_CHECK_TEAM_KEY, _XIULUO_HEZI_KEY, _XIULUO_HIGH_PERF_KEY_OLD,
    _XIULUO_MIN_KEY, _XIULUO_RACES_KEY, _XIULUO_REPAIR_INTERVAL_KEY, _XIULUO_SHOUT_SWITCH_KEY,
    _XIULUO_SHOUT_TEXT_KEY, _XIULUO_TAB_GROUP_KEY, _XIULUO_WUYI_INTERVAL_KEY, _ZHONGZU_AUTO_CREATE_TEAM_KEY,
    _ZHONGZU_AUTO_REJOIN_KEY, _ZHONGZU_AUTO_REPAIR_KEY, _ZHONGZU_AUTO_WUYI_KEY, _ZHONGZU_CHECK_TEAM_KEY,
    _ZHONGZU_REPAIR_INTERVAL_KEY, _ZHONGZU_SHOUT_SWITCH_KEY, _ZHONGZU_SHOUT_TEXT_KEY, _ZHONGZU_TAB_GROUP_KEY,
    _ZHONGZU_WUYI_INTERVAL_KEY, _as_bool, _clamp_count,
    _kv_remove, _kv_save, _norm_shout_text,
    _sanitize_modes,
    build_group_last_scan_payload, load_auto_famen, load_auto_refill, load_battle_actions,
    load_battle_only_auto_rejoin, load_clear_screen, load_debug_overlay, load_debug_ui_unlocked,
    load_group_actions, load_group_last_scan, load_high_perf, load_selected_modes,
    load_silent_mode, load_task_counts, load_thresholds, load_ui_theme,
    load_update_auto_check, load_update_feed_url, load_update_last_check, load_update_skipped_version,
    load_use_sheyaoxiang, load_wubei_auto_create_team, load_wubei_auto_rejoin, load_wubei_auto_repair,
    load_wubei_auto_wuyi, load_wubei_check_team, load_wubei_hezi, load_wubei_kuimulang_switch,
    load_wubei_repair_interval, load_wubei_shout_switch, load_wubei_shout_text, load_wubei_tab_group,
    load_wubei_wuyi_interval, load_wuhuan_vitality_continue, load_xiuluo_auto_create_team, load_xiuluo_auto_rejoin,
    load_xiuluo_auto_kick_away, load_xiuluo_kick_away_minutes,
    load_xiuluo_auto_repair, load_xiuluo_auto_wuyi, load_xiuluo_check_team, load_xiuluo_hezi,
    load_xiuluo_repair_interval, load_xiuluo_settings, load_xiuluo_shout_switch, load_xiuluo_shout_text,
    load_xiuluo_tab_group, load_xiuluo_wuyi_interval, load_zhongzu_auto_create_team, load_zhongzu_auto_rejoin,
    load_zhongzu_auto_repair, load_zhongzu_auto_wuyi, load_zhongzu_check_team, load_zhongzu_repair_interval,
    load_zhongzu_shout_switch, load_zhongzu_shout_text, load_zhongzu_tab_group, load_zhongzu_wuyi_interval,
    save_auto_famen, save_auto_refill, save_battle_action, save_battle_only_auto_rejoin,
    save_clear_screen, save_debug_overlay, save_debug_ui_unlocked, save_group_actions,
    save_group_last_scan, save_high_perf, save_selected_modes, save_silent_mode,
    save_task_count, save_threshold, save_ui_theme, save_update_auto_check,
    save_update_last_check, save_update_skipped_version, save_use_sheyaoxiang, save_wubei_auto_create_team,
    save_wubei_auto_rejoin, save_wubei_auto_repair, save_wubei_auto_wuyi, save_wubei_check_team,
    save_wubei_hezi, save_wubei_kuimulang_switch, save_wubei_repair_interval, save_wubei_shout_switch,
    save_wubei_shout_text, save_wubei_tab_group, save_wubei_wuyi_interval, save_wuhuan_vitality_continue,
    save_xiuluo_auto_create_team, save_xiuluo_auto_rejoin, save_xiuluo_auto_kick_away,
    save_xiuluo_kick_away_minutes, save_xiuluo_auto_repair, save_xiuluo_auto_wuyi,
    save_xiuluo_check_team, save_xiuluo_hezi, save_xiuluo_min, save_xiuluo_races,
    save_xiuluo_repair_interval, save_xiuluo_shout_switch, save_xiuluo_shout_text, save_xiuluo_tab_group,
    save_xiuluo_wuyi_interval, save_zhongzu_auto_create_team, save_zhongzu_auto_rejoin, save_zhongzu_auto_repair,
    save_zhongzu_auto_wuyi, save_zhongzu_check_team, save_zhongzu_repair_interval, save_zhongzu_shout_switch,
    save_zhongzu_shout_text, save_zhongzu_tab_group, save_zhongzu_wuyi_interval,
)  # 过渡期 re-export：旧 import 仍可用，逐步切到 core.settings
# 注意：ControlWindow 方法按本模块全局名解析设置函数；测试 patch 类方法
# 路径时须 patch control_window 的绑定，patch core.settings 不会替换它。

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


# 静默按键诊断按钮白名单：hotkey 标识 → (修饰键, 主键, 展示文案,
# 投主框架窗口, WM_CHAR 字符码)。后两项为变体按钮：Ctrl+Tab 切 Tab
# 是客户端外壳功能改投主框架窗口；Enter 追加 WM_CHAR（部分应用只
# 消费 WM_CHAR 不消费 WM_KEYDOWN）。
_SILENT_KEY_HOTKEYS = {
    "altc": (("alt",), "c", "Alt+C", False, None),
    "altu": (("alt",), "u", "Alt+U", False, None),
    "alte": (("alt",), "e", "Alt+E", False, None),
    "alt5": (("alt",), "5", "Alt+5", False, None),
    "alt6": (("alt",), "6", "Alt+6", False, None),
    "ctrltab": (("ctrl",), "tab", "Ctrl+Tab", False, None),
    "enter": ((), "enter", "Enter", False, None),
    "ctrltabmain": (("ctrl",), "tab", "Ctrl+Tab→主窗口", True, None),
    "enterchar": ((), "enter", "Enter+WM_CHAR", False, 0x0D),
}


class ControlWindow(WebWindow):
    """应用主窗口：任务启动/停止、配置页面；日志展示在面板内日志抽屉。

    启停通过桥接调用 TaskManager（任务子进程管理），状态与日志由
    TaskManager 回调推送（sync_status / add_logs → 面板看板日志抽屉）。
    """

    def __init__(self, tm, title="啄木鸟", license_info=None, license_gate=None):
        super().__init__(R.ui("control_window.html"))
        self._tm = tm
        self._title = title
        # 启动任务前授权校验（main 注入的闭包：在线校验证书到期，返回
        # {"ok", "msg", "info"}）；启动按钮与老板键共用 start_task 入口
        self._license_gate = license_gate
        self._start_lock = threading.Lock()  # 校验+启动整体串行（按钮/老板键并发防双启）
        # 窗口标题附加绑定角色短文案（启动时解析一次；仅角色信息，
        # 保持简短——服务器+角色名，如「啄木鸟 - 一生所爱「长相思」」）
        label = bound_short_label()
        self._window_title = f"{title} - {label}" if label else title
        self._visible = True  # 启动时直接可见
        self._real_close = threading.Event()  # 置位后 X/销毁真正放行（退出路径）
        self._verifying = False  # 修罗回程验证进行中标记（防连点）
        self._group_checking = False  # 组号设置页「检查当前队伍」进行中标记（防连点）
        self._scheme_dialog_busy = False  # 方案导入导出文件对话框在途标记（防连点）
        self._pending_import = None  # 待确认的导入（{"name", "settings"}；同名冲突时挂起）
        self._license_info = license_info or {}  # 授权信息（高级设置-授权信息展示）
        self._panel_ready = False  # 页面首屏就绪标记（get_init_data 首次回调置位）
        self._pending_logs = []  # 页面就绪前的日志暂存（首屏加载补投抽屉）
        # 软件更新状态（后台检查/手动检查/下载/应用共用；页面经
        # get_init_data 或 onUpdateState 回调恢复）
        self._update_state = {"status": "idle", "current": __version__,
                              "progress": None, "error": "",
                              "manifest": None}
        self._update_lock = threading.RLock()
        self._update_apply_started = False  # 更新助手已拉起（防重复启动）
        self._update_last_result = None  # 上次升级结果（首屏展示一次）
        try:
            self._update_last_result = updater.read_last_result()
        except Exception:
            self._update_last_result = None
        self.expose(self.get_init_data)
        self.expose(self.save_task_modes)
        self.expose(self.save_task_count)
        self.expose(self.get_task_progress)
        self.expose(self.get_task_stats)
        self.expose(self.clear_task_stats)
        self.expose(self.save_use_sheyaoxiang)
        self.expose(self.save_threshold)
        self.expose(self.save_battle_action)
        self.expose(self.save_xiuluo_min)
        self.expose(self.save_xiuluo_races)
        self.expose(self.save_wuhuan_vitality_continue)
        self.expose(self.save_xiuluo_hezi)
        self.expose(self.save_xiuluo_check_team)
        self.expose(self.save_xiuluo_auto_create_team)
        self.expose(self.save_xiuluo_auto_rejoin)
        self.expose(self.save_xiuluo_auto_kick_away)
        self.expose(self.save_xiuluo_kick_away_minutes)
        self.expose(self.save_xiuluo_auto_wuyi)
        self.expose(self.save_xiuluo_wuyi_interval)
        self.expose(self.save_xiuluo_auto_repair)
        self.expose(self.save_xiuluo_repair_interval)
        self.expose(self.save_xiuluo_tab_group)
        self.expose(self.group_check_team)
        self.expose(self.group_last_scan)
        self.expose(self.group_save_actions)
        self.expose(self.get_group_state)
        self.expose(self.get_role_info)
        self.expose(self.save_high_perf)
        self.expose(self.save_auto_refill)
        self.expose(self.save_clear_screen)
        self.expose(self.save_silent_mode)
        self.expose(self.save_wubei_hezi)
        self.expose(self.save_wubei_check_team)
        self.expose(self.save_wubei_auto_create_team)
        self.expose(self.save_wubei_auto_rejoin)
        self.expose(self.save_battle_only_auto_rejoin)
        self.expose(self.save_wubei_shout_switch)
        self.expose(self.save_wubei_shout_text)
        self.expose(self.save_wubei_auto_wuyi)
        self.expose(self.save_wubei_wuyi_interval)
        self.expose(self.save_wubei_auto_repair)
        self.expose(self.save_wubei_repair_interval)
        self.expose(self.save_wubei_kuimulang_switch)
        self.expose(self.save_wubei_tab_group)
        self.expose(self.save_zhongzu_check_team)
        self.expose(self.save_zhongzu_auto_create_team)
        self.expose(self.save_zhongzu_auto_rejoin)
        self.expose(self.save_zhongzu_shout_switch)
        self.expose(self.save_zhongzu_shout_text)
        self.expose(self.save_zhongzu_auto_wuyi)
        self.expose(self.save_zhongzu_wuyi_interval)
        self.expose(self.save_zhongzu_auto_repair)
        self.expose(self.save_zhongzu_repair_interval)
        self.expose(self.save_zhongzu_tab_group)
        self.expose(self.save_xiuluo_shout_switch)
        self.expose(self.save_xiuluo_shout_text)
        self.expose(self.save_debug_overlay)
        self.expose(self.save_debug_ui_unlocked)
        self.expose(self.save_auto_famen)
        self.expose(self.save_ui_theme)
        self.expose(self.start_task)
        self.expose(self.stop_task)
        self.expose(self.verify_xiuluo_return)
        self.expose(self.verify_xiuluo_claim)
        self.expose(self.verify_xiuluo_wuyi)
        self.expose(self.verify_xiuluo_tab_wuyi)
        self.expose(self.verify_xiuluo_repair)
        self.expose(self.verify_xiuluo_tab_repair)
        self.expose(self.verify_xiuluo_claim_silent)
        self.expose(self.verify_xiuluo_wuyi_silent)
        self.expose(self.verify_xiuluo_return_silent)
        self.expose(self.verify_wubei_claim)
        self.expose(self.verify_wubei_return)
        self.expose(self.verify_zhongzu_claim)
        self.expose(self.verify_wubei_wuyi)
        self.expose(self.verify_wubei_repair)
        self.expose(self.verify_wubei_tab_repair)
        self.expose(self.verify_wubei_xj)
        self.expose(self.verify_wuhuan_cloth)
        self.expose(self.verify_wuhuan_claim)
        self.expose(self.verify_kezhan_awang)
        self.expose(self.verify_mingwang_claim)
        self.expose(self.verify_mingwang_claim_task)
        self.expose(self.verify_clear_screen)
        self.expose(self.verify_shout)
        self.expose(self.verify_jiguan)
        self.expose(self.verify_position)
        self.expose(self.verify_dialog_detect)
        self.expose(self.verify_apply_panel)
        self.expose(self.verify_team_check)
        self.expose(self.verify_team_create)
        self.expose(self.verify_zuoqi_open)
        self.expose(self.verify_zuoqi_close)
        self.expose(self.verify_zuoqi_fly)
        self.expose(self.verify_zuoqi_land)
        self.expose(self.verify_rejoin)
        self.expose(self.verify_team_away)
        self.expose(self.verify_team_kick_away)
        self.expose(self.verify_group_rejoin)
        self.expose(self.verify_capture_diag)
        self.expose(self.verify_silent_click)
        self.expose(self.verify_silent_key)
        self.expose(self.verify_silent_paste)
        self.expose(self.verify_silent_unichar)
        self.expose(self.verify_silent_char)
        self.expose(self.verify_child_windows)
        self.expose(self.unbind_license)
        self.expose(self.get_env_info)
        self.expose(self.unbind_window)
        self.expose(self.get_schemes)
        self.expose(self.switch_scheme)
        self.expose(self.scheme_resave)
        self.expose(self.scheme_copy)
        self.expose(self.scheme_delete)
        self.expose(self.scheme_export)
        self.expose(self.scheme_import)
        self.expose(self.scheme_import_confirm)
        self.expose(self.get_runtime_status)
        # 软件更新（系统页「关于」）
        self.expose(self.check_update)
        self.expose(self.get_update_state)
        self.expose(self.start_update)
        self.expose(self.skip_update_version)
        self.expose(self.set_update_auto_check)
        self.expose(self.open_external)
        # 任务状态/日志回流接线：TaskManager → 主窗口状态 + 日志窗口
        self._tm.set_callbacks(on_status=self.sync_status, on_log=self.add_logs)

    def _sync_role_binding(self):
        """绑定窗口已关闭、脚本回退操作其他游戏窗口时，把绑定记录对齐到
        实际操作窗口并立即刷新角色缓存。返回是否发生了改绑。

        find_main_window 在绑定窗口消失后动态回退（任务/验证实际就操作
        回退窗口），但不改绑会让方案/设置/成就读写停在旧角色——A 退出、
        打开 B 后面板仍显示 A（2026-11-13 用户反馈）。组号 pass 期间绑定
        窗口仍在，本函数不触发（绑定记录优先于标题的防漂移语义不变）。
        """
        try:
            win, rebound = game.ensure_binding_follows_live_window()
        except Exception as e:
            app_utils.log(f"[绑定] 自动改绑检测失败: {e}", "WARN")
            return False
        if not rebound:
            return False
        line = f"[绑定] 绑定窗口已关闭，自动改绑实际操作窗口: {win.title}"
        app_utils.log(line, "SYS")
        try:
            self.add_logs([line])
        except Exception:
            pass
        # 立即切到新角色目录：面板进度/方案/成就读写不等 30s 缓存
        user_data.current_role_id(refresh=True)
        return True

    def get_role_info(self):
        """当前角色信息（页面 5s 轮询）：检测运行期换号并驱动页面刷新。

        绑定窗口关闭后打开其他游戏窗口时先自动改绑（_sync_role_binding），
        角色随之切换；页面把返回的 id 与 get_init_data.role 比对，变化时
        全量重同步方案下拉/设置/顶部角色文案（按角色隔离数据）。
        """
        self._sync_role_binding()
        return {"id": user_data.current_role_id(),
                "label": bound_short_label() or ""}

    def get_env_info(self):
        """使用环境信息（系统页-使用环境展示）：适配主题/分辨率、
        当前游戏窗口分辨率、绑定窗口（含短角色文案 bound_short，
        顶栏 chip 用，与窗口标题同源）与设备 ID。

        绑定丢失且有自动选择窗口时注明实跑目标（find_main_window
        兜底：已登录窗口优先），并同步刷新窗口标题角色文案——防
        标题显示旧绑定角色而任务实际跑在自动选择窗口上。无绑定但
        存在窗口（自动选择生效）时同样直接展示该窗口标题，与标题
        栏角色一致（单游戏窗口首次启动即此场景）。绑定窗口已关闭时
        先把绑定记录改绑到实际操作窗口（_sync_role_binding），角色
        数据随实际窗口切换。
        """
        self._sync_role_binding()
        cur = "未检测到游戏窗口"
        win = game.find_main_window()
        if win is not None:
            cur = f"{win.rect.width} × {win.rect.height}"
        bound = game.find_bound_window()
        if bound is not None:
            bound_info = bound.title
        elif game.bound_window_id():
            raw = game.bound_window_id()
            old = raw.get("title") if isinstance(raw, dict) else str(raw)
            if win is not None:
                bound_info = f"已自动选择：{win.title}（原绑定 {old} 未匹配）"
            else:
                bound_info = "绑定丢失（重启后重选）"
        elif win is not None:
            bound_info = win.title  # 无绑定记录：自动选择窗口即操作目标
        else:
            bound_info = "未绑定（自动选择）"
        self.refresh_window_title()  # 标题角色文案随解析结果刷新
        try:
            device_id = Device.id  # classproperty：属性访问即为字符串，不可加括号调用
        except Exception:
            device_id = "获取失败"
        return {"theme": ENV_THEME,
                "adapt_res": f"{ENV_ADAPT_RES[0]} × {ENV_ADAPT_RES[1]}",
                "cur_res": cur,
                "bound": bound_info,
                "bound_short": bound_short_label() or "",
                "device_id": device_id}

    def unbind_window(self):
        """解除窗口绑定（下次启动多开时重新弹框选择）。"""
        return game.clear_bound_window()

    # ---- 配置方案管理（顶部下拉一键切换 + 通用设置管理区） ----
    # 方案 = 全部设置的快照（schemes.py）；任务运行中全部禁止（切换会
    # 改战斗阈值/任务列表等运行中任务正在读的键，删除活动方案会应用
    # 「默认」——页面同步禁用，此处为双保险）。

    def get_schemes(self):
        """方案列表与当前活动方案（页面下拉/管理区渲染）。"""
        return schemes.list_schemes()

    def _scheme_guard(self):
        """方案操作前置检查：任务运行中拒绝。"""
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再操作"}
        return None

    def switch_scheme(self, name):
        """一键切换方案：整体替换当前设置（切换前自动保存当前改动）。"""
        guard = self._scheme_guard()
        return guard if guard else schemes.switch_scheme(name)

    def scheme_resave(self, name):
        """保存：当前设置快照写回活动方案（输入名即方案名，改了名即重命名）。"""
        guard = self._scheme_guard()
        return guard if guard else schemes.resave_scheme(name)

    def scheme_copy(self, name):
        """另存新方案：当前设置另存为新方案（重名自动加序号）。"""
        guard = self._scheme_guard()
        return guard if guard else schemes.save_scheme_copy(name)

    def scheme_delete(self):
        """删除当前活动方案（页面二次点击确认；删除后回落「默认」）。"""
        guard = self._scheme_guard()
        if guard:
            return guard
        name = schemes.active_name()
        if not name:
            return {"status": "error", "msg": "当前无活动方案"}
        return schemes.delete_scheme(name)

    def scheme_export(self):
        """导出当前活动方案：后台线程弹另存为对话框，结果经
        onSchemeExportResult 回推页面（同验证流程模式，不阻塞 webview）。"""
        guard = self._scheme_guard()
        if guard:
            return guard
        if self._scheme_dialog_busy:
            return {"status": "error", "msg": "文件对话框已打开，请先完成当前操作"}
        name = schemes.active_name()
        if not name:
            return {"status": "error", "msg": "当前无活动方案"}
        self._scheme_dialog_busy = True
        threading.Thread(target=self._run_scheme_export, args=(name,),
                         daemon=True, name="scheme-export").start()
        return {"status": "success", "msg": "请在弹出的窗口选择导出位置"}

    def _run_scheme_export(self, name):
        """导出线程体：文件对话框 → 写文件 → 结果回推页面。"""
        try:
            path = schemes.pick_save_path(f"{name}.json")
            if not path:
                ok, info = False, "已取消导出"
            else:
                schemes.export_scheme_to(path, name)
                ok, info = True, f"已导出方案「{name}」"
        except Exception as e:
            ok, info = False, f"导出失败: {e}"
        self._push_js("if(window.onSchemeExportResult){window.onSchemeExportResult("
                      f"{'true' if ok else 'false'}, {json.dumps(info)});}}")
        self._scheme_dialog_busy = False

    def scheme_import(self):
        """导入方案：后台线程弹打开对话框；同名冲突经 onSchemeImportPending
        回推页面确认（覆盖/另存为/取消），无冲突直接入列（不激活）。"""
        guard = self._scheme_guard()
        if guard:
            return guard
        if self._scheme_dialog_busy:
            return {"status": "error", "msg": "文件对话框已打开，请先完成当前操作"}
        self._scheme_dialog_busy = True
        threading.Thread(target=self._run_scheme_import,
                         daemon=True, name="scheme-import").start()
        return {"status": "success", "msg": "请选择要导入的方案文件"}

    def _run_scheme_import(self):
        """导入线程体：对话框 → 解析校验 → 无冲突直接入列 / 冲突挂起等确认。"""
        try:
            path = schemes.pick_open_path()
            if not path:
                self._finish_scheme_import(False, "已取消导入")
                return
            name, settings = schemes.read_import_file(path)
            if schemes.name_exists(name):
                self._pending_import = {"name": name, "settings": settings}
                self._push_js("if(window.onSchemeImportPending)"
                              f"{{window.onSchemeImportPending({json.dumps(name)});}}")
                self._scheme_dialog_busy = False
                return
            result = schemes.add_scheme(name, settings)
            if result.get("status") != "success":
                self._finish_scheme_import(False, result.get("msg", "导入失败"))
                return
            self._finish_scheme_import(True, f"已导入方案「{name}」（未激活，可在顶部下拉切换）")
        except Exception as e:
            self._finish_scheme_import(False, f"导入失败: {e}")

    def _finish_scheme_import(self, ok, info):
        """导入结束回推页面（附最新方案列表供下拉重渲染）。"""
        self._pending_import = None
        self._push_js("if(window.onSchemeImportResult){window.onSchemeImportResult("
                      f"{'true' if ok else 'false'}, {json.dumps(info)}, "
                      f"{json.dumps(schemes.list_schemes())});}}")
        self._scheme_dialog_busy = False

    def scheme_import_confirm(self, action):
        """导入同名冲突确认（页面确认行按钮）：overwrite/rename/cancel。"""
        pending = self._pending_import
        self._pending_import = None
        if not pending:
            self._scheme_dialog_busy = False
            return {"status": "error", "msg": "无待确认的导入"}
        name, settings = pending["name"], pending["settings"]
        try:
            if action == "cancel":
                self._scheme_dialog_busy = False
                return {"status": "success", "msg": "已取消导入",
                        "schemes": schemes.list_schemes()}
            if action == "overwrite":
                result = schemes.replace_scheme(name, settings)
                msg = f"已覆盖方案「{name}」"
            elif action == "rename":
                new_name = schemes.unique_name(name)
                result = schemes.add_scheme(new_name, settings)
                msg = f"已导入为「{new_name}」（未激活，可在顶部下拉切换）"
            else:
                return {"status": "error", "msg": "未知操作",
                        "schemes": schemes.list_schemes()}
        finally:
            self._scheme_dialog_busy = False
        if result.get("status") != "success":
            return {**result, "schemes": schemes.list_schemes()}
        return {"status": "success", "msg": msg,
                "schemes": schemes.list_schemes()}

    def _push_js(self, js):
        """执行页面 JS（导入导出结果异步回推用；页面未就绪时静默忽略）。"""
        if self.webview_instance:
            try:
                self.webview_instance.evaluate_js(js)
            except Exception:
                pass

    # ==================== 软件更新（系统页「关于」） ====================

    def _set_update_state(self, **changes):
        """更新状态并异步推回页面；页面未就绪时由 get_init_data 兜底。"""
        with self._update_lock:
            self._update_state.update(changes)
        state = self.get_update_state()
        self._push_js(
            "if(window.onUpdateState){window.onUpdateState(%s);}"
            % json.dumps(state, ensure_ascii=False))
        return state

    def get_update_state(self):
        """当前更新状态 + 配置项（页面初始化/刷新恢复用）。"""
        with self._update_lock:
            state = dict(self._update_state)
        state.update({
            "current": __version__,
            "auto_check": load_update_auto_check(),
            "skipped_version": load_update_skipped_version(),
            "last_check": load_update_last_check(),
        })
        return state

    def schedule_update_check(self):
        """启动后延迟自动检查（24h 节流；主进程 prepare 后调用一次）。"""
        if not load_update_auto_check():
            return
        if time.time() - load_update_last_check() < update_config.AUTO_CHECK_INTERVAL:
            return
        threading.Thread(target=self._auto_update_worker, daemon=True,
                         name="update-check").start()

    def _auto_update_worker(self):
        time.sleep(update_config.STARTUP_CHECK_DELAY)
        if not load_update_auto_check():
            return
        # 用户可能在 8 秒内手动检查过：节流命中则不再请求
        if time.time() - load_update_last_check() < update_config.AUTO_CHECK_INTERVAL:
            return
        if not self._begin_update_check():
            return
        self._check_update_worker(False)

    def check_update(self):
        """手动检查更新（系统页按钮）：后台线程执行，结果经 onUpdateState 回推。"""
        if not self._begin_update_check():
            return self.get_update_state()
        threading.Thread(target=self._check_update_worker, args=(True,),
                         daemon=True, name="update-check-manual").start()
        return {"status": "checking"}

    def _begin_update_check(self):
        """占用检查状态；已有检查/下载在进行时返回 False。"""
        with self._update_lock:
            if self._update_state.get("status") in (
                    "checking", "downloading", "preparing",
                    "stopping", "applying"):
                return False
            self._update_state.update(status="checking", error="",
                                      progress=None)
        self._set_update_state()
        return True

    def _check_update_worker(self, manual):
        try:
            self._check_update_core(manual=manual)
        except Exception as e:
            app_utils.log(f"[更新] 检查异常：{e}", "WARN")
            self._set_update_state(status="error", error=str(e),
                                   progress=None)

    def _check_update_core(self, manual):
        result = updater.check_for_update(
            __version__, feed_url=load_update_feed_url() or None)
        if result.get("status") == "error":
            err = result.get("error") or "检查更新失败"
            if manual:
                app_utils.log(f"[更新] 检查失败：{err}", "WARN")
            return self._set_update_state(status="error", error=err,
                                          progress=None)
        save_update_last_check(time.time())
        manifest = result.get("manifest") or {}
        if result.get("status") == "up_to_date":
            if manual:
                app_utils.log("[更新] 已是最新版本", "SYS")
            return self._set_update_state(status="up_to_date", manifest=None,
                                          error="", progress=None)
        skipped = load_update_skipped_version()
        if (not manual and skipped
                and skipped == str(manifest.get("version") or "")):
            return self._set_update_state(status="up_to_date", manifest=None,
                                          error="", progress=None,
                                          skipped_version=skipped)
        app_utils.log(
            "[更新] 发现新版本 v%s（当前 v%s）"
            % (manifest.get("version"), __version__), "SYS")
        return self._set_update_state(status="available", manifest=manifest,
                                      error="", progress=None)

    def start_update(self, confirm_stop=False):
        """下载并应用更新。任务运行中需页面二次确认（confirm_stop=True）。"""
        confirm_stop = _as_bool(confirm_stop)
        if self._update_apply_started:
            return {"status": "busy", "msg": "更新助手已启动，程序即将重启"}
        state = self.get_update_state()
        if state.get("status") in ("downloading", "preparing", "stopping",
                                   "applying"):
            return {"status": "busy", "msg": "更新正在进行中"}
        manifest = state.get("manifest") or {}
        if state.get("status") != "available" or not manifest:
            return {"status": "error", "msg": "当前没有可用的更新"}
        if not updater.is_frozen():
            return {"status": "error", "msg": "源码运行不支持自动更新"}
        if self._tm.is_running() and not confirm_stop:
            return {"status": "need_stop",
                    "msg": "任务正在运行。更新会先停止任务并重启程序，是否继续？"}
        self._set_update_state(status="downloading", error="", progress={
            "stage": "downloading", "percent": 0, "done": 0, "total": 0})
        threading.Thread(target=self._update_worker, args=(manifest,),
                         daemon=True, name="update-apply").start()
        return {"status": "started"}

    def _on_update_progress(self, payload):
        """下载/解压进度回调（updater 在工作线程调用）。"""
        stage = str(payload.get("stage") or "")
        status = "downloading" if stage == "downloading" else "preparing"
        with self._update_lock:
            self._update_state["status"] = status
            self._update_state["progress"] = payload
        self._push_js(
            "if(window.onUpdateProgress){window.onUpdateProgress(%s);}"
            % json.dumps(payload, ensure_ascii=False))

    def _update_worker(self, manifest):
        """后台执行：下载/解压 → 停任务 → 启动更新助手 → 退出主进程。"""
        try:
            pending = updater.prepare_update(
                manifest,
                updater.install_root(),
                current_version=__version__,
                cwd=os.getcwd(),
                progress_cb=self._on_update_progress,
            )
            self._set_update_state(
                status="stopping",
                progress={"stage": "stopping", "percent": 100})
            if self._tm.is_running():
                result = self._tm.stop()
                if self._tm.is_running():
                    raise updater.UpdateError(
                        "任务停止失败，更新已取消：%r" % (result,))
            try:
                from ..core import debug_overlay
                debug_overlay.shutdown()
            except Exception:
                pass
            self._set_update_state(
                status="applying",
                progress={"stage": "applying", "percent": 100})
            updater.launch_apply_helper(pending["pending_path"])
            self._update_apply_started = True
            app_utils.log("[更新] 更新助手已启动，程序即将重启", "SYS")
            # Runtime.stop 正常会退出 GUI 循环；极端情况下 15 秒后强退，
            # 保证更新助手能等到主进程结束（它只等 60 秒）。
            threading.Thread(target=self._force_exit_after, args=(15,),
                             daemon=True, name="update-force-exit").start()
            from ascript.windows.system import Runtime
            Runtime.stop()
        except Exception as e:
            app_utils.log(f"[更新] 更新失败：{e}", "WARN")
            # 保留 available 状态与 manifest：错误多为下载/停止任务失败，
            # 用户修好网络/任务后可再次点「立即更新」，不必重新检查。
            self._set_update_state(status="available", error=str(e),
                                   progress=None)

    def _force_exit_after(self, seconds):
        time.sleep(seconds)
        app_utils.log("[更新] 主进程未在时限内退出，强制结束以便继续更新",
                      "WARN")
        os._exit(0)

    def open_external(self, url):
        """更新日志里的链接：仅放行 http(s)，用系统默认浏览器打开。

        链接文本来自远端清单（renderMarkdown 已限制 https?:// 并转义），
        这里再校验一次协议，拒绝 javascript: 等危险协议。
        """
        url = str(url or "").strip()
        if url.startswith(("https://", "http://")):
            try:
                webbrowser.open(url)
            except Exception as e:
                app_utils.log(f"[更新] 打开链接失败：{e}", "WARN")
        return {"status": "success"}

    def skip_update_version(self, version=None):
        """跳过指定版本（强制更新不可跳过）。"""
        state = self.get_update_state()
        manifest = state.get("manifest") or {}
        if manifest.get("mandatory"):
            return {"status": "error", "msg": "该版本为强制更新，不能跳过"}
        target = str(version or manifest.get("version") or "")
        if not target:
            return {"status": "error", "msg": "没有可跳过的版本"}
        save_update_skipped_version(target)
        app_utils.log(f"[更新] 已跳过版本 v{target}", "SYS")
        return self._set_update_state(status="up_to_date", manifest=None,
                                      skipped_version=target,
                                      error="", progress=None)

    def set_update_auto_check(self, on):
        """自动检查更新开关；开启时若已过 24h 立即补一次检查。"""
        on = _as_bool(on)
        result = save_update_auto_check(on)
        if on and (time.time() - load_update_last_check()
                   >= update_config.AUTO_CHECK_INTERVAL):
            threading.Thread(target=self._auto_update_worker, daemon=True,
                             name="update-check").start()
        return result

    def get_init_data(self):
        """页面初始化数据：主题、任务模式多选、任务次数、摄妖香开关、
        各条阈值、战斗动作、修罗/五倍/种族比武设置与调试信息开关。

        首次回调即页面首屏就绪：置位后就绪标记并补投启动阶段的暂存日志
        （授权校验结果等主进程启动消息，页面加载前无法投递）。
        读设置前先对齐绑定/角色（绑定窗口已关闭时改绑实际窗口），保证
        整包数据与 role 属于同一角色（页面据此建立角色切换检测基线）。"""
        self._sync_role_binding()
        self._panel_ready = True
        if self._pending_logs:
            pending, self._pending_logs = self._pending_logs, []
            self._push_panel_logs(pending)
        last_result = self._update_last_result
        self._update_last_result = None
        if last_result:
            try:
                updater.clear_last_result()
            except Exception:
                pass
        return {"theme": load_ui_theme(),
                "modes": load_selected_modes(),
                "task_counts": load_task_counts(),
                "use_sheyaoxiang": load_use_sheyaoxiang(),
                "thresholds": load_thresholds(),
                "battle": load_battle_actions(),
                "group_check_team": {"xiuluo": load_xiuluo_check_team(),
                                     "wubei": load_wubei_check_team(),
                                     "zhongzu": load_zhongzu_check_team()},
                "xiuluo": load_xiuluo_settings(),
                "wuhuan_vitality_continue": load_wuhuan_vitality_continue(),
                "xiuluo_hezi": load_xiuluo_hezi(),
                "xiuluo_check_team": load_xiuluo_check_team(),
                "xiuluo_auto_create_team": load_xiuluo_auto_create_team(),
                "xiuluo_auto_rejoin": load_xiuluo_auto_rejoin(),
                "xiuluo_auto_kick_away": load_xiuluo_auto_kick_away(),
                "xiuluo_kick_away_minutes": load_xiuluo_kick_away_minutes(),
                "xiuluo_auto_wuyi": load_xiuluo_auto_wuyi(),
                "xiuluo_wuyi_interval": load_xiuluo_wuyi_interval(),
                "xiuluo_auto_repair": load_xiuluo_auto_repair(),
                "xiuluo_repair_interval": load_xiuluo_repair_interval(),
                "xiuluo_tab_group": load_xiuluo_tab_group(),
                "high_perf": load_high_perf(),
                "auto_refill": load_auto_refill(),
                "clear_screen": load_clear_screen(),
                "silent_mode": load_silent_mode(),
                "wubei": {"hezi": load_wubei_hezi(),
                          "check_team": load_wubei_check_team(),
                          "auto_create_team": load_wubei_auto_create_team(),
                          "auto_rejoin": load_wubei_auto_rejoin(),
                          "shout": load_wubei_shout_switch(),
                          "shout_text": load_wubei_shout_text(),
                          "auto_wuyi": load_wubei_auto_wuyi(),
                          "wuyi_interval": load_wubei_wuyi_interval(),
                          "auto_repair": load_wubei_auto_repair(),
                          "repair_interval": load_wubei_repair_interval(),
                          "kuimulang": load_wubei_kuimulang_switch(),
                          "tab_group": load_wubei_tab_group()},
                "zhongzu": {"check_team": load_zhongzu_check_team(),
                            "auto_create_team": load_zhongzu_auto_create_team(),
                            "auto_rejoin": load_zhongzu_auto_rejoin(),
                            "shout": load_zhongzu_shout_switch(),
                            "shout_text": load_zhongzu_shout_text(),
                            "auto_wuyi": load_zhongzu_auto_wuyi(),
                            "wuyi_interval": load_zhongzu_wuyi_interval(),
                            "auto_repair": load_zhongzu_auto_repair(),
                            "repair_interval": load_zhongzu_repair_interval(),
                            "tab_group": load_zhongzu_tab_group()},
                "xiuluo_shout": load_xiuluo_shout_switch(),
                "xiuluo_shout_text": load_xiuluo_shout_text(),
                "battle_only_auto_rejoin": load_battle_only_auto_rejoin(),
                "auto_famen": load_auto_famen(),
                "debug_overlay": load_debug_overlay(),
                "debug_ui_unlocked": load_debug_ui_unlocked(),
                "license": self._license_info,
                "env": self.get_env_info(),
                "role": user_data.current_role_id(),  # 角色基线（页面换号检测）
                "version": __version__,
                "schemes": schemes.list_schemes(),
                "boss_hotkey": hotkey.load_hotkey(),
                "update": self.get_update_state(),
                "update_last_result": last_result}

    def save_battle_action(self, target, value):
        """战斗设置变更：保存人物/召唤兽默认战斗动作。"""
        return save_battle_action(target, value)

    # ---- 组号设置页（侧边栏「组号」：检查当前队伍 + 成员战斗动作） ----

    def group_check_team(self):
        """组号设置页「检查当前队伍」：主进程枚举 Tab + 开队伍面板读
        成员昵称匹配（tab_group.scan_group_members，不依赖任务状态），
        结果经 onGroupCheckResult 异步回推页面。

        前置：任务未运行、游戏窗口在前台（面板开合走真实点击）。
        """
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再检查"}
        if self._group_checking:
            return {"status": "error", "msg": "队伍检查进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        if not _game_window_clickable(win):
            return {"status": "error", "msg": "游戏窗口被其他窗口遮挡，请将游戏窗口置于前台后再检查"}
        self._group_checking = True
        threading.Thread(target=self._run_group_check, args=(win,),
                         daemon=True, name="group-check-team").start()
        return {"status": "success",
                "msg": "检查已开始：枚举 Tab → 开队伍面板读成员昵称 → 匹配本窗口队员"}

    def _run_group_check(self, win):
        """组号队伍检查线程体：结果（成员列表与已存动作）回推页面与日志窗。"""
        from ..device import tab_group  # 延迟导入：与其余验证模块保持一致
        try:
            self.add_logs(["[组号] 开始检查当前队伍（枚举 Tab + 队伍面板成员匹配）..."])
            ok, info, data = tab_group.scan_group_members(win)
        except Exception as e:
            ok, info, data = False, f"检查异常: {e}", None
        try:
            self.add_logs([f"[组号] 检查结果: {info}"])
        except Exception:
            pass
        if ok and data:
            # 手动检查同时刷新快照：组号页下次打开（含程序重启）自动展示
            ts = save_group_last_scan(data)
            if ts is not None:
                data["snapshot_ts"] = ts
        self._notify_group_check_result(ok, info, data)
        self._group_checking = False

    def group_last_scan(self):
        """组号页打开自动展示最近一次队伍检查快照（任务启动检查写入）。

        只读：不枚举窗口、不点击游戏。载荷与手动检查同结构，页面据此
        渲染队长/队员行；同一会话已有较新数据时页面自行忽略。
        """
        return build_group_last_scan_payload()

    def _notify_group_check_result(self, ok, info, data):
        """组号队伍检查结果回推页面（组号设置页成员行渲染）。

        载荷先 json.dumps 成 JSON 文本，再 json.dumps 成 JS 字符串字面量
        嵌入（与其余验证回调 onXxxVerifyResult 同款）——直接嵌 JSON 文本
        会让 JS 收到对象而非字符串，页面 JSON.parse 报「结果解析失败」
        （2026-11-07 实测教训）。
        """
        if self.webview_instance:
            try:
                payload = json.dumps({"ok": ok, "info": info, "data": data},
                                     ensure_ascii=False)
                self.webview_instance.evaluate_js(
                    f"if(window.onGroupCheckResult){{window.onGroupCheckResult({json.dumps(payload)});}}")
            except Exception:
                pass

    def group_save_actions(self, mapping):
        """组号设置页保存成员战斗动作。任务运行中拒绝修改（同任务选择）。"""
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再修改"}
        return save_group_actions(mapping)

    def save_threshold(self, name, value):
        """阈值设置变更：保存某条血蓝阈值。"""
        return save_threshold(name, value)

    def save_task_modes(self, modes):
        """任务多选变更：保存选中列表。任务运行中拒绝修改（只能停止后
        修改，用户要求；页面侧同样禁用控件，此为双保险）。"""
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再修改"}
        return save_selected_modes(modes)

    def save_task_count(self, mode, value):
        """任务次数变更：保存某模式的执行次数。任务运行中拒绝修改
        （只能停止后修改，用户要求；页面侧同样禁用控件，此为双保险）。"""
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再修改"}
        return save_task_count(mode, value)

    def get_task_progress(self):
        """任务总进度（页面进度条轮询）：选中任务当天完成次数合计 /
        次数上限合计；进度由任务子进程写入 config.json，本函数直读
        文件实时反映（见 task_progress_summary）。"""
        return task_progress_summary()

    def get_runtime_status(self):
        """运行看板状态：直读任务子进程写入的悬浮层状态文件的结构化
        status 字段（task/progress/battle/bars/team/name）。任务未运行
        或状态陈旧时返回 None。"""
        from ..core import debug_overlay  # 延迟导入：与其余验证模块保持一致
        return debug_overlay.read_runtime_status()

    def get_task_stats(self):
        """任务成就数据：直读当前角色 data/<ID>/task_stats.json
        （累计/各模式/最近 7 天/最近一次会话快照）。任务未运行或已
        停止时文件保留，照常返回；附 role（id/label）标明当前角色。"""
        from ..core import debug_overlay
        data = debug_overlay.read_task_stats()
        role = user_data.current_role_id()
        data["role"] = {"id": role, "label": bound_label_from_binding() or ""}
        return data

    def clear_task_stats(self):
        """清空任务统计：任务运行中拒绝（子进程正在写文件，防竞态）。"""
        if self._tm.is_running():
            return {"status": "error", "msg": "RUNNING"}
        from ..core import debug_overlay
        return debug_overlay.clear_task_stats()

    def get_group_state(self):
        """组号侧边栏入口显隐状态（页面 1.5s 轮询，任务未运行也生效）：

        show = 组号开关开（修罗/五倍/种族比武任一）且任务启动检查曾识别到组号
        （group_detected）且当前窗口已登录 Tab ≥2（实时枚举，账号变化
        即时反映——tab2 角色不在队伍不算组号，用户 2026-11-07 定义）。
        group_detected 由任务进程写入当前角色 state.json，主进程每次
        直读文件（跨进程可见）。
        """
        switch_on = (load_xiuluo_tab_group() or load_wubei_tab_group()
                     or load_zhongzu_tab_group())
        detected = bool(app_utils.role_state_get(_GROUP_DETECTED_KEY, False))
        if not detected and not user_data.is_migrated():
            detected = bool(app_utils.kv_read_fresh(_GROUP_DETECTED_KEY, False))
        tabs = 0
        if switch_on and detected:
            try:
                win = game.find_main_window()
                if win is not None:
                    tabs = len([c for c in game.list_tab_windows(win) if c["id"]])
            except Exception:
                tabs = 0
        return {"show": switch_on and detected and tabs >= 2, "tabs": tabs}

    def save_use_sheyaoxiang(self, on):
        """摄妖香开关变更：保存开关状态。"""
        return save_use_sheyaoxiang(on)

    def save_auto_famen(self, on):
        """战斗设置变更：保存自动使用法门开关状态。"""
        return save_auto_famen(on)

    def save_xiuluo_min(self, value):
        """修罗设置变更：保存最少人数。"""
        return save_xiuluo_min(value)

    def save_xiuluo_races(self, races):
        """修罗设置变更：保存必须有的种族列表。"""
        return save_xiuluo_races(races)

    def save_wuhuan_vitality_continue(self, on):
        """五环设置变更：保存活力不足继续任务开关状态。"""
        return save_wuhuan_vitality_continue(on)

    def save_xiuluo_hezi(self, on):
        """修罗设置变更：保存自动抢盒子开关状态。"""
        return save_xiuluo_hezi(on)

    def save_xiuluo_check_team(self, on):
        """修罗设置变更：保存检查队伍开关状态。"""
        return save_xiuluo_check_team(on)

    def save_xiuluo_auto_create_team(self, on):
        """修罗设置变更：保存自动创建队伍开关状态。"""
        return save_xiuluo_auto_create_team(on)

    def save_xiuluo_auto_rejoin(self, on):
        """修罗设置变更：保存自动归队开关状态。"""
        return save_xiuluo_auto_rejoin(on)

    def save_xiuluo_auto_kick_away(self, on):
        """修罗设置变更：保存暂离超时自动请离开关状态。"""
        return save_xiuluo_auto_kick_away(on)

    def save_xiuluo_kick_away_minutes(self, value):
        """修罗设置变更：保存暂离超时（分钟）。"""
        return save_xiuluo_kick_away_minutes(value)

    def save_xiuluo_auto_wuyi(self, on):
        """修罗设置变更：保存自动巫医开关状态。"""
        return save_xiuluo_auto_wuyi(on)

    def save_xiuluo_wuyi_interval(self, value):
        """修罗设置变更：保存自动巫医间隔（轮）。"""
        return save_xiuluo_wuyi_interval(value)

    def save_xiuluo_auto_repair(self, on):
        """修罗设置变更：保存自动修理装备开关状态。"""
        return save_xiuluo_auto_repair(on)

    def save_xiuluo_repair_interval(self, value):
        """修罗设置变更：保存自动修理间隔（场战斗）。"""
        return save_xiuluo_repair_interval(value)

    def save_xiuluo_tab_group(self, on):
        """修罗设置变更：保存组号专用开关状态。"""
        return save_xiuluo_tab_group(on)

    def save_high_perf(self, on):
        """通用设置变更：保存高性能模式开关状态。"""
        return save_high_perf(on)

    def save_auto_refill(self, on):
        """通用设置变更：保存战斗结束自动补血量蓝量开关状态。"""
        return save_auto_refill(on)

    def save_clear_screen(self, on):
        """通用设置变更：保存启动清屏开关状态。"""
        return save_clear_screen(on)

    def save_silent_mode(self, on):
        """通用设置变更：保存静默操作开关状态（主进程立即生效）。"""
        return save_silent_mode(on)

    def save_wubei_hezi(self, on):
        """五倍设置变更：保存自动抢盒子开关状态。"""
        return save_wubei_hezi(on)

    def save_wubei_check_team(self, on):
        """五倍设置变更：保存检查队伍开关状态。"""
        return save_wubei_check_team(on)

    def save_wubei_auto_create_team(self, on):
        """五倍设置变更：保存自动创建队伍开关状态。"""
        return save_wubei_auto_create_team(on)

    def save_wubei_auto_rejoin(self, on):
        """五倍设置变更：保存自动归队开关状态。"""
        return save_wubei_auto_rejoin(on)

    def save_battle_only_auto_rejoin(self, on):
        """仅战斗设置变更：保存自动归队开关状态。"""
        return save_battle_only_auto_rejoin(on)

    def save_wubei_shout_switch(self, on):
        """五倍设置变更：保存缺人自动喊话开关状态。"""
        return save_wubei_shout_switch(on)

    def save_wubei_shout_text(self, text):
        """五倍设置变更：保存喊话内容（返回规范化后的生效内容）。"""
        return save_wubei_shout_text(text)

    def save_wubei_auto_wuyi(self, on):
        """五倍设置变更：保存自动巫医开关状态。"""
        return save_wubei_auto_wuyi(on)

    def save_wubei_wuyi_interval(self, value):
        """五倍设置变更：保存自动巫医间隔（轮）。"""
        return save_wubei_wuyi_interval(value)

    def save_wubei_auto_repair(self, on):
        """五倍设置变更：保存自动修理装备开关状态。"""
        return save_wubei_auto_repair(on)

    def save_wubei_repair_interval(self, value):
        """五倍设置变更：保存自动修理间隔（场战斗）。"""
        return save_wubei_repair_interval(value)

    def save_wubei_kuimulang_switch(self, on):
        """五倍设置变更：保存领取奎木狼任务开关状态。"""
        return save_wubei_kuimulang_switch(on)

    def save_wubei_tab_group(self, on):
        """五倍设置变更：保存组号专用开关状态。"""
        return save_wubei_tab_group(on)

    def save_zhongzu_check_team(self, on):
        """种族比武设置变更：保存检查队伍开关状态。"""
        return save_zhongzu_check_team(on)

    def save_zhongzu_auto_create_team(self, on):
        """种族比武设置变更：保存自动创建队伍开关状态。"""
        return save_zhongzu_auto_create_team(on)

    def save_zhongzu_auto_rejoin(self, on):
        """种族比武设置变更：保存自动归队开关状态。"""
        return save_zhongzu_auto_rejoin(on)

    def save_zhongzu_shout_switch(self, on):
        """种族比武设置变更：保存缺人自动喊话开关状态。"""
        return save_zhongzu_shout_switch(on)

    def save_zhongzu_shout_text(self, text):
        """种族比武设置变更：保存喊话内容（返回规范化后的生效内容）。"""
        return save_zhongzu_shout_text(text)

    def save_zhongzu_auto_wuyi(self, on):
        """种族比武设置变更：保存自动巫医开关状态。"""
        return save_zhongzu_auto_wuyi(on)

    def save_zhongzu_wuyi_interval(self, value):
        """种族比武设置变更：保存自动巫医间隔（轮）。"""
        return save_zhongzu_wuyi_interval(value)

    def save_zhongzu_auto_repair(self, on):
        """种族比武设置变更：保存自动修理装备开关状态。"""
        return save_zhongzu_auto_repair(on)

    def save_zhongzu_repair_interval(self, value):
        """种族比武设置变更：保存自动修理间隔（场战斗）。"""
        return save_zhongzu_repair_interval(value)

    def save_zhongzu_tab_group(self, on):
        """种族比武设置变更：保存组号专用开关状态。"""
        return save_zhongzu_tab_group(on)

    def save_xiuluo_shout_switch(self, on):
        """修罗设置变更：保存缺人自动喊话开关状态。"""
        return save_xiuluo_shout_switch(on)

    def save_xiuluo_shout_text(self, text):
        """修罗设置变更：保存喊话内容（返回规范化后的生效内容）。"""
        return save_xiuluo_shout_text(text)

    def save_debug_overlay(self, on):
        """调试信息开关变更：保存开关状态（悬浮层由任务侧读取、
        诊断导航显隐由页面侧同步）。"""
        return save_debug_overlay(on)

    def save_debug_ui_unlocked(self, on):
        """「显示与调试」卡片解锁（关于页连点版本号 6 次）：保存
        解锁标记，解锁后下次启动直接展示该卡片。"""
        return save_debug_ui_unlocked(on)

    def save_ui_theme(self, theme):
        """主题切换：保存浅色/深色选择。"""
        return save_ui_theme(theme)

    def start_task(self):
        """启动按钮：启动任务子进程。

        先刷新标题角色文案（任务目标窗口解析在绑定丢失后可能已自动选择
        新窗口）；每次启动前在线校验证书到期状态（续期/换卡无需重启软件，
        下次启动即生效；过期/无效/网络异常拦截启动并提示）。老板键与按钮
        共用本入口，锁内校验+启动整体串行，防并发双启。
        """
        self.refresh_window_title()
        with self._start_lock:
            if self._tm.is_running():
                return {"status": "success", "running": True}
            if self._license_gate is not None:
                try:
                    result = self._license_gate() or {}
                except Exception as e:
                    result = {"ok": False, "msg": f"授权服务异常：{e}"}
                self._set_license_info(result.get("info") or {})
                if not result.get("ok"):
                    msg = result.get("msg") or "授权校验未通过"
                    if "无法连接授权服务器" in msg:
                        hint = "请检查网络后重试"
                    else:
                        hint = "请重启软件重新激活或联系作者续费"
                    fail_line = f"[授权] 校验未通过：{msg}"
                    app_utils.log(fail_line, "WARN")
                    self.add_logs([fail_line])
                    return {"status": "error",
                            "msg": f"授权校验未通过，任务未启动：{msg}（{hint}）"}
                # 校验通过：授权信息入日志（文件+抽屉，与拦截日志对称）
                info = self._license_info
                pass_line = f"[授权] 校验通过：{info.get('status', '未知')}"
                if info.get("expire"):
                    pass_line += f"，到期 {info['expire']}"
                app_utils.log(pass_line, "SYS")
                self.add_logs([pass_line])
            self._sync_start_tab_binding()  # 角色目录跟随「任务启动所在 Tab」
            self.refresh_window_title()     # 绑定变化后刷新标题角色文案
            return self._tm.start()

    def _sync_start_tab_binding(self):
        """任务启动前把窗口绑定对齐到游戏当前所在 Tab。

        组号（一窗口多 Tab）模式下任务在「启动所在 Tab」执行
        （tab_group.init_task 以可见 Tab 为队长候选），而角色数据目录以
        绑定记录为准（user_data._probe_role_id 绑定优先——pass 切到队员
        Tab 时窗口标题会短暂变成队员账号，不能按标题解析）。启动前两者
        对齐，避免用户手动切到别的 Tab 后开任务仍读写旧角色目录。
        未登录（标题无 ID）或窗口未找到时不改绑定。
        """
        try:
            win = game.find_main_window()
            rid = user_data.role_id_from_text(
                getattr(win, "title", "") if win is not None else "")
            if not rid:
                return
            raw = game.bound_window_id()
            bid = raw.get("id") if isinstance(raw, dict) else raw
            if bid == f"（ID：{rid}）":
                return
            game.save_bound_window(win)
            app_utils.log(f"[绑定] 任务启动 Tab 角色变化，绑定更新: {win.title}", "SYS")
            # 立即切到新角色目录：面板进度/成就/设置读写不等 30s 缓存
            user_data.current_role_id(refresh=True)
        except Exception as e:
            app_utils.log(f"[绑定] 启动 Tab 对齐失败: {e}", "WARN")

    def _set_license_info(self, info):
        """更新授权信息并推送面板（系统页授权信息区 + 侧边栏状态点实时刷新）。"""
        self._license_info = info or {}
        if self.webview_instance:
            try:
                self.webview_instance.evaluate_js(
                    "if(window.applyLicense){window.applyLicense(%s);}"
                    % json.dumps(self._license_info))
            except Exception:
                pass

    def unbind_license(self):
        """高级设置-解绑激活码：清除本地缓存激活码。

        解绑后下次点击「启动」即被授权门拦截（启动前校验发现无缓存码、
        旧会话授权数据同步作废）；重启软件弹出激活窗口可输入新卡密。
        """
        _kv_remove("license_cache_no")
        return {"status": "success",
                "msg": "已解绑本地激活码：后续启动任务将被拦截，重启软件可重新输入卡密激活"}

    def stop_task(self):
        """停止按钮：停止任务子进程（升级强杀，保证任务线程全清）。"""
        return self._tm.stop()

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
        from ..tasks import xiuluo  # 延迟导入：避免启动期加载任务模块
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

    def verify_xiuluo_tab_wuyi(self):
        """高级设置-修罗组号巫医验证按钮：队长巫医链路 + 队员 Tab 巫医 pass。

        主进程无战斗线程，队长医治选项由验证自行点击（与 verify_wuyi_flow
        一致）；前置：窗口内 ≥2 个已登录 Tab（开关状态不校验）。
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
        threading.Thread(target=self._run_tab_wuyi_verify, args=(win,),
                         daemon=True, name="xiuluo-tab-wuyi-verify").start()
        return {"status": "success",
                "msg": "验证已开始：队长巫医链路 → 逐个队员 Tab 点击医治对话框 → 切回队长（需 ≥2 个已登录 Tab）"}

    def _run_tab_wuyi_verify(self, win):
        """组号巫医验证线程体：队长巫医链路 + 队员 Tab pass，结果回推页面与日志窗。"""
        from ..tasks import xiuluo  # 延迟导入：避免启动期加载任务模块
        try:
            self.add_logs(["[验证] 开始验证修罗组号巫医（队长巫医 → 逐个队员 Tab 医治对话框 → 切回队长）..."])
            ok, info = xiuluo.verify_tab_wuyi_flow(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_tab_wuyi_verify_result(ok, info)
        self._verifying = False

    def _notify_tab_wuyi_verify_result(self, ok, info):
        """组号巫医验证结果回推页面（高级设置-组号巫医验证行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onTabWuyiVerifyResult){{window.onTabWuyiVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    # ---- 自动修理装备验证（诊断页：修罗/五倍 × 单号/组号） ----

    def verify_xiuluo_repair(self):
        """诊断-验证修理：修罗自动修理链路（主进程自行点选项，不改任务状态）。"""
        return self._start_repair_verify("xiuluo", group=False)

    def verify_xiuluo_tab_repair(self):
        """诊断-验证组号修理：队长修理链路 + 队员 Tab 修理 pass。"""
        return self._start_repair_verify("xiuluo", group=True)

    def verify_wubei_repair(self):
        """诊断-验证修理：五倍自动修理链路（主进程自行点选项，不改任务状态）。"""
        return self._start_repair_verify("wubei", group=False)

    def verify_wubei_tab_repair(self):
        """诊断-验证组号修理：五倍队长修理链路 + 队员 Tab 修理 pass。"""
        return self._start_repair_verify("wubei", group=True)

    def _start_repair_verify(self, task, group):
        """修理验证公共入口：校验任务/窗口状态后起验证线程。"""
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        if not _game_window_clickable(win):
            return {"status": "error",
                    "msg": "游戏窗口被其他窗口遮挡，请将游戏窗口置于前台后再验证"}
        self._verifying = True
        label = "修罗" if task == "xiuluo" else "五倍"
        name = f"{task}-{'tab-' if group else ''}repair-verify"
        threading.Thread(target=self._run_repair_verify,
                         args=(task, group, win), daemon=True, name=name).start()
        msg = f"验证已开始：{label}修理链路（世界地图 → 洛阳城 → 修理 NPC → 对话框选项）"
        if group:
            msg += "；随后逐个队员 Tab 点修理对话框并切回队长（需 ≥2 个已登录 Tab）"
        return {"status": "success", "msg": msg}

    def _run_repair_verify(self, task, group, win):
        """修理验证线程体：跑任务模块的修理链路，结果回推页面与日志窗。"""
        from ..tasks import xiuluo, wubei  # 延迟导入：任务模块反向导入本模块（避免循环）
        mod = xiuluo if task == "xiuluo" else wubei
        label = "修罗" if task == "xiuluo" else "五倍"
        try:
            self.add_logs([f"[验证] 开始验证{label}自动修理"
                           + ("（含组号队员 pass）" if group else "") + "..."])
            if group:
                ok, info = mod.verify_tab_repair_flow(win)
            else:
                ok, info = mod.verify_repair_flow(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_repair_verify_result(task, group, ok, info)
        self._verifying = False

    def _notify_repair_verify_result(self, task, group, ok, info):
        """修理验证结果回推页面（按任务/单号组号选择对应回调）。"""
        callbacks = {
            ("xiuluo", False): "onRepairVerifyResult",
            ("xiuluo", True): "onTabRepairVerifyResult",
            ("wubei", False): "onWubeiRepairVerifyResult",
            ("wubei", True): "onWubeiTabRepairVerifyResult",
        }
        cb = callbacks[(task, group)]
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.{cb}){{window.{cb}({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def _run_claim_verify(self, win):
        """领取验证线程体：跑修罗领取链路（选项悬停代替点击），结果回推页面与日志窗。"""
        from ..tasks import xiuluo  # 延迟导入：避免启动期加载任务模块
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

    def verify_xiuluo_claim_silent(self):
        """诊断-静默修罗领取按钮：与验证领取同一流程，但全程静默点击
        （不移动鼠标、不激活游戏窗口，游戏窗口可不前台）——验证静默
        通道跑通完整领取链路。不需要 _game_window_clickable 前置
        （消息点击不落屏幕坐标，遮挡无关）。
        """
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        self._verifying = True
        threading.Thread(target=self._run_claim_verify_silent, args=(win,),
                         daemon=True, name="xiuluo-claim-silent-verify").start()
        return {"status": "success",
                "msg": "验证已开始：静默修罗领取（全程消息点击，不动鼠标不抢前台）"}

    def _run_claim_verify_silent(self, win):
        """静默修罗领取验证线程体：与领取验证同一流程，全程静默点击。"""
        from ..tasks import xiuluo  # 延迟导入：避免启动期加载任务模块
        try:
            self.add_logs(["[验证] 开始静默修罗领取（全程消息点击，不动鼠标不抢前台）..."])
            with app_utils.silent_clicks():
                ok, info = xiuluo.verify_claim_flow(win, silent=True)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_silent_claim_verify_result(ok, info)
        self._verifying = False

    def _notify_silent_claim_verify_result(self, ok, info):
        """静默修罗领取验证结果回推页面（诊断-静默修罗领取行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onSilentClaimVerifyResult){{window.onSilentClaimVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_xiuluo_wuyi_silent(self):
        """诊断-静默修罗巫医按钮：与验证巫医同一流程，但全程静默点击
        （不移动鼠标、不激活游戏窗口，游戏窗口可不前台）——验证静默
        通道跑通完整巫医链路。不需要 _game_window_clickable 前置
        （消息点击不落屏幕坐标，遮挡无关）。
        """
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        self._verifying = True
        threading.Thread(target=self._run_wuyi_verify_silent, args=(win,),
                         daemon=True, name="xiuluo-wuyi-silent-verify").start()
        return {"status": "success",
                "msg": "验证已开始：静默修罗巫医（全程消息点击，不动鼠标不抢前台）"}

    def _run_wuyi_verify_silent(self, win):
        """静默修罗巫医验证线程体：与巫医验证同一流程，全程静默点击。"""
        from ..tasks import xiuluo  # 延迟导入：避免启动期加载任务模块
        try:
            self.add_logs(["[验证] 开始静默修罗巫医（全程消息点击，不动鼠标不抢前台）..."])
            with app_utils.silent_clicks():
                ok, info = xiuluo.verify_wuyi_flow(win, silent=True)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_silent_wuyi_verify_result(ok, info)
        self._verifying = False

    def _notify_silent_wuyi_verify_result(self, ok, info):
        """静默修罗巫医验证结果回推页面（诊断-静默修罗巫医行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onSilentWuyiVerifyResult){{window.onSilentWuyiVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_xiuluo_return_silent(self):
        """诊断-静默修罗回程按钮：与验证回程同一流程，但全程静默点击
        （不移动鼠标、不激活游戏窗口，游戏窗口可不前台）——验证静默
        通道跑通完整回程链路。不需要 _game_window_clickable 前置
        （消息点击不落屏幕坐标，遮挡无关）。
        """
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        self._verifying = True
        threading.Thread(target=self._run_return_verify_silent, args=(win,),
                         daemon=True, name="xiuluo-return-silent-verify").start()
        return {"status": "success",
                "msg": "验证已开始：静默修罗回程（全程消息点击，不动鼠标不抢前台）"}

    def _run_return_verify_silent(self, win):
        """静默修罗回程验证线程体：与回程验证同一流程，全程静默点击。"""
        from ..tasks import xiuluo  # 延迟导入：避免启动期加载任务模块
        try:
            self.add_logs(["[验证] 开始静默修罗回程（全程消息点击，不动鼠标不抢前台）..."])
            with app_utils.silent_clicks():
                ok, info = xiuluo.verify_return_flow(win, silent=True)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_silent_return_verify_result(ok, info)
        self._verifying = False

    def _notify_silent_return_verify_result(self, ok, info):
        """静默修罗回程验证结果回推页面（诊断-静默修罗回程行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onSilentReturnVerifyResult){{window.onSilentReturnVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

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
        from ..tasks import wubei  # 延迟导入：避免启动期加载任务模块
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

    def verify_zhongzu_claim(self):
        """诊断页-种族比武-测试领取任务按钮：走位到长安领取点 → 点击
        领取 NPC 打开对话框 → 绿色领取选项仅定位不点击（点击即领取）
        → 点屏幕中间关闭对话框。不领取任务、不检查队伍（单人可验证），
        与修罗/五倍领取验证同模式。"""
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
        threading.Thread(target=self._run_zhongzu_claim_verify, args=(win,),
                         daemon=True, name="zhongzu-claim-verify").start()
        return {"status": "success",
                "msg": "验证已开始：走位到长安领取点 → 领取 NPC → "
                       "选项仅定位不点击 → 关对话框（不领取）"}

    def _run_zhongzu_claim_verify(self, win):
        """种族比武领取测试线程体：选项悬停代替点击，结果回推页面与日志窗。"""
        from ..tasks import zhongzu  # 延迟导入：避免启动期加载任务模块
        try:
            self.add_logs(["[验证] 开始测试种族比武领取"
                           "（选项仅定位不点击，最后关闭对话框）..."])
            ok, info = zhongzu.verify_claim_flow(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_zhongzu_claim_verify_result(ok, info)
        self._verifying = False

    def _notify_zhongzu_claim_verify_result(self, ok, info):
        """种族比武领取测试结果回推页面（诊断页-种族比武链路结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onZhongzuClaimVerifyResult){{window.onZhongzuClaimVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_wubei_wuyi(self):
        """高级设置-五倍巫医验证按钮：与自动巫医同一链路（条件门槛跳过、
        血蓝状态仅报告），NPC 点击前先移动鼠标定位（无延时）；对话框处理
        由验证流程自行点击医治选项（主进程没有战斗线程，与任务进程中
        战斗线程点击行为等效）。"""
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
        threading.Thread(target=self._run_wubei_wuyi_verify, args=(win,),
                         daemon=True, name="wubei-wuyi-verify").start()
        return {"status": "success",
                "msg": "验证已开始：血蓝检查 → 走位到巫医 → NPC 定位 → 点击 → 对话框处理"}

    def _run_wubei_wuyi_verify(self, win):
        """五倍巫医验证线程体：跑自动巫医链路（悬停代替直接点击），结果回推页面与日志窗。"""
        from ..tasks import wubei  # 延迟导入：避免启动期加载任务模块
        try:
            self.add_logs(["[验证] 开始验证五倍巫医（走位 → NPC 定位 → 点击 → 对话框处理）..."])
            ok, info = wubei.verify_wuyi_flow(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_wubei_wuyi_verify_result(ok, info)
        self._verifying = False

    def _notify_wubei_wuyi_verify_result(self, ok, info):
        """五倍巫医验证结果回推页面（高级设置-五倍巫医验证行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onWubeiWuyiVerifyResult){{window.onWubeiWuyiVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_wubei_xj(self):
        """高级设置-测试白龙马模板按钮：按任务流程实际点击候选模板
        （与任务流程同算法同阈值找候选 → 逐一真实点击 → 误点对话框
        关闭后继续 → 开打对话出现即停止）。真实点击会打开/关闭游戏
        对话框；开打对话出现后停留不点开打选项（点击即进入战斗）。
        任务运行中不可验证（点击与任务流程冲突）。"""
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
        threading.Thread(target=self._run_wubei_xj_verify, args=(win,),
                         daemon=True, name="wubei-xj-verify").start()
        return {"status": "success",
                "msg": "验证已开始：按任务流程实际点击白龙马模板候选，结果见日志"}

    def _run_wubei_xj_verify(self, win):
        """白龙马模板测试线程体：实际点击候选模板（与任务流程一致），结果回推页面与日志窗。"""
        from ..tasks import wubei  # 延迟导入：与其余验证模块保持一致
        try:
            self.add_logs(["[验证] 开始测试白龙马模板（按任务流程实际点击候选）..."])
            ok, info = wubei.verify_xj_flow(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_wubei_xj_verify_result(ok, info)
        self._verifying = False

    def _notify_wubei_xj_verify_result(self, ok, info):
        """白龙马模板测试结果回推页面（高级设置-五倍验证行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onWubeiXjVerifyResult){{window.onWubeiXjVerifyResult({ok_js}, {json.dumps(info)});}}")
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
        from ..tasks import wubei  # 延迟导入：避免启动期加载任务模块
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

    def verify_wuhuan_cloth(self):
        """诊断页-五环-测试进入布店按钮：与任务购买流程的进店段一致
        （走位到布店 → 降落检查 → 点入口 → 点老板 → 点「我想买点
        东西」），商店对话框打开即成功——不购买任何装备。"""
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
        threading.Thread(target=self._run_wuhuan_cloth_verify, args=(win,),
                         daemon=True, name="wuhuan-cloth-verify").start()
        return {"status": "success",
                "msg": "验证已开始：走位到布店 → 降落检查 → 进店 → 点老板 → 打开商店对话框（不购买）"}

    def _run_wuhuan_cloth_verify(self, win):
        """进入布店测试线程体：跑真实进店链路（商店对话框打开即成功），结果回推页面与日志窗。"""
        from ..tasks import wuhuan  # 延迟导入：避免启动期加载任务模块
        try:
            self.add_logs(["[验证] 开始测试进入布店（进店打开商店对话框即成功，不购买）..."])
            ok, info = wuhuan.verify_cloth_shop_flow(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_wuhuan_cloth_verify_result(ok, info)
        self._verifying = False

    def _notify_wuhuan_cloth_verify_result(self, ok, info):
        """进入布店测试结果回推页面（诊断页-五环链路-测试进入布店行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onWuhuanClothVerifyResult){{window.onWuhuanClothVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_wuhuan_claim(self):
        """诊断页-五环-测试领取五环按钮：与领取流程一致到点击领取任务
        按钮为止，对话面板出现即成功——不点任何选项、不真正领取。"""
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
        threading.Thread(target=self._run_wuhuan_claim_verify, args=(win,),
                         daemon=True, name="wuhuan-claim-verify").start()
        return {"status": "success",
                "msg": "验证已开始：走位到云游大师 → 点领取任务按钮 → 对话面板出现即成功（不领取）"}

    def _run_wuhuan_claim_verify(self, win):
        """领取五环测试线程体：跑真实领取链路到对话面板出现为止，结果回推页面与日志窗。"""
        from ..tasks import wuhuan  # 延迟导入：避免启动期加载任务模块
        try:
            self.add_logs(["[验证] 开始测试领取五环（对话面板出现即成功，不领取）..."])
            ok, info = wuhuan.verify_claim_flow(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_wuhuan_claim_verify_result(ok, info)
        self._verifying = False

    def _notify_wuhuan_claim_verify_result(self, ok, info):
        """领取五环测试结果回推页面（诊断页-五环链路-测试领取五环行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onWuhuanClaimVerifyResult){{window.onWuhuanClaimVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_kezhan_awang(self):
        """诊断页-客栈-找阿旺按钮：与任务找阿旺流程一致（走位到大理寺
        墙外 → 定位阿旺 → 走近 → 点击交互点；未找到时兜底走洛阳城
        阿旺出现点重找一轮），点击阿旺会真实推进任务对话。"""
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
        threading.Thread(target=self._run_kezhan_awang_verify, args=(win,),
                         daemon=True, name="kezhan-awang-verify").start()
        return {"status": "success",
                "msg": "验证已开始：走位到大理寺墙外 → 定位阿旺 → 走近 → 点击（未找到兜底洛阳城出现点）"}

    def _run_kezhan_awang_verify(self, win):
        """找阿旺测试线程体：跑真实找阿旺链路（点击阿旺即成功），结果回推页面与日志窗。"""
        from ..tasks import kezhan  # 延迟导入：避免启动期加载任务模块
        try:
            self.add_logs(["[验证] 开始测试找阿旺（走位到大理寺墙外 → 定位 → 走近 → 点击）..."])
            ok, info = kezhan.verify_find_awang(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_kezhan_awang_verify_result(ok, info)
        self._verifying = False

    def _notify_kezhan_awang_verify_result(self, ok, info):
        """找阿旺测试结果回推页面（诊断页-客栈链路-找阿旺行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onKezhanAwangVerifyResult){{window.onKezhanAwangVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_mingwang_claim(self):
        """诊断页-名望-测试名望领取按钮：与任务第一阶段链路一致（种族
        判定 → 前往大唐边境领取点 → 点击逆天魔 → 对话框选「开始山河
        名望」→ 点击山河名望入口打开任务领取面板），到打开面板为止，
        不在面板内领取任务。仙族/其他种族判定后结束并提示待补充。
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
        threading.Thread(target=self._run_mingwang_claim_verify, args=(win,),
                         daemon=True, name="mingwang-claim-verify").start()
        return {"status": "success",
                "msg": "验证已开始：种族判定 → 前往大唐边境领取点 → 点击逆天魔 → 选择开启山河名望志 → 打开面板"}

    def _run_mingwang_claim_verify(self, win):
        """名望领取验证线程体：跑第一阶段领取入口链路，结果回推页面与日志窗。"""
        from ..tasks import mingwang  # 延迟导入：避免启动期加载任务模块
        try:
            self.add_logs(["[验证] 开始验证名望领取（种族判定 → 领取点 → 逆天魔 → 开启山河名望志 → 打开面板）..."])
            ok, info = mingwang.verify_claim_flow(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_mingwang_claim_verify_result(ok, info)
        self._verifying = False

    def _notify_mingwang_claim_verify_result(self, ok, info):
        """名望领取验证结果回推页面（诊断页-名望链路行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onMingwangClaimVerifyResult){{window.onMingwangClaimVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_mingwang_claim_task(self):
        """诊断页-名望-测试领取名望任务按钮：面板已打开则直接扫描任务栏
        识别并点击接取；未打开则先走第一阶段链路（种族判定 → 领取点 →
        打开面板）再领取。已实现高级任务「蛟魔复仇」「沙海迷踪」
        「沙都修罗」等，按级别优先级 + 卡片顺序选第一个可用的。
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
        threading.Thread(target=self._run_mingwang_claim_task_verify,
                         args=(win,), daemon=True,
                         name="mingwang-claim-task-verify").start()
        return {"status": "success",
                "msg": "验证已开始：扫描山河名望任务栏 → 识别已实现任务（蛟魔复仇/沙海迷踪/沙都修罗等）→ 点击接取 → 跟踪至完成"}

    def _run_mingwang_claim_task_verify(self, win):
        """名望任务领取验证线程体：真实点击接取，结果回推页面与日志窗。"""
        from ..tasks import mingwang  # 延迟导入：避免启动期加载任务模块
        try:
            self.add_logs(["[验证] 开始测试领取名望任务（识别任务栏 → 已实现任务（蛟魔复仇/沙海迷踪/沙都修罗等）→ 点击接取 → 跟踪至完成）..."])
            ok, info = mingwang.verify_claim_task_flow(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_mingwang_claim_task_verify_result(ok, info)
        self._verifying = False

    def _notify_mingwang_claim_task_verify_result(self, ok, info):
        """名望任务领取验证结果回推页面（诊断页-名望链路第二行）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onMingwangClaimTaskVerifyResult){{window.onMingwangClaimTaskVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_shout(self, target="wubei"):
        """高级设置-喊话测试：向游戏聊天发送一次喊话内容（验证发送
        链路；与验证按钮同款约束——任务运行中拒绝、游戏须前台）。

        target：wubei/xiuluo/zhongzu 分别发对应任务设置里保存的喊话内容；
        修罗的 {种族} 占位符按必须种族实时替换（_build_shout_text）。
        """
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if target == "xiuluo":
            from ..tasks import xiuluo  # 延迟导入：避免启动期加载任务模块
            text = xiuluo._build_shout_text()
        elif target == "zhongzu":
            text = load_zhongzu_shout_text()
        else:
            text = load_wubei_shout_text()
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        if not _game_window_clickable(win):
            return {"status": "error", "msg": "游戏窗口被其他窗口遮挡，请将游戏窗口置于前台后再验证"}
        threading.Thread(target=self._run_shout_test, args=(win, text),
                         daemon=True, name="shout-test").start()
        return {"status": "success", "msg": f"喊话测试已开始：{text}"}

    def _run_shout_test(self, win, text):
        """喊话测试线程体：激活游戏窗口 → Enter 打开聊天输入 → 输入
        内容 → Enter 发送（与任务内喊话同链路），结果推日志窗。"""
        try:
            self.add_logs([f"[喊话测试] 发送中: {text}"])
            win.activate()  # 键盘事件须游戏前台（不点击，避免误触世界）
            time.sleep(0.5)
            # 游戏聊天输入常驻（无需 Enter 打开）：直接输入 + Enter 发送
            Keyboard.input(text)
            time.sleep(0.8)  # 等待剪贴板粘贴完成（与任务内喊话同时序）
            Keyboard.key("enter")
            time.sleep(0.5)
            self.add_logs([f"[喊话测试] 已发送: {text}（请查看游戏公屏）"])
        except Exception as e:
            self.add_logs([f"[喊话测试] 发送失败: {e}"])

    def verify_clear_screen(self):
        """高级设置-清屏验证按钮：执行真实清屏流程（Alt+6 屏蔽非队伍
        玩家 + Alt+5 屏蔽摊位 + 收起游戏活动侧栏；结束时两个屏蔽均
        处于开启态、活动侧栏收起，与任务启动时的清屏结果一致）。
        流程自带游戏窗口激活与前台账校验，故不做遮挡检查（热键经前台
        窗口送达，而非点击屏幕坐标；活动侧栏点击走 human_click）。
        """
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        self._verifying = True
        threading.Thread(target=self._run_clear_screen_verify, args=(win,),
                         daemon=True, name="clear-screen-verify").start()
        return {"status": "success",
                "msg": "验证已开始：激活游戏 → Alt+6 屏蔽玩家 → Alt+5 屏蔽摊位 → 收起活动侧栏（结束均为屏蔽开启态+侧栏收起）"}

    def _run_clear_screen_verify(self, win):
        """清屏验证线程体：跑真实清屏流程，结果回推页面与日志窗。"""
        from ..device import clear_screen  # 延迟导入：与其余验证模块保持一致
        try:
            self.add_logs(["[验证] 开始验证清屏（Alt+6 屏蔽玩家 + Alt+5 屏蔽摊位 + 收起活动侧栏）..."])
            ok, info = clear_screen.run_clear_screen(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_clear_screen_verify_result(ok, info)
        self._verifying = False

    def _notify_clear_screen_verify_result(self, ok, info):
        """清屏验证结果回推页面（高级设置-清屏验证行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onClearScreenVerifyResult){{window.onClearScreenVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_jiguan(self):
        """高级设置-测试机关模板按钮：对当前画面逐一测试黄/蓝/红三个
        机关模板，只读不点击（截图+全帧匹配），打印最佳匹配度与位置
        日志供阈值/模板校准。只读操作不影响任务，任务运行中允许测试
        （封印场景只在任务流程中出现）。
        """
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        self._verifying = True
        threading.Thread(target=self._run_jiguan_verify, args=(win,),
                         daemon=True, name="jiguan-verify").start()
        return {"status": "success",
                "msg": "测试已开始：对当前画面测试三个机关模板，结果见日志"}

    def _run_jiguan_verify(self, win):
        """机关模板测试线程体：只读测试三个模板，结果回推页面与日志窗。"""
        from ..tasks import xuanshu  # 延迟导入：与其余验证模块保持一致
        try:
            self.add_logs(["[验证] 开始测试机关模板（黄/蓝/红，只读不点击）..."])
            ok, info = xuanshu.verify_jiguan_templates(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_jiguan_verify_result(ok, info)
        self._verifying = False

    def _notify_jiguan_verify_result(self, ok, info):
        """机关模板测试结果回推页面（高级设置-机关模板测试行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onJiguanVerifyResult){{window.onJiguanVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_team_check(self):
        """诊断-检查队伍信息按钮：走真实队伍检查逻辑（team.check_team_min
        完整链路：面板开合 → 队长判定 → 5 区域成员扫描），检查过程会
        真实点击组队按钮/面板开关/关闭按钮，结果输出到日志抽屉。
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
        threading.Thread(target=self._run_team_check_verify, args=(win,),
                         daemon=True, name="team-check-verify").start()
        return {"status": "success",
                "msg": "检查已开始：队伍面板开合 → 队长判定 → 成员扫描，结果见日志"}

    def _ensure_bound_tab(self, win):
        """诊断线程前置护栏：游戏被手动切到别的 Tab 时先切回绑定角色
        Tab（错误 Tab 上执行检查/请离会操作错账号——用户 2026-11-07
        实测：手动切到队员 B 后点诊断在 B 上执行）。返回 (是否放行,
        描述)，False 时调用方放弃本轮并回推失败。"""
        from ..device import tab_group  # 延迟导入：与其余验证模块保持一致
        ok, info = tab_group.ensure_bound_tab(win)
        try:
            self.add_logs([f"[验证] {info}"])
        except Exception:
            pass
        return ok, info

    def _run_team_check_verify(self, win):
        """检查队伍信息线程体：真实 check_team_min 链路，结果打印日志。"""
        from ..device import team  # 延迟导入：与其余验证模块保持一致
        ok_tab, tab_info = self._ensure_bound_tab(win)
        if not ok_tab:
            self._notify_team_check_verify_result(False, tab_info)
            self._verifying = False
            return
        try:
            self.add_logs(["[验证] 开始检查队伍信息（组队菜单 → 队伍面板 → "
                           "队长判定 → 成员扫描 → 关闭）..."])
            status, info = team.diagnose_team_check(win)
            # 检查完成即有确定性结论（ready/wait/非队长/未组队）均算通过；
            # 「队长无法判定」等瞬态失败才算不通过
            ok = "队长无法判定" not in info and "截图失败" not in info
            self.add_logs([f"[验证] 队伍检查结论: {status} - {info}"])
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
            try:
                self.add_logs([f"[验证] 队伍检查异常: {info}"])
            except Exception:
                pass
        self._notify_team_check_verify_result(ok, info)
        self._verifying = False

    def _notify_team_check_verify_result(self, ok, info):
        """检查队伍信息结果回推页面（诊断-检查队伍信息行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onTeamCheckVerifyResult){{window.onTeamCheckVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_team_create(self):
        """诊断-创建队伍按钮：真实执行创建链路——组队菜单 → 队伍信息
        判定（已组队直接返回）→ 点击「创建队伍」→ 校验队伍面板打开并
        关闭。未组队时会真实创建队伍（点组队按钮/菜单创建队伍项），
        仅手动测试/确认识别用。
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
        threading.Thread(target=self._run_team_create_verify, args=(win,),
                         daemon=True, name="team-create-verify").start()
        return {"status": "success",
                "msg": "检查已开始：组队菜单 → 创建队伍 → 面板校验，结果见日志"}

    def _run_team_create_verify(self, win):
        """创建队伍线程体：ensure_team_created 链路，结果打印日志并回推页面。"""
        from ..device import team  # 延迟导入：与其余验证模块保持一致
        ok_tab, tab_info = self._ensure_bound_tab(win)
        if not ok_tab:
            self._notify_team_create_verify_result(False, tab_info)
            self._verifying = False
            return
        try:
            self.add_logs(["[验证] 开始创建队伍（组队菜单 → 队伍信息判定 → "
                           "创建队伍 → 面板校验 → 关闭）..."])
            ok, info = team.ensure_team_created(win, prefix="[验证]")
            self.add_logs([f"[验证] 创建队伍结论: {info}"])
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
            try:
                self.add_logs([f"[验证] 创建队伍异常: {info}"])
            except Exception:
                pass
        self._notify_team_create_verify_result(ok, info)
        self._verifying = False

    def _notify_team_create_verify_result(self, ok, info):
        """创建队伍结果回推页面（诊断-创建队伍行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onTeamCreateVerifyResult){{window.onTeamCreateVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_zuoqi_open(self):
        """诊断-打开飞行驭器面板按钮：真实执行打开链路（座骑按钮 →
        菜单「飞行驭器」→ 面板校验；主进程无高性能开关，恒识别路径，
        战斗中拒绝）。全程真实点击，仅手动测试/确认识别用。
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
        threading.Thread(target=self._run_zuoqi_open_verify, args=(win,),
                         daemon=True, name="zuoqi-open-verify").start()
        return {"status": "success",
                "msg": "检查已开始：座骑按钮 → 菜单「飞行驭器」 → 面板校验，结果见日志"}

    def _run_zuoqi_open_verify(self, win):
        """打开飞行驭器面板线程体：zuoqi.open_feixingyuqi_panel 链路。"""
        from ..device import zuoqi  # 延迟导入：与其余验证模块保持一致
        ok_tab, tab_info = self._ensure_bound_tab(win)
        if not ok_tab:
            self._notify_zuoqi_open_verify_result(False, tab_info)
            self._verifying = False
            return
        try:
            self.add_logs(["[验证] 开始打开飞行驭器面板（非战斗校验 → 座骑按钮 → "
                           "菜单「飞行驭器」 → 面板校验）..."])
            ok, info = zuoqi.open_feixingyuqi_panel(win)
            self.add_logs([f"[验证] 打开飞行驭器面板结论: {info}"])
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
            try:
                self.add_logs([f"[验证] 打开飞行驭器面板异常: {info}"])
            except Exception:
                pass
        self._notify_zuoqi_open_verify_result(ok, info)
        self._verifying = False

    def _notify_zuoqi_open_verify_result(self, ok, info):
        """打开飞行驭器面板结果回推页面（诊断-打开飞行驭器面板行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onZuoqiOpenVerifyResult){{window.onZuoqiOpenVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_zuoqi_close(self):
        """诊断-关闭飞行驭器面板按钮：真实执行关闭链路（面板校验 →
        点关闭按钮 → 校验面板消失）。关闭按钮与队伍面板关闭按钮同款，
        关闭前必须先校验飞行驭器面板打开，防止点到其他面板的同款按钮。
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
        threading.Thread(target=self._run_zuoqi_close_verify, args=(win,),
                         daemon=True, name="zuoqi-close-verify").start()
        return {"status": "success",
                "msg": "检查已开始：面板校验 → 点关闭按钮 → 面板消失校验，结果见日志"}

    def _run_zuoqi_close_verify(self, win):
        """关闭飞行驭器面板线程体：zuoqi.close_feixingyuqi_panel 链路。"""
        from ..device import zuoqi  # 延迟导入：与其余验证模块保持一致
        ok_tab, tab_info = self._ensure_bound_tab(win)
        if not ok_tab:
            self._notify_zuoqi_close_verify_result(False, tab_info)
            self._verifying = False
            return
        try:
            self.add_logs(["[验证] 开始关闭飞行驭器面板（面板校验 → 关闭按钮 → "
                           "面板消失校验）..."])
            ok, info = zuoqi.close_feixingyuqi_panel(win)
            self.add_logs([f"[验证] 关闭飞行驭器面板结论: {info}"])
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
            try:
                self.add_logs([f"[验证] 关闭飞行驭器面板异常: {info}"])
            except Exception:
                pass
        self._notify_zuoqi_close_verify_result(ok, info)
        self._verifying = False

    def _notify_zuoqi_close_verify_result(self, ok, info):
        """关闭飞行驭器面板结果回推页面（诊断-关闭飞行驭器面板行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onZuoqiCloseVerifyResult){{window.onZuoqiCloseVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_zuoqi_fly(self):
        """诊断-测试飞行按钮：真实执行起飞链路（面板打开/复用 → 面板内
        双模板比分数判飞行状态 → 未飞行点「飞行」按钮 → 校验降落按钮
        出现即起飞）。已在飞行中直接返回成功。全程真实点击。
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
        threading.Thread(target=self._run_zuoqi_fly_verify, args=(win,),
                         daemon=True, name="zuoqi-fly-verify").start()
        return {"status": "success",
                "msg": "检查已开始：面板 → 飞行状态判定 → 起飞 → 降落按钮校验，结果见日志"}

    def _run_zuoqi_fly_verify(self, win):
        """测试飞行线程体：zuoqi.ensure_flying 链路。"""
        from ..device import zuoqi  # 延迟导入：与其余验证模块保持一致
        ok_tab, tab_info = self._ensure_bound_tab(win)
        if not ok_tab:
            self._notify_zuoqi_fly_verify_result(False, tab_info)
            self._verifying = False
            return
        try:
            self.add_logs(["[验证] 开始测试飞行（面板 → 双模板飞行状态判定 → "
                           "未飞行点「飞行」→ 降落按钮校验）..."])
            ok, info = zuoqi.ensure_flying(win)
            self.add_logs([f"[验证] 测试飞行结论: {info}"])
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
            try:
                self.add_logs([f"[验证] 测试飞行异常: {info}"])
            except Exception:
                pass
        self._notify_zuoqi_fly_verify_result(ok, info)
        self._verifying = False

    def _notify_zuoqi_fly_verify_result(self, ok, info):
        """测试飞行结果回推页面（诊断-测试飞行行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onZuoqiFlyVerifyResult){{window.onZuoqiFlyVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_zuoqi_land(self):
        """诊断-测试降落按钮：真实执行降落链路（面板打开/复用 → 面板内
        双模板比分数判飞行状态 → 飞行中点「降落」按钮 → 校验飞行按钮
        出现即降落）。已降落直接返回成功。全程真实点击。
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
        threading.Thread(target=self._run_zuoqi_land_verify, args=(win,),
                         daemon=True, name="zuoqi-land-verify").start()
        return {"status": "success",
                "msg": "检查已开始：面板 → 飞行状态判定 → 降落 → 飞行按钮校验，结果见日志"}

    def _run_zuoqi_land_verify(self, win):
        """测试降落线程体：zuoqi.ensure_landed 链路。"""
        from ..device import zuoqi  # 延迟导入：与其余验证模块保持一致
        ok_tab, tab_info = self._ensure_bound_tab(win)
        if not ok_tab:
            self._notify_zuoqi_land_verify_result(False, tab_info)
            self._verifying = False
            return
        try:
            self.add_logs(["[验证] 开始测试降落（面板 → 双模板飞行状态判定 → "
                           "飞行中点「降落」→ 飞行按钮校验）..."])
            ok, info = zuoqi.ensure_landed(win)
            self.add_logs([f"[验证] 测试降落结论: {info}"])
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
            try:
                self.add_logs([f"[验证] 测试降落异常: {info}"])
            except Exception:
                pass
        self._notify_zuoqi_land_verify_result(ok, info)
        self._verifying = False

    def _notify_zuoqi_land_verify_result(self, ok, info):
        """测试降落结果回推页面（诊断-测试降落行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onZuoqiLandVerifyResult){{window.onZuoqiLandVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_rejoin(self):
        """诊断-测试归队按钮：读取当前位置判定是否在轮回司（打日志），
        在轮回司则直接走 team.maybe_rejoin 自动归队链路（点「回归队伍」
        按钮传送回队长）。测试流程由用户手动操作、无法模拟战斗结束——
        按用户约定假设战斗已结束（maybe_rejoin 的战斗判定本就在调用方，
        此处不判战斗）。全程真实点击（组队按钮/队伍面板/归队按钮）。
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
        threading.Thread(target=self._run_rejoin_verify, args=(win,),
                         daemon=True, name="rejoin-verify").start()
        return {"status": "success",
                "msg": "测试已开始：读取当前位置 → 轮回司判定 → 自动归队，结果见日志"}

    def _run_rejoin_verify(self, win):
        """测试归队线程体：位置判定 + maybe_rejoin 链路，结果打印日志并回推页面。"""
        from ..device import team  # 延迟导入：与其余验证模块保持一致
        from ..device import map as game_map  # 位置读取（轮回司判定）
        ok_tab, tab_info = self._ensure_bound_tab(win)
        if not ok_tab:
            self._notify_rejoin_verify_result(False, tab_info)
            self._verifying = False
            return
        ok, info = False, ""
        try:
            self.add_logs(["[验证] 开始测试归队（读取当前位置 → 轮回司判定 → "
                           "自动归队，按约定假设战斗已结束）..."])
            pos = game_map.read_map_position(win)
            if pos is None:
                info = "位置读取失败（无法判定是否在轮回司），未执行归队"
                self.add_logs([f"[验证] {info}"])
            elif not team.is_in_lunhuisi(pos):
                # 判定链路正常即算通过：不在轮回司是场景不对（归队测试
                # 须死亡后落轮回司再点），不算链路失败
                info = (f"当前位置: {pos[0]}[{pos[1]},{pos[2]}]，不在轮回司，"
                        f"未执行归队（归队测试须死亡后落轮回司再点）")
                self.add_logs([f"[验证] {info}"])
                ok = True
            else:
                self.add_logs([f"[验证] 当前位置: {pos[0]}[{pos[1]},{pos[2]}]，"
                               f"在轮回司，开始自动归队..."])
                handled, desc = team.maybe_rejoin(win, prefix="[验证]")
                info = desc
                ok = handled and desc.startswith("已归队")
                self.add_logs([f"[验证] 归队结果: {desc}"])
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
            try:
                self.add_logs([f"[验证] 归队测试异常: {info}"])
            except Exception:
                pass
        self._notify_rejoin_verify_result(ok, info)
        self._verifying = False

    def _notify_rejoin_verify_result(self, ok, info):
        """测试归队结果回推页面（诊断-队伍卡片测试归队行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onRejoinVerifyResult){{window.onRejoinVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_team_away(self):
        """诊断-检查队伍暂离按钮：打开队伍面板逐成员区域扫描「召回」
        标记（出现即该队员暂离），结果输出到日志抽屉。全程真实点击
        （组队按钮/面板开关），不改变队伍状态（不请离）。"""
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
        threading.Thread(target=self._run_team_away_verify, args=(win,),
                         daemon=True, name="team-away-verify").start()
        return {"status": "success",
                "msg": "检查已开始：打开队伍面板 → 逐成员区域扫描召回标记，结果见日志"}

    def _run_team_away_verify(self, win):
        """检查队伍暂离线程体：scan_away_members 链路，结果打印日志并回推页面。"""
        from ..device import team  # 延迟导入：与其余验证模块保持一致
        ok_tab, tab_info = self._ensure_bound_tab(win)
        if not ok_tab:
            self._notify_team_away_verify_result(False, tab_info)
            self._verifying = False
            return
        try:
            self.add_logs(["[验证] 开始检查队伍暂离（开面板 → 逐成员区域找召回标记"
                           "「召回」→ 关闭面板）..."])
            ok, _idxs, info = team.scan_away_members(win)
            self.add_logs([f"[验证] 暂离检查结论: {info}"])
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
            try:
                self.add_logs([f"[验证] 暂离检查异常: {info}"])
            except Exception:
                pass
        self._notify_team_away_verify_result(ok, info)
        self._verifying = False

    def _notify_team_away_verify_result(self, ok, info):
        """检查队伍暂离结果回推页面（诊断-检查队伍暂离行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onTeamAwayVerifyResult){{window.onTeamAwayVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_team_kick_away(self):
        """诊断-请离暂离成员按钮：真实执行请离链路——点选暂离成员 →
        「请离队伍」按钮 → 请离。会真实踢出队员，仅手动测试/确认识别
        链路用；一次调用至多请离一名（队首暂离成员）。"""
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
        threading.Thread(target=self._run_team_kick_away_verify, args=(win,),
                         daemon=True, name="team-kick-away-verify").start()
        return {"status": "success",
                "msg": "请离已开始：点选暂离成员 → 请离队伍按钮（会真实踢人，一次一名），结果见日志"}

    def _run_team_kick_away_verify(self, win):
        """请离暂离成员线程体：kick_away_member 链路，结果打印日志并回推页面。"""
        from ..device import team  # 延迟导入：与其余验证模块保持一致
        ok_tab, tab_info = self._ensure_bound_tab(win)
        if not ok_tab:
            self._notify_team_kick_away_verify_result(False, tab_info)
            self._verifying = False
            return
        try:
            self.add_logs(["[验证] 开始请离暂离成员（找召回标记 → 点选成员 → "
                           "请离队伍按钮 → 校验，会真实踢人）..."])
            ok, info = team.kick_away_member(win)
            self.add_logs([f"[验证] 请离结果: {info}"])
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
            try:
                self.add_logs([f"[验证] 请离异常: {info}"])
            except Exception:
                pass
        self._notify_team_kick_away_verify_result(ok, info)
        self._verifying = False

    def _notify_team_kick_away_verify_result(self, ok, info):
        """请离暂离成员结果回推页面（诊断-请离暂离成员行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onTeamKickAwayVerifyResult){{window.onTeamKickAwayVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_group_rejoin(self):
        """诊断-组号自动归队按钮：先检查队伍暂离（面板开合扫描召回标记+
        昵称），暂离成员匹配本组号队员 Tab 则逐个切过去替其点「回归
        队伍」归队。主进程无任务模式，走 init_task 枚举 + ready() 校验
        + member_rejoin_pass(force=True)。全程真实点击与真实切 Tab。
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
        threading.Thread(target=self._run_group_rejoin_verify, args=(win,),
                         daemon=True, name="group-rejoin-verify").start()
        return {"status": "success",
                "msg": "归队已开始：检查暂离 → 匹配队员 Tab → 切过去点归队，结果见日志"}

    def _run_group_rejoin_verify(self, win):
        """组号自动归队线程体：检查暂离 → 匹配队员 Tab → 切过去归队。"""
        from ..device import team  # 延迟导入：与其余验证模块保持一致
        from ..device import tab_group  # 组号枚举/切换（主进程走 force 路径）
        ok_tab, tab_info = self._ensure_bound_tab(win)
        if not ok_tab:
            self._notify_group_rejoin_verify_result(False, tab_info)
            self._verifying = False
            return
        try:
            self.add_logs(["[验证] 开始组号自动归队（检查暂离 → 匹配队员 Tab → "
                           "切过去点归队）..."])
            ok, away, info = team.scan_away_members(win)
            self.add_logs([f"[验证] 暂离检查结论: {info}"])
            if not ok:
                return_info = info
            elif not away:
                # 检查链路正常即算通过（无暂离成员 = 无归队对象）
                return_info = "无暂离成员，无需归队"
                ok = True
            else:
                # 主进程无任务模式（active() 不可用）：init_task 枚举 Tab +
                # ready() 校验 + force pass（与组号巫医验证同款）
                tab_group.init_task(win, "修罗", True)
                if not tab_group.ready():
                    return_info = (f"{info}；组号模式未激活"
                                   f"（需窗口内 ≥2 个已登录 Tab），无法自动归队")
                    ok = False
                else:
                    done, pass_info, _matched = tab_group.member_rejoin_pass(
                        win, [n for _i, n, _r in away if n], force=True)
                    if pass_info:
                        self.add_logs([f"[验证] {pass_info}"])
                    if done:
                        return_info = pass_info
                    else:
                        return_info = pass_info or (f"{info}；暂离成员与本组号"
                                                    f"队员 Tab 无匹配，无法自动归队")
        except Exception as e:
            ok, return_info = False, f"验证异常: {e}"
            try:
                self.add_logs([f"[验证] 组号归队异常: {return_info}"])
            except Exception:
                pass
        self._notify_group_rejoin_verify_result(ok, return_info)
        self._verifying = False

    def _notify_group_rejoin_verify_result(self, ok, info):
        """组号自动归队结果回推页面（诊断-组号自动归队行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onGroupRejoinVerifyResult){{window.onGroupRejoinVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_position(self):
        """高级设置-获取当前坐标按钮：读取当前地图位置（只读，不按键
        不点击），结果回推页面并打日志。只读操作不影响任务，任务
        运行中允许使用。
        """
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        self._verifying = True
        threading.Thread(target=self._run_position_verify, args=(win,),
                         daemon=True, name="position-verify").start()
        return {"status": "success", "msg": "正在读取当前坐标，结果见下方与日志"}

    def _run_position_verify(self, win):
        """获取当前坐标线程体：只读位置识别，结果回推页面与日志窗。"""
        from ..device import map as game_map  # 延迟导入：与其余验证模块保持一致
        try:
            ok, info = game_map.verify_map_position(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_position_verify_result(ok, info)
        self._verifying = False

    def _notify_position_verify_result(self, ok, info):
        """获取当前坐标结果回推页面（高级设置-获取当前坐标行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onPositionVerifyResult){{window.onPositionVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_dialog_detect(self):
        """诊断-测试对话框识别按钮：单帧模板判定对话框在场 + 对话区
        OCR 计时（只读不点击）。有/无对话框场景各点一次，对照日志
        中的模板分数/耗时与 OCR 耗时——验证「模板未命中即跳过对话
        OCR」方案（模板 res/img/dialog_frame.png）。任务运行中允许
        使用（只读操作不影响任务）。
        """
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        self._verifying = True
        threading.Thread(target=self._run_dialog_detect_verify, args=(win,),
                         daemon=True, name="dialog-detect-verify").start()
        return {"status": "success",
                "msg": "正在测试对话框识别（模板匹配+对话区OCR计时），结果见下方与日志"}

    def _run_dialog_detect_verify(self, win):
        """对话框识别测试线程体：只读模板匹配+OCR 计时，结果回推页面与日志窗。"""
        from ..device import dialog_common  # 延迟导入：与其余验证模块保持一致
        # 奇遇补充门评分由 app 层注入（dialog_common 不得反向依赖任务
        # 模块）；注入失败不影响基础对话框诊断
        extras = {}
        try:
            from ..tasks import kezhan
            extras = dict(extra_score=kezhan.qiyu_frame_score,
                          extra_threshold=kezhan.QIYU_FRAME_THRESHOLD,
                          extra_label="客栈奇遇")
        except Exception:
            pass
        try:
            ok, info = dialog_common.verify_dialog_detect(win, **extras)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_dialog_detect_verify_result(ok, info)
        self._verifying = False

    def _notify_dialog_detect_verify_result(self, ok, info):
        """对话框识别测试结果回推页面（诊断-测试对话框识别行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onDialogDetectVerifyResult){{window.onDialogDetectVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_apply_panel(self):
        """诊断-测试申请加入按钮：单帧模板匹配申请加入面板 + 申请人行
        识别（只读不点击，任务运行中可用）。请打开申请加入面板（有人
        申请时点组队按钮出现）后点一次，对照日志中的模板分数/匹配框
        与识别出的申请人行（名字+等级+点击点坐标）是否与面板实际一致。
        """
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        self._verifying = True
        threading.Thread(target=self._run_apply_panel_verify, args=(win,),
                         daemon=True, name="apply-panel-verify").start()
        return {"status": "success",
                "msg": "正在测试申请加入面板识别（模板匹配+申请人行OCR），结果见下方与日志"}

    def _run_apply_panel_verify(self, win):
        """测试申请加入线程体：只读模板匹配+申请人行 OCR，结果回推页面与日志窗。"""
        from ..device import team  # 延迟导入：与其余验证模块保持一致
        try:
            ok, info = team.verify_apply_panel(win)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_apply_panel_verify_result(ok, info)
        self._verifying = False

    def _notify_apply_panel_verify_result(self, ok, info):
        """测试申请加入结果回推页面（诊断-测试申请加入行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onApplyPanelVerifyResult){{window.onApplyPanelVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_capture_diag(self):
        """高级设置-截屏通道诊断按钮：对比 PrintWindow(3)/PrintWindow(0)/
        窗口 BitBlt/全屏 BitBlt 四条截屏通道并记录环境事实（显卡/DWM/
        进程提权/兼容模式），结果全部写入日志（本地日志文件 + 日志窗）。
        只读不点击；任务未运行时额外尝试把游戏窗口置前复测 A/C（任务
        运行中跳过激活，避免抢前台干扰）。
        """
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        self._verifying = True
        allow_activate = not self._tm.is_running()
        threading.Thread(target=self._run_capture_diag, args=(win, allow_activate),
                         daemon=True, name="capture-diag").start()
        return {"status": "success",
                "msg": "诊断已开始：四通道截图对比与环境事实将写入日志（约 10 秒）"}

    def _run_capture_diag(self, win, allow_activate):
        """截屏通道诊断线程体：逐行写入本地日志文件与日志窗，结束回推页面。"""
        from . import capture_diag  # 延迟导入：与其余验证模块保持一致

        def emit(msg):
            app_utils.log(f"[诊断] {msg}")
            try:
                self.add_logs([f"[诊断] {msg}"])
            except Exception:
                pass

        try:
            ok, info = capture_diag.run_diag(win.hwnd, emit,
                                             allow_activate=allow_activate)
        except Exception as e:
            ok, info = False, f"诊断异常: {e}"
        try:
            app_utils.log(f"[诊断] 结果: {info}")
            self.add_logs([f"[诊断] 结果: {info}"])
        except Exception:
            pass
        self._notify_capture_diag_result(ok, info)
        self._verifying = False

    def _notify_capture_diag_result(self, ok, info):
        """截屏通道诊断结果回推页面（高级设置-截屏通道诊断行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onCaptureDiagVerifyResult){{window.onCaptureDiagVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_silent_click(self, button="primary", x=None, y=None, action=None):
        """诊断-静默点击按钮：不移动鼠标、不激活窗口，向游戏窗口投递
        一次消息点击，验证游戏窗口不在前台时是否响应后台点击（经典版
        客户端曾实测不消费合成鼠标消息，多标签客户端下重新验证）。
        button：'primary' 左键 / 'secondary' 右键（右键点击世界会弹
        操作菜单，同为生效信号）。x/y 为窗口坐标（human_click 同空间，
        如静默回蓝右键血蓝条 (1316,105)）：给了 x/y 只投递不打分，
        结果由用户自行观察；不给时点画面中心并对比前后画面差异。
        action：点位按钮的结果回推路由（'mp' 静默回蓝 / 'hp' 静默回血），
        不给时回推到中心点击诊断的对应回调。
        """
        if button not in ("primary", "secondary"):
            return {"status": "error", "msg": "不支持的按键类型（primary/secondary）"}
        if action not in (None, "mp", "hp"):
            return {"status": "error", "msg": "不支持的按钮类型（mp/hp）"}
        if (x is None) != (y is None):
            return {"status": "error", "msg": "坐标 x/y 须同时提供"}
        try:
            x = int(x) if x is not None else None
            y = int(y) if y is not None else None
        except (TypeError, ValueError):
            return {"status": "error", "msg": "坐标必须是整数"}
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        self._verifying = True
        threading.Thread(target=self._run_silent_click_verify,
                         args=(win, button, x, y, action),
                         daemon=True, name="silent-click-verify").start()
        if x is not None and y is not None:
            return {"status": "success",
                    "msg": f"已开始：投递{('右键' if button == 'secondary' else '左键')}"
                           f"点击 窗口({x},{y})，请自行观察游戏效果"}
        return {"status": "success",
                "msg": "验证已开始：投递一次消息点击并对比前后画面（约 4 秒）"}

    def _run_silent_click_verify(self, win, button, x, y, action):
        """静默点击验证线程体：逐行写入本地日志文件与日志窗，结束回推页面。"""
        from ..device import silent_click  # 延迟导入：与其余验证模块保持一致

        def emit(msg):
            app_utils.log(f"[诊断] {msg}")
            try:
                self.add_logs([f"[诊断] {msg}"])
            except Exception:
                pass

        try:
            if x is not None and y is not None:
                ok, info = silent_click.send_and_report(win, emit, button=button, x=x, y=y)
            else:
                ok, info = silent_click.run_diag(win, emit, button=button)
        except Exception as e:
            ok, info = False, f"诊断异常: {e}"
        try:
            app_utils.log(f"[诊断] 结果: {info}")
            self.add_logs([f"[诊断] 结果: {info}"])
        except Exception:
            pass
        self._notify_silent_click_result(ok, info, button, action)
        self._verifying = False

    def verify_silent_key(self, hotkey="altc"):
        """诊断-静默按键按钮：向游戏窗口投递一次组合键消息（不移动
        鼠标、不激活窗口），验证键盘消息通道可行性。hotkey 白名单：
        altc（降落/位置刷新）/ alte（开包）/ alt5+alt6（清屏屏蔽）/
        ctrltab（切 Tab）/ enter（聊天输入开关）。结果用户自判。
        """
        entry = _SILENT_KEY_HOTKEYS.get(hotkey)
        if entry is None:
            return {"status": "error", "msg": f"不支持的按键类型: {hotkey}"}
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        self._verifying = True
        threading.Thread(target=self._run_silent_key_verify, args=(win, hotkey),
                         daemon=True, name="silent-key-verify").start()
        return {"status": "success",
                "msg": f"已开始：投递一次 {entry[2]} 按键消息，请自行观察游戏效果"}

    def _run_silent_key_verify(self, win, hotkey):
        """静默按键验证线程体：逐行写入本地日志文件与日志窗，结束回推页面。"""
        from ..device import silent_click  # 延迟导入：与其余验证模块保持一致

        def emit(msg):
            app_utils.log(f"[诊断] {msg}")
            try:
                self.add_logs([f"[诊断] {msg}"])
            except Exception:
                pass

        try:
            modifiers, key, _label, main_window, char = _SILENT_KEY_HOTKEYS[hotkey]
            ok, info = silent_click.send_and_report_key(
                win, emit, modifiers, key, main_window=main_window, char=char)
        except Exception as e:
            ok, info = False, f"诊断异常: {e}"
        try:
            app_utils.log(f"[诊断] 结果: {info}")
            self.add_logs([f"[诊断] 结果: {info}"])
        except Exception:
            pass
        self._notify_silent_key_result(ok, info, hotkey)
        self._verifying = False

    def verify_silent_paste(self):
        """诊断-静默粘贴按钮：剪贴板写入「测试粘贴」后向游戏窗口
        投递 WM_PASTE（不移动鼠标、不激活窗口），验证文本输入通道
        可行性——游戏聊天输入框出现粘贴文本即生效（前提：先手动
        点开聊天输入框）。结果用户自判。
        """
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        self._verifying = True
        threading.Thread(target=self._run_silent_paste_verify, args=(win,),
                         daemon=True, name="silent-paste-verify").start()
        return {"status": "success",
                "msg": "已开始：投递一次 WM_PASTE，请自行观察游戏输入框"}

    def _run_silent_paste_verify(self, win):
        """静默粘贴验证线程体：逐行写入本地日志文件与日志窗，结束回推页面。"""
        from ..device import silent_click  # 延迟导入：与其余验证模块保持一致

        def emit(msg):
            app_utils.log(f"[诊断] {msg}")
            try:
                self.add_logs([f"[诊断] {msg}"])
            except Exception:
                pass

        try:
            ok, info = silent_click.send_and_report_paste(win, emit)
        except Exception as e:
            ok, info = False, f"诊断异常: {e}"
        try:
            app_utils.log(f"[诊断] 结果: {info}")
            self.add_logs([f"[诊断] 结果: {info}"])
        except Exception:
            pass
        self._notify_silent_paste_result(ok, info)
        self._verifying = False

    def verify_silent_unichar(self):
        """诊断-静默字符按钮：向游戏窗口投递 WM_UNICHAR「测」字符
        消息（不移动鼠标、不激活窗口），验证 Unicode 字符注入通道
        可行性——游戏聊天输入框出现「测」即生效（前提：先手动点开
        聊天输入框）。结果用户自判。
        """
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        self._verifying = True
        threading.Thread(target=self._run_silent_unichar_verify, args=(win,),
                         daemon=True, name="silent-unichar-verify").start()
        return {"status": "success",
                "msg": "已开始：投递 WM_UNICHAR「测」，请自行观察游戏输入框"}

    def _run_silent_unichar_verify(self, win):
        """静默字符验证线程体：逐行写入本地日志文件与日志窗，结束回推页面。"""
        from ..device import silent_click  # 延迟导入：与其余验证模块保持一致

        def emit(msg):
            app_utils.log(f"[诊断] {msg}")
            try:
                self.add_logs([f"[诊断] {msg}"])
            except Exception:
                pass

        try:
            ok, info = silent_click.send_and_report_unichar(win, emit)
        except Exception as e:
            ok, info = False, f"诊断异常: {e}"
        try:
            app_utils.log(f"[诊断] 结果: {info}")
            self.add_logs([f"[诊断] 结果: {info}"])
        except Exception:
            pass
        self._notify_silent_unichar_result(ok, info)
        self._verifying = False

    def _notify_silent_unichar_result(self, ok, info):
        """静默字符验证结果回推页面（诊断-静默字符行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onSilentUnicharVerifyResult){{window.onSilentUnicharVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_silent_char(self):
        """诊断-静默字符 WM_CHAR 按钮：向游戏窗口投递 WM_CHAR「测」
        字符消息（不移动鼠标、不激活窗口）。游戏已实证消费 WM_CHAR
        （Enter+WM_CHAR 生效），中文/数字可能同样被其输入系统消费
        ——聊天输入框出现「测」即生效（前提：先手动点开输入框）。
        """
        if self._tm.is_running():
            return {"status": "error", "msg": "任务运行中，请先停止任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        self._verifying = True
        threading.Thread(target=self._run_silent_char_verify, args=(win,),
                         daemon=True, name="silent-char-verify").start()
        return {"status": "success",
                "msg": "已开始：投递 WM_CHAR「测」，请自行观察游戏输入框"}

    def _run_silent_char_verify(self, win):
        """静默字符 WM_CHAR 验证线程体：逐行写入本地日志文件与日志窗。"""
        from ..device import silent_click  # 延迟导入：与其余验证模块保持一致

        def emit(msg):
            app_utils.log(f"[诊断] {msg}")
            try:
                self.add_logs([f"[诊断] {msg}"])
            except Exception:
                pass

        try:
            ok, info = silent_click.send_and_report_char(win, emit)
        except Exception as e:
            ok, info = False, f"诊断异常: {e}"
        try:
            app_utils.log(f"[诊断] 结果: {info}")
            self.add_logs([f"[诊断] 结果: {info}"])
        except Exception:
            pass
        self._notify_silent_char_result(ok, info)
        self._verifying = False

    def _notify_silent_char_result(self, ok, info):
        """静默字符 WM_CHAR 验证结果回推页面（诊断-静默字符行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onSilentCharVerifyResult){{window.onSilentCharVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_child_windows(self):
        """诊断-子窗口枚举按钮：枚举游戏主窗口下所有可见子窗口
        （只读，不点击不按键），逐行写入日志——定位聊天输入框等
        子控件，为静默文本输入通道排查提供依据。
        """
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        self._verifying = True
        threading.Thread(target=self._run_child_windows_dump, args=(win,),
                         daemon=True, name="child-windows-dump").start()
        return {"status": "success",
                "msg": "已开始：枚举游戏窗口可见子窗口（只读，结果写入日志）"}

    def _run_child_windows_dump(self, win):
        """子窗口枚举线程体：逐行写入本地日志文件与日志窗，结束回推页面。"""
        try:
            self.add_logs(["[诊断] 开始枚举游戏窗口可见子窗口..."])
            for row in game.dump_child_windows(win):
                app_utils.log(f"[诊断] {row}")
                try:
                    self.add_logs([f"[诊断] {row}"])
                except Exception:
                    pass
            ok, info = True, "子窗口枚举完成（结果见日志）"
        except Exception as e:
            ok, info = False, f"诊断异常: {e}"
        self._notify_child_windows_result(ok, info)
        self._verifying = False

    def _notify_child_windows_result(self, ok, info):
        """子窗口枚举结果回推页面（诊断-子窗口枚举行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onChildWindowsVerifyResult){{window.onChildWindowsVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def _notify_silent_paste_result(self, ok, info):
        """静默粘贴验证结果回推页面（诊断-静默粘贴行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onSilentPasteVerifyResult){{window.onSilentPasteVerifyResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def _notify_silent_key_result(self, ok, info, hotkey):
        """静默按键验证结果回推页面（按 hotkey 分发到对应按钮的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onSilentKeyVerifyResult){{window.onSilentKeyVerifyResult"
                    f"({ok_js}, {json.dumps(info)}, {json.dumps(hotkey)});}}")
            except Exception:
                pass

    def _notify_silent_click_result(self, ok, info, button="primary", action=None):
        """静默点击验证结果回推页面（诊断-静默点击行的结果文案；
        action 点位按钮路由：mp=回蓝 / hp=回血；无 action 时按
        左键/右键回推到中心点击诊断各自的回调）。"""
        if self.webview_instance:
            try:
                if action == "mp":
                    handler = "onSilentMpVerifyResult"
                elif action == "hp":
                    handler = "onSilentHpVerifyResult"
                elif button == "secondary":
                    handler = "onSilentClickRightVerifyResult"
                else:
                    handler = "onSilentClickVerifyResult"
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.{handler}){{window.{handler}({ok_js}, {json.dumps(info)});}}")
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
        from ..tasks import xiuluo  # 延迟导入：避免启动期加载任务模块
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
        """单行日志推送到面板日志抽屉（主进程 [系统] 消息用）。"""
        self.add_logs([line])

    def add_logs(self, lines):
        """批量日志推送到主面板看板日志抽屉。"""
        self._push_panel_logs(lines)

    def _push_panel_logs(self, lines):
        """主面板看板日志抽屉推送（页面未就绪时暂存、首屏加载补投；行数上限由页面截断）。"""
        if not self._panel_ready:
            self._pending_logs.extend(lines)
            if len(self._pending_logs) > 500:  # 防御：长期不就绪也不膨胀
                del self._pending_logs[:len(self._pending_logs) - 500]
            return
        if self.webview_instance:
            try:
                self.webview_instance.evaluate_js(
                    f"if(window.addPanelLogs){{window.addPanelLogs({json.dumps(lines)});}}")
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
        if self._visible:
            self.refresh_window_title()  # 重新显示时刷新角色文案（窗口解析可能已变化）

    def refresh_window_title(self):
        """刷新窗口标题的角色短文案。

        绑定丢失时 find_main_window 自动选择新窗口（已登录优先），
        实际运行目标变化而标题若停在启动时的旧绑定角色会误导——
        在环境刷新/任务启动/托盘显示时重解析刷新（set_title 在窗口
        shown 前为 no-op，此时仅更新内存值，创建窗口时仍用新值）。
        """
        label = bound_short_label()
        new_title = f"{self._title} - {label}" if label else self._title
        if new_title == self._window_title:
            return
        self._window_title = new_title
        if self.webview_instance:
            try:
                self.webview_instance.set_title(new_title)
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
        return False

    def close_window(self):
        """真正退出：放行关闭并销毁主窗口 → Application.Exit → webview.start 返回。"""
        self._real_close.set()
        if self.webview_instance:
            try:
                self.webview_instance.destroy()
            except Exception:
                pass

    def prepare(self, width=440, height=500):
        """创建主窗口（不启动事件循环，webview.start 统一执行）。"""
        file_url = Path(self.abs_path).as_uri()
        win = webview.create_window(
            title=self._window_title,
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


def _game_window_clickable(win):
    """检查游戏窗口中心点是否被其他窗口遮挡（点击会落到遮挡窗口上）。

    截图不依赖前台（窗口 DC 直接读取），但鼠标点击是屏幕坐标——
    游戏被最大化窗口遮挡时点击全落到遮挡窗口上。沿父窗口链向上
    查找，能走到游戏窗口 hwnd 才认为可点击。
    静默操作模式：点击走窗口消息通道、不落屏幕坐标，遮挡无关，
    直接放行。
    注意：必须放在类定义之后（模块级），否则类方法会被吞进本函数体。
    """
    if app_utils.silent_mode_on():
        return True
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
