"""授权应用 ID 配置（构建期烙入，勿在运行时改）。

- 本地默认 24528：本地源码运行/本地打包使用（不消耗生产卡密）。
- GitHub 打包（.github/workflows/build-exe.yml）在 Nuitka 构建前把本文件
  覆写为 23651（生产 app），随 exe 烙入分发给用户。
"""
LICENSE_APP_ID = "24528"
