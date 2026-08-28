import pytest
import numpy as np
import os
import av
from clipmask.models.project import ProjectState, Track, Keyframe, MaskConfig, WorkRange, SourceMetadata
from clipmask.track.evaluator import TrackEvaluator
from clipmask.media.source import VideoSource

def test_project_state_json_roundtrip():
    state = ProjectState(
        source=SourceMetadata(path="test.mp4", width=1920, height=1080, fps=30.0, duration=60.0),
        work_range=WorkRange(in_time=10.0, out_time=20.0),
        tracks=[
            Track(
                id="t1",
                label="人臉",
                type="face",
                mask=MaskConfig(style="mosaic", strength=20, padding=0.2),
                keyframes=[
                    Keyframe(time=10.0, pts=900000, rect_px=(100, 100, 50, 50)),
                    Keyframe(time=12.0, pts=1080000, rect_px=(200, 200, 60, 60))
                ]
            )
        ]
    )
    json_str = state.to_json()
    restored = ProjectState.from_json(json_str)
    
    assert restored.source.width == 1920
    assert restored.work_range.in_time == 10.0
    assert len(restored.tracks) == 1
    assert restored.tracks[0].mask.padding == 0.2
    assert len(restored.tracks[0].keyframes) == 2
    assert restored.tracks[0].keyframes[1].rect_px == [200, 200, 60, 60]

def test_track_evaluator_lerp_and_padding():
    # 建立 10.0s -> 20.0s 的 Track
    # 10.0s: (100, 100, 100, 100)
    # 20.0s: (200, 200, 100, 100)
    # padding: 0.1 (四周各 10%)
    track = Track(
        id="t1",
        mask=MaskConfig(padding=0.1),
        keyframes=[
            Keyframe(time=10.0, rect_px=(100, 100, 100, 100)),
            Keyframe(time=20.0, rect_px=(200, 200, 100, 100))
        ]
    )
    
    # 測試 t = 15.0s (正中間): 原始 (150, 150, 100, 100)
    # 加上 10% padding: x=150 - 10 = 140, y=150 - 10 = 140, w=100 + 20 = 120, h=100 + 20 = 120
    rect = TrackEvaluator.evaluate_track_at(track, current_time=15.0, video_w=1920, video_h=1080)
    assert rect == (140, 140, 120, 120)
    
    # 測試超出範圍
    assert TrackEvaluator.evaluate_track_at(track, current_time=5.0, video_w=1920, video_h=1080) is None
    assert TrackEvaluator.evaluate_track_at(track, current_time=25.0, video_w=1920, video_h=1080) is None

def test_padding_clamp_boundaries():
    # 測試靠近左上角超出
    rect = (5, 5, 50, 50)
    clamped = TrackEvaluator.apply_padding_and_clamp(rect, padding=0.2, video_w=100, video_h=100)
    # pad_w = 10, pad_h = 10 -> nx = -5, ny = -5 -> clamped x=0, y=0, w=70, h=70
    assert clamped[0] == 0
    assert clamped[1] == 0
    assert clamped[2] == 70
    assert clamped[3] == 70

def test_video_source_with_synthetic_video(tmp_path):
    video_file = str(tmp_path / "test.mp4")
    # 用 PyAV 建立 30 幀的測試影片 (1秒，30fps，320x240)
    container = av.open(video_file, mode="w")
    stream = container.add_stream("h264", rate=30)
    stream.width = 320
    stream.height = 240
    stream.pix_fmt = "yuv420p"
    
    for i in range(30):
        # 建立純色漸層畫面
        img = np.full((240, 320, 3), fill_value=i * 8, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(img, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    
    # 驗證 VideoSource 讀取與 Seek
    source = VideoSource(video_file)
    assert source.width == 320
    assert source.height == 240
    assert source.fps == 30.0
    
    frame_05 = source.seek_exact(0.5)
    assert frame_05 is not None
    assert frame_05.shape == (240, 320, 3)
    source.close()
