# 🛡️ ClipMask-AI v1.0.0

> **極速離線影音去識別化、AI 人臉追蹤與智慧聽打工作站**
> *Designed for Newsrooms, Law Enforcement, Public Sector & Privacy Protection.*

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12%20%7C%203.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-v1.0.0-blue.svg)](CHANGELOG.md)
[![GUI](https://img.shields.io/badge/GUI-PySide6%20%2F%20Qt6-41CD52?logo=qt&logoColor=white)](https://www.qt.io/)
[![Engine](https://img.shields.io/badge/Video%20Engine-PyAV%20(FFmpeg%20C%20Binding)-007808?logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Style](https://img.shields.io/badge/Design-Morandi%20Journal-8C7A6B)](clipmask/gui/styles.py)

---

## 📖 專案簡介

**ClipMask-AI** 是一款專為**電視新聞記者、調查報導、公務執法、醫療教育與隱私工作者**量身打造的現代影音工具。

傳統專業剪輯軟體（如 Premiere、DaVinci）體積龐大且學習成本高；而線上 AI 辨識工具又有嚴重**個資外洩與雲端上傳資安風險**。ClipMask-AI 實現了 **100% 離線本地運算**，將「**AI 人臉自動偵測追蹤**」、「**手動關鍵影格平滑微調**」、「**純聲學人聲活動標記 (VAD)**」、「**即時人工聽打字幕 (在地口音/現場音專用)**」與「**極速無損剪輯**」完美融合於日系莫蘭迪手帳風格介面中。

---

## ✨ 核心特色

### 1. 🤖 AI 智能偵測 ＋ 人工關鍵影格 (AI + Human-in-the-loop)
- **ONNX YuNet 深度學習**：內嵌輕量人臉偵測模型，自動掃描工作區間內所有人物並建立連續追蹤軌跡。
- **芥末暖黃關鍵影格 (🔷 Keyframes)**：AI 漏抓或轉頭時，隨時按 `K` 鍵或拉框手動打點；系統採用 **Lerp 線性內插平滑補間**，徹底杜絕 1 格露餡的法律風險。
- **關鍵影格快速跳轉**：支援 `[` / `]` 一鍵跳轉至上一顆 / 下一顆鑽石標記 🔷。

### 2. 🎙️ 純聲學語音活動偵測 (VAD) ＋ 智慧聽打字幕
- **純聲學能量掃描 (零 STT 錯字地獄)**：避開國台語、方言、吵雜現場音的 AI 錯字困擾，以 PyAV 毫秒級提取人聲發言區間。
- **時間軸鼠尾草綠語音條 (🌿 Speech Bar)**：影片載入自動在時間軸上方標記出所有有人說話的區間。
- **🧲 一鍵智慧磁吸起訖**：打字按 `Enter`，字幕起訖秒數自動對齊當前語音段的真實起點與結尾！
- **打字機 Live HUD**：邊打字、畫面即時同步浮現半透明繁中手帳字卡。
- **一鍵匯出**：支援直接燒錄進去識別影片，亦可單獨匯出標準繁中 `.srt` 字幕檔。

### 3. ⏱️ 雙軌 Pro 時間軸與懸浮縮圖 (Hover Thumbnails)
- **👀 懸浮小縮圖偷看 (YouTube / Premiere 模式)**：滑鼠移到時間軸任何位置，自動飄出 160×90 圓角小縮圖偷看場景，主畫面完全不被打擾。
- **⚡ 雙軌極速秒刷**：拖曳中採用 0 毫秒關鍵影格瞬刷；放開滑鼠精確停格。
- **手帳 Work Range 剪輯**：右鍵拖拉或 `Shift+左鍵` 自由框出剪輯範圍。

### 4. ⚡ 雙模式安全匯出
- **🛡️ 匯出馬賽克影片 (壓制遮蔽)**：Single-pass 渲染引擎，將 AI/手動馬賽克、高斯模糊與聽打字幕一次壓制完成。
- **⚡ 純剪輯影片 (無馬賽克/快速輸出)**：由 PyAV 進行封包層串流複製，不需要系統安裝 `ffmpeg.exe`；輸出速度與切點精度依來源檔的關鍵影格和容器而定。
- **智慧防重名命名**：自動帶入原片名與時間碼戳記，避免覆寫遺憾。

---

## ⌨️ 鍵盤快捷鍵一覽

| 快捷鍵 | 功能說明 |
| :--- | :--- |
| **`Space`** | 播放 / 暫停 |
| **`←` / `→`** | 後退 1 秒 / 前進 1 秒 |
| **`↑` / `↓`** | 前進 0.1 秒 / 後退 0.1 秒 |
| **`滑鼠滾輪`** | 逐格前進 / 後退 1 幀（`Shift+滾輪` 快跳 5 幀） |
| **`J` / `L`** | 上一幀 / 下一幀 |
| **`I` / `O`** | 設定工作區間起點 (In) / 終點 (Out) |
| **`[` / `]`** | 跳至上一個 🔷 / 下一個 🔷 關鍵影格 |
| **`K`** | 在當前秒數新增 / 刪除關鍵影格 🔷 |
| **`T`** | 游標快速聚焦到聽打字幕輸入框 |
| **`Enter`** | 立即打點固化當前字幕（自動磁吸語音段） |

---

## 🚀 快速開始

### 系統需求
- Windows 10 / 11 (64-bit)
- Python 3.11 ~ 3.13
- 不需要另外安裝 `ffmpeg.exe`；PyAV 會隨 Python 套件一併提供所需媒體函式庫。

### 安裝步驟

```powershell
# 1. 複製專案庫
git clone https://github.com/<your-username>/ClipMask-AI.git
cd ClipMask-AI

# 2. 安裝必要套件
pip install -r requirements.txt

# 3. 啟動工作站
python run.py
```

> **Windows 便捷啟動**：亦可直接雙擊專案根目錄下的 `啟動ClipMask-AI.bat`！

---

## 🧪 單元測試

本專案包含完整的全流程自動化測試：

```powershell
pytest tests/ -v
```

測試涵蓋：
- 專案狀態 JSON 序列化與跨平台反序列化
- Lerp 線性內插與邊界安全 Padding 檢驗
- YuNet 深度學習人臉偵測與 IoU 空間距離運算
- CSRT 微追蹤器向後預測
- 音訊純聲學 VAD 活動偵測與起訖磁吸
- SRT 字幕解析與標準格式匯出
- Single-pass 壓制匯出全管道整合測試

---

## 📄 開源授權

本專案採用 [MIT License](LICENSE) 授權開源。
