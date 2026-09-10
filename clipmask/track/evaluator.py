"""
ClipMask-AI Track Evaluator
單一真理來源（Single Source of Truth）：
給定 Track 與 Timestamp (秒數)，計算出該時刻經過時間插值、Padding 膨脹與畫面邊界 Clamp 後的最終像素遮蔽矩形。
"""
from typing import Optional, Tuple, List
import math
from ..models.project import Track, Keyframe

class TrackEvaluator:
    MAX_PADDING = 0.25  # 每邊最多外擴原始框的 25%，禁止由已外擴框累積放大

    @staticmethod
    def evaluate_track_at(track: Track, current_time: float, video_w: int, video_h: int) -> Optional[Tuple[int, int, int, int]]:
        rect = TrackEvaluator.evaluate_raw_rect_at(track, current_time)
        if rect is None:
            return None
        return TrackEvaluator.apply_padding_and_clamp(rect, track.mask.padding, video_w, video_h)

    @staticmethod
    def evaluate_raw_rect_at(track: Track, current_time: float) -> Optional[Tuple[int, int, int, int]]:
        """
        取得未外擴的插值框；編輯與追蹤回存只能使用此框，避免重複 padding。
        若時間超出 keyframe 範圍或 track 停用，回傳 None。
        """
        if not track.enabled or not track.keyframes:
            return None
        
        kfs = track.keyframes
        # 若只有一個 keyframe，在前後 1 秒內有效（單點遮蔽）
        if len(kfs) == 1:
            if abs(current_time - kfs[0].time) <= 1.0:
                return kfs[0].rect_px
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
        
        return interpolated_rect

    @staticmethod
    def apply_padding_and_clamp(rect: Tuple[int, int, int, int], padding: float, video_w: int, video_h: int) -> Tuple[int, int, int, int]:
        """
        四周等比例增加 padding (例如 padding=0.15 代表四周各外擴 15% 的寬高)，並鉗位在影片範圍內
        """
        x, y, w, h = rect
        padding = min(TrackEvaluator.MAX_PADDING, max(0.0, padding))
        pad_w = w * padding
        pad_h = h * padding
        
        # 分別裁切四邊，避免左上溢出的寬高被平移到右下而誤遮更多區域。
        left = min(video_w, max(0, math.floor(x - pad_w)))
        top = min(video_h, max(0, math.floor(y - pad_h)))
        right = min(video_w, max(0, math.ceil(x + w + pad_w)))
        bottom = min(video_h, max(0, math.ceil(y + h + pad_h)))
        return (left, top, max(0, right - left), max(0, bottom - top))

    @staticmethod
    def evaluate_all_tracks_at(tracks: List[Track], current_time: float, video_w: int, video_h: int) -> List[Tuple[Track, Tuple[int, int, int, int]]]:
        """計算給定時間點所有有效 track 的 (track, rect) 列表"""
        results = []
        for t in tracks:
            rect = TrackEvaluator.evaluate_track_at(t, current_time, video_w, video_h)
            if rect is not None:
                results.append((t, rect))
        return results
