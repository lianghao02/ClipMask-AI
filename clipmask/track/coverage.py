"""遮蔽軌跡完整性檢查；僅檢查編輯資料，不能取代人工看片。

判定原則：
- critical（阻擋匯出）：沒有可用軌跡、軌跡沒有關鍵影格、相鄰關鍵影格間隔過大（中間遮蔽位置只是估算）。
- warnings（需使用者確認）：單一關鍵影格的軌跡、工作區間內沒有任何遮蔽的時段。
  人物只出現在區間中段是正常情形，不再要求每條軌跡涵蓋整段；改為明確告知哪些時段不會遮蔽。
"""
from dataclasses import dataclass, field
from typing import List, Tuple
from ..models.project import Track

SINGLE_KEYFRAME_REACH = 1.0  # 與 TrackEvaluator 單點遮蔽的前後有效秒數一致


@dataclass
class CoverageReport:
    critical: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    uncovered_ranges: List[Tuple[float, float]] = field(default_factory=list)
    uncovered_seconds: float = 0.0
    total_seconds: float = 0.0

    @property
    def is_safe_to_continue(self) -> bool:
        return not self.critical and not self.warnings

    @property
    def messages(self) -> List[str]:
        return self.critical + self.warnings


class CoverageAnalyzer:
    @staticmethod
    def _track_span(keyframes) -> Tuple[float, float]:
        if len(keyframes) == 1:
            time = keyframes[0].time
            return time - SINGLE_KEYFRAME_REACH, time + SINGLE_KEYFRAME_REACH
        return keyframes[0].time, keyframes[-1].time

    @staticmethod
    def analyze(tracks: List[Track], in_time: float, out_time: float, max_keyframe_gap: float = 3.0) -> CoverageReport:
        report = CoverageReport(total_seconds=max(0.0, out_time - in_time))
        enabled = [track for track in tracks if track.enabled]
        if not enabled:
            report.critical.append("工作區間沒有啟用中的遮蔽軌跡。")
            report.uncovered_ranges = [(in_time, out_time)] if out_time > in_time else []
            report.uncovered_seconds = report.total_seconds
            return report

        spans: List[Tuple[float, float]] = []
        for track in enabled:
            keyframes = sorted(track.keyframes, key=lambda item: item.time)
            name = track.label or track.id
            if not keyframes:
                report.critical.append(f"「{name}」沒有關鍵影格。")
                continue
            if len(keyframes) == 1:
                report.warnings.append(f"「{name}」只有一個關鍵影格，僅會在該點前後約 1 秒遮蔽。")
            for left, right in zip(keyframes, keyframes[1:]):
                if right.time - left.time > max_keyframe_gap:
                    report.critical.append(
                        f"「{name}」在 {left.time:.1f}s 到 {right.time:.1f}s 之間有 {right.time - left.time:.1f} 秒沒有關鍵影格，中間遮蔽位置僅為估算。"
                    )
            spans.append(CoverageAnalyzer._track_span(keyframes))

        # 以所有軌跡的有效時段聯集，找出工作區間內完全沒有遮蔽的時段。
        cursor = in_time
        for start, end in sorted(spans):
            if end <= cursor:
                continue
            if start > cursor:
                report.uncovered_ranges.append((cursor, min(start, out_time)))
            cursor = max(cursor, end)
            if cursor >= out_time:
                break
        if cursor < out_time:
            report.uncovered_ranges.append((cursor, out_time))
        report.uncovered_ranges = [(a, b) for a, b in report.uncovered_ranges if b - a > 0.1]
        report.uncovered_seconds = sum(b - a for a, b in report.uncovered_ranges)
        if report.uncovered_ranges:
            percent = report.uncovered_seconds / report.total_seconds * 100 if report.total_seconds else 0.0
            report.warnings.append(
                f"工作區間有 {report.uncovered_seconds:.1f} 秒（{percent:.0f}%）沒有任何遮蔽，"
                f"共 {len(report.uncovered_ranges)} 段，這些時段會原樣輸出。"
            )
        return report
