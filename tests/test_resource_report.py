import unittest
from scripts.resource_report import DEFAULT, parse_summary


class ResourceReportTests(unittest.TestCase):
    def test_reference_counts_and_actual_headroom(self):
        result = parse_summary(DEFAULT.read_text())
        self.assertEqual(result['alms']['used'], 17060)
        self.assertEqual(result['alms']['free'], 1420)
        self.assertEqual(result['ram_blocks']['free'], 1)
        self.assertEqual(result['dsp_blocks']['free'], 8)

    def test_incomplete_report_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Missing resource field'):
            parse_summary('Fitter Status : Successful\n')

    def test_invalid_capacity_is_rejected(self):
        text = DEFAULT.read_text().replace('17,060 / 18,480', '17,060 / 0')
        with self.assertRaisesRegex(ValueError, 'Invalid resource counts'):
            parse_summary(text)


if __name__ == '__main__':
    unittest.main()
