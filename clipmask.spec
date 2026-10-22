# -*- mode: python ; coding: utf-8 -*-
import os
import sys

block_cipher = None

project_dir = os.path.abspath(SPECPATH)
models_dir = os.path.join(project_dir, 'models')

# 僅從 Python 與 Windows 標準路徑解析 DLL，避免 PATH 中其他工具的
# ICU 等同名 DLL 混入發布包，造成 Qt 載入失敗。
system_root = os.environ['SystemRoot']
os.environ['PATH'] = os.pathsep.join([
    sys.base_prefix,
    os.path.join(sys.base_prefix, 'Scripts'),
    os.path.join(system_root, 'System32'),
    system_root,
])

datas = [
    (models_dir, 'models'),
]

hiddenimports = [
    'av',
    'cv2',
    'numpy',
    'PIL',
    'PySide6',
    'sounddevice',
]

a = Analysis(
    ['run.py'],
    pathex=[project_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'IPython'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ClipMask-AI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ClipMask-AI-Portable',
)
