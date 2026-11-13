# 项目代码规范（目录 / 分层 / 依赖）

> 状态：P0–P2 已落地（2026-11-13）。本文件是模块归属与依赖方向的唯一约定，
> 与 CLAUDE.md 的「任务模块隔离」规则配套，由 `tests/test_import_rules.py`
> 强制执行。迁移路线见文末。

## 0. 一句话规则

**任务只编排，能力下沉；任务之间零 import；通用模块不依赖任务与 UI。
新增例外必须进白名单、写原因、标注清理阶段。**

---

## 1. 目标目录结构

```
src/
├─ core/        # 无设备、无 GUI 依赖：app_utils、user_data、role_settings、
│               # settings（已从 control_window 抽出）、schemes、debug_overlay
├─ device/      # 感知与执行：game、map、task_tracker、dialog_common、battle、
│               # bag、status_bars、silent_click、tab_group、zuoqi、clear_screen、
│               # team（队伍）、repair（修理）
├─ workflows/   # 跨任务业务流：follow（跟踪引擎）；claim_return/wuyi 待抽取
├─ minigames/   # 独立小游戏：pochufengyin、duchong、yinlv、shahai、jiguan、...
├─ tasks/       # 任务编排：xuanshu、wuhuan、kezhan、xiuluo、wubei、
│               # zhongzu、mingwang
└─ app/         # 外壳/UI/生命周期：control_window、task_manager、updater、
                # hotkey、select_window、env_check、capture_diag、license_app_id
```

`main.py` 留在仓库根目录，是唯一装配入口。

### 1.1 迁移状态（2026-11-13）

目录迁移已完成：`src/` 顶层只保留 `__init__.py` 与 `core/`、`device/`、
`tasks/`、`app/`、`workflows/`、`minigames/` 六个包。`tests/test_import_rules.py`
按目录自动归类，不再维护平铺模块名单。

目录迁移只做了 `git mv` + import 改写，函数/常量逐字节未变；但搬迁后
必须复核两类位置敏感问题：基于 `__file__` 的路径深度（如
`app/updater.py` 的 `parents[2]`）与模块级可变状态的归属（状态读写必须
落在同一模块，2026-11-13 审查发现拍毒虫退避状态跨模块分裂）。

---

## 2. 分层与依赖方向

```
core  ←  device  ←  workflows  ←  tasks
                         ↑
                    minigames
```

| 层 | 职责 | 允许依赖 |
|---|---|---|
| core | 配置、数据目录、日志、持久化、版本号 | 仅标准库/第三方（历史例外见 §6） |
| device | 窗口、截图、OCR、模板、点击、背包、战斗、组号 | core |
| workflows | 跨任务复用的业务流（跟踪、领取、返程、队伍、巫医、修理） | core、device |
| minigames | 独立小游戏闭环（模板/常量/状态自包含） | core、device、workflows |
| tasks | 任务入口与编排（领取 → 跟踪 → 返程） | core、device、workflows、minigames |
| app | GUI、托盘、更新、诊断按钮、进程管理 | 任意层 |

**硬规则：**

1. `tasks` 不得 import 另一个 `tasks` 模块（CLAUDE.md 任务隔离规则）。
2. `core` / `device` / `workflows` / `minigames` 不得 import `tasks`。
   任务专属逻辑留在任务里；通用诊断用回调注入
   （例：`dialog_common.verify_dialog_detect(extra_score=...)`）。
3. `core` / `device` / `workflows` / `minigames` / `tasks` 不得 import `app`。
   `app` 可以 import 任意层，`main.py` 是组合根。
   P2-A 已把 `control_window` 里的 `load_*/save_*` 抽到 `core/settings.py`，检查已启用。
4. 已知例外：`battle ↔ team/tab_group` 是现有设计（通过函数内延迟导入
   打断循环），暂不重构；新增代码不要复制这个模式。

---

## 3. 新代码放哪里

| 场景 | 位置 | 公开入口 |
|---|---|---|
| 新任务 | `src/tasks/<task>.py` | `run_loop(win)`；诊断用 `verify_*` |
| 新小游戏 | `src/minigames/<game>.py` | `play_<game>(win, ...) -> (ok, info)` |
| 两个及以上任务复用的流程 | `src/workflows/<flow>.py` | 参数化配置 + 主流程函数 |
| 设备感知/执行能力 | `src/device/` | 按能力命名 |
| 纯数据/配置/日志/持久化 | `src/core/` | 按能力命名 |
| 控制窗口、托盘、更新、诊断按钮 | `src/app/` | 组合与展示 |

**任务模块只做装配**：入口 → 领取 → 跟踪 → 返程；目标 ≤ 1200 行。
新文件建议 ≤ 800 行；超限通常说明模块边界没切对。

**小游戏自包含**：模板名、时序常量、内部状态、点击策略都归小游戏模块；
任务模块只负责「何时 arm / 何时触发」，不得把游戏内循环写回任务文件。

**共享能力下沉**：两处以上需要同一逻辑时，进 `workflows/` 或 `device/`，
禁止复制到任务之间。复制过的代码已经漂移的（如领取/寻路），合并前先
逐处 diff 并实机确认，不得按「看起来一样」直接统一。

---

## 4. 重构纪律（保证正确性）

1. **一个提交只做一件事**：移动/改名提交不含行为变化；行为变化单独提交。
2. **重构提交禁止修改**：轮询顺序、时序常量、OCR 区域、模板阈值、
   ROI 条带、重试/防抖参数。这些是实测校准资产，原样搬运。
3. **兼容期留 shim**：如 `xuanshu.follow_task = workflows.follow.follow_task`
   保留一个提交，调用方全部改完后删除；测试同提交更新。
4. **每个抽取的模块保留/补齐测试**；提交前 `python -m pytest -q` 全绿
   （基线：874 passed, 2 deselected）。
5. **涉及任务链路的抽取至少真机跑一轮**对应任务；纯移动/改名可只跑测试。
6. **打包**：`pyproject.toml` 的 `packages` 必须包含新子包；
   Nuitka `--include-package=src` 会自动跟随，但 pip/ascript 打包不会。

---

## 5. 命名与资源

- 模块名：小写 + 下划线；一个任务 / 小游戏 / 流程一个模块，不建大杂烩。
- 模板：`res/img/<模块前缀>_<用途>.png`（沿用现状）；不整目录搬 `res/img`
  （模板常量散落各处，迁移收益低于风险）。
- 日志：任务模块统一 `[任务名]` 前缀；共享模块使用调用方传入的 `prefix`。
- 时序常量：名称带含义，注释写实测日期与来源；重构时不得「顺手调」。

---

## 6. 执行与例外

`tests/test_import_rules.py` 当前检查：

- 任务 → 任务 import（`TASK_EDGE_ALLOWLIST` 当前为空，只减不增）。
- 通用/设备/流程/小游戏 → 任务 import（P0 已清零）。
- 非 app → app import（设置读取走 `core/settings`）。
- 低层 → 高层 import（`core ← device ← workflows/minigames ← tasks`）；
  `UPWARD_ALLOWLIST` 精确到子模块，登记 core 历史环的依赖边，只减不增。
- `src.*` 导入目标必须真实存在；旧平铺路径残留（含函数内延迟导入）
  直接失败，不再被解析器静默丢弃。
- 两个白名单的 stale 检查：边消失后必须同步删除条目。

新增例外必须同时满足：白名单条目 + 注释原因 + 标注清理阶段，缺一不可。

---

## 7. 迁移路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 本规范 + 导入规则测试 + 3 处反向依赖修复 | ✅ 已完成（2026-11-13） |
| P1 | `follow_task` 及消失确认/对话框链下沉 `workflows/follow.py` | ✅ 已完成（2026-11-13）：任务互引清零；玄枢专属小游戏改为 `wait_handler` 注入，其他任务不再执行玄枢小游戏检测 |
| P1 | 小游戏逐个下沉 `src/minigames/` | ✅ 已完成（2026-11-13）：`pochufengyin`、拍毒虫、音律、沙海迷踪、机关、锄遗聚宝的阻塞循环 + 检测/常量已迁移；任务侧只保留薄包装与触发/armed/重置（触发点函数逐字节未变）。钓鱼/破除封印的入口 armed 仍在任务侧，按「改动即搬迁」处理 |
| P2 | `wubei`/`xiuluo`/`zhongzu` 领取/返程/队伍/巫医流程去重 | 待做 |
| P2 | `settings.py` 从 `control_window` 抽出，启用「禁止依赖 app」检查 | ✅ 已完成（2026-11-13） |
| P2 | 目录迁移（纯 `git mv` + import 改写） | ✅ 已完成（2026-11-13） |
| P2 | 重构审查修复（拍毒虫退避状态、updater 路径深度、工具链/CI 路径、导入规则补强） | ✅ 已完成（2026-11-13） |
| P2 | 既有问题清理（独立诊断脚本移出包、package-data 去用户数据/旧模板路径、清屏状态键命名、拍毒虫完成态 TTL 清理、恒真测试修复、小游戏 re-export 清理、binding/progress 专属测试） | ✅ 已完成（2026-11-13） |
| P3 | 拆分 `control_window.py`、main 任务注册表、模板目录分组 | 待定 |
