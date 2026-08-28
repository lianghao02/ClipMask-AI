"""
ClipMask-AI Pro Timeline Widget
專業多軌視覺化時間軸：
1. Work Range 亮藍色半透明選取區間
2. 關鍵影格鑽石 (Keyframe Diamonds 🔷) 視覺化標記
3. 關鍵影格導航 (Previous / Next Keyframe Jump) 與單點增刪
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPolygonF, QFont
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from typing import List, Optional

class TimelineTrackCanvas(QWidget):
    """自訂繪製時間軸軌道、Work Range 與關鍵影格鑽石"""
    seek_requested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(38)
        self.setMouseTracking(True)
        self.duration = 0.0
        self.current_time = 0.0
        self.in_time = 0.0
        self.out_time = 0.0
        self.keyframe_times: List[float] = []
        self.is_dragging = False

    def update_state(self, current_time: float, duration: float, in_time: float, out_time: float, keyframe_times: List[float]):
        self.current_time = current_time
        self.duration = max(0.001, duration)
        self.in_time = in_time
        self.out_time = out_time if out_time > in_time else duration
        self.keyframe_times = keyframe_times
        self.update()

    def time_to_x(self, t: float) -> float:
        if self.duration <= 0:
            return 0.0
        return (t / self.duration) * self.width()

    def x_to_time(self, x: float) -> float:
        if self.width() <= 0:
            return 0.0
        pct = max(0.0, min(1.0, x / float(self.width())))
        return pct * self.duration

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()

        # 1. 時間軸背景槽
        painter.fillRect(0, 0, w, h, QColor(26, 28, 34))
        painter.setPen(QPen(QColor(42, 45, 54), 1))
        painter.drawRoundedRect(0, 0, w, h, 4, 4)

        # 2. 繪製 Work Range 亮藍色區間帶
        if self.out_time > self.in_time and self.duration > 0:
            x_in = self.time_to_x(self.in_time)
            x_out = self.time_to_x(self.out_time)
            range_w = max(2.0, x_out - x_in)
            painter.fillRect(QRectF(x_in, 2, range_w, h - 4), QColor(37, 99, 235, 75))
            
            # In/Out 邊界標記線
            painter.setPen(QPen(QColor(96, 165, 250, 220), 2))
            painter.drawLine(int(x_in), 2, int(x_in), h - 2)
            painter.drawLine(int(x_out), 2, int(x_out), h - 2)

        # 3. 繪製刻度線
        painter.setPen(QPen(QColor(55, 60, 72), 1))
        steps = 10
        for i in range(1, steps):
            sx = (w / steps) * i
            painter.drawLine(int(sx), h - 8, int(sx), h - 2)

        # 4. 繪製關鍵影格鑽石 (Keyframe Diamonds 🔷)
        for kf_t in self.keyframe_times:
            kx = self.time_to_x(kf_t)
            ky = h / 2.0
            size = 5.5
            
            # 建立鑽石多邊形 (菱形)
            diamond = QPolygonF([
                QPointF(kx, ky - size),
                QPointF(kx + size, ky),
                QPointF(kx, ky + size),
                QPointF(kx - size, ky)
            ])
            painter.setPen(QPen(QColor(245, 158, 11, 240), 1.5))
            painter.setBrush(QBrush(QColor(251, 191, 36, 230)))
            painter.drawPolygon(diamond)

        # 5. 繪製當前播放時間指針 (Playhead Cursor)
        cx = self.time_to_x(self.current_time)
        painter.setPen(QPen(QColor(239, 68, 68, 255), 2))
        painter.drawLine(int(cx), 0, int(cx), h)
        
        # 指針頂部倒三角形
        tri_size = 4.0
        tri = QPolygonF([
            QPointF(cx - tri_size, 0),
            QPointF(cx + tri_size, 0),
            QPointF(cx, tri_size * 1.5)
        ])
        painter.setBrush(QBrush(QColor(239, 68, 68, 255)))
        painter.drawPolygon(tri)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = True
            t = self.x_to_time(event.position().x())
            self.seek_requested.emit(t)

    def mouseMoveEvent(self, event):
        if self.is_dragging:
            t = self.x_to_time(event.position().x())
            self.seek_requested.emit(t)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = False
            t = self.x_to_time(event.position().x())
            self.seek_requested.emit(t)

class TimelineWidget(QWidget):
    play_toggled = Signal(bool)
    seek_requested = Signal(float)
    step_requested = Signal(int)
    set_in_point = Signal()
    set_out_point = Signal()
    prev_keyframe_requested = Signal()
    next_keyframe_requested = Signal()
    toggle_keyframe_at_current = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 0.0
        self.current_time = 0.0
        self.in_time = 0.0
        self.out_time = 0.0
        self.keyframe_times: List[float] = []
        self.is_playing = False
        
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 4, 10, 6)
        main_layout.setSpacing(6)

        # 1. 專業視覺化時間軸軌道 Canvas
        self.canvas = TimelineTrackCanvas()
        self.canvas.seek_requested.connect(self.seek_requested.emit)
        main_layout.addWidget(self.canvas)

        # 2. 控制列
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(6)

        # 播放控制
        self.btn_step_back = QPushButton("◀ 上一格")
        self.btn_step_back.setToolTip("上一幀 (J)")
        self.btn_step_back.clicked.connect(lambda: self.step_requested.emit(-1))
        ctrl_layout.addWidget(self.btn_step_back)

        self.btn_play = QPushButton("▶ 播放")
        self.btn_play.setStyleSheet("font-weight: bold; padding: 6px 16px; min-width: 60px;")
        self.btn_play.clicked.connect(self._toggle_play)
        ctrl_layout.addWidget(self.btn_play)

        self.btn_step_fwd = QPushButton("下一格 ▶")
        self.btn_step_fwd.setToolTip("下一幀 (L)")
        self.btn_step_fwd.clicked.connect(lambda: self.step_requested.emit(1))
        ctrl_layout.addWidget(self.btn_step_fwd)

        ctrl_layout.addSpacing(10)

        # 關鍵影格導航群組 (Keyframe Pro Jumper)
        self.btn_prev_kf = QPushButton("⏮ 上一個 🔷")
        self.btn_prev_kf.setToolTip("跳至上一個關鍵影格 ([)")
        self.btn_prev_kf.clicked.connect(self.prev_keyframe_requested.emit)
        ctrl_layout.addWidget(self.btn_prev_kf)

        self.btn_toggle_kf = QPushButton("🔷 打上/移除影格")
        self.btn_toggle_kf.setToolTip("在當前秒數新增或刪除關鍵影格 (K)")
        self.btn_toggle_kf.setStyleSheet("color: #f59e0b; font-weight: bold;")
        self.btn_toggle_kf.clicked.connect(self.toggle_keyframe_at_current.emit)
        ctrl_layout.addWidget(self.btn_toggle_kf)

        self.btn_next_kf = QPushButton("🔷 下一個 ⏭")
        self.btn_next_kf.setToolTip("跳至下一個關鍵影格 (])")
        self.btn_next_kf.clicked.connect(self.next_keyframe_requested.emit)
        ctrl_layout.addWidget(self.btn_next_kf)

        ctrl_layout.addSpacing(10)

        # Work Range 設定
        self.btn_in = QPushButton("[ 設定起點 (I)")
        self.btn_in.clicked.connect(self.set_in_point.emit)
        ctrl_layout.addWidget(self.btn_in)

        self.btn_out = QPushButton("設定終點 (O) ]")
        self.btn_out.clicked.connect(self.set_out_point.emit)
        ctrl_layout.addWidget(self.btn_out)

        ctrl_layout.addStretch()

        # 時間碼顯示
        self.lbl_time = QLabel("00:00:00.000 / 00:00:00.000")
        self.lbl_time.setStyleSheet("font-family: Consolas, monospace; font-size: 13px; font-weight: bold; color: #60a5fa;")
        ctrl_layout.addWidget(self.lbl_time)

        main_layout.addLayout(ctrl_layout)

    def set_duration(self, duration: float):
        self.duration = max(0.0, duration)
        self.in_time = 0.0
        self.out_time = duration
        self.update_state(0.0, self.in_time, self.out_time, [])

    def update_state(self, current_time: float, in_time: float, out_time: float, keyframe_times: List[float]):
        self.current_time = current_time
        self.in_time = in_time
        self.out_time = out_time
        self.keyframe_times = keyframe_times
        
        self.canvas.update_state(current_time, self.duration, in_time, out_time, keyframe_times)
        
        cur_str = self._format_time(self.current_time)
        dur_str = self._format_time(self.duration)
        self.lbl_time.setText(f"{cur_str} / {dur_str}")

    def update_time_display(self, current_time: float):
        self.update_state(current_time, self.in_time, self.out_time, self.keyframe_times)

    def _format_time(self, seconds: float) -> str:
        ms = int((seconds - int(seconds)) * 1000)
        s = int(seconds) % 60
        m = (int(seconds) // 60) % 60
        h = int(seconds) // 3600
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

    def _toggle_play(self):
        self.is_playing = not self.is_playing
        self.btn_play.setText("⏸ 暫停" if self.is_playing else "▶ 播放")
        self.play_toggled.emit(self.is_playing)

    def set_playing_state(self, playing: bool):
        self.is_playing = playing
        self.btn_play.setText("⏸ 暫停" if self.is_playing else "▶ 播放")
