"""
ClipMask-AI 純聲學人聲活動偵測 (Voice Activity Detection - VAD)
利用 PyAV 讀取音訊串流並計算短時 RMS 能量與動態閾值，極速提取語音區間 (秒級起訖)。
"""
from dataclasses import dataclass
from typing import List, Tuple, Optional
import av
import numpy as np
import os

@dataclass
class SpeechSegment:
    start_sec: float
    end_sec: float

class VoiceActivityDetector:
    @staticmethod
    def scan_audio_speech_segments(
        video_path: str,
        frame_duration_ms: int = 50,
        min_speech_duration_ms: int = 250,
        min_silence_duration_ms: int = 300
    ) -> List[SpeechSegment]:
        if not os.path.exists(video_path):
            return []

        try:
            container = av.open(video_path)
            if not container.streams.audio:
                container.close()
                return []

            audio_stream = container.streams.audio[0]
            # 重新取樣為 16000Hz 單聲道
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)

            samples_list = []
            for frame in container.decode(audio_stream):
                resampled_frames = resampler.resample(frame)
                for rf in resampled_frames:
                    arr = rf.to_ndarray()[0]  # shape: (N,)
                    samples_list.append(arr)
            container.close()

            if not samples_list:
                return []

            audio_data = np.concatenate(samples_list)
            sample_rate = 16000

            # 依 50ms 計算 RMS 能量
            samples_per_window = int(sample_rate * (frame_duration_ms / 1000.0))
            num_windows = len(audio_data) // samples_per_window
            if num_windows == 0:
                return []

            rms_values = []
            for i in range(num_windows):
                window = audio_data[i * samples_per_window : (i + 1) * samples_per_window]
                rms = np.sqrt(np.mean(window ** 2) + 1e-9)
                rms_values.append(rms)

            rms_arr = np.array(rms_values)
            # 動態能量門檻 (中位數 + 0.35 * 標準差)
            threshold = float(np.median(rms_arr) + 0.35 * np.std(rms_arr))
            threshold = max(0.008, threshold)

            is_speech = rms_arr > threshold

            # 合併語音區間
            segments: List[SpeechSegment] = []
            in_speech = False
            start_idx = 0
            silence_counter = 0
            max_silence_windows = int(min_silence_duration_ms / frame_duration_ms)

            for i, active in enumerate(is_speech):
                if active:
                    if not in_speech:
                        in_speech = True
                        start_idx = i
                    silence_counter = 0
                else:
                    if in_speech:
                        silence_counter += 1
                        if silence_counter >= max_silence_windows:
                            end_idx = i - silence_counter
                            dur_ms = (end_idx - start_idx) * frame_duration_ms
                            if dur_ms >= min_speech_duration_ms:
                                s_sec = max(0.0, (start_idx * frame_duration_ms) / 1000.0 - 0.1)
                                e_sec = (end_idx * frame_duration_ms) / 1000.0 + 0.15
                                segments.append(SpeechSegment(start_sec=s_sec, end_sec=e_sec))
                            in_speech = False
                            silence_counter = 0

            # 收尾未結束的區間
            if in_speech:
                s_sec = max(0.0, (start_idx * frame_duration_ms) / 1000.0 - 0.1)
                e_sec = (num_windows * frame_duration_ms) / 1000.0
                segments.append(SpeechSegment(start_sec=s_sec, end_sec=e_sec))

            return segments

        except Exception:
            return []

    @staticmethod
    def find_current_speech_segment(segments: List[SpeechSegment], current_time: float, tolerance: float = 0.5) -> Optional[SpeechSegment]:
        for seg in segments:
            if seg.start_sec - tolerance <= current_time <= seg.end_sec + tolerance:
                return seg
        return None
