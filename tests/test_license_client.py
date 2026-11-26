"""雪松授权客户端单测：验签、过期/撤销/指纹、激活、心跳降级、后台心跳。"""
import base64
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pss

from src.core import license_client as lc


@pytest.fixture(scope="module")
def rsa_key():
    return RSA.generate(2048)


@pytest.fixture(scope="module")
def public_pem(rsa_key):
    return rsa_key.publickey().export_key().decode()


def make_license(key, fingerprint, status="active", start_delta=-1, end_delta=30,
                 **extra):
    now = datetime.now(timezone.utc)
    payload = {
        "license_key": "LIC-DEVICE-TEST",
        "hardware_fingerprint": fingerprint,
        "status": status,
        "start_date": (now + timedelta(days=start_delta)).isoformat(),
        "end_date": (now + timedelta(days=end_delta)).isoformat(),
        "max_activations": 1,
        "authorization_code": "LIC-TEST-CODE",
    }
    payload.update(extra)
    data = json.dumps(payload)
    signature = pss.new(key, salt_bytes=32).sign(SHA256.new(data.encode()))
    outer = {
        "data": data,
        "signature": base64.b64encode(signature).decode(),
        "algorithm": "RSA-PSS-SHA256",
    }
    return base64.b64encode(json.dumps(outer).encode()).decode()


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append((url, json))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(*item)


@pytest.fixture
def client(tmp_path, rsa_key, public_pem, monkeypatch):
    monkeypatch.setattr(lc, "machine_fingerprint", lambda: "fp-test")
    return lc.LicenseClient(
        "http://license.test", public_pem, app_version="2.13.0",
        data_dir=tmp_path / "_license")


def test_verify_valid(rsa_key, public_pem):
    lic = make_license(rsa_key, "fp-test")
    payload = lc.verify_license_file(lic, public_pem, "fp-test")
    assert payload["status"] == "active"
    assert payload["max_activations"] == 1


def test_verify_tampered(rsa_key, public_pem):
    lic = make_license(rsa_key, "fp-test")
    outer = json.loads(base64.b64decode(lic))
    outer["data"] = outer["data"].replace('"active"', '"revoked"')
    bad = base64.b64encode(json.dumps(outer).encode()).decode()
    with pytest.raises(lc.LicenseError) as ei:
        lc.verify_license_file(bad, public_pem, "fp-test")
    assert ei.value.code == "bad_license"
    assert "签名校验失败" in ei.value.message


def test_verify_component_error_not_reported_as_tampered(rsa_key, public_pem,
                                                        monkeypatch):
    """打包缺原生模块（OSError）须带出底层原因，不能误报签名被篡改。

    2026-11-20 v2.14.0 打包事故：pycryptodome 4.x 的 Crypto.Util._base64
    未被 Nuitka 收录，RSA.import_key 抛 OSError，旧实现统一报「签名校验
    失败」，SOURCE 正常、打包版全部无法激活且看不出原因。
    """
    lic = make_license(rsa_key, "fp-test")

    def _broken(_pem):
        raise OSError("Cannot load native module 'Crypto.Util._base64'")

    monkeypatch.setattr(lc.RSA, "import_key", _broken)
    with pytest.raises(lc.LicenseError) as ei:
        lc.verify_license_file(lic, public_pem, "fp-test")
    assert ei.value.code == "crypto"
    assert "Crypto.Util._base64" in ei.value.message


def test_verify_bad_public_key(rsa_key, public_pem):
    lic = make_license(rsa_key, "fp-test")
    with pytest.raises(lc.LicenseError) as ei:
        lc.verify_license_file(lic, "not a pem", "fp-test")
    assert ei.value.code == "bad_license"
    assert "公钥" in ei.value.message


def test_pycryptodome_pinned_below_4_for_nuitka():
    """pycryptodome 必须锁 3.x，否则打包版验签全部失败。

    4.x 的 Crypto.IO.PEM 依赖 ctypes 动态加载的 Crypto.Util._base64
    （RSA.import_key 按需导入），Nuitka ImplicitImports 的原生模块清单
    是硬编码的、不认识它，打包后抛 OSError（2026-11-20 v2.14.0 打包版
    全部无法激活）。依赖被升级/放宽时让 CI 先失败，而不是带着坏包发版。
    """
    import Crypto
    major = int(str(Crypto.__version__).split(".")[0])
    assert major < 4, (
        "pycryptodome %s 与 Nuitka 打包不兼容（见 requirements.txt 注释）"
        % Crypto.__version__)


def test_verify_expired(rsa_key, public_pem):
    lic = make_license(rsa_key, "fp-test", start_delta=-10, end_delta=-1)
    with pytest.raises(lc.LicenseError) as ei:
        lc.verify_license_file(lic, public_pem, "fp-test")
    assert ei.value.code == "expired"


def test_verify_revoked(rsa_key, public_pem):
    lic = make_license(rsa_key, "fp-test", status="revoked")
    with pytest.raises(lc.LicenseError) as ei:
        lc.verify_license_file(lic, public_pem, "fp-test")
    assert ei.value.code == "revoked"


def test_verify_fingerprint_mismatch(rsa_key, public_pem):
    lic = make_license(rsa_key, "other-device")
    with pytest.raises(lc.LicenseError) as ei:
        lc.verify_license_file(lic, public_pem, "fp-test")
    assert ei.value.code == "fingerprint"


def test_activate_and_info(client, rsa_key):
    lic = make_license(rsa_key, "fp-test")
    client._session = FakeSession([(200, {
        "code": lc.CODE_OK,
        "data": {"license_key": "LIC-DEVICE-TEST", "license_file": lic,
                 "heartbeat_interval": 300},
    })])
    info = client.activate("LIC-TEST-CODE")
    assert info["status"] == lc.STATUS_ACTIVE
    assert info["days_left"] >= 1
    assert client.cached_code() == "LIC-TEST-CODE"
    assert client.info()["status"] == lc.STATUS_ACTIVE


def test_activate_limit_message(client, rsa_key):
    client._session = FakeSession([(409, {"code": "300004",
                                          "message": "激活数量已达上限"})])
    with pytest.raises(lc.LicenseError) as ei:
        client.activate("LIC-TEST-CODE")
    assert ei.value.code == "300004"
    assert "激活数量已达上限" in ei.value.message


def test_activate_rate_limited(client):
    client._session = FakeSession([(429, {"code": "429",
                                          "message": "请求过于频繁，请稍后再试"})])
    with pytest.raises(lc.LicenseError) as ei:
        client.activate("LIC-TEST-CODE")
    assert ei.value.code == "429"
    assert "频繁" in ei.value.message


def test_check_offline_fallback(client, rsa_key):
    """宽限期内断网：本地许可证有效即放行（不误伤正常用户）。"""
    lic = make_license(rsa_key, "fp-test")
    client._save_record({
        "license_key": "LIC-DEVICE-TEST", "license_file": lic,
        "public_key": client.public_key_pem,
        "last_heartbeat": time.time() - 60,  # 宽限期内，且已到心跳间隔
    })
    client._session = FakeSession([__import__("requests").ConnectionError("down")])
    info = client.check(online=True)
    assert info["status"] == lc.STATUS_ACTIVE
    assert info["offline"] is True


def test_check_offline_beyond_grace_blocks(client, rsa_key):
    """离线超过宽限期：必须联网校验成功，网络失败直接拦截。

    last_heartbeat 可被改文件冻结在线撤销/过期检查（2026-11-20 实测），
    超过 MAX_OFFLINE_SECONDS 后不允许再按离线放行。
    """
    lic = make_license(rsa_key, "fp-test")
    client._save_record({
        "license_key": "LIC-DEVICE-TEST", "license_file": lic,
        "public_key": client.public_key_pem,
        "last_heartbeat": time.time() - lc.MAX_OFFLINE_SECONDS - 60,
    })
    client._session = FakeSession([__import__("requests").ConnectionError("down")])
    with pytest.raises(lc.LicenseError) as ei:
        client.check(online=True)
    assert ei.value.code == "network"
    assert "联网" in ei.value.message


def test_check_future_heartbeat_forces_online(client, rsa_key):
    """last_heartbeat 在未来 = 记录被改/时钟回拨：不信任，强制联网。

    旧实现下未来时间戳让 elapsed 为负、心跳永不发送，撤销检查被冻结。
    """
    lic = make_license(rsa_key, "fp-test")
    client._save_record({
        "license_key": "LIC-DEVICE-TEST", "license_file": lic,
        "public_key": client.public_key_pem,
        "last_heartbeat": time.time() + 10 ** 9,
    })
    client._session = FakeSession([__import__("requests").ConnectionError("down")])
    with pytest.raises(lc.LicenseError) as ei:
        client.check(online=True)
    assert ei.value.code == "network"  # 未按离线放行（stale 强制在线）
    # 联网成功时正常放行并刷新心跳
    client._session = FakeSession([(200, {"code": lc.CODE_OK, "data": {
        "status": "active", "heartbeat_interval": 300}})])
    info = client.check(online=True)
    assert info["status"] == lc.STATUS_ACTIVE
    assert info["offline"] is False


def test_embedded_public_key_is_valid():
    """产品公钥内嵌在 license_config（编译进 exe），且必须是可用 RSA 公钥。

    信任锚不能是安装目录里的明文 pem（可被替换后自签）；本用例防止手工
    改坏常量导致所有客户端验签失败（发布前 CI 即拦截）。
    """
    from src.app.license_config import LICENSE_PUBLIC_KEY_PEM
    key = RSA.import_key(LICENSE_PUBLIC_KEY_PEM)
    assert key.size_in_bits() == 2048


def test_record_public_key_not_trusted(client):
    """整份 license.json 换成攻击者公钥 + 自签许可证不通过。

    信任锚是随包分发的产品公钥；记录文件里的 public_key 只是历史字段，
    参与验签等于把钥匙和锁放在同一个可改的抽屉里（2026-11-20 实测）。
    """
    forged_key = RSA.generate(2048)
    forged_lic = make_license(forged_key, "fp-test", end_delta=3650)
    client._save_record({
        "license_key": "LIC-DEVICE-FORGED", "license_file": forged_lic,
        "public_key": forged_key.publickey().export_key().decode(),
        "last_heartbeat": time.time() - 60,
    })
    with pytest.raises(lc.LicenseError) as ei:
        client.check(online=False)
    assert ei.value.code == "bad_license"


def test_heartbeat_updates_license(client, rsa_key):
    old = make_license(rsa_key, "fp-test", start_delta=-1, end_delta=10)
    new = make_license(rsa_key, "fp-test", start_delta=-1, end_delta=60,
                       config_updated_at="2026-11-19T12:00:00+08:00")
    rec = {"license_key": "LIC-DEVICE-TEST", "license_file": old,
           "public_key": client.public_key_pem, "last_heartbeat": 0,
           "config_updated_at": "2026-11-01T00:00:00+08:00"}
    client._save_record(rec)
    client._session = FakeSession([(200, {"code": lc.CODE_OK, "data": {
        "status": "active", "license_file": new, "heartbeat_interval": 60}})])
    info = client.check(online=True)
    assert info["status"] == lc.STATUS_ACTIVE
    saved = client._read_record()
    assert saved["license_file"] == new
    assert saved["config_updated_at"] == "2026-11-19T12:00:00+08:00"


def test_check_revoked_blocks(client, rsa_key):
    lic = make_license(rsa_key, "fp-test")
    client._save_record({
        "license_key": "LIC-DEVICE-TEST", "license_file": lic,
        "public_key": client.public_key_pem, "last_heartbeat": 0,
    })
    client._session = FakeSession([(200, {"code": lc.CODE_OK,
                                          "data": {"status": "revoked"}})])
    with pytest.raises(lc.LicenseError) as ei:
        client.check(online=True)
    assert ei.value.code == "revoked"


def test_info_shows_fingerprint_when_inactive(client):
    info = client.info()
    assert info["status"] == lc.STATUS_INACTIVE
    assert info["fingerprint"] == "fp-test"
    assert info["fingerprint_error"] == ""


def test_unbind_local(client, rsa_key):
    lic = make_license(rsa_key, "fp-test")
    client._save_record({"license_key": "k", "license_file": lic})
    client.unbind_local()
    assert client.info()["status"] == lc.STATUS_INACTIVE


# ---------- 后台周期心跳（start_heartbeat_loop） ----------

def _seed_record(client, rsa_key, **extra):
    """写入一条本地许可证记录（服务端可下发的字段按需覆盖）。"""
    lic = make_license(rsa_key, "fp-test")
    rec = {
        "license_key": "LIC-DEVICE-TEST", "license_file": lic,
        "public_key": client.public_key_pem, "last_heartbeat": 0,
        "heartbeat_interval": 0.05,  # 配合 MIN_HEARTBEAT_LOOP_INTERVAL 的测试值
    }
    rec.update(extra)
    client._save_record(rec)
    return rec


def _start_heartbeat(client, session, monkeypatch):
    """测试用启动：把间隔下限压到 0.05s，返回收集到的日志 (level, msg)。"""
    monkeypatch.setattr(lc, "MIN_HEARTBEAT_LOOP_INTERVAL", 0.05)
    client._session = session
    logs = []
    client.start_heartbeat_loop(
        log=lambda msg, level="SYS": logs.append((level, msg)))
    return logs


def _wait_until(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class RepeatingSession:
    """每次心跳都返回同一成功响应（周期上报测试）。"""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append((url, json))
        return FakeResponse(200, self.payload)


class FailingSession:
    """心跳始终网络失败（连续失败告警测试）。"""

    def __init__(self, exc=None):
        self.exc = exc or requests.ConnectionError("down")
        self.calls = []

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append((url, json))
        raise self.exc


class SequenceSession:
    """按顺序返回响应，最后一个响应重复使用（撤销→恢复测试）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append((url, json))
        item = self.responses.pop(0) if len(self.responses) > 1 \
            else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return FakeResponse(*item)


def test_heartbeat_wait_seconds_clamped_and_default(client, rsa_key,
                                                    monkeypatch):
    assert client.heartbeat_wait_seconds() == lc.DEFAULT_HEARTBEAT_INTERVAL
    _seed_record(client, rsa_key, heartbeat_interval=1)
    # 服务端下发异常小值时按模块下限跑，防止高频请求
    assert client.heartbeat_wait_seconds() == lc.MIN_HEARTBEAT_LOOP_INTERVAL
    _seed_record(client, rsa_key, heartbeat_interval=600)
    assert client.heartbeat_wait_seconds() == 600


def test_heartbeat_loop_reports_periodically(client, rsa_key, monkeypatch):
    """运行期按 heartbeat_interval 周期上报，并刷新 last_heartbeat。"""
    _seed_record(client, rsa_key)
    session = RepeatingSession(
        {"code": lc.CODE_OK, "data": {"status": "active"}})
    logs = _start_heartbeat(client, session, monkeypatch)
    try:
        assert _wait_until(lambda: len(session.calls) >= 2)
    finally:
        client.stop_heartbeat_loop()
    assert all(url.endswith("/api/v1/heartbeat")
               for url, _payload in session.calls)
    assert client._read_record()["last_heartbeat"] > 0


def test_heartbeat_loop_skips_without_license(client, monkeypatch):
    """未激活/已解绑：心跳待命，不访问授权服务器。"""
    session = RepeatingSession(
        {"code": lc.CODE_OK, "data": {"status": "active"}})
    _start_heartbeat(client, session, monkeypatch)
    try:
        time.sleep(0.2)
    finally:
        client.stop_heartbeat_loop()
    assert session.calls == []
    assert client.info()["status"] == lc.STATUS_INACTIVE


def test_heartbeat_loop_escalates_once(client, rsa_key, monkeypatch):
    """网络失败静默重试，连续 3 次才升级一条 ERROR，且不重复刷屏。"""
    _seed_record(client, rsa_key)
    session = FailingSession()
    logs = _start_heartbeat(client, session, monkeypatch)
    try:
        assert _wait_until(
            lambda: any(level == "ERROR" for level, _msg in logs))
        time.sleep(0.3)  # 再多跑几轮：升级后不再重复告警
    finally:
        client.stop_heartbeat_loop()
    errors = [msg for level, msg in logs if level == "ERROR"]
    assert len(errors) == 1
    assert "连续 3 次失败" in errors[0]


def test_heartbeat_loop_revoke_alert_once_then_recover(client, rsa_key,
                                                       monkeypatch):
    """服务端撤销：立即 ERROR（同码只报一次），恢复后提示重新武装。"""
    _seed_record(client, rsa_key)
    revoked = (200, {"code": lc.CODE_OK, "data": {"status": "revoked"}})
    active = (200, {"code": lc.CODE_OK, "data": {"status": "active"}})
    session = SequenceSession([revoked, revoked, active])
    logs = _start_heartbeat(client, session, monkeypatch)
    try:
        assert _wait_until(
            lambda: any("恢复" in msg for _level, msg in logs))
    finally:
        client.stop_heartbeat_loop()
    errors = [msg for level, msg in logs if level == "ERROR"]
    assert len(errors) == 1
    assert "被拒绝" in errors[0] and "撤销" in errors[0]
    # 恢复后重新武装：下一次撤销仍会再次告警
    assert client._hb_reject_code == ""


def test_heartbeat_loop_server_revoke_code_alerts(client, rsa_key,
                                                  monkeypatch):
    """心跳接口直接返回业务错误码（300007）也按明确拒绝立即告警。"""
    _seed_record(client, rsa_key)
    revoked = (403, {"code": "300007", "message": "许可证已被撤销"})
    active = (200, {"code": lc.CODE_OK, "data": {"status": "active"}})
    session = SequenceSession([revoked, active])
    logs = _start_heartbeat(client, session, monkeypatch)
    try:
        assert _wait_until(
            lambda: any("恢复" in msg for _level, msg in logs))
    finally:
        client.stop_heartbeat_loop()
    errors = [msg for level, msg in logs if level == "ERROR"]
    assert len(errors) == 1
    assert "被拒绝" in errors[0] and "撤销" in errors[0]


def test_heartbeat_does_not_resurrect_unbound_license(client, rsa_key):
    """心跳在途时解绑：结果丢弃，不能把旧许可证写回磁盘。"""
    _seed_record(client, rsa_key)

    class UnbindDuringPost:
        def post(self, url, json=None, timeout=None, headers=None):
            client.unbind_local()  # 模拟网络请求期间用户在面板点「解绑」
            return FakeResponse(200, {"code": lc.CODE_OK,
                                      "data": {"status": "active"}})

    client._session = UnbindDuringPost()
    info = client.heartbeat()
    assert info["status"] == lc.STATUS_INACTIVE
    assert client._read_record() is None
    assert not os.path.exists(client.record_path)
