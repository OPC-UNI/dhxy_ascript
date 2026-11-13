# claim / walk 去重清单（2026-11-13）

范围：`wubei` / `xiuluo` / `zhongzu` 的领取与走位链路。`team` / `return`
已在 P2-C 下沉 `workflows/team_flow.py`、`workflows/return_flow.py`。

数据来源：AST 逐函数源码对比；相似度为 `difflib` 源码相似度，仅作参考，
不代替行为核对。

## 0. 结论

1. **不要整体合并** `_claim_task`、`_walk_to_claim_spot`、`_wait_claim_entry`：
   相似度只有 0.12~0.55，差异是领取 NPC、地图、任务类型和队伍确认策略的
   真实业务差异，不是复制漂移。
2. 先做低风险批：`_task_state`、`_claim_verify_wait`、`_probe_task_block`、
   `_find_claim_option`、`_click_claim_option`、`_in_claim_map`、
   `_near_claim_spot`、`_follow_to_done`。
3. 中风险批：`_open_claim_map`、`_relocate_to_claim_spot`、以及
   `_click_entry_ensuring_dialog` 的重试骨架。
4. 保留在任务侧：`_wait_claim_entry`、`_walk_to_claim_spot`、`_claim_task`、
   `verify_claim_flow`。

## 1. 逐函数清单

| 函数 | 模块 | 行数 | 相似度 | 差异要点 | 结论 |
|---|---|---|---:|---|---|
| `_claim_task` | wubei/xiuluo/zhongzu | 173/281/102 | .30~.38 | 领取前签名、入口点击、选项点击、队伍确认/警告、完成次数解析、2x 重识别、缓存直点、失败后补人/重走 | **保留**；只共享受控子步骤 |
| `_claim_verify_wait` | wubei/xiuluo | 23/22 | .82 | 仅日志前缀与等待间隔；探针命中即返回；间隔已统一 0.8s（2026-11-13） | **可下沉**（参数化 prefix/interval） |
| `_probe_task_block` | wubei/xiuluo | 20/18 | .88 | 任务块搜索区域来源不同（wubei 自带 fallback，xiuluo 用 `_task_block_region`） | **可下沉**（注入 region_fn） |
| `_click_claim_option` | wubei/xiuluo/zhongzu | 16/16/11 | .85 | 选项文案、缓存键（`option_<文案>`/`claim_option`/不缓存）、日志前缀 | **可下沉**（参数化 needles/cache_key） |
| `_find_claim_option` | wubei/xiuluo/zhongzu | 22/22/20 | .92/.56 | 三层匹配 needle 不同；wubei/xiuluo 用 `re.sub`，zhongzu 用 `norm_text`（二者等价） | **可下沉**（注入 needles） |
| `_in_claim_map` | wubei/xiuluo/zhongzu | 10/10/6 | .71/.63 | 目标地图名不同（宝象国/灵兽村/长安） | **可下沉**（注入 map_name） |
| `_near_claim_spot` | wubei/xiuluo/zhongzu | 4/4/6 | .95/.85 | 目标地图名/领取点不同；zhongzu 增加 `CLAIM_SPOT is None` 保护 | **可下沉**（注入 map_name/spot） |
| `_open_claim_map` | wubei/xiuluo/zhongzu | 35/35/28 | .92/.81 | 目标地图、世界地图图标、小地图点击点；zhongzu 走 `_click_claim_map_pos` 间接点击 | **可下沉**（注入 map config + click callback） |
| `_relocate_to_claim_spot` | wubei/xiuluo/zhongzu | 44/39/34 | .88/.84/.74 | 循环骨架相同；到达判据不同（按钮/侍卫/使者/NPC），zhongzu 多 `_claim_configured` 预检 | **可下沉**（注入 arrival_fn + precheck） |
| `_follow_to_done` | wubei/xiuluo/zhongzu | 26/39/13 | .53/.52/.42 | `follow_task` 参数与额外对话处理器不同；xiuluo 多一次领取点补偿重试 | **可下沉**（config + 可选 recovery hook） |
| `_click_entry_ensuring_dialog` | wubei/xiuluo/zhongzu | 109/98/74 | .80/.51/.53 | 返回 5 元组 vs 3 元组；点击 `human_click` vs `precise_click`；缓存键/固定等待不同；xiuluo 处理队伍警告；zhongzu 先 Alt+4 清屏、无 hover/缓存 | **中风险，暂缓**；先抽出重试骨架 |
| `_wait_claim_entry` | wubei/xiuluo/zhongzu | 58/25/41 | .12/.30/.39 | wubei 三规则坐标门控（走位/站位/降魔侍卫）；xiuluo 双模板无坐标门控；zhongzu NPC 候选 + 坐标门控 | **保留**（三种门控模型） |
| `_walk_to_claim_spot` | wubei/xiuluo/zhongzu | 136/129/80 | .55/.31/.29 | wubei 有 `_arrive_by_walk`/降魔侍卫/返程加载补偿；xiuluo 有使者框/返程加载补偿；zhongzu NPC+Alt+4 清屏+坐标优先 | **保留**（三种到达模型） |
| `_task_state` | xiuluo/zhongzu | 22/18 | .85 | 主体逐行相同，仅 docstring 差异 | **可直接下沉**（注入 title/fallback） |
| `verify_claim_flow` | wubei/xiuluo/zhongzu | 50/57/38 | .83/.33/.31 | 诊断流程；xiuluo 有 silent 模式、队伍警告；zhongzu 用 NPC 三件套 | **保留**（诊断入口） |

## 2. 实施状态

### 批次 1：低风险（✅ 已实施 2026-11-13）

新增 `src/workflows/claim_flow.py`：

- `task_state(win, title, fallbacks)`
- `find_claim_option(items, img_np, needles)` / `click_claim_option(...)`
- `in_claim_map(pos, map_name)` / `near_claim_spot(pos, map_name, spot, tol)`
- `claim_verify_wait(win, pre_sig, t_opt, polls, prefix, interval, probe_fn)`
- `probe_task_block(win, title, fallbacks, region_fn)`
- `follow_to_done(win, first_items, config...)`（follow_task 参数收敛为配置；
  修罗恢复钩子作为可选 `recovery`）

任务侧保留同名薄包装；选项 needle、缓存键、地图名、探针区域函数由任务
注入。新增 `tests/test_claim_flow.py`。此批不改变轮询顺序、坐标门控；
校验等待间隔按用户 2026-11-13 指定统一为 0.8s（原五倍 1.0 / 修罗 0.8）。
当前全量 902 passed。

### 批次 2：中风险（批次 1 实机通过后）

- `open_claim_map(win, cfg, pos=None)`：注入地图名/世界图标/小地图点/点击回调。
- `relocate_to_claim_spot(win, cfg, arrival_fn)`：保留原循环；到达判据由任务注入。
- `click_entry_ensuring_dialog(...)`：先抽「等待入口 → 点击 → 等对话框 → 重定位 →
  再等」的重试骨架，任务用回调提供入口等待、点击函数、到达判据、队伍警告、
  清屏和返回值适配。此步建议单独提交并逐任务实机回归。

### 批次 3：不合并

`_wait_claim_entry`、`_walk_to_claim_spot`、`_claim_task`、`verify_claim_flow`
保留任务实现。理由：

- 入口门控是实测校准资产：wubei 的走位/站位/侍卫三规则、xiuluo 的双模板、
  zhongzu 的 NPC 候选+坐标门控，合并会把「何时允许点击」变得不可读。
- 到达模型不同：走位落点、侍卫/使者坐标框、返程加载补偿、Alt+4 清屏。
- `_claim_task` 的队伍确认/警告、完成次数解析、2x 重识别、失败补人路径
  任务间差异大；强行配置化会引入大量 hook，收益低于风险。

## 3. 逐函数差异备注

### `_claim_verify_wait` / `_probe_task_block`

wubei/xiuluo 逐行同构：探针命中则跳过固定等待；差异只有日志前缀、等待
常量、任务块区域来源。直接下沉不会改变领取确认时序。

### `_find_claim_option` / `_click_claim_option`

三层 needle（完整文案 → 尾部 → 短兜底）结构相同，文案不同；缓存键
wubei 为 `option_<完整文案>`、xiuluo 为 `claim_option`、zhongzu 不缓存。
参数化后三任务共用点击/悬停逻辑，但**不要**改 needle 顺序和兜底判定。

### `_open_claim_map` / `_relocate_to_claim_spot`

`_open_claim_map` 是纯地图配置差异，适合下沉。`_relocate` 的循环骨架
（战斗跳过 → 开图 → 等待 → 每 5 轮日志 → 2s 轮询）相同，差异集中在
「到达判据」；用 `arrival_fn(frame, pos)` 注入即可，但必须保留各任务的
按钮/侍卫/使者/NPC 判据与坐标门控。

### `_follow_to_done`

`follow_task` 的参数差异可以直接配置化：prefix、extra_dialog_handler、
fallback_keywords、first_items、skip_texts、story_known_texts、
pre_click_handler、post_track_handler、task_gone_polls、battle_extras_tpl_gate。
xiuluo 的「首次跟踪失败后走领取点补偿一次」作为可选 recovery hook；wubei/
zhongzu 不启用。不要改 `_hpg_gone_polls` 的实时求值语义。

### `_click_entry_ensuring_dialog`

共同骨架：两次尝试 → 等待入口 → 点击 → 等对话框 → 首次失败小地图重定位 →
再等到达 → 第二次尝试。差异：

- 返回：wubei/xiuluo `(ok, info, src, pt, seen_frame)`；zhongzu `(ok, info, seen_frame)`。
- 点击：wubei `human_click`；xiuluo/zhongzu `precise_click(move_duration=0)`。
- 高性能缓存：wubei `option_<文案>` + `HIGH_PERF_DIALOG_WAIT`；xiuluo
  `claim_option` + 随机固定等待；zhongzu 不启用。
- xiuluo 轮询中处理队伍警告；zhongzu 进入前先 `_clear_claim_screen`。
- 入口等待函数签名不同（wubei 3 元组 vs 其余 2 元组）。

这些差异可以做成配置，但收益要到批次 2 才明显；当前先保留，避免把
首次领取热路径改复杂。

### `_wait_claim_entry` / `_walk_to_claim_spot` / `_claim_task` / `verify_claim_flow`

见上表；建议保持现状。后续若要进一步统一，先补真实设备上的到达/点击
日志样本，再按任务逐个抽「到达判据」而非整体合并。

## 4. 回归要求

- 批次 1：全量单测 + 三任务各跑一轮领取（重点看入口点击、选项点击、
  任务进追踪确认）。
- 批次 2：每改一个函数，三任务各实机跑一轮；重点看跨图误命中、返程后
  加载慢、队伍人数不足、非队长保持。
- 除用户明确指定的「校验等待统一 0.8s」外，任何批次都不得修改：轮询顺序、
  坐标门控容差、`TrackGate(min_interval=0.0)`、其余时序常量、2x 重识别门控。
