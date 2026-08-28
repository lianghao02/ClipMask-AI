from clipmask.models.project import Track, Keyframe
from clipmask.track.coverage import CoverageAnalyzer


def test_coverage_reports_missing_and_incomplete_tracks():
    assert CoverageAnalyzer.analyze([], 0.0, 10.0).critical
    track = Track(id="face-1", label="人物", keyframes=[Keyframe(time=2.0), Keyframe(time=7.0)])
    report = CoverageAnalyzer.analyze([track], 0.0, 10.0)
    assert len(report.warnings) == 3


def test_coverage_accepts_track_covering_work_range():
    track = Track(id="face-1", keyframes=[Keyframe(time=0.0), Keyframe(time=2.0), Keyframe(time=4.0)])
    assert CoverageAnalyzer.analyze([track], 0.0, 4.0).is_safe_to_continue
