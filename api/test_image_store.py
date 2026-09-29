import unittest

import cv2
import numpy as np
from PIL import Image

from src.services.image_store import _autocrop_and_correct


class CardCropTests(unittest.TestCase):
    def test_colored_card_is_cropped_with_its_dark_band(self):
        canvas = np.full((900, 1400, 3), (155, 152, 150), dtype=np.uint8)
        card = np.array([[185, 125], [1225, 130], [1290, 765], [145, 770]], dtype=np.int32)
        cv2.fillConvexPoly(canvas, card, (230, 229, 228))
        band = np.array([[155, 545], [1265, 542], [1285, 765], [145, 770]], dtype=np.int32)
        cv2.fillConvexPoly(canvas, band, (70, 105, 150))

        cropped = _autocrop_and_correct(Image.fromarray(canvas))

        self.assertLess(cropped.width, 1250)
        self.assertLess(cropped.height, 750)
        self.assertGreater(cropped.width / cropped.height, 1.5)


if __name__ == "__main__":
    unittest.main()
