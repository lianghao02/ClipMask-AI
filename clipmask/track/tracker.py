"""
ClipMask-AI MicroTracker (短程輔助追蹤器)
使用 OpenCV CSRT/KCF 追蹤器向後預測 N 幀 (預設 2 秒)，
並在終點建立一個新的 Keyframe，由 TrackEvaluator 負責平滑 Lerp 內插。
"""
import cv2
import numpy as np
from typing import Tuple, List, Optional, Callable
from ..models.project import Track, Keyframe
from ..media.source import VideoSource

class MicroTracker:
    @staticmethod
    def track_forward(
        video_source: VideoSource,
        track: Track,
        duration_sec: float = 2.0,
        progress_callback: Optional[Callable[[int], None]] = None
    ) -> bool:
        """
        從當前時間點向後追蹤 duration_sec 秒，並在結束點新增一個 Keyframe。
        """
        if not track.keyframes:
            return False

        # 以最靠近當前時間點的 keyframe 作為起始框
        cur_t = video_source.current_time
        kf_start = track.keyframes[-1]
        for kf in track.keyframes:
            if kf.time <= cur_t:
                kf_start = kf

        start_time = cur_t
        end_time = min(video_source.duration, start_time + duration_sec)
        
        # 讀取起始影格
        frame_rgb = video_source.seek_exact(start_time)
        if frame_rgb is None:
            return False
            
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        
        # 初始化 OpenCV 追蹤器 (優先使用 CSRT，精準度最高)
        try:
            tracker = cv2.TrackerCSRT_create()
        except AttributeError:
            tracker = cv2.TrackerMIL_create()
            
        x, y, w, h = kf_start.rect_px
        # 防護：確保 bounding box 位於畫面內
        h_img, w_img = frame_bgr.shape[:2]
        x = max(0, min(w_img - 1, x))
        y = max(0, min(h_img - 1, y))
        w = max(1, min(w_img - x, w))
        h = max(1, min(h_img - y, h))
        
        tracker.init(frame_bgr, (x, y, w, h))
        
        last_box = (x, y, w, h)
        last_t = start_time
        last_pts = video_source.current_pts
        
        # 逐幀追蹤
        total_time = end_time - start_time
        while True:
            frame_rgb = video_source.read_next_frame()
            if frame_rgb is None or video_source.current_time > end_time:
                break
                
            frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
            ok, bbox = tracker.update(frame_bgr)
            if ok:
                bx, by, bw, bh = [int(v) for v in bbox]
                last_box = (bx, by, bw, bh)
                last_t = video_source.current_time
                last_pts = video_source.current_pts
                
            if progress_callback and total_time > 0:
                pct = int(((video_source.current_time - start_time) / total_time) * 100)
                progress_callback(min(99, max(0, pct)))

        # 在追蹤終點建立/更新 Keyframe
        track.add_or_update_keyframe(
            time=last_t,
            pts=last_pts,
            rect_px=last_box,
            source="tracker"
        )
        
        if progress_callback:
            progress_callback(100)
            
        return True
