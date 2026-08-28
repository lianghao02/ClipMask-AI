"""
ClipMask-AI Video Source & Seek Controller
使用 PyAV 提供幀精確解碼與三段式 Seek。
"""
import av
import numpy as np
from typing import Optional, Tuple, Generator
from ..models.project import SourceMetadata

class VideoSource:
    def __init__(self, video_path: str):
        self.video_path = video_path
        self.container = av.open(video_path)
        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"
        
        self.width = self.stream.codec_context.width
        self.height = self.stream.codec_context.height
        self.time_base = float(self.stream.time_base)
        
        if self.stream.average_rate:
            self.fps = float(self.stream.average_rate)
        elif self.stream.base_rate:
            self.fps = float(self.stream.base_rate)
        else:
            self.fps = 30.0
            
        if self.stream.duration:
            self.duration = float(self.stream.duration * self.time_base)
        elif self.container.duration:
            self.duration = float(self.container.duration / av.time.AV_TIME_BASE)
        else:
            self.duration = 0.0

        self.metadata = SourceMetadata(
            path=video_path,
            width=self.width,
            height=self.height,
            fps=self.fps,
            duration=self.duration,
            time_base=str(self.stream.time_base)
        )
        
        self.current_pts = 0
        self.current_time = 0.0
        self._decode_gen = None

    def time_to_pts(self, seconds: float) -> int:
        return int(round(seconds / self.time_base))

    def pts_to_time(self, pts: int) -> float:
        return float(pts * self.time_base)

    def seek_exact(self, target_seconds: float) -> Optional[np.ndarray]:
        target_pts = self.time_to_pts(target_seconds)
        self.container.seek(target_pts, any_frame=False, backward=True, stream=self.stream)
        self._decode_gen = self.container.decode(self.stream)
        
        last_rgb = None
        last_pts = None
        
        try:
            for frame in self._decode_gen:
                if frame.pts is None:
                    continue
                
                rgb = frame.to_ndarray(format="rgb24")
                self.current_pts = frame.pts
                self.current_time = self.pts_to_time(frame.pts)
                
                if frame.pts >= target_pts:
                    if last_pts is not None and abs(last_pts - target_pts) < abs(frame.pts - target_pts):
                        self.current_pts = last_pts
                        self.current_time = self.pts_to_time(last_pts)
                        return last_rgb
                    return rgb
                    
                last_rgb = rgb
                last_pts = frame.pts
        except Exception:
            pass
            
        return last_rgb

    def read_next_frame(self) -> Optional[np.ndarray]:
        if self._decode_gen is None:
            self._decode_gen = self.container.decode(self.stream)
            
        try:
            frame = next(self._decode_gen)
            if frame.pts is not None:
                self.current_pts = frame.pts
                self.current_time = self.pts_to_time(frame.pts)
            return frame.to_ndarray(format="rgb24")
        except Exception:
            return None

    def close(self):
        if self.container:
            self.container.close()
