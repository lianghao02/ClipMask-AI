import pytest
from PySide6.QtWidgets import QApplication
from clipmask.gui.timeline import TimelineWidget, TimelineTrackCanvas
from clipmask.models.project import ProjectState, Track, Keyframe, MaskConfig, WorkRange
from clipmask.ai.subtitles import SubtitleItem

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

def test_timeline_canvas_subtitle_bars(qapp):
    canvas = TimelineTrackCanvas()
    canvas.resize(1000, 68)
    subs = [
        SubtitleItem(id=1, start_sec=1.0, end_sec=3.0, text="第一句"),
        SubtitleItem(id=2, start_sec=4.0, end_sec=6.0, text="第二句"),
    ]
    canvas.update_state(
        current_time=2.0,
        duration=10.0,
        in_time=0.0,
        out_time=10.0,
        keyframe_times=[1.5, 4.5],
        speech_segments=[],
        subtitles=subs,
        selected_sub_id=2,
        uncovered_ranges=[(0.0, 1.0), (6.0, 10.0)]
    )
    assert len(canvas.subtitles) == 2
    assert canvas.selected_sub_id == 2
    assert len(canvas.uncovered_ranges) == 2
    assert canvas.time_to_x(5.0) > 0

    # 測試字幕 Hit Test (x=200 處在 1.0~3.0s 區間內，y=45 位於下層字幕軌)
    hit_sub, hit_mode = canvas._find_sub_hit(200, 45)
    assert hit_sub is not None
    assert hit_sub.id == 1
    assert hit_mode in ("left", "right", "body")

def test_timeline_edit_context_display(qapp):
    timeline = TimelineWidget()
    timeline.set_duration(10.0)
    
    # 測試設定為字幕情境
    timeline.set_edit_context("🎙", "字幕「測試字幕內容」", 1.0, 3.5, "不適用")
    assert "🎙" in timeline.lbl_edit_context.text()
    assert "測試字幕內容" in timeline.lbl_edit_context.text()
    assert timeline.btn_reset_range.text() == "不適用"

    # 測試設定為遮蔽情境
    timeline.set_edit_context("🎭", "遮蔽「人物 1」", 0.0, 5.0, "不適用")
    assert "🎭" in timeline.lbl_edit_context.text()
    assert "人物 1" in timeline.lbl_edit_context.text()

    # 測試設定為全片工作區間
    timeline.set_edit_context("✂", "影片工作區間", 0.0, 10.0, "重設全片")
    assert "✂" in timeline.lbl_edit_context.text()
    assert timeline.btn_reset_range.text() == "重設全片"
