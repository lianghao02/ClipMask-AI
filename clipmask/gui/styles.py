"""
ClipMask-AI Modern Morandi Dark Theme (QSS)
現代深石墨灰、霧面石青與圓角卡片式專業影音主題。
"""

DARK_THEME_QSS = """
/* 全域基礎設定 */
QMainWindow, QWidget {
    background-color: #16171a;
    color: #e2e4e9;
    font-family: "Segoe UI", "Microsoft JhengHei", "PingFang TC", sans-serif;
    font-size: 12px;
}

/* 分割面板 */
QSplitter::handle {
    background-color: #24262c;
    width: 2px;
}

/* 按鈕樣式 */
QPushButton {
    background-color: #262830;
    color: #e2e4e9;
    border: 1px solid #363942;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #323540;
    border-color: #484c58;
}
QPushButton:pressed {
    background-color: #1e2026;
}
QPushButton:disabled {
    background-color: #1c1d22;
    color: #555864;
    border-color: #262830;
}

/* 核心操作主按鈕 */
QPushButton#btn_primary {
    background-color: #2563eb;
    color: #ffffff;
    border: 1px solid #3b82f6;
    font-weight: bold;
}
QPushButton#btn_primary:hover {
    background-color: #1d4ed8;
    border-color: #60a5fa;
}

/* 群組卡片面板 */
QGroupBox {
    background-color: #1e2026;
    border: 1px solid #2a2d36;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 14px;
    font-weight: bold;
    color: #94a3b8;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
    background-color: #16171a;
}

/* 清單列表 (Tracks) */
QListWidget {
    background-color: #18191f;
    border: 1px solid #2a2d36;
    border-radius: 6px;
    padding: 4px;
    outline: none;
}
QListWidget::item {
    background-color: #20222a;
    border: 1px solid #2a2d36;
    border-radius: 5px;
    margin: 3px 2px;
    padding: 8px 10px;
    color: #e2e4e9;
}
QListWidget::item:hover {
    background-color: #292c36;
    border-color: #3d414e;
}
QListWidget::item:selected {
    background-color: #1e3a5f;
    border: 1px solid #3b82f6;
    color: #60a5fa;
    font-weight: bold;
}

/* 下拉選單與數值調整框 */
QComboBox, QSpinBox {
    background-color: #20222a;
    border: 1px solid #363942;
    border-radius: 5px;
    padding: 5px 8px;
    color: #e2e4e9;
}
QComboBox:hover, QSpinBox:hover {
    border-color: #484c58;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #20222a;
    border: 1px solid #363942;
    selection-background-color: #1e3a5f;
    selection-color: #60a5fa;
}

/* 訊息與進度條 */
QProgressBar {
    background-color: #1c1d22;
    border: 1px solid #2a2d36;
    border-radius: 5px;
    text-align: center;
    color: #e2e4e9;
    font-weight: bold;
}
QProgressBar::chunk {
    background-color: #3b82f6;
    border-radius: 4px;
}
"""
