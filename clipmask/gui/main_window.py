"""
ClipMask-AI Main Window (智慧自動命名與防重名版)
匯出時自動帶入原檔名、起訖時間碼或時間戳，徹底解決重複命名困擾。
"""
import sys
import os
import time
from datetime import datetime
import cv2
import numpy as np
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QFileDialog, QListWidget, QListWidgetItem, QLabel, QGroupBox,
    QMessageBox, QSplitter, QProgressBar, QComboBox, QSpinBox,
    QProgressDialog
)
from PySide6.QtGui import QImage, QKeySequence, QShortcut, QDragEnterEvent, QDropEvent
from PySide6.QtCore import Qt, QThread, Signal, Slot
from .video_view import VideoGraphicsView
from .timeline import TimelineWidget
from .styles import MORANDI_JOURNAL_QSS
from ..models.project import ProjectState, Track, Keyframe, MaskConfig, WorkRange
from ..media.source import VideoSource
from ..track.tracker import MicroTracker
from ..ai.detector import FaceDetector
from ..ai.subtitles import SubtitleManager
from ..export.exporter import FastCopyExporter, RenderExporter

# ── 1. 播放背景 Worker ──
class PlaybackWorker(QThread):
    frame_ready = Signal(np.ndarray, float)
    finished = Signal()

    def __init__(self, video_path: str, start_time: float, fps: float):
        super().__init__()
        self.video_path = video_path
        self.start_time = start_time
        self.fps = fps if fps > 0 else 30.0
        self.frame_delay = 1.0 / self.fps
        self._is_running = True

    def stop(self):
        self._is_running = False
        self.wait(500)

    def run(self):
        try:
            source = VideoSource(self.video_path)
            source.seek_exact(self.start_time)
            
            while self._is_running:
                t0 = time.perf_counter()
                frame = source.read_next_frame()
                if frame is None or not self._is_running:
                    break
                    
                cur_time = source.current_time
                self.frame_ready.emit(frame.copy(), cur_time)
                
                elapsed = time.perf_counter() - t0
                sleep_time = max(0.001, self.frame_delay - elapsed)
                time.sleep(sleep_time)
                
            source.close()
        except Exception:
            pass
        self.finished.emit()

# ── 2. AI 偵測背景 Worker ──
class AiDetectWorker(QThread):
    progress = Signal(int)
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, video_path: str, in_time: float, out_time: float):
        super().__init__()
        self.video_path = video_path
        self.in_time = in_time
        self.out_time = out_time
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            detector = FaceDetector()
            source = VideoSource(self.video_path)
            
            tracks = detector.scan_work_range(
                source,
                self.in_time,
                self.out_time,
                step_sec=0.25,
                progress_callback=lambda p: self.progress.emit(p)
            )
            source.close()
            self.finished.emit(tracks)
        except Exception as e:
            self.error.emit(str(e))

# ── 3. 匯出壓制背景 Worker ──
class ExportWorker(QThread):
    progress = Signal(int)
    finished = Signal(bool, str)

    def __init__(self, project: ProjectState, output_path: str):
        super().__init__()
        self.project = project
        self.output_path = output_path

    def run(self):
        try:
            def on_progress(pct: int):
                self.progress.emit(pct)

            success = RenderExporter.render_export(
                self.project,
                self.output_path,
                progress_callback=on_progress
            )
            self.finished.emit(success, self.output_path)
        except Exception as e:
            self.finished.emit(False, str(e))

# ── 4. 主視窗 ──
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ClipMask-AI — 智慧影音去識別化手帳工作站")
        self.resize(1380, 880)
        self.setStyleSheet(MORANDI_JOURNAL_QSS)
        self.setAcceptDrops(True)
        
        self.project = ProjectState()
        self.video_source: VideoSource = None
        self.current_frame_rgb = None
        self.playback_worker: PlaybackWorker = None
        self.ai_worker: AiDetectWorker = None
        self.export_worker: ExportWorker = None
        
        self.init_ui()
        self.setup_shortcuts()

    def init_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(12, 12, 12, 12)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 左側：預覽畫面與時間軸 ──
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # 上方功能列
        top_bar = QHBoxLayout()
        self.btn_open = QPushButton("📂 開啟 / 拖曳影片")
        self.btn_open.setToolTip("點擊選取或直接將影片檔案拖曳至視窗內")
        self.btn_open.setStyleSheet("padding: 7px 16px;")
        self.btn_open.clicked.connect(self.open_video)
        top_bar.addWidget(self.btn_open)

        self.btn_ai_detect = QPushButton("🤖 AI 人臉追蹤偵測")
        self.btn_ai_detect.setObjectName("btn_ai")
        self.btn_ai_detect.clicked.connect(self.run_ai_face_detection)
        top_bar.addWidget(self.btn_ai_detect)

        self.btn_toggle_preview = QPushButton("👁️ 即時效果預覽: 關")
        self.btn_toggle_preview.setToolTip("切換是否直接顯示真實馬賽克/模糊效果")
        self.btn_toggle_preview.clicked.connect(self._toggle_real_mask_preview)
        top_bar.addWidget(self.btn_toggle_preview)

        top_bar.addSpacing(15)

        self.btn_fast_export = QPushButton("⚡ 快速無損剪輯 (Stream Copy)")
        self.btn_fast_export.clicked.connect(self.export_fast_copy)
        top_bar.addWidget(self.btn_fast_export)

        self.btn_render_export = QPushButton("🌿 匯出遮蔽影片 (Single-pass)")
        self.btn_render_export.setObjectName("btn_primary")
        self.btn_render_export.clicked.connect(self.export_render)
        top_bar.addWidget(self.btn_render_export)

        top_bar.addStretch()
        left_layout.addLayout(top_bar)

        # 視訊畫面檢視
        self.video_view = VideoGraphicsView()
        self.video_view.rect_drawn.connect(self._on_user_drawn_rect)
        self.video_view.wheel_stepped.connect(self.step_frame)
        self.video_view.file_dropped.connect(self.load_video_path)
        left_layout.addWidget(self.video_view, stretch=1)

        # 專業手帳時間軸控制器
        self.timeline = TimelineWidget()
        self.timeline.play_toggled.connect(self._on_play_toggled)
        self.timeline.seek_requested.connect(self.seek_to)
        self.timeline.step_requested.connect(self.step_frame)
        self.timeline.set_in_point.connect(self._set_in_point)
        self.timeline.set_out_point.connect(self._set_out_point)
        self.timeline.prev_keyframe_requested.connect(self._jump_prev_keyframe)
        self.timeline.next_keyframe_requested.connect(self._jump_next_keyframe)
        self.timeline.toggle_keyframe_at_current.connect(self._toggle_keyframe_at_current)
        left_layout.addWidget(self.timeline)

        splitter.addWidget(left_widget)

        # ── 右側：遮蔽物件與設定清單 ──
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(6, 0, 6, 0)
        right_layout.setSpacing(12)

        # 遮蔽物件清單
        grp_tracks = QGroupBox("📋 遮蔽人物與軌跡 (Tracks)")
        grp_layout = QVBoxLayout(grp_tracks)
        
        self.track_list = QListWidget()
        self.track_list.currentRowChanged.connect(self._on_track_selection_changed)
        grp_layout.addWidget(self.track_list)

        track_btn_layout = QHBoxLayout()
        self.btn_track_forward = QPushButton("🎯 向後追蹤 2 秒")
        self.btn_track_forward.clicked.connect(self._track_selected_forward)
        track_btn_layout.addWidget(self.btn_track_forward)

        btn_del_track = QPushButton("🗑️ 刪除")
        btn_del_track.clicked.connect(self._delete_selected_track)
        track_btn_layout.addWidget(btn_del_track)
        grp_layout.addLayout(track_btn_layout)

        right_layout.addWidget(grp_tracks)

        # 樣式調整
        grp_style = QGroupBox("⚙️ 遮蔽手帳樣式")
        style_layout = QVBoxLayout(grp_style)
        
        row_style = QHBoxLayout()
        row_style.addWidget(QLabel("樣式:"))
        self.combo_style = QComboBox()
        self.combo_style.addItems(["馬賽克 (Mosaic)", "高斯模糊 (Blur)"])
        self.combo_style.currentIndexChanged.connect(self._on_style_changed)
        row_style.addWidget(self.combo_style)
        style_layout.addLayout(row_style)

        row_strength = QHBoxLayout()
        row_strength.addWidget(QLabel("強度 / 區塊:"))
        self.spin_strength = QSpinBox()
        self.spin_strength.setRange(4, 80)
        self.spin_strength.setValue(20)
        self.spin_strength.valueChanged.connect(self._on_strength_changed)
        row_strength.addWidget(self.spin_strength)
        style_layout.addLayout(row_strength)

        lbl_hint = QLabel("💡 快捷技巧：\n• 支援直接拖曳影片進視窗\n• 左右鍵 ← →: 跳轉 1.0 秒\n• 上下鍵 ↑ ↓: 微調 0.1 秒\n• 滾輪: 逐格微調 (Shift: ±5格)\n• 鍵盤 K: 快速打上/刪除關鍵影格 🔷")
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet("color: #78716c; font-size: 11px; margin-top: 4px; line-height: 1.4;")
        style_layout.addWidget(lbl_hint)

        right_layout.addWidget(grp_style)

        # 字幕管理
        grp_subs = QGroupBox("🎙️ 語音字幕 (Subtitles)")
        sub_layout = QVBoxLayout(grp_subs)
        
        self.btn_import_srt = QPushButton("📄 匯入 SRT 字幕檔")
        self.btn_import_srt.clicked.connect(self._import_srt)
        sub_layout.addWidget(self.btn_import_srt)

        self.lbl_sub_status = QLabel("目前無載入字幕")
        self.lbl_sub_status.setStyleSheet("color: #8c857b; font-size: 11px;")
        sub_layout.addWidget(self.lbl_sub_status)

        right_layout.addWidget(grp_subs)
        right_layout.addStretch()

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        main_layout.addWidget(splitter)

    # ──── 全視窗拖曳開檔 ────
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                ext = os.path.splitext(url.toLocalFile())[1].lower()
                if ext in [".mp4", ".mkv", ".mov", ".avi", ".ts", ".wmv", ".flv", ".webm", ".m4v"]:
                    event.acceptProposedAction()
                    return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                fpath = url.toLocalFile()
                ext = os.path.splitext(fpath)[1].lower()
                if ext in [".mp4", ".mkv", ".mov", ".avi", ".ts", ".wmv", ".flv", ".webm", ".m4v"]:
                    self.load_video_path(fpath)
                    event.acceptProposedAction()
                    return
        super().dropEvent(event)

    def setup_shortcuts(self):
        QShortcut(QKeySequence("Space"), self, self.timeline._toggle_play)
        QShortcut(QKeySequence("J"), self, lambda: self.step_frame(-1))
        QShortcut(QKeySequence("L"), self, lambda: self.step_frame(1))
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self._jump_relative_time(-1.0))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self._jump_relative_time(1.0))
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, lambda: self._jump_relative_time(0.1))
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, lambda: self._jump_relative_time(-0.1))
        QShortcut(QKeySequence("I"), self, self._set_in_point)
        QShortcut(QKeySequence("O"), self, self._set_out_point)
        QShortcut(QKeySequence("["), self, self._jump_prev_keyframe)
        QShortcut(QKeySequence("]"), self, self._jump_next_keyframe)
        QShortcut(QKeySequence("K"), self, self._toggle_keyframe_at_current)

    def _jump_relative_time(self, dt: float):
        if not self.video_source:
            return
        self._stop_playback()
        target_t = max(0.0, min(self.video_source.duration, self.video_source.current_time + dt))
        self.seek_to(target_t)

    def _toggle_real_mask_preview(self):
        self.video_view.show_real_mask_preview = not self.video_view.show_real_mask_preview
        txt = "👁️ 即時效果預覽: 開" if self.video_view.show_real_mask_preview else "👁️ 即時效果預覽: 關"
        self.btn_toggle_preview.setText(txt)
        if self.current_frame_rgb is not None and self.video_source:
            self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.video_source.current_time)

    def open_video(self):
        path, _ = QFileDialog.getOpenFileName(self, "選擇影片", "", "Video Files (*.mp4 *.mkv *.mov *.avi *.ts *.wmv *.webm *.flv *.m4v)")
        if path:
            self.load_video_path(path)

    def load_video_path(self, path: str):
        self._stop_playback()
        try:
            if not os.path.exists(path):
                QMessageBox.critical(self, "開啟失敗", f"找不到檔案：\n{path}")
                return

            if self.video_source:
                self.video_source.close()
                self.video_source = None
                
            self.video_source = VideoSource(path)
            self.project.source = self.video_source.metadata
            self.project.work_range = WorkRange(0.0, self.video_source.duration)
            self.project.tracks.clear()
            
            self.timeline.set_duration(self.video_source.duration)
            self.seek_to(0.0)
            self._refresh_track_list()
            self.setWindowTitle(f"ClipMask-AI — {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.critical(self, "載入影片失敗", f"無法解碼影片檔案：\n{path}\n\n錯誤訊息：{e}")

    def seek_to(self, seconds: float):
        if not self.video_source:
            return
        frame = self.video_source.seek_exact(seconds)
        if frame is not None:
            self.current_frame_rgb = frame
            self.video_view.update_frame_data(frame, self.project.tracks, self.video_source.current_time)
            self._update_timeline_state()

    def step_frame(self, delta: int):
        if not self.video_source:
            return
        self._stop_playback()
        dt = delta * (1.0 / self.video_source.fps)
        target_t = max(0.0, min(self.video_source.duration, self.video_source.current_time + dt))
        self.seek_to(target_t)

    def _on_play_toggled(self, playing: bool):
        if not self.video_source:
            return
        if playing:
            self._start_playback()
        else:
            self._stop_playback()

    def _start_playback(self):
        if not self.video_source:
            return
        cur_t = self.video_source.current_time
        if cur_t >= self.video_source.duration:
            cur_t = 0.0
            
        self.playback_worker = PlaybackWorker(
            self.video_source.video_path,
            cur_t,
            self.video_source.fps
        )
        self.playback_worker.frame_ready.connect(self._on_worker_frame)
        self.playback_worker.finished.connect(self._on_worker_finished)
        self.playback_worker.start()

    def _stop_playback(self):
        if self.playback_worker and self.playback_worker.isRunning():
            self.playback_worker.stop()
            self.playback_worker = None
        self.timeline.set_playing_state(False)

    @Slot(np.ndarray, float)
    def _on_worker_frame(self, frame_rgb: np.ndarray, current_time: float):
        self.current_frame_rgb = frame_rgb
        if self.video_source:
            self.video_source.current_time = current_time
        self.video_view.update_frame_data(frame_rgb, self.project.tracks, current_time)
        self._update_timeline_state()

    @Slot()
    def _on_worker_finished(self):
        self.timeline.set_playing_state(False)

    def _update_timeline_state(self):
        if not self.video_source:
            return
        cur_t = self.video_source.current_time
        in_t = self.project.work_range.in_time if self.project.work_range else 0.0
        out_t = self.project.work_range.out_time if self.project.work_range else self.video_source.duration
        
        kf_times = []
        row = self.track_list.currentRow()
        if 0 <= row < len(self.project.tracks):
            kf_times = [kf.time for kf in self.project.tracks[row].keyframes]
            
        self.timeline.update_state(cur_t, in_t, out_t, kf_times)

    def _on_user_drawn_rect(self, x: int, y: int, w: int, h: int):
        if not self.video_source:
            return
        cur_t = self.video_source.current_time
        track_id = f"track_{len(self.project.tracks)+1}"
        track = Track(
            id=track_id,
            label=f"遮蔽 {len(self.project.tracks)+1} ({x},{y})",
            mask=MaskConfig(
                style="mosaic" if self.combo_style.currentIndex() == 0 else "blur",
                strength=self.spin_strength.value(),
                padding=0.25
            ),
            keyframes=[Keyframe(time=cur_t, pts=self.video_source.current_pts, rect_px=(x, y, w, h), source="manual")]
        )
        self.project.tracks.append(track)
        self._refresh_track_list()
        self.track_list.setCurrentRow(len(self.project.tracks) - 1)
        if self.current_frame_rgb is not None and self.video_source:
            self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, cur_t)

    def _jump_prev_keyframe(self):
        if not self.video_source:
            return
        row = self.track_list.currentRow()
        if not (0 <= row < len(self.project.tracks)):
            return
        track = self.project.tracks[row]
        cur_t = self.video_source.current_time
        
        prev_kfs = [kf.time for kf in track.keyframes if kf.time < cur_t - 0.05]
        if prev_kfs:
            self.seek_to(prev_kfs[-1])

    def _jump_next_keyframe(self):
        if not self.video_source:
            return
        row = self.track_list.currentRow()
        if not (0 <= row < len(self.project.tracks)):
            return
        track = self.project.tracks[row]
        cur_t = self.video_source.current_time
        
        next_kfs = [kf.time for kf in track.keyframes if kf.time > cur_t + 0.05]
        if next_kfs:
            self.seek_to(next_kfs[0])

    def _toggle_keyframe_at_current(self):
        if not self.video_source:
            return
        row = self.track_list.currentRow()
        if not (0 <= row < len(self.project.tracks)):
            return
        track = self.project.tracks[row]
        cur_t = self.video_source.current_time
        
        if track.remove_keyframe_at(cur_t, tolerance=0.1):
            QMessageBox.information(self, "關鍵影格", f"已刪除 {cur_t:.2f}s 處的關鍵影格。")
        else:
            from ..track.evaluator import TrackEvaluator
            evaluated = TrackEvaluator.evaluate_track_at(track, cur_t, self.video_source.width, self.video_source.height)
            rect = evaluated if evaluated else (100, 100, 100, 100)
            track.add_or_update_keyframe(cur_t, rect, self.video_source.current_pts, source="manual")
            QMessageBox.information(self, "關鍵影格", f"已在 {cur_t:.2f}s 打上新關鍵影格 🔷！")
            
        self._refresh_track_list()
        self.seek_to(cur_t)

    def _track_selected_forward(self):
        self._stop_playback()
        row = self.track_list.currentRow()
        if not (0 <= row < len(self.project.tracks)) or not self.video_source:
            QMessageBox.warning(self, "提示", "請先選取一個遮蔽物件。")
            return
            
        track = self.project.tracks[row]
        ok = MicroTracker.track_forward(self.video_source, track, duration_sec=2.0)
        if ok:
            self._refresh_track_list()
            self.seek_to(self.video_source.current_time)
            QMessageBox.information(self, "追蹤完成", f"已為 [{track.label}] 向後預測建立關鍵影格！")
        else:
            QMessageBox.warning(self, "追蹤失敗", "追蹤器無法初始化。")

    def run_ai_face_detection(self):
        self._stop_playback()
        if not self.video_source or not self.project.work_range:
            QMessageBox.warning(self, "提示", "請先開啟影片。")
            return

        in_t = self.project.work_range.in_time
        out_t = min(self.video_source.duration, self.project.work_range.out_time)

        progress_dialog = QProgressDialog("正在進行 AI 人臉連續追蹤偵測，請稍候...", "取消", 0, 100, self)
        progress_dialog.setWindowTitle("AI 人臉偵測中")
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dialog.setAutoClose(True)
        progress_dialog.setMinimumDuration(0)

        self.ai_worker = AiDetectWorker(self.video_source.video_path, in_t, out_t)
        self.ai_worker.progress.connect(progress_dialog.setValue)
        progress_dialog.canceled.connect(self.ai_worker.cancel)

        def on_ai_finished(tracks):
            progress_dialog.close()
            if tracks:
                self.project.tracks.extend(tracks)
                self._refresh_track_list()
                self.seek_to(in_t)
                QMessageBox.information(self, "AI 偵測完成", f"共偵測並建立 {len(tracks)} 條人物追蹤軌跡！")
            else:
                QMessageBox.information(self, "AI 偵測完成", "在工作區間內未偵測到明顯人臉。")

        def on_ai_error(err_msg):
            progress_dialog.close()
            QMessageBox.critical(self, "AI 偵測失敗", f"偵測過程發生錯誤:\n{err_msg}")

        self.ai_worker.finished.connect(on_ai_finished)
        self.ai_worker.error.connect(on_ai_error)
        self.ai_worker.start()

    def _refresh_track_list(self):
        cur_row = self.track_list.currentRow()
        self.track_list.clear()
        for t in self.project.tracks:
            kf_count = len(t.keyframes)
            item = QListWidgetItem(f"{t.label} [{kf_count} 關鍵影格 🔷]")
            self.track_list.addItem(item)
        if 0 <= cur_row < self.track_list.count():
            self.track_list.setCurrentRow(cur_row)
        self._update_timeline_state()

    def _on_track_selection_changed(self, row: int):
        if 0 <= row < len(self.project.tracks):
            track = self.project.tracks[row]
            self.combo_style.blockSignals(True)
            self.combo_style.setCurrentIndex(0 if track.mask.style == "mosaic" else 1)
            self.combo_style.blockSignals(False)
            
            self.spin_strength.blockSignals(True)
            self.spin_strength.setValue(track.mask.strength)
            self.spin_strength.blockSignals(False)
            
        self._update_timeline_state()

    def _on_style_changed(self, index: int):
        row = self.track_list.currentRow()
        if 0 <= row < len(self.project.tracks):
            self.project.tracks[row].mask.style = "mosaic" if index == 0 else "blur"
            if self.current_frame_rgb is not None and self.video_source:
                self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.video_source.current_time)

    def _on_strength_changed(self, val: int):
        row = self.track_list.currentRow()
        if 0 <= row < len(self.project.tracks):
            self.project.tracks[row].mask.strength = val
            if self.current_frame_rgb is not None and self.video_source:
                self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.video_source.current_time)

    def _delete_selected_track(self):
        row = self.track_list.currentRow()
        if 0 <= row < len(self.project.tracks):
            del self.project.tracks[row]
            self._refresh_track_list()
            if self.current_frame_rgb is not None and self.video_source:
                self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.video_source.current_time)

    def _import_srt(self):
        path, _ = QFileDialog.getOpenFileName(self, "匯入 SRT 字幕", "", "SRT Files (*.srt)")
        if path:
            subs = SubtitleManager.parse_srt_file(path)
            self.project.subtitles = subs
            self.lbl_sub_status.setText(f"已載入 {len(subs)} 條字幕: {os.path.basename(path)}")
            QMessageBox.information(self, "字幕載入", f"已成功載入 {len(subs)} 條字幕！")

    def _set_in_point(self):
        if self.video_source and self.project.work_range:
            self.project.work_range.in_time = self.video_source.current_time
            self._update_timeline_state()

    def _set_out_point(self):
        if self.video_source and self.project.work_range:
            self.project.work_range.out_time = self.video_source.current_time
            self._update_timeline_state()

    # ──── 智慧產生不重複匯出檔名 ────
    def _generate_default_export_name(self, mode: str = "redacted") -> str:
        if not self.video_source or not self.project.source:
            return f"output_{mode}.mp4"
            
        src_path = self.project.source.path
        dir_name = os.path.dirname(src_path)
        base_name = os.path.splitext(os.path.basename(src_path))[0]
        
        if mode == "fast":
            in_s = int(self.project.work_range.in_time) if self.project.work_range else 0
            out_s = int(self.project.work_range.out_time) if self.project.work_range else int(self.video_source.duration)
            suggested = f"{base_name}_cut_{in_s}s_to_{out_s}s.mp4"
        else:
            time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            suggested = f"{base_name}_redacted_{time_str}.mp4"
            
        return os.path.join(dir_name, suggested)

    def export_fast_copy(self):
        self._stop_playback()
        if not self.video_source or not self.project.source:
            return
            
        default_name = self._generate_default_export_name(mode="fast")
        out_path, _ = QFileDialog.getSaveFileName(self, "儲存無損剪輯影片", default_name, "MP4 Files (*.mp4)")
        if not out_path:
            return
            
        in_t = self.project.work_range.in_time if self.project.work_range else 0.0
        out_t = self.project.work_range.out_time if self.project.work_range else self.video_source.duration
        success = FastCopyExporter.export(self.project.source.path, in_t, out_t, out_path)
        if success:
            QMessageBox.information(self, "匯出成功", f"無損剪輯完成！已儲存至：\n{out_path}")
        else:
            QMessageBox.critical(self, "匯出失敗", "FFmpeg Stream Copy 執行失敗。")

    def export_render(self):
        self._stop_playback()
        if not self.video_source or not self.project.source:
            return
            
        default_name = self._generate_default_export_name(mode="redacted")
        out_path, _ = QFileDialog.getSaveFileName(self, "儲存遮蔽壓制影片", default_name, "MP4 Files (*.mp4)")
        if not out_path:
            return

        progress_dialog = QProgressDialog("正在壓制遮蔽影片，請稍候...", "取消", 0, 100, self)
        progress_dialog.setWindowTitle("影片壓制匯出中")
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dialog.setAutoClose(True)
        progress_dialog.setMinimumDuration(0)

        self.export_worker = ExportWorker(self.project, out_path)
        self.export_worker.progress.connect(progress_dialog.setValue)
        progress_dialog.canceled.connect(self.export_worker.terminate)

        def on_export_finished(success, msg):
            progress_dialog.close()
            if success:
                QMessageBox.information(self, "匯出成功", f"去識別化影片壓制完成！已儲存至：\n{msg}")
            else:
                QMessageBox.critical(self, "匯出失敗", f"影片壓制失敗:\n{msg}")

        self.export_worker.finished.connect(on_export_finished)
        self.export_worker.start()

    def closeEvent(self, event):
        self._stop_playback()
        if self.video_source:
            self.video_source.close()
        super().closeEvent(event)
