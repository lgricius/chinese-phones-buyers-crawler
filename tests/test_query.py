import unittest

from phones import query as q

OFFERS = [{"model_key": k} for k in ("xiaomi 18 pro", "xiaomi 18 pro max", "oppo find x10 pro max", "oppo find n6")]


class ResolveModelsTest(unittest.TestCase):
    def test_comma_separated_models(self):
        keys, _ = q.resolve_models("xiaomi 18 pro, oppo find n6", OFFERS)
        self.assertEqual(keys, ["xiaomi 18 pro", "oppo find n6"])  # exact match per part
        keys, _ = q.resolve_models("xiaomi 18, find n6,", OFFERS)
        self.assertEqual(keys, ["xiaomi 18 pro", "xiaomi 18 pro max", "oppo find n6"])

    def test_unmatched_part(self):
        keys, _ = q.resolve_models("oppo find n6, nokia 9999", OFFERS)
        self.assertEqual(keys, ["oppo find n6"])
        self.assertEqual(q.unmatched_parts("oppo find n6, nokia 9999", OFFERS), ["nokia 9999"])


if __name__ == "__main__":
    unittest.main()
