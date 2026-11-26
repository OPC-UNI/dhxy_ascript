"""雪松授权（cedar-v/license-manager）客户端：设备绑定 + 在线激活/心跳。

服务端协议（v1.2.0，见 /opt/license-manager 后端源码）：

- ``POST /api/v1/activate``
  请求 ``{authorization_code, hardware_fingerprint, software_version, device_info}``
  成功返回 ``{code:"000000", data:{license_key, license_file, heartbeat_interval}}``
- ``POST /api/v1/heartbeat``
  请求 ``{license_key, hardware_fingerprint, config_updated_at, software_version}``
  返回 ``{code:"000000", data:{status, config_updated, license_file?}}``

``license_file`` 是 base64 后的 JSON：``{data: "<json 字符串>", signature, algorithm}``，
签名算法 RSA-PSS-SHA256（salt 长度 = SHA256 摘要长度 32）。验签**固定**使用随
客户端内置的产品公钥（``src/app/license_config.py`` 的
``LICENSE_PUBLIC_KEY_PEM``，编译进 exe）：``data/_license/license.json``
是可被本地篡改的明文文件，其中的 ``public_key`` 字段以及激活响应里的公钥都
不能作为信任锚（否则换个公钥自签许可证即可绕过）；换密钥必须随客户端发版。

离线策略：本地许可证有效时断网可用，但距上次成功心跳超过
``MAX_OFFLINE_SECONDS`` 视为离线超期，必须联网校验一次——防止改
``last_heartbeat`` 把在线撤销/过期检查永久冻结。

设计约束（与项目现有授权门一致）：

- 本地许可证优先：启动/点「启动」时先本地验签，避免把每次启动都绑死在
  实时网络上；在线心跳失败只降级为 ``offline``，不误判撤销。
- 服务端明确返回撤销/过期/设备不匹配时必须拦截（不能被离线宽松策略吞掉）。
- 指纹遵循 cedar 官方建议：Windows ``MachineGuid`` 规范化后 SHA-256，
  不采集 MAC/IP/网卡，避免换网卡、VPN、虚拟网卡导致掉授权。
"""
import base64
import hashlib
import json
import os
import socket
import threading
import time
from datetime import datetime, timezone

import requests
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pss

CODE_OK = "000000"

# 离线宽限：距上次成功心跳超过该时长必须联网校验一次（网络失败即拦截）。
# 本地 license.json 可被篡改，last_heartbeat 不能作为可信依据无限离线；
# 宽限期内的网络异常仍按离线放行（断网不误伤正常用户）。
MAX_OFFLINE_SECONDS = 7 * 86400
# last_heartbeat 允许的未来偏差；超出视为记录被改/系统时钟回拨，不信任该值。
HEARTBEAT_CLOCK_SKEW = 60.0

# 后台周期心跳（start_heartbeat_loop）：程序运行期间持续上报在线状态，
# 服务端才能看到「在线」并下发撤销/续期。间隔取激活/心跳响应里的
# heartbeat_interval，缺省 300s；下限 30s 防止异常值打成高频请求。
DEFAULT_HEARTBEAT_INTERVAL = 300.0
MIN_HEARTBEAT_LOOP_INTERVAL = 30.0
# 连续失败达到该次数只升级一条 ERROR 告警（恢复后重新武装），
# 避免每轮失败都刷日志/面板——与项目周期失败告警约定一致。
HEARTBEAT_FAIL_ESCALATE = 3
# 服务端明确拒绝的状态（撤销/过期/设备不匹配等）：立即告警，同一错误码
# 只报一次；其余错误（网络/服务端临时异常）按可恢复失败静默重试。
# 前半为客户端归类码（心跳 status 字段 / 许可证验签），后半为服务端
# 直接在心跳接口返回的业务错误码（如 300007 撤销、300008 设备不匹配）。
HEARTBEAT_REJECT_CODES = frozenset({
    "revoked", "status", "expired", "not_started", "fingerprint",
    "bad_license", "crypto",
    "300001", "300003", "300004", "300005", "300006", "300007", "300008",
})

# 服务端错误码 → 用户可读文案（zh-CN 语言包）
ERROR_MESSAGES = {
    "300001": "授权码不存在或已过期",
    "300003": "授权码已被锁定",
    "300004": "激活数量已达上限（该卡已绑定其他设备，请联系作者解绑）",
    "300005": "硬件指纹格式错误",
    "300006": "许可证不存在，请重新激活",
    "300007": "许可证已被撤销，请联系作者",
    "300008": "硬件指纹不匹配（设备已更换，请联系作者解绑）",
    "900001": "请求参数错误",
    "900004": "授权服务器内部错误",
}

STATUS_ACTIVE = "已激活"
STATUS_INACTIVE = "未激活"
STATUS_EXPIRED = "已过期"
STATUS_REVOKED = "已撤销"
STATUS_DEVICE_MISMATCH = "设备不匹配"


class LicenseError(Exception):
    """授权异常；``code`` 供调用方区分网络/撤销/指纹等情形。"""

    def __init__(self, message, code=""):
        super().__init__(message)
        self.message = message
        self.code = code


def machine_fingerprint():
    """当前 Windows 设备的稳定指纹（MachineGuid 规范化后 SHA-256）。

    与 cedar 官方文档一致：系统稳定设备 ID，不使用 MAC/IP/网卡信息。
    重装系统或镜像克隆可能改变该值（服务端需解绑后重新激活）。
    """
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as key:
            guid, _ = winreg.QueryValueEx(key, "MachineGuid")
    except Exception as e:  # noqa: BLE001 - 统一转成授权错误
        raise LicenseError("无法读取设备指纹（MachineGuid）：%s" % e,
                           code="fingerprint")
    raw = "windows-machine-guid:" + str(guid).strip().lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_time(value):
    """解析 RFC3339 / ISO8601 时间；无时区按本地时区处理。"""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


def _now_utc():
    return datetime.now(timezone.utc)


def verify_license_file(license_file, public_key_pem, fingerprint, now=None):
    """验签并校验本地许可证，返回许可证 payload（dict）。

    校验顺序：签名 → 状态 → 硬件指纹 → 生效/到期时间。
    失败抛 ``LicenseError``，``code`` 为 bad_license/crypto/revoked/
    fingerprint/not_started/expired。
    """
    if not license_file:
        raise LicenseError("许可证文件为空", code="bad_license")
    try:
        outer = json.loads(base64.b64decode(license_file))
    except Exception as e:  # noqa: BLE001
        raise LicenseError("许可证文件格式错误：%s" % e, code="bad_license")
    if not isinstance(outer, dict):
        raise LicenseError("许可证文件格式错误", code="bad_license")
    if str(outer.get("algorithm") or "") != "RSA-PSS-SHA256":
        raise LicenseError("不支持的许可证签名算法：%s" % outer.get("algorithm"),
                           code="bad_license")
    data = outer.get("data")
    signature = outer.get("signature")
    if not isinstance(data, str) or not signature:
        raise LicenseError("许可证文件缺少签名数据", code="bad_license")
    try:
        key = RSA.import_key(public_key_pem)
    except (ImportError, OSError) as e:
        # 打包缺原生模块等环境问题（如 pycryptodome 4.x 的
        # Crypto.Util._base64 未被 Nuitka 收录，2026-11-20 打包事故），
        # 不能误报成「签名被篡改」——把底层原因带出来便于定位。
        raise LicenseError("许可证校验组件异常（%s: %s）"
                           % (type(e).__name__, e), code="crypto")
    except Exception:  # noqa: BLE001
        raise LicenseError("产品公钥格式错误（公钥文件可能损坏）",
                           code="bad_license")
    try:
        sig = base64.b64decode(signature)
        pss.new(key, salt_bytes=32).verify(
            SHA256.new(data.encode("utf-8")), sig)
    except (ImportError, OSError) as e:
        raise LicenseError("许可证校验组件异常（%s: %s）"
                           % (type(e).__name__, e), code="crypto")
    except Exception:  # noqa: BLE001
        raise LicenseError("许可证签名校验失败（可能被篡改或公钥不匹配）",
                           code="bad_license")
    try:
        payload = json.loads(data)
    except Exception as e:  # noqa: BLE001
        raise LicenseError("许可证内容格式错误：%s" % e, code="bad_license")
    if not isinstance(payload, dict):
        raise LicenseError("许可证内容格式错误", code="bad_license")

    status = str(payload.get("status") or "").lower()
    if status == "revoked":
        raise LicenseError("许可证已被撤销，请联系作者", code="revoked")
    if status != "active":
        raise LicenseError("许可证状态无效：%s" % (status or "空"),
                           code="status")

    expected_fp = str(payload.get("hardware_fingerprint") or "")
    if fingerprint and expected_fp and expected_fp != fingerprint:
        raise LicenseError("硬件指纹不匹配（设备已更换，请联系作者解绑）",
                           code="fingerprint")

    now = now or _now_utc()
    start = _parse_time(payload.get("start_date"))
    end = _parse_time(payload.get("end_date"))
    if start is not None and now < start:
        raise LicenseError("许可证尚未生效（生效时间 %s）"
                           % payload.get("start_date"), code="not_started")
    if end is not None and now > end:
        raise LicenseError("许可证已过期（到期时间 %s）"
                           % payload.get("end_date"), code="expired")
    return payload


def _atomic_write(path, text):
    """同目录临时文件 + os.replace，避免心跳更新时读到半个文件。"""
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


class LicenseClient:
    """雪松授权客户端（线程安全；主进程与面板共用）。"""

    def __init__(self, server_url, public_key_pem, product_name="啄木鸟",
                 app_version="", data_dir=None, session=None,
                 timeout=(5, 15)):
        self.server_url = str(server_url or "").rstrip("/")
        self.public_key_pem = public_key_pem or ""
        self.product_name = product_name
        self.app_version = app_version
        self.timeout = timeout
        self._session = session or requests.Session()
        self._lock = threading.RLock()
        self._last_error = ""
        if data_dir is None:
            from src.core import user_data
            data_dir = user_data.data_root() / "_license"
        self.data_dir = str(data_dir)
        self.record_path = os.path.join(self.data_dir, "license.json")
        self._record = None
        # 记录版本号：心跳在途期间解绑/换卡（_save_record / unbind 都 +1）
        # 时丢弃本次心跳结果，避免把旧许可证写回来（解绑后被心跳复活）。
        self._record_gen = 0
        # 后台周期心跳：每轮独立停止事件（重启循环时旧线程仍持旧事件，
        # 不会因新事件被清除而复活）+ 告警状态
        self._hb_stop = threading.Event()
        self._hb_thread = None
        self._hb_log_fn = None
        self._hb_fail_streak = 0
        self._hb_reject_code = ""
        self._hb_first_ok = False

    # ---------- 基础 ----------

    def _post(self, path, payload):
        url = self.server_url + path
        try:
            resp = self._session.post(
                url, json=payload, timeout=self.timeout,
                headers={"User-Agent": "Zhuomuniao/%s" % (self.app_version or "")})
        except requests.RequestException as e:
            raise LicenseError("无法连接授权服务器，请检查网络（%s）"
                               % str(e)[:120], code="network")
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            raise LicenseError("授权服务器返回异常（HTTP %s）" % resp.status_code,
                               code="server")
        code = str((data or {}).get("code") or "")
        if resp.status_code == 200 and code == CODE_OK:
            return data
        message = (data or {}).get("message") or (data or {}).get("msg") \
            or ERROR_MESSAGES.get(code) or ("授权失败（%s）" % (code or resp.status_code))
        raise LicenseError(str(message), code=code or "http_%s" % resp.status_code)

    def _read_record(self):
        with self._lock:
            if self._record is not None:
                return dict(self._record)
            try:
                with open(self.record_path, "r", encoding="utf-8") as f:
                    record = json.load(f)
                if not isinstance(record, dict):
                    record = None
            except Exception:  # noqa: BLE001
                record = None
            self._record = record
            return dict(record) if record else None

    def _save_record(self, record):
        os.makedirs(self.data_dir, exist_ok=True)
        with self._lock:
            self._record = dict(record)
            self._record_gen += 1
            _atomic_write(self.record_path, json.dumps(
                record, ensure_ascii=False, indent=1))

    def _save_record_if_current(self, record, gen):
        """仅当记录版本未变时回写（心跳在途时解绑/换卡则丢弃结果）。

        后台周期心跳让「读记录 → 网络请求 → 写记录」之间的窗口常态存在；
        期间面板解绑（_record=None）或换卡会推进 ``_record_gen``，旧心跳
        不能再把过期记录写回磁盘。
        """
        with self._lock:
            if gen != self._record_gen:
                return False
            if not self._record:
                return False
            if self._record.get("license_key") != record.get("license_key"):
                return False
            self._save_record(record)
            return True

    def _license_pem(self):
        """验签公钥：固定用随包分发的产品公钥，不信任本地记录里的字段。

        license.json 是明文文件，若把其中 ``public_key`` 当作信任锚，
        改文件的人换一把自己的公钥、自签一张远期许可证即可全部通过
        （2026-11-20 实测）。因此 record（以及服务端响应）里的公钥一律
        忽略；服务端换 RSA 密钥必须随客户端发版（docs/license-server.md）。
        """
        return self.public_key_pem

    def cached_code(self):
        record = self._read_record() or {}
        return str(record.get("authorization_code") or "")

    def unbind_local(self):
        """清除本地许可证（不改变服务端绑定；换机需后台释放设备）。"""
        with self._lock:
            self._record = None
            self._record_gen += 1
            try:
                os.remove(self.record_path)
            except FileNotFoundError:
                pass
            except OSError as e:
                raise LicenseError("清除本地授权失败：%s" % e, code="local")

    # ---------- 激活 / 心跳 ----------

    def activate(self, code):
        """用卡密激活当前设备；成功返回授权信息 dict。"""
        code = str(code or "").strip()
        if not code:
            raise LicenseError("请输入卡密", code="empty")
        fingerprint = machine_fingerprint()
        payload = {
            "authorization_code": code,
            "hardware_fingerprint": fingerprint,
            "software_version": self.app_version,
            "device_info": {
                "os": "Windows",
                "hostname": socket.gethostname(),
                "version": self.app_version,
            },
        }
        data = self._post("/api/v1/activate", payload)
        body = data.get("data") or {}
        license_file = body.get("license_file")
        license_key = body.get("license_key")
        if not license_file or not license_key:
            raise LicenseError("激活响应缺少许可证文件", code="server")
        public_key = self.public_key_pem
        if not public_key:
            raise LicenseError("缺少产品公钥，无法校验许可证", code="bad_license")
        payload_inner = verify_license_file(license_file, public_key, fingerprint)
        now = time.time()
        record = {
            "server_url": self.server_url,
            "product": self.product_name,
            "authorization_code": code,
            "license_key": license_key,
            "license_file": license_file,
            "public_key": public_key,
            "heartbeat_interval": int(body.get("heartbeat_interval") or 300),
            "config_updated_at": payload_inner.get("config_updated_at")
            or payload_inner.get("generated_at")
            or payload_inner.get("activated_at") or "",
            "activated_at": now,
            "last_heartbeat": 0.0,
            "fingerprint": fingerprint,
        }
        self._save_record(record)
        info = self.info()
        info["offline"] = False
        return info

    def heartbeat(self, record=None):
        """在线同步状态；撤销/过期/设备不匹配时抛错。返回最新 info。

        启动校验与后台周期心跳可能并发调用：落盘前用
        ``_save_record_if_current`` 校验记录版本未变——后到的请求不会
        覆盖先到的结果；心跳在途期间面板解绑/换卡也不会把旧许可证写回。
        """
        record = record or self._read_record()
        if not record:
            raise LicenseError("本地暂无许可证，请先激活", code="not_activated")
        gen = self._record_gen
        fingerprint = machine_fingerprint()
        payload = {
            "license_key": record.get("license_key"),
            "hardware_fingerprint": fingerprint,
            "software_version": self.app_version,
        }
        if record.get("config_updated_at"):
            payload["config_updated_at"] = record.get("config_updated_at")
        data = self._post("/api/v1/heartbeat", payload)
        body = data.get("data") or {}
        status = str(body.get("status") or "").lower()
        if status and status != "active":
            if status == "revoked":
                raise LicenseError("许可证已被撤销，请联系作者", code="revoked")
            raise LicenseError("许可证状态异常：%s" % status, code="status")
        new_payload = None
        if body.get("license_file"):
            public_key = self._license_pem()
            new_payload = verify_license_file(
                body["license_file"], public_key, fingerprint)
            record["license_file"] = body["license_file"]
        record["public_key"] = self._license_pem()
        # 续期/改配置后服务端返回新许可证；用新许可证的 config_updated_at
        # 回传，避免下一轮心跳继续判定“配置有更新”而重复下发。
        if new_payload is not None:
            record["config_updated_at"] = (
                new_payload.get("config_updated_at")
                or new_payload.get("generated_at")
                or record.get("config_updated_at") or "")
        else:
            record["config_updated_at"] = body.get("config_updated_at") \
                or record.get("config_updated_at") or ""
        record["last_heartbeat"] = time.time()
        if body.get("heartbeat_interval"):
            record["heartbeat_interval"] = int(body["heartbeat_interval"])
        self._save_record_if_current(record, gen)
        info = self.info()
        info["offline"] = False
        return info

    # ---------- 后台周期心跳 ----------

    def heartbeat_wait_seconds(self):
        """后台心跳等待间隔：服务端下发的 heartbeat_interval，钳制下限。"""
        record = self._read_record() or {}
        try:
            interval = float(record.get("heartbeat_interval") or 0)
        except (TypeError, ValueError):
            interval = 0.0
        if interval <= 0:
            interval = DEFAULT_HEARTBEAT_INTERVAL
        return max(MIN_HEARTBEAT_LOOP_INTERVAL, interval)

    def start_heartbeat_loop(self, log=None):
        """启动后台周期心跳线程（程序运行期间持续上报在线状态）。

        - 每轮读记录里的 ``heartbeat_interval``（服务端可下发新值），
          未激活/已解绑时静默待命，激活后自动开始上报；
        - ``log(msg, level)`` 可选回调（主进程写文件/面板），不传则静默；
        - 只上报不改门禁：服务端撤销/过期记 ERROR 告警，但不中止已运行
          任务，下次点「启动」仍由 ``check()`` 拦截；
        - daemon 线程随主进程退出；重复调用无副作用。
        """
        if self._hb_thread is not None and self._hb_thread.is_alive():
            return
        self._hb_log_fn = log
        self._hb_fail_streak = 0
        self._hb_reject_code = ""
        self._hb_first_ok = False
        stop = threading.Event()
        self._hb_stop = stop
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop, args=(stop,),
            name="license-heartbeat", daemon=True)
        self._hb_thread.start()
        if self._read_record():
            self._log_line(
                "[授权] 在线心跳已开启：每 %d 秒上报一次（运行期间持续校验）"
                % int(self.heartbeat_wait_seconds()), "SYS")
            self._hb_first_ok = True

    def stop_heartbeat_loop(self, timeout=2.0):
        """停止后台心跳线程（退出/测试用；daemon 线程不调用也随进程退出）。"""
        self._hb_stop.set()
        thread = self._hb_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)

    def _heartbeat_loop(self, stop):
        """心跳线程主体：等间隔上报；线程内异常一律吞掉，不允许退出。"""
        while True:
            if stop.wait(self.heartbeat_wait_seconds()):
                return
            if not self._read_record():
                # 未激活/面板解绑：待命，激活后下一轮自动开始
                self._hb_fail_streak = 0
                self._hb_reject_code = ""
                continue
            try:
                self.heartbeat()
            except LicenseError as e:
                self._on_heartbeat_error(e)
            except Exception as e:  # noqa: BLE001 - 心跳线程不允许死
                self._on_heartbeat_error(
                    LicenseError("心跳线程异常：%s" % e, code="unexpected"))
            else:
                self._on_heartbeat_ok()

    def _on_heartbeat_ok(self):
        if not self._hb_first_ok:
            self._hb_first_ok = True
            self._log_line(
                "[授权] 在线心跳正常：每 %d 秒上报一次"
                % int(self.heartbeat_wait_seconds()), "SYS")
        if self._hb_reject_code:
            self._hb_reject_code = ""
            self._log_line("[授权] 在线心跳恢复：服务端已重新接受授权", "SYS")
        if self._hb_fail_streak:
            streak = self._hb_fail_streak
            self._hb_fail_streak = 0
            self._log_line(
                "[授权] 在线心跳已恢复（此前连续失败 %d 次）" % streak, "SYS")

    def _on_heartbeat_error(self, err):
        """心跳失败分级：网络类静默重试（连续 3 次升级一条 ERROR），
        撤销/过期等明确拒绝立即告警（同码只报一次），恢复后重新武装。"""
        code = str(getattr(err, "code", "") or "")
        if code == "not_activated":
            # 解绑/清除本地授权：转为待命（不告警）
            self._hb_fail_streak = 0
            self._hb_reject_code = ""
            return
        if code in HEARTBEAT_REJECT_CODES:
            if code != self._hb_reject_code:
                self._hb_reject_code = code
                self._hb_fail_streak = 0
                self._log_line(
                    "[授权] 在线心跳被拒绝：%s（已运行任务不中止，"
                    "下次启动任务将被拦截）" % err.message, "ERROR")
            return
        self._hb_fail_streak += 1
        if self._hb_fail_streak == HEARTBEAT_FAIL_ESCALATE:
            self._log_line(
                "[授权] 在线心跳连续 %d 次失败：%s（网络恢复后自动继续）"
                % (self._hb_fail_streak, err.message), "ERROR")

    def _log_line(self, msg, level="SYS"):
        fn = self._hb_log_fn
        if fn is None:
            return
        try:
            fn(msg, level)
        except Exception:  # noqa: BLE001 - 日志失败不影响心跳
            pass

    # ---------- 本地校验 / 对外信息 ----------

    def check(self, online=True, min_heartbeat_interval=30):
        """启动校验：本地验签 → （可选）在线心跳。

        本地无效直接抛错；网络失败降级为 offline（本地许可证仍有效）；
        服务端明确撤销/过期时抛错，不会被离线宽松策略吞掉。

        离线宽限：距上次成功心跳超过 ``MAX_OFFLINE_SECONDS``（或记录里的
        ``last_heartbeat`` 在未来、不可信）时必须联网校验成功才能继续，
        网络失败直接抛错——防止改 last_heartbeat 把在线撤销/过期检查
        永久冻结（2026-11-20 实测：未来时间戳可让心跳永不发送）。
        """
        record = self._read_record()
        if not record:
            raise LicenseError("未激活，请先输入卡密激活", code="not_activated")
        fingerprint = machine_fingerprint()
        verify_license_file(record.get("license_file"),
                            self._license_pem(), fingerprint)
        if online:
            now = time.time()
            try:
                last = float(record.get("last_heartbeat") or 0)
            except (TypeError, ValueError):
                last = 0.0
            stale = False
            if last > now + HEARTBEAT_CLOCK_SKEW:
                # 未来时间戳只可能来自改文件/系统时钟回拨，不信任该值
                last = 0.0
                stale = True
            elif now - last > MAX_OFFLINE_SECONDS:
                stale = True
            interval = max(5, min_heartbeat_interval)
            if stale or now - last >= interval:
                try:
                    return self.heartbeat(record)
                except LicenseError as e:
                    if e.code == "network" and not stale:
                        info = self.info()
                        info["offline"] = True
                        return info
                    if e.code == "network":
                        raise LicenseError(
                            "离线已超过 %d 天，需联网校验授权后才能继续使用"
                            "（%s）" % (MAX_OFFLINE_SECONDS // 86400, e.message),
                            code="network")
                    raise
        info = self.info()
        info["offline"] = False
        return info

    def fingerprint(self):
        """当前设备指纹（不依赖本地许可证；未激活时面板也展示）。"""
        return machine_fingerprint()

    def info(self):
        """面板展示用授权信息（不抛错；未激活也返回设备指纹）。"""
        fingerprint = ""
        fingerprint_error = ""
        try:
            fingerprint = machine_fingerprint()
        except LicenseError as e:
            fingerprint_error = e.message
        record = self._read_record()
        if not record:
            return {"status": STATUS_INACTIVE, "expire": "", "days_left": None,
                    "fingerprint": fingerprint,
                    "fingerprint_error": fingerprint_error,
                    "server": self.server_url}
        try:
            payload = verify_license_file(
                record.get("license_file"), self._license_pem(),
                fingerprint)
        except LicenseError as e:
            status = STATUS_INACTIVE
            if e.code == "expired":
                status = STATUS_EXPIRED
            elif e.code == "revoked":
                status = STATUS_REVOKED
            elif e.code == "fingerprint":
                status = STATUS_DEVICE_MISMATCH
            return {"status": status, "expire": "", "days_left": None,
                    "fingerprint": record.get("fingerprint") or fingerprint,
                    "fingerprint_error": fingerprint_error,
                    "server": self.server_url, "error": e.message}
        end_text = str(payload.get("end_date") or "")
        end = _parse_time(end_text)
        days_left = None
        expire = end_text[:10] if end_text else ""
        if end is not None:
            days_left = (end.date() - datetime.now().date()).days
        return {
            "status": STATUS_ACTIVE,
            "expire": expire,
            "days_left": days_left,
            "license_key": record.get("license_key") or "",
            "authorization_code": payload.get("authorization_code") or "",
            "fingerprint": record.get("fingerprint") or fingerprint,
            "fingerprint_error": fingerprint_error,
            "server": self.server_url,
            "max_activations": payload.get("max_activations"),
        }
