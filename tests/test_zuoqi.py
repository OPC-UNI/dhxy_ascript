"""zuoqi.py：飞行驭器面板开合（2026-11-07 用户提供模板）。

验证点：
- 四模板校准：目标帧（贴入原模板）唯一命中 ≥0.8、纯噪声帧低于阈值
- 关闭按钮与队伍面板关闭按钮同款（zudui_close 匹配它 0.993）——关闭
  必须先校验飞行驭器面板打开（行为测试覆盖）
- 识别路径：座骑按钮点击 → 菜单项点击 → 面板校验
- 高性能路径：Alt+U 直接打开
- 战斗中拒绝（飞行驭器只能非战斗使用）
"""
import numpy as np
from PIL import Image
from ascript.windows.system import R

from src.device import zuoqi


class _FakeWin:
    """capture 恒返回非空帧（find_template_in 已 mock，帧内容无关）。"""

    def capture(self, force_activate=False):
        return Image.new("RGB", (100, 100))


def _frame(seed=20261107):
    """合成噪声帧（尺寸同游戏窗口）。"""
    r = np.random.default_rng(seed)
    return r.integers(0, 256, (831, 1378, 3), dtype=np.uint8)


def _paste(icon, seed=20261107):
    """把 res/img 模板贴入噪声帧（BGR）。"""
    import cv2
    img = Image.open(R.img(icon)).convert("RGB")
    tpl = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    out = _frame(seed)
    th, tw = tpl.shape[:2]
    out[400:400 + th, 600:600 + tw] = tpl
    return out


def _clicker(monkeypatch, find_in=None, find_win=None, panel_seq=None,
             menu_seq=None, high_perf=False, alt_u=True, in_battle=False):
    """搭 open/close 桩环境，返回记录器 dict。"""
    env = {"clicks": [], "alt_u": 0, "menu_closed": 0, "panel_checks": 0}
    win = _FakeWin()

    def fake_panel_open(w):
        env["panel_checks"] += 1
        return bool(panel_seq.pop(0)) if panel_seq else False

    monkeypatch.setattr(zuoqi, "is_in_battle", lambda w: in_battle)
    monkeypatch.setattr(zuoqi, "is_feixingyuqi_panel_open", fake_panel_open)
    monkeypatch.setattr(zuoqi, "app_utils", _FakeAppUtils(high_perf))
    monkeypatch.setattr(zuoqi, "_press_alt_u",
                        lambda w: env.__setitem__("alt_u", env["alt_u"] + 1) or alt_u)
    monkeypatch.setattr(zuoqi, "human_click",
                        lambda w, x, y: env["clicks"].append((x, y)))
    monkeypatch.setattr(zuoqi, "_close_menu_if_open",
                        lambda w: env.__setitem__("menu_closed", env["menu_closed"] + 1)
                        or (True, "座骑菜单已关闭"))
    monkeypatch.setattr(zuoqi, "time", _FakeTime())

    def fake_find_in(img, icon, threshold=0.8, region=None):
        if find_in is None:
            return None
        return find_in(icon)

    monkeypatch.setattr(zuoqi.game_map, "find_template_in", fake_find_in)
    monkeypatch.setattr(zuoqi.game_map, "find_template",
                        lambda w, icon, threshold=0.8: find_win if find_win else None)

    def fake_menu_open(img):
        return bool(menu_seq.pop(0)) if menu_seq else False

    monkeypatch.setattr(zuoqi, "_menu_open_in", fake_menu_open)
    return env


class _FakeAppUtils:
    def __init__(self, high_perf):
        self._high_perf = high_perf

    def high_perf_on(self):
        return self._high_perf

    def silent_mode_on(self):
        return False

    def keyboard_ok(self, win=None):
        return True


class _FakeTime:
    """time 模块桩：sleep 空操作（time.time 保留原语义不需要）。"""

    def sleep(self, s):
        pass


# ---- 模板校准（合成帧） ----

def test_templates_target_unique_hit():
    """四模板目标帧唯一命中 ≥0.8（0.8 阈值）。"""
    for icon in (zuoqi.ZUOQI_ICON, zuoqi.ZUOQI_FEIYINGYUQI_ICON,
                 zuoqi.FEIYINGYUQI_PANEL_ICON, zuoqi.FEIYINGYUQI_PANEL_CLOSE_ICON):
        hits = zuoqi.game_map.match_frame_all(_paste(icon), icon, 0.8)
        assert len(hits) == 1, icon
        assert hits[0][0] >= 0.8, icon


def test_templates_noise_below_threshold():
    """纯噪声帧（非目标场景）四模板最佳分低于 0.8 阈值。"""
    for icon in (zuoqi.ZUOQI_ICON, zuoqi.ZUOQI_FEIYINGYUQI_ICON,
                 zuoqi.FEIYINGYUQI_PANEL_ICON, zuoqi.FEIYINGYUQI_PANEL_CLOSE_ICON):
        score, _ = zuoqi.game_map.best_match_np(_frame(), icon)
        assert score < 0.8, icon


def test_close_button_same_style_as_team_close():
    """飞行驭器关闭模板会匹配队伍面板关闭按钮（贴 zudui_close 帧匹配
    0.993 ≥ 0.8）——不先校验飞行驭器面板打开就点关闭，会点到队伍面板
    的同款关闭按钮（关闭前置校验的根因）。实机帧（2026-11-07）反向
    zudui_close 匹配飞行驭器面板关闭按钮 0.984，两按钮双向同款。"""
    frame = _paste("zudui_close.png")
    score, _ = zuoqi.game_map.best_match_np(frame, zuoqi.FEIYINGYUQI_PANEL_CLOSE_ICON)
    assert score >= 0.8


def test_zuoqi_not_confused_as_panel_or_item():
    """座骑按钮不得被面板/菜单项模板命中，面板不得被座骑/菜单项命中。"""
    f1 = _paste(zuoqi.ZUOQI_ICON)
    assert zuoqi.game_map.best_match_np(f1, zuoqi.FEIYINGYUQI_PANEL_ICON)[0] < 0.8
    assert zuoqi.game_map.best_match_np(f1, zuoqi.ZUOQI_FEIYINGYUQI_ICON)[0] < 0.8
    f2 = _paste(zuoqi.FEIYINGYUQI_PANEL_ICON)
    assert zuoqi.game_map.best_match_np(f2, zuoqi.ZUOQI_ICON)[0] < 0.8
    assert zuoqi.game_map.best_match_np(f2, zuoqi.ZUOQI_FEIYINGYUQI_ICON)[0] < 0.8


# ---- 打开面板（行为） ----

def test_open_refuses_in_battle(monkeypatch):
    """战斗中拒绝（飞行驭器只能非战斗使用），不点击。"""
    env = _clicker(monkeypatch, in_battle=True)
    ok, desc = zuoqi.open_feixingyuqi_panel(_FakeWin())
    assert not ok and "战斗" in desc
    assert env["clicks"] == [] and env["alt_u"] == 0


def test_open_reuses_when_panel_open(monkeypatch):
    """面板已打开：复用不重复点击（开关式交互，重复点击会关闭）。"""
    env = _clicker(monkeypatch, panel_seq=[True])
    ok, desc = zuoqi.open_feixingyuqi_panel(_FakeWin())
    assert ok and "复用" in desc
    assert env["clicks"] == [] and env["alt_u"] == 0


def test_open_recognition_path_success(monkeypatch):
    """识别路径：座骑按钮点击 → 菜单出现 → 菜单项点击 → 面板出现。"""
    def find_in(icon):
        return (120, 300) if icon == zuoqi.ZUOQI_ICON else (140, 350)

    env = _clicker(monkeypatch, find_in=find_in,
                   panel_seq=[False, True], menu_seq=[False, True, True])
    ok, desc = zuoqi.open_feixingyuqi_panel(_FakeWin())
    assert ok and "已打开" in desc
    assert env["clicks"] == [(120, 300), (140, 350)]


def test_open_menu_not_appearing(monkeypatch):
    """点座骑按钮后菜单未出现：失败，不点菜单项。"""
    def find_in(icon):
        return (120, 300) if icon == zuoqi.ZUOQI_ICON else None

    env = _clicker(monkeypatch, find_in=find_in,
                   panel_seq=[False], menu_seq=[False, False, False])
    ok, desc = zuoqi.open_feixingyuqi_panel(_FakeWin())
    assert not ok and "未出现菜单" in desc
    assert env["clicks"] == [(120, 300)]


def test_open_menu_item_not_found(monkeypatch):
    """菜单中未定位到「飞行驭器」：失败并兜底关闭菜单。"""
    def find_in(icon):
        return (120, 300) if icon == zuoqi.ZUOQI_ICON else None

    env = _clicker(monkeypatch, find_in=find_in,
                   panel_seq=[False], menu_seq=[True, True])
    ok, desc = zuoqi.open_feixingyuqi_panel(_FakeWin())
    assert not ok and "未定位到" in desc
    assert env["menu_closed"] == 1


def test_open_panel_never_appears(monkeypatch):
    """点菜单项后面板未出现：失败并兜底关闭菜单。"""
    def find_in(icon):
        return (120, 300) if icon == zuoqi.ZUOQI_ICON else (140, 350)

    env = _clicker(monkeypatch, find_in=find_in,
                   panel_seq=[False, False, False], menu_seq=[True, True])
    ok, desc = zuoqi.open_feixingyuqi_panel(_FakeWin())
    assert not ok and "面板未出现" in desc
    assert env["clicks"] == [(120, 300), (140, 350)]
    assert env["menu_closed"] == 1


def test_open_high_perf_alt_u(monkeypatch):
    """高性能：Alt+U 直接打开（免识别点击），面板出现即成功。"""
    env = _clicker(monkeypatch, high_perf=True, alt_u=True,
                   panel_seq=[False, True])
    ok, desc = zuoqi.open_feixingyuqi_panel(_FakeWin())
    assert ok and "Alt+U" in desc
    assert env["alt_u"] == 1 and env["clicks"] == []


def test_open_high_perf_alt_u_not_delivered(monkeypatch):
    """高性能 Alt+U 未投递（前台/静默通道失败）：失败不点击。"""
    env = _clicker(monkeypatch, high_perf=True, alt_u=False,
                   panel_seq=[False])
    ok, desc = zuoqi.open_feixingyuqi_panel(_FakeWin())
    assert not ok and "未投递" in desc
    assert env["clicks"] == []


# ---- 关闭面板（行为） ----

def test_close_success(monkeypatch):
    """面板打开：点关闭按钮 → 校验面板消失即成功。"""
    env = _clicker(monkeypatch, find_win=(200, 150),
                   panel_seq=[True, False])
    ok, desc = zuoqi.close_feixingyuqi_panel(_FakeWin())
    assert ok and "已关闭" in desc
    assert env["clicks"] == [(200, 150)]


def test_close_noop_when_panel_closed(monkeypatch):
    """面板本就未打开：空操作成功，不点击。"""
    env = _clicker(monkeypatch, find_win=(200, 150),
                   panel_seq=[False])
    ok, desc = zuoqi.close_feixingyuqi_panel(_FakeWin())
    assert ok and "本就未打开" in desc
    assert env["clicks"] == []


def test_close_panel_stays_open(monkeypatch):
    """点击关闭按钮后面板仍可见：报失败不盲点（同款关闭按钮风险，
    失败交由调用方/人工处理）。"""
    env = _clicker(monkeypatch, find_win=(200, 150),
                   panel_seq=[True, True, True, True, True, True, True, True, True])
    ok, desc = zuoqi.close_feixingyuqi_panel(_FakeWin())
    assert not ok and "关闭失败" in desc
    assert env["clicks"] == [(200, 150)]


# ---- 飞行/降落按钮（2026-11-07：飞行按钮=未飞行，降落按钮=飞行中） ----

def _pil_frame(seed=20261107):
    """合成噪声帧（PIL，尺寸同游戏窗口）。"""
    r = np.random.default_rng(seed)
    return Image.fromarray(r.integers(0, 256, (831, 1378, 3), dtype=np.uint8))


def _pil_paste(frame, icon, x, y):
    """把 res/img 模板（转 RGBA）贴入 PIL 帧。"""
    tpl = Image.open(R.img(icon)).convert("RGBA")
    out = frame.copy()
    out.paste(tpl, (x, y), tpl)
    return out


def test_feixing_jiangluo_calibration():
    """飞行/降落模板目标帧唯一命中 ≥0.8、噪声低于阈值、互匹配低于
    0.8（合成帧实测 0.548/0.549——同款样式但文字不同，双模板比分数
    仍有意义，防渲染变体互误匹配）。"""
    for icon in (zuoqi.FEIYINGYUQI_FEIXING_ICON, zuoqi.FEIYINGYUQI_JIANGLUO_ICON):
        hits = zuoqi.game_map.match_frame_all(_paste(icon), icon, 0.8)
        assert len(hits) == 1 and hits[0][0] >= 0.8, icon
        score, _ = zuoqi.game_map.best_match_np(_frame(), icon)
        assert score < 0.8, icon
    f1 = _paste(zuoqi.FEIYINGYUQI_FEIXING_ICON)
    f2 = _paste(zuoqi.FEIYINGYUQI_JIANGLUO_ICON)
    assert zuoqi.game_map.best_match_np(f1, zuoqi.FEIYINGYUQI_JIANGLUO_ICON)[0] < 0.8
    assert zuoqi.game_map.best_match_np(f2, zuoqi.FEIYINGYUQI_FEIXING_ICON)[0] < 0.8


def test_classify_frame_flying():
    """帧内降落按钮在场 → flying（飞行中），降落分达标且更高。"""
    frame = _pil_paste(_pil_frame(), zuoqi.FEIYINGYUQI_JIANGLUO_ICON, 600, 500)
    state, f, j = zuoqi._classify_flight_frame(frame)
    assert state == "flying"
    assert j[0] >= zuoqi._TEMPLATE_THRESHOLD and j[0] > f[0]


def test_classify_frame_ground():
    """帧内飞行按钮在场 → ground（未飞行），飞行分达标且更高。"""
    frame = _pil_paste(_pil_frame(), zuoqi.FEIYINGYUQI_FEIXING_ICON, 600, 500)
    state, f, j = zuoqi._classify_flight_frame(frame)
    assert state == "ground"
    assert f[0] >= zuoqi._TEMPLATE_THRESHOLD and f[0] > j[0]


def test_classify_frame_unclear():
    """两按钮均不在场（面板未打开/噪声帧）→ unclear，不算确定性状态。"""
    state, f, j = zuoqi._classify_flight_frame(_pil_frame())
    assert state == "unclear"
    assert f[0] < zuoqi._TEMPLATE_THRESHOLD and j[0] < zuoqi._TEMPLATE_THRESHOLD


def _flight_clicker(monkeypatch, pre_state, open_ok=True, in_battle=False,
                    post_seq=None):
    """搭 ensure_flying/ensure_landed 桩环境，返回记录器 dict。

    pre_state：点击前的分类状态（flying/ground/unclear）；
    post_seq：点击后等待循环逐帧分类状态序列（耗尽复用末项）。
    """
    env = {"clicks": [], "opened": 0}
    monkeypatch.setattr(zuoqi, "is_in_battle", lambda w: in_battle)
    monkeypatch.setattr(zuoqi, "open_feixingyuqi_panel",
                        lambda w: env.__setitem__("opened", env["opened"] + 1)
                        or (open_ok, "飞行驭器面板已打开"))
    monkeypatch.setattr(zuoqi, "human_click",
                        lambda w, x, y: env["clicks"].append((x, y)))
    monkeypatch.setattr(zuoqi, "time", _FakeTime())
    if pre_state == "flying":
        pre = ("flying", (0.5, (0, 0)), (0.95, (140, 350)))
    elif pre_state == "ground":
        pre = ("ground", (0.95, (120, 300)), (0.5, (0, 0)))
    else:
        pre = ("unclear", (0.4, (0, 0)), (0.4, (0, 0)))
    monkeypatch.setattr(zuoqi, "_classify_current", lambda w: pre)
    post_seq = list(post_seq or ["unclear"])
    calls = {"i": 0}

    def fake_post(img):
        s = post_seq[calls["i"]] if calls["i"] < len(post_seq) else post_seq[-1]
        calls["i"] += 1
        if s == "flying":
            return "flying", (0.5, (0, 0)), (0.95, (140, 350))
        if s == "ground":
            return "ground", (0.95, (120, 300)), (0.5, (0, 0))
        return "unclear", (0.4, (0, 0)), (0.4, (0, 0))

    monkeypatch.setattr(zuoqi, "_classify_flight_frame", fake_post)
    return env


def test_ensure_flying_already_flying(monkeypatch):
    """已在飞行中（降落按钮在场）：直接返回成功，不点击。"""
    env = _flight_clicker(monkeypatch, pre_state="flying")
    ok, desc = zuoqi.ensure_flying(_FakeWin())
    assert ok and "已在飞行中" in desc
    assert env["clicks"] == []


def test_ensure_flying_take_off(monkeypatch):
    """未飞行：点飞行按钮 → 降落按钮出现即起飞成功。"""
    env = _flight_clicker(monkeypatch, pre_state="ground",
                          post_seq=["flying"])
    ok, desc = zuoqi.ensure_flying(_FakeWin())
    assert ok and "已起飞" in desc
    assert env["clicks"] == [(120, 300)]


def test_ensure_flying_take_off_not_confirmed(monkeypatch):
    """点飞行按钮后降落按钮始终未出现：报失败（不误报成功）。"""
    env = _flight_clicker(monkeypatch, pre_state="ground",
                          post_seq=["ground"])
    ok, desc = zuoqi.ensure_flying(_FakeWin())
    assert not ok and "未确认起飞" in desc
    assert env["clicks"] == [(120, 300)]


def test_ensure_flying_unclear_no_click(monkeypatch):
    """两按钮均未命中（面板渲染中）：报失败绝不盲点。"""
    env = _flight_clicker(monkeypatch, pre_state="unclear")
    ok, desc = zuoqi.ensure_flying(_FakeWin())
    assert not ok and "未定位到飞行按钮" in desc
    assert env["clicks"] == []


def test_ensure_flying_refuses_in_battle(monkeypatch):
    """战斗中拒绝（飞行驭器只能非战斗使用），不开面板不点击。"""
    env = _flight_clicker(monkeypatch, pre_state="ground", in_battle=True)
    ok, desc = zuoqi.ensure_flying(_FakeWin())
    assert not ok and "战斗" in desc
    assert env["opened"] == 0 and env["clicks"] == []


def test_ensure_flying_panel_open_fails(monkeypatch):
    """面板打不开：透传失败描述，不点击。"""
    env = _flight_clicker(monkeypatch, pre_state="ground", open_ok=False)
    ok, desc = zuoqi.ensure_flying(_FakeWin())
    assert not ok and "面板" in desc
    assert env["clicks"] == []


def test_ensure_landed_land(monkeypatch):
    """飞行中：点降落按钮 → 飞行按钮出现即降落成功。"""
    env = _flight_clicker(monkeypatch, pre_state="flying",
                          post_seq=["ground"])
    ok, desc = zuoqi.ensure_landed(_FakeWin())
    assert ok and "已降落" in desc
    assert env["clicks"] == [(140, 350)]


def test_ensure_landed_already_ground(monkeypatch):
    """已降落（飞行按钮在场）：直接返回成功，不点击。"""
    env = _flight_clicker(monkeypatch, pre_state="ground")
    ok, desc = zuoqi.ensure_landed(_FakeWin())
    assert ok and "已降落" in desc
    assert env["clicks"] == []


def test_ensure_landed_not_confirmed(monkeypatch):
    """点降落按钮后飞行按钮始终未出现：报失败。"""
    env = _flight_clicker(monkeypatch, pre_state="flying",
                          post_seq=["flying"])
    ok, desc = zuoqi.ensure_landed(_FakeWin())
    assert not ok and "未确认降落" in desc
    assert env["clicks"] == [(140, 350)]


# ---- Alt+U 投递异常兜底（2026-11-07 静默按键表缺 'u' ValueError
#      打崩五环进店前降落检查的回归） ----

def test_press_alt_u_silent_exception_returns_false(monkeypatch):
    """静默投递异常（如按键表缺键 ValueError）按未投递返回 False，
    不向外抛——任务流程不得因按键通道问题崩溃。"""
    monkeypatch.setattr(zuoqi.app_utils, "silent_mode_on", lambda: True)

    def boom(win, modifiers, key):
        raise ValueError("不支持的主键: 'u'")

    from src.device import silent_click as _sc
    monkeypatch.setattr(_sc, "send_silent_hotkey", boom)
    ok = zuoqi._press_alt_u(_FakeWin())
    assert ok is False


def test_press_alt_u_physical_not_foreground(monkeypatch):
    """物理路径游戏不在前台：按未投递返回 False，不按键。"""
    pressed = []
    monkeypatch.setattr(zuoqi.app_utils, "silent_mode_on", lambda: False)
    monkeypatch.setattr(zuoqi.app_utils, "keyboard_ok", lambda win: False)
    monkeypatch.setattr(zuoqi.Keyboard, "key_hot", lambda *a: pressed.append(a))
    ok = zuoqi._press_alt_u(_FakeWin())
    assert ok is False and pressed == []
