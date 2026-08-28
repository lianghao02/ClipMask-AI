import pytest
import numpy as np
import cv2
from clipmask.ai.detector import FaceDetector

def test_face_detector_initialization():
    detector = FaceDetector()
    assert detector.face_cascade.empty() is False
    
    # 測試全黑畫面不會產生崩潰
    blank = np.zeros((100, 100, 3), dtype=np.uint8)
    faces = detector.detect_in_frame(blank)
    assert isinstance(faces, list)
    assert len(faces) == 0
