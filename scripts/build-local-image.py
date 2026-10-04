#!/usr/bin/env python3
"""Build and optionally smoke a local image using the same validated CI matrix."""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def inputs(minor, platform):
    rows = json.loads(subprocess.check_output(
        [str(ROOT / 'scripts/validate-versions.py'), '--matrix', '--minor', minor], cwd=ROOT
    ))['include']
    return next(row for row in rows if row['platform'] == platform)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minor', choices=('8.2', '8.3', '8.4', '8.5'), default='8.5')
    parser.add_argument('--platform', choices=('linux/amd64', 'linux/arm64'), required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--builder', help='Optional existing isolated Buildx builder')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    row = inputs(args.minor, args.platform)
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    created = subprocess.check_output(['git', 'show', '-s', '--format=%cI', 'HEAD'], cwd=ROOT, text=True).strip()
    epoch = subprocess.check_output(['git', 'show', '-s', '--format=%ct', 'HEAD'], cwd=ROOT, text=True).strip()
    values = {'PHP_BASE_IMAGE': row['php_base_image'], 'SOURCE_DATE_EPOCH': epoch,
              'OCI_SOURCE': 'https://github.com/woosungchoi/fpm-alpine',
              'OCI_REVISION': revision, 'OCI_CREATED': created, 'OCI_VERSION': row['php_patch']}
    for name in ('imagick', 'redis', 'apcu'):
        for field in ('url', 'sha256'):
            values[f'{name}_{field}'.upper()] = row[f'{name}_{field}']
    command = ['docker', 'buildx', 'build', '--load', '--platform', args.platform, '--tag', args.tag]
    for key, value in values.items():
        command += ['--build-arg', f'{key}={value}']
    if args.builder:
        command += ['--builder', args.builder]
    command += [str(ROOT)]
    if args.dry_run:
        print(json.dumps({'command': command, 'phpPatch': row['php_patch']}))
        return
    subprocess.run(command, cwd=ROOT, check=True)
    if args.smoke:
        env = os.environ.copy()
        env.update(EXPECTED_PHP_MINOR=args.minor, EXPECTED_PHP_PATCH=row['php_patch'],
                   EXPECTED_PLATFORM=args.platform)
        for name in ('imagick', 'redis', 'apcu'):
            env[f'EXPECTED_{name.upper()}_VERSION'] = row[f'{name}_version']
        for key, value in row.items():
            if key.startswith('iconv_'):
                env['EXPECTED_' + key.upper()] = value
        subprocess.run([str(ROOT / 'scripts/smoke-test-image.sh'), args.tag], cwd=ROOT, env=env, check=True)


if __name__ == '__main__':
    main()
