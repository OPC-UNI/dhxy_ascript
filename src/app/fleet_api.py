# -*- coding: utf-8 -*-
"""多窗口舰队看板 API（自 control_window.py 抽出的 mixin）。

行数据来自 app.window_registry（枚举 + 勾选/选中持久化），这里补充
任务运行态、授权、启动/停止、逐行日志推送。ControlWindow 继承本
mixin，页面 callPython 名称与行为保持可预期。

启动动作（全部启动 / 单窗口启动）各做一次在线授权校验（2026-11-22
用户确认），N 个窗口共享结果；全部启动错峰 1.2s，避免同时拉起多个
OCR 引擎/窗口操作。
"""
import json
import re
import threading
import time

from ..core import app_utils, debug_overlay, role_lock, schemes, user_data
from ..core.settings import load_silent_mode
from ..device import progress
from . import window_registry
from .host_contract import ControlHost

FLEET_START_STAGGER = 1.2  # 全部启动的错峰间隔（秒）


class FleetApiMixin(ControlHost):
    # ---- 授权 / 启动守卫 ----

    def _fleet_check_license(self):
        """启动动作前在线校验（每次动作一次）；通过返回 None。"""
        if self._license_gate is None:
            return None
        try:
            result = self._license_gate() or {}
        except Exception as e:
            result = {"ok": False, "msg": f"授权服务异常：{e}"}
        self._set_license_info(result.get("info") or {})
        if result.get("ok"):
            info = self._license_info
            pass_line = f"[授权] 校验通过：{info.get('status', '未知')}"
            if info.get("expire"):
                pass_line += f"，到期 {info['expire']}"
            if info.get("offline"):
                pass_line += "（网络异常，使用本地许可证）"
            app_utils.log(pass_line, "SYS")
            self.add_logs([pass_line])
            return None
        msg = result.get("msg") or "授权校验未通过"
        if "无法连接授权服务器" in msg:
            hint = "请检查网络后重试"
        elif "激活数量" in msg or "设备" in msg:
            hint = "请联系作者在后台释放旧设备后重新激活"
        elif "过期" in msg:
            hint = "请续费后重试"
        elif "未激活" in msg or "输入卡密" in msg:
            hint = "请先在激活页输入卡密激活"
        else:
            hint = "请联系作者处理"
        fail_line = f"[授权] 校验未通过：{msg}"
        app_utils.log(fail_line, "WARN")
        self.add_logs([fail_line])
        return {"status": "error", "license": False,
                "msg": f"授权校验未通过，任务未启动：{msg}（{hint}）"}

    def _fleet_action_lock(self):
        """启动/停止动作串行锁（按钮/全部启动/单窗口启动并发防双启）。

        RLock：start_window 持锁后 _fleet_start_one 需要再进一次；全部启动
        后台线程逐窗口取锁，stop_all 取锁取消并停任务，避免「取消后又有
        一个窗口刚好被拉起」的竞态。
        """
        lock = getattr(self, "_fleet_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._fleet_lock = lock
        return lock

    def is_fleet_starting(self):
        """全部启动后台线程是否在途（老板键/顶栏停止判断用）。"""
        return bool(getattr(self, "_fleet_starting", False))

    def _fleet_cancel_pending(self):
        """取消全部启动：递增代际，后台线程下一轮检查即退出。"""
        self._fleet_start_gen = getattr(self, "_fleet_start_gen", 0) + 1
        self._fleet_starting = False

    def _fleet_map_nav_guard(self):
        """诊断-地图导航进行中拒绝启动（避免与任务子进程互抢操作）。"""
        if getattr(self, "_map_nav_running", False):
            return {"status": "error",
                    "msg": "地图导航进行中，请先点「中断寻路」或等待完成后再启动任务"}
        return None

    # ---- 启停 ----

    def start_window(self, role_id, hwnd=None):
        """启动单个窗口（行内按钮）：一次授权校验 + 目标解析 + 启动。"""
        rid = str(role_id or "")
        if not re.fullmatch(r"\d+", rid):
            return {"status": "error", "msg": "该窗口未登录，不能启动"}
        if getattr(self, "_fleet_starting", False):
            return {"status": "error", "msg": "全部启动进行中，请稍候"}
        lock = self._fleet_action_lock()
        if not lock.acquire(blocking=False):
            return {"status": "error", "msg": "启动操作进行中，请稍候"}
        try:
            guard = self._fleet_map_nav_guard()
            if guard:
                return guard
            err = self._fleet_check_license()
            if err:
                return err
            return self._fleet_start_one(rid, hwnd)
        finally:
            lock.release()

    def start_all(self):
        """全部启动：一次授权校验，后台按 1.2s 错峰拉起已勾选窗口。"""
        if getattr(self, "_fleet_starting", False):
            return {"status": "error", "msg": "全部启动进行中，请稍候"}
        lock = self._fleet_action_lock()
        if not lock.acquire(blocking=False):
            return {"status": "error", "msg": "启动操作进行中，请稍候"}
        try:
            guard = self._fleet_map_nav_guard()
            if guard:
                return guard
            err = self._fleet_check_license()
            if err:
                return err
            rows = [r for r in self.get_fleet_state()["rows"]
                    if r.get("can_start")]
            if not rows:
                return {"status": "error",
                        "msg": "没有已勾选且已登录的窗口（新窗口默认不勾选，请先加入多开）"}
            # 静默是多窗口并行硬前提（2026-11-22 用户确认）：任一参与角色
            # 关闭静默时，仅允许单窗口运行。
            running_roles = set(self._tm.running_roles())
            to_start = [r for r in rows if r["role_id"] not in running_roles]
            involved = {r["role_id"] for r in to_start} | running_roles
            if involved and len(involved) > 1 and not all(
                    load_silent_mode(role=rid) for rid in involved):
                return {"status": "error",
                        "msg": "多窗口并行需要所有参与窗口开启静默操作（通用设置）；"
                               "关闭静默时只允许单窗口运行"}
            self._fleet_starting = True
            self._fleet_start_gen = getattr(self, "_fleet_start_gen", 0) + 1
            gen = self._fleet_start_gen
            threading.Thread(target=self._fleet_start_rows, args=(rows, gen),
                             daemon=True, name="fleet-start").start()
            return {"status": "success", "running": True,
                    "starting": len(rows)}
        finally:
            lock.release()

    def _fleet_start_rows(self, rows, gen):
        """全部启动线程体：错峰逐个启动；单个失败不影响其他窗口。

        代际（gen）取消：stop_all/单行停止/老板键会递增 _fleet_start_gen，
        本循环下一轮检查即退出，不再拉起剩余窗口。
        """
        try:
            for idx, row in enumerate(rows):
                if gen != getattr(self, "_fleet_start_gen", gen):
                    return
                if idx:
                    # 分片睡眠，取消后最多 0.1s 响应
                    for _ in range(max(1, int(FLEET_START_STAGGER * 10))):
                        if gen != getattr(self, "_fleet_start_gen", gen):
                            return
                        time.sleep(0.1)
                if gen != getattr(self, "_fleet_start_gen", gen):
                    return
                label = row.get("label") or row.get("role_id")
                result = self._fleet_start_one(row["role_id"], row.get("hwnd"),
                                               gen=gen)
                if result.get("status") != "success":
                    msg = result.get("msg") or "未知错误"
                    if msg == "启动已取消":
                        return
                    line = f"[多开] {label} 启动失败：{msg}"
                    app_utils.log(line, "WARN")
                    self.add_logs([line])
                    # 结构化错误：行内红点 + 顶栏横幅（选中日志视图可见）
                    self._push_js(
                        "if(window.onFleetStartError){window.onFleetStartError("
                        "%s,%s,%s);}"
                        % (json.dumps(str(row["role_id"])),
                           json.dumps(str(label), ensure_ascii=False),
                           json.dumps(str(msg), ensure_ascii=False)))
        finally:
            if gen == getattr(self, "_fleet_start_gen", gen):
                self._fleet_starting = False
                # 非看板页轮询被门控：用事件结束「启动中...」按钮态
                self._push_js(
                    "if(window.onFleetStartDone){window.onFleetStartDone();}")

    def _fleet_start_one(self, role_id, hwnd=None, gen=None):
        """启动单个窗口（调用方已完成授权/互斥等动作级守卫）。

        gen 非空 = 全部启动后台调用；在动作锁内复核代际，取消后不再拉起。
        """
        rid = str(role_id or "")
        with self._fleet_action_lock():
            if gen is not None and gen != getattr(self, "_fleet_start_gen", gen):
                return {"status": "error", "msg": "启动已取消"}
            if rid not in window_registry.enabled_roles():
                return {"status": "error", "msg": "该窗口未勾选参与多开"}
            target = window_registry.resolve_target(rid, hwnd)
            if target is None:
                return {"status": "error",
                        "msg": "窗口不存在或已关闭（请确认游戏仍在登录界面之后）"}
            # 窗口当前 Tab 必须就是该角色：组号客户端手动切到队员 Tab 时
            # 启动会让任务以队员为队长、角色数据却按选中角色写，状态会串。
            shown_rid = user_data.role_id_from_text(target.get("title") or "")
            if shown_rid and shown_rid != rid:
                return {"status": "error",
                        "msg": f"当前窗口显示的是角色 {shown_rid}，"
                               f"请切回角色 {rid} 的 Tab 后再启动"}
            target_hwnd = int(target.get("hwnd") or 0)
            for slot in self._tm.running_targets():
                if (target_hwnd and int(slot.get("hwnd") or 0) == target_hwnd
                        and str(slot.get("key")) != rid):
                    return {"status": "error",
                            "msg": "该游戏窗口已有其他角色的任务在运行"}
            if self._tm.is_running(rid):
                return {"status": "error", "msg": "该窗口任务已在运行"}
            if role_lock.is_held(rid):
                return {"status": "error", "msg": "该角色正在另一实例中运行"}
            # 静默硬前提：待启动 + 已运行的全部角色都必须开启静默
            involved = {rid} | {str(k) for k in self._tm.running_roles()
                                if str(k) != rid}
            if len(involved) > 1 and not all(
                    load_silent_mode(role=r) for r in involved):
                return {"status": "error",
                        "msg": "多窗口并行需要所有参与窗口开启静默操作（通用设置）；"
                               "关闭静默时只允许单窗口运行"}
            if gen is not None and gen != getattr(self, "_fleet_start_gen", gen):
                return {"status": "error", "msg": "启动已取消"}
            return self._tm.start(rid, target["hwnd"], target["title"])

    def stop_window(self, role_id):
        """停止单个窗口（只杀该窗口 worker；同时取消未完成的全量启动）。"""
        rid = str(role_id or "")
        if not re.fullmatch(r"\d+", rid):
            return {"status": "error", "msg": "无效的角色"}
        with self._fleet_action_lock():
            self._fleet_cancel_pending()
            return self._tm.stop(rid)

    def stop_all(self):
        """全部停止（老板键/顶栏主按钮共用；取消未完成的全量启动）。"""
        with self._fleet_action_lock():
            self._fleet_cancel_pending()
            return self._tm.stop()

    # ---- 行数据 ----

    def get_fleet_state(self):
        """舰队看板行数据：注册表行 + 运行态/方案/进度/阶段/占用。"""
        state = window_registry.list_rows()
        running_targets = self._tm.running_targets()
        slot_by_hwnd = {int(t["hwnd"]): t for t in running_targets
                        if t.get("hwnd")}
        rows = []
        for row in state["rows"]:
            slot = slot_by_hwnd.get(int(row.get("hwnd") or 0))
            if (slot is not None and row.get("role_id")
                    and str(row["role_id"]) != str(slot.get("key"))):
                # 组号 pass：主窗口标题显示队员 Tab，运行槽位角色优先
                continue
            rows.append(dict(row))
        # 运行槽位补齐/覆盖：leader 行在标题变成队员时可能只剩灰行
        role_index = {str(r.get("role_id")): r for r in rows if r.get("role_id")}
        for t in running_targets:
            key = str(t.get("key") or "")
            if not re.fullmatch(r"\d+", key):
                continue
            row = role_index.get(key)
            if row is None:
                row = {"key": key, "role_id": key,
                       "label": t.get("label") or key,
                       "server": "", "name": "",
                       "hwnd": int(t.get("hwnd") or 0),
                       "title": t.get("title") or "",
                       "live": True, "logged_in": True, "enabled": True,
                       "selected": state.get("selected") == key,
                       "selectable": True, "duplicate": False,
                       "seq": 1e9}  # Tab 固定顺序：运行槽兜底行排最后
                rows.append(row)
                role_index[key] = row
            else:
                row["live"] = True
                row["logged_in"] = True
                if t.get("hwnd"):
                    row["hwnd"] = int(t["hwnd"])
                if t.get("title"):
                    row["title"] = t["title"]
                if t.get("label"):
                    row["label"] = t["label"]
        hwnd_busy = {int(t["hwnd"]) for t in running_targets if t.get("hwnd")}
        slot_keys = {str(t.get("key") or "") for t in running_targets}
        slot_hwnds = {}
        for t in running_targets:
            slot_hwnds.setdefault(str(t.get("key") or ""), set()).add(
                int(t.get("hwnd") or 0))
        enriched = []
        for row in rows:
            rid = row.get("role_id")
            hwnd = int(row.get("hwnd") or 0)
            role_running = bool(rid and str(rid) in slot_keys)
            role_hwnds = slot_hwnds.get(str(rid) or "") or set()
            # 行运行态按窗口判定（同角色双窗口只亮真正在跑的那行）；
            # role_running 用于设置只读/禁止再启同角色。
            running = bool(role_running and (
                not role_hwnds or not hwnd or hwnd in role_hwnds))
            other = bool(rid and not role_running and role_lock.is_held(rid))
            status = (debug_overlay.read_runtime_status(rid)
                      if rid and (not role_running or running) else None)
            prog = progress.task_progress_summary(rid) if rid else None
            scheme = schemes.active_name(rid) if rid else ""
            window_busy = bool(hwnd and hwnd in hwnd_busy and not running)
            can_start = bool(row.get("enabled") and row.get("live")
                             and row.get("logged_in") and rid
                             and not role_running and not other
                             and not row.get("duplicate") and not window_busy
                             and not getattr(self, "_fleet_starting", False))
            enriched.append({**row,
                             "running": running,
                             "role_running": role_running,
                             "other_instance": other,
                             "can_start": can_start,
                             "can_stop": running,
                             "window_busy": window_busy,
                             "scheme": scheme,
                             "status": status,
                             "progress": prog})
        rows = enriched
        # 固定顺序（2026-11-22 用户要求）：多开管理列表不因运行/选中/勾选
        # 重排；与窗口 Tab 一致按注册表 seq（角色首次出现顺序）。
        rows.sort(key=lambda r: (r.get("seq", 1e9),
                                 str(r.get("role_id") or r.get("key"))))
        running_count = len(running_targets)
        selected_row = next((r for r in rows if r.get("selected")), None)
        return {"rows": rows,
                "selected": state.get("selected"),
                "selected_hwnd": state.get("selected_hwnd"),
                "selected_label": (selected_row or {}).get("label", ""),
                "running_count": running_count,
                "enabled_count": sum(1 for r in rows if r["enabled"]),
                "total": len(rows),
                "starting": bool(getattr(self, "_fleet_starting", False))}

    # ---- 选中 / 勾选 ----

    def select_window(self, role_id, hwnd=None):
        """选中某行：注入主进程目标，设置页/成就/组号入口跟随。"""
        rid = str(role_id or "")
        result = window_registry.select(rid, hwnd)
        if result.get("status") != "success":
            return result
        user_data.current_role_id(refresh=True)
        try:
            schemes.ensure_role_schemes(rid)  # 新角色首次选中时建「默认」方案
        except Exception:
            pass
        # 主进程静默标志跟随选中角色（验证/诊断/多窗口前置检查共用）
        try:
            app_utils.set_silent_mode(load_silent_mode())
        except Exception:
            pass
        self.refresh_window_title()
        return result

    def set_window_enabled(self, role_id, enabled):
        """勾选/取消勾选参与多开。"""
        result = window_registry.set_enabled(role_id, enabled)
        if result.get("status") == "success":
            line = f"[多开] {'已加入' if enabled else '已移出'}多开: {role_id}"
            app_utils.log(line, "SYS")
        return result

    # ---- 行级状态/日志推送（TaskManager 回调） ----

    def on_fleet_status(self, key, running):
        """单窗口启停状态：页面行状态即时刷新（轮询兜底）。"""
        self._push_js(
            "if(window.onFleetStatus){window.onFleetStatus(%s,%s);}"
            % (json.dumps(str(key)), "true" if running else "false"))

    def on_fleet_log(self, key, lines):
        """单窗口日志：结构化行日志事件（合并日志由 on_log 统一推送）。

        TaskManager 同时回调 on_role_log（本方法，未加前缀）与 on_log
        （已加角色前缀，喂面板全量视图/消息服务）——这里不再重复推全量
        日志，避免抽屉双份。
        """
        label = self._tm.label_for(key) if hasattr(self._tm, "label_for") else ""
        self._push_js(
            "if(window.onFleetLogs){window.onFleetLogs(%s,%s,%s);}"
            % (json.dumps(str(key)), json.dumps(label, ensure_ascii=False),
               json.dumps(lines, ensure_ascii=False)))
