"""
ClipMask-AI Vision AI Detector (極速版)
使用 640px 降採樣加速運算，偵測完成後等比例映射回原解析度座標。
"""
import os
import cv2
import numpy as np
from typing import List, Tuple, Callable, Optional
from ..models.project import Track, Keyframe, MaskConfig
from ..media.source import VideoSource

class FaceDetector:
    def __init__(self, model_path: Optional[str] = None):
        if not model_path:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            model_path = os.path.join(base_dir, "models", "face", "haarcascade_frontalface_default.xml")
            
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"人臉偵測模型不存在: {model_path}")
            
        self.face_cascade = cv2.CascadeClassifier(model_path)

    def detect_in_frame(self, frame_rgb: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """在單一影格中偵測所有人臉 [x, y, w, h] (自動加速)"""
        h_orig, w_orig = frame_rgb.shape[:2]
        
        # 降採樣到 640 寬度以獲得 10 倍以上加速
        if w_orig > 640:
            scale = 640.0 / w_orig
            small_rgb = cv2.resize(frame_rgb, (640, int(h_orig * scale)), interpolation=cv2.INTER_LINEAR)
            inv_scale = 1.0 / scale
        else:
            small_rgb = frame_rgb
            inv_scale = 1.0
            
        gray = cv2.cvtColor(small_rgb, cv2.COLOR_RGB2GRAY)
        faces = self.face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.15,
            minNeighbors=4,
            minSize=(20, 20)
        )
        
        # 放大回原始像素座標
        result = []
        for (sx, sy, sw, sh) in faces:
            rx = int(round(sx * inv_scale))
            ry = int(round(sy * inv_scale))
            rw = int(round(sw * inv_scale))
            rh = int(round(sh * inv_scale))
            result.append((rx, ry, rw, rh))
            
        return result

    def scan_work_range(
        self,
        video_source: VideoSource,
        in_time: float,
        out_time: float,
        step_sec: float = 0.5,
        progress_callback: Optional[Callable[[int, str], bool]] = None
    ) -> List[Track]:
        """
        在 Work Range 內掃描人臉。若 progress_callback 回傳 False 代表使用者取消。
        """
        tracks: List[Track] = []
        cur_t = in_time
        total_time = max(0.1, out_time - in_time)
        
        while cur_t <= out_time:
            frame_rgb = video_source.seek_exact(cur_t)
            if frame_rgb is not None:
                faces = self.detect_in_frame(frame_rgb)
                for i, face_rect in enumerate(faces):
                    track_id = f"ai_face_{len(tracks)+1}"
                    track = Track(
                        id=track_id,
                        label=f"AI 人臉 {len(tracks)+1} ({int(cur_t)}s)",
                        type="face",
                        mask=MaskConfig(style="mosaic", strength=15, padding=0.2),
                        keyframes=[
                            Keyframe(
                                time=cur_t,
                                pts=video_source.current_pts,
                                rect_px=face_rect,
                                source="face_detector"
                            )
                        ]
                    )
                    tracks.append(track)
                    
            if progress_callback:
                pct = int(((cur_t - in_time) / total_time) * 100)
                status_msg = f"正在分析 {cur_t:.1f}s / {out_time:.1f}s (已找到 {len(tracks)} 個目標)..."
                should_continue = progress_callback(min(99, max(0, pct)), status_msg)
                if should_continue is False:
                    break
                
            cur_t += step_sec

        return tracks
