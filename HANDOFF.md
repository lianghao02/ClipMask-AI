# HANDOFF

## 核心元資料 (Metadata)
- **Repository**：lianghao02/ClipMask-AI
- **Branch**：master
- **Commit SHA**：578c586（本輪修改前基準；本輪修改尚未提交）
- **Skill Version**：v1.0.0
- **Task Type**：FIX
- **Local Path Hint**：12_ClipMask-AI

---

## 後續追加（提交 99c1871 之後，尚未提交）
新增「精確剪輯（不套用遮蔽）」：`export_exact_cut`（exporter.py）重新編碼並精準裁切至工作區間，不帶遮蔽與字幕、不更動原專案；介面按鈕於「剪輯」與「完整工作站」模式顯示，使用 `ExactCutWorker` 背景執行，可取消。完整 pytest 63/63。實測顯示快速剪輯起點會退回前一關鍵影格，壓制匯出則精確。

介面調整（同樣尚未提交）：新增流程步驟列 `lbl_step_guide`（① 設定區間 → ② AI 偵測／手動框選 → ③ 確認軌跡 → ④ 匯出，依狀態標示「目前」步驟與下一步提示；剪輯模式改為兩步）；頂部工具列精簡：「字幕聽打」「完整工作站」收進「更多 ▾」選單（`btn_more_modes`，原按鈕物件保留但隱藏），匯出按鈕與模式按鈕改短標籤，說明移至 tooltip。pytest 65/65；離屏截圖僅驗證版面（無中文字型，文字顯示為方框），未在真實視窗確認文字與外觀。

覆蓋檢查改版（依真實 67 分鐘影片實測：AI 產生 23 條軌跡、82 項缺口，匯出被擋且對話框過長）：`CoverageAnalyzer` 不再要求每條軌跡涵蓋整個工作區間；人物只出現一段屬正常，改以所有軌跡聯集計算「沒有任何遮蔽的時段」，列為提醒（匯出前需使用者確認，預設取消）。仍阻擋：無啟用軌跡、軌跡無關鍵影格、相鄰關鍵影格間隔 >3 秒。單一關鍵影格改為提醒。對話框只顯示前 6 項，完整清單放「顯示詳細資料」；時間軸未覆蓋區段改用同一聯集。此為安全政策調整，需使用者知悉：未遮蔽時段會在確認後原樣輸出。已知未處理：來源有 4 條音軌，壓制匯出只保留第一條；AI 偵測對同一人碎成多條軌跡（約 3 人產生 23 條）。pytest 72/72。

## 本輪目標
修正功能審查找出的三項高風險問題：壓制匯出幀率取整造成音畫不同步、軌跡修改後「已確認」未失效、AI 偵測取消無效。

## 基準與已確認事實 (Baseline & Confirmed Facts)
起始為 master／578c586，工作目錄乾淨，pytest 55/55 通過。三項問題皆先寫測試重現失敗後才修正（fps 實測 20 秒漂移 53 ms）。

## 已完成 (Completed)
- 壓制匯出改用來源精確有理數幀率（如 30000/1001），並依來源時間換算輸出 pts，變動幀率不再被拉成等間隔。`VideoSource` 新增 `fps_fraction`。
- `Track.add_or_update_keyframe` 與 `remove_keyframe_at` 實際變動時將 `reviewed` 重設為 False；手動調整軌跡起訖亦同。
- `FaceDetector.scan_work_range` 新增 `should_cancel`；`AiDetectWorker` 取消時停止掃描且不回傳部分結果。

## 異動檔案 (Changed Files)
clipmask/media/source.py、clipmask/export/exporter.py、clipmask/models/project.py、clipmask/ai/detector.py、clipmask/track/tracker.py、clipmask/gui/main_window.py、tests/test_export_safety.py、tests/test_review_workflow.py、HANDOFF.md。

- P2 已修：重複 AI 偵測後新軌跡的 id／標籤重新編號避免重複（`renumber_ai_tracks`）；「追蹤2秒」目標從未被追到時回傳失敗且不建立關鍵影格；快速剪輯改在背景執行緒（`FastCopyWorker`），並在啟動前檢查目標是否同來源（`validate_destination`）以顯示明確原因。

## 刻意未修改 (Do Not Do / Deliberately Omitted)
未動字幕／VAD（使用者很少用，僅考慮日後預設收合介面）、未重構 main_window.py、未處理下列低風險項目。未更動覆蓋檢查規則（見下方待決策項）。

## 尚未完成 (Remaining Work)
- **P1 (阻斷/必須)**：無已知阻斷。
- **P2 (重要/當次)**：
  - 待使用者決策：AI 軌跡只在區間中段出現時，覆蓋檢查判為嚴重缺口而擋下匯出。放寬會改變安全政策，需確認後再改，並以實片確認發生頻率。
  - 不重複偵測同一張臉：重複執行 AI 偵測仍會對同一人建立多條軌跡（僅修正 id／標籤重複）。
- **P3 (改善建議/暫緩)**：範圍外封包仍被完整讀取、音訊重採樣未 flush、影片旋轉資訊未處理、字幕／VAD 面板預設收合。

## 驗證結果 (Validation)
### 已執行測試與結果
完整 pytest 62/62 通過（新增 7 項測試；除 FastCopyWorker 結果回報測試外，各項修正前皆確認測試失敗）。
### 尚未驗證項目
原生 GUI 人工操作、真實 29.97 fps 影片的音畫同步實測、重新打包的乾淨電腦驗收。
### 已知風險 (Known Risks)
fps 修正僅以合成影片驗證；既有 Portable 下載包不含此修正。

## Git 狀態
- Commit：本輪修改尚未提交，等待使用者授權。
- Working Tree：有未提交修改（上列異動檔案）。
- Branch：master。

## 下一步建議動作 (Next Recommended Action)
使用者確認後提交；再處理 P2 項目，或以真實影片實測音畫同步。
