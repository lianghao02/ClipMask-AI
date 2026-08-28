"""
ClipMask-AI Video Graphics View (支援遮蔽框 + 聽打字幕即時渲染)
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
    wheel_stepped = Signal(int)
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setBackgroundBrush(QBrush(QColor(242, 239, 233)))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.pixmap_item = QGraphicsPixmapItem()
        self.scene.addItem(self.pixmap_item)
        
        self.video_w = 0
        self.video_h = 0
        self.show_real_mask_preview = False
        
        self.is_drawing = False
        self.draw_start_pt = QPointF()
        self.preview_rect_item: Optional[QGraphicsRectItem] = None
        self.mask_items: List[QGraphicsRectItem] = []

    def set_video_dimensions(self, width: int, height: int):
        self.video_w = width
        self.video_h = height
        self.scene.setSceneRect(0, 0, width, height)
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def update_frame_data(self, frame_rgb: np.ndarray, tracks: List[Track], subtitles: List[SubtitleItem], current_time: float):
        orig_h, orig_w = frame_rgb.shape[:2]
        if self.video_w != orig_w or self.video_h != orig_h:
            self.set_video_dimensions(orig_w, orig_h)

        display_rgb = frame_rgb.copy()
        evaluated = TrackEvaluator.evaluate_all_tracks_at(tracks, current_time, self.video_w, self.video_h)
        
        # 1. 應用遮蔽
        if self.show_real_mask_preview:
            for track, rect in evaluated:
                display_rgb = RenderExporter.apply_mosaic_or_blur(display_rgb, rect, track.mask.style, track.mask.strength)

        # 2. 應用聽打字幕
        if subtitles:
            sub_text = SubtitleManager.get_active_subtitle_at(subtitles, current_time)
            if sub_text:
                display_rgb = SubtitleManager.draw_subtitle_on_image(display_rgb, sub_text)

        bytes_per_line = 3 * orig_w
        qimg = QImage(display_rgb.data, orig_w, orig_h, bytes_per_line, QImage.Format.Format_RGB888)
        self.pixmap_item.setPixmap(QPixmap.fromImage(qimg))
        
        # 輔助框
        for item in self.mask_items:
            self.scene.removeItem(item)
        self.mask_items.clear()
        
        if not self.show_real_mask_preview:
            for track, (x, y, mw, mh) in evaluated:
                rect_item = QGraphicsRectItem(x, y, mw, mh)
                pen = QPen(QColor(201, 102, 75, 230), 2, Qt.PenStyle.DashLine)
                brush = QBrush(QColor(201, 102, 75, 70))
                rect_item.setPen(pen)
                rect_item.setBrush(brush)
                self.scene.addItem(rect_item)
                self.mask_items.append(rect_item)

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

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.video_w > 0:
            scene_pos = self.mapToScene(event.pos())
            if 0 <= scene_pos.x() <= self.video_w and 0 <= scene_pos.y() <= self.video_h:
                self.is_drawing = True
                self.draw_start_pt = scene_pos
                if not self.preview_rect_item:
                    self.preview_rect_item = QGraphicsRectItem()
                    pen = QPen(QColor(92, 124, 153, 255), 2, Qt.PenStyle.SolidLine)
                    brush = QBrush(QColor(92, 124, 153, 60))
                    self.preview_rect_item.setPen(pen)
                    self.preview_rect_item.setBrush(brush)
                    self.scene.addItem(self.preview_rect_item)
                self.preview_rect_item.setRect(QRectF(scene_pos, scene_pos))
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.is_drawing and self.preview_rect_item:
            scene_pos = self.mapToScene(event.pos())
            cx = max(0, min(self.video_w, scene_pos.x()))
            cy = max(0, min(self.video_h, scene_pos.y()))
            rect = QRectF(self.draw_start_pt, QPointF(cx, cy)).normalized()
            self.preview_rect_item.setRect(rect)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
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
