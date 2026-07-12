# -*- coding: utf-8 -*-
"""
导入管理器
处理图像导入、视频抽帧、标注导入等功能
"""

import os
import math
import random
import shutil
import hashlib
import threading
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Callable
from datetime import datetime
import cv2
from PIL import Image
import numpy as np

from models.database import db

# 抽帧方式
VIDEO_MODE_INTERVAL = 'interval'   # 每 N 帧取 1 张
VIDEO_MODE_RANDOM = 'random'       # 随机取 N 张


def probe_video_metadata(video_path: str) -> Dict:
    """只读一下视频的元信息，不解码、不导入任何一帧。

    抽帧弹窗要先知道「这段视频有多少帧、多少 fps」才能给出预计张数和建议，
    但那时候用户还没确认导入——所以这里只开一下文件读几个属性就关掉。

    Returns:
        {'total_frames': int, 'fps': float, 'duration': float | None}
        总帧数 / fps 读不到时为 0，duration 相应为 None（不是所有容器都写了这些）。
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"无法打开视频: {video_path}")

    try:
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS))
    finally:
        cap.release()

    # 有的视频（尤其是流式录制的）这两个值是 0、负数甚至 nan，一律当「不知道」
    if total_frames <= 0:
        total_frames = 0
    if not math.isfinite(fps) or fps <= 0:
        fps = 0.0

    duration = (total_frames / fps) if (total_frames and fps) else None

    return {'total_frames': total_frames, 'fps': fps, 'duration': duration}


def estimate_interval_frame_count(total_frames: int, frame_interval: int) -> Optional[int]:
    """固定间隔能抽出多少张。总帧数未知时返回 None。

    向上取整：31 帧、间隔 30，第 0 帧和第 30 帧都会被抽到，是 2 张不是 1 张。
    """
    if total_frames <= 0 or frame_interval < 1:
        return None
    return (total_frames + frame_interval - 1) // frame_interval


def plan_random_frame_indices(total_frames: int, sample_count: int,
                              rng: Optional[random.Random] = None) -> List[int]:
    """随机挑 sample_count 个帧号，去重后按升序返回。

    升序是硬要求：抽帧是顺序解码一遍视频、路过选中的帧就存下来，
    帧号乱序的话就得来回 seek（很多容器 seek 不准），或者把整段视频缓存起来。
    要几张就是几张（不会重复抽到同一帧），要得比总帧数还多时按总帧数封顶。
    """
    if total_frames <= 0:
        raise ValueError("总帧数未知，无法随机抽取固定张数")
    if sample_count < 1:
        raise ValueError("随机抽取的张数至少是 1")

    count = min(sample_count, total_frames)
    generator = rng if rng is not None else random.Random()
    return sorted(generator.sample(range(total_frames), count))


class ImportManager:
    """导入管理器类"""
    
    # 支持的图像格式
    SUPPORTED_IMAGE_FORMATS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp', '.gif'}
    
    # 支持的视频格式
    SUPPORTED_VIDEO_FORMATS = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv'}
    
    def __init__(self, project_id: int, group_id: int = None):
        """
        初始化导入管理器
        
        Args:
            project_id: 项目ID
            group_id: 导入时默认放入的分组（None 表示未分组）
        """
        self.project_id = project_id
        self.group_id = group_id
        self.project = db.get_project(project_id)
        if not self.project:
            raise ValueError(f"项目 {project_id} 不存在")
        
        # 确保项目存储目录存在
        self.storage_path = Path(self.project['storage_path'])
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # 创建images子目录
        self.images_path = self.storage_path / "images"
        self.images_path.mkdir(exist_ok=True)
    
    def import_folder(self, folder_path: str,
                     progress_callback: Callable[[int, str], None] = None,
                     cancel_event: Optional[threading.Event] = None) -> Tuple[int, int]:
        """
        导入文件夹中的所有图像

        Args:
            folder_path: 文件夹路径
            progress_callback: 进度回调函数，参数为(进度百分比, 状态信息)；
                进度为 -1 表示总量未知/尚在准备，调用方应显示忙碌态而非具体百分比
            cancel_event: 取消信号，每张图片处理前检查一次，最迟在下一张图片边界停止

        Returns:
            (成功导入数量, 跳过数量)
        """
        folder_path = Path(folder_path)
        if not folder_path.exists() or not folder_path.is_dir():
            raise ValueError(f"无效的文件夹路径: {folder_path}")

        if progress_callback:
            progress_callback(-1, f"正在扫描文件夹: {folder_path.name}")

        # 获取所有图像文件
        image_files = []
        for ext in self.SUPPORTED_IMAGE_FORMATS:
            image_files.extend(folder_path.rglob(f"*{ext}"))
            image_files.extend(folder_path.rglob(f"*{ext.upper()}"))

        # 去重并排序
        image_files = sorted(set(image_files))

        if not image_files:
            if progress_callback:
                progress_callback(100, "文件夹中没有找到图片")
            return 0, 0

        total = len(image_files)
        imported = 0
        skipped = 0
        last_progress = 0

        if progress_callback:
            progress_callback(0, f"共 {total} 张待导入")

        for i, image_file in enumerate(image_files):
            if cancel_event is not None and cancel_event.is_set():
                break

            try:
                # 更新进度
                progress = int((i / total) * 100)
                last_progress = progress
                if progress_callback:
                    progress_callback(progress, f"正在导入: {image_file.name} ({i + 1}/{total})")

                # 导入单张图像
                result = self.import_single_image(str(image_file))
                if result:
                    imported += 1
                else:
                    skipped += 1

            except Exception as e:
                print(f"导入图像失败 {image_file}: {e}")
                skipped += 1

        # 完成进度：取消时保留最后的真实进度，不要显示成 100% 完成
        if progress_callback:
            cancelled = cancel_event is not None and cancel_event.is_set()
            status = "已取消" if cancelled else "导入完成"
            final_progress = last_progress if cancelled else 100
            progress_callback(final_progress, f"{status}: 成功 {imported}, 跳过 {skipped}")

        return imported, skipped

    def import_images(self, file_paths: List[str],
                     progress_callback: Callable[[int, str], None] = None,
                     cancel_event: Optional[threading.Event] = None) -> Tuple[int, int]:
        """
        导入多张图像

        Args:
            file_paths: 图像文件路径列表
            progress_callback: 进度回调函数
            cancel_event: 取消信号，每张图片处理前检查一次，最迟在下一张图片边界停止

        Returns:
            (成功导入数量, 跳过数量)
        """
        total = len(file_paths)
        imported = 0
        skipped = 0
        last_progress = 0

        if progress_callback:
            progress_callback(0, f"正在导入 0/{total} 张图片")

        for i, file_path in enumerate(file_paths):
            if cancel_event is not None and cancel_event.is_set():
                break

            try:
                # 更新进度
                progress = int((i / total) * 100) if total else 100
                last_progress = progress
                if progress_callback:
                    progress_callback(progress, f"正在导入: {Path(file_path).name} ({i + 1}/{total})")

                # 导入单张图像
                result = self.import_single_image(file_path)
                if result:
                    imported += 1
                else:
                    skipped += 1

            except Exception as e:
                print(f"导入图像失败 {file_path}: {e}")
                skipped += 1

        # 完成进度：取消时保留最后的真实进度，不要显示成 100% 完成
        if progress_callback:
            cancelled = cancel_event is not None and cancel_event.is_set()
            status = "已取消" if cancelled else "导入完成"
            final_progress = last_progress if cancelled else 100
            progress_callback(final_progress, f"{status}: 成功 {imported}, 跳过 {skipped}")

        return imported, skipped
    
    def import_single_image(self, file_path: str) -> bool:
        """
        导入单张图像
        
        Args:
            file_path: 图像文件路径
            
        Returns:
            是否成功导入
        """
        file_path = Path(file_path)
        
        # 检查文件是否存在
        if not file_path.exists():
            return False
        
        # 检查文件格式
        if file_path.suffix.lower() not in self.SUPPORTED_IMAGE_FORMATS:
            return False
        
        # 检查是否已存在（通过文件哈希）
        file_hash = self._calculate_file_hash(str(file_path))
        if self._check_duplicate(file_hash):
            print(f"图像已存在，跳过: {file_path.name}")
            return False
        
        try:
            # 读取图像信息
            image_info = self._get_image_info(str(file_path))
            if not image_info:
                return False
            
            # 生成目标文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            target_filename = f"{timestamp}_{file_path.name}"
            target_path = self.images_path / target_filename
            
            # 复制文件到项目目录
            shutil.copy2(str(file_path), str(target_path))
            
            # 添加到数据库
            db.add_image(
                project_id=self.project_id,
                filename=file_path.name,
                storage_path=str(target_path),
                width=image_info['width'],
                height=image_info['height'],
                size=image_info['size'],
                image_format=image_info['format'],
                original_path=str(file_path),
                group_id=self.group_id,
            )
            
            return True
            
        except Exception as e:
            print(f"导入图像失败 {file_path}: {e}")
            return False
    
    def import_video(self, video_path: str, frame_interval: int = 1,
                    progress_callback: Callable[[int, str], None] = None,
                    cancel_event: Optional[threading.Event] = None,
                    mode: str = VIDEO_MODE_INTERVAL,
                    sample_count: Optional[int] = None,
                    rng: Optional[random.Random] = None) -> Tuple[int, int]:
        """
        从视频中抽取帧导入

        两种抽法：
            interval —— 每 frame_interval 帧取 1 张（默认，老行为）
            random   —— 从整段视频里随机取 sample_count 张，帧号不重复

        随机抽取也只顺序解码一遍：先把要的帧号排好序，路过就存，
        最后一个要的帧存完就收工（后面的帧不再解码）。不 seek——
        很多容器按帧号 seek 不准，会取到隔壁的帧甚至取不到。

        Args:
            video_path: 视频文件路径
            frame_interval: 抽帧间隔（每隔多少帧抽取一帧），仅 interval 模式用
            progress_callback: 进度回调函数；进度为 -1 表示总帧数未知，
                调用方应显示忙碌态而非具体百分比
            cancel_event: 取消信号，每帧读取后检查一次，最迟在下一帧边界停止
            mode: VIDEO_MODE_INTERVAL / VIDEO_MODE_RANDOM
            sample_count: 随机模式要抽的张数
            rng: 随机源，测试里传一个定种子的 random.Random 就能复现

        Returns:
            (成功导入数量, 跳过数量)

        Raises:
            ValueError: 随机模式但视频读不到总帧数（不知道有多少帧，就没法随机取 N 张）
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise ValueError(f"视频文件不存在: {video_path}")

        if video_path.suffix.lower() not in self.SUPPORTED_VIDEO_FORMATS:
            raise ValueError(f"不支持的视频格式: {video_path.suffix}")

        if progress_callback:
            progress_callback(-1, f"正在打开视频: {video_path.name}")

        # 打开视频
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise ValueError(f"无法打开视频: {video_path}")

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames < 0:
            total_frames = 0

        # 随机模式：先把帧号定下来。定不下来（总帧数未知）就别开始，
        # 免得解码了半天才发现这活干不了。
        wanted_frames = None
        last_wanted = -1
        target_count = 0
        if mode == VIDEO_MODE_RANDOM:
            try:
                indices = plan_random_frame_indices(total_frames, sample_count or 0, rng)
            except ValueError:
                cap.release()
                raise
            wanted_frames = set(indices)
            last_wanted = indices[-1]
            target_count = len(indices)

        if progress_callback:
            if wanted_frames is not None:
                progress_callback(
                    0, f"视频信息就绪: 共 {total_frames} 帧，随机抽取 {target_count} 张"
                )
            else:
                estimated = estimate_interval_frame_count(total_frames, frame_interval)
                if estimated is not None:
                    progress_callback(
                        0,
                        f"视频信息就绪: 共 {total_frames} 帧，按固定间隔 {frame_interval} 抽取，"
                        f"预计抽取 {estimated} 张",
                    )
                else:
                    progress_callback(
                        -1, f"视频总帧数未知，按固定间隔 {frame_interval} 抽取，将持续抽帧"
                    )

        imported = 0
        skipped = 0
        frame_count = 0
        last_progress = 0
        cancelled = False

        try:
            while True:
                # 随机模式：要的帧都过完了就停，别再白解码后面的
                if wanted_frames is not None and frame_count > last_wanted:
                    break

                ret, frame = cap.read()
                if not ret:
                    break

                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break

                if wanted_frames is not None:
                    wanted = frame_count in wanted_frames
                else:
                    wanted = frame_count % frame_interval == 0

                if wanted:
                    try:
                        # 更新进度
                        if progress_callback:
                            if wanted_frames is not None:
                                progress = int((imported / target_count) * 100)
                                last_progress = progress
                                progress_callback(
                                    progress,
                                    f"正在抽取第 {frame_count} 帧"
                                    f"（随机第 {imported + 1}/{target_count} 张）",
                                )
                            elif total_frames > 0:
                                progress = int((frame_count / total_frames) * 100)
                                last_progress = progress
                                progress_callback(
                                    progress,
                                    f"正在抽取帧 {frame_count}/{total_frames}（已导入 {imported}）",
                                )
                            else:
                                progress_callback(
                                    -1, f"正在抽取帧 {frame_count}（已导入 {imported}）"
                                )

                        # 保存帧为图像
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                        target_filename = f"{timestamp}_frame_{frame_count:06d}.jpg"
                        target_path = self.images_path / target_filename

                        cv2.imwrite(str(target_path), frame)

                        # 获取图像信息
                        height, width = frame.shape[:2]
                        size = target_path.stat().st_size

                        # 添加到数据库
                        db.add_image(
                            project_id=self.project_id,
                            filename=target_filename,
                            storage_path=str(target_path),
                            width=width,
                            height=height,
                            size=size,
                            image_format='jpg',
                            original_path=str(video_path),
                            group_id=self.group_id,
                        )

                        imported += 1

                    except Exception as e:
                        print(f"保存帧失败 {frame_count}: {e}")
                        skipped += 1

                frame_count += 1

        finally:
            cap.release()

        # 完成进度：取消时保留最后的真实进度，不要显示成 100% 完成
        if progress_callback:
            if wanted_frames is not None:
                how = f"随机 {target_count} 张"
            else:
                how = f"每 {frame_interval} 帧取 1 张"
            status = "已取消" if cancelled else "视频导入完成"
            final_progress = last_progress if cancelled else 100
            progress_callback(
                final_progress, f"{status}（{how}）: 成功 {imported}, 跳过 {skipped}"
            )

        return imported, skipped
    
    def _calculate_file_hash(self, file_path: str) -> str:
        """
        计算文件哈希值（用于去重）
        
        Args:
            file_path: 文件路径
            
        Returns:
            文件哈希值
        """
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def _check_duplicate(self, file_hash: str) -> bool:
        """
        检查文件是否已存在
        
        Args:
            file_hash: 文件哈希值
            
        Returns:
            是否已存在
        """
        # TODO: 实现基于哈希的去重检查
        # 目前简单检查文件名是否已存在
        return False
    
    def _get_image_info(self, file_path: str) -> Optional[Dict]:
        """
        获取图像信息
        
        Args:
            file_path: 图像文件路径
            
        Returns:
            图像信息字典，失败返回None
        """
        try:
            # 使用PIL获取图像信息
            with Image.open(file_path) as img:
                width, height = img.size
                image_format = img.format.lower() if img.format else 'unknown'
            
            # 获取文件大小
            size = Path(file_path).stat().st_size
            
            return {
                'width': width,
                'height': height,
                'size': size,
                'format': image_format
            }
            
        except Exception as e:
            print(f"获取图像信息失败 {file_path}: {e}")
            return None
    
    def get_project_images(self) -> List[Dict]:
        """
        获取项目中的所有图像
        
        Returns:
            图像列表
        """
        return db.get_project_images(self.project_id)
    
    def delete_image(self, image_id: int) -> bool:
        """
        删除图像
        
        Args:
            image_id: 图像ID
            
        Returns:
            是否成功删除
        """
        try:
            # 获取图像信息
            images = db.get_project_images(self.project_id)
            image_info = None
            for img in images:
                if img['id'] == image_id:
                    image_info = img
                    break
            
            if not image_info:
                return False
            
            # 删除文件
            storage_path = image_info.get('storage_path')
            if storage_path and Path(storage_path).exists():
                Path(storage_path).unlink()
            
            # TODO: 从数据库删除记录
            # 需要在database.py中添加delete_image方法
            
            return True
            
        except Exception as e:
            print(f"删除图像失败 {image_id}: {e}")
            return False
