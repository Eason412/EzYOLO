# -*- coding: utf-8 -*-
"""图片显示名：数据库里存什么还是什么，这里只决定「界面上怎么念它」。

视频抽帧落库的文件名长这样：

    20260712_000602_575947_frame_000223.jpg

时间戳是为了不重名，帧号才是人要看的东西。一屏几十行全是这种名字时，
前 22 个字符完全一样，用户得横着扫到第 23 位才能分辨两张图——列表再宽也没用。
所以列表里显示「帧 223」，完整文件名留在 tooltip 里。

普通图片导入时 filename 存的就是用户自己的文件名（时间戳只加在磁盘副本上），
本来就可读——原样返回，让界面自己去打省略号，不要替用户改写文件名。

只做显示：不改文件名，不动数据库。
"""

import re
from collections import defaultdict
from typing import Dict, List, Optional, Sequence

# 抽帧文件名：<日期>_<时间>_<微秒>_frame_<帧号>.<扩展名>
# 由 core.import_manager.import_video 生成，格式变了这里会认不出来，
# 认不出来就原样显示——退化成「难看但正确」，不会显示成错的帧号。
_FRAME_NAME = re.compile(
    r"^(?P<date>\d{8})_(?P<time>\d{6})_(?P<micro>\d{6})_frame_(?P<frame>\d+)\.[^.]+$"
)


def parse_frame_name(filename: str) -> Optional[Dict[str, str]]:
    """认出抽帧生成的文件名，拆出日期 / 时间 / 帧号；不是这种名字就返回 None。"""
    match = _FRAME_NAME.match((filename or "").strip())
    return match.groupdict() if match else None


def display_name(filename: str) -> str:
    """一个文件名在界面上显示成什么。

    抽帧名 → 「帧 223」（前导零去掉；全是零就是「帧 0」，别显示成空的）。
    其余一律原样返回。
    """
    parsed = parse_frame_name(filename)
    if not parsed:
        return filename or ""
    return f"帧 {int(parsed['frame'])}"


def _discriminator(filename: str, level: str) -> Optional[str]:
    """重名时用来区分的那一小截，取自文件名里的生成时间。"""
    parsed = parse_frame_name(filename)
    if not parsed:
        return None

    if level == "date":
        date = parsed["date"]
        return f"{date[4:6]}-{date[6:8]}"          # 20260712 → 07-12

    time = parsed["time"]
    return f"{time[0:2]}:{time[2:4]}:{time[4:6]}"  # 000602 → 00:06:02


def display_names(filenames: Sequence[str]) -> List[str]:
    """一整个列表的显示名：重名的补一个最短的区分信息。

    同一段视频里帧号不会重复，重名只发生在「两段视频都抽到了第 223 帧」。
    这时先用抽帧日期分（多半就够了），同一天的用时间分，同一秒的才退到序号。
    """
    aliases = [display_name(name) for name in filenames]

    groups: Dict[str, List[int]] = defaultdict(list)
    for index, alias in enumerate(aliases):
        groups[alias].append(index)

    for alias, indexes in groups.items():
        if len(indexes) < 2:
            continue

        for level in ("date", "time"):
            marks = [_discriminator(filenames[i], level) for i in indexes]
            # 每个都拿得到、且两两不同，这一档才真的能把它们分开
            if all(marks) and len(set(marks)) == len(indexes):
                for index, mark in zip(indexes, marks):
                    aliases[index] = f"{alias} · {mark}"
                break
        else:
            # 时间也分不开（同一秒抽的，或者压根不是抽帧名）：给一个稳定的序号
            for ordinal, index in enumerate(indexes, start=1):
                aliases[index] = f"{alias} ({ordinal})"

    return aliases
