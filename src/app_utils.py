"""通用工具：日志输出、拟人化鼠标操作。"""
import json
import os
import pathlib
import random
import re
import threading
import time

from ascript.windows.hardware.mouse import Mouse
from ascript.windows.system.key_value import KeyValue

# ---- 本地日志文件（开发调试用：日志落盘到工程目录 logs/，运行中/结束后
# 可直接读取文件排查问题，无需翻浮动窗口日志面板）----
_LOG_DIR = pathlib.Path(os.getcwd()) / "logs"
_LOG_FILE = None       # 当前日志文件路径（按日期滚动）
_LOG_FH = None         # 当前日志文件句柄（惰性打开，每行 flush）
_LOG_DATE = ""         # 句柄对应的日期（跨天滚动用）
_LOG_LOCK = threading.Lock()  # 战斗监控线程与任务主线程都会打日志


def _open_log_file():
    """惰性打开本地日志文件（logs/YYYY-MM-DD.log），跨天自动滚动。

    打开新一天的日志文件时顺带清理超过 7 天的旧日志（保留近 7 天，
    用户 2026-10-15 要求；进程内每天至多清理一次）。
    """
    global _LOG_FILE, _LOG_FH, _LOG_DATE
    today = time.strftime("%Y-%m-%d")
    if _LOG_FH is not None and _LOG_DATE == today:
        return
    if _LOG_FH is not None:
        try:
            _LOG_FH.close()
        except Exception:
            pass
        _LOG_FH = None
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = _LOG_DIR / f"{today}.log"
        _LOG_FH = open(path, "a", encoding="utf-8")
        _LOG_FILE = str(path)
        _LOG_DATE = today
        _cleanup_old_logs()
    except Exception:
        _LOG_FH = None


def _cleanup_old_logs():
    """删除 logs/ 下修改时间超过 7 天的日志文件（清理失败静默）。"""
    try:
        cutoff = time.time() - 7 * 86400
        for p in _LOG_DIR.glob("*.log"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
    except Exception:
        pass


def log_file_path():
    """返回本地日志文件路径（惰性创建文件与目录），供启动时打印。"""
    with _LOG_LOCK:
        _open_log_file()
        return _LOG_FILE


def log(msg, level="INFO"):
    """带时间戳和级别的日志输出：控制台（浮动窗口日志面板）+ 本地文件。

    本地文件为 logs/日期.log（进程工作目录下），每行即时落盘，
    运行中/结束后可直接读取文件排查问题。
    """
    line = f"{time.strftime('%H:%M:%S')} [{level}] {msg}"
    print(line)
    with _LOG_LOCK:
        try:
            _open_log_file()
            if _LOG_FH is not None:
                _LOG_FH.write(line + "\n")
                _LOG_FH.flush()  # 每行落盘，运行中即可直接读取
        except Exception:
            pass  # 文件日志失败不影响自动化流程


# 全局输入互斥：战斗监控线程（战后血蓝右键/巫医点击/盒子点击）与任务
# 线程（追踪链接/对话框/领取点击）共享物理鼠标，无锁时「移动+点击」序列
# 会被交错——A 线程移动后 B 线程插入移动，A 的点击落在 B 的目标上
# （审计实测风险：误右键背包物品=使用物品）。RLock 可重入：human_click
# 内调 human_move 的二次获取不阻塞；锁内仅 40~90ms 移动+一次点击，
# 不会饿死 0.5s 轮询的战斗线程。
_INPUT_LOCK = threading.RLock()
# 背包流程级互斥锁：整段背包流程（摄妖香/修罗返程/五倍返程）持锁，
# 防止两个流程并行操作同一背包交错破坏对方状态（实测：摄妖香
# watchdog 关包后返程流程的右键回城符点在游戏世界，没传送——
# 2026-10-15）。_INPUT_LOCK 只保证单次移动+点击原子，不保证整段
# 流程互斥；新增背包用户必须整段持本锁。
_BAG_LOCK = threading.RLock()


def human_move(target_x: int, target_y: int, duration: float = None):
    """直接移动到屏幕坐标（无轨迹），保持极短耗时。

    Args:
        target_x: 目标屏幕 X 坐标
        target_y: 目标屏幕 Y 坐标
        duration: 移动耗时（秒），None 时随机 40~90 毫秒
    """
    with _INPUT_LOCK:
        if duration is None:
            duration = random.uniform(0.04, 0.09)
        Mouse.mouse_move(target_x, target_y, move_duration=duration)


def human_click(win, x: int, y: int, button: str = "primary"):
    """拟人化点击：平滑弧线移动到窗口相对坐标并点击。

    落点带 ±2px 随机偏移，模拟人手的不精确性。
    移动+点击整体持锁（见 _INPUT_LOCK），防止与其他线程的点击序列交错。

    Args:
        win: Window 对象（用于窗口相对坐标转屏幕坐标）
        x: 窗口相对 X 坐标
        y: 窗口相对 Y 坐标
        button: 鼠标按键，'primary' 左键 / 'secondary' 右键
    """
    with _INPUT_LOCK:
        sx = win.rect.left + x + random.randint(-2, 2)
        sy = win.rect.top + y + random.randint(-2, 2)
        human_move(sx, sy)
        Mouse.click(button=button)


def precise_click(win, x: int, y: int, times: int = 1, interval: float = 0.4,
                  move_duration: float = None):
    """精确点击：跳过拟人化落点抖动（±2px），支持原地连点（购买多件）。

    全程持 _INPUT_LOCK（含点击间隔），与战斗/任务线程互斥；
    move_duration=None 时按拟人化时长随机移动（human_move 同款）。

    Args:
        win: Window 对象（窗口相对坐标转屏幕坐标）
        x: 窗口相对 X 坐标
        y: 窗口相对 Y 坐标
        times: 原地连点次数（每次间隔 interval 秒，落点不变）
        interval: 连点间隔（秒）
        move_duration: 移动耗时（秒），None 时随机 40~90 毫秒
    """
    with _INPUT_LOCK:
        sx = win.rect.left + int(x)
        sy = win.rect.top + int(y)
        Mouse.mouse_move(sx, sy, move_duration=(
            random.uniform(0.04, 0.09) if move_duration is None else move_duration))
        for _ in range(int(times)):
            Mouse.click(button="primary")
            if interval:
                time.sleep(interval)


def move_away(win):
    """点击对话框选项后把鼠标移走（窗口左下角中性区域）。

    鼠标停留在选项上会使选项进入悬停变色状态，干扰后续的绿字判定
    （选项变色后绿色占比下降，二次 OCR 会漏判/误判——用户实测）。
    """
    human_move(win.rect.left + 20, win.rect.top + win.rect.height - 20)


def norm_text(s):
    """去标点/空格/特殊字符（保留中文与字母数字），匹配前统一规范化。

    OCR 对标点误读常见（「，」读成「；」、句号变逗号等），去标点后
    再做精确/模糊匹配可大幅降低漏配（各任务模块共用，勿再本地复制）。
    """
    return re.sub(r"[^\w]", "", s)


# ---- KeyValue 跨进程合并读写 ----
# KeyValue 是整文件覆写的 JSON 存储，_data 每进程只在首次访问时从磁盘
# 加载一次；库的 _load_data() 只返回文件内容、不更新 _data——保存前
# 调用它是空操作。面板进程与任务子进程各自持有启动时的旧快照，任意
# 一方直接 save 都会用旧快照抹掉对方的运行时写入（实测：五环用后
# 摄妖香时间戳被面板保存抹掉，23 分钟后修罗再吃一次）。
# 以下包装在锁内完成「重读→改→写」，把跨进程覆盖窗口缩到最小。
def kv_save_merged(key, value):
    """重读 config.json 后覆盖写入单键（防旧快照整文件覆写）。"""
    KeyValue._initialize()
    with KeyValue._lock:
        KeyValue._data = KeyValue._load_data()
        KeyValue._data[key] = value
        _kv_commit_locked()


def kv_update_merged(key, update):
    """重读 config.json 后按回调改写单键（回调收到该键当前值，返回新值）。"""
    KeyValue._initialize()
    with KeyValue._lock:
        KeyValue._data = KeyValue._load_data()
        KeyValue._data[key] = update(KeyValue._data.get(key))
        _kv_commit_locked()


def kv_remove_merged(key):
    """重读 config.json 后删除单键（不存在则不动文件）。"""
    KeyValue._initialize()
    with KeyValue._lock:
        KeyValue._data = KeyValue._load_data()
        if key in KeyValue._data:
            del KeyValue._data[key]
            _kv_commit_locked()


def _kv_commit_locked():
    """锁内写盘（格式同库的 _commit；外层已持 _lock，不能再调 _commit——
    其内部会再次获取同一把非可重入锁导致死锁）。"""
    KeyValue._file_path.write_text(
        json.dumps(KeyValue._data, indent=4, ensure_ascii=False),
        encoding="utf-8")


# ---- 高性能模式共享设施（固定位置 UI 坐标缓存） ----
# 各任务模块共用：任务进程启动时由 main.task_start 按通用设置置位；
# 主进程（验证流程等）恒为 False，始终走识别路径。
_high_perf = False
_fixed_pos_cache = {}  # key -> (窗口宽, 窗口高, x, y)
# 高性能：入口点击后等待对话框渲染的固定延时（用户实测 500ms 即出，
# 免 OCR 轮询）
HIGH_PERF_DIALOG_WAIT = 0.5
# 高性能缓存持久化键（钝化到 config.json，重启后直接可用，无需每次
# 启动先跑识别路径建立缓存；坐标是窗口相对值，窗口移动不影响）
_HIGH_PERF_CACHE_KEY = "high_perf_cache"


def _persist_high_perf_cache():
    """把固定位置缓存写入配置（kv_save_merged：重读合并，防任务子进程
    用启动时的旧快照覆盖面板进程运行中新保存的设置）。"""
    try:
        kv_save_merged(_HIGH_PERF_CACHE_KEY,
                       {k: list(v) for k, v in _fixed_pos_cache.items()})
    except Exception as e:
        log(f"[高性能] 缓存持久化失败: {e}", "WARN")


def set_high_perf(on):
    """设置高性能开关（任务进程启动时调用一次）；开启时加载持久化缓存。"""
    global _high_perf
    _high_perf = bool(on)
    if _high_perf:
        try:
            data = KeyValue.get(_HIGH_PERF_CACHE_KEY, {}) or {}
            for key, entry in data.items():
                if (isinstance(entry, (list, tuple)) and len(entry) == 4
                        and all(isinstance(v, int) for v in entry)):
                    _fixed_pos_cache[key] = tuple(entry)
        except Exception:
            pass  # 缓存加载失败不影响运行（回退识别路径自建缓存）


def high_perf_on():
    """高性能开关是否开启（关闭时全部缓存设施退化为识别路径）。"""
    return _high_perf


def fixed_pos_get(key, win):
    """读取固定位置缓存坐标；窗口尺寸不匹配（缩放/换窗口）视为失效返回 None。"""
    entry = _fixed_pos_cache.get(key)
    if entry is None:
        return None
    w, h, x, y = entry
    return (x, y) if (w, h) == (win.rect.width, win.rect.height) else None


def fixed_pos_set(key, win, x, y):
    """记录固定位置坐标（识别路径成功时调用，位置漂移自愈），并持久化。"""
    _fixed_pos_cache[key] = (win.rect.width, win.rect.height, int(x), int(y))
    _persist_high_perf_cache()


def fixed_pos_clear(key):
    """清除固定位置缓存（缓存点击校验失败时调用，下轮回退识别路径）。"""
    if _fixed_pos_cache.pop(key, None) is not None:
        _persist_high_perf_cache()


# ---- 角色维度运行时状态（按窗口账号 ID 键控，跨重启持久） ----
# 摄妖香 1 小时有效期、当天任务完成进度、清屏幂等标记均按角色记录：
# 键 = 窗口标题中的「（ID：xxx）」（未登录/无 ID 时退完整标题）。
def char_key(win):
    """角色键：窗口标题中的账号 ID 片段；无 ID 时回退完整标题。"""
    title = getattr(win, "title", "") or ""
    m = re.search(r"ID：(\d+)", title)
    return f"（ID：{m.group(1)}）" if m else title


def _char_state_get(key, ckey, default=None):
    """读取角色维度状态（损坏数据按未记录处理）。"""
    try:
        data = KeyValue.get(key, {}) or {}
        return data.get(ckey, default)
    except Exception:
        return default


def _char_state_set(key, ckey, value):
    """写入角色维度状态（kv_update_merged：保存前重读合并，防任务子进程
    整文件覆写面板进程运行中保存的设置——同高性能缓存持久化的坑）。"""
    try:
        kv_update_merged(key, lambda data: {**(data or {}), ckey: value})
    except Exception as e:
        log(f"[状态] {key} 持久化失败: {e}", "WARN")


# ---- 摄妖香 1 小时有效期管理（实际有效期 1 小时；按 57 分钟续用，
#      提前 3 分钟，避免到点空挡遇敌——日志文案仍按「1 小时」表述） ----
SHEYAOXIANG_VALID_SEC = 57 * 60                  # 续用间隔 57 分钟（有效期 1 小时）
_SHEYAOXIANG_KEY = "sheyaoxiang_last_use"       # {char_key: 使用时间戳}


def sheyaoxiang_due(win):
    """该角色摄妖香是否到期需重新使用（从未使用/记录损坏视为到期）。"""
    last = _char_state_get(_SHEYAOXIANG_KEY, char_key(win))
    if not isinstance(last, (int, float)):
        return True
    return time.time() - last >= SHEYAOXIANG_VALID_SEC


def sheyaoxiang_mark_used(win):
    """记录该角色已使用摄妖香（成功使用后调用）。"""
    _char_state_set(_SHEYAOXIANG_KEY, char_key(win), time.time())


def sheyaoxiang_skip_nonleader(mode):
    """修罗/五倍模式下非队长不续用摄妖香（跟队无需自用，用户
    2026-10-21）；其他模式与队长/身份未判定一律 False（照常续用）。"""
    if mode not in ("修罗", "五倍"):
        return False
    from . import team  # 函数内导入：team 依赖 app_utils，避免循环导入
    return team.known_leader() is False


# ---- 当天任务完成进度持久化（停止/重启不重置完成度） ----
_TASK_PROGRESS_KEY = "task_progress"  # {char_key: {"date": "YYYY-MM-DD", "modes": {mode: done}}}


def task_progress_done(win, mode):
    """该角色当天该任务已完成的次数（跨重启持久；跨天自动清零）。"""
    ckey = char_key(win)
    today = time.strftime("%Y-%m-%d")
    try:
        data = KeyValue.get(_TASK_PROGRESS_KEY, {}) or {}
        entry = data.get(ckey, {})
        if not isinstance(entry, dict) or entry.get("date") != today:
            return 0
        return int(entry.get("modes", {}).get(mode, 0) or 0)
    except Exception:
        return 0


def task_progress_add(win, mode, done):
    """记录该角色当天任务完成次数（done 为含本轮总数）。"""
    ckey = char_key(win)
    today = time.strftime("%Y-%m-%d")

    def _merge(data):
        data = data or {}
        entry = data.get(ckey, {})
        if not isinstance(entry, dict) or entry.get("date") != today:
            entry = {"date": today, "modes": {}}
        entry.setdefault("modes", {})[mode] = int(done)
        data[ckey] = entry
        return data

    try:
        kv_update_merged(_TASK_PROGRESS_KEY, _merge)
    except Exception as e:
        log(f"[进度] {mode} 进度持久化失败: {e}", "WARN")


# ---- 启动清屏幂等（窗口句柄与角色均未变化时跳过） ----
_CLEAR_SCREEN_KEY = "clear_screen_state"  # {char_key: hwnd}


def clear_screen_skip(win):
    """窗口句柄与角色均未变化时跳过启动清屏（重复按键无收益且有
    误触风险；窗口重开/换角色后句柄变化，自动重新清屏）。"""
    hwnd = getattr(win, "hwnd", None)
    if not hwnd:
        return False
    return _char_state_get(_CLEAR_SCREEN_KEY, char_key(win)) == int(hwnd)


def clear_screen_mark(win):
    """记录该角色已清屏的窗口句柄。"""
    hwnd = getattr(win, "hwnd", None)
    if not hwnd:
        return
    _char_state_set(_CLEAR_SCREEN_KEY, char_key(win), int(hwnd))


def high_perf_click(win, key, label, prefix=""):
    """高性能缓存点击：有缓存坐标即点击并返回 True（调用方自行校验
    效果并按需回退识别路径）；开关关闭或无缓存返回 False 不点击。"""
    if not _high_perf:
        return False
    pos = fixed_pos_get(key, win)
    if pos is None:
        return False
    human_click(win, pos[0], pos[1])
    log(f"{prefix}[高性能] 已按缓存坐标点击{label} ({pos[0]}, {pos[1]})", "SYS")
    return True
