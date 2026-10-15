"""Restrict the compatibility conversion to explicit signed shift expressions."""
import unittest

from scripts.ghdl_verilog import normalize_signed_shifts


class GHDLVerilogTests(unittest.TestCase):
    def test_signed_logical_shifts_become_arithmetic_and_unsigned_shifts_stay_logical(self):
        original='  assign n1 = $signed(n0) >> shift;\n  assign n2 = n0 >> shift;\n  assign n3 = $signed(n0) >>> shift;\n'
        converted,count=normalize_signed_shifts(original)
        self.assertEqual(count,1)
        self.assertEqual(converted,original.replace('$signed(n0) >> shift','$signed(n0) >>> shift'))
        self.assertEqual(normalize_signed_shifts(converted),(converted,0))

    def test_unrecognized_signed_shift_form_is_rejected(self):
        with self.assertRaises(ValueError):normalize_signed_shifts('always @* n1 = $signed(n0) >> shift;\n')


if __name__=='__main__':unittest.main()
