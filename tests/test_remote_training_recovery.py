# -*- coding: utf-8 -*-
"""远程训练重开恢复与断线续接的离线回归测试。"""

import _bootstrap  # noqa: F401  必须先隔离 Qt、数据库和用户设置

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from PyQt6.QtCore import QLockFile  # noqa: E402

from core.remote_training.jobs import (  # noqa: E402
    REMOTE_TRAINING_JOBS_KEY,
    RemoteTrainingJobRecord,
    ResultStager,
)
from core.remote_training.profiles import RemoteTrainingProfile  # noqa: E402
from core.remote_training.results import verify_result_bundle  # noqa: E402
from gui.pages import train_page as train_page_module  # noqa: E402
from gui.pages.train_page import TrainPage  # noqa: E402
from gui.remote_training_thread import RemoteTrainingRecoveryThread  # noqa: E402
from remote_protocol.v1 import (  # noqa: E402
    REMOTE_PROTOCOL_VERSION,
    JobStatus,
    ManifestEntry,
    RemoteStatus,
    ResultManifest,
    ResultReceipt,
    ServerCapabilities,
)
from test_remote_training_profiles import _ed25519_key  # noqa: E402


_app = _bootstrap.app()
JOB_ID = "8" * 32
PROFILE_ID = "7" * 32
SHA256 = "6" * 64


class MemoryJobStore:
    def __init__(self, record):
        self.record = record

    def replace(self, record, *, expected=None):
        assert record.job_id == self.record.job_id
        if expected is not None:
            assert expected == self.record
        self.record = record


class RecoveryBackend:
    def __init__(self):
        self.calls = []

    def preflight(self, profile):
        self.calls.append("preflight")
        return ServerCapabilities(
            protocol_version=REMOTE_PROTOCOL_VERSION,
            canonical_remote_root=profile.remote_root,
            supported_tasks=("detect",),
            model_symbols=("yolo11n",),
            max_epochs=300,
            max_runtime_seconds=3600,
            max_payload_bytes=10_000_000,
            max_result_bytes=10_000_000,
        )

    def poll(self, _profile, job_id):
        self.calls.append("poll")
        return RemoteStatus(
            REMOTE_PROTOCOL_VERSION,
            job_id,
            JobStatus.REMOTE_SUCCEEDED_PENDING_COLLECTION,
        )

    def collect_manifest(self, _profile, job_id):
        self.calls.append("collect-manifest")
        manifest_bytes, result_bytes = _result_bundle(job_id)
        return ResultReceipt(
            REMOTE_PROTOCOL_VERSION,
            job_id,
            hashlib.sha256(manifest_bytes).hexdigest(),
            result_count=1,
            result_bytes=len(result_bytes),
        )

    def download_results(self, _profile, job_id, staging_dir):
        self.calls.append("download-results")
        manifest_bytes, result_bytes = _result_bundle(job_id)
        weights = Path(staging_dir) / "weights"
        weights.mkdir()
        (weights / "best.pt").write_bytes(result_bytes)
        (Path(staging_dir) / "manifest.json").write_bytes(manifest_bytes)

    def cancel(self, _profile, job_id):
        self.calls.append("cancel")
        return RemoteStatus(
            REMOTE_PROTOCOL_VERSION,
            job_id,
            JobStatus.CANCELLED,
        )


def _profile():
    return RemoteTrainingProfile(
        name="训练服务器",
        host="train-lab",
        port=22,
        username="trainer",
        remote_root="/srv/ezyolo/trainer",
        host_public_key=_ed25519_key(),
        id=PROFILE_ID,
    )


def _record(
    project_id=3,
    status=JobStatus.UNKNOWN,
    local_result_dir=None,
    *,
    job_id=JOB_ID,
    updated_at="2026-07-28T04:05:00+00:00",
):
    payload = RemoteTrainingJobRecord.new(
        job_id=job_id,
        project_id=project_id,
        profile_id=PROFILE_ID,
        canonical_remote_root="/srv/ezyolo/trainer",
        snapshot_hash=SHA256,
        now=datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc),
    ).to_dict()
    payload["last_status"] = status.value
    payload["updated_at"] = updated_at
    payload["local_result_dir"] = (
        str(local_result_dir) if local_result_dir is not None else None
    )
    return RemoteTrainingJobRecord.from_dict(payload)


def _result_bundle(job_id):
    result_bytes = b"verified-result"
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
    return manifest_bytes, result_bytes


def test_unknown_job_reconnects_by_job_id_and_collects_without_upload_or_start():
    root = Path(tempfile.mkdtemp(prefix="ezyolo-recovery-"))
    interrupted = root / "staging" / JOB_ID
    interrupted.mkdir(parents=True)
    marker = interrupted / "partial-result"
    marker.write_text("preserve", encoding="utf-8")
    store = MemoryJobStore(_record())
    backend = RecoveryBackend()
    thread = RemoteTrainingRecoveryThread(
        profile=_profile(),
        record=store.record,
        backend=backend,
        job_store=store,
        result_stager=ResultStager(root / "staging", root / "runs" / "train"),
        result_verifier=verify_result_bundle,
        poll_interval_seconds=0,
    )
    finished = []
    thread.training_finished.connect(lambda ok, message: finished.append((ok, message)))

    thread.run()

    assert backend.calls == [
        "preflight",
        "poll",
        "collect-manifest",
        "download-results",
    ]
    assert store.record.last_status == JobStatus.SUCCEEDED
    assert Path(store.record.local_result_dir, "weights", "best.pt").is_file()
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert finished[-1][0] is True


def test_recovery_adopts_only_verified_existing_final_result_without_downloading():
    root = Path(tempfile.mkdtemp(prefix="ezyolo-recovery-adopt-"))
    stager = ResultStager(root / "staging", root / "runs" / "train")
    final_dir = stager.final_dir_for(3, JOB_ID)
    manifest_bytes, result_bytes = _result_bundle(JOB_ID)
    (final_dir / "weights").mkdir(parents=True)
    (final_dir / "weights" / "best.pt").write_bytes(result_bytes)
    (final_dir / "manifest.json").write_bytes(manifest_bytes)
    store = MemoryJobStore(_record())
    backend = RecoveryBackend()
    thread = RemoteTrainingRecoveryThread(
        profile=_profile(),
        record=store.record,
        backend=backend,
        job_store=store,
        result_stager=stager,
        result_verifier=verify_result_bundle,
        poll_interval_seconds=0,
    )

    thread.run()

    assert backend.calls == ["preflight", "poll", "collect-manifest"]
    assert store.record.last_status == JobStatus.SUCCEEDED
    assert Path(store.record.local_result_dir) == final_dir


def test_recovery_root_mismatch_stays_unknown_and_never_polls_or_starts():
    class WrongRootBackend(RecoveryBackend):
        def preflight(self, profile):
            capabilities = super().preflight(profile)
            return ServerCapabilities(
                protocol_version=capabilities.protocol_version,
                canonical_remote_root="/srv/ezyolo/other",
                supported_tasks=capabilities.supported_tasks,
                model_symbols=capabilities.model_symbols,
                max_epochs=capabilities.max_epochs,
                max_runtime_seconds=capabilities.max_runtime_seconds,
                max_payload_bytes=capabilities.max_payload_bytes,
                max_result_bytes=capabilities.max_result_bytes,
            )

    root = Path(tempfile.mkdtemp(prefix="ezyolo-recovery-root-"))
    store = MemoryJobStore(_record())
    backend = WrongRootBackend()
    thread = RemoteTrainingRecoveryThread(
        profile=_profile(),
        record=store.record,
        backend=backend,
        job_store=store,
        result_stager=ResultStager(root / "staging", root / "runs" / "train"),
        result_verifier=verify_result_bundle,
        poll_interval_seconds=0,
    )

    thread.run()

    assert backend.calls == ["preflight"]
    assert store.record.last_status == JobStatus.UNKNOWN


def test_detaching_recovery_never_cancels_or_changes_server_job_state():
    root = Path(tempfile.mkdtemp(prefix="ezyolo-recovery-detach-"))
    store = MemoryJobStore(_record(status=JobStatus.RUNNING))
    backend = RecoveryBackend()
    thread = RemoteTrainingRecoveryThread(
        profile=_profile(),
        record=store.record,
        backend=backend,
        job_store=store,
        result_stager=ResultStager(root / "staging", root / "runs" / "train"),
        result_verifier=verify_result_bundle,
        poll_interval_seconds=0,
    )
    finished = []
    thread.training_finished.connect(lambda ok, message: finished.append((ok, message)))

    thread.request_detach()
    thread.run()

    assert "cancel" not in backend.calls
    assert store.record.last_status == JobStatus.RUNNING
    assert finished[-1][0] is False
    assert "服务器任务未停止" in finished[-1][1]


def test_close_detach_completion_restores_state_without_failure_popup():
    page = TrainPage()
    page.settings.clear()
    page.settings.sync()
    page._active_training_is_remote = True
    page._active_remote_operation = "new_training"
    page._last_remote_job_record = None

    with patch.object(page, "restore_project_training_state") as restore, patch.object(
        train_page_module.QMessageBox,
        "warning",
        side_effect=AssertionError("关窗 detach 不应弹训练失败框"),
    ):
        page.on_training_finished(False, "已停止本机监控；服务器任务未停止")

    restore.assert_called_once_with()
    assert page._active_remote_operation is None


def test_recovery_lease_blocks_second_window_without_backend_or_store_changes():
    root = Path(tempfile.mkdtemp(prefix="ezyolo-recovery-lease-"))
    stager = ResultStager(root / "staging", root / "runs" / "train")
    stager.staging_parent.mkdir(parents=True)
    lease = QLockFile(str(stager.staging_parent / f".recovery-{JOB_ID}.lock"))
    assert lease.tryLock(0)
    try:
        store = MemoryJobStore(_record())
        backend = RecoveryBackend()
        thread = RemoteTrainingRecoveryThread(
            profile=_profile(),
            record=store.record,
            backend=backend,
            job_store=store,
            result_stager=stager,
            result_verifier=verify_result_bundle,
            poll_interval_seconds=0,
        )
        finished = []
        thread.training_finished.connect(
            lambda ok, message: finished.append((ok, message))
        )

        thread.run()

        assert backend.calls == []
        assert store.record.last_status == JobStatus.UNKNOWN
        assert "另一个 EzYOLO 窗口" in finished[-1][1]
    finally:
        lease.unlock()


def test_reopened_train_page_restores_verified_success_instead_of_resetting_to_start():
    project_id = _bootstrap.create_temp_project(
        name="恢复成功项目",
        project_type="detect",
        classes=["gas_cylinder"],
    )
    root = Path(tempfile.mkdtemp(prefix="ezyolo-success-"))
    best = root / "weights" / "best.pt"
    best.parent.mkdir()
    best.write_bytes(b"verified")
    page = TrainPage()
    page.settings.clear()
    page.settings.sync()
    page.remote_job_store.create(
        _record(
            project_id=project_id,
            status=JobStatus.SUCCEEDED,
            local_result_dir=root,
        )
    )
    page.show()
    with patch.object(
        train_page_module,
        "get_project_snapshot",
        side_effect=lambda selected: {
            "project_id": selected,
            "project": {"id": selected},
            "name": "恢复成功项目",
            "task_type": "detect",
            "image_count": 1,
            "annotated_count": 1,
            "class_count": 1,
            "run_dirs": [root],
            "weights": best,
        },
    ):
        page.set_project(project_id)

    assert page._last_remote_job_record.last_status == JobStatus.SUCCEEDED
    assert page.btn_goto_result.isVisible()
    assert not page.btn_start.isVisible()
    assert str(root) in page.done_label.toolTip()


def test_reopened_unknown_job_blocks_new_training_and_offers_reconnect():
    project_id = _bootstrap.create_temp_project(
        name="待核验项目",
        project_type="detect",
        classes=["gas_cylinder"],
    )
    page = TrainPage()
    page.settings.clear()
    page.settings.sync()
    page.remote_job_store.create(_record(project_id=project_id))
    page.show()
    with patch.object(
        train_page_module,
        "get_project_snapshot",
        side_effect=lambda selected: {
            "project_id": selected,
            "project": {"id": selected},
            "name": "待核验项目",
            "task_type": "detect",
            "image_count": 1,
            "annotated_count": 1,
            "class_count": 1,
            "run_dirs": [],
            "weights": None,
        },
    ):
        page.set_project(project_id)

    assert page.btn_reconnect_remote.isVisible()
    assert "重新连接" in page.btn_reconnect_remote.text()
    assert not page.btn_start.isVisible()
    assert "未知" in page.status_label.text()


def test_older_unresolved_job_remains_visible_and_blocks_training_with_newer_success():
    project_id = _bootstrap.create_temp_project(
        name="已有模型且待核验",
        project_type="detect",
        classes=["gas_cylinder"],
    )
    root = Path(tempfile.mkdtemp(prefix="ezyolo-success-with-unresolved-"))
    best = root / "weights" / "best.pt"
    best.parent.mkdir()
    best.write_bytes(b"verified")
    page = TrainPage()
    page.settings.clear()
    page.settings.sync()
    page.remote_job_store.create(
        _record(
            project_id=project_id,
            status=JobStatus.UNKNOWN,
            job_id="1" * 32,
            updated_at="2026-07-28T04:00:00+00:00",
        )
    )
    page.remote_job_store.create(
        _record(
            project_id=project_id,
            status=JobStatus.SUCCEEDED,
            local_result_dir=root,
            job_id="2" * 32,
            updated_at="2026-07-28T05:00:00+00:00",
        )
    )
    page.show()
    snapshot = {
        "project_id": project_id,
        "project": {"id": project_id},
        "name": "已有模型且待核验",
        "task_type": "detect",
        "image_count": 1,
        "annotated_count": 1,
        "class_count": 1,
        "run_dirs": [root],
        "weights": best,
    }
    with patch.object(train_page_module, "get_project_snapshot", return_value=snapshot):
        page.set_project(project_id)

        assert page.btn_goto_result.isVisible()
        assert page.btn_reconnect_remote.isVisible()
        assert not page.btn_train_again.isVisible()
        assert not page.btn_start.isVisible()
        assert "另有 1 个远程任务" in page.status_label.text()

        warnings = []
        with patch.object(
            train_page_module.QMessageBox,
            "warning",
            side_effect=lambda *args: warnings.append(args),
        ), patch.object(
            page,
            "maybe_confirm_template_before_training",
            side_effect=AssertionError("不应进入新训练配置"),
        ):
            page.start_training()

    assert warnings[-1][1] == "先核验已有远程任务"


def test_corrupted_remote_job_store_hides_start_and_fails_closed_without_rewriting():
    project_id = _bootstrap.create_temp_project(
        name="任务记录损坏",
        project_type="detect",
        classes=["gas_cylinder"],
    )
    page = TrainPage()
    page.settings.clear()
    raw = '{"broken":true}'
    page.settings.setValue(REMOTE_TRAINING_JOBS_KEY, raw)
    page.settings.sync()
    page.show()
    snapshot = {
        "project_id": project_id,
        "project": {"id": project_id},
        "name": "任务记录损坏",
        "task_type": "detect",
        "image_count": 1,
        "annotated_count": 1,
        "class_count": 1,
        "run_dirs": [],
        "weights": None,
    }
    with patch.object(train_page_module, "get_project_snapshot", return_value=snapshot):
        page.set_project(project_id)
        assert not page.btn_start.isVisible()
        assert not page.btn_start.isEnabled()
        assert "已阻止新训练" in page.status_label.text()

        warnings = []
        with patch.object(
            train_page_module.QMessageBox,
            "warning",
            side_effect=lambda *args: warnings.append(args),
        ):
            page.start_training()

    assert warnings[-1][1] == "无法开始新训练"
    assert page.settings.value(REMOTE_TRAINING_JOBS_KEY) == raw


def test_existing_model_stays_available_while_latest_attempt_is_shown_as_failed():
    project_id = _bootstrap.create_temp_project(
        name="旧模型可用且最近失败",
        project_type="detect",
        classes=["gas_cylinder"],
    )
    root = Path(tempfile.mkdtemp(prefix="ezyolo-model-with-failed-attempt-"))
    best = root / "weights" / "best.pt"
    best.parent.mkdir()
    best.write_bytes(b"verified")
    page = TrainPage()
    page.settings.clear()
    page.settings.sync()
    page.remote_job_store.create(
        _record(
            project_id=project_id,
            status=JobStatus.SUCCEEDED,
            local_result_dir=root,
            job_id="3" * 32,
            updated_at="2026-07-28T04:00:00+00:00",
        )
    )
    failed_payload = _record(
        project_id=project_id,
        job_id="4" * 32,
        updated_at="2026-07-28T05:00:00+00:00",
    ).to_dict()
    failed_payload["last_status"] = JobStatus.FAILED.value
    failed_payload["failure_code"] = "REMOTE_CRASHED"
    page.remote_job_store.create(RemoteTrainingJobRecord.from_dict(failed_payload))
    page.show()
    snapshot = {
        "project_id": project_id,
        "project": {"id": project_id},
        "name": "旧模型可用且最近失败",
        "task_type": "detect",
        "image_count": 1,
        "annotated_count": 1,
        "class_count": 1,
        "run_dirs": [root],
        "weights": best,
    }
    with patch.object(train_page_module, "get_project_snapshot", return_value=snapshot):
        page.set_project(project_id)

    assert page.btn_goto_result.isVisible()
    assert not page.btn_start.isVisible()
    assert "已有模型可用" in page.status_label.text()
    assert "最近一次训练尝试失败" in page.status_label.text()


def test_unknown_completion_remains_unknown_and_does_not_claim_complete_server_log():
    project_id = _bootstrap.create_temp_project(
        name="断线项目",
        project_type="detect",
        classes=["gas_cylinder"],
    )
    page = TrainPage()
    page.settings.clear()
    page.settings.sync()
    page.remote_job_store.create(_record(project_id=project_id))
    page.show()
    snapshot = {
        "project_id": project_id,
        "project": {"id": project_id},
        "name": "断线项目",
        "task_type": "detect",
        "image_count": 1,
        "annotated_count": 1,
        "class_count": 1,
        "run_dirs": [],
        "weights": None,
    }
    with patch.object(train_page_module, "get_project_snapshot", return_value=snapshot):
        page.set_project(project_id)
        page._active_training_is_remote = True
        warnings = []
        with patch.object(
            train_page_module.QMessageBox,
            "warning",
            side_effect=lambda *args: warnings.append(args),
        ):
            page.on_training_finished(False, "远程连接中断；任务状态未知")

    assert page._last_remote_job_record.last_status == JobStatus.UNKNOWN
    assert page.btn_reconnect_remote.isVisible()
    assert "未知" in page.status_label.text()
    assert warnings[-1][1] == "远程状态仍待核验"
    assert "有完整报错" not in warnings[-1][2]


if __name__ == "__main__":
    raise SystemExit(_bootstrap.run_module_tests(globals()))
