"""旧版源码目录数据库到用户应用数据目录的安全引导。

这里只迁移 SQLite 主库。旧项目、模型和训练结果保持原位并继续按数据库中的绝对
路径读取；大文件工作区迁移必须经过单独清单和用户确认。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Callable


class StorageMigrationError(RuntimeError):
    """旧数据库无法被安全核验或复制。"""


@dataclass(frozen=True)
class DatabasePreparation:
    database_file: Path
    source: str
    migration_backup: Path | None = None
    manifest_file: Path | None = None


def _regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise StorageMigrationError(f"{label}不是受支持的普通文件: {path}")


def _integrity_check(path: Path) -> None:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise StorageMigrationError(f"数据库无法打开或核验: {exc}") from exc
    if not result or result[0] != "ok":
        raise StorageMigrationError(f"数据库完整性检查失败: {result!r}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_backup(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
    finally:
        source_connection.close()


def _publish_no_replace(temporary: Path, destination: Path) -> None:
    """同目录原子发布普通文件；目标已存在时绝不覆盖。"""
    os.link(temporary, destination)
    temporary.unlink()


def prepare_database(
    *,
    legacy_database: str | Path,
    target_database: str | Path,
    backup_root: str | Path,
    backup_api: Callable[[Path, Path], None] = _sqlite_backup,
    copy_file: Callable[[Path, Path], object] = shutil.copy2,
    publish_file: Callable[[Path, Path], None] = _publish_no_replace,
) -> DatabasePreparation:
    """选择或复制数据库；永不覆盖目标，也永不修改/删除旧库。"""
    legacy = Path(legacy_database).expanduser()
    target = Path(target_database).expanduser()
    backups = Path(backup_root).expanduser()
    if not target.is_absolute() or not legacy.is_absolute() or not backups.is_absolute():
        raise StorageMigrationError("数据库迁移路径必须是绝对路径")

    if target.exists() or target.is_symlink():
        _regular_file(target, "目标数据库")
        _integrity_check(target)
        return DatabasePreparation(target, "existing")

    if not legacy.exists():
        if legacy.is_symlink():
            raise StorageMigrationError("旧数据库符号链接目标不存在，已拒绝继续")
        return DatabasePreparation(target, "new")

    _regular_file(legacy, "旧数据库")
    _integrity_check(legacy)
    target.parent.mkdir(parents=True, exist_ok=True)
    backups.mkdir(parents=True, exist_ok=True)

    backup_temp: Path | None = None
    target_temp: Path | None = None
    try:
        file_descriptor, raw_backup_temp = tempfile.mkstemp(
            prefix=".legacy-db-", suffix=".db", dir=backups
        )
        os.close(file_descriptor)
        backup_temp = Path(raw_backup_temp)
        backup_temp.unlink()
        backup_api(legacy, backup_temp)
        _regular_file(backup_temp, "迁移备份")
        _integrity_check(backup_temp)
        backup_hash = _sha256(backup_temp)
        backup_file = backups / f"legacy-EzYOLO-{backup_hash[:16]}.db"
        if backup_file.exists():
            _regular_file(backup_file, "已有迁移备份")
            if _sha256(backup_file) != backup_hash:
                raise StorageMigrationError("已有迁移备份与本次内容冲突")
            backup_temp.unlink()
            backup_temp = None
        else:
            try:
                publish_file(backup_temp, backup_file)
                backup_temp = None
            except FileExistsError:
                _regular_file(backup_file, "并发创建的迁移备份")
                if _sha256(backup_file) != backup_hash:
                    raise StorageMigrationError("迁移备份被并发创建且内容冲突")
                backup_temp.unlink()
                backup_temp = None

        file_descriptor, raw_target_temp = tempfile.mkstemp(
            prefix=".EzYOLO-", suffix=".db", dir=target.parent
        )
        os.close(file_descriptor)
        target_temp = Path(raw_target_temp)
        copy_file(backup_file, target_temp)
        if _sha256(target_temp) != backup_hash:
            raise StorageMigrationError("数据库复制后的哈希不一致")
        _integrity_check(target_temp)
        try:
            publish_file(target_temp, target)
            target_temp = None
        except FileExistsError as exc:
            raise StorageMigrationError(
                "迁移期间目标数据库被其他进程创建；没有覆盖该文件"
            ) from exc

        manifest_file = backup_file.with_suffix(".json")
        if not manifest_file.exists():
            manifest = {
                "version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source": str(legacy),
                "backup": backup_file.name,
                "sha256": backup_hash,
                "size": backup_file.stat().st_size,
                "target": str(target),
                "source_preserved": True,
            }
            temporary_manifest = manifest_file.with_suffix(".json.tmp")
            temporary_manifest.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            try:
                publish_file(temporary_manifest, manifest_file)
            except FileExistsError as exc:
                temporary_manifest.unlink(missing_ok=True)
                try:
                    existing_manifest = json.loads(
                        manifest_file.read_text(encoding="utf-8")
                    )
                except (OSError, ValueError) as read_exc:
                    raise StorageMigrationError(
                        "迁移清单被并发创建且无法核验"
                    ) from read_exc
                expected_fields = ("source", "backup", "sha256", "size", "target")
                if any(
                    existing_manifest.get(field) != manifest.get(field)
                    for field in expected_fields
                ):
                    raise StorageMigrationError("迁移清单被并发创建且内容冲突") from exc
        return DatabasePreparation(target, "migrated", backup_file, manifest_file)
    except (OSError, sqlite3.Error, StorageMigrationError) as exc:
        if isinstance(exc, StorageMigrationError):
            raise
        raise StorageMigrationError(f"旧数据库迁移失败: {exc}") from exc
    finally:
        for temporary in (backup_temp, target_temp):
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass


__all__ = [
    "DatabasePreparation",
    "StorageMigrationError",
    "prepare_database",
]
