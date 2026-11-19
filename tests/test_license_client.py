"""雪松授权客户端单测：验签、过期/撤销/指纹、激活、心跳降级。"""
import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
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
    lic = make_license(rsa_key, "fp-test")
    client._save_record({
        "license_key": "LIC-DEVICE-TEST", "license_file": lic,
        "public_key": client.public_key_pem, "last_heartbeat": 0,
    })
    client._session = FakeSession([__import__("requests").ConnectionError("down")])
    info = client.check(online=True)
    assert info["status"] == lc.STATUS_ACTIVE
    assert info["offline"] is True


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
