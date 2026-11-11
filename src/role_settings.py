"""当前角色的生效设置（data/<角色ID>/settings.json）。

config.json 只保留软件全局设置（窗口绑定、授权缓存、主题、更新偏好、
调试开关、老板键）；任务与方案相关设置按角色存于 settings.json：
控制窗口写入，任务子进程读取。

进程内按角色缓存：控制窗口写入即更新缓存；任务子进程启动时读一次
（任务运行中面板锁定设置，文件不再变化）。schema 与方案白名单
（schemes._settings_spec）一致，主题等全局键不在本文件。
"""
import threading

from . import user_data

_LOCK = threading.RLock()
_CACHE = {}


def _role(role=None):
    return role or user_data.current_role_id()


def _ensure(role):
    """锁内取角色设置（首次从磁盘加载）。调用方必须持 _LOCK。"""
    if role not in _CACHE:
        _CACHE[role] = user_data.read_settings(role)
    return _CACHE[role]


def all(role=None):
    """返回角色设置副本。"""
    role = _role(role)
    with _LOCK:
        return dict(_ensure(role))


def get(key, default=None, role=None):
    """读取一个设置键（缺失返回 default）。"""
    role = _role(role)
    with _LOCK:
        return _ensure(role).get(key, default)


def set_value(key, value, role=None):
    """写入一个设置键（值未变化则不落盘）。"""
    role = _role(role)
    with _LOCK:
        data = _ensure(role)
        if key in data and data[key] == value:
            return
        data[key] = value
        user_data.write_settings(data, role)


def update(mapping, role=None):
    """批量写入设置键（方案应用用）。"""
    if not isinstance(mapping, dict):
        return
    role = _role(role)
    with _LOCK:
        data = _ensure(role)
        data.update(mapping)
        user_data.write_settings(data, role)


def refresh(role=None):
    """丢弃缓存并重读（测试/角色切换兜底）。"""
    role = _role(role)
    with _LOCK:
        _CACHE[role] = user_data.read_settings(role)
        return dict(_CACHE[role])
