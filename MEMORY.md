# 🧠 技術決策與踩坑歷史 (MEMORY.md)

## 1. 重要架構決策 (Architecture Decisions)

- **捨棄外部 FFmpeg.exe，全面改為純原生 PyAV Stream Copy**：
  - 原因：部分 Windows 機器未將 ffmpeg 設入 PATH，導致無損剪輯失敗。
  - 處方：使用 PyAV `add_stream_from_template` 與封包級 `mux` 轉發，達成零外部可執行檔依賴的串流複製；輸出速度依來源檔與儲存媒體而定。
- **純聲學 VAD 取代語音辨識 (STT) 語音標記**：
  - 原因：現場採訪、吵雜環境與國台語/在地口音容易造成 AI 錯字連篇。
  - 處方：使用短時 RMS 能量與動態門檻直接提取真實發音區間，時間軸顯示鼠尾草綠語音條，打字按 Enter 一鍵磁吸起訖秒數。
- **雙軌解碼管線隔離 (Decoupled Pipeline)**：
  - 時間軸懸浮縮圖採用獨立 `ThumbnailExtractor`，主畫面與播放執行緒採用 `VideoSource`，互不爭搶解碼器鎖。
- **多目標人臉 3.0s 長間隔關聯與全段常駐機制**：
  - 解決後方人物轉頭或低頭時馬賽克突然消失中斷的問題。手動框選亦自動延伸至整個 WorkRange。

## 2. Bug 修復與細節記憶

- **Qt 模組 Import 遺漏**：`QLineEdit`, `SubtitleItem`, `QIcon` 均已正確加入。
- **Timeline 佈局作用域**：`_on_canvas_leave` 函式獨立於 `init_ui` 外部，防止佈局階層被截斷。
- **Windows CP950 控制台 Emoji 輸出**：命令列呼叫時需設定 `PYTHONIOENCODING=utf-8`。
