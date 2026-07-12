# -*- coding: utf-8 -*-
"""抽帧设置对话框

替掉 QInputDialog.getInt：那个框只问「间隔多少帧」，可用户想知道的是
「我最后能得到几张图」——一段 30fps 的视频填 30，到底是 20 张还是 200 张？
原来的框既不说总帧数，也不说预计张数，只能靠猜。

这里在开抽之前先读一遍视频元信息（只读属性，不解码），然后：
    固定间隔  每 N 帧取 1 张 —— 边填边算「预计 X 张」，并按 fps 折算成时间间隔
    随机抽取  随机取 N 张    —— 直接要张数，按视频时长给一句人话建议

总帧数读不到时（有些流式录制的视频就是不写这个值），固定间隔照常能用
（只是算不出预计张数），随机抽取则整个禁掉：不知道有多少帧，就没法从里面随机取 N 张。
"""

import math
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QSpinBox, QWidget, QFrame,
)

from core.import_manager import (
    VIDEO_MODE_INTERVAL, VIDEO_MODE_RANDOM,
    probe_video_metadata, estimate_interval_frame_count,
)
from gui.styles import COLORS, RADIUS_SM

# 间隔上限跟原来的 QInputDialog 保持一致
MAX_INTERVAL = 1000

# 够放下「每 [30] 帧取 1 张   → 预计 10 张，约每 1.0 秒 1 张」一整行，
# 又不至于让下面那段说明稀稀拉拉铺成一整片
DIALOG_WIDTH = 480
_DIALOG_MARGIN = 24          # 对话框左右边距
_RESULT_PADDING = 12         # 说明块自己的左右内边距

# 会换行的文字实际能用多宽。Qt 不会自己把「换行之后要多高」算进对话框的高度——
# QLabel 报的 sizeHint 是按不换行算的，于是框子按一行的高度开出来，
# 三行字就画到按钮上去了。所以这里按真实宽度把高度算出来，显式钉给标签。
TEXT_WIDTH = DIALOG_WIDTH - 2 * _DIALOG_MARGIN
RESULT_TEXT_WIDTH = TEXT_WIDTH - 2 * _RESULT_PADDING


def _fit_wrapped_height(label: QLabel, width: int):
    """按给定宽度把换行后的高度算出来，钉成标签的最小高度。"""
    label.setMinimumHeight(label.heightForWidth(width))


def format_duration(seconds: Optional[float]) -> str:
    """把秒数说成人话：72.5 → 「1 分 12 秒」。"""
    if not seconds or seconds <= 0:
        return ""
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


class VideoExtractDialog(QDialog):
    """抽帧设置：选方式 → 填数 → 看预计张数。"""

    def __init__(self, parent, metadata: dict, filename: str = ""):
        super().__init__(parent)

        self.setWindowTitle("抽帧设置")
        self.setFixedWidth(DIALOG_WIDTH)

        self.total_frames = int(metadata.get('total_frames') or 0)
        self.fps = float(metadata.get('fps') or 0.0)
        self.duration = metadata.get('duration')
        self.knows_total = self.total_frames > 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(_DIALOG_MARGIN, 20, _DIALOG_MARGIN, 18)
        layout.setSpacing(10)

        heading = QLabel("从视频里抽帧")
        heading.setObjectName("h2")
        layout.addWidget(heading)

        self.lbl_meta = QLabel(self._metadata_text(filename))
        self.lbl_meta.setObjectName("subtitle")
        self.lbl_meta.setWordWrap(True)
        # 文件名可以很长，这一行本来就可能折成两行——高度得先算出来
        _fit_wrapped_height(self.lbl_meta, TEXT_WIDTH)
        layout.addWidget(self.lbl_meta)

        # --- 方式一：固定间隔 ---
        self.rbtn_interval = QRadioButton("固定间隔：每 N 帧取 1 张")
        self.rbtn_interval.setChecked(True)
        self.rbtn_interval.toggled.connect(self._on_mode_changed)
        layout.addWidget(self.rbtn_interval)

        # 预计张数就跟在输入框后面：用户改的是这个数，想知道的结果也在这一行，
        # 眼睛不用往下跑。右边那片空白本来什么都不放，现在正好给它。
        self.interval_row = QWidget()
        interval_layout = QHBoxLayout(self.interval_row)
        interval_layout.setContentsMargins(24, 0, 0, 0)
        interval_layout.setSpacing(8)

        interval_layout.addWidget(QLabel("每"))
        self.sb_interval = QSpinBox()
        self.sb_interval.setRange(1, MAX_INTERVAL)
        self.sb_interval.setValue(30)
        self.sb_interval.setFixedWidth(88)
        self.sb_interval.valueChanged.connect(self._refresh_estimate)
        interval_layout.addWidget(self.sb_interval)
        interval_layout.addWidget(QLabel("帧取 1 张"))

        self.lbl_interval_estimate = QLabel()
        self.lbl_interval_estimate.setObjectName("caption")
        interval_layout.addWidget(self.lbl_interval_estimate, 1)

        layout.addWidget(self.interval_row)

        # --- 方式二：随机抽取 ---
        self.rbtn_random = QRadioButton("随机抽取：随机取 N 张")
        self.rbtn_random.toggled.connect(self._on_mode_changed)
        layout.addWidget(self.rbtn_random)

        self.random_row = QWidget()
        random_layout = QHBoxLayout(self.random_row)
        random_layout.setContentsMargins(24, 0, 0, 0)
        random_layout.setSpacing(8)

        random_layout.addWidget(QLabel("随机取"))
        self.sb_random_count = QSpinBox()
        self.sb_random_count.setRange(1, max(self.total_frames, 1))
        self.sb_random_count.setValue(self._recommended_count())
        self.sb_random_count.setFixedWidth(88)
        self.sb_random_count.valueChanged.connect(self._refresh_estimate)
        random_layout.addWidget(self.sb_random_count)
        random_layout.addWidget(QLabel("张"))

        self.lbl_random_estimate = QLabel()
        self.lbl_random_estimate.setObjectName("caption")
        random_layout.addWidget(self.lbl_random_estimate, 1)

        layout.addWidget(self.random_row)

        # 总帧数未知：随机抽取没法用，说清楚为什么，而不是让它看着能点却报错
        if not self.knows_total:
            self.rbtn_random.setEnabled(False)
            self.sb_random_count.setEnabled(False)
            self.rbtn_random.setToolTip("读不到视频总帧数，无法随机取固定张数")

        # --- 说明块：只在真有话说的时候出现 ---
        # 固定间隔 + 总帧数已知时，该说的话上面那行已经说完了，这里再重复一遍
        # 「预计 10 张」只是把框撑高。所以它平时是收起来的，只有随机模式要给建议、
        # 或者总帧数读不到要解释一句时才露面。
        # 底色比对话框画布再深一点点。用 background 的话跟画布同色，
        # 这个「块」就只是一段浮在那儿的灰字，根本不成块。
        self.result_box = QFrame()
        self.result_box.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['hover']};
                border: none;
                border-radius: {RADIUS_SM}px;
            }}
        """)
        result_layout = QVBoxLayout(self.result_box)
        result_layout.setContentsMargins(_RESULT_PADDING, 10, _RESULT_PADDING, 10)
        result_layout.setSpacing(0)

        self.lbl_estimate = QLabel()
        self.lbl_estimate.setObjectName("caption")
        self.lbl_estimate.setWordWrap(True)
        result_layout.addWidget(self.lbl_estimate)

        layout.addWidget(self.result_box)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 10, 0, 0)
        button_row.setSpacing(8)
        button_row.addStretch()

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        button_row.addWidget(self.btn_cancel)

        self.btn_confirm = QPushButton("开始抽帧")
        self.btn_confirm.setObjectName("primary")
        self.btn_confirm.setDefault(True)
        self.btn_confirm.clicked.connect(self.accept)
        button_row.addWidget(self.btn_confirm)

        layout.addLayout(button_row)

        self._on_mode_changed()

    # ==================== 文案 ====================

    def _metadata_text(self, filename: str) -> str:
        """抬头那行：这段视频是什么样的。读不到就直说读不到。"""
        name = filename or "视频"
        if not self.knows_total:
            return f"{name}：读不到总帧数（有些视频不写这个信息），只能按固定间隔抽。"

        parts = [f"共 {self.total_frames} 帧"]
        if self.fps:
            parts.append(f"{self.fps:.0f} fps")
        length = format_duration(self.duration)
        if length:
            parts.append(f"时长约 {length}")
        return f"{name}：" + "，".join(parts) + "。"

    def _recommended_count(self) -> int:
        """随机张数的默认值：按「每秒 1 张」估，没有时长信息就给 20 张。"""
        if self.duration and self.duration > 0:
            recommended = int(math.ceil(self.duration))
        else:
            recommended = 20
        upper = self.total_frames if self.knows_total else recommended
        return max(1, min(recommended, max(upper, 1)))

    def _random_advice(self) -> str:
        """随机模式下给一句建议，用时长说话，不让用户自己换算帧。"""
        length = format_duration(self.duration)
        if not length:
            return "建议先少抽一些看看效果，不够再补。"

        per_second = int(math.ceil(self.duration))
        sparse = max(1, per_second // 2)
        return (
            f"这段视频约 {length}：想每秒大约 1 张，填 {per_second} 张；"
            f"画面变化不大的话，{sparse} 张通常也够用。"
        )

    def _set_result(self, text: str):
        """说明块：没话说就整块收起来，不留一条空白的灰条。"""
        self.lbl_estimate.setText(text)
        self.result_box.setVisible(bool(text))
        if text:
            _fit_wrapped_height(self.lbl_estimate, RESULT_TEXT_WIDTH)

        # 已经摆出来之后再换模式（一行的说明变成三行，或者整块收起）：
        # 高度要重新收敛到当前内容。宽度是钉死的，adjustSize 只会动高度。
        if self.isVisible():
            self.adjustSize()

    def _refresh_estimate(self):
        """预计张数 / 建议，跟着输入实时更新。

        当前这一行给「几张」（短、就在输入框旁边），
        下面的说明块只在还有别的话要说时才出现。
        """
        if self.rbtn_random.isChecked():
            count = self.sb_random_count.value()
            self.lbl_interval_estimate.setText("")
            self.lbl_random_estimate.setText(f"→ 预计 {count} 张")
            # 随机模式下「抽几张」全靠用户拍脑袋，这里必须给个参照
            self._set_result(
                f"将随机抽取 {count} 张（帧号不重复，按先后顺序导入）。{self._random_advice()}"
            )
            return

        self.lbl_random_estimate.setText("")

        interval = self.sb_interval.value()
        estimated = estimate_interval_frame_count(self.total_frames, interval)

        if estimated is None:
            self.lbl_interval_estimate.setText("→ 张数未知")
            self._set_result(
                f"每 {interval} 帧取 1 张。总帧数未知，抽完才知道是几张。"
            )
            return

        text = f"→ 预计 {estimated} 张"
        if self.fps:
            spacing = interval / self.fps
            text += f"，约每 {spacing:.1f} 秒 1 张"
        self.lbl_interval_estimate.setText(text)
        # 张数和时间间隔上面那行都写了，这里没有新东西可说
        self._set_result("")

    def _on_mode_changed(self, *_args):
        random_mode = self.rbtn_random.isChecked()
        self.interval_row.setEnabled(not random_mode)
        self.random_row.setEnabled(random_mode and self.knows_total)
        self._refresh_estimate()

    # ==================== 结果 ====================

    def get_plan(self) -> dict:
        """用户选的抽帧方案，直接喂给 ImportManager.import_video。"""
        if self.rbtn_random.isChecked():
            return {
                'mode': VIDEO_MODE_RANDOM,
                'sample_count': self.sb_random_count.value(),
                'frame_interval': 1,
            }
        return {
            'mode': VIDEO_MODE_INTERVAL,
            'frame_interval': self.sb_interval.value(),
            'sample_count': None,
        }


def ask_video_extract_plan(parent, video_path: str) -> Optional[dict]:
    """读元信息 → 弹抽帧设置框。用户取消返回 None。

    元信息读不出来（文件损坏、格式不认）时抛 ValueError，由调用方报错。
    """
    metadata = probe_video_metadata(video_path)
    dialog = VideoExtractDialog(parent, metadata, filename=Path(video_path).name)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.get_plan()
