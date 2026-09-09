import unittest

from rapidtag_text import clean_word, contains_permanent_ban_marker, is_permanently_banned


class PermanentBanMarkerTests(unittest.TestCase):
    def test_blocks_page_variants(self):
        blocked = (
            "page",
            "PAGE7",
            "page 7",
            "pageNN",
            "page NN",
            "208u2\u00a0page\u00a012",
            "208u2 pageNN",
            "208u2page7",
            "-FTA102:XT2:C page7",
        )
        for value in blocked:
            with self.subTest(value=value):
                self.assertTrue(is_permanently_banned(value))
                self.assertEqual(contains_permanent_ban_marker(value), "page")

    def test_blocks_reserve_variants(self):
        for value in ("reserve", "RESERVE", "reserve2", "reserve NN",
                      "208u2 reserve", "208u2reserve", "208u2reserve2"):
            with self.subTest(value=value):
                self.assertTrue(is_permanently_banned(value))
                self.assertEqual(contains_permanent_ban_marker(value), "reserve")

    def test_accepts_normal_engineering_designations(self):
        allowed = ("208u2", "XT101", "XTFE107", "PU4-2", "PU 4-2",
                   "RES-101", "page7A", "reserveA")
        for value in allowed:
            with self.subTest(value=value):
                self.assertFalse(is_permanently_banned(value))
                self.assertIsNone(contains_permanent_ban_marker(value))


class WordFilteringTests(unittest.TestCase):
    def test_filters_service_suffix_but_keeps_designation(self):
        cases = {
            "201U1page 3": "201U1",
            "205U3 page3": "205U3",
            "-FTA202:XT2:C page7": "FTA202:XT2:C",
            "XT21:1 page reserve": "XT21:1",
            "208U2 reserveNN": "208U2",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(clean_word(raw), expected)

    def test_filters_pure_service_text_to_empty(self):
        for raw in ("page", "page 3", "reserve", "reserveNN", "-page7"):
            with self.subTest(raw=raw):
                self.assertEqual(clean_word(raw), "")

    def test_keeps_engineering_words_containing_page_or_reserve(self):
        for raw in ("page7A", "reserveA"):
            with self.subTest(raw=raw):
                self.assertEqual(clean_word(raw), raw)


if __name__ == "__main__":
    unittest.main()