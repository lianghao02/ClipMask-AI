# 🏗️ 系統架構與模組設計 (ARCHITECTURE.md)

## 1. 模組分層拓撲

```text
[ GUI Layer (PySide6) ]
  ├── MainWindow (clipmask/gui/main_window.py)
  ├── VideoGraphicsView (clipmask/gui/video_view.py)  <-- 原生像素座標系與 Live Typing HUD
  ├── TimelineWidget (clipmask/gui/timeline.py)        <-- 雙軌 Pro 時間軸 + 懸浮縮圖 Popup
  └── MorandiStyles (clipmask/gui/styles.py)

[ Core Media & Decoding Engine ]
  ├── VideoSource (clipmask/media/source.py)           <-- PyAV 精確/快速解碼
  └── ThumbnailExtractor (clipmask/media/source.py)   <-- 獨立輕量縮圖提取

[ AI & Signal Processing Modules ]
  ├── FaceDetector (clipmask/ai/detector.py)          <-- YuNet 0.35 多目標人臉追蹤
  ├── MicroTracker (clipmask/track/tracker.py)        <-- CSRT 向後預測
  ├── TrackEvaluator (clipmask/track/evaluator.py)    <-- 關鍵影格 Lerp 內插與 Padding
  ├── VoiceActivityDetector (clipmask/ai/vad.py)      <-- 純聲學 RMS 能量 VAD
  └── SubtitleManager (clipmask/ai/subtitles.py)      <-- 繁中字卡繪製與 SRT 匯出

[ Export & Rendering Pipelines ]
  ├── FastCopyExporter (clipmask/export/exporter.py)  <-- 純 PyAV 原生 Stream Copy (秒出)
  └── RenderExporter (clipmask/export/exporter.py)    <-- Single-pass 馬賽克/字卡壓制
```

## 2. 核心數據結構 (Models)

- `ProjectState`：包含 `SourceMetadata`、`WorkRange`、`List[Track]` 與字幕字典清單，支援完整 JSON 序列化。
- `Track`：由多顆 `Keyframe (time, pts, rect_px)` 組成，支援多目標多標籤。
- `SubtitleItem`：執行期資料類別，包含 `id`、`start_sec`、`end_sec`、`text`；寫入 `ProjectState` 時使用相容的字幕字典資料。
