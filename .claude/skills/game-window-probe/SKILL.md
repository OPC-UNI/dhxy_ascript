---
name: game-window-probe
description: 用项目自身的窗口句柄通道（src.device.game.find_main_window + win.capture(force_activate=False) + 静默点击）实机观察/操作正在运行的大话西游窗口，摸清新小游戏或 UI 流程后再实现自动化。当用户说「你可以操作窗口看下如何实现」「看下这个小游戏怎么玩」、需要实机取帧/OCR/模板/点击/长按/时序采样时使用。**本项目严禁 windows-mcp / 桌面级键鼠**，本 skill 就是替代通道。
---

# 游戏窗口探针（窗口句柄通道）

用来在**不移动物理鼠标、不抢前台**的前提下，直接操作正在运行的游戏窗口：
截图 → 看清楚界面 → 量坐标/颜色/模板 → 试着点 → 观察反馈 → 把结论
沉淀进生产代码。

## 铁律（先读）

- **windows-mcp 在本项目严禁使用**（用户 2026-11-13 明确规则）。桌面级
  截图/点击/键盘会干扰运行中的游戏与自动化任务，属高危操作。同理禁止
  pyautogui、PowerShell 桌面模拟输入等一切桌面级输入/截图。
- 只用项目自己的通道：`game.find_main_window()` 拿窗口句柄 →
  `win.capture(force_activate=False)` 截图 → `app_utils.human_click` /
  `silent_click.send_silent_click`（静默模式默认开启，走窗口消息）。
- 操作前先确认**没有任务进程在跑**（控制窗口显示运行中时先请用户停止），
  否则会和自动化互踩；观察期间也不要让用户以为任务还在跑。
- 窗口最小化时点击可能不生效（消息通道仍可投递，但游戏可能暂停渲染）——
  请用户恢复窗口，不要用桌面工具去还原。
- 探针脚本、帧文件、分析脚本一律放**系统临时目录**，不往仓库里写东西；
  结论再落到正式代码/文档/测试。

## 步骤

### 1. 连窗口、看状态

```bash
cd <仓库根>
python .claude/skills/game-window-probe/scripts/probe.py info
```

输出标题、hwnd、窗口矩形、前台/最小化状态。截图/点击坐标一律是**窗口
相对坐标**（win.rect 左上角为原点，与 `human_click` 同空间）。

### 2. 截帧看界面

```bash
python .claude/skills/game-window-probe/scripts/probe.py capture /tmp/step1.png
```

用 Read 工具直接打开 PNG 看画面；需要精确定位文字/坐标时用：

```bash
python .claude/skills/game-window-probe/scripts/probe.py ocr            # 全帧
python .claude/skills/game-window-probe/scripts/probe.py ocr 400 270 980 600
python .claude/skills/game-window-probe/scripts/probe.py match kezhan_yinlv.png
```

OCR 每行输出 `置信度 (左,上,右,下) (中心x,中心y) 文字`——中心坐标可直接
喂 `click`。全帧 OCR 约 1s，区域约 0.1-0.2s：**能区域就不全帧**。

### 3. 交互与反馈

```bash
python .claude/skills/game-window-probe/scripts/probe.py click 487 410
python .claude/skills/game-window-probe/scripts/probe.py hold 500 400 2.5
python .claude/skills/game-window-probe/scripts/probe.py capture /tmp/after.png
```

点击后一定重新截帧确认反馈（对话框推进/窗口出现/面板打开），不要假设
点击生效。

### 4. 时序采样（动态界面必修）

对会出现/闪过的元素（高亮、按钮、弹字），用固定间隔连拍再逐帧分析：

```bash
python .claude/skills/game-window-probe/scripts/probe.py burst 100 0.08 /tmp/burst
```

然后用临时 Python 脚本逐帧算指标——常用手段：

- **颜色/高亮**：HSV 掩码 + 小 ROI 像素计数（比全帧模板快得多）
- **模板**：`game_map.best_match_np(bgr, "xxx.png", region=...)`
- **差分**：帧间差分找变化区域/动效
- **OCR**：只在关键帧做区域 OCR

把「事件序列 + 时间戳」打印出来，确定节奏、阈值、稳定性。

### 5. 闭环验证（必须实机跑通再写生产代码）

把候选算法直接塞进探针脚本实机闭环：轮询 → 判定 → 动作 → 再判定，
跑一整局确认成功，再补跑一次「不响应」的失败场景确认失败判定。
探针脚本必须**有界**（有限时长/次数），不得写 `while True`。

实测参考节奏：小游戏点击响应窗口约 0.3-0.5s，轮询 0.06-0.1s 足够；
同一目标连点要防抖（参考 0.5s）。

### 6. 沉淀到生产代码

1. 阈值/区域/节奏写成模块常量，注释里写**实测值 + 余量来源**（哪一天、
   什么场景测的）。
2. 轮询遵循 `docs/performance.md` 与 CLAUDE.md 性能规则：单帧复用、
   区域优先、条带优先、全帧兜底限频、轮询截图 `force_activate=False`。
3. 判定要有**成功/失败两条路径**（退出确认、失败重试），不能只写成功。
4. 加单元测试（pytest），模板桩 + 常量桩，跑全量测试。
5. 同步 README.md 功能列表（用户约定）。

## 实测坑位（2026-11-13 音律编钟验证时踩到/确认的）

- **R 资源根**：`ascript.windows.system.R` 按 `sys.argv[0]` 所在目录解析
  res/——探针脚本在 `.claude/skills/...` 下运行时会找不到模板和 OCR 模型，
  必须显式 `R._BASE_DIR = 仓库根`（probe.py 已处理）。
- **控制台 GBK**：直接 print 中文/⚠️ 会 UnicodeEncodeError——probe.py 已
  `sys.stdout.reconfigure(encoding="utf-8")`；自己写临时脚本时同样处理，
  或把结果写 UTF-8 文件再 Read。
- **截图含标题栏/Tab 条**：`win.capture` 返回整个窗口（1378×831 含标题栏），
  帧上量的坐标与 `human_click` 坐标同空间，直接可用。
- **静默点击已验证**：多标签客户端（xy2_tab_x64）投递到当前可见 Tab 子窗口
  即生效，窗口不必在前台；坐标按 win.rect 相对空间传入，内部自动换算。
- **截图失败返回 None**：循环里跳过并当作「状态未知」，不要据此判结束/
  判失败。
- **不要依赖窗口横幅分数做每帧判定**：静态 UI 模板分数稳定（实测 1.00 vs
  关闭后 0.67），但每次全帧匹配 ~100ms；用条带（banner 中心 ± 模板尺寸 +
  余量）+ 区域匹配，退出确认用「连续 N 轮未命中」防抖。
- **世界画面有金色元素**：小游戏窗口关闭后，固定 ROI 的颜色检测会命中世界
  画面（实测金色像素 55-185）——**先判窗口横幅在不在，再做游戏内检测**。

## 案例：客栈音律编钟（2026-11-13）

- 界面：5 个编钟 + 「开始演奏」；开始后游戏按随机顺序逐个点亮编钟（金色
  光晕 + 上方白色音符字），**亮哪个敲哪个**，共 15 音；不响应约 15 个音符
  后弹「少侠请认真演奏好吗！」失败，全中则「多谢少侠指导…」成功。
- 探针闭环：0.08s 截帧 → 每钟模板中心 ±45×±40 ROI 数 HSV 金掩码像素 →
  argmax ≥ 阈值（实测亮钟 327-742、噪声 ≤19，取 80）→ 立即静默点击该钟
  （同钟 0.5s 防抖）→ 横幅消失停点。实机 15 音全中成功，任务追踪正常推进。
- 生产实现见 `src/tasks/kezhan.py`：`_yinlv_bell_rois` / `_yinlv_glow_index` /
  `_play_yinlv`（动态追亮点，取代旧的固定 15 音序列）。横幅消失即按完成
  处理（2026-11-13 用户规则：动态追亮点保证正确性，不再保留按敲击数判
  失败并重新点击的逻辑）。
