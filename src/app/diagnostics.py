# -*- coding: utf-8 -*-
"""控制面板诊断/验证工具（自 control_window.py 抽出的 mixin）。

包含「诊断」页的全部 verify_* / _run_*_verify / _notify_*_verify 方法。
ControlWindow 继承本 mixin，callPython 方法名与行为保持不变
（纯搬迁，不改接口语义；见 docs/conventions.md P3）。

方法内的任务/设备模块仍按原样在函数内延迟导入；本模块顶层只依赖
json / threading / time / Keyboard / app_utils / game 与两个喊话默认值。

注意：测试 monkeypatch 本模块的模块级名字（game/app_utils/Keyboard/
load_*_shout_text/_game_window_clickable 等）时必须 patch 本模块
（src.app.diagnostics），patch control_window 上的同名导入不会影响
已迁到这里的验证方法。
"""
import json
import threading
import time
from typing import Any

from ascript.windows.hardware.keyboard import Keyboard

from ..core import app_utils
from ..core.settings import load_wubei_shout_text, load_zhongzu_shout_text
from ..device import game
from .host_contract import ControlHost


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


# 诊断-任务点位导航（坐标化测试）：key → (标签, 地图, x, y)
# 用于逐个验证「任务点位改用地图坐标导航」的效果（落点是否在任务的
# 「附近」判定内、是否仍触发黄字/POI 行为、到达耗时）。
# 坐标来源（2026-11-21）：
# - 领取/快送等**点黄字/POI 的固定像素**用内置标定反算，保证点击像素
#   与现流程一致（±1px）：修罗领取 627,396→120,88；五倍领取
#   517,488→91,84；客栈入口 884,600→381,26；客栈阿旺兜底
#   548,297→180,208。
# - 其余用任务常量里的目标坐标：巫医/修理/五环/沙都路点/大理寺/比武场/
#   种族领取/玄枢/预走路。
SITE_NAV_POINTS = {
    "prewalk": ("预走路点", "灵兽村", 14, 13),
    "xiuluo_claim": ("修罗领取", "灵兽村", 120, 88),
    "xiuluo_wuyi": ("修罗巫医", "灵兽村", 111, 72),
    "wubei_claim": ("五倍领取", "宝象国", 91, 84),
    "wubei_wuyi": ("五倍巫医", "宝象国", 93, 122),
    "repair": ("修理", "洛阳城", 321, 110),
    "wuhuan_cloth": ("五环布店", "长安城", 130, 128),
    "wuhuan_yunyou": ("五环云游大师", "长安城", 95, 175),
    "shadu_1": ("沙都路点1", "蟠桃园", 126, 32),
    "shadu_2": ("沙都路点2", "蟠桃园", 66, 87),
    "shadu_3": ("沙都路点3", "蟠桃园", 27, 112),
    "shadu_4": ("沙都路点4", "蟠桃园", 33, 171),
    "shadu_5": ("沙都路点5", "蟠桃园", 111, 130),
    "shadu_6": ("沙都路点6", "蟠桃园", 138, 111),
    "kezhan_dali": ("客栈大理寺", "洛阳城", 223, 195),
    "kezhan_awang": ("客栈阿旺兜底", "洛阳城", 180, 208),
    "kezhan_biwu": ("客栈比武场", "洛阳城", 244, 12),
    "kezhan_inn": ("客栈入口(快送POI)", "洛阳城", 381, 26),
    "zhongzu_claim": ("种族领取", "长安城", 285, 160),
    "xuanshu_task": ("玄枢任务点", "洛阳城", 93, 125),
    "xuanshu_jiantianpan": ("玄枢监天盘", "火云戈壁", 180, 86),
}

# 诊断-五倍（降魔任务）识别与寻路模拟：key → (按钮标签, 期望识别 kind)。
# 降魔任务即五倍任务（同一玩法的不同叫法，用户 2026-11-23 明确）；按钮
# 归入「五倍链路」诊断组。流程：打开任务栏 → task_panel 读取当前五倍
# 任务 → demon_task 识别 → 关闭任务栏 → 按识别出的地图/坐标执行手动
# 寻路（到达即完成）。任务类型由当前实际任务决定：点与任务不符的按钮
# 会提示当前识别结果。
DEMON_NAV_KINDS = {
    "kill": ("五倍·三藏逢魔（击杀）", "kill"),
    "huangpao": ("五倍·智斗黄袍（黄袍怪多轮）", "huangpao"),
    "xianxing": ("五倍·宝象迷情（显形镜 A/B）", "xianxing"),
    "reclaim": ("五倍·殿前献艺（暗雷，需重领）", "reclaim"),
}


def _task_running_here(tm):
    """当前选中窗口是否有任务在跑（诊断按窗口放行，用户 2026-11-23）。

    诊断只操作当前选中窗口（game.find_main_window 跟随选中目标），
    多开时其他窗口的任务运行不应阻塞本窗口诊断——只在「本窗口已启动
    任务」时拒绝，不再要求全部窗口停止。未选中时回退当前角色 ID
    （与消息服务/启动按钮的解析顺序一致）；都解析不到时保持旧的全量
    判定（任一窗口在跑即拒绝，安全兜底）。
    """
    from ..core import user_data
    from . import window_registry
    rid = window_registry.selected_role() or user_data.current_role_id()
    return tm.is_running(rid)


def _game_window_clickable(win):
    """检查游戏窗口中心点是否被其他窗口遮挡（点击会落到遮挡窗口上）。

    截图不依赖前台（窗口 DC 直接读取），但鼠标点击是屏幕坐标——
    游戏被最大化窗口遮挡时点击全落到遮挡窗口上。沿父窗口链向上
    查找，能走到游戏窗口 hwnd 才认为可点击。
    静默操作模式：点击走窗口消息通道、不落屏幕坐标，遮挡无关，
    直接放行。诊断页点击前的前置检查（同时供 control_window 的队伍
    检查复用）。
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


class DiagnosticsMixin(ControlHost):
    def verify_xiuluo_return(self):
        """高级设置-修罗回程验证按钮：与实际返程流程一致，但不右键使用
        回城符——仅将鼠标移动到回城符上定位（不点击）后关闭背包，供人工
        确认整条链路（开背包 → 任务储物格 → 回城符识别）是否正确。

        验证在主进程后台线程执行（不占用任务子进程通道）；
        本窗口任务运行中拒绝验证，避免与任务子进程互抢游戏操作；
        多开时其他窗口的任务运行不影响本窗口诊断（用户 2026-11-23）。
        """
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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

    def verify_mingwang_claim_mozu(self):
        """诊断页-名望-魔族领取（小地图快捷）按钮：仅走小地图快捷领取。"""
        return self._start_mingwang_map_claim_verify("魔族")

    def verify_mingwang_claim_xianzu(self):
        """诊断页-名望-仙族领取（小地图快捷）按钮：仅走小地图快捷领取。"""
        return self._start_mingwang_map_claim_verify("仙族")

    def _start_mingwang_map_claim_verify(self, race):
        """名望种族领取（小地图快捷）验证公共入口：校验环境并起线程。"""
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        if not _game_window_clickable(win):
            return {"status": "error", "msg": "游戏窗口被其他窗口遮挡，请将游戏窗口置于前台后再验证"}
        self._verifying = True
        threading.Thread(target=self._run_mingwang_map_claim_verify,
                         args=(win, race), daemon=True,
                         name=f"mingwang-map-claim-{race}").start()
        return {"status": "success",
                "msg": f"验证已开始（仅小地图快捷领取）：打开{race}地图小地图 → "
                       f"勾选任务筛选 → 点击黄色任务 NPC 名 → 等自动寻路/对话框 → "
                       f"点开启山河名望志 → 打开面板后停止"}

    def _run_mingwang_map_claim_verify(self, win, race):
        """名望种族领取（小地图快捷）验证线程体：结果回推页面与日志窗。"""
        from ..tasks import mingwang  # 延迟导入：避免启动期加载任务模块
        try:
            self.add_logs([f"[验证] 开始验证{race}名望领取（仅小地图快捷领取："
                           f"打开地图小地图 → 勾任务筛选 → 点黄色任务 NPC 名 → "
                           f"自动对话框 → 开启山河名望志 → 打开面板）..."])
            ok, info = mingwang.verify_map_claim_flow(win, race)
        except Exception as e:
            ok, info = False, f"验证异常: {e}"
        try:
            self.add_logs([f"[验证] 结果: {info}"])
        except Exception:
            pass
        self._notify_mingwang_map_claim_verify_result(race, ok, info)
        self._verifying = False

    def _notify_mingwang_map_claim_verify_result(self, race, ok, info):
        """名望种族领取（小地图快捷）验证结果回推页面。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onMingwangMapClaimVerifyResult){{"
                    f"window.onMingwangMapClaimVerifyResult({json.dumps(race)}, "
                    f"{ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_shout(self, target="wubei"):
        """高级设置-喊话测试：向游戏聊天发送一次喊话内容（验证发送
        链路；与验证按钮同款约束——本窗口任务运行中拒绝、游戏须前台）。

        target：wubei/xiuluo/zhongzu 分别发对应任务设置里保存的喊话内容；
        修罗的 {种族} 占位符按必须种族实时替换（_build_shout_text）。
        """
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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

    def get_navigate_maps(self):
        """诊断-地图导航下拉选项：常规地图 + 龙窟/凤巢/大雁塔各层。

        层级图只提供具体层名（基名 龙窟/凤巢/大雁塔 无法直接进入）；
        海底迷宫/地狱迷宫用户暂缓，不在下拉中。
        """
        from ..device import map_nav  # 延迟导入：与其余验证模块一致
        return {"status": "success", "maps": map_nav.all_navigate_maps()}

    def verify_map_navigate(self, map_name=None, x=None, y=None):
        """诊断-地图导航按钮：打开目标地图（含层级图）→ 地图窗口悬停
        读坐标标定像素换算 → 悬停校验后点击地图点寻路 → 等待 HUD
        地名+坐标 ±10 到达。

        2026-11-20 实机验证机制（层级图先点层块进层，层内小地图同样
        支持悬停读坐标）。会真实移动人物，本窗口任务运行中拒绝执行（其他窗口运行不影响），避免与
        任务子进程互抢游戏操作。
        """
        from ..device import map_nav  # 延迟导入：与其余验证模块一致
        name = (map_name or "").strip()
        if name not in map_nav.all_navigate_maps():
            return {"status": "error", "msg": "请选择有效的地图"}
        try:
            x, y = int(x), int(y)
        except (TypeError, ValueError):
            return {"status": "error", "msg": "坐标必须是整数"}
        if not (0 <= x <= 9999 and 0 <= y <= 9999):
            return {"status": "error", "msg": "坐标超出范围（0-9999）"}
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "任务运行中，请先停止任务再导航"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        if not _game_window_clickable(win):
            return {"status": "error",
                    "msg": "游戏窗口被其他窗口遮挡，请将游戏窗口置于前台后再导航"}
        self._verifying = True
        self._map_nav_running = True
        try:
            threading.Thread(target=self._run_map_navigate,
                             args=(win, name, x, y),
                             daemon=True, name="map-navigate").start()
        except Exception as e:  # noqa: BLE001 —— 线程启动失败须复位标志
            self._map_nav_running = False
            self._verifying = False
            return {"status": "error", "msg": f"启动导航线程失败：{e}"}
        return {"status": "success",
                "msg": f"已开始：导航到 {name} ({x},{y})，过程见日志"}

    def verify_site_navigate(self, site=None):
        """诊断-任务点位导航（坐标化测试）：按预置点位执行地图导航。

        与「地图导航」共用执行/中断机制（_map_nav_running、中断寻路
        按钮、日志逐行输出）。点位表见模块级 SITE_NAV_POINTS——用于
        逐个验证任务点位坐标化的实际效果：落点是否在任务「附近」判定
        内、点黄字/POI 是否仍触发游戏行为、到达耗时。真实点击并移动
        人物，本窗口任务运行中拒绝执行（其他窗口运行不影响）。
        """
        from ..device import map_nav  # 延迟导入：与其余验证模块一致
        key = (site or "").strip()
        spec = SITE_NAV_POINTS.get(key)
        if spec is None:
            return {"status": "error", "msg": f"未知点位: {key or '(空)'}"}
        label, name, x, y = spec
        if name not in map_nav.all_navigate_maps():
            return {"status": "error",
                    "msg": f"{label}: 地图「{name}」不在导航地图表"}
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "任务运行中，请先停止任务再导航"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error", "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        if not _game_window_clickable(win):
            return {"status": "error",
                    "msg": "游戏窗口被其他窗口遮挡，请将游戏窗口置于前台后再导航"}
        self._verifying = True
        self._map_nav_running = True
        try:
            threading.Thread(target=self._run_map_navigate,
                             args=(win, name, x, y, key),
                             daemon=True, name="site-navigate").start()
        except Exception as e:  # noqa: BLE001 —— 线程启动失败须复位标志
            self._map_nav_running = False
            self._verifying = False
            return {"status": "error", "msg": f"启动导航线程失败：{e}"}
        return {"status": "success",
                "msg": f"已开始：{label} → {name} ({x},{y})，过程见日志"}

    def cancel_map_navigate(self):
        """中断正在进行的诊断-地图导航（协作式，最长约 1-3s 生效）。

        只在确有导航线程时生效（普通地图导航与任务点位测试共用）；
        中断后导航线程负责关地图/寻路面板并回推结果「已中断寻路」。
        人物身上的客户端自动寻路不会停止。
        """
        from ..device import map_nav  # 延迟导入：与其余验证模块一致
        if not getattr(self, "_map_nav_running", False):
            return {"status": "ignored", "msg": "当前没有进行中的地图导航"}
        map_nav.request_cancel()
        return {"status": "success", "msg": "已发送中断请求（1-3 秒内停止）"}

    def _run_map_navigate(self, win, name, x, y, site=None):
        """地图导航线程体：逐行写本地日志文件与日志窗，结束回推页面。

        site 非空 = 任务点位测试（回推 onSiteNavResult，按点位行显示）。
        """
        from ..device import map_nav  # 延迟导入：与其余验证模块一致

        def emit(msg):
            app_utils.log(f"[诊断] {msg}")
            try:
                self.add_logs([f"[诊断] {msg}"])
            except Exception:
                pass

        # 整个函数体兜 finally：UserAbort 等 BaseException 逃出时也要复位
        # 标志（否则 _verifying 永久 True，所有诊断验证都报「验证进行中」）
        try:
            try:
                if site == "kezhan_inn":
                    # 客栈入口走真实任务链路：坐标点击会命中玩家路标/
                    # 飞行旗菜单，需要点「快送我去」后再按客栈地名确认
                    from ..tasks import kezhan
                    ok, info = kezhan.verify_go_to_inn(win)
                else:
                    ok, info = map_nav.navigate_to_map_coordinate(
                        win, name, x, y, log_fn=emit, verbose=True)
            except Exception as e:
                ok, info = False, f"导航异常: {e}"
            label = SITE_NAV_POINTS[site][0] if site else ""
            result_line = f"{label}: {info}" if label else info
            try:
                app_utils.log(f"[诊断] 地图导航结果: {result_line}")
                self.add_logs([f"[诊断] 地图导航结果: {result_line}"])
            except Exception:
                pass
            if site:
                self._notify_site_nav_result(site, ok, info)
            else:
                self._notify_map_navigate_result(ok, info)
        finally:
            self._map_nav_running = False
            self._verifying = False

    def _notify_map_navigate_result(self, ok, info):
        """地图导航结果回推页面（诊断-地图导航行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onMapNavigateResult){{window.onMapNavigateResult({ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def _notify_site_nav_result(self, site, ok, info):
        """任务点位导航结果回推页面（对应点位行的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onSiteNavResult){{window.onSiteNavResult({json.dumps(site)}, {ok_js}, {json.dumps(info)});}}")
            except Exception:
                pass

    def verify_demon_navigate(self, kind=None):
        """诊断-五倍（降魔任务）识别与寻路模拟：打开任务栏 → 识别当前
        五倍任务 → 关闭任务栏 → 按识别出的地图/坐标手动寻路（显形镜
        依次 A、B），寻路完成即算完成；暗雷/锁妖网只识别并提示需重新领取。

        与「地图导航」共用执行/中断机制（_map_nav_running、中断寻路
        按钮、日志逐行输出）。会真实移动人物，本窗口任务运行中拒绝执行（其他窗口运行不影响）。
        """
        key = (kind or "").strip()
        spec = DEMON_NAV_KINDS.get(key)
        if spec is None:
            return {"status": "error", "msg": f"未知任务类型: {key or '(空)'}"}
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "任务运行中，请先停止任务再模拟"}
        if self._verifying:
            return {"status": "error", "msg": "验证进行中，请稍候"}
        win = game.find_main_window()
        if win is None:
            return {"status": "error",
                    "msg": "未检测到游戏窗口（游戏未启动或已最小化）"}
        if not _game_window_clickable(win):
            return {"status": "error",
                    "msg": "游戏窗口被其他窗口遮挡，请将游戏窗口置于前台后再模拟"}
        self._verifying = True
        self._map_nav_running = True
        try:
            threading.Thread(target=self._run_demon_navigate,
                             args=(win, key),
                             daemon=True, name="demon-navigate").start()
        except Exception as e:  # noqa: BLE001 —— 线程启动失败须复位标志
            self._map_nav_running = False
            self._verifying = False
            return {"status": "error", "msg": f"启动模拟线程失败：{e}"}
        return {"status": "success",
                "msg": f"已开始：{spec[0]}（打开任务栏→识别→寻路），过程见日志"}

    def _run_demon_navigate(self, win, kind):
        """降魔任务模拟线程体：识别 → 校验类型 → 寻路 → 回推页面。

        kill/huangpao：识别出的地图 + coord_a 寻路一次；
        xianxing：先 A 再 B（识别出 B 时），两段寻路都到达才算完成；
        reclaim：只识别并提示需重新领取（不移动人物）。
        """
        from ..device import demon_task, map_nav, task_panel

        spec = DEMON_NAV_KINDS.get(kind) or (kind, kind)
        label, expected = spec

        def emit(msg):
            app_utils.log(f"[诊断] {msg}")
            try:
                self.add_logs([f"[诊断] {msg}"])
            except Exception:
                pass

        ok, info = False, "未知错误"
        try:
            try:
                emit(f"五倍（降魔任务）模拟开始：{label}")
                result, rinfo = task_panel.read_first_group_task(win)
                if result is None:
                    ok, info = False, f"任务栏读取失败: {rinfo}"
                else:
                    rec = result.get("recognition") or {}
                    emit(f"识别结果：{demon_task.describe(rec)}")
                    closed, cinfo = task_panel.close_task_panel(win)
                    emit(f"关闭任务栏：{'成功' if closed else '失败'}（{cinfo}）")
                    got = rec.get("kind")
                    if not closed:
                        ok, info = False, f"关闭任务栏失败: {cinfo}"
                    elif got != expected:
                        ok, info = False, (
                            f"当前任务「{rec.get('task') or '?'}」识别为 "
                            f"{demon_task.describe(rec)}，与按钮要求"
                            f"「{label}」不符；请领取对应任务后重试")
                    elif expected == "reclaim":
                        ok, info = True, (
                            f"识别为需重新领取（{rec.get('task') or '?'}）："
                            f"{rec.get('reclaim_reason')}（诊断到识别为止）")
                    else:
                        map_name = rec.get("map")
                        coord = rec.get("coord_a")
                        if not map_name or not coord:
                            ok, info = False, (
                                f"识别结果缺少地图/坐标："
                                f"{demon_task.describe(rec)}")
                        elif map_name not in map_nav.all_navigate_maps():
                            ok, info = False, f"地图「{map_name}」不在导航地图表"
                        else:
                            legs = [("A", tuple(coord))]
                            if expected == "xianxing" and rec.get("coord_b"):
                                legs.append(("B", tuple(rec["coord_b"])))
                            success = True
                            done = []
                            for leg_label, leg_coord in legs:
                                emit(f"寻路[{leg_label}] → "
                                     f"{map_name} {leg_coord}")
                                ok_leg, nav_info = \
                                    map_nav.navigate_to_map_coordinate(
                                        win, map_name, leg_coord[0],
                                        leg_coord[1], log_fn=emit, verbose=True)
                                emit(f"寻路[{leg_label}]结果：{nav_info}")
                                done.append(
                                    f"{leg_label} {leg_coord}"
                                    f"{'到达' if ok_leg else '失败'}")
                                if not ok_leg:
                                    success = False
                                    info = (f"寻路[{leg_label}]失败：{nav_info}"
                                            + ("；已完成 " + "、".join(done[:-1])
                                               if len(done) > 1 else ""))
                                    break
                            if success:
                                ok, info = True, (
                                    f"寻路完成（{'；'.join(done)}）"
                                    f"——按寻路完成算完成")
            except Exception as e:  # noqa: BLE001 —— 线程体兜底
                ok, info = False, f"模拟异常: {e}"
            try:
                app_utils.log(f"[诊断] 五倍（降魔任务）模拟结果: {info}")
                self.add_logs([f"[诊断] 降魔任务模拟结果: {info}"])
            except Exception:
                pass
            self._notify_demon_nav_result(kind, ok, info)
        finally:
            self._map_nav_running = False
            self._verifying = False

    def _notify_demon_nav_result(self, kind, ok, info):
        """降魔任务模拟结果回推页面（对应类型按钮的结果文案）。"""
        if self.webview_instance:
            try:
                ok_js = "true" if ok else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.onDemonNavResult){{window.onDemonNavResult("
                    f"{json.dumps(kind)}, {ok_js}, {json.dumps(info)});}}")
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
        extras: dict[str, Any] = {}
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
        allow_activate = not _task_running_here(self._tm)
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
        if _task_running_here(self._tm):
            return {"status": "error", "msg": "本窗口任务运行中，请先停止本窗口任务再验证"}
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
