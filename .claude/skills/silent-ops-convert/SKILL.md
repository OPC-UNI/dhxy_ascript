---
name: silent-ops-convert
description: 用静默窗口消息（不移动鼠标、不抢前台、游戏可后台）实现新的游戏操作，或把已有鼠标点击/快捷键/键盘按键/文本输入/Tab 切换/前台激活在静默与非静默通道之间互转，并审计只有单通道、漏接静默实现的位置。当用户说「用静默操作实现 X」「不要用鼠标实现 X」「这个操作要能后台跑」「把 X 改成静默/静默改回物理鼠标」「哪些操作还没静默」「静默 Alt+X/喊话/输入/切 Tab 不生效」时使用。
---

# 静默 / 非静默操作：实现与互转

本项目所有输入都走两条通道：

| 通道 | 机制 | 代价 | 前提 |
|---|---|---|---|
| 静默 | 向游戏窗口投递窗口消息（`WM_*`） | 不移动物理鼠标、不抢前台、可与办公并行 | 窗口存在且未最小化 |
| 非静默 | 物理鼠标/键盘（pyautogui/pydirectinput） | 占用鼠标、抢前台 | 键盘只进**前台窗口** |

**「互转」不是删掉一条通道换成另一条**，而是让同一个操作同时具备两条
路径，由通用设置「静默操作」开关（`app_utils.silent_mode_on()`）在运行时
选择。点击类已在 `app_utils` 统一门控，业务代码零改动；快捷键/单键/文本
输入/Tab 切换需要显式双分支。反向转换就是确认另一条分支存在并能被开关
选中——**禁止**只留单通道。

## 需求路由

| 用户说 | 走哪节 |
|---|---|
| 「用静默操作实现 X」「不要用鼠标实现 X」「这个操作要后台跑」 | 新操作静默优先实现 |
| 「把 X 改成静默」「静默改回物理鼠标」「补一条静默通道」 | 转换配方 |
| 「哪些操作还没静默」「静默 X 不生效」 | 审计（先跑脚本）+ 硬性约束 |

## 新操作静默优先实现

用户要求「用静默操作实现某个操作」时，按以下顺序做，不要先写物理鼠标
再想办法改。

### 1. 先定验收信号

静默投递成功 ≠ 游戏生效。先确定操作成功的**可观测信号**（画面模板、
OCR 文案、任务追踪面板、地图坐标、日志/状态变化），没有信号就先补观测
手段。动作后必须轮询该信号出现，不能固定 `sleep` 后假设成功。

### 2. 拆成最小动作，逐动作选通道

| 动作 | 首选实现 |
|---|---|
| 点击（左/右/连点/长按） | `app_utils.human_click` / `precise_click` / `rapid_click` / `rapid_click_burst` / `MouseHold`（自动门控，静默时不碰鼠标） |
| 快捷键（Alt+X 等） | `silent_click.send_silent_hotkey(win, ("alt",), key)` |
| 单键（Esc/Enter） | `send_silent_hotkey(win, (), key)` |
| 文本输入 | `send_silent_text(win, text, end_enter=...)` |
| 切 Tab | `send_silent_tab_click(win, index)` |
| 定位/识别 | `win.capture(force_activate=False)` + 模板/OCR/找色（零鼠标） |
| 打开面板 | 优先找快捷键（Alt+E 背包、Alt+U 飞行驭器、Alt+C 降落），比找按钮点击更稳 |

先 `grep` 现有能力，不重复造轮子：

```bash
grep -rn "send_silent_\|def .*hotkey\|def .*click" src/device src/core --include=*.py
```

### 3. 选路原则（不得偷偷降级）

1. 项目 API + 静默消息；
2. 键盘消息替代鼠标（能用快捷键就不点坐标）；
3. **没有静默等价物**（滚轮、拖拽、只有物理输入才能完成的系统级动作）→
   明确告知限制，给替代方案（点击/键盘/消息）或停下确认，**不得**因为
   「实现方便」改用物理鼠标；
4. 必须前台/必须激活的功能（某些客户端外壳行为）→ 说明限制，不能假装
   静默可用。

### 4. 写代码：静默为主路径，物理分支仅为非静默开关保留

- 点击类**只用 `app_utils` 封装**，出现裸 `Mouse.*` 即不合格。
- 热键/文本写双分支，静默分支在前（配方见「转换配方」）。
- 静默分支禁止 `win.activate()`、禁止依赖前台/遮挡检查。
- 静默投递返回 `None`/False 必须记 WARN 并返回失败，让调用方重试/跳过。
- 任务流程调用静默按键要 `try/except ValueError`（按键表缺键会抛，见
  硬性约束第 4 条）。

**模板：静默优先的完整动作**

```python
def open_panel_and_click(win):
    # 1) 零鼠标定位（复用已截帧；滚动/轮询一律 force_activate=False）
    img = win.capture(force_activate=False)
    if img is None:
        return False, "截帧失败"
    pt = game_map.find_template_in(img, PANEL_BTN)
    if pt is None:
        return False, "未找到按钮"

    # 2) 点击：静默模式自动消息投递，非静默模式物理鼠标
    human_click(win, pt[0], pt[1])

    # 3) 判定：轮询目标状态出现，不假设点击生效
    deadline = time.time() + 3.0
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is not None and game_map.find_template_in(img, PANEL):
            return True, "面板已打开"
        time.sleep(0.5)
    return False, "面板未打开"
```

**模板：静默优先的热键/文本** 见「转换配方」的快捷键与文本输入两节。

### 5. 「不使用鼠标」的两种理解

- **运行期不占用鼠标（默认）**：保持双通道，静默开关开启时走消息；
  这是项目现有约定，也是「静默实现」的标准交付。
- **代码里不容许任何物理分支**：与项目双通道约定冲突。不要删除物理
  分支来「证明静默」——正确做法是用静默模式运行并证明光标/前台未变。
  若用户坚持单通道，先确认再改，不要擅自删。

### 6. 验证「真的没用鼠标」

三层验证缺一不可：

1. **代码层**：`audit_channels.py --strict` 无新增违规；新代码无裸
   `Mouse.*`；静默分支无 `activate`。
2. **运行层（开发期只读校验，不发任何输入）**：操作前后物理光标位置与
   前台窗口都不变：

   ```python
   import win32api
   import win32gui

   before = (win32api.GetCursorPos(), win32gui.GetForegroundWindow())
   do_silent_action(win)                         # 被测操作
   after = (win32api.GetCursorPos(), win32gui.GetForegroundWindow())
   assert after[0] == before[0], f"物理光标被移动: {before[0]} -> {after[0]}"
   assert after[1] == before[1], "前台窗口被抢占"
   ```

   （只读调用；不要在操作期间手动碰鼠标，否则会误报。）
3. **效果层**：游戏画面/状态按预期变化，失败路径也能触发（超时、目标
   不在、投递失败）。

实机观察、取模板、量坐标用 **game-window-probe** skill（项目窗口句柄
通道）；本 skill 负责通道选择与实现纪律。

## 审计：找出需要转换的位置

```bash
cd <仓库根>
python .claude/skills/silent-ops-convert/scripts/audit_channels.py
python .claude/skills/silent-ops-convert/scripts/audit_channels.py --strict
```

输出三类：物理输入（`Mouse.*` / `Keyboard.*` / `win.activate()`，逐条给
静默对应物）、静默投递（`send_silent_*`）、门控调用。`--strict` 把
「所在函数没有 `silent_mode_on` / `keyboard_ok` / `silent_clicks` 门控」
的物理输入判为漏接，退出码 1——转换完成或显式标注 `# silent-ok` 后应
清零（封装层 `src/core/app_utils.py` 自动豁免）。

手工补充搜索：

```bash
grep -rn "Keyboard\.\|Mouse\.\|win\.activate()" src/ --include=*.py
grep -rn "send_silent_" src/ --include=*.py
grep -rn "silent_mode_on\|keyboard_ok\|silent_clicks" src/ --include=*.py
```

## 通道对照表

| 操作 | 非静默（物理） | 静默 | 备注 |
|---|---|---|---|
| 左/右键点击 | `human_click(win, x, y, button)` | 同一 API 内部转 `send_silent_click` | 业务代码零改动 |
| 精确/连点 | `precise_click` / `rapid_click` | 同一 API 自动门控 | 落点抖动/按压时长语义保留 |
| 高频连点 | `rapid_click_burst` | `send_silent_click_burst` | 一次定位、逐击按下抬起 |
| 长按 | `MouseHold` | `begin_silent_hold` / `end_silent_hold` | 必须用 `MouseHold`，勿手拼 |
| 移动鼠标 | `human_move` / `move_away` | 空操作 | 消息点击无物理悬停态 |
| Alt+X 组合键 | `Keyboard.key_hot("alt", "c")` | `send_silent_hotkey(win, ("alt",), "c")` | Alt 走 `WM_SYSKEY*` |
| Ctrl+Tab 切 Tab | `Keyboard.key_hot("ctrl", "tab")` | `send_silent_tab_click(win, index)` | 后台切 Tab 走 Tab 条消息点击 |
| Esc / Enter 单键 | `Keyboard.key("esc")` | `send_silent_hotkey(win, (), "esc")` | 裸键走 `WM_KEY*` |
| 文本输入 | `Keyboard.input(text)`（剪贴板+Ctrl+V） | `send_silent_text(win, text, end_enter=...)` | `WM_CHAR` 逐字符，中文实测有效 |
| 标准 EDIT 粘贴 | Ctrl+V | `send_silent_paste(win, text)`（`WM_PASTE`） | 游戏自绘输入优先 `WM_CHAR` |
| 前台激活 | `win.activate()` + `GetForegroundWindow` 校验 | **禁止激活**；`keyboard_ok(win)` 只判前台 | 静默分支不得抢焦点 |

坐标空间两条通道完全一致：`x/y` 以 `win.rect` 左上角为原点（与
`human_click` 同空间），内部换算为可见 Tab 子窗口客户区坐标。迁移时
坐标原样搬，不要自己减标题栏/边框。

## 转换配方

### 点击：先换封装，不写分支

任务/设备模块出现裸 `Mouse.click` / `Mouse.mouse_move` /
`Mouse.mouse_down|up` 时，换成 `app_utils` 封装即可自动获得静默通道：

| 原写法 | 换成 |
|---|---|
| `Mouse.click(button=...)`（前面手拼移动） | `human_click(win, x, y, button)` |
| 原地多击 | `precise_click(win, x, y, times, interval)` |
| 极速单击 | `rapid_click(win, x, y, press)` |
| 高频连点 | `rapid_click_burst(win, x, y, times, press, interval)` |
| 按下后延时抬起 | `MouseHold(win, x, y).start()/stop()`（`try/finally`） |
| 点完把鼠标挪开 | 删除——`send_silent_click` 末尾自动把虚拟光标移到客户区左下中性区，防悬停变色 |

`MouseHold` 在**构造时**捕获当前模式（`self._silent = _SILENT_CLICK`）：
先构造再切换模式不会生效，长按对象要现用现建。

### 快捷键 / 单键：双分支配方

非静默 → 静默的标准写法（以 Alt+C 为例，照抄现有实现风格）：

```python
def _press_alt_c(win):
    """Alt+C：静默走消息投递（游戏窗口无需前台）；物理热键只进前台。"""
    if app_utils.silent_mode_on():
        from ..device import silent_click  # 延迟导入：避免模块级循环
        try:
            ok = silent_click.send_silent_hotkey(win, ("alt",), "c") is not None
        except Exception as e:  # 按键表缺键等异常不得打崩任务流程
            log(f"静默 Alt+C 投递异常: {e}", "WARN")
            return False
        if not ok:
            log("静默 Alt+C 投递失败（窗口/句柄失效）", "WARN")
        return ok
    if not app_utils.keyboard_ok(win):
        log("游戏不在前台：跳过 Alt+C", "WARN")
        return False
    Keyboard.key_hot("alt", "c")
    return True
```

要点：

- **静默分支在前**，物理分支在后；物理分支用 `keyboard_ok(win)` 兜底，
  静默模式下它只在游戏已前台时才放行物理键盘（不激活）。
- 静默投递返回 `None`/False 必须记 WARN（2026-11-16 地图点击失效排查
  教训：投递失败无日志等于静默丢失）。
- 任务流程调用静默按键要 `try/except`：`_KEY_CODES` 缺键会抛
  `ValueError`（2026-11-07 静默 Alt+U 缺键打崩五环的教训）。
- 调用处若只需「按了就往下走」，可像 `map.read_position_with_recovery`
  一样 `if silent: send_silent_hotkey(...) elif keyboard_ok: Keyboard.key_hot(...)
  else: 跳过并返回 None`。

参考实现：`src/device/map.py`（Alt+C 恢复）、`src/device/bag.py`
（Alt+E 开包）、`src/device/zuoqi.py`（Alt+U，带异常兜底）、
`src/device/clear_screen.py`（Alt+5/6）、`src/core/app_utils.py`
（Alt+4 清屏）、`src/minigames/jiguan.py`（Alt+C 降落）。

### 单键 Esc / Enter

```python
def _press_esc(win):
    if app_utils.silent_mode_on():
        from ..device import silent_click  # 延迟导入
        return silent_click.send_silent_hotkey(win, (), "esc") is not None
    if app_utils.keyboard_ok(win):
        Keyboard.key("esc")
        return True
    return False
```

参考：`src/tasks/mingwang.py`、`src/minigames/shahai.py`。回车同理
（`send_silent_hotkey(win, (), "enter")`），或文本输入时直接
`send_silent_text(..., end_enter=True)`。

### 文本输入

```python
text = "百次五倍来人，效率队"
if app_utils.silent_mode_on():
    from ..device import silent_click  # 延迟导入
    info = silent_click.send_silent_text(win, text, end_enter=True)
    if info is None:
        log("静默喊话投递失败（窗口不存在）", "WARN")
        return  # 不记成功时间戳，下轮重试
    log(f"已静默喊话: {text}", "SYS")
else:
    win.activate()
    time.sleep(0.5)
    Keyboard.input(text)
    time.sleep(0.8)  # 等剪贴板粘贴落地（物理路径时序）
    Keyboard.key("enter")
    time.sleep(0.5)
```

要点：

- 静默文本走 `WM_CHAR` 逐字符（wParam 为 UTF-16 码元），中文/数字/标点
  均实测有效（2026-11-03）；**不要**用 `Keyboard.input` 模拟静默——
  它走剪贴板 + Ctrl+V，只会进前台窗口。
- `end_enter=True` 内置「输入落地 0.3s 后回车」；若游戏聊天常驻、无需
  打开输入框，直接调用即可（`workflows/team_flow.py` 喊话同款）。
- `send_silent_paste`（`WM_PASTE`）与 `send_silent_unichar`（`WM_UNICHAR`）
  只对标准 EDIT 子控件可靠，游戏自绘输入不保证——优先 `WM_CHAR`。

### Tab 切换（组号）

- 静默 + 游戏不在前台：`silent_click.send_silent_tab_click(win, index)`——
  UIA 读 Tab 项矩形（客户区坐标缓存）+ 向 `SysTabControl32` 投
  `WM_LBUTTONDOWN/UP` 最小序列，2026-11-04 实机验证。
- 静默 + 游戏已在前台：物理 `Ctrl+Tab` 仍可用（`keyboard_ok(win)` 为
  True），`tab_group._switch_next_raw` 就是「`keyboard_ok` 不通 → 静默
  Tab 条点击」的写法。
- **不要**用 `send_silent_hotkey(("ctrl",), "tab")` 做静默切 Tab——实测
  客户端不消费；`TCM_SETCURSEL` 也只动控件选中、不切视图。
- **禁区**：Tab 条带指针的 TCM 消息（`TCM_GETITEMRECT` 等）跨进程不
  封送，会让客户端崩溃退出；只能用 int-only 的 `TCM_GETITEMCOUNT` /
  `TCM_GETCURSEL`（`silent_click` 已封装，勿绕开）。
- Tab 项矩形缓存是**客户区坐标**，窗口拖动不失效；控件尺寸变化/Tab 数
  变化才重读，越界时 `invalidate_tab_items` 自愈。

### 前台激活与遮挡检查

- 物理分支：可以 `win.activate()` + `GetForegroundWindow` 校验（清屏、
  组号 `_ensure_foreground`、喊话）。
- 静默分支：**禁止** `win.activate()`；需要判键盘能否用时只读
  `app_utils.keyboard_ok(win)`，不通就跳过/等下一轮，不得强行激活。
- 遮挡检查 `_game_window_clickable` 在静默模式下已自动放行（消息点击
  不落屏幕坐标，遮挡无关）；不要给静默路径加前台/遮挡前置。

### 整段流程临时静默（诊断/验证）

```python
with app_utils.silent_clicks():   # 期间全部点击走消息通道，退出恢复
    ok, info = xiuluo.verify_claim_flow(win, silent=True)
```

- 用于控制窗口「诊断-静默修罗领取/巫医/回程」等验证按钮；与物理验证
  共用同一业务代码，只切换点击通道。
- 开关是进程级全局，主进程一次只跑一个验证（`_verifying` 互斥）。
- 全局持久开关：`settings.save_silent_mode(on)` → 写角色设置并立刻
  `app_utils.set_silent_mode(on)`；任务子进程只在启动时
  （`main.task_start`）按配置置位，**运行中改面板开关不影响已在跑的
  任务**。

### 反向转换：静默 → 非静默

| 原状态 | 反向动作 |
|---|---|
| 点击只用 `send_silent_click` | 改回 `human_click` / `precise_click` 等封装即可——物理/静默由开关选，无需再写分支 |
| 只有静默分支的快捷键/文本 | 补 `else` 物理分支：`win.activate()` → 等 0.3~0.5s → 前台校验 → `Keyboard.*`（按「快捷键 / 单键」「文本输入」配方） |
| 代码里硬编码 `send_silent_*` 且无开关判断 | 加 `if app_utils.silent_mode_on():` 双分支；不要保留无条件静默 |
| 想临时全局切物理（不删代码） | 面板关「静默操作」或 `app_utils.set_silent_mode(False)`（进程内）；测试里注意复原 |
| 仅为验证而全流程静默 | 去掉 `silent_clicks()` 上下文，或保留给诊断按钮 |

反向转换后仍要跑快捷键配方里的四条要点（WARN 日志、`keyboard_ok` 兜底、
不激活静默分支、try/except）。

### 新增热键键位

`silent_click._KEY_CODES` 是「键名 → (虚拟键码, 扫描码)」表，只收已登记
的键（`alt/ctrl/tab/enter/esc/e/c/d/u/v/4/5/6`）。新增组合键时：

1. 加 `(VK, scan)`（扫描码按美式键盘标准布局）；
2. 跑 `tests/test_silent_click.py` 的序列断言（Alt 组合 `WM_SYSKEY*`、
   Ctrl/裸键 `WM_KEY*`、抬起带转换位）；
3. 任务流程调用点 `try/except ValueError`，缺键只 WARN 不崩；
4. 实机验证按键真的生效（看画面信号，不只看投递返回值）。

## 硬性约束（踩过坑）

1. **静默分支禁止 `win.activate()`**——抢前台违背静默契约；键盘事件
   只进前台窗口，非前台时跳过而非激活。
2. **不要手写 `SendMessageW` 序列**：Alt 组合必须 `WM_SYSKEY*`（带 Alt
   上下文位）、Ctrl/裸键 `WM_KEY*`、文本 `WM_CHAR`、按键 lParam 的
   扫描码/前态/转换位都由 `silent_click` 封装，手拼必错。
3. **消息点击后自动移出光标**（防悬停高亮污染后续绿字判定）——不要删
   末尾的 `WM_MOUSEMOVE`，也不要再调 `move_away`。
4. **静默投递失败要留痕**：`send_silent_*` 返回 `None` 时记 WARN 并让
   调用方重试/跳过，不能当成已执行。
5. **静默模式不代表键盘可用**：`keyboard_ok(win)` 只表示游戏已在前台；
   静默 + 后台时任何物理键盘都到不了游戏。
6. **Ctrl+Tab 静默走 Tab 条点击**，不用键盘消息；Tab 条带指针消息是
   崩溃禁区（见「Tab 切换」）。
7. **滚轮/拖拽没有静默实现**：当前 `silent_click` 只有点击/按键/文本/
   长按/Tab；遇到滚轮或拖拽需求，改用点击/键盘替代或先补消息通道，
   不要假装能转换。
8. **坐标空间统一 `win.rect` 相对**；`main_window=True` 只用于客户端
   外壳功能（如某些主窗口处理的按键），业务点击一律默认投可见 Tab
   子窗口。
9. **静默点击目标窗口可能失效**：老式单窗口客户端曾实测不消费合成
   鼠标消息（2026-10-26）；当前多标签客户端已实机验证。新客户端/新
   场景先做一次「点击前后帧差异」或游戏状态判定，别只信投递返回值。
10. **不要用此 skill 绕过项目通道**：本项目严禁 windows-mcp / 桌面级
    键鼠（CLAUDE.md 用户规则），静默通道就是替代方案。

## 验证清单

实现或转换一条操作后，按顺序做：

1. 单测：`python -m pytest -q tests/test_silent_click.py tests/test_app_utils.py`
   （序列/坐标/门控回归），涉及模块再加对应测试文件。
2. 全量：`python -m pytest -q`（基线全绿；重构类改动逐字节不动时序常量）。
3. 审计：`python .claude/skills/silent-ops-convert/scripts/audit_channels.py --strict`
   → 目标位置不再出现在违规列表（或已 `# silent-ok` 注明理由）。
4. 实机静默路径：面板开「静默操作」，游戏窗口放后台（不前台），触发
   该操作；日志出现「已静默…」、游戏有正确反馈，且操作前后物理光标
   与前台窗口不变（用「验证真的没用鼠标」的只读片段）。
5. 实机物理路径：面板关「静默操作」，游戏前台，再触发一次；确认物理
   分支的前台校验与按键时序正常。
6. 成功/失败双路径：目标不在、窗口失效、投递失败时，日志告警且流程
   重试/跳过，不卡死。
7. 日志/README：新增或改变用户可见行为时同步 `README.md` 功能列表
   （用户约定）；日志文案区分「已静默投递…」与「已按…」。

## 参考实现索引

| 场景 | 文件 |
|---|---|
| 统一门控封装（点击/长按/Alt+4） | `src/core/app_utils.py` |
| 消息投递全部原语 | `src/device/silent_click.py` |
| 面板开关读写 | `src/core/settings.py`（`load/save_silent_mode`） |
| Alt+C 恢复位置 | `src/device/map.py` |
| Alt+E 开背包 | `src/device/bag.py` |
| Alt+5/6 清屏 | `src/device/clear_screen.py` |
| Alt+U 飞行驭器 | `src/device/zuoqi.py` |
| 喊话文本（静默 WM_CHAR） | `src/workflows/team_flow.py` |
| Esc 关面板 | `src/tasks/mingwang.py`、`src/minigames/shahai.py` |
| 组号 Ctrl+Tab / 静默 Tab 条 | `src/device/tab_group.py` |
| 全流程静默验证入口 | `src/app/control_window.py`（诊断按钮） |
| 静默单元测试 | `tests/test_silent_click.py`、`tests/test_app_utils.py` |
