---
name: ocr-image
description: 用项目自身的 OCR 引擎识别本地图片并返回文字。当用户附带的截图无法直接查看（Read 返回 Unsupported Image）、或用户要求读取图片中的文字/界面内容（对话框文案、选项、按钮、坐标）时使用。识别引擎与自动化脚本同一套（src/map.py RapidOCR），结果即脚本所见。
---

# OCR 图片

## 何时使用

- 用户附带图片但 Read 工具返回 `Unsupported Image`（本会话出现过）
- 用户要求读取截图内容：对话框文案、选项文字、界面按钮、UI 坐标等
- 需要与自动化脚本「所见一致」的识别结果（同一引擎同一区域，识别结论可直接用于写脚本判定）

## 步骤

1. 从用户消息中取得图片路径（附件路径，形如 `C:\Users\...\Temp\claude\...\images\1.png`）；用户没附图片时先询问路径。
2. 在项目根目录用 Bash 执行下面的 Python（`cd` 到项目根目录，`sys.path.insert(0, ".")` 加载 src.map）：

```python
python - <<'EOF'
import sys
sys.path.insert(0, ".")
from PIL import Image
from src import map as game_map
img = Image.open(r"<图片绝对路径>")
items, _ = game_map.ocr_regions(img, ((0, 0, img.size[0], img.size[1]),))
lines = [f"{s:.2f} ({r[0]},{r[1]},{r[2]},{r[3]}) {t}"
         for t, r, s in sorted(items, key=lambda x: (x[1][1], x[1][0]))]
with open("_ocr_out.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("items:", len(lines), "size:", img.size)
EOF
```

3. 用 Read 工具读 `_ocr_out.txt`，每行格式：`置信度 (左,上,右,下) 文字`。
4. 用 Bash 删除临时文件：`rm -f _ocr_out.txt`。
5. 按用户需求解读：文案内容、选项文字与中心坐标 `((左+右)/2, (上+下)/2)`、区域布局等。

## 要点

- **控制台是 GBK，识别中文直接 print 会乱码**——识别结果一律写 UTF-8 文件后用 Read 读，这是本 skill 存在的核心原因。
- 识别引擎与自动化脚本同一套（RapidOCR + 同参数），结果即脚本运行时所见的 OCR 输出，可直接用来定匹配键、点击坐标。
- **只需要部分区域时换 regions**：如对话框区 `((400, 270, 980, 600),)`（游戏窗口 1378x831），全帧 ~1.2s、区域 ~0.1-0.2s，更快更准。
- 识别为空：检查图片路径有效性与图片内容（可能本来就是无文字画面）；需要更细致时可用 `ocr_zoom(img)` 或调整区域。
- 首次调用引擎有约 1s 模型加载，属正常现象。
- 多张图片：逐张跑上面的脚本（不同文件名），或在一个脚本里循环处理。
