"""Pocket image format: release art encodes to the sizes APF expects and round-trips."""
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
try:
    import pocket_image
except ImportError:          # Pillow missing
    pocket_image = None


@unittest.skipIf(pocket_image is None, 'Pillow not installed')
class PocketImage(unittest.TestCase):
    def test_release_art_sizes(self):
        banner = pocket_image.encode(ROOT/'support/pocket-art/playstation-banner.png', (521, 165))
        icon = pocket_image.encode(ROOT/'support/pocket-art/core-icon.png', (36, 36))
        self.assertEqual(len(banner), 521*165*2)
        self.assertEqual(len(icon), 36*36*2)
        self.assertFalse(any(banner[1::2]) or any(icon[1::2]))   # high byte unused

    def test_rotation_and_ink(self):
        from PIL import Image
        import tempfile
        image = Image.new('L', (3, 2), 255); image.putpixel((2, 0), 0)    # one black pixel, top right
        with tempfile.NamedTemporaryFile(suffix='.png') as f:
            image.save(f.name); data = pocket_image.encode(f.name, (3, 2))
        self.assertEqual(data[0], 255)    # stored rotated CCW: top-right becomes the first word, full ink


if __name__ == '__main__': unittest.main()
