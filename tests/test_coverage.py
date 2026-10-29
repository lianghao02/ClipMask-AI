from clipmask.models.project import Track, Keyframe
from clipmask.track.coverage import CoverageAnalyzer


def test_coverage_reports_missing_and_incomplete_tracks():
    assert CoverageAnalyzer.analyze([], 0.0, 10.0).critical
    track = Track(id="face-1", label="人物", keyframes=[Keyframe(time=2.0), Keyframe(time=7.0)])
    report = CoverageAnalyzer.analyze([track], 0.0, 10.0)
    assert len(report.critical) == 1 and "5.0 秒" in report.critical[0]  # 關鍵影格間隔過大才阻擋
    assert len(report.warnings) == 1 and "沒有任何遮蔽" in report.warnings[0]
    assert report.uncovered_ranges == [(0.0, 2.0), (7.0, 10.0)]
    assert abs(report.uncovered_seconds - 5.0) < 1e-6


def test_coverage_accepts_track_covering_work_range():
    track = Track(id="face-1", keyframes=[Keyframe(time=0.0), Keyframe(time=2.0), Keyframe(time=4.0)])
    assert CoverageAnalyzer.analyze([track], 0.0, 4.0).is_safe_to_continue


def test_track_in_middle_of_range_is_warning_not_blocking():
    track = Track(id="face", label="人物", keyframes=[Keyframe(time=t) for t in (4.0, 5.0, 6.0)])
    report = CoverageAnalyzer.analyze([track], 0.0, 10.0)
    assert not report.critical
    assert report.uncovered_ranges == [(0.0, 4.0), (6.0, 10.0)]
    assert any("沒有任何遮蔽" in message for message in report.warnings)


def test_many_partial_tracks_do_not_repeat_messages_and_union_coverage():
    tracks = [Track(id=f"t{i}", label=f"人物 {i}", keyframes=[Keyframe(time=t) for t in (i * 2.0, i * 2.0 + 2.0)])
              for i in range(5)]
    report = CoverageAnalyzer.analyze(tracks, 0.0, 10.0)
    assert report.is_safe_to_continue and not report.uncovered_ranges  # 聯集剛好涵蓋整段
    assert len(CoverageAnalyzer.analyze(tracks[:3], 0.0, 10.0).messages) == 1


def test_single_keyframe_track_is_warning_with_one_second_reach():
    track = Track(id="face", label="人物", keyframes=[Keyframe(time=5.0)])
    report = CoverageAnalyzer.analyze([track], 4.0, 6.0)
    assert not report.critical and report.uncovered_ranges == []
    assert any("只有一個關鍵影格" in message for message in report.warnings)
