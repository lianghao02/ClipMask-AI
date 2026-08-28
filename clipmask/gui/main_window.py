"""
ClipMask-AI Main Window (完整版)
整合 PyAV 幀精確播放、QGraphicsScene 原生像素畫框、
MicroTracker 向後 2 秒追蹤、AI 人臉自動偵測、SRT 字幕匯入與雙模式匯出。
"""
import sys
import os
import uuid
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QFileDialog, QListWidget, QListWidgetItem, QLabel, QGroupBox,
    QMessageBox, QSplitter, QProgressBar, QComboBox, QSpinBox,
    QCheckBox, QInputDialog
)
from PySide6.QtCore import Qt, QTimer
from .video_view import VideoGraphicsView
from .timeline import TimelineWidget
from ..models.project import ProjectState, Track, Keyframe, MaskConfig, WorkRange
from ..media.source import VideoSource
from ..track.tracker import MicroTracker
from ..ai.detector import FaceDetector
from ..ai.subtitles import SubtitleManager
from ..export.exporter import FastCopyExporter, RenderExporter

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ClipMask-AI — 智慧影音去識別化與離線剪輯工作站")
        self.resize(1360, 860)
        
        self.project = ProjectState()
        self.video_source: VideoSource = None
        self.current_frame = None
        self.face_detector = None
        
        # 播放計時器
        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self._on_playback_tick)
        
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

        # 上方主要功能列
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

        # 視訊畫面檢視
        self.video_view = VideoGraphicsView()
        self.video_view.rect_drawn.connect(self._on_user_drawn_rect)
        left_layout.addWidget(self.video_view, stretch=1)

        # 時間軸控制器
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

        # 1. 遮蔽物件清單
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

        # 2. 遮蔽樣式設定
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

        lbl_hint = QLabel("💡 提示：用滑鼠在影片上拉框即可建立遮蔽，點選「向後追蹤」可自動預測移動路徑。")
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet("color: #888; font-size: 11px; margin-top: 4px;")
        style_layout.addWidget(lbl_hint)

        right_layout.addWidget(grp_style)

        # 3. 字幕管理
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
            self.current_frame = frame
            self.video_view.update_frame(frame, self.project.tracks, self.video_source.current_time)
            self.timeline.update_time_display(self.video_source.current_time)

    def step_frame(self, delta: int):
        if not self.video_source:
            return
        if self.play_timer.isActive():
            self.play_timer.stop()
            self.timeline.set_playing_state(False)
            
        dt = delta * (1.0 / self.video_source.fps)
        target_t = max(0.0, min(self.video_source.duration, self.video_source.current_time + dt))
        self.seek_to(target_t)

    def _on_play_toggled(self, playing: bool):
        if not self.video_source:
            return
        if playing:
            interval = int(1000.0 / self.video_source.fps)
            self.play_timer.start(interval)
        else:
            self.play_timer.stop()

    def _on_playback_tick(self):
        if not self.video_source:
            return
        frame = self.video_source.read_next_frame()
        if frame is None or self.video_source.current_time >= self.video_source.duration:
            self.play_timer.stop()
            self.timeline.set_playing_state(False)
            return
        self.current_frame = frame
        self.video_view.update_frame(frame, self.project.tracks, self.video_source.current_time)
        self.timeline.update_time_display(self.video_source.current_time)

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
        if self.current_frame is not None:
            self.video_view.update_frame(self.current_frame, self.project.tracks, cur_t)

    def _track_selected_forward(self):
        row = self.track_list.currentRow()
        if not (0 <= row < len(self.project.tracks)) or not self.video_source:
            QMessageBox.warning(self, "提示", "請先從清單中選取一個要向後追蹤的遮蔽物件。")
            return
            
        track = self.project.tracks[row]
        ok = MicroTracker.track_forward(self.video_source, track, duration_sec=2.0)
        if ok:
            self._refresh_track_list()
            self.seek_to(self.video_source.current_time)
            QMessageBox.information(self, "追蹤完成", f"已為 [{track.label}] 向後預測並建立關鍵影格！")
        else:
            QMessageBox.warning(self, "追蹤失敗", "追蹤器無法初始化或影像無效。")

    def run_ai_face_detection(self):
        if not self.video_source or not self.project.work_range:
            QMessageBox.warning(self, "提示", "請先開啟影片。")
            return
            
        try:
            if not self.face_detector:
                self.face_detector = FaceDetector()
        except Exception as e:
            QMessageBox.critical(self, "錯誤", f"無法載入人臉偵測模型: {e}")
            return
            
        in_t = self.project.work_range.in_time
        out_t = min(self.video_source.duration, self.project.work_range.out_time)
        
        detected_tracks = self.face_detector.scan_work_range(
            self.video_source,
            in_t,
            out_t,
            step_sec=0.5
        )
        
        if detected_tracks:
            self.project.tracks.extend(detected_tracks)
            self._refresh_track_list()
            self.seek_to(in_t)
            QMessageBox.information(self, "AI 偵測完成", f"在工作區間內共偵測到 {len(detected_tracks)} 處人臉目標並已加入清單！")
        else:
            QMessageBox.information(self, "AI 偵測完成", "在當前工作區間內未偵測到明顯人臉。")

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
            if self.current_frame is not None and self.video_source:
                self.video_view.update_frame(self.current_frame, self.project.tracks, self.video_source.current_time)

    def _on_strength_changed(self, val: int):
        row = self.track_list.currentRow()
        if 0 <= row < len(self.project.tracks):
            self.project.tracks[row].mask.strength = val
            if self.current_frame is not None and self.video_source:
                self.video_view.update_frame(self.current_frame, self.project.tracks, self.video_source.current_time)

    def _delete_selected_track(self):
        row = self.track_list.currentRow()
        if 0 <= row < len(self.project.tracks):
            del self.project.tracks[row]
            self._refresh_track_list()
            if self.current_frame is not None and self.video_source:
                self.video_view.update_frame(self.current_frame, self.project.tracks, self.video_source.current_time)

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
        if not self.video_source or not self.project.source:
            return
        out_path, _ = QFileDialog.getSaveFileName(self, "儲存遮蔽壓制影片", "redacted_output.mp4", "MP4 Files (*.mp4)")
        if not out_path:
            return
        if self.play_timer.isActive():
            self.play_timer.stop()
            self.timeline.set_playing_state(False)
            
        success = RenderExporter.render_export(self.project, out_path)
        if success:
            QMessageBox.information(self, "匯出成功", f"去識別化影片壓制完成！已儲存至：\n{out_path}")
        else:
            QMessageBox.critical(self, "匯出失敗", "影片壓制失敗，請檢查環境。")

    def closeEvent(self, event):
        if self.video_source:
            self.video_source.close()
        super().closeEvent(event)
