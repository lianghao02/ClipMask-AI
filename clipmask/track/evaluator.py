"""
ClipMask-AI Track Evaluator
單一真理來源（Single Source of Truth）：
給定 Track 與 Timestamp (秒數)，計算出該時刻經過時間插值、Padding 膨脹與畫面邊界 Clamp 後的最終像素遮蔽矩形。
"""
from typing import Optional, Tuple, List
from ..models.project import Track, Keyframe

class TrackEvaluator:
    @staticmethod
    def evaluate_track_at(track: Track, current_time: float, video_w: int, video_h: int) -> Optional[Tuple[int, int, int, int]]:
        """
        計算特定時間點 track 的遮蔽矩形 [x, y, w, h]。
        若時間超出 keyframe 範圍或 track 停用，回傳 None。
        """
        if not track.enabled or not track.keyframes:
            return None
        
        kfs = track.keyframes
        # 若只有一個 keyframe，在前後 1 秒內有效（單點遮蔽）
        if len(kfs) == 1:
            if abs(current_time - kfs[0].time) <= 1.0:
                return TrackEvaluator.apply_padding_and_clamp(kfs[0].rect_px, track.mask.padding, video_w, video_h)
            return None
        
        # 超出起訖範圍則不顯示
        if current_time < kfs[0].time or current_time > kfs[-1].time:
            return None
        
        # 尋找左側 k1 與右側 k2
        k1 = kfs[0]
        k2 = kfs[-1]
        for i in range(len(kfs) - 1):
            if kfs[i].time <= current_time <= kfs[i + 1].time:
                k1 = kfs[i]
                k2 = kfs[i + 1]
                break
        
        # 線性 Lerp 內插
        dt = k2.time - k1.time
        if dt <= 1e-5:
            interpolated_rect = k1.rect_px
        else:
            alpha = (current_time - k1.time) / dt
            x1, y1, w1, h1 = k1.rect_px
            x2, y2, w2, h2 = k2.rect_px
            ix = x1 + alpha * (x2 - x1)
            iy = y1 + alpha * (y2 - y1)
            iw = w1 + alpha * (w2 - w1)
            ih = h1 + alpha * (h2 - h1)
            interpolated_rect = (int(round(ix)), int(round(iy)), int(round(iw)), int(round(ih)))
        
        # 套用 Padding 膨脹與 Clamp 邊界限制
        return TrackEvaluator.apply_padding_and_clamp(interpolated_rect, track.mask.padding, video_w, video_h)

    @staticmethod
    def apply_padding_and_clamp(rect: Tuple[int, int, int, int], padding: float, video_w: int, video_h: int) -> Tuple[int, int, int, int]:
        """
        四周等比例增加 padding (例如 padding=0.15 代表四周各外擴 15% 的寬高)，並鉗位在影片範圍內
        """
        x, y, w, h = rect
        pad_w = w * padding
        pad_h = h * padding
        
        # 計算外擴後的座標
        nx = x - pad_w
        ny = y - pad_h
        nw = w + 2 * pad_w
        nh = h + 2 * pad_h
        
        # Clamp 邊界防護
        clamped_x = max(0, int(round(nx)))
        clamped_y = max(0, int(round(ny)))
        
        # 確保寬高不超過右下邊界
        max_w = video_w - clamped_x
        max_h = video_h - clamped_y
        
        clamped_w = max(1, min(max_w, int(round(nw))))
        clamped_h = max(1, min(max_h, int(round(nh))))
        
        return (clamped_x, clamped_y, clamped_w, clamped_h)

    @staticmethod
    def evaluate_all_tracks_at(tracks: List[Track], current_time: float, video_w: int, video_h: int) -> List[Tuple[Track, Tuple[int, int, int, int]]]:
        """計算給定時間點所有有效 track 的 (track, rect) 列表"""
        results = []
        for t in tracks:
            rect = TrackEvaluator.evaluate_track_at(t, current_time, video_w, video_h)
            if rect is not None:
                results.append((t, rect))
        return results
