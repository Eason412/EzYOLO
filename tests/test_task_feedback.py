# -*- coding: utf-8 -*-
"""任务反馈回归测试：耗时任务不能冻界面、取消要立刻生效、密钥不能泄露。

覆盖两条最近改过的路径：
    LLMBatchWorker（gui/pages/annotate_page.py）——网络请求搬到后台线程
    TestPage.stop_inference（gui/pages/test_page.py）——停止不再 wait() 界面线程

llm_detect 全程用 monkeypatch 换掉：不发真实请求、不读真实配置、不需要 API Key。
QMessageBox 一律替换成不弹窗的桩，任何模态都会让离屏测试挂死。

运行：
    python -m pytest tests/test_task_feedback.py -q
    或
    python tests/test_task_feedback.py
"""

import _bootstrap  # noqa: F401  必须第一个导入

import sys  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
from pathlib import Path  # noqa: E402

from PyQt6.QtCore import Qt, QThread  # noqa: E402

from gui.pages import annotate_page, test_page  # noqa: E402
from gui.pages.annotate_page import LLMBatchWorker  # noqa: E402

_app = _bootstrap.app()

FAKE_CONFIG = {
    'api_key': 'TEST_SECRET_MUST_NOT_LEAK_0123456789',
    'base_url': 'http://127.0.0.1:0/never-called',
    'model_name': 'fake-model',
    'system_prompt': 'sys',
    'user_prompt': '{target}',
}

_TMP = Path(tempfile.mkdtemp(prefix="ezyolo-taskfeedback-"))


class _NoModalMessageBox:
    """QMessageBox 的桩：不弹窗，直接返回。测试里任何模态都会挂死。"""

    class StandardButton:
        Yes = 1
        No = 0
        Ok = 1
        Cancel = 0

    calls = []

    @classmethod
    def information(cls, *args, **kwargs):
        cls.calls.append(('information', args[2] if len(args) > 2 else ''))
        return cls.StandardButton.Ok

    warning = critical = information

    @classmethod
    def question(cls, *args, **kwargs):
        cls.calls.append(('question', args[2] if len(args) > 2 else ''))
        return cls.StandardButton.Yes


def _make_image(name: str) -> str:
    """造一张真实存在的临时文件——worker 只做 os.path.exists 检查。"""
    path = _TMP / name
    path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    return str(path)


def _images(n: int):
    return [
        {'id': 100 + i, 'filename': f"img{i}.jpg", 'storage_path': _make_image(f"img{i}.jpg")}
        for i in range(n)
    ]


class _Patch:
    """临时替换模块属性，退出时还原。"""

    def __init__(self, module, **attrs):
        self.module = module
        self.attrs = attrs
        self.saved = {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.saved[k] = getattr(self.module, k)
            setattr(self.module, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(self.module, k, v)
        return False


def _run_worker(worker: LLMBatchWorker, on_started=None, timeout_ms=5000):
    """跑一个 worker 到线程真正退出为止，返回 (image_done 记录, batch_finished 记录)。

    信号用 DirectConnection：槽在 worker 线程里执行，不需要界面事件循环。
    收集只是往 list 里 append，够安全。
    """
    done = []
    finished = []
    worker.image_done.connect(
        lambda image_id, dets, err: done.append((image_id, dets, err)),
        Qt.ConnectionType.DirectConnection,
    )
    worker.batch_finished.connect(
        lambda ok, bad, cancelled: finished.append((ok, bad, cancelled)),
        Qt.ConnectionType.DirectConnection,
    )
    if on_started is not None:
        worker.image_started.connect(on_started, Qt.ConnectionType.DirectConnection)

    worker.start()
    assert worker.wait(timeout_ms), "worker 线程没有在超时内退出"
    assert not worker.isRunning()
    return done, finished


# --- 1) llm_detect 必须在后台线程里被调用 ---

def test_llm_detect_runs_off_the_gui_thread():
    main_qthread = QThread.currentThread()
    main_pythread = threading.current_thread().ident
    seen = []

    def fake_llm_detect(config, image_path, target):
        seen.append((QThread.currentThread(), threading.current_thread().ident))
        return [{'label': target, 'bbox': [1, 2, 3, 4]}]

    with _Patch(annotate_page, llm_detect=fake_llm_detect, QMessageBox=_NoModalMessageBox):
        worker = LLMBatchWorker(FAKE_CONFIG, _images(2), "cat")
        done, finished = _run_worker(worker)

    assert len(seen) == 2, f"llm_detect 应被调用 2 次，实际 {len(seen)}"
    for qthread, ident in seen:
        assert qthread is not main_qthread, "llm_detect 跑在了界面线程上"
        assert qthread is worker, "llm_detect 应该跑在 LLMBatchWorker 这个 QThread 里"
        assert ident != main_pythread

    assert [d[2] for d in done] == ["", ""]
    assert done[0][1] == [{'label': 'cat', 'bbox': [1, 2, 3, 4]}]
    assert finished == [(2, 0, False)]


# --- 2) 取消后最多做完当前这张，不再开下一张 ---

def test_cancel_stops_after_current_image():
    started = threading.Event()
    release = threading.Event()
    calls = []

    def fake_llm_detect(config, image_path, target):
        calls.append(image_path)
        started.set()
        release.wait(5)  # 卡住第一张，给主线程时间发出取消
        return []

    with _Patch(annotate_page, llm_detect=fake_llm_detect, QMessageBox=_NoModalMessageBox):
        worker = LLMBatchWorker(FAKE_CONFIG, _images(4), "cat")

        done = []
        finished = []
        worker.image_done.connect(
            lambda i, d, e: done.append((i, d, e)), Qt.ConnectionType.DirectConnection
        )
        worker.batch_finished.connect(
            lambda ok, bad, c: finished.append((ok, bad, c)), Qt.ConnectionType.DirectConnection
        )

        worker.start()
        assert started.wait(5), "worker 没有开始处理第一张"
        worker.cancel()          # 界面线程只置标志，不阻塞
        release.set()            # 放行当前这张

        assert worker.wait(5000), "取消后线程没退出"

    assert not worker.isRunning()
    assert len(calls) == 1, f"取消后不该再调 llm_detect，实际调了 {len(calls)} 次"
    assert len(done) == 1, "取消后只应有当前这一张的结果"
    assert finished == [(1, 0, True)], f"batch_finished 应带 cancelled=True，实际 {finished}"


# --- 3) 异常里的 API Key 不能经 error signal 泄露 ---

def test_error_message_does_not_leak_api_key():
    key = FAKE_CONFIG['api_key']

    def fake_llm_detect(config, image_path, target):
        raise RuntimeError(f"401 Unauthorized: Bearer {key} rejected by http://x/v1")

    with _Patch(annotate_page, llm_detect=fake_llm_detect, QMessageBox=_NoModalMessageBox):
        worker = LLMBatchWorker(FAKE_CONFIG, _images(1), "cat")
        done, finished = _run_worker(worker)

    assert len(done) == 1
    reason = done[0][2]
    assert reason, "失败应该给出原因"
    assert key not in reason, "error signal 里带出了 API Key"
    assert "***" in reason
    assert "401 Unauthorized" in reason  # 抹掉密钥，但别把有用的信息也抹了
    assert finished == [(0, 1, False)]  # 成功 0 / 失败 1 / 未取消


# --- 4)(5) TestPage.stop_inference：正在跑的线程只 stop 不 wait；非运行线程安全复位 ---

class _FakeThread:
    """假的推理线程：只记录被调了什么，绝不真起线程。"""

    def __init__(self, running: bool):
        self._running = running
        self.stop_calls = 0
        self.wait_calls = 0

    def isRunning(self):
        return self._running

    def stop(self):
        self.stop_calls += 1

    def wait(self, *args):
        self.wait_calls += 1
        return True


def _make_test_page():
    page = test_page.TestPage()
    page.data_paths = ["a.jpg", "b.jpg"]
    page.is_running = True
    page._stopping = False
    page.btn_stop_inference.setVisible(True)
    page.btn_stop_inference.setEnabled(True)
    page.btn_stop_inference.setText("停止")
    return page


def test_stop_inference_does_not_block_gui_thread():
    with _Patch(test_page, QMessageBox=_NoModalMessageBox):
        page = _make_test_page()
        thread = _FakeThread(running=True)
        page.inference_thread = thread

        page.stop_inference()

    assert thread.stop_calls == 1, "应该请求线程停止"
    assert thread.wait_calls == 0, "界面线程不得 wait() 推理线程"
    assert page._stopping is True
    assert page.btn_stop_inference.text() == "正在停止…"
    assert not page.btn_stop_inference.isEnabled()
    assert page.status_label.text() == "正在停止…"
    # 还没收尾：停止按钮仍在，等 inference_finished 回调复位
    assert page.is_running is True


def test_stop_inference_resets_when_thread_not_running():
    for thread in (_FakeThread(running=False), None):
        with _Patch(test_page, QMessageBox=_NoModalMessageBox):
            page = _make_test_page()
            page.inference_thread = thread

            page.stop_inference()

        if thread is not None:
            assert thread.stop_calls == 0, "非运行线程不该再 stop"
            assert thread.wait_calls == 0

        assert page._stopping is False
        assert page.is_running is False
        assert page.btn_stop_inference.text() == "停止"
        assert page.btn_stop_inference.isHidden()
        assert not page.btn_stop_inference.isEnabled()
        assert page.status_label.text() == "已停止"


if __name__ == "__main__":
    sys.exit(_bootstrap.run_module_tests(dict(globals())))
