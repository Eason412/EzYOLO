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
from remote_protocol.v1 import (
    REMOTE_PROTOCOL_VERSION,
    JobStatus,
    ManifestEntry,
    ResultManifest,
    ResultReceipt,
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


def _verified_result_receipt(result_root: Path, job_id: str) -> ResultReceipt:
    result_bytes = (result_root / "weights" / "best.pt").read_bytes()
    manifest = ResultManifest(
        protocol_version=REMOTE_PROTOCOL_VERSION,
        job_id=job_id,
        entries=(
            ManifestEntry(
                path="weights/best.pt",
                size=len(result_bytes),
                sha256=hashlib.sha256(result_bytes).hexdigest(),
            ),
        ),
        total_bytes=len(result_bytes),
    )
    manifest_bytes = (
        json.dumps(
            manifest.to_wire(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    (result_root / "manifest.json").write_bytes(manifest_bytes)
    return ResultReceipt(
        protocol_version=REMOTE_PROTOCOL_VERSION,
        job_id=job_id,
        result_manifest_hash=hashlib.sha256(manifest_bytes).hexdigest(),
        result_count=1,
        result_bytes=len(result_bytes),
    )


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


def _write_json(path: Path, payload: dict) -> None:
    temporary_file = path.with_name(f".{path.name}.tmp")
    temporary_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary_file, path)


class _FailFirstJsonWrite:
    def __init__(self, file_name: str):
        self.file_name = file_name
        self.failed = False

    def __call__(self, path: Path, payload: dict) -> None:
        if path.name == self.file_name and not self.failed:
            self.failed = True
            raise OSError(f"simulated {self.file_name} failure")
        _write_json(path, payload)


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
        fail_first_receipt = _FailFirstJsonWrite("switch-receipt.json")

        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
                write_json=fail_first_receipt,
            )
        except OSError as exc:
            assert "simulated switch-receipt.json failure" in str(exc)
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


def test_recovery_rejects_symlinked_target_parent_after_database_switch():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target, database, _image, _run_best, _partial = _fixture(root)
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        fail_first_receipt = _FailFirstJsonWrite("switch-receipt.json")
        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
                write_json=fail_first_receipt,
            )
        except OSError:
            pass
        else:
            raise AssertionError("测试必须进入数据库已切换、回执未写入的窗口")

        outside = root / "outside-projects"
        (target / "projects").rename(outside)
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
            raise AssertionError("恢复时目标父目录 symlink 必须阻止完成回执")


def test_recovery_rejects_corrupted_database_backup():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target, database, _image, _run_best, _partial = _fixture(root)
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        fail_first_receipt = _FailFirstJsonWrite("switch-receipt.json")
        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
                write_json=fail_first_receipt,
            )
        except OSError:
            pass
        else:
            raise AssertionError("测试必须进入数据库已切换、回执未写入的窗口")

        state_file = (
            root
            / "app-state"
            / "workspace-migrations"
            / plan.migration_id
            / "state.json"
        )
        recovery_state = json.loads(state_file.read_text(encoding="utf-8"))
        Path(recovery_state["database_backup"]).write_bytes(b"corrupt")
        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
            )
        except WorkspaceMigrationError:
            pass
        else:
            raise AssertionError("损坏的数据库备份必须阻止完成回执")


def test_recovery_treats_new_legacy_project_as_mixed_database_state():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target, database, _image, _run_best, _partial = _fixture(root)
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        fail_first_receipt = _FailFirstJsonWrite("switch-receipt.json")
        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
                write_json=fail_first_receipt,
            )
        except OSError:
            pass
        else:
            raise AssertionError("测试必须进入数据库已切换、回执未写入的窗口")

        extra_project = source / "projects" / "late-project"
        extra_image = extra_project / "images" / "late.jpg"
        extra_image.parent.mkdir(parents=True)
        extra_image.write_bytes(b"late")
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "INSERT INTO projects VALUES (2, ?, 'late')",
                (str(extra_project),),
            )
            connection.execute(
                "INSERT INTO images VALUES (20, 2, ?)",
                (str(extra_image),),
            )
            connection.commit()
        finally:
            connection.close()
        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
            )
        except WorkspaceMigrationError as exc:
            assert "混合状态" in str(exc)
        else:
            raise AssertionError("新增旧路径项目必须被识别为 mixed")


def test_state_directory_symlink_and_corrupted_recovery_state_fail_closed():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target, database, _image, _run_best, _partial = _fixture(root)
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        state_root = root / "app-state"
        outside = root / "outside-state"
        state_root.mkdir()
        outside.mkdir()
        (state_root / "workspace-migrations").symlink_to(
            outside,
            target_is_directory=True,
        )
        try:
            execute_workspace_migration(
                plan,
                state_root=state_root,
                database_backups_root=root / "db-backups",
            )
        except WorkspaceMigrationError:
            pass
        else:
            raise AssertionError("迁移状态目录 symlink 必须阻止写入")
        assert list(outside.iterdir()) == []

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target, database, _image, _run_best, _partial = _fixture(root)
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
                write_json=_FailFirstJsonWrite("switch-receipt.json"),
            )
        except OSError:
            pass
        state_file = (
            root
            / "app-state"
            / "workspace-migrations"
            / plan.migration_id
            / "state.json"
        )
        recovery_state = json.loads(state_file.read_text(encoding="utf-8"))
        del recovery_state["database_backup_sha256"]
        _write_json(state_file, recovery_state)
        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
            )
        except WorkspaceMigrationError as exc:
            assert "状态字段损坏" in str(exc)
        else:
            raise AssertionError("字段损坏的恢复状态必须明确阻止继续")


def test_legacy_v1_recovery_state_is_upgraded_without_losing_remote_count():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target, database, _image, _run_best, _partial = _fixture(root)
        old_result = source / "runs" / "train" / "exp_1_remote_abcd"
        receipt = _verified_result_receipt(old_result, "a" * 32)
        succeeded = SimpleNamespace(
            job_id="a" * 32,
            local_result_dir=str(old_result),
            last_status=JobStatus.SUCCEEDED,
            result_receipt=receipt,
        )

        class StatefulStore:
            def list(self):
                return [succeeded]

            def relocate_verified_result(
                self, job_id, *, expected_local_result_dir, new_local_result_dir
            ):
                assert job_id == succeeded.job_id
                assert Path(succeeded.local_result_dir) == Path(expected_local_result_dir)
                succeeded.local_result_dir = str(new_local_result_dir)

        store = StatefulStore()
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
                remote_job_store=store,
                write_json=_FailFirstJsonWrite("switch-receipt.json"),
            )
        except OSError:
            pass
        state_file = (
            root
            / "app-state"
            / "workspace-migrations"
            / plan.migration_id
            / "state.json"
        )
        current_state = json.loads(state_file.read_text(encoding="utf-8"))
        legacy_state = {
            "version": 1,
            "migration_id": current_state["migration_id"],
            "phase": current_state["phase"],
            "database_backup": current_state["database_backup"],
            "copied_files": current_state["copied_files"],
            "reused_files": current_state["reused_files"],
            "relocated_remote_results": 1,
        }
        _write_json(state_file, legacy_state)

        result = execute_workspace_migration(
            plan,
            state_root=root / "app-state",
            database_backups_root=root / "db-backups",
            remote_job_store=store,
        )
        upgraded_state = json.loads(state_file.read_text(encoding="utf-8"))
        receipt = json.loads(result.receipt_file.read_text(encoding="utf-8"))
        assert upgraded_state["version"] == 2
        assert receipt["relocated_remote_results"] == 1
        assert Path(succeeded.local_result_dir).is_relative_to(target / "runs")


def test_legacy_v1_recovery_rejects_unknown_target_record():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target, database, _image, _run_best, _partial = _fixture(root)
        old_result = source / "runs" / "train" / "exp_1_remote_abcd"
        receipt = _verified_result_receipt(old_result, "a" * 32)
        record = SimpleNamespace(
            job_id="a" * 32,
            local_result_dir=str(old_result),
            last_status=JobStatus.SUCCEEDED,
            result_receipt=receipt,
        )

        class StatefulStore:
            def list(self):
                return [record]

            def relocate_verified_result(
                self, job_id, *, expected_local_result_dir, new_local_result_dir
            ):
                record.local_result_dir = str(new_local_result_dir)

        store = StatefulStore()
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
                remote_job_store=store,
                write_json=_FailFirstJsonWrite("switch-receipt.json"),
            )
        except OSError:
            pass
        state_file = (
            root
            / "app-state"
            / "workspace-migrations"
            / plan.migration_id
            / "state.json"
        )
        current_state = json.loads(state_file.read_text(encoding="utf-8"))
        _write_json(
            state_file,
            {
                "version": 1,
                "migration_id": current_state["migration_id"],
                "phase": current_state["phase"],
                "database_backup": current_state["database_backup"],
                "copied_files": current_state["copied_files"],
                "reused_files": current_state["reused_files"],
                "relocated_remote_results": 1,
            },
        )
        record.last_status = JobStatus.UNKNOWN
        record.result_receipt = None

        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
                remote_job_store=store,
            )
        except WorkspaceMigrationError:
            pass
        else:
            raise AssertionError("UNKNOWN 任务不能被旧版状态适配器计为已迁移成功")
        assert record.last_status == JobStatus.UNKNOWN
        assert not state_file.with_name("switch-receipt.json").exists()


def test_remote_relocation_waits_for_durable_state_and_count_survives_retry():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target, database, _image, _run_best, _partial = _fixture(root)
        old_result = source / "runs" / "train" / "exp_1_remote_abcd"
        succeeded = SimpleNamespace(
            job_id="s" * 32,
            local_result_dir=str(old_result),
        )

        class StatefulStore:
            def list(self):
                return [succeeded]

            def relocate_verified_result(
                self, job_id, *, expected_local_result_dir, new_local_result_dir
            ):
                assert job_id == succeeded.job_id
                assert Path(succeeded.local_result_dir) == Path(expected_local_result_dir)
                succeeded.local_result_dir = str(new_local_result_dir)

        store = StatefulStore()
        plan = inventory_legacy_workspace(
            source_root=source,
            target_root=target,
            database_file=database,
        )
        try:
            execute_workspace_migration(
                plan,
                state_root=root / "app-state",
                database_backups_root=root / "db-backups",
                remote_job_store=store,
                write_json=_FailFirstJsonWrite("state.json"),
            )
        except OSError:
            pass
        else:
            raise AssertionError("测试必须进入远程指针已迁移、状态未写入的窗口")
        assert Path(succeeded.local_result_dir) == old_result
        assert _db_paths(database)[0] == source / "projects" / "legacy-project"

        result = execute_workspace_migration(
            plan,
            state_root=root / "app-state",
            database_backups_root=root / "db-backups",
            remote_job_store=store,
        )
        receipt = json.loads(result.receipt_file.read_text(encoding="utf-8"))
        assert receipt["remote_job_records_modified"] is True
        assert receipt["relocated_remote_results"] == 1


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
