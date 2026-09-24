# Third-party software

Simple Clipboard is GPL-3.0-only. Its complete application source and build scripts are at https://github.com/asoming/simple-clipboard and in the matching source artifact of each build.

The Windows and macOS previews use unmodified PyPI wheels with dynamically loaded Qt libraries. They include PyQt5 (GPL v3), PyQt5-sip (BSD/GPL options), Qt (LGPL v3), Python (PSF), and the PyInstaller bootloader (GPL with its distribution exception). macOS also includes PyObjC (MIT). These are separate from the operating system's libraries. Third-party copyright notices and licenses are retained in the source archives; the build source manifest identifies exact versions and download hashes.

Download the corresponding platform `sources.zip` asset alongside the installer from the same [GitHub Release](https://github.com/asoming/simple-clipboard/releases/tag/v0.5.0-preview.1). It includes the application, Python/PyPI source archives, and the complete source archive for the bundled Qt release (including its third-party code and license texts). Build steps are in `.github/workflows/tests.yml`, `requirements-build.txt` and `packaging/`. The source manifest records the exact binary build commit; later release documentation changes do not alter that source bundle. No Qt library was modified or statically linked by this project. Replacing a compatible dynamic library or rebuilding is permitted; a rebuilt macOS bundle needs its own ad-hoc or Developer ID signature.

Ubuntu packages include application Python source only and use distribution-provided dependencies. Their source and license notices are provided by Ubuntu's corresponding packages.

These are unsigned/ad-hoc-signed engineering previews, not verified production releases. Windows currently bundles Qt 5.15.2 because it is the available Windows PyQt5-Qt5 wheel; macOS uses Qt 5.15.19. Updating the Windows Qt runtime is a release-readiness item. See `docs/第三阶段验收报告.md` for supported test environments and outstanding acceptance checks.

References:

- https://riverbankcomputing.com/software/pyqt/intro
- https://www.qt.io/licensing/open-source-lgpl-obligations
- https://pyinstaller.org/en/stable/license.html
- https://docs.python.org/3/license.html
- https://pyobjc.readthedocs.io/en/latest/license.html
