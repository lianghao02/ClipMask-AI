"""
ClipMask-AI Video Graphics View (支援遮蔽框繪製/拖曳微調 + 聽打字幕即時渲染)
"""
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsPixmapItem
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QBrush, QWheelEvent, QDragEnterEvent, QDropEvent
from PySide6.QtCore import Qt, QRectF, Signal, QPointF
from typing import Optional, Tuple, List
import os
import cv2
import numpy as np
from ..models.project import Track
from ..track.evaluator import TrackEvaluator
from ..export.exporter import RenderExporter
from ..ai.subtitles import SubtitleManager, SubtitleItem


class VideoGraphicsView(QGraphicsView):
    rect_drawn = Signal(int, int, int, int)
    rect_adjusted = Signal(int, int, int, int)
    track_selected = Signal(object)
    wheel_stepped = Signal(int)
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)

        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(QColor(255, 249, 236)))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.pixmap_item = QGraphicsPixmapItem()
        self.scene.addItem(self.pixmap_item)

        self.video_w = 0
        self.video_h = 0
        self.show_real_mask_preview = False

        self.is_drawing = False
        self.draw_start_pt = QPointF()
        self.edit_mode: Optional[str] = None
        self.edit_orig_rect: Optional[QRectF] = None
        self.edit_anchor_pt = QPointF()
        self._edit_moved_enough = False
        self._edit_started_by_select = False
        self.preview_rect_item: Optional[QGraphicsRectItem] = None
        self.mask_items: List[QGraphicsRectItem] = []
        self._handle_items: List[QGraphicsRectItem] = []
        self.selected_track: Optional[Track] = None
        self._overlay_tracks = []

    def set_selected_track(self, track: Optional[Track]):
        """只更新輔助框樣式，不重新解碼、重做馬賽克或改變影片縮放。"""
        self.selected_track = track
        self._update_overlay_styles()

    def _clear_handles(self):
        for h in self._handle_items:
            self.scene.removeItem(h)
        self._handle_items.clear()

    def _update_overlay_styles(self):
        self._clear_handles()
        selected_rect = None
        for track, item in self._overlay_tracks:
            selected = track is self.selected_track
            pen = QPen(QColor("#00a9ff") if selected else QColor(201, 102, 75, 230),
                       4 if selected else 2,
                       Qt.PenStyle.SolidLine if selected else Qt.PenStyle.DashLine)
            pen.setCosmetic(True)  # 縮放影片時仍維持可辨識的螢幕線寬
            item.setPen(pen)
            item.setBrush(QBrush(Qt.BrushStyle.NoBrush) if selected else QBrush(QColor(201, 102, 75, 70)))
            item.setZValue(2 if selected else 1)
            item.setVisible(selected or not self.show_real_mask_preview)
            if selected and item.isVisible():
                selected_rect = item.rect()

        if selected_rect is not None:
            view_scale = max(0.05, float(self.transform().m11()))
            hs = 8.0 / view_scale
            half = hs / 2.0
            pen = QPen(QColor("#00a9ff"), 1.5)
            pen.setCosmetic(True)
            brush = QBrush(QColor("#ffffff"))
            for cx, cy in (
                (selected_rect.left(), selected_rect.top()),
                (selected_rect.right(), selected_rect.top()),
                (selected_rect.left(), selected_rect.bottom()),
                (selected_rect.right(), selected_rect.bottom()),
            ):
                handle = QGraphicsRectItem(cx - half, cy - half, hs, hs)
                handle.setPen(pen)
                handle.setBrush(brush)
                handle.setZValue(3)
                self.scene.addItem(handle)
                self._handle_items.append(handle)

    def _selected_overlay_rect(self) -> Optional[QRectF]:
        if self.selected_track is None:
            return None
        for track, item in self._overlay_tracks:
            if track is self.selected_track and item.isVisible():
                return item.rect()
        return None

    def _hit_test_selected(self, scene_pos: QPointF) -> Optional[str]:
        target_rect = self._selected_overlay_rect()
        if target_rect is None:
            return None

        view_scale = max(0.05, float(self.transform().m11()))
        tol = 10.0 / view_scale
        corners = (
            ("tl", target_rect.left(), target_rect.top()),
            ("tr", target_rect.right(), target_rect.top()),
            ("bl", target_rect.left(), target_rect.bottom()),
            ("br", target_rect.right(), target_rect.bottom()),
        )
        for name, cx, cy in corners:
            if abs(scene_pos.x() - cx) <= tol and abs(scene_pos.y() - cy) <= tol:
                return name
        if target_rect.contains(scene_pos):
            return "move"
        return None

    def _hit_test_any_track(self, scene_pos: QPointF) -> Optional[Track]:
        """點選畫面上其他未選取的遮蔽框（優先選面積較小者，避免大框擋住小框）。"""
        matches = []
        for track, item in self._overlay_tracks:
            if track is self.selected_track or not item.isVisible():
                continue
            r = item.rect()
            if r.contains(scene_pos):
                matches.append((r.width() * r.height(), track))
        if not matches:
            return None
        matches.sort(key=lambda pair: pair[0])
        return matches[0][1]

    def set_video_dimensions(self, width: int, height: int):
        self.video_w = width
        self.video_h = height
        self.scene.setSceneRect(0, 0, width, height)
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def update_frame_data(self, frame_rgb: np.ndarray, tracks: List[Track], subtitles: List[SubtitleItem], current_time: float, live_typing_text: str = "", is_speech_active: bool = False):
        orig_h, orig_w = frame_rgb.shape[:2]
        if self.video_w != orig_w or self.video_h != orig_h:
            self.set_video_dimensions(orig_w, orig_h)

        display_rgb = frame_rgb.copy()
        evaluated = TrackEvaluator.evaluate_all_tracks_at(tracks, current_time, self.video_w, self.video_h)

        # 1. 應用遮蔽
        if self.show_real_mask_preview:
            for track, rect in evaluated:
                display_rgb = RenderExporter.apply_mosaic_or_blur(display_rgb, rect, track.mask.style, track.mask.strength)

        # 2. 應用聽打字幕 (優先顯示正在即時輸入的文字，若無則依時間點抓取已記錄的字幕)
        target_sub_text = live_typing_text.strip() if live_typing_text.strip() else None
        if not target_sub_text and subtitles:
            target_sub_text = SubtitleManager.get_active_subtitle_at(subtitles, current_time)

        if target_sub_text:
            display_rgb = SubtitleManager.draw_subtitle_on_image(display_rgb, target_sub_text)
        elif is_speech_active:
            # 當有偵測到人聲但尚未建立字幕時，右下角顯示柔和的語音提示
            display_rgb = SubtitleManager.draw_speech_indicator(display_rgb)

        bytes_per_line = 3 * orig_w
        qimg = QImage(display_rgb.data, orig_w, orig_h, bytes_per_line, QImage.Format.Format_RGB888)
        self.pixmap_item.setPixmap(QPixmap.fromImage(qimg))

        # 輔助框
        for item in self.mask_items:
            self.scene.removeItem(item)
        self.mask_items.clear()
        self._overlay_tracks.clear()

        for track, (x, y, mw, mh) in evaluated:
            rect_item = QGraphicsRectItem(x, y, mw, mh)
            self.scene.addItem(rect_item)
            self.mask_items.append(rect_item)
            self._overlay_tracks.append((track, rect_item))
        self._update_overlay_styles()

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
                    self.file_dropped.emit(fpath)
                    event.acceptProposedAction()
                    return
        super().dropEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta != 0:
            step = 5 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            direction = 1 if delta > 0 else -1
            self.wheel_stepped.emit(direction * step)
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.scene.sceneRect().isValid() and self.video_w > 0:
            self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            self._update_overlay_styles()

    def _ensure_preview_item(self, color: QColor, fill_alpha: int = 60):
        if not self.preview_rect_item:
            self.preview_rect_item = QGraphicsRectItem()
            pen = QPen(color, 2, Qt.PenStyle.SolidLine)
            pen.setCosmetic(True)
            brush = QBrush(QColor(color.red(), color.green(), color.blue(), fill_alpha))
            self.preview_rect_item.setPen(pen)
            self.preview_rect_item.setBrush(brush)
            self.preview_rect_item.setZValue(4)
            self.scene.addItem(self.preview_rect_item)

    def _compute_edited_rect(self, scene_pos: QPointF) -> QRectF:
        orig = self.edit_orig_rect
        cx = max(0.0, min(float(self.video_w), scene_pos.x()))
        cy = max(0.0, min(float(self.video_h), scene_pos.y()))
        if self.edit_mode == "move":
            dx = scene_pos.x() - self.edit_anchor_pt.x()
            dy = scene_pos.y() - self.edit_anchor_pt.y()
            nx = max(0.0, min(float(self.video_w) - orig.width(), orig.x() + dx))
            ny = max(0.0, min(float(self.video_h) - orig.height(), orig.y() + dy))
            return QRectF(nx, ny, orig.width(), orig.height())
        if self.edit_mode == "tl":
            return QRectF(QPointF(cx, cy), orig.bottomRight()).normalized()
        if self.edit_mode == "tr":
            return QRectF(QPointF(orig.left(), cy), QPointF(cx, orig.bottom())).normalized()
        if self.edit_mode == "bl":
            return QRectF(QPointF(cx, orig.top()), QPointF(orig.right(), cy)).normalized()
        # "br"
        return QRectF(orig.topLeft(), QPointF(cx, cy)).normalized()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.video_w > 0:
            scene_pos = self.mapToScene(event.position().toPoint())
            hit = self._hit_test_selected(scene_pos)
            if hit is not None:
                self.edit_mode = hit
                self.edit_orig_rect = self._selected_overlay_rect()
                self.edit_anchor_pt = scene_pos
                self._edit_started_by_select = False
                self._edit_moved_enough = False
                self._ensure_preview_item(QColor("#00a9ff"), fill_alpha=50)
                self.preview_rect_item.setRect(self.edit_orig_rect)
                return
            clicked_track = self._hit_test_any_track(scene_pos)
            if clicked_track is not None:
                self.set_selected_track(clicked_track)
                self.track_selected.emit(clicked_track)
                self.edit_mode = "move"
                self.edit_orig_rect = self._selected_overlay_rect()
                self.edit_anchor_pt = scene_pos
                self._edit_started_by_select = True
                self._edit_moved_enough = False
                self._ensure_preview_item(QColor("#00a9ff"), fill_alpha=50)
                if self.edit_orig_rect is not None:
                    self.preview_rect_item.setRect(self.edit_orig_rect)
                return
            if 0 <= scene_pos.x() <= self.video_w and 0 <= scene_pos.y() <= self.video_h:
                self.is_drawing = True
                self.draw_start_pt = scene_pos
                self._ensure_preview_item(QColor(92, 124, 153, 255), fill_alpha=60)
                self.preview_rect_item.setRect(QRectF(scene_pos, scene_pos))
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        scene_pos = self.mapToScene(event.position().toPoint())
        if self.edit_mode and self.edit_orig_rect is not None and self.preview_rect_item:
            if abs(scene_pos.x() - self.edit_anchor_pt.x()) >= 3.0 or abs(scene_pos.y() - self.edit_anchor_pt.y()) >= 3.0:
                self._edit_moved_enough = True
            self.preview_rect_item.setRect(self._compute_edited_rect(scene_pos))
            return
        if self.is_drawing and self.preview_rect_item:
            cx = max(0, min(self.video_w, scene_pos.x()))
            cy = max(0, min(self.video_h, scene_pos.y()))
            rect = QRectF(self.draw_start_pt, QPointF(cx, cy)).normalized()
            self.preview_rect_item.setRect(rect)
            return
        if self.video_w > 0:
            hit = self._hit_test_selected(scene_pos)
            if hit in ("tl", "br"):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif hit in ("tr", "bl"):
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            elif hit == "move":
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            elif self._hit_test_any_track(scene_pos) is not None:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            elif 0 <= scene_pos.x() <= self.video_w and 0 <= scene_pos.y() <= self.video_h:
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.edit_mode and event.button() == Qt.MouseButton.LeftButton:
            self.edit_mode = None
            self.edit_orig_rect = None
            started_by_select = self._edit_started_by_select
            moved_enough = self._edit_moved_enough
            self._edit_started_by_select = False
            self._edit_moved_enough = False
            if self.preview_rect_item:
                rect = self.preview_rect_item.rect()
                self.scene.removeItem(self.preview_rect_item)
                self.preview_rect_item = None
                if (not started_by_select or moved_enough) and rect.width() >= 5 and rect.height() >= 5:
                    rx = int(round(rect.x()))
                    ry = int(round(rect.y()))
                    rw = int(round(rect.width()))
                    rh = int(round(rect.height()))
                    self.rect_adjusted.emit(rx, ry, rw, rh)
            return
        if self.is_drawing and event.button() == Qt.MouseButton.LeftButton:
            self.is_drawing = False
            if self.preview_rect_item:
                rect = self.preview_rect_item.rect()
                self.scene.removeItem(self.preview_rect_item)
                self.preview_rect_item = None
                if rect.width() >= 5 and rect.height() >= 5:
                    rx = int(round(rect.x()))
                    ry = int(round(rect.y()))
                    rw = int(round(rect.width()))
                    rh = int(round(rect.height()))
                    self.rect_drawn.emit(rx, ry, rw, rh)
            return
        super().mouseReleaseEvent(event)
