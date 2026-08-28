"""
ClipMask-AI Main Window (完整非同步版)
包含 PlaybackWorker、AIDetectionWorker 與即時進度對話框，UI 永不卡死。
"""
import sys
import os
import time
import cv2
import numpy as np
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QFileDialog, QListWidget, QListWidgetItem, QLabel, QGroupBox,
    QMessageBox, QSplitter, QProgressBar, QComboBox, QSpinBox,
    QProgressDialog
)
from PySide6.QtGui import QImage
from PySide6.QtCore import Qt, QThread, Signal, Slot
from .video_view import VideoGraphicsView
from .timeline import TimelineWidget
from ..models.project import ProjectState, Track, Keyframe, MaskConfig, WorkRange
from ..media.source import VideoSource
from ..track.tracker import MicroTracker
from ..ai.detector import FaceDetector
from ..ai.subtitles import SubtitleManager
from ..export.exporter import FastCopyExporter, RenderExporter

class PlaybackWorker(QThread):
    frame_ready = Signal(QImage, float, int, int)
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
        self.wait(1000)

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
                orig_h, orig_w = frame.shape[:2]
                bytes_per_line = 3 * orig_w
                qimg = QImage(frame.data, orig_w, orig_h, bytes_per_line, QImage.Format.Format_RGB888).copy()
                
                self.frame_ready.emit(qimg, cur_time, orig_w, orig_h)
                
                elapsed = time.perf_counter() - t0
                sleep_time = max(0.001, self.frame_delay - elapsed)
                time.sleep(sleep_time)
                
            source.close()
        except Exception:
            pass
            
        self.finished.emit()

class AIDetectionWorker(QThread):
    progress = Signal(int, str)
    finished = Signal(list)

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
            
            def on_progress(pct: int, msg: str) -> bool:
                self.progress.emit(pct, msg)
                return not self._is_cancelled

            tracks = detector.scan_work_range(
                source,
                self.in_time,
                self.out_time,
                step_sec=0.5,
                progress_callback=on_progress
            )
            source.close()
            self.finished.emit(tracks)
        except Exception:
            self.finished.emit([])

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ClipMask-AI — 智慧影音去識別化與離線剪輯工作站")
        self.resize(1360, 860)
        
        self.project = ProjectState()
        self.video_source: VideoSource = None
        self.current_qimage = None
        self.playback_worker: PlaybackWorker = None
        self.ai_worker: AIDetectionWorker = None
        self.progress_dialog: QProgressDialog = None
        
        self.init_ui()

    def init_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 10)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 左側：預覽畫面與時間軸 ──
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        top_bar = QHBoxLayout()
        self.btn_open = QPushButton("📂 開啟影片")
        self.btn_open.setStyleSheet("font-weight: bold; padding: 5px 12px;")
        self.btn_open.clicked.connect(self.open_video)
        top_bar.addWidget(self.btn_open)

        self.btn_ai_detect = QPushButton("🤖 AI 自動偵測人臉")
        self.btn_ai_detect.clicked.connect(self.run_ai_face_detection)
        top_bar.addWidget(self.btn_ai_detect)

        top_bar.addSpacing(10)

        self.btn_fast_export = QPushButton("⚡ 快速無損剪輯 (Stream Copy)")
        self.btn_fast_export.clicked.connect(self.export_fast_copy)
        top_bar.addWidget(self.btn_fast_export)

        self.btn_render_export = QPushButton("🛡️ 匯出遮蔽影片 (Single-pass)")
        self.btn_render_export.setStyleSheet("background-color: #2b5b84; color: white; font-weight: bold; padding: 5px 14px;")
        self.btn_render_export.clicked.connect(self.export_render)
        top_bar.addWidget(self.btn_render_export)

        top_bar.addStretch()
        left_layout.addLayout(top_bar)

        self.video_view = VideoGraphicsView()
        self.video_view.rect_drawn.connect(self._on_user_drawn_rect)
        left_layout.addWidget(self.video_view, stretch=1)

        self.timeline = TimelineWidget()
        self.timeline.play_toggled.connect(self._on_play_toggled)
        self.timeline.seek_requested.connect(self.seek_to)
        self.timeline.step_requested.connect(self.step_frame)
        self.timeline.set_in_point.connect(self._set_in_point)
        self.timeline.set_out_point.connect(self._set_out_point)
        left_layout.addWidget(self.timeline)

        splitter.addWidget(left_widget)

        # ── 右側：遮蔽物件、樣式設定與字幕管理 ──
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(5, 5, 5, 5)
        right_layout.setSpacing(10)

        grp_tracks = QGroupBox("📋 遮蔽物件清單 (Tracks)")
        grp_layout = QVBoxLayout(grp_tracks)
        
        self.track_list = QListWidget()
        self.track_list.currentRowChanged.connect(self._on_track_selection_changed)
        grp_layout.addWidget(self.track_list)

        track_btn_layout = QHBoxLayout()
        self.btn_track_forward = QPushButton("🎯 向後追蹤 2 秒 (Tracker)")
        self.btn_track_forward.clicked.connect(self._track_selected_forward)
        track_btn_layout.addWidget(self.btn_track_forward)

        btn_del_track = QPushButton("🗑️ 刪除")
        btn_del_track.clicked.connect(self._delete_selected_track)
        track_btn_layout.addWidget(btn_del_track)
        grp_layout.addLayout(track_btn_layout)

        right_layout.addWidget(grp_tracks)

        grp_style = QGroupBox("⚙️ 遮蔽樣式調整")
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
        self.spin_strength.setValue(15)
        self.spin_strength.valueChanged.connect(self._on_strength_changed)
        row_strength.addWidget(self.spin_strength)
        style_layout.addLayout(row_strength)

        lbl_hint = QLabel("💡 提示：在畫面上拉框即可建立遮蔽，點選「向後追蹤」可自動預測路徑。")
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet("color: #888; font-size: 11px; margin-top: 4px;")
        style_layout.addWidget(lbl_hint)

        right_layout.addWidget(grp_style)

        grp_subs = QGroupBox("🎙️ 語音字幕 (Subtitles)")
        sub_layout = QVBoxLayout(grp_subs)
        
        self.btn_import_srt = QPushButton("📄 匯入 / 管理 SRT 字幕")
        self.btn_import_srt.clicked.connect(self._import_srt)
        sub_layout.addWidget(self.btn_import_srt)

        self.lbl_sub_status = QLabel("目前無載入字幕")
        self.lbl_sub_status.setStyleSheet("color: #aaa; font-size: 11px;")
        sub_layout.addWidget(self.lbl_sub_status)

        right_layout.addWidget(grp_subs)
        right_layout.addStretch()

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        main_layout.addWidget(splitter)

    def open_video(self):
        self._stop_playback()
        path, _ = QFileDialog.getOpenFileName(self, "選擇影片", "", "Video Files (*.mp4 *.mkv *.mov *.avi *.ts)")
        if not path:
            return
            
        if self.video_source:
            self.video_source.close()
            
        self.video_source = VideoSource(path)
        self.project.source = self.video_source.metadata
        self.project.work_range = WorkRange(0.0, self.video_source.duration)
        self.project.tracks.clear()
        
        self.timeline.set_duration(self.video_source.duration)
        self.seek_to(0.0)
        self._refresh_track_list()

    def seek_to(self, seconds: float):
        if not self.video_source:
            return
        frame = self.video_source.seek_exact(seconds)
        if frame is not None:
            h, w = frame.shape[:2]
            qimg = QImage(frame.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
            self.current_qimage = qimg
            self.video_view.update_qimage(qimg, self.project.tracks, self.video_source.current_time, w, h)
            self.timeline.update_time_display(self.video_source.current_time)

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

    @Slot(QImage, float, int, int)
    def _on_worker_frame(self, qimg: QImage, current_time: float, width: int, height: int):
        self.current_qimage = qimg
        if self.video_source:
            self.video_source.current_time = current_time
        self.video_view.update_qimage(qimg, self.project.tracks, current_time, width, height)
        self.timeline.update_time_display(current_time)

    @Slot()
    def _on_worker_finished(self):
        self.timeline.set_playing_state(False)

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
                padding=0.15
            ),
            keyframes=[Keyframe(time=cur_t, pts=self.video_source.current_pts, rect_px=(x, y, w, h), source="manual")]
        )
        self.project.tracks.append(track)
        self._refresh_track_list()
        self.track_list.setCurrentRow(len(self.project.tracks) - 1)
        if self.current_qimage is not None and self.video_source:
            self.video_view.update_qimage(
                self.current_qimage, 
                self.project.tracks, 
                cur_t,
                self.video_source.width,
                self.video_source.height
            )

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
        
        # 建立進度對話框
        self.progress_dialog = QProgressDialog("正在啟動 AI 人臉偵測...", "取消", 0, 100, self)
        self.progress_dialog.setWindowTitle("AI 人臉自動掃描中")
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        
        self.ai_worker = AIDetectionWorker(self.video_source.video_path, in_t, out_t)
        self.ai_worker.progress.connect(self._on_ai_progress)
        self.ai_worker.finished.connect(self._on_ai_finished)
        self.progress_dialog.canceled.connect(self.ai_worker.cancel)
        
        self.ai_worker.start()
        self.progress_dialog.show()

    @Slot(int, str)
    def _on_ai_progress(self, pct: int, msg: str):
        if self.progress_dialog:
            self.progress_dialog.setValue(pct)
            self.progress_dialog.setLabelText(msg)

    @Slot(list)
    def _on_ai_finished(self, detected_tracks: list):
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None
            
        if detected_tracks:
            self.project.tracks.extend(detected_tracks)
            self._refresh_track_list()
            in_t = self.project.work_range.in_time if self.project.work_range else 0.0
            self.seek_to(in_t)
            QMessageBox.information(self, "AI 偵測完成", f"共偵測到 {len(detected_tracks)} 處人臉目標！")
        else:
            QMessageBox.information(self, "AI 偵測完成", "未偵測到明顯人臉或任務已取消。")

    def _refresh_track_list(self):
        cur_row = self.track_list.currentRow()
        self.track_list.clear()
        for t in self.project.tracks:
            kf_count = len(t.keyframes)
            item = QListWidgetItem(f"{t.label} [{kf_count} 關鍵影格]")
            self.track_list.addItem(item)
        if 0 <= cur_row < self.track_list.count():
            self.track_list.setCurrentRow(cur_row)

    def _on_track_selection_changed(self, row: int):
        if 0 <= row < len(self.project.tracks):
            track = self.project.tracks[row]
            self.combo_style.blockSignals(True)
            self.combo_style.setCurrentIndex(0 if track.mask.style == "mosaic" else 1)
            self.combo_style.blockSignals(False)
            
            self.spin_strength.blockSignals(True)
            self.spin_strength.setValue(track.mask.strength)
            self.spin_strength.blockSignals(False)

    def _on_style_changed(self, index: int):
        row = self.track_list.currentRow()
        if 0 <= row < len(self.project.tracks):
            self.project.tracks[row].mask.style = "mosaic" if index == 0 else "blur"
            if self.current_qimage is not None and self.video_source:
                self.video_view.update_qimage(
                    self.current_qimage,
                    self.project.tracks,
                    self.video_source.current_time,
                    self.video_source.width,
                    self.video_source.height
                )

    def _on_strength_changed(self, val: int):
        row = self.track_list.currentRow()
        if 0 <= row < len(self.project.tracks):
            self.project.tracks[row].mask.strength = val
            if self.current_qimage is not None and self.video_source:
                self.video_view.update_qimage(
                    self.current_qimage,
                    self.project.tracks,
                    self.video_source.current_time,
                    self.video_source.width,
                    self.video_source.height
                )

    def _delete_selected_track(self):
        row = self.track_list.currentRow()
        if 0 <= row < len(self.project.tracks):
            del self.project.tracks[row]
            self._refresh_track_list()
            if self.current_qimage is not None and self.video_source:
                self.video_view.update_qimage(
                    self.current_qimage,
                    self.project.tracks,
                    self.video_source.current_time,
                    self.video_source.width,
                    self.video_source.height
                )

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
            QMessageBox.information(self, "工作起點", f"已設定工作起點：{self.video_source.current_time:.3f} 秒")

    def _set_out_point(self):
        if self.video_source and self.project.work_range:
            self.project.work_range.out_time = self.video_source.current_time
            QMessageBox.information(self, "工作終點", f"已設定工作終點：{self.video_source.current_time:.3f} 秒")

    def export_fast_copy(self):
        self._stop_playback()
        if not self.video_source or not self.project.source:
            return
        out_path, _ = QFileDialog.getSaveFileName(self, "儲存無損剪輯影片", "fast_trimmed.mp4", "MP4 Files (*.mp4)")
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
        out_path, _ = QFileDialog.getSaveFileName(self, "儲存遮蔽壓制影片", "redacted_output.mp4", "MP4 Files (*.mp4)")
        if not out_path:
            return
            
        success = RenderExporter.render_export(self.project, out_path)
        if success:
            QMessageBox.information(self, "匯出成功", f"去識別化影片壓制完成！已儲存至：\n{out_path}")
        else:
            QMessageBox.critical(self, "匯出失敗", "影片壓制失敗，請檢查環境。")

    def closeEvent(self, event):
        self._stop_playback()
        if self.ai_worker and self.ai_worker.isRunning():
            self.ai_worker.cancel()
            self.ai_worker.wait(500)
        if self.video_source:
            self.video_source.close()
        super().closeEvent(event)
