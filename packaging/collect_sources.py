"""Collect matching upstream source archives, not private working-tree files."""

import hashlib
import importlib.metadata as metadata
import json
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def download(url, directory, expected=None):
    destination = directory / url.rsplit('/', 1)[-1]
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=300) as response, destination.open('wb') as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
    if expected and digest.hexdigest() != expected:
        raise ValueError('Source archive hash mismatch: ' + destination.name)
    return {'file': destination.name, 'url': url, 'sha256': digest.hexdigest()}


def main():
    directory = ROOT / 'sources'
    directory.mkdir(exist_ok=True)
    manifest = {'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(), 'archives': []}
    subprocess.run(['git', 'archive', '--format=tar.gz', '-o', str(directory / 'simple-clipboard-source.tar.gz'), 'HEAD'], check=True)
    names = ['PyQt5', 'PyQt5-sip', 'PyInstaller', 'pyinstaller-hooks-contrib']
    if sys.platform == 'darwin':
        names += ['pyobjc-core', 'pyobjc-framework-Cocoa', 'pyobjc-framework-Quartz']
    notices = ROOT / 'build/notices'
    notices.mkdir(parents=True, exist_ok=True)
    for name in names:
        distribution = metadata.distribution(name)
        version = distribution.version
        with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=60) as response:
            release = json.load(response)
        source = next(file for file in release['urls'] if file['packagetype'] == 'sdist')
        manifest['archives'].append(download(source['url'], directory, source['digests']['sha256']))
        for file in distribution.files or []:
            if any(word in file.name.lower() for word in ('license', 'copying', 'copyright')) and distribution.locate_file(file).is_file():
                destination = notices / name / str(file)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(distribution.locate_file(file), destination)
    version = platform.python_version()
    manifest['archives'].append(download(f'https://www.python.org/ftp/python/{version}/Python-{version}.tar.xz', directory))
    version = metadata.version('PyQt5-Qt5')
    filename = ('qt-everywhere-src-' if version == '5.15.2' else 'qt-everywhere-opensource-src-') + version + '.tar.xz'
    manifest['archives'].append(download(f'https://download.qt.io/archive/qt/5.15/{version}/single/{filename}', directory))
    manifest['qt'] = version
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    shutil.copyfile(directory / 'manifest.json', notices / 'source-manifest.json')


if __name__ == '__main__':
    main()
