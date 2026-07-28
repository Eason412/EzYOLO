"""数据库导入副作用和项目路径注入测试。"""

import _bootstrap  # noqa: F401

from pathlib import Path
import tempfile

from models.database import Database, DatabaseProxy
import models.database as database_module
from core.model_manager import ModelManager


def test_module_global_database_is_inert_until_configured():
    # _bootstrap 会用显式临时 Database 替换模块全局；类本身仍必须提供无写入代理。
    proxy = DatabaseProxy()
    assert proxy._target is None
    try:
        proxy.get_all_projects()
    except RuntimeError:
        pass
    else:
        raise AssertionError("未配置的数据库代理不得偷偷创建默认数据库")


def test_new_project_uses_injected_stable_project_root():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = Database(
            db_path=str(root / "state" / "EzYOLO.db"),
            projects_root=root / "workspace" / "projects",
        )
        project_id = database.create_project("名称可以修改")
        project = database.get_project(project_id)
        assert project["storage_path"] == str(
            root / "workspace" / "projects" / f"project-{project_id:06d}"
        )
        assert Path(project["storage_path"]).is_dir()
        assert "名称可以修改" not in project["storage_path"]


def test_default_sync_scan_uses_injected_projects_root():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = Database(
            db_path=str(root / "state" / "EzYOLO.db"),
            projects_root=root / "workspace" / "projects",
        )
        database.projects_root.mkdir(parents=True)
        orphan = database.projects_root / "orphan"
        orphan.mkdir()
        result = database.sync_files_with_database()
        assert result["orphan_disk_count"] == 1
        assert any(issue.get("path") == str(orphan) for issue in result["issues"])


def test_model_manager_uses_injected_cache_without_import_side_effect():
    with tempfile.TemporaryDirectory() as temporary:
        models_root = Path(temporary) / "cache" / "models"
        manager = ModelManager(pretrained_dir=models_root)
        assert manager.pretrained_dir == models_root
        assert models_root.is_dir()


if __name__ == "__main__":
    raise SystemExit(_bootstrap.run_module_tests(globals()))
