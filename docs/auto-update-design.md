# 软件自动更新设计方案（P3）

> 状态：已实现（v2.10.0，待发版实机验证）。日期：2026-11-09。
> 目标：在现有「GitHub Actions 打包 → zip 解压即用」的分发方式上，增加
> **检查更新 → 下载 → 覆盖升级 → 自动重启** 的完整链路；升级只替换程序文件，
> `config.json` / `data/`（按角色隔离的方案/成就/进度/高性能缓存）/
> `schemes.json` / `task_stats.json` 等用户数据原样保留。

---

## 1. 结论摘要

| 项目 | 方案 |
| --- | --- |
| 托管 | 阿里云 OSS 或腾讯云 COS（二选一），公共读前缀 + CI 写入 |
| 更新包 | 拆成 `app`（exe + res）与 `pylibs`（numpy/onnxruntime）两个 zip；pylibs 哈希未变则跳过下载 |
| 手动安装包 | 保留现有完整 zip（exe + res + pylibs），首次安装/救急用 |
| 清单 | CI 自动生成 `update.json`，不手工维护；最后上传（先资产后清单，保证原子发布） |
| 更新日志 | CI 从上一 tag 到当前 tag 的提交生成；可用 `changelog/vX.Y.Z.md` 覆盖 |
| 客户端 | 启动后台静默检查（24h 一次）+ 系统页「检查更新」；发现新版提示，用户确认后下载 |
| 升级方式 | 下载 → 解压到 `update/staging` → 停任务 → 复制一份旧 exe 到 `%TEMP%` 以 `--apply-update` 启动 → 等主进程退出 → 备份 → 覆盖 → 健康检查 → 重启 |
| 数据保留 | 只覆盖 `zhuomuniao.exe`、`res/`、`pylibs/`；根目录用户文件与 `logs/` 一律不动 |
| 回滚 | 旧文件移动到 `update/backup/`，失败自动恢复；新 exe 启动即崩溃时自动回退 |
| 仓库现状 | `OPC-UNI/dhxy_ascript` 为私有仓库，Release 不能直接作为用户下载源，因此必须走对象存储 |

**首次上线注意**：当前所有已分发版本都没有 updater，因此**第一个带 updater 的版本
仍需用户手动下载覆盖一次**；从该版本之后才能自动升级。这是 bootstrap 的固有约束。

---

## 2. 现状与约束

### 2.1 现有发布链

- 版本号唯一来源：`src/__init__.py` 的 `__version__`；`pyproject.toml` 同步。
- 发版：打 `vX.Y.Z` tag → `.github/workflows/build-exe.yml`：
  1. 把 tag 版本号烙进 `src/__init__.py`；
  2. Nuitka onefile 构建 `zhuomuniao.exe`；
  3. numpy/onnxruntime 排除出 exe，组装为 `pylibs/`；
  4. 打包 `release_pkg`（exe + res + pylibs）为 `zhuomuniao-windows.zip`；
  5. 上传 Actions artifact，打 tag 时发 GitHub Release；
  6. 打 tag 时还要：上传 OSS → 公网清单校验 → 清理 `app/`、`pylibs/` 旧包
     （只留最新两版，见 §4.2）。
- 用户侧：解压 zip 到任意目录，双击 `zhuomuniao.exe` 运行。

### 2.2 与更新相关的既有事实

- **配置在 CWD**：`KeyValue._determine_path()` 取 `os.getcwd()`，无写权限才回退
  `~/.app_data`。双击 exe 时 CWD = exe 目录；快捷方式「起始位置」不同则配置可能在
  别处。升级重启必须沿用**原进程的 CWD**，否则用户会看到配置「丢失」。
- **程序目录 = exe 目录**：`main.py` 用 `os.path.dirname(sys.executable)` 定位
  `pylibs/`；`res/` 由 AScript `R` 的冻结分支按 exe 目录解析。
- **子进程同样锁定 exe**：任务子进程通过 multiprocessing spawn 启动；调试悬浮层
  绘制进程用 `sys.executable --overlay-helper` 启动（`src/core/debug_overlay.py`）。
  升级前这些进程必须全部退出，否则 exe / pylibs DLL 被占用无法替换。
- **没有单实例限制**：当前允许多开；升级时必须检测并拒绝「还有其它实例在跑」，
  否则覆盖到一半会失败或让其它实例崩溃。
- **任务停止能力现成**：`TaskManager.stop()` 已是四级强杀（stop_event → join →
  terminate → kill），升级前直接复用。
- **Nuitka onefile**：每次启动会把自己解压到 `%TEMP%`。因此：
  - 不能直接让「同一个 exe」在后台等待并替换自己——该进程自身会锁住目标 exe；
  - 必须把旧 exe **复制到 `%TEMP%`**，用副本以 `--apply-update` 启动，副本运行在
    临时目录，不锁安装目录里的 exe。
- **`sys.frozen` 不可用**：Nuitka 不设置，冻结判据是 `"__compiled__" in globals()`。

### 2.3 更新不做什么

- 不做安装器、不写注册表、不建快捷方式；仍是绿色解压目录。
- 不更新源码运行形态（`python main.py`）；源码模式只允许检查、不允许套用。
- 不做强制静默升级；下载与安装都要用户确认（`min_supported_version` 除外）。

---

## 3. 总体架构

```
发布侧（GitHub Actions，tag 触发）
  ┌─────────────┐   ┌──────────────────┐   ┌─────────────────────┐
  │ Nuitka 构建 │ → │ app.zip / pylibs.zip │ → │ OSS/COS stable/     │
  │ + 完整 zip  │   │ + update.json     │   │ 先传资产，最后传清单 │
  └─────────────┘   └──────────────────┘   └─────────────────────┘

客户端（主进程）
  ┌────────────┐   ┌──────────────┐   ┌──────────────┐   ┌─────────────┐
  │ 启动/手动   │ → │ 拉 update.json│ → │ 下载缺失分片 │ → │ 解压 staging │
  │ 检查更新    │   │ 版本比较/提示 │   │ 校验 sha256  │   │ 校验目录结构 │
  └────────────┘   └──────────────┘   └──────────────┘   └──────┬──────┘
                                                                ↓
  ┌────────────┐   ┌──────────────┐   ┌──────────────┐   ┌─────────────┐
  │ 新 exe 启动 │ ← │ 覆盖 + 备份   │ ← │ 等所有进程退出│ ← │ 停任务 + 拉起│
  │ 健康检查    │   │ 失败自动回滚  │   │ 复制 exe 到   │   │ update helper│
  │ 失败回滚    │   │              │   │ %TEMP% 运行   │   │ (旧主进程退出)│
  └────────────┘   └──────────────┘   └──────────────┘   └─────────────┘
```

模块划分：

| 模块 | 职责 |
| --- | --- |
| `src/app/update_config.py` | 编译期常量：清单地址、渠道、超时、包名 |
| `src/app/updater.py` | 版本比较、清单拉取/校验、下载、解压、应用更新、helper 入口、回滚 |
| `src/app/control_window.py` | 暴露 `check_update` / `start_update` / `skip_update` 等桥接方法 |
| `res/ui/control_window.html` | 系统页「关于」内嵌更新区、进度与结果回调 |
| `scripts/make_update_manifest.py` | CI/本地生成并校验 `update.json` |
| `.github/workflows/build-exe.yml` | 拆包、算哈希、生成清单、上传 OSS/COS、保留内部 Release |

---

## 4. 托管与目录设计（阿里云 OSS / 腾讯云 COS）

### 4.1 为什么不用 GitHub Release

已实测 `https://api.github.com/repos/OPC-UNI/dhxy_ascript` 返回 **404**（私有仓库）。
私有仓库的 Release 资产需要登录鉴权，终端用户无法匿名下载。若改用公开仓库会暴露
源码/提交历史，不可取。因此更新资产放对象存储，GitHub Actions 只负责构建和上传。

### 4.2 对象存储布局

已确定使用阿里云 OSS `dhxy-ascript-new`（`cn-beijing`）：

```
bucket: dhxy-ascript-new（私有）
  endpoint: oss-cn-beijing.aliyuncs.com
  公共读前缀：zhuomuniao/stable/*
  写入权限：仅 CI 使用的 RAM 子账号，限该前缀 PutObject/GetObject

zhuomuniao/stable/update.json                          # 始终指向最新版
zhuomuniao/stable/app/zhuomuniao-app-<version>.zip     # 版本命名，不可变；只留最新两版
zhuomuniao/stable/pylibs/zhuomuniao-pylibs-<sha8>.zip  # 内容寻址，未变则复用；只留最新两版
zhuomuniao/stable/manual/zhuomuniao-windows-latest.zip # 完整包：仅首个带自动更新的
                                                       # 版本（v2.10.0）上传一次
```

manual 完整包**只在 v2.10.0 上传一次，之后发版不再上传/覆盖 OSS**：新用户
装 v2.10.0 后走面板升级即可（该版本已带 updater），省存储与流量成本。
版本命名的 `zhuomuniao-windows-<version>.zip` 仍每次发版生成，由 GitHub
Release 归档（workflow 附产物）。

`app/` 与 `pylibs/` 的旧对象由发布流程自动清理：CI 在公网清单校验通过后执行
`upload_release.py --prune-only --prune-keep 1`，删除未被当前清单引用的对象，
只保留「本次引用 + 上一版」。客户端只按当前 `update.json` 下载缺失分片，
不会请求历史版本；上一版留作兜底——客户端可能已拉到旧清单、下载尚未完成，
此时旧 zip 仍需可下载。清理失败不影响发布，历史版本以 GitHub Release 归档为准。

当前客户端清单地址（编译期常量 `src/app/update_config.py`）：

```
https://dhxy-ascript-new.oss-cn-beijing.aliyuncs.com/zhuomuniao/stable/update.json
```

后续若绑定稳定域名（如 `update.example.com`），改 DNS/回源并把仓库 Variable
`UPDATE_BASE_URL` 与 `src/app/update_config.py` 同步即可；客户端不依赖 bucket 内部布局。

**bucket 公共读策略（示例）**：bucket 保持私有，仅对更新前缀开放匿名 GetObject：

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": ["*"],
      "Action": ["oss:GetObject"],
      "Resource": ["acs:oss:*:*:dhxy-ascript-new/zhuomuniao/*"]
    }
  ]
}
```

RAM 子账号策略：目标前缀的 `oss:PutObject` / `oss:GetObject` / `oss:HeadObject`，
加上旧包清理所需的前缀级 `oss:DeleteObject` 与 `oss:ListObjects`（用
`oss:Prefix` 条件限到 `zhuomuniao/stable/*`）：

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["oss:PutObject", "oss:GetObject", "oss:HeadObject"],
      "Resource": ["acs:oss:*:*:dhxy-ascript-new/zhuomuniao/stable/*"]
    },
    {
      "Effect": "Allow",
      "Action": ["oss:DeleteObject"],
      "Resource": [
        "acs:oss:*:*:dhxy-ascript-new/zhuomuniao/stable/app/*",
        "acs:oss:*:*:dhxy-ascript-new/zhuomuniao/stable/pylibs/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": ["oss:ListObjects"],
      "Resource": ["acs:oss:*:*:dhxy-ascript-new"],
      "Condition": {"StringLike": {"oss:Prefix": ["zhuomuniao/stable/*"]}}
    }
  ]
}
```

CI 在上传后会请求一次公网清单，策略未配置时会直接失败并提示；清理步骤在缺少
`ListObjects` / `DeleteObject` 时只打印警告并跳过，不影响发布。

缓存头：

| 对象 | Cache-Control |
| --- | --- |
| `update.json` | `no-cache, max-age=60` |
| zip（版本/哈希命名） | `public, max-age=31536000, immutable` |

客户端拉清单时追加 `?ts=<unix>` 防 CDN 缓存；下载 zip 不加随机参数，保证可缓存。

### 4.3 CI 凭据

GitHub Actions Secrets（以 OSS 为例）：

```
OSS_ENDPOINT          # oss-cn-xxx.aliyuncs.com
OSS_BUCKET
OSS_ACCESS_KEY_ID
OSS_ACCESS_KEY_SECRET
UPDATE_BASE_URL       # https://update.example.com/zhuomuniao/stable
```

RAM 子账号最小权限：目标前缀的 `PutObject` / `GetObject` / `HeadObject`，旧包
清理用的前缀级 `DeleteObject` 与（`oss:Prefix` 限定）`ListObjects`，不给其它
bucket 权限（策略示例见 §4.2）。密钥只存在于 Actions Secrets，不写进仓库。

腾讯云 COS 对应 `COS_SECRET_ID` / `COS_SECRET_KEY` / `COS_REGION` / `COS_BUCKET`，
CI 步骤换成 `coscli cp`。两者只需要实现其中一套，另一套留作迁移备注。

---

## 5. `update.json` 清单设计

### 5.1 示例

```json
{
  "schema": 1,
  "app": "zhuomuniao",
  "channel": "stable",
  "version": "2.10.0",
  "min_supported_version": "2.0.0",
  "published_at": "2026-11-09T13:00:00Z",
  "release_url": "https://github.com/OPC-UNI/dhxy_ascript/releases/tag/v2.10.0",
  "notes": "## v2.10.0\n- 新增软件自动更新\n- 修复 ...",
  "parts": [
    {
      "name": "app",
      "url": "https://update.example.com/zhuomuniao/stable/app/zhuomuniao-app-2.10.0.zip",
      "sha256": "3f2a...（64 位小写）",
      "size": 61234567,
      "targets": ["zhuomuniao.exe", "res"]
    },
    {
      "name": "pylibs",
      "url": "https://update.example.com/zhuomuniao/stable/pylibs/zhuomuniao-pylibs-1a2b3c4d.zip",
      "sha256": "1a2b...（64 位小写）",
      "size": 45678901,
      "targets": ["pylibs"]
    }
  ]
}
```

### 5.2 字段语义

| 字段 | 说明 |
| --- | --- |
| `schema` | 清单结构版本，当前 `1`；客户端不认识则拒绝更新 |
| `version` | 目标版本，必须匹配 `^\d+\.\d+\.\d+$`，且必须与 tag 一致 |
| `min_supported_version` | 低于此版本的客户端**强制更新且不可跳过**；默认 `0.0.0` |
| `published_at` | ISO8601 UTC，仅展示/排查 |
| `notes` | 更新日志正文（简单 Markdown：标题/列表/加粗/行内代码/引用/链接），客户端转义后渲染 |
| `release_url` | 可选，内部 Release 页；客户端只做「查看详情」链接 |
| `parts[].name` | `app` / `pylibs`；未知 name 忽略或拒绝 |
| `parts[].url` | HTTPS 绝对地址；可选 `mirror_urls` 数组做备用源 |
| `parts[].sha256` | 下载后必校验；不一致立即删除并报错 |
| `parts[].size` | 字节数；用于进度、磁盘空间检查和下载完整性初判 |
| `parts[].targets` | 该分片解压后覆盖的顶层路径，只允许白名单内 |
| `mandatory`（可选） | 显式强制更新；等价于 `current < min_supported_version` |

### 5.3 维护方式

- **完全由 CI 生成**，仓库里不提交 `update.json`，避免「清单与 zip 不同步」。
- 生成脚本 `scripts/make_update_manifest.py` 同时支持 `generate` 与 `validate`：
  - 校验版本格式、tag 一致性、zip 存在、sha256/size、targets 白名单、必需文件；
  - `validate` 在 CI 上传前再跑一次，失败即中止发布。
- 发布顺序：**先传 zip，再传完整包，最后传 `update.json`**。清单出现即代表资产已就绪。
- `pylibs` 用内容哈希命名（`sha256[:8]`）：CI 先 `HEAD` 判断对象是否存在，存在即跳过
  上传，`update.json` 继续引用旧 URL。这样依赖没变时每次发版只上传几十 MB 的 app 包。

### 5.4 更新日志

- 默认：取上一 tag 到当前 tag 的提交列表，沿用现有 Release 正文的生成逻辑：

  ```powershell
  git log --pretty=format:"- %s" $prev..HEAD
  ```

- 可选覆盖：仓库存在 `changelog/vX.Y.Z.md` 时，用其内容作为 `notes`。这样既保留
  「提交即日志」的零维护，又允许发版时写面向用户的说明。
- CI 产物分两份：`update_notes.md`（写入 `update.json` 的 `notes`，面板展示，
  **不含**「完整对比」等外部链接）与 `release_notes.md`（GitHub Release 正文，
  末尾附完整对比链接）。两者共用同一份基础内容。
- 客户端展示 `notes`：面板内极简 Markdown 渲染（标题/列表/加粗/行内代码/
  引用/链接），先整体 HTML 转义再套标签，链接点击经后端用系统浏览器打开。
- 对用户隐藏纯内部提交（如 `docs:` / `chore:`）可作为后续脚本增强，不在 v1 范围。

---

## 6. 发布流水线（GitHub Actions）

在现有 `build-exe.yml` 上增量修改，tag 触发才发布清单：

1. 构建 exe、组装 `pylibs`（不变）。
2. 组装三类产物：
   - `app_pkg/`：`zhuomuniao.exe` + `res/` → `zhuomuniao-app-<version>.zip`
   - `pylibs_pkg/`：`pylibs/` → `zhuomuniao-pylibs-<sha8>.zip`
   - `release_pkg/`：三者合并 → `zhuomuniao-windows-<version>.zip`（手动安装包）
3. `Get-FileHash -Algorithm SHA256` 计算各 zip 哈希与大小。
4. 生成更新日志两份（现有步骤前移）：`update_notes.md` 供清单使用（无外部链接），
   `release_notes.md` 供 GitHub Release 正文（附完整对比链接）。
5. 调 `python scripts/make_update_manifest.py generate ...` 生成 `update.json`。
6. `python scripts/make_update_manifest.py validate ...` 自检。
7. 上传 OSS/COS（仅 tag）：
   - `app zip` → `stable/app/`；
   - `pylibs zip` → 先 HEAD，不存在才上传；
   - `manual zip` **不上传 OSS**（v2.10.0 已一次性留存，后续版本无需覆盖）；
   - `update.json` → `stable/update.json`（最后）。
8. 保留现有 GitHub Release 与 Actions artifact（内部留档/手动下载），打 tag 时附
   `zhuomuniao-windows-<version>.zip` 与 `update.json`。
9. `workflow_dispatch` 手动构建**不发布清单**（默认）；需要预发布时增加
   `publish` 输入并显式指定版本，防止误覆盖线上清单。

CI 失败策略：任一步失败即终止，`update.json` 不更新，线上继续指向旧版；zip 上传
成功但清单失败不会影响用户（只是多一个未被引用的对象）。

### 6.1 发布流程不变

版本 bump、提交、打 tag 仍走 `release-tag` skill / `release.py`。新增的可选文件：

```
release/update_policy.json     # {"min_supported_version": "2.0.0"}，可选
changelog/v2.10.0.md           # 面向用户的更新日志，可选
```

---

## 7. 客户端检查更新

### 7.1 触发时机

| 场景 | 行为 |
| --- | --- |
| 启动后 | 控制窗口 `prepare()` 完成约 8 秒后，后台线程检查；不阻塞启动、不抢 OCR 预热 |
| 自动检查频率 | `update_last_check_ts` 距今 < 24h 则跳过；手动检查不受限 |
| 手动 | 系统页「关于」→「检查更新」，立即拉取并回显结果 |
| 任务运行中 | 允许检查；**下载/安装前必须停止任务**（见 8.1） |
| 源码运行 | 允许检查，`开始更新` 置灰并提示「源码运行不支持自动更新」 |

### 7.2 请求与容错

- 使用标准库 `urllib.request`（不新增依赖），UA `Zhuomuniao-Updater/1.0`。
- 连接/读取超时 8 秒；清单请求加 `Cache-Control: no-cache` 和 `?ts=`；失败静默重试
  1 次，自动检查失败只写日志，不打扰用户；手动检查显示具体错误。
- 走系统代理（urllib 在 Windows 下读取注册表代理配置），兼容常见代理环境。
- 镜像：清单可选 `mirror_urls`；下载失败先换镜像，再提示错误。v1 至少实现
  「清单 URL 可被 `ZMN_UPDATE_FEED` 环境变量覆盖」，方便本地/测试渠道。

### 7.3 版本判定

- 只接受 `X.Y.Z` 三段数字（与 `release.py` 一致），忽略 `v` 前缀。
- `remote > current` 才提示；`remote <= current` 显示「已是最新」。
- `current < min_supported_version` 或 `mandatory=true` → 强制更新：启动即提示，
  不提供「跳过此版本」。
- 用户选择「跳过此版本」写入 `update_skipped_version`；自动检查不再提示该版本，
  手动检查仍展示（可撤销）。版本号变化后自动恢复提示。

### 7.4 检查结果状态

```
idle → checking → available / up_to_date / error
available → downloading → ready → applying → restarting
                      ↘ error（可重试）
```

状态保存在 `ControlWindow._update_state`，页面每次初始化通过 `get_init_data` 取回，
避免窗口刷新后丢状态。

---

## 8. 下载与覆盖升级

### 8.1 前置条件（逐项检查，任一失败即中止并提示）

1. 冻结 exe 运行（`"__compiled__" in globals()`）。
2. 安装目录可写：向 `update/` 写一个探测文件。
3. 磁盘剩余空间 ≥ `zip 总大小 + 解压后大小 + 旧文件备份`（预估 2.5 倍）。
4. 无其它实例运行（见 8.6）。
5. 任务已停止：用户确认后调用 `TaskManager.stop()`；返回非成功则中止。
6. 清单 `schema`、版本、URL、哈希格式全部合法。

### 8.2 下载

- 目录：`<安装目录>/update/download/`（与 staging 同盘，后续 `os.replace` 为同卷
  原子重命名；也便于磁盘空间统一检查）。
- 分片选择：读取 `update/installed.json`（上次成功安装的各分片 sha256），
  **哈希相同的分片直接跳过下载**，沿用本地已安装文件。
- 流式写入（64 KiB/块），边下边算 sha256，进度经 `evaluate_js` 推送页面；
  完成后比对 `size` 与 `sha256`，不一致删除重试一次，再失败报错。
- 下载失败/中断：保留 `.part` 文件，v1 直接删除重下；断点续传列为后续增强。
- zip 完整性：`zipfile.ZipFile.testzip()` + 条目数/总解压大小上限（防 zip bomb）。

### 8.3 解压

- 解压到 `update/staging-<version>/`，逐条校验：
  - 拒绝绝对路径、`..`、盘符、符号链接（Zip Slip）；
  - 总解压大小 ≤ 清单声明大小的 1.2 倍且 ≤ 1 GB；
  - 顶层只允许 `zhuomuniao.exe`、`res/`、`pylibs/`（按分片 `targets` 白名单）。
- 解压后校验 `zhuomuniao.exe` 存在且大小与清单 zip 内记录一致（可加 `exe` 的条目
  CRC/size 校验）。
- 写 `update/pending.json`：

  ```json
  {
    "from_version": "2.9.6",
    "to_version": "2.10.0",
    "install_dir": "C:\\...\\zhuomuniao",
    "cwd": "C:\\...\\zhuomuniao",
    "staging": "C:\\...\\zhuomuniao\\update\\staging-2.10.0",
    "parts": ["app", "pylibs"],
    "main_pid": 12345,
    "log": "C:\\...\\zhuomuniao\\update\\update.log"
  }
  ```

  `cwd` 记录主进程启动时的当前目录，重启必须沿用它（配置跟随 CWD）。
  `install_dir` 由主进程写入（helper 运行在 `%TEMP%`，`sys.executable` 已不是安装目录）。

### 8.4 停止与拉起 helper

1. 页面确认「立即更新」→ 停止任务（`TaskManager.stop()`），等待子进程全部退出。
2. 关闭调试悬浮层（`debug_overlay.shutdown()`），回收 `--overlay-helper` 进程；
   它同样运行安装目录的 exe，会锁定文件。
3. 把当前 `zhuomuniao.exe` 复制到
   `%TEMP%\zhuomuniao-updater-<pid>\zhuomuniao.exe`。
4. 用副本启动：

   ```
   %TEMP%\zhuomuniao-updater-<pid>\zhuomuniao.exe --apply-update <pending.json>
   ```

   helper 运行在临时目录，**不锁定安装目录的 exe**。
5. 主进程走正常退出路径（`Runtime.stop()` / `tm.stop()` / `debug_overlay.shutdown()`），
   让托盘、webview、热键线程干净结束。

`main.py` 必须在**导入 ascript / webview / uiautomation 等重依赖之前**识别
`--apply-update`，直接进入 `src.app.updater.apply_update_entry()`，不创建任何 GUI、
不走授权门。`src/app/updater.py` 本身只依赖标准库。

### 8.5 helper 执行步骤

```
1. 读 pending.json，打开 update.log
2. 轮询等待：main_pid 退出 + 安装目录下不再有进程（重试 60s）
   - 枚举进程镜像路径，排除 helper 自身（在 %TEMP%）
   - 超时：写日志 + MessageBox「请关闭其它实例后重试」+ 重新启动旧 exe → 退出
3. 记 journal：phase=backup
4. 备份并移走旧文件（同卷 rename，快）：
   app 分片：zhuomuniao.exe、res/
   pylibs 分片：pylibs/
   → update/backup/<from_version>-<ts>/
5. journal phase=apply
6. 把 staging 中的 zhuomuniao.exe、res/、pylibs/ 移入安装目录
7. journal phase=verify
8. 校验新 exe 存在、大小正确、可执行
9. 写 last_result.json=success（新版本首屏即可展示；健康检查失败会覆盖）
10. 启动新 exe（cwd = pending.cwd；失败回退 install_dir）
11. 健康检查：等待 20 秒
    - 进程已退出且退出码非 0 → 回滚：恢复 backup → 覆盖 last_result=rolled_back
      → 启动旧 exe → MessageBox 报错
    - 进程存活 → 写 installed.json、journal phase=done
12. 清理：删除 zip / staging；保留最近 1 份 backup
13. helper 退出（自身在 %TEMP%，无需自删）
```

启动新 exe 时使用 `subprocess.Popen([exe], cwd=cwd)`，不继承 helper 的控制台（
`creationflags=CREATE_NO_WINDOW` / `DETACHED_PROCESS`）。

### 8.6 多实例检测

升级前/helper 中都要检测安装目录下是否还有其它 `zhuomuniao.exe` 进程。实现用
`ctypes` + `CreateToolhelp32Snapshot` + `QueryFullProcessImageNameW` 枚举，比对镜像
路径是否位于 `install_dir` 下：

- 主进程自身在停止任务后退出；
- 若还有第二个用户实例在跑，helper 等待 60 秒后放弃并提示
  「检测到其它实例，请关闭所有窗口后重新打开再试」；此时文件未改动，
  不自动再拉一个实例（避免与用户已开的实例重复）；
- 不尝试杀掉其它实例（可能正在跑别人的任务，误杀风险高）。

### 8.7 覆盖 / 保留清单

| 路径 | 升级处理 |
| --- | --- |
| `zhuomuniao.exe` | 替换（app 分片） |
| `res/` 整目录 | 替换（app 分片；模板、UI、模型随版本更新） |
| `pylibs/` 整目录 | 有变化才替换（pylibs 分片） |
| `config.json` | **保留**（全局设置、当前角色设置快照、窗口绑定） |
| `data/` | **保留**（按角色隔离的 settings / schemes / task_stats / state / high_perf_cache） |
| `schemes.json` | **保留**（旧版全局方案；迁移后新版本不再读写，回滚用） |
| `task_stats.json` | **保留**（旧版全局统计；迁移后新版本不再读写，回滚用） |
| `path_cache.json` | **保留**（固定坐标缓存） |
| `logs/` | **保留** |
| `debug_overlay_state.json` | 保留（运行时状态，下次启动会重建） |
| `update/` | 升级过程自身使用，保留 `backup/` 与 `installed.json` |
| 其它用户文件 | 不动（根目录不做镜像删除） |

目录替换采用「旧目录 rename 到 backup → staging 目录 rename 到目标」的方式，
**不是逐文件覆盖**，因此新版删除的模板/旧 DLL 不会残留。根目录绝不 mirror。

### 8.8 回滚与崩溃恢复

- helper 内任何异常：按 journal 逆序恢复 backup，启动旧 exe，写 `last_result.json`。
- 新 exe 启动 20 秒内异常退出：视为升级失败，自动回滚。
- 断电/被强杀导致 journal 停在 `backup`/`apply`：下次启动主进程检测
  `update/journal.json` 未完成 → 用 backup 恢复（此时目录可能不完整，恢复逻辑只
  搬运 backup 中存在的条目），写日志并提示。
- 成功启动后保留一份 backup（`update/backup/`），连续成功启动一次后清除更老的备份。
- 不提供自动「降级」到任意历史版本；需要时手工解压完整包覆盖。

### 8.9 更新结果回显

- helper 写 `update/last_result.json`：
  `{"status": "success|rolled_back|failed", "from": "...", "to": "...", "ts": ...}`。
- 新版本启动时读取：
  - `success`：日志「[更新] 已升级 v2.9.6 → v2.10.0」，面板可选提示；
  - `rolled_back`/`failed`：日志 + 面板警告，提示查看 `update/update.log`。
- 清理 `last_result.json` 与 `pending.json` 在展示后删除。

---

## 9. 界面设计

### 9.1 系统页「关于」区（内嵌，不弹窗）

在现有版本行下方增加：

```
版本        v2.9.6
软件更新    已是最新 / 发现 v2.10.0（61.2 MB）  [检查更新] [立即更新] [跳过此版本]
进度        ███████████░░░░░░  下载中 63%
            （发现新版本时展开更新日志，只读文本）
自动检查更新（每天一次）  [开关]
运行日志    ...
```

- 有更新时状态文字高亮、侧边栏版本号变金色并显示悬浮提示；
- 下载中显示进度条；不提供「取消」（下载包较小，中断由下次重试兜底）；
- 「立即更新」在下载解压完成后才出现（`available` 状态），
  点击后任务运行中会弹 `confirm` 二次确认；
- 强制更新时隐藏「跳过此版本」，后端也拒绝跳过；
- 更新说明用 `textContent` 写入 `<pre>`，不渲染 HTML（防清单注入）。

### 9.2 状态与文案

```
idle → checking → available / up_to_date / error
available → downloading → preparing（解压）→ stopping → applying → 重启
```

- `checking`：正在检查…
- `up_to_date`：已是最新版本 / 已跳过 vX.Y.Z
- `available`：发现 vX.Y.Z（大小）
- `downloading` / `preparing`：下载中 N% / 解压中 N%
- `stopping`：正在停止任务…
- `applying`：正在应用更新，程序即将重启…
- `error`：检查失败 / 更新失败（错误详情在提示行）

### 9.3 桥接方法（已实现）

`src/app/control_window.py`：

| 方法 | 作用 |
| --- | --- |
| `check_update()` | 后台检查（立即返回 checking，结果经 `onUpdateState` 回推） |
| `get_update_state()` | 返回当前状态（初始化/页面刷新恢复用） |
| `start_update(confirm_stop=False)` | 后台下载/解压 → 停任务 → 拉起 helper；任务运行中返回 `need_stop` |
| `skip_update_version(version)` | 记录跳过版本（强制更新拒绝） |
| `set_update_auto_check(on)` | 自动检查开关 |

进度与结果通过 `webview_instance.evaluate_js` 调
`window.onUpdateState({...})` / `window.onUpdateProgress({...})` 推送，沿用现有
方案导入/队伍检查的回调模式。首屏 `get_init_data` 同时返回 `update` 状态与
`update_last_result`，避免页面加载前推送丢失。

---

## 10. 新增配置项（`config.json`，升级永久保留）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `update_auto_check` | `true` | 启动后是否自动检查 |
| `update_last_check_ts` | `0` | 上次检查时间（24h 节流） |
| `update_skipped_version` | `""` | 用户跳过的版本 |
| `update_feed_url` | `""` | 可选，覆盖编译期清单地址（测试/应急） |

均通过 `app_utils.kv_save_merged` 写入，避免覆盖任务子进程的运行时键。
`update/installed.json` 单独存分片哈希，不塞进 `config.json`。

---

## 11. 安全设计

- **传输**：仅允许 `https://`；清单与 zip 同源或显式白名单域名。
- **完整性**：大小 + SHA-256 双重校验；哈希来自清单，清单走 HTTPS。
- **解压**：Zip Slip 防护、条目数/解压大小上限、只允许白名单顶层路径。
- **清单注入**：更新说明按纯文本展示；`release_url` 仅允许 https 且长度受限。
- **路径**：`install_dir` 由主进程写入 pending.json，helper 不接受命令行任意路径；
  helper 只操作 `install_dir` 下的白名单条目。
- **签名（后续增强）**：当前没有代码签名证书，SHA-256 只能防损坏/传输错误，不能
  防「托管账号被攻破后同时替换 zip 与清单」。后续可用仓库现有 `Crypto` 依赖做
  Ed25519 分离签名：CI 用 Secret 私钥签 `update.json`，客户端内置公钥验签。
  成本低，建议 Phase 4 上线。
- **SmartScreen**：更新后的 exe 仍是无签名程序，首次运行可能提示。这是现有分发
  方式的既有问题，自动更新不会额外加重；如需彻底解决需购买代码签名证书。

---

## 12. 失败模式与处理

| 场景 | 处理 |
| --- | --- |
| 清单 404 / 超时 / JSON 损坏 | 自动检查静默记日志；手动检查提示网络错误；不进入下载 |
| 清单版本 ≤ 当前 | 显示已是最新 |
| 版本格式/字段非法 | 拒绝更新，记录原始响应摘要 |
| 下载 sha256 不符 | 删除文件，重试 1 次；仍失败提示 |
| 磁盘空间不足 | 下载前提示所需空间，中止 |
| 安装目录不可写 | 提示「请将程序放到有写权限的目录」 |
| 任务停止失败 | 中止更新，任务继续运行 |
| 其它实例运行 | helper 等待 60s 后放弃，重启旧版并提示 |
| 文件被占用（杀软/残留进程） | 重试替换（间隔 1s，最多 30s）；失败回滚 |
| 新 exe 启动即崩溃 | 20s 健康检查失败 → 自动回滚旧版 |
| 升级中断电 | 下次启动按 journal + backup 恢复 |
| 清单指向错误 targets | 拒绝，防误删用户目录 |

---

## 13. 测试与验收

### 13.1 单元测试（`tests/test_updater.py`）

- 版本比较：`2.10.0 > 2.9.9`、`v` 前缀、非法格式、相等。
- 清单校验：缺字段、schema 不支持、sha256 长度错误、targets 越界、版本与 tag 不一致。
- 分片选择：`installed.json` 哈希相同 → 跳过下载；缺失 → 全下载。
- 安全解压：`../evil`、绝对路径、符号链接、超大解压全部拒绝。
- 覆盖计划：只动白名单、保留 config/schemes/task_stats/logs。
- 回滚：临时目录中模拟 replace 失败，backup 恢复后文件内容一致。
- 磁盘空间估算函数。

### 13.2 本地联调

- `python -m http.server` 提供假清单与假 zip；设 `ZMN_UPDATE_FEED` 指向本地。
- 源码模式只验证检查/下载/解压/UI 状态；`apply` 用临时目录直接调核心函数验证。
- manifest 脚本：对真实构建产物生成并 `validate`。

### 13.3 真机验收（首个 updater 版本发布后）

1. 安装 v2.9.6（无 updater）→ 手动覆盖一次 v2.9.7（带 updater）。
2. 修改若干配置/方案/统计 → 发布 v2.9.8 测试包到 `test/` 前缀。
3. 客户端检查 → 下载 → 更新 → 重启：
   - 版本显示正确；
   - `config.json`、`data/`、`schemes.json`、`task_stats.json` 内容不变；
   - 任务能正常启动。
4. 回滚测试：测试包故意放一个启动即退的 exe → 确认自动回滚并能启动旧版。
5. 多实例测试：开两个实例 → 更新被拒绝且提示明确。
6. 断网/超时/哈希错误：各自处理正确。

### 13.4 CI

- `ci.yml` 的 pytest 增加 updater 用例（纯逻辑，不碰窗口/输入）。
- `build-exe.yml` 增加清单 generate/validate；上传步骤仅在 tag 时执行。
- 建议先用 `workflow_dispatch` + 测试前缀演练一整轮，再切正式 `stable/`。

---

## 14. 实施计划

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| Phase 0 | 开通 OSS/COS bucket、RAM 子账号、Secrets、稳定域名（可选） | CI 能上传测试对象 |
| Phase 1 | 拆包 + `make_update_manifest.py` + CI 生成/上传 `update.json` | 手工能通过 URL 下载清单与两个 zip，哈希一致 |
| Phase 2 | `src/app/updater.py` 检查逻辑 + 系统页 UI + 配置项 | 面板能检查、显示更新日志、跳过版本 |
| Phase 3 | 下载/解压/停任务/helper/备份/覆盖/重启/回滚 | 真机完成 13.3 全流程 |
| Phase 4 | 可选：Ed25519 签名、`pylibs` 内容寻址优化、镜像、断点续传 | 按需 |

预计新增/修改文件：

```
新增  src/app/updater.py
新增  src/app/update_config.py
新增  scripts/make_update_manifest.py
新增  tests/test_updater.py
新增  docs/auto-update-design.md（本文）
修改  main.py                       # --apply-update 早期分支 + 启动检查 + 结果回显
修改  src/app/control_window.py         # 桥接方法 + 初始化状态
修改  res/ui/control_window.html    # 关于区内嵌更新区 + 进度
修改  .github/workflows/build-exe.yml
修改  .gitignore                    # update/
修改  README.md                     # 功能说明与发布流程
```

---

## 15. 已确认决策（2026-11-09）

1. **托管**：阿里云 OSS `dhxy-ascript-new`，endpoint `oss-cn-beijing.aliyuncs.com`，
   前缀 `zhuomuniao/stable/`。暂不绑定自定义域名；后续可加 DNS 并用
   `UPDATE_BASE_URL` / `update_feed_url` 覆盖。
2. **首发版本**：v2.10.0。该版本仍需用户手动覆盖安装一次（bootstrap），
   之后再发版即可自动升级。
3. **更新日志**：启用 `changelog/<tag>.md` 人工覆盖；没有该文件时用 git log。
4. **交互**：默认「启动静默检查（24h 节流）+ 提示，用户确认后下载安装」，
   不做后台静默预下载。
5. **强制更新策略**：`release/update_policy.json` 平时保持 `0.0.0`；
   仅在配置结构不兼容时抬升。

## 16. 实现状态

- ✅ `src/app/updater.py`：版本比较、清单校验、分片下载/sha256、安全解压、
  helper 覆盖/备份/回滚、journal 启动恢复。
- ✅ `src/app/update_config.py`：OSS 地址、渠道、超时、节流参数。
- ✅ `src/app/control_window.py` + `res/ui/control_window.html`：关于区检查/进度/
  跳过/自动检查开关、状态回调、升级结果展示。
- ✅ `main.py`：`--apply-update` 早期分支、启动恢复、自动检查调度。
- ✅ `scripts/make_update_manifest.py`：清单生成/校验。
- ✅ `scripts/make_zip.py`：确定性 zip（固定时间戳/顺序），保证 pylibs 内容寻址稳定。
- ✅ `scripts/upload_release.py`：OSS 上传（内容寻址 pylibs、清单最后）。
- ✅ `.github/workflows/build-exe.yml`：拆包、生成/校验清单、上传、公网验证。
- ✅ `tests/test_updater.py` + `tests/test_make_zip.py`：19 个纯逻辑用例
  （含 helper 全流程成功/回滚桩测、确定性打包）。
- ⏳ 待实机：v2.9.6 → v2.10.0 手动覆盖一次，再发 v2.9.8 测试包验证自动更新、
  配置保留、回滚与多实例拒绝。
