"""本地训练输出命名和路径隔离测试；不加载模型、不启动训练。"""

import _bootstrap  # noqa: F401

from core.app_paths import get_runtime_paths
from gui.pages.train_page import TrainingThread


def test_each_local_training_thread_gets_a_unique_workspace_run():
    first = TrainingThread({}, project_id=7)
    second = TrainingThread({}, project_id=7)
    assert first.run_name.startswith("train/exp_7_local_")
    assert second.run_name.startswith("train/exp_7_local_")
    assert first.run_name != second.run_name
    assert get_runtime_paths().workspace.runs_root.is_absolute()


if __name__ == "__main__":
    raise SystemExit(_bootstrap.run_module_tests(globals()))
