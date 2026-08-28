"""
ClipMask-AI Video Graphics View
使用 QGraphicsScene 與原生像素座標系繪製影片與遮蔽框。
"""
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsItem
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QBrush
from PySide6.QtCore import Qt, QRectF, Signal, QPointF
import numpy as np
from typing import Optional, Tuple, List
from ..models.project import Track
from ..track.evaluator import TrackEvaluator

class VideoGraphicsView(QGraphicsView):
    # 當使用者用滑鼠在畫面上拉出新框時觸發: (x, y, w, h) 原生像素座標
    rect_drawn = Signal(int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setBackgroundBrush(QBrush(QColor(25, 25, 30)))
        
        self.current_qimage: Optional[QImage] = None
        self.video_w = 0
        self.video_h = 0
        
        # 繪圖狀態
        self.is_drawing = False
        self.draw_start_pt = QPointF()
        self.preview_rect_item: Optional[QGraphicsRectItem] = None
        
        # 遮蔽預覽框 Items
        self.mask_items: List[QGraphicsRectItem] = []

    def update_frame(self, rgb_array: np.ndarray, tracks: List[Track], current_time: float):
        """更新當前影格與所有遮蔽框（Zero-copy View 管道）"""
        self.video_h, self.video_w, channels = rgb_array.shape
        bytes_per_line = channels * self.video_w
        
        # Zero-copy 將 numpy array 指標包裝為 QImage
        self.current_qimage = QImage(
            rgb_array.data,
            self.video_w,
            self.video_h,
            bytes_per_line,
            QImage.Format.Format_RGB888
        )
        
        # 更新 Scene 大小為原生像素尺寸
        self.scene.setSceneRect(0, 0, self.video_w, self.video_h)
        
        # 清除舊的遮蔽框，重新計算並繪製
        for item in self.mask_items:
            self.scene.removeItem(item)
        self.mask_items.clear()
        
        # 透過 TrackEvaluator 取得所有遮蔽框
        evaluated = TrackEvaluator.evaluate_all_tracks_at(tracks, current_time, self.video_w, self.video_h)
        for track, (x, y, w, h) in evaluated:
            rect_item = QGraphicsRectItem(x, y, w, h)
            # 遮蔽外觀：半透明莫蘭迪紅/灰底 + 虛線框
            pen = QPen(QColor(230, 80, 80, 240), 2, Qt.PenStyle.DashLine)
            brush = QBrush(QColor(230, 80, 80, 70))
            rect_item.setPen(pen)
            rect_item.setBrush(brush)
            self.scene.addItem(rect_item)
            self.mask_items.append(rect_item)
            
        self.scene.update()
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def drawBackground(self, painter: QPainter, rect: QRectF):
        super().drawBackground(painter, rect)
        if self.current_qimage is not None and not self.current_qimage.isNull():
            painter.drawImage(0, 0, self.current_qimage)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.scene.sceneRect().isValid():
            self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    # ──── 滑鼠手動畫框互動 ────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.video_w > 0:
            scene_pos = self.mapToScene(event.pos())
            # 限制在影片範圍內
            if 0 <= scene_pos.x() <= self.video_w and 0 <= scene_pos.y() <= self.video_h:
                self.is_drawing = True
                self.draw_start_pt = scene_pos
                if not self.preview_rect_item:
                    self.preview_rect_item = QGraphicsRectItem()
                    pen = QPen(QColor(80, 180, 255, 255), 2, Qt.PenStyle.SolidLine)
                    brush = QBrush(QColor(80, 180, 255, 50))
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
                
                # 只有當拉出的框大於 5x5 像素時才觸發建立
                if rect.width() >= 5 and rect.height() >= 5:
                    rx = int(round(rect.x()))
                    ry = int(round(rect.y()))
                    rw = int(round(rect.width()))
                    rh = int(round(rect.height()))
                    self.rect_drawn.emit(rx, ry, rw, rh)
            return
        super().mouseReleaseEvent(event)
