import unittest

import cv2
import numpy as np
from PIL import Image

from src.services.image_store import _autocrop_and_correct, _enhance_processed_image, _perspective_warp


class CardCropTests(unittest.TestCase):
    def test_internal_certification_frame_does_not_replace_a_scanned_card(self):
        for scale in (.75, 1, 2):
            canvas = np.full((500, 890, 3), 255, dtype=np.uint8)
            cv2.putText(canvas, 'COMPANY NAME', (35, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
            cv2.putText(canvas, 'SAMPLE PERSON', (35, 195), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 0), 3)
            for index, text in enumerate(('ADDRESS 1-2-3', 'TEL 045-123-4567', 'MAIL example@test.jp')):
                cv2.putText(canvas, text, (35, 300 + index * 45), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 0, 0), 2)
            cv2.rectangle(canvas, (405, 325), (645, 465), (0, 0, 0), 3)
            cv2.putText(canvas, 'CERTIFIED', (420, 385), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 0, 0), 2)
            canvas = cv2.resize(canvas, None, fx=scale, fy=scale)
            source = Image.fromarray(canvas)
            with self.subTest(scale=scale):
                cropped = _autocrop_and_correct(source)
                self.assertEqual(cropped.size, source.size)
                self.assertTrue(np.array_equal(np.array(cropped), canvas))

    def test_crop_rejects_large_internal_graphics_and_works_in_both_polarities(self):
        for background, ink in ((255, 0), (0, 255)):
            canvas = np.full((600, 1000, 3), background, dtype=np.uint8)
            cv2.rectangle(canvas, (400, 120), (960, 470), (ink,) * 3, 3)
            cv2.putText(canvas, 'NAME', (30, 220), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (ink,) * 3, 3)
            quad = np.array([[400, 120], [960, 120], [960, 470], [400, 470]], dtype=np.float32)
            with self.subTest(background=background):
                self.assertIsNone(_perspective_warp(canvas, quad))

    def test_real_outer_boundary_keeps_all_text_and_is_still_corrected(self):
        canvas = np.full((800, 1200, 3), 110, dtype=np.uint8)
        quad = np.array([[150, 100], [1050, 120], [1080, 680], [120, 660]], dtype=np.int32)
        cv2.fillConvexPoly(canvas, quad, (245, 245, 245))
        cv2.putText(canvas, 'NAME', (230, 270), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
        cv2.putText(canvas, 'TEL 045-123-4567', (230, 380), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        cropped = _autocrop_and_correct(Image.fromarray(canvas))
        self.assertLess(cropped.width, canvas.shape[1])
        self.assertLess(cropped.height, canvas.shape[0])
        self.assertAlmostEqual(cropped.width / cropped.height, 91 / 55, places=2)

    def test_small_card_on_a_plain_backdrop_can_still_be_cropped(self):
        canvas = np.full((1000, 1400, 3), 100, dtype=np.uint8)
        quad = np.array([[450, 350], [950, 350], [950, 650], [450, 650]], dtype=np.int32)
        cv2.fillConvexPoly(canvas, quad, (245, 245, 245))
        cv2.putText(canvas, 'NAME', (520, 475), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        cropped = _autocrop_and_correct(Image.fromarray(canvas))
        self.assertLess(cropped.width, 600)
        self.assertLess(cropped.height, 400)

    def test_keyboard_labels_outside_a_light_card_do_not_prevent_cropping(self):
        canvas = np.full((1200, 900, 3), 150, dtype=np.uint8)
        cv2.rectangle(canvas, (0, 0), (899, 250), (20, 20, 20), -1)
        cv2.putText(canvas, 'CTRL  ALT  SHIFT', (80, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (240, 240, 240), 3)
        quad = np.array([[200, 650], [700, 650], [700, 950], [200, 950]], dtype=np.int32)
        cv2.fillConvexPoly(canvas, quad, (245, 245, 245))
        cv2.putText(canvas, 'NAME', (270, 775), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        cropped = _autocrop_and_correct(Image.fromarray(canvas))
        self.assertLess(cropped.width, 600)
        self.assertLess(cropped.height, 400)

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

    def test_underexposed_neutral_paper_is_brightened(self):
        image = Image.new("RGB", (400, 240), (180, 180, 185))
        image.paste((35, 35, 40), (30, 90, 370, 110))

        corrected = _enhance_processed_image(image)

        self.assertGreater(corrected.getpixel((200, 40))[0], 215)
        self.assertLess(corrected.getpixel((200, 100))[0], 70)

    def test_already_bright_paper_is_not_brightened(self):
        image = Image.new("RGB", (400, 240), (228, 228, 230))

        corrected = _enhance_processed_image(image)

        self.assertLessEqual(corrected.getpixel((200, 40))[0], 235)


if __name__ == "__main__":
    unittest.main()
