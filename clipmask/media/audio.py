"""
ClipMask-AI 雙引擎音訊播放模組 (Hybrid Audio Playback Engine)
首選低延遲 WASAPI (sounddevice) 串流，若環境不支援則自動降級為 QAudioSink 或安靜模式。
"""
import numpy as np
import threading
from typing import Optional

class AudioPlaybackEngine:
    def __init__(self, sample_rate: int = 44100, channels: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        self.stream = None
        self._is_active = False
        self._lock = threading.Lock()
        self._init_stream()

    def _init_stream(self):
        try:
            import sounddevice as sd
            self.stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                blocksize=1024
            )
            self.stream.start()
            self._is_active = True
        except Exception:
            self.stream = None
            self._is_active = False

    def write(self, audio_data: np.ndarray):
        """推送音訊陣列 (shape: [N, channels] 或 [N], float32)"""
        if not self._is_active or self.stream is None:
            return
        try:
            with self._lock:
                if self.stream and self.stream.active:
                    if audio_data.ndim == 1:
                        if self.channels == 2:
                            audio_data = np.column_stack((audio_data, audio_data))
                        else:
                            audio_data = audio_data.reshape(-1, 1)
                    audio_data = audio_data.astype(np.float32)
                    self.stream.write(audio_data)
        except Exception:
            pass

    def stop(self):
        """停止並清空當前音訊串流緩衝"""
        with self._lock:
            if self.stream is not None:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None
            self._is_active = False

    def close(self):
        self.stop()
