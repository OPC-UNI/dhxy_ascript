"""组队流程编排（五倍 / 修罗 / 种族比武共用）。

只做任务无关的编排：检查队伍、自动建队、缺人补人、喊话、非队长保持
与自动归队。任务差异全部由构造参数注入：

- prefix：日志前缀（如 "[五倍]"），派生 "[创建队伍]"/"[归队]" 后缀
- min_members：最少人数，可传 int 或返回 int 的 callable
- auto_rejoin：读取自动归队开关的 callable
- shout_text：读取本轮喊话内容的 callable（空字符串=不喊话）
- check/recog/shout interval：沿用任务模块的校准常量

组队机制本身仍在 device/team.py；本模块只负责流程与日志。
"""
import re
import time

from ascript.windows.hardware.keyboard import Keyboard

from ..core import app_utils
from ..core.app_utils import log
from ..device import battle
from ..device import tab_group
from ..device import team
from ..device.battle import is_in_battle, is_in_battle_frame


# 战斗中队伍检查顺延（2026-11-26 实机教训）：12:39:48 首次检查按钮未命中，
# 重试 sleep 2s 跨进 12:39:51 的第 29 场战斗，12:39:54 在战斗中把「组队
# 菜单中无『队伍信息』」当确定性未组队，队长被误转保持模式跳过核心流程。
# 战斗中组队按钮/菜单不可读、队伍面板会干扰战斗——队伍身份一律不在战斗
# 期间判定。该描述刻意不含「未组队」「非队长」字样，不触发
# team.is_definitive_not_leader（调用方按瞬态失败在战后重查）。
BATTLE_DEFER_INFO = "战斗中，队伍检查顺延到战斗结束后重查"


def _in_battle(win):
    """战斗判定：战斗线程确认态优先（零成本内存读），单帧判定兜底。

    战斗线程的确认态覆盖战斗全程；进场确认前约 1s 的窗口由单帧模板
    判定补位。组号 pass 期间 is_in_battle 恒 True，检查同样顺延。
    """
    return battle.watch_in_battle() or is_in_battle(win)


class TeamFlow:
    """一个任务模式的组队流程实例（持有喊话限频状态）。"""

    def __init__(self, *, prefix, min_members, auto_rejoin, shout_text,
                 check_retry_interval=10, recog_retry_interval=3,
                 shout_min_interval=30, kick_overdue=None, kick_timeout=None):
        self.prefix = prefix
        self.create_prefix = prefix + "[创建队伍]"
        self.rejoin_prefix = prefix + "[归队]"
        self._min_members = min_members
        self._auto_rejoin = auto_rejoin
        self._shout_text = shout_text
        self.check_retry_interval = check_retry_interval
        self.recog_retry_interval = recog_retry_interval
        self.shout_min_interval = shout_min_interval
        # 暂离超时自动请离（修罗设置开关/超时）：None = 保持原行为
        # （始终请离、默认阈值）；callable/常量由任务注入。关闭时组号
        # 暂离队员归队 pass 仍照常执行（仅不请离）。
        self._kick_overdue = kick_overdue
        self._kick_timeout = kick_timeout
        self._shout_last_at = 0.0

    def _team_min(self):
        value = self._min_members
        return value() if callable(value) else value

    def _kick_overdue_now(self):
        value = self._kick_overdue
        if value is None:
            return True
        return bool(value() if callable(value) else value)

    def _kick_timeout_now(self):
        value = self._kick_timeout
        return value() if callable(value) else value

    def check_team_ready(self, win, create_if_missing=False):
        """领取前队伍检查：一次面板开关，结果顺带更新悬浮层队伍行。

        战斗期间不检查；检查期间开打的结果一并作废（见 BATTLE_DEFER_INFO）
        ——战斗中组队菜单/面板读数不可信，交调用方战后重查。
        """
        if _in_battle(win):
            return "not_leader", BATTLE_DEFER_INFO
        status, info, members = team.check_team_min(
            win, min_members=self._team_min(), allow_kick=True,
            create_if_missing=create_if_missing, prefix=self.create_prefix,
            kick_timeout=self._kick_timeout_now(),
            kick_overdue=self._kick_overdue_now())
        if _in_battle(win):
            # 检查期间进入战斗：本次读到的身份/人数可能来自战斗界面或
            # 被战斗打断的菜单，不能作为任何确定性结论；不写悬浮层
            return "not_leader", BATTLE_DEFER_INFO
        team.update_overlay_team(status, info, members)
        return status, info

    def check_team_with_retry(self, win, tries=3, create_if_missing=False):
        """队伍检查带瞬态失败重试：截窗失败/面板打不开可能是瞬时问题，
        一次失败即转非队长保持模式会把队长永久挂起。未组队与明确非队长
        是确定性状态，不重试。

        已确认队长（team.known_leader() is True）时身份未决（菜单打不开/
        按钮未命中/截图失败）**不返回「非队长」**：返回 wait 交调用方按
        队长继续重试（用户 2026-11-14：只有明确证据——菜单无「队伍信息」
        （duiwuxinxi）或面板打开后无「解散队伍」（jiesanduiwu）——才判
        非队长；瞬态两三次失败不能把队长降级）。"""
        last = ("not_leader", "队伍检查未执行")
        for i in range(tries):
            if _in_battle(win):
                # 战斗中不判身份：不打开组队菜单、不消耗重试，直接交
                # 调用方按瞬态处理（战后再重查）
                return "not_leader", BATTLE_DEFER_INFO
            status, info = self.check_team_ready(
                win, create_if_missing=create_if_missing)
            last = (status, info)
            if info == BATTLE_DEFER_INFO:
                # 检查期间开打：结果已作废，战斗中菜单不可读，不再重试
                return "not_leader", BATTLE_DEFER_INFO
            if status != "not_leader":
                return status, info
            # 确定性非队长（未组队 / 面板打开且无解散按钮）：立即返回
            if team.is_definitive_not_leader(info):
                return status, info
            if i < tries - 1:
                log(f"{self.prefix} {info}，瞬态失败重试"
                    f"（{i + 1}/{tries - 1}）", "WARN")
                time.sleep(2.0)
        status, info = last
        if status == "not_leader" and team.known_leader() is True:
            # 身份未决但已确认过队长：保持队长状态（wait），调用方继续
            # 重试/等待，不转保持模式
            return "wait", f"{info}（已确认队长，保持队长状态重试）"
        return last

    def auto_create_team_once(self, win):
        """执行一次自动创建队伍（调用方已做开关门控）。"""
        try:
            ok, info = team.ensure_team_created(win, prefix=self.create_prefix)
        except Exception as e:
            ok, info = False, f"创建队伍异常: {e}"
        log(f"{self.create_prefix} {info}", "SYS" if ok else "WARN")

    def maybe_tab_bars_pass(self, win):
        """组号模式：点完任务追踪（寻路中）检查队员血蓝。"""
        try:
            if tab_group.active():
                tab_group.member_bars_pass(win)
        except Exception as e:
            log(f"{self.prefix} 组号队员血蓝 pass 异常: {e}", "WARN")

    def shout_recruit(self, win):
        """缺人自动喊话：输入内容 → Enter 发送到游戏当前默认频道。

        整段打字+回车持跨进程聊天输入互斥（主进程远程 /shout 可能与
        本喊话同时发生，不互斥会把两条文案拼成一条）；等锁超时放弃本
        轮、不记时间戳，下轮重试。静默模式走 WM_CHAR 逐字符注入，无需
        前台；投递失败同样不记时间戳。
        """
        text = self._shout_text()
        with app_utils.chat_input_lock() as locked:
            if not locked:
                log(f"{self.prefix} 喊话等待输入锁超时，下轮重试", "WARN")
                return
            if app_utils.silent_mode_on():
                from ..device import silent_click  # 延迟导入：避免模块级循环
                info = silent_click.send_silent_text(win, text, end_enter=True)
                if info is None:
                    log(f"{self.prefix} 静默喊话投递失败（窗口不存在）", "WARN")
                    return
                self._shout_last_at = time.time()
                log(f"{self.prefix} 已静默喊话: {text}", "SYS")
                return
            # 键盘事件只进前台窗口；喊话偶现静默丢失，先激活游戏窗口
            win.activate()
            time.sleep(0.5)
            # 游戏聊天输入常驻（无需 Enter 打开）：直接输入 + Enter 发送
            Keyboard.input(text)
            time.sleep(0.8)  # 等待剪贴板粘贴完成（中文走 Ctrl+V）
            Keyboard.key("enter")  # 发送
            time.sleep(0.5)
            self._shout_last_at = time.time()
            log(f"{self.prefix} 已自动喊话: {text}", "SYS")

    def keep_alive(self, win, info):
        """非队长：核心流程不执行，保持任务运行（战斗线程自动操作）。

        置位 battle.wuyi_fast_idle：队伍共享的巫医对话框由队长触发，
        本进程不设 wuyi_expected_at，空闲节奏提升到 2s。自动归队：死亡
        落轮回司后按 REJOIN_POLL_INTERVAL 轮询、REJOIN_RETRY_INTERVAL
        放慢重试。
        """
        log(f"{self.prefix} {info}，跳过核心流程，仅保持战斗线程运行", "SYS")
        battle.wuyi_fast_idle = True
        auto_rejoin = self._auto_rejoin()
        log(f"{self.prefix} 自动归队: "
            f"{'开（死亡落轮回司自动点归队回队长）' if auto_rejoin else '关'}",
            "SYS")
        next_rejoin_at = 0.0
        while True:
            time.sleep(1)
            if not auto_rejoin or tab_group.pass_active:
                continue
            now = time.time()
            if now < next_rejoin_at:
                continue
            next_rejoin_at = now + team.REJOIN_POLL_INTERVAL
            # 战斗判定只在轮询到期时执行（is_in_battle 是截窗+模板匹配）
            if is_in_battle(win):
                continue
            try:
                handled, desc = team.maybe_rejoin(
                    win, prefix=self.rejoin_prefix)
                if handled:
                    log(f"{self.rejoin_prefix} {desc}",
                        "SYS" if desc.startswith("已归队") else "WARN")
                    next_rejoin_at = time.time() + team.REJOIN_RETRY_INTERVAL
            except Exception as e:
                log(f"{self.rejoin_prefix} 归队检查异常: {e}", "WARN")

    def monitor_team_and_fill(self, win, create_if_missing=False):
        """队长缺人监控循环（领取前与被拒后共用）。

        先看申请人证据，有则按缺口一次补满（缺 N 补 N）；无则查人数：
        缺人按间隔重查、人满返回；非队长转保持模式（不返回）。缺人且
        无申请人时按 shout_min_interval 限频喊话招募。
        """
        shortfall = None  # 最近一次人数检查的缺口；None=未知按 1 人保守补
        battle_wait_logged = False
        while True:
            # 战斗中不查队伍、不补人（组队菜单/申请面板会干扰战斗且读数
            # 不可信）；战斗线程确认态是内存读，先短路免截帧；非战斗时
            # 本帧同时给申请人证据判定复用。只在进入等待时记一条，不刷屏，
            # 3s 轮询便于战斗结束立即恢复检查
            in_battle = battle.watch_in_battle()
            img = None
            if not in_battle:
                img = win.capture(force_activate=False)
                in_battle = img is not None and is_in_battle_frame(img)
            if in_battle:
                if not battle_wait_logged:
                    battle_wait_logged = True
                    log(f"{self.prefix} 战斗中，队伍检查/补人顺延到战斗结束后",
                        "INFO")
                time.sleep(self.recog_retry_interval)
                continue
            battle_wait_logged = False
            if img is not None and team.has_pending_apply(img):
                ok_fill, fill_info = team.fill_team(
                    win, target=self._team_min(), max_allow=shortfall or 1)
                log(f"{self.prefix} 补人: {fill_info}",
                    "SYS" if ok_fill else "INFO")
                if ok_fill:
                    continue  # 已补人，立即重查
            status, info = self.check_team_with_retry(
                win, create_if_missing=create_if_missing)
            create_if_missing = False  # 自动创建只在首次检查内执行
            if status == "not_leader":
                if team.is_definitive_not_leader(info):
                    self.keep_alive(win, info)  # 无限循环，用户手动停止
                # 瞬态失败（遮挡/加载/按钮未命中等）：不退出监控，按间隔重查；
                # 战斗中/识别失败用快节奏，战后立即恢复检查
                interval = (self.recog_retry_interval
                            if "识别失败" in info or info == BATTLE_DEFER_INFO
                            else self.check_retry_interval)
                log(f"{self.prefix} {info}（瞬态失败，{interval}s 后重查）",
                    "WARN")
                time.sleep(interval)
                continue
            if status == "ready":
                return
            # 人数不足信息里解析缺口（识别失败/申请态无人数时保留旧值）
            m = re.search(r"队伍人数不足（(\d+)/(\d+)", info)
            if m:
                shortfall = max(0, int(m.group(2)) - int(m.group(1)))
            # 等待状态下复查申请证据：申请人可能恰在两次截帧间出现
            img = win.capture(force_activate=False)
            if img is not None and team.has_pending_apply(img):
                ok_fill, fill_info = team.fill_team(
                    win, target=self._team_min(), max_allow=shortfall or 1)
                log(f"{self.prefix} 补人: {fill_info}",
                    "SYS" if ok_fill else "INFO")
                if ok_fill:
                    continue  # 已补人，立即重查
            interval = (self.recog_retry_interval if "识别失败" in info
                        else self.check_retry_interval)
            # 缺人自动喊话（间隔限频；有申请人正在补时不喊话）
            if self._shout_text() and \
                    time.time() - self._shout_last_at >= self.shout_min_interval:
                try:
                    self.shout_recruit(win)
                except Exception as e:
                    log(f"{self.prefix} 喊话失败: {e}", "WARN")
            log(f"{self.prefix} {info}，暂无申请人可补，{interval}s 后重查",
                "WARN")
            time.sleep(interval)
