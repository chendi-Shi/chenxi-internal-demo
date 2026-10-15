"""Resume and integrity behavior without any remote access."""
import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'benchmarks'))
from public_download import download_verified  # noqa: E402


class Response(io.BytesIO):
    def __init__(self, value, start, end, total):
        super().__init__(value)
        self.status = 206
        self.headers = {'Content-Range': f'bytes {start}-{end}/{total}'}


class DownloadTests(unittest.TestCase):
    def test_verified_file_is_reused_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'asset'
            path.write_bytes(b'abc')
            with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
                self.assertEqual(download_verified('https://example.invalid/a', path,
                                 hashlib.sha256(b'abc').hexdigest(), 3), path)

    def test_resume_downloads_only_missing_bytes(self):
        value = b'abcdef'
        digest = hashlib.sha256(value).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'asset'
            parts = Path(directory) / 'asset.parts'
            parts.mkdir()
            (parts / 'identity.txt').write_text(f'{digest}\n6\n')
            (parts / '000000.part').write_bytes(b'abc')

            def fetch(request, timeout):
                self.assertEqual(request.headers['Range'], 'bytes=3-5')
                return Response(b'def', 3, 5, 6)

            with patch('urllib.request.urlopen', side_effect=fetch):
                download_verified('https://example.invalid/a', path, digest, 6)
            self.assertEqual(path.read_bytes(), value)
            self.assertFalse(parts.exists())

    def test_corruption_does_not_publish_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'asset'
            with patch('urllib.request.urlopen', side_effect=lambda *a, **k: Response(b'bad', 0, 2, 3)):
                with self.assertRaisesRegex(ValueError, 'sha256_mismatch'):
                    download_verified('https://example.invalid/a', path,
                                      hashlib.sha256(b'yes').hexdigest(), 3)
            self.assertFalse(path.exists())

    def test_wrong_range_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'asset'
            with patch('urllib.request.urlopen', return_value=Response(b'abc', 1, 3, 4)):
                with self.assertRaisesRegex(ValueError, 'exact_range'):
                    download_verified('https://example.invalid/a', path,
                                      hashlib.sha256(b'abc').hexdigest(), 3)


if __name__ == '__main__':
    unittest.main()
