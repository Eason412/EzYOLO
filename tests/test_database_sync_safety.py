# -*- coding: utf-8 -*-
"""Database.sync_files_with_database 必须是只读扫描，绝不删除任何东西。

背景：旧实现会直接删掉数据库里指向缺失文件的记录，以及 projects/ 目录下
不在数据库中的文件夹/文件。main_window 启动时无参数调用它——一旦文件系统
和数据库出现任何不一致（哪怕只是权限问题、正在写入的临时文件），用户的
项目、图片、标注就会被无声删除。

这里只测 models.database.Database，不走 tests/_bootstrap（它会把
sync_files_with_database 整个换成空 lambda，测不出真实行为），所以自己
造临时数据库 + 临时 projects 目录，绝不碰真实 data/EzYOLO.db 或 projects/。

运行：
    python tests/test_database_sync_safety.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(APP_ROOT))

from models.database import Database  # noqa: E402


def _new_db():
    """临时 sqlite 文件，不落在真实 data/ 目录下。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Database(db_path=tmp.name), tmp.name


def _new_projects_dir():
    return Path(tempfile.mkdtemp(prefix="ezyolo-projects-test-"))


def _insert_project(db_path, name, storage_path):
    """直接向临时 db 的 projects 表插入一行。

    不能用 Database.create_project()：它无视 db_path，永远在真实
    APP_ROOT/projects 下 mkdir（见 tests/_bootstrap.py 的同一个坑），
    这个安全测试必须从一开始就不碰真实 projects/。
    """
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO projects (name, description, type, classes, storage_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, "", "detection", "[]", storage_path),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def test_consistent_state_reports_no_issues():
    db, db_path = _new_db()
    projects_dir = _new_projects_dir()
    try:
        folder = projects_dir / "proj_a_20260101_000000"
        pid = _insert_project(db_path, "proj_a", str(folder))
        folder.mkdir(parents=True, exist_ok=True)
        image_path = folder / "img.jpg"
        image_path.write_bytes(b"fake")
        db.add_image(pid, "img.jpg", str(image_path))

        result = db.sync_files_with_database(projects_dir=str(projects_dir))

        assert result['deleted_db_count'] == 0
        assert result['deleted_file_count'] == 0
        assert result['total_deleted'] == 0
        assert result['orphan_db_count'] == 0
        assert result['orphan_disk_count'] == 0
        assert result['issues'] == []
        assert result['has_issues'] is False
    finally:
        shutil.rmtree(projects_dir, ignore_errors=True)
        os.unlink(db_path)


def test_missing_file_is_reported_but_db_row_survives():
    db, db_path = _new_db()
    projects_dir = _new_projects_dir()
    try:
        project_folder = projects_dir / "proj_b_20260101_000000"
        project_folder.mkdir(parents=True, exist_ok=True)
        pid = _insert_project(db_path, "proj_b", str(project_folder))
        image_id = db.add_image(pid, "gone.jpg", str(projects_dir / "does_not_exist.jpg"))

        result = db.sync_files_with_database(projects_dir=str(projects_dir))

        assert result['deleted_db_count'] == 0
        assert result['total_deleted'] == 0
        assert result['orphan_db_count'] == 1
        assert result['has_issues'] is True
        assert any(i.get('type') == 'missing_file' and i.get('image_id') == image_id
                   for i in result['issues'])

        # 记录必须原封不动地还在数据库里
        assert db.get_image(image_id) is not None
    finally:
        shutil.rmtree(projects_dir, ignore_errors=True)
        os.unlink(db_path)


def test_orphan_project_dir_is_reported_but_kept_on_disk():
    db, db_path = _new_db()
    projects_dir = _new_projects_dir()
    try:
        orphan_folder = projects_dir / "mystery_project_20260101_000000"
        orphan_folder.mkdir(parents=True, exist_ok=True)
        sentinel = orphan_folder / "sentinel.jpg"
        sentinel.write_bytes(b"keep me")

        result = db.sync_files_with_database(projects_dir=str(projects_dir))

        assert result['deleted_file_count'] == 0
        assert result['total_deleted'] == 0
        assert result['orphan_disk_count'] >= 1
        assert result['has_issues'] is True
        assert any(i.get('type') == 'orphan_project_dir' for i in result['issues'])

        # 文件夹和哨兵文件必须原封不动
        assert orphan_folder.exists()
        assert sentinel.exists()
        assert sentinel.read_bytes() == b"keep me"
    finally:
        shutil.rmtree(projects_dir, ignore_errors=True)
        os.unlink(db_path)


def test_default_call_without_arguments_is_safe():
    """main_window 启动时是无参数调用，签名必须兼容且默认路径依旧只读。"""
    db, db_path = _new_db()
    try:
        result = db.sync_files_with_database()

        assert result['deleted_db_count'] == 0
        assert result['deleted_file_count'] == 0
        assert result['total_deleted'] == 0
        assert 'orphan_db_count' in result
        assert 'orphan_disk_count' in result
        assert 'issues' in result
    finally:
        os.unlink(db_path)


def test_repeated_scan_is_idempotent():
    db, db_path = _new_db()
    projects_dir = _new_projects_dir()
    try:
        pid = _insert_project(db_path, "proj_c", str(projects_dir / "proj_c_20260101_000000"))
        db.add_image(pid, "gone.jpg", str(projects_dir / "missing.jpg"))
        orphan_folder = projects_dir / "orphan_20260101_000000"
        orphan_folder.mkdir(parents=True, exist_ok=True)

        first = db.sync_files_with_database(projects_dir=str(projects_dir))
        second = db.sync_files_with_database(projects_dir=str(projects_dir))

        assert first['orphan_db_count'] == second['orphan_db_count']
        assert first['orphan_disk_count'] == second['orphan_disk_count']
        assert first['deleted_db_count'] == second['deleted_db_count'] == 0
        assert first['deleted_file_count'] == second['deleted_file_count'] == 0
    finally:
        shutil.rmtree(projects_dir, ignore_errors=True)
        os.unlink(db_path)


def test_short_project_name_does_not_claim_unrelated_folder():
    """项目名是短片段（如 "a"）时，不能靠子串匹配误认领别的目录。"""
    db, db_path = _new_db()
    projects_dir = _new_projects_dir()
    try:
        pid = _insert_project(db_path, "a", str(projects_dir / "proj_a_20260101_000000"))
        (projects_dir / "proj_a_20260101_000000").mkdir(parents=True, exist_ok=True)

        # 这个目录名里包含 "a"，但它不是数据库项目 "a" 的 storage_path
        unrelated_folder = projects_dir / "banana_20260101_000000"
        unrelated_folder.mkdir(parents=True, exist_ok=True)
        sentinel = unrelated_folder / "sentinel.jpg"
        sentinel.write_bytes(b"keep me")

        result = db.sync_files_with_database(projects_dir=str(projects_dir))

        assert result['deleted_file_count'] == 0
        assert result['has_issues'] is True
        assert any(
            i.get('type') == 'orphan_project_dir' and i.get('path') == str(unrelated_folder)
            for i in result['issues']
        )
        # 无关目录必须原封不动
        assert unrelated_folder.exists()
        assert sentinel.exists()
    finally:
        shutil.rmtree(projects_dir, ignore_errors=True)
        os.unlink(db_path)


def test_missing_project_storage_dir_is_reported():
    """项目在数据库里存在但 storage_path 目录不存在（且没有任何图片）时必须被发现。"""
    db, db_path = _new_db()
    projects_dir = _new_projects_dir()
    try:
        missing_storage = projects_dir / "proj_e_20260101_000000"
        pid = _insert_project(db_path, "proj_e", str(missing_storage))
        # 故意不 mkdir missing_storage，且不给这个项目添加任何图片

        result = db.sync_files_with_database(projects_dir=str(projects_dir))

        assert result['deleted_db_count'] == 0
        assert result['orphan_db_count'] >= 1
        assert result['has_issues'] is True
        assert any(
            i.get('type') == 'missing_project_dir' and i.get('project_id') == pid
            for i in result['issues']
        )
    finally:
        shutil.rmtree(projects_dir, ignore_errors=True)
        os.unlink(db_path)


def test_scan_exception_does_not_trigger_any_deletion():
    db, db_path = _new_db()
    projects_dir = _new_projects_dir()
    try:
        pid = _insert_project(db_path, "proj_d", str(projects_dir / "proj_d_20260101_000000"))
        image_path = projects_dir / "flaky.jpg"
        image_path.write_bytes(b"fake")
        image_id = db.add_image(pid, "flaky.jpg", str(image_path))

        with patch("models.database.os.path.exists", side_effect=OSError("boom")):
            try:
                result = db.sync_files_with_database(projects_dir=str(projects_dir))
            except Exception:
                result = None

        # 无论是抛异常还是把异常收进 issues，都不能删任何东西
        assert db.get_image(image_id) is not None
        assert image_path.exists()
        if result is not None:
            assert result['deleted_db_count'] == 0
            assert result['deleted_file_count'] == 0
            assert result['total_deleted'] == 0
    finally:
        shutil.rmtree(projects_dir, ignore_errors=True)
        os.unlink(db_path)


if __name__ == "__main__":
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {exc!r}")
    print(f"\n{'all passed' if not failures else f'{failures} failed'}")
    sys.exit(1 if failures else 0)
