"""A short run of the cross-language wire fuzz (wire_fuzz.py): the same generated frames through Python, C++ and Rust, no
verdict different. Needs the built control_wire_check (CONTROL_WIRE_CHECK, as ctest control_wire sets it) and the Rust example
(cargo build --release --manifest-path rust/Cargo.toml -p xr-wire --example wire_fuzz_verdicts, or WIRE_FUZZ_VERDICTS);
skipped where either is missing. The gate itself is a million frames: wire_fuzz.py --frames 1000000.
"""
import os
from pathlib import Path
import unittest

import wire_fuzz

CPP = os.environ.get('CONTROL_WIRE_CHECK')
RUST = os.environ.get('WIRE_FUZZ_VERDICTS', str(wire_fuzz.RUST_TOOL))


class Rewrite(unittest.TestCase):
    def test_negative_zero_outside_strings_only(self):
        self.assertEqual(wire_fuzz.as_rust_reads(b'{"a":-0,"b":[-0],"c":"-0","d":-0.5,"e":-0e1,"f":-01}\n'),
                         b'{"a":-0.0,"b":[-0.0],"c":"-0","d":-0.5,"e":-0e1,"f":-01}\n')
        self.assertEqual(wire_fuzz.as_rust_reads(b'["\\"-0",-0]\n'), b'["\\"-0",-0.0]\n')
        self.assertEqual(wire_fuzz.as_rust_reads(b'[1e-0,2E-0]\n'), b'[1e-0,2E-0]\n')  # an exponent's sign is left

    def test_byte_order_mark_unreadable_to_cpp(self):
        self.assertEqual(wire_fuzz.as_rust_reads(b'\xef\xbb\xbf{}\n'), b'\xff\xff\xff{}\n')

    def test_nul_unreadable_to_cpp(self):
        self.assertEqual(wire_fuzz.as_rust_reads(b'{"a":1}\x00\n'), b'{"a":1}\xff\n')


@unittest.skipUnless(CPP and Path(RUST).exists(), 'needs control_wire_check (CONTROL_WIRE_CHECK) and wire_fuzz_verdicts')
class WireFuzz(unittest.TestCase):
    def test_no_verdict_differs(self):
        report = wire_fuzz.fuzz(20_000, CPP, RUST, seed=7)
        summary = report.summary()
        self.assertEqual(report.differences, 0, summary['examples'])
        # every wire saw accepts and refusals, the frame wires many kinds of refusal
        self.assertTrue({'accepted', 'refused'} <= set(summary['counts']['json']), summary['counts']['json'])
        for wire in ('renderer', 'run'):
            self.assertIn('accepted', summary['counts'][wire], wire)
            self.assertGreater(len(summary['counts'][wire]), 10, summary['counts'][wire])


if __name__ == '__main__':
    unittest.main()
