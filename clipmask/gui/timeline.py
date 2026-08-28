"""
ClipMask-AI Pro Timeline Widget (支援滑鼠拖拉選取 Work Range 區間)
- 滑鼠右鍵拖拉 / Shift+左鍵拖拉：直接拉出 Work Range 剪輯區間
- 左右點擊：精確跳轉播放時間
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QFrame, QLineEdit
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPolygonF, QMouseEvent, QImage, QPixmap
from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QPoint
from typing import List, Optional
from collections import OrderedDict
import time
import numpy as np

class HoverThumbnailPopup(QFrame):
    """時間軸懸浮小縮圖視窗 (YouTube/Premiere 模式)"""
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(168, 118)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # 縮圖容器
        self.lbl_thumb = QLabel()
        self.lbl_thumb.setFixedSize(160, 90)
        self.lbl_thumb.setStyleSheet("background-color: #1a1a1a; border-radius: 4px;")
        self.lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_thumb)

        # 時間標籤
        self.lbl_time = QLabel("00:00:00.000")
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_time.setStyleSheet("color: #ffffff; font-family: Consolas, monospace; font-size: 11px; font-weight: bold; background: rgba(0,0,0,160); border-radius: 3px; padding: 1px;")
        layout.addWidget(self.lbl_time)

    def set_content(self, rgb_frame: Optional[np.ndarray], time_str: str):
        self.lbl_time.setText(time_str)
        if rgb_frame is not None:
            h, w = rgb_frame.shape[:2]
            qimg = QImage(rgb_frame.data, w, h, 3 * w, QImage.Format.Format_RGB888)
            self.lbl_thumb.setPixmap(QPixmap.fromImage(qimg))
        else:
            self.lbl_thumb.setText("載入中...")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QBrush(QColor(30, 32, 35, 235)))
        painter.setPen(QPen(QColor(180, 160, 140, 180), 1))
        painter.drawRoundedRect(0, 0, self.width(), self.height(), 6, 6)

class TimelineTrackCanvas(QWidget):
    seek_started = Signal()
    seek_requested = Signal(float)
    seek_fast_requested = Signal(float)
    range_selected = Signal(float, float)  # (in_time, out_time)
    hover_requested = Signal(float, QPoint) # (hover_time, global_pos)
    hover_leave = Signal()
    subtitle_selected = Signal(int)  # sub_id
    subtitle_range_adjusted = Signal(int, float, float)  # sub_id, new_start, new_end

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self.setMouseTracking(True)
        self.duration = 0.0
        self.current_time = 0.0
        self.in_time = 0.0
        self.out_time = 0.0
        self.keyframe_times: List[float] = []
        self.speech_segments: list = []  # [(start_sec, end_sec), ...]
        self.subtitles: list = []  # SubtitleItem list
        self.selected_sub_id: int = -1
        self.uncovered_ranges: list = []  # [(start_sec, end_sec), ...]
        
        # 拖拉狀態
        self.is_seeking = False
        self.is_selecting_range = False
        self.drag_start_time = 0.0
        self.temp_drag_time = 0.0
        
        # 字幕手柄拖拉狀態
        self.drag_sub_mode = None  # None, "left", "right", "body"
        self.dragging_sub_id = -1
        self.drag_sub_orig_start = 0.0
        self.drag_sub_orig_end = 0.0
        self.drag_sub_anchor_time = 0.0

    def update_state(self, current_time: float, duration: float, in_time: float, out_time: float, keyframe_times: List[float], speech_segments: list = None, subtitles: list = None, selected_sub_id: int = -1, uncovered_ranges: list = None):
        self.current_time = current_time
        self.duration = max(0.001, duration)
        self.in_time = in_time
        self.out_time = out_time if out_time > in_time else duration
        self.keyframe_times = keyframe_times
        if speech_segments is not None:
            self.speech_segments = speech_segments
        if subtitles is not None:
            self.subtitles = subtitles
        self.selected_sub_id = selected_sub_id
        if uncovered_ranges is not None:
            self.uncovered_ranges = uncovered_ranges
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

        # 1. 溫潤紙質底槽
        painter.fillRect(0, 0, w, h, QColor(240, 237, 230))
        painter.setPen(QPen(QColor(220, 215, 204), 1.5))
        painter.drawRoundedRect(0, 0, w, h, 6, 6)

        # 2. 繪製語音聲波活動區間 (頂部鼠尾草綠人聲條 🌿)
        for seg in self.speech_segments:
            s_t = seg.start_sec if hasattr(seg, 'start_sec') else seg[0]
            e_t = seg.end_sec if hasattr(seg, 'end_sec') else seg[1]
            if e_t > s_t:
                sx = self.time_to_x(s_t)
                ex = self.time_to_x(e_t)
                seg_w = max(3.0, ex - sx)
                painter.fillRect(QRectF(sx, 1, seg_w, 5), QColor(95, 135, 104, 210))

        # 3. 繪製未覆蓋安全警示細線 (頂部柔和陶土紅 ⚠️)
        for ur_start, ur_end in self.uncovered_ranges:
            if ur_end > ur_start:
                ux1 = self.time_to_x(ur_start)
                ux2 = self.time_to_x(ur_end)
                painter.fillRect(QRectF(ux1, 0, max(2.0, ux2 - ux1), 2), QColor(215, 85, 65, 220))

        # 4. 繪製聽打字幕區段 (底部莫蘭迪紫丁香色條 🎙️)
        for sub in self.subtitles:
            s_t = sub.start_sec
            e_t = sub.end_sec
            if e_t > s_t:
                sx = self.time_to_x(s_t)
                ex = self.time_to_x(e_t)
                sub_w = max(5.0, ex - sx)
                is_selected = (sub.id == self.selected_sub_id)
                
                if is_selected:
                    # 選取中：高亮明亮紫粉色 + 左右手柄外框
                    painter.fillRect(QRectF(sx, h - 10, sub_w, 8), QColor(165, 135, 185, 230))
                    painter.setPen(QPen(QColor(130, 95, 155, 255), 1.5))
                    painter.drawRect(QRectF(sx, h - 10, sub_w, 8))
                    # 左右手柄標記 [ ]
                    painter.setPen(QPen(QColor(255, 255, 255, 240), 2))
                    painter.drawLine(int(sx + 1), h - 9, int(sx + 1), h - 3)
                    painter.drawLine(int(ex - 1), h - 9, int(ex - 1), h - 3)
                else:
                    # 一般狀態：柔和莫蘭迪灰紫色
                    painter.fillRect(QRectF(sx, h - 7, sub_w, 5), QColor(154, 144, 168, 180))

        # 5. 繪製 Work Range 區間帶 (或正在拖拉中的臨時區間)
        display_in = self.in_time
        display_out = self.out_time
        
        if self.is_selecting_range:
            display_in = min(self.drag_start_time, self.temp_drag_time)
            display_out = max(self.drag_start_time, self.temp_drag_time)

        if display_out > display_in and self.duration > 0:
            x_in = self.time_to_x(display_in)
            x_out = self.time_to_x(display_out)
            range_w = max(2.0, x_out - x_in)
            painter.fillRect(QRectF(x_in, 3, range_w, h - 6), QColor(92, 124, 153, 75))
            
            # 手帳紙膠帶感邊界
            painter.setPen(QPen(QColor(92, 124, 153, 230), 2))
            painter.drawLine(int(x_in), 3, int(x_in), h - 3)
            painter.drawLine(int(x_out), 3, int(x_out), h - 3)

        # 6. 刻度線
        painter.setPen(QPen(QColor(198, 192, 180), 1))
        steps = 10
        for i in range(1, steps):
            sx = (w / steps) * i
            painter.drawLine(int(sx), h - 14, int(sx), h - 9)

        # 7. 關鍵影格鑽石 (芥末暖黃 🔷)
        for kf_t in self.keyframe_times:
            kx = self.time_to_x(kf_t)
            ky = h / 2.0
            size = 6.0
            
            diamond = QPolygonF([
                QPointF(kx, ky - size),
                QPointF(kx + size, ky),
                QPointF(kx, ky + size),
                QPointF(kx - size, ky)
            ])
            painter.setPen(QPen(QColor(180, 130, 40, 240), 1.5))
            painter.setBrush(QBrush(QColor(235, 175, 55, 230)))
            painter.drawPolygon(diamond)

        # 8. 當前播放時間指針 (陶土紅)
        cx = self.time_to_x(self.current_time)
        painter.setPen(QPen(QColor(201, 122, 99, 255), 2.5))
        painter.drawLine(int(cx), 0, int(cx), h)
        
        tri_size = 4.5
        tri = QPolygonF([
            QPointF(cx - tri_size, 0),
            QPointF(cx + tri_size, 0),
            QPointF(cx, tri_size * 1.6)
        ])
        painter.setBrush(QBrush(QColor(201, 122, 99, 255)))
        painter.drawPolygon(tri)

    def _find_sub_hit(self, pos_x: float, pos_y: float):
        """判斷是否點擊到字幕色塊，回傳 (sub, mode: 'left'|'right'|'body'|None)"""
        h = self.height()
        if pos_y < h - 14:
            return None, None
        for sub in self.subtitles:
            sx = self.time_to_x(sub.start_sec)
            ex = self.time_to_x(sub.end_sec)
            if sx - 4 <= pos_x <= ex + 4:
                if abs(pos_x - sx) <= 5:
                    return sub, "left"
                elif abs(pos_x - ex) <= 5:
                    return sub, "right"
                else:
                    return sub, "body"
        return None, None

    def mousePressEvent(self, event: QMouseEvent):
        self.hover_leave.emit()
        pos_x = event.position().x()
        pos_y = event.position().y()

        # 檢查是否點擊字幕色塊或拖拉左右手柄
        if event.button() == Qt.MouseButton.LeftButton:
            hit_sub, hit_mode = self._find_sub_hit(pos_x, pos_y)
            if hit_sub:
                self.drag_sub_mode = hit_mode
                self.dragging_sub_id = hit_sub.id
                self.drag_sub_orig_start = hit_sub.start_sec
                self.drag_sub_orig_end = hit_sub.end_sec
                self.drag_sub_anchor_time = self.x_to_time(pos_x)
                self.subtitle_selected.emit(hit_sub.id)
                self.update()
                return

        # 右鍵拖拉 或 Shift+左鍵拖拉：選取剪輯區間
        if event.button() == Qt.MouseButton.RightButton or (event.button() == Qt.MouseButton.LeftButton and (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
            self.is_selecting_range = True
            t = self.x_to_time(pos_x)
            self.drag_start_time = t
            self.temp_drag_time = t
            self.update()
        elif event.button() == Qt.MouseButton.LeftButton:
            # 一般左鍵：點擊/拖曳 Seek 時間指針
            self.is_seeking = True
            self.seek_started.emit()
            t = self.x_to_time(pos_x)
            self.seek_fast_requested.emit(t)

    def mouseMoveEvent(self, event: QMouseEvent):
        pos_x = event.position().x()
        pos_y = event.position().y()

        # 處理字幕手柄拖曳
        if self.drag_sub_mode and self.dragging_sub_id != -1:
            cur_t = self.x_to_time(pos_x)
            delta_t = cur_t - self.drag_sub_anchor_time
            
            if self.drag_sub_mode == "left":
                new_start = min(self.drag_sub_orig_end - 0.2, max(0.0, self.drag_sub_orig_start + delta_t))
                self.subtitle_range_adjusted.emit(self.dragging_sub_id, new_start, self.drag_sub_orig_end)
            elif self.drag_sub_mode == "right":
                new_end = max(self.drag_sub_orig_start + 0.2, min(self.duration, self.drag_sub_orig_end + delta_t))
                self.subtitle_range_adjusted.emit(self.dragging_sub_id, self.drag_sub_orig_start, new_end)
            elif self.drag_sub_mode == "body":
                dur = self.drag_sub_orig_end - self.drag_sub_orig_start
                new_start = max(0.0, min(self.duration - dur, self.drag_sub_orig_start + delta_t))
                new_end = new_start + dur
                self.subtitle_range_adjusted.emit(self.dragging_sub_id, new_start, new_end)
            return

        # 滑鼠游標形狀切換 (靠近字幕邊緣顯示 ↔)
        hit_sub, hit_mode = self._find_sub_hit(pos_x, pos_y)
        if hit_mode in ("left", "right"):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif hit_mode == "body":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

        if self.is_selecting_range:
            self.hover_leave.emit()
            self.temp_drag_time = self.x_to_time(pos_x)
            self.update()
        elif self.is_seeking:
            self.hover_leave.emit()
            t = self.x_to_time(pos_x)
            self.seek_fast_requested.emit(t)
        else:
            # 純懸浮 (Hover)：發送縮圖請求
            if self.duration > 0:
                t = self.x_to_time(pos_x)
                global_pos = self.mapToGlobal(event.position().toPoint())
                self.hover_requested.emit(t, global_pos)

    def leaveEvent(self, event):
        self.hover_leave.emit()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self.drag_sub_mode:
            self.drag_sub_mode = None
            self.dragging_sub_id = -1
            self.update()
            return

        if self.is_selecting_range:
            self.is_selecting_range = False
            t_end = self.x_to_time(event.position().x())
            in_t = min(self.drag_start_time, t_end)
            out_t = max(self.drag_start_time, t_end)
            if out_t - in_t >= 0.2:  # 至少 0.2 秒才算有效選取
                self.range_selected.emit(in_t, out_t)
            self.update()
        elif self.is_seeking:
            self.is_seeking = False
            t = self.x_to_time(event.position().x())
            self.seek_requested.emit(t)

class TimelineWidget(QWidget):
    play_toggled = Signal(bool)
    seek_requested = Signal(float)
    seek_fast_requested = Signal(float)
    step_requested = Signal(int)
    set_in_point = Signal()
    set_out_point = Signal()
    reset_range_requested = Signal()
    range_selected = Signal(float, float)
    prev_keyframe_requested = Signal()
    next_keyframe_requested = Signal()
    toggle_keyframe_at_current = Signal()
    seek_started = Signal()
    transcript_submitted = Signal(str)
    subtitle_selected = Signal(int)  # sub_id
    subtitle_range_adjusted = Signal(int, float, float)  # sub_id, new_start, new_end

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 0.0
        self.current_time = 0.0
        self.in_time = 0.0
        self.out_time = 0.0
        self.is_playing = False
        self.thumb_extractor = None
        self.hover_popup = HoverThumbnailPopup(self)
        self._thumbnail_cache = OrderedDict()
        self._last_thumbnail_request = 0.0
        
        self.init_ui()

    def set_thumbnail_extractor(self, extractor):
        self.thumb_extractor = extractor
        self._thumbnail_cache.clear()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 4, 10, 6)
        main_layout.setSpacing(6)

        # 頂部剪輯範圍提示標籤
        self.lbl_range_info = QLabel("✂ 工作區間: 00:00:00.000 ~ 00:00:00.000 (整部影片)")
        self.lbl_range_info.setStyleSheet("font-weight: 600; color: #4a6882; font-size: 11px;")
        main_layout.addWidget(self.lbl_range_info)

        # 時間軸軌道
        self.canvas = TimelineTrackCanvas()
        self.canvas.seek_requested.connect(self.seek_requested.emit)
        self.canvas.seek_started.connect(self.seek_started.emit)
        self.canvas.seek_fast_requested.connect(self.seek_fast_requested.emit)
        self.canvas.range_selected.connect(self.range_selected.emit)
        self.canvas.hover_requested.connect(self._on_canvas_hover)
        self.canvas.hover_leave.connect(self._on_canvas_leave)
        self.canvas.subtitle_selected.connect(self.subtitle_selected.emit)
        self.canvas.subtitle_range_adjusted.connect(self.subtitle_range_adjusted.emit)
        main_layout.addWidget(self.canvas)

        transcript_layout = QHBoxLayout()
        transcript_layout.addWidget(QLabel("🎙 聽打："))
        self.edit_transcript = QLineEdit()
        self.edit_transcript.setPlaceholderText("在此輸入字幕；Enter 新增並自動對齊語音區段")
        self.edit_transcript.returnPressed.connect(lambda: self.transcript_submitted.emit(self.edit_transcript.text()))
        transcript_layout.addWidget(self.edit_transcript, 1)
        btn_add = QPushButton("新增 (Enter)")
        btn_add.clicked.connect(lambda: self.transcript_submitted.emit(self.edit_transcript.text()))
        transcript_layout.addWidget(btn_add)
        main_layout.addLayout(transcript_layout)

        self.lbl_edit_context = QLabel("目前編輯：✂ 影片工作區間")
        self.lbl_edit_context.setStyleSheet("font-weight: 600; color: #4a6882; padding: 2px 4px;")
        main_layout.addWidget(self.lbl_edit_context)

        # 控制列
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(6)

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

        main_layout.addLayout(ctrl_layout)

        edit_layout = QHBoxLayout()
        edit_layout.setSpacing(6)

        # 關鍵影格導航
        self.btn_prev_kf = QPushButton("⏮ 上個 🔷")
        self.btn_prev_kf.setToolTip("跳至上一個關鍵影格 ([)")
        self.btn_prev_kf.clicked.connect(self.prev_keyframe_requested.emit)
        edit_layout.addWidget(self.btn_prev_kf)

        self.btn_toggle_kf = QPushButton("🔷 標記影格")
        self.btn_toggle_kf.setToolTip("在當前秒數新增或刪除關鍵影格 (K)")
        self.btn_toggle_kf.setStyleSheet("color: #b45309; font-weight: bold;")
        self.btn_toggle_kf.clicked.connect(self.toggle_keyframe_at_current.emit)
        edit_layout.addWidget(self.btn_toggle_kf)

        self.btn_next_kf = QPushButton("🔷 下個 ⏭")
        self.btn_next_kf.setToolTip("跳至下一個關鍵影格 (])")
        self.btn_next_kf.clicked.connect(self.next_keyframe_requested.emit)
        edit_layout.addWidget(self.btn_next_kf)

        edit_layout.addSpacing(10)

        # Work Range 手動按鈕
        self.btn_in = QPushButton("設起點 (I)")
        self.btn_in.clicked.connect(self.set_in_point.emit)
        edit_layout.addWidget(self.btn_in)

        self.btn_out = QPushButton("設終點 (O)")
        self.btn_out.clicked.connect(self.set_out_point.emit)
        edit_layout.addWidget(self.btn_out)

        self.btn_reset_range = QPushButton("🔄 重設")
        self.btn_reset_range.setToolTip("重設工作區間為整部影片")
        self.btn_reset_range.clicked.connect(self.reset_range_requested.emit)
        edit_layout.addWidget(self.btn_reset_range)

        edit_layout.addStretch()

        # 時間碼顯示
        self.lbl_time = QLabel("00:00:00.000 / 00:00:00.000")
        self.lbl_time.setStyleSheet("font-family: Consolas, monospace; font-size: 13px; font-weight: bold; color: #4a6882;")
        edit_layout.addWidget(self.lbl_time)

        main_layout.addLayout(edit_layout)

    def _on_canvas_hover(self, hover_sec: float, global_pos: QPoint):
        if self.is_playing or not self.thumb_extractor:
            self.hover_popup.hide()
            return
            
        cache_key = round(hover_sec * 4) / 4
        thumb_rgb = self._thumbnail_cache.get(cache_key)
        if thumb_rgb is None:
            if time.monotonic() - self._last_thumbnail_request < 0.12:
                return
            self._last_thumbnail_request = time.monotonic()
            thumb_rgb = self.thumb_extractor.get_thumbnail(cache_key, width=160, height=90)
            if thumb_rgb is not None:
                self._thumbnail_cache[cache_key] = thumb_rgb
                if len(self._thumbnail_cache) > 64:
                    self._thumbnail_cache.popitem(last=False)
        time_str = self._format_time(cache_key)
        self.hover_popup.set_content(thumb_rgb, time_str)
        
        # 顯示在游標正上方中央
        popup_x = global_pos.x() - self.hover_popup.width() // 2
        popup_y = global_pos.y() - self.hover_popup.height() - 8
        self.hover_popup.move(popup_x, popup_y)
        self.hover_popup.show()

    def _on_canvas_leave(self):
        self.hover_popup.hide()

    def set_duration(self, duration: float):
        self.duration = max(0.0, duration)
        self.in_time = 0.0
        self.out_time = duration
        self.update_state(0.0, self.in_time, self.out_time, [])

    def set_edit_context(self, icon: str, label: str, start: float, end: float, reset_text: str):
        self.lbl_edit_context.setText(f"目前編輯：{icon} {label}　{self._format_time(start)} ～ {self._format_time(end)}")
        self.btn_reset_range.setText(reset_text)

    def update_state(self, current_time: float, in_time: float, out_time: float, keyframe_times: List[float], speech_segments: list = None, subtitles: list = None, selected_sub_id: int = -1, uncovered_ranges: list = None):
        self.current_time = current_time
        self.in_time = in_time
        self.out_time = out_time if out_time > in_time else self.duration
        self.keyframe_times = keyframe_times
        
        self.canvas.update_state(current_time, self.duration, self.in_time, self.out_time, keyframe_times, speech_segments, subtitles, selected_sub_id, uncovered_ranges)
        
        cur_str = self._format_time(self.current_time)
        dur_str = self._format_time(self.duration)
        self.lbl_time.setText(f"{cur_str} / {dur_str}")

        # 更新頂部剪輯範圍資訊
        in_str = self._format_time(self.in_time)
        out_str = self._format_time(self.out_time)
        len_sec = self.out_time - self.in_time
        if abs(len_sec - self.duration) < 0.1 or len_sec <= 0:
            self.lbl_range_info.setText(f"✂ 工作區間: {in_str} ~ {out_str} (整部影片，長度: {len_sec:.2f} 秒)")
        else:
            self.lbl_range_info.setText(f"✂ 剪輯工作區間: {in_str} ~ {out_str} (⭐ 已選取精華: {len_sec:.2f} 秒)")

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
