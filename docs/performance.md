# 性能优化规则与实测基准（2026-10 实测）

CLAUDE.md「Performance Rules」的详细参考：实测数据、规则由来、正确/错误写法对照。

## 实测基准（Windows 10，Python 3.13，游戏窗口 1378x831，.venv 运行）

| 环节 | 实测耗时 | 备注 |
|---|---|---|
| win.capture（PIL，不激活） | 33ms（战斗负载下可达 129ms） | 逐轮询的最大成本之一 |
| win.capture（OPENCV 格式） | 47ms | 多一次 np.array+RGB2BGR；模板热路径值得 |
| PIL→BGR 转换（AsCv.to_numpy） | 7.2ms/次/模板 | 同帧 4 模板 = 29ms 纯浪费 |
| 模板磁盘加载+解码 | 0.3ms/个 | R.img 只拼路径不读盘 |
| matchTemplate TM_CCOEFF_NORMED 全帧 | 25-28ms/模板 | 与 cv2.setNumThreads 无关（1~16 线程同速） |
| 同上，限定 ROI 条带 | 1.5-5.7ms | 最大的杠杆（7 倍） |
| is_in_battle_frame 4 模板（旧实现） | ~130ms | 匹配占 80% |
| is_in_battle_frame 条带+限频兜底（现实现） | 17-22ms（命中）/ 20-30ms（空闲） | 兜底 137ms，限频 3s 一次 |
| OCR 全帧（ocr_image） | 904-930ms | 21 个检测框 |
| OCR 区域（region 引擎，cls OFF） | 92-171ms | det_limit_type='max'/736 |
| 区域 OCR 关闭 use_angle_cls | 再省 50%（277→138ms） | 识别框数不变 |
| 巫医检查（区域无变化，差分跳过） | 7ms | 全量 OCR 148-311ms |
| OCR 双区域 CPU 成本（onnxruntime 默认全核） | 2.4 CPU 秒/次（wall 318ms） | 16 线程并行推理，wall 不降反烧 CPU（线程争抢 15.7s 极端帧） |
| OCR 双区域（intra=2/inter=1，2026-10-22 起） | 0.9 CPU 秒/次（wall 443ms） | CPU -63%，wall 仍在 0.5s 轮询预算内；任务进程常驻 CPU 从 ~6 核降至 ~2 核 |
| 任务进程 CPU：战斗期（2026-10-27 优化前/后） | 118% → 37% | 战斗帧判定前置到 OCR 之前（follow 内层循环） |
| 任务进程 CPU：寻路行走期（2026-10-27 优化前/后） | 186% → 133-165% | 面板差分门省面板 OCR；余量为对话框 OCR + 开打兜底全帧 OCR |
| 面板差分门命中率（行走中，门区域 (0,200,200,400)） | 11/11 帧严格相等 | 面板为不透明固定 UI，核心区像素在移动场景中纹丝不动 |
| 面板区域 OCR 单次（差分门省下的成本） | 224ms wall / 594 CPU ms | 门持续命中时降至 ~0（5s 兜底一次） |

## 规则详解与写法对照

### 1. 每轮询只截一帧（禁止二次截窗 + 抢焦点）

错误（旧写法，每轮询 2 次截图，find_template 还带激活抢焦点）：

```python
while time.time() < deadline:
    if not is_in_battle(win):          # 截 1 帧
        return False
    if game_map.find_template(win, icon) is not None:  # 又截 1 帧（且激活窗口）
        return True
    time.sleep(0.5)
```

正确（battle.py `_wait_icon` / xiuluo、wuhuan `_wait_template` 现行写法）：

```python
while time.time() < deadline:
    img = win.capture(force_activate=False, format=WindowCaptureFormat.OPENCV)
    if img is None:
        time.sleep(0.5)
        continue
    if is_in_battle_frame(img):        # 同帧判定战斗状态
        ...
    if game_map.match_frame_np(img, icon) is not None:  # 同帧匹配
        return True
    time.sleep(0.5)
```

### 2. 帧格式分工（脚枪：BGR 帧进 OCR 会反转绿字判定）

- 模板匹配热路径：`win.capture(force_activate=False, format=WindowCaptureFormat.OPENCV)`（BGR numpy）
- OCR / 绿字判定（is_green_text / green_text_center 读 RGB 通道）：必须 PIL 帧（默认格式）
- 对外复用的帧判定函数（如 is_in_battle_frame）须兼容 PIL 输入：入口处 `cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)` 转一次

### 3. 模板匹配快速接口 + 搜索条带（map.py）

接口（旧接口全部保留，不得删除）：`match_frame_np(img_bgr, icon, threshold, region, fallback)`、
`best_match_np(img_bgr, icon, region)`、`match_frame_multi(img_bgr, icons, threshold, regions)`。
模板灰度自动缓存（mtime 失效）。

新检测点的条带必须从真实截图标定（留 ≥50px 余量），如：

```python
BANNER_BAND = (0, 40, 400, 240)   # 回合横幅（实测 x34-121 y88-116）
BAR_BAND_HEIGHT = 190             # 操作条按钮（实测 y≈677）
```

带区全未命中才全帧兜底，**兜底必须限频**：非目标场景（如非战斗画面）带区每轮询都全未命中，
不限频的全帧兜底会让空闲轮询比旧实现还慢（实测 137ms/轮询 vs 条带 20ms）。
参考 battle.py 的 `FULL_CHECK_INTERVAL=3.0` + `_last_full_check` 时间戳限频。

TM_CCOEFF_NORMED 分数在裁剪区域内与全图严格一致，限定条带**不需要重标定阈值**。

### 4. 相似模板双模板比分数（禁止单模板阈值判定）

教训：解散队伍模板对离开队伍按钮误匹配 0.807-0.834 ≥ 0.8 阈值 → 队员被误判队长；
第1回合横幅模板对第2回合误匹配 0.893 → 回合误判。
两者都是「目标模板分数达标 **且** 高于对方模板分数」才成立（winner-take-all）。

新模板验收流程（两步都要做）：
1. 目标帧：分数 ≥ 0.9 且全图唯一命中
2. 非目标帧（对方模板的截图）：分数 < 阈值或低于对方模板

### 5. OCR 三原则

- **能区域就不全帧**：全帧 930ms vs 区域 92-171ms。区域 OCR 返回的 rect 与 img_np 是全帧坐标，
  下游绿字判定零改动。面板区右缘要比 PANEL_MAX_X 放宽（如 400→430），否则标题框边缘被裁导致误读。
- **引擎构造**：`RapidOCR(det_model_path=None, det_limit_type="max", det_limit_side_len=736, use_angle_cls=False)`
  ——带 det_* 参数必须同时传 `det_model_path=None`（否则 KeyError）；cls 只判 0/180 度翻转，游戏无倒置文本。
- **轮询类 OCR 前置像素差分**：区域 `np.abs(roi - prev).mean() < 2.0` 则跳过 OCR（实测 148-311ms → 7ms）；
  已知检测目标存在但处理失败的重试状态要绕过差分门（见 battle.py `_handle_wuyi_dialog` 的 warn 标记）。

### 6. 防抖资产（改动前必须对照）

| 项 | 值 | 能否改 |
|---|---|---|
| 进场确认 | 2 次轮询 × 0.5s | 可快不可慢；保持双样本语义 |
| 退出确认 | 0.5s × 4 轮询 = 2s 窗口 | **不许缩短**（回合间隙误判结束会打乱场次调度） |
| 模板阈值 | 按钮 0.85 / 横幅 0.8 | 不许动（实测校准） |
| 双模板比分数 | 达标且更高者胜 | 不许改成单模板 |
| 模板匹配算法 | TM_CCOEFF_NORMED | 不许换（积分图分解实测 72ms 更慢；SQDIFF 阈值语义不同） |
| cv2.setNumThreads | 不调 | 1~16 线程同速，无收益 |
| ocr_regions 掩码缓存 | 不做 | 实测仅省 1ms，内存 3.4MB/组 |
| 对话框文案扫描（140+ 条映射每轮询全扫） | 不做 | 实测 0.7-1.5ms/轮询（多重集预过滤有效），无优化价值（2026-10-26 kezhan 审计） |
| human_click 拟人化时长 | 不动 | 防检测设计，非性能项 |

### 7. 状态切换等待：轮询直接信号，固定时延只作超时上限

错误：`time.sleep(1.5)  # 等操作条恢复`
正确：`_wait_icon(win, AUTO_ICON, 1.5, want_found=True)  # 自动按钮出现即恢复，1.5s 超时上限`

### 8. 批量检测共用一帧

- 血蓝条：4 条一次截图（status_bars.check_all_bars）
- 第 1 回合：取消自动 + 回合判定 + 人物操作按钮定位共用一帧（battle.py handle_battle round1_frame）

### 9. 新增轮询的验收流程

1. 用 .venv python 写一次性计时脚本，实测分项（截图/转换/匹配/OCR）耗时
2. 按实测定条带与限频参数（不凭直觉）
3. 截图回归：目标帧、非目标帧、相似帧各测判定结果与耗时
4. 确认不触碰「防抖资产」表

### 10. follow 轮询的战斗帧跳过 + 面板差分门（2026-10-27 实测）

背景：follow_task 内层等待循环原先先跑双区域 OCR 再判战斗（xuanshu.py 旧
1277 行在 OCR 之后）——战斗动画帧帧不同、帧复用失效，战斗期每轮空烧双区域
OCR 1.4-2.2 CPU 秒。整改后结构（内层循环每轮顺序固定为）：

```
截帧 → is_in_battle_frame 前置判定 → 战斗帧: [确认轮询 2s 退避] + extras 对话框 OCR 限频
                                   → 非战斗帧: 帧全等复用 → 面板差分门 + 对话框逐轮 OCR
```

- **战斗帧判定前置**：命中即跳过面板/对话框全量 OCR；战斗中的任务对话
  （如五环提交装备）由 extras 保留通道处理——对话框区域 OCR 限频
  `BATTLE_EXTRAS_INTERVAL=2.0s`（战斗动画帧差分门失效，限频替代门控）。
  战斗期任务进程 118% → 37%（-70%）。
- **面板差分门**：共享组件 `task_tracker.TrackGate`（2026-10-27 由 follow
  内嵌抽取）。三态门顺序「节奏上限 → 像素相等 → 全量 OCR」：①
  `TRACK_GATE_MIN_INTERVAL=2.0s` 距上次 OCR 不足 2s 一律复用（2026-10-28
  实测：面板渲染状态可变，半透明/浮字渲染下门区域背景随世界移动，逐行
  实测 83% 像素每轮变化、面板内容 bbox 仅 y204-272，严格相等判定被击穿——
  节奏上限封顶面板 OCR 频率，任务变化检测时延 ≤2s、精度由 0.2s 确认轮询
  保证）；② 门区域像素与上次 OCR 帧相等且未超 `TRACK_GATE_MAX_SKIP=5.0s`
  时复用（OCR 确定性已实测验证：同输入两次 OCR items 逐字相等；拆分合并
  与 ocr_regions 双区域输出 items/img_np 全等）；③ 其余全量 OCR（含 5s
  兜底强制刷新，面板下部/门区域外的变化不漏检，限频模式同
  battle.FULL_CHECK_INTERVAL）。**分工（2026-10-28 教训）**：follow 轮询用
  默认限频门；玄枢/五环/五倍/修罗领取校验循环必须用
  `TrackGate(min_interval=0.0)`（不限频）——限频会把「未出现」结论复用 2s、
  任务 ~1s 渲染后被 2x 重识别兜底捡走，每次领取多 3-4s（实测当天 24 次
  兜底触发 vs 前一天 0 次）。只复用 items，img_np 一律 `TrackGate.paste_track`
  贴当前帧裁剪——绿字/进度键颜色判定不随复用而陈旧。战斗帧不得喂给
  ocr()（战斗 UI 污染门缓存，调用方先判战斗）。
- **寻路行走期剩余成本**（py-spy 实测，2026-10-27）：对话框区域 OCR 每轮
  ~32%（设计内，对话随时可能出现）、开打选项兜底全帧 OCR ~41%
  （_click_fight_option_fallback / wubei _handle_fight_dialog：故事行
  误触发 + 2x 放大 + 全帧引擎 ocr_image，5s 冷却）、面板 OCR ~12%
  （≈5s 兜底节奏，门持续命中）。行走期 186% → 133-165%。
- **领取校验循环**（2026-10-27）：五倍/修罗/五环/玄枢领取校验循环此前每轮
  双区域 OCR（窗口期实测占任务线程 OCR 27%），已全部套用 TrackGate——
  任务进追踪必然改变面板核心像素（门必开），阻断对话框与缓存补点在对话框
  区域逐轮新鲜 OCR，检测时延不变。
- **2x 放大兜底 OCR**（2026-10-27）：三处对话框区 2x 兜底
  （修罗/五倍开打选项、巫医选项）改走 map.ocr_zoom 快引擎（max/1600——
  检出分辨率与旧全帧路径 1293x736 同档，det 不放大白烧）。小块 2x 裁剪
  （修罗任务块 2x 重识别、队伍弹窗放大）依赖全帧引擎 'min'/736 的额外
  放大检出小字，保持不动。
- **巫医检查节奏窗口化**（2026-10-27）：战斗结束后 15s 高发窗口内保持 2s
  节奏（5s 教训：弹窗等久人物寻路走远对话框即关闭、点击失效），窗口外
  10s 兜底（弹窗仅在战斗结束/巫医 NPC 对话出现，NPC 场景人物静止无走远
  关闭问题，修罗流程等待上限 8s、超时后流程自行点击选项兜底——巫医完
  才继续追踪，2026-10-28 实测弹窗动画期（约前 3-4s）点击会被吞、
  15s 超时后带对话框点任务绿字顺序错乱）。不得恢复全时 2s 或直接放宽到 5s。
- **后续优化候选（未实施）**：对话框区域 OCR 加「对话框面板存在性」预检
  （模板/底色判定，无对话时跳过——需真实对话框样本标定阈值）；巫医差分
  双阈值（行走范围 diff 2~25 跳过 OCR，需弹窗样本标定）。

改动前必读：门区域/兜底时限/战斗限频/巫医窗口是实测校准资产，不得随意
放宽或收紧；战斗帧判定与面板差分门的顺序不得调换（先战斗后差分，战斗帧
会污染门缓存）。
