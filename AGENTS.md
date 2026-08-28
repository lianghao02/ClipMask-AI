# 🤖 ClipMask-AI Agent 指南 (AGENTS.md)

> **核心定位**：極速離線影音去識別化、AI 人臉追蹤、純聲學 VAD 語音標記與智慧聽打工作站。
> **遵循標準**：全域開發憲法 v8.1（100% 台灣繁體中文、莫蘭迪手帳風格、零外部 FFmpeg 依賴、離線隱私第一）。

---

## 1. 核心架構與技術棧約束

- **GUI 框架**：`PySide6` (Qt 6.6+)，採用日系莫蘭迪手帳風格 QSS (`clipmask/gui/styles.py`)。
- **視訊解碼引擎**：`PyAV` (FFmpeg C Binding)，嚴格精確對齊 PTS 時間戳，相容 VFR 與長 GOP 串流。
- **解碼管線隔離 (Decoupled Pipeline)**：
  - `VideoSource`：主畫面播放與拖曳（支援 `seek_fast` 關鍵影格秒刷與 `seek_exact` 精確解碼）。
  - `ThumbnailExtractor`：獨立低負載懸浮縮圖管線，在時間軸 Hover 時提取 $160\times90$ 縮圖。
- **AI 視覺模組**：
  - `FaceDetector` (`models/face/face_detection_yunet_2023mar.onnx`)：YuNet 深度學習人臉偵測，預設門檻 `0.35`，支援遠景小臉、多目標捕捉與 3.0s 動作關聯聚合。
  - `MicroTracker`：基於 OpenCV CSRT 的手動框選向後追蹤器。
  - `TrackEvaluator`：關鍵影格 Lerp 線性內插與 25% 安全外擴 Padding 邊界計算。
- **純聲學 VAD 模組**：
  - `VoiceActivityDetector`：PyAV 音軌短時 RMS 能量與動態門檻掃描；預設以 50ms 視窗提取人聲活動區間，避免引入 STT 文字辨識。
- **無損秒出引擎**：
  - `FastCopyExporter`：純原生 PyAV `add_stream_from_template` 封包轉發，零系統 `ffmpeg.exe` 依賴；實際輸出時間依來源檔、儲存媒體與容器而定。
- **Single-pass 壓制引擎**：
  - `RenderExporter`：逐影格套用馬賽克/高斯模糊、繁中字卡燒錄並以 PyAV 壓制輸出。

---

## 2. 工程規範與嚴禁事項

1. **嚴禁破壞離線安全性**：所有影像、音訊與 AI 運算嚴禁引入任何雲端 API 或外部未授權網路請求。
2. **字型與 Windows 相容**：字卡與介面字型一律優先使用 `Microsoft JhengHei`（微軟正黑體）與系統預設無襯線字型。
3. **二進位檔案安全**：音訊、影片、模型與圖片檔案讀寫嚴禁套用文字編碼參數。
4. **測試覆蓋**：任何核心演算法或 GUI 改動後，必須執行 `pytest tests/ -v` 確保全套測試通過。
