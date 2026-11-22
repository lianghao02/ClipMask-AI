# 🏗️ 系統架構與模組設計 (ARCHITECTURE.md)

## 1. 模組分層拓撲

```text
[ GUI Layer (PySide6) ]
  ├── MainWindow (clipmask/gui/main_window.py)        <-- 五大工作模式切換與審核閘門
  ├── CompressPage (clipmask/gui/compress_page.py)    <-- 獨立影片容量壓縮工作頁 (v1.3.0)
  ├── VideoGraphicsView (clipmask/gui/video_view.py)  <-- 原生像素座標系與 Live Typing HUD
  ├── TimelineWidget (clipmask/gui/timeline.py)       <-- 雙軌 Pro 時間軸 + 懸浮縮圖 Popup
  └── MorandiStyles (clipmask/gui/styles.py)

[ Core Media & Decoding Engine ]
  ├── VideoSource (clipmask/media/source.py)          <-- PyAV 精確/快速解碼
  ├── ThumbnailExtractor (clipmask/media/source.py)   <-- 獨立輕量縮圖提取
  └── AudioExtractor (clipmask/media/audio.py)        <-- 音訊串流與能量擷取

[ AI & Signal Processing Modules ]
  ├── FaceDetector (clipmask/ai/detector.py)          <-- YuNet 0.35 多目標人臉追蹤 + 鄰近軌跡自動合併
  ├── MicroTracker (clipmask/track/tracker.py)        <-- CSRT 向後預測
  ├── TrackEvaluator (clipmask/track/evaluator.py)    <-- 關鍵影格 Lerp 內插與 Padding (上限 25%)
  ├── CoverageAnalyzer (clipmask/track/coverage.py)   <-- 匯出前覆蓋缺口與間隔檢查
  ├── VoiceActivityDetector (clipmask/ai/vad.py)      <-- 純聲學 RMS 能量 VAD
  └── SubtitleManager (clipmask/ai/subtitles.py)      <-- 繁中字卡繪製與 SRT 匯出

[ Export & Compression Pipelines ]
  ├── FastCopyExporter (clipmask/export/exporter.py)  <-- 純 PyAV 原生 Stream Copy (無遮蔽)
  ├── export_exact_cut (clipmask/export/exporter.py)  <-- 重新編碼精準裁切 (無遮蔽)
  ├── RenderExporter (clipmask/export/exporter.py)    <-- Single-pass 馬賽克/模糊/字卡壓制
  └── VideoCompressor (clipmask/export/compressor.py) <-- 依目標容量推算位元率與解析度 (v1.3.0)
```

## 2. 核心數據結構 (Models)

- `ProjectState`：包含 `SourceMetadata`、`WorkRange`、`List[Track]` 與字幕字典清單，支援完整 JSON 序列化。
- `Track`：由多顆 `Keyframe (time, pts, rect_px, source)` 組成，含 `enabled`、`reviewed`（任何編修會自動重設為 `False`）與 `auto_merged` 旗標。
- `SubtitleItem`：執行期資料類別，包含 `id`、`start_sec`、`end_sec`、`text`；寫入 `ProjectState` 時使用相容的字幕字典資料。

## 3. 核心安全與工作流圖解

### 去識別化安全流程與匯出閘門

```mermaid
flowchart TD
    A["1. 開啟影片並設定工作區間 (In ~ Out)"] --> B{"選擇遮蔽建立方式"}
    B -->|"AI 偵測人臉"| C["YuNet 掃描 (每 0.25s 取樣, 門檻 0.35)"]
    C --> D["自動合併相接軌跡\n(間隔 <= 6.0s 且距離 < 3 倍臉寬, 每 2.0s 線性補點)"]
    B -->|"手動框選 / K 鍵打點"| E["建立手動關鍵影格\n(同時間點不受 AI 結果覆蓋)"]
    D --> F["軌跡狀態：待檢查 (reviewed = False)"]
    E --> F

    F --> G{"任何編輯 / 改起訖?"}
    G -->|"是"| F
    G -->|"人工逐條巡檢確認"| H["勾選已確認 (reviewed = True)"]

    H --> I["按「匯出馬賽克影片」"]
    I --> J{"閘門 1：是否仍有待檢查軌跡?"}
    J -->|"有"| K["直接阻擋匯出\n提示回到清單逐條確認"]
    J -->|"全部已確認"| L["閘門 2：CoverageAnalyzer 覆蓋率檢查"]

    L --> M{"嚴重缺口?\n(無啟用軌跡 / 無關鍵影格 / 影格間隔 > 3.0s)"}
    M -->|"是"| N["嚴重阻擋匯出\n須補齊關鍵影格"]
    M -->|"否"| O{"僅單點關鍵影格 或\n工作區間有無遮蔽時段 (> 0.1s)?"}
    O -->|"有"| P["跳出確認對話框 (預設取消)\n列出未遮蔽秒數由人工決定"]
    P -->|"取消"| F
    P -->|"確認繼續"| Q["RenderExporter 安全原子寫入"]
    O -->|"無風險訊息"| Q

    Q --> R["檢查非來源同檔 → 寫入暫存檔\n逐幀 Lerp 內插＋安全外擴＋遮蔽壓制 → 成功後取代輸出檔"]
```

### 五大工作模式與輸出路徑總覽

```mermaid
flowchart LR
    U["MainWindow 工作站切換"] --> M1["🛡️ 去識別化 (mask)"]
    U --> M2["🎙️ 字幕聽打 (transcribe)"]
    U --> M3["✂️ 剪輯模式 (cut)"]
    U --> M4["🧰 完整工作站 (full)"]
    U --> M5["📦 影片壓縮 (compress)"]

    M1 --> P1["FaceDetector + CoverageAnalyzer"]
    M2 --> P2["純聲學 VAD (RMS 能量) + SubtitleManager"]
    M4 --> P1
    M4 --> P2

    P1 --> E1["RenderExporter\n(壓制遮蔽與字幕, 只輸出工作區間)"]
    P2 --> E1
    P2 --> E2["匯出 .srt 字幕檔"]

    M3 --> E3["FastCopyExporter\n(封包層無損秒剪, 無遮蔽)"]
    M3 --> E4["export_exact_cut\n(重新編碼精準裁切, 無遮蔽)"]

    M5 --> C1["CompressPage + compressor.py\n(依 DVD 4,300MB / 7,900MB / 500MB 等目標重編碼)"]
```
