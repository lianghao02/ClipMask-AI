"""
ClipMask-AI Timeline Controller Widget
包含進度滑桿、Work Range 標記、Play/Pause、Step ±1 幀、時間碼顯示。
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QSlider, QLabel, QStyle
from PySide6.QtCore import Qt, Signal, QTime

class TimelineWidget(QWidget):
    # 信號
    play_toggled = Signal(bool)
    seek_requested = Signal(float)  # 秒數
    step_requested = Signal(int)     # -1 或 +1 幀
    set_in_point = Signal()
    set_out_point = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 0.0
        self.current_time = 0.0
        self.is_playing = False
        self.is_user_scrubbing = False
        
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 5, 10, 5)
        main_layout.setSpacing(5)

        # 1. 時間軸滑桿
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 10000)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.slider.sliderReleased.connect(self._on_slider_released)
        main_layout.addWidget(self.slider)

        # 2. 控制按鈕列
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(8)

        # Step -1
        self.btn_step_back = QPushButton("◀ 上一幀 (J)")
        self.btn_step_back.clicked.connect(lambda: self.step_requested.emit(-1))
        ctrl_layout.addWidget(self.btn_step_back)

        # Play / Pause
        self.btn_play = QPushButton("▶ 播放 (Space)")
        self.btn_play.setStyleSheet("font-weight: bold; padding: 4px 12px;")
        self.btn_play.clicked.connect(self._toggle_play)
        ctrl_layout.addWidget(self.btn_play)

        # Step +1
        self.btn_step_fwd = QPushButton("下一幀 (L) ▶")
        self.btn_step_fwd.clicked.connect(lambda: self.step_requested.emit(1))
        ctrl_layout.addWidget(self.btn_step_fwd)

        ctrl_layout.addSpacing(15)

        # In / Out 標記
        self.btn_in = QPushButton("[ 設定起點 (I)")
        self.btn_in.clicked.connect(self.set_in_point.emit)
        ctrl_layout.addWidget(self.btn_in)

        self.btn_out = QPushButton("設定終點 (O) ]")
        self.btn_out.clicked.connect(self.set_out_point.emit)
        ctrl_layout.addWidget(self.btn_out)

        ctrl_layout.addStretch()

        # 時間標籤
        self.lbl_time = QLabel("00:00:00.000 / 00:00:00.000")
        self.lbl_time.setStyleSheet("font-family: monospace; font-size: 13px; font-weight: bold;")
        ctrl_layout.addWidget(self.lbl_time)

        main_layout.addLayout(ctrl_layout)

    def set_duration(self, duration: float):
        self.duration = max(0.0, duration)
        self.update_time_display(0.0)

    def update_time_display(self, current_time: float):
        self.current_time = current_time
        if not self.is_user_scrubbing and self.duration > 0:
            val = int((current_time / self.duration) * 10000)
            self.slider.blockSignals(True)
            self.slider.setValue(val)
            self.slider.blockSignals(False)
            
        cur_str = self._format_time(self.current_time)
        dur_str = self._format_time(self.duration)
        self.lbl_time.setText(f"{cur_str} / {dur_str}")

    def _format_time(self, seconds: float) -> str:
        ms = int((seconds - int(seconds)) * 1000)
        s = int(seconds) % 60
        m = (int(seconds) // 60) % 60
        h = int(seconds) // 3600
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

    def _toggle_play(self):
        self.is_playing = not self.is_playing
        self.btn_play.setText("⏸ 暫停 (Space)" if self.is_playing else "▶ 播放 (Space)")
        self.play_toggled.emit(self.is_playing)

    def set_playing_state(self, playing: bool):
        self.is_playing = playing
        self.btn_play.setText("⏸ 暫停 (Space)" if self.is_playing else "▶ 播放 (Space)")

    def _on_slider_pressed(self):
        self.is_user_scrubbing = True

    def _on_slider_moved(self, value):
        if self.duration > 0:
            target_time = (value / 10000.0) * self.duration
            self.update_time_display(target_time)
            self.seek_requested.emit(target_time)

    def _on_slider_released(self):
        self.is_user_scrubbing = False
        val = self.slider.value()
        if self.duration > 0:
            target_time = (val / 10000.0) * self.duration
            self.seek_requested.emit(target_time)
