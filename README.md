# ClipMask-AI

> **智慧影音去識別化與離線剪輯工作站**
> 專為公務、執法、醫療與個資保護打造的極速離線影音處理工具。

## 核心特性
- ⚡ **雙模式匯出**：
  - **快速無損剪輯**：基於 FFmpeg Stream Copy，2 秒秒出檔案，零畫質損失。
  - **去識別化壓制**：Single-pass Pipeline，自動套用馬賽克/高斯模糊並壓制輸出。
- 🎯 **幀精確定位**：底層採用 PyAV (FFmpeg C binding)，精確對齊 PTS 時間戳，相容 VFR 與長 GOP 影片。
- 📐 **原生像素座標系**：基於 PySide6 `QGraphicsScene`，滑鼠拉框即是影片原始解析度座標。
- 🛡️ **安全 Padding 與 Privacy Mode**：遮蔽框自動外擴 15%，防止運動模糊露餡；硬邊界處理防透光。
- 🔒 **100% 離線運作**：所有影音運算皆在本地執行，極致保護機敏資訊。

## 快速啟動
雙擊執行根目錄下的 `啟動ClipMask-AI.bat` 或在終端機執行：
```powershell
python run.py
```
