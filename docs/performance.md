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
