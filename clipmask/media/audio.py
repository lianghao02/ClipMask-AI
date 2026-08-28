"""
ClipMask-AI 原生音訊播放模組 (Qt Native QAudioSink Engine)
100% 使用 PySide6.QtMultimedia 原生驅動，與 QThread 完美原生綁定，零底層崩潰風險。
"""
import numpy as np
from PySide6.QtCore import QByteArray, QIODevice
from PySide6.QtMultimedia import QAudioSink, QAudioFormat, QMediaDevices

class AudioPlaybackEngine:
    def __init__(self, sample_rate: int = 44100, channels: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        self.sink = None
        self.io_device = None
        self._is_active = False
        self._init_sink()

    def _init_sink(self):
        try:
            device = QMediaDevices.defaultAudioOutput()
            fmt = QAudioFormat()
            fmt.setSampleRate(self.sample_rate)
            fmt.setChannelCount(self.channels)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

            if not device.isFormatSupported(fmt):
                fmt = device.preferredFormat()

            self.sink = QAudioSink(device, fmt)
            self.sink.setBufferSize(self.sample_rate * self.channels * 2 // 4)  # 250ms 緩衝
            self.io_device = self.sink.start()
            self._is_active = (self.io_device is not None)
        except Exception:
            self.sink = None
            self.io_device = None
            self._is_active = False

    def write(self, audio_data: np.ndarray):
        """推送音訊陣列 (shape: [N, channels], int16 或 float32)"""
        if not self._is_active or self.io_device is None:
            return
        try:
            # 轉換為 Int16 PCM 二進位
            if audio_data.dtype != np.int16:
                if np.issubdtype(audio_data.dtype, np.floating):
                    pcm_int16 = (np.clip(audio_data, -1.0, 1.0) * 32767).astype(np.int16)
                else:
                    pcm_int16 = audio_data.astype(np.int16)
            else:
                pcm_int16 = audio_data

            raw_bytes = pcm_int16.tobytes()
            self.io_device.write(raw_bytes)
        except Exception:
            pass

    def stop(self):
        if self.sink is not None:
            try:
                self.sink.stop()
                self.sink.reset()
            except Exception:
                pass
            self.sink = None
            self.io_device = None
        self._is_active = False

    def close(self):
        self.stop()

