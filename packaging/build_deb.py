"""Build a source-only Ubuntu package without reading runtime directories."""

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'dist'


def main():
    OUTPUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory)
        app = stage / 'usr/share/simple-clipboard'
        app.mkdir(parents=True)
        for source in (ROOT / 'clipboard_app').glob('*.py'):
            destination = app / 'clipboard_app' / source.name
            destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, destination)
        shutil.copyfile(ROOT / 'run_app.py', app / 'run_app.py')
        shutil.copyfile(ROOT / 'LICENSE', app / 'LICENSE')
        launcher = stage / 'usr/bin/simple-clipboard'
        launcher.parent.mkdir(parents=True)
        launcher.write_text('#!/bin/sh\nexport QT_QPA_PLATFORM=xcb\nexport QT_XCB_GL_INTEGRATION=none\nexec /usr/bin/python3 /usr/share/simple-clipboard/run_app.py "$@"\n')
        launcher.chmod(0o755)
        desktop = stage / 'usr/share/applications/simple-clipboard.desktop'
        desktop.parent.mkdir(parents=True)
        desktop.write_text('[Desktop Entry]\nType=Application\nName=剪贴板\nName[en]=Simple Clipboard\nExec=simple-clipboard\nIcon=edit-paste\nTerminal=false\nCategories=Utility;\n', encoding='utf-8')
        control = stage / 'DEBIAN/control'
        control.parent.mkdir()
        control.write_text('Package: simple-clipboard\nVersion: 0.3.1~preview1\nSection: utils\nPriority: optional\nArchitecture: all\nMaintainer: asoming <185788094+asoming@users.noreply.github.com>\nDepends: python3 (>= 3.10), python3-pyqt5, python3-gi, libx11-6, libxtst6\nHomepage: https://github.com/asoming/simple-clipboard\nDescription: Local clipboard history for Ubuntu X11\n Text, HTML and images with local search and optional global paste.\n')
        subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(stage), str(OUTPUT / 'simple-clipboard_0.3.1-preview1_all.deb')], check=True)


if __name__ == '__main__':
    main()
