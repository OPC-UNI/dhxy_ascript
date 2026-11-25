---
name: special-map-trace
description: 给异形特殊地图（莲花洞这类世界地图无标签、洞内无小地图的地图）做人工描边建图：后台按固定频率只读 HUD 坐标记录用户手动操作人物的实走轨迹（同时记录角色屏幕锚点）→ 生成该图路网 special_map_routes.json → 把描边数据画成地图 PNG。当用户说「给某某图描边/建路网/记录坐标」「自动探图不好用，用人走」「把描边数据画成地图」「其他异形图也照这个方法做」时使用。
---

# 特殊地图人工描边建图

用于**异形地图**：世界地图无标签、进入后没有小地图，坐标点击不可用。
思路（用户 2026-11-23 定稿）：**人手动操作人物沿可走区域描边，脚本只读
HUD 坐标**。语义：**描边以内都可走、以外不可走**。导航时目标在边界内
直接直线走（客户端自动绕障），目标在边界外落到最近可走边界点，绝不
点空洞；不再沿描边绕一圈（那样 106s，直线正常 10s 级）。

描边时同时按较低频率 OCR 角色名字标签，记录「世界坐标 → 角色屏幕位置」
锚点样品 → 路网 `self_anchor`。运行时给 HUD 坐标就近查表即可得到角色
屏幕位置，点击只点角色附近（≤320px），**不依赖运行期名字 OCR**（人多
读不到名字也不受影响）与探针；采集与运行条件一致（用户确认）。

> 本项目禁止 windows-mcp / 桌面级键鼠/截图。本 skill 只用项目自己的
> 窗口句柄通道截图 + OCR（`find_main_window` / `win.capture(force_activate=False)`），
> 记录器不点鼠标、不抢前台。

## 何时使用

- 用户要求给某个特殊地图「描边 / 建坐标系 / 建路网 / 记录坐标」。
- 用户反馈自动探图/直线步进在异形图上乱走、点进空洞。
- 用户要求把描边数据画成地图（PNG）看看轮廓。
- 新增了类似莲花洞的异形地图，需要一张可靠的导航路网。

## 前置确认

1. 游戏窗口已打开且已登录；人物在目标地图内（HUD 地名 = 目标图名）。
2. **没有任务进程在跑**（否则描边时脚本与任务互相抢窗口/移动人物）。
3. 用户本人可以操作人物；记录期间由用户走，脚本只读。
4. 知道目标地图的 HUD 地名（如「莲花洞」），不要用世界地图名。

## 流程

### 1. 启动描边记录（后台）

```bash
cd <仓库根>
# 多开时加 --role <角色ID>（按窗口标题「（ID：xxx）」精确选窗口，
# 避免 find_main_window 绑定缺失时读到别的号）
python scripts/trace_special_map.py --map <地图名> \
    --out map_trace/<地图名>.jsonl --interval 0.4 --duration 1800 \
    --anchor-interval 1.0 --role <角色ID>
```

用 `run_in_background` 启动。脚本行为：

- 每 0.4s 截一帧读 HUD 位置；只在坐标变化时追加 JSONL（每行即时 flush）。
- 只记录 HUD 地名等于 `--map` 的点；走出地图时暂停记录，回来后继续。
- 第 1 个点立即记录（起始位置），之后是用户实走轨迹。
- 角色屏幕锚点：开始时用**画面中部 2x 放大 OCR** 找一次角色名字标签
  标定相机 B（标签是小号彩色字，全帧引擎时好时坏、区域快引擎读不到；
  2x 裁剪实测 0.07~0.5s，容忍 1 字误识别），之后每帧用相位相关跟踪相机
  位移，每 `--anchor-interval`（默认 1s）追加锚点样品行
  `{"kind":"anchor",...,"ax":..,"ay":..,"src":"track","w":..,"h":..}`
  （= 角色屏幕位置）；每 `--reanchor-interval`（默认 8s）在预测位置附近
  小框重标定一次，限制相位相关 +0.5px/次 的系统漂移（小框连续 3 次失败
  则整片重扫，防止初始锚点认到别人头上）。`--anchor-interval 0` 关闭。
- 停止：Ctrl+C、到时（默认 900s）、或创建停止文件
  `map_trace/special_map_trace.stop`。
- 启动后先看 `map_trace/special_map_trace.jsonl` 是否有第一行，确认记录生效；
  日志会显示「角色屏幕锚点：每 1.0s 记录，每 8s 名字标签重标定」和
  首次「锚点标定 (x,y) conf=…」。

告诉用户：「记录已开始，请操作人物描边」；**尽量把全图可走区域跑一遍**
（不只是外轮廓）——锚点样品覆盖越全，运行期查表越准，边界内的直线
导航也才敢放心走。需要跨臂捷径时可以再描几笔内部中线（点越密越好用）。

### 2. 停止记录

用户说「好了」时：

```bash
touch map_trace/special_map_trace.stop
```

等 1~2s，确认脚本退出；检查 `map_trace/special_map_trace.summary.json`
（点数 / 包围盒 / 里程，`--out` 对应 `<name>.summary.json`）。

### 3. 生成路网

```bash
# 只录了外轮廓（描边同时就是边界）
python scripts/build_special_map_route.py --map <地图名> \
    --trace map_trace/<地图名>.jsonl --role <角色ID> --res

# 全图轨迹（外轮廓 + 内部路线）：边界必须另给外轮廓描边文件，
# 不能拿全图轨迹当边界（内部路线会被当成多边形边）
python scripts/build_special_map_route.py --map <地图名> \
    --trace map_trace/<地图名>_full.jsonl \
    --boundary-trace map_trace/<边界描边>.jsonl \
    --role <角色ID> --res

# 只跑了一遍全图（没单独走外轮廓）：从轨迹外轮廓提取边界（省一次录制；
# 单独走外轮廓仍更准）。会同时输出核对图 <out>_check.png
python .claude/skills/special-map-trace/scripts/derive_boundary.py \
    --map <地图名> --trace map_trace/<地图名>.jsonl \
    --out map_trace/<地图名>_boundary.jsonl

# 多分区地图（波月洞：同一坐标平面分 A/B 两区、区之间是洞口传送）：
# 加 --zones 自动按传送跳变拆分每区边界与传送链接
python scripts/build_special_map_route.py --map 波月洞 \
    --trace map_trace/波月洞.jsonl --boundary-trace map_trace/波月洞_boundary.jsonl \
    --role <角色ID> --res --zones
```

- **路网数据属于所有角色（用户 2026-11-25 规则）**：正式数据一律写内置表
  `res/special_map_routes.json`（命令带 `--res`，所有角色共用同一份）。角色
  目录的 `data/<角色>/special_map_routes.json` 只作**特殊覆盖**（如窗口尺寸
  不同需另行采集），常规不保留：加载顺序是角色文件优先、内置表兜底，留一份
  旧的角色副本会让该角色读不到后续 res 更新（2026-11-25 波月洞 B 区补描后
  已删除 36096466 的冗余副本，备份在 map_trace/）。
- `--zones`（多分区地图，2026-11-24 波月洞）：相邻轨迹点跳变 ≥
  `--zone-jump`（默认 10 格）切段（传送瞬间 HUD 坐标大幅跳变，步行采样
  不会）；段间最小距离 < `--zone-join`（默认 5 格）聚为同一分区；每区
  独立生成 `boundary`+`trace`，相邻且跨区的两段记为传送链接
  （触发点=离开段末点、落点=进入段首点，多次观测取中位数），写
  `zones`/`links`。运行期 `_walk_special_zones`：按位置判定当前区/目标区
  → BFS 传送链 → 先走到本区触发点（±2 格）→ 等位置进入目标区且连续
  2 次稳定 → 在目标区按该区边界步行到目标；区内其他触发点按 3 格半径
  避让。若录制时某区坐标没走全，先补录该区再 `--zones` 重建。
- 路网格式：`{"trace": [[x,y], ...], "trace_source": "manual ..."}`。
  **不需要中心/臂节点**；`--keep-old` 才保留旧 center/arms 兜底字段。
- 锚点样品清洗（同坐标中位数 + 近邻离群剔除，防名字 OCR 命中别人）后
  写 `"self_anchor": {"window": [w,h], "samples": [[x,y,ax,ay], ...]}`；
  日志会打印「锚点样品：N 个 → 清洗后 M 个，窗口 WxH」。
- 写完 `map_nav.special_map_route()` 会自动优先使用角色数据，其次内置表。

### 4. 画出描边地图

```bash
python .claude/skills/special-map-trace/scripts/plot_trace.py \
    --map <地图名> --trace map_trace/<地图名>.jsonl
# 或直接用路网数据：
#   --route-json res/special_map_routes.json
```

默认输出 `map_trace/special_map_<地图名>.png`（网格 + 轨迹折线 + 起终点标记）。
用 Read 工具打开 PNG 给用户看，并说明轮廓/包围盒/点数。

### 5. 验证导航（可选但推荐）

在游戏内让 `map_nav._walk_special_coordinate` 走一个已知坐标：

```bash
python - <<'EOF'
import sys
sys.path.insert(0, ".")
from src.core import app_utils
from src.device import game, map as game_map, map_nav
app_utils.set_silent_mode(True)
win = game.find_main_window()
ok, info = map_nav._walk_special_coordinate(
    win, "<地图名>", <x>, <y>, lambda m: print(m))
print("RESULT:", ok, info, "final:", game_map.read_map_position(win))
EOF
```

检查日志：目标在边界内应出现「目标在描边范围内，直线导航」，
目标在边界外应出现「目标在描边外，按最近可走点 … 导航」；最后位置
误差在 ±3 格内，全程不应点到描边以外的空洞。

## 数据与代码位置

| 内容 | 位置 |
|---|---|
| 描边记录器 | `scripts/trace_special_map.py` |
| 路网生成 | `scripts/build_special_map_route.py` |
| 轨迹外轮廓→边界 | `.claude/skills/special-map-trace/scripts/derive_boundary.py` |
| 描边画图 | `.claude/skills/special-map-trace/scripts/plot_trace.py` |
| 路网数据（角色） | `data/<角色ID>/special_map_routes.json` |
| 路网数据（内置） | `res/special_map_routes.json` |
| 原始轨迹 | `map_trace/<地图名>.jsonl`（默认 `map_trace/special_map_trace.jsonl`） |

导航侧：`src/device/map_nav.py`

- `special_map_route(name)`：角色数据 → 内置表。
- `_special_trace_waypoints`：描边折线建图 + Dijkstra，当前点/目标各取
  最近描边点，途经点全部是人走过的坐标。
- `_special_model_anchor`：`self_anchor` 锚点模型 → 当前 HUD 坐标的
  角色屏幕位置（K 近邻反距离加权；窗口尺寸须与采集一致 ±8px，覆盖
  半径 12 格）。运行时**优先**用它；无模型/覆盖不到才退 OCR 名字标签，
  再退 HUD 坐标+当前 B 反算。
- `_walk_special_coordinate`：**可见性导航**——用角色屏幕位置算出视窗内
  可见世界范围，每次点路线上**屏幕内最远**的途经点（中间点跳过，视线随
  滚动自动推进），不把点击钳到屏幕边缘；路线用边界多边形**栅格 A***
  （几何最短路，绕开两臂间空洞），洞内传送光圈不可穿过（避让半径 3 格，
  端点在内放行）；**无首步停顿**：连续右击（0.5s，行走可打断）；接近目标时停稳复核，
  误差超 ±3 按最终误差修正点击偏置补一步（终点闭环）；相机位移逐帧相位相关更新屏幕↔世界映射
  （单次超区域半幅限幅；不可测时用锚点模型重标定）；上次成功导航的帧+B
  做下次热启动免探针；±3 格到达。`zone=` 限定用该分区边界/描边，
  `avoid=` 避让区内其他洞口触发点，`zones=/zone_state=` 监视步行途中
  是否已传送到别的分区。
- `_walk_special_zones`：**分区导航**（波月洞 A/B）——位置判定当前区/
  目标区，BFS 传送链，逐段走到本区触发点并等场景切换（±2 格、90s；
  未触发朝洞口方向补点最多 3 次），传送后在目标区按该区边界步行。
  **落点锚点可信度按建图标记**：`--zones` 用轨迹跳变时间窗（落点后 4s）
  过滤转场期锚点样品，落点 8 格内不足 3 个稳态样品 → 链接标
  `land_recalibrate`（波月洞只有 B→A 的 A 落点 (11,69)）；运行期只在
  被标记的落点起步时先用名字标签 OCR、失败再强制探针重标定，未标记的
  B 落点仍用锚点模型（避免探针绕路）。洞内 HUD 地名比较容忍 1 个 OCR
  错字（「波月同」=「波月洞」，否则传送瞬间误判场景变化直接失败）。
  `_walk_special_zones` 无分区数据时等价于 `_walk_special_coordinate`。
- `_special_model_anchor`：运行期丢弃窗口外的锚点样品（采集期相机跟踪
  跑飞的黑屏样品，如 `ay>窗口高`），避免点击点被换算到屏幕外。
- 无描边数据时才退回自动探图（`explore_special_map` 中心/四臂兜底）。

## 注意

- **记录期间不要用脚本移动人物/点击**：轨迹必须是用户真实操作走出来的。
- 不要用 `windows-mcp`、`pyautogui`、PowerShell 桌面输入。
- 描边尽量沿可走区域走；外轮廓建的是安全但较长的路线，跨臂捷径需要
  再描内部中线（重新录制/生成即可）。
- **尽量跑全图**（不只外轮廓）：锚点样品覆盖决定运行期查表精度；某处
  没有样品（12 格内）时才退回名字 OCR/探针。窗口尺寸变化（改窗口大小）
  会让锚点模型失效，需要重新录制。
- **新洞府要在 `map_nav.SPECIAL_MAP_ENTRIES` 补 `portal`**（进出地图的
  传送光圈坐标；莲花洞实测 (84,9)、避让半径 `SPECIAL_PORTAL_AVOID_R`
  =3 格）：路线穿过光圈会被传送出图（2026-11-24 实机教训）。
- **同一坐标平面分多块区域、靠洞口传送连接的地图**（波月洞 A/B）必须
  用 `--zones` 建分区路网：单一边界会让 A* 在区域间空洞里绕、永远进不了
  另一区。传送触发点/落点从描边轨迹的坐标跳变自动识别，不要手工编；
  录制时每个区都要走到（每区至少一段轨迹），否则该区边界/传送链接缺失。
- 轨迹点很多时不用手动简化：导航时 `SPECIAL_ROUTE_WP_TOLERANCE` 会
  跳过近距离途经点；描边点越密越安全。
- 目标离描边太远时（>25 格，自动探图路网才有的限制），描边路网仍然
  用最近描边点 + 最后直接到目标；目标本身不可走时只会走到最近可走点。
- 生成路网失败先查：JSONL 是否有行、`--map` 是否与 HUD 地名完全一致、
  角色 ID 是否正确、是否给了错误的 `--trace` 路径。
