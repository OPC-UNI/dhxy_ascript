"""自动更新编译期配置（发布地址 / 渠道 / 超时）。

发布托管：阿里云 OSS `dhxy-ascript`（cn-beijing）。
对象布局（公共读前缀 zhuomuniao/stable/）：

    update.json
    app/zhuomuniao-app-<version>.zip          # exe + res
    pylibs/zhuomuniao-pylibs-<sha8>.zip      # numpy/onnxruntime，内容寻址
    manual/zhuomuniao-windows-<version>.zip  # 完整包（首次安装/救急）

改托管只需改 UPDATE_BASE_URL 或设置环境变量 ZMN_UPDATE_FEED；
config.json 的 update_feed_url 可临时覆盖（测试/应急）。
"""

APP_NAME = "zhuomuniao"
CHANNEL = "stable"

# 稳定域名优先；当前直接用 OSS 默认域名，后续可换自定义域名而不改代码。
UPDATE_BASE_URL = (
    "https://dhxy-ascript.oss-cn-beijing.aliyuncs.com/zhuomuniao/stable"
)
UPDATE_FEED_URL = UPDATE_BASE_URL + "/update.json"

# 检查清单超时（秒）。清单很小，8 秒足够；失败静默重试 1 次。
CHECK_TIMEOUT = 8
# 下载超时（秒）：单次 socket 读操作超时，不是整个下载总时长。
DOWNLOAD_TIMEOUT = 30
# 启动后延迟多久做后台检查（秒）：避开启动阶段 OCR 预热与授权校验。
STARTUP_CHECK_DELAY = 8
# 自动检查最小间隔（秒）：24 小时。
AUTO_CHECK_INTERVAL = 24 * 3600
# 保留的旧版本备份份数。
KEEP_BACKUPS = 1
