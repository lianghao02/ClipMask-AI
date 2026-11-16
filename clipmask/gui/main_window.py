"""
ClipMask-AI Main Window (直覺化按鈕文字與防呆提示版)
"""
import sys
import os
import subprocess
import time
from datetime import datetime
from typing import List
import cv2
import numpy as np
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QFileDialog, QListWidget, QListWidgetItem, QLabel, QDialog,
    QMessageBox, QSplitter, QProgressBar, QComboBox, QSpinBox, QInputDialog,
    QLineEdit, QButtonGroup, QScrollArea, QFrame, QAbstractSpinBox, QStackedWidget
)
from PySide6.QtGui import QImage, QKeySequence, QShortcut, QDragEnterEvent, QDropEvent, QIcon, QColor
from PySide6.QtCore import Qt, QThread, Signal, Slot, QUrl, QTimer
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from .video_view import VideoGraphicsView
from .timeline import TimelineWidget
from .compress_page import CompressPage
from .styles import MORANDI_JOURNAL_QSS
from ..models.project import ProjectState, Track, Keyframe, MaskConfig, WorkRange
from ..media.source import VideoSource, ThumbnailExtractor
from ..track.tracker import MicroTracker, TrackTargetResult
from ..track.evaluator import TrackEvaluator
from ..track.coverage import CoverageAnalyzer
from ..ai.detector import FaceDetector, renumber_ai_tracks
from ..ai.subtitles import SubtitleManager, SubtitleItem
from ..ai.vad import VoiceActivityDetector, SpeechSegment
from ..export.exporter import FastCopyExporter, RenderExporter, export_exact_cut, validate_destination

# ── 1. 播放背景 Worker (Master Clock 精確畫面同步) ──
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
        self.wait()

    def run(self):
        try:
            v_source = VideoSource(self.video_path)
            v_source.seek_exact(self.start_time)

            # 使用高精度系統 Master Clock 同步播放
            start_wall = time.perf_counter()
            start_pts = self.start_time
            
            while self._is_running:
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
                progress_callback=lambda p: self.progress.emit(p),
                should_cancel=lambda: self._is_cancelled
            )
            source.close()
            # 使用者已取消時不回傳部分結果，避免未完成的軌跡被加入專案。
            if not self._is_cancelled:
                self.finished.emit(tracks)
        except Exception as e:
            self.error.emit(str(e))


class TrackTargetWorker(QThread):
    progress = Signal(int)
    finished = Signal(object, list)
    error = Signal(str)

    def __init__(self, video_path: str, track_snapshot: Track, start_time: float, end_time: float):
        super().__init__()
        self.video_path = video_path
        self.track_snapshot = track_snapshot
        self.start_time = start_time
        self.end_time = end_time
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        source = None
        try:
            source = VideoSource(self.video_path)
            result = MicroTracker.track_target(
                source,
                self.track_snapshot,
                start_time=self.start_time,
                end_time=self.end_time,
                keyframe_interval=0.25,
                use_face_assist=True,
                progress_callback=lambda p: self.progress.emit(p),
                is_cancelled=lambda: self._is_cancelled,
            )
            if not self._is_cancelled:
                self.finished.emit(result, list(self.track_snapshot.keyframes))
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if source is not None:
                source.close()

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

# ── 3a. 精確剪輯背景 Worker（重新編碼、不遮蔽） ──
class ExactCutWorker(ExportWorker):
    def run(self):
        try:
            success = export_exact_cut(
                self.project,
                self.output_path,
                progress_callback=self.progress.emit,
                should_cancel=lambda: self._is_cancelled
            )
            self.finished.emit(success, self.output_path)
        except Exception as e:
            self.finished.emit(False, str(e))

# ── 3b. 快速剪輯背景 Worker（避免大檔案凍結介面） ──
class FastCopyWorker(QThread):
    finished = Signal(bool, str)

    def __init__(self, source_path: str, in_time: float, out_time: float, output_path: str):
        super().__init__()
        self.source_path = source_path
        self.in_time = in_time
        self.out_time = out_time
        self.output_path = output_path

    def run(self):
        try:
            ok = FastCopyExporter.export(self.source_path, self.in_time, self.out_time, self.output_path)
            self.finished.emit(ok, self.output_path)
        except Exception as e:
            self.finished.emit(False, str(e))

# ── 4. 語音活動偵測 (VAD) 背景 Worker ──
class VadWorker(QThread):
    finished = Signal(list)

    def __init__(self, video_path: str):
        super().__init__()
        self.video_path = video_path
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            if not self._is_cancelled:
                segments = VoiceActivityDetector.scan_audio_speech_segments(self.video_path)
                if not self._is_cancelled:
                    self.finished.emit(segments)
        except Exception:
            pass

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".ts", ".wmv", ".flv", ".webm", ".m4v"}


class EmptyDropOverlay(QWidget):
    """未載入影片時顯示於畫布中央的溫暖引導卡片，支援點擊開檔與直接拖曳放入。"""
    file_dropped = Signal(str)
    open_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame()
        card.setObjectName("emptyDropCard")
        card.setMaximumWidth(440)
        card.setMinimumWidth(340)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_lbl = QLabel("🎬")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet("font-size: 40px; background: transparent;")
        layout.addWidget(icon_lbl)

        title_lbl = QLabel("拖曳影片到這裡，或點擊選擇影片")
        title_lbl.setObjectName("emptyDropTitle")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setWordWrap(True)
        layout.addWidget(title_lbl)

        sub_lbl = QLabel("支援 MP4 / MOV / MKV / AVI — 全程本機離線處理，不連網也不上傳資料")
        sub_lbl.setObjectName("emptyDropSub")
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_lbl.setWordWrap(True)
        layout.addWidget(sub_lbl)

        layout.addSpacing(6)
        self.btn_open_empty = QPushButton("📂 選擇影片檔案")
        self.btn_open_empty.setObjectName("btn_primary")
        self.btn_open_empty.setMinimumHeight(38)
        self.btn_open_empty.clicked.connect(self.open_clicked.emit)
        layout.addWidget(self.btn_open_empty, alignment=Qt.AlignmentFlag.AlignCenter)

        outer.addWidget(card)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if os.path.splitext(url.toLocalFile())[1].lower() in VIDEO_EXTENSIONS:
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
                if os.path.splitext(fpath)[1].lower() in VIDEO_EXTENSIONS:
                    self.file_dropped.emit(fpath)
                    event.acceptProposedAction()
                    return
        super().dropEvent(event)


class BusyOverlay(QWidget):
    """嵌於預覽畫布中央的柔和進度覆蓋層，取代突兀的系統白色彈窗。"""
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("BusyOverlay { background-color: rgba(246, 243, 236, 0.78); border-radius: 8px; }")
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame()
        card.setObjectName("busyCard")
        card.setMinimumWidth(340)
        card.setMaximumWidth(440)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)

        self.lbl_title = QLabel("處理中...")
        self.lbl_title.setObjectName("busyTitle")
        self.lbl_title.setWordWrap(True)
        layout.addWidget(self.lbl_title)

        self.lbl_detail = QLabel("")
        self.lbl_detail.setObjectName("busyDetail")
        self.lbl_detail.setWordWrap(True)
        layout.addWidget(self.lbl_detail)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        outer.addWidget(card)
        self.hide()

    def _on_cancel_clicked(self):
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("正在停止...")
        self.cancelled.emit()

    def start(self, title: str, detail: str, cancellable: bool = True, indeterminate: bool = False):
        self.lbl_title.setText(title)
        self.lbl_detail.setText(detail)
        if indeterminate:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
        self.btn_cancel.setVisible(cancellable)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setText("取消")
        self.show()
        self.raise_()


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
        self.track_worker: TrackTargetWorker = None
        self.export_worker: ExportWorker = None
        self.vad_worker: VadWorker = None
        self.thumb_extractor: ThumbnailExtractor = None

        # Qt 原生音訊引擎 (零噪音、CD 級音質與 A/V 精準同步)
        self.audio_output = QAudioOutput(self)
        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio_output)

        # 播放 vs Seek 狀態競爭保護機制
        self._seek_generation = 0
        self._is_seeking = False
        self._resume_after_scrub = False
        self.current_work_mode = "mask"
        self._user_hid_sidebar = False
        self._single_key_shortcuts = []
        self._undo_stack = []
        self._redo_stack = []
        self._max_undo = 30
        self._last_undo_merge_key = None
        self._last_undo_time = 0.0
        self._busy_cancel_cb = None
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._update_step_guide)
        
        self.init_ui()
        self._update_review_summary()
        self._update_undo_buttons()
        self.setup_shortcuts()
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)
        self._update_action_state()

    def init_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(6)

        # 上方全寬功能列 (左側：開檔 + 復原/重做 + 分段模式；右側：動作 + 快捷鍵/側欄 + 主匯出)
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(2, 2, 2, 5)
        top_bar.setSpacing(6)

        self.btn_open = QPushButton("📂 開啟影片")
        self.btn_open.setToolTip("開啟影片檔案，或直接將影片拖曳進視窗")
        self.btn_open.clicked.connect(self.open_video)
        top_bar.addWidget(self.btn_open)

        self.btn_undo = QPushButton("↩ 復原")
        self.btn_undo.setProperty("class", "compact-btn")
        self.btn_undo.setToolTip("復原上一步編輯 (Ctrl+Z)")
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(self.undo)
        top_bar.addWidget(self.btn_undo)

        self.btn_redo = QPushButton("↪ 重做")
        self.btn_redo.setProperty("class", "compact-btn")
        self.btn_redo.setToolTip("重做已復原的操作 (Ctrl+Y)")
        self.btn_redo.setEnabled(False)
        self.btn_redo.clicked.connect(self.redo)
        top_bar.addWidget(self.btn_redo)

        top_bar.addSpacing(2)

        # ── 分段式工作模式膠囊（最右為完全獨立的影片壓縮分頁） ──
        mode_track = QFrame()
        mode_track.setObjectName("modeTrack")
        mode_layout = QHBoxLayout(mode_track)
        mode_layout.setContentsMargins(3, 3, 3, 3)
        mode_layout.setSpacing(3)

        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)

        self.btn_mode_mask = QPushButton("🎭 去識別")
        self.btn_mode_mask.setCheckable(True)
        self.btn_mode_mask.setProperty("class", "mode-btn")
        self.btn_mode_mask.setChecked(True)
        self.btn_mode_mask.setToolTip("人臉偵測、軌跡追蹤、遮蔽調整與去識別匯出 (1)")
        self.btn_mode_mask.clicked.connect(lambda: self.set_work_mode("mask"))
        self.mode_group.addButton(self.btn_mode_mask)
        mode_layout.addWidget(self.btn_mode_mask)

        self.btn_mode_cut = QPushButton("✂️ 剪輯")
        self.btn_mode_cut.setCheckable(True)
        self.btn_mode_cut.setProperty("class", "mode-btn")
        self.btn_mode_cut.setToolTip("設定剪輯起終點後，快速或精確剪輯影片 (2)")
        self.btn_mode_cut.clicked.connect(lambda: self.set_work_mode("cut"))
        self.mode_group.addButton(self.btn_mode_cut)
        mode_layout.addWidget(self.btn_mode_cut)

        self.btn_mode_transcribe = QPushButton("🎙️ 字幕")
        self.btn_mode_transcribe.setCheckable(True)
        self.btn_mode_transcribe.setProperty("class", "mode-btn")
        self.btn_mode_transcribe.setToolTip("VAD 語音對齊、聽打字幕與 SRT 匯出 (3)")
        self.btn_mode_transcribe.clicked.connect(lambda: self.set_work_mode("transcribe"))
        self.mode_group.addButton(self.btn_mode_transcribe)
        mode_layout.addWidget(self.btn_mode_transcribe)

        self.btn_mode_compress = QPushButton("📦 影片壓縮")
        self.btn_mode_compress.setCheckable(True)
        self.btn_mode_compress.setProperty("class", "mode-btn")
        self.btn_mode_compress.setToolTip("單純縮小影片檔案容量，預設壓進一張標準 DVD (4)")
        self.btn_mode_compress.clicked.connect(lambda: self.set_work_mode("compress"))
        self.mode_group.addButton(self.btn_mode_compress)
        mode_layout.addWidget(self.btn_mode_compress)

        self.btn_mode_full = QPushButton("🎛️ 完整工作站")
        self.btn_mode_full.setCheckable(True)
        self.btn_mode_full.setProperty("class", "mode-btn")
        self.btn_mode_full.setToolTip("展開全部控制項（去識別、聽打、剪輯）")
        self.btn_mode_full.clicked.connect(lambda: self.set_work_mode("full"))
        self.mode_group.addButton(self.btn_mode_full)
        self.btn_mode_full.hide()  # 不在工具列顯示；模式切換邏輯保留

        top_bar.addWidget(mode_track)
        top_bar.addStretch()

        self.btn_ai_detect = QPushButton("🤖 AI 偵測")
        self.btn_ai_detect.setToolTip("自動偵測工作區間內的所有人臉並建立追蹤軌跡")
        self.btn_ai_detect.setObjectName("btn_ai")
        self.btn_ai_detect.clicked.connect(self.run_ai_face_detection)
        top_bar.addWidget(self.btn_ai_detect)

        self.btn_toggle_preview = QPushButton("👁 預覽 (P): 關")
        self.btn_toggle_preview.setToolTip("切換是否在畫面上即時顯示真實馬賽克/模糊效果 (P)")
        self.btn_toggle_preview.clicked.connect(self._toggle_real_mask_preview)
        top_bar.addWidget(self.btn_toggle_preview)

        # 核心主按鈕：匯出馬賽克去識別影片
        self.btn_render_export = QPushButton("🛡 匯出影片")
        self.btn_render_export.setToolTip("將影片連同遮蔽與字幕一起壓制輸出（有選取區間則只輸出該區間）")
        self.btn_render_export.setObjectName("btn_primary")
        self.btn_render_export.clicked.connect(self.export_render)
        top_bar.addWidget(self.btn_render_export)

        # 剪輯模式按鈕
        self.btn_fast_export = QPushButton("⚡ 快速剪輯")
        self.btn_fast_export.setToolTip("直接複製串流，不套用遮蔽或字幕；起點可能回退到前一個關鍵影格")
        self.btn_fast_export.clicked.connect(self.export_fast_copy)
        top_bar.addWidget(self.btn_fast_export)

        self.btn_exact_cut = QPushButton("🎯 精確剪輯")
        self.btn_exact_cut.setToolTip("重新編碼並精準裁切到所選區間，不套用遮蔽或字幕；耗時較長")
        self.btn_exact_cut.clicked.connect(self.export_exact_cut_video)
        top_bar.addWidget(self.btn_exact_cut)

        self.btn_shortcuts = QPushButton("⌨")
        self.btn_shortcuts.setProperty("class", "compact-btn")
        self.btn_shortcuts.setToolTip("查看快捷鍵一覽 (?)")
        self.btn_shortcuts.clicked.connect(self._show_shortcuts_dialog)
        top_bar.addWidget(self.btn_shortcuts)

        self.btn_toggle_sidebar = QPushButton("◧ 側欄")
        self.btn_toggle_sidebar.setCheckable(True)
        self.btn_toggle_sidebar.setChecked(True)
        self.btn_toggle_sidebar.setProperty("class", "compact-btn")
        self.btn_toggle_sidebar.setToolTip("收合或展開右側遮蔽／字幕面板")
        self.btn_toggle_sidebar.clicked.connect(self._toggle_sidebar)
        top_bar.addWidget(self.btn_toggle_sidebar)

        # 窄視窗保護外殼；預留充足高度避免 Q 版底部立體厚邊框被截切
        toolbar_widget = QWidget()
        toolbar_widget.setLayout(top_bar)
        self.toolbar_scroll = QScrollArea()
        self.toolbar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.toolbar_scroll.setWidgetResizable(True)
        self.toolbar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.toolbar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.toolbar_scroll.setWidget(toolbar_widget)
        self.toolbar_scroll.setFixedHeight(max(52, toolbar_widget.sizeHint().height() + 14))
        main_layout.addWidget(self.toolbar_scroll)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 左側：預覽畫面與時間軸 ──
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        # 單一整合狀態提示列（Q 版遊戲對話捲軸條）
        self.lbl_step_guide = QLabel()
        self.lbl_step_guide.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_step_guide.setWordWrap(True)
        self.lbl_step_guide.setStyleSheet("padding: 7px 14px; border-radius: 11px; background: #fffaf0; border: 2.5px solid #8a5a32; font-size: 14px;")
        left_layout.addWidget(self.lbl_step_guide)

        # 保留內部安全摘要文字欄位以供檢核邏輯使用，畫面不額外佔位
        self.lbl_safety_status = QLabel("⚪ 請先載入影片，再建立或檢查遮蔽軌跡。")
        self.lbl_safety_status.hide()

        # 視訊畫面檢視 + 空狀態引導卡片 + 畫布內嵌進度覆蓋層（包在日系 Q 版圓角木框內）
        video_stage = QFrame()
        video_stage.setObjectName("videoStageFrame")
        video_stage_layout = QGridLayout(video_stage)
        video_stage_layout.setContentsMargins(6, 6, 6, 6)
        video_stage_layout.setSpacing(0)

        self.video_view = VideoGraphicsView()
        self.video_view.rect_drawn.connect(self._on_user_drawn_rect)
        self.video_view.rect_adjusted.connect(self._on_selected_rect_adjusted)
        self.video_view.track_selected.connect(self._on_canvas_track_selected)
        self.video_view.wheel_stepped.connect(self.step_frame)
        self.video_view.file_dropped.connect(self.load_video_path)
        video_stage_layout.addWidget(self.video_view, 0, 0)

        self.empty_overlay = EmptyDropOverlay()
        self.empty_overlay.open_clicked.connect(self.open_video)
        self.empty_overlay.file_dropped.connect(self.load_video_path)
        video_stage_layout.addWidget(self.empty_overlay, 0, 0)

        self.busy_overlay = BusyOverlay()
        self.busy_overlay.cancelled.connect(self._cancel_active_busy_task)
        video_stage_layout.addWidget(self.busy_overlay, 0, 0)

        left_layout.addWidget(video_stage, stretch=1)

        # 專業手帳時間軸控制器 (I/O 永遠只設定影片剪輯工作區間)
        self.timeline = TimelineWidget()
        self.timeline.play_toggled.connect(self._on_play_toggled)
        self.timeline.seek_requested.connect(self.seek_to)
        self.timeline.seek_fast_requested.connect(self.seek_to_fast)
        self.timeline.step_requested.connect(self.step_frame)
        self.timeline.set_in_point.connect(self._set_in_point)
        self.timeline.set_out_point.connect(self._set_out_point)
        self.timeline.reset_range_requested.connect(self._reset_work_range)
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

        # ── 右側：遮蔽管理 + 聽打字幕卡片面板 ──
        self.right_widget = QWidget()
        self.right_widget.setMinimumWidth(360)
        right_layout = QVBoxLayout(self.right_widget)
        right_layout.setContentsMargins(6, 0, 2, 0)
        right_layout.setSpacing(6)

        # 1. 遮蔽物件清單卡片
        self.grp_tracks = QFrame()
        self.grp_tracks.setObjectName("panelCard")
        grp_layout = QVBoxLayout(self.grp_tracks)
        grp_layout.setContentsMargins(10, 8, 10, 10)
        grp_layout.setSpacing(6)

        track_header = QHBoxLayout()
        track_header.setContentsMargins(0, 0, 0, 2)
        lbl_track_title = QLabel("📋 遮蔽人物清單")
        lbl_track_title.setObjectName("panelTitle")
        track_header.addWidget(lbl_track_title)
        track_header.addStretch()
        self.lbl_track_header_badge = QLabel("0 條")
        self.lbl_track_header_badge.setStyleSheet(
            "color: #3d2410; background: #f4e2bd; border: 2px solid #8a5a32; padding: 2px 10px; border-radius: 10px; font-size: 12.5px; font-weight: 700;"
        )
        track_header.addWidget(self.lbl_track_header_badge)
        grp_layout.addLayout(track_header)

        self.track_list = QListWidget()
        self.track_list.setMinimumHeight(235)
        self.track_list.setWordWrap(True)
        self.track_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.track_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.track_list.setToolTip("點擊選取遮蔽項目；連點兩下或按 F2 可重新命名")
        self.track_list.currentRowChanged.connect(self._on_track_selection_changed)
        self.track_list.itemChanged.connect(self._on_track_review_changed)
        self.track_list.itemDoubleClicked.connect(self._rename_track_item)
        grp_layout.addWidget(self.track_list, stretch=1)

        self.lbl_review_summary = QLabel("尚未建立遮蔽：按「AI 偵測」，或在畫面上拖曳框選。")
        self.lbl_review_summary.setWordWrap(True)
        self.lbl_review_summary.setStyleSheet("color: #5e3c14; background: #fdf3dc; border: 2px solid #a67541; padding: 7px 10px; border-radius: 10px; font-size: 13.5px; font-weight: 600;")
        grp_layout.addWidget(self.lbl_review_summary)

        review_btn_row = QHBoxLayout()
        review_btn_row.setSpacing(6)
        self.btn_accept_all = QPushButton("✅ 全部確認")
        self.btn_accept_all.setToolTip("將所有遮蔽項目一次標記為已確認")
        self.btn_accept_all.clicked.connect(lambda: self._set_all_tracks_reviewed(True))
        review_btn_row.addWidget(self.btn_accept_all)

        self.btn_next_review = QPushButton("🔍 下一個")
        self.btn_next_review.setToolTip("跳到下一條待檢查的人臉遮蔽 (N)")
        self.btn_next_review.clicked.connect(self._jump_to_next_pending_review)
        review_btn_row.addWidget(self.btn_next_review)
        grp_layout.addLayout(review_btn_row)

        self.lbl_track_empty_hint = QLabel("點選項目微調；連點兩下可更名")
        self.lbl_track_empty_hint.setWordWrap(True)
        self.lbl_track_empty_hint.setStyleSheet("color: #6b533b; font-size: 13px; padding: 2px 2px;")
        grp_layout.addWidget(self.lbl_track_empty_hint)

        # 單條遮蔽微調卡片（未選取時收合隱藏，減少視覺雜訊）
        self.track_detail_card = QFrame()
        self.track_detail_card.setObjectName("detailCard")
        detail_layout = QVBoxLayout(self.track_detail_card)
        detail_layout.setContentsMargins(9, 6, 9, 6)
        detail_layout.setSpacing(4)

        self.lbl_track_range = QLabel("起訖：--")
        self.lbl_track_range.setStyleSheet("font-size: 13px; font-weight: 700; color: #365675;")
        detail_layout.addWidget(self.lbl_track_range)

        track_range_row = QHBoxLayout()
        track_range_row.setSpacing(6)
        self.btn_track_set_start = QPushButton("設為起點")
        self.btn_track_set_start.setProperty("class", "compact-btn")
        self.btn_track_set_start.setToolTip("把此遮蔽項目的開始時間設為目前畫面秒數")
        self.btn_track_set_start.clicked.connect(lambda: self._set_track_boundary(True))
        track_range_row.addWidget(self.btn_track_set_start)

        self.btn_track_set_end = QPushButton("設為終點")
        self.btn_track_set_end.setProperty("class", "compact-btn")
        self.btn_track_set_end.setToolTip("把此遮蔽項目的結束時間設為目前畫面秒數")
        self.btn_track_set_end.clicked.connect(lambda: self._set_track_boundary(False))
        track_range_row.addWidget(self.btn_track_set_end)
        detail_layout.addLayout(track_range_row)

        track_btn_layout = QHBoxLayout()
        track_btn_layout.setSpacing(5)
        self.btn_track_forward = QPushButton("🎯 追蹤目標")
        self.btn_track_forward.setObjectName("btn_track_primary")
        self.btn_track_forward.setProperty("class", "compact-btn")
        self.btn_track_forward.setToolTip("從目前秒數一路鎖定追蹤此單一目標至區間結束 (F)")
        self.btn_track_forward.clicked.connect(self._track_selected_forward)
        track_btn_layout.addWidget(self.btn_track_forward)

        self.btn_persist_track = QPushButton("📌 常駐全段")
        self.btn_persist_track.setProperty("class", "compact-btn")
        self.btn_persist_track.setToolTip("延伸鎖定到整個剪輯工作區間（適合固定人物）")
        self.btn_persist_track.clicked.connect(self._persist_selected_track)
        track_btn_layout.addWidget(self.btn_persist_track)

        self.btn_del_track = QPushButton("🗑️ 刪除")
        self.btn_del_track.setProperty("class", "compact-btn danger-btn")
        self.btn_del_track.setToolTip("刪除目前選取的遮蔽軌跡 (Delete)")
        self.btn_del_track.clicked.connect(self._delete_selected_track)
        track_btn_layout.addWidget(self.btn_del_track)
        detail_layout.addLayout(track_btn_layout)

        row_style = QHBoxLayout()
        row_style.setSpacing(6)
        row_style.addWidget(QLabel("樣式:"))
        self.combo_style = QComboBox()
        self.combo_style.addItems(["馬賽克", "高斯模糊"])
        self.combo_style.currentIndexChanged.connect(self._on_style_changed)
        row_style.addWidget(self.combo_style, 1)

        row_style.addWidget(QLabel("強度:"))
        self.spin_strength = QSpinBox()
        self.spin_strength.setRange(4, 80)
        self.spin_strength.setValue(20)
        self.spin_strength.valueChanged.connect(self._on_strength_changed)
        row_style.addWidget(self.spin_strength)
        detail_layout.addLayout(row_style)

        self.track_detail_card.hide()
        grp_layout.addWidget(self.track_detail_card)

        self.review_splitter = QSplitter(Qt.Orientation.Vertical)
        self.review_splitter.setChildrenCollapsible(False)
        self.review_splitter.setHandleWidth(8)
        self.review_splitter.addWidget(self.grp_tracks)
        right_layout.addWidget(self.review_splitter)

        # 2. 即時聽打字幕面板卡片
        self.grp_subs = QFrame()
        self.grp_subs.setObjectName("panelCard")
        sub_layout = QVBoxLayout(self.grp_subs)
        sub_layout.setContentsMargins(10, 8, 10, 10)
        sub_layout.setSpacing(6)

        lbl_sub_title = QLabel("🎙️ 字幕清單")
        lbl_sub_title.setObjectName("panelTitle")
        sub_layout.addWidget(lbl_sub_title)

        # 字幕清單 (點選即跳轉重聽；連點兩下可編輯錯字)
        self.sub_list = QListWidget()
        self.sub_list.setMinimumHeight(56)
        self.sub_list.setWordWrap(True)
        self.sub_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sub_list.setToolTip("點選字幕跳轉重聽；連點兩下或按 F2 可修改字幕文字")
        self.sub_list.currentRowChanged.connect(self._on_sub_selection_changed)
        self.sub_list.itemDoubleClicked.connect(self._edit_subtitle_item)
        sub_layout.addWidget(self.sub_list, stretch=1)

        self.lbl_sub_empty_hint = QLabel("點選調整時間；連點兩下可修改文字")
        self.lbl_sub_empty_hint.setWordWrap(True)
        self.lbl_sub_empty_hint.setStyleSheet("color: #6b533b; font-size: 13px;")
        sub_layout.addWidget(self.lbl_sub_empty_hint)

        # 單句字幕微調卡片（未選取時隱藏）
        self.sub_detail_card = QFrame()
        self.sub_detail_card.setObjectName("detailCard")
        sub_detail_layout = QHBoxLayout(self.sub_detail_card)
        sub_detail_layout.setContentsMargins(8, 6, 8, 6)
        sub_detail_layout.setSpacing(5)

        self.btn_sub_set_start = QPushButton("設開始")
        self.btn_sub_set_start.setProperty("class", "compact-btn")
        self.btn_sub_set_start.setToolTip("將這句字幕開始時間設為目前秒數")
        self.btn_sub_set_start.clicked.connect(self._set_sub_in_point)
        sub_detail_layout.addWidget(self.btn_sub_set_start)

        self.btn_sub_set_end = QPushButton("設結束")
        self.btn_sub_set_end.setProperty("class", "compact-btn")
        self.btn_sub_set_end.setToolTip("將這句字幕結束時間設為目前秒數")
        self.btn_sub_set_end.clicked.connect(self._set_sub_out_point)
        sub_detail_layout.addWidget(self.btn_sub_set_end)

        self.btn_del_sub = QPushButton("🗑️ 刪除")
        self.btn_del_sub.setProperty("class", "compact-btn danger-btn")
        self.btn_del_sub.setToolTip("刪除目前選取的字幕條目 (Delete)")
        self.btn_del_sub.clicked.connect(self._delete_selected_sub)
        sub_detail_layout.addWidget(self.btn_del_sub)

        self.sub_detail_card.hide()
        sub_layout.addWidget(self.sub_detail_card)

        self.btn_export_srt = QPushButton("💾 匯出 SRT 字幕檔")
        self.btn_export_srt.setToolTip("將聽打內容匯出為標準繁中 SRT 字幕檔；匯出影片時也會自動燒錄字卡")
        self.btn_export_srt.clicked.connect(self._export_srt)
        sub_layout.addWidget(self.btn_export_srt)

        self.review_splitter.addWidget(self.grp_subs)

        # 3. 快速／精確剪輯右側控制側欄（與去識別、字幕維持統一「左舞台、右控制」版面）
        self.grp_cut = QFrame()
        self.grp_cut.setObjectName("panelCard")
        cut_layout = QVBoxLayout(self.grp_cut)
        cut_layout.setContentsMargins(10, 8, 10, 10)
        cut_layout.setSpacing(8)

        lbl_cut_title = QLabel("✂️ 剪輯區間與匯出")
        lbl_cut_title.setObjectName("panelTitle")
        cut_layout.addWidget(lbl_cut_title)

        cut_range_card = QFrame()
        cut_range_card.setObjectName("detailCard")
        cr_layout = QVBoxLayout(cut_range_card)
        cr_layout.setContentsMargins(10, 8, 10, 8)
        cr_layout.setSpacing(4)

        self.lbl_cut_in = QLabel("起點：--")
        self.lbl_cut_in.setStyleSheet("font-weight: 600; color: #3b2412;")
        cr_layout.addWidget(self.lbl_cut_in)

        self.lbl_cut_out = QLabel("終點：--")
        self.lbl_cut_out.setStyleSheet("font-weight: 600; color: #3b2412;")
        cr_layout.addWidget(self.lbl_cut_out)

        self.lbl_cut_duration = QLabel("片段長度：--")
        self.lbl_cut_duration.setStyleSheet("font-size: 15px; font-weight: 700; color: #c44c1d;")
        cr_layout.addWidget(self.lbl_cut_duration)

        cut_io_hint = QLabel("在時間軸按「設起點 / 設終點」或右鍵拖曳選取要留下的片段。")
        cut_io_hint.setWordWrap(True)
        cut_io_hint.setStyleSheet("font-size: 13px; color: #5e442c; padding-top: 2px;")
        cr_layout.addWidget(cut_io_hint)
        cut_layout.addWidget(cut_range_card)

        fast_card = QFrame()
        fast_card.setObjectName("detailCard")
        fc_layout = QVBoxLayout(fast_card)
        fc_layout.setContentsMargins(10, 8, 10, 8)
        fc_layout.setSpacing(6)
        fc_title = QLabel("⚡ 快速剪輯（秒出不重編碼）")
        fc_title.setStyleSheet("font-size: 14px; font-weight: 700; color: #3d2410;")
        fc_layout.addWidget(fc_title)
        fc_desc = QLabel("2 秒直接複製輸出、畫質零損耗；但起點可能自動對齊前一個關鍵影格。")
        fc_desc.setWordWrap(True)
        fc_desc.setStyleSheet("font-size: 13px; color: #5e442c;")
        fc_layout.addWidget(fc_desc)
        self.btn_cut_panel_fast = QPushButton("⚡ 執行快速剪輯")
        self.btn_cut_panel_fast.clicked.connect(self.export_fast_copy)
        fc_layout.addWidget(self.btn_cut_panel_fast)
        cut_layout.addWidget(fast_card)

        exact_card = QFrame()
        exact_card.setObjectName("detailCard")
        ec_layout = QVBoxLayout(exact_card)
        ec_layout.setContentsMargins(10, 8, 10, 8)
        ec_layout.setSpacing(6)
        ec_title = QLabel("🎯 精確剪輯（秒數一格不差）")
        ec_title.setStyleSheet("font-size: 14px; font-weight: 700; color: #3d2410;")
        ec_layout.addWidget(ec_title)
        ec_desc = QLabel("重新編碼精準裁切至設定的起終點，不包含馬賽克或字幕；耗時稍長。")
        ec_desc.setWordWrap(True)
        ec_desc.setStyleSheet("font-size: 13px; color: #5e442c;")
        ec_layout.addWidget(ec_desc)
        self.btn_cut_panel_exact = QPushButton("🎯 執行精確剪輯")
        self.btn_cut_panel_exact.setObjectName("btn_primary")
        self.btn_cut_panel_exact.clicked.connect(self.export_exact_cut_video)
        ec_layout.addWidget(self.btn_cut_panel_exact)
        cut_layout.addWidget(exact_card)

        cut_layout.addStretch()
        self.grp_cut.hide()
        self.review_splitter.addWidget(self.grp_cut)

        self.review_splitter.setStretchFactor(0, 6)
        self.review_splitter.setStretchFactor(1, 1)
        self.review_splitter.setStretchFactor(2, 1)

        splitter.addWidget(self.right_widget)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        self.workspace_stack = QStackedWidget()
        self.workspace_stack.addWidget(splitter)

        self.compress_page = CompressPage()
        self.compress_page.export_succeeded.connect(self._show_export_success)
        self.workspace_stack.addWidget(self.compress_page)

        main_layout.addWidget(self.workspace_stack, stretch=1)
        self.set_work_mode("mask")


    # ──── 全視窗拖曳開檔 ────
    def _is_busy_worker_running(self) -> bool:
        if hasattr(self, "busy_overlay") and not self.busy_overlay.isHidden():
            return True
        for name in ("ai_worker", "track_worker", "export_worker", "exact_cut_worker", "fast_copy_worker"):
            w = getattr(self, name, None)
            if w and w.isRunning():
                return True
        return False

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self.current_work_mode != "compress" and self._is_busy_worker_running():
            event.ignore()
            return
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                ext = os.path.splitext(url.toLocalFile())[1].lower()
                if ext in VIDEO_EXTENSIONS:
                    event.acceptProposedAction()
                    return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self.current_work_mode != "compress" and self._is_busy_worker_running():
            event.ignore()
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        if self.current_work_mode != "compress" and self._is_busy_worker_running():
            event.ignore()
            return
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                fpath = url.toLocalFile()
                ext = os.path.splitext(fpath)[1].lower()
                if ext in VIDEO_EXTENSIONS:
                    if self.current_work_mode == "compress":
                        self.compress_page.load_file(fpath)
                    else:
                        self.load_video_path(fpath)
                    event.acceptProposedAction()
                    return
        super().dropEvent(event)

    def setup_shortcuts(self):
        self._single_key_shortcuts = []
        self._mode_only_keys = {"1", "2", "3", "4", "?"}

        def add_single_key(key, handler):
            sc = QShortcut(QKeySequence(key), self, handler)
            self._single_key_shortcuts.append(sc)
            return sc

        add_single_key("Space", self.timeline._toggle_play)
        add_single_key("J", lambda: self.step_frame(-1))
        add_single_key("L", lambda: self.step_frame(1))
        add_single_key(Qt.Key.Key_Left, lambda: self._jump_relative_time(-1.0))
        add_single_key(Qt.Key.Key_Right, lambda: self._jump_relative_time(1.0))
        add_single_key(Qt.Key.Key_Up, lambda: self._jump_relative_time(0.1))
        add_single_key(Qt.Key.Key_Down, lambda: self._jump_relative_time(-0.1))
        add_single_key("I", self._set_in_point)
        add_single_key("O", self._set_out_point)
        add_single_key("R", self._reset_work_range)
        add_single_key("[", self._jump_prev_keyframe)
        add_single_key("]", self._jump_next_keyframe)
        add_single_key("K", self._toggle_keyframe_at_current)
        add_single_key("T", self._focus_sub_input)
        add_single_key("P", self._toggle_real_mask_preview)
        add_single_key("N", self._jump_to_next_pending_review)
        add_single_key("F", self._track_selected_forward)
        add_single_key("F2", self._rename_or_edit_active_item)
        add_single_key("1", lambda: self.set_work_mode("mask"))
        add_single_key("2", lambda: self.set_work_mode("cut"))
        add_single_key("3", lambda: self.set_work_mode("transcribe"))
        add_single_key("4", lambda: self.set_work_mode("compress"))
        add_single_key("-", lambda: self.timeline.canvas.set_zoom(self.timeline.canvas.zoom_factor * 0.75))
        add_single_key("=", lambda: self.timeline.canvas.set_zoom(self.timeline.canvas.zoom_factor * 1.35))
        add_single_key("0", self.timeline.canvas.reset_zoom)
        add_single_key("?", self._show_shortcuts_dialog)
        add_single_key(Qt.Key.Key_Delete, self._delete_active_item)
        add_single_key("Ctrl+Z", self.undo)
        add_single_key("Ctrl+Y", self.redo)

        # Esc 永遠保持啟用，用來離開輸入框並恢復單鍵快捷鍵
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._clear_input_focus)

    def _toggle_sidebar(self, checked: bool = None):
        if checked is None:
            if self.current_work_mode == "compress":
                checked = not self.compress_page.right_sidebar.isVisible()
            else:
                checked = not self.right_widget.isVisible()
        self._user_hid_sidebar = not checked
        if self.current_work_mode == "compress":
            self.compress_page.set_sidebar_visible(checked)
        else:
            self.right_widget.setVisible(checked)
        self.btn_toggle_sidebar.blockSignals(True)
        self.btn_toggle_sidebar.setChecked(checked)
        self.btn_toggle_sidebar.blockSignals(False)

    def _show_shortcuts_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("⌨ 快捷鍵一覽")
        dlg.setMinimumWidth(520)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        intro = QLabel("在字幕輸入框或數值欄打字時會自動暫停單鍵快捷鍵；按 <b>Esc</b> 可回到畫布操作。")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #634b33; font-size: 13px;")
        layout.addWidget(intro)

        groups = [
            ("播放與移動", [
                ("Space", "播放 / 暫停"),
                ("J / L", "逐格後退 / 前進"),
                ("← / →", "後退 / 前進 1 秒"),
                ("↑ / ↓", "前進 / 後退 0.1 秒"),
            ]),
            ("遮蔽追蹤與編輯", [
                ("F", "鎖定追蹤選取目標"),
                ("F2", "重新命名選取遮蔽項目"),
                ("N", "跳到下一條待檢查"),
                ("P", "切換即時馬賽克預覽"),
                ("Delete", "刪除目前選取遮蔽或字幕"),
                ("Ctrl+Z / Ctrl+Y", "復原 / 重做編輯"),
            ]),
            ("時間軸與關鍵影格", [
                ("I / O / R", "設起點 / 設終點 / 重設全片"),
                ("K", "目前秒數新增或移除關鍵格"),
                ("[ / ]", "跳到上一個 / 下一個關鍵格"),
                ("= / - / 0", "時間軸放大 / 縮小 / 重設"),
            ]),
            ("工作模式與輸入", [
                ("1 / 2 / 3 / 4", "切換 去識別 / 剪輯 / 字幕 / 壓縮 分頁"),
                ("T", "跳到聽打字幕輸入框"),
                ("Esc", "離開文字輸入框"),
                ("?", "開啟此快捷鍵說明"),
            ]),
        ]

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        for idx, (section, items) in enumerate(groups):
            card = QFrame()
            card.setObjectName("detailCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            card_layout.setSpacing(4)
            sec_lbl = QLabel(section)
            sec_lbl.setStyleSheet("font-weight: 700; color: #3b2412; font-size: 13.5px;")
            card_layout.addWidget(sec_lbl)
            for key_txt, desc in items:
                row = QLabel(f'<code style="background:#e8d5b0;padding:1px 6px;border-radius:4px;">{key_txt}</code>　{desc}')
                row.setStyleSheet("font-size: 13px; color: #563a1f;")
                card_layout.addWidget(row)
            grid.addWidget(card, idx // 2, idx % 2)
        layout.addLayout(grid)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("知道了")
        btn_close.setObjectName("btn_primary")
        btn_close.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)
        dlg.exec()

    # ──── 內嵌進度覆蓋層與 Undo / Redo 狀態 ────
    def _set_busy_ui_locked(self, locked: bool):
        for widget in (self.toolbar_scroll, self.video_view, self.timeline, self.right_widget):
            widget.setEnabled(not locked)

    def _show_busy_overlay(self, title: str, detail: str, on_cancel=None, cancellable: bool = True, indeterminate: bool = False):
        self._busy_cancel_cb = on_cancel
        self._set_busy_ui_locked(True)
        self.busy_overlay.start(title, detail, cancellable=cancellable, indeterminate=indeterminate)

    def _update_busy_progress(self, value: int):
        if not self.busy_overlay.isHidden():
            self.busy_overlay.progress_bar.setValue(int(value))

    def _cancel_active_busy_task(self):
        cb = self._busy_cancel_cb
        if callable(cb):
            cb()

    def _hide_busy_overlay(self):
        self._busy_cancel_cb = None
        self.busy_overlay.hide()
        self._set_busy_ui_locked(False)
        self._update_action_state()

    @staticmethod
    def _clone_tracks(tracks: List[Track]) -> List[Track]:
        cloned = []
        for t in tracks:
            cloned.append(
                Track(
                    id=t.id,
                    label=t.label,
                    type=t.type,
                    enabled=t.enabled,
                    reviewed=t.reviewed,
                    auto_merged=getattr(t, "auto_merged", False),
                    mask=MaskConfig(
                        style=t.mask.style,
                        strength=t.mask.strength,
                        padding=t.mask.padding,
                        privacy_mode=t.mask.privacy_mode,
                    ),
                    keyframes=[
                        Keyframe(time=kf.time, pts=kf.pts, rect_px=tuple(kf.rect_px), source=kf.source)
                        for kf in t.keyframes
                    ],
                )
            )
        return cloned

    @staticmethod
    def _clone_subs(subs: List[SubtitleItem]) -> List[SubtitleItem]:
        return [SubtitleItem(id=s.id, start_sec=s.start_sec, end_sec=s.end_sec, text=s.text) for s in subs]

    def _capture_snapshot(self, desc: str) -> dict:
        sel_track = self._selected_track()
        sel_sub = self._selected_subtitle()
        return {
            "desc": desc,
            "tracks": self._clone_tracks(self.project.tracks),
            "subtitles": self._clone_subs(self.project.subtitles),
            "selected_track_id": sel_track.id if sel_track else None,
            "selected_sub_id": sel_sub.id if sel_sub else None,
        }

    def _push_undo_snapshot(self, desc: str, snapshot: dict, merge_key: str = None):
        now = time.perf_counter()
        if merge_key and merge_key == self._last_undo_merge_key and (now - self._last_undo_time) < 1.0 and self._undo_stack:
            self._last_undo_time = now
            self._redo_stack.clear()
            self._update_undo_buttons()
            return
        snapshot["desc"] = desc
        self._undo_stack.append(snapshot)
        if len(self._undo_stack) > self._max_undo:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._last_undo_merge_key = merge_key
        self._last_undo_time = now
        self._update_undo_buttons()

    def _push_undo(self, desc: str, merge_key: str = None):
        snap = self._capture_snapshot(desc)
        self._push_undo_snapshot(desc, snap, merge_key=merge_key)

    def _restore_snapshot(self, snapshot: dict):
        self.project.tracks = self._clone_tracks(snapshot["tracks"])
        self.project.subtitles = self._clone_subs(snapshot["subtitles"])
        self._refresh_track_list()
        self._refresh_sub_list()

        target_tid = snapshot.get("selected_track_id")
        target_sid = snapshot.get("selected_sub_id")
        if target_tid is not None:
            for idx, tr in enumerate(self.project.tracks):
                if tr.id == target_tid:
                    self.track_list.setCurrentRow(idx)
                    break
        if target_sid is not None:
            for idx, sb in enumerate(self.project.subtitles):
                if sb.id == target_sid:
                    self.sub_list.setCurrentRow(idx)
                    break

        if self.current_frame_rgb is not None and self.video_source:
            self.video_view.update_frame_data(
                self.current_frame_rgb, self.project.tracks, self.project.subtitles, self.video_source.current_time
            )
        self._update_timeline_state()
        self._update_detail_cards()
        self._update_undo_buttons()

    def _update_undo_buttons(self):
        if hasattr(self, "btn_undo"):
            has_undo = bool(self._undo_stack)
            self.btn_undo.setEnabled(has_undo)
            self.btn_undo.setToolTip(f"復原：{self._undo_stack[-1]['desc']} (Ctrl+Z)" if has_undo else "目前無可復原的操作 (Ctrl+Z)")
        if hasattr(self, "btn_redo"):
            has_redo = bool(self._redo_stack)
            self.btn_redo.setEnabled(has_redo)
            self.btn_redo.setToolTip(f"重做：{self._redo_stack[-1]['desc']} (Ctrl+Y)" if has_redo else "目前無可重做的操作 (Ctrl+Y)")

    def undo(self):
        if not self._undo_stack or not self.busy_overlay.isHidden():
            return
        snap = self._undo_stack.pop()
        redo_snap = self._capture_snapshot(snap["desc"])
        self._redo_stack.append(redo_snap)
        self._last_undo_merge_key = None
        self._restore_snapshot(snap)
        self._flash_status(f"↩ 已復原：{snap['desc']}")

    def redo(self):
        if not self._redo_stack or not self.busy_overlay.isHidden():
            return
        snap = self._redo_stack.pop()
        undo_snap = self._capture_snapshot(snap["desc"])
        self._undo_stack.append(undo_snap)
        self._last_undo_merge_key = None
        self._restore_snapshot(snap)
        self._flash_status(f"↪ 已重做：{snap['desc']}")

    def _sync_shortcut_enabled_state(self, focus_widget=None):
        if focus_widget is None:
            focus_widget = QApplication.focusWidget()
        is_editing = isinstance(focus_widget, (QLineEdit, QAbstractSpinBox))
        in_compress = (self.current_work_mode == "compress")
        for sc in self._single_key_shortcuts:
            if is_editing:
                sc.setEnabled(False)
            elif in_compress:
                sc.setEnabled(sc.key().toString() in self._mode_only_keys)
            else:
                sc.setEnabled(True)

    def _on_focus_changed(self, old, new):
        self._sync_shortcut_enabled_state(new)

    def _clear_input_focus(self):
        fw = QApplication.focusWidget()
        if isinstance(fw, (QLineEdit, QAbstractSpinBox)):
            fw.clearFocus()
        self.setFocus()

    def _focus_sub_input(self):
        if self.edit_sub_text.isVisibleTo(self):
            self.edit_sub_text.setFocus()

    def _delete_active_item(self):
        if self.current_work_mode == "compress":
            return
        sub = self._selected_subtitle()
        track = self._selected_track()
        if (self.sub_list.hasFocus() and sub is not None) or (
            self.current_work_mode == "transcribe" and sub is not None
        ):
            self._delete_selected_sub()
            self._flash_status("🗑 已刪除選取字幕")
        elif track is not None:
            self._delete_selected_track()
            self._flash_status("🗑 已刪除選取遮蔽")

    def _on_canvas_track_selected(self, track):
        for row, item in enumerate(self.project.tracks):
            if item is track:
                self.track_list.setCurrentRow(row)
                break

    def _rename_track_item(self, item: QListWidgetItem):
        row = self.track_list.row(item)
        if not (0 <= row < len(self.project.tracks)):
            return
        track = self.project.tracks[row]
        new_name, ok = QInputDialog.getText(
            self,
            "重新命名遮蔽項目",
            "輸入辨識名稱（例如：張先生、車牌、公文）：",
            text=track.label,
        )
        cleaned = new_name.strip() if ok else ""
        if ok and cleaned and cleaned != track.label:
            self._push_undo("重新命名遮蔽")
            track.label = cleaned
            self.track_list.blockSignals(True)
            item.setText(self._format_track_item_text(track))
            self.track_list.blockSignals(False)
            self._flash_status(f"✏️ 已更名為 [{track.label}]")

    def _rename_selected_track(self):
        row = self.track_list.currentRow()
        if 0 <= row < len(self.project.tracks):
            item = self.track_list.item(row)
            if item is not None:
                self._rename_track_item(item)

    def _edit_subtitle_item(self, item: QListWidgetItem):
        row = self.sub_list.row(item)
        if not (0 <= row < len(self.project.subtitles)):
            return
        sub = self.project.subtitles[row]
        new_text, ok = QInputDialog.getText(
            self,
            "修改字幕文字",
            "編輯這段時間的字幕內容：",
            text=sub.text,
        )
        cleaned = new_text.strip() if ok else ""
        if ok and cleaned and cleaned != sub.text:
            self._push_undo("修改字幕文字")
            sub.text = cleaned
            self._refresh_sub_list()
            self.sub_list.setCurrentRow(row)
            if self.current_frame_rgb is not None and self.video_source:
                self.video_view.update_frame_data(
                    self.current_frame_rgb, self.project.tracks, self.project.subtitles, self.video_source.current_time
                )
            self._flash_status("✏️ 已更新字幕文字")

    def _edit_selected_subtitle(self):
        row = self.sub_list.currentRow()
        if 0 <= row < len(self.project.subtitles):
            item = self.sub_list.item(row)
            if item is not None:
                self._edit_subtitle_item(item)

    def _rename_or_edit_active_item(self):
        if self.current_work_mode == "transcribe" or self.sub_list.hasFocus():
            self._edit_selected_subtitle()
        else:
            self._rename_selected_track()

    def _jump_relative_time(self, dt: float):
        if not self.video_source:
            return
        self._stop_playback()
        target_t = max(0.0, min(self.video_source.duration, self.video_source.current_time + dt))
        self.seek_to(target_t)

    def set_work_mode(self, mode: str):
        """切換工作模式分頁 (單一視窗元件開合與壓縮分頁切換)
        mode: 'mask' | 'transcribe' | 'cut' | 'compress' | 'full'
        """
        self.current_work_mode = mode

        # 同步按鈕勾選狀態
        self.btn_mode_mask.setChecked(mode == "mask")
        self.btn_mode_transcribe.setChecked(mode == "transcribe")
        self.btn_mode_cut.setChecked(mode == "cut")
        self.btn_mode_compress.setChecked(mode == "compress")
        self.btn_mode_full.setChecked(mode == "full")

        if mode == "compress":
            self._stop_playback()
            show_sidebar = not self._user_hid_sidebar
            self.btn_open.show()
            self.btn_undo.hide()
            self.btn_redo.hide()
            self.btn_ai_detect.hide()
            self.btn_toggle_preview.hide()
            self.btn_render_export.hide()
            self.btn_fast_export.hide()
            self.btn_exact_cut.hide()
            self.btn_toggle_sidebar.show()
            self.btn_toggle_sidebar.setEnabled(True)
            self.btn_toggle_sidebar.blockSignals(True)
            self.btn_toggle_sidebar.setChecked(show_sidebar)
            self.btn_toggle_sidebar.blockSignals(False)
            self.compress_page.set_sidebar_visible(show_sidebar)
            self.workspace_stack.setCurrentIndex(1)
            self._sync_shortcut_enabled_state()
            return

        self.workspace_stack.setCurrentIndex(0)
        self.btn_open.show()
        self.btn_undo.show()
        self.btn_redo.show()
        self.btn_toggle_sidebar.show()

        if hasattr(self, "lbl_step_guide"):
            self._update_step_guide()

        # 時間軸控制項開合
        self.timeline.set_mode(mode)
        show_sidebar = not self._user_hid_sidebar
        self.right_widget.setVisible(show_sidebar)
        self.btn_toggle_sidebar.setEnabled(True)
        self.btn_toggle_sidebar.blockSignals(True)
        self.btn_toggle_sidebar.setChecked(show_sidebar)
        self.btn_toggle_sidebar.blockSignals(False)

        if mode == "mask":
            self.btn_ai_detect.show()
            self.btn_toggle_preview.show()
            self.btn_render_export.show()
            self.btn_fast_export.hide()
            self.btn_exact_cut.hide()

            self.grp_tracks.show()
            self.grp_subs.hide()
            self.grp_cut.hide()
        elif mode == "transcribe":
            self.btn_ai_detect.hide()
            self.btn_toggle_preview.hide()
            self.btn_render_export.show()
            self.btn_fast_export.hide()
            self.btn_exact_cut.hide()

            self.grp_tracks.hide()
            self.grp_subs.show()
            self.grp_cut.hide()
        elif mode == "cut":
            self.btn_ai_detect.hide()
            self.btn_toggle_preview.hide()
            self.btn_render_export.hide()
            self.btn_fast_export.hide()
            self.btn_exact_cut.hide()

            self.grp_tracks.hide()
            self.grp_subs.hide()
            self.grp_cut.show()
            self._update_cut_summary()
        else:  # full
            self.btn_ai_detect.show()
            self.btn_toggle_preview.show()
            self.btn_render_export.show()
            self.btn_fast_export.show()
            self.btn_exact_cut.show()

            self.grp_tracks.show()
            self.grp_subs.show()
            self.grp_cut.hide()

        self._sync_shortcut_enabled_state()

    def _toggle_real_mask_preview(self):
        self.video_view.show_real_mask_preview = not self.video_view.show_real_mask_preview
        txt = "👁 預覽 (P): 開" if self.video_view.show_real_mask_preview else "👁 預覽 (P): 關"
        self.btn_toggle_preview.setText(txt)
        if self.current_frame_rgb is not None and self.video_source:
            self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.project.subtitles, self.video_source.current_time)

    def open_video(self):
        if self.current_work_mode == "compress":
            self.compress_page.choose_video_file()
            return
        path, _ = QFileDialog.getOpenFileName(self, "選擇影片", "", "Video Files (*.mp4 *.mkv *.mov *.avi *.ts *.wmv *.webm *.flv *.m4v)")
        if path:
            self.load_video_path(path)

    def load_video_path(self, path: str):
        self._stop_playback()
        if hasattr(self, "media_player") and self.media_player:
            self.media_player.stop()

        # 安全阻斷所有背景工作執行緒，徹底避免 QThread: Destroyed 崩潰
        for worker_name in ("playback_worker", "vad_worker", "ai_worker", "track_worker", "export_worker", "exact_cut_worker", "fast_copy_worker"):
            w = getattr(self, worker_name, None)
            if w and w.isRunning():
                if hasattr(w, "cancel"):
                    w.cancel()
                elif hasattr(w, "stop"):
                    w.stop()
                w.wait(1000)
            setattr(self, worker_name, None)
        self._hide_busy_overlay()

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
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._update_undo_buttons()
            self.empty_overlay.hide()
            
            self.timeline.set_duration(self.video_source.duration)
            self.seek_to(0.0)
            self._refresh_track_list()
            self._refresh_sub_list()
            self.setWindowTitle(f"ClipMask-AI — {os.path.basename(path)}")
            self._update_action_state()
            self._update_safety_status()

            # 設定 Qt 原生音訊來源
            self.media_player.setSource(QUrl.fromLocalFile(path))

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
        # 若播放中進行快速拖曳 Seek，暫停播放以防雙執行緒競爭
        if self.playback_worker and self.playback_worker.isRunning():
            self._resume_after_scrub = True
            self._stop_playback()

        self._seek_generation += 1
        self._is_seeking = True

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
        """精準跳轉 (滑鼠放開、點擊時間軸或指定秒數時使用)"""
        if not self.video_source:
            return
        # 檢查是否原本在播放中
        was_playing = bool(self.playback_worker and self.playback_worker.isRunning())
        should_resume = was_playing or getattr(self, "_resume_after_scrub", False)
        if was_playing:
            self._stop_playback()

        self._seek_generation += 1
        self._is_seeking = False

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
            
        # 同步音訊播放位置
        if self.media_player:
            self.media_player.setPosition(int(round(seconds * 1000)))

        self._resume_after_scrub = False
        if should_resume:
            self._start_playback()

    def _begin_timeline_scrub(self):
        self._is_seeking = True
        self._seek_generation += 1
        if self.playback_worker and self.playback_worker.isRunning():
            self._resume_after_scrub = True
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
            
        # 同步啟動 Qt 原生音訊播放
        if self.media_player:
            self.media_player.setPosition(int(round(cur_t * 1000)))
            self.media_player.play()

        self._seek_generation += 1
        current_gen = self._seek_generation
        self._is_seeking = False

        self.playback_worker = PlaybackWorker(
            self.video_source.video_path,
            cur_t,
            self.video_source.fps
        )
        self.playback_worker.frame_ready.connect(lambda f, t, g=current_gen: self._on_worker_frame(f, t, g))
        self.playback_worker.finished.connect(self._on_worker_finished)
        self.playback_worker.start()
        self.timeline.set_playing_state(True)

    def _stop_playback(self):
        self._seek_generation += 1
        if self.media_player:
            self.media_player.pause()
        if self.playback_worker and self.playback_worker.isRunning():
            self.playback_worker.stop()
            self.playback_worker = None
        self.timeline.set_playing_state(False)

    @Slot(np.ndarray, float)
    def _on_worker_frame(self, frame_rgb: np.ndarray, current_time: float, generation: int = -1):
        # 阻斷過期訊號與 Seeking 期間干擾
        if generation != -1 and generation != self._seek_generation:
            return
        if self._is_seeking:
            return

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
        pending_ranges = []
        reviewed_ranges = []
        if in_t < out_t:
            uncovered_ranges = list(CoverageAnalyzer.analyze(self.project.tracks, in_t, out_t).uncovered_ranges)

        for track in self.project.tracks:
            if not track.keyframes:
                continue
            track_range = (track.keyframes[0].time, track.keyframes[-1].time)
            if track.reviewed:
                reviewed_ranges.append(track_range)
            else:
                pending_ranges.append(track_range)

        self.timeline.update_state(
            cur_t, in_t, out_t, kf_times,
            speech_segments=self.speech_segments,
            subtitles=self.project.subtitles,
            selected_sub_id=selected_sub_id,
            uncovered_ranges=uncovered_ranges,
            pending_ranges=pending_ranges,
            reviewed_ranges=reviewed_ranges,
        )
        self._update_cut_summary()

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
                self._push_undo("調整字幕時間", merge_key=f"sub_drag:{sub_id}")
                sub.start_sec = new_start
                sub.end_sec = new_end
                self._refresh_sub_list()
                self.sub_list.blockSignals(True)
                self.sub_list.setCurrentRow(idx)
                self.sub_list.blockSignals(False)
                self._update_detail_cards()
                if self.current_frame_rgb is not None and self.video_source:
                    self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.project.subtitles, self.video_source.current_time)
                break

    def _on_user_drawn_rect(self, x: int, y: int, w: int, h: int):
        if not self.video_source:
            return
        self._push_undo("新增手動遮蔽")
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
        self._flash_status("💡 已建立遮蔽框：固定位置可直接用；若目標會移動，按「🎯 追蹤目標」或快捷鍵 F 一路跟著走")

    def _on_selected_rect_adjusted(self, x: int, y: int, w: int, h: int):
        """在畫面上直接拖移或縮放選中藍框後，於目前時間寫入人工關鍵格。"""
        if not self.video_source:
            return
        track = self._selected_track()
        if not track:
            return
        self._push_undo("調整遮蔽框位置")
        cur_t = self.video_source.current_time
        raw_rect = TrackEvaluator.unpad_rect((x, y, w, h), track.mask.padding)
        track.add_or_update_keyframe(
            time=cur_t,
            pts=self.video_source.current_pts,
            rect_px=raw_rect,
            source="manual",
        )
        self._refresh_track_list()
        if self.current_frame_rgb is not None:
            self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.project.subtitles, cur_t)
        self._flash_status(f"✏️ 已在 {cur_t:.2f}s 更新 [{track.label}] 的位置與大小")

    def _split_selected_track(self):
        """在當前秒數截斷選取的軌跡 (防鏡頭切換漂移)"""
        row = self.track_list.currentRow()
        if not (0 <= row < len(self.project.tracks)) or not self.video_source:
            QMessageBox.warning(self, "提示", "請先選取一個遮蔽物件。")
            return
            
        track = self.project.tracks[row]
        cur_t = self.video_source.current_time
        
        from ..track.evaluator import TrackEvaluator
        evaluated = TrackEvaluator.evaluate_raw_rect_at(track, cur_t)
        if not evaluated:
            QMessageBox.warning(self, "提示", f"當前時間 ({cur_t:.2f}s) 不在該人物的有效範圍內，無法截斷。")
            return
            
        self._push_undo("截斷軌跡")
        # 移除 cur_t 之後的所有關鍵影格，並在 cur_t 設置最後一顆關鍵影格
        track.keyframes = [kf for kf in track.keyframes if kf.time < cur_t - 0.05]
        tb = self.video_source.time_base
        pts = int(round(cur_t / float(tb))) if tb else 0
        track.add_or_update_keyframe(cur_t, rect_px=evaluated, pts=pts, source="split")
        
        self._refresh_track_list()
        self.seek_to(cur_t)
        self._flash_status(f"✅ 已將 [{track.label}] 截斷於 {cur_t:.2f}s")

    def _merge_with_previous_track(self):
        """將選取的軌跡與前一條軌跡合併為同一人物"""
        row = self.track_list.currentRow()
        if row <= 0 or not (0 <= row < len(self.project.tracks)):
            QMessageBox.warning(self, "提示", "請選取第 2 條以上的軌跡來與前一條合併。")
            return
            
        self._push_undo("合併遮蔽軌跡")
        curr_track = self.project.tracks[row]
        prev_track = self.project.tracks[row - 1]
        
        # 合併關鍵影格
        for kf in curr_track.keyframes:
            prev_track.add_or_update_keyframe(kf.time, rect_px=kf.rect_px, pts=kf.pts, source=kf.source)
            
        # 刪除目前的軌跡
        del self.project.tracks[row]
        self._refresh_track_list()
        self.track_list.setCurrentRow(row - 1)
        self._flash_status(f"✅ 已將 [{curr_track.label}] 合併至 [{prev_track.label}]")

    def _persist_selected_track(self):
        """將選取的軌跡擴展為全工作區間常駐鎖定"""
        row = self.track_list.currentRow()
        if not (0 <= row < len(self.project.tracks)) or not self.video_source:
            QMessageBox.warning(self, "提示", "請先選取一個遮蔽物件。")
            return
            
        self._push_undo("常駐全段鎖定")
        track = self.project.tracks[row]
        in_t = self.project.work_range.in_time if self.project.work_range else 0.0
        out_t = self.project.work_range.out_time if self.project.work_range else self.video_source.duration
        
        from ..track.evaluator import TrackEvaluator
        cur_t = self.video_source.current_time
        evaluated = TrackEvaluator.evaluate_raw_rect_at(track, cur_t)
        rect = evaluated if evaluated else (track.keyframes[0].rect_px if track.keyframes else (100, 100, 100, 100))
        
        tb = self.video_source.time_base
        in_pts = int(round(in_t / float(tb))) if tb else 0
        out_pts = int(round(out_t / float(tb))) if tb else 0
        
        track.add_or_update_keyframe(in_t, rect_px=rect, pts=in_pts, source="manual_persist")
        track.add_or_update_keyframe(out_t, rect_px=rect, pts=out_pts, source="manual_persist")
        self._refresh_track_list()
        self.seek_to(cur_t)
        self._flash_status(f"📌 已將 [{track.label}] 常駐鎖定於工作區間 ({in_t:.1f}s ~ {out_t:.1f}s)")

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
        self._push_undo("標記或移除關鍵影格")
        track = self.project.tracks[row]
        cur_t = self.video_source.current_time
        
        if track.remove_keyframe_at(cur_t, tolerance=0.1):
            msg = f"🗑 已移除 {cur_t:.2f}s 的關鍵影格"
        else:
            from ..track.evaluator import TrackEvaluator
            evaluated = TrackEvaluator.evaluate_raw_rect_at(track, cur_t)
            rect = evaluated if evaluated else (100, 100, 100, 100)
            track.add_or_update_keyframe(cur_t, rect, self.video_source.current_pts, source="manual")
            msg = f"🔷 已在 {cur_t:.2f}s 標記關鍵影格"
            
        self._refresh_track_list()
        self.seek_to(cur_t)
        self._flash_status(msg)

    def _apply_tracked_keyframes(self, track: Track, start_time: float, tracked_kfs: List[Keyframe], result: TrackTargetResult):
        """套用鎖定追蹤結果：清掉後續預設常駐佔位點，防止目標移開後又飄回舊位置。"""
        placeholder_sources = {"manual_out", "manual_persist"}
        track.keyframes = [
            kf for kf in track.keyframes
            if not (kf.time > start_time + 0.05 and kf.source in placeholder_sources)
        ]
        for kf in tracked_kfs:
            if kf.time >= start_time - 0.01:
                track.add_or_update_keyframe(
                    time=kf.time,
                    pts=kf.pts,
                    rect_px=kf.rect_px,
                    source=kf.source,
                )
        if track.label.endswith(" (手動常駐)"):
            track.label = track.label[:-7] + " (已追蹤)"

    def _track_selected_forward(self):
        self._stop_playback()
        if self.track_worker and self.track_worker.isRunning():
            return
        row = self.track_list.currentRow()
        if not (0 <= row < len(self.project.tracks)) or not self.video_source:
            QMessageBox.warning(self, "提示", "請先選取一個遮蔽物件。")
            return

        target_track = self.project.tracks[row]
        start_time = self.video_source.current_time
        if TrackEvaluator.evaluate_raw_rect_at(target_track, start_time) is None:
            QMessageBox.warning(self, "提示", f"目前秒數 ({start_time:.2f}s) 不在該遮蔽物件的出現範圍內，請先切到有框選的位置。")
            return

        work_out = self.project.work_range.out_time if self.project.work_range else self.video_source.duration
        end_time = work_out if work_out > start_time + 0.05 else self.video_source.duration

        # 建立供背景追蹤的乾淨快照（先移除 start_time 之後的預設常駐占位點）
        snapshot_kfs = [
            Keyframe(time=kf.time, pts=kf.pts, rect_px=kf.rect_px, source=kf.source)
            for kf in target_track.keyframes
            if not (kf.time > start_time + 0.05 and kf.source in ("manual_out", "manual_persist"))
        ]
        snapshot = Track(
            id=target_track.id,
            label=target_track.label,
            type=target_track.type,
            enabled=target_track.enabled,
            reviewed=target_track.reviewed,
            mask=MaskConfig(
                style=target_track.mask.style,
                strength=target_track.mask.strength,
                padding=target_track.mask.padding,
                privacy_mode=target_track.mask.privacy_mode,
            ),
            keyframes=snapshot_kfs,
        )

        pre_snap = self._capture_snapshot("鎖定追蹤目標")
        self.track_worker = TrackTargetWorker(self.video_source.video_path, snapshot, start_time, end_time)
        self.track_worker.progress.connect(self._update_busy_progress)
        self._show_busy_overlay(
            "🎯 單目標鎖定追蹤中",
            f"正在追蹤 [{target_track.label}]（{start_time:.1f}s → {end_time:.1f}s）...",
            on_cancel=self.track_worker.cancel,
        )

        def on_track_finished(result: TrackTargetResult, tracked_kfs: list):
            self._hide_busy_overlay()
            if target_track not in self.project.tracks:
                return
            if result.success:
                self._push_undo_snapshot("鎖定追蹤目標", pre_snap)
                self._apply_tracked_keyframes(target_track, start_time, tracked_kfs, result)
                self._refresh_track_list()
                self.seek_to(start_time)
                if result.lost_reason == "lost":
                    self._flash_status(
                        f"🎯 已追蹤至 {result.end_time:.1f}s（共 {len(target_track.keyframes)} 點），後續目標疑離開畫面，可拖曳藍框微調後再續追",
                        bg="#fbf4e4",
                        fg="#7c5619",
                    )
                else:
                    self._flash_status(
                        f"🎯 已為 [{target_track.label}] 追蹤至 {result.end_time:.1f}s（共 {len(target_track.keyframes)} 個關鍵格）"
                    )
            else:
                QMessageBox.warning(
                    self,
                    "追蹤失敗",
                    "追蹤器無法穩定鎖定此目標（畫面模糊、太小或目標已離開）。\n建議直接拖曳畫面上的藍框在各秒手動調整位置。",
                )

        def on_track_error(err_msg: str):
            self._hide_busy_overlay()
            QMessageBox.critical(self, "追蹤錯誤", f"追蹤過程發生錯誤:\n{err_msg}")

        self.track_worker.finished.connect(on_track_finished)
        self.track_worker.error.connect(on_track_error)
        self.track_worker.start()

    def run_ai_face_detection(self):
        self._stop_playback()
        if not self.video_source or not self.project.work_range:
            QMessageBox.warning(self, "提示", "請先開啟影片。")
            return

        in_t = self.project.work_range.in_time
        out_t = min(self.video_source.duration, self.project.work_range.out_time)
        dur = out_t - in_t
        pre_snap = self._capture_snapshot("AI 人臉偵測")

        self.ai_worker = AiDetectWorker(self.video_source.video_path, in_t, out_t)
        self.ai_worker.progress.connect(self._update_busy_progress)
        self._show_busy_overlay(
            "🤖 AI 自動人臉偵測",
            f"正在掃描工作區間 {in_t:.1f}s ～ {out_t:.1f}s（共 {dur:.1f} 秒）...",
            on_cancel=self.ai_worker.cancel,
        )

        def on_ai_finished(tracks):
            self._hide_busy_overlay()
            if tracks:
                self._push_undo_snapshot("AI 人臉偵測", pre_snap)
                renumber_ai_tracks(tracks, self.project.tracks)
                self.project.tracks.extend(tracks)
                self._refresh_track_list()
                self.seek_to(in_t)
                self._flash_status(f"🤖 AI 偵測完成：共建立 {len(tracks)} 條人物追蹤軌跡，請在右側逐條檢查並可連點兩下改名")
            else:
                self._flash_status("🤖 AI 偵測完成：該區間未偵測到明顯人臉，可直接於畫面拖曳手動框選", bg="#fff4dc", fg="#8a5b20")

        def on_ai_error(err_msg):
            self._hide_busy_overlay()
            QMessageBox.critical(self, "AI 偵測失敗", f"偵測過程發生錯誤:\n{err_msg}")

        self.ai_worker.finished.connect(on_ai_finished)
        self.ai_worker.error.connect(on_ai_error)
        self.ai_worker.start()

    @staticmethod
    def _format_track_item_text(t: Track) -> str:
        status_tag = "✅ 已確認" if t.reviewed else "🟡 待檢查"
        kf_count = len(t.keyframes)
        style_name = "馬賽克" if t.mask.style == "mosaic" else "模糊"
        if t.keyframes:
            span_str = f"{t.keyframes[0].time:.1f}s～{t.keyframes[-1].time:.1f}s"
        else:
            span_str = "無關鍵格"
        return f"{status_tag} {t.label}\n{span_str} · {kf_count}點 · {style_name}"

    def _refresh_track_list(self):
        selected = self.video_view.selected_track
        self.track_list.blockSignals(True)
        self.track_list.clear()
        for t in self.project.tracks:
            item = QListWidgetItem(self._format_track_item_text(t))
            item.setData(Qt.ItemDataRole.UserRole, t.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if t.reviewed else Qt.CheckState.Unchecked)
            self.track_list.addItem(item)
        for row, track in enumerate(self.project.tracks):
            if track is selected:
                self.track_list.setCurrentRow(row)
                break
        self.track_list.blockSignals(False)
        self.video_view.set_selected_track(self._selected_track())
        self._update_detail_cards()
        self._update_review_summary()
        self._update_timeline_state()
        self._update_safety_status()

    def _on_track_review_changed(self, item: QListWidgetItem):
        row = self.track_list.row(item)
        if 0 <= row < len(self.project.tracks):
            track = self.project.tracks[row]
            new_reviewed = item.checkState() == Qt.CheckState.Checked
            if track.reviewed != new_reviewed:
                self._push_undo("更新確認狀態")
                track.reviewed = new_reviewed
            self.track_list.blockSignals(True)
            item.setText(self._format_track_item_text(track))
            self.track_list.blockSignals(False)
        self._update_review_summary()
        self._update_timeline_state()

    def _set_all_tracks_reviewed(self, reviewed: bool = True):
        """一次性全選同意確認（或取消全選）所有遮蔽軌跡"""
        if not self.project.tracks:
            return
        self._push_undo("全部確認遮蔽" if reviewed else "取消全部確認")
        for track in self.project.tracks:
            track.reviewed = reviewed
        self._refresh_track_list()

    def _update_review_summary(self):
        self._update_step_guide()
        total = len(self.project.tracks)
        reviewed = sum(1 for track in self.project.tracks if track.reviewed)
        pending = total - reviewed
        if hasattr(self, "lbl_track_header_badge"):
            self.lbl_track_header_badge.setText(f"{reviewed}/{total} 已確認" if total else "0 條")
        if not total:
            self.lbl_review_summary.setText("尚未建立遮蔽：按「AI 偵測」，或在畫面上拖曳框選。")
            self.btn_accept_all.setEnabled(False)
            self.btn_next_review.setEnabled(False)
        elif pending:
            self.lbl_review_summary.setText(f"待檢查遮蔽：已確認 {reviewed}/{total}，尚有 {pending} 條。")
            self.btn_accept_all.setEnabled(True)
            self.btn_next_review.setEnabled(True)
        else:
            self.lbl_review_summary.setText(f"遮蔽人工檢查完成：{reviewed}/{total} 條。")
            self.btn_accept_all.setEnabled(True)
            self.btn_next_review.setEnabled(False)

    def _jump_to_next_pending_review(self):
        if not self.video_source:
            return
        for index, track in enumerate(self.project.tracks):
            if not track.reviewed:
                self.track_list.setCurrentRow(index)
                if track.keyframes:
                    self.seek_to(track.keyframes[0].time)
                return

    def _update_cut_summary(self):
        if not hasattr(self, "lbl_cut_in"):
            return
        if not self.video_source or not self.project.work_range:
            self.lbl_cut_in.setText("起點：--")
            self.lbl_cut_out.setText("終點：--")
            self.lbl_cut_duration.setText("裁切後長度：請先開啟影片")
            return
        in_t = self.project.work_range.in_time
        out_t = self.project.work_range.out_time
        dur = max(0.0, out_t - in_t)
        fmt = lambda s: f"{int(s // 60):02d}:{s % 60:05.2f}"
        self.lbl_cut_in.setText(f"起點：{fmt(in_t)}（{in_t:.2f}s）")
        self.lbl_cut_out.setText(f"終點：{fmt(out_t)}（{out_t:.2f}s）")
        self.lbl_cut_duration.setText(f"裁切後長度：{dur:.2f} 秒")

    def _update_action_state(self):
        has_video = self.video_source is not None
        for button in (
            self.btn_ai_detect,
            self.btn_toggle_preview,
            self.btn_render_export,
            self.btn_fast_export,
            self.btn_exact_cut,
            self.btn_cut_panel_fast,
            self.btn_cut_panel_exact,
        ):
            button.setEnabled(has_video)
        self._update_cut_summary()

    def _flash_status(self, message: str, bg: str = "#e2f4df", fg: str = "#1c3d1f"):
        """在單一狀態列短暫顯示操作結果，不跳出打斷操作流程的訊息視窗。"""
        self._flash_timer.stop()
        self.lbl_step_guide.setStyleSheet(
            f"padding: 7px 14px; border-radius: 11px; border: 2.5px solid #8a5a32; background: {bg}; color: {fg}; font-weight: 700; font-size: 14px;"
        )
        self.lbl_step_guide.setText(message)
        self._flash_timer.start(3000)

    def _update_step_guide(self):
        """單一整合狀態列：同時顯示目前步驟、工作區間與安全提醒。"""
        self._flash_timer.stop()

        def render(steps, current, hint, bg="#fffaf0"):
            parts = []
            for index, name in enumerate(steps, start=1):
                if index < current:
                    parts.append(f'<span style="color:#2f5532;">✓ {name}</span>')
                elif index == current:
                    parts.append(f'<b style="color:#b84814;">▶ {name}</b>')
                else:
                    parts.append(f'<span style="color:#8c775c;">{name}</span>')
            self.lbl_step_guide.setStyleSheet(
                f"padding: 7px 14px; border-radius: 11px; border: 2.5px solid #8a5a32; background: {bg}; font-size: 14px;"
            )
            return " → ".join(parts) + f'　｜　<span style="color:#563a1f;">{hint}</span>'

        if not self.video_source or not self.project.work_range:
            self.lbl_step_guide.setText(render(["開啟影片"], 1, "點「開啟影片」或把影片拖進視窗。"))
            return
        work = self.project.work_range
        span = f"區間 {work.in_time:.1f}s～{work.out_time:.1f}s"
        if self.current_work_mode == "cut":
            self.lbl_step_guide.setText(render(
                ["① 設定區間", "② 匯出剪輯"], 2,
                f"{span}；在右側選擇「快速剪輯」或「精確剪輯」。"))
            return
        if self.current_work_mode == "transcribe":
            sub_cnt = len(self.project.subtitles)
            if sub_cnt == 0:
                t_step, t_hint, t_bg = 2, f"{span}；在下方聽打列輸入文字按 Enter 建立字幕（會自動對齊人聲）。", "#f8efda"
            else:
                t_step, t_hint, t_bg = 3, f"{span}；已建立 {sub_cnt} 句字幕（連點兩下清單可改字），可按「匯出 SRT」或「匯出影片」。", "#dfeedd"
            self.lbl_step_guide.setText(render(
                ["① 設定區間", "② 邊聽邊打字", "③ 檢查／匯出"], t_step, t_hint, bg=t_bg
            ).replace("▶ ", "▶ 目前：", 1))
            return
        steps = ["① 設定區間", "② AI 偵測／框選", "③ 確認軌跡", "④ 匯出"]
        tracks = self.project.tracks
        pending = sum(1 for track in tracks if not track.reviewed)
        if not tracks:
            current, hint, bg = 2, f"{span}；按「AI 偵測」或直接在畫面拖曳框選。", "#f8efda"
        elif pending:
            current, hint, bg = 3, f"{span}；還有 {pending} 條遮蔽未確認，請在右側勾選。", "#fbf1d8"
        else:
            report = CoverageAnalyzer.analyze(tracks, work.in_time, work.out_time)
            if report.is_safe_to_continue:
                current, hint, bg = 4, f"{span}；可按「匯出影片」（建議仍逐段看片確認）。", "#dfeedd"
            elif report.critical:
                current, hint, bg = 3, f"⚠ {len(report.critical)} 項嚴重缺口，請補齊關鍵影格後再匯出。", "#f7deda"
            else:
                gap = f"其中 {report.uncovered_seconds:.1f} 秒沒有遮蔽，" if report.uncovered_seconds > 0.05 else f"有 {len(report.warnings)} 項提醒，"
                current, hint, bg = 4, f"ℹ 可匯出；{gap}匯出前會再確認。", "#fbf1d8"
        self.lbl_step_guide.setText(render(steps, current, hint, bg=bg).replace("▶ ", "▶ 目前：", 1))

    def _update_safety_status(self):
        self._update_step_guide()
        self.lbl_safety_status.hide()
        if not self.video_source or not self.project.work_range:
            return
        report = CoverageAnalyzer.analyze(self.project.tracks, self.project.work_range.in_time, self.project.work_range.out_time)
        if report.is_safe_to_continue:
            self.lbl_safety_status.setText("✅ 軌道完整性檢查未發現可判定風險；仍請人工逐段看片。")
        elif report.critical:
            self.lbl_safety_status.setText(f"⚠ 遮蔽檢查發現 {len(report.critical)} 項嚴重缺口，需補齊後才能匯出。")
        else:
            gap = f"{report.uncovered_seconds:.1f} 秒沒有遮蔽，" if report.uncovered_seconds > 0.05 else ""
            self.lbl_safety_status.setText(f"ℹ {len(report.warnings)} 項提醒：{gap}匯出前會再請你確認。")

    @staticmethod
    def _coverage_dialog_texts(report, preview_count: int = 6):
        """對話框只顯示前幾項摘要，完整清單放進可捲動的詳細資料，避免視窗被長清單撐爆。"""
        lines = [f"• {message}" for message in report.messages]
        summary = "\n".join(lines[:preview_count])
        if len(lines) > preview_count:
            summary += f"\n…另有 {len(lines) - preview_count} 項，請展開「顯示詳細資料」。"
        return summary, "\n".join(lines)

    def _confirm_redaction_export(self):
        pending_tracks = [track for track in self.project.tracks if not track.reviewed]
        if pending_tracks:
            QMessageBox.critical(
                self,
                "尚有待檢查遮蔽",
                f"尚有 {len(pending_tracks)} 條遮蔽軌跡未經人工確認。請逐條檢查並勾選「已確認」後再匯出。",
            )
            return False
        report = CoverageAnalyzer.analyze(self.project.tracks, self.project.work_range.in_time, self.project.work_range.out_time)
        if report.is_safe_to_continue:
            return True
        summary, details = self._coverage_dialog_texts(report)
        box = QMessageBox(self)
        box.setDetailedText(details)
        if report.critical:
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle("已阻擋去識別化匯出")
            box.setText(f"遮蔽覆蓋檢查發現嚴重缺口：\n\n{summary}\n\n請補齊關鍵影格並逐段檢視後再匯出。")
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.exec()
            return False
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("匯出前確認")
        box.setText(f"以下情形不會被遮蔽，請確認是否符合預期：\n\n{summary}\n\n此檢查不能取代人工逐段看片。是否仍要匯出？")
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Save

    def _on_track_selection_changed(self, row: int):
        self.video_view.set_selected_track(self._selected_track())
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
            # 目前影格沒有此軌跡時才跳轉；一般切換只更新 overlay，不解碼或 resize。
            if self.video_source and track.enabled and track.keyframes:
                from ..track.evaluator import TrackEvaluator
                if TrackEvaluator.evaluate_raw_rect_at(track, self.video_source.current_time) is None:
                    self.seek_to(track.keyframes[0].time)
            
        self._update_timeline_state()
        self._update_detail_cards()

    def _on_style_changed(self, index: int):
        row = self.track_list.currentRow()
        if 0 <= row < len(self.project.tracks):
            track = self.project.tracks[row]
            new_style = "mosaic" if index == 0 else "blur"
            if track.mask.style != new_style:
                self._push_undo("變更遮蔽樣式")
                track.mask.style = new_style
                item = self.track_list.item(row)
                if item is not None:
                    self.track_list.blockSignals(True)
                    item.setText(self._format_track_item_text(track))
                    self.track_list.blockSignals(False)
            if self.current_frame_rgb is not None and self.video_source:
                self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.project.subtitles, self.video_source.current_time)

    def _on_strength_changed(self, val: int):
        row = self.track_list.currentRow()
        if 0 <= row < len(self.project.tracks):
            track = self.project.tracks[row]
            if track.mask.strength != val:
                self._push_undo("調整遮蔽強度", merge_key=f"strength:{track.id}")
                track.mask.strength = val
            if self.current_frame_rgb is not None and self.video_source:
                self.video_view.update_frame_data(self.current_frame_rgb, self.project.tracks, self.project.subtitles, self.video_source.current_time)

    def _delete_selected_track(self):
        row = self.track_list.currentRow()
        if 0 <= row < len(self.project.tracks):
            self._push_undo("刪除遮蔽軌跡")
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
        self._push_undo("新增字幕")
            
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
        selected = self._selected_subtitle()
        self.sub_list.blockSignals(True)
        self.sub_list.clear()
        if not self.project.subtitles:
            empty_item = QListWidgetItem("💡 尚無字幕：在時間軸下方打字按 Enter 即可建立")
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            empty_item.setForeground(QColor(140, 130, 120))
            self.sub_list.addItem(empty_item)
        else:
            for idx, sub in enumerate(self.project.subtitles):
                s_str = f"{int(sub.start_sec//60):02d}:{int(sub.start_sec%60):02d}"
                e_str = f"{int(sub.end_sec//60):02d}:{int(sub.end_sec%60):02d}"
                item = QListWidgetItem(f"[{s_str}~{e_str}] {sub.text}")
                self.sub_list.addItem(item)
                if sub is selected:
                    self.sub_list.setCurrentRow(idx)
        self.sub_list.blockSignals(False)
        self._update_detail_cards()

    def _on_sub_selection_changed(self, row: int):
        if 0 <= row < len(self.project.subtitles):
            sub = self.project.subtitles[row]
            self.seek_to(sub.start_sec)
        self._update_detail_cards()

    def _set_sub_in_point(self):
        row = self.sub_list.currentRow()
        if 0 <= row < len(self.project.subtitles) and self.video_source:
            self._push_undo("調整字幕開始時間")
            self.project.subtitles[row].start_sec = self.video_source.current_time
            self._refresh_sub_list()
            self.seek_to(self.video_source.current_time)

    def _set_sub_out_point(self):
        row = self.sub_list.currentRow()
        if 0 <= row < len(self.project.subtitles) and self.video_source:
            self._push_undo("調整字幕結束時間")
            self.project.subtitles[row].end_sec = max(self.project.subtitles[row].start_sec + 0.5, self.video_source.current_time)
            self._refresh_sub_list()
            self.seek_to(self.video_source.current_time)

    def _delete_selected_sub(self):
        row = self.sub_list.currentRow()
        if 0 <= row < len(self.project.subtitles):
            self._push_undo("刪除字幕")
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
            self._update_safety_status()

    def _set_out_point(self):
        if self.video_source and self.project.work_range:
            self.project.work_range.out_time = self.video_source.current_time
            self._update_timeline_state()
            self._update_safety_status()

    def _reset_work_range(self):
        if self.video_source and self.project.work_range:
            self.project.work_range.in_time = 0.0
            self.project.work_range.out_time = self.video_source.duration
            self._update_timeline_state()
            self._update_safety_status()

    def _selected_subtitle(self):
        row = self.sub_list.currentRow()
        return self.project.subtitles[row] if 0 <= row < len(self.project.subtitles) else None

    def _selected_track(self):
        row = self.track_list.currentRow()
        return self.project.tracks[row] if 0 <= row < len(self.project.tracks) else None

    def _set_track_boundary(self, is_start):
        track = self._selected_track()
        if track and self.video_source and track.keyframes:
            self._push_undo("調整遮蔽起訖")
            target = track.keyframes[0] if is_start else track.keyframes[-1]
            target.time = self.video_source.current_time
            track.keyframes.sort(key=lambda item: item.time)
            track.reviewed = False  # 起訖已變動，需重新人工確認
            self._refresh_track_list()
            label = "起點" if is_start else "終點"
            self._flash_status(f"🎭 已將 [{track.label}] 的{label}設為 {self.video_source.current_time:.2f}s（請重新確認）", bg="#fff4dc", fg="#8a5b20")

    def _update_detail_cards(self):
        """依右側清單選取狀態漸進顯示單條微調卡片，未選取時收起避免雜訊。"""
        track = self._selected_track()
        has_track = track is not None
        self.lbl_track_empty_hint.setVisible(not has_track)
        self.track_detail_card.setVisible(has_track)
        if track and track.keyframes:
            self.lbl_track_range.setText(f"起訖：{track.keyframes[0].time:.1f}s ～ {track.keyframes[-1].time:.1f}s")
        elif track:
            self.lbl_track_range.setText("起訖：無關鍵影格")

        sub = self._selected_subtitle()
        has_sub = sub is not None
        self.lbl_sub_empty_hint.setVisible(not has_sub)
        self.sub_detail_card.setVisible(has_sub)

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

    @staticmethod
    def _reveal_in_explorer(output_path: str):
        norm = os.path.normpath(output_path)
        try:
            if sys.platform.startswith("win"):
                if os.path.exists(norm):
                    subprocess.Popen(["explorer", f"/select,{norm}"])
                else:
                    os.startfile(os.path.dirname(norm))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", norm])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(norm)])
        except Exception:
            pass

    def _show_export_success(self, title: str, message: str, output_path: str):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(title)
        box.setText(message)
        btn_open_dir = box.addButton("📂 開啟輸出資料夾", QMessageBox.ButtonRole.ActionRole)
        btn_ok = box.addButton("確定", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(btn_ok)
        box.exec()
        if box.clickedButton() is btn_open_dir and output_path:
            self._reveal_in_explorer(output_path)

    def export_fast_copy(self):
        self._stop_playback()
        if not self.video_source or not self.project.source:
            return
        answer = QMessageBox.warning(self, "快速串流剪輯", "此模式不套用任何馬賽克或字幕，且起點可能回退到前一個關鍵影格。\n\n若需要精確去識別輸出，請使用「匯出影片」。\n\n是否繼續？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
            
        default_name = self._generate_default_export_name(mode="fast")
        out_path, _ = QFileDialog.getSaveFileName(self, "儲存剪輯影片 (無馬賽克)", default_name, "MP4 Files (*.mp4)")
        if not out_path:
            return
            
        in_t = self.project.work_range.in_time if self.project.work_range else 0.0
        out_t = self.project.work_range.out_time if self.project.work_range else self.video_source.duration
        try:
            validate_destination(self.project.source.path, out_path)
        except ValueError as e:
            QMessageBox.critical(self, "匯出失敗", str(e))
            return

        self.fast_copy_worker = FastCopyWorker(self.project.source.path, in_t, out_t, out_path)
        self._show_busy_overlay(
            "⚡ 快速串流剪輯中",
            "正在直接無損封裝選定區間，請稍候...",
            cancellable=False,
            indeterminate=True,
        )

        def on_fast_copy_finished(success, msg):
            self._hide_busy_overlay()
            if success:
                self._show_export_success("匯出成功", f"剪輯完成！已儲存至：\n{msg}", msg)
            else:
                QMessageBox.critical(self, "匯出失敗", "快速剪輯執行失敗，來源與既有檔案未被更動。")

        self.fast_copy_worker.finished.connect(on_fast_copy_finished)
        self.fast_copy_worker.start()

    def export_exact_cut_video(self):
        self._stop_playback()
        if not self.video_source or not self.project.source:
            return
        answer = QMessageBox.warning(self, "精確剪輯", "此模式會重新編碼並精準裁切，但不套用任何馬賽克或字幕，輸出不具去識別化效果。\n\n是否繼續？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return

        time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        base, _ = os.path.splitext(self._generate_default_export_name(mode="fast"))
        out_path, _ = QFileDialog.getSaveFileName(self, "儲存精確剪輯影片 (無馬賽克)", f"{base}_exact_{time_str}.mp4", "MP4 Files (*.mp4)")
        if not out_path:
            return
        try:
            validate_destination(self.project.source.path, out_path)
        except ValueError as e:
            QMessageBox.critical(self, "匯出失敗", str(e))
            return

        self.exact_cut_worker = ExactCutWorker(self.project, out_path)
        self.exact_cut_worker.progress.connect(self._update_busy_progress)
        self._show_busy_overlay(
            "🎯 精確剪輯轉碼中",
            "正在精準裁切並重新編碼影片，請稍候...",
            on_cancel=self.exact_cut_worker.cancel,
        )

        def on_finished(success, msg):
            self._hide_busy_overlay()
            if success:
                self._show_export_success("匯出成功", f"精確剪輯完成！已儲存至：\n{msg}", msg)
            elif self.exact_cut_worker and self.exact_cut_worker._is_cancelled:
                QMessageBox.information(self, "已取消匯出", "已停止剪輯，並清除未完成輸出檔。")
            else:
                QMessageBox.critical(self, "匯出失敗", f"精確剪輯失敗:\n{msg}")

        self.exact_cut_worker.finished.connect(on_finished)
        self.exact_cut_worker.start()

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

        self.export_worker = ExportWorker(self.project, out_path)
        self.export_worker.progress.connect(self._update_busy_progress)
        self._show_busy_overlay(
            "🛡 去識別化影片壓制中",
            "正在將馬賽克與字幕壓制輸出，請稍候...",
            on_cancel=self.export_worker.cancel,
        )

        def on_export_finished(success, msg):
            self._hide_busy_overlay()
            if success:
                self._show_export_success("匯出成功", f"去識別化影片壓制完成！已儲存至：\n{msg}", msg)
            elif self.export_worker and self.export_worker._is_cancelled:
                QMessageBox.information(self, "已取消匯出", "已安全停止壓制，並清除未完成輸出檔。")
            else:
                QMessageBox.critical(self, "匯出失敗", f"影片壓制失敗:\n{msg}")

        self.export_worker.finished.connect(on_export_finished)
        self.export_worker.start()

    def closeEvent(self, event):
        app = QApplication.instance()
        if app is not None:
            try:
                app.focusChanged.disconnect(self._on_focus_changed)
            except (RuntimeError, TypeError):
                pass
        self._stop_playback()
        if hasattr(self, "media_player") and self.media_player:
            self.media_player.stop()
        if hasattr(self, "compress_page"):
            self.compress_page.stop_worker()
        for worker_name in ("ai_worker", "track_worker", "export_worker", "exact_cut_worker", "fast_copy_worker", "vad_worker"):
            w = getattr(self, worker_name, None)
            if w and w.isRunning():
                if hasattr(w, "cancel"):
                    w.cancel()
                w.wait()
        if self.video_source and hasattr(self.video_source, "close"):
            self.video_source.close()
        if self.thumb_extractor:
            self.thumb_extractor.close()
        super().closeEvent(event)
