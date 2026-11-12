# 啄木鸟 · 大话西游2经典版自动化助手 — 技术架构

> 面向：参与维护、排障和扩展本项目的工程师。
> 代码基线：`src/` 当前主干（v2.9.5 之后）。图中节点名与函数名一致，便于直接定位。
> 约定：实线为主路径，边上的文字为分支条件；带「」的文案是游戏内 OCR 原文。
> 阅读顺序：图 1 看进程与模块边界 → 图 4-1 ~ 4-6 看各任务分支 → 图 5/6 看共享引擎 → 图 7 看组队与组号。
> 渲染：GitHub 直接渲染；VSCode 需安装 Mermaid 预览插件（如 Markdown Preview Mermaid Support）。

---

## 0. 架构速览

| 维度 | 设计 |
|---|---|
| 进程模型 | **主进程**（pywebview GUI / 控制平面）+ **任务子进程**（spawn，daemon）+ **悬浮层绘制子进程**（subprocess，独立进程） |
| 线程模型 | 主进程：GUI 主线程、TaskManager 日志监听、窗口看护、老板键消息泵、托盘、Runtime 驻留、OCR 预热；任务子进程：`task_start` daemon 线程、`battle.watch_loop` 战斗线程、摄妖香空闲兜底线程、debug_overlay 监督线程 |
| 停止模型 | **杀进程是唯一可靠的全线程清理方式**：`stop_event → join 3s → terminate → join 1s → kill`（业务循环无协作式停止标志） |
| 任务编排 | 面板勾选任务 → 按顺序逐个进入 `_run_task_mode`；每个任务循环自然结束后切下一个，全部结束任务进程退出 |
| 感知 | Win32 窗口截图（不激活）→ PIL 帧（OCR / 绿字）或 BGR 帧（模板匹配）；RapidOCR 三种引擎（全帧 / 区域 / 2x 放大）+ OpenCV 模板匹配 |
| 执行 | 物理鼠标（拟人化，全局 `RLock` 串行）或静默消息通道（`WM_MOUSEMOVE/LBUTTONDOWN/UP`，不移动鼠标、不抢前台）；键盘同理区分物理 / `WM_SYSKEY` / `WM_CHAR` |
| 持久化 | `config.json`（KeyValue：软件全局设置 + 窗口绑定）、`data/<角色ID>/`（settings/schemes/state/task_stats/high_perf，按角色隔离）、`debug_overlay_state.json`（看板/悬浮层心跳）、`logs/日期.log`；详见 docs/user-data-layout.md |
| 硬约束 | 每轮询只截一帧；模板热路径必须用 ROI 条带；OCR 与绿字判定必须用 PIL(RGB) 帧；跟随超时不计战斗时间；关键时序常量是实测校准资产 |

---

## 1. 系统全景架构图

```mermaid
flowchart TB
    subgraph EXT["外部环境"]
        GAME["游戏客户端 xy2_tab_x64<br/>主窗口类 DHXYJYMainFrame<br/>多个登录 Tab 子窗口"]
        ADMIN["操作员<br/>控制面板 / F11 老板键 / 托盘"]
    end

    subgraph MAIN["主进程（GUI + 控制平面）"]
        ENTRY["main.py<br/>授权门 → 窗口绑定 → 方案迁移 → 装配"]
        UI["ControlWindow（pywebview）<br/>ApiBridge / 运行看板 / 任务页 / 诊断页"]
        TM["TaskManager<br/>spawn、强杀、日志回流、窗口看护"]
        HK["hotkey.py<br/>全局 F11 启停"]
        TRAY["pystray 托盘"]
        VERIFY["诊断 / 验证按钮<br/>主进程内跑真实链路"]
        MP_OCR["OCR 预热线程<br/>供验证按钮使用"]
    end

    subgraph SUB["任务子进程（daemon, spawn）"]
        WRAP["_worker_wrapper<br/>stdout/stderr → multiprocessing.Queue"]
        BODY["task_start → _task_start_body<br/>启动检查 + 模式队列"]
        BT["battle.watch_loop<br/>战斗监控线程"]
        TASKS["任务主循环<br/>玄枢 / 五环 / 客栈 / 修罗 / 五倍"]
        SHY["摄妖香空闲兜底线程"]
        OVS["debug_overlay._supervise<br/>状态文件 + 统计心跳"]
    end

    subgraph HELPER["悬浮层绘制子进程"]
        OVH["_helper_main<br/>WS_EX_LAYERED 分层窗口 GDI 绘制"]
    end

    subgraph DATA["持久化 / 资源"]
        CFG[("config.json<br/>KeyValue 软件全局设置")]
        ROLE[("data/&lt;角色ID&gt;/<br/>settings / schemes / state / task_stats / high_perf")]
        STATE[("debug_overlay_state.json")]
        LOG[("logs/日期.log")]
        RES[("res/img 模板<br/>res/models OCR 模型")]
    end

    ADMIN --> ENTRY
    ENTRY --> UI
    ENTRY --> TM
    ENTRY --> HK
    ENTRY --> TRAY
    UI --> TM
    UI --> VERIFY
    HK --> TM
    VERIFY --> GAME
    VERIFY --> MP_OCR
    UI --> CFG
    UI --> ROLE

    TM -->|"multiprocessing.Process"| WRAP
    TM -->|"stop_event / terminate / kill"| WRAP
    WRAP -->|"日志 Queue（批 ≤50 行）"| TM
    TM -->|"sync_status / add_logs"| UI
    WRAP --> BODY
    BODY --> BT
    BODY --> TASKS
    BODY --> SHY
    BODY --> OVS

    OVS -->|"原子写状态 + 心跳"| STATE
    STATE -->|"读状态（15s 心跳超时自尽）"| OVH
    OVH -->|"分层窗口叠加绘制"| GAME

    BT --> GAME
    TASKS --> GAME
    TASKS --> CFG
    TASKS --> ROLE
    OVS --> ROLE
    OVS --> LOG
    TASKS --> LOG
    UI -->|"read_runtime_status"| STATE
    UI -->|"read_task_stats"| ROLE
    TASKS --> RES
    BT --> RES

    TM -->|"窗口句柄看护<br/>失效 3s → 自动停任务"| GAME
```

### 1.1 进程 / 线程职责

| 单元 | 职责 | 关键约束 |
|---|---|---|
| 主进程 | 唯一 GUI、配置管理、授权门、窗口绑定、任务启停、诊断验证 | 主线程必须跑 `webview.start()`；授权窗口 / 绑定弹窗各独占一次消息循环后清理 `webview.windows` |
| TaskManager | 子进程生命周期、日志队列回流、窗口看护、启停互斥 | 停止采用四级升级；旧 monitor 不得覆盖新任务状态 |
| 任务子进程 | 启动检查、战斗线程、任务主循环、摄妖香续用、悬浮层监督 | 所有循环可被强杀；`task_start` 顶层兜底 `UserAbort` 与未捕获异常落盘 |
| 悬浮层子进程 | 读取状态文件绘制游戏窗口上的任务信息 | 父进程死 15s 后 `os._exit`；崩溃由监督线程自动重启；GDI 崩溃只丢悬浮层 |
| 更新助手 | 自动更新：等主进程退出 → 备份/覆盖 exe/res/pylibs → 重启/回滚 | 运行在 `%TEMP%` 的 exe 副本，不锁安装目录；只动白名单文件；详细设计见 `docs/auto-update-design.md` |
| 主进程验证 | 「诊断」页按钮在主进程跑真实链路（OCR 预热在此） | 与任务子进程共用 `find_main_window` 接缝；绑定 Tab 护栏 |

---

## 2. 模块分层与依赖

```mermaid
flowchart TB
    subgraph L1["表现 / 控制层"]
        M_MAIN["main.py"]
        M_UI["control_window.py"]
        M_SCHEME["schemes.py"]
        M_HOTKEY["hotkey.py"]
        M_SELECT["select_window.py"]
        M_OVERLAY["debug_overlay.py"]
    end

    subgraph L2["任务编排层"]
        M_TM["task_manager.py"]
        M_XS["xuanshu.py<br/>玄枢轶闻"]
        M_WH["wuhuan.py<br/>五环"]
        M_KZ["kezhan.py<br/>客栈"]
        M_XL["xiuluo.py<br/>修罗"]
        M_WB["wubei.py<br/>五倍"]
        M_MW["mingwang.py<br/>名望（第一阶段）"]
        M_BATTLE["battle.py<br/>战斗监控"]
        M_CLEAR["clear_screen.py<br/>启动清屏"]
    end

    subgraph L3["领域服务层"]
        M_FOLLOW["xuanshu.follow_task<br/>共享任务跟踪引擎"]
        M_TEAM["team.py<br/>队伍检查/补人/请离/归队"]
        M_TAB["tab_group.py<br/>组号多 Tab"]
        M_BAG["bag.py<br/>背包 / 摄妖香"]
        M_ZUOQI["zuoqi.py<br/>飞行驭器"]
    end

    subgraph L4["感知层"]
        M_MAP["map.py<br/>OCR 引擎 / 模板 / 地图"]
        M_TRACK["task_tracker.py<br/>追踪面板 / 绿字 / TrackGate"]
        M_DIALOG["dialog_common.py<br/>对话框通用操作"]
        M_BARS["status_bars.py<br/>血蓝条"]
    end

    subgraph L5["执行层"]
        M_UTIL["app_utils.py<br/>点击/键盘/锁/持久化"]
        M_SILENT["silent_click.py<br/>Win32 消息投递"]
        M_GAME["game.py<br/>窗口/Tab/绑定"]
        M_ENV["env_check.py / capture_diag.py"]
    end

    subgraph L6["平台与依赖"]
        AS["AScript Windows<br/>Window / Mouse / Keyboard / R / FindImages"]
        WIN["Win32 / UIAutomation"]
        OCR["RapidOCR + ONNXRuntime"]
        CV["OpenCV + NumPy"]
    end

    M_MAIN --> M_UI
    M_MAIN --> M_TM
    M_MAIN --> M_XS
    M_MAIN --> M_WH
    M_MAIN --> M_KZ
    M_MAIN --> M_XL
    M_MAIN --> M_WB
    M_MAIN --> M_BATTLE
    M_MAIN --> M_CLEAR
    M_MAIN --> M_OVERLAY
    M_UI --> M_SCHEME
    M_UI -->|"验证按钮"| M_XS
    M_UI -->|"验证按钮"| M_WH
    M_UI -->|"验证按钮"| M_KZ
    M_UI -->|"验证按钮"| M_XL
    M_UI -->|"验证按钮"| M_WB
    M_UI -->|"验证按钮"| M_TEAM
    M_UI -->|"验证按钮"| M_ZUOQI

    M_XS --> M_FOLLOW
    M_WH --> M_FOLLOW
    M_KZ --> M_FOLLOW
    M_XL --> M_FOLLOW
    M_WB --> M_FOLLOW
    M_XS --> M_TRACK
    M_WH --> M_TRACK
    M_KZ --> M_TRACK
    M_XL --> M_TRACK
    M_WB --> M_TRACK
    M_XS --> M_MAP
    M_WH --> M_MAP
    M_KZ --> M_MAP
    M_XL --> M_MAP
    M_WB --> M_MAP
    M_XS --> M_DIALOG
    M_WH --> M_DIALOG
    M_KZ --> M_DIALOG
    M_XL --> M_DIALOG
    M_WB --> M_DIALOG
    M_XL --> M_TEAM
    M_WB --> M_TEAM
    M_XL --> M_TAB
    M_WB --> M_TAB
    M_KZ --> M_BAG
    M_WH --> M_BAG
    M_WH --> M_ZUOQI

    M_BATTLE --> M_BARS
    M_BATTLE --> M_TAB
    M_BATTLE --> M_MAP
    M_BATTLE --> M_DIALOG
    M_FOLLOW --> M_TRACK
    M_FOLLOW --> M_MAP
    M_FOLLOW --> M_DIALOG
    M_TEAM --> M_MAP
    M_TEAM --> M_TRACK
    M_TEAM --> M_GAME
    M_TAB --> M_TEAM
    M_TAB --> M_BATTLE
    M_TAB --> M_GAME
    M_TAB --> M_SILENT
    M_TRACK --> M_MAP
    M_BAG --> M_MAP
    M_BAG --> M_GAME
    M_MAP --> M_UTIL
    M_MAP --> AS
    M_MAP --> OCR
    M_MAP --> CV
    M_TRACK --> M_MAP
    M_TRACK --> M_UTIL
    M_DIALOG --> M_TRACK
    M_DIALOG --> M_MAP
    M_UTIL --> M_SILENT
    M_SILENT --> M_GAME
    M_SILENT --> WIN
    M_GAME --> AS
    M_GAME --> WIN
    M_OVERLAY --> WIN
```

> 依赖方向：任务模块只向下依赖感知/执行层；`battle ↔ team/tab_group`、`app_utils ↔ silent_click` 存在反向调用，均通过**函数内延迟导入**打破循环依赖。

---

## 3. 启动、运行与停止生命周期

### 3.1 主进程启动时序

```mermaid
sequenceDiagram
    autonumber
    participant U as 操作员
    participant M as main.py
    participant L as LicenseWindow
    participant B as 窗口绑定
    participant S as schemes.py
    participant C as ControlWindow
    participant T as TaskManager
    participant P as 任务子进程

    U->>M: 启动（源码 / exe）
    M->>L: 静默在线校验（get_app_info → activate_key → start_app）
    alt 校验被拒 / 网络异常
        M->>L: show 激活窗口（阻塞，独占一次 webview.start）
        L-->>M: 激活成功或关闭
    end
    M->>M: _ensure_window_binding<br/>单窗口自动绑定 / 多开弹框选择
    M->>S: migrate_if_needed<br/>存量配置迁移为「默认」方案
    M->>M: set_silent_mode（主进程验证通道）
    M->>T: TaskManager(task_start, task_stop)
    M->>C: ControlWindow(tm, license_info, license_gate)
    M->>M: webview.windows.clear()<br/>prepare 唯一窗口 → webview.start()
    M->>M: 托盘 / 老板键 / Runtime.wait / OCR 预热（后台线程）

    U->>C: 点击「启动」或按 F11
    C->>C: license_gate 在线校验到期
    C->>T: start()
    T->>P: spawn _worker_wrapper
    P->>P: task_start（daemon 工作线程）
    P-->>T: stdout/stderr → Queue
    T-->>C: sync_status(True) / add_logs(batch)

    U->>C: 点击「停止」
    C->>T: stop()
    T->>P: stop_event → join 3s → terminate → join 1s → kill
    T-->>C: sync_status(False) + 结束原因日志
```

### 3.2 任务子进程启动检查

```mermaid
flowchart TB
    A["task_start<br/>顶层兜底 UserAbort / 未捕获异常完整堆栈落盘"] --> B["读取配置<br/>modes / 摄妖香 / 战斗动作 / 悬浮 / 补血蓝 / 清屏 / 高性能 / 静默"]
    B --> C["共享设施置位<br/>set_high_perf / set_silent_mode / set_current_task_mode"]
    C --> D["OCR 预热 warmup_ocr"]
    D --> E["UIAutomationInitializerInThread<br/>COM 线程初始化"]
    E --> F["列游戏窗口 + env_check 环境自检<br/>DPI / 显示器 / 首帧黑帧"]
    F --> G{"30s 内找到主窗口？"}
    G -- 否 --> Z["debug_overlay.shutdown<br/>子进程退出"]
    G -- 是 --> H{"modes 含「仅战斗」？"}
    H -- 是 --> BO["_battle_only_loop<br/>启动战斗线程 + 自动归队轮询"]
    BO --> Z
    H -- 否 --> I["debug_overlay.start<br/>set_phase(准备中)"]
    I --> J["read_login_tab<br/>标题角色段优先"]
    J --> K{"已登录？"}
    K -- 否 --> Y["跳过清屏/摄妖香/任务<br/>任务进程结束"]
    Y --> Z
    K -- 是 --> L["启动清屏<br/>窗口与角色未变则跳过"]
    L --> M{"摄妖香开关开 且 到期<br/>且（修罗/五倍）非队长 且 非战斗？"}
    M -- 是 --> M1["bag.use_sheyaoxiang → 记录时间戳"]
    M -- 否 --> M2["跳过（寻路期 / 空闲期兜底再补）"]
    M1 --> N["启动摄妖香空闲兜底线程<br/>每 60s 检查，≥10 分钟无寻路才补用"]
    M2 --> N
    N --> O["启动 battle.watch_loop 线程"]
    O --> P["按面板顺序遍历 modes"]
    P --> Q["_run_task_mode(mode)<br/>任务前置 + 主循环"]
    Q --> R{"还有 mode？"}
    R -- 是 --> Q
    R -- 否 --> S["status_bars.check_all_bars<br/>血蓝条检测一次"]
    S --> Z
```

### 3.3 停止与异常收口

| 路径 | 行为 |
|---|---|
| 面板停止 / F11 | `TaskManager.stop()`：置 `stop_event` → 子进程 wrapper 在 ≤3s 内执行 `task_stop`（回收悬浮层进程）→ 未退出则 `terminate` → 再 `kill` |
| 任务自然结束 | 模式列表跑完 / 达到次数上限 / 每日上限 / 无法继续（如客栈找不到掌柜）→ `_task_start_body` 结束 → wrapper 执行 `task_stop` → monitor 收口 `exitcode` |
| 游戏窗口关闭 | `_window_watchdog` 每秒 `IsWindow(target)`，连续 3 次失效判定窗口已关闭 → 自动 `stop()` 防截图失败刷屏 |
| 用户接管鼠标 | 物理鼠标移到屏幕角落触发 fail-safe → `UserAbort`（继承 `BaseException`，穿透所有 `except Exception`）→ 顶层记 WARN 后干净中止 |
| 任务子进程异常 | `task_start` 记录完整堆栈到日志文件后重抛；monitor 输出 `exitcode` |

---

## 4. 任务分支总览

### 4.1 模式分发

```mermaid
flowchart LR
    Q["面板已选任务<br/>按加入顺序执行"] --> XS["玄枢轶闻"]
    Q --> WH["五环"]
    Q --> KZ["客栈"]
    Q --> XL["修罗"]
    Q --> WB["五倍"]
    Q --> BO["仅战斗<br/>与其它任务互斥"]
    Q --> MW["名望<br/>第一阶段：种族判定→魔族领取入口"]

    XS --> XSP["前置：无追踪任务 → 退队/解散"]
    KZ --> KZP["前置：退队/解散"]
    XL --> XLP["可组号：队长带队 + 队员 pass"]
    WB --> WBP["可组号：队长带队 + 队员 pass"]
    BO --> BOP["只跑战斗线程 + 自动归队"]
```

### 4.2 分支速查表（详细流程见图 4-1 ~ 4-6）

| 任务 | 阶段 | 分支条件 | 行为 |
|---|---|---|---|
| 玄枢轶闻 | 前置 | 追踪中无任务 | `team.leave_or_disband_team` 单人执行 |
| 玄枢轶闻 | 领取 | 已在洛阳 93,125 附近 | 跳过寻路，直接找领取按钮 |
| 玄枢轶闻 | 领取 | 不在洛阳 | 世界地图 → 洛阳 → 小地图点任务点 → 等待到达 |
| 玄枢轶闻 | 领取 | 领取按钮被对话框遮挡 | 点屏幕中间关闭后重试 |
| 玄枢轶闻 | 领取 | 「少侠已经完成5次任务」 | 结束任务（每日次数已满） |
| 玄枢轶闻 | 跟踪 | 追踪出现「使用监天盘」 | 火云戈壁寻路 → 点监天盘 → 探妖 → 仙药 |
| 玄枢轶闻 | 跟踪 | 出现封印提示对话框 | 关对话 → Alt+C 降落 → 按 黄→蓝→红 点机关 |
| 玄枢轶闻 | 跟踪 | 追踪出现「机关·东/南/西」 | 按 南→东→西 顺序点链接（进度键 + 下划线定位） |
| 玄枢轶闻 | 跟踪 | 出现锄遗聚宝小游戏 | 点开始 → 清红色杂气 → 主图消失结束 |
| 客栈 | 前置 | 始终 | 退队/解散单人执行 |
| 客栈 | 判定 | 追踪无任务 | 隔 2s 复核一次，防瞬时漏读 |
| 客栈 | 跟踪 | 任务要求大理寺/比武场 | 手动寻路（带 REDO_INTERVAL 节流） |
| 客栈 | 跟踪 | 追踪出现智戏成空三封印链接 | 按 风吟印→炎阳印→水镜印 顺序点链接（进度键「封印N」+ 下划线定位；三处全开文案「封印已经全部开启」，同三才） |
| 客栈 | 跟踪 | 出现「你输了/平局」 | 关对话 → 重新点任务绿字推进 |
| 客栈 | 跟踪 | 奇遇对话框 | 多选项恒点第一个 |
| 客栈 | 跟踪 | 音律小游戏入口被点击后 | 检测窗口横幅 → 辨八音演奏 15 次 |
| 客栈 | 跟踪 | 神秘宝箱 / 给与面板 | 独立处理链路 |
| 客栈 | 领取 | 每日次数已满 / 任务链结束 | 结束任务 |
| 客栈 | 领取 | 在客栈但找不到掌柜 | 终止任务 |
| 五环 | 准备 | 背包已有项链 | 跳过购买 |
| 五环 | 准备 | 背包无项链 | 布店寻路 → 降落检查 → 进店 → 买 2 件 → 购买 |
| 五环 | 领取 | 活力值不足 | 开关开：点「不差那点小钱…」继续；关：点「那可不行…」结束 |
| 五环 | 领取 | 今日两次已满 | 结束任务 |
| 五环 | 跟踪 | 提交装备对话 | 点「刚好我这里有，送你啦」→ 给与面板选项链 → 给与 |
| 五环 | 跟踪 | 单次/每日完成恭喜 | `SingleComplete` / `DailyComplete`，按对话框证据计数 |
| 五环 | 跟踪 | 任务消失但无恭喜对话框 | 8s 窗口补查；仍未命中判面板瞬时不可读、不计完成 |
| 五环 | 跟踪 | 给与面板找不到项链 | `MissingEquipment` → 结束任务 |
| 修罗 | 启动 | 组号激活但「检查队伍」关闭 | 停止任务并提示 |
| 修罗 | 启动 | 组号激活但非队长 Tab | 停止任务；确定性未组队则降级为普通非队长继续 |
| 修罗 | 启动 | 队员 Tab 未设置战斗动作 | 停止任务并提示先到组号页设置 |
| 修罗 | 每轮 | 追踪已有任务 | 直接跟踪，并记队长/已检查队伍 |
| 修罗 | 领取前 | 不在领取点 | 先判队长身份，非队长不白走寻路 |
| 修罗 | 领取前 | 缺人 | 进入监控循环：补申请人 → 重查 → 人满才领取 |
| 修罗 | 领取 | 不足 5 人确认弹窗 | 检查队伍开：拒绝 → 补人；关：拒绝并下轮重试 |
| 修罗 | 领取 | 「战斗力异常强大」（<3 人） | 关对话框 → 补人 |
| 修罗 | 领取 | 首次领取成功 | 后续轮次跳过队伍检查 |
| 修罗 | 跟踪 | 「看打！」对话 | 点绿字进战斗；若 OCR 漏检则 2x 放大兜底 |
| 修罗 | 返程 | 本轮由「看打！」进入战斗 | 开背包任务格 → 修罗回城符 → 关面板 → 走回 NPC |
| 修罗 | 非队长 | 确定性非队长 | `_keep_alive` 保持模式：只跑战斗线程 + 自动归队 |
| 五倍 | 跟踪 | 追踪任务含锁妖网 | 抛 `ReplaceTaskError` → 下一轮重新领取替换 |
| 五倍 | 跟踪 | 领取奎木狼开关关闭且任务含奎木狼 | 同上替换 |
| 五倍 | 跟踪 | 显形镜 A 处未触发对话框 | 点链接去 B 处；两处用镜失败 → 替换任务 |
| 五倍 | 跟踪 | 显形镜任务按钮候选多处 | 逐一点击，误点阻塞对话框先关闭再换候选 |
| 五倍 | 跟踪 | 黄袍怪多杀 | 每次战斗结束后重点追踪链接走下一处 |
| 五倍 | 返程前 | 黄袍怪轮次且任务仍在 | 4s 内复查，仍在则跳过返程继续跟踪 |
| 五倍 | 返程 | 本轮由「看打！」进入战斗 | 开背包任务格 → 五倍回城符 → 关面板 → 走回 NPC |
| 五倍 | 结束 | 完成任务次数上限 | 结束并记录进度 |
| 名望（第一阶段） | 启动 | 种族判定（按角色每天缓存一次） | 当天缓存直接复用；无当天缓存且需领取时：点 (1230,95) 开人物属性 → 匹配标题牌 → 标题牌右侧 120px 条带 OCR → 魔族/仙族，成功后写当天缓存（同日不再重判；角色可转种族，跨天重判；诊断重判可刷新） |
| 名望（第一阶段） | 领取 | 前往领取点（2026-11-12 去掉起飞/降落） | 直接经小地图/世界地图点目的地寻路；到达领取点不做起飞/降落（旧版 zuoqi.ensure_flying 起飞 + ensure_landed 降落已去掉）→ Alt+4 清屏 → 点 NPC |
| 名望（第一阶段） | 领取 | 魔族且不在大唐边境 | 世界地图「大唐边境」→ 小地图点 (985,384) → 等到达 477,180 |
| 名望（第一阶段） | 领取 | 已在大唐边境 | 开小地图点 (985,384) → 等到达 |
| 名望（第一阶段） | 领取 | 到达后 | 点逆天魔 → 绿字「开启山河名望志」（不用对话框门控）→ 点山河名望入口打开面板 |
| 名望（第一阶段） | 启动 | 追踪面板已有「山河名望」 | 跳过种族判定与领取，直接点击追踪绿字续跑跟踪（对齐玄枢轶闻） |
| 名望（第一阶段） | 面板领取 | 面板锚点下方 6 卡（3列×2行） | 逐卡 OCR 级别/标题 → 优先级 高级>中级>初级 + 卡片顺序选已实现任务（蛟魔复仇/沙海迷踪/蛟魔探秘/五庄封印/沙都修罗）→ 卡内点「接取」→ 按钮消失/任务进追踪面板即成功 |
| 名望（第一阶段） | 面板领取 | 无已实现任务 | 1s 复扫一次，仍无 → 结束并打印当前 6 卡级别/标题 |
| 名望（第一阶段） | 领取后 | 接取后出现对话框 | 对话框模板门控命中 → 点屏幕中间关闭（多屏复检，最多 3 次） |
| 名望（第一阶段） | 领取后 | 追踪出现「山河名望」 | 复用 xuanshu.follow_task 跟踪至任务完成（追踪面板标题条模板为门，防面板竖牌同名误判） |
| 名望（第一阶段） | 任务跟踪 | 对话「黑水河深，不如蛟魔之心。」 | 点绿色选项「逆天魔大将派我前来」（DIALOG_OPTION_MAP + extra_dialog_handler；story_known_texts 防误关） |
| 名望（第一阶段） | 沙海迷踪 | 对话「少侠准备好清理地图了吗？」→「我已经准备好了」→「开始」按钮 | armed 窗口（20s）内识别 mingwang_chenjiuditu_kaishi 点击进入「记住混沌」小游戏：地图卷轴区 363,262-1031,630 检测深黑墨团并按命中累计记录坐标（最大通道<60+面积/形状过滤，排除山体噪点）→ 底部提示含「显形」切换点击阶段 → 逐坐标点两下（第一下显形→间隔 0.2s 第二下净化；因点击被吞导致烟雾残留时 200ms 后补点）→ 中部横条「此处混沌已消散！」确认 → 循环下一批墨团，倒计时耗尽/弹窗关闭/结算文字结束（2026-11-11 实机验证通过） |
| 名望（第一阶段） | 净化水晶丛林 | 对话「糟糕，水晶丛林竟真被混沌缠上了，得赶快净化这些混沌！」→「马上净化水晶丛林上的混沌。」 | armed 窗口（20s）内识别「开始」按钮 mingwang_qingxishuijing_kaishi 点击进入战斗，战斗结束即完成 |
| 名望（第一阶段） | 蛟魔探秘 | 对话「老头子我最喜欢的，就是在这里钓鱼了~」→「请将鱼竿借我一用！」 | armed 窗口（120s/限频 1s）内识别窗口标题 mingwang_diaochushuizhongguaiwu，复用 pochufengyin 点击冲刺连点闭环（mash：在线估算每击推进量连点冲刺，到带后维持泵点击率伺服（按位置误差调节持续点击率）对抗自动回退、逼近右缘停泵，不按倒计时停手），标题消失即结束 |
| 名望（第一阶段） | 蛟魔复仇（暗雷战斗） | 追踪文案含「（暗雷战斗）」（如「请在女儿村仔细搜查」） | 点击追踪绿字由 follow 引擎完成寻路（本状态机不点链接）；解析「请在<地图>仔细搜查」目标地图，按 1s 限频识别地图位置地名（区域 OCR + 共享帧缓存），到达目标地图 → zuoqi.ensure_landed 降落（面板识别飞行状态、飞行中点「降落」，降落后关面板）→ 等待暗雷遭遇战斗（battle 线程托管，不做特殊处理）。已降落标志保持到目标地图变化/触发词连续 5 轮缺失复位（空 OCR 帧为战斗心跳不计缺失），新任务领取复位 |
| 名望（第一阶段） | 沙都修罗 | 追踪文案包含「根据三张蟠桃园残像所示位置」 | 不在蟠桃园 → 世界地图点「蟠桃园」标签 shijie_pantaoyuan 开小地图；依次点小地图 (739,588)→(126,32)、(623,476)→(66,87)、(541,428)→(27,112)、(552,310)→(33,171)、(710,393)→(111,130) 寻路到达 → 先查 NPC 本体模板 mingwang_shaduxiuluo（阈值 0.79，命中直接点模板中心）→ 未命中再世界区域 OCR 找「沙都修罗」（排除任务追踪同名文案 x&lt;400）→ 点文案上方 40px；全部路点均未找到限频重试 |
| 名望（第一阶段） | 主循环 | 完成一轮 | 回去领取下一轮，直到次数上限；启动时已有任务则跳过领取直接续跑 |
| 名望（第一阶段） | 启动 | 仙族/其他种族 | 结束并提示待补充（当前仅魔族） |
| 仅战斗 | 启动 | 已登录 | 只启动战斗线程；死亡落轮回司自动归队（默认开） |
| 仅战斗 | 启动 | 未登录 | 直接结束 |

### 4.3 图 4-1：玄枢轶闻（`xuanshu.py`）

```mermaid
flowchart TB
    A["main._run_task_mode(玄枢轶闻)"] --> A1{"追踪中已有任务？"}
    A1 -- 否 --> A2["team.leave_or_disband_team<br/>单人执行"]
    A1 -- 是 --> A3["直接续跑"]
    A2 --> B["run_loop"]
    A3 --> B
    B --> C["run_cycle"]
    C --> C1{"战斗中？"}
    C1 -- 是 --> C2["本轮失败，等待下一轮"]
    C2 --> B
    C1 -- 否 --> C3{"task_tracker.has_task<br/>玄枢轶闻？"}
    C3 -- 是 --> F["follow_task<br/>extra=_handle_jiantianpan<br/>progress=_jiguan_link_progress<br/>picker=_jiguan_link_picker"]
    C3 -- 否 --> D["navigate_to_task"]
    D --> D1

    subgraph NAV["领取链路 navigate_to_task"]
        D1["close_scene_panels<br/>关小地图/世界地图/背包"] --> D2["read_map_position"]
        D2 --> D3{"已在洛阳 93,125 附近？"}
        D3 -- 是 --> D7["等待到达"]
        D3 -- 否 --> D4{"当前在洛阳城？"}
        D4 -- 是 --> D5["open_mini_map"]
        D4 -- 否 --> D6["open_mini_map_city<br/>世界地图→洛阳→小地图"]
        D5 --> D8["点击小地图任务点<br/>close_scene_panels"]
        D6 --> D8
        D8 --> D7
        D7 --> D9["find_template 领取按钮"]
        D9 -- 未命中 --> D10["clear_blocking_dialog<br/>关闭遮挡后重试"]
        D10 --> D9
        D9 -- 命中 --> D11["点击领取按钮"]
        D11 --> D12["对话框1「玄枢轶闻」"]
        D12 -- 「请明天再来」 --> D13["每日次数已满 → 结束"]
        D13 --> Z
        D12 --> D14["对话框2「领取玄枢轶闻」"]
        D14 --> D15["轮询追踪面板确认任务出现<br/>TrackGate(min_interval=0)"]
        D15 --> D16["返回校验帧 → follow 首轮复用"]
    end

    D16 --> F
    F --> FB{"follow 内处理"}
    FB --> F1["监天盘子任务<br/>火云戈壁→监天盘→探妖→仙药"]
    FB --> F2["封印提示对话框<br/>关对话→Alt+C 降落→黄蓝红机关"]
    FB --> F3["封印按序开启<br/>南→东→西（下划线定位）"]
    FB --> F4["锄遗聚宝小游戏<br/>点开始→清红色杂气"]
    FB --> F5["绿字点击寻路 + 任务签名/进度键确认"]
    FB --> F6["任务消失确认（默认 2 轮）"]
    F --> G{"本轮完成？"}
    G -- 是 --> G1["done++ → 进度持久化 → record_round"]
    G1 --> G2{"达到次数上限？"}
    G2 -- 是 --> Z["结束返回"]
    G2 -- 否 --> B
    G -- 否 --> B
```

### 4.4 图 4-2：客栈（`kezhan.py`）

```mermaid
flowchart TB
    A["main._run_task_mode(客栈)"] --> A1["team.leave_or_disband_team<br/>单人执行"]
    A1 --> B["run_loop → run_cycle"]
    B --> C{"战斗中？"}
    C -- 是 --> C1["本轮失败，等待"]
    C1 --> B
    C -- 否 --> D{"追踪无任务？"}
    D -- 是 --> D1["sleep 2s 后复核一次"]
    D1 --> E{"复核结果"}
    D -- 否 --> E
    E -- 有任务 --> F["特殊步骤判断"]
    F --> F1{"要求前往大理寺？"}
    F1 -- 是 --> F2["_walk_to_dali<br/>DALI_REDO_INTERVAL 节流"]
    F1 -- 否 --> F3{"要求前往比武场？"}
    F2 --> F3
    F3 -- 是 --> F4["_walk_to_biwu<br/>BIWU_REDO_INTERVAL 节流"]
    F3 -- 否 --> F5["_follow_kezhan"]
    F4 --> F5
    F5 --> FX["follow_task<br/>extra=_handle_follow_extras<br/>green_order / progress_key / green_picker<br/>dialog_tpl_gate=False"]
    FX --> EX1
    FX --> EX2
    FX --> EX3
    FX --> EX4
    FX --> EX5
    FX --> EX6
    FX --> EX7
    FX --> EX8
    FX --> EX9
    FX --> EX10

    subgraph EX["客栈 follow extras 分支"]
        EX1["拍毒虫（药圃除害）<br/>按钮模板命中即进入<br/>帧间差分→15 模板只拍毒虫<br/>签名变化前循环重开"]
        EX2["音律小游戏<br/>入口点击后 armed 15s<br/>横幅模板→_play_yinlv"]
        EX3["大理寺 / 比武场 / 神秘宝箱"]
        EX4["给与面板<br/>物品 / 银两"]
        EX5["你输了/平局<br/>关对话→重点任务绿字"]
        EX6["奇遇对话框<br/>多选项点第一个"]
        EX7["答题<br/>答案匹配 + 重OCR + 2x放大 + 绿字块兜底"]
        EX8["DIALOG_OPTION_MAP 绿字选项"]
        EX9["DIALOG_SKIP_TEXTS 连点跳过"]
        EX10["结束文案<br/>→ DailyLimitError"]
    end

    E -- 无任务 --> G["_ensure_zhanggui"]
    G --> G1{"已在洛阳客栈？"}
    G1 -- 否 --> G2["_go_to_inn<br/>世界图→洛阳→小地图(884,600)"]
    G2 --> G3["快送对话框？<br/>点「快送我去」传送"]
    G2 --> G4["等待到达 / 位置连续稳定 5 次"]
    G3 --> G5{"找到掌柜模板？"}
    G4 --> G5
    G1 -- 是 --> G5
    G5 -- 否 --> G6["Alt+6 + 兜底点击仍无<br/>多次尝试后 → 终止任务"]
    G5 -- 是 --> H["领前复核：任务仍在？"]
    H -- 是 --> F5
    H -- 否 --> I["_claim_task"]
    I --> I1["高性能快速链<br/>接取客栈任务→瑶池选项→校验任务"]
    I1 -- 成功 --> F5
    I1 -- 失败 --> I2["回退 OCR 对话链<br/>对话框区域 OCR + 每日上限检测"]
    I2 --> I3["点「接取客栈任务」<br/>处理两屏任务对话"]
    I3 --> I4["轮询任务进追踪"]
    I4 -- 成功 --> F5
    I4 -- 每日已满 --> Z["结束任务"]

    F5 --> J{"本轮成功？"}
    J -- 是 --> J1["done++ → 进度 → record_round"]
    J1 --> J2{"达到次数上限？"}
    J2 -- 是 --> Z
    J2 -- 否 --> B
    J -- 否 --> J3["记 WARN；终止性失败直接结束"]
    J3 --> B
```

### 4.5 图 4-3：五环（`wuhuan.py`）

```mermaid
flowchart TB
    A["run_loop"] --> A1["先查当天进度上限<br/>达上限直接返回"]
    A1 --> A2{"追踪中已有五环任务？"}
    A2 -- 否 --> P["_prepare 准备阶段"]
    A2 -- 是 --> L
    P --> P1["open_bag + 储物格搜索项链"]
    P1 -- 已有 --> P2["close_bag，跳过购买"]
    P1 -- 没有 --> P3["_buy_equipments"]
    P1 -- 开包失败 --> P3
    P3 --> P4["长安小地图点布店 → 等待到达"]
    P4 --> P5["zuoqi.ensure_landed<br/>降落检查 + 关面板"]
    P5 --> P6["点入口 → 点老板 → 「我想买点东西」"]
    P6 --> P7["项链图标点 2 次 → 点购买"]
    P2 --> L
    P7 --> L["轮次循环"]

    L --> L1{"战斗中？"}
    L1 -- 是 --> L2["sleep 3s，静默等待"]
    L2 --> L
    L1 -- 否 --> L3{"_daily_complete_open<br/>今日全部完成对话框？"}
    L3 -- 是 --> L4["点屏幕中间关闭<br/>done++ → 结束任务"]
    L3 -- 否 --> L5{"追踪已有五环任务？"}
    L5 -- 是 --> F["follow_task"]
    L5 -- 否 --> C["_claim_task"]
    C --> C1["_handle_claim_blockers<br/>活力不足：继续 / 结束<br/>今日两次已满：结束"]
    C1 --> C2["走位到云游大师<br/>点击领取任务按钮"]
    C2 --> C3["高性能：缓存坐标直点领取选项"]
    C3 --> C4["点「我可以帮助这些人」"]
    C4 --> C5["轮询任务进追踪<br/>TrackGate(min_interval=0)"]
    C5 -- 未命中 --> C6["兜底点含五环/帮助的绿字"]
    C6 --> C5
    C5 -- 活力不足 --> C7["按开关继续或结束"]
    C5 -- 今日两次已满 --> C8["结束任务"]
    C5 -- 成功 --> F

    F --> F1{"extras: 完成对话框？"}
    F1 -- 单次恭喜 --> F2["SingleComplete<br/>点屏幕中间 → 计入本轮"]
    F1 -- 每日完成 --> F3["DailyComplete<br/>点屏幕中间 → 计入并结束"]
    F1 -- 开场白 --> F4["点屏幕中间推进"]
    F1 -- 给与面板已开 --> F5["_do_give<br/>找项链→选中→给与"]
    F1 -- 提交装备对话 --> F6["点「刚好我这里有，送你啦」<br/>等给与面板 → _do_give"]
    F6 -- 找不到项链 --> F7["MissingEquipment → 结束"]
    F --> F8{"跟踪成功且见完成信号？"}
    F8 -- 是 --> G["done++ → 进度 → record_round"]
    F8 -- 否 --> F9{"任务消失但未见恭喜？"}
    F9 -- 是 --> F10["_wait_completion_dialog 8s 补查"]
    F10 -- daily --> F3
    F10 -- single --> F2
    F10 -- 无 --> F11["判面板瞬时不可读，不计完成，下轮重试"]
    F9 -- 否 --> F11
    G --> G1{"达到次数上限？"}
    G1 -- 是 --> Z["结束任务"]
    G1 -- 否 --> L
    F11 --> L
```

### 4.6 图 4-4：修罗（`xiuluo.py`）

```mermaid
flowchart TB
    A["run_loop"] --> A1["读取设置<br/>检查队伍 / 自动巫医间隔 / 喊话 / 自动创建"]
    A1 --> A2["启动清场<br/>申请待处理态：清空申请列表"]
    A2 --> A3["自动创建队伍：开关开且「检查队伍」开时<br/>并入首次队伍检查（组号 startup_check /<br/>单号首次领取前检查）；检查队伍关时单独执行<br/>战斗中顺延"]
    A3 --> A4{"当天进度已达上限？"}
    A4 -- 是 --> Z0["结束任务"]
    A4 -- 否 --> A5["tab_group.init_task<br/>组号开关 + 已登录 Tab ≥2"]
    A5 --> A6{"组号激活？"}
    A6 -- 否 --> L
    A6 -- 是 --> A7{"「检查队伍」开关开？"}
    A7 -- 否 --> Z1["停止任务并提示"]
    A7 -- 是 --> A8["startup_check<br/>一次开面板：队长三分类 + 成员昵称"]
    A8 --> A9{"队长判定结果"}
    A9 -- 确定性未组队 --> A10["组号降级关闭<br/>按普通非队长继续"]
    A9 -- 非队长/无法判定 --> Z2["停止任务：请在队长 Tab 启动"]
    A9 -- 队长 --> A11["startup_member_check<br/>队员动作缺失 → 停止任务"]
    A10 --> L
    A11 --> L["轮次循环"]

    L --> L1["ensure_leader_tab 护栏"]
    L1 --> L2{"战斗中？"}
    L2 -- 是 --> L3["等待战斗结束"]
    L2 -- 否 --> L4["申请清场；自动创建仅「检查队伍」关闭时顺延执行"]
    L4 --> L5{"追踪已有修罗任务？"}
    L5 -- 是 --> L6["record_leader_status(True)<br/>_team_checked=True → follow"]
    L5 -- 否 --> L7{"检查队伍开 且 首次未检查？"}
    L7 -- 是 --> L7a{"组号启动检查已读到人数？"}
    L7a -- 是 --> L7b["人数下限达标 → ready 直接领取<br/>不足 → 走位后监控补人<br/>不再为身份重开面板"]
    L7a -- 否 --> L8["读位置/帧判断是否在领取点"]
    L8 -- 不在 --> L9["_check_team_with_retry<br/>自动创建并入首次检查（同一次菜单）<br/>瞬态失败原地重查；确定性非队长 → keep_alive"]
    L8 -- 在 --> L10["跳过身份预检"]
    L7 -- 否 --> L10
    L7b --> L11
    L9 --> L11["_walk_to_claim_spot"]
    L10 --> L11
    L11 --> L12{"检查队伍开 且 首次未检查？"}
    L12 -- 是 --> L13["预检 ready：直接领取<br/>预检 wait：等 10s 进监控<br/>未预检：立即监控"]
    L13 --> L14["_monitor_team_and_fill<br/>人满返回；非队长转 keep_alive"]
    L12 -- 否 --> C
    L14 --> C["_claim_task(allow_fill=check_team)"]
    C --> C1["_click_entry_ensuring_dialog<br/>按钮/NPC 入口；无对话框则小地图重定位重试"]
    C1 --> C2["同帧点「闲来无事，要我帮忙吗」"]
    C2 --> C3{"领取弹窗"}
    C3 -- 不足5人确认 --> C4["拒绝 → 监控补人；开关关则拒绝重试"]
    C3 -- 战斗力异常强大 --> C5["关对话框 → 监控补人"]
    C3 -- 无 --> C6["校验任务进追踪<br/>TrackGate(min_interval=0) + 对话框按需 OCR"]
    C6 -- 2x 重识别命中 --> C7["成功，返回校验帧"]
    C6 -- 未命中 --> C8["小地图重走领取点后再检索一次"]
    C8 -- 仍无 --> C9["失败，下轮重试"]
    C6 --> C7
    C4 --> C7
    C5 --> C7

    L6 --> W
    C7 --> W{"自动巫医轮次？"}
    W -- 是 --> W1["取消阻塞对话框 → _auto_wuyi"]
    W -- 否 --> W2["领取后取消阻塞对话框"]
    W1 --> F
    W2 --> F["follow_task<br/>extra=_handle_fight_dialog<br/>battle_extras_tpl_gate=True"]
    F --> F1["对话开打：点「看打！」<br/>OCR 漏检 → 2x 放大重识别"]
    F1 -- 任务内容中未找到绿色文案<br/>且领取后 30s 内 --> F2["走到领取点补偿重试一次"]
    F --> G{"本轮成功？"}
    G -- 否 --> G1["记 WARN，下一轮"]
    G -- 是 --> G2{"本轮由看打进入战斗？"}
    G2 -- 是 --> G3["返程：开背包任务格→回城符→关面板→走回 NPC"]
    G2 -- 否 --> G4["不返程"]
    G3 --> H["done++ → 进度 → record_round"]
    G4 --> H
    H --> H1{"达到次数上限？"}
    H1 -- 是 --> Z["结束"]
    H1 -- 否 --> L

    L9 -.->|"确定性非队长"| K["_keep_alive 非队长保持模式"]
    K --> K1["battle.wuyi_fast_idle=True"]
    K1 --> K2["每 3s：非战斗且未 pass → maybe_rejoin"]
    K2 --> K3["落点轮回司 → 开面板→点「回归队伍」→校验离开"]
    K3 --> K4["尝试后 30s 放慢重查"]
    K4 --> K2
```

### 4.7 图 4-5：五倍（`wubei.py`）

```mermaid
flowchart TB
    A["run_loop"] --> A1["读取设置<br/>检查队伍 / 自动巫医 / 喊话 / 自动创建 / 奎木狼开关"]
    A1 --> A2["启动清场：申请待处理态 → 清空申请列表"]
    A2 --> A3["自动创建队伍：开关开且「检查队伍」开时<br/>并入首次队伍检查（组号 startup_check /<br/>单号首次领取前检查）；检查队伍关时单独执行<br/>战斗中顺延"]
    A3 --> A4{"当天进度已达上限？"}
    A4 -- 是 --> Z0["结束任务"]
    A4 -- 否 --> A5["tab_group.init_task + 启动检查<br/>同修罗：队长判定 / 成员动作 / 未组队降级"]
    A5 --> L["轮次循环"]

    L --> L1["ensure_leader_tab 护栏"]
    L1 --> L2{"战斗中？"}
    L2 -- 是 --> L3["等待战斗结束"]
    L3 --> L
    L2 -- 否 --> L4["申请清场；自动创建仅「检查队伍」关闭时顺延"]
    L4 --> T1["_track_state 一次 OCR<br/>任务存在？锁妖网？奎木狼？"]
    T1 --> T2{"_task_gone_last_round？"}
    T2 -- 是 --> T3["跳过错检，直接领取"]
    T2 -- 否 --> T4{"需替换？<br/>锁妖网 / 奎木狼开关关 / 显形镜失败"}
    T4 -- 是 --> T5["距上次替换 < 8s 则等待<br/>重新领取；比对任务签名，连续失败 3 次终止"]
    T4 -- 否 --> T6{"任务存在？"}
    T6 -- 是 --> F["直接跟踪<br/>_fight_via_kanda=False"]
    T6 -- 否 --> T3
    T5 --> C
    T3 --> C["领取前队伍预检（开关开且首次）<br/>组号启动检查已读人数 → ready / 走位后补人；<br/>否则不在领取点先判身份<br/>自动创建并入首次检查；瞬态失败原地重查"]
    C --> C1["_walk_to_claim_spot"]
    C1 --> C2["首次：_monitor_team_and_fill<br/>人满才领取；非队长 keep_alive"]
    C2 --> C3["_claim_task(allow_fill)"]
    C3 --> C4["入口识别（按钮/降魔侍卫）<br/>点「除魔卫国，义不容辞」"]
    C4 --> C5{"「前途凶险」队伍不足？"}
    C5 -- 是且开关开 --> C6["关提示 → 监控补人"]
    C5 -- 否 --> C7["校验任务进追踪 + 缓存补点"]
    C6 --> C7
    C7 -- 成功 --> C8["_dismiss_claim_dialog_after_claim"]
    C7 -- 未命中 --> C9["小地图重走后再检索一次"]
    C9 -- 仍无 --> C10["失败，下轮重试"]
    C10 --> L
    C8 --> W{"自动巫医轮次？"}
    W -- 是 --> W1["_auto_wuyi"]
    W -- 否 --> F
    W1 --> F

    F --> FE["follow extras 分支"]
    subgraph EXTRA["五倍 follow extras"]
        E1["追踪含锁妖网 → ReplaceTaskError"]
        E2["奎木狼开关关且含奎木狼 → ReplaceTaskError"]
        E3["显形镜：白龙马对话框<br/>点屏幕中间 → 多候选模板点击<br/>误点阻塞对话框先关闭 → 失败替换"]
        E4["显形镜 A→B 回退<br/>A 用镜未出对话框 → 点链接去 B"]
        E5["对话开打：证明实力 / 消灭它<br/>小字 2x 放大兜底"]
        E6["黄袍怪多杀：战斗结束沿<br/>重点追踪链接 → 下一处"]
    end
    FE --> E1
    FE --> E2
    FE --> E3
    FE --> E4
    FE --> E5
    FE --> E6

    F --> G{"本轮完成？"}
    G -- 否 --> G1["记 WARN，下一轮"]
    G1 --> L
    G -- 是 --> G2{"黄袍怪轮次且任务仍在？"}
    G2 -- 是 --> G3["4s 复查，仍在 → 跳过返程继续跟踪"]
    G2 -- 否 --> G4{"本轮由看打进入战斗？"}
    G4 -- 是 --> G5["返程：开背包任务格→五倍回城符→关面板→走回 NPC"]
    G4 -- 否 --> G6["不返程"]
    G5 --> H["done++ → 进度 → record_round"]
    G6 --> H
    H --> H1{"达到次数上限？"}
    H1 -- 是 --> Z["结束"]
    H1 -- 否 --> L
    G3 --> L
```

> 五倍与修罗共享 `_keep_alive`（非队长保持模式 + 自动归队）、`_monitor_team_and_fill`、`_claim_task` 的领取校验骨架、返程链路（`_open_task_box → _use_return_talisman → _close_return_panels → _return_to_npc`）与自动巫医链。

### 4.8 图 4-6：仅战斗（`main._battle_only_loop`）

```mermaid
flowchart TB
    A["main._battle_only_loop"] --> A1["debug_overlay.start + 准备中"]
    A1 --> A2["read_login_tab"]
    A2 --> A3{"已登录？"}
    A3 -- 否 --> Z["任务结束"]
    A3 -- 是 --> A4["读取仅战斗自动归队开关（默认开）"]
    A4 --> A5["启动 battle.watch_loop 线程"]
    A5 --> L["驻留循环：sleep 1s"]
    L --> L1{"战斗线程仍存活？"}
    L1 -- 否 --> Z2["日志：战斗监控线程退出，任务结束"]
    L1 -- 是 --> L2{"自动归队开 且 非 pass？"}
    L2 -- 否 --> L
    L2 -- 是 --> L3{"距下次归队检查到期？"}
    L3 -- 否 --> L
    L3 -- 是 --> L4{"战斗中？"}
    L4 -- 是 --> L
    L4 -- 否 --> L5["team.maybe_rejoin"]
    L5 --> L6["已尝试 → 30s 放慢；未处理 → 3s 节奏"]
    L6 --> L
```

---

## 5. 共享任务跟踪引擎 `follow_task`（`xuanshu.py`）

五环 / 客栈 / 修罗 / 五倍 / 玄枢共用同一套跟踪状态机。理解它是理解全部任务分支的前提。

```mermaid
flowchart TB
    O0["deadline = now + timeout"] --> O1{"tab_group.pass_active？"}
    O1 -- 是 --> O1a["sleep，跳过本轮"]
    O1a -.-> O1
    O1 -- 否 --> O2{"组号激活 且 未回到队长 Tab？"}
    O2 -- 是 --> O2a["sleep，跳过本轮"]
    O2a -.-> O1
    O2 -- 否 --> O3{"is_in_battle？"}
    O3 -- 是 --> O3a["battle_since 记录，sleep 等待"]
    O3a -.-> O1
    O3 -- 否 --> O3b["deadline += 战斗耗时<br/>（战斗不计跟踪超时）"]
    O3b --> O4["取帧：last_items → first_items → capture + ocr_follow"]
    O4 --> O5{"find_task 命中？"}
    O5 -- 否 --> O6{"_confirm_task_gone<br/>连续 polls 轮未见？"}
    O6 -- 否 --> O1
    O6 -- 是 --> O7{"_task_gone_dialog_guard<br/>有阻塞对话框？"}
    O7 -- 是 --> O7a["先处理对话框，下轮再确认"]
    O7 -- 否 --> O8{"跟踪期间任务曾出现？"}
    O8 -- 否 --> O8a["返回失败：任务从未出现<br/>（领取可能失败）"]
    O8 -- 是 --> O8b["返回成功：任务已从追踪消失"]
    O5 -- 是 --> O9["task_seen=True，计算签名 sig"]
    O9 --> O10{"sig 为空？"}
    O10 -- 是 --> O10a["extras 处理 + 退避至 1.5s<br/>（链接行未渲染的过渡帧）"]
    O10a -.-> O1
    O10 -- 否 --> O11{"sig 与上次点击签名相似<br/>且未 progressed？"}
    O11 -- 是 --> O11a["extras 处理（阻塞期对话）<br/>不重复点击"]
    O11a -.-> O1
    O11 -- 否 --> O12["pre_click_handler<br/>（可抛异常终止跟踪）"]
    O12 --> O13["progress_key 基线（点击前捕获）"]
    O13 --> O14["track_task 点击任务绿字/链接"]
    O14 --> O15["mark_track_walk → 寻路期摄妖香<br/>post_track_handler（组号血蓝 pass）"]
    O15 --> IENTRY["内层等待循环"]

    subgraph INNER["内层等待：任务信息变化 / 任务消失"]
        IENTRY --> IENTER["sleep：CONFIRM 0.2s 或 FOLLOW 0.5s"] --> I1{"pass_active？"}
        I1 -- 是 --> IENTER
        I1 -- 否 --> I2["capture(force_activate=False)"]
        I2 --> I3{"is_in_battle_frame？"}
        I3 -- 是 --> I3a["battle_since；确认期 sleep 2s<br/>非确认期：限频 extras（2s）<br/>可选模板门控跳过对话框 OCR"]
        I3a --> IENTER
        I3 -- 否 --> I3b["首个非战斗帧：deadline += 战斗耗时"]
        I3b --> I4{"changed_once？<br/>（已见一次变化）"}
        I4 -- 是 --> I5["确认轮询：只 OCR 追踪面板<br/>TrackGate.ocr + paste_track"]
        I5 --> I6{"任务消失？"}
        I6 -- 是 --> I6a["消失确认 + 对话框守卫"]
        I6a -.->|"未确认或对话框在场"| IENTER
        I6a -.->|"确认消失"| O8b
        I6 -- 否 --> I7{"同一信号确认？<br/>文本：新签名与首次变化相似<br/>进度键：与首次变化相等"}
        I7 -- 是 --> I7a["对话框守卫 → progressed=True<br/>break 回外层点击下一个绿字"]
        I7a -.->|"回外层"| O1
        I7 -- 否 --> I4a["changed_once=False，回正常轮询"]
        I4a --> IENTER
        I4 -- 否 --> I8{"上一轮是战斗帧？"}
        I8 -- 是 --> I8a["转换帧：先做任务消失确认"]
        I8a --> IENTER
        I8 -- 否 --> I9["帧全等复用 → 否则 TrackGate 面板 OCR<br/>+ 对话框区域 OCR（模板门控可选）"]
        I9 --> I10["extras handler 优先"]
        I10 -- 已处理 --> IENTER
        I10 -- 未处理 --> I11{"封印机关提示对话框？"}
        I11 -- 是 --> I11a["完整处理"] --> IENTER
        I11 -- 否 --> I12{"锄遗聚宝模板？<br/>仅玄枢开启"}
        I12 -- 是 --> I12a["完整处理"] --> IENTER
        I12 -- 否 --> I13{"find_task 命中？"}
        I13 -- 否 --> I6
        I13 -- 是 --> I14["文本签名 / 进度键是否变化？"]
        I14 -- 是 --> I15["连续两轮确认同一信号 → break"]
        I15 -.->|"回外层"| O1
        I14 -- 否 --> I16["DIALOG_OPTION_MAP 绿字 → 序号兜底"]
        I16 --> I17["「你输了/平局」→ 重点任务绿字"]
        I17 --> I18["DIALOG_SKIP_TEXTS 连点屏幕中间"]
        I18 --> I19["DIALOG_OPTIONS 已知绿字兜底"]
        I19 --> IENTER
    end

    IENTER -.->|"超时"| T0["返回失败：本轮跟踪后信息无变化"]
```

### 5.1 判定优先级与关键设计

| 优先级 | 判定 | 原因 |
|---|---|---|
| 1 | `tab_group.pass_active` 直接跳过 | 屏幕可能是队员 Tab，任何识别/点击都会作用到错误账号 |
| 2 | 组号队长 Tab 护栏 | pass 切回失败残留时不能点寻路链接 |
| 3 | `is_in_battle_frame` 前置 | 战斗 UI 遮挡追踪面板，且动画帧帧不同，门控/帧复用全部失效 |
| 4 | `extras` 优先于内置对话表 | 防止同一帧「extras 已点开打选项，内置表又点一次」的双击 |
| 5 | 任务消失确认（默认 2 轮） | 面板半透明渲染/瞬时漏读会误判完成；五倍非黄袍怪轮次可降为 1 轮；追踪面板缺席按任务结束判定（游戏无追踪任务时自动收起），判定帧查遮挡负条件（背包/地图/队伍）——标准对话框不挡追踪面板、不在遮挡名单（用户 2026-11-12 确认，完成瞬间的收尾剧情对话不阻塞判定）；战斗结束返程路径跳过遮挡检查 |
| 6 | 文本签名 + 进度键双信号 | 文字不变仅颜色变化（封印进度）也要识别为推进；单信号抖动被两轮确认过滤 |
| 7 | 变化确认同一信号 | 防止「文本抖一次 + 进度键抖一次」组合骗过确认、重复点击 |
| 8 | 寻路期摄妖香 + 组号血蓝 pass | 点击追踪链接后的寻路窗口无弹框遮挡，是背包交互/切 Tab 的安全窗口 |

**超时规则**：`FOLLOW_TIMEOUT=300s`，但战斗中时间不计入——进入战斗记 `battle_since`，首个非战斗帧把战斗耗时加回 `deadline`。这是黄袍怪多杀任务单轮含 4~5 场战斗仍能完成的必要条件。

---

## 6. 战斗监控线程（`battle.watch_loop`）

战斗线程与任务线程并行，负责：按场次调度战斗操作、战后补血蓝、巫医对话框、修罗盒子、组号队员 pass。

```mermaid
flowchart TB
    A["watch_loop 启动"] --> L["每轮：sleep 0.5s"]
    L --> L0{"tab_group.pass_active？"}
    L0 -- 是 --> L
    L0 -- 否 --> L1["capture(force_activate=False)<br/>PIL 帧 → BGR"]
    L1 --> L2["is_in_battle_frame(bgr)"]
    L2 --> L3{"in_battle 且 进场 ≥10s？"}
    L3 -- 是 --> L4["read_map_position(img=同帧)<br/>坐标可读 = 战斗实质结束证据"]
    L3 -- 否 --> L5["坐标不可读"]
    L4 --> L6["_end_poll_advance<br/>坐标路径：连续 2 轮确认<br/>模板路径：连续 4 轮确认"]
    L5 --> L6

    L6 --> B1{"战斗帧？"}
    B1 -- 是 --> B2{"已确认在战斗？"}
    B2 -- 否 --> B3["连续 2 轮确认进入"]
    B3 --> B4["battle_no++<br/>悬浮层/看板写阶段"]
    B4 --> B5{"第 1 场？"}
    B5 -- 是 --> B6["handle_battle：<br/>取消自动→第1回合判定→人物动作<br/>→召唤兽动作→点自动"]
    B6 --> B6a["组号：队长挂自动后<br/>队员 Tab 执行配置动作"]
    B5 -- 否 --> B7["_click_famen（人物法术时）"]
    B7 --> B8{"每 5 场？"}
    B8 -- 是 --> B9["_click_auto_once + 组号队员点自动"]
    B8 -- 否 --> B10{"首场未挂上自动？"}
    B10 -- 是 --> B9
    B10 -- 否 --> B11["不操作，游戏自动战斗"]
    B2 -- 是 --> B11
    B1 -- 否 --> NB1["in_polls=0 / pos_hold=False"]

    L6 --> E1{"结束确认？"}
    E1 -- 是 --> E2["in_battle=False<br/>pos_hold=True（防胜利残留横幅误判新战斗）"]
    E2 --> E3["清空看板阶段"]
    E3 --> E4{"结束帧是场景帧<br/>且自动补血蓝开？"}
    E4 -- 是 --> E5["status_bars.check_all_bars"]
    E4 -- 否 --> E6["bars_pending=True<br/>首个场景帧再检查"]
    E2 --> E7["重置巫医/盒子检查节流"]

    NB1 --> N1["非战斗处理"]
    E7 --> N1
    N1 --> N2{"bars_pending 且场景帧？"}
    N2 -- 是 --> N3["补血蓝检查"]
    N2 -- 否 --> N4["巫医检查节奏判定"]
    N3 --> N4
    N4 --> N5{"距上次巫医检查 ≥ 节奏？"}
    N5 -- 是 --> N6["_handle_wuyi_dialog<br/>绿字选项 + 缓存坐标 + OCR 降级"]
    N5 -- 否 --> N7["修罗盒子检查限频"]
    N6 --> N7
    N7 --> N8{"距上次盒子检查 ≥ 间隔？"}
    N8 -- 是 --> N9["_handle_hezi<br/>队长规则：team.known_leader()"]
    N8 -- 否 --> L
    N9 --> L
```

### 6.1 场次调度规则

| 场次 | 行为 |
|---|---|
| 第 1 场 | `handle_battle`：取消自动 → 判定第 1 回合 → 人物动作 → 召唤兽动作 → 点自动；组号队员按账号配置执行一次 |
| 第 6/11/16…（每 5 场） | 进场后点一次自动按钮；组号队员只点自动 |
| 其余场次 | 不操作，依赖自动战斗跨场持续 |
| 首场未挂上自动 | `pending_auto=True`，下一场进场补点一次 |
| 每场第 1 回合 | 自动使用法门（仅人物动作为法术时） |

### 6.2 战斗结束与巫医

| 机制 | 说明 |
|---|---|
| 进入确认 | 连续 2 轮战斗帧（`BATTLE_CONFIRM_POLLS=2`） |
| 模板结束确认 | 连续 4 轮非战斗帧（`0.5s × 4 = 2s`），防回合间隙误判 |
| 坐标快速结束 | 进场 ≥10s 后，地图坐标可读连续 2 轮即确认结束（覆盖胜利画面残留横幅仍匹配战斗模板的设备形态） |
| 巫医节奏 | 战后高发窗口 2s；主流程点 NPC 后预期窗口 2s；窗口外 10s 兜底；空闲场景连续空检后降级 4s |
| 巫医选项缓存 | 按（模式 + 文案）分键，禁止跨弹窗复用；点击成功即缓存坐标 |
| 盒子（修罗） | 独立限频检查，仅队长身份参与 |

---

## 7. 队伍与组号子系统

### 7.1 图 7-1：队伍检查 / 补人 / 暂离请离 / 归队（`team.py`）

```mermaid
flowchart TB
    A["check_team_min"] --> A1["面板未开：先截帧查申请人证据"]
    A1 --> A2{"申请加入面板已开<br/>或组队按钮为 zudui_item2？"}
    A2 -- 是 --> A3["确定性队长 → 返回 wait（人数待补）"]
    A2 -- 否 --> A4["open_team_panel：组队按钮→菜单→队伍信息"]
    A4 -- "未组队且 create_if_missing" --> A4b["同一次菜单打开内点「创建队伍」<br/>面板打开即创建成功"]
    A4b --> A7
    A4 -- 未组队 --> A5["返回 not_leader（确定性状态，不重试）"]
    A4 -- 菜单打不开 --> A6["重截 2 帧复查申请人证据<br/>仍无 → 队长无法判定（瞬态）"]
    A4 -- 面板打开 --> A7["截帧：双模板比分数判队长<br/>解散 vs 离开，各达标且更高者胜"]
    A7 --> A8{"按钮未命中 unclear？"}
    A8 -- 是 --> A9["重截复核，仍 unclear → 队长无法判定<br/>（不得单帧判非队长）"]
    A8 -- 否 --> A10{"队长？"}
    A10 -- 否 --> A11["采集成员信息 → 返回 not_leader"]
    A10 -- 是 --> A12["同帧 5 固定区域成员扫描<br/>空位模板截断、种族/等级 OCR"]
    A12 --> A13{"allow_kick？"}
    A13 -- 是 --> A14["_auto_kick_overdue<br/>暂离超 5 分钟从后往前连踢<br/>踢后重扫人数"]
    A13 -- 否 --> A15["人数下限检查"]
    A14 --> A15
    A14 -.->|"调用"| D
    A15 -- 不足 --> A16["返回 wait + 缺口人数"]
    A15 -- 满足 --> A17["返回 ready"]

    B["_monitor_team_and_fill"] --> B1["每轮先查申请人证据"]
    B1 -- 有申请人 --> B2["fill_team：按最近缺口一次补满"]
    B2 --> B1
    B1 -- 无 --> B3["_check_team_with_retry"]
    B3 -- ready --> B4["返回，调用方领取"]
    B3 -- wait 缺人 --> B5{"复查到申请人？"}
    B5 -- 是 --> B2
    B5 -- 否 --> B6["缺人喊话（距上次 ≥30s）<br/>10s 后重查（识别失败 3s）"]
    B6 --> B1
    B3 -- not_leader 确定性 --> B7["_keep_alive 保持模式"]
    B3 -- not_leader 瞬态 --> B8["按间隔持续重查，不退出监控"]
    B3 -.->|"调用"| A
    B2 -.->|"调用"| C
    B7 -.->|"调用"| E

    C["fill_team"] --> C1["确保申请加入面板打开<br/>zudui_item2 命中即记录队长"]
    C1 --> C2["OCR 申请人行 → 选第一个未失败者"]
    C2 --> C3["点行 → 点「允许」zudui_yunxu"]
    C3 --> C4{"「对方已经加入其他队伍」？"}
    C4 -- 是 --> C5["记失败，换下一个"]
    C4 -- 否 --> C6["校验该行从列表消失"]
    C6 -- 仍在 --> C5
    C6 -- 已消失 --> C7["allowed++"]
    C7 --> C2
    C2 -- 补满/耗尽 --> C8["点「清空」zudui_qingkong"]
    C8 --> C9["关闭申请面板"]

    D["scan_away_members"] --> D1["逐成员区域找「召回」标记"]
    D1 --> D2["OCR 昵称 + 右缘竖排种族<br/>名字模糊 ≥0.7 + 种族精确 = 复合身份"]
    D2 --> D3["首次识别暂离起计时"]
    D3 --> D4{"超过 5 分钟仍暂离？"}
    D4 -- 是 --> D5["从后往前连踢<br/>点成员卡→请离队伍"]
    D4 -- 否 --> D6["下轮检查继续"]

    E["maybe_rejoin"] --> E1["连续 2 轮位置 OCR 确认轮回司"]
    E1 -- 否 --> E2["不在轮回司，本轮无需归队"]
    E1 -- 是 --> E3["open_team_panel"]
    E3 --> E4{"身份"}
    E4 -- 队长 --> E5["跳过（队长无归队按钮）"]
    E4 -- 无法判定 --> E6["不误点，下轮重查"]
    E4 -- 队员 --> E7["匹配 zudui_guidui 阈值 0.9"]
    E7 --> E8["点击「回归队伍」"]
    E8 --> E9["轮询位置离开轮回司（≤8s）"]
    E9 -- 离开 --> E10["已归队"]
    E9 -- 仍在 --> E11["未确认，稍后重试"]
```

### 7.2 图 7-2：组号多 Tab（`tab_group.py`）

```mermaid
flowchart TB
    A["init_task(mode, switch_on)"] --> A1{"mode ∈ 修罗/五倍<br/>且 开关开？"}
    A1 -- 否 --> Z["不激活，单号行为"]
    A1 -- 是 --> A2["game.list_tab_windows<br/>EnumChildWindows 枚举子窗口"]
    A2 --> A3{"已登录 Tab ≥2？"}
    A3 -- 否 --> Z
    A3 -- 是 --> A4["可见已登录 Tab = 队长候选<br/>解析账号 ID / 角色名"]
    A4 --> A5{"当前 Tab 身份可解析？"}
    A5 -- 否 --> Z
    A5 -- 是 --> A6["_active=True<br/>记录队长候选 ID"]

    A6 --> S["startup_check"]
    S --> S1["战斗中先等结束（≤90s）"]
    S1 --> S2["clear_pending_apply 启动清场"]
    S2 --> S3["classify_leader_with_panel<br/>双模板 + 复核<br/>create_if_missing：未组队同菜单内创建"]
    S3 --> S4{"unclear？"}
    S4 -- 是 --> S5["3s 后同一面板重判一次"]
    S4 -- 否 --> S6{"队长？"}
    S5 --> S6
    S6 -- 否 --> S7["未组队 → deactivate_group<br/>按普通非队长继续<br/>其它 → 停止任务"]
    S6 -- 是 --> S7a["同面板读成员昵称<br/>漏检区域补读一帧"]
    S7a --> S8["close_team_panel"]

    S8 --> S8a["startup_team_count：昵称可读数=人数下限<br/>达标时任务侧免首次领取前队伍检查"]
    S8a --> M["startup_member_check"]
    M --> M1["枚举 Tab + 面板昵称模糊匹配"]
    M1 --> M2["确认在队伍中的队员 Tab 集合<br/>排除队伍里的外人"]
    M2 --> M3{"确认队员已设置战斗动作？"}
    M3 -- 否 --> M4["缺失 → 停止任务并提示"]
    M3 -- 是 --> M5["_team_member_ids 写定<br/>group_detected 写面板显隐"]
    M2 -- 面板昵称读不到 --> M6["宽松放行：未确认 Tab 只点自动"]

    M5 --> R["运行时 pass"]
    R --> R1["_try_enter_pass 互斥<br/>同一时刻至多一个 pass"]
    R1 --> R2["按账号 ID 遍历确认队员 Tab"]
    R2 --> R3{"切换通道"}
    R3 -- 游戏前台 --> R4["Ctrl+Tab 物理按键"]
    R3 -- 后台/静默 --> R5["Tab 条 UIA 矩形 + 消息点击"]
    R4 --> R6["执行队员操作"]
    R5 --> R6
    R6 --> R7["切回队长 Tab（_for_each_member 收尾）"]
    R7 --> R8["_exit_pass 清抑制标志"]

    R2 --> P1["member_auto_pass<br/>首场执行配置动作 / 每5场点自动"]
    R2 --> P2["member_wuyi_pass<br/>队长医治后逐个点队员对话框"]
    R2 --> P3["member_bars_pass<br/>寻路期切 Tab 检查血蓝（60s 节流）"]
    R2 --> P4["member_rejoin_pass<br/>暂离昵称匹配队员 Tab → 替其归队"]
```

**组号结论原则**：多个 Tab 都登录 **不一定**是组号。只有「队伍检查 + Tab 标题角色名比对」确认**在队伍中**的 Tab 才算组员；不在队伍/未确认的 Tab 完全不碰。确定性未组队时降级关闭组号、按普通非队长继续，不停止任务。

---

## 8. 感知与执行基础设施

### 8.1 感知管线

```mermaid
flowchart LR
    CAP["win.capture(force_activate=False)<br/>PIL 帧 / OPENCV 帧"] --> P1["PIL(RGB) 通道"]
    CAP --> P2["BGR 通道"]

    P1 --> O1["game_map.ocr_regions<br/>追踪面板 + 对话框区域"]
    P1 --> O2["ocr_track / ocr_follow<br/>追踪面板"]
    P1 --> O3["ocr_image 全帧<br/>仅特殊场景"]
    P1 --> O4["绿字判定 is_green_text<br/>green_text_center / 下划线"]
    P1 --> O5["read_map_position<br/>地图坐标区域 OCR"]

    P2 --> T1["match_frame_np / best_match_np<br/>模板匹配（自动缓存模板）"]
    P2 --> T2["match_frame_multi<br/>双模板比分数"]
    P2 --> T3["is_in_battle_frame<br/>4 模板 + ROI 条带 + 限频兜底"]
    P2 --> T4["status_bars 血蓝条像素扫描"]

    O1 --> G["TrackGate<br/>节奏上限 → 像素相等 → 全量 OCR"]
    O2 --> G
    G --> DEC["任务决策"]
    T1 --> DEC
    T2 --> DEC
    T3 --> DEC
    T4 --> DEC
    O4 --> DEC
    O5 --> DEC
    DEC --> ACT["执行层"]
```

### 8.2 OCR 引擎

| 引擎 | 用途 | 参数特征 |
|---|---|---|
| 全帧引擎 `_engine` | 极少数全帧识别、任务块 2x 重识别 | `min/736`，`use_angle_cls=False` |
| 区域引擎 `_region_engine` | 追踪面板 / 对话框区域（热路径） | `max/736`，`use_angle_cls=False` |
| 2x 放大引擎 `_zoom_engine` | 对话框区 2x 裁剪（巫医/开打/答题小字） | `max/1600`，仅限放大后最长边 ≤1600 |
| 推理线程 | 全部引擎 `intra=2 / inter=1` | 禁止改回默认全核（CPU -63%，wall 持平） |

### 8.3 模板匹配与 ROI

- 模板文件：`res/img/*.png`，`map._load_template` 灰度缓存（mtime 失效）。
- 快速接口：`match_frame_np` / `best_match_np` / `match_frame_multi`（BGR 帧、阈值默认 0.8、返回匹配中心）。
- 新增检测点必须：
  1. 从真实截图标定 ROI 条带（≥50px 余量）；
  2. 带区全未命中才全帧兜底；
  3. 全帧兜底必须限频（参考 `FULL_CHECK_INTERVAL=3s`）；
  4. 相似模板互匹配必须**双模板比分数**，各自达标且更高者胜。

### 8.4 输入通道

| 操作 | 静默模式开（默认） | 静默模式关 |
|---|---|---|
| 鼠标点击 | `silent_click.send_silent_click` → 向可见 Tab 子窗口投递 `WM_MOUSEMOVE/WM_LBUTTONDOWN/UP`（或右键） | `Mouse` 物理移动 + 点击（拟人化 ±2px） |
| 鼠标移动 | 空操作（无物理悬停态） | `Mouse.mouse_move`，40~90ms |
| Alt 组合键 | `WM_SYSKEYDOWN/UP`（无需前台） | `Keyboard.key_hot`（只进前台窗口） |
| 文本输入 | `WM_CHAR` 逐字符注入 + `WM_CHAR` 回车发送 | `Keyboard.input`（中文走剪贴板粘贴）+ Enter |
| Tab 切换 | 前台：Ctrl+Tab；后台：Tab 条 UIA 矩形 + 消息点击 | Ctrl+Tab |
| 全局互斥 | 所有点击/按键序列持 `app_utils._INPUT_LOCK`，移动+点击原子 | 同左 |

**禁区**：Tab 条不要投递带指针的消息（`TCM_GETITEMRECT` 等跨进程不封送，曾导致客户端崩溃）；只使用 int-only 的 TCM 消息。

### 8.5 关键性能约定（热路径）

| 约定 | 说明 |
|---|---|
| 每轮询只截一帧 | 同帧复用：`is_in_battle_frame` / `match_frame_np` / `read_map_position(img=)`；禁止二次截窗 |
| 帧格式分工 | 模板匹配用 BGR；OCR / 绿字判定用 PIL(RGB)，BGR 会导致绿字颜色判定反转 |
| 轮询截图 `force_activate=False` | 禁止抢前台焦点 |
| TrackGate 三态门 | 节奏上限 → 像素相等 → 全量 OCR；`img_np` 必须 `paste_track` 当前帧裁剪 |
| 领取校验 `TrackGate(min_interval=0.0)` | 短窗口关键检测不限频；限频会把「未出现」结论复用 2s，拖慢领取 3-4s |
| 固定 sleep 改轮询目标状态 | 如等待「自动按钮出现」= 手动状态恢复，固定时延只作超时上限 |
| 批量检测共用一帧 | 多血蓝、多模板、取消自动 + 回合判定 + 按钮定位 |

### 8.6 高性能模式（固定位置缓存）

| 项 | 行为 |
|---|---|
| 开关置位 | 任务子进程 `app_utils.set_high_perf(load_high_perf())`；主进程验证按钮恒走识别路径 |
| 缓存键 | `fixed_pos_get/set/clear(key, win)`，按固定键持久化到当前角色 `data/<角色ID>/high_perf_cache.json`；读取时校验窗口尺寸，缩放/换窗口自动失效 |
| 点击流程 | `high_perf_click`：缓存命中 → 直点；未命中 → 识别点击成功后写缓存；点击未生效 → 清缓存回退识别 |
| 适用对象 | **只缓存固定 UI**（地图按钮、背包按钮、对话框选项、领取选项等）；识图定位的 NPC / 领取按钮随站位变化，禁止缓存 |
| 背包捷径 | 高性能模式走 Alt+E 开包，与按钮位置漂移无关 |

---

## 9. 数据持久化与可观测性

| 文件 | 写入方 | 内容 | 读取方 |
|---|---|---|---|
| `config.json` | 主进程控制窗口 / 任务子进程（KeyValue 合并写） | 软件全局设置（主题/授权/更新/调试/老板键）、窗口绑定；任务设置清理后不再存放 | 两侧 |
| `data/<角色ID>/settings.json` | 主进程控制窗口 `role_settings` | 该角色当前生效设置（任务选择/次数/阈值/战斗动作/各开关） | 两侧（任务子进程只读） |
| `data/<角色ID>/schemes.json` | 主进程 `schemes.py` | 该角色多套配置快照 + 当前方案 | 主进程 |
| `data/<角色ID>/task_stats.json` | 任务子进程 `debug_overlay` | 轮次 / 战斗 / 耗时；累计与最近 90 天日档 | 主进程成就页 |
| `data/<角色ID>/state.json` | 两侧 `app_utils.role_state_*`（读-改-写原子替换） | 任务进度、摄妖香时间戳、清屏标记、名望种族、组号状态 | 两侧 |
| `data/<角色ID>/high_perf_cache.json` | 任务子进程 `app_utils` | 高性能固定位置坐标缓存 | 任务子进程 |
| `data/_meta.json` | 主进程 `user_data` | layout=2、迁移信息、known_roles、last_active_role、settings_migrated/settings_pruned、stats_imported、旧 config 字段清理标记 | 两侧 |
| `data/_legacy_config.json` | 主进程 `user_data` | 第二次启动从 config.json 清理出的旧角色字段备份（手动回滚恢复用） | — |
| `data/_legacy/` | 主进程 `user_data` | 根目录旧版 schemes.json / task_stats.json 归档（回滚旧版本用） | — |
| `debug_overlay_state.json` | 任务子进程监督线程（原子写） | 悬浮层文案 + 运行看板结构化 status + 心跳 | 悬浮层子进程、主进程看板 |
| `logs/日期.log` | 两侧 `app_utils.log` | 双写日志（控制台 + 文件） | 人工排障 |

**KeyValue 注意**：`_load_data` 不刷新内存快照；任务子进程写配置必须走 `kv_save_merged` / `kv_update_merged` / `kv_remove_merged`，否则会覆盖主进程写入的运行时状态。

**角色数据注意**：`data/` 下的设置/方案/成就/进度/高性能缓存按角色隔离；角色在启动阶段确定（`user_data.current_role_id`：实时窗口标题 → 绑定记录 → `_default`），切换账号需重启。`config.json` 在两阶段清理后只保留软件全局设置。旧版数据迁移与回滚规则见 docs/user-data-layout.md。

---

## 10. 诊断与验证体系（主进程内）

| 分组 | 按钮 / 链路 | 说明 |
|---|---|---|
| 真实链路验证 | 检查队伍信息、创建队伍、暂离检查、请离暂离成员、组号归队、测试归队 | 会真实点击、开面板、踢人；部分要求任务未运行 |
| 领取/返程验证 | 修罗领取/回程/巫医、修罗组号巫医、静默修罗领取/巫医/回程、五倍领取/回程/巫医、白龙马模板、五环进店/领取 | 到关键节点停止，不改变任务状态；对话框停留需手动关闭 |
| 只读测试 | 获取当前坐标、机关模板测试、测试申请加入、截屏通道诊断、子窗口枚举 | 只读不点击；用于模板/阈值/环境校准 |
| 输入通道 | 静默点击、静默回蓝/回血、静默按键 Alt+C/E/5/6/Ctrl+Tab/Enter、静默粘贴/Unicode/字符 | 验证后台消息通道是否被游戏消费 |
| 通用 | 清屏、喊话测试、飞行驭器开/关/飞/降 | 复用任务同款链路 |

**绑定 Tab 护栏**：诊断按钮执行前校验当前 Tab 是否为绑定角色；被手动切走时先切回，失败放弃本轮，防操作错账号。

---

## 11. 关键设计约束（改动前必读）

1. **杀进程停止**：任务循环没有协作式停止标志，任何长循环都靠 TaskManager 强杀；不要在任务线程里假设能优雅退出。
2. **每轮询一帧 + ROI 条带 + 兜底限频**：新增检测点先实测截图/匹配/OCR 分项耗时，再决定条带与限频参数。
3. **帧格式不可混用**：OCR / 绿字永远 PIL(RGB)；模板热路径 BGR。`is_in_battle_frame` 入口兼容 PIL 输入。
4. **双模板比分数**：相似模板互匹配（队伍解散/离开、飞行/降落、第1/第2回合、选中/未选中背包格）禁止单模板阈值判定。
5. **关键时序常量是实测校准资产**：进场确认 2 轮、模板退出确认 4 轮（2s）、坐标退出确认 2 轮、`BATTLE_EXTRAS_INTERVAL=2s`、`TRACK_GATE_MIN_INTERVAL=2s`、`TRACK_GATE_MAX_SKIP=5s`、`FOLLOW_TIMEOUT=300s`（战斗不计）、`REPLACE_INTERVAL=8s`、`REPLACE_FAIL_LIMIT=3`。不得凭直觉缩短/放宽。
6. **跟随超时不消耗战斗时间**：`battle_since` 顺延 deadline；否则黄袍怪多杀会死于战斗中途并丢失完成信号。
7. **任务消失确认轮数**：五倍黄袍怪轮次必须 2 轮；非黄袍怪 1 轮是性能优化，由主循环 `has_task` 复查兜底。
7a. **任务消失确认的面板门**：`_confirm_task_gone(require_panel=)` 默认要求追踪面板标题条在场（防手关面板误判完成，2026-11-11 修罗教训）；战斗结束路径与客栈传 `task_gone_require_panel=False`——游戏无追踪任务时自动收起面板，客栈交付完成瞬间面板即消失，若仍要求在场会空转到 `FOLLOW_TIMEOUT` 才去领取（2026-11-11 16:29/16:33 实测各卡 2~3 分钟）。
8. **领取校验不限频**：`TrackGate(min_interval=0.0)` 用于领取校验；follow 主循环用默认 2s 节奏门。
9. **巫医选项缓存按弹窗分键**：`(模式, 文案)`；禁止跨弹窗复用坐标。
10. **组号 pass 互斥**：同一时刻至多一个 pass；`pass_active` 期间战斗线程与任务线程全部跳过屏幕处理。
11. **队长判定三分类**：`leader / member / unclear`；`unclear` 必须复核，不得单帧判非队长并停任务。
12. **物理输入全局互斥**：所有物理鼠标/键盘序列持 `app_utils._INPUT_LOCK`，移动 + 点击原子，防战斗线程与任务线程交错。
13. **静默模式键盘限制**：键盘消息只进前台窗口；静默模式下非前台时 `keyboard_ok()` 返回 False，流程应跳过而非强行激活。
14. **配置合并写**：任务子进程写 `config.json` 必须 `kv_*_merged`，否则覆盖主进程运行时状态。

---

## 12. 模块索引

| 文件 | 职责 | 关键入口 |
|---|---|---|
| `main.py` | 进程入口、授权门、绑定、模式分发、仅战斗、停止善后 | `task_start`、`_task_start_body`、`_run_task_mode`、`_battle_only_loop`、`task_stop` |
| `src/task_manager.py` | 任务子进程生命周期、日志回流、窗口看护 | `TaskManager.start/stop/is_running`、`_worker_wrapper`、`_window_watchdog` |
| `src/control_window.py` | 控制面板、配置读写、诊断/验证按钮、看板 | `ControlWindow`、`ApiBridge`、`start_task`、`verify_*` |
| `src/wubei.py` | 五倍任务 | `run_loop`、`_claim_task`、`_handle_follow_extras`、`_return_to_npc` |
| `src/xiuluo.py` | 修罗任务 | `run_loop`、`_claim_task`、`_keep_alive`、`_return_to_npc` |
| `src/zhongzu.py` | 种族比武任务（队伍 5 人；巫医链路待实现） | `run_loop`、`_claim_task`、`_handle_task_dialog`、`_keep_alive`、`_return_to_npc` |
| `src/kezhan.py` | 客栈任务 | `run_loop`、`run_cycle`、`_claim_task`、`_handle_follow_extras` |
| `src/wuhuan.py` | 五环任务 | `run_loop`、`_prepare`、`_claim_task`、`_handle_submit_dialog` |
| `src/xuanshu.py` | 玄枢轶闻 + 共享 `follow_task` | `run_loop`、`run_cycle`、`follow_task`、`navigate_to_task` |
| `src/battle.py` | 战斗监控、血蓝、巫医、盒子、组号队员 pass | `watch_loop`、`handle_battle`、`_handle_wuyi_dialog`、`_handle_hezi` |
| `src/team.py` | 队伍检查、补人、暂离请离、归队、退队 | `check_team_min`、`fill_team`、`maybe_rejoin`、`leave_or_disband_team` |
| `src/tab_group.py` | 组号多 Tab 身份、pass、队员操作 | `init_task`、`startup_check`、`startup_member_check`、`member_*_pass` |
| `src/task_tracker.py` | 追踪面板 OCR、绿字/下划线、TrackGate | `ocr_track`、`read_tasks`、`has_task`、`track_task`、`TrackGate` |
| `src/map.py` | OCR 三引擎、模板匹配、地图开关与坐标 | `ocr_regions`、`match_frame_np`、`read_map_position`、`open_mini_map_city` |
| `src/dialog_common.py` | 对话框通用：绿字选项、序号点击、连点、面板清理 | `_click_green_option_once`、`_click_dialog_option_by_index`、`close_scene_panels` |
| `src/app_utils.py` | 点击/键盘/全局锁/静默开关/KeyValue 合并写/进度 | `human_click`、`precise_click`、`high_perf_click`、`kv_*_merged` |
| `src/silent_click.py` | Win32 消息点击/按键/文本/Tab 条切换 | `send_silent_click`、`send_silent_hotkey`、`send_silent_text`、`send_silent_tab_click` |
| `src/bag.py` | 背包开合、物品检索、摄妖香 | `open_bag`、`find_item_with_slots`、`use_sheyaoxiang_if_due` |
| `src/zuoqi.py` | 飞行驭器面板、飞行/降落状态 | `open_feixingyuqi_panel`、`ensure_landed`、`ensure_flying` |
| `src/clear_screen.py` | 启动清屏 Alt+6 / Alt+5 / 收起侧栏 | `run_clear_screen` |
| `src/debug_overlay.py` | 悬浮层、运行看板状态、任务统计 | `start`、`shutdown`、`read_runtime_status`、`record_round` |
| `src/game.py` | 窗口查找、Tab 枚举、登录角色、窗口绑定 | `find_main_window`、`list_tab_windows`、`read_login_tab` |
| `src/status_bars.py` | 人物/召唤兽血蓝条扫描 | `check_all_bars`、`beast_below_threshold` |
| `src/schemes.py` | 配置方案快照/切换/导入导出 | `switch_scheme`、`resave_scheme`、`migrate_if_needed` |

---

## 附录 A：任务阶段徽章与看板数据流

```
任务模块 → debug_overlay.set_phase("前往领取点/检查队伍/领取任务/自动巫医/返程/寻路中/任务对话…")
战斗线程 → set_phase("战斗中 · 第 N 场") / 战斗结束 set_phase("")
任务模块 → set_progress("done/total")、record_round()
debug_overlay 监督线程 → 每 2s 原子写 debug_overlay_state.json（含 status）
主进程 ControlWindow → read_runtime_status() 1.5s 轮询看板 / read_task_stats() 统计页
悬浮层绘制子进程 → 读状态文件，仅内容变化时重绘
```

## 附录 B：关键时序常量（实测校准）

| 常量 | 当前值 | 含义 |
|---|---|---|
| `WATCH_POLL_INTERVAL` | 0.5s | 战斗监控轮询 |
| `BATTLE_CONFIRM_POLLS` | 2 | 进入战斗连续确认轮数 |
| `BATTLE_END_CONFIRM_POLLS` | 4 | 模板路径战斗结束确认（2s 窗口） |
| `BATTLE_END_POS_POLLS` | 2 | 坐标路径战斗结束确认（1s） |
| `BATTLE_END_POS_AFTER` | 10s | 进场后多久启用坐标快速结束信号 |
| `FOLLOW_TIMEOUT` | 300s | 跟踪总超时（战斗时间不计） |
| `FOLLOW_POLL_INTERVAL` | 0.5s | 跟踪正常轮询 |
| `CONFIRM_POLL_INTERVAL` | 0.2s | 变化确认轮询 |
| `TASK_GONE_CONFIRM_POLLS` | 2 | 任务消失确认轮数（五倍非黄袍怪 1 轮） |
| `TRACK_GATE_MIN_INTERVAL` | 2.0s | 追踪面板 OCR 最小间隔 |
| `TRACK_GATE_MAX_SKIP` | 5.0s | 面板变化兜底强制 OCR |
| `BATTLE_EXTRAS_INTERVAL` | 2.0s | 战斗中 extras 限频 |
| `REPLACE_INTERVAL` | 8s | 五倍连续替换领取最小间隔 |
| `REPLACE_FAIL_LIMIT` | 3 | 五倍替换连续失败上限 |
| `TEAM_CHECK_RETRY_INTERVAL` | 10s | 队伍缺人无申请人重查 |
| `TEAM_RECOG_RETRY_INTERVAL` | 3s | 队伍识别失败重查 |
| `SHOUT_MIN_INTERVAL` | 30s | 缺人喊话最小间隔 |
| `REJOIN_POLL_INTERVAL` | 3s | 非队长归队轮询 |
| `REJOIN_RETRY_INTERVAL` | 30s | 归队尝试后放慢 |
| `WUYI_RETRY_COOLDOWN` | — | 巫医点击冷却（见 battle 常量） |

> 常量定义分散在各模块顶部；改动前先读本表与 `docs/performance.md`，并在真实设备上复测。
