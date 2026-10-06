"""Select images by comparing their published source with the release commit."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


IMAGES = {
    'api': {'context': 'api', 'dockerfile': 'api/Dockerfile',
            'paths': ['api/Dockerfile', 'api/.dockerignore', 'api/requirements.txt', 'api/src/']},
    'ui': {'context': 'ui', 'dockerfile': 'ui/Dockerfile',
           'paths': ['ui/', ':(exclude)ui/tests/', ':(exclude)ui/node_modules/',
                     ':(exclude)ui/dist/', ':(exclude)ui/.env*']},
    'kana': {'context': 'api', 'dockerfile': 'api/Dockerfile.kana',
             'paths': ['api/Dockerfile.kana', 'api/.dockerignore', 'api/kana/']},
}
REVISION = 'org.opencontainers.image.revision'
VERSION = 'org.opencontainers.image.version'


class ReleaseError(RuntimeError):
    pass


def version_parts(value):
    match = re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?', value)
    if not match or len(value.removeprefix('v')) > 128:
        raise ReleaseError(f'Invalid release version: {value}')
    prerelease = match[4]
    if prerelease and any(part.isdigit() and len(part) > 1 and part.startswith('0') for part in prerelease.split('.')):
        raise ReleaseError(f'Invalid release version: {value}')
    return tuple(int(match[i]) for i in (1, 2, 3)), bool(prerelease)


def inspect_labels(reference):
    result = subprocess.run(
        ['docker', 'buildx', 'imagetools', 'inspect', reference,
         '--format', '{{json .Image.Config.Labels}}'],
        capture_output=True, text=True, timeout=60)
    if result.returncode:
        error = result.stderr.strip()
        if re.search(r'(manifest unknown|: not found(?:\s|$)|404 Not Found)', error, re.I):
            return None
        raise ReleaseError(f'Cannot read {reference}: {error}')
    try:
        labels = json.loads(result.stdout)
        if labels is not None and not isinstance(labels, dict):
            raise ValueError('labels must be an object')
        return labels or {}
    except ValueError as error:
        raise ReleaseError(f'Invalid image metadata for {reference}') from error


def source_changed(directory, base, head, paths):
    if not isinstance(base, str) or not re.fullmatch(r'[0-9a-f]{40,64}', base):
        return True
    exists = subprocess.run(['git', 'cat-file', '-e', base + '^{commit}'],
                            cwd=directory, capture_output=True)
    if exists.returncode:
        return True
    result = subprocess.run(['git', 'diff', '--quiet', base, head, '--', *paths],
                            cwd=directory, capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise ReleaseError(f'Cannot compare source: {result.stderr.strip()}')
    return result.returncode == 1


def plan_release(directory, prefix, tag, force='none', inspect=inspect_labels):
    if not tag.startswith('v'):
        raise ReleaseError('Release tags must start with v')
    core, prerelease = version_parts(tag)
    forced = set(IMAGES) if force == 'all' else set() if force == 'none' else set(force.split(','))
    if not forced.issubset(IMAGES):
        raise ReleaseError('Unknown image in force_images')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=directory, text=True).strip()
    selected, decisions = [], []
    for service, config in IMAGES.items():
        image = f'{prefix}/bzcard-{service}'
        labels = inspect(image + ':latest')
        previous = (labels or {}).get(VERSION, 'unpublished')
        base = (labels or {}).get(REVISION)
        changed = labels is None or source_changed(directory, base, head, config['paths'])
        publish = changed or service in forced
        reason = 'source changed' if changed else 'forced rebuild' if publish else 'unchanged'
        if publish:
            if previous != 'unpublished':
                previous_core, _ = version_parts(previous)
                if core < previous_core or (prerelease and core <= previous_core):
                    raise ReleaseError(f'{service}: {tag} would precede published {previous}')
            target = inspect(image + ':' + tag[1:])
            if target is not None:
                if target.get(REVISION) != head:
                    raise ReleaseError(f'{service}: {tag} is already used by another commit')
                # Stable retries can finish a partially pushed set of tags.
                if prerelease or previous == tag[1:]:
                    publish, reason = False, 'already published'
        if publish:
            selected.append({'service': service, 'context': config['context'], 'dockerfile': config['dockerfile']})
        decisions.append({'service': service, 'previous': previous,
                          'release': tag[1:] if publish else previous, 'publish': publish, 'reason': reason})
    return {'include': selected}, decisions


def write_report(matrix, decisions, output=None, summary=None):
    if output:
        with Path(output).open('a') as stream:
            stream.write('has_changes=' + ('true' if matrix['include'] else 'false') + '\n')
            stream.write('matrix=' + json.dumps(matrix, separators=(',', ':')) + '\n')
    report = '| Image | Published | After release | Action |\n| --- | --- | --- | --- |\n'
    for decision in decisions:
        report += f"| {decision['service']} | {decision['previous']} | {decision['release']} | {decision['reason']} |\n"
    print(report)
    if summary:
        with Path(summary).open('a') as stream:
            stream.write('## Image release plan\n\n' + report)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tag', required=True)
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--force', default='none')
    args = parser.parse_args()
    try:
        matrix, decisions = plan_release(Path.cwd(), args.prefix, args.tag, args.force)
        write_report(matrix, decisions, os.environ.get('GITHUB_OUTPUT'), os.environ.get('GITHUB_STEP_SUMMARY'))
    except (ReleaseError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Image release selection failed: {error}\n')


if __name__ == '__main__':
    main()
