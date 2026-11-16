import os
from fractions import Fraction
from types import SimpleNamespace

import av
import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QInputDialog

from clipmask.export.compressor import (
    DEFAULT_DVD_TARGET_MB,
    VideoCompressor,
    VideoProbeInfo,
    calculate_compression_plan,
)
from clipmask.gui.main_window import MainWindow
from clipmask.models.project import Keyframe, MaskConfig, Track, WorkRange


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_canvas_click_selects_other_track_without_drawing_new(qapp):
    window = MainWindow()
    window.video_source = SimpleNamespace(
        current_time=2.0, current_pts=20, duration=10.0, time_base=Fraction(1, 10), close=lambda: None
    )
    window.seek_to = lambda seconds: None
    t1 = Track(id="t1", label="人物 1", mask=MaskConfig(padding=0.0), keyframes=[Keyframe(0.0, rect_px=(20, 20, 80, 80)), Keyframe(5.0, rect_px=(20, 20, 80, 80))])
    t2 = Track(id="t2", label="人物 2", mask=MaskConfig(padding=0.0), keyframes=[Keyframe(0.0, rect_px=(240, 120, 90, 90)), Keyframe(5.0, rect_px=(240, 120, 90, 90))])
    window.project.tracks = [t1, t2]
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    window.current_frame_rgb = frame
    window._refresh_track_list()
    window.track_list.setCurrentRow(0)
    window.video_view.resize(640, 360)
    window.video_view.update_frame_data(frame, window.project.tracks, [], 2.0)

    # 取得第二個遮蔽框中心於 viewport 上的座標並點擊，應切換選取而非畫新框
    center_scene = QPointF(285.0, 165.0)
    view_pt = QPointF(window.video_view.mapFromScene(center_scene))
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        view_pt,
        view_pt,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        view_pt,
        view_pt,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.video_view.mousePressEvent(press)
    window.video_view.mouseReleaseEvent(release)

    assert len(window.project.tracks) == 2
    assert window.track_list.currentRow() == 1
    assert window.video_view.selected_track is t2
    assert len(t2.keyframes) == 2  # 單純點選切換不應新增多餘關鍵格
    window.close()


def test_rename_track_with_double_click_and_undo(qapp, monkeypatch):
    window = MainWindow()
    t1 = Track(id="t1", label="遮蔽 1 (手動常駐)", mask=MaskConfig(), keyframes=[Keyframe(0.0, rect_px=(10, 10, 40, 40))])
    window.project.tracks = [t1]
    window._refresh_track_list()

    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("櫃台陳先生", True))
    item = window.track_list.item(0)
    window._rename_track_item(item)

    assert t1.label == "櫃台陳先生"
    assert "櫃台陳先生" in window.track_list.item(0).text()

    window.undo()
    assert window.project.tracks[0].label == "遮蔽 1 (手動常駐)"
    window.redo()
    assert window.project.tracks[0].label == "櫃台陳先生"
    window.close()


def test_compression_plan_dvd_default_and_downscale():
    # 長片 2 小時、6.8 GB，要壓進標準 4.7GB DVD（安全上限 4,300 MB）
    long_hd = VideoProbeInfo(
        path="demo.mp4",
        file_size_bytes=6800 * 1024 * 1024,
        duration_sec=7200.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
        source_bitrate_bps=7_500_000,
    )
    plan = calculate_compression_plan(long_hd, target_mb=DEFAULT_DVD_TARGET_MB, auto_downscale=True)
    assert plan.target_mb == 4300
    assert not plan.already_smaller
    assert plan.estimated_output_bytes <= plan.target_bytes
    assert plan.video_bitrate_kbps > 0

    # 非常緊的目標容量（如 500 MB 裝 2 小時 1080p）：開啟自動降解析度應縮到 480p 保清晰
    tight_plan = calculate_compression_plan(long_hd, target_mb=500, auto_downscale=True)
    assert tight_plan.downscaled
    assert tight_plan.output_height <= 720
    assert tight_plan.output_width % 2 == 0 and tight_plan.output_height % 2 == 0

    no_scale_plan = calculate_compression_plan(long_hd, target_mb=500, auto_downscale=False)
    assert not no_scale_plan.downscaled
    assert (no_scale_plan.output_width, no_scale_plan.output_height) == (1920, 1080)

    # 原檔本來就比 DVD 小時明確標記 already_smaller
    small_clip = VideoProbeInfo(
        path="small.mp4",
        file_size_bytes=120 * 1024 * 1024,
        duration_sec=60.0,
        width=1280,
        height=720,
        fps=30.0,
        has_audio=True,
        source_bitrate_bps=16_000_000,
    )
    small_plan = calculate_compression_plan(small_clip, target_mb=DEFAULT_DVD_TARGET_MB)
    assert small_plan.already_smaller


def test_video_compressor_compresses_real_mp4(tmp_path):
    source = tmp_path / "src.mp4"
    with av.open(str(source), "w") as container:
        v_stream = container.add_stream("h264", rate=15)
        v_stream.width, v_stream.height, v_stream.pix_fmt = 320, 240, "yuv420p"
        a_stream = container.add_stream("aac", rate=48000)
        a_stream.layout = "stereo"
        for i in range(15):
            vf = av.VideoFrame.from_ndarray(
                np.full((240, 320, 3), (i * 15) % 255, dtype=np.uint8), format="rgb24"
            )
            for pkt in v_stream.encode(vf):
                container.mux(pkt)
        for _ in range(3):
            af = av.AudioFrame.from_ndarray(np.zeros((2, 1024), dtype=np.float32), format="fltp", layout="stereo")
            af.sample_rate = 48000
            for pkt in a_stream.encode(af):
                container.mux(pkt)
        for pkt in v_stream.encode():
            container.mux(pkt)
        for pkt in a_stream.encode():
            container.mux(pkt)

    output = tmp_path / "compressed.mp4"
    progress_values = []
    ok = VideoCompressor.compress(
        str(source),
        str(output),
        target_mb=1,
        auto_downscale=True,
        progress_callback=progress_values.append,
    )
    assert ok
    assert output.exists()
    assert os.path.getsize(output) <= 1 * 1024 * 1024
    assert progress_values[-1] == 100
    with av.open(str(output)) as out_c:
        assert len(out_c.streams.video) == 1
        assert len(list(out_c.decode(video=0))) >= 10


def test_compress_tab_is_isolated_and_defaults_to_dvd(qapp, tmp_path):
    source = tmp_path / "tiny.mp4"
    with av.open(str(source), "w") as container:
        stream = container.add_stream("h264", rate=10)
        stream.width, stream.height, stream.pix_fmt = 320, 240, "yuv420p"
        for i in range(10):
            frame = av.VideoFrame.from_ndarray(np.zeros((240, 320, 3), dtype=np.uint8), format="rgb24")
            for pkt in stream.encode(frame):
                container.mux(pkt)
        for pkt in stream.encode():
            container.mux(pkt)

    window = MainWindow()
    cp = window.compress_page
    assert cp.spin_target_mb.value() == DEFAULT_DVD_TARGET_MB
    assert cp.btn_preset_dvd.isChecked()
    assert not cp.btn_start_compress.isEnabled()

    # 切到獨立「影片壓縮」分頁：保留統一「左拖曳/預覽 + 右側欄」版面，並隔離編輯功能按鈕與快捷鍵
    window.set_work_mode("compress")
    assert window.workspace_stack.currentIndex() == 1
    assert window.btn_mode_compress.isChecked()
    assert not window.btn_open.isHidden()
    assert not window.btn_toggle_sidebar.isHidden()
    assert not cp.right_sidebar.isHidden()
    assert cp.stage_stack.currentIndex() == 0
    assert window.btn_undo.isHidden()
    assert window.btn_ai_detect.isHidden()
    assert window.btn_render_export.isHidden()
    assert window.btn_fast_export.isHidden()

    # 載入影片後左區切換成畫面預覽與壓縮前容量、右欄可壓縮並可一鍵收合側欄
    cp.load_file(str(source))
    assert cp.stage_stack.currentIndex() == 1
    assert "壓縮前大小" in cp.lbl_before_size.text()
    assert "MB" in cp.lbl_before_size.text()
    assert cp.btn_start_compress.isEnabled()
    # 小檔案（< 4,300 MB）按鈕不應以橘色主按鈕誘導重壓，且 4 顆預設按鈕皆套用 preset-btn
    assert cp.btn_start_compress.objectName() == ""
    assert "重新編碼" in cp.btn_start_compress.text()
    assert cp.btn_preset_dvd.property("class") == "preset-btn"

    window._toggle_sidebar(False)
    assert cp.right_sidebar.isHidden()
    window._toggle_sidebar(True)
    assert not cp.right_sidebar.isHidden()

    # 切回 mask 分頁即恢復完整編輯介面
    window.set_work_mode("mask")
    assert window.workspace_stack.currentIndex() == 0
    assert not window.btn_open.isHidden()
    assert not window.btn_ai_detect.isHidden()
    window.close()


def test_transcribe_step_guide_and_subtitle_double_click_edit(qapp, monkeypatch):
    from clipmask.ai.subtitles import SubtitleItem
    window = MainWindow()
    window.video_source = SimpleNamespace(
        current_time=1.0, current_pts=10, duration=10.0, time_base=Fraction(1, 10), close=lambda: None
    )
    window.seek_to = lambda seconds: None
    window.project.work_range = WorkRange(0.0, 10.0)
    window.project.subtitles = [SubtitleItem(id=1, start_sec=1.0, end_sec=3.0, text="原始錯字")]
    window._refresh_sub_list()

    window.set_work_mode("transcribe")
    assert "AI 偵測" not in window.lbl_step_guide.text()
    assert "字幕" in window.lbl_step_guide.text()

    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("修正後字幕", True))
    window._edit_subtitle_item(window.sub_list.item(0))
    assert window.project.subtitles[0].text == "修正後字幕"

    window.undo()
    assert window.project.subtitles[0].text == "原始錯字"
    window.redo()
    assert window.project.subtitles[0].text == "修正後字幕"
    window.close()


def test_drop_blocked_while_busy_or_compressing(qapp, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtGui import QDragEnterEvent
    from PySide6.QtCore import QPoint

    window = MainWindow()
    dummy = tmp_path / "other.mp4"
    dummy.write_bytes(b"00")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(dummy))])

    window._show_busy_overlay("處理中", "正在匯出...")
    ev = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dragEnterEvent(ev)
    assert not ev.isAccepted()
    window._hide_busy_overlay()

    window.compress_page._is_compressing = lambda: True
    ev2 = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.compress_page.dragEnterEvent(ev2)
    assert not ev2.isAccepted()
    window.close()
