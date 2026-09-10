# HANDOFF

## 目前狀態
可交付（v1.1.0 已正式發布；進入 Stable / Maintenance）

## 本輪目標
修正遮罩重複外擴、改善 Tracks／檢查區操作空間、清單選取與畫面框同步高亮。

## 已完成
- `TrackEvaluator` 分離原始插值框與顯示／匯出框；外擴每邊上限 25%，四邊獨立裁切。
- 修正關鍵影格截斷、合併、常駐、追蹤的座標／PTS 參數順序，避免將已外擴框回存造成持續放大。
- 同時間人工關鍵影格優先於自動追蹤／延伸結果。
- YuNet 原始 bbox 端點採 floor／ceil 邊界保護；未更換模型或降低召回率。
- Tracks 清單改為可捲動、移除 125px 上限；完整工作站加入可拖曳垂直分隔器；頂端工具列可局部橫向捲動。
- 清單選取以亮藍 4px 框高亮，切換／刪除／切換字幕時正確更新；高亮不寫入匯出像素。
- README、CHANGELOG、IMPLEMENTATION_PLAN 補上操作與本輪變更。

## 刻意未修改
- 未更換 YuNet／未調整全域信心閾值；誤抓由人工高亮後刪除整條軌跡。
- 未重構影音核心、RenderExporter 管線、timeline 或全域 QSS。
- 未更動 AI 模型、核心影音管線或無關 P3；發布後不再擴大功能。

## 驗證結果
### 已執行
- Python 3.13.15：`pytest tests/ -q` → 49 passed。
- 相關測試：遮罩、人工優先、重複 padding、編輯流程、選取高亮、清單捲動／splitter、Render → 通過。
- 指定影片本機流程：載入、YuNet 偵測、多軌跡選取高亮、刪除兩條手部誤抓、保留人臉遮蔽、1366／1380／1920 resize、splitter 拖曳、0.8 秒 Render → PASS。
- 指定影片原始證據：80 秒 YuNet 偵測到真正人臉約 `(17,283,237,280)` 信心 0.795，另有手部誤抓約 `(681,290,169,240)` 信心 0.373。

### 尚未驗證
- 未以使用者逐格人工判定所有影片區間的人臉覆蓋；目前實測僅抽查代表性片段與自動流程。
- 未在真實硬體顯示器上驗證 DPI／系統縮放。

### 已知風險
- YuNet 仍可能產生低信心誤抓或漏抓；為避免漏臉，本輪保留候選，使用者需逐條確認後刪除誤抓。

## Git 狀態
- Commit：`91e4c70 release: finalize ClipMask-AI v1.1.0`
- Push：是（`master` 已同步至 `origin/master`）
- Working Tree：Clean（發布提交後）
- Branch：master；Tag `v1.1.0` 已推送。
- Release：<https://github.com/lianghao02/ClipMask-AI/releases/tag/v1.1.0>，Portable Asset `ClipMask-AI-v1.1.0-Portable.zip`（167,572,893 bytes；SHA-256 `c768de085b2ade21d7a27f49fcce7d983b64ea9dc7f387799743d1b17a10e251`）。

## 下一步
僅處理已重現的高嚴重度問題或安全修正；任何功能擴張另開版本規劃。
