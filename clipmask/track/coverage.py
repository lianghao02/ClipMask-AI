"""遮蔽軌跡完整性檢查；僅檢查編輯資料，不能取代人工看片。"""
from dataclasses import dataclass, field
from typing import List
from ..models.project import Track


@dataclass
class CoverageReport:
    critical: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def is_safe_to_continue(self) -> bool:
        return not self.critical and not self.warnings

    @property
    def messages(self) -> List[str]:
        return self.critical + self.warnings


class CoverageAnalyzer:
    @staticmethod
    def analyze(tracks: List[Track], in_time: float, out_time: float, max_keyframe_gap: float = 3.0) -> CoverageReport:
        report = CoverageReport()
        enabled = [track for track in tracks if track.enabled]
        if not enabled:
            report.critical.append("工作區間沒有啟用中的遮蔽軌跡。")
            return report
        for track in enabled:
            keyframes = sorted(track.keyframes, key=lambda item: item.time)
            name = track.label or track.id
            if not keyframes:
                report.critical.append(f"「{name}」沒有關鍵影格。")
                continue
            if len(keyframes) == 1:
                report.critical.append("單一關鍵影格無法覆蓋完整工作區間。")
                report.warnings.append(f"「{name}」只有一個關鍵影格，僅會在該點前後約 1 秒遮蔽。")
                continue
            if keyframes[0].time > in_time + 0.1:
                report.critical.append("遮蔽軌道未覆蓋工作區間起點。")
                report.warnings.append(f"「{name}」在工作區間起點後才開始遮蔽。")
            if keyframes[-1].time < out_time - 0.1:
                report.critical.append("遮蔽軌道未覆蓋工作區間終點。")
                report.warnings.append(f"「{name}」在工作區間終點前停止遮蔽。")
            for left, right in zip(keyframes, keyframes[1:]):
                if right.time - left.time > max_keyframe_gap:
                    report.critical.append("相鄰關鍵影格間隔超過安全上限。")
                    report.warnings.append(f"「{name}」有 {right.time - left.time:.1f} 秒未經確認的關鍵影格間隔。")
        return report
