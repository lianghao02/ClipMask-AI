"""
ClipMask-AI 獨立影片容量壓縮分頁
版面與前三個分頁統一：左側為拖曳與影片資訊預覽區，右側為壓縮目標與匯出側欄。
預設目標：壓縮進一張標準單層 4.7 GB DVD 安全容量（4,300 MB）。
"""
import os
from datetime import datetime
from typing import Optional

import av
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..export.compressor import (
    CLOUD_SHARE_TARGET_MB,
    DEFAULT_DVD_TARGET_MB,
    DVD_DL_TARGET_MB,
    VideoCompressor,
    VideoProbeInfo,
    calculate_compression_plan,
    format_file_size,
    probe_video_info,
)
from ..export.exporter import validate_destination

COMPRESS_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".ts", ".wmv", ".flv", ".webm", ".m4v"}


class CompressWorker(QThread):
    progress = Signal(int)
    finished = Signal(bool, str)

    def __init__(self, source_path: str, output_path: str, target_mb: int, auto_downscale: bool):
        super().__init__()
        self.source_path = source_path
        self.output_path = output_path
        self.target_mb = target_mb
        self.auto_downscale = auto_downscale
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            ok = VideoCompressor.compress(
                source_path=self.source_path,
                output_path=self.output_path,
                target_mb=self.target_mb,
                auto_downscale=self.auto_downscale,
                progress_callback=lambda val: self.progress.emit(val),
                should_cancel=lambda: self._is_cancelled,
            )
            self.finished.emit(ok, self.output_path)
        except Exception as e:
            self.finished.emit(False, str(e))


class CompressPage(QWidget):
    """左側拖曳／影片資訊預覽區 ＋ 右側壓縮設定控制側欄。"""

    export_succeeded = Signal(str, str, str)  # title, detail, output_path

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.source_info: Optional[VideoProbeInfo] = None
        self.worker: Optional[CompressWorker] = None
        self._preview_pixmap: Optional[QPixmap] = None
        self._init_ui()
        self._refresh_plan()

    def _init_ui(self):
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 左側主工作區：指引條 + 拖曳/影片預覽舞台 ──
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.lbl_compress_guide = QLabel(
            "📦 <b>影片容量壓縮（獨立分頁）</b>　｜　將影片拖曳進下方區域，在右側選擇目標容量（預設裝進標準 DVD）。"
        )
        self.lbl_compress_guide.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_compress_guide.setWordWrap(True)
        self.lbl_compress_guide.setStyleSheet(
            "padding: 7px 14px; border-radius: 11px; background: #fffaf0; border: 2.5px solid #8a5a32; color: #3d2410; font-size: 14px;"
        )
        left_layout.addWidget(self.lbl_compress_guide)

        stage_frame = QFrame()
        stage_frame.setObjectName("videoStageFrame")
        sf_layout = QVBoxLayout(stage_frame)
        sf_layout.setContentsMargins(6, 6, 6, 6)
        sf_layout.setSpacing(0)

        self.stage_stack = QStackedWidget()

        # 狀態 A：未載入影片（與前幾頁同款的虛線拖曳卡）
        empty_page = QWidget()
        empty_outer = QVBoxLayout(empty_page)
        empty_outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        empty_card = QFrame()
        empty_card.setObjectName("emptyDropCard")
        empty_card.setMaximumWidth(460)
        empty_card.setMinimumWidth(340)
        ec_layout = QVBoxLayout(empty_card)
        ec_layout.setContentsMargins(28, 28, 28, 24)
        ec_layout.setSpacing(10)
        ec_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_lbl = QLabel("📦")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet("font-size: 40px;")
        ec_layout.addWidget(icon_lbl)

        title_lbl = QLabel("拖曳要瘦身壓縮的影片到這裡")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 700; color: #3b2412;")
        ec_layout.addWidget(title_lbl)

        sub_lbl = QLabel("不影響去識別或剪輯工作區；全程本機壓縮並另存新檔，原檔完全保留")
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet("font-size: 13px; color: #6b533b;")
        ec_layout.addWidget(sub_lbl)

        ec_layout.addSpacing(6)
        self.btn_choose_file = QPushButton("📂 選擇影片檔案")
        self.btn_choose_file.setObjectName("btn_primary")
        self.btn_choose_file.setMinimumHeight(38)
        self.btn_choose_file.clicked.connect(self.choose_video_file)
        ec_layout.addWidget(self.btn_choose_file, alignment=Qt.AlignmentFlag.AlignCenter)

        empty_outer.addWidget(empty_card)
        self.stage_stack.addWidget(empty_page)

        # 狀態 B：已載入影片（縮圖預覽＋壓縮前大小清楚展示）
        loaded_page = QWidget()
        lp_layout = QVBoxLayout(loaded_page)
        lp_layout.setContentsMargins(10, 10, 10, 10)
        lp_layout.setSpacing(10)

        self.lbl_thumb = QLabel("影片畫面預覽")
        self.lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumb.setMinimumHeight(240)
        self.lbl_thumb.setStyleSheet(
            "background-color: #2b1d12; color: #efe3ca; border: 2.5px solid #6b4528; border-radius: 10px; font-size: 14px;"
        )
        lp_layout.addWidget(self.lbl_thumb, stretch=1)

        info_box = QFrame()
        info_box.setObjectName("detailCard")
        ib_layout = QHBoxLayout(info_box)
        ib_layout.setContentsMargins(12, 10, 12, 10)
        ib_layout.setSpacing(12)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(4)
        self.lbl_file_name = QLabel("尚未選擇影片")
        self.lbl_file_name.setWordWrap(True)
        self.lbl_file_name.setStyleSheet("font-size: 15px; font-weight: 700; color: #3d2410;")
        meta_col.addWidget(self.lbl_file_name)

        self.lbl_before_size = QLabel("壓縮前大小：--")
        self.lbl_before_size.setWordWrap(True)
        self.lbl_before_size.setStyleSheet("font-size: 17px; font-weight: 700; color: #c44c1d;")
        meta_col.addWidget(self.lbl_before_size)

        self.lbl_video_meta = QLabel("片長：--　｜　解析度：--　｜　原始平均碼率：--")
        self.lbl_video_meta.setWordWrap(True)
        self.lbl_video_meta.setStyleSheet("font-size: 13.5px; color: #5e442c;")
        meta_col.addWidget(self.lbl_video_meta)
        ib_layout.addLayout(meta_col, 1)

        self.btn_rechoose_file = QPushButton("📂 更換影片")
        self.btn_rechoose_file.setToolTip("挑選另一支要壓縮的影片（也可直接把檔案拖進左側）")
        self.btn_rechoose_file.clicked.connect(self.choose_video_file)
        ib_layout.addWidget(self.btn_rechoose_file, alignment=Qt.AlignmentFlag.AlignVCenter)

        lp_layout.addWidget(info_box)
        self.stage_stack.addWidget(loaded_page)

        sf_layout.addWidget(self.stage_stack)
        left_layout.addWidget(stage_frame, stretch=1)
        splitter.addWidget(left_widget)

        # ── 右側控制側欄（與前幾頁維持相同寬度與日系 Q 版圓角面板樣式） ──
        self.right_sidebar = QWidget()
        self.right_sidebar.setMinimumWidth(360)
        sb_outer = QVBoxLayout(self.right_sidebar)
        sb_outer.setContentsMargins(6, 0, 2, 0)
        sb_outer.setSpacing(6)

        sidebar_card = QFrame()
        sidebar_card.setObjectName("panelCard")
        sb_layout = QVBoxLayout(sidebar_card)
        sb_layout.setContentsMargins(12, 10, 12, 12)
        sb_layout.setSpacing(10)

        header_row = QHBoxLayout()
        lbl_sb_title = QLabel("📀 壓縮目標設定")
        lbl_sb_title.setObjectName("panelTitle")
        header_row.addWidget(lbl_sb_title)
        header_row.addStretch()
        badge = QLabel("預設標準 DVD")
        badge.setStyleSheet(
            "color: #3d2410; background: #f4e2bd; border: 2px solid #8a5a32; padding: 2px 10px; border-radius: 10px; font-size: 12.5px; font-weight: 700;"
        )
        header_row.addWidget(badge)
        sb_layout.addLayout(header_row)

        preset_slot = QFrame()
        preset_slot.setObjectName("detailCard")
        ps_layout = QVBoxLayout(preset_slot)
        ps_layout.setContentsMargins(8, 8, 8, 8)
        ps_layout.setSpacing(6)

        ps_hint = QLabel("快速選擇目標大小：")
        ps_hint.setStyleSheet("font-size: 13.5px; font-weight: 700; color: #563a1f;")
        ps_layout.addWidget(ps_hint)

        preset_grid = QGridLayout()
        preset_grid.setSpacing(6)
        self.preset_group = QButtonGroup(self)
        self.preset_group.setExclusive(True)

        self.btn_preset_dvd = QPushButton("📀 標準 DVD\n4,300 MB")
        self.btn_preset_dvd.setToolTip("適配一張標準 4.7 GB 單層 DVD 光碟（已預留燒錄空間）")
        self.btn_preset_dvd_dl = QPushButton("💿 雙層 DVD\n7,900 MB")
        self.btn_preset_dvd_dl.setToolTip("適配一張 8.5 GB 雙層 DVD-DL 光碟")
        self.btn_preset_cloud = QPushButton("💬 傳檔瘦身\n500 MB")
        self.btn_preset_cloud.setToolTip("適合 LINE、Email 附件或公務系統上傳限制")
        self.btn_preset_custom = QPushButton("✏️ 自訂上限\n手動輸入")
        self.btn_preset_custom.setToolTip("在下方輸入想要的目標檔案上限（MB）")

        for idx, btn in enumerate(
            (self.btn_preset_dvd, self.btn_preset_dvd_dl, self.btn_preset_cloud, self.btn_preset_custom)
        ):
            btn.setCheckable(True)
            btn.setProperty("class", "preset-btn")
            self.preset_group.addButton(btn)
            preset_grid.addWidget(btn, idx // 2, idx % 2)

        self.btn_preset_dvd.setChecked(True)
        self.btn_preset_dvd.clicked.connect(lambda: self._apply_preset(DEFAULT_DVD_TARGET_MB))
        self.btn_preset_dvd_dl.clicked.connect(lambda: self._apply_preset(DVD_DL_TARGET_MB))
        self.btn_preset_cloud.clicked.connect(lambda: self._apply_preset(CLOUD_SHARE_TARGET_MB))
        self.btn_preset_custom.clicked.connect(self._focus_custom_mb)
        ps_layout.addLayout(preset_grid)

        custom_row = QHBoxLayout()
        custom_row.setSpacing(6)
        custom_row.addWidget(QLabel("目標上限："))
        self.spin_target_mb = QSpinBox()
        self.spin_target_mb.setMinimumHeight(34)
        self.spin_target_mb.setRange(10, 50000)
        self.spin_target_mb.setValue(DEFAULT_DVD_TARGET_MB)
        self.spin_target_mb.setSuffix(" MB")
        self.spin_target_mb.valueChanged.connect(self._on_target_mb_changed)
        custom_row.addWidget(self.spin_target_mb, 1)
        ps_layout.addLayout(custom_row)

        self.chk_auto_downscale = QCheckBox("容量較緊時自動降解析度以保清晰")
        self.chk_auto_downscale.setToolTip("例如長片塞進小容量時，自動從 1080p 降至 720p 或 480p 以避免畫面糊掉")
        self.chk_auto_downscale.setChecked(True)
        self.chk_auto_downscale.toggled.connect(self._refresh_plan)
        ps_layout.addWidget(self.chk_auto_downscale)

        sb_layout.addWidget(preset_slot)

        self.lbl_plan_summary = QLabel("請在左側選擇要壓縮的影片，系統會即時試算壓縮前後容量。")
        self.lbl_plan_summary.setWordWrap(True)
        self.lbl_plan_summary.setStyleSheet(
            "background: #f9efd9; border: 2px solid #966b41; border-radius: 11px; padding: 10px 12px; color: #3d2410; font-size: 13.5px;"
        )
        sb_layout.addWidget(self.lbl_plan_summary)

        sb_layout.addStretch()

        self.progress_widget = QWidget()
        prog_layout = QHBoxLayout(self.progress_widget)
        prog_layout.setContentsMargins(0, 0, 0, 0)
        prog_layout.setSpacing(6)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        prog_layout.addWidget(self.progress_bar, 1)

        self.btn_cancel_compress = QPushButton("取消")
        self.btn_cancel_compress.setProperty("class", "compact-btn")
        self.btn_cancel_compress.clicked.connect(self.cancel_compress)
        prog_layout.addWidget(self.btn_cancel_compress)
        self.progress_widget.hide()
        sb_layout.addWidget(self.progress_widget)

        self.btn_start_compress = QPushButton("📦 開始壓縮並另存新檔")
        self.btn_start_compress.setObjectName("btn_primary")
        self.btn_start_compress.setMinimumHeight(38)
        self.btn_start_compress.setEnabled(False)
        self.btn_start_compress.clicked.connect(self.start_compress)
        sb_layout.addWidget(self.btn_start_compress)

        sb_outer.addWidget(sidebar_card)
        splitter.addWidget(self.right_sidebar)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        root_layout.addWidget(splitter)

    def set_sidebar_visible(self, visible: bool):
        self.right_sidebar.setVisible(visible)

    # ──── 拖曳開檔 ────
    def _is_compressing(self) -> bool:
        return bool(self.worker and self.worker.isRunning())

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self._is_compressing():
            event.ignore()
            return
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if os.path.splitext(url.toLocalFile())[1].lower() in COMPRESS_VIDEO_EXTENSIONS:
                    event.acceptProposedAction()
                    return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._is_compressing():
            event.ignore()
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        if self._is_compressing():
            event.ignore()
            return
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                fpath = url.toLocalFile()
                if os.path.splitext(fpath)[1].lower() in COMPRESS_VIDEO_EXTENSIONS:
                    self.load_file(fpath)
                    event.acceptProposedAction()
                    return
        super().dropEvent(event)

    # ──── 操作邏輯 ────
    def choose_video_file(self):
        if self._is_compressing():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "選擇要壓縮的影片", "", "Video Files (*.mp4 *.mkv *.mov *.avi *.ts *.wmv *.flv *.webm *.m4v)"
        )
        if path:
            self.load_file(path)

    def _update_preview_thumbnail(self, video_path: str):
        self._preview_pixmap = None
        try:
            with av.open(video_path) as container:
                if container.streams.video:
                    for frame in container.decode(video=0):
                        rgb = frame.to_ndarray(format="rgb24")
                        h, w = rgb.shape[:2]
                        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
                        self._preview_pixmap = QPixmap.fromImage(qimg)
                        break
        except Exception:
            self._preview_pixmap = None
        self._apply_thumbnail_pixmap()

    def _apply_thumbnail_pixmap(self):
        if self._preview_pixmap is not None and not self._preview_pixmap.isNull():
            target_size = self.lbl_thumb.size()
            if target_size.width() > 40 and target_size.height() > 40:
                scaled = self._preview_pixmap.scaled(
                    target_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                self.lbl_thumb.setPixmap(scaled)
                return
        if self._preview_pixmap is None:
            self.lbl_thumb.setText("已載入影片（無法擷取預覽縮圖，仍可正常壓縮）")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._preview_pixmap is not None:
            self._apply_thumbnail_pixmap()

    def load_file(self, path: str):
        if self._is_compressing():
            QMessageBox.information(self, "正在壓縮中", "影片正在壓縮，請先等待完成或按取消後再更換檔案。")
            return
        try:
            info = probe_video_info(path)
        except Exception as e:
            QMessageBox.critical(self, "無法讀取影片", f"讀取影片資訊失敗：\n{e}")
            return
        self.source_info = info
        mins = int(info.duration_sec // 60)
        secs = int(round(info.duration_sec % 60))
        src_kbps = int(info.source_bitrate_bps / 1000)
        self.lbl_file_name.setText(f"🎞️ {os.path.basename(info.path)}")
        self.lbl_file_name.setToolTip(info.path)
        self.lbl_before_size.setText(f"壓縮前大小：{format_file_size(info.file_size_bytes)}")
        self.lbl_video_meta.setText(
            f"片長：{mins} 分 {secs:02d} 秒　｜　解析度：{info.width}×{info.height}　｜　約 {src_kbps:,} kbps"
        )
        self.stage_stack.setCurrentIndex(1)
        self._update_preview_thumbnail(path)
        self._refresh_plan()

    def _apply_preset(self, target_mb: int):
        self.spin_target_mb.blockSignals(True)
        self.spin_target_mb.setValue(target_mb)
        self.spin_target_mb.blockSignals(False)
        self._refresh_plan()

    def _focus_custom_mb(self):
        self.spin_target_mb.setFocus()
        self.spin_target_mb.selectAll()

    def _on_target_mb_changed(self, value: int):
        self.btn_preset_dvd.setChecked(value == DEFAULT_DVD_TARGET_MB)
        self.btn_preset_dvd_dl.setChecked(value == DVD_DL_TARGET_MB)
        self.btn_preset_cloud.setChecked(value == CLOUD_SHARE_TARGET_MB)
        if value not in (DEFAULT_DVD_TARGET_MB, DVD_DL_TARGET_MB, CLOUD_SHARE_TARGET_MB):
            self.btn_preset_custom.setChecked(True)
        self._refresh_plan()

    def _refresh_plan(self):
        is_running = bool(self.worker and self.worker.isRunning())
        if not self.source_info:
            self.btn_start_compress.setEnabled(False)
            self.lbl_plan_summary.setText(
                f"目前預設目標為標準單層 4.7 GB DVD（壓縮上限 {self.spin_target_mb.value():,} MB）。\n請先於左側選取或拖曳影片。"
            )
            return

        target_mb = self.spin_target_mb.value()
        plan = calculate_compression_plan(
            self.source_info,
            target_mb=target_mb,
            auto_downscale=self.chk_auto_downscale.isChecked(),
        )
        self.btn_start_compress.setEnabled(not is_running)

        before_txt = format_file_size(plan.source_size_bytes)
        est_txt = format_file_size(plan.estimated_output_bytes)
        scale_txt = (
            f"{self.source_info.width}×{self.source_info.height} → {plan.output_width}×{plan.output_height}"
            if plan.downscaled
            else f"維持 {plan.output_width}×{plan.output_height}"
        )
        if plan.already_smaller:
            self.lbl_plan_summary.setStyleSheet(
                "background: #fdf3dc; border: 2px solid #a67541; border-radius: 11px; padding: 10px 12px; color: #5e3c14; font-size: 13.5px;"
            )
            self.lbl_plan_summary.setText(
                f"ℹ {plan.quality_note}\n若只是要裝進 {target_mb:,} MB 光碟，可直接燒錄原檔；想再縮小請點「傳檔瘦身」或調整上限。"
            )
            self.btn_start_compress.setObjectName("")
            self.btn_start_compress.setText("📦 仍要重新編碼另存")
        else:
            saved_pct = max(0, int(round((1.0 - plan.estimated_output_bytes / max(1, plan.source_size_bytes)) * 100)))
            self.lbl_plan_summary.setStyleSheet(
                "background: #e2f4df; border: 2px solid #3f7543; border-radius: 11px; padding: 10px 12px; color: #1c3d1f; font-size: 13.5px;"
            )
            self.lbl_plan_summary.setText(
                f"壓縮前：{before_txt}\n"
                f"預估後：{est_txt}（省約 {saved_pct}%）\n"
                f"規格：{scale_txt} · {plan.video_bitrate_kbps:,} kbps\n"
                f"{plan.quality_note}"
            )
            self.btn_start_compress.setObjectName("btn_primary")
            self.btn_start_compress.setText("📦 開始壓縮並另存新檔")
        self.btn_start_compress.style().unpolish(self.btn_start_compress)
        self.btn_start_compress.style().polish(self.btn_start_compress)

    def _default_output_path(self) -> str:
        assert self.source_info is not None
        src_dir = os.path.dirname(self.source_info.path)
        stem = os.path.splitext(os.path.basename(self.source_info.path))[0]
        target_mb = self.spin_target_mb.value()
        tag = "dvd" if target_mb == DEFAULT_DVD_TARGET_MB else f"{target_mb}mb"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(src_dir, f"{stem}_compressed_{tag}_{ts}.mp4")

    def _set_running_ui(self, running: bool):
        for w in (
            self.btn_choose_file,
            self.btn_rechoose_file,
            self.btn_preset_dvd,
            self.btn_preset_dvd_dl,
            self.btn_preset_cloud,
            self.btn_preset_custom,
            self.spin_target_mb,
            self.chk_auto_downscale,
            self.btn_start_compress,
        ):
            w.setEnabled(not running)
        self.progress_widget.setVisible(running)
        if running:
            self.progress_bar.setValue(0)
            self.btn_cancel_compress.setEnabled(True)
            self.btn_cancel_compress.setText("取消")

    def start_compress(self):
        if not self.source_info or (self.worker and self.worker.isRunning()):
            return
        target_mb = self.spin_target_mb.value()
        out_path, _ = QFileDialog.getSaveFileName(
            self, "儲存壓縮後影片", self._default_output_path(), "MP4 Files (*.mp4)"
        )
        if not out_path:
            return
        try:
            validate_destination(self.source_info.path, out_path)
        except ValueError as e:
            QMessageBox.critical(self, "路徑錯誤", str(e))
            return

        before_bytes = self.source_info.file_size_bytes
        self.worker = CompressWorker(
            source_path=self.source_info.path,
            output_path=out_path,
            target_mb=target_mb,
            auto_downscale=self.chk_auto_downscale.isChecked(),
        )
        self.worker.progress.connect(self.progress_bar.setValue)

        def on_finished(success: bool, msg: str):
            self._set_running_ui(False)
            self._refresh_plan()
            if success and os.path.exists(msg):
                after_bytes = os.path.getsize(msg)
                ratio = max(0, int(round((1.0 - after_bytes / max(1, before_bytes)) * 100)))
                target_bytes = target_mb * 1024 * 1024
                overshoot_note = (
                    f"\n⚠️ 注意：因該片高動態畫面較多，實際大小略高於目標 {target_mb:,} MB，若需嚴格塞進光碟可再微調降低上限後重壓。"
                    if after_bytes > target_bytes
                    else ""
                )
                detail = (
                    f"影片壓縮完成！\n\n"
                    f"壓縮前：{format_file_size(before_bytes)}\n"
                    f"壓縮後：{format_file_size(after_bytes)}（縮減 {ratio}%）{overshoot_note}\n\n"
                    f"輸出檔案：\n{msg}"
                )
                self.export_succeeded.emit("壓縮完成", detail, msg)
            elif self.worker and self.worker._is_cancelled:
                QMessageBox.information(self, "已取消壓縮", "已安全停止壓縮，原始檔案未受任何影響。")
            else:
                QMessageBox.critical(self, "壓縮失敗", f"壓縮過程發生錯誤：\n{msg}")

        self.worker.finished.connect(on_finished)
        self._set_running_ui(True)
        self.worker.start()

    def cancel_compress(self):
        if self.worker and self.worker.isRunning():
            self.btn_cancel_compress.setEnabled(False)
            self.btn_cancel_compress.setText("停止中...")
            self.worker.cancel()

    def stop_worker(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()
