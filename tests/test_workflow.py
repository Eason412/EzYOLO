# -*- coding: utf-8 -*-
"""主流程状态机的轻量测试。

只测「事实 → 状态 / 拦截原因 / 下一步」这层纯逻辑，不需要真的起界面，
所以跑起来很快，也不会碰数据库和用户文件。

运行：
    python -m pytest tests/test_workflow.py -q
    或
    python tests/test_workflow.py
"""

import _bootstrap  # noqa: F401  必须第一个导入

import sys
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from gui import workflow as workflow_module  # noqa: E402
from gui.workflow import (  # noqa: E402
    STEP_IMPORT, STEP_ANNOTATE, STEP_TRAIN, STEP_RESULT, STEP_TEST,
    DONE, CURRENT, READY, LOCKED,
    compute_step_states, find_project_runs, find_project_weights,
    get_blocker, get_next_action, step_status_text,
)

_app = _bootstrap.app()


def snapshot(**kwargs):
    base = {
        'project_id': 1,
        'image_count': 0,
        'annotated_count': 0,
        'class_count': 0,
        'run_dirs': [],
        'weights': None,
    }
    base.update(kwargs)
    return base


def test_no_project_locks_everything_after_import():
    snap = snapshot(project_id=None)
    states = compute_step_states(snap)

    assert states[STEP_IMPORT] == CURRENT
    for index in (STEP_ANNOTATE, STEP_TRAIN, STEP_RESULT, STEP_TEST):
        assert states[index] == LOCKED

    blocker = get_blocker(STEP_ANNOTATE, snap)
    assert blocker is not None
    assert blocker['action_index'] == STEP_IMPORT


def test_empty_project_blocks_annotate_with_reason():
    snap = snapshot(image_count=0)

    assert get_blocker(STEP_IMPORT, snap) is None

    blocker = get_blocker(STEP_ANNOTATE, snap)
    assert blocker['action_index'] == STEP_IMPORT
    assert '图片' in blocker['title']


def test_images_without_annotation_block_training():
    snap = snapshot(image_count=10, annotated_count=0)
    states = compute_step_states(snap)

    assert states[STEP_IMPORT] == DONE
    assert states[STEP_ANNOTATE] == CURRENT
    assert states[STEP_TRAIN] == LOCKED

    assert get_blocker(STEP_ANNOTATE, snap) is None

    blocker = get_blocker(STEP_TRAIN, snap)
    assert blocker['action_index'] == STEP_ANNOTATE


def test_partial_annotation_unlocks_training():
    snap = snapshot(image_count=10, annotated_count=4)
    states = compute_step_states(snap)

    assert states[STEP_ANNOTATE] == CURRENT
    assert states[STEP_TRAIN] == CURRENT
    assert get_blocker(STEP_TRAIN, snap) is None

    assert step_status_text(STEP_ANNOTATE, snap, states) == "已标注 4/10"


def test_all_annotated_marks_annotate_done():
    snap = snapshot(image_count=6, annotated_count=6)
    states = compute_step_states(snap)
    assert states[STEP_ANNOTATE] == DONE


def test_result_and_test_need_training_output():
    snap = snapshot(image_count=6, annotated_count=6)
    states = compute_step_states(snap)

    assert states[STEP_RESULT] == LOCKED
    assert states[STEP_TEST] == LOCKED
    assert get_blocker(STEP_RESULT, snap)['action_index'] == STEP_TRAIN

    test_blocker = get_blocker(STEP_TEST, snap)
    assert test_blocker['action_index'] == STEP_TRAIN
    # 已经有现成权重的用户可以跳过训练直接测试
    assert test_blocker.get('bypass_text')


def test_trained_project_unlocks_result_and_test():
    snap = snapshot(
        image_count=6, annotated_count=6,
        run_dirs=[Path('runs/train/exp_1')],
        weights=Path('runs/train/exp_1/weights/best.pt'),
    )
    states = compute_step_states(snap)

    assert states[STEP_TRAIN] == DONE
    assert states[STEP_RESULT] == READY
    assert states[STEP_TEST] == READY
    assert get_blocker(STEP_RESULT, snap) is None
    assert get_blocker(STEP_TEST, snap) is None


def test_next_action_walks_the_flow():
    def next_index(snap, current):
        action = get_next_action(snap, compute_step_states(snap), current)
        return action['index'] if action else None

    # 空项目 → 先导入
    assert next_index(snapshot(), STEP_ANNOTATE) == STEP_IMPORT
    # 有图没标 → 先标注
    assert next_index(snapshot(image_count=5), STEP_IMPORT) == STEP_ANNOTATE
    # 标了一半，站在导入页 → 回去把标注做完
    assert next_index(snapshot(image_count=5, annotated_count=2), STEP_IMPORT) == STEP_ANNOTATE
    # 标了一半，正站在标注页 → 可以去训练了
    assert next_index(snapshot(image_count=5, annotated_count=2), STEP_ANNOTATE) == STEP_TRAIN
    # 站在训练页但还没训练过 → 动作是页面里的「开始训练」，页头不再催
    assert next_index(snapshot(image_count=5, annotated_count=5), STEP_TRAIN) is None
    # 训练完站在训练页 → 去看结果
    trained = snapshot(image_count=5, annotated_count=5,
                       run_dirs=[Path('runs/train/exp_1')],
                       weights=Path('runs/train/exp_1/weights/best.pt'))
    assert next_index(trained, STEP_TRAIN) == STEP_RESULT
    # 看完结果 → 去测试
    assert next_index(trained, STEP_RESULT) == STEP_TEST
    # 已经在最后一步 → 不再催下一步
    assert next_index(trained, STEP_TEST) is None


def test_settings_and_about_are_never_blocked():
    from gui.workflow import PAGE_SETTINGS, PAGE_ABOUT

    snap = snapshot(project_id=None)
    assert get_blocker(PAGE_SETTINGS, snap) is None
    assert get_blocker(PAGE_ABOUT, snap) is None


def test_run_discovery_respects_exact_project_id_boundary():
    root = Path(tempfile.mkdtemp(prefix="ezyolo-run-boundary-"))
    own = root / "runs" / "train" / "exp_1_remote_aaaaaaaa" / "weights"
    other = root / "runs" / "train" / "exp_10_remote_bbbbbbbb" / "weights"
    own.mkdir(parents=True)
    other.mkdir(parents=True)

    with patch.object(workflow_module, "APP_ROOT", root):
        assert find_project_runs(1) == [own.parent]


def test_latest_weights_use_filesystem_recency_not_random_job_id_sorting():
    root = Path(tempfile.mkdtemp(prefix="ezyolo-run-recency-"))
    older = root / "runs" / "train" / "exp_1_remote_ffffffff" / "weights"
    newer = root / "runs" / "train" / "exp_1_remote_00000000" / "weights"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    older_best = older / "best.pt"
    newer_best = newer / "best.pt"
    older_best.write_bytes(b"older")
    newer_best.write_bytes(b"newer")
    os.utime(older_best, (10, 10))
    os.utime(newer_best, (20, 20))

    with patch.object(workflow_module, "APP_ROOT", root):
        assert find_project_runs(1) == [older.parent, newer.parent]
        assert find_project_weights(1) == newer_best


def test_new_workspace_and_legacy_runs_are_both_read_without_copying():
    root = Path(tempfile.mkdtemp(prefix="ezyolo-run-compat-"))
    workspace = root / "workspace"
    resources = root / "source"
    legacy = resources / "runs" / "train" / "exp_1_remote_legacy" / "weights"
    current = workspace / "runs" / "train" / "exp_1_remote_current" / "weights"
    legacy.mkdir(parents=True)
    current.mkdir(parents=True)
    (legacy / "best.pt").write_bytes(b"legacy")
    (current / "best.pt").write_bytes(b"current")

    fake_runtime = SimpleNamespace(
        workspace=SimpleNamespace(runs_root=workspace / "runs")
    )
    with (
        patch.object(workflow_module, "APP_ROOT", resources),
        patch.object(workflow_module, "_RESOURCE_ROOT", resources),
        patch.object(workflow_module, "get_runtime_paths", return_value=fake_runtime),
    ):
        assert set(find_project_runs(1)) == {legacy.parent, current.parent}


def test_result_and_test_pages_share_same_available_model_when_newer_run_is_incomplete():
    from gui.pages.result_page import ResultPage
    from gui.pages.test_page import TestPage

    root = Path(tempfile.mkdtemp(prefix="ezyolo-page-model-selection-"))
    available = root / "runs" / "train" / "exp_1_remote_aaaaaaaa" / "weights"
    incomplete = root / "runs" / "train" / "exp_1_remote_zzzzzzzz" / "weights"
    available.mkdir(parents=True)
    incomplete.mkdir(parents=True)
    best = available / "best.pt"
    best.write_bytes(b"available")
    os.utime(best, (10, 10))
    os.utime(incomplete.parent, (20, 20))

    with patch.object(workflow_module, "APP_ROOT", root):
        result_page = ResultPage()
        result_page.current_project_id = 1
        result_page.scan_runs_directory()
        test_page = TestPage()
        test_page.current_project_id = 1
        test_page.set_default_model_path()

    assert result_page.current_run == available.parent
    assert Path(test_page.model_path) == best


if __name__ == "__main__":
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print(f"\n{'all passed' if not failures else f'{failures} failed'}")
    sys.exit(1 if failures else 0)
