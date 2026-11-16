import pytest
import numpy as np
import os
import av
from clipmask.models.project import Track, Keyframe, MaskConfig
from clipmask.media.source import VideoSource
from clipmask.track.tracker import MicroTracker

def test_micro_tracker_forward(tmp_path):
    video_file = str(tmp_path / "moving_box.mp4")
    # 建立 30 幀測試影片 (1 秒，30fps)，黑色方塊從 (10, 10) 往右移動到 (40, 10)
    container = av.open(video_file, mode="w")
    stream = container.add_stream("h264", rate=30)
    stream.width = 160
    stream.height = 120
    stream.pix_fmt = "yuv420p"
    
    for i in range(30):
        img = np.full((120, 160, 3), 255, dtype=np.uint8)
        pos_x = 10 + i  # 每格向右移動 1 像素
        img[10:30, pos_x:pos_x+20] = 0
        frame = av.VideoFrame.from_ndarray(img, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()

    source = VideoSource(video_file)
    track = Track(
        id="t1",
        label="移動方塊",
        keyframes=[Keyframe(time=0.0, rect_px=(10, 10, 20, 20))]
    )
    
    # 向後追蹤 0.5 秒 (15 幀)
    ok = MicroTracker.track_forward(source, track, duration_sec=0.5)
    assert ok is True
    # 應產生第二個 Keyframe
    assert len(track.keyframes) == 2
    final_kf = track.keyframes[1]
    assert final_kf.source == "tracker"
    # 終點 X 座標應有明顯往右移動 (> 10)
    assert final_kf.rect_px[0] > 15
    source.close()


def _make_moving_box_video(video_file: str, num_frames: int = 60, width: int = 240, height: int = 120):
    container = av.open(video_file, mode="w")
    stream = container.add_stream("h264", rate=30)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"

    for i in range(num_frames):
        img = np.full((height, width, 3), 240, dtype=np.uint8)
        pos_x = 12 + i * 2
        if pos_x < width:
            x2 = min(width, pos_x + 24)
            img[40:64, pos_x:x2] = 15
        frame = av.VideoFrame.from_ndarray(img, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def test_track_target_long_sequence_and_cancel(tmp_path):
    video_file = str(tmp_path / "long_move.mp4")
    _make_moving_box_video(video_file, num_frames=60, width=240, height=120)

    source = VideoSource(video_file)
    track = Track(
        id="t_target",
        label="單一鎖定目標",
        keyframes=[Keyframe(time=0.0, rect_px=(12, 40, 24, 24))],
    )

    result = MicroTracker.track_target(
        source,
        track,
        start_time=0.0,
        end_time=1.8,
        keyframe_interval=0.25,
        use_face_assist=False,
    )
    assert result.success is True
    assert len(track.keyframes) >= 6
    assert track.keyframes[-1].rect_px[0] > 60
    assert track.keyframes[-1].source == "tracker"

    # 測試中途取消
    cancel_track = Track(
        id="t_cancel",
        label="取消測試",
        keyframes=[Keyframe(time=0.0, rect_px=(12, 40, 24, 24))],
    )
    cancelled_res = MicroTracker.track_target(
        source,
        cancel_track,
        start_time=0.0,
        end_time=1.8,
        use_face_assist=False,
        is_cancelled=lambda: True,
    )
    assert cancelled_res.success is False
    assert cancelled_res.lost_reason == "cancelled"
    source.close()
