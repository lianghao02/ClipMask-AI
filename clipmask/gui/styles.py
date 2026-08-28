"""
ClipMask-AI Modern Morandi Journal / Handcraft Style (現代莫蘭迪日系手帳風格)
燕麥奶茶底色、手作卡片感、鼠尾草綠、霧面陶土紅與溫潤暖灰。
"""

MORANDI_JOURNAL_QSS = """
/* 全域手帳溫暖底色 */
QMainWindow, QWidget {
    background-color: #f7f5f0;
    color: #383734;
    font-family: "Segoe UI", "Microsoft JhengHei", "PingFang TC", sans-serif;
    font-size: 12px;
}

/* 分割面板 */
QSplitter::handle {
    background-color: #e5e0d8;
    width: 3px;
}

/* 手帳按鈕樣式 */
QPushButton {
    background-color: #ffffff;
    color: #4a4843;
    border: 1px solid #ddd7cd;
    border-radius: 8px;
    padding: 7px 14px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #f0ece1;
    border-color: #c9c2b5;
    color: #2b2a27;
}
QPushButton:pressed {
    background-color: #e5e0d3;
}
QPushButton:disabled {
    background-color: #ede9e1;
    color: #a8a49c;
    border-color: #e0dbd1;
}

/* 核心主要按鈕 (鼠尾草綠) */
QPushButton#btn_primary {
    background-color: #5f8768;
    color: #ffffff;
    border: 1px solid #4f7357;
    font-weight: bold;
}
QPushButton#btn_primary:hover {
    background-color: #507458;
    border-color: #3f5d46;
}

/* AI 人臉按鈕 (莫蘭迪霧藍) */
QPushButton#btn_ai {
    background-color: #5c7c99;
    color: #ffffff;
    border: 1px solid #4a6882;
    font-weight: bold;
}
QPushButton#btn_ai:hover {
    background-color: #4c6a85;
}

/* 手帳卡片面板 (GroupBox) */
QGroupBox {
    background-color: #ffffff;
    border: 1px solid #e2ddd4;
    border-radius: 10px;
    margin-top: 14px;
    padding: 16px 12px 12px 12px;
    font-weight: bold;
    color: #6b665c;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 2px 8px;
    background-color: #eae5dc;
    border-radius: 4px;
    color: #4a473f;
}

/* 遮蔽清單 (Journal Card Items) */
QListWidget {
    background-color: #faf8f5;
    border: 1px solid #e5e0d8;
    border-radius: 8px;
    padding: 6px;
    outline: none;
}
QListWidget::item {
    background-color: #ffffff;
    border: 1px solid #e8e3da;
    border-radius: 6px;
    margin: 4px 2px;
    padding: 8px 12px;
    color: #45433e;
}
QListWidget::item:hover {
    background-color: #f3efe6;
    border-color: #d8d2c5;
}
QListWidget::item:selected {
    background-color: #ebf2ea;
    border: 1.5px solid #5f8768;
    color: #3b5941;
    font-weight: bold;
}

/* 下拉選單與數值調整框 */
QComboBox, QSpinBox {
    background-color: #ffffff;
    border: 1px solid #dcd6cb;
    border-radius: 6px;
    padding: 5px 10px;
    color: #3d3b36;
}
QComboBox:hover, QSpinBox:hover {
    border-color: #b5ada0;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    border: 1px solid #dcd6cb;
    selection-background-color: #ebf2ea;
    selection-color: #3b5941;
}

/* 訊息與進度條 */
QProgressBar {
    background-color: #ece8df;
    border: 1px solid #d8d2c6;
    border-radius: 6px;
    text-align: center;
    color: #383734;
    font-weight: bold;
}
QProgressBar::chunk {
    background-color: #6b8f71;
    border-radius: 5px;
}
"""
