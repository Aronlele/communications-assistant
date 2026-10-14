import unittest

from scripts.endurance import cpu_seconds, interval_cpu


class ResourceMeasurementTests(unittest.TestCase):
    def test_cpu_counter_formats(self):
        self.assertEqual(cpu_seconds('0:01.25'), 1.25)
        self.assertEqual(cpu_seconds('01:02:03'), 3723)
        self.assertEqual(cpu_seconds('1-02:03:04'), 93784)
        with self.assertRaises(ValueError): cpu_seconds('nan:00')

    def test_short_snapshot_peak_does_not_become_interval_average(self):
        previous = {'sampled': 100, 'pid': 1, 'native_pid': 2, 'cpu_seconds': 10}
        current = {**previous, 'sampled': 130, 'cpu_seconds': 10.3, 'cpu_snapshot_percent': 20}
        self.assertAlmostEqual(interval_cpu(previous, current, 90), 1)

    def test_sleep_restart_and_counter_reset_are_not_idle_samples(self):
        previous = {'sampled': 100, 'pid': 1, 'native_pid': 2, 'cpu_seconds': 10}
        for changes in ({'sampled': 300}, {'pid': 3}, {'cpu_seconds': 9}, {'stale': True}):
            self.assertIsNone(interval_cpu(previous, {**previous, 'sampled': 130, **changes}, 90))
