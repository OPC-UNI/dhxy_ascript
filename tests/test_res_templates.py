# -*- coding: utf-8 -*-
"""模板图片格式约束：res/img 下的模板必须是 RGB/RGBA。

背景（2026-11-18 打包版实机崩溃）：SDK 1.3.29+ 的
`AsCv.find_all_template` 对模板走 `to_numpy(im_search, preserve_alpha=True)`
→ `cv2.imdecode(..., IMREAD_UNCHANGED)`。灰度模式（mode L）PNG 会被解码成
2 维单通道数组，随后 `cv2.cvtColor(im_search, COLOR_BGR2GRAY)` 抛
`cv2.error: (-15:Bad number of channels) ... 'scn' is 1`，任务线程直接崩。
旧 SDK（如 1.3.4）用 IMREAD_COLOR 强制转 3 通道，所以同一张图在旧 SDK
源码环境正常、升级 SDK 的打包版才炸——靠实机才能发现，必须有静态测试兜住。

这里直接按 SDK 的解码方式（IMREAD_UNCHANGED）断言通道数，而不是只查
PIL mode：将来出现灰度+alpha、16 位灰度等变体也能拦住。

同时校验模板命名与引用一致性（P3 命名治理）：文件名小写、新增模板
必须 `<模块前缀>_<用途>.png`（历史裸名只减不增）、代码引用与文件双向
存在（缺失模板/未引用模板都会失败，保留名单只减不增）。
"""
import ast
import re
from pathlib import Path

import cv2
import numpy as np

RES_IMG = Path(__file__).resolve().parent.parent / "res" / "img"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def test_res_img_templates_decode_to_3_or_4_channels():
    bad = []
    for path in sorted(RES_IMG.rglob("*")):
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        data = np.fromfile(str(path), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None:
            bad.append(f"{path.relative_to(RES_IMG)}: 解码失败")
        elif img.ndim != 3 or img.shape[2] not in (3, 4):
            bad.append(f"{path.relative_to(RES_IMG)}: shape={img.shape}")
    assert not bad, (
        "模板必须是 RGB/RGBA（灰度模板在 SDK 1.3.29+ 的 "
        "IMREAD_UNCHANGED 路径下导致 cvtColor 崩溃）: " + "; ".join(bad)
    )


# ---- 模板命名与引用一致性（P3 命名治理，2026-11-18） ----
ROOT = Path(__file__).resolve().parent.parent
SOURCE_FILES = [ROOT / "main.py", *sorted((ROOT / "src").rglob("*.py"))]
# 数据文件中的模板引用（如 res/huafu_runes.json 的符文掩码表）：JSON
# 不能 ast.parse，按文本扫描文件名 token（2026-11-28 符文配方库）。
DATA_FILES = sorted((ROOT / "res").glob("*.json"))
DATA_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:png|ico)")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]*(\.[a-z0-9]+)?$")

# 运行时产物（诊断截图/失败落盘），不是模板
RUNTIME_OUTPUTS = {"capture.png", "first_frame.png",
                   "tmp_err_img.png", "tmp_team_err.png"}

# 历史裸名模板（无模块前缀）：保持引用稳定，不强制改名；只减不增。
# 新增模板必须含 `_`（<模块前缀>_<用途>.png），否则测试失败。
LEGACY_BARE_NAMES = {
    "auto.png", "auto2.png", "awang.png", "awang2.png", "awang3.png",
    "bag.png", "chuangjianduiwu.png", "chuyijuebao.png", "duchong1.png",
    "duchong2.png", "duchong3.png", "duiwumianban.png", "duiwuxinxi.png",
    "fangyu.png", "fashu.png", "give.png", "goumai.png", "jiesanduiwu.png",
    "kaicaishitou.png", "likaiduiwu.png", "logo.ico", "renwushuxing.png",
    "renwuzhuizong.png", "sheyaoxiang.png", "xiuluohezi.png", "zhanggui.png",
    "zhanggui2.png", "zuoqi.png",
}

# 故意保留但当前代码未引用的素材（只减不增：删除前先确认用户素材用途）
UNREFERENCED_KEEP = {
    "duchong1.png": "旧拍毒虫模板，已被 kezhan_duchong_* 替代",
    "duchong2.png": "旧拍毒虫模板，已被 kezhan_duchong_* 替代",
    "duchong3.png": "旧拍毒虫模板，已被 kezhan_duchong_* 替代",
    "fuzhuangdian_xianglian1.png": "服装店项链备选，未接入",
    "give.png": "旧给与整条横幅，已被 give_title/give_btn 替代",
    "mingwang_nitianmo.png": "名望旧 NPC 模板，领取链改为坐标门控后不再引用",
    "mingwang_nitianmo2.png": "名望旧 NPC 模板，领取链改为坐标门控后不再引用",
    "mingwang_wuzunshen.png": "名望旧 NPC 模板，领取链改为坐标门控后不再引用",
    "mingwang_wuzunshen2.png": "名望旧 NPC 模板，领取链改为坐标门控后不再引用",
    "xiuluo_huichengfu1.png": "回城符备选模板，未接入",
}


def _template_files():
    return {p.name for p in RES_IMG.iterdir() if p.is_file()}


def _referenced_names():
    refs = set()
    for src in SOURCE_FILES:
        tree = ast.parse(src.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                name = Path(node.value.strip()).name
                if NAME_RE.match(name) and name.lower().endswith(
                        (".png", ".ico")):
                    refs.add(name)
    for src in DATA_FILES:
        for token in DATA_TOKEN_RE.findall(src.read_text(encoding="utf-8")):
            name = Path(token).name
            if NAME_RE.match(name):
                refs.add(name)
    return refs


def test_template_filenames_are_lowercase_snake_case():
    """模板文件名统一小写；新增模板按 `<模块前缀>_<用途>.png` 命名。"""
    bad = sorted(name for name in _template_files()
                 if not NAME_RE.match(name))
    assert bad == [], f"模板文件名不合规: {bad}"


def test_res_img_stays_flat():
    """模板目录保持平铺，不建子目录（子目录会绕过命名/引用检查）。"""
    dirs = sorted(p.name for p in RES_IMG.iterdir() if p.is_dir())
    assert dirs == [], f"res/img 保持平铺，不得建子目录: {dirs}"


def test_new_templates_must_use_prefix_naming():
    """新增模板必须 `<模块前缀>_<用途>.png`；历史裸名只减不增。

    裸名白名单是存量资产：新增无 `_` 的文件会失败；改名/删除历史裸名后
    必须同步移除条目，防止白名单悄悄膨胀。
    """
    files = _template_files()
    bare = {name for name in files if "_" not in Path(name).stem}
    unexpected = sorted(bare - LEGACY_BARE_NAMES)
    assert unexpected == [], (
        f"新增模板必须带模块前缀（<模块前缀>_<用途>.png）: {unexpected}")
    stale = sorted(LEGACY_BARE_NAMES - files)
    assert stale == [], f"历史裸名白名单存在过期条目: {stale}"


def test_whitelist_sizes_are_locked():
    """两个白名单「只减不增」：增长必须是一次显式、可 review 的改动。

    上面的双向/stale 测试保证条目与磁盘一致，但拦不住「新增裸名模板时
    顺手往 LEGACY_BARE_NAMES 加一行」或「新增死模板时登记进
    UNREFERENCED_KEEP」。这里锁死数量，让增长必须显式改断言。
    """
    assert len(LEGACY_BARE_NAMES) == 28
    assert len(UNREFERENCED_KEEP) == 10


def test_referenced_templates_exist():
    """代码引用的模板必须真实存在（防止写错路径/漏提交素材）。"""
    files = _template_files()
    missing = sorted(name for name in _referenced_names()
                     if name not in files and name not in RUNTIME_OUTPUTS)
    assert missing == [], f"引用了不存在的模板: {missing}"


def test_no_unreferenced_templates():
    """每个模板都被代码引用，或登记在只减不增的保留名单。"""
    dead = sorted(name for name in _template_files()
                  - _referenced_names() - RUNTIME_OUTPUTS
                  if name not in UNREFERENCED_KEEP)
    assert dead == [], f"存在未引用的模板（确认后删除或登记理由）: {dead}"


def test_unreferenced_keep_list_has_no_stale_entries():
    """保留名单只减不增：已删除/已引用的条目必须同步移除。"""
    files = _template_files()
    refs = _referenced_names()
    stale = sorted(name for name in UNREFERENCED_KEEP
                   if name not in files or name in refs)
    assert stale == [], f"保留名单存在过期条目: {stale}"
