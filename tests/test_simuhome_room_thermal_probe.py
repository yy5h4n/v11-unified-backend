import json, subprocess, sys
from pathlib import Path
import unittest

P = Path(__file__).resolve().parents[1]
class TestSimuHomeProbe(unittest.TestCase):
    def test_probe_is_fail_closed_and_reports_scope(self):
        r = subprocess.run([sys.executable, str(P/'probe_simuhome_room_thermal.py')], cwd=P, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads((P/'generated/simuhome_room_thermal_probe_v1.json').read_text())
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual(set(data['rooms']), {'kitchen','bathroom'})
        self.assertEqual(data['unsupported_lifecycle_semantics'], ['cooking','shower','wake','presence','fireplace','newborn'])
        self.assertTrue(data['supported']['room_thermal_dynamics'])

if __name__ == '__main__': unittest.main()
