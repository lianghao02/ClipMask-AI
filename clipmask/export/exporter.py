"""
ClipMask-AI Export Engine
1. FastCopyExporter: FFmpeg -c copy 秒級無損快速剪輯
2. RenderExporter: Single-pass Python Pipeline (PyAV 解碼 -> TrackEvaluator 遮蔽 -> OpenCV/FFmpeg 壓制)
"""
import subprocess
import os
import cv2
import numpy as np
from typing import Callable, Optional
from ..models.project import ProjectState
from ..media.source import VideoSource
from ..track.evaluator import TrackEvaluator

class FastCopyExporter:
    @staticmethod
    def export(video_path: str, in_time: float, out_time: float, output_path: str) -> bool:
        """調用 FFmpeg 進行快速無損 Stream Copy 裁切"""
        duration = out_time - in_time
        if duration <= 0:
            return False
            
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(in_time),
            "-i", video_path,
            "-t", str(duration),
            "-c", "copy",
            "-avoid_negative_ts", "1",
            output_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0

class RenderExporter:
    @staticmethod
    def apply_mosaic_or_blur(frame_rgb: np.ndarray, rect: tuple, style: str = "mosaic", strength: int = 15) -> np.ndarray:
        """在影像的指定矩形區域套用像素馬賽克或高斯模糊"""
        x, y, w, h = rect
        sub_img = frame_rgb[y:y+h, x:x+w]
        if sub_img.size == 0 or w <= 0 or h <= 0:
            return frame_rgb
            
        if style == "mosaic":
            # 縮小後放大形成馬賽克塊
            block_size = max(4, strength)
            small_w = max(1, w // block_size)
            small_h = max(1, h // block_size)
            small = cv2.resize(sub_img, (small_w, small_h), interpolation=cv2.INTER_NEAREST)
            mosaic = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
            frame_rgb[y:y+h, x:x+w] = mosaic
        else:
            # 高斯模糊
            ksize = max(3, strength | 1)  # 確保為奇數
            blur = cv2.GaussianBlur(sub_img, (ksize, ksize), 0)
            frame_rgb[y:y+h, x:x+w] = blur
            
        return frame_rgb

    @staticmethod
    def render_export(project: ProjectState, output_path: str, progress_callback: Optional[Callable[[int], None]] = None) -> bool:
        """
        執行 Single-Pass 遮蔽壓制導出
        """
        if not project.source or not os.path.exists(project.source.path):
            return False
            
        src = VideoSource(project.source.path)
        in_time = project.work_range.in_time if project.work_range else 0.0
        out_time = project.work_range.out_time if project.work_range and project.work_range.out_time > 0 else src.duration
        
        if out_time <= in_time:
            out_time = src.duration

        # 輸出暫存視訊（無音訊）
        temp_video = output_path + ".temp.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(temp_video, fourcc, src.fps, (src.width, src.height))

        # 精確 Seek 到 in_time
        src.seek_exact(in_time)
        
        total_duration = max(0.1, out_time - in_time)
        
        while True:
            frame_rgb = src.read_next_frame()
            if frame_rgb is None or src.current_time > out_time:
                break
                
            cur_t = src.current_time
            # 取得該影格的所有遮蔽矩形
            evaluated = TrackEvaluator.evaluate_all_tracks_at(project.tracks, cur_t, src.width, src.height)
            for track, rect in evaluated:
                frame_rgb = RenderExporter.apply_mosaic_or_blur(frame_rgb, rect, track.mask.style, track.mask.strength)
                
            # OpenCV 寫入需要 BGR
            frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
            writer.write(frame_bgr)
            
            if progress_callback:
                progress = int(((cur_t - in_time) / total_duration) * 100)
                progress_callback(min(99, max(0, progress)))

        writer.release()
        src.close()

        # 結合音訊與轉碼為標準 H.264
        cmd = [
            "ffmpeg", "-y",
            "-i", temp_video,
            "-ss", str(in_time),
            "-i", project.source.path,
            "-t", str(total_duration),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-map", "0:v:0",
            "-map", "1:a:0?",
            "-shortest",
            output_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if os.path.exists(temp_video):
            os.remove(temp_video)
            
        if progress_callback:
            progress_callback(100)
            
        return res.returncode == 0
