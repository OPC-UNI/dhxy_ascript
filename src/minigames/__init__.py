"""独立小游戏实现模块。

约定：
- 小游戏模块不得 import 任务模块（tasks/*）；只能依赖 core/device/workflows。
- 模块 import 期零副作用：不截图、不 OCR、不点击、不启动线程，只定义
  常量与函数。
- 触发时机（armed 窗口、限频、armed 重置）由任务侧或 follow 的
  wait_handler/option_click_handler 控制；小游戏模块只在被调用时执行。
- 物理搬迁按「改动即搬迁」：一次性小游戏先留在任务文件，共享/被复用的
  先下沉（如 pochufengyin）。
"""
