# 角色数据目录设计（data/<角色ID>/）

> 2026-11-11 上线。目标：同一安装目录下切换不同游戏账号时，方案、
> 成就、高性能坐标缓存、任务进度完全隔离；旧版本升级自动迁移，根目录
> 旧文件原样保留以支持回滚。

## 1. 目录布局

```
data/
  _meta.json                     # layout=2、迁移信息、known_roles、last_active_role、清理/归档标记
  _legacy_config.json            # 第二次启动清理 config.json 旧键时的备份（手动恢复用）
  _legacy/                       # 根目录旧版 schemes.json / task_stats.json 归档（回滚用）
    schemes.json
    task_stats.json
  <角色ID>/                      # 窗口标题「（ID：数字）」中的数字
    settings.json                # 该角色当前生效设置（任务选择/次数/阈值/战斗动作/各开关）
    schemes.json                 # 该角色的配置方案（多套 + active）
    state.json                   # 任务进度 / 摄妖香 / 清屏 / 名望种族 / 组号状态
    task_stats.json              # 该角色的成就统计（累计/日档/会话）
    high_perf_cache.json         # 该角色的高性能固定坐标缓存
  _default/                      # 无 ID 兜底（未登录窗口/空标题的旧数据）
```

- `data/` 与 `config.json` 同目录：开发环境为工程根，打包后为 exe
  所在目录；不可写时跟随 KeyValue 退 `~/.app_data`。
- 角色 ID 只取数字；非法/缺失退 `_default`。

## 2. 角色归属

| 数据 | 归属 | 说明 |
|------|------|------|
| 生效设置 `settings.json` | 角色 | 任务选择/次数/阈值/战斗动作/各任务开关；控制窗口写、任务子进程直接读 |
| 方案 `schemes.json` | 角色 | 命名方案快照 + active；方案切换写入当前角色 settings.json |
| 成就 `task_stats.json` | 角色 | 成就页顶部标明当前角色 |
| 高性能缓存 `high_perf_cache.json` | 角色 | 换角色重新学习（迁移时只有当前角色继承旧缓存） |
| 任务进度 / 摄妖香 / 清屏 / 名望种族 | 角色 | `state.json` 平铺键 |
| 组号成员动作 / 队伍快照 / 组号识别 | 角色（队长） | 换队长账号各自一套（2026-11-11 用户确认） |
| 主题、老板键、授权、更新、调试开关、窗口绑定 | 全局 | 留在 `config.json`（清理后仅这些） |
| 日志 | 全局 | `logs/日期.log`（不按角色拆） |

## 3. 运行流程

1. `main._ensure_window_binding()`：绑定/复用游戏窗口；若绑定窗口的
   标题账号 ID 与绑定记录不一致（同窗口换号），自动更新绑定。
2. `user_data.migrate_if_needed()`：首次升级执行迁移（见 §4）；
   保证当前角色有 `settings.json`；已迁移则补导旧成就/认领 `_default`。
3. `schemes.bootstrap_current_role()`（刚迁出角色设置）或
   `schemes.ensure_role_schemes()`：确保当前角色方案文件存在。
4. `schemes.activate_role(上次活动角色, 当前角色)`：只确保当前角色
   方案文件——生效设置已按角色存放，换角色不改写 `config.json`。
5. `user_data.set_last_active_role()` 记录本次角色。
6. 控制窗口写 `data/<角色>/settings.json`；任务子进程通过
   `role_settings` 直接读当前角色设置（`user_data.current_role_id()`：
   **绑定记录 → 实时窗口标题 → `_default`**，30s 缓存）。绑定优先
   （2026-11-13）：组号（一窗口多 Tab）pass 切到队员 Tab 时窗口标题会
   短暂变成队员账号，按标题解析会让面板/统计在 pass 期间漂移到队员
   角色目录（任务进度归零、成就页空态）。任务启动时
   `ControlWindow._sync_start_tab_binding()` 把绑定对齐到启动所在
   Tab（组号队长候选 = 可见 Tab），保证「启动在哪个 Tab 就是哪个角色」。
7. **运行期换号（A 退出、打开 B）**：`find_main_window` 在绑定窗口
   消失后动态回退到其他游戏窗口（脚本实际就操作 B），但绑定记录
   不变会让角色数据停在 A。控制窗口 5s 轮询
   `get_role_info` → `game.ensure_binding_follows_live_window()` 把
   绑定改绑到实际回退窗口并立即刷新角色缓存，页面比对
   `get_init_data.role` 变化后全量重同步方案下拉/设置/角色文案。
   绑定窗口存在时（含组号 pass 切 Tab）不改绑，仍以绑定记录为准；
   未登录窗口（标题无账号 ID）不改绑；游戏窗口全部关闭时沿用绑定
   记录（客户端重启间隙不落到 `_default`）。

## 4. 旧版本升级迁移（layout 2）

在 `_ensure_window_binding()` 之后、控制窗口创建之前执行一次：

1. 读取根目录 `config.json` / `schemes.json` / `task_stats.json`。
2. 从 `task_progress`、`sheyaoxiang_last_use`、`clear_screen_state`、
   `mingwang_race` 按 `（ID：xxx）` 键拆分到各角色 `state.json`；
   非 ID 键（完整标题/空键）进 `_default`，完整原映射保留在
   `state["legacy"]`。
3. 组号相关键（`group_member_actions` / `group_last_scan` /
   `group_detected`）整体归迁移时的当前角色。
4. **当前角色**：复制旧 `schemes.json` / `task_stats.json` /
   `high_perf_cache` 到 `data/<当前角色>/`（用户 2026-11-11 选择 B：
   其他角色不继承方案/成就/缓存，从默认方案、空成就、重新学习开始）。
5. 其他角色：只写各自的 `state.json`。
6. 写 `data/_meta.json`（layout=2、migration_role、known_roles、
   stats_role、copied、last_active_role=null）。
7. **角色设置**：`config.json` 的生效设置键（方案白名单
   `schemes._settings_spec` + 旧 `task_mode` / `xiuluo_high_perf`）
   写入 `settings_owner`（上次活动角色 / 迁移角色）的
   `data/<角色>/settings.json`，含未保存改动；当前角色若不同，用自己的
   活动方案或默认值建自己的一份。任务子进程从此只读该文件。
8. 根目录 `schemes.json` / `task_stats.json` 在**后续启动**（迁移/设置迁出
   当次之后）确认角色副本已就绪后，`user_data.archive_legacy_files()`
   移动到 `data/_legacy/`；成就未补导完成前不移动 `task_stats.json`。
   `config.json` 里的旧字段按 §5.1 两阶段清理。

迁移是复制不是移动、带 `layout` 标记且幂等：中途失败重跑只补缺失
文件，不覆盖 `data/` 里已有数据。

**成就补导兜底**：若迁移时目的角色 `task_stats.json` 为空/复制失败，
后续启动在 `stats_role`（迁移时的当前角色）上一次性补导根目录旧成就；
成功后写 `stats_imported` 标记，用户之后清空成就不再重导。

### 无绑定时的 _default

迁移时没有可用角色（游戏未启动且无绑定记录）→ 数据写进
`data/_default/`。首次绑定真实角色后，`_maybe_adopt_default()` 把
`_default` 的方案/成就/高性能缓存复制给该角色（只补缺失），并记录
`meta.adopted_by`，只认领一次。

## 5. 升级 / 回滚

- 自动更新只覆盖 `zhuomuniao.exe` / `res/` / `pylibs/`（`updater.PART_TARGETS`），
  `data/` 与根目录旧文件都不在覆盖范围，升级天然保留。
- 旧版本回滚：根目录旧 `schemes.json` / `task_stats.json` 已归档到
  `data/_legacy/`，回滚前需手动移回根目录（旧版只读根目录）；
  `config.json` 的方案设置键在两阶段清理后删除，需要时从
  `data/_legacy_config.json` 恢复。新版本运行期间写进 `data/` 的
  数据在回滚后不可见；再次升级时 `layout=2` 已存在，不会自动合并
  回滚期间产生的旧版数据（已知边界，避免重复累计）。
- 全新安装：无旧文件，按当前角色建目录并写 `_meta.json`；方案/设置
  在首次启动时以当前设置创建「默认」。

### 5.1 config.json 两阶段清理

清理两类键：

1. **旧版角色维度运行时键**：`task_progress`、`sheyaoxiang_last_use`、
   `clear_screen_state`、`mingwang_race`、`group_member_actions`、
   `group_last_scan`、`group_detected`、`high_perf_cache`（`group_last_scan`
   可能不存在）。
2. **已迁入 `settings.json` 的方案设置键**：`task_modes`、`task_count_*`、
   阈值、战斗动作、修罗/五倍/五环/客栈各开关、`silent_mode`、`high_perf`、
   `auto_refill`、`clear_screen`、`auto_famen`，以及旧 `task_mode` /
   `xiuluo_high_perf`。

- **迁移/设置迁出当次不动 config**（回滚安全，留一轮成功启动窗口）。
- **下一次启动** `user_data.prune_legacy_config()`：把待清理键与已有备份合并，
  完整写入 `data/_legacy_config.json`，再从 `config.json` 删除；
  `_meta.json` 写 `legacy_config_pruned` / `settings_pruned` /
  `legacy_config_backup` / `legacy_config_keys`。
- 回滚到旧版本若需要这些键：把备份里的 `keys` 写回 `config.json`（旧版
  不会自动读备份）；不恢复则旧版按缺失处理（任务设置回默认）。
- 根目录 `schemes.json` / `task_stats.json` 不受此清理影响；在角色副本
  就绪后的下一次启动归档到 `data/_legacy/`（见 §4 第 8 步）。

清理完成后 `config.json` 只剩软件全局设置：

- `bound_window_id`、`license_cache_no`、`ui_theme`、`debug_overlay`、
  `debug_ui_unlocked`（若解锁过）、`boss_hotkey`（若配置过）、
  `update_auto_check` / `update_last_check_ts` / `update_skipped_version` /
  `update_feed_url`（若存在）

## 6. 代码入口

| 模块 | 职责 |
|------|------|
| `src/core/user_data.py` | 角色解析、data 路径、state/settings 读写、高性能缓存、迁移、旧 config 字段清理、旧版文件归档 |
| `src/core/role_settings.py` | 当前角色生效设置（settings.json）的进程内缓存与读写 |
| `src/core/app_utils.py` | 角色状态/任务进度/摄妖香/清屏/高性能缓存的读写封装；`kv_remove_many_merged` |
| `src/core/debug_overlay.py` | 成就文件路径 `data/<角色>/task_stats.json` |
| `src/core/schemes.py` | 按角色方案文件、方案切换写 settings.json、迁移后 bootstrap |
| `src/core/settings.py` | 角色设置 load/save（从 control_window 抽出）；进度/组号状态读当前角色 |
| `src/app/control_window.py` | 设置项 UI 接线与 re-export；成就页返回 role 信息 |
| `main.py` | 绑定后执行迁移 + 角色设置建立 + 两次启动后的旧 config 字段清理 |

## 7. 测试

- `tests/test_user_data.py`：迁移拆分/复制范围/幂等/根文件保留/
  `_default` 认领/角色隔离/旧 config 字段备份清理/成就补导。
- `tests/test_schemes_role.py`：默认方案、迁移 bootstrap、换角色、
  方案应用写 settings.json。
- `tests/test_app_utils.py`：角色状态与任务进度按角色隔离。
- `tests/test_control_window.py`：组号动作/快照按角色 store、主题不进方案。
- `tests/test_tab_group.py`：组号识别状态写角色 store。
