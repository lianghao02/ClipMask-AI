"""
ClipMask-AI 日系 Q 版遊戲面板質感樣式（圓潤澎皮按鈕、糖霜木框雙層卡片、清晰高對比台灣繁體中文字級）
保留明確工具名詞與操作流程，僅強化視覺圓潤質感、按鈕立體按壓與全面板字級可讀性。
"""

MORANDI_JOURNAL_QSS = """
QMainWindow, QWidget {
    background-color: #f4e9d6;
    color: #3d2410;
    font-family: "Segoe UI", "Microsoft JhengHei UI", "PingFang TC", sans-serif;
    font-size: 14px;
}

QSplitter::handle {
    background-color: #dec9a4;
    border-radius: 4px;
}
QSplitter::handle:hover {
    background-color: #c29d67;
}

/* 基礎日系 Q 版澎潤立體按鈕（柔和漸層高光 + 圓角可可邊框 + 實體下緣，按壓下沉） */
QPushButton {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:0.45 #fff8e7, stop:1 #f4e3c1);
    color: #3d2410;
    border-top: 2.5px solid #6b4528;
    border-left: 2.5px solid #6b4528;
    border-right: 2.5px solid #6b4528;
    border-bottom: 4.5px solid #8a5a32;
    border-radius: 12px;
    padding: 5px 13px;
    font-size: 14px;
    font-weight: 700;
}
QPushButton:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:0.5 #fffbef, stop:1 #faeacc);
    border-color: #54341c;
    border-bottom: 4.5px solid #a66c3b;
}
QPushButton:pressed {
    background-color: #edd8b1;
    border-top: 4px solid #8a5a32;
    border-bottom: 2px solid #6b4528;
    padding-top: 7px;
    padding-bottom: 3px;
}
QPushButton:checked {
    background-color: #f9e3b4;
    color: #2f1a0a;
    border-top: 3px solid #6b4528;
    border-bottom: 2.5px solid #6b4528;
}
QPushButton:disabled {
    background-color: #e4d8c0;
    color: #6b5a46;
    border-color: #a69276;
    border-bottom: 2.5px solid #a69276;
}

/* 工具列捲軸區域透明化 */
QScrollArea {
    background-color: transparent;
    border: none;
}

/* 主要 CTA 按鈕（陽光蜜橘主色：匯出影片、開始壓縮、開啟影片卡片） */
QPushButton#btn_primary {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ff9e59, stop:0.45 #f0762d, stop:1 #d95e1a);
    color: #ffffff;
    border-top: 2.5px solid #6b2e0a;
    border-left: 2.5px solid #6b2e0a;
    border-right: 2.5px solid #6b2e0a;
    border-bottom: 4.5px solid #8c3c0e;
    font-weight: 700;
    padding: 6px 15px;
}
QPushButton#btn_primary:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffae70, stop:0.5 #f7843d, stop:1 #e36a24);
}
QPushButton#btn_primary:pressed {
    background-color: #cc5818;
    border-top: 4px solid #8c3c0e;
    border-bottom: 2px solid #6b2e0a;
    padding-top: 8px;
    padding-bottom: 4px;
}
QPushButton#btn_primary:disabled {
    background-color: #d6c5a9;
    color: #faf3e6;
    border-color: #aa987d;
    border-bottom: 2.5px solid #aa987d;
}

/* 單目標追蹤強調按鈕（春日薄荷綠） */
QPushButton#btn_track_primary {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #73ad75, stop:0.5 #57915a, stop:1 #467a49);
    color: #ffffff;
    border-top: 2.5px solid #264729;
    border-left: 2.5px solid #264729;
    border-right: 2.5px solid #264729;
    border-bottom: 4.5px solid #2c5730;
    font-weight: 700;
}
QPushButton#btn_track_primary:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #84bd86, stop:1 #538a56);
}
QPushButton#btn_track_primary:pressed {
    background-color: #3f6e42;
    border-top: 4px solid #2c5730;
    border-bottom: 2px solid #264729;
}
QPushButton#btn_track_primary:disabled {
    background-color: #c5cfb9;
    color: #f1f5ec;
    border-color: #9ba892;
    border-bottom: 2.5px solid #9ba892;
}

/* AI 輔助按鈕（晴空藍晶石） */
QPushButton#btn_ai {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #709ac2, stop:0.5 #507aa3, stop:1 #41688f);
    color: #ffffff;
    border-top: 2.5px solid #243e57;
    border-left: 2.5px solid #243e57;
    border-right: 2.5px solid #243e57;
    border-bottom: 4.5px solid #2c4c6b;
    font-weight: 700;
}
QPushButton#btn_ai:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #82abd1, stop:1 #4d78a1);
}
QPushButton#btn_ai:pressed {
    background-color: #385c7f;
    border-top: 4px solid #2c4c6b;
    border-bottom: 2px solid #243e57;
}
QPushButton#btn_ai:disabled {
    background-color: #bfc8d1;
    color: #eff3f7;
    border-color: #9aa5b0;
    border-bottom: 2.5px solid #9aa5b0;
}

/* 危險／刪除按鈕（粉櫻緋紅提醒） */
QPushButton.danger-btn {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fff0ee, stop:1 #f7d6d1);
    color: #85241e;
    border-top: 2.5px solid #963932;
    border-left: 2.5px solid #963932;
    border-right: 2.5px solid #963932;
    border-bottom: 4px solid #963932;
    font-weight: 700;
}
QPushButton.danger-btn:hover {
    background-color: #f4c6bf;
    color: #691712;
}
QPushButton.danger-btn:pressed {
    background-color: #eab5ad;
    border-top: 3.5px solid #963932;
    border-bottom: 2px solid #963932;
}

/* 側欄／時間軸緊湊操作按鈕（保持明顯字級與圓潤手感） */
QPushButton.compact-btn {
    padding: 4px 10px;
    border-radius: 9px;
    font-size: 13px;
    min-height: 24px;
}

/* 頂部 Q 版遊戲分頁托盤與立體頁簽 */
QFrame#modeTrack {
    background-color: #e4cfa9;
    border: 2.5px solid #6b4528;
    border-bottom: 4px solid #6b4528;
    border-radius: 13px;
}

QPushButton.mode-btn {
    background-color: #f5e9cf;
    color: #5c3b1e;
    border: 1.5px solid #ba996c;
    border-bottom: 2.5px solid #a68254;
    border-radius: 9px;
    padding: 4px 11px;
    font-size: 13.5px;
    font-weight: 700;
}
QPushButton.mode-btn:hover {
    background-color: #fff6e0;
    color: #3d2410;
    border-color: #8a5a32;
}
QPushButton.mode-btn:checked {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #fff4d6);
    color: #2f1a0a;
    border-top: 2.5px solid #6b4528;
    border-left: 2.5px solid #6b4528;
    border-right: 2.5px solid #6b4528;
    border-bottom: 4px solid #f0762d;
    font-weight: 700;
}

/* 壓縮目標預設按鈕（清晰圓角糖霜方塊） */
QPushButton.preset-btn {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fffcf5, stop:1 #f2e2c2);
    color: #4a2e16;
    border-top: 2px solid #8a5a32;
    border-left: 2px solid #8a5a32;
    border-right: 2px solid #8a5a32;
    border-bottom: 4px solid #8a5a32;
    border-radius: 11px;
    padding: 6px 8px;
    font-size: 13.5px;
    font-weight: 700;
}
QPushButton.preset-btn:hover {
    background-color: #fff9eb;
    border-color: #6b4528;
}
QPushButton.preset-btn:checked {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #ffefc9);
    color: #2f1a0a;
    border-top: 2.5px solid #6b4528;
    border-left: 2.5px solid #6b4528;
    border-right: 2.5px solid #6b4528;
    border-bottom: 4.5px solid #f0762d;
}

/* 時間軸按鈕分組外框 */
QFrame#timelineGroup {
    background-color: #fffaf0;
    border: 2.5px solid #6b4528;
    border-bottom: 4px solid #8a5a32;
    border-radius: 12px;
}

/* 影片畫布外框與側欄主卡片（日系 Q 版圓角雙層軟木卡片） */
QFrame#videoStageFrame {
    background-color: #fff9ec;
    border: 3px solid #6b4528;
    border-bottom: 5px solid #59361c;
    border-radius: 14px;
}
QFrame#panelCard {
    background-color: #fffaf0;
    border: 3px solid #6b4528;
    border-bottom: 5px solid #59361c;
    border-radius: 14px;
}
QLabel#panelTitle {
    font-size: 16px;
    font-weight: 700;
    color: #3d2410;
    background-color: transparent;
}

/* 單條項目與目標預設內嵌蜜糖槽位 */
QFrame#detailCard {
    background-color: #f9efd9;
    border: 2px solid #966b41;
    border-radius: 11px;
}

/* 空狀態拖放軟糖卡片 & 畫布忙碌視窗 */
QFrame#emptyDropCard {
    background-color: #fffdf6;
    border: 3.5px dashed #8f6239;
    border-radius: 18px;
}
QFrame#busyCard {
    background-color: #fffdf6;
    border: 3px solid #6b4528;
    border-bottom: 5px solid #59361c;
    border-radius: 16px;
}
QFrame#emptyDropCard QLabel,
QFrame#busyCard QLabel,
QFrame#panelCard QLabel,
QFrame#detailCard QLabel,
QFrame#panelCard QCheckBox,
QFrame#detailCard QCheckBox {
    background-color: transparent;
}

/* 遮蔽清單與字幕清單（Q 版圓角道具卡） */
QListWidget {
    background-color: #f2e4c7;
    border: 2px solid #8a5a32;
    border-radius: 11px;
    padding: 4px;
    font-size: 13px;
    outline: none;
}
QListWidget::item {
    background-color: #fffdf6;
    border: 2px solid #b89568;
    border-radius: 9px;
    margin: 1px 1px;
    padding: 2px 9px;
    font-size: 13px;
    color: #3d2410;
}
QListWidget::item:hover {
    background-color: #fff5d9;
    border-color: #6b4528;
}
QListWidget::item:selected {
    background-color: #e2f4df;
    border: 2.5px solid #3f7543;
    color: #1c3d1f;
    font-weight: 700;
}

/* 下拉選單、數值與輸入框（Q 版圓潤大輸入槽） */
QComboBox, QSpinBox, QLineEdit {
    background-color: #fffdf6;
    border: 2px solid #8a5a32;
    border-radius: 9px;
    padding: 4px 10px;
    min-height: 24px;
    font-size: 14px;
    color: #3d2410;
}
QLineEdit {
    min-height: 28px;
}
QSpinBox::up-button, QSpinBox::down-button {
    width: 22px;
}
QComboBox:hover, QSpinBox:hover, QLineEdit:focus {
    border-color: #6b4528;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    background-color: #fffdf6;
    border: 2.5px solid #6b4528;
    border-radius: 8px;
    selection-background-color: #e2f4df;
    selection-color: #1c3d1f;
}

/* Q 版圓弧進度能量條 */
QProgressBar {
    background-color: #e6d4b3;
    border: 2.5px solid #6b4528;
    border-radius: 10px;
    text-align: center;
    color: #3d2410;
    font-weight: bold;
    min-height: 20px;
}
QProgressBar::chunk {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #7cc27e, stop:1 #4a8a4d);
    border-radius: 7px;
}
"""
