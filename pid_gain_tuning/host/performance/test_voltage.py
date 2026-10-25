import unittest
from voltage import vpp_to_codes,codes_to_vpp


class VoltageTests(unittest.TestCase):
    def test_peak_to_peak_and_integer_rounding(self):
        self.assertAlmostEqual(codes_to_vpp(64),.0836224)
        self.assertEqual(vpp_to_codes(.084),64)
        for vpp in [.02,.04,.08,.16,.32]:
            self.assertLessEqual(abs(codes_to_vpp(vpp_to_codes(vpp))-vpp),.0006533)

    def test_invalid_values_do_not_create_an_injection(self):
        for value in [0,-1,float('nan'),float('inf'),30,.00001]:
            with self.assertRaises(ValueError):vpp_to_codes(value)

if __name__=='__main__':unittest.main()
