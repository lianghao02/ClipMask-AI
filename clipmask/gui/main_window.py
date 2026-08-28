"""
ClipMask-AI Main Window (直覺化按鈕文字與防呆提示版)
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
    QLineEdit, QProgressDialog
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

        # 上方功能列 (極致直覺化文字)
        top_bar = QHBoxLayout()
        self.btn_open = QPushButton("📂 開啟 / 拖入影片")
        self.btn_open.setToolTip("點擊開啟檔案，或直接把影片檔案拖曳至視窗內")
        self.btn_open.setStyleSheet("padding: 7px 16px;")
        self.btn_open.clicked.connect(self.open_video)
        top_bar.addWidget(self.btn_open)

        self.btn_ai_detect = QPushButton("🤖 AI 偵測區間人臉")
        self.btn_ai_detect.setToolTip("自動偵測所選時間區間內的所有人臉並建立連續追蹤軌跡")
        self.btn_ai_detect.setObjectName("btn_ai")
        self.btn_ai_detect.clicked.connect(self.run_ai_face_detection)
        top_bar.addWidget(self.btn_ai_detect)

        self.btn_toggle_preview = QPushButton("👁️ 真實打碼預覽: 關")
        self.btn_toggle_preview.setToolTip("切換是否直接在畫面顯示真實馬賽克/高斯模糊效果")
        self.btn_toggle_preview.clicked.connect(self._toggle_real_mask_preview)
        top_bar.addWidget(self.btn_toggle_preview)

        top_bar.addSpacing(15)

        # 核心主按鈕：匯出馬賽克去識別影片
        self.btn_render_export = QPushButton("🛡️ 匯出馬賽克影片 (壓制遮蔽)")
        self.btn_render_export.setToolTip("將影片連同所有 AI/手動馬賽克與聽打字幕一起壓制輸出（若有選取區間則只輸出該區間）")
        self.btn_render_export.setObjectName("btn_primary")
        self.btn_render_export.clicked.connect(self.export_render)
        top_bar.addWidget(self.btn_render_export)

        # 輔助次按鈕：純剪輯無碼秒出
        self.btn_fast_export = QPushButton("⚡ 純剪輯影片 (無馬賽克/秒出)")
        self.btn_fast_export.setToolTip("僅無損切出選取的時間區間，不加任何馬賽克，2 秒內完成")
        self.btn_fast_export.clicked.connect(self.export_fast_copy)
        top_bar.addWidget(self.btn_fast_export)

        top_bar.addStretch()
        left_layout.addLayout(top_bar)

        # 視訊畫面檢視
        self.video_view = VideoGraphicsView()
        self.video_view.rect_drawn.connect(self._on_user_drawn_rect)
        self.video_view.wheel_stepped.connect(self.step_frame)
        self.video_view.file_dropped.connect(self.load_video_path)
        left_layout.addWidget(self.video_view, stretch=1)

        # 專業手帳時間軸控制器 (支援滑鼠拖拉選區間)
        self.timeline = TimelineWidget()
        self.timeline.play_toggled.connect(self._on_play_toggled)
        self.timeline.seek_requested.connect(self.seek_to)
        self.timeline.step_requested.connect(self.step_frame)
        self.timeline.set_in_point.connect(self._set_in_point)
        self.timeline.set_out_point.connect(self._set_out_point)
        self.timeline.reset_range_requested.connect(self._reset_work_range)
        self.timeline.range_selected.connect(self._on_range_drag_selected)
        self.timeline.prev_keyframe_requested.connect(self._jump_prev_keyframe)
        self.timeline.next_keyframe_requested.connect(self._jump_next_keyframe)
        self.timeline.toggle_keyframe_at_current.connect(self._toggle_keyframe_at_current)
        left_layout.addWidget(self.timeline)

        splitter.addWidget(left_widget)

        # ── 右側：遮蔽管理 + 聽打字幕面板 ──
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(6, 0, 6, 0)
        right_layout.setSpacing(10)

        # 1. 遮蔽物件清單
        grp_tracks = QGroupBox("📋 遮蔽人物與軌跡 (Tracks)")
        grp_layout = QVBoxLayout(grp_tracks)
        
        self.track_list = QListWidget()
        self.track_list.setMaximumHeight(125)
        self.track_list.currentRowChanged.connect(self._on_track_selection_changed)
        grp_layout.addWidget(self.track_list)

        track_btn_layout = QHBoxLayout()
        self.btn_track_forward = QPushButton("🎯 向後預測 2 秒")
        self.btn_track_forward.clicked.connect(self._track_selected_forward)
        track_btn_layout.addWidget(self.btn_track_forward)

        btn_del_track = QPushButton("🗑️ 刪除")
        btn_del_track.clicked.connect(self._delete_selected_track)
        track_btn_layout.addWidget(btn_del_track)
        grp_layout.addLayout(track_btn_layout)

        # 樣式調整
        row_style = QHBoxLayout()
        row_style.addWidget(QLabel("樣式:"))
        self.combo_style = QComboBox()
        self.combo_style.addItems(["馬賽克", "高斯模糊"])
        self.combo_style.currentIndexChanged.connect(self._on_style_changed)
        row_style.addWidget(self.combo_style)

        row_style.addWidget(QLabel("強度:"))
        self.spin_strength = QSpinBox()
        self.spin_strength.setRange(4, 80)
        self.spin_strength.setValue(20)
        self.spin_strength.valueChanged.connect(self._on_strength_changed)
        row_style.addWidget(self.spin_strength)
        grp_layout.addLayout(row_style)

        right_layout.addWidget(grp_tracks)

        # 2. 即時聽打字幕專屬工作站
        grp_subs = QGroupBox("🎙️ 即時聽打字幕 (Transcribe)")
        sub_layout = QVBoxLayout(grp_subs)

        # 聽打輸入列 (Enter 送出)
        row_input = QHBoxLayout()
        self.edit_sub_text = QLineEdit()
        self.edit_sub_text.setPlaceholderText("聽打這句話... (按 Enter 立即打點新增)")
        self.edit_sub_text.returnPressed.connect(self._add_current_transcribe)
        row_input.addWidget(self.edit_sub_text)

        self.btn_add_sub = QPushButton("➕ 新增 (Enter)")
        self.btn_add_sub.setStyleSheet("font-weight: bold; color: #5f8768;")
        self.btn_add_sub.clicked.connect(self._add_current_transcribe)
        row_input.addWidget(self.btn_add_sub)
        sub_layout.addLayout(row_input)

        # 時間點微調
        row_sub_time = QHBoxLayout()
        self.btn_sub_in = QPushButton("[ 設當前為起點")
        self.btn_sub_in.clicked.connect(self._set_sub_in_point)
        row_sub_time.addWidget(self.btn_sub_in)

        self.btn_sub_out = QPushButton("設當前為終點 ]")
        self.btn_sub_out.clicked.connect(self._set_sub_out_point)
        row_sub_time.addWidget(self.btn_sub_out)
        sub_layout.addLayout(row_sub_time)

        # 字幕清單 (點選即跳轉重聽)
        self.sub_list = QListWidget()
        self.sub_list.currentRowChanged.connect(self._on_sub_selection_changed)
        sub_layout.addWidget(self.sub_list)

        # 字幕管理按鈕列
        row_sub_actions = QHBoxLayout()
        self.btn_del_sub = QPushButton("🗑️ 刪除這句")
        self.btn_del_sub.clicked.connect(self._delete_selected_sub)
        row_sub_actions.addWidget(self.btn_del_sub)

        self.btn_export_srt = QPushButton("💾 匯出 SRT 檔")
        self.btn_export_srt.setToolTip("將聽打內容匯出為標準繁中 SRT 字幕檔")
        self.btn_export_srt.clicked.connect(self._export_srt)
        row_sub_actions.addWidget(self.btn_export_srt)
        sub_layout.addLayout(row_sub_actions)

        lbl_sub_hint = QLabel("💡 聽打技巧：\n• 停在講話起點，打字後按 Enter 即可完成打點\n• 點選清單任一句，畫面瞬間跳轉至該秒數反覆重聽\n• 匯出影片時會自動在畫面底部燒錄字卡！")
        lbl_sub_hint.setWordWrap(True)
        lbl_sub_hint.setStyleSheet("color: #78716c; font-size: 11px; margin-top: 4px; line-height: 1.4;")
        sub_layout.addWidget(lbl_sub_hint)

        right_layout.addWidget(grp_subs)

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
        QShortcut(QKeySequence("T"), self, lambda: self.edit_sub_text.setFocus())

    def _jump_relative_time(self, dt: float):
        if not self.video_source:
            return
        self._stop_playback()
        target_t = max(0.0, min(self.video_source.duration, self.video_source.current_time + dt))
        self.seek_to(target_t)

    def _toggle_real_mask_preview(self):
        self.video_view.show_real_mask_preview = not self.video_view.show_real_mask_preview
        txt = "👁️ 真實打碼預覽: 開" if self.video_view.show_real_mask_preview else "👁️ 真實打碼預覽: 關"
        self.btn_toggle_preview.setText(txt)
        if self.current_frame_rgb is not None and self.video_source:
            self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.project.subtitles, self.video_source.current_time)

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
            self.project.subtitles.clear()
            
            self.timeline.set_duration(self.video_source.duration)
            self.seek_to(0.0)
            self._refresh_track_list()
            self._refresh_sub_list()
            self.setWindowTitle(f"ClipMask-AI — {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.critical(self, "載入影片失敗", f"無法解碼影片檔案：\n{path}\n\n錯誤訊息：{e}")

    def seek_to(self, seconds: float):
        if not self.video_source:
            return
        frame = self.video_source.seek_exact(seconds)
        if frame is not None:
            self.current_frame_rgb = frame
            self.video_view.update_frame_data(frame, self.project.tracks, self.project.subtitles, self.video_source.current_time)
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
        self.video_view.update_frame_data(frame_rgb, self.project.tracks, self.project.subtitles, current_time)
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
        dur = out_t - in_t

        progress_dialog = QProgressDialog(f"正在針對工作區間 ({in_t:.1f}s ~ {out_t:.1f}s, 共 {dur:.1f}秒) 進行 AI 追蹤偵測...", "取消", 0, 100, self)
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
                QMessageBox.information(self, "AI 偵測完成", f"在所選區間內共偵測並建立 {len(tracks)} 條人物追蹤軌跡！")
            else:
                QMessageBox.information(self, "AI 偵測完成", "在該區間內未偵測到明顯人臉。")

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
                self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.project.subtitles, self.video_source.current_time)

    def _on_strength_changed(self, val: int):
        row = self.track_list.currentRow()
        if 0 <= row < len(self.project.tracks):
            self.project.tracks[row].mask.strength = val
            if self.current_frame_rgb is not None and self.video_source:
                self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.project.subtitles, self.video_source.current_time)

    def _delete_selected_track(self):
        row = self.track_list.currentRow()
        if 0 <= row < len(self.project.tracks):
            del self.project.tracks[row]
            self._refresh_track_list()
            if self.current_frame_rgb is not None and self.video_source:
                self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.project.subtitles, self.video_source.current_time)

    # ──── 即時聽打字幕系統 ────
    def _add_current_transcribe(self):
        if not self.video_source:
            return
        text = self.edit_sub_text.text().strip()
        if not text:
            return
            
        cur_t = self.video_source.current_time
        end_t = min(self.video_source.duration, cur_t + 3.0)
        
        new_id = len(self.project.subtitles) + 1
        item = SubtitleItem(id=new_id, start_sec=cur_t, end_sec=end_t, text=text)
        self.project.subtitles.append(item)
        self.project.subtitles.sort(key=lambda s: s.start_sec)
        
        self.edit_sub_text.clear()
        self._refresh_sub_list()
        self.seek_to(cur_t)

    def _refresh_sub_list(self):
        self.sub_list.blockSignals(True)
        self.sub_list.clear()
        for idx, sub in enumerate(self.project.subtitles):
            s_str = f"{int(sub.start_sec//60):02d}:{int(sub.start_sec%60):02d}"
            e_str = f"{int(sub.end_sec//60):02d}:{int(sub.end_sec%60):02d}"
            item = QListWidgetItem(f"[{s_str}~{e_str}] {sub.text}")
            self.sub_list.addItem(item)
        self.sub_list.blockSignals(False)

    def _on_sub_selection_changed(self, row: int):
        if 0 <= row < len(self.project.subtitles):
            sub = self.project.subtitles[row]
            self.seek_to(sub.start_sec)

    def _set_sub_in_point(self):
        row = self.sub_list.currentRow()
        if 0 <= row < len(self.project.subtitles) and self.video_source:
            self.project.subtitles[row].start_sec = self.video_source.current_time
            self._refresh_sub_list()
            self.seek_to(self.video_source.current_time)

    def _set_sub_out_point(self):
        row = self.sub_list.currentRow()
        if 0 <= row < len(self.project.subtitles) and self.video_source:
            self.project.subtitles[row].end_sec = max(self.project.subtitles[row].start_sec + 0.5, self.video_source.current_time)
            self._refresh_sub_list()
            self.seek_to(self.video_source.current_time)

    def _delete_selected_sub(self):
        row = self.sub_list.currentRow()
        if 0 <= row < len(self.project.subtitles):
            del self.project.subtitles[row]
            self._refresh_sub_list()
            if self.current_frame_rgb is not None and self.video_source:
                self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.project.subtitles, self.video_source.current_time)

    def _export_srt(self):
        if not self.project.subtitles:
            QMessageBox.warning(self, "提示", "目前尚無聽打字幕可匯出。")
            return
        out_path, _ = QFileDialog.getSaveFileName(self, "匯出 SRT 字幕檔", "subtitles.srt", "SRT Files (*.srt)")
        if out_path:
            ok = SubtitleManager.export_srt_file(self.project.subtitles, out_path)
            if ok:
                QMessageBox.information(self, "匯出成功", f"SRT 字幕檔已成功儲存至：\n{out_path}")
            else:
                QMessageBox.critical(self, "匯出失敗", "儲存 SRT 檔案時發生錯誤。")

    def _set_in_point(self):
        if self.video_source and self.project.work_range:
            self.project.work_range.in_time = self.video_source.current_time
            self._update_timeline_state()

    def _set_out_point(self):
        if self.video_source and self.project.work_range:
            self.project.work_range.out_time = self.video_source.current_time
            self._update_timeline_state()

    def _reset_work_range(self):
        if self.video_source and self.project.work_range:
            self.project.work_range.in_time = 0.0
            self.project.work_range.out_time = self.video_source.duration
            self._update_timeline_state()

    def _on_range_drag_selected(self, in_time: float, out_time: float):
        if self.video_source and self.project.work_range:
            self.project.work_range.in_time = in_time
            self.project.work_range.out_time = out_time
            self.seek_to(in_time)
            self._update_timeline_state()

    def _generate_default_export_name(self, mode: str = "redacted") -> str:
        if not self.video_source or not self.project.source:
            return f"output_{mode}.mp4"
            
        src_path = self.project.source.path
        dir_name = os.path.dirname(src_path)
        base_name = os.path.splitext(os.path.basename(src_path))[0]
        
        in_s = int(self.project.work_range.in_time) if self.project.work_range else 0
        out_s = int(self.project.work_range.out_time) if self.project.work_range else int(self.video_source.duration)
        
        if mode == "fast":
            suggested = f"{base_name}_cut_{in_s}s_to_{out_s}s.mp4"
        else:
            time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            suggested = f"{base_name}_redacted_{in_s}s_to_{out_s}s_{time_str}.mp4"
            
        return os.path.join(dir_name, suggested)

    def export_fast_copy(self):
        self._stop_playback()
        if not self.video_source or not self.project.source:
            return
            
        default_name = self._generate_default_export_name(mode="fast")
        out_path, _ = QFileDialog.getSaveFileName(self, "儲存剪輯影片 (無馬賽克)", default_name, "MP4 Files (*.mp4)")
        if not out_path:
            return
            
        in_t = self.project.work_range.in_time if self.project.work_range else 0.0
        out_t = self.project.work_range.out_time if self.project.work_range else self.video_source.duration
        success = FastCopyExporter.export(self.project.source.path, in_t, out_t, out_path)
        if success:
            QMessageBox.information(self, "匯出成功", f"剪輯完成！已儲存至：\n{out_path}")
        else:
            QMessageBox.critical(self, "匯出失敗", "快速剪輯執行失敗。")

    def export_render(self):
        self._stop_playback()
        if not self.video_source or not self.project.source:
            return
            
        default_name = self._generate_default_export_name(mode="redacted")
        out_path, _ = QFileDialog.getSaveFileName(self, "儲存馬賽克去識別影片", default_name, "MP4 Files (*.mp4)")
        if not out_path:
            return

        progress_dialog = QProgressDialog("正在壓制馬賽克去識別影片，請稍候...", "取消", 0, 100, self)
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
