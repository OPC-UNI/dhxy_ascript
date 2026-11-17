"""退出游戏：先解散/离开队伍，再关闭游戏客户端（所有登录 Tab）。

实机标定（2026-11-17，1378×831，单 Tab 验证）：
- 右下角显示器图标（msg_exit_system_icon.png，ROI 1290,770-1378,831）
  → 右侧系统菜单 → OCR「退出游戏」
  → 「玩法进度提醒」面板底部「退出游戏」按钮（msg_exit_game_btn.png，
     ROI 600,560-1050,780）→ 客户端进程直接关闭，无二次确认框。
- 队伍处理复用 team.leave_or_disband_team（队长解散/队员离开，双模板
  比分数）；多 Tab 逐个切换，找到队长 Tab 解散后即停止队伍处理。
- 退出客户端按「窗口消失 / 无已登录 Tab」判定成功；多 Tab 客户端若只
  关闭当前 Tab，则循环处理剩余 Tab（最多 6 轮），不写 while True。
"""
import time

from ..core import app_utils
from . import game, map as game_map, silent_click, tab_group, team

SYSTEM_ICON = "msg_exit_system_icon.png"
EXIT_BUTTON = "msg_exit_game_btn.png"
SYSTEM_ICON_REGION = (1290, 770, 1378, 831)
EXIT_BUTTON_REGION = (600, 560, 1050, 780)
MENU_OCR_REGION = (1180, 430, 1378, 810)
PANEL_OCR_REGION = (400, 230, 1000, 730)
SYSTEM_ICON_FALLBACK = (1357, 808)
MENU_ITEM_FALLBACK = (1313, 773)
EXIT_BUTTON_FALLBACK = (860, 683)
MENU_WAIT = 4.0
PANEL_WAIT = 4.0
CLIENT_CLOSE_WAIT = 8.0
MAX_CLIENT_ATTEMPTS = 6


def _emit(progress, text):
    if progress is not None:
        try:
            progress(text)
        except Exception:
            pass


def _log(text, level="SYS"):
    try:
        app_utils.log(f"[退出] {text}", level)
    except Exception:
        pass


def _logged_tab_ids(win):
    try:
        return [str(c["id"]) for c in game.list_tab_windows(win)
                if c.get("id")]
    except Exception:
        return []


def _switch_to_tab(win, target_id, attempts=8):
    for _ in range(max(1, int(attempts))):
        if tab_group.current_tab_id(win) == target_id:
            return True
        if tab_group.switch_next_logged(win) is None:
            return False
    return tab_group.current_tab_id(win) == target_id


def leave_teams_for_exit(win, progress=None):
    """退出前处理队伍：逐 Tab 尝试，队长解散即成功；队员离开后继续找队长。

    返回 (ok, 描述)。未组队返回成功（无需操作）；多 Tab 中找不到队长
    Tab 时返回成功但附警告（已离开可离开的队伍，原队伍可能仍存在）。
    """
    tabs = _logged_tab_ids(win)
    if not tabs:
        return True, "没有已登录 Tab"
    current = tab_group.current_tab_id(win)
    visited = {str(current)} if current else set()
    left_info = ""
    max_attempts = max(1, len(tabs)) * 2 + 2
    for _ in range(max_attempts):
        _emit(progress, "正在检查队伍（解散/离开）…")
        ok, info = team.leave_or_disband_team(win)
        if not ok:
            _log(f"队伍处理失败：{info}", "WARN")
            return False, f"队伍处理失败：{info}"
        _log(f"队伍处理：{info}")
        if "已解散队伍" in info:
            return True, info
        if "已离开队伍" in info:
            left_info = info
        target = next((str(tid) for tid in tabs
                       if str(tid) not in visited), None)
        if target is None:
            if left_info:
                return True, left_info + "（未找到队长 Tab，原队伍可能仍存在）"
            return True, "未组队，无需解散"
        visited.add(target)
        if not _switch_to_tab(win, target):
            return False, f"切换 Tab {target} 失败，已停止退出"
    return False, "队伍处理尝试次数超限"


def _scan_text(img, region):
    try:
        items, _ = game_map.ocr_region(img, region)
    except Exception:
        return []
    return [(str(text or ""), rect) for text, rect, _score in (items or [])]


def _find_menu_exit(img):
    """在已打开的系统菜单里找「退出游戏」；返回 (point, menu_visible)。"""
    point = None
    visible = False
    for text, rect in _scan_text(img, MENU_OCR_REGION):
        if not text:
            continue
        visible = True
        if "退出游戏" in text:
            try:
                point = ((int(rect[0]) + int(rect[2])) // 2,
                         (int(rect[1]) + int(rect[3])) // 2)
            except Exception:
                point = MENU_ITEM_FALLBACK
    return point, visible


def _exit_panel_visible(img):
    for text, _rect in _scan_text(img, PANEL_OCR_REGION):
        if any(mark in text for mark in ("玩法进度提醒", "离线修元",
                                         "到主画面")):
            return True
    return False


def _click(win, point, label):
    info = silent_click.send_silent_click(win, point[0], point[1])
    if info is None:
        _log(f"{label}点击投递失败", "WARN")
        return False
    return True


def _open_and_click_exit(win):
    """打开系统菜单 →「退出游戏」→ 提醒面板底部「退出游戏」。"""
    img = win.capture(force_activate=False)
    if img is None:
        return False, "窗口截图失败"
    point = game_map.find_template_in(
        img, SYSTEM_ICON, region=SYSTEM_ICON_REGION)
    if point is None:
        point = SYSTEM_ICON_FALLBACK
    if not _click(win, point, "系统菜单图标"):
        return False, "系统菜单图标点击失败"

    menu_point = None
    menu_visible = False
    deadline = time.time() + MENU_WAIT
    while time.time() < deadline:
        time.sleep(0.35)
        img = win.capture(force_activate=False)
        if img is None:
            return False, "窗口截图失败"
        menu_point, menu_visible = _find_menu_exit(img)
        if menu_point is not None:
            break
    if menu_point is None:
        if menu_visible:
            menu_point = MENU_ITEM_FALLBACK
        else:
            return False, "系统菜单未打开（未找到「退出游戏」）"
    _log(f"系统菜单退出游戏 @ {menu_point}")
    if not _click(win, menu_point, "退出游戏菜单"):
        return False, "退出游戏菜单点击失败"

    button_point = None
    deadline = time.time() + PANEL_WAIT
    while time.time() < deadline:
        time.sleep(0.3)
        img = win.capture(force_activate=False)
        if img is None:
            return False, "窗口截图失败"
        button_point = game_map.find_template_in(
            img, EXIT_BUTTON, region=EXIT_BUTTON_REGION)
        if button_point is not None:
            break
        if _exit_panel_visible(img):
            button_point = EXIT_BUTTON_FALLBACK
            break
    if button_point is None:
        return False, "退出游戏提醒面板未打开（未找到底部退出按钮）"
    _log(f"提醒面板退出游戏 @ {button_point}")
    if not _click(win, button_point, "提醒面板退出游戏"):
        return False, "提醒面板退出游戏点击失败"
    return True, "已点击退出游戏"


def exit_client(win, progress=None):
    """关闭游戏客户端；多 Tab 客户端逐 Tab 退出，直到窗口消失/无登录 Tab。"""
    for _ in range(MAX_CLIENT_ATTEMPTS):
        current = game.find_main_window()
        if current is None:
            return True, "游戏客户端已关闭"
        win = current
        tabs = _logged_tab_ids(win)
        if not tabs:
            return True, "已退出到登录界面"
        _emit(progress, "正在打开系统菜单并退出游戏…")
        ok, info = _open_and_click_exit(win)
        _log(info, "SYS" if ok else "WARN")
        if not ok:
            return False, info
        before = tuple(tabs)
        deadline = time.time() + CLIENT_CLOSE_WAIT
        while time.time() < deadline:
            time.sleep(0.4)
            current = game.find_main_window()
            if current is None:
                return True, "游戏客户端已关闭"
            if tuple(_logged_tab_ids(current)) != before:
                break
    if game.find_main_window() is None:
        return True, "游戏客户端已关闭"
    return False, "已点击退出游戏，但客户端仍未关闭"
