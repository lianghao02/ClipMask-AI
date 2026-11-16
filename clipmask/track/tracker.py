"""
ClipMask-AI MicroTracker (單目標追蹤器)
1. track_forward: 相容既有 2 秒短程追蹤 API。
2. track_target: 從指定時間一路追蹤鎖定單一目標至工作區間結束（支援縮圖加速、局部人臉吸附校正與出鏡/跟丟保護）。
"""
from dataclasses import dataclass
import cv2
import numpy as np
from typing import Tuple, List, Optional, Callable
from ..models.project import Track, Keyframe
from ..media.source import VideoSource
from .evaluator import TrackEvaluator


@dataclass
class TrackTargetResult:
    success: bool
    keyframes_added: int = 0
    end_time: float = 0.0
    lost_reason: str = ""  # "" | "cancelled" | "lost" | "init_failed"


def _create_cv_tracker():
    try:
        return cv2.TrackerCSRT_create()
    except AttributeError:
        return cv2.TrackerMIL_create()


def _clamp_box(box: Tuple[int, int, int, int], img_w: int, img_h: int) -> Tuple[int, int, int, int]:
    x, y, w, h = box
    x = max(0, min(img_w - 2, int(round(x))))
    y = max(0, min(img_h - 2, int(round(y))))
    w = max(2, min(img_w - x, int(round(w))))
    h = max(2, min(img_h - y, int(round(h))))
    return (x, y, w, h)


def _scale_box(box: Tuple[int, int, int, int], scale: float) -> Tuple[int, int, int, int]:
    x, y, w, h = box
    return (
        int(round(x * scale)),
        int(round(y * scale)),
        max(2, int(round(w * scale))),
        max(2, int(round(h * scale))),
    )


def _visible_ratio(box: Tuple[float, float, float, float], img_w: int, img_h: int) -> float:
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return 0.0
    ix1 = max(0.0, x)
    iy1 = max(0.0, y)
    ix2 = min(float(img_w), x + w)
    iy2 = min(float(img_h), y + h)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    return inter / (w * h)


def _compute_color_hist(bgr: np.ndarray, box: Tuple[int, int, int, int]) -> Optional[np.ndarray]:
    h_img, w_img = bgr.shape[:2]
    x, y, w, h = _clamp_box(box, w_img, h_img)
    crop = bgr[y:y + h, x:x + w]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist


def _hist_similarity(h1: Optional[np.ndarray], h2: Optional[np.ndarray]) -> float:
    if h1 is None or h2 is None:
        return 1.0
    return float(cv2.compareHist(h1, h2, cv2.HISTCMP_CORREL))


def _find_matching_face_in_roi(
    detector,
    frame_bgr: np.ndarray,
    ref_box: Tuple[int, int, int, int],
    max_dist_ratio: float = 0.55,
) -> Optional[Tuple[int, int, int, int]]:
    """只在目前鎖定目標周圍的局部 ROI 搜尋相符人臉，避免抓到場景裡的其他人物。"""
    if detector is None:
        return None
    h_img, w_img = frame_bgr.shape[:2]
    rx, ry, rw, rh = ref_box
    cx = rx + rw / 2.0
    cy = ry + rh / 2.0
    roi_half_w = max(50.0, rw * 1.15)
    roi_half_h = max(50.0, rh * 1.15)
    x1 = max(0, int(round(cx - roi_half_w)))
    y1 = max(0, int(round(cy - roi_half_h)))
    x2 = min(w_img, int(round(cx + roi_half_w)))
    y2 = min(h_img, int(round(cy + roi_half_h)))
    roi_w = x2 - x1
    roi_h = y2 - y1
    if roi_w < 24 or roi_h < 24:
        return None

    roi_bgr = frame_bgr[y1:y2, x1:x2]
    detector.detector.setInputSize((roi_w, roi_h))
    _, faces = detector.detector.detect(roi_bgr)
    if faces is None or len(faces) == 0:
        return None

    best_box = None
    best_dist = float("inf")
    max_dist = max(rw, rh) * max_dist_ratio
    for face in faces:
        fx, fy, fw, fh = int(face[0]) + x1, int(face[1]) + y1, int(face[2]), int(face[3])
        if fw <= 4 or fh <= 4:
            continue
        area_ratio = (fw * fh) / max(1.0, float(rw * rh))
        if area_ratio < 0.48 or area_ratio > 2.1:
            continue
        fcx = fx + fw / 2.0
        fcy = fy + fh / 2.0
        dist = float(np.hypot(fcx - cx, fcy - cy))
        if dist <= max_dist and dist < best_dist:
            best_dist = dist
            best_box = (fx, fy, fw, fh)
    return best_box


class MicroTracker:
    @staticmethod
    def track_target(
        video_source: VideoSource,
        track: Track,
        start_time: float,
        end_time: Optional[float] = None,
        keyframe_interval: float = 0.25,
        use_face_assist: bool = True,
        progress_callback: Optional[Callable[[int], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> TrackTargetResult:
        """從 start_time 往後一路追蹤同一個目標到 end_time，定期寫入關鍵影格。"""
        if not track.keyframes:
            return TrackTargetResult(success=False, lost_reason="init_failed")

        start_rect = TrackEvaluator.evaluate_raw_rect_at(track, start_time)
        if start_rect is None:
            return TrackTargetResult(success=False, lost_reason="init_failed")

        target_end = video_source.duration if end_time is None else min(video_source.duration, float(end_time))
        if target_end <= start_time + 0.04:
            return TrackTargetResult(success=True, keyframes_added=0, end_time=start_time)

        frame_rgb = video_source.seek_exact(start_time)
        if frame_rgb is None:
            return TrackTargetResult(success=False, lost_reason="init_failed")

        orig_h, orig_w = frame_rgb.shape[:2]
        scale = min(1.0, 320.0 / max(1, orig_w))
        if scale < 1.0:
            proc_w = max(64, int(round(orig_w * scale)))
            proc_h = max(64, int(round(orig_h * scale)))
            proc_rgb = cv2.resize(frame_rgb, (proc_w, proc_h), interpolation=cv2.INTER_AREA)
        else:
            proc_w, proc_h = orig_w, orig_h
            proc_rgb = frame_rgb

        orig_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        proc_bgr = cv2.cvtColor(proc_rgb, cv2.COLOR_RGB2BGR)
        orig_start_box = _clamp_box(start_rect, orig_w, orig_h)
        proc_box = _clamp_box(_scale_box(orig_start_box, scale), proc_w, proc_h)

        # 若起始框內就是人臉，鎖定該人臉單一目標並記下外觀直方圖防止跳到其他路人
        face_detector = None
        face_locked = False
        locked_face_hist = None
        if use_face_assist:
            try:
                from ..ai.detector import FaceDetector
                face_detector = FaceDetector(conf_threshold=0.35)
                init_face = _find_matching_face_in_roi(face_detector, orig_bgr, orig_start_box, max_dist_ratio=0.75)
                if init_face is not None:
                    face_locked = True
                    locked_face_hist = _compute_color_hist(orig_bgr, init_face)
            except Exception:
                face_detector = None

        tracker = _create_cv_tracker()
        tracker.init(proc_bgr, proc_box)

        fps = float(getattr(video_source, "fps", 30.0) or 30.0)
        track_stride = max(1, int(round(fps / 12.0)))

        inv_scale = 1.0 / scale
        last_valid_orig_box = orig_start_box
        last_valid_t = start_time
        last_valid_pts = video_source.current_pts
        last_saved_t = start_time

        sampled: List[Tuple[float, Optional[int], Tuple[int, int, int, int]]] = []
        consecutive_miss = 0
        frame_idx = 0
        step_idx = 0
        total_time = max(0.05, target_end - start_time)
        lost_reason = ""

        # 先把起點這格確定鎖成人工/起點基準
        track.add_or_update_keyframe(
            time=start_time,
            pts=video_source.current_pts,
            rect_px=last_valid_orig_box,
            source="manual",
        )

        while True:
            if is_cancelled and is_cancelled():
                lost_reason = "cancelled"
                break

            frame_rgb = video_source.read_next_frame()
            if frame_rgb is None or video_source.current_time > target_end + 1e-4:
                break

            frame_idx += 1
            if (frame_idx % track_stride) != 0:
                continue

            cur_t = video_source.current_time
            cur_pts = video_source.current_pts
            step_idx += 1

            if scale < 1.0:
                proc_rgb = cv2.resize(frame_rgb, (proc_w, proc_h), interpolation=cv2.INTER_AREA)
            else:
                proc_rgb = frame_rgb
            proc_bgr = cv2.cvtColor(proc_rgb, cv2.COLOR_RGB2BGR)

            ok, raw_bbox = tracker.update(proc_bgr)
            matched_face_orig = None
            if face_locked and face_detector is not None and (step_idx % 2 == 1):
                orig_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
                ref_orig = _clamp_box(_scale_box(raw_bbox, inv_scale), orig_w, orig_h) if ok else last_valid_orig_box
                cand_face = _find_matching_face_in_roi(face_detector, orig_bgr, ref_orig, max_dist_ratio=0.55)
                if cand_face is not None:
                    # 確認外觀與前次已鎖定臉部沒有明顯跳換成別人（防畫面另一人走近時誤吸附）
                    cand_hist = _compute_color_hist(orig_bgr, cand_face)
                    hist_sim = _hist_similarity(locked_face_hist, cand_hist)
                    jump_dist = float(np.hypot(
                        (cand_face[0] + cand_face[2] / 2.0) - (last_valid_orig_box[0] + last_valid_orig_box[2] / 2.0),
                        (cand_face[1] + cand_face[3] / 2.0) - (last_valid_orig_box[1] + last_valid_orig_box[3] / 2.0),
                    ))
                    if hist_sim >= 0.42 and jump_dist <= max(orig_start_box[2], orig_start_box[3]) * 0.75:
                        matched_face_orig = cand_face
                        if hist_sim >= 0.65:
                            locked_face_hist = cv2.addWeighted(locked_face_hist, 0.85, cand_hist, 0.15, 0.0) if locked_face_hist is not None else cand_hist

            if matched_face_orig is not None:
                orig_box = _clamp_box(matched_face_orig, orig_w, orig_h)
                new_proc_box = _clamp_box(_scale_box(orig_box, scale), proc_w, proc_h)
                if not ok or np.hypot(
                    (new_proc_box[0] + new_proc_box[2] / 2.0) - (raw_bbox[0] + raw_bbox[2] / 2.0),
                    (new_proc_box[1] + new_proc_box[3] / 2.0) - (raw_bbox[1] + raw_bbox[3] / 2.0),
                ) > max(3.0, new_proc_box[2] * 0.25):
                    tracker = _create_cv_tracker()
                    tracker.init(proc_bgr, new_proc_box)
                proc_box = new_proc_box
                consecutive_miss = 0
            elif ok:
                vis = _visible_ratio(raw_bbox, proc_w, proc_h)
                if vis < 0.20:
                    consecutive_miss += 1
                    if consecutive_miss >= 8:
                        lost_reason = "lost"
                        break
                    continue
                proc_box = _clamp_box(raw_bbox, proc_w, proc_h)
                orig_box = _clamp_box(_scale_box(proc_box, inv_scale), orig_w, orig_h)
                consecutive_miss = 0
            else:
                consecutive_miss += 1
                if consecutive_miss >= 8:
                    lost_reason = "lost"
                    break
                continue

            last_valid_orig_box = orig_box
            last_valid_t = cur_t
            last_valid_pts = cur_pts

            if (cur_t - last_saved_t) >= keyframe_interval:
                sampled.append((cur_t, cur_pts, orig_box))
                last_saved_t = cur_t

            if progress_callback:
                pct = int(((cur_t - start_time) / total_time) * 100)
                progress_callback(min(99, max(0, pct)))

        if not sampled and last_valid_t <= start_time:
            if progress_callback:
                progress_callback(100)
            return TrackTargetResult(
                success=False,
                keyframes_added=0,
                end_time=start_time,
                lost_reason=lost_reason or "lost",
            )

        if (last_valid_t - last_saved_t) > 0.04:
            sampled.append((last_valid_t, last_valid_pts, last_valid_orig_box))

        before_count = len(track.keyframes)
        for kf_t, kf_pts, kf_box in sampled:
            track.add_or_update_keyframe(
                time=kf_t,
                pts=kf_pts,
                rect_px=kf_box,
                source="tracker",
            )

        if progress_callback:
            progress_callback(100)

        return TrackTargetResult(
            success=True,
            keyframes_added=max(len(sampled), len(track.keyframes) - before_count),
            end_time=last_valid_t,
            lost_reason=lost_reason,
        )

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

        # 以目前時間的未外擴框啟動，包含該時間點的人工修正。
        cur_t = video_source.current_time
        start_rect = TrackEvaluator.evaluate_raw_rect_at(track, cur_t)
        if start_rect is None:
            return False

        start_time = cur_t
        end_time = min(video_source.duration, start_time + duration_sec)
        
        # 讀取起始影格
        frame_rgb = video_source.seek_exact(start_time)
        if frame_rgb is None:
            return False
            
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        
        # 初始化 OpenCV 追蹤器 (優先使用 CSRT，精準度最高)
        tracker = _create_cv_tracker()
            
        x, y, w, h = start_rect
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
        tracked_any = False
        
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
                tracked_any = True
                last_t = video_source.current_time
                last_pts = video_source.current_pts
                
            if progress_callback and total_time > 0:
                pct = int(((video_source.current_time - start_time) / total_time) * 100)
                progress_callback(min(99, max(0, pct)))

        # 目標從未被追蹤到時不建立關鍵影格，並回報失敗讓使用者手動補點。
        if not tracked_any:
            if progress_callback:
                progress_callback(100)
            return False

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
