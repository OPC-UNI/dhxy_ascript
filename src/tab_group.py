"""组号专用（一窗口多 Tab 多账号）：队员自动挂战斗、巫医、补血补蓝。

机制（2026-10-31 实机验证，多标签客户端 xy2_tab_x64 系列）：
- 每个登录 Tab 对应主窗口一个 Win32Window 子窗口，标题格式
  「[大话西游2_经典版] 大话西游2经典版 $Revision: xxx - 服务器
  - 角色名（ID：账号）」（角色名解析见 _tab_char_name，与
  game.read_login_tab 同款：split " - " 取末段）；当前 Tab 子窗口
  可见、其余隐藏——计数/登录判定/当前身份全部走句柄枚举
  （game.list_tab_windows），零 OCR
- 切换：Ctrl+Tab（Keyboard.key_hot 经前台窗口送达，切换前校验前台）；
  TCM_SETCURSEL 消息与 Alt+Tab 实测均无效（2026-10-31 探测；
  2026-11-04 定位 SysTabControl32 子窗口后复测仍无效：控件选中
  序号变化但游戏视图不切——客户端不响应 TCN_SELCHANGE）
- 静默操作且游戏不在前台：键盘事件只进前台窗口且不允许激活，
  Ctrl+Tab 不可用——改走 Tab 条消息点击（UIA 矩形 + DOWN/UP 最小
  序列，silent_click.send_silent_tab_click；2026-11-04 实机验证：
  视图切换、客户端稳定），切换成败仍按可见子窗口句柄变化判定
  （与键盘路径同判据）；未登录 Tab 按 UIA 名（= 游戏标题）跳过
  不切换（_next_logged_index），不浪费切换轮次。矩形缓存为客户区
  坐标、窗口移动不失效；未生效时丢弃缓存重读 UIA 重试同一 Tab
  （2026-11-10 拖动窗口后整轮切换失效的自愈）
- 【禁区】Tab 条（SysTabControl32 子窗口）带指针的消息不可投递：
  2026-11-04 实测 TCM_GETITEMRECT 的 lParam 裸指针跨进程不封送，
  游戏按我方虚拟地址在自身进程写内存直接崩溃退出（无 WER 记录，
  客户端内部处理）——TCM 系仅用 int-only 消息（GETITEMCOUNT/
  GETCURSEL）
- pass 期间 pass_active 置位：战斗线程与任务线程轮询见此标志跳过
  本轮处理（防误点队员屏幕/误判战斗结束）；pass 由单线程同步执行
- 护栏：Tab 条区域（窗口相对 y < 60）为鼠标禁入区，pass 点击经
  _guarded_click 过滤；切 Tab 只走 Ctrl+Tab/静默 Tab 条点击，
  普通流程点击永不碰 Tab 条
- 激活条件（active()）：init_task 时开关开 + 已登录 Tab ≥2 + 启动
  Tab ID 可解析，且当前任务模式仍为修罗/五倍——单号（已登录 Tab
  <2）或开关关闭时全部新代码路径不执行，行为与旧版完全一致。
  init 阶段只陈述启动 Tab 与登录数；队长/组号结论必须由
  startup_check（队伍检查 + Tab 标题角色名比对）给出
"""
import difflib
import re
import threading
import time

from PIL import Image
from ascript.windows.hardware.keyboard import Keyboard
from ascript.windows.window.Window import WindowCaptureFormat

from .app_utils import (log, human_click, move_away, current_task_mode,
                        fixed_pos_get, fixed_pos_set, norm_name_for_cmp)
from . import app_utils  # 静默操作门控（silent_mode_on/keyboard_ok）
from .control_window import (load_high_perf,  # 高性能开关：巫医 pass 直点门控
                             load_group_actions,  # 组号成员战斗动作（按账号 ID）
                             load_battle_actions,  # 通用战斗动作（队长回退）
                             save_group_last_scan,  # 队伍检查快照（组号页自动展示）
                             CHAR_BATTLE_ACTIONS,  # 组号设置页下拉选项（人物）
                             BEAST_BATTLE_ACTIONS)  # 组号设置页下拉选项（召唤兽）
from . import game  # 子窗口枚举（每个 Tab 一个 Win32Window）
from . import map as game_map  # 模板匹配快速接口
from . import status_bars  # 血蓝条检测回满（队员 pass 复用）
from . import task_tracker  # 对话框区域 OCR 与绿色选项定位
from .dialog_common import _dialog_text_present  # 巫医对话框文案模糊匹配

# ---- 时序与预算参数（秒） ----
TAB_SWITCH_SETTLE = 0.8    # Ctrl+Tab 后等待标题/可见性更新
TAB_SWITCH_TRIES = 3       # Tab 条点击最大尝试次数（未生效时重试同一
                           # Tab；每次重试前丢弃矩形缓存重读 UIA——窗口
                           # 移动/布局变化会让缓存坐标过时，2026-11-10）
TAB_CYCLE_GUARD = 10       # 切回队长/遍历队员的最大切换次数
NO_CLICK_ZONE_Y = 60       # Tab 条区域（窗口相对 y<60）：鼠标禁入区
AUTO_WAIT_FIRST = 5.0      # 首场战斗队员自动按钮等待上限（操作条轮询）
AUTO_WAIT_ROUTINE = 1.0    # 每 5 场例行队员自动按钮等待上限（多半已挂自动）
BARS_PASS_INTERVAL = 60.0  # 队员血蓝检查最小间隔（寻路中触发，节流）
WUYI_DIALOG_WAIT = 3.0     # 队员巫医对话框出现等待上限
WUYI_OPTION_Y_OFFSET = 6   # 选项点击 y 下移修正：绿字中心比可点击中心偏高
                           # （2026-11-02 实测点击落在「复」字上方、对话框
                           # 不关），下移半行高（选项字高 ~13px）落在文字
                           # 中部——点中选项即由游戏机制保证关闭，不做事后回验
WUYI_POS_FRESH = 30.0      # 队长选项坐标新鲜期：期内队员 pass 可直点复用
                           # （队长点击后 pass 数秒内开始，30s 足够覆盖）
PASS_TOTAL_DEADLINE = 30.0  # 单次 pass 总时长上限（防异常拖垮主流程）
# 配置动作队员的 pass 时长（2026-11-07 组号战斗动作）：每名配置队员
# 追加 MEMBER_ACT_BUDGET 预算（取消自动+等操作条+人物/召唤兽动作+
# 点自动，典型 15~25s/人）；BASE 覆盖切换与截图开销。上限防异常
# 拖垮主流程，超时未处理的队员下一场例行 pass 兜底（自动跨场持续）
MEMBER_ACT_DEADLINE_BASE = 60.0
MEMBER_ACT_BUDGET = 50.0
REJOIN_PASS_MIN_INTERVAL = 30.0  # 归队 pass 最小间隔：队伍检查每 10s 一次，
                           # 暂离队员持续在场会每轮触发——30s 节流防频繁
                           # 切 Tab；归队成功后暂离名单消失自然不再触发
REJOIN_NAME_RATIO = 0.7     # Tab 标题角色名与面板 OCR 昵称的模糊比阈值

# ---- 状态（任务进程内共享） ----
_active = False        # 组号模式是否激活（init_task 置位）
_mode = ""             # 激活时的任务模式（active() 校验模式仍为修罗/五倍）
_leader_id = None      # 队长账号 ID（任务启动时可见 Tab 子窗口标题解析）
_tab_count = 0         # 窗口内 Tab 总数
_logged_count = 0      # 已登录 Tab 数
_members = 0           # 队员数（已登录 - 队长）
_team_member_ids = set()  # 启动检查确认「在队伍中」的队员账号 ID：仅这些
                          # Tab 套用组号战斗动作配置（未确认的维持旧行为只
                          # 点自动；启动成员检查时写入，init_task 清空）
_acted_ids = set()        # 本次运行已执行过配置动作的队员账号 ID：动作只
                          # 执行一次（与队长对称，自动跨场持续），pass 再次
                          # 触发（补挂自动场）时这些队员直接点自动即可
                          # （2026-11-07 用户反馈状态机），init_task 清空
_no_members_logged = False  # 「未确认队员 Tab 跳过 pass」告警去重（每任务一次）
pass_active = False    # pass 进行中：战斗线程/任务线程轮询见此跳过处理
_last_bars_pass_at = 0.0  # 上次队员血蓝 pass 时间（节流）
_last_rejoin_pass_at = 0.0  # 上次归队 pass 时间（节流）
_pass_lock = threading.Lock()  # pass 入口互斥：同一时刻至多一个 pass（双线程防撞车）


def _try_enter_pass():
    """pass 互斥入口：另一 pass 进行中则放弃本轮（返回 False）。

    战斗线程的自动 pass 与任务线程的巫医/血蓝 pass 理论上可能撞车
    （寻路中遇敌 → 战斗线程进场挂自动 pass，任务线程恰好在跑血蓝
    pass），双线程同时 Ctrl+Tab 会切乱 Tab——入口加锁，后到者放弃、
    等下一轮时机（每 5 场/60s 节流/下轮巫医）。
    """
    global pass_active
    with _pass_lock:
        if pass_active:
            return False
        pass_active = True
        return True


def _exit_pass():
    """pass 结束：清除抑制标志（与 _try_enter_pass 配对，finally 调用）。"""
    global pass_active
    with _pass_lock:
        pass_active = False


def init_task(win, mode, switch_on):
    """任务启动时初始化组号状态（开关开才枚举，一次）。

    激活条件：mode ∈ {修罗, 五倍} 且 switch_on 且已登录 Tab ≥2 且
    当前 Tab ID 可解析（可见子窗口标题）。不满足时置不激活——单号
    （已登录 Tab <2）行为与旧版完全一致。枚举失败只告警不激活。

    注意（2026-11-07 用户纠正）：本阶段只能陈述「在哪个 Tab 启动、
    窗口内登录了几个 Tab」——_leader_id 只是启动所在 Tab 的账号 ID
    （队长候选），谁当队长/是否是组号必须等 startup_check 队伍检查
    + 名称比对后的结论，激活日志不得提前下结论。
    """
    global _active, _mode, _leader_id, _tab_count, _logged_count, _members, \
        _team_member_ids, _acted_ids, _no_members_logged
    _active = False
    _mode = ""
    _leader_id = None
    _tab_count = 0
    _logged_count = 0
    _members = 0
    _team_member_ids = set()
    _acted_ids = set()
    _no_members_logged = False
    if mode not in ("修罗", "五倍") or not switch_on:
        return
    try:
        children = game.list_tab_windows(win)
    except Exception as e:
        log(f"[组号] 枚举子窗口失败: {e}", "WARN")
        return
    _tab_count = len(children)
    logged = [c for c in children if c["id"]]
    _logged_count = len(logged)
    if _logged_count < 2:
        log(f"[组号] 窗口内 {_tab_count} 个 Tab、已登录 {_logged_count} 个（<2），"
            f"组号模式不激活，按单号流程执行", "SYS")
        return
    visible = [c for c in children if c["visible"] and c["id"]]
    if not visible:
        log("[组号] 未识别到当前 Tab 身份（无可见已登录子窗口），组号模式不激活", "WARN")
        return
    _leader_id = visible[0]["id"]  # 启动所在 Tab 的账号 ID（队长候选，
                                   # 是否队长由 startup_check 队伍检查确认）
    _members = _logged_count - 1
    _mode = mode
    _active = True
    # 其余 Tab 角色名一并列出（标题「角色名（ID：账号）」，解析失败
    # 回退账号 ID——用户 2026-11-07 要求，方便人工核对对应关系）；
    # 只陈述事实：这些 Tab 是否组员待队伍检查 + 名称比对确认
    other_names = [_tab_char_name(c["title"]) or c["id"]
                   for c in children if c["id"] and c["id"] != _leader_id]
    other_txt = f"；其余已登录 Tab：{'、'.join(other_names)}" if other_names else ""
    start_char = _tab_char_name(visible[0]["title"]) or _leader_id
    log(f"[组号] 本任务在 Tab「{start_char}」（ID {_leader_id}）启动："
        f"窗口内 {_tab_count} 个 Tab、已登录 {_logged_count} 个{other_txt}；"
        f"队长与组号结论待队伍检查确认", "SYS")


def active():
    """组号模式是否激活（任务模式须仍为修罗/五倍）。

    多任务顺序执行时切到其他模式（如客栈）后不激活，防误切 Tab。
    """
    return _active and current_task_mode() == _mode


def ready():
    """组号基础条件是否具备（已登录 Tab ≥2 且队长 ID 可解析）。

    验证模式用：主进程没有任务模式（active() 需 current_task_mode
    匹配），验证时用 ready() + pass(force=True) 绕过模式校验。
    """
    return _leader_id is not None and _logged_count >= 2


def leader_id():
    """当前队长账号 ID（组号激活时解析；未激活/单号 None）。"""
    return _leader_id


def startup_check(win, create_if_missing=False, prefix="[组号]"):
    """组号模式启动检查：一次打开队伍面板完成队长判定与成员昵称读取。

    战斗中先等战斗结束（队伍面板战斗中不可用，误报非队长会停任务）；
    队长三分类（classify_leader_with_panel，面板保持打开）；unclear
    3s 后同面板重判一次（不再重新开面板）；队长时同面板读成员昵称
    （team.panel_member_names）供 startup_member_check 复用。
    旧链路启动开 3 次队伍面板（队长判定 + 复核重开 + 成员检查），
    2026-11-07 用户反馈后合并为 1 次。
    create_if_missing=True：自动创建队伍并入本次菜单打开（未组队时
    直接点「创建队伍」，见 team.open_team_panel）——启动时不再先单独
    开菜单查是否组队（2026-11-11 组号逻辑优化）。
    返回 (是否队长, 描述, 成员昵称或None)；昵称仅队长时读取，
    None 表示读不到（成员检查按宽松处理）。
    """
    from . import team  # 函数内导入：team 依赖 app_utils，避免循环依赖
    from .battle import is_in_battle  # 函数内导入，避免循环依赖
    deadline = time.time() + 90
    while is_in_battle(win) and time.time() < deadline:
        time.sleep(2.0)
    # 申请待处理态清场（2026-11-07 用户结论）：item2 命中即确定性
    # 队长，但申请态下组队菜单打不开、队长判定必失败（22:03 停任务
    # 教训）；先点按钮开面板 → 清空申请列表 → 关面板，再走正常判定。
    # 启动时的申请多为陈旧请求，缺人由监控循环喊话等新申请
    hit, clear_info = team.clear_pending_apply(win)
    if hit:
        log(f"[组号] 启动清场: {clear_info}", "SYS")
    kind, info = team.classify_leader_with_panel(
        win, create_if_missing=create_if_missing, prefix=prefix)
    if kind == "unclear":
        time.sleep(3)
        kind, info = team.classify_leader_with_panel(win)
    names = None
    if kind == "leader":
        img = win.capture(force_activate=False)
        if img is not None:
            names = team.panel_member_names(img)
        else:
            log("[组号] 队伍面板截图失败，成员昵称未读取（按未确认处理）", "WARN")
        if names is None or any(n is None for _i, n in names):
            # 面板昵称未读到/部分区域 OCR 漏检：同面板重截一帧重读合并
            # （不重新开面板）——单帧漏检会把真实队员误判「不在队伍」
            # 而不参与队员操作（2026-11-07 用户基本原则：队伍检查+比对
            # 得出组号结论，比对失败代价是队员全程无人管）
            time.sleep(1.0)
            img2 = win.capture(force_activate=False)
            if img2 is not None:
                names2 = team.panel_member_names(img2)
                if names2 is not None:
                    if names is None:
                        names = names2
                    else:
                        # names/names2 均为 [(序号, 昵称或None), ...]：逐序号
                        # 取两次读取的非 None 昵称（漏检区域由重读补齐）
                        names = [(idx, name if name is not None else
                                  (names2[idx][1] if idx < len(names2) else None))
                                 for idx, (_i2, name) in enumerate(names)]
    team.close_team_panel(win)
    return kind == "leader", info, names


def startup_team_count(panel_names):
    """启动检查面板昵称 → 队伍人数下限（None=面板未读到，需正常检查）。

    panel_member_names 返回 5 个区域（空位与 OCR 漏读均为 None），
    非 None 数量是真实人数的下限：下限已达到要求人数即确定人满，
    调用方可跳过首次领取前的队伍检查；下限不足时交正常检查复核
    （保守，绝不把未读到的队员当空位误判 ready）。
    """
    if panel_names is None:
        return None
    return sum(1 for _i, n in panel_names if n)


# ---- 组号成员战斗动作（按账号 ID，组号设置面板编辑） ----

def deactivate_group(reason=""):
    """组号降级关闭（启动检查确定性未组队等场景，2026-11-07 用户反馈）。

    本次运行不再切 Tab/队员 pass（_active 置 False，全部 pass 与
    护栏自然跳过），并写组号识别状态 False（侧边栏「组号」入口隐藏）。
    调用方随后按普通非队长流程继续运行（任务循环 check_team_min →
    确定性非队长 → _keep_alive 保持模式）。队长 ID 保留：队长动作
    解析仍可用（该账号有组号设置时继续用它的设置）。
    """
    global _active, _mode, _team_member_ids, _acted_ids
    _active = False
    _mode = ""
    _team_member_ids = set()
    _acted_ids = set()
    log(f"[组号] 组号模式降级关闭{('：' + reason) if reason else ''}", "WARN")
    try:
        app_utils.kv_save_merged("group_detected", False)
    except Exception as e:
        log(f"[组号] 组号识别状态写入失败: {e}", "WARN")


def leader_battle_actions():
    """队长战斗动作解析：队长账号有组号设置则用之，否则通用战斗设置。

    队长换人自动生效（新队长读自己账号的组号设置；无设置回退通用
    设置——用户 2026-11-07 需求：B 昨天以队员身份设置过、今天当队长
    直接读它的设置）。单号模式 _leader_id 为 None，恒返回通用设置
    （行为与旧版完全一致）。
    """
    if _leader_id:
        entry = load_group_actions().get(_leader_id)
        if entry:
            return entry
    return load_battle_actions()


def scan_group_members(win, panel_names=None):
    """枚举 Tab + 成员昵称 → 匹配「在队伍中的队员 Tab」。

    任务进程启动检查与主进程组号设置面板共用（不依赖 _active/_mode，
    不切 Tab）。流程：枚举子窗口（队长 = 可见已登录 Tab）→ 面板昵称
    匹配 → 每个已登录非队长 Tab 的标题角色名与面板昵称模糊匹配
    （_name_similar）。
    panel_names 缺省（None，组号设置面板检查路径）：自行打开队伍面板
    → team.panel_member_names 读全员昵称 → 关闭面板；传入时（任务
    启动路径，startup_check 同次面板打开已读取）直接复用不再开面板。
    宽松兜底（用户确认）：面板打不开/名字读不到时全部成员标记
    in_team=False——不拦截启动、不套动作配置（只点自动）。
    返回 (是否成功, 描述, 数据)；数据 = {"leader": {"id","name","char",
    "beast"}, "members": [{"id","name","in_team","char","beast"}, ...],
    "panel_read": 面板昵称是否读到, "options": {...}}，char/beast 为
    已保存的组号动作（未设置 None）。
    """
    try:
        children = game.list_tab_windows(win)
    except Exception as e:
        return False, f"枚举 Tab 子窗口失败: {e}", None
    logged = [c for c in children if c["id"]]
    leader = next((c for c in logged if c["visible"]), None)
    if leader is None:
        return False, "未识别到当前 Tab 身份（无可见已登录子窗口）", None
    member_children = [c for c in logged if c["id"] != leader["id"]]
    saved = load_group_actions()
    if panel_names is None:
        # 组号设置面板检查路径：自行开面板读昵称（宽松兜底）
        from . import team  # 函数内导入：team 顶层不依赖 tab_group
        panel_open = False
        try:
            ok_open, open_info = team.is_team_panel_open(win)
            if not ok_open:
                ok_open, open_info = team.open_team_panel(win)
                if not ok_open:
                    log(f"[组号] 队伍面板未打开（{open_info}），成员按未确认处理", "WARN")
            panel_open = ok_open
            if ok_open:
                img = win.capture(force_activate=False)
                if img is None:
                    log("[组号] 队伍面板截图失败，成员按未确认处理", "WARN")
                else:
                    panel_names = team.panel_member_names(img)
        except Exception as e:
            log(f"[组号] 读取队伍面板成员昵称异常: {e}，成员按未确认处理", "WARN")
        finally:
            if panel_open:
                try:
                    team.close_team_panel(win)
                except Exception:
                    pass
        if panel_names is None:
            log("[组号] 队伍面板成员昵称未识别（面板不可见或未渲染），成员按未确认处理", "WARN")
    panel_name_list = [n for _i, n in (panel_names or []) if n]
    members = []
    for c in member_children:
        cname = _tab_char_name(c["title"]) or ""
        in_team = bool(panel_name_list) and any(
            _name_similar(cname, n) for n in panel_name_list)
        entry = saved.get(c["id"])
        members.append({"id": c["id"], "name": cname,
                        "in_team": in_team,
                        "char": entry["char"] if entry else None,
                        "beast": entry["beast"] if entry else None})
    leader_entry = saved.get(leader["id"])
    leader_data = {"id": leader["id"],
                   "name": _tab_char_name(leader["title"]) or "",
                   "char": leader_entry["char"] if leader_entry else None,
                   "beast": leader_entry["beast"] if leader_entry else None}
    confirmed = [m for m in members if m["in_team"]]
    desc = [f"队长 {leader_data['name']}（ID {leader_data['id']}）"]
    if panel_names is None:
        desc.append("面板昵称未识别（成员未确认）")
    elif confirmed:
        desc.append("已确认队员 "
                    + "、".join(f"{m['name']}({m['id']})" for m in confirmed))
    elif member_children:
        desc.append(f"已登录 {len(member_children)} 个队员 Tab 均未匹配到队伍成员")
    else:
        desc.append("窗口内无其他已登录 Tab")
    return True, "；".join(desc), {"leader": leader_data, "members": members,
                                  "panel_read": panel_names is not None,
                                  "options": {"char": CHAR_BATTLE_ACTIONS,
                                              "beast": BEAST_BATTLE_ACTIONS}}


def startup_member_check(win, panel_names=None):
    """组号启动成员动作检查（队长判定通过后调用，仅组号激活时）。

    确认在队伍中的队员 Tab（标题角色名与面板昵称匹配）必须已设置
    组号战斗动作，否则返回缺失名单——调用方停止任务并提示先到组号
    设置面板设置。面板读不到名字时宽松放行（用户确认：不拦截启动，
    未确认 Tab 维持旧行为只点自动）。
    panel_names：startup_check 同次面板打开读到的成员昵称（复用免
    二次开面板）；None 时内部再开面板读取（组号设置面板等独立路径）。
    确认结果写入 _team_member_ids（队员 pass 套用动作配置的范围）；
    面板昵称可读时同步写组号识别状态（group_detected）——面板侧栏
    「组号」入口按此自动显隐（用户 2026-11-07 需求：识别到是组号
    才展示入口）。
    返回 (缺失队员列表或 None, 描述)；缺失列表 = [{"id","name"}, ...]。
    """
    global _team_member_ids
    _team_member_ids = set()
    ok, info, data = scan_group_members(win, panel_names)
    if not ok or not data:
        return None, info
    members = data["members"]
    confirmed = [m for m in members if m["in_team"]]
    _team_member_ids = {m["id"] for m in confirmed}
    if data.get("panel_read"):
        try:
            app_utils.kv_save_merged("group_detected", bool(confirmed))
        except Exception as e:
            log(f"[组号] 组号识别状态写入失败: {e}", "WARN")
        # 队伍检查快照：组号页打开自动展示队长与在队伍中的队员（此前
        # 必须手动点「检查当前队伍」才出列表，2026-11-11 用户需求）
        try:
            save_group_last_scan(data)
        except Exception as e:
            log(f"[组号] 保存队伍检查快照失败: {e}", "WARN")
    missing = [m for m in confirmed if m["char"] is None]
    if missing:
        names = "、".join(f"{m['name']}(ID {m['id']})" for m in missing)
        return missing, f"{names} 未设置战斗动作"
    if confirmed:
        unmanaged = len(members) - len(confirmed)
        suffix = (f"；其余 {unmanaged} 个 Tab 不在队伍/未确认，不参与队员操作"
                  if unmanaged else "")
        return None, f"组号成员已确认并全部设置（{len(confirmed)} 名）{suffix}"
    if data.get("panel_read"):
        # 面板昵称读到了，但已登录队员 Tab 一个都没匹配上——队伍里
        # 没有本窗口的其他账号（1 人队/队员还没进队/换人了），按基本
        # 原则不算组号：不拦截启动，本次运行不管理任何队员 Tab
        # （2026-11-07 日志 20:38 教训：此情形误报「面板昵称未识别」，
        # 实际是启动时队伍仅队长 1 人）
        return None, (f"队伍中无本窗口队员（{len(members)} 个队员 Tab 均未匹配），"
                      f"本次运行按无组号处理（队员 pass 跳过）；队员入队后重启任务生效")
    return None, "组号成员未确认（面板昵称未识别），按宽松放行（队员 pass 跳过，不操作未确认 Tab）"


# ---- 切换与身份 ----

def _current(win):
    """当前 Tab 的 (子窗口句柄, 账号 ID)：按可见子窗口判定，未登录 id=None。

    切 Tab 成败按句柄变化判定（未登录 Tab 无 ID，不能作判据）。
    """
    try:
        children = game.list_tab_windows(win)
    except Exception:
        return None, None
    for c in children:
        if c["visible"]:
            return c["hwnd"], c["id"]
    return None, None


def _current_id(win):
    """当前 Tab 的账号 ID（可见已登录子窗口标题解析），失败 None。"""
    return _current(win)[1]


def _ensure_foreground(win):
    """Ctrl+Tab 经前台窗口送达：激活游戏窗口并校验前台（同清屏链路）。

    静默操作模式：不再激活（激活违背静默约束），仅检查当前前台。
    """
    try:
        if not app_utils.silent_mode_on():
            win.activate()
            time.sleep(0.3)
        import win32gui
        return win32gui.GetForegroundWindow() == int(win.hwnd)
    except Exception as e:
        log(f"[组号] 前台检查异常: {e}", "WARN")
        return False


def _pass_foreground_ok(win):
    """pass 前置检查：静默模式下无需前台（切 Tab 走 Tab 条消息点击、
    pass 点击走消息通道，均不激活窗口；2026-11-04 实机验证）；
    非静默模式须激活并确认前台（Ctrl+Tab 经前台窗口送达）。"""
    if app_utils.silent_mode_on():
        return True
    return _ensure_foreground(win)


def _switch_next(win):
    """切到下一个 Tab（Ctrl+Tab 键盘通道；静默+后台走 Tab 条点击）。

    返回切换后当前 Tab 的账号 ID；空串 = 切换成功但该 Tab 未登录；
    None = 切换失败（按键未送达/前台丢失，重试一次后仍失败）。
    静默操作且游戏不在前台：键盘事件只进前台窗口且不允许激活，
    改走 _switch_next_by_click（Tab 条消息点击，无需前台）。
    """
    if not app_utils.keyboard_ok(win):
        return _switch_next_by_click(win)
    before_hwnd, _before_id = _current(win)
    Keyboard.key_hot("ctrl", "tab")
    time.sleep(TAB_SWITCH_SETTLE)
    after_hwnd, after_id = _current(win)
    if after_hwnd is not None and after_hwnd != before_hwnd:
        return after_id if after_id is not None else ""
    if not _ensure_foreground(win):
        return None
    Keyboard.key_hot("ctrl", "tab")
    time.sleep(TAB_SWITCH_SETTLE)
    after_hwnd, after_id = _current(win)
    if after_hwnd is None or after_hwnd == before_hwnd:
        return None
    return after_id if after_id is not None else ""


def _next_logged_index(win, tab, cur, count):
    """下一已登录 Tab 序号：未登录 Tab 跳过不切换（UIA 名字过滤）。

    未登录 Tab 的 UIA 名为游戏标题（客户端默认 Tab 名，已登录为
    角色名）；从 cur+1 环形找第一个名字 != 游戏标题的 Tab。返回
    None = 绕一圈回到自己、除当前 Tab 外无已登录 Tab（无切换目标，
    调用方放弃本轮）。UIA 不可用时回退 (cur+1)%count（旧行为：
    切换后按可见子窗口 ID 判空跳过，兜底语义不变）。
    """
    from . import silent_click  # 函数内导入：保持模块间依赖单向
    items = silent_click.tab_items(win, count)
    if items:
        for i in range(1, count + 1):
            idx = (cur + i) % count
            if idx == cur:
                return None
            if items[idx][0] != game.GAME_TITLE:
                return idx
    return (cur + 1) % count


def _tab_name_by_id(win, tab_id):
    """按账号 ID 从 Tab 子窗口标题解析角色名（切换结果日志用）。

    标题格式见 _tab_char_name；解析失败回退账号 ID 本身。
    """
    try:
        for c in game.list_tab_windows(win):
            if c["id"] == tab_id:
                return _tab_char_name(c["title"]) or tab_id
    except Exception:
        pass
    return tab_id


def _switch_next_by_click(win):
    """静默+后台的切 Tab 兜底：Tab 条消息点击（2026-11-04 实机验证
    生效——无需前台、无需键盘、不移动物理鼠标）。

    下一 Tab 序号 = 下一已登录 Tab（未登录 Tab 按 UIA 名字跳过不切，
    _next_logged_index）；点击后按可见子窗口句柄变化判定（与键盘
    路径同判据）。未生效最多尝试 TAB_SWITCH_TRIES 次，始终点同一
    Tab（不重算序号——首次点击已生效而判定竞态时，重点同一 Tab 是
    无操作，重算会连切两个）；每次重试前丢弃 Tab 矩形缓存重读 UIA
    （窗口被拖动/缩放后缓存坐标会过期，点击落到 Tab 条外客户端不
    消费——2026-11-10 实机：拖动窗口后整轮「点击投递未被消费」）。
    切换生效后输出「切至哪个角色」日志：角色名（子窗口标题解析）+
    账号 ID（用户 2026-11-07 要求——只有切换生效后 ID 才可信，
    点击时仅知道 Tab 序号）。
    返回语义同 _switch_next。
    """
    from . import silent_click  # 函数内导入：保持模块间依赖单向
    tab = silent_click.tab_control_hwnd(win)
    if not tab:
        log("[组号] 未找到 Tab 条控件（SysTabControl32），无法静默切 Tab", "WARN")
        return None
    count = silent_click.tab_item_count(tab)
    cur = silent_click.tab_current_index(tab)
    if count <= 0 or cur < 0:
        log(f"[组号] Tab 条读取失败（总数 {count}、当前 {cur}），无法静默切 Tab", "WARN")
        return None
    next_i = _next_logged_index(win, tab, cur, count)
    if next_i is None:
        log("[组号] 除当前 Tab 外无已登录 Tab，跳过切换", "WARN")
        return None

    def _log_switch_ok(ok_id, info):
        """切换生效日志：角色名 + 账号 ID（ok_id 为 None = 未登录 Tab）。"""
        if ok_id:
            cname = _tab_name_by_id(win, ok_id)
            log(f"[组号] 静默切 Tab：已切至「{cname}」（ID {ok_id}）"
                f"（Tab {next_i}，点击中心 ({info['point'][0]},{info['point'][1]})）", "INFO")
        else:
            log(f"[组号] 静默切 Tab：已点击 Tab {next_i} 中心 "
                f"({info['point'][0]},{info['point'][1]})（当前 Tab 未登录）", "INFO")

    before_hwnd, _before_id = _current(win)
    info = None
    for attempt in range(1, TAB_SWITCH_TRIES + 1):
        if attempt > 1:
            # 前一次未生效：缓存坐标可能已过时（窗口移动/缩放/布局
            # 变化），丢弃重读后再点同一 Tab
            silent_click.invalidate_tab_items(win)
        info = silent_click.send_silent_tab_click(win, next_i)
        if info is None:
            time.sleep(TAB_SWITCH_SETTLE)  # 投递失败也给控件留恢复时间
            continue
        time.sleep(TAB_SWITCH_SETTLE)
        after_hwnd, after_id = _current(win)
        if after_hwnd is not None and after_hwnd != before_hwnd:
            _log_switch_ok(after_id, info)
            return after_id if after_id is not None else ""
    if info is None:
        log("[组号] 静默切 Tab 点击投递失败（Tab 条不可达），中止本轮", "WARN")
    else:
        log(f"[组号] 静默切 Tab 未生效（点击投递未被消费，Tab {next_i} "
            f"中心 ({info['point'][0]},{info['point'][1]})，已重读缓存"
            f"重试 {TAB_SWITCH_TRIES} 次），中止本轮", "WARN")
    return None


def return_to_leader(win):
    """切回队长 Tab（Ctrl+Tab 循环直至当前 ID == 队长 ID）。

    只认 _leader_id（不查 active()——验证模式 force 走 pass 时
    _active 为 False，仍须切回队长）。
    """
    if _leader_id is None:
        return
    for _ in range(TAB_CYCLE_GUARD):
        if _current_id(win) == _leader_id:
            return
        if _switch_next(win) is None:
            log("[组号] 切回队长 Tab 失败（身份未确认）", "WARN")
            return
    log("[组号] 切回队长 Tab 超时（未在循环内命中队长）", "WARN")


def ensure_leader_tab(win):
    """确保当前在队长 Tab（pass 切回失败残留时的自愈护栏）。

    调用方（follow 循环/主循环每轮）在点击任何东西前先过此门：
    不在队长 Tab 就尝试切回，切不回返回 False、调用方本轮跳过——
    队长流程的点寻路/领任务/回程永远不会落在队员 Tab 上。
    未激活或无队长 ID 时返回 True（不干预单号流程）。
    """
    if not _active or _leader_id is None:
        return True
    if _current_id(win) == _leader_id:
        return True
    return_to_leader(win)
    return _current_id(win) == _leader_id


def ensure_bound_tab(win):
    """主进程诊断护栏：游戏被手动切到别的 Tab 时，先切回绑定角色 Tab。

    绑定记录（game.bound_window_id）含账号 ID（绑定语义「这个窗口的
    这个角色」）；枚举 Tab 子窗口找到 ID 匹配的 Tab，循环切换直至
    命中（Ctrl+Tab 前台 / Tab 条消息点击后台双通道，_switch_next）。
    未绑定 / 已登录 Tab <2（单号无切换目标）/ 绑定 Tab 未登录时不做
    切换。返回 (是否已在绑定 Tab, 描述)——False 时调用方应放弃本轮
    诊断（在错误 Tab 上执行检查/点击会操作错账号）。
    """
    try:
        bound = game.bound_window_id()
        if not bound or not isinstance(bound, dict):
            return True, "未绑定窗口，无切换目标"
        m = re.search(r"ID：(\d+)", str(bound.get("id") or ""))
        if not m:
            return True, "绑定记录无账号 ID，无切换目标"
        bound_id = m.group(1)
        children = game.list_tab_windows(win)
        logged = [c for c in children if c["id"]]
        if len(logged) < 2:
            return True, "单号（已登录 Tab <2），无需切 Tab"
        if _current_id(win) == bound_id:
            return True, "已在绑定角色 Tab"
        if not any(c["id"] == bound_id for c in logged):
            log(f"[组号] 绑定角色（ID {bound_id}）的 Tab 未登录，无法切回，"
                f"按当前 Tab 执行", "WARN")
            return True, "绑定角色 Tab 未登录，按当前 Tab 执行"
        for _ in range(TAB_CYCLE_GUARD):
            cur = _switch_next(win)
            if cur is None:
                log("[组号] 切回绑定 Tab 失败", "WARN")
                return False, "切回绑定角色 Tab 失败（请手动切回后重试）"
            if cur == bound_id:
                log("[组号] 游戏停在队员 Tab，已切回绑定角色 Tab", "SYS")
                return True, "已切回绑定角色 Tab"
        return False, "切回绑定角色 Tab 超时（请手动切回后重试）"
    except Exception as e:
        log(f"[组号] 绑定 Tab 护栏异常: {e}", "WARN")
        return True, "绑定 Tab 护栏异常（按当前 Tab 执行）"


# ---- 护栏与队员遍历 ----

def _guarded_click(win, x, y, button="primary"):
    """pass 专用点击：Tab 条区域（y<60）为鼠标禁入区，落点进禁区跳过。

    防 OCR 抖动坐标漂移把点击落到 Tab 条上误切账号——切 Tab 只走
    Ctrl+Tab 或静默 Tab 条点击通道，pass 流程点击永不碰 Tab 条。
    返回是否执行了点击。
    """
    if y < NO_CLICK_ZONE_Y:
        log(f"[组号] 点击落点 ({x},{y}) 进入 Tab 条禁入区，已跳过", "WARN")
        return False
    human_click(win, x, y, button=button)
    return True


def _for_each_member(win, handler, label, force=False, total_deadline=None,
                     only_members=False):
    """遍历队员 Tab：Ctrl+Tab 切一整圈（_tab_count 次），逐个执行 handler。

    handler 签名 (win, tab_id)；未登录 Tab（切换成功但无 ID，
    cur == ""）跳过；绕回队长提前结束；handler 异常内部消化；
    整体受总时长上限保护（total_deadline 缺省 PASS_TOTAL_DEADLINE；
    战斗动作 pass 按配置队员数放大）。结束切回队长。
    only_members=True：只处理启动检查确认「在队伍中」的 Tab
    （_team_member_ids）——一个窗口多个 Tab 不全是组号（2026-11-07
    用户基本原则：队伍检查 + Tab 名比对才得出组号结论），不在队伍
    的角色完全不碰（它可能在别的队伍/干别的事，切过去点自动会
    干扰）；非组员 Tab 仍要切过去（遍历路径），但 handler 不执行。
    force=True 供验证模式绕过 active() 检查（主进程无任务模式，无
    队伍检查结果，维持全部遍历的旧语义）。
    pass_active 由调用方置位/复位。
    """
    if not _active and not force:
        return
    if total_deadline is None:
        total_deadline = PASS_TOTAL_DEADLINE
    deadline = time.time() + total_deadline
    for _ in range(_tab_count):
        if time.time() > deadline:
            log(f"[组号] {label} 超总时长上限，提前切回队长", "WARN")
            break
        cur = _switch_next(win)
        if cur is None:
            log(f"[组号] {label}：切 Tab 失败，中止", "WARN")
            break
        if cur == _leader_id:
            break  # 已绕回队长
        if cur == "":
            log(f"[组号] {label}：当前 Tab 未登录，跳过", "INFO")
            continue
        if only_members and not force and cur not in _team_member_ids:
            log(f"[组号] {label}：Tab（ID {cur}）不在队伍/未确认，跳过不操作", "INFO")
            continue
        try:
            handler(win, cur)
        except Exception as e:
            log(f"[组号] {label}：队员 Tab（ID {cur}）处理异常: {e}", "WARN")
    return_to_leader(win)


# ---- 战斗自动 pass ----

def member_auto_pass(win, wait_auto=AUTO_WAIT_FIRST, act_configured=False):
    """战斗自动 pass：队长点完自动后遍历队员 Tab。

    act_configured=True（首场挂自动后/补挂自动场）：确认在队伍中的
    队员按账号配置执行战斗动作（与队长对称：取消自动→等操作条→
    人物+召唤兽动作→点自动，仅此一场，此后自动跨场持续）；未确认
    /未配置的 Tab 维持旧自动路径。总时长上限按配置队员数放大。
    act_configured=False（每 5 场例行）：全部队员只点自动。
    触发时机跟随队长：首场挂自动后（wait_auto=5s，等队员操作条）、
    每 5 场例行确认后（wait_auto=1s，多半已挂自动跳过）。队员不在
    战斗中（单挑等）跳过；已挂自动时自动按钮不出现，轮询超时跳过。
    """
    global pass_active, _no_members_logged
    if not _active:
        return
    if not _team_member_ids:
        # 未确认到在队伍中的队员 Tab（面板昵称未识别/队伍无本窗口队员）：
        # 一个窗口多个 Tab 不全是组号，未比对确认的 Tab 不碰
        # （2026-11-07 用户基本原则），整个 pass 跳过不再切 Tab
        if not _no_members_logged:
            _no_members_logged = True
            log("[组号] 未确认到在队伍中的队员 Tab，跳过队员自动 pass"
                "（面板昵称未识别或队伍无本窗口队员）", "WARN")
        return
    if not _try_enter_pass():
        return  # 另一 pass 进行中：放弃本轮，等下一轮时机
    try:
        if not _pass_foreground_ok(win):
            log("[组号] 游戏窗口未在前台，跳过战斗自动 pass", "WARN")
            return
        total_deadline = None
        if act_configured:
            saved = load_group_actions()
            n_act = sum(1 for tid in _team_member_ids
                        if saved.get(tid) and not _is_noop_actions(saved[tid]))
            if n_act:
                total_deadline = MEMBER_ACT_DEADLINE_BASE + n_act * MEMBER_ACT_BUDGET
        _for_each_member(win, lambda w, cur: _member_one(w, cur, wait_auto,
                                                         act_configured),
                         "战斗自动 pass", total_deadline=total_deadline,
                         only_members=True)
    finally:
        _exit_pass()


def _is_noop_actions(actions):
    """配置动作是否为空操作（人物与召唤兽均为「无」——等同未配置，
    走旧自动路径，取消自动再挂回去纯属浪费一轮）。"""
    return actions["char"] == "无" and actions["beast"] == "无"


def _member_one(win, tab_id, wait_auto, act_configured):
    """单个队员 Tab 的战斗处理：配置动作路径或旧自动路径。

    配置动作仅套用在启动检查确认「在队伍中」的 Tab（_team_member_ids）
    ——未确认 Tab 只点自动（其账号可能并不在队伍里，取消自动执行
    动作会干扰它自己的场景）。已执行过配置动作的队员（_acted_ids）
    直接走点自动路径——动作与队长对称只执行一次（2026-11-07 用户
    反馈：pass 再次触发时不得取消自动重做动作）。
    """
    if act_configured and tab_id in _team_member_ids \
            and tab_id not in _acted_ids:
        actions = load_group_actions().get(tab_id)
        if actions and not _is_noop_actions(actions):
            if _act_one(win, tab_id, actions, wait_auto):
                _acted_ids.add(tab_id)  # 已尝试动作（含等不到操作条）：
                                        # 再触发时直接点自动，不重复尝试
            return
    _auto_one(win, tab_id, wait_auto)


def _act_one(win, tab_id, actions, wait_auto):
    """单个队员 Tab 的配置战斗动作（与队长 handle_battle 对称）。

    截一帧确认战斗 → battle.perform_member_actions（取消自动→等操作
    条→人物动作→召唤兽动作→点自动）。返回是否已尝试（True 记入
    _acted_ids，pass 再触发时该队员直接点自动）；不在战斗/截图失败
    返回 False（未消耗动作机会）。执行期受调用方总时长上限保护，
    超时剩余队员由补挂自动场/例行 pass 兜底。
    """
    from . import battle  # 函数内导入：battle 顶层导入 tab_group，避免循环依赖
    deadline = time.time() + max(1.0, wait_auto)
    while time.time() < deadline:
        img = win.capture(force_activate=False, format=WindowCaptureFormat.OPENCV)
        if img is None:
            time.sleep(0.5)
            continue
        if not battle.is_in_battle_frame(img):
            log(f"[组号] 队员 Tab（ID {tab_id}）不在战斗中，跳过配置动作", "INFO")
            return False
        status = battle.perform_member_actions(win, actions)
        log(f"[组号] 队员 Tab（ID {tab_id}）配置动作: {status}", "SYS")
        return status != "截图失败"
    log(f"[组号] 队员 Tab（ID {tab_id}）未确认战斗状态，跳过配置动作", "WARN")
    return False


def _auto_one(win, tab_id, wait_auto):
    """单个队员 Tab 的战斗自动：截一帧判战斗 → 等自动按钮出现即点。

    高性能缓存：自动按钮位置固定（同队长同一客户端），队长例行确认
    刚记下的缓存坐标直接复用——小 ROI 双模板比分数判态，「取消自动」
    即已挂自动跳过，「自动」点缓存坐标；无缓存走操作条带识别并记缓存。
    """
    from . import battle  # 函数内导入：battle 顶层导入 tab_group，避免循环依赖
    deadline = time.time() + max(0.5, wait_auto)
    while time.time() < deadline:
        img = win.capture(force_activate=False, format=WindowCaptureFormat.OPENCV)
        if img is None:
            time.sleep(0.5)
            continue
        if not battle.is_in_battle_frame(img):
            log(f"[组号] 队员 Tab（ID {tab_id}）不在战斗中，跳过自动", "INFO")
            return
        pos = fixed_pos_get(battle.AUTO_POS_CACHE_KEY, win)
        if pos is not None:
            state = battle._auto_state_at(img, pos)
            if state == "cancel":
                log(f"[组号] 队员 Tab（ID {tab_id}）已挂自动（取消自动按钮可见），跳过", "INFO")
                return
            if state == "auto":
                if _guarded_click(win, pos[0], pos[1]):
                    log(f"[组号] 队员 Tab（ID {tab_id}）已点击自动"
                        f" @ ({pos[0]},{pos[1]})（高性能缓存）", "SYS")
                return
        else:
            pt = battle._match_auto_band(img)  # 自动态两变体（auto/auto2）任一命中
            if pt is not None:
                if _guarded_click(win, pt[0], pt[1]):
                    log(f"[组号] 队员 Tab（ID {tab_id}）已点击自动 @ ({pt[0]},{pt[1]})", "SYS")
                fixed_pos_set(battle.AUTO_POS_CACHE_KEY, win, pt[0], pt[1])  # 识别成功记缓存
                return
        time.sleep(0.5)
    log(f"[组号] 队员 Tab（ID {tab_id}）未识别到自动按钮（可能已挂自动），跳过", "INFO")


# ---- 巫医 pass ----

def member_wuyi_pass(win, force=False):
    """巫医 pass：队长点完全部医治后，逐个队员点击其自动弹出的巫医对话框。

    前置（用户确认）：队长点击巫医 NPC 后所有队员自动弹出对话框、
    不点不消失——队员不点 NPC、不检查血蓝（医治的是召唤兽忠诚度）、
    不重点任务追踪；对话框未弹出/选项未识别 → 超时跳过记日志。
    高性能模式 + 队长选项坐标新鲜（battle.record_wuyi_option_pos 刚记录，
    同一对话框布局队员位置相同）时直点同一坐标，省每队员截图+OCR。
    force=True 供验证模式绕过 active() 检查（主进程无任务模式）。
    """
    global pass_active, _no_members_logged
    if not _active and not force:
        return
    if not force and not _team_member_ids:
        # 见 member_auto_pass 同款说明：未确认队员 Tab 不碰，跳过不切
        if not _no_members_logged:
            _no_members_logged = True
            log("[组号] 未确认到在队伍中的队员 Tab，跳过巫医 pass", "WARN")
        return
    if not _try_enter_pass():
        return  # 另一 pass 进行中：放弃本轮，等下一轮时机
    try:
        if not _pass_foreground_ok(win):
            log("[组号] 游戏窗口未在前台，跳过巫医 pass", "WARN")
            return
        from . import battle  # 函数内导入，避免循环依赖
        pos = battle.last_wuyi_option_pos
        fresh = pos is not None \
            and time.time() - battle.last_wuyi_option_at <= WUYI_POS_FRESH
        if fresh and load_high_perf():
            log(f"[组号] 巫医 pass 高性能直点：复用队长选项坐标 ({pos[0]},{pos[1]})", "INFO")
            _for_each_member(win, lambda w, cur: _wuyi_click_direct(w, cur, pos),
                             "巫医 pass（高性能直点）", force=force,
                             only_members=True)
        else:
            _for_each_member(win, _wuyi_one, "巫医 pass", force=force,
                             only_members=True)
    finally:
        _exit_pass()


def _wuyi_state(win):
    """一帧判定巫医对话框是否在屏（True 在 / False 不在 / None 截图失败）。"""
    from . import battle  # 函数内导入，避免循环依赖
    img = win.capture(force_activate=False)
    if img is None:
        return None
    items, _ = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
    return any(_dialog_text_present(items, t) for t in battle.WUYI_DIALOG_TEXTS)


def _wuyi_click_direct(win, tab_id, pos):
    """高性能直点：队员对话框与队长同位置，直接点队长选项坐标。

    绿字中心比可点击中心偏高（2026-11-02 实测点击落在「复」字
    上方、对话框不关），点击 y 下移半行高落在文字中部——点中选项
    即由游戏机制保证关闭，不做事后回验。点击前确认对话框在屏
    （无对话框跳过，不点场景）。
    """
    if not _wuyi_state(win):
        # 无对话框（或截图失败按无处理）：跳过，不点场景
        log(f"[组号] 队员 Tab（ID {tab_id}）无巫医对话框，跳过", "WARN")
        return
    x, y = pos[0], pos[1] + WUYI_OPTION_Y_OFFSET
    if _guarded_click(win, x, y):
        log(f"[组号] 队员 Tab（ID {tab_id}）已点击「全部医治和修复」"
            f" @ ({x},{y})（高性能直点）", "SYS")
    move_away(win)


def _wuyi_one(win, tab_id):
    """单个队员 Tab 的巫医对话框处理（出现→点全部医治和修复）。

    识别路径：在队员自己的屏幕上定位选项并点击（y 同下移半行高）；
    点中即由游戏机制保证关闭，不做事后回验。预算 = 出现等待。
    """
    from . import battle  # 函数内导入，避免循环依赖
    deadline = time.time() + WUYI_DIALOG_WAIT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        if not any(_dialog_text_present(items, t) for t in battle.WUYI_DIALOG_TEXTS):
            time.sleep(0.5)
            continue
        pt = battle._find_wuyi_option(items, img_np)
        if pt is None:
            # 选项文字可能很小（实测字高仅 13px），对话框区 2x 放大重识别
            # （同 battle._handle_wuyi_dialog 兜底）
            l, t, r, b = task_tracker.DIALOG_REGION
            crop = img.crop((l, t, r, b)).resize(((r - l) * 2, (b - t) * 2),
                                                 Image.LANCZOS)
            items2, _ = game_map.ocr_zoom(crop)
            mapped = [(txt, [l + rect[0] // 2, t + rect[1] // 2,
                             l + rect[2] // 2, t + rect[3] // 2], score)
                      for txt, rect, score in items2]
            pt = battle._find_wuyi_option(mapped, img_np)
        if pt is not None:
            x, y = pt[0], pt[1] + WUYI_OPTION_Y_OFFSET
            if _guarded_click(win, x, y):
                log(f"[组号] 队员 Tab（ID {tab_id}）已点击「全部医治和修复」"
                    f" @ ({x},{y})", "SYS")
            move_away(win)  # 悬停会让选项变色
            return
        time.sleep(0.5)  # 对话框在但选项未识别：继续轮询
    log(f"[组号] 队员 Tab（ID {tab_id}）巫医对话框未出现/选项未识别，跳过", "WARN")


# ---- 回血回蓝 pass ----

def member_bars_pass(win):
    """回血回蓝 pass：队长点完任务追踪后（寻路中）触发，60s 节流。

    逐个队员切过去检查血蓝条：战斗中跳过（战斗中不右键血蓝条）；
    低于阈值右键回满（复用 status_bars.check_all_bars，阈值沿用
    通用设置）。队长自己的战后补血蓝不受影响（战斗线程原逻辑）。
    """
    global pass_active, _last_bars_pass_at, _no_members_logged
    if not _active:
        return
    now = time.time()
    if now - _last_bars_pass_at < BARS_PASS_INTERVAL:
        return
    _last_bars_pass_at = now
    if not _team_member_ids:
        # 见 member_auto_pass 同款说明：未确认队员 Tab 不碰
        if not _no_members_logged:
            _no_members_logged = True
            log("[组号] 未确认到在队伍中的队员 Tab，跳过回血回蓝 pass", "WARN")
        return
    if not _try_enter_pass():
        return  # 另一 pass 进行中：放弃本轮，等下一轮时机
    try:
        if not _pass_foreground_ok(win):
            log("[组号] 游戏窗口未在前台，跳过回血回蓝 pass", "WARN")
            return
        _for_each_member(win, _bars_one, "回血回蓝 pass", only_members=True)
    finally:
        _exit_pass()


def _bars_one(win, tab_id):
    """单个队员 Tab 的血蓝检查（战斗中跳过）。"""
    from . import battle  # 函数内导入，避免循环依赖
    img = win.capture(force_activate=False)
    if img is not None and battle.is_in_battle_frame(img):
        log(f"[组号] 队员 Tab（ID {tab_id}）在战斗中，跳过血蓝检查", "INFO")
        return
    log(f"[组号] 检查队员 Tab（ID {tab_id}）血蓝", "INFO")
    status_bars.check_all_bars(win)


# ---- 暂离队员归队 pass（todo3：识别到的暂离队员是某个 Tab → 切过去归队） ----

def _tab_char_name(title):
    """Tab 标题中的角色名（与 game.read_login_tab 同解析）。

    实测标题格式（2026-11-07 用户日志）：
    「[大话西游2_经典版] 大话西游2经典版 $Revision: 2057701
    - 佳期良年 - 碧桃娘子（ID：888888888）」——split " - " 取末段、
    （ID： 之前即角色名。旧版注释的「角色名（ID：账号）」直接格式
    （无 " - " 分隔）兼容：整个标题取（ID： 之前。
    """
    if not title:
        return None
    parts = title.split(" - ")
    if len(parts) >= 2 and "（ID：" in parts[-1]:
        name = parts[-1].split("（ID：")[0].strip()
        return name or None
    m = re.match(r"^(.+?)（ID：", title)
    if m and " - " not in m.group(1):
        return m.group(1).strip() or None
    return None


def _name_similar(a, b):
    """角色名模糊比对（Tab 标题名 vs 队伍面板 OCR 昵称，容 OCR 噪声）。

    比对前两侧都过易混字符归一化（norm_name_for_cmp）：特殊字符名
    （〃ㄨˉ 等）在游戏字体下同形、OCR 稳定误读（2026-11-09
    「〃忆ㄨ嚣魂ˉ」→「”忆×嚣魂一」致 0.5 < 0.7 漏配成员），
    归一化后同一类字符不再拖低模糊比。
    """
    if not a or not b:
        return False
    if a == b:
        return True
    return difflib.SequenceMatcher(
        None, norm_name_for_cmp(a), norm_name_for_cmp(b)
    ).ratio() >= REJOIN_NAME_RATIO


def member_rejoin_pass(win, away_names, force=False):
    """暂离队员归队 pass（todo3 用户 2026-11-07 定稿）：队长检查队伍
    发现暂离队员后，切到对应队员 Tab 替其点「回归队伍」归队。

    away_names：暂离成员昵称列表（队伍面板 OCR，含噪声）。与 Tab 标题
    角色名模糊匹配（_name_similar）——匹配到的队员 Tab 才切过去执行
    team.rejoin_from_away 完整链路（开面板 → 身份判定 → 点归队 → 校验；
    队员视角非队长，面板有归队按钮）。**不限于死亡落轮回司**——暂离
    队员在任何位置都点归队传送回队长（2026-11-07 用户要求：启动阶段
    发现暂离即使不在轮回司也要归队）。无匹配不做任何切换（零成本）。
    30s 节流（REJOIN_PASS_MIN_INTERVAL）防每轮检查频繁切 Tab；归队
    成功后该成员离开暂离名单，自然不再触发。
    pass 互斥/前台校验/未登录跳过/绕回队长与其余 pass 同款。
    force=True 供验证模式绕过 active() 检查（主进程无任务模式）。
    返回 (已归队数, 描述, 匹配到的暂离昵称集合)——昵称集合供调用方
    （超时请离）排除自家号：自家队员 Tab 归队优先、不自动请离。
    """
    global pass_active, _last_rejoin_pass_at
    if not _active and not force:
        return 0, "", set()
    now = time.time()
    if not force and now - _last_rejoin_pass_at < REJOIN_PASS_MIN_INTERVAL:
        return 0, "", set()  # 任务流程节流；force（诊断按钮）不节流
    if not _try_enter_pass():
        return 0, "", set()  # 另一 pass 进行中：放弃本轮，等下一轮时机
    _last_rejoin_pass_at = now
    try:
        if not _pass_foreground_ok(win):
            log("[组号] 游戏窗口未在前台，跳过归队 pass", "WARN")
            return 0, "", set()
        children = game.list_tab_windows(win)
        matched = {}  # 账号 ID → (Tab 角色名, 匹配的暂离昵称)
        for c in children:
            if not c["id"] or c["id"] == _leader_id:
                continue
            cname = _tab_char_name(c["title"])
            for n in away_names:
                if n and _name_similar(cname, n):
                    matched[c["id"]] = (cname, n)
                    break
        if not matched:
            return 0, "", set()
        from . import team  # 函数内导入：避免循环依赖
        from . import battle  # 函数内导入：与 _auto_one 同款
        done = 0

        def _rejoin_one(w, tab_id):
            nonlocal done
            entry = matched.get(tab_id)
            if entry is None:
                return
            cname, _n = entry
            # 战斗判定必须用帧级 is_in_battle_frame——pass_active 期间
            # is_in_battle 恒返回 True（battle.py 设计：pass 期间屏幕可能
            # 是队员 Tab，统一按战斗等待），误用会把轮回司场景误报成
            # 「战斗中」跳过归队（2026-11-07 用户日志实测）；与 _auto_one/
            # _bars_one 同款帧级判定
            img = w.capture(force_activate=False)
            if img is not None and battle.is_in_battle_frame(img):
                log(f"[组号] 队员 Tab（{cname}）战斗中，跳过归队", "INFO")
                return
            handled, info = team.rejoin_from_away(w, prefix="[组号]")
            ok = handled and info.startswith("已归队")
            log(f"[组号] 队员 Tab（{cname}）归队: {info}",
                "SYS" if ok else "WARN")
            if ok:
                done += 1

        _for_each_member(win, _rejoin_one, "归队 pass", force=force)
        matched_away = {n for _cname, n in matched.values()}
        if done:
            return done, f"已归队 {done} 名队员", matched_away
        return 0, f"队员归队未成功（{len(matched)} 名匹配队员）", matched_away
    finally:
        return_to_leader(win)
        _exit_pass()
