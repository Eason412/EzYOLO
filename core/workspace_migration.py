"""旧版源码工作区到用户 Workspace 的可预览、可重试复制迁移。

迁移只复制注册项目、训练结果和测试输出；不处理 backups、预训练缓存或来源不明
目录。旧文件永不移动或删除。数据库路径仅在全部文件完成哈希核验后于单个事务中
切换，失败不会改变训练任务状态。
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
import stat
import tempfile
from typing import Callable, Iterable, Protocol

from PyQt6.QtCore import QLockFile


class WorkspaceMigrationError(RuntimeError):
    """工作区盘点或迁移无法安全继续。"""


@dataclass(frozen=True)
class MigrationFile:
    source: Path
    destination_relative: Path
    size: int
    sha256: str
    category: str

    def to_dict(self) -> dict:
        return {
            "source": str(self.source),
            "destination_relative": self.destination_relative.as_posix(),
            "size": self.size,
            "sha256": self.sha256,
            "category": self.category,
        }


@dataclass(frozen=True)
class ProjectPathMigration:
    project_id: int
    source_root: Path
    destination_relative: Path
    image_paths: tuple[tuple[int, Path, Path], ...]

    @property
    def destination_root_relative(self) -> Path:
        return self.destination_relative


@dataclass(frozen=True)
class WorkspaceMigrationPlan:
    migration_id: str
    source_root: Path
    target_root: Path
    database_file: Path
    files: tuple[MigrationFile, ...]
    projects: tuple[ProjectPathMigration, ...]
    blockers: tuple[str, ...]
    total_bytes: int

    @property
    def file_count(self) -> int:
        return len(self.files)

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "migration_id": self.migration_id,
            "source_root": str(self.source_root),
            "target_root": str(self.target_root),
            "database_file": str(self.database_file),
            "total_bytes": self.total_bytes,
            "file_count": self.file_count,
            "blockers": list(self.blockers),
            "files": [item.to_dict() for item in self.files],
            "projects": [
                {
                    "project_id": project.project_id,
                    "source_root": str(project.source_root),
                    "destination_relative": project.destination_relative.as_posix(),
                    "image_paths": [
                        {
                            "image_id": image_id,
                            "source": str(source),
                            "destination_relative": destination.as_posix(),
                        }
                        for image_id, source, destination in project.image_paths
                    ],
                }
                for project in self.projects
            ],
        }


@dataclass(frozen=True)
class WorkspaceMigrationResult:
    migration_id: str
    copied_files: int
    reused_files: int
    database_backup: Path
    receipt_file: Path
    relocated_remote_results: int = 0


class RemoteJobStoreLike(Protocol):
    def list(self) -> list: ...

    def relocate_verified_result(
        self,
        job_id: str,
        *,
        expected_local_result_dir: Path | str,
        new_local_result_dir: Path | str,
    ): ...


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _scan_tree(
    source_root: Path,
    destination_prefix: Path,
    category: str,
    blockers: list[str],
) -> list[MigrationFile]:
    if not source_root.exists():
        return []
    if source_root.is_symlink() or not source_root.is_dir():
        blockers.append(f"{category} 来源不是普通目录: {source_root}")
        return []

    result: list[MigrationFile] = []
    for directory, directory_names, file_names in os.walk(source_root, followlinks=False):
        directory_path = Path(directory)
        for name in list(directory_names):
            child = directory_path / name
            try:
                mode = child.lstat().st_mode
            except OSError as exc:
                blockers.append(f"无法读取目录: {child} ({exc})")
                directory_names.remove(name)
                continue
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                blockers.append(f"拒绝迁移符号链接或特殊目录: {child}")
                directory_names.remove(name)
        for name in file_names:
            source = directory_path / name
            try:
                info = source.lstat()
            except OSError as exc:
                blockers.append(f"无法读取文件: {source} ({exc})")
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                blockers.append(f"拒绝迁移符号链接或特殊文件: {source}")
                continue
            relative = source.relative_to(source_root)
            try:
                file_hash = _sha256(source)
            except OSError as exc:
                blockers.append(f"无法计算文件哈希: {source} ({exc})")
                continue
            result.append(
                MigrationFile(
                    source=source,
                    destination_relative=destination_prefix / relative,
                    size=info.st_size,
                    sha256=file_hash,
                    category=category,
                )
            )
    return result


def _read_registered_projects(
    database_file: Path,
    legacy_projects_root: Path,
    blockers: list[str],
) -> tuple[list[ProjectPathMigration], list[MigrationFile]]:
    projects: list[ProjectPathMigration] = []
    files: list[MigrationFile] = []
    try:
        connection = sqlite3.connect(f"file:{database_file}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        project_rows = connection.execute(
            "SELECT id, storage_path FROM projects ORDER BY id"
        ).fetchall()
        image_rows = connection.execute(
            "SELECT id, project_id, storage_path FROM images ORDER BY id"
        ).fetchall()
    except sqlite3.Error as exc:
        blockers.append(f"无法读取项目数据库: {exc}")
        return projects, files
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass

    images_by_project: dict[int, list[sqlite3.Row]] = {}
    for image in image_rows:
        images_by_project.setdefault(int(image["project_id"]), []).append(image)

    for row in project_rows:
        project_id = int(row["id"])
        raw_storage = row["storage_path"]
        if not raw_storage:
            blockers.append(f"项目 {project_id} 没有存储路径")
            continue
        source_root = Path(raw_storage).expanduser()
        if not _inside(source_root, legacy_projects_root):
            # 外部项目引用不属于本次源码工作区迁移。
            continue
        if source_root.is_symlink() or not source_root.is_dir():
            blockers.append(f"项目 {project_id} 的旧目录不可用: {source_root}")
            continue
        destination = Path("projects") / f"project-{project_id:06d}"
        project_files = _scan_tree(
            source_root,
            destination,
            f"project:{project_id}",
            blockers,
        )
        files.extend(project_files)

        image_mappings = []
        for image in images_by_project.get(project_id, []):
            raw_image_path = image["storage_path"]
            if not raw_image_path:
                blockers.append(f"图片 {image['id']} 没有存储路径")
                continue
            source_image = Path(raw_image_path).expanduser()
            if not _inside(source_image, source_root):
                blockers.append(
                    f"图片 {image['id']} 路径逃逸项目目录: {source_image}"
                )
                continue
            relative = source_image.resolve(strict=False).relative_to(
                source_root.resolve(strict=False)
            )
            image_mappings.append(
                (int(image["id"]), source_image, destination / relative)
            )
        projects.append(
            ProjectPathMigration(
                project_id=project_id,
                source_root=source_root,
                destination_relative=destination,
                image_paths=tuple(image_mappings),
            )
        )
    return projects, files


def _target_conflicts(
    target_root: Path,
    files: Iterable[MigrationFile],
    blockers: list[str],
) -> None:
    folded: dict[str, Path] = {}
    for item in files:
        key = item.destination_relative.as_posix().casefold()
        previous = folded.get(key)
        if previous is not None and previous != item.destination_relative:
            blockers.append(
                f"目标平台可能发生大小写冲突: {previous} / {item.destination_relative}"
            )
        folded[key] = item.destination_relative

        target = target_root / item.destination_relative
        if not _inside(target, target_root):
            blockers.append(f"目标路径逃逸工作区: {item.destination_relative}")
            continue
        if len(str(target)) > 240:
            blockers.append(f"目标路径过长: {target}")
        if target.is_symlink():
            blockers.append(f"目标是符号链接，拒绝覆盖: {target}")
        elif target.exists():
            if not target.is_file():
                blockers.append(f"目标不是普通文件: {target}")
            else:
                try:
                    if target.stat().st_size != item.size or _sha256(target) != item.sha256:
                        blockers.append(f"目标已有不同内容: {target}")
                except OSError as exc:
                    blockers.append(f"无法核验已有目标: {target} ({exc})")


def inventory_legacy_workspace(
    *,
    source_root: str | Path,
    target_root: str | Path,
    database_file: str | Path,
) -> WorkspaceMigrationPlan:
    """只读盘点注册项目、runs 和 outputs；不创建目录或修改数据库。"""
    source = Path(source_root).expanduser()
    target = Path(target_root).expanduser()
    database = Path(database_file).expanduser()
    if not source.is_absolute() or not target.is_absolute() or not database.is_absolute():
        raise WorkspaceMigrationError("工作区迁移路径必须是绝对路径")
    if _inside(target, source):
        raise WorkspaceMigrationError("目标工作区不能位于旧源码目录内")
    if source.is_symlink() or target.is_symlink():
        raise WorkspaceMigrationError("工作区根目录不能是符号链接")

    blockers: list[str] = []
    projects, project_files = _read_registered_projects(
        database,
        source / "projects",
        blockers,
    )
    files = [
        *project_files,
        *_scan_tree(source / "runs", Path("runs"), "runs", blockers),
        *_scan_tree(source / "outputs", Path("outputs"), "outputs", blockers),
    ]
    files.sort(key=lambda item: item.destination_relative.as_posix())
    _target_conflicts(target, files, blockers)
    total_bytes = sum(item.size for item in files)

    existing_parent = target
    while not existing_parent.exists() and existing_parent != existing_parent.parent:
        existing_parent = existing_parent.parent
    try:
        free_bytes = shutil.disk_usage(existing_parent).free
    except OSError as exc:
        blockers.append(f"无法读取目标磁盘空间: {exc}")
    else:
        required = total_bytes + max(64 * 1024 * 1024, total_bytes // 20)
        if free_bytes < required:
            blockers.append(
                f"目标空间不足：需要至少 {required} 字节，可用 {free_bytes} 字节"
            )

    digest = hashlib.sha256()
    digest.update(str(source).encode())
    digest.update(str(target).encode())
    for item in files:
        digest.update(item.destination_relative.as_posix().encode())
        digest.update(item.sha256.encode())
    migration_id = digest.hexdigest()[:20]
    return WorkspaceMigrationPlan(
        migration_id=migration_id,
        source_root=source,
        target_root=target,
        database_file=database,
        files=tuple(files),
        projects=tuple(projects),
        blockers=tuple(dict.fromkeys(blockers)),
        total_bytes=total_bytes,
    )


def _publish_no_replace(temporary: Path, destination: Path) -> None:
    os.link(temporary, destination)
    temporary.unlink()


def _ensure_safe_directory(allowed_root: Path, directory: Path) -> None:
    if not _inside(directory, allowed_root):
        raise WorkspaceMigrationError(f"目录逃逸工作区: {directory}")
    try:
        relative = directory.relative_to(allowed_root)
    except ValueError as exc:
        raise WorkspaceMigrationError(f"目录不属于工作区: {directory}") from exc
    current = allowed_root
    if not current.exists():
        current.mkdir(parents=True)
    if current.is_symlink() or not current.is_dir():
        raise WorkspaceMigrationError(f"目标工作区不是普通目录: {current}")
    for part in relative.parts:
        current = current / part
        if current.exists():
            if current.is_symlink() or not current.is_dir():
                raise WorkspaceMigrationError(f"目标父目录不安全: {current}")
        else:
            current.mkdir()
    if not _inside(directory, allowed_root):
        raise WorkspaceMigrationError(f"创建后目录逃逸工作区: {directory}")


def _verify_existing_safe_directory(allowed_root: Path, directory: Path) -> None:
    if allowed_root.is_symlink() or not allowed_root.is_dir():
        raise WorkspaceMigrationError(f"受控根目录不是普通目录: {allowed_root}")
    try:
        relative = directory.relative_to(allowed_root)
    except ValueError as exc:
        raise WorkspaceMigrationError(f"目录不属于受控根目录: {directory}") from exc
    current = allowed_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise WorkspaceMigrationError(f"目录包含不安全的父级: {current}")
    if not _inside(directory, allowed_root):
        raise WorkspaceMigrationError(f"目录解析后逃逸受控根目录: {directory}")


def _copy_verified(
    source: Path,
    destination: Path,
    expected_size: int,
    expected_hash: str,
    *,
    allowed_root: Path,
    copy_file: Callable[[Path, Path], object],
) -> bool:
    """复制一个文件；返回 True 表示新复制，False 表示已存在且相同。"""
    if not _inside(destination, allowed_root):
        raise WorkspaceMigrationError(f"目标路径逃逸工作区: {destination}")
    _ensure_safe_directory(allowed_root, destination.parent)
    if destination.exists() or destination.is_symlink():
        if (
            destination.is_file()
            and not destination.is_symlink()
            and destination.stat().st_size == expected_size
            and _sha256(destination) == expected_hash
        ):
            return False
        raise WorkspaceMigrationError(f"目标已有不同内容: {destination}")
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(raw_temporary)
    try:
        copy_file(source, temporary)
        if temporary.stat().st_size != expected_size or _sha256(temporary) != expected_hash:
            raise WorkspaceMigrationError(f"复制后校验失败: {source}")
        try:
            _publish_no_replace(temporary, destination)
        except FileExistsError:
            if (
                destination.is_file()
                and not destination.is_symlink()
                and destination.stat().st_size == expected_size
                and _sha256(destination) == expected_hash
            ):
                return False
            raise WorkspaceMigrationError(f"复制期间目标被并发创建: {destination}")
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _backup_database(database: Path, backup_root: Path, migration_id: str) -> Path:
    if backup_root.is_symlink():
        raise WorkspaceMigrationError("数据库备份目录不能是符号链接")
    backup_root.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=".workspace-db-", suffix=".db", dir=backup_root
    )
    os.close(descriptor)
    temporary = Path(raw_temporary)
    temporary.unlink()
    try:
        source_connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        destination_connection = sqlite3.connect(temporary)
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
            source_connection.close()
        check = sqlite3.connect(f"file:{temporary}?mode=ro", uri=True)
        try:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise WorkspaceMigrationError("迁移前数据库备份完整性检查失败")
        finally:
            check.close()
        backup_hash = _sha256(temporary)
        backup_file = (
            backup_root
            / f"workspace-migration-{migration_id}-{backup_hash[:16]}.db"
        )
        try:
            _publish_no_replace(temporary, backup_file)
        except FileExistsError:
            if (
                not backup_file.is_file()
                or backup_file.is_symlink()
                or _sha256(backup_file) != backup_hash
            ):
                raise WorkspaceMigrationError("已有数据库备份内容冲突")
        return backup_file
    finally:
        temporary.unlink(missing_ok=True)


def _verify_database_backup(
    backup_file: Path,
    *,
    backup_root: Path,
    expected_hash: str,
) -> None:
    if (
        len(expected_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_hash)
    ):
        raise WorkspaceMigrationError("迁移恢复状态中的数据库备份哈希不合法")
    _verify_existing_safe_directory(backup_root, backup_file.parent)
    try:
        info = backup_file.lstat()
    except OSError as exc:
        raise WorkspaceMigrationError("迁移恢复状态中的数据库备份不可用") from exc
    if not stat.S_ISREG(info.st_mode) or _sha256(backup_file) != expected_hash:
        raise WorkspaceMigrationError("迁移恢复状态中的数据库备份校验失败")
    try:
        connection = sqlite3.connect(f"file:{backup_file}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise WorkspaceMigrationError("迁移恢复状态中的数据库备份无法打开") from exc
    if not result or result[0] != "ok":
        raise WorkspaceMigrationError("迁移恢复状态中的数据库备份完整性检查失败")


def _switch_database_paths(plan: WorkspaceMigrationPlan) -> None:
    connection = sqlite3.connect(plan.database_file)
    try:
        connection.execute("BEGIN IMMEDIATE")
        for project in plan.projects:
            row = connection.execute(
                "SELECT storage_path FROM projects WHERE id = ?",
                (project.project_id,),
            ).fetchone()
            if row is None or Path(row[0]) != project.source_root:
                raise WorkspaceMigrationError(
                    f"项目 {project.project_id} 路径已变化，拒绝陈旧迁移"
                )
            destination_root = plan.target_root / project.destination_root_relative
            current_images = connection.execute(
                "SELECT id, storage_path FROM images WHERE project_id = ? ORDER BY id",
                (project.project_id,),
            ).fetchall()
            expected_images = [
                (image_id, str(source))
                for image_id, source, _destination in project.image_paths
            ]
            if [(int(row[0]), row[1]) for row in current_images] != expected_images:
                raise WorkspaceMigrationError(
                    f"项目 {project.project_id} 的图片清单已变化，拒绝陈旧迁移"
                )
            connection.execute(
                "UPDATE projects SET storage_path = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (str(destination_root), project.project_id),
            )
            for image_id, expected_source, destination_relative in project.image_paths:
                image_row = connection.execute(
                    "SELECT storage_path FROM images WHERE id = ? AND project_id = ?",
                    (image_id, project.project_id),
                ).fetchone()
                if image_row is None or Path(image_row[0]) != expected_source:
                    raise WorkspaceMigrationError(
                        f"图片 {image_id} 路径已变化，拒绝陈旧迁移"
                    )
                connection.execute(
                    "UPDATE images SET storage_path = ? WHERE id = ?",
                    (str(plan.target_root / destination_relative), image_id),
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _database_path_state(plan: WorkspaceMigrationPlan) -> str:
    """返回 old/new/mixed；mixed 必须人工核查，不能猜测或继续写入。"""
    connection = sqlite3.connect(f"file:{plan.database_file}?mode=ro", uri=True)
    try:
        old_matches = True
        new_matches = True
        planned_project_ids = {project.project_id for project in plan.projects}
        for project_id, raw_storage_path in connection.execute(
            "SELECT id, storage_path FROM projects ORDER BY id"
        ).fetchall():
            if int(project_id) in planned_project_ids or not raw_storage_path:
                continue
            storage_path = Path(raw_storage_path)
            if _inside(storage_path, plan.source_root / "projects") or _inside(
                storage_path, plan.target_root / "projects"
            ):
                return "mixed"
        for project in plan.projects:
            project_row = connection.execute(
                "SELECT storage_path FROM projects WHERE id = ?",
                (project.project_id,),
            ).fetchone()
            if project_row is None:
                return "mixed"
            old_matches &= Path(project_row[0]) == project.source_root
            new_matches &= (
                Path(project_row[0])
                == plan.target_root / project.destination_root_relative
            )

            current_images = connection.execute(
                "SELECT id, storage_path FROM images WHERE project_id = ? ORDER BY id",
                (project.project_id,),
            ).fetchall()
            old_images = [
                (image_id, str(source))
                for image_id, source, _destination in project.image_paths
            ]
            new_images = [
                (image_id, str(plan.target_root / destination))
                for image_id, _source, destination in project.image_paths
            ]
            normalized = [(int(row[0]), row[1]) for row in current_images]
            old_matches &= normalized == old_images
            new_matches &= normalized == new_images
        if new_matches:
            return "new"
        if old_matches:
            return "old"
        return "mixed"
    finally:
        connection.close()


def _verify_published_files(plan: WorkspaceMigrationPlan) -> None:
    for item in plan.files:
        destination = plan.target_root / item.destination_relative
        _verify_existing_safe_directory(plan.target_root, destination.parent)
        try:
            info = destination.lstat()
        except OSError as exc:
            raise WorkspaceMigrationError(
                f"迁移目标文件无法核验: {destination}"
            ) from exc
        if not stat.S_ISREG(info.st_mode) or info.st_size != item.size:
            raise WorkspaceMigrationError(
                f"迁移目标文件未通过恢复核验: {destination}"
            )
        if _sha256(destination) != item.sha256:
            raise WorkspaceMigrationError(
                f"迁移目标文件未通过恢复核验: {destination}"
            )


def _plan_remote_result_relocations(
    plan: WorkspaceMigrationPlan,
    remote_job_store: RemoteJobStoreLike | None,
) -> list[dict[str, str]]:
    if remote_job_store is None:
        return []
    relocations = []
    legacy_runs = plan.source_root / "runs"
    for record in remote_job_store.list():
        local_result = getattr(record, "local_result_dir", None)
        if not local_result:
            continue
        old_result = Path(local_result)
        if not _inside(old_result, legacy_runs):
            continue
        relative = old_result.resolve(strict=False).relative_to(
            legacy_runs.resolve(strict=False)
        )
        relocations.append(
            {
                "job_id": record.job_id,
                "old_result_dir": str(old_result),
                "new_result_dir": str(plan.target_root / "runs" / relative),
            }
        )
    return relocations


def _validate_remote_result_relocations(
    plan: WorkspaceMigrationPlan,
    raw_relocations: object,
) -> list[dict[str, str]]:
    if not isinstance(raw_relocations, list):
        raise WorkspaceMigrationError("迁移恢复状态中的远程结果清单不合法")
    validated = []
    seen_job_ids = set()
    for item in raw_relocations:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("job_id"), str)
            or not isinstance(item.get("old_result_dir"), str)
            or not isinstance(item.get("new_result_dir"), str)
        ):
            raise WorkspaceMigrationError("迁移恢复状态中的远程结果记录不合法")
        job_id = item["job_id"]
        old_result = Path(item["old_result_dir"])
        new_result = Path(item["new_result_dir"])
        if (
            job_id in seen_job_ids
            or not _inside(old_result, plan.source_root / "runs")
            or not _inside(new_result, plan.target_root / "runs")
        ):
            raise WorkspaceMigrationError("迁移恢复状态中的远程结果路径不合法")
        old_relative = old_result.resolve(strict=False).relative_to(
            (plan.source_root / "runs").resolve(strict=False)
        )
        new_relative = new_result.resolve(strict=False).relative_to(
            (plan.target_root / "runs").resolve(strict=False)
        )
        if old_relative != new_relative:
            raise WorkspaceMigrationError("迁移恢复状态中的远程结果映射不一致")
        seen_job_ids.add(job_id)
        validated.append(dict(item))
    return validated


def _apply_remote_result_relocations(
    relocations: list[dict[str, str]],
    remote_job_store: RemoteJobStoreLike | None,
) -> int:
    if not relocations:
        return 0
    if remote_job_store is None:
        raise WorkspaceMigrationError("迁移恢复需要远程任务记录存储")
    records = {
        getattr(record, "job_id", None): record
        for record in remote_job_store.list()
    }
    for relocation in relocations:
        record = records.get(relocation["job_id"])
        local_result = getattr(record, "local_result_dir", None)
        if record is None or not local_result:
            raise WorkspaceMigrationError("待迁移的远程成功任务记录已变化")
        current = Path(local_result)
        old_result = Path(relocation["old_result_dir"])
        new_result = Path(relocation["new_result_dir"])
        if current == new_result:
            continue
        if current != old_result:
            raise WorkspaceMigrationError("远程成功任务结果路径已变化")
        remote_job_store.relocate_verified_result(
            relocation["job_id"],
            expected_local_result_dir=old_result,
            new_local_result_dir=new_result,
        )
    return len(relocations)


def _migration_receipt(
    plan: WorkspaceMigrationPlan,
    *,
    backup_file: Path,
    backup_sha256: str,
    copied_files: int,
    reused_files: int,
    relocated_remote_results: int,
) -> dict:
    return {
        "version": 1,
        "migration_id": plan.migration_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(plan.source_root),
        "target_root": str(plan.target_root),
        "database_backup": str(backup_file),
        "database_backup_sha256": backup_sha256,
        "copied_files": copied_files,
        "reused_files": reused_files,
        "source_preserved": True,
        "remote_job_records_modified": bool(relocated_remote_results),
        "relocated_remote_results": relocated_remote_results,
    }


def execute_workspace_migration(
    plan: WorkspaceMigrationPlan,
    *,
    state_root: str | Path,
    database_backups_root: str | Path,
    copy_file: Callable[[Path, Path], object] = shutil.copy2,
    remote_job_store: RemoteJobStoreLike | None = None,
    write_json: Callable[[Path, dict], None] = _write_json_atomic,
) -> WorkspaceMigrationResult:
    """执行已盘点计划；旧源不删除，失败可用同一计划重试。"""
    if plan.blockers:
        raise WorkspaceMigrationError("迁移盘点存在阻塞项，拒绝执行")
    state = Path(state_root).expanduser()
    backups = Path(database_backups_root).expanduser()
    if not state.is_absolute() or not backups.is_absolute():
        raise WorkspaceMigrationError("迁移状态和备份目录必须是绝对路径")
    if state.is_symlink() or backups.is_symlink():
        raise WorkspaceMigrationError("迁移状态和备份根目录不能是符号链接")

    migration_root = state / "workspace-migrations" / plan.migration_id
    _ensure_safe_directory(state, migration_root)
    lock = QLockFile(str(migration_root / "migration.lock"))
    if not lock.tryLock(0):
        raise WorkspaceMigrationError("另一个迁移流程正在处理同一工作区")
    try:
        plan_file = migration_root / "plan.json"
        if plan_file.exists():
            try:
                stored_plan = json.loads(plan_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkspaceMigrationError("迁移计划记录无法读取") from exc
            if stored_plan != plan.to_dict():
                raise WorkspaceMigrationError("同一迁移 ID 的计划内容不一致")
        else:
            write_json(plan_file, plan.to_dict())

        state_file = migration_root / "state.json"
        receipt_file = migration_root / "switch-receipt.json"
        recovery_state = None
        backup_file = None
        backup_sha256 = None
        copied = 0
        reused = 0
        remote_relocations: list[dict[str, str]] = []
        if state_file.exists():
            try:
                recovery_state = json.loads(state_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkspaceMigrationError("迁移恢复状态无法读取") from exc
            if (
                not isinstance(recovery_state, dict)
                or recovery_state.get("version") != 1
                or recovery_state.get("migration_id") != plan.migration_id
                or recovery_state.get("phase")
                not in {"ready_to_switch", "database_switched", "completed"}
            ):
                raise WorkspaceMigrationError("迁移恢复状态不合法，拒绝继续")

            try:
                raw_backup = recovery_state["database_backup"]
                raw_backup_hash = recovery_state["database_backup_sha256"]
                raw_copied = recovery_state["copied_files"]
                raw_reused = recovery_state["reused_files"]
                if (
                    not isinstance(raw_backup, str)
                    or not isinstance(raw_backup_hash, str)
                    or type(raw_copied) is not int
                    or raw_copied < 0
                    or type(raw_reused) is not int
                    or raw_reused < 0
                ):
                    raise ValueError
                backup_file = Path(raw_backup)
                backup_sha256 = raw_backup_hash
                copied = raw_copied
                reused = raw_reused
                remote_relocations = _validate_remote_result_relocations(
                    plan,
                    recovery_state["remote_result_relocations"],
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise WorkspaceMigrationError(
                    "迁移恢复状态字段损坏，拒绝继续"
                ) from exc
            _verify_published_files(plan)
            _verify_database_backup(
                backup_file,
                backup_root=backups,
                expected_hash=backup_sha256,
            )
            database_state = _database_path_state(plan)
            if database_state == "mixed":
                raise WorkspaceMigrationError(
                    "数据库路径处于混合状态，拒绝自动继续迁移"
                )
            if (
                database_state == "old"
                and recovery_state["phase"] != "ready_to_switch"
            ):
                raise WorkspaceMigrationError(
                    "迁移阶段与数据库路径不一致，拒绝自动继续"
                )
            if database_state == "new":
                relocated = _apply_remote_result_relocations(
                    remote_relocations,
                    remote_job_store,
                )
                receipt = _migration_receipt(
                    plan,
                    backup_file=backup_file,
                    backup_sha256=backup_sha256,
                    copied_files=copied,
                    reused_files=reused,
                    relocated_remote_results=relocated,
                )
                write_json(receipt_file, receipt)
                completed_state = dict(recovery_state)
                completed_state["phase"] = "completed"
                write_json(state_file, completed_state)
                return WorkspaceMigrationResult(
                    migration_id=plan.migration_id,
                    copied_files=copied,
                    reused_files=reused,
                    database_backup=backup_file,
                    receipt_file=receipt_file,
                    relocated_remote_results=relocated,
                )

        if recovery_state is None:
            refreshed = inventory_legacy_workspace(
                source_root=plan.source_root,
                target_root=plan.target_root,
                database_file=plan.database_file,
            )
            if (
                refreshed.migration_id != plan.migration_id
                or refreshed.files != plan.files
                or refreshed.projects != plan.projects
                or refreshed.blockers != plan.blockers
            ):
                raise WorkspaceMigrationError("迁移清单已变化，请重新盘点后再确认")

            staging_root = (
                plan.target_root
                / ".ezyolo"
                / "workspace-migrations"
                / plan.migration_id
            )
            staging_files = staging_root / "files"
            for item in plan.files:
                staged = staging_files / item.destination_relative
                if _copy_verified(
                    item.source,
                    staged,
                    item.size,
                    item.sha256,
                    allowed_root=plan.target_root,
                    copy_file=copy_file,
                ):
                    copied += 1
                else:
                    reused += 1

            # 全部 staging 核验完成后，逐文件 no-replace 发布；DB 仍指向旧目录。
            for item in plan.files:
                staged = staging_files / item.destination_relative
                final = plan.target_root / item.destination_relative
                _copy_verified(
                    staged,
                    final,
                    item.size,
                    item.sha256,
                    allowed_root=plan.target_root,
                    copy_file=copy_file,
                )

            for project in plan.projects:
                _ensure_safe_directory(
                    plan.target_root,
                    plan.target_root / project.destination_root_relative,
                )

            backup_file = _backup_database(
                plan.database_file, backups, plan.migration_id
            )
            backup_sha256 = _sha256(backup_file)
            remote_relocations = _plan_remote_result_relocations(
                plan,
                remote_job_store,
            )
            ready_state = {
                "version": 1,
                "migration_id": plan.migration_id,
                "phase": "ready_to_switch",
                "database_backup": str(backup_file),
                "database_backup_sha256": backup_sha256,
                "copied_files": copied,
                "reused_files": reused,
                "remote_result_relocations": remote_relocations,
            }
            write_json(state_file, ready_state)
        else:
            ready_state = dict(recovery_state)

        relocated_remote_results = _apply_remote_result_relocations(
            remote_relocations,
            remote_job_store,
        )
        _switch_database_paths(plan)
        switched_state = dict(ready_state)
        switched_state["phase"] = "database_switched"
        write_json(state_file, switched_state)

        receipt = _migration_receipt(
            plan,
            backup_file=backup_file,
            backup_sha256=backup_sha256,
            copied_files=copied,
            reused_files=reused,
            relocated_remote_results=relocated_remote_results,
        )
        write_json(receipt_file, receipt)
        completed_state = dict(switched_state)
        completed_state["phase"] = "completed"
        write_json(state_file, completed_state)
        return WorkspaceMigrationResult(
            migration_id=plan.migration_id,
            copied_files=copied,
            reused_files=reused,
            database_backup=backup_file,
            receipt_file=receipt_file,
            relocated_remote_results=relocated_remote_results,
        )
    except sqlite3.Error as exc:
        raise WorkspaceMigrationError(f"数据库路径切换失败: {exc}") from exc
    finally:
        lock.unlock()


__all__ = [
    "MigrationFile",
    "ProjectPathMigration",
    "WorkspaceMigrationError",
    "WorkspaceMigrationPlan",
    "WorkspaceMigrationResult",
    "execute_workspace_migration",
    "inventory_legacy_workspace",
]
