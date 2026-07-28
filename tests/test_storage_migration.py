"""SQLite 旧库迁移和单实例租约的离线测试。"""

import _bootstrap  # noqa: F401

import hashlib
from pathlib import Path
import sqlite3
import tempfile

from core.instance_lease import ApplicationInstanceLease
from core.storage_migration import StorageMigrationError, prepare_database


def _create_database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_legacy_database_is_backed_up_verified_and_source_is_unchanged():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        legacy = root / "source" / "EzYOLO.db"
        target = root / "state" / "EzYOLO.db"
        _create_database(legacy, "成功模型和 UNKNOWN 任务仍在")
        before = _hash(legacy)

        result = prepare_database(
            legacy_database=legacy,
            target_database=target,
            backup_root=root / "backups",
        )

        assert result.source == "migrated"
        assert _hash(legacy) == before
        assert result.migration_backup and result.migration_backup.is_file()
        assert result.manifest_file and result.manifest_file.is_file()
        connection = sqlite3.connect(target)
        try:
            assert connection.execute("SELECT value FROM evidence").fetchone()[0].startswith("成功模型")
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            connection.close()


def test_existing_target_is_never_overwritten_or_merged():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        legacy = root / "legacy.db"
        target = root / "target.db"
        _create_database(legacy, "legacy")
        _create_database(target, "target")
        before = _hash(target)
        result = prepare_database(
            legacy_database=legacy,
            target_database=target,
            backup_root=root / "backups",
        )
        assert result.source == "existing"
        assert _hash(target) == before
        assert not (root / "backups").exists()


def test_backup_or_copy_failure_leaves_no_target_and_preserves_source():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        legacy = root / "legacy.db"
        target = root / "target" / "EzYOLO.db"
        _create_database(legacy, "legacy")
        before = _hash(legacy)

        def fail_backup(source, destination):
            raise OSError("disk full")

        try:
            prepare_database(
                legacy_database=legacy,
                target_database=target,
                backup_root=root / "backups",
                backup_api=fail_backup,
            )
        except StorageMigrationError:
            pass
        else:
            raise AssertionError("备份失败必须阻止启动")
        assert not target.exists()
        assert _hash(legacy) == before


def test_symlink_source_is_rejected():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        real = root / "real.db"
        link = root / "legacy.db"
        _create_database(real, "legacy")
        link.symlink_to(real)
        try:
            prepare_database(
                legacy_database=link,
                target_database=root / "target.db",
                backup_root=root / "backups",
            )
        except StorageMigrationError:
            pass
        else:
            raise AssertionError("数据库迁移不得跟随来源符号链接")


def test_application_lease_blocks_second_instance_and_releases():
    with tempfile.TemporaryDirectory() as temporary:
        lock_file = Path(temporary) / "app.lock"
        first = ApplicationInstanceLease(lock_file)
        second = ApplicationInstanceLease(lock_file)
        assert first.try_acquire()
        assert not second.try_acquire()
        first.release()
        assert second.try_acquire()
        second.release()


if __name__ == "__main__":
    raise SystemExit(_bootstrap.run_module_tests(globals()))
