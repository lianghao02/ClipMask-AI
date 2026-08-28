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
from PySide6.QtGui import QImage, QKeySequence, QShortcut, QDragEnterEvent, QDropEvent, QIcon, QColor
from PySide6.QtCore import Qt, QThread, Signal, Slot
from .video_view import VideoGraphicsView
from .timeline import TimelineWidget
from .styles import MORANDI_JOURNAL_QSS
from ..models.project import ProjectState, Track, Keyframe, MaskConfig, WorkRange
from ..media.source import VideoSource, ThumbnailExtractor
from ..track.tracker import MicroTracker
from ..track.coverage import CoverageAnalyzer
from ..ai.detector import FaceDetector
from ..ai.subtitles import SubtitleManager, SubtitleItem
from ..ai.vad import VoiceActivityDetector, SpeechSegment
from ..export.exporter import FastCopyExporter, RenderExporter

# ── 1. 播放背景 Worker (音畫絕對同步與實時音訊輸出) ──
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
        self.audio_engine = None
        self.audio_source = None

    def stop(self):
        self._is_running = False
        if self.audio_engine:
            self.audio_engine.stop()
        if self.audio_source:
            self.audio_source.close()
        self.wait()

    def run(self):
        try:
            from ..media.audio import AudioPlaybackEngine
            from ..media.source import AudioSource
            
            v_source = VideoSource(self.video_path)
            v_source.seek_exact(self.start_time)
            
            self.audio_source = AudioSource(self.video_path)
            if self.audio_source.has_audio:
                self.audio_source.seek_exact(self.start_time)
                self.audio_engine = AudioPlaybackEngine(sample_rate=44100, channels=2)

            # 使用高精度系統 Master Clock 同步播放
            start_wall = time.perf_counter()
            start_pts = self.start_time
            
            while self._is_running:
                # 1. 讀取並推送音訊 (每次讀取音訊封包)
                if self.audio_source and self.audio_source.has_audio and self.audio_engine:
                    a_chunk = self.audio_source.read_next_chunk()
                    if a_chunk is not None:
                        self.audio_engine.write(a_chunk)

                # 2. 讀取並發射視訊影格
                frame = v_source.read_next_frame()
                if frame is None or not self._is_running:
                    break
                    
                cur_time = v_source.current_time
                self.frame_ready.emit(frame, cur_time)
                
                # 計算與系統 Master Clock 的精準時間差 (A/V Sync)
                expected_elapsed = cur_time - start_pts
                actual_elapsed = time.perf_counter() - start_wall
                delay_needed = expected_elapsed - actual_elapsed
                
                if delay_needed > 0.002:
                    time.sleep(delay_needed)
                
            if self.audio_engine:
                self.audio_engine.stop()
            if self.audio_source:
                self.audio_source.close()
            v_source.close()
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
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            def on_progress(pct: int):
                self.progress.emit(pct)

            success = RenderExporter.render_export(
                self.project,
                self.output_path,
                progress_callback=on_progress,
                should_cancel=lambda: self._is_cancelled
            )
            self.finished.emit(success, self.output_path)
        except Exception as e:
            self.finished.emit(False, str(e))

# ── 4. 語音活動偵測 (VAD) 背景 Worker ──
class VadWorker(QThread):
    finished = Signal(list)

    def __init__(self, video_path: str):
        super().__init__()
        self.video_path = video_path

    def run(self):
        segments = VoiceActivityDetector.scan_audio_speech_segments(self.video_path)
        self.finished.emit(segments)

# ── 5. 主視窗 ──
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ClipMask-AI — 智慧影音去識別化手帳工作站")
        self.resize(1380, 880)
        self.setStyleSheet(MORANDI_JOURNAL_QSS)
        self.setAcceptDrops(True)

        # 設定應用程式與視窗專屬圖示
        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources", "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        self.project = ProjectState()
        self.video_source: VideoSource = None
        self.current_frame_rgb = None
        self.speech_segments: list = []
        self.playback_worker: PlaybackWorker = None
        self.ai_worker: AiDetectWorker = None
        self.export_worker: ExportWorker = None
        self.vad_worker: VadWorker = None
        self.thumb_extractor: ThumbnailExtractor = None
        
        self.init_ui()
        self.setup_shortcuts()
        self._update_action_state()

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
        self.btn_fast_export = QPushButton("⚡ 快速串流剪輯 (不套用遮蔽)")
        self.btn_fast_export.setToolTip("直接複製串流，不套用遮蔽或字幕；起點可能回退到前一個關鍵影格")
        self.btn_fast_export.clicked.connect(self.export_fast_copy)
        top_bar.addWidget(self.btn_fast_export)

        top_bar.addStretch()
        left_layout.addLayout(top_bar)

        self.lbl_safety_status = QLabel("⚪ 請先載入影片，再建立或檢查遮蔽軌跡。")
        self.lbl_safety_status.setWordWrap(True)
        left_layout.addWidget(self.lbl_safety_status)

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
        self.timeline.seek_fast_requested.connect(self.seek_to_fast)
        self.timeline.step_requested.connect(self.step_frame)
        self.timeline.set_in_point.connect(self._set_context_in)
        self.timeline.set_out_point.connect(self._set_context_out)
        self.timeline.reset_range_requested.connect(self._reset_context_range)
        self.timeline.range_selected.connect(self._on_range_drag_selected)
        self.timeline.prev_keyframe_requested.connect(self._jump_prev_keyframe)
        self.timeline.next_keyframe_requested.connect(self._jump_next_keyframe)
        self.timeline.toggle_keyframe_at_current.connect(self._toggle_keyframe_at_current)
        self.timeline.seek_started.connect(self._begin_timeline_scrub)
        self.timeline.transcript_submitted.connect(self._add_current_transcribe)
        self.timeline.subtitle_selected.connect(self._on_timeline_sub_selected)
        self.timeline.subtitle_range_adjusted.connect(self._on_timeline_sub_adjusted)
        self.edit_sub_text = self.timeline.edit_transcript
        self.edit_sub_text.textChanged.connect(self._on_sub_typing_changed)
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
        self.btn_track_forward = QPushButton("🎯 追蹤2秒")
        self.btn_track_forward.setToolTip("使用 CSRT 追蹤器向後預測並建立關鍵影格")
        self.btn_track_forward.clicked.connect(self._track_selected_forward)
        track_btn_layout.addWidget(self.btn_track_forward)

        self.btn_persist_track = QPushButton("📌 常駐全段")
        self.btn_persist_track.setToolTip("將此遮蔽框鎖定並延伸至整個工作區間 (適合固定站位/背景人物)")
        self.btn_persist_track.clicked.connect(self._persist_selected_track)
        track_btn_layout.addWidget(self.btn_persist_track)

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

        lbl_sub_focus = QLabel("字幕輸入列位於時間軸正下方；點選字幕可由下方共用控制列或時間軸手柄微調起訖。")
        lbl_sub_focus.setWordWrap(True)
        lbl_sub_focus.setStyleSheet("color: #78716c; font-size: 11px;")
        sub_layout.addWidget(lbl_sub_focus)

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
        QShortcut(QKeySequence("I"), self, self._set_context_in)
        QShortcut(QKeySequence("O"), self, self._set_context_out)
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
                
            if self.thumb_extractor:
                self.thumb_extractor.close()
                self.thumb_extractor = None

            self.video_source = VideoSource(path)
            self.thumb_extractor = ThumbnailExtractor(path)
            self.timeline.set_thumbnail_extractor(self.thumb_extractor)

            self.project.source = self.video_source.metadata
            self.project.work_range = WorkRange(0.0, self.video_source.duration)
            self.project.tracks.clear()
            self.project.subtitles.clear()
            
            self.timeline.set_duration(self.video_source.duration)
            self.seek_to(0.0)
            self._refresh_track_list()
            self._refresh_sub_list()
            self.setWindowTitle(f"ClipMask-AI — {os.path.basename(path)}")
            self._update_action_state()
            self._update_safety_status()

            # 啟動背景人聲活動偵測 (VAD)
            self.speech_segments.clear()
            self.vad_worker = VadWorker(path)
            self.vad_worker.finished.connect(self._on_vad_finished)
            self.vad_worker.start()
        except Exception as e:
            QMessageBox.critical(self, "載入影片失敗", f"無法解碼影片檔案：\n{path}\n\n錯誤訊息：{e}")

    def _on_vad_finished(self, segments):
        self.speech_segments = segments
        self._update_timeline_state()

    def seek_to_fast(self, seconds: float):
        """極速粗略跳轉 (滑鼠拖曳時間軸時使用，0 延遲秒刷)"""
        if not self.video_source:
            return
        frame = self.video_source.seek_fast(seconds)
        if frame is not None:
            self.current_frame_rgb = frame
            is_speech = VoiceActivityDetector.find_current_speech_segment(self.speech_segments, self.video_source.current_time) is not None
            self.video_view.update_frame_data(
                frame,
                self.project.tracks,
                self.project.subtitles,
                self.video_source.current_time,
                is_speech_active=is_speech
            )
            self._update_timeline_state()

    def seek_to(self, seconds: float):
        """精準跳轉 (滑鼠放開或指定秒數時使用)"""
        if not self.video_source:
            return
        frame = self.video_source.seek_exact(seconds)
        if frame is not None:
            self.current_frame_rgb = frame
            is_speech = VoiceActivityDetector.find_current_speech_segment(self.speech_segments, self.video_source.current_time) is not None
            self.video_view.update_frame_data(
                frame,
                self.project.tracks,
                self.project.subtitles,
                self.video_source.current_time,
                is_speech_active=is_speech
            )
            self._update_timeline_state()
        if getattr(self, "_resume_after_scrub", False):
            self._resume_after_scrub = False
            self._start_playback()

    def _begin_timeline_scrub(self):
        self._resume_after_scrub = bool(self.playback_worker and self.playback_worker.isRunning())
        if self._resume_after_scrub:
            self._stop_playback()

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
        is_speech = VoiceActivityDetector.find_current_speech_segment(self.speech_segments, current_time) is not None
        self.video_view.update_frame_data(
            frame_rgb,
            self.project.tracks,
            self.project.subtitles,
            current_time,
            is_speech_active=is_speech
        )
        
        # 效能極限優化：播放中僅刷新指針與時間碼，每 200ms 節流刷新全量狀態
        now = time.perf_counter()
        if not hasattr(self, "_last_timeline_full_update") or (now - self._last_timeline_full_update > 0.2):
            self._last_timeline_full_update = now
            self._update_timeline_state()
        else:
            self.timeline.update_time_display(current_time)

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

        selected_sub = self._selected_subtitle()
        selected_sub_id = selected_sub.id if selected_sub else -1
            
        # 計算未覆蓋安全警示區間
        uncovered_ranges = []
        if in_t < out_t:
            if not self.project.tracks:
                uncovered_ranges.append((in_t, out_t))
            else:
                # 簡單計算所有軌跡聯集的覆蓋範圍
                cov_min = min(t.keyframes[0].time for t in self.project.tracks if t.keyframes) if any(t.keyframes for t in self.project.tracks) else in_t
                cov_max = max(t.keyframes[-1].time for t in self.project.tracks if t.keyframes) if any(t.keyframes for t in self.project.tracks) else in_t
                if in_t < cov_min:
                    uncovered_ranges.append((in_t, cov_min))
                if cov_max < out_t:
                    uncovered_ranges.append((cov_max, out_t))

        self.timeline.update_state(
            cur_t, in_t, out_t, kf_times,
            speech_segments=self.speech_segments,
            subtitles=self.project.subtitles,
            selected_sub_id=selected_sub_id,
            uncovered_ranges=uncovered_ranges
        )

    def _on_timeline_sub_selected(self, sub_id: int):
        """時間軸直接點選字幕色塊"""
        for idx, sub in enumerate(self.project.subtitles):
            if sub.id == sub_id:
                self.sub_list.setCurrentRow(idx)
                break

    def _on_timeline_sub_adjusted(self, sub_id: int, new_start: float, new_end: float):
        """時間軸拖曳字幕色塊手柄微調起訖"""
        for idx, sub in enumerate(self.project.subtitles):
            if sub.id == sub_id:
                sub.start_sec = new_start
                sub.end_sec = new_end
                self._refresh_sub_list()
                self.sub_list.blockSignals(True)
                self.sub_list.setCurrentRow(idx)
                self.sub_list.blockSignals(False)
                self._update_edit_context()
                if self.current_frame_rgb is not None and self.video_source:
                    self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.project.subtitles, self.video_source.current_time)
                break

    def _on_user_drawn_rect(self, x: int, y: int, w: int, h: int):
        if not self.video_source:
            return
        cur_t = self.video_source.current_time
        in_t = self.project.work_range.in_time if self.project.work_range else 0.0
        out_t = self.project.work_range.out_time if self.project.work_range else self.video_source.duration
        
        track_id = f"track_{len(self.project.tracks)+1}"
        
        # 智慧建立關鍵影格：自動在當前工作區間前後與當前點鎖定位置 (防脫落、防漏抓)
        kfs = [Keyframe(time=cur_t, pts=self.video_source.current_pts, rect_px=(x, y, w, h), source="manual")]
        if abs(cur_t - in_t) > 0.1:
            kfs.insert(0, Keyframe(time=in_t, pts=int(round(in_t / float(self.video_source.time_base))) if self.video_source.time_base else 0, rect_px=(x, y, w, h), source="manual_in"))
        if abs(out_t - cur_t) > 0.1:
            kfs.append(Keyframe(time=out_t, pts=int(round(out_t / float(self.video_source.time_base))) if self.video_source.time_base else 0, rect_px=(x, y, w, h), source="manual_out"))

        track = Track(
            id=track_id,
            label=f"遮蔽 {len(self.project.tracks)+1} (手動常駐)",
            mask=MaskConfig(
                style="mosaic" if self.combo_style.currentIndex() == 0 else "blur",
                strength=self.spin_strength.value(),
                padding=0.25
            ),
            keyframes=kfs
        )
        self.project.tracks.append(track)
        self._refresh_track_list()
        self.track_list.setCurrentRow(len(self.project.tracks) - 1)
        if self.current_frame_rgb is not None and self.video_source:
            self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.project.subtitles, cur_t)

    def _split_selected_track(self):
        """在當前秒數截斷選取的軌跡 (防鏡頭切換漂移)"""
        row = self.track_list.currentRow()
        if not (0 <= row < len(self.project.tracks)) or not self.video_source:
            QMessageBox.warning(self, "提示", "請先選取一個遮蔽物件。")
            return
            
        track = self.project.tracks[row]
        cur_t = self.video_source.current_time
        
        from ..track.evaluator import TrackEvaluator
        evaluated = TrackEvaluator.evaluate_track_at(track, cur_t, self.video_source.width, self.video_source.height)
        if not evaluated:
            QMessageBox.warning(self, "提示", f"當前時間 ({cur_t:.2f}s) 不在該人物的有效範圍內，無法截斷。")
            return
            
        # 移除 cur_t 之後的所有關鍵影格，並在 cur_t 設置最後一顆關鍵影格
        track.keyframes = [kf for kf in track.keyframes if kf.time < cur_t - 0.05]
        tb = self.video_source.time_base
        pts = int(round(cur_t / float(tb))) if tb else 0
        track.add_or_update_keyframe(cur_t, pts, evaluated, source="split")
        
        self._refresh_track_list()
        self.seek_to(cur_t)
        QMessageBox.information(self, "截斷成功", f"已將 [{track.label}] 截斷於 {cur_t:.2f}s，之後將不再遮蔽。")

    def _merge_with_previous_track(self):
        """將選取的軌跡與前一條軌跡合併為同一人物"""
        row = self.track_list.currentRow()
        if row <= 0 or not (0 <= row < len(self.project.tracks)):
            QMessageBox.warning(self, "提示", "請選取第 2 條以上的軌跡來與前一條合併。")
            return
            
        curr_track = self.project.tracks[row]
        prev_track = self.project.tracks[row - 1]
        
        # 合併關鍵影格
        for kf in curr_track.keyframes:
            prev_track.add_or_update_keyframe(kf.time, kf.pts, kf.rect_px, kf.source)
            
        # 刪除目前的軌跡
        del self.project.tracks[row]
        self._refresh_track_list()
        self.track_list.setCurrentRow(row - 1)
        QMessageBox.information(self, "合併成功", f"已將 [{curr_track.label}] 成功合併至 [{prev_track.label}]！")

    def _persist_selected_track(self):
        """將選取的軌跡擴展為全工作區間常駐鎖定"""
        row = self.track_list.currentRow()
        if not (0 <= row < len(self.project.tracks)) or not self.video_source:
            QMessageBox.warning(self, "提示", "請先選取一個遮蔽物件。")
            return
            
        track = self.project.tracks[row]
        in_t = self.project.work_range.in_time if self.project.work_range else 0.0
        out_t = self.project.work_range.out_time if self.project.work_range else self.video_source.duration
        
        from ..track.evaluator import TrackEvaluator
        cur_t = self.video_source.current_time
        evaluated = TrackEvaluator.evaluate_track_at(track, cur_t, self.video_source.width, self.video_source.height)
        rect = evaluated if evaluated else (track.keyframes[0].rect_px if track.keyframes else (100, 100, 100, 100))
        
        tb = self.video_source.time_base
        in_pts = int(round(in_t / float(tb))) if tb else 0
        out_pts = int(round(out_t / float(tb))) if tb else 0
        
        track.add_or_update_keyframe(in_t, in_pts, rect, source="manual_persist")
        track.add_or_update_keyframe(out_t, out_pts, rect, source="manual_persist")
        self._refresh_track_list()
        self.seek_to(cur_t)
        QMessageBox.information(self, "常駐鎖定", f"已將 [{track.label}] 成功鎖定並延伸至整個工作區間 ({in_t:.2f}s ~ {out_t:.2f}s)！")

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
        self._update_safety_status()

    def _update_action_state(self):
        has_video = self.video_source is not None
        for button in (self.btn_ai_detect, self.btn_toggle_preview, self.btn_render_export, self.btn_fast_export):
            button.setEnabled(has_video)

    def _update_safety_status(self):
        if not self.video_source or not self.project.work_range:
            return
        report = CoverageAnalyzer.analyze(self.project.tracks, self.project.work_range.in_time, self.project.work_range.out_time)
        if report.is_safe_to_continue:
            self.lbl_safety_status.setText("✅ 軌道完整性檢查未發現可判定風險；仍請人工逐段看片。")
            self.lbl_safety_status.setStyleSheet("color: #3b5941; background: #ebf2ea; padding: 6px 8px; border-radius: 6px;")
        else:
            self.lbl_safety_status.setText(f"⚠ 遮蔽檢查發現 {len(report.messages)} 項風險；安全壓制前需確認。")
            self.lbl_safety_status.setStyleSheet("color: #8a3b2e; background: #f8e8e2; padding: 6px 8px; border-radius: 6px; font-weight: 600;")

    def _confirm_redaction_export(self):
        report = CoverageAnalyzer.analyze(self.project.tracks, self.project.work_range.in_time, self.project.work_range.out_time)
        if report.is_safe_to_continue:
            return True
        detail = "\n".join(f"• {message}" for message in report.messages)
        answer = QMessageBox.warning(self, "遮蔽覆蓋檢查", f"偵測到可能造成漏遮蔽的軌道風險：\n\n{detail}\n\n此檢查不能取代人工逐段看片。是否仍要繼續壓制匯出？", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        return answer == QMessageBox.StandardButton.Save

    def _on_track_selection_changed(self, row: int):
        if 0 <= row < len(self.project.tracks):
            # 互斥清除字幕選取，確保共用起訖控制列切換至遮蔽軌跡
            self.sub_list.blockSignals(True)
            self.sub_list.setCurrentRow(-1)
            self.sub_list.clearSelection()
            self.sub_list.blockSignals(False)

            track = self.project.tracks[row]
            self.combo_style.blockSignals(True)
            self.combo_style.setCurrentIndex(0 if track.mask.style == "mosaic" else 1)
            self.combo_style.blockSignals(False)
            
            self.spin_strength.blockSignals(True)
            self.spin_strength.setValue(track.mask.strength)
            self.spin_strength.blockSignals(False)
            
        self._update_timeline_state()
        self._update_edit_context()

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
    def _on_sub_typing_changed(self, text: str):
        if self.current_frame_rgb is not None and self.video_source:
            self.video_view.update_frame_data(
                self.current_frame_rgb,
                self.project.tracks,
                self.project.subtitles,
                self.video_source.current_time,
                live_typing_text=text
            )

    def _add_current_transcribe(self, submitted_text=None):
        if not self.video_source:
            return
        text = (submitted_text if submitted_text is not None else self.edit_sub_text.text()).strip()
        if not text:
            return
            
        cur_t = self.video_source.current_time
        
        # 智慧磁吸：若當前秒數落在某語音區間內，自動對齊該語音段的真實起訖點
        matched_seg = VoiceActivityDetector.find_current_speech_segment(self.speech_segments, cur_t, tolerance=0.3)
        if matched_seg:
            start_t = matched_seg.start_sec
            end_t = min(self.video_source.duration, matched_seg.end_sec)
        else:
            start_t = cur_t
            end_t = min(self.video_source.duration, cur_t + 3.0)
        
        new_id = len(self.project.subtitles) + 1
        item = SubtitleItem(id=new_id, start_sec=start_t, end_sec=end_t, text=text)
        self.project.subtitles.append(item)
        self.project.subtitles.sort(key=lambda s: s.start_sec)
        
        self.edit_sub_text.blockSignals(True)
        self.edit_sub_text.clear()
        self.edit_sub_text.blockSignals(False)
        self.timeline.edit_transcript.clear()
        
        self._refresh_sub_list()
        # 強制重繪當前幀，確保字幕立刻固化顯示在畫布上
        if self.current_frame_rgb is not None:
            self.video_view.update_frame_data(
                self.current_frame_rgb,
                self.project.tracks,
                self.project.subtitles,
                cur_t
            )
        self.seek_to(cur_t)

    def _refresh_sub_list(self):
        self.sub_list.blockSignals(True)
        self.sub_list.clear()
        if not self.project.subtitles:
            empty_item = QListWidgetItem("💡 尚無字幕：請在時間軸正下方打字按 Enter，即可自動建立並對齊發音！")
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            empty_item.setForeground(QColor(140, 130, 120))
            self.sub_list.addItem(empty_item)
        else:
            for idx, sub in enumerate(self.project.subtitles):
                s_str = f"{int(sub.start_sec//60):02d}:{int(sub.start_sec%60):02d}"
                e_str = f"{int(sub.end_sec//60):02d}:{int(sub.end_sec%60):02d}"
                item = QListWidgetItem(f"[{s_str}~{e_str}] {sub.text}")
                self.sub_list.addItem(item)
        self.sub_list.blockSignals(False)

    def _on_sub_selection_changed(self, row: int):
        if 0 <= row < len(self.project.subtitles):
            # 互斥清除遮蔽軌跡選取，確保共用起訖控制列切換至字幕
            self.track_list.blockSignals(True)
            self.track_list.setCurrentRow(-1)
            self.track_list.clearSelection()
            self.track_list.blockSignals(False)

            sub = self.project.subtitles[row]
            self.seek_to(sub.start_sec)
        self._update_edit_context()

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

    def _set_context_in(self):
        if self._selected_subtitle():
            self._set_sub_in_point()
        elif self._selected_track():
            self._set_track_boundary(True)
        else:
            self._set_in_point()
        self._update_edit_context()

    def _set_out_point(self):
        if self.video_source and self.project.work_range:
            self.project.work_range.out_time = self.video_source.current_time
            self._update_timeline_state()

    def _set_context_out(self):
        if self._selected_subtitle():
            self._set_sub_out_point()
        elif self._selected_track():
            self._set_track_boundary(False)
        else:
            self._set_out_point()
        self._update_edit_context()

    def _reset_work_range(self):
        if self.video_source and self.project.work_range:
            self.project.work_range.in_time = 0.0
            self.project.work_range.out_time = self.video_source.duration
            self._update_timeline_state()

    def _selected_subtitle(self):
        row = self.sub_list.currentRow()
        return self.project.subtitles[row] if 0 <= row < len(self.project.subtitles) else None

    def _selected_track(self):
        row = self.track_list.currentRow()
        return self.project.tracks[row] if 0 <= row < len(self.project.tracks) else None

    def _set_track_boundary(self, is_start):
        track = self._selected_track()
        if track and self.video_source and track.keyframes:
            target = track.keyframes[0] if is_start else track.keyframes[-1]
            target.time = self.video_source.current_time
            track.keyframes.sort(key=lambda item: item.time)
            self._refresh_track_list()

    def _reset_context_range(self):
        if not self._selected_subtitle() and not self._selected_track():
            self._reset_work_range()
        self._update_edit_context()

    def _update_edit_context(self):
        if not self.video_source:
            return
        sub = self._selected_subtitle()
        if sub:
            self.timeline.set_edit_context("🎙", f"字幕「{sub.text[:18]}」", sub.start_sec, sub.end_sec, "不適用")
            self.timeline.btn_reset_range.setEnabled(False)
            return
        track = self._selected_track()
        if track and track.keyframes:
            self.timeline.set_edit_context("🎭", f"遮蔽「{track.label}」", track.keyframes[0].time, track.keyframes[-1].time, "不適用")
            self.timeline.btn_reset_range.setEnabled(False)
            return
        work = self.project.work_range
        if work:
            self.timeline.set_edit_context("✂", "影片工作區間", work.in_time, work.out_time, "重設全片")
            self.timeline.btn_reset_range.setEnabled(True)

    def _on_range_drag_selected(self, in_time: float, out_time: float):
        if self.video_source and self.project.work_range:
            self.project.work_range.in_time = in_time
            self.project.work_range.out_time = out_time
            self.seek_to(in_time)
            self._update_timeline_state()
            self._update_safety_status()

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
        answer = QMessageBox.warning(self, "快速串流剪輯", "此模式不套用任何馬賽克或字幕，且起點可能回退到前一個關鍵影格。\n\n若需要精確去識別輸出，請使用「匯出馬賽克影片」。\n\n是否繼續？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
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
        if not self._confirm_redaction_export():
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
        progress_dialog.canceled.connect(self.export_worker.cancel)

        def on_export_finished(success, msg):
            progress_dialog.close()
            if success:
                QMessageBox.information(self, "匯出成功", f"去識別化影片壓制完成！已儲存至：\n{msg}")
            elif self.export_worker and self.export_worker._is_cancelled:
                QMessageBox.information(self, "已取消匯出", "已安全停止壓制，並清除未完成輸出檔。")
            else:
                QMessageBox.critical(self, "匯出失敗", f"影片壓制失敗:\n{msg}")

        self.export_worker.finished.connect(on_export_finished)
        self.export_worker.start()

    def closeEvent(self, event):
        self._stop_playback()
        for worker_name in ("ai_worker", "export_worker", "vad_worker"):
            w = getattr(self, worker_name, None)
            if w and w.isRunning():
                if hasattr(w, "cancel"):
                    w.cancel()
                w.wait()
        if self.video_source:
            self.video_source.close()
        if self.thumb_extractor:
            self.thumb_extractor.close()
        super().closeEvent(event)
