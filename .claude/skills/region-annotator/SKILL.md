---
name: region-annotator
description: 在用户提供的游戏截屏上标注当前项目的全部识别区域（OCR 区域、模板搜索条带、找色/血条矩形、全屏识别文字说明），按界面分组出 PNG 图例，并生成可点击联动的自包含 HTML（点框↔列表、悬停提示、分类筛选、搜索）。当用户说「标注识别区域」「看看脚本都识别哪些区域」「把项目识别范围画在截图上」「圈一下 OCR/找图区域」「做个可以点的标注」并提供游戏截图时使用。用户必须提供一张游戏截屏；本项目严禁 windows-mcp / 桌面级截图与键鼠。
---

# 识别区域标注

把项目里所有「识别屏幕的地方」画到用户提供的游戏截图上：固定区域画
彩色编号框，同一编号在图例里给出名称、坐标与用途；**全屏识别不画框**，
直接在图上加文字说明；动态区域（由锚点推导）只写进图例。

区域清单是数据文件：`references/regions.json`（基准 1378×831 窗口相对
坐标，与 `human_click` 同一空间）。渲染脚本：
`scripts/annotate_regions.py`。

输出两种产物：

- **PNG**：每组一张（截图 + 编号框 + 图例），适合截图/文档
- **交互 HTML**：`00_识别区域交互.html`（自包含，截图 base64 内嵌）——
  点框或右侧条目互相联动、悬停提示、分类筛选、名称/用途/坐标搜索、
  分组切换（主场景/其他界面/小游戏）、全屏说明开关

## 何时使用

- 用户提供一张游戏截图，要求说明/标注项目识别了哪些区域
- 需要审计「脚本都看哪里」、排查某块 UI 是否被识别覆盖
- 新增识别区域后，更新区域清单并出一张对照图

## 输入

1. **一张游戏截屏（必须由用户提供）**。附件路径通常在图片元数据里；
   拿不到路径就直接问用户，不要用 windows-mcp、pyautogui、PowerShell
   桌面截图——本项目禁止桌面级通道（2026-11-13 用户规则）。
2. 截图应尽量是 1378×831 的游戏窗口画面；尺寸不同脚本会按比例缩放并
   在图例里标注缩放系数（非等比缩放可能错位，需人工复核）。

## 步骤

### 1. 确认截图与分组

```bash
.venv/Scripts/python.exe .claude/skills/region-annotator/scripts/annotate_regions.py --list
```

默认三组：

| 组 | 内容 | 画框规则 |
|---|---|---|
| `main` | 常驻 HUD/状态、任务追踪、对话框、战斗、领取 | 实线框（本场景生效） |
| `windows` | 地图/寻路/层块页/特殊地图、队伍、系统退出、世界领取 | 虚线框（对应界面打开时才生效） |
| `minigames` | 小游戏专用区域 | 虚线框（小游戏窗口打开时才生效） |

### 2. 区域清单是否需要刷新

代码改动可能新增/删除识别区域。先快速核对（仓库根）：

```bash
grep -rnE '^[A-Z_]*REGION[A-Z_]*\s*=|^[A-Z_]*RECT[A-Z_]*\s*=|^[A-Z_]*ROI[A-Z_]*\s*=|^[A-Z_]*AREA[A-Z_]*\s*=' src/ --include=*.py
grep -rnE '^[A-Za-z_][A-Za-z0-9_]*\s*=\s*\(\s*[0-9]{1,4}\s*,\s*[0-9]{1,4}\s*,\s*[0-9]{1,4}\s*,\s*[0-9]{1,4}\s*\)' src/device src/tasks src/workflows --include=*.py
grep -rnE '^[A-Z_]*_(BAND|AREA|ROI|BOX|RECT|ZONE|RANGE)[A-Z_]*\s*=' src/ --include=*.py
```

与 `references/regions.json` 对比。新增区域的落位规则：

- **固定像素区域**（常量 4 元组 / 血条矩形）→ 加进对应组 `regions`，
  `rect` 用窗口相对坐标，补 `name`、`purpose`、可选 `chip` 编号位置
- **锚点推导的动态区域**（如任务内容区、队伍成员区、背包储物格）→ 写进
  该组 `legend_notes`，不画框
- **全屏模板/全屏 OCR 兜底** → 写进 `fullscreen_notes`（图上文字块 +
  图例），不画框
- **地图坐标 / 点击点 / 时序参数** → 不是屏幕识别区域，不收录

`references/regions.json` 结构：`categories`（颜色/类别）→ `groups`
（标题、文件、dim、solid、regions、on_image_notes、fullscreen_notes、
legend_notes）。改完保持 JSON 合法。

### 3. 渲染

```bash
.venv/Scripts/python.exe .claude/skills/region-annotator/scripts/annotate_regions.py \
    --image "<用户截图路径>" --out logs/region_maps
```

- 默认 `--format both` 同时出 PNG 与交互 HTML；只要一种时传
  `--format png` 或 `--format html`
- 输出默认写 `logs/region_maps/`（`logs/` 已 gitignore，不污染仓库）；
  用户明确要求入库时再换到 `docs/`
- 可用 `--groups main,windows` 只出部分组
- 脚本打印 JSON 摘要（输出路径、每组区域数、警告），照此汇报

### 4. 验证（必须）

用 Read 打开每张 PNG 检查框是否贴住对应 UI、编号是否被遮挡、图例是否
完整、中文是否乱码；截图尺寸非基准时是否带缩放提示；全屏识别是否只有
文字说明、没有大框。

HTML 可让用户直接双击打开（或 IDE 预览）检查交互。需要自己视觉校验时，
用 Edge headless 渲染一张（不涉及桌面键鼠，不影响游戏）：

```bash
"/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" \
  --headless=new --disable-gpu --hide-scrollbars --window-size=1600,1000 \
  --virtual-time-budget=6000 --screenshot="$TEMP/region_html.png" \
  "file:///$(cygpath -m "<HTML 绝对路径>")"
```

重点看：框位置、编号、侧栏列表与图例、全屏说明文字块、分组切换后
区域数是否变化。发现错位：优先核对 `regions.json` 的坐标/分组，或代码
里的常量是否已漂移；修清单后重跑。不要用桌面工具截图核对。

### 5. 汇报

给出每张图的路径（相对仓库根的 markdown 链接）、覆盖的类别与区域数、
全屏识别与动态区域清单。若清单有更新，说明改了哪些条目。

## 规则（用户约定）

- **全屏识别不标注框**，直接在图片上加描述文案
- 同一时刻只会生效一组界面：地图/小游戏组必须虚线 + 免责说明
- 坐标一律窗口相对（窗口左上角为原点）；不要混入屏幕绝对坐标
- 禁止 windows-mcp / 桌面级截图键鼠；只用用户提供的截图与本脚本
- 图片默认放 `logs/`，不入库；用户要求入库时才放 `docs/`

## 维护

新增识别区域常量时同步 `references/regions.json` 并在 SKILL.md 覆盖的
类别里归位；`updated` 字段改成当天日期。区域清单是「项目当前识别面」
的快照，代码改动后未同步会让标注图误导。
