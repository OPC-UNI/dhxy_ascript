"""同安装目录单实例锁：命名按目录隔离且大小写不敏感。"""
from src.core import instance_lock


def test_lock_name_scoped_by_install_root():
    a1 = instance_lock.lock_name(r"C:\Apps\Zhuomuniao")
    a2 = instance_lock.lock_name(r"c:\apps\zhuomuniao")
    b = instance_lock.lock_name(r"C:\Apps\Other")
    assert a1 == a2
    assert a1 != b
    assert a1.startswith("Local\\ZhuomuniaoInstance_")


