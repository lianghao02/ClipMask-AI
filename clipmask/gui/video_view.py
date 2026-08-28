"""
ClipMask-AI Video Graphics View (穩定深拷貝版)
使用 QImage 深拷貝直接更新 Pixmap，保證執行緒安全與不閃退。
"""
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsPixmapItem
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QBrush
from PySide6.QtCore import Qt, QRectF, Signal, QPointF
from typing import Optional, Tuple, List
from ..models.project import Track
from ..track.evaluator import TrackEvaluator

class VideoGraphicsView(QGraphicsView):
    rect_drawn = Signal(int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setBackgroundBrush(QBrush(QColor(20, 20, 24)))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.pixmap_item = QGraphicsPixmapItem()
        self.scene.addItem(self.pixmap_item)
        
        self.video_w = 0
        self.video_h = 0
        
        self.is_drawing = False
        self.draw_start_pt = QPointF()
        self.preview_rect_item: Optional[QGraphicsRectItem] = None
        self.mask_items: List[QGraphicsRectItem] = []

    def set_video_dimensions(self, width: int, height: int):
        self.video_w = width
        self.video_h = height
        self.scene.setSceneRect(0, 0, width, height)
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def update_qimage(self, qimg: QImage, tracks: List[Track], current_time: float, orig_w: int, orig_h: int):
        if self.video_w != orig_w or self.video_h != orig_h:
            self.set_video_dimensions(orig_w, orig_h)

        self.pixmap_item.setPixmap(QPixmap.fromImage(qimg))
        
        for item in self.mask_items:
            self.scene.removeItem(item)
        self.mask_items.clear()
        
        evaluated = TrackEvaluator.evaluate_all_tracks_at(tracks, current_time, self.video_w, self.video_h)
        for track, (x, y, mw, mh) in evaluated:
            rect_item = QGraphicsRectItem(x, y, mw, mh)
            pen = QPen(QColor(240, 70, 70, 240), 2, Qt.PenStyle.DashLine)
            brush = QBrush(QColor(240, 70, 70, 80))
            rect_item.setPen(pen)
            rect_item.setBrush(brush)
            self.scene.addItem(rect_item)
            self.mask_items.append(rect_item)

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
                    pen = QPen(QColor(80, 180, 255, 255), 2, Qt.PenStyle.SolidLine)
                    brush = QBrush(QColor(80, 180, 255, 60))
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
