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

def test_timeline_modes(qapp):
    timeline = TimelineWidget()
    
    # 遮蔽模式 (mask)：隱藏聽打輸入，顯示關鍵影格按鈕
    timeline.set_mode("mask")
    assert timeline.widget_transcript.isHidden()
    assert not timeline.btn_toggle_kf.isHidden()
    assert not timeline.btn_in.isHidden()

    # 聽打模式 (transcribe)：顯示聽打輸入，隱藏關鍵影格按鈕
    timeline.set_mode("transcribe")
    assert not timeline.widget_transcript.isHidden()
    assert timeline.btn_toggle_kf.isHidden()
    assert not timeline.btn_in.isHidden()

    # 快速剪輯模式 (cut)：隱藏聽打輸入與關鍵影格按鈕，保留 In/Out/Reset
    timeline.set_mode("cut")
    assert timeline.widget_transcript.isHidden()
    assert timeline.btn_toggle_kf.isHidden()
    assert not timeline.btn_in.isHidden()
    assert not timeline.btn_out.isHidden()
    assert not timeline.btn_reset_range.isHidden()

    # 完整工作站 (full)：全部顯示
    timeline.set_mode("full")
    assert not timeline.widget_transcript.isHidden()
    assert not timeline.btn_toggle_kf.isHidden()
    assert not timeline.btn_in.isHidden()

def test_main_window_modes_and_seek_state(qapp):
    from clipmask.gui.main_window import MainWindow
    window = MainWindow()
    
    # 預設模式為 mask (人臉去識別)
    assert window.current_work_mode == "mask"
    assert window.btn_mode_mask.isChecked()
    assert not window.grp_tracks.isHidden()
    assert window.grp_subs.isHidden()
    assert not window.btn_ai_detect.isHidden()
    assert window.btn_fast_export.isHidden()

    # 切換至 transcribe (字幕聽打)
    window.set_work_mode("transcribe")
    assert window.current_work_mode == "transcribe"
    assert window.btn_mode_transcribe.isChecked()
    assert window.grp_tracks.isHidden()
    assert not window.grp_subs.isHidden()
    assert window.btn_ai_detect.isHidden()
    assert not window.timeline.widget_transcript.isHidden()

    # 切換至 cut (快速剪輯)
    window.set_work_mode("cut")
    assert window.current_work_mode == "cut"
    assert window.btn_mode_cut.isChecked()
    assert window.right_widget.isHidden()
    assert not window.btn_fast_export.isHidden()
    assert window.btn_render_export.isHidden()

    # 切換至 full (完整工作站)
    window.set_work_mode("full")
    assert window.current_work_mode == "full"
    assert window.btn_mode_full.isChecked()
    assert not window.right_widget.isHidden()
    assert not window.grp_tracks.isHidden()
    assert not window.grp_subs.isHidden()
    assert not window.btn_ai_detect.isHidden()
    assert not window.btn_fast_export.isHidden()
    assert not window.btn_render_export.isHidden()

    # 驗證 Seek 狀態保護旗標初始化
    assert window._seek_generation >= 0
    assert window._is_seeking is False

def test_set_all_tracks_reviewed(qapp):
    from clipmask.gui.main_window import MainWindow
    from clipmask.models.project import Track, MaskConfig, Keyframe
    window = MainWindow()

    t1 = Track(id="t1", label="人物 1", mask=MaskConfig(), keyframes=[Keyframe(0.0, 0, (10, 10, 20, 20))], reviewed=False)
    t2 = Track(id="t2", label="人物 2", mask=MaskConfig(), keyframes=[Keyframe(0.0, 0, (30, 30, 20, 20))], reviewed=False)
    window.project.tracks = [t1, t2]
    window._refresh_track_list()

    assert not t1.reviewed
    assert not t2.reviewed
    assert window.btn_accept_all.isEnabled()

    # 執行全選同意確認
    window._set_all_tracks_reviewed(True)
    assert t1.reviewed
    assert t2.reviewed
    assert not window.btn_next_review.isEnabled()
    assert "檢查完成" in window.lbl_review_summary.text()

    # 取消全選
    window._set_all_tracks_reviewed(False)
    assert not t1.reviewed
    assert not t2.reviewed
    assert window.btn_next_review.isEnabled()

