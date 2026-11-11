"""通用工具：日志输出、拟人化鼠标操作。"""
import json
import os
import pathlib
import random
import re
import sys
import threading
import time
from contextlib import contextmanager

from ascript.windows.hardware.mouse import Mouse
from ascript.windows.system.key_value import KeyValue

from . import user_data

try:
    from pydirectinput import FailSafeException as _PDIFailSafe
except ImportError:  # 理论不可达（ascript Mouse 依赖 pydirectinput），防御性兜底
    _PDIFailSafe = ()

# ---- 本地日志文件（开发调试用：日志落盘到工程目录 logs/，运行中/结束后
# 可直接读取文件排查问题，无需翻浮动窗口日志面板）----
_LOG_DIR = pathlib.Path(os.getcwd()) / "logs"
_LOG_FILE = None       # 当前日志文件路径（按日期滚动）
_LOG_FH = None         # 当前日志文件句柄（惰性打开，每行 flush）
_LOG_DATE = ""         # 句柄对应的日期（跨天滚动用）
_LOG_LOCK = threading.Lock()  # 战斗监控线程与任务主线程都会打日志

# 单测进程不落盘本地日志：pytest 从工程根目录运行，log() 会写进
# logs/日期.log 与真实应用日志混排（如测试 _FakeWin 异常 WARN 混入
# 生产日志，2026-11-06 排查时被误读）。控制台 print 保留（pytest
# 自行捕获）。
_UNDER_PYTEST = "pytest" in sys.modules \
    or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _open_log_file():
    """惰性打开本地日志文件（logs/YYYY-MM-DD.log），跨天自动滚动。

    打开新一天的日志文件时顺带清理超过 7 天的旧日志（保留近 7 天，
    用户 2026-10-15 要求；进程内每天至多清理一次）。单测进程跳过
    （见 _UNDER_PYTEST）。
    """
    global _LOG_FILE, _LOG_FH, _LOG_DATE
    if _UNDER_PYTEST:
        return
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

# 静默点击模式：置位期间所有鼠标点击改走窗口消息投递（不移动物理
# 鼠标、不激活窗口），鼠标移动/移开变为空操作——消息点击不产生物理
# 悬停态，move_away 的防悬停变色不再需要。诊断页静默修罗领取/巫医
# 验证按钮使用（流程代码与物理鼠标版完全复用，仅点击通道切换）；
# 主进程一次只跑一个验证（_verifying 互斥），普通全局开关即可。
# 任务子进程不启用该通道（维持物理鼠标方案，两通道并存时由
# _INPUT_LOCK 在消息投递侧互斥）。
_SILENT_CLICK = False


@contextmanager
def silent_clicks():
    """上下文：期间所有鼠标点击走静默消息通道（见 _SILENT_CLICK）。"""
    global _SILENT_CLICK
    prev = _SILENT_CLICK
    _SILENT_CLICK = True
    try:
        yield
    finally:
        _SILENT_CLICK = prev


def set_silent_mode(on):
    """设置静默操作开关（通用设置，默认开启；主进程与任务子进程启动
    时按持久化配置调用）。开启后全部点击走消息通道（见 _SILENT_CLICK），
    鼠标移动变空操作——游戏窗口可不前台。"""
    global _SILENT_CLICK
    _SILENT_CLICK = bool(on)


def silent_mode_on():
    """静默操作开关是否开启。"""
    return _SILENT_CLICK


def keyboard_ok(win=None):
    """键盘/激活类操作的前置：非静默模式恒可用（各流程自带激活）；
    静默模式下键盘事件只进前台窗口——游戏已在前台才允许，不再额外
    激活（激活违背静默约束）。win=None 或读取失败返回 False 保守处理。"""
    if not _SILENT_CLICK:
        return True
    if win is None:
        return False
    try:
        import win32gui
        return win32gui.GetForegroundWindow() == int(getattr(win, "hwnd", 0))
    except Exception:
        return False


def _silent_click(win, x, y, button="primary"):
    """静默点击统一出口：延迟导入防循环（silent_click 反向导入本模块
    拿 _INPUT_LOCK）。"""
    from . import silent_click
    silent_click.send_silent_click(win, x=x, y=y, button=button)


class UserAbort(BaseException):
    """用户人工接管鼠标（fail-safe 触发）——任务应干净中止。

    触发条件：物理鼠标位于屏幕角落时，PyDirectInput/PyAutoGUI 的
    fail-safe 会拒绝一切输入操作（脚本自身所有移动目标都在游戏窗口
    内，不可能把鼠标移到角落，只有人能触发）。

    继承 BaseException 而非 Exception：任务循环内遍布的
    ``except Exception`` 兜底不应吞掉用户中断——与 KeyboardInterrupt
    同理，直接穿透到 task_start 顶层收口（日志落盘 + 线程干净退出，
    2026-10-31 fail-safe 致线程裸崩、堆栈丢失在控制台的教训）。
    """


def _convert_failsafe(e):
    """鼠标输入异常识别：fail-safe 命中转 UserAbort，其余原样返回。

    ascript Mouse 走 pydirectinput（FAILSAFE=True），类型信号为
    pydirectinput.FailSafeException；消息兜底（"fail-safe"）覆盖后端
    替换/异常被包装后类型丢失的情况。返回后调用方以裸 ``raise``
    重抛原异常（本函数不抛即非 fail-safe）。
    """
    if isinstance(e, _PDIFailSafe) or "fail-safe" in str(e).lower():
        raise UserAbort(
            "检测到鼠标位于屏幕角落（fail-safe 触发），判定用户人工接管") from e


def _mouse_call(fn, *args, **kwargs):
    """执行 Mouse 调用并把 fail-safe 异常转成 UserAbort（其余原样抛出）。"""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        _convert_failsafe(e)
        raise


def human_move(target_x: int, target_y: int, duration: float = None):
    """直接移动到屏幕坐标（无轨迹），保持极短耗时。

    Args:
        target_x: 目标屏幕 X 坐标
        target_y: 目标屏幕 Y 坐标
        duration: 移动耗时（秒），None 时随机 40~90 毫秒
    """
    if _SILENT_CLICK:
        return  # 静默模式：消息点击不需要物理移动
    with _INPUT_LOCK:
        if duration is None:
            duration = random.uniform(0.04, 0.09)
        _mouse_call(Mouse.mouse_move, target_x, target_y, move_duration=duration)


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
    if _SILENT_CLICK:
        _silent_click(win, x, y, button=button)
        return
    with _INPUT_LOCK:
        sx = win.rect.left + x + random.randint(-2, 2)
        sy = win.rect.top + y + random.randint(-2, 2)
        human_move(sx, sy)
        _mouse_call(Mouse.click, button=button)


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
    if _SILENT_CLICK:
        for _ in range(int(times)):
            _silent_click(win, x, y)
            if interval:
                time.sleep(interval)
        return
    with _INPUT_LOCK:
        sx = win.rect.left + int(x)
        sy = win.rect.top + int(y)
        _mouse_call(Mouse.mouse_move, sx, sy, move_duration=(
            random.uniform(0.04, 0.09) if move_duration is None else move_duration))
        for _ in range(int(times)):
            _mouse_call(Mouse.click, button="primary")
            if interval:
                time.sleep(interval)


def rapid_click(win, x: int, y: int, press: float = 0.02):
    """极速点击：瞬时移动到窗口相对坐标并按下抬起（默认按压 20ms）。

    供锄遗聚宝批量消除杂气等吞吐优先的热路径使用——每击省掉拟人化
    移动耗时与 50ms 默认按压，约 25ms/击；按压时长小于 ascript 默认
    50ms（游戏点击注册通常 10ms 即足够，20ms 为保守折中，如出现漏
    注册可调回 0.05）。仍持全局输入锁防与战斗/任务线程交错。
    """
    if _SILENT_CLICK:
        _silent_click(win, x, y)
        return
    with _INPUT_LOCK:
        sx = win.rect.left + int(x)
        sy = win.rect.top + int(y)
        _mouse_call(Mouse.mouse_move, sx, sy, move_duration=0.0)
        _mouse_call(Mouse.mouse_down, button="primary")
        time.sleep(press)
        _mouse_call(Mouse.mouse_up, button="primary")


def rapid_click_burst(win, x, y, times=1, press=0.02, interval=0.0):
    """原地高频连点：一次定位，连点 times 次（冲刺连点小游戏热路径）。

    与 rapid_click 的区别：整批点击持一次全局输入锁、只移动一次，
    静默模式一整批只做一次窗口/坐标换算并逐击投递按下/抬起消息（无
    3×50ms 填充延迟，约 20ms/击），物理模式同理只移动一次后逐击
    按下/抬起。供「每秒需连点十余次」的小游戏使用（蛟魔探秘钓鱼：
    单击仅推进 ~0.1 刻度，逐击走 rapid_click 静默通道实测 ~150ms/击，
    倒计时内推不到目标带）。press 为每击按压时长（秒）。
    """
    n = max(1, int(times))
    if _SILENT_CLICK:
        from . import silent_click  # 延迟导入防循环（见 _silent_click）
        return silent_click.send_silent_click_burst(
            win, x=x, y=y, times=n, press=press, interval=interval)
    with _INPUT_LOCK:
        sx = win.rect.left + int(x)
        sy = win.rect.top + int(y)
        _mouse_call(Mouse.mouse_move, sx, sy, move_duration=0.0)
        for _ in range(n):
            _mouse_call(Mouse.mouse_down, button="primary")
            if press > 0:
                time.sleep(press)
            _mouse_call(Mouse.mouse_up, button="primary")
            if interval > 0:
                time.sleep(interval)
    return n


class MouseHold:
    """长按鼠标按键：按下后保持，直到 stop() 抬起。

    钓鱼小游戏「长按」按钮使用——按住期间进度持续推进、松开即停。
    整个长按周期持有全局输入锁，防止战斗/任务线程移动物理鼠标或插入
    点击破坏按下状态（真实长按的语义就是按住不动）。start/stop 必须
    在同一线程（_INPUT_LOCK 为 RLock，线程归属），调用方必须 try/finally
    保证 stop（异常/战斗打断时释放，否则鼠标卡在按下状态）。

    静默模式：投递 WM_LBUTTONDOWN 后延后 WM_LBUTTONUP（游戏内部按键
    状态保持按下），不需要物理光标；物理模式：Mouse.mouse_down/up。
    窗口/客户区获取失败时 start 返回 False（本次未按下，下轮重试）。
    """

    def __init__(self, win, x, y, button="primary"):
        self.win = win
        self.x = int(x)
        self.y = int(y)
        self.button = button
        self._silent = _SILENT_CLICK
        self._active = False
        self._info = None

    @property
    def active(self):
        """当前是否处于按下状态。"""
        return self._active

    def start(self):
        """按下按键；成功返回 True（输入锁保持到 stop）。"""
        if self._active:
            return True
        if self._silent:
            from . import silent_click  # 延迟导入防循环（见 _silent_click）
            _INPUT_LOCK.acquire()
            try:
                info = silent_click.begin_silent_hold(
                    self.win, self.x, self.y, button=self.button)
            except BaseException:
                _INPUT_LOCK.release()
                raise
            if info is None:
                _INPUT_LOCK.release()
                return False
            self._info = info
        else:
            _INPUT_LOCK.acquire()
            try:
                sx = self.win.rect.left + self.x
                sy = self.win.rect.top + self.y
                _mouse_call(Mouse.mouse_move, sx, sy,
                            move_duration=random.uniform(0.04, 0.09))
                _mouse_call(Mouse.mouse_down, button=self.button)
            except BaseException:
                _INPUT_LOCK.release()
                raise
        self._active = True
        return True

    def stop(self):
        """抬起按键并释放输入锁（未按下时无操作）。"""
        if not self._active:
            return
        try:
            if self._silent:
                from . import silent_click
                silent_click.end_silent_hold(self._info)
            else:
                _mouse_call(Mouse.mouse_up, button=self.button)
        finally:
            self._active = False
            self._info = None
            _INPUT_LOCK.release()


def move_away(win):
    """点击对话框选项后把鼠标移走（窗口左下角中性区域）。

    鼠标停留在选项上会使选项进入悬停变色状态，干扰后续的绿字判定
    （选项变色后绿色占比下降，二次 OCR 会漏判/误判——用户实测）。
    """
    if _SILENT_CLICK:
        return  # 静默模式无物理悬停态，无需移开
    human_move(win.rect.left + 20, win.rect.top + win.rect.height - 20)


def norm_text(s):
    """去标点/空格/特殊字符（保留中文与字母数字），匹配前统一规范化。

    OCR 对标点误读常见（「，」读成「；」、句号变逗号等），去标点后
    再做精确/模糊匹配可大幅降低漏配（各任务模块共用，勿再本地复制）。
    """
    return re.sub(r"[^\w]", "", s)


# 角色名比对的易混字符归一化表（2026-11-09：组号成员少识别 1 个的教训）：
# 队员名「〃忆ㄨ嚣魂ˉ」被面板 OCR 稳定误读成「”忆×嚣魂一」——三个特殊
# 字符在游戏字体下同形，逐字符归一化到同一类后模糊比 0.5 → 1.0。
# 只合并「OCR 无法区分的同形家族」，不用 norm_text（去标点保不住 ㄨ/×、
# ˉ/一 这类字样式误读——ㄨ 是注音字母、× 是数学符号，去标点后两侧仍不同）。
_NORM_NAME_CONFUSABLE = {
    # 引号/重复号家族：〃 与弯引号在游戏字体下同形，OCR 互通
    "〃": "q", "”": "q", "“": "q", '"': "q", "'": "q", "‘": "q",
    "’": "q", "′": "q", "´": "q",
    # x 家族：注音 ㄨ 与乘号 × 同形（本例 ㄨ→× 误读）
    "ㄨ": "x", "×": "x", "✕": "x", "✖": "x", "χ": "x",
    # 一/横线家族：ˉ 与 一 同形（本例 ˉ→一 误读）；ー/— 亦常互换
    "ˉ": "i", "¯": "i", "一": "i", "ー": "i", "—": "i", "–": "i",
    "‐": "i", "-": "i",
}


def norm_name_for_cmp(s):
    """角色名比对前归一化：易混字符映射到同一类，OCR 稳定误读不再漏配。

    与 norm_text 互补：norm_text 去标点，这里把「字模样式家族」内的
    字符归为一类（q/x/i），两侧同样归一后 0.7 模糊比能扛住多字符
    系统性误读。仅用于角色名比对（Tab 标题 vs 面板 OCR、暂离身份
    记账），不用于对话框文案等其他文本链路。
    """
    if not s:
        return ""
    return "".join(_NORM_NAME_CONFUSABLE.get(c, c) for c in s)


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


def kv_save_many_merged(mapping):
    """重读 config.json 后一次批量写入多键（防旧快照整文件覆写）。

    配置方案切换用：一次「重读→改→写」替换全部设置键，避免逐键调用
    kv_save_merged 造成的 N 次整文件读写。
    """
    KeyValue._initialize()
    with KeyValue._lock:
        KeyValue._data = KeyValue._load_data()
        KeyValue._data.update(mapping)
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


def kv_remove_many_merged(keys):
    """重读 config.json 后一次删除多个键（旧角色字段清理用）。

    返回实际删除的键数量；无键可删时不写文件。
    """
    KeyValue._initialize()
    removed = 0
    with KeyValue._lock:
        KeyValue._data = KeyValue._load_data()
        for key in keys:
            if key in KeyValue._data:
                del KeyValue._data[key]
                removed += 1
        if removed:
            _kv_commit_locked()
    return removed


def _kv_commit_locked():
    """锁内写盘（格式同库的 _commit；外层已持 _lock，不能再调 _commit——
    其内部会再次获取同一把非可重入锁导致死锁）。"""
    KeyValue._file_path.write_text(
        json.dumps(KeyValue._data, indent=4, ensure_ascii=False),
        encoding="utf-8")


def kv_read_fresh(key, default=None):
    """直读磁盘取单键——主进程读任务进程（或反向）的运行时写入。

    KeyValue.get 读的是本进程 _data 快照（首次访问时从磁盘加载一次、
    _load_data 不更新快照），对侧进程 kv_save_merged 写入后本进程
    永远读不到新值（2026-11-07 实测：任务进程启动检查写 group_detected，
    主进程入口不展示）。锁内重读文件与 kv_save_merged 同源，返回
    该键当前值；文件读取异常返回 default。
    """
    try:
        KeyValue._initialize()
        with KeyValue._lock:
            data = KeyValue._load_data()
        return data.get(key, default)
    except Exception:
        return default


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


def _load_high_perf_cache():
    """读取当前角色的高性能缓存。

    迁移后的新角色没有缓存文件、也不回退旧版全局缓存（2026-11-11
    用户确认：只有迁移时的当前角色继承旧缓存，其他角色重新学习）。
    未迁移的旧版/单测环境才回退 config.json 的全局键。
    """
    try:
        data = user_data.read_high_perf()
        if data:
            return data
        if not user_data.is_migrated():
            return KeyValue.get(_HIGH_PERF_CACHE_KEY, {}) or {}
    except Exception:
        pass
    return {}


def _persist_high_perf_cache():
    """把固定位置缓存写入当前角色 high_perf_cache.json。"""
    try:
        user_data.write_high_perf(
            {k: list(v) for k, v in _fixed_pos_cache.items()})
    except Exception as e:
        log(f"[高性能] 缓存持久化失败: {e}", "WARN")


def set_high_perf(on):
    """设置高性能开关（任务进程启动时调用一次）；开启时加载当前角色缓存。"""
    global _high_perf
    _high_perf = bool(on)
    if _high_perf:
        try:
            data = _load_high_perf_cache()
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


_MISSING = object()


def _char_state_get(key, ckey, default=None):
    """读取角色维度状态（data/<角色>/state.json；旧版未迁移时回退 config）。"""
    role = user_data.role_id_from_char_key(ckey)
    try:
        value = user_data.get_state(key, role, _MISSING)
    except Exception:
        value = _MISSING
    if value is not _MISSING:
        return value
    if not user_data.is_migrated():  # 旧版未迁移/单测兜底
        try:
            data = KeyValue.get(key, {}) or {}
            return data.get(ckey, default)
        except Exception:
            pass
    return default


def _char_state_set(key, ckey, value):
    """写入角色维度状态（data/<角色>/state.json，读-改-写原子替换）。"""
    role = user_data.role_id_from_char_key(ckey)
    try:
        user_data.set_state(key, value, role)
    except Exception as e:
        log(f"[状态] {key} 持久化失败: {e}", "WARN")


def char_state_get(key, win, default=None):
    """读取角色维度状态（key 为状态用途键，内部按 char_key(win) 键控）。

    供任务模块持久化不随轮次变化、跨重启有效的角色属性（如名望种族
    判定缓存）。损坏数据按未记录处理。
    """
    return _char_state_get(key, char_key(win), default)


def char_state_set(key, win, value):
    """写入角色维度状态（data/<角色>/state.json）。"""
    _char_state_set(key, char_key(win), value)


def role_state_get(key, default=None):
    """读取当前角色 state.json 的键（每次直读文件，跨进程可见）。"""
    try:
        return user_data.get_state(key, None, default)
    except Exception:
        return default


def role_state_set(key, value):
    """写入当前角色 state.json 的键。成功返回 True。"""
    try:
        user_data.set_state(key, value)
        return True
    except Exception as e:
        log(f"[状态] {key} 持久化失败: {e}", "WARN")
        return False


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


# ---- 当前任务模式（多任务顺序执行：任务主循环切换任务时更新，
#      战斗线程/寻路期摄妖香续用按当前模式决策，替代旧的单一模式读取） ----
_current_task_mode = ""


def set_current_task_mode(mode):
    """设置当前正在执行的任务模式（任务主循环切换任务时调用）。"""
    global _current_task_mode
    _current_task_mode = mode or ""


def current_task_mode():
    """读取当前正在执行的任务模式（任务进程内共享；无任务为空串）。"""
    return _current_task_mode


def sheyaoxiang_skip_nonleader(mode=None):
    """修罗/五倍模式下非队长不续用摄妖香（跟队无需自用，用户
    2026-10-21）；其他模式与队长/身份未判定一律 False（照常续用）。
    mode 缺省时取当前任务模式（多任务顺序执行场景）。"""
    if mode is None:
        mode = current_task_mode()
    if mode not in ("修罗", "五倍"):
        return False
    from . import team  # 函数内导入：team 依赖 app_utils，避免循环导入
    return team.known_leader() is False


# ---- 寻路信号（摄妖香续用双通道：寻路期续用 + 空闲期兜底的时钟） ----
_last_track_walk = 0.0


def mark_track_walk():
    """记录一次「点击任务追踪成功」（寻路开始）的时间戳。

    follow_task 点击任务绿字成功后调用；空闲期兜底据此判断
    「任务运行但 N 分钟无寻路」（寻路期续用没机会触发——连续
    回城符/地图点击赶路、流程卡在无寻路环节等）。
    """
    global _last_track_walk
    _last_track_walk = time.time()


def track_walk_idle_seconds():
    """距上次点击追踪寻路的秒数（从未寻路返回大值——视为长期空闲）。"""
    if not _last_track_walk:
        return 1e9
    return time.time() - _last_track_walk


def sheyaoxiang_idle_due(win, idle_minutes):
    """空闲期兜底条件：摄妖香到期且距上次寻路 ≥ idle_minutes 分钟。

    win 仅供 due 判定（角色维度时间戳）；空闲期兜底线程每分钟查
    此条件，命中才走 use_sheyaoxiang_if_due 完整门控使用流程。
    """
    return sheyaoxiang_due(win) \
        and track_walk_idle_seconds() >= idle_minutes * 60


# ---- 当天任务完成进度持久化（停止/重启不重置完成度） ----
_TASK_PROGRESS_KEY = "task_progress"  # {char_key: {"date": "YYYY-MM-DD", "modes": {mode: done}}}


def task_progress_done(win, mode):
    """该角色当天该任务已完成的次数（跨重启持久；跨天自动清零）。"""
    ckey = char_key(win)
    role = user_data.role_id_from_char_key(ckey)
    today = time.strftime("%Y-%m-%d")
    try:
        entry = user_data.get_state(_TASK_PROGRESS_KEY, role, None)
    except Exception:
        entry = None
    if entry is None and not user_data.is_migrated():  # 旧版未迁移/单测兜底
        try:
            data = KeyValue.get(_TASK_PROGRESS_KEY, {}) or {}
            entry = data.get(ckey, {})
        except Exception:
            entry = {}
    if not isinstance(entry, dict) or entry.get("date") != today:
        return 0
    try:
        return int(entry.get("modes", {}).get(mode, 0) or 0)
    except Exception:
        return 0


def task_progress_add(win, mode, done):
    """记录该角色当天任务完成次数（done 为含本轮总数）。"""
    ckey = char_key(win)
    role = user_data.role_id_from_char_key(ckey)
    today = time.strftime("%Y-%m-%d")

    def _merge(state):
        entry = state.get(_TASK_PROGRESS_KEY)
        if not isinstance(entry, dict) or entry.get("date") != today:
            entry = {"date": today, "modes": {}}
        entry.setdefault("modes", {})[mode] = int(done)
        state[_TASK_PROGRESS_KEY] = entry
        return state

    try:
        user_data.update_state(_merge, role)
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
