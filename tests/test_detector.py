import pytest
import numpy as np
from clipmask.ai.detector import FaceDetector

def test_yunet_face_detector():
    detector = FaceDetector(conf_threshold=0.5)
    
    # 測試全黑畫面正常回傳空清單
    blank = np.zeros((300, 300, 3), dtype=np.uint8)
    faces = detector.detect_in_frame(blank)
    assert isinstance(faces, list)
    assert len(faces) == 0

def test_iou_and_center_distance():
    boxA = (10, 10, 50, 50)
    boxB = (10, 10, 50, 50)
    boxC = (100, 100, 50, 50)
    
    iou_same = FaceDetector._calculate_iou(boxA, boxB)
    assert abs(iou_same - 1.0) < 1e-4
    
    iou_diff = FaceDetector._calculate_iou(boxA, boxC)
    assert iou_diff == 0.0
    
    dist = FaceDetector._center_distance(boxA, boxB)
    assert dist == 0.0
