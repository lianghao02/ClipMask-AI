"""遮蔽檢查回歸測試；只使用合成像素與假偵測框，不包含案件影片。"""
from fractions import Fraction
from types import SimpleNamespace

import av
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from clipmask.ai.detector import FaceDetector
from clipmask.gui.main_window import MainWindow
from clipmask.gui.video_view import VideoGraphicsView
from clipmask.models.project import Keyframe, MaskConfig, Track, WorkRange, ProjectState, SourceMetadata
from clipmask.track.evaluator import TrackEvaluator
from clipmask.export.exporter import RenderExporter


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def make_track(track_id="face", rect=(100, 100, 80, 80)):
    return Track(id=track_id, mask=MaskConfig(padding=0.25), keyframes=[
        Keyframe(0, rect_px=rect, source="face_detector"),
        Keyframe(2, rect_px=rect, source="face_detector"),
    ])


@pytest.mark.parametrize("padding", [0.25, 1.0, 100.0])
def test_padding_is_bounded_and_keeps_original_face(padding):
    assert TrackEvaluator.apply_padding_and_clamp((100, 100, 80, 80), padding, 640, 480) == (80, 80, 120, 120)


def test_padding_clips_edges_without_shifting_or_creating_offscreen_masks():
    assert TrackEvaluator.apply_padding_and_clamp((0, 0, 80, 80), 0.25, 640, 480) == (0, 0, 100, 100)
    assert TrackEvaluator.apply_padding_and_clamp((700, 500, 10, 10), 0.25, 640, 480) == (640, 480, 0, 0)


@pytest.mark.parametrize("manual_source", ["manual", "manual_in", "manual_out", "manual_persist", "split"])
@pytest.mark.parametrize("automatic_source", ["tracker", "face_detector", "persistent_extension"])
def test_manual_keyframe_has_priority(manual_source, automatic_source):
    track = make_track()
    track.add_or_update_keyframe(1, (120, 100, 60, 60), pts=10, source=manual_source)
    track.add_or_update_keyframe(1, (0, 0, 300, 300), pts=99, source=automatic_source)
    assert TrackEvaluator.evaluate_raw_rect_at(track, 1) == (120, 100, 60, 60)
    assert track.keyframes[1].pts == 10
    track.add_or_update_keyframe(1, (130, 100, 60, 60), source="manual")
    assert TrackEvaluator.evaluate_raw_rect_at(track, 1) == (130, 100, 60, 60)


def test_detector_clips_original_endpoints_without_extra_growth():
    detector = FaceDetector.__new__(FaceDetector)
    detector.detector = SimpleNamespace(setInputSize=lambda size: None, detect=lambda frame: (
        None, np.array([[-10, -5, 50, 40], [110, 90, 20, 30], [99, 99, 20, 20]], dtype=float)))
    assert detector.detect_in_frame(np.zeros((100, 100, 3), dtype=np.uint8)) == [(0, 0, 40, 35), (99, 99, 1, 1)]


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    win = MainWindow()
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    yield win
    win.video_source = None
    win.close()


def test_gui_keyframe_edits_do_not_accumulate_padding(window):
    track = make_track()
    window.project.tracks = [track]
    window.project.work_range = WorkRange(0, 2)
    window.video_source = SimpleNamespace(current_time=1, current_pts=10, width=640, height=480,
                                          duration=2, time_base=Fraction(1, 10))
    window.seek_to = lambda seconds: None
    window._refresh_track_list()
    window.track_list.setCurrentRow(0)
    for seconds in (0.5, 1.0, 1.5):
        window.video_source.current_time = seconds
        window._toggle_keyframe_at_current()
        assert TrackEvaluator.evaluate_track_at(track, seconds, 640, 480) == (80, 80, 120, 120)
    for _ in range(3):
        window._persist_selected_track()
        assert all(k.rect_px == (100, 100, 80, 80) for k in track.keyframes)
        assert isinstance(track.keyframes[0].pts, int)
    window._split_selected_track()
    assert track.keyframes[-1].time == 1.5
    assert track.keyframes[-1].rect_px == (100, 100, 80, 80)
    other = make_track("second", (200, 100, 80, 80))
    window.project.tracks.append(other)
    window._refresh_track_list()
    window.track_list.setCurrentRow(1)
    window._merge_with_previous_track()
    assert len(window.project.tracks) == 1
    assert all(isinstance(k.rect_px, tuple) for k in track.keyframes)


def test_list_selection_updates_overlay_without_frame_redraw(window):
    # 舊版多次 AI 掃描可產生相同 ID；選取與勾選必須仍對到實際物件。
    first, second = make_track(), make_track(rect=(250, 100, 80, 80))
    window.project.tracks = [first, second]
    view = window.video_view
    view.update_frame_data(np.zeros((480, 640, 3), dtype=np.uint8), window.project.tracks, [], 1)
    window._refresh_track_list()
    old_items = list(view.mask_items)
    transform = view.transform()
    for row in (0, 1, 0, 1):
        window.track_list.setCurrentRow(row)
        assert view.selected_track is window.project.tracks[row]
        assert view.mask_items[row].pen().width() == 4
        assert view.mask_items[1 - row].pen().style() == Qt.PenStyle.DashLine
        assert view.mask_items == old_items
        assert view.transform() == transform
    window.track_list.item(1).setCheckState(Qt.CheckState.Checked)
    assert second.reviewed and not first.reviewed
    window._delete_selected_track()
    assert view.selected_track is None
    assert window.track_list.currentRow() == -1
    assert all(item.pen().width() == 2 for item in view.mask_items)


def test_preview_pixels_match_render_and_highlight_is_overlay_only(qapp):
    view = VideoGraphicsView()
    track = make_track()
    frame = np.random.default_rng(7).integers(0, 256, (480, 640, 3), dtype=np.uint8)
    view.show_real_mask_preview = True
    view.update_frame_data(frame, [track], [], 1)
    expected = RenderExporter.apply_mosaic_or_blur(frame.copy(), (80, 80, 120, 120), "mosaic", track.mask.strength)
    before = view.pixmap_item.pixmap().toImage()
    # Qt 的 Pixmap 為平台格式，轉回 RGB888 比較而非假定通道排列。
    from PySide6.QtGui import QImage
    rgb = before.convertToFormat(QImage.Format.Format_RGB888)
    actual = np.frombuffer(rgb.bits(), dtype=np.uint8).reshape(480, rgb.bytesPerLine())[:, :640 * 3].reshape(480, 640, 3)
    np.testing.assert_array_equal(actual, expected)
    view.set_selected_track(track)
    assert view.mask_items[0].isVisible()
    assert view.pixmap_item.pixmap().toImage() == before
    view.set_selected_track(None)
    assert not view.mask_items[0].isVisible()
    view.close()


def test_review_list_height_scroll_and_splitter(window, qapp):
    window.video_source = SimpleNamespace(current_time=1, duration=133.18)
    window.project.work_range = WorkRange(0, 2)
    window.project.tracks = [make_track(str(i)) for i in range(40)]
    window._refresh_track_list()
    window.track_list.setCurrentRow(0)
    window.show()
    for mode in ("mask", "full"):
        window.set_work_mode(mode)
        for width, height in ((1380, 880), (1366, 768), (1920, 1080)):
            window.resize(width, height)
            qapp.processEvents()
            # Qt 可能因右側操作列的可用按鈕寬度維持較大的實際寬度；確認不被壓扁且版面仍可操作。
            assert window.width() >= width
            assert window.track_list.height() >= 160
            assert window.track_list.height() // window.track_list.sizeHintForRow(0) >= 4
            assert window.track_list.verticalScrollBar().maximum() > 0
            assert window.grp_tracks.rect().contains(window.btn_persist_track.mapTo(window.grp_tracks, window.btn_persist_track.rect().bottomRight()))
    window.review_splitter.setSizes([450, 300])
    qapp.processEvents()
    before = window.review_splitter.sizes()
    window.review_splitter.setSizes([600, 150])
    qapp.processEvents()
    assert window.review_splitter.sizes()[0] != before[0]
    window.resize(1366, 768)
    qapp.processEvents()
    scrollbar = window.toolbar_scroll.horizontalScrollBar()
    assert scrollbar.maximum() > 0
    scrollbar.setValue(scrollbar.maximum())
    qapp.processEvents()
    button_pos = window.btn_fast_export.mapTo(window.toolbar_scroll.viewport(), window.btn_fast_export.rect().center())
    assert window.toolbar_scroll.viewport().rect().contains(button_pos)


def test_selection_seeks_only_when_track_is_not_in_current_frame(window):
    track = make_track()
    window.project.tracks = [track]
    window.video_source = SimpleNamespace(current_time=10, duration=15)
    seeks = []
    window.seek_to = seeks.append
    window._refresh_track_list()
    window.track_list.setCurrentRow(0)
    assert seeks == [0]
    window.video_source.current_time = 1
    window._on_track_selection_changed(0)
    assert seeks == [0]


def test_render_export_uses_manual_rect_with_only_one_padding(tmp_path, monkeypatch):
    source = tmp_path / "synthetic.mp4"
    with av.open(str(source), "w") as container:
        stream = container.add_stream("h264", rate=10)
        stream.width, stream.height, stream.pix_fmt = 320, 240, "yuv420p"
        for i in range(10):
            frame = av.VideoFrame.from_ndarray(np.full((240, 320, 3), i * 20, dtype=np.uint8), format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    track = make_track()
    for sec in (0.2, 0.8):
        track.add_or_update_keyframe(sec, (100, 50, 40, 40), source="manual")
        track.add_or_update_keyframe(sec, (0, 0, 300, 200), source="tracker")
    project = ProjectState(source=SourceMetadata(str(source), 320, 240, 10, 1),
                           work_range=WorkRange(0.2, 0.8), tracks=[track])
    actual_apply = RenderExporter.apply_mosaic_or_blur
    used_rects = []
    def record_rect(frame, rect, style, strength):
        used_rects.append(rect)
        return actual_apply(frame, rect, style, strength)
    monkeypatch.setattr(RenderExporter, "apply_mosaic_or_blur", record_rect)
    output = tmp_path / "render.mp4"
    assert RenderExporter.render_export(project, str(output))
    assert used_rects and all(rect == (90, 40, 60, 60) for rect in used_rects)
    with av.open(str(output)) as container:
        assert len(list(container.decode(video=0))) >= 5
