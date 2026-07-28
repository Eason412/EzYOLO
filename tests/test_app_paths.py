"""跨平台运行目录解析的纯离线测试。"""

import _bootstrap  # noqa: F401

from pathlib import Path, PureWindowsPath
import tempfile

from core.app_paths import (
    AppPathError,
    platform_default_locations,
    resolve_pretrained_models_root,
    resolve_runtime_paths,
)


def _rejects(callable_object, *args, **kwargs):
    try:
        callable_object(*args, **kwargs)
    except AppPathError:
        return
    raise AssertionError("预期拒绝不安全路径")


def test_platform_default_locations_cover_macos_windows_and_linux():
    mac = platform_default_locations("darwin", home="/Users/test")
    assert mac["app_data"] == Path("/Users/test/Library/Application Support/EzYOLO")
    assert mac["documents"] == Path("/Users/test/Documents")

    windows = platform_default_locations(
        "win32",
        home="C:/Users/test",
        environ={
            "LOCALAPPDATA": "C:/Users/test/AppData/Local",
            "USERPROFILE": "C:/Users/test",
        },
    )
    assert windows["app_data"] == PureWindowsPath("C:/Users/test/AppData/Local/EzYOLO")
    assert "Roaming" not in str(windows["app_data"])

    linux = platform_default_locations("linux", home="/home/test")
    assert linux["app_data"] == Path("/home/test/.local/share/EzYOLO")
    assert linux["cache"] == Path("/home/test/.cache/EzYOLO")


def test_resolver_is_cwd_independent_and_creates_nothing():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        paths = resolve_runtime_paths(
            resource_root=root / "source",
            app_data_location=root / "state",
            cache_location=root / "cache",
            documents_location=root / "documents",
        )
        assert paths.workspace.root == root / "documents" / "EzYOLO"
        assert paths.app.database_file == root / "state" / "database" / "EzYOLO.db"
        assert paths.workspace.remote_result_staging.parent == paths.workspace.runs_root
        assert paths.workspace.runs_train_root.parent == paths.workspace.runs_root
        assert not paths.app.data_root.exists()
        assert not paths.workspace.root.exists()


def test_resolver_rejects_relative_empty_and_source_nested_workspace():
    common = {
        "resource_root": "/opt/EzYOLO",
        "app_data_location": "/tmp/state",
        "cache_location": "/tmp/cache",
        "documents_location": "/tmp/documents",
    }
    _rejects(resolve_runtime_paths, **{**common, "app_data_location": "relative"})
    _rejects(resolve_runtime_paths, **{**common, "cache_location": ""})
    _rejects(
        resolve_runtime_paths,
        **{**common, "workspace_root": "/opt/EzYOLO/runs"},
    )


def test_legacy_source_pretrained_setting_falls_back_to_user_cache():
    paths = resolve_runtime_paths(
        resource_root="/opt/EzYOLO",
        app_data_location="/tmp/state",
        cache_location="/tmp/cache",
        documents_location="/tmp/documents",
    )
    assert resolve_pretrained_models_root(
        paths, "/opt/EzYOLO/pretrained"
    ) == Path("/tmp/cache/models")
    assert resolve_pretrained_models_root(
        paths, "/mnt/models"
    ) == Path("/mnt/models")


if __name__ == "__main__":
    raise SystemExit(_bootstrap.run_module_tests(globals()))
