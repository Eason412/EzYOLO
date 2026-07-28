"""旧源码工作区迁移的 inventory、复制、核验和回滚边界测试。"""

import _bootstrap  # noqa: F401

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

from core.workspace_migration import (
    WorkspaceMigrationError,
    execute_workspace_migration,
    inventory_legacy_workspace,
)


def _database(path: Path, project_root: Path, image_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY,
                storage_path TEXT,
                updated_at TEXT
            );
            CREATE TABLE images (
                id INTEGER PRIMARY KEY,
                project_id INTEGER,
                storage_path TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO projects VALUES (1, ?, 'old')",
            (str(project_root),),
        )
        connection.execute(
            "INSERT INTO images VALUES (10, 1, ?)",
            (str(image_path),),
        )
        connection.commit()
    finally:
        connection.close()


def _fixture(root: Path):
    source = root / "source"
    target = root / "Documents" / "EzYOLO"
    project = source / "projects" / "legacy-project"
    image = project / "images" / "one.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    run_best = source / "runs" / "train" / "exp_1_remote_abcd" / "weights" / "best.pt"
    run_best.parent.mkdir(parents=True)
    run_best.write_bytes(b"best")
    partial = source / "runs" / ".remote-staging" / "unknown-job" / "manifest.json"
    partial.parent.mkdir(parents=True)
    partial.write_text('{"status":"partial"}', encoding="utf-8")
    database = root / "state" / "EzYOLO.db"
    _database(database, project, image)
    return source, target, database, image, run_best, partial


def _db_paths(database: Path):
    connection = sqlite3.connect(database)
    try:
        project = connection.execute(
            "SELECT storage_path FROM projects WHERE id = 1"
        ).fetchone()[0]
        image = connection.execute(
            "SELECT storage_path FROM images WHERE id = 10"
        ).fetchone()[0]
        return Path(project), Path(image)
    finally:
        connection.close()


def test_inventory_is_read_only_and_includes_project_run_and_unknown_staging():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target, database, image, run_best, partial = _fixture(root)
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        assert not plan.blockers
        assert plan.file_count == 3
        assert plan.total_bytes == sum(path.stat().st_size for path in (image, run_best, partial))
        assert not target.exists()
        assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_migration_copies_and_hashes_then_switches_db_without_deleting_source():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target, database, image, run_best, partial = _fixture(root)
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        result = execute_workspace_migration(
            plan,
            state_root=root / "app-state",
            database_backups_root=root / "db-backups",
        )
        project_path, image_path = _db_paths(database)
        assert project_path == target / "projects" / "project-000001"
        assert image_path == project_path / "images" / "one.jpg"
        assert image_path.read_bytes() == b"image"
        assert (
            target / "runs" / "train" / "exp_1_remote_abcd" / "weights" / "best.pt"
        ).read_bytes() == b"best"
        assert (
            target / "runs" / ".remote-staging" / "unknown-job" / "manifest.json"
        ).read_text(encoding="utf-8") == '{"status":"partial"}'
        assert image.exists() and run_best.exists() and partial.exists()
        assert result.database_backup.is_file()
        assert result.receipt_file.is_file()


def test_copy_failure_keeps_database_on_old_paths_and_is_retryable():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target, database, image, _run_best, _partial = _fixture(root)
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        calls = 0

        def fail_once(source_file, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk full")
            import shutil
            return shutil.copy2(source_file, destination)

        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
                copy_file=fail_once,
            )
        except (WorkspaceMigrationError, OSError):
            pass
        else:
            raise AssertionError("复制失败必须停止迁移")
        assert _db_paths(database)[0] == source / "projects" / "legacy-project"
        assert image.exists()

        result = execute_workspace_migration(
            plan,
            state_root=root / "app-state",
            database_backups_root=root / "db-backups",
        )
        assert result.receipt_file.is_file()
        assert _db_paths(database)[0] == target / "projects" / "project-000001"


def test_receipt_write_failure_after_database_switch_is_recoverable():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target, database, image, _run_best, _partial = _fixture(root)
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        failed_once = False

        def fail_first_receipt(path, payload):
            nonlocal failed_once
            if path.name == "switch-receipt.json" and not failed_once:
                failed_once = True
                raise OSError("simulated receipt failure")
            temporary_file = path.with_name(f".{path.name}.tmp")
            temporary_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary_file, path)

        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
                write_json=fail_first_receipt,
            )
        except OSError as exc:
            assert "simulated receipt failure" in str(exc)
        else:
            raise AssertionError("第一次完成回执写入失败必须向调用方报告")

        expected_project = target / "projects" / "project-000001"
        assert _db_paths(database)[0] == expected_project
        assert image.exists()

        result = execute_workspace_migration(
            plan,
            state_root=root / "app-state",
            database_backups_root=root / "db-backups",
            write_json=fail_first_receipt,
        )
        assert result.receipt_file.is_file()
        assert _db_paths(database)[0] == expected_project
        assert image.exists()


def test_conflict_and_symlink_block_execution_without_writes():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target, database, _image, _run_best, _partial = _fixture(root)
        conflict = target / "runs" / "train" / "exp_1_remote_abcd" / "weights" / "best.pt"
        conflict.parent.mkdir(parents=True)
        conflict.write_bytes(b"different")
        link = source / "runs" / "linked"
        link.symlink_to(root / "outside", target_is_directory=True)
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        assert any("不同内容" in blocker for blocker in plan.blockers)
        assert any("符号链接" in blocker for blocker in plan.blockers)
        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
            )
        except WorkspaceMigrationError:
            pass
        else:
            raise AssertionError("存在 blocker 时不得执行")
        assert _db_paths(database)[0] == source / "projects" / "legacy-project"


def test_stale_inventory_and_symlinked_target_parent_fail_closed():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target, database, image, _run_best, _partial = _fixture(root)
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        image.write_bytes(b"changed after preview")
        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
            )
        except WorkspaceMigrationError as exc:
            assert "清单已变化" in str(exc)
        else:
            raise AssertionError("盘点后来源变化必须要求重新确认")
        assert _db_paths(database)[0] == source / "projects" / "legacy-project"

        # 重新盘点后，在目标内植入父目录 symlink，执行时不得跟随。
        image.write_bytes(b"image")
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        target.mkdir(parents=True)
        outside = root / "outside"
        outside.mkdir()
        (target / "projects").symlink_to(outside, target_is_directory=True)
        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
            )
        except WorkspaceMigrationError:
            pass
        else:
            raise AssertionError("目标父目录符号链接必须阻止迁移")
        assert list(outside.iterdir()) == []
        assert _db_paths(database)[0] == source / "projects" / "legacy-project"


def test_remote_result_pointer_moves_to_verified_copy_while_unknown_is_untouched():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target, database, _image, _run_best, _partial = _fixture(root)
        old_result = source / "runs" / "train" / "exp_1_remote_abcd"
        unknown = SimpleNamespace(job_id="u" * 32, local_result_dir=None)
        succeeded = SimpleNamespace(job_id="s" * 32, local_result_dir=str(old_result))

        class FakeStore:
            def __init__(self):
                self.calls = []

            def list(self):
                return [unknown, succeeded]

            def relocate_verified_result(
                self, job_id, *, expected_local_result_dir, new_local_result_dir
            ):
                assert Path(new_local_result_dir, "weights", "best.pt").read_bytes() == b"best"
                self.calls.append(
                    (job_id, Path(expected_local_result_dir), Path(new_local_result_dir))
                )

        store = FakeStore()
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        result = execute_workspace_migration(
            plan,
            state_root=root / "app-state",
            database_backups_root=root / "db-backups",
            remote_job_store=store,
        )
        assert store.calls == [
            (
                "s" * 32,
                old_result,
                target / "runs" / "train" / "exp_1_remote_abcd",
            )
        ]
        assert result.relocated_remote_results == 1


if __name__ == "__main__":
    raise SystemExit(_bootstrap.run_module_tests(globals()))
