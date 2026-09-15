# Run from the repository root: pyinstaller --noconfirm packaging/app.spec
import sys
from pathlib import Path

root = Path(SPECPATH).parent
excluded = ['tkinter', 'PyQt5.QtWebEngine', 'PyQt5.QtWebEngineWidgets', 'PyQt5.QtQml', 'PyQt5.QtQuick']
if sys.platform == 'win32':
    excluded += ['clipboard_app.macos', 'clipboard_app.x11', 'clipboard_app.appearance_linux', 'PyQt5.QtDBus']
else:
    excluded += ['clipboard_app.windows', 'clipboard_app.x11', 'clipboard_app.appearance_linux', 'PyQt5.QtDBus']

a = Analysis([str(root / 'run_app.py')], pathex=[str(root)],
             datas=[(str(root / 'LICENSE'), '.'), (str(root / 'packaging/THIRD-PARTY.md'), '.'),
                    (str(root / 'build/notices'), 'notices')],
             hiddenimports=[], excludes=excluded, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='SimpleClipboard',
          debug=False, strip=False, upx=False, console=False, target_arch=None,
          codesign_identity=None, entitlements_file=None)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='SimpleClipboard')
if sys.platform == 'darwin':
    app = BUNDLE(collection, name='SimpleClipboard.app', bundle_identifier='io.github.asoming.simpleclipboard',
                 info_plist={'CFBundleShortVersionString':'0.3.0', 'CFBundleVersion':'0.3.0',
                             'LSMinimumSystemVersion':'14.0', 'LSUIElement':True,
                             'NSHighResolutionCapable':True})
