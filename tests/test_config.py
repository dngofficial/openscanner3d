"""Local configuration validation and imported-photo normalisation."""
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from backend.app.config import load_settings
from backend.app.images import normalize_photo


class ConfigurationTests(unittest.TestCase):
    def test_duplicate_keys_name_the_config_file_and_line(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.local.toml"
            path.write_text('quality = "medium"\nquality = "medium"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"config\.local\.toml: Cannot overwrite a value.*line 2"):
                load_settings(path)

    def test_safe_default_and_invalid_settings(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=True):
            path = Path(root) / "config.toml"
            self.assertEqual(load_settings(path).mode, "capture")
            for content in ('mode = "cloud"', 'max_photos = 0', 'timeout_seconds = "forever"', 'unknown = 3',
                            'lan = "yes"', 'port = 80', 'tls_cert = ""', 'masks = "magic"', 'detail = "ultra"',
                            'segmentation_device = "tpu"', 'sam2_checkpoint = 3'):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    load_settings(path)
            path.write_text('mode = "reconstruct"\ncolmap = \'C:\\Tools\\COLMAP.exe\'')
            self.assertEqual(load_settings(path).colmap, r"C:\Tools\COLMAP.exe")

    def test_imported_photo_is_upright_bounded_and_size_limited(self):
        image = Image.new('RGB', (640, 480))
        exif = image.getexif()
        exif[274] = 6
        data = io.BytesIO()
        image.save(data, format='JPEG', exif=exif)
        normalized, suffix = normalize_photo(data.getvalue())
        self.assertEqual(suffix, '.jpg')
        with Image.open(io.BytesIO(normalized)) as working:
            self.assertEqual(working.size, (480, 640))
        data = io.BytesIO()
        Image.new('RGB', (4000, 3000)).save(data, format='JPEG')
        with Image.open(io.BytesIO(normalize_photo(data.getvalue())[0])) as working:
            self.assertEqual(working.size, (3200, 2400))
        data = io.BytesIO()
        Image.new('RGB', (9000, 32)).save(data, format='JPEG')
        with self.assertRaises(ValueError):
            normalize_photo(data.getvalue())


if __name__ == '__main__':
    unittest.main()
