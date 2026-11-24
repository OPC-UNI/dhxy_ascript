"""world_target：手动寻路到达后世界区名字 OCR 定位（2026-11-24）。

验证点：
- 完整目标名精确匹配 → 点击名字框中心正上方 40px（角色本体）
- 玩家名与 NPC 名同排合并成一行 → 按子串字符位置收窄点击点
- 精确全未命中才启用模糊兜底（4 字名 1 字误读 = 0.75，截图实测）
- 修罗「xx修罗」后缀匹配：行尾 4 字换算子串框（合并行不点玩家）
- 任务追踪面板同名文案按动态标题条矩形排除；标题条未命中不静态排除
"""
from PIL import Image

from src.device import world_target


def _img():
    return Image.new("RGB", (1378, 831))


def _env(monkeypatch, items, panel=None):
    monkeypatch.setattr(world_target.game_map, "ocr_regions",
                        lambda img, bands: (list(items), None))
    monkeypatch.setattr(world_target.task_tracker, "track_content_region",
                        lambda img_np: panel)


def test_find_named_points_exact_click_offset(monkeypatch):
    """精确命中：点击点 = 名字框中心正上方 40px（用户标定）。"""
    _env(monkeypatch, [("智慧魔尊", (510, 441, 578, 464), 0.995)])
    pts = world_target.find_named_points(_img(), ("智慧魔尊",))
    assert pts == [("智慧魔尊", (544, 401))]


def test_find_named_points_merged_line_narrows_subrect(monkeypatch):
    """合并行「玩家名+NPC名」：整行中心会点到玩家，须按子串位置收窄。"""
    _env(monkeypatch, [("20可乐可口愤怒的黄袍怪", (100, 500, 400, 520), 0.9)])
    pts = world_target.find_named_points(_img(), ("愤怒的黄袍怪",))
    # 子串从第 6 字起（12 字行）→ 子框 (250,500,400,520) → 中心 325
    assert pts == [("20可乐可口愤怒的黄袍怪", (325, 460))]


def test_find_named_points_fuzzy_only_after_exact_miss(monkeypatch):
    """精确未命中 → 模糊兜底（2026-11-24 截图：智慧魔尊读成岩慧魔尊）。"""
    items = [("「可岩慧魔尊", (1240, 581, 1338, 605), 0.902)]
    _env(monkeypatch, items)
    assert world_target.find_named_points(_img(), ("神通魔尊",)) == []
    pts = world_target.find_named_points(_img(), ("智慧魔尊",))
    assert pts == [("「可岩慧魔尊", (1289, 541))]


def test_find_named_points_exact_beats_fuzzy(monkeypatch):
    """同屏有精确命中时不做模糊（防相似名怪误点）。"""
    items = [("「可岩慧魔尊", (1240, 581, 1338, 605), 0.902),
             ("智慧魔尊", (510, 441, 578, 464), 0.995)]
    _env(monkeypatch, items)
    pts = world_target.find_named_points(_img(), ("智慧魔尊",))
    assert pts == [("智慧魔尊", (544, 401))]


def test_find_named_points_skips_panel_and_low_score(monkeypatch):
    """追踪面板同名文案/低置信度行不参与（面板行 x=40 在面板矩形内）。"""
    items = [("狂风修罗", (40, 260, 120, 280), 0.95),
             ("白龙马", (600, 500, 680, 520), 0.90),
             ("白龙马", (600, 560, 680, 580), 0.30)]
    _env(monkeypatch, items, panel=(0, 200, 400, 600))
    pts = world_target.find_named_points(_img(), ("白龙马",))
    assert pts == [("白龙马", (640, 460))]


def test_find_named_points_no_static_panel_fallback(monkeypatch):
    """标题条未命中（Alt+A 隐藏界面后）不做静态左列排除——左缘 NPC 可点。"""
    items = [("智慧魔尊", (30, 600, 110, 620), 0.95)]
    _env(monkeypatch, items, panel=None)
    pts = world_target.find_named_points(_img(), ("智慧魔尊",))
    assert pts == [("智慧魔尊", (70, 560))]


def test_find_suffix_points_takes_last_four_chars(monkeypatch):
    """修罗「xx修罗」：合并行按行尾 4 字收窄（NPC 名在行尾）。"""
    _env(monkeypatch, [("20可乐可口狂风修罗", (100, 500, 400, 520), 0.9)])
    pts = world_target.find_suffix_points(_img(), ("修罗", "修羅"))
    # 10 字行取行尾 4 字（第 6 字起）→ 子框 (280,500,400,520) → 中心 340
    assert pts == [("20可乐可口狂风修罗", (340, 460))]


def test_find_suffix_points_requires_suffix_and_dedupes(monkeypatch):
    """非后缀行不点；两段条带重叠区的重复命中按点击点去重。"""
    items = [("消灭修罗阻止它为非作歹", (100, 500, 400, 520), 0.9),
             ("狂风修罗", (600, 500, 680, 520), 0.9),
             ("狂风修罗", (602, 501, 682, 521), 0.88)]
    _env(monkeypatch, items)
    pts = world_target.find_suffix_points(_img(), ("修罗",))
    assert pts == [("狂风修罗", (640, 460))]


def test_find_suffix_points_empty_suffixes(monkeypatch):
    _env(monkeypatch, [])
    assert world_target.find_suffix_points(_img(), ()) == []


def test_scan_name_lines_returns_scored_triples(monkeypatch):
    """诊断用原始扫描：带置信度三元组，低分/面板行同样过滤。"""
    items = [("智慧魔尊", (510, 441, 578, 464), 0.995),
             ("噪声", (400, 260, 460, 280), 0.30),
             ("面板文案", (40, 260, 120, 280), 0.95)]
    _env(monkeypatch, items, panel=(0, 200, 400, 600))
    assert world_target.scan_name_lines(_img()) == [
        ("智慧魔尊", (510, 441, 578, 464), 0.995)]


def test_matchers_reuse_scanned_lines_without_ocr(monkeypatch):
    """诊断一次扫描复用：传 lines 时不再触发 OCR。"""
    calls = []

    def _ocr(img, bands):
        calls.append(1)
        return [], None

    monkeypatch.setattr(world_target.game_map, "ocr_regions", _ocr)
    lines = [("20可乐可口智慧魔尊", (100, 500, 400, 520), 0.9)]
    assert world_target.find_named_points(
        _img(), ("智慧魔尊",), lines=lines) == [
            ("20可乐可口智慧魔尊", (340, 460))]
    assert world_target.find_suffix_points(
        _img(), ("修罗",), lines=lines) == []
    assert calls == []
