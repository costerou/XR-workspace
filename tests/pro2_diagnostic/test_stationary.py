import unittest
from tools.pro2_diagnostic.stationary import summarize

class StationaryTests(unittest.TestCase):
    def rows(self):
        return [dict(kind='pose',sdk_values=[0,0,0,1,0,0,0],timestamp_raw=10**12+i*16666667,
                     host_monotonic_ns=10**15+i*16666667,issues=[]) for i in range(901)]
    def test_paired_clock_fit_does_not_declare_units(self):
        result=summarize(self.rows())
        self.assertAlmostEqual(result['sdk_ticks_per_host_second_ols'],1e9,delta=1)
        self.assertIn('unvalidated',result['timestamp_unit'])
        self.assertEqual(result['angular_distance_from_first_deg']['max'],0)
    def test_rejects_bad_order_and_payload(self):
        for field,value in [('timestamp_raw',0),('host_monotonic_ns',0),('sdk_values',[0]*6)]:
            rows=self.rows();rows[1][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):summarize(rows)
    def test_no_serial_or_arbitrary_metadata_export(self):
        rows=self.rows()+[dict(kind='contract',serial='PRIVATE',library_sha256='serial'),dict(kind='firmware_query',status=0,version='PRIVATE')]
        result=summarize(rows)
        self.assertNotIn('PRIVATE',str(result))

    def test_zero_quaternion_rejected(self):
        rows=self.rows();rows[0]['sdk_values']=[0]*7
        with self.assertRaisesRegex(ValueError,'zero quaternion'):summarize(rows)

    def test_clock_fit_with_receipt_jitter_and_offset(self):
        rows=self.rows()
        for i,r in enumerate(rows):r['host_monotonic_ns'] += (i%3-1)*400000
        result=summarize(rows)
        self.assertAlmostEqual(result['sdk_ticks_per_host_second_ols']/1e9,1,delta=.0001)
