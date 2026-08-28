"""
ClipMask-AI Vision AI Detector (OpenCV YuNet 深度學習 + 連續多目標軌跡追蹤)
1. 使用 OpenCV 官方現代深度學習人臉偵測器 YuNet (FaceDetectorYN)
   - 支援 360 度人臉旋轉、側臉、遮擋、遠距離小人臉、暗光高準度辨識
2. 連續軌跡聚合 (Continuous Multi-Object Tracking)：
   - 將同一個人物在不同時間點移動的座標歸入同一個 Track
   - 自動交由 TrackEvaluator 執行平滑 Lerp 遮蔽！
"""
import os
import cv2
import numpy as np
from typing import List, Tuple, Callable, Optional
from ..models.project import Track, Keyframe, MaskConfig
from ..media.source import VideoSource

class FaceDetector:
    def __init__(self, model_path: Optional[str] = None, conf_threshold: float = 0.5):
        if not model_path:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            model_path = os.path.join(base_dir, "models", "face", "face_detection_yunet_2023mar.onnx")
            
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"YuNet 人臉模型不存在: {model_path}")
            
        self.model_path = model_path
        self.conf_threshold = conf_threshold
        # 初始化 YuNet (預設輸入大小 320x320，推論前動態調整)
        self.detector = cv2.FaceDetectorYN_create(
            model=model_path,
            config="",
            input_size=(320, 320),
            score_threshold=conf_threshold,
            nms_threshold=0.3,
            top_k=5000
        )

    def detect_in_frame(self, frame_rgb: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """在單一影格中偵測所有人臉 [x, y, w, h]"""
        h_img, w_img = frame_rgb.shape[:2]
        self.detector.setInputSize((w_img, h_img))
        
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        _, faces = self.detector.detect(frame_bgr)
        
        results = []
        if faces is not None:
            for face in faces:
                x, y, w, h = face[:4]
                # 轉為 int 與邊界保護
                ix = max(0, min(w_img - 1, int(round(x))))
                iy = max(0, min(h_img - 1, int(round(y))))
                iw = max(10, min(w_img - ix, int(round(w))))
                ih = max(10, min(h_img - iy, int(round(h))))
                results.append((ix, iy, iw, ih))
                
        return results

    @staticmethod
    def _calculate_iou(boxA, boxB) -> float:
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[0] + boxA[2], boxB[0] + boxB[2])
        yB = min(boxA[1] + boxA[3], boxB[1] + boxB[3])
        interArea = max(0, xB - xA) * max(0, yB - yA)
        boxAArea = boxA[2] * boxA[3]
        boxBArea = boxB[2] * boxB[3]
        denom = float(boxAArea + boxBArea - interArea)
        return interArea / denom if denom > 0 else 0.0

    @staticmethod
    def _center_distance(boxA, boxB) -> float:
        cA_x, cA_y = boxA[0] + boxA[2] / 2.0, boxA[1] + boxA[3] / 2.0
        cB_x, cB_y = boxB[0] + boxB[2] / 2.0, boxB[1] + boxB[3] / 2.0
        return ((cA_x - cB_x) ** 2 + (cA_y - cB_y) ** 2) ** 0.5

    def scan_work_range(
        self,
        video_source: VideoSource,
        in_time: float,
        out_time: float,
        step_sec: float = 0.25,
        progress_callback: Optional[Callable[[int], None]] = None
    ) -> List[Track]:
        """
        以 0.25 秒高頻率掃描，並自動將連續偵測到的同一人座標合併為單一 Track 的 Keyframes
        """
        active_tracks: List[Track] = []
        cur_t = in_time
        total_time = max(0.1, out_time - in_time)
        
        while cur_t <= out_time:
            frame_rgb = video_source.seek_exact(cur_t)
            if frame_rgb is not None:
                faces = self.detect_in_frame(frame_rgb)
                matched_track_indices = set()
                
                for face_rect in faces:
                    best_match_idx = -1
                    best_score = -1.0
                    
                    for t_idx, track in enumerate(active_tracks):
                        if t_idx in matched_track_indices:
                            continue
                        last_kf = track.keyframes[-1]
                        # 允許 1.5 秒內的動作關聯
                        if cur_t - last_kf.time > 1.5:
                            continue
                            
                        iou = self._calculate_iou(face_rect, last_kf.rect_px)
                        dist = self._center_distance(face_rect, last_kf.rect_px)
                        max_diag = max(face_rect[2], face_rect[3]) * 2.5
                        
                        if iou > 0.15 or dist < max_diag:
                            score = iou + (1.0 / (dist + 1.0))
                            if score > best_score:
                                best_score = score
                                best_match_idx = t_idx
                                
                    if best_match_idx != -1:
                        matched_track_indices.add(best_match_idx)
                        active_tracks[best_match_idx].add_or_update_keyframe(
                            time=cur_t,
                            pts=video_source.current_pts,
                            rect_px=face_rect,
                            source="face_detector"
                        )
                    else:
                        new_track_id = f"ai_person_{len(active_tracks)+1}"
                        new_track = Track(
                            id=new_track_id,
                            label=f"人物 {len(active_tracks)+1} ({int(cur_t)}s起)",
                            type="face",
                            mask=MaskConfig(style="mosaic", strength=20, padding=0.25),
                            keyframes=[
                                Keyframe(
                                    time=cur_t,
                                    pts=video_source.current_pts,
                                    rect_px=face_rect,
                                    source="face_detector"
                                )
                            ]
                        )
                        active_tracks.append(new_track)
                        
            if progress_callback:
                pct = int(((cur_t - in_time) / total_time) * 100)
                progress_callback(min(99, max(0, pct)))
                
            cur_t += step_sec

        if progress_callback:
            progress_callback(100)
            
        return active_tracks
