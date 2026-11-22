# -*- coding: utf-8 -*-
"""消息服务面板 API（自 control_window.py 抽出的 mixin）。

包含消息配置读写、通道验证，以及 /capture、/log、/exitgame、/disband、
/shout 等面板侧编排。底层服务在 src/app/messages/；ControlWindow 继承
本 mixin，callPython 方法名与行为不变（纯搬迁）。

注意：测试 monkeypatch 本模块的模块级名字（game/app_utils/
_window_minimized/load_* 等）时必须 patch 本模块（src.app.message_api），
patch control_window 上的同名导入不会影响已迁到这里的消息方法。
"""
import io
import json
import time
from typing import Any, cast

from ascript.windows.hardware.keyboard import Keyboard

from ..core import app_utils, user_data
from ..core.settings import (SHOUT_TEXT_MAX_LEN, load_message_config,
                             load_selected_modes, load_wubei_shout_text,
                             load_zhongzu_shout_text, mask_message_config,
                             merge_message_config)
from ..core.settings import save_message_config as save_message_config_kv
from ..device import game
from . import env_check, window_registry
from .host_contract import ControlHost
from .messages import log_tail as message_log_tail
from .messages import registry as message_registry


def _selected_role():
    """消息动作的作用角色 = 运行看板选中窗口（多窗口，2026-11-22）。"""
    return window_registry.selected_role() or user_data.current_role_id()


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


class MessageApiMixin(ControlHost):
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
            # SDK 的 capture 实际返回 PIL.Image，类型标注是 NDArray；
            # cast 仅消除静态检查误报，运行期仍是同一对象。
            cast(Any, img).save(buffer, format="PNG")
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
        """今天日志末尾 n 行（/log 指令）。

        多窗口（2026-11-22）：日志按角色分文件（logs/<日期>/<角色ID>.log
        + system.log），这里合并当天全部文件按时间戳排序，角色文件行加
        「[角色ID]」前缀；单角色/旧版单文件时与原来等价。
        """
        try:
            count = max(1, min(100, int(n)))
        except (TypeError, ValueError):
            count = 20
        day_dir = app_utils.log_day_dir()
        try:
            files = sorted(day_dir.glob("*.log"))
            # 升级当天：旧版单文件 logs/<日期>.log 也并入（重启前的日志不丢）
            legacy = day_dir.parent / (day_dir.name + ".log")
            if legacy.exists():
                files.append(legacy)
            merged = []
            for p in files:
                try:
                    tail = message_log_tail.read_tail_lines(p, count)
                except OSError:
                    continue
                tag = p.stem
                for line in tail:
                    merged.append((line[:8], tag, line))
            merged.sort(key=lambda item: (item[0], item[1]))
            lines = [ln if tag == "system" else f"[{tag}] {ln}"
                     for _ts, tag, ln in merged[-count:]]
            return {"ok": True, "lines": lines, "path": str(day_dir)}
        except OSError as e:
            return {"ok": False, "reason": str(e), "lines": [],
                    "path": str(day_dir)}

    def _message_exit_game(self, progress=None):
        """消息服务 /exitgame：停任务 → 解散/离开队伍 → 关闭游戏客户端。"""
        if not self._game_op_lock.acquire(blocking=False):
            return {"ok": False,
                    "message": "另一项游戏操作正在进行（退出/解散队伍/喊话），请稍后再试"}
        try:
            from ..device import exit_game as exit_game_mod
            role = _selected_role()
            if self._tm.is_running(role):
                self._message_log("退出游戏：先停止该窗口任务")
                if progress is not None:
                    try:
                        progress("正在停止该窗口任务…")
                    except Exception:
                        pass
                self._tm.stop(role)
                deadline = time.time() + 10.0
                while self._tm.is_running(role) and time.time() < deadline:
                    time.sleep(0.2)
                if self._tm.is_running(role):
                    return {"ok": False,
                            "message": "该窗口任务未在 10 秒内停止，已放弃退出游戏"}
            win = game.find_main_window()
            if win is None:
                return {"ok": True, "message": "游戏窗口已关闭，无需退出"}
            # 队伍处理是尽力而为：未组队时组队菜单可能根本打不开
            # （2026-11-22 用户实测「组队菜单未打开」导致退出被中止）。
            # 失败只记 WARN 并继续关闭客户端，回复里注明跳过原因。
            ok, info = exit_game_mod.leave_teams_for_exit(win, progress)
            team_note = ""
            if ok:
                self._message_log(f"退出游戏-队伍：{info}", "SYS")
            else:
                # 去掉 leave_teams_for_exit 已加的前缀，回复里只留具体原因
                detail = str(info).replace("队伍处理失败：", "", 1)
                team_note = f"（队伍处理跳过：{detail}）"
                self._message_log(
                    f"退出游戏-队伍：{info}，跳过队伍处理继续关闭客户端",
                    "WARN")
            ok, info = exit_game_mod.exit_client(win, progress)
            self._message_log(f"退出游戏：{info}", "SYS" if ok else "WARN")
            return {"ok": ok,
                    "message": (info + team_note) if ok else info}
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
        操作。该窗口任务运行中拒绝（任务自己管队伍，抢面板会打乱其检查/补人）。
        """
        if self._tm.is_running(_selected_role()):
            return {"ok": False, "message": "该窗口任务运行中，请先停止该窗口任务再解散队伍"}
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
            running = bool(self._tm.is_running(_selected_role()))
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
