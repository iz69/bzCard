import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_release import IMAGES, REVISION, VERSION, ReleaseError, inspect_labels, plan_release, version_parts, write_report


class ImageReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.git('init', '-q')
        for path in ('api/Dockerfile', 'api/Dockerfile.kana', 'api/.dockerignore', 'api/requirements.txt',
                     'api/src/main.py', 'api/kana/model.py', 'ui/Dockerfile', 'ui/src/main.tsx', 'README.md'):
            self.write(path, 'initial\n')
        self.base = self.commit()
        self.published = {name: {REVISION: self.base, VERSION: '0.9.1'} for name in IMAGES}
        self.targets = {}

    def git(self, *arguments):
        return subprocess.check_output(['git', *arguments], cwd=self.directory, text=True).strip()

    def write(self, path, content='changed\n'):
        target = self.directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'user.name=Release test', '-c', 'user.email=release@example.invalid',
                 'commit', '-qm', 'test changes')
        return self.git('rev-parse', 'HEAD')

    def inspect(self, reference):
        image, tag = reference.rsplit(':', 1)
        service = image.rsplit('bzcard-', 1)[1]
        return self.published[service] if tag == 'latest' else self.targets.get((service, tag))

    def plan(self, tag='v0.9.2', force='none'):
        return plan_release(self.directory, 'ghcr.io/test', tag, force, self.inspect)

    def selected(self, **options):
        matrix, _ = self.plan(**options)
        return [item['service'] for item in matrix['include']]

    def test_this_release_changes_api_and_ui_but_not_kana(self):
        self.write('api/src/main.py')
        self.write('ui/src/main.tsx')
        self.commit()
        self.assertEqual(self.selected(), ['api', 'ui'])
        _, decisions = self.plan()
        self.assertEqual(decisions[-1]['release'], '0.9.1')

    def test_each_image_uses_its_own_last_published_commit(self):
        self.write('api/src/main.py')
        api_release = self.commit()
        self.published['api'] = {REVISION: api_release, VERSION: '0.9.2'}
        self.write('ui/src/main.tsx')
        self.commit()
        self.assertEqual(self.selected(tag='v0.9.3'), ['ui'])

    def test_accuracy_revision_order_and_numeric_version_transitions(self):
        self.write('api/src/main.py')
        self.commit()
        for previous, release in (
                ('0.9.8', '0.9.8A'), ('0.9.8A', '0.9.8B'), ('0.9.8Z', '0.9.8AA'),
                ('0.9.8AZ', '0.9.8BA'), ('0.9.8ZZ', '0.9.8AAA'), ('0.9.8ZZ', '0.9.9'),
                ('0.9.9Z', '0.10.0'), ('0.99.99Z', '1.0.0')):
            with self.subTest(previous=previous, release=release):
                self.published['api'] = {REVISION: self.base, VERSION: previous}
                matrix, decisions = self.plan(tag='v' + release)
                self.assertEqual([item['service'] for item in matrix['include']], ['api'])
                self.assertEqual(decisions[0]['release'], release)
                self.assertEqual(decisions[1]['release'], '0.9.1')

    def test_accuracy_revision_downgrades_and_prereleases_cannot_replace_latest(self):
        self.write('api/src/main.py')
        self.commit()
        for previous, release in (
                ('0.9.8A', '0.9.8'), ('0.9.8B', '0.9.8A'), ('0.9.8AA', '0.9.8Z'),
                ('0.9.8BA', '0.9.8AZ'), ('0.9.9', '0.9.8ZZ'), ('0.10.0', '0.9.8ZZ'),
                ('0.9.8A', '0.9.8A-rc.1'), ('0.9.8B', '0.9.8A-rc.1')):
            with self.subTest(previous=previous, release=release):
                self.published['api'] = {REVISION: self.base, VERSION: previous}
                with self.assertRaises(ReleaseError):
                    self.selected(tag='v' + release)

    def test_failed_release_is_not_used_as_a_baseline(self):
        self.write('api/src/main.py')
        self.commit()
        self.git('tag', 'v0.9.2')  # Publication failed; GHCR still has 0.9.1.
        self.write('README.md')
        self.commit()
        self.assertEqual(self.selected(tag='v0.9.3'), ['api'])

    def test_kana_change_does_not_publish_api(self):
        self.write('api/kana/model.py')
        self.commit()
        self.assertEqual(self.selected(), ['kana'])

    def test_runtime_configuration_and_assets_publish_ui(self):
        self.write('ui/runtime/generate_config.py')
        self.write('ui/public/favicon.png')
        self.commit()
        self.assertEqual(self.selected(), ['ui'])

    def test_docs_tests_and_workflow_changes_do_not_bump_images(self):
        for path in ('README.md', 'api/test_contact_index.py', 'ui/tests/contactPagination.mjs',
                     '.github/workflows/container-images.yml', 'ui/.env.local', 'ui/dist/index.html'):
            self.write(path)
        self.commit()
        self.assertEqual(self.selected(), [])

    def test_dependency_and_shared_ignore_changes_select_the_right_images(self):
        self.write('api/requirements.txt')
        self.commit()
        self.assertEqual(self.selected(), ['api'])
        self.write('api/.dockerignore')
        self.commit()
        self.assertEqual(self.selected(), ['api', 'kana'])

    def test_deletion_and_net_reverts_are_detected(self):
        (self.directory / 'api/src/main.py').unlink()
        self.commit()
        self.assertEqual(self.selected(), ['api'])
        self.write('api/src/main.py', 'initial\n')
        self.commit()
        self.assertEqual(self.selected(), [])

    def test_initial_publication_and_legacy_images_are_rebuilt(self):
        self.published = {service: None for service in IMAGES}
        self.assertEqual(self.selected(), ['api', 'ui', 'kana'])
        self.published = {service: {VERSION: '0.9.1'} for service in IMAGES}
        self.assertEqual(self.selected(), ['api', 'ui', 'kana'])

    def test_forced_rebuild_selects_only_requested_images(self):
        self.assertEqual(self.selected(force='api,ui'), ['api', 'ui'])
        self.assertEqual(self.selected(force='kana'), ['kana'])
        self.assertEqual(self.selected(force='all'), ['api', 'ui', 'kana'])
        with self.assertRaises(ReleaseError):
            self.selected(force='unknown')

    def test_successful_publication_is_skipped_on_retry(self):
        self.write('api/src/main.py')
        current = self.commit()
        for version in ('0.9.2', '0.9.2A'):
            with self.subTest(version=version):
                self.published['api'] = {REVISION: current, VERSION: version}
                self.targets['api', version] = self.published['api']
                self.assertEqual(self.selected(tag='v' + version), [])
                self.assertEqual(self.selected(tag='v' + version, force='api'), [])

    def test_prerelease_retry_and_stable_promotion(self):
        self.write('api/src/main.py')
        current = self.commit()
        for version in ('0.9.2', '0.9.2A'):
            with self.subTest(version=version):
                prerelease = version + '-rc.1'
                self.targets['api', prerelease] = {REVISION: current, VERSION: prerelease}
                self.assertEqual(self.selected(tag='v' + prerelease), [])
                self.assertEqual(self.selected(tag='v' + version), ['api'])

    def test_partial_publication_retries_only_the_incomplete_image(self):
        self.write('api/src/main.py')
        self.write('ui/src/main.tsx')
        current = self.commit()
        for version in ('0.9.2', '0.9.2A'):
            with self.subTest(version=version):
                metadata = {REVISION: current, VERSION: version}
                self.published['api'] = metadata
                self.targets['api', version] = metadata
                self.targets['ui', version] = metadata  # UI version exists, but its latest push failed.
                self.assertEqual(self.selected(tag='v' + version), ['ui'])

    def test_invalid_versions_downgrades_and_reused_tags_fail(self):
        for tag in ('main', 'vnext', 'v01.2.3', 'v0.9.2-rc.01', 'v0.9.2a', 'v0.9.2Aa',
                    'v0.9.2A1', 'v0.9.2.A', 'v0.9.2A-rc.01', 'v0.9.２A', 'v0.9.2+A',
                    'v0.9.2' + 'A' * 124):
            with self.assertRaises(ReleaseError):
                self.selected(tag=tag)
        self.write('api/src/main.py')
        self.commit()
        with self.assertRaises(ReleaseError):
            self.selected(tag='v0.9.0')
        with self.assertRaises(ReleaseError):
            self.selected(tag='v0.9.1-rc.1')
        for version in ('0.9.2', '0.9.2A'):
            with self.subTest(version=version):
                self.targets['api', version] = {REVISION: self.base, VERSION: version}
                with self.assertRaises(ReleaseError):
                    self.selected(tag='v' + version)

    def test_github_outputs_and_summary_include_skipped_versions(self):
        output, summary = self.directory / 'output', self.directory / 'summary'
        matrix, decisions = self.plan()
        with patch('builtins.print'):
            write_report(matrix, decisions, 'v0.9.2', output, summary)
        values = dict(line.split('=', 1) for line in output.read_text().splitlines())
        self.assertEqual(values['has_changes'], 'false')
        self.assertEqual(json.loads(values['matrix']), {'include': []})
        self.assertEqual(values['image_version'], '0.9.2')
        self.assertEqual(values['stable_release'], 'true')
        self.assertIn('| kana | 0.9.1 | 0.9.1 | unchanged |', summary.read_text())

    def test_github_metadata_preserves_accuracy_suffix_and_prerelease_status(self):
        for version, stable in (('0.9.8A', True), ('0.9.8AA', True),
                                ('0.9.8A-rc.1', False), ('0.9.9-rc.1', False)):
            with self.subTest(version=version):
                output = self.directory / 'output'
                output.write_text('')
                matrix, decisions = self.plan(tag='v' + version, force='api')
                with patch('builtins.print'):
                    write_report(matrix, decisions, 'v' + version, output)
                values = dict(line.split('=', 1) for line in output.read_text().splitlines())
                self.assertEqual(values['has_changes'], 'true')
                self.assertEqual(values['image_version'], version)
                self.assertEqual(values['stable_release'], str(stable).lower())
                self.assertEqual(version_parts(version), version_parts('v' + version))

    def test_registry_absence_is_distinguished_from_auth_and_network_errors(self):
        with patch('image_release.subprocess.run') as run:
            run.return_value = subprocess.CompletedProcess([], 1, '', 'ERROR: ghcr.io/test:latest: not found')
            self.assertIsNone(inspect_labels('ghcr.io/test:latest'))
            for error in ('401 Unauthorized', '403 Forbidden', 'connection refused', '503 Service Unavailable'):
                run.return_value = subprocess.CompletedProcess([], 1, '', error)
                with self.assertRaises(ReleaseError):
                    inspect_labels('ghcr.io/test:latest')
            run.return_value = subprocess.CompletedProcess([], 0, json.dumps(self.published['api']), '')
            self.assertEqual(inspect_labels('ghcr.io/test:latest'), self.published['api'])
            run.return_value = subprocess.CompletedProcess([], 0, 'invalid JSON', '')
            with self.assertRaises(ReleaseError):
                inspect_labels('ghcr.io/test:latest')


if __name__ == '__main__':
    unittest.main()
