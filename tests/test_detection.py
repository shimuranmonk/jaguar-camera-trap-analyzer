import unittest
from jaguar.adapters.megadetector import validated_box


class DetectionTests(unittest.TestCase):
    def test_classes_coordinates_and_threshold(self):
        box = validated_box(1, .9, [10, 20, 60, 80], 100, 100, .2)
        self.assertEqual(box["class"], "person")
        self.assertEqual(box["box"], [.1, .2, .5, .6])
        self.assertIsNone(validated_box(0, .1, [0, 0, 10, 10], 100, 100, .2))

    def test_unknown_class_invalid_score_and_empty_box_are_rejected(self):
        for arguments in [(3, .5, [0, 0, 1, 1]), (0, float('nan'), [0, 0, 1, 1]), (0, .5, [10, 10, 1, 1])]:
            with self.assertRaises(ValueError):
                validated_box(*arguments, 100, 100, .2)
