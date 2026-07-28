"""EzYOLO 可写目录的唯一解析入口。

资源目录只用于读取随应用发布的文件。数据库、项目、训练结果和推理输出均由
``RuntimePaths`` 指向用户目录，不能再从当前工作目录或源码目录推导。
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PureWindowsPath
import sys

from PyQt6.QtCore import QSettings, QStandardPaths


WORKSPACE_SETTING_KEY = "workspace_root_v1"


class AppPathError(ValueError):
    """平台没有提供安全、绝对的可写目录。"""


@dataclass(frozen=True)
class AppPaths:
    resource_root: Path
    data_root: Path
    cache_root: Path
    log_root: Path
    database_file: Path
    database_backups_root: Path
    remote_state_root: Path


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    projects_root: Path
    runs_root: Path
    runs_train_root: Path
    remote_result_staging: Path
    datasets_root: Path
    outputs_root: Path


@dataclass(frozen=True)
class RuntimePaths:
    app: AppPaths
    workspace: WorkspacePaths


_runtime_paths: RuntimePaths | None = None


def _absolute_path(value: str | Path, label: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise AppPathError(f"本机没有可用的{label}")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise AppPathError(f"{label}必须是绝对路径")
    return path


def resolve_runtime_paths(
    *,
    resource_root: str | Path,
    app_data_location: str | Path,
    cache_location: str | Path,
    documents_location: str | Path,
    workspace_root: str | Path | None = None,
    log_location: str | Path | None = None,
) -> RuntimePaths:
    """从已知平台目录计算路径；纯解析，不创建文件或目录。"""
    resources = _absolute_path(resource_root, "应用资源目录")
    data_root = _absolute_path(app_data_location, "应用数据目录")
    cache_root = _absolute_path(cache_location, "缓存目录")
    documents_root = _absolute_path(documents_location, "文档目录")
    logs = _absolute_path(log_location or data_root / "logs", "日志目录")
    workspace = _absolute_path(workspace_root or documents_root / "EzYOLO", "工作区目录")

    # 源码/安装目录可能只读，也可能在升级时被替换。禁止把新的工作区配置回去。
    try:
        workspace.relative_to(resources)
    except ValueError:
        pass
    else:
        raise AppPathError("工作区不能放在应用安装或源码目录内")

    app = AppPaths(
        resource_root=resources,
        data_root=data_root,
        cache_root=cache_root,
        log_root=logs,
        database_file=data_root / "database" / "EzYOLO.db",
        database_backups_root=data_root / "database-backups",
        remote_state_root=data_root / "remote-training-v1",
    )
    workspace_paths = WorkspacePaths(
        root=workspace,
        projects_root=workspace / "projects",
        runs_root=workspace / "runs",
        runs_train_root=workspace / "runs" / "train",
        remote_result_staging=workspace / "runs" / ".remote-staging",
        datasets_root=cache_root / "training-datasets",
        outputs_root=workspace / "outputs",
    )
    return RuntimePaths(app=app, workspace=workspace_paths)


def platform_default_locations(
    platform_name: str,
    *,
    home: str | Path,
    environ: dict[str, str] | None = None,
) -> dict[str, Path]:
    """返回三平台的约定位置，供无 Qt 环境的映射测试和诊断使用。"""
    env = environ or {}
    if platform_name == "win32":
        home_path = PureWindowsPath(home)
        if not home_path.is_absolute():
            raise AppPathError("用户目录必须是绝对路径")
        local = PureWindowsPath(env.get("LOCALAPPDATA", ""))
        profile = PureWindowsPath(env.get("USERPROFILE", str(home_path)))
        if not local.is_absolute() or not profile.is_absolute():
            raise AppPathError("LOCALAPPDATA 和 USERPROFILE 必须是绝对路径")
        return {
            "app_data": local / "EzYOLO",
            "cache": local / "EzYOLO" / "cache",
            "logs": local / "EzYOLO" / "logs",
            "documents": profile / "Documents",
        }
    home_path = _absolute_path(home, "用户目录")
    if platform_name == "darwin":
        return {
            "app_data": home_path / "Library" / "Application Support" / "EzYOLO",
            "cache": home_path / "Library" / "Caches" / "EzYOLO",
            "logs": home_path / "Library" / "Logs" / "EzYOLO",
            "documents": home_path / "Documents",
        }
    if platform_name.startswith("linux"):
        data = _absolute_path(env.get("XDG_DATA_HOME", home_path / ".local" / "share"), "XDG_DATA_HOME")
        cache = _absolute_path(env.get("XDG_CACHE_HOME", home_path / ".cache"), "XDG_CACHE_HOME")
        state = _absolute_path(env.get("XDG_STATE_HOME", home_path / ".local" / "state"), "XDG_STATE_HOME")
        documents = _absolute_path(env.get("XDG_DOCUMENTS_DIR", home_path / "Documents"), "XDG_DOCUMENTS_DIR")
        return {
            "app_data": data / "EzYOLO",
            "cache": cache / "EzYOLO",
            "logs": state / "EzYOLO" / "logs",
            "documents": documents,
        }
    raise AppPathError(f"暂不支持的平台: {platform_name}")


def current_runtime_paths(
    resource_root: str | Path,
    *,
    settings: QSettings | None = None,
) -> RuntimePaths:
    """使用 Qt 当前平台目录和已保存的 Workspace 设置解析路径。"""
    settings = settings or QSettings("EzYOLO", "Settings")
    configured_workspace = str(settings.value(WORKSPACE_SETTING_KEY, "") or "").strip()
    log_location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    if sys.platform == "darwin":
        defaults = platform_default_locations("darwin", home=Path.home(), environ=os.environ)
        log_location = str(defaults["logs"])
    elif sys.platform.startswith("linux"):
        defaults = platform_default_locations("linux", home=Path.home(), environ=os.environ)
        log_location = str(defaults["logs"])
    return resolve_runtime_paths(
        resource_root=resource_root,
        app_data_location=QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        ),
        cache_location=QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation
        ),
        documents_location=QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        ),
        workspace_root=configured_workspace or None,
        log_location=log_location,
    )


def configure_runtime_paths(paths: RuntimePaths) -> None:
    """在 QApplication 建立后安装本进程唯一的路径配置。"""
    global _runtime_paths
    _runtime_paths = paths


def get_runtime_paths() -> RuntimePaths:
    if _runtime_paths is None:
        raise RuntimeError("EzYOLO 运行路径尚未配置")
    return _runtime_paths


def prepare_runtime_directories(paths: RuntimePaths) -> None:
    """显式创建本次运行需要的根目录；解析函数本身保持无副作用。"""
    for directory in (
        paths.app.database_file.parent,
        paths.app.database_backups_root,
        paths.app.cache_root,
        paths.app.log_root,
        paths.app.remote_state_root,
        paths.workspace.projects_root,
        paths.workspace.runs_train_root,
        paths.workspace.remote_result_staging,
        paths.workspace.outputs_root,
    ):
        directory.mkdir(parents=True, exist_ok=True)


__all__ = [
    "AppPathError",
    "AppPaths",
    "RuntimePaths",
    "WORKSPACE_SETTING_KEY",
    "WorkspacePaths",
    "configure_runtime_paths",
    "current_runtime_paths",
    "get_runtime_paths",
    "platform_default_locations",
    "prepare_runtime_directories",
    "resolve_runtime_paths",
]
