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
import io
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
from ..device import game  # 游戏窗口查找（绑定/消息服务/组号检查；game 不反向依赖本模块，可顶层导入）
from ..core import app_utils  # KeyValue 合并读写（kv_save_merged/kv_remove_merged）
from ..core import schemes  # 配置方案（多套设置快照切换 + 导入导出）
from ..core import user_data  # 角色数据目录（data/<角色ID>/）
from ..core import role_settings  # 当前角色生效设置（data/<角色ID>/settings.json）
from . import hotkey  # 老板键（启动/停止按钮文案展示热键名）
from . import updater  # 软件自动更新（清单/下载/覆盖升级）
from . import update_config  # 更新渠道与节流常量
from . import env_check  # 截屏黑帧判定（消息服务 /capture）
from .diagnostics import DiagnosticsMixin, _game_window_clickable  # 诊断页验证工具（P3 拆分）
from .messages import log_tail as message_log_tail  # 日志末尾读取（/log）
from .messages import registry as message_registry  # 通道 spec/命令文档
from .messages.service import MessageService  # 消息服务编排
from ..core.settings import (load_message_config, mask_message_config,
                             merge_message_config)
from ..core.settings import save_message_config as save_message_config_kv
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
    _BATTLE_KEYS, _BATTLE_ONLY_AUTO_REJOIN_KEY, _BATTLE_ONLY_HEZI_KEY, _CLEAR_SCREEN_KEY, _COUNT_KEYS,
    _DEBUG_OVERLAY_KEY, _DEBUG_UI_KEY, _DEFAULT_BATTLE, _DEFAULT_MODES,
    _GROUP_ACTIONS_KEY, _GROUP_DETECTED_KEY, _GROUP_LAST_SCAN_KEY, _HIGH_PERF_KEY,
    _MODES_KEY, _MODE_KEY_OLD, _REPAIR_INTERVAL_DEFAULT, _REPAIR_INTERVAL_MAX,
    _REPAIR_INTERVAL_MIN, _SILENT_MODE_KEY, _TASK_PROGRESS_KEY, _THRESHOLD_KEYS,
    _UI_THEME_KEY, _UPDATE_AUTO_CHECK_KEY, _UPDATE_FEED_KEY, _UPDATE_LAST_CHECK_KEY,
    _UPDATE_SKIPPED_KEY, _USE_SHEYAOXIANG_KEY, _WUBEI_AUTO_CREATE_TEAM_KEY,
    _WUBEI_AUTO_KICK_AWAY_KEY, _WUBEI_KICK_AWAY_MINUTES_KEY, _WUBEI_AUTO_REJOIN_KEY,
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
    load_battle_only_auto_rejoin, load_battle_only_hezi, load_clear_screen, load_debug_overlay, load_debug_ui_unlocked,
    load_group_actions, load_group_last_scan, load_high_perf, load_selected_modes,
    load_silent_mode, load_task_counts, load_thresholds, load_ui_theme,
    load_update_auto_check, load_update_feed_url, load_update_last_check, load_update_skipped_version,
    load_use_sheyaoxiang, load_wubei_auto_create_team, load_wubei_auto_kick_away,
    load_wubei_kick_away_minutes, load_wubei_auto_rejoin, load_wubei_auto_repair,
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
    save_battle_only_hezi,
    save_clear_screen, save_debug_overlay, save_debug_ui_unlocked, save_group_actions,
    save_group_last_scan, save_high_perf, save_selected_modes, save_silent_mode,
    save_task_count, save_threshold, save_ui_theme, save_update_auto_check,
    save_update_last_check, save_update_skipped_version, save_use_sheyaoxiang, save_wubei_auto_create_team,
    save_wubei_auto_kick_away, save_wubei_kick_away_minutes,
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


class _ControlMessageHost:
    """消息服务的主进程能力注入（service 不 import UI/device）。"""

    def __init__(self, cw):
        self._cw = cw

    def is_running(self):
        return self._cw._tm.is_running()

    def runtime_status(self):
        return self._cw.get_runtime_status()

    def task_progress(self):
        return task_progress_summary()

    def identity(self):
        return bound_short_label() or ""

    def capture(self):
        return self._cw._message_capture()

    def log_tail(self, n):
        return self._cw._message_log_tail(n)

    def game_window_present(self):
        """游戏主窗口是否在场（/exitgame 游戏未启动时直接返回，免二次确认）。"""
        return game.find_main_window() is not None

    def exit_game(self, progress=None):
        return self._cw._message_exit_game(progress)

    # ---- 控制指令（/schemes /switch /start /stop）----
    # 一律转调控制窗口同类入口，与面板按钮共用同一套护栏：切方案走
    # switch_scheme（任务运行中拒绝）、启动走 start_task（授权校验 +
    # 窗口绑定对齐）、停止走 stop_task。

    def list_schemes(self):
        return self._cw.get_schemes()

    def switch_scheme(self, name):
        result = self._cw.switch_scheme(name)
        if isinstance(result, dict) and result.get("status") == "success":
            # 远程切换后面板设置已整体替换：推一次全量刷新（与页面下拉
            # 切换成功后的 syncInitData 一致），避免面板显示旧方案设置
            try:
                if self._cw.webview_instance:
                    self._cw.webview_instance.evaluate_js(
                        "if(window.syncInitData){window.syncInitData();}")
            except Exception:
                pass
        return result

    def start_task(self):
        return self._cw.start_task()

    def stop_task(self):
        return self._cw.stop_task()

    def disband_team(self):
        return self._cw._message_disband()

    def shout(self, text=""):
        return self._cw._message_shout(text)


class ControlWindow(DiagnosticsMixin, WebWindow):
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
        # 消息服务（构造只读配置，不启线程；prepare() 建窗后 start）
        self._capture_lock = threading.Lock()  # /capture 串行（主进程内）
        # 消息侧游戏操作串行（/exitgame、/disband、/shout 互斥——都会操作
        # 队伍面板/聊天输入，交错执行会打乱面板状态或输入内容）
        self._game_op_lock = threading.Lock()
        self._message_service = MessageService(
            host=_ControlMessageHost(self),
            config=load_message_config(),
            log=self._message_log)
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
        self.expose(self.save_wubei_auto_kick_away)
        self.expose(self.save_wubei_kick_away_minutes)
        self.expose(self.save_battle_only_auto_rejoin)
        self.expose(self.save_battle_only_hezi)
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
        self.expose(self.verify_mingwang_claim_mozu)
        self.expose(self.verify_mingwang_claim_xianzu)
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
        # 消息服务（侧边栏「消息」）
        self.expose(self.get_message_config)
        self.expose(self.save_message_config)
        self.expose(self.verify_message_channel)
        self.expose(self.get_message_state)
        self.expose(self.restart_message_service)
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
                          "auto_kick_away": load_wubei_auto_kick_away(),
                          "kick_away_minutes": load_wubei_kick_away_minutes(),
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
                "battle_only_hezi": load_battle_only_hezi(),
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

    # ---- 消息服务（侧边栏「消息」；v1 只读查询 + 截图） ----

    def get_message_config(self):
        """消息页初始化：掩码配置 + 通道/命令元数据 + 运行状态。"""
        raw = load_message_config()
        effective = message_registry.effective_config(raw)
        return {"config": mask_message_config(effective),
                "meta": message_registry.payload(),
                "state": self._message_service.get_state()}

    def save_message_config(self, patch=None):
        """保存消息配置（深合并；掩码占位不覆盖旧 Token）并热应用。"""
        result = save_message_config_kv(patch or {})
        if result.get("status") != "success":
            return result
        raw = result.get("config") or load_message_config()
        self._message_service.apply_config(raw)
        effective = message_registry.effective_config(raw)
        return {"status": "success",
                "config": mask_message_config(effective),
                "state": self._message_service.get_state(),
                "warnings": message_registry.config_warnings(effective)}

    def get_message_state(self):
        """消息页轮询：服务/通道健康、实例锁、最近指令、验证状态。"""
        return self._message_service.get_state()

    def restart_message_service(self):
        """重试启动（锁 stale 接管 / 配置修正后）。"""
        state = self._message_service.restart_with_config(
            load_message_config())
        return {"status": "success", "state": state}

    def verify_message_channel(self, channel=None, patch=None):
        """一键验证通道：用表单当前值（可未保存）走 getMe + 测试消息。"""
        channel = str(channel or "telegram")
        try:
            candidate = merge_message_config(patch or {})
        except ValueError as e:
            return {"status": "error", "msg": str(e)}
        return self._message_service.verify_async(
            channel, candidate,
            on_step=self._push_message_verify_step,
            on_done=self._push_message_verify_result)

    def _push_message_verify_step(self, step):
        payload = {"key": getattr(step, "key", ""),
                   "label": getattr(step, "label", ""),
                   "ok": bool(getattr(step, "ok", False)),
                   "detail": getattr(step, "detail", "")}
        self._push_js(
            "if(window.onMessageVerifyStep){window.onMessageVerifyStep(%s);}"
            % json.dumps(payload, ensure_ascii=False))

    def _push_message_verify_result(self, report):
        self._push_js(
            "if(window.onMessageVerifyResult){window.onMessageVerifyResult(%s);}"
            % json.dumps(report or {}, ensure_ascii=False))

    def stop_message_service(self):
        """真正退出时停止服务（点 X 隐藏托盘不调用，远程服务继续）。"""
        service = getattr(self, "_message_service", None)
        if service is None:
            return
        try:
            service.stop(timeout=1.5)
        except Exception:
            pass

    def _message_log(self, msg, level="INFO"):
        """消息服务日志：文件 + 面板抽屉（事件规则会忽略 [消息] 前缀）。"""
        line = f"[消息] {msg}"
        try:
            app_utils.log(line, level)
        except Exception:
            pass
        try:
            self.add_logs([line])
        except Exception:
            pass

    def _message_capture(self):
        """游戏窗口截屏（不抢焦点）；返回 PNG bytes + 黑帧判定。"""
        if not self._capture_lock.acquire(blocking=False):
            return {"ok": False, "reason": "busy"}
        try:
            win = game.find_main_window()
            if win is None:
                return {"ok": False, "reason": "no_window"}
            hwnd = int(getattr(win, "hwnd", 0) or 0)
            try:
                import win32gui
                if hwnd and win32gui.IsIconic(hwnd):
                    return {"ok": False, "reason": "minimized"}
            except Exception:
                pass
            img = win.capture(force_activate=False)
            if img is None:
                return {"ok": False, "reason": "capture_failed"}
            stats = env_check.frame_stats(img)
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            return {"ok": True, "image": buffer.getvalue(),
                    "w": stats["w"], "h": stats["h"],
                    "black_ratio": stats["black_ratio"],
                    "black": stats["black_ratio"] >
                             env_check._BLACK_RATIO_THRESHOLD}
        except Exception as e:
            return {"ok": False, "reason": "error", "detail": str(e)}
        finally:
            self._capture_lock.release()

    def _message_log_tail(self, n=20):
        """今天日志末尾 n 行（/log 指令）。"""
        try:
            count = max(1, min(100, int(n)))
        except (TypeError, ValueError):
            count = 20
        path = app_utils.log_file_path()
        try:
            lines = message_log_tail.read_tail_lines(path, count)
            return {"ok": True, "lines": lines, "path": str(path)}
        except OSError as e:
            return {"ok": False, "reason": str(e), "lines": [],
                    "path": str(path)}

    def _message_exit_game(self, progress=None):
        """消息服务 /exitgame：停任务 → 解散/离开队伍 → 关闭游戏客户端。"""
        if not self._game_op_lock.acquire(blocking=False):
            return {"ok": False,
                    "message": "另一项游戏操作正在进行（退出/解散队伍/喊话），请稍后再试"}
        try:
            from ..device import exit_game as exit_game_mod
            if self._tm.is_running():
                self._message_log("退出游戏：先停止任务")
                if progress is not None:
                    try:
                        progress("正在停止任务…")
                    except Exception:
                        pass
                self._tm.stop()
                deadline = time.time() + 10.0
                while self._tm.is_running() and time.time() < deadline:
                    time.sleep(0.2)
                if self._tm.is_running():
                    return {"ok": False,
                            "message": "任务进程未在 10 秒内停止，已放弃退出游戏"}
            win = game.find_main_window()
            if win is None:
                return {"ok": True, "message": "游戏窗口已关闭，无需退出"}
            ok, info = exit_game_mod.leave_teams_for_exit(win, progress)
            self._message_log(f"退出游戏-队伍：{info}",
                              "SYS" if ok else "WARN")
            if not ok:
                return {"ok": False, "message": info}
            ok, info = exit_game_mod.exit_client(win, progress)
            self._message_log(f"退出游戏：{info}", "SYS" if ok else "WARN")
            return {"ok": ok, "message": info}
        except Exception as e:
            self._message_log(
                f"退出游戏异常：{type(e).__name__}: {e}", "ERROR")
            return {"ok": False,
                    "message": f"退出游戏异常：{type(e).__name__}: {e}"}
        finally:
            self._game_op_lock.release()

    def _message_disband(self):
        """消息服务 /disband：绑定 Tab 护栏 → 队长解散 / 队员离开（单次面板流程）。

        与诊断页「检查队伍信息」同一套护栏：先切回绑定角色 Tab（错误 Tab
        上执行会操作错账号）；未组队时 leave_or_disband_team 直接返回无需
        操作。任务运行中拒绝（任务自己管队伍，抢面板会打乱其检查/补人）。
        """
        if self._tm.is_running():
            return {"ok": False, "message": "任务运行中，请先停止任务再解散队伍"}
        if not self._game_op_lock.acquire(blocking=False):
            return {"ok": False,
                    "message": "另一项游戏操作正在进行（退出/解散队伍/喊话），请稍后再试"}
        try:
            win = game.find_main_window()
            if win is None:
                return {"ok": False, "message": "未找到游戏窗口（游戏未启动）。"}
            if _window_minimized(win):
                return {"ok": False, "message": "游戏窗口已最小化，请恢复窗口后再试。"}
            from ..device import tab_group, team
            ok_tab, tab_info = tab_group.ensure_bound_tab(win)
            if not ok_tab:
                return {"ok": False, "message": tab_info}
            ok, info = team.leave_or_disband_team(win)
            self._message_log(f"解散/离开队伍：{info}",
                              "SYS" if ok else "WARN")
            return {"ok": ok, "message": info}
        except Exception as e:
            self._message_log(
                f"解散队伍异常：{type(e).__name__}: {e}", "ERROR")
            return {"ok": False,
                    "message": f"解散队伍异常：{type(e).__name__}: {e}"}
        finally:
            self._game_op_lock.release()

    def _default_shout_text(self):
        """未带内容时的喊话文案：按任务选择顺序取首个可喊话任务的设置。

        修罗模板的 {种族} 与任务内自动喊话同源（_build_shout_text），保证
        手机远程喊话和任务自动喊话文案一致；无相关任务选中返回空串。
        """
        try:
            modes = load_selected_modes()
        except Exception:
            return ""
        for mode in modes:
            try:
                if mode == "五倍":
                    return load_wubei_shout_text()
                if mode == "修罗":
                    from ..tasks import xiuluo  # 延迟导入：避免启动期加载任务模块
                    return xiuluo._build_shout_text()
                if mode == "种族比武":
                    return load_zhongzu_shout_text()
            except Exception as e:
                self._message_log(f"读取喊话文案失败（{mode}）：{e}", "WARN")
                return ""
        return ""

    def _message_shout(self, text=""):
        """消息服务 /shout：向游戏聊天发送一次喊话（任务运行中也可用）。

        与任务内喊话同链路：静默模式走 WM_CHAR 逐字符注入（无需前台），
        关闭静默时激活窗口走物理键盘（Enter 发送）；文本上限沿用喊话
        设置的长度上限。整段打字+回车持跨进程聊天输入互斥（与任务自动
        喊话、客栈金额输入排队执行，防止两段文字拼成一条）。运行中不
        切 Tab（避免打断组号 pass），按当前 Tab 角色发送——若任务正处
        对话/战斗中，公屏可能显示不出来，回复里注明。
        """
        try:
            running = bool(self._tm.is_running())
        except Exception:
            running = False
        if not self._game_op_lock.acquire(blocking=False):
            return {"ok": False,
                    "message": "另一项游戏操作正在进行（退出/解散队伍/喊话），请稍后再试"}
        try:
            # 多行输入会把换行当回车投进聊天（提前发送半截文案）：
            # 空白折叠成单个空格，只保留一行
            text = " ".join(str(text or "").split())
            if not text:
                text = self._default_shout_text()
                if not text:
                    return {"ok": False,
                            "message": "未选中五倍/修罗/种族比武任务，没有可用的喊话文案；"
                                       "请带上内容：/shout 喊话内容"}
            text = text[:SHOUT_TEXT_MAX_LEN]
            win = game.find_main_window()
            if win is None:
                return {"ok": False, "message": "未找到游戏窗口（游戏未启动）。"}
            if _window_minimized(win):
                return {"ok": False, "message": "游戏窗口已最小化，请恢复窗口后再试。"}
            if not running:
                # 任务未运行才做绑定 Tab 护栏；运行中切 Tab 会打断组号 pass
                from ..device import tab_group
                ok_tab, tab_info = tab_group.ensure_bound_tab(win)
                if not ok_tab:
                    return {"ok": False, "message": tab_info}
            with app_utils.chat_input_lock() as locked:
                if not locked:
                    return {"ok": False,
                            "message": "喊话通道正忙（另一条喊话发送中），请稍后再试。"}
                if app_utils.silent_mode_on():
                    from ..device import silent_click  # 延迟导入：仅静默路径需要
                    info = silent_click.send_silent_text(win, text, end_enter=True)
                    if info is None:
                        return {"ok": False, "message": "喊话投递失败（窗口句柄不可用）。"}
                else:
                    # 键盘事件只进前台窗口；与任务内物理喊话同时序
                    win.activate()
                    time.sleep(0.5)
                    Keyboard.input(text)
                    time.sleep(0.8)  # 等待剪贴板粘贴完成（中文走 Ctrl+V）
                    Keyboard.key("enter")
                    time.sleep(0.5)
            self._message_log(f"喊话已发送：{text}")
            note = ""
            if running:
                note = ("\n（任务运行中：按当前 Tab 发送；若正处对话/战斗中，"
                        "公屏可能显示不出来）")
            return {"ok": True, "message": f"已喊话：{text}" + note}
        except Exception as e:
            self._message_log(f"喊话异常：{type(e).__name__}: {e}", "ERROR")
            return {"ok": False,
                    "message": f"喊话异常：{type(e).__name__}: {e}"}
        finally:
            self._game_op_lock.release()

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

    def save_wubei_auto_kick_away(self, on):
        """五倍设置变更：保存暂离超时自动请离开关状态。"""
        return save_wubei_auto_kick_away(on)

    def save_wubei_kick_away_minutes(self, value):
        """五倍设置变更：保存暂离超时（分钟）。"""
        return save_wubei_kick_away_minutes(value)

    def save_battle_only_auto_rejoin(self, on):
        """仅战斗设置变更：保存自动归队开关状态。"""
        return save_battle_only_auto_rejoin(on)

    def save_battle_only_hezi(self, on):
        """仅战斗设置变更：保存自动抢盒子开关状态。"""
        return save_battle_only_hezi(on)

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


    def add_log(self, line):
        """单行日志推送到面板日志抽屉（主进程 [系统] 消息用）。"""
        self.add_logs([line])

    def add_logs(self, lines):
        """批量日志推送到主面板看板日志抽屉（并旁路给消息服务事件规则）。"""
        self._push_panel_logs(lines)
        service = getattr(self, "_message_service", None)
        if service is not None:
            try:
                service.on_logs(lines)
            except Exception:
                pass

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
        """真正退出：停止消息服务 → 放行关闭并销毁主窗口 → webview.start 返回。"""
        self.stop_message_service()
        self._real_close.set()
        if self.webview_instance:
            try:
                self.webview_instance.destroy()
            except Exception:
                pass

    def prepare(self, width=440, height=500):
        """创建主窗口（不启动事件循环，webview.start 统一执行）。"""
        if not os.path.isfile(self.abs_path):
            # 资源根目录解析异常时 webview 只会显示「未找到文件」白屏，
            # 这里留一行明确日志便于定位（v2.13.0 SDK 路径回归教训：
            # res/ui 被解析到 %TEMP% 解压目录，模板/OCR 模型同时失效）
            app_utils.log(f"[界面] 控制面板页面文件缺失: {self.abs_path}", "ERROR")
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
        try:
            self._message_service.start()  # 只取锁+起 daemon 线程，不阻塞 GUI
        except Exception as e:
            self._message_log(f"启动失败：{type(e).__name__}: {e}", "WARN")

        def on_loaded(*_args):
            self._inject("control window loaded")

        win.events.loaded += on_loaded
        win.events.closing += self._on_closing




def _window_minimized(win):
    """游戏窗口是否最小化（最小化时截图/面板点击/聊天输入都不可靠）。

    查询失败按未最小化处理（与 _game_window_clickable 的放行语义一致，
    真正的失败由后续操作自身报错）。
    """
    try:
        import win32gui
        hwnd = int(getattr(win, "hwnd", 0) or 0)
        return bool(hwnd and win32gui.IsIconic(hwnd))
    except Exception:
        return False
