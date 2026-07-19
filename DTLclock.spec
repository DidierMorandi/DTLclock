# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['DTLclock.py'],
    pathex=[],
    binaries=[],
    datas=[('DTLclock.ini', '.'),('horloge.png', '.'), ('balancier.png', '.'), ('bell1700.wav', '.'), ('bell1800.wav', '.'), ('tick.wav', '.'), ('tock.wav', '.')],
    hiddenimports=[],
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
    a.binaries,
    a.datas,
    [],
    name='DTLclock',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='DTLclock.version.txt',
)
