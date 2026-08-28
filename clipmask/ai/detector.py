"""
ClipMask-AI Vision AI Detector
使用本地 models/face/ 下的模型，在 Work Range 內自動掃描並建立初始 Track 與 Keyframes。
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
            # 尋找專案內 models/face
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            model_path = os.path.join(base_dir, "models", "face", "haarcascade_frontalface_default.xml")
            
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"人臉偵測模型不存在: {model_path}")
            
        self.face_cascade = cv2.CascadeClassifier(model_path)

    def detect_in_frame(self, frame_rgb: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """在單一影格中偵測所有人臉 [x, y, w, h]"""
        gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
        faces = self.face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=4,
            minSize=(24, 24)
        )
        return [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in faces]

    def scan_work_range(
        self,
        video_source: VideoSource,
        in_time: float,
        out_time: float,
        step_sec: float = 0.5,
        progress_callback: Optional[Callable[[int], None]] = None
    ) -> List[Track]:
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
                progress_callback(min(99, max(0, pct)))
                
            cur_t += step_sec

        if progress_callback:
            progress_callback(100)
            
        return tracks
