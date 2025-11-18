# pyinstaller.spec — empaqueta el COMPILADOR (GUI PySide6)
# - Windowed (sin consola del compilador)
# - Incluye builder_core.py como módulo y el icono si existe
# - El compilador seguirá requiriendo un Python del sistema para crear venvs de los proyectos

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hidden = []
# Algunas distros de Qt requieren submódulos; si hubiera problemas, descomenta:
# hidden += collect_submodules("PySide6")

a = Analysis(
    ['main.py', 'builder_core.py'],
    pathex=[],
    binaries=[],
    datas=[('icon.ico', '.') ] if os.path.exists('icon.ico') else [],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

icon_path = 'icon.ico' if os.path.exists('icon.ico') else None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='compiler-app',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,   # GUI app
    icon=icon_path
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='compiler-app'
)
