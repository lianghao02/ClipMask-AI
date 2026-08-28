import os
import pytest
import numpy as np
import cv2
import av
from clipmask.ai.vad import VoiceActivityDetector, SpeechSegment

def test_vad_audio_scanning(tmp_path):
    # 產生帶有正弦波聲音 (模擬語音) 的測試影片
    video_path = str(tmp_path / "audio_test.mp4")
    
    container = av.open(video_path, mode="w")
    v_stream = container.add_stream("libx264", rate=30)
    v_stream.width = 320
    v_stream.height = 240
    v_stream.pix_fmt = "yuv420p"

    a_stream = container.add_stream("aac", rate=16000)
    a_stream.layout = "mono"

    # 建立 2 秒靜音 + 2 秒正弦波聲音 + 1 秒靜音
    total_frames = 150 # 5秒 @ 30fps
    for i in range(total_frames):
        img = np.zeros((240, 320, 3), dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(img, format="bgr24")
        for packet in v_stream.encode(frame):
            container.mux(packet)

    # 寫入音訊
    sample_rate = 16000
    duration_s = 5
    t = np.linspace(0, duration_s, sample_rate * duration_s, endpoint=False)
    # 前2秒靜音，2~4秒為440Hz音，4~5秒靜音
    audio_signal = np.zeros_like(t)
    speech_mask = (t >= 2.0) & (t <= 4.0)
    audio_signal[speech_mask] = 0.5 * np.sin(2 * np.pi * 440 * t[speech_mask])
    audio_signal = audio_signal.astype(np.float32)

    frame_size = 1024
    for i in range(0, len(audio_signal), frame_size):
        chunk = audio_signal[i : i + frame_size]
        if len(chunk) < frame_size:
            chunk = np.pad(chunk, (0, frame_size - len(chunk)))
        a_frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="fltp", layout="mono")
        a_frame.sample_rate = 16000
        for packet in a_stream.encode(a_frame):
            container.mux(packet)

    for packet in v_stream.encode():
        container.mux(packet)
    for packet in a_stream.encode():
        container.mux(packet)
    container.close()

    # 執行 VAD 掃描
    segments = VoiceActivityDetector.scan_audio_speech_segments(video_path)
    assert len(segments) >= 1
    # 驗證語音區間大約落在 2.0s ~ 4.0s
    found = False
    for seg in segments:
        if abs(seg.start_sec - 2.0) < 0.6 and abs(seg.end_sec - 4.0) < 0.6:
            found = True
            break
    assert found

    # 測試磁吸判定
    matched = VoiceActivityDetector.find_current_speech_segment(segments, 2.5)
    assert matched is not None
