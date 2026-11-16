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

def test_timeline_range_and_panel_separation(qapp):
    from types import SimpleNamespace
    from fractions import Fraction
    from clipmask.gui.main_window import MainWindow

    window = MainWindow()
    window.video_source = SimpleNamespace(current_time=4.0, current_pts=40, duration=10.0, time_base=Fraction(1, 10))
    window.seek_to = lambda seconds: None
    window.project.work_range = WorkRange(0.0, 10.0)
    track = Track(id="t1", label="人物 1", mask=MaskConfig(), keyframes=[Keyframe(1.0, rect_px=(10, 10, 20, 20)), Keyframe(6.0, rect_px=(10, 10, 20, 20))])
    sub = SubtitleItem(id=1, start_sec=2.0, end_sec=5.0, text="測試字幕")
    window.project.tracks = [track]
    window.project.subtitles = [sub]
    window._refresh_track_list()
    window._refresh_sub_list()

    # 未選取項目時，單條微調卡片預設收合
    assert window.track_detail_card.isHidden()
    assert window.sub_detail_card.isHidden()

    # 選取軌跡後微調卡片展開，但時間軸設起點/終點仍只改影片工作區間
    window.track_list.setCurrentRow(0)
    assert not window.track_detail_card.isHidden()
    window.timeline.btn_in.click()
    assert window.project.work_range.in_time == 4.0
    assert track.keyframes[0].time == 1.0

    # 右側「設為起點」才會修改單條軌跡起點
    window.video_source.current_time = 2.5
    window.btn_track_set_start.click()
    assert track.keyframes[0].time == 2.5

    # 選取字幕後，時間軸全片按鈕仍只重設工作區間，不改字幕
    window.sub_list.setCurrentRow(0)
    assert not window.sub_detail_card.isHidden()
    window.timeline.btn_reset_range.click()
    assert window.project.work_range.in_time == 0.0
    assert sub.start_sec == 2.0

    # 右側「設開始」才會修改選取的字幕
    window.video_source.current_time = 3.0
    window.btn_sub_set_start.click()
    assert sub.start_sec == 3.0

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

    # 切換至 cut (快速剪輯)：右側顯示剪輯說明與匯出側欄，隱藏遮蔽與字幕清單，並移除頂部重複剪輯鈕
    window.set_work_mode("cut")
    assert window.current_work_mode == "cut"
    assert window.btn_mode_cut.isChecked()
    assert not window.right_widget.isHidden()
    assert not window.grp_cut.isHidden()
    assert window.grp_tracks.isHidden()
    assert window.grp_subs.isHidden()
    assert window.btn_fast_export.isHidden()
    assert not window.btn_cut_panel_fast.isHidden()
    assert not window.btn_cut_panel_exact.isHidden()
    assert window.btn_render_export.isHidden()

    # 切換至 full (完整工作站)
    window.set_work_mode("full")
    assert window.current_work_mode == "full"
    assert window.btn_mode_full.isChecked()
    assert not window.right_widget.isHidden()
    assert not window.grp_tracks.isHidden()
    assert not window.grp_subs.isHidden()
    assert window.grp_cut.isHidden()
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
    window.close()


def _find_shortcut(window, key):
    from PySide6.QtGui import QKeySequence
    target = QKeySequence(key)
    for sc in window._single_key_shortcuts:
        if sc.key() == target:
            return sc
    raise AssertionError(f"找不到快捷鍵: {target.toString()}")


def test_delete_active_item_shortcut_routing(qapp):
    from PySide6.QtCore import Qt
    from clipmask.gui.main_window import MainWindow
    from clipmask.models.project import Track, MaskConfig, Keyframe
    from clipmask.ai.subtitles import SubtitleItem

    window = MainWindow()
    t1 = Track(id="t1", label="人物 1", mask=MaskConfig(), keyframes=[Keyframe(0.0, 0, (10, 10, 20, 20))])
    s1 = SubtitleItem(id=1, start_sec=1.0, end_sec=3.0, text="測試字幕")
    window.project.tracks = [t1]
    window.project.subtitles = [s1]
    window._refresh_track_list()
    window._refresh_sub_list()

    del_sc = _find_shortcut(window, Qt.Key.Key_Delete)

    # mask 模式選取遮蔽軌跡 -> Delete 只刪遮蔽軌跡
    window.set_work_mode("mask")
    window.track_list.setCurrentRow(0)
    del_sc.activated.emit()
    assert len(window.project.tracks) == 0
    assert len(window.project.subtitles) == 1

    # transcribe 模式選取字幕 -> Delete 刪字幕
    window.set_work_mode("transcribe")
    window.sub_list.setCurrentRow(0)
    del_sc.activated.emit()
    assert len(window.project.subtitles) == 0
    window.close()


def test_shortcuts_disabled_during_text_or_spin_focus(qapp):
    from PySide6.QtCore import Qt
    from clipmask.gui.main_window import MainWindow

    window = MainWindow()
    assert window._single_key_shortcuts
    assert all(sc.isEnabled() for sc in window._single_key_shortcuts)

    del_sc = _find_shortcut(window, Qt.Key.Key_Delete)
    assert del_sc.isEnabled()

    # 焦點進入字幕文字框或強度數值框時，單鍵快捷鍵必須停用
    window._on_focus_changed(window, window.edit_sub_text)
    assert not any(sc.isEnabled() for sc in window._single_key_shortcuts)

    window._on_focus_changed(window.edit_sub_text, window.spin_strength)
    assert not any(sc.isEnabled() for sc in window._single_key_shortcuts)

    # Esc / 焦點離開輸入框後恢復啟用
    window._clear_input_focus()
    window._on_focus_changed(window.spin_strength, window)
    assert all(sc.isEnabled() for sc in window._single_key_shortcuts)
    window.close()


def test_simple_shortcuts_actions(qapp):
    from types import SimpleNamespace
    from clipmask.gui.main_window import MainWindow
    from clipmask.models.project import WorkRange

    window = MainWindow()
    window.video_source = SimpleNamespace(duration=20.0, current_time=0.0, close=lambda: None)
    window.timeline.set_duration(20.0)
    window.project.work_range = WorkRange(in_time=3.0, out_time=12.0)

    # 1 / 2 / 3 模式切換
    _find_shortcut(window, "2").activated.emit()
    assert window.current_work_mode == "cut"
    _find_shortcut(window, "3").activated.emit()
    assert window.current_work_mode == "transcribe"
    _find_shortcut(window, "1").activated.emit()
    assert window.current_work_mode == "mask"

    # P 切換即時馬賽克預覽
    before_preview = window.video_view.show_real_mask_preview
    _find_shortcut(window, "P").activated.emit()
    assert window.video_view.show_real_mask_preview is (not before_preview)
    assert "(P)" in window.btn_toggle_preview.text()

    # = / - / 0 時間軸縮放與重設
    _find_shortcut(window, "=").activated.emit()
    assert window.timeline.canvas.zoom_factor > 1.0
    zoomed = window.timeline.canvas.zoom_factor
    _find_shortcut(window, "-").activated.emit()
    assert window.timeline.canvas.zoom_factor < zoomed
    _find_shortcut(window, "0").activated.emit()
    assert window.timeline.canvas.zoom_factor == 1.0

    # R 重設工作區間
    _find_shortcut(window, "R").activated.emit()
    assert window.project.work_range.in_time == 0.0
    assert window.project.work_range.out_time == 20.0
    window.close()


def test_selected_overlay_drag_hit_test_and_adjust(qapp):
    import numpy as np
    from PySide6.QtCore import QPointF
    from types import SimpleNamespace
    from clipmask.gui.main_window import MainWindow
    from clipmask.models.project import Track, MaskConfig, Keyframe
    from clipmask.track.evaluator import TrackEvaluator

    window = MainWindow()
    window.video_source = SimpleNamespace(duration=10.0, current_time=2.0, current_pts=20, time_base=0.1, close=lambda: None)
    track = Track(
        id="m1",
        label="遮蔽 1 (手動常駐)",
        mask=MaskConfig(style="mosaic", strength=20, padding=0.25),
        keyframes=[
            Keyframe(time=0.0, rect_px=(100, 80, 60, 60), source="manual_in"),
            Keyframe(time=10.0, rect_px=(100, 80, 60, 60), source="manual_out"),
        ],
    )
    window.project.tracks = [track]
    window.current_frame_rgb = np.zeros((360, 640, 3), dtype=np.uint8)
    window._refresh_track_list()
    window.track_list.setCurrentRow(0)
    window.video_view.update_frame_data(window.current_frame_rgb, window.project.tracks, [], 2.0)

    rect = window.video_view._selected_overlay_rect()
    assert rect is not None
    assert len(window.video_view._handle_items) == 4
    assert window.video_view._hit_test_selected(rect.center()) == "move"
    assert window.video_view._hit_test_selected(rect.topLeft()) == "tl"
    assert window.video_view._hit_test_selected(rect.bottomRight()) == "br"
    assert window.video_view._hit_test_selected(QPointF(5.0, 5.0)) is None

    # 模擬將選中框拖移放大到 (220, 120, 100, 80)
    window._on_selected_rect_adjusted(220, 120, 100, 80)
    assert len(window.project.tracks) == 1
    evaluated = TrackEvaluator.evaluate_track_at(track, 2.0, 640, 360)
    assert evaluated is not None
    assert abs(evaluated[0] - 220) <= 3
    assert abs(evaluated[1] - 120) <= 3
    assert abs(evaluated[2] - 100) <= 4
    assert abs(evaluated[3] - 80) <= 4
    window.close()


def test_apply_tracked_keyframes_and_f_shortcut(qapp):
    from clipmask.gui.main_window import MainWindow
    from clipmask.models.project import Track, MaskConfig, Keyframe
    from clipmask.track.tracker import TrackTargetResult

    window = MainWindow()
    track = Track(
        id="t_follow",
        label="遮蔽 1 (手動常駐)",
        mask=MaskConfig(),
        keyframes=[
            Keyframe(time=0.0, rect_px=(10, 10, 40, 40), source="manual_in"),
            Keyframe(time=2.0, rect_px=(10, 10, 40, 40), source="manual"),
            Keyframe(time=10.0, rect_px=(10, 10, 40, 40), source="manual_out"),
        ],
    )
    tracked = [
        Keyframe(time=3.0, rect_px=(50, 20, 40, 40), source="tracker"),
        Keyframe(time=5.0, rect_px=(110, 35, 40, 40), source="tracker"),
    ]
    res = TrackTargetResult(success=True, keyframes_added=2, end_time=5.0)
    window._apply_tracked_keyframes(track, 2.0, tracked, res)

    assert track.label == "遮蔽 1 (已追蹤)"
    # 原本 10.0s 的 manual_out 預設佔位應被清掉，避免目標飄回 (10, 10)
    assert all(kf.source != "manual_out" for kf in track.keyframes)
    assert track.keyframes[-1].time == 5.0
    assert track.keyframes[-1].rect_px == (110, 35, 40, 40)

    # F 快捷鍵註冊且於文字框輸入時會被暫停
    f_sc = _find_shortcut(window, "F")
    assert f_sc.isEnabled()
    window._on_focus_changed(window, window.edit_sub_text)
    assert not f_sc.isEnabled()
    window.close()


def test_option_b_undo_redo_and_two_line_items(qapp):
    import numpy as np
    from types import SimpleNamespace
    from fractions import Fraction
    from clipmask.gui.main_window import MainWindow
    from clipmask.models.project import WorkRange

    window = MainWindow()
    window.video_source = SimpleNamespace(
        current_time=1.0,
        current_pts=10,
        duration=10.0,
        time_base=Fraction(1, 10),
        close=lambda: None,
    )
    window.seek_to = lambda seconds: None
    window.project.work_range = WorkRange(0.0, 10.0)
    window.current_frame_rgb = np.zeros((240, 320, 3), dtype=np.uint8)

    # 空狀態下尚未開檔，空狀態引導卡片顯示，復原/重做皆停用
    assert not window.empty_overlay.isHidden()
    assert window.busy_overlay.isHidden()
    assert not window.btn_undo.isEnabled()
    assert not window.btn_redo.isEnabled()

    # 新增手動遮蔽 -> 產生兩行清晰清單項與可復原紀錄
    window._on_user_drawn_rect(40, 30, 80, 80)
    assert len(window.project.tracks) == 1
    assert window.btn_undo.isEnabled()
    item_text = window.track_list.item(0).text()
    assert "\n" in item_text
    assert "馬賽克" in item_text
    assert "點" in item_text

    # 拖曳微調藍框 -> 記錄復原點
    before_kfs = len(window.project.tracks[0].keyframes)
    window.video_source.current_time = 2.5
    window._on_selected_rect_adjusted(100, 50, 90, 90)
    assert len(window.project.tracks[0].keyframes) == before_kfs + 1

    # 刪除軌跡 -> 復原應完整還原該軌跡與關鍵格，重做則再次刪除
    window._delete_selected_track()
    assert len(window.project.tracks) == 0

    window.undo()
    assert len(window.project.tracks) == 1
    assert window.btn_redo.isEnabled()

    window.redo()
    assert len(window.project.tracks) == 0

    # Ctrl+Z / Ctrl+Y 快捷鍵也已綁定
    undo_sc = _find_shortcut(window, "Ctrl+Z")
    redo_sc = _find_shortcut(window, "Ctrl+Y")
    undo_sc.activated.emit()
    assert len(window.project.tracks) == 1
    redo_sc.activated.emit()
    assert len(window.project.tracks) == 0
    window.close()


def test_option_b_busy_overlay_and_sidebar_collapse(qapp):
    from clipmask.gui.main_window import MainWindow

    window = MainWindow()
    cancelled = []
    window._show_busy_overlay("測試處理中", "正在計算...", on_cancel=lambda: cancelled.append(True))
    assert not window.busy_overlay.isHidden()
    assert not window.toolbar_scroll.isEnabled()
    window._update_busy_progress(45)
    assert window.busy_overlay.progress_bar.value() == 45

    window.busy_overlay.btn_cancel.click()
    assert cancelled == [True]
    assert not window.busy_overlay.btn_cancel.isEnabled()

    window._hide_busy_overlay()
    assert window.busy_overlay.isHidden()
    assert window.toolbar_scroll.isEnabled()

    # 側欄收合：關閉側欄後切換模式不應擅自展開，手動開啟才恢復
    window._toggle_sidebar(False)
    assert window.right_widget.isHidden()
    assert not window.btn_toggle_sidebar.isChecked()
    window.set_work_mode("cut")
    assert window.right_widget.isHidden()
    window.set_work_mode("mask")
    assert window.right_widget.isHidden()
    window._toggle_sidebar(True)
    assert not window.right_widget.isHidden()
    assert window.btn_toggle_sidebar.isChecked()
    window.close()

