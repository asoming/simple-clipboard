"""Transfer validated CI artifacts to an empty draft; publishing stays explicit."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def api(path):
    return json.loads(command('gh', 'api', path))


def sha256(stream):
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    tag = os.environ['RELEASE_TAG']
    run_id = os.environ['BUILD_RUN_ID']
    repository = os.environ['GITHUB_REPOSITORY']
    require(re.fullmatch(r'v0\.7\.0-preview\.\d+', tag), 'Expected a 0.7.0 preview tag')
    require(run_id.isdecimal(), 'Invalid run ID')
    base = f'repos/{repository}'
    # The tag endpoint only returns published releases, so find the draft in the list.
    releases = json.loads(command('gh', 'api', '--paginate', '--slurp', f'{base}/releases?per_page=100'))
    matches = [release for page in releases for release in page if release['tag_name'] == tag]
    require(len(matches) == 1, 'Expected exactly one matching draft release')
    release = matches[0]
    require(release['draft'] and release['prerelease'], 'Release must be a draft prerelease')
    require(not release['assets'], 'Draft already has assets; inspect before retrying')
    run = api(f'{base}/actions/runs/{run_id}')
    require(run['conclusion'] == 'success' and run['event'] == 'push', 'Expected a successful push build')
    require(run['path'] == '.github/workflows/tests.yml', 'Unexpected build workflow')
    require(run['head_repository']['full_name'] == repository, 'Unexpected source repository')
    build_commit = run['head_sha']
    target = release['target_commitish']
    require(re.fullmatch(r'[0-9a-f]{40}', target), 'Draft must target an exact commit')
    subprocess.run(['git', 'merge-base', '--is-ancestor', build_commit, target], check=True)
    changed = command('git', 'diff', '--name-only', '-z', build_commit, target).split('\0')
    publication_files = {
        'AGENTS.md', 'README.md', 'packaging/THIRD-PARTY.md',
        '.github/workflows/release-assets.yml', 'packaging/prepare_release.py',
    }
    require(all(name in publication_files or name.startswith(('docs/', 'verification/'))
                for name in changed if name), 'Application or build inputs changed since the tested commit')

    installers = {
        'ubuntu-22.04-x11': 'simple-clipboard_0.7.0-preview1_all.deb',
        'windows-x64': 'SimpleClipboard-0.7.0-preview-windows-x64-setup.exe',
        'macos-arm64': 'SimpleClipboard-0.7.0-preview-macos-arm64.dmg',
        'macos-intel': 'SimpleClipboard-0.7.0-preview-macos-intel.dmg',
    }
    sources = {
        'windows-x64': 'SimpleClipboard-0.7.0-preview-windows-sources.zip',
        'macos-arm64': 'SimpleClipboard-0.7.0-preview-macos-arm64-sources.zip',
        'macos-intel': 'SimpleClipboard-0.7.0-preview-macos-intel-sources.zip',
    }
    artifacts = api(f'{base}/actions/runs/{run_id}/artifacts?per_page=100')['artifacts']
    artifacts = {item['name']: item for item in artifacts}
    expected = {label + '-preview' for label in installers} | {label + '-sources' for label in sources}
    require(expected == artifacts.keys(), 'Missing or unexpected build artifacts')
    summary = json.loads(Path('verification/ui-ci-summary.json').read_text())
    require(summary['commit'] == build_commit, 'Validation summary refers to a different build')
    summary.update(release_tag=tag, release_commit=target, reports={})
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        assets = work / 'assets'
        assets.mkdir()
        for name, artifact in artifacts.items():
            require(not artifact['expired'], 'Build artifact expired')
            archive = work / (name + '.zip')
            with archive.open('wb') as output:
                subprocess.run(['gh', 'api', f"{base}/actions/artifacts/{artifact['id']}/zip"],
                               stdout=output, check=True)
            require(archive.stat().st_size == artifact['size_in_bytes'], 'Artifact size mismatch')
            with zipfile.ZipFile(archive) as bundle:
                require(bundle.testzip() is None, 'Corrupt artifact archive')
                names = bundle.namelist()
                require(len(names) == len(set(names)), 'Duplicate archive paths')
                if name.endswith('-sources'):
                    label = name.removesuffix('-sources')
                    manifest = json.loads(bundle.read('manifest.json'))
                    require(manifest['commit'] == build_commit, 'Source commit mismatch')
                    expected_sources = {'manifest.json', 'simple-clipboard-source.tar.gz'}
                    expected_sources.update(item['file'] for item in manifest['archives'])
                    require(set(names) == expected_sources, 'Unexpected source archive contents')
                    for item in manifest['archives']:
                        with bundle.open(item['file']) as source:
                            require(sha256(source) == item['sha256'], 'Upstream source hash mismatch')
                    archive.rename(assets / sources[label])
                else:
                    label = name.removesuffix('-preview')
                    require(installers[label] in names, 'Installer missing')
                    for filename in names:
                        require(Path(filename).name == filename, 'Unexpected nested preview file')
                        if filename == installers[label]:
                            (assets / filename).write_bytes(bundle.read(filename))
                        else:
                            require(filename.endswith('.json'), 'Unexpected preview contents')
                            report = json.loads(bundle.read(filename))
                            summary['reports'][name + '/' + filename] = report
                            if 'smoke' in filename:
                                require(report['ok'], 'Packaged startup report failed')
        (assets / 'verification.json').write_text(json.dumps(summary, indent=2) + '\n')
        checksums = []
        for path in sorted(assets.iterdir()):
            with path.open('rb') as source:
                checksums.append(f'{sha256(source)}  {path.name}')
        (assets / 'SHA256SUMS').write_text('\n'.join(checksums) + '\n')
        current = api(f"{base}/releases/{release['id']}")
        require(current['draft'] and current['prerelease'] and not current['assets']
                and current['target_commitish'] == target, 'Draft changed during transfer')
        subprocess.run(['gh', 'release', 'upload', tag, *map(str, sorted(assets.iterdir()))], check=True)
        uploaded = api(f"{base}/releases/{release['id']}")['assets']
        require({item['name'] for item in uploaded} == {path.name for path in assets.iterdir()},
                'Release assets incomplete')
        for item in uploaded:
            path = assets / item['name']
            with path.open('rb') as source:
                require(item['size'] == path.stat().st_size
                        and item.get('digest') == 'sha256:' + sha256(source), 'Uploaded hash mismatch')
        print('All nine assets verified. Release remains a draft for final publication.')


if __name__ == '__main__':
    main()
