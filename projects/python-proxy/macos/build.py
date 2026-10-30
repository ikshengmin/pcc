#!/usr/bin/env python3
"""Build the python-proxy macOS app and its bundled transparent proxy provider."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
NETWORK_ENTITLEMENT = 'com.apple.developer.networking.networkextension'


def run(*args):
    subprocess.run(list(map(str, args)), check=True, timeout=180)


def plist(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(content))


def check_profile(path, identifier, team):
    data = subprocess.check_output(['/usr/bin/security', 'cms', '-D', '-i', str(path)], timeout=30)
    profile = plistlib.loads(data)
    entitlements = profile.get('Entitlements', {})
    app_id = entitlements.get('com.apple.application-identifier', '')
    if app_id != team + '.' + identifier:
        raise ValueError('Provisioning profile does not match %s / %s' % (team, identifier))
    if 'app-proxy-provider-systemextension' not in entitlements.get(NETWORK_ENTITLEMENT, []):
        raise ValueError('Provisioning profile is missing the system-extension app-proxy entitlement')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path, help='new output directory (never overwritten)')
    parser.add_argument('--bundle-id', default='io.pproxy.process')
    parser.add_argument('--arch', choices=('arm64', 'x86_64', 'universal'), default='universal')
    parser.add_argument('--unsigned', action='store_true', help='build/test only; cannot activate the system extension')
    parser.add_argument('--identity', help='Developer ID Application signing identity')
    parser.add_argument('--team-id')
    parser.add_argument('--app-profile', type=Path)
    parser.add_argument('--extension-profile', type=Path)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('the macOS package must be built on macOS with Xcode')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)+', args.bundle_id):
        parser.error('invalid bundle identifier')
    if args.out.exists():
        parser.error('output directory already exists; choose a new path')
    signing = [args.identity, args.team_id, args.app_profile, args.extension_profile]
    if args.unsigned and any(signing):
        parser.error('--unsigned cannot be combined with signing options')
    if not args.unsigned and not all(signing):
        parser.error('provide --identity, --team-id, --app-profile and --extension-profile, or use --unsigned for tests')
    extension_id = args.bundle_id + '.extension'
    if not args.unsigned:
        check_profile(args.app_profile, args.bundle_id, args.team_id)
        check_profile(args.extension_profile, extension_id, args.team_id)
    args.out.mkdir(parents=True)
    app = args.out / 'PythonProxy.app'
    extension = app / ('Contents/Library/SystemExtensions/%s.systemextension' % extension_id)
    for bundle in (app, extension):
        (bundle / 'Contents/MacOS').mkdir(parents=True)
    common = dict(CFBundleShortVersionString='1.0', CFBundleVersion='1', LSMinimumSystemVersion='11.0')
    plist(app / 'Contents/Info.plist', dict(common, CFBundleName='PythonProxy', CFBundleDisplayName='Python Proxy',
          CFBundleIdentifier=args.bundle_id, CFBundleExecutable='PythonProxy', CFBundlePackageType='APPL', LSUIElement=True,
          NSSystemExtensionUsageDescription='Route selected applications through your python-proxy listener.'))
    plist(extension / 'Contents/Info.plist', dict(common, CFBundleName='Python Proxy Process Routing',
          CFBundleIdentifier=extension_id, CFBundleExecutable='PProxyExtension', CFBundlePackageType='SYSX',
          NetworkExtension={'NEProviderClasses': {'com.apple.networkextension.app-proxy': 'PProxyExtension.AppProxyProvider'}}))
    app_entitlements = {NETWORK_ENTITLEMENT: ['app-proxy-provider-systemextension'],
                        'com.apple.developer.system-extension.install': True}
    extension_entitlements = {NETWORK_ENTITLEMENT: ['app-proxy-provider-systemextension'],
                              'com.apple.security.app-sandbox': True,
                              'com.apple.security.network.client': True,
                              'com.apple.security.network.server': True}
    if not args.unsigned:
        for identifier, entitlements in [(args.bundle_id, app_entitlements), (extension_id, extension_entitlements)]:
            entitlements['com.apple.application-identifier'] = args.team_id + '.' + identifier
            entitlements['com.apple.developer.team-identifier'] = args.team_id
        shutil.copyfile(args.app_profile, app / 'Contents/embedded.provisionprofile')
        shutil.copyfile(args.extension_profile, extension / 'Contents/embedded.provisionprofile')
    shared = sorted((ROOT / 'macos/Shared').glob('*.swift'))
    extension_sources = shared + [ROOT / 'macos/Extension' / f for f in ('AppProxyProvider.swift', 'RoutedUDPFlow.swift', 'main.swift')]
    app_sources = shared + [ROOT / 'macos/Controller/main.swift']
    sdk = subprocess.check_output(['xcrun', '--show-sdk-path'], text=True).strip()
    architectures = ['arm64', 'x86_64'] if args.arch == 'universal' else [args.arch]
    with tempfile.TemporaryDirectory(prefix='pproxy-macos-build-') as temporary:
        scratch = Path(temporary)
        for name, module, sources, target in [
            ('app', 'PProxyController', app_sources, app / 'Contents/MacOS/PythonProxy'),
            ('extension', 'PProxyExtension', extension_sources, extension / 'Contents/MacOS/PProxyExtension'),
        ]:
            binaries = []
            for arch in architectures:
                output = scratch / (name + '-' + arch)
                run('xcrun', 'swiftc', '-swift-version', '5', '-O', '-sdk', sdk,
                    '-target', arch + '-apple-macosx11.0', '-module-name', module,
                    '-module-cache-path', scratch / 'modules', *sources,
                    '-framework', 'NetworkExtension', '-framework', 'SystemExtensions', '-o', output)
                binaries.append(output)
            run('xcrun', 'lipo', '-create', *binaries, '-output', target)
        resources = app / 'Contents/Resources'
        python_root = resources / 'python-proxy'
        python_root.mkdir(parents=True)
        shutil.copytree(ROOT / 'pproxy', python_root / 'pproxy', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copyfile(ROOT / 'LICENSE', python_root / 'LICENSE')
        shutil.copyfile(ROOT / 'third_party/ProxyBridge.LICENSE', resources / 'ProxyBridge.LICENSE')
        shutil.copyfile(ROOT / 'macos/README.md', resources / 'README.md')
        (python_root / 'launch.py').write_text('from pproxy.server import main\nmain()\n')
        shutil.copytree(ROOT / 'macos/examples', resources / 'examples')
        sources = extension_sources + app_sources + sorted((ROOT / 'pproxy').glob('*.py'))
        manifest = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        (resources / 'build.json').write_text(json.dumps(dict(signed=not args.unsigned, bundle_id=args.bundle_id,
                                                            architectures=architectures, sources=manifest), indent=2) + '\n')
        identity = args.identity if not args.unsigned else '-'
        for name, bundle, entitlements in [('extension', extension, extension_entitlements), ('app', app, app_entitlements)]:
            entitlements_file = scratch / (name + '.entitlements')
            # Restricted NE entitlements on an ad-hoc signature cause AMFI to
            # terminate the executable even for --help/check-config. Development
            # bundles can run those checks but must never activate a provider.
            plist(entitlements_file, {} if args.unsigned else entitlements)
            command = ['/usr/bin/codesign', '--force', '--sign', identity, '--entitlements', entitlements_file]
            if not args.unsigned:
                command.extend(['--options', 'runtime', '--timestamp'])
            run(*command, bundle)
            run('/usr/bin/codesign', '--verify', '--strict', bundle)
    launcher = args.out / 'pproxy'
    launcher.write_text('#!/bin/sh\nset -eu\n'
                        'root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                        'app=${PPROXY_MACOS_APP:-"$root/PythonProxy.app"}\n'
                        'export PPROXY_MACOS_APP="$app"\n'
                        'exec "${PPROXY_PYTHON:-python3}" "$app/Contents/Resources/python-proxy/launch.py" "$@"\n')
    launcher.chmod(0o755)
    archive = args.out / 'python-proxy-macos.zip'
    # ditto preserves executable permissions and the signed bundle layout.
    with tempfile.TemporaryDirectory(prefix='pproxy-dist-') as temporary:
        staging = Path(temporary) / 'python-proxy-macos'
        staging.mkdir()
        shutil.copytree(app, staging / app.name)
        shutil.copyfile(launcher, staging / 'pproxy')
        (staging / 'pproxy').chmod(0o755)
        run('/usr/bin/ditto', '-c', '-k', '--keepParent', staging, archive)
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise RuntimeError('archive verification failed')
    print(json.dumps(dict(app=str(app), archive=str(archive), signed=not args.unsigned,
                          sha256=hashlib.sha256(archive.read_bytes()).hexdigest())))


if __name__ == '__main__':
    main()
