# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['app_debug.py'],
    pathex=['C:\\Users\\Riccardo\\Desktop\\my_repos\\Maroccos\\headless-player'],
    binaries=[],
    datas=[('C:\\Users\\Riccardo\\Desktop\\my_repos\\Maroccos\\headless-player\\VERSION', '.'), ('C:\\Users\\Riccardo\\Desktop\\my_repos\\Maroccos\\headless-player\\media', 'media')],
    hiddenimports=['uvicorn'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='headless-player-debug',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='headless-player-debug',
)
