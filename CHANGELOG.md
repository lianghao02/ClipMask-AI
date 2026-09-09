# 📝 變更紀錄 (CHANGELOG)

本文件記錄 ClipMask-AI 的重要異動，版本格式遵循 Semantic Versioning。

## 🏆 v1.0.1 (2026-09-09) - Stable Release Gate

- **音訊編碼相容性修復**：修正 `RenderExporter` 中的音訊 channel layout 對應，相容非標記或特殊命名音軌（如 WMV `wmav2` 之 "2 channels"），保證穩定轉碼為標準 AAC。
- **容器 FPS 偵測防禦**：針對 WMV/ASF 容器 `base_rate` 過高 (1000) 異常進行上下限防護，避免時間軸跳轉與播放延遲計算偏差。
- **Windows Portable 支援**：提供 PyInstaller 免安裝打包規格 `clipmask.spec`，支援離線環境與模型自動載入。
- **版本庫清理**：解除已追蹤之 Python 快取檔案 (`*.pyc`)。
- **真實公務影音驗證**：3 段真實公務調查案件影音完整通過載入、AI 人臉追蹤、純聲學 VAD、聽打磁吸、模糊壓制與快速剪輯實機驗收。

## 🏆 v1.0.0 (2026-08-31)

- **初始發布**：建立離線影音去識別化工作站的正式版本宣告。
- **核心能力**：提供 PySide6 工作介面、PyAV 影音處理、YuNet 人臉偵測、人工關鍵影格追蹤、純聲學 VAD 與 SRT 字幕工作流程。
- **隱私邊界**：影音、音訊與 AI 推論均在本機執行，不依賴外部 FFmpeg 可執行檔或雲端 API。
