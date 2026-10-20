import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('archive_external', Path(__file__).resolve().parents[1] / 'scripts/archive_external_development.py')
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)

class ArchiveBoundaryTests(unittest.TestCase):
    def test_archive_preserves_bytes_and_never_includes_auth_or_native_homes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve(); source=root/'source'; source.mkdir()
            (source/'code.py').write_bytes(b'print("fixture")\r\n')
            (source/'auth.json').write_text('secret-fixture',encoding='utf-8')
            home=source/'home-A'; home.mkdir(); (home/'session.jsonl').write_text('private-fixture',encoding='utf-8')
            result=archive.archive(source,root/'sealed')
            self.assertEqual(result['files'],1)
            with zipfile.ZipFile(result['archive']) as z:
                self.assertEqual(z.read('code.py'),(source/'code.py').read_bytes())
                self.assertNotIn('auth.json',z.namelist())
                self.assertNotIn('home-A/session.jsonl',z.namelist())
            archive.verify(root/'sealed/archive-manifest.json')
            self.assertTrue((source/'auth.json').exists())

    def test_changed_source_refuses_cleanup_verification(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve(); source=root/'source'; source.mkdir()
            (source/'file').write_text('before',encoding='utf-8')
            archive.archive(source,root/'sealed')
            (source/'file').write_text('after',encoding='utf-8')
            with self.assertRaises(ValueError): archive.verify(root/'sealed/archive-manifest.json')

    def test_archive_can_be_verified_after_source_cleanup_without_authorizing_delete(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve(); source=root/'source'; source.mkdir()
            (source/'file').write_text('original',encoding='utf-8')
            archive.archive(source,root/'sealed')
            (source/'file').unlink(); source.rmdir()
            report=archive.verify(root/'sealed/archive-manifest.json',verify_source=False)
            self.assertFalse(report['source_verified'])
            with self.assertRaises(FileNotFoundError): archive.verify(root/'sealed/archive-manifest.json')

    def test_manifest_tampering_and_self_archive_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve(); source=root/'source'; source.mkdir()
            with self.assertRaises(ValueError): archive.archive(source,source/'sealed')
            archive.archive(source,root/'sealed')
            path=root/'sealed/archive-manifest.json'
            report=json.loads(path.read_text(encoding='utf-8')); report['files']=[{'path':'../escape','sha256':'x'}]
            path.write_text(json.dumps(report),encoding='utf-8')
            with self.assertRaises(ValueError): archive.verify(path)

if __name__=='__main__': unittest.main()
