"""Safety contracts for the causal ORB-SLAM3 adapter (no native library needed)."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import cv2
import numpy as np

import orbslam3_input as sensor
import orbslam3_report as report
import summarize


class SensorInputTests(unittest.TestCase):
    def setUp(self):
        # Image contracts must not depend on free space on the CI host.
        disk = patch.object(sensor.shutil, 'disk_usage', return_value=SimpleNamespace(free=100*1024**3))
        disk.start()
        self.addCleanup(disk.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.session = self.root / 'source'
        self.session.mkdir()
        for folder in ('frames', 'depth'):
            (self.session / folder).mkdir()
        self.meta = dict(width=8, height=6, target_hz=10, calibration_constant=True,
                         calibration=dict(fx=4., fy=4., cx=4., cy=3., w=8, h=6),
                         flip_vertical=False,
                         depth=dict(unit='millimetres', format='png16_gray'),
                         start_pose={'loc': ['FORBIDDEN']})
        self.write_meta()
        for i in (0, 1, 2):
            cv2.imwrite(str(self.session / 'frames' / f'{i:06}.png'),
                        np.zeros((6, 8, 3), np.uint8))
            cv2.imwrite(str(self.session / 'depth' / f'{i:06}.png'),
                        np.full((6, 8), 1000, np.uint16))

    def write_meta(self):
        (self.session / 'meta.json').write_text(json.dumps(self.meta))

    def test_prepares_without_any_truth_file_and_excludes_metadata_pose(self):
        out = self.root / 'input'
        sensor.prepare(self.session, out)
        self.assertEqual({p.name for p in out.iterdir()},
                         {'frames', 'depth', 'sensor.json', 'frames.tsv', 'settings.yaml'})
        payload = (out / 'sensor.json').read_text()
        self.assertNotIn('FORBIDDEN', payload)
        self.assertNotIn('start_pose', payload)
        manifest = (out / 'frames.tsv').read_text().splitlines()
        self.assertEqual([float(line.split()[1]) for line in manifest], [0., .1, .2])
        self.assertIn('nominal', payload)
        self.assertIn('RGBD.DepthMapFactor: 1000.0', (out / 'settings.yaml').read_text())
        self.assertIn('Camera.RGB: 0', (out / 'settings.yaml').read_text())

    def test_rejects_unpaired_depth(self):
        (self.session / 'depth' / '000001.png').unlink()
        with self.assertRaisesRegex(ValueError, 'pair'):
            sensor.prepare(self.session, self.root / 'input')

    def test_rejects_changing_calibration(self):
        self.meta['calibration_constant'] = False
        self.write_meta()
        with self.assertRaisesRegex(ValueError, 'calibration'):
            sensor.prepare(self.session, self.root / 'input')

    def test_rejects_wrong_depth_type(self):
        cv2.imwrite(str(self.session / 'depth' / '000001.png'), np.zeros((6, 8), np.uint8))
        with self.assertRaisesRegex(ValueError, 'uint16'):
            sensor.prepare(self.session, self.root / 'input')

    def test_bgra_capture_is_explicitly_converted_to_bgr_without_flipping(self):
        rgb = np.zeros((6, 8, 4), np.uint8)
        rgb[0, 0] = [7, 17, 37, 255]
        cv2.imwrite(str(self.session / 'frames' / '000000.png'), rgb)
        out = self.root / 'input'
        sensor.prepare(self.session, out)
        actual = cv2.imread(str(out / 'frames' / '000000.png'), -1)
        self.assertEqual(actual.shape, (6, 8, 3))
        self.assertEqual(actual[0, 0].tolist(), [7, 17, 37])

    def test_refuses_existing_output(self):
        out = self.root / 'input'
        out.mkdir()
        with self.assertRaises(FileExistsError):
            sensor.prepare(self.session, out)

    def test_disk_guard_refuses_new_copy(self):
        with patch.object(sensor.shutil, 'disk_usage', return_value=SimpleNamespace(free=1024**3)):
            with self.assertRaisesRegex(ValueError, 'disk budget'):
                sensor.prepare(self.session, self.root / 'input')

    def test_saturated_depth_is_invalid_instead_of_an_invented_surface(self):
        depth = np.full((6, 8), 1000, np.uint16)
        depth[0, 0] = 65535
        cv2.imwrite(str(self.session / 'depth' / '000000.png'), depth)
        out = self.root / 'input'
        sensor.prepare(self.session, out)
        self.assertEqual(cv2.imread(str(out / 'depth' / '000000.png'), -1)[0, 0], 0)


class ReportTests(unittest.TestCase):
    def test_report_without_truth_preserves_invalid_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            records = [dict(frame_id=i, state=2 if i != 1 else 3, valid=i != 1,
                            map_id=0 if i != 1 else -1, tracking_ms=2., load_ms=3.)
                       for i in range(3)]
            (out/'tracking.jsonl').write_text('\n'.join(json.dumps(r) for r in records))
            (out/'run.json').write_text(json.dumps(dict(frames=3, processing_wall_s=1., pace_hz=0)))
            (out/'sparse_snapshot.jsonl').write_text('')
            result = report.report(out)
            self.assertEqual(result['ground_truth_usage'], 'none')
            self.assertEqual(result['invalid_frames'], 1)
            self.assertEqual(len(result['segments']), 2)

    def test_native_hash_manifest_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / 'run.json').write_text('{}')
            (out / 'native-sha256.json').write_text(json.dumps({'run.json': 'old'}))
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                report.report(out)
            self.assertEqual(json.loads((out/'native-sha256.json').read_text())['run.json'], 'old')

    def test_summary_keeps_coverage_next_to_error_and_does_not_mix_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / 'orbslam3-run' / 'replay-a-0'
            out.mkdir(parents=True)
            (out / 'orbslam3_eval.json').write_text(json.dumps(dict(
                session='a', run='replay-a-0', total_frames=10, valid_frames=5,
                coverage=.5, observed_map_ids=[0, 1], segments=[{}, {}],
                longest_invalid_run=5, active_map_resets=1, system_resets=0,
                processing_fps=12, timing={'tracking_ms': {'p50': 30, 'p95': 90}},
                native={'pace_hz': 0}, sparse_snapshot_points=90)))
            table = summarize.orbslam3_table(root)
            self.assertIn('5/10', table)
            self.assertIn('0.5', table)
            self.assertIn('逐段见报告', table)

    def test_lost_frames_break_segments_and_maps_are_never_joined(self):
        records = [dict(frame_id=i, map_id=m, valid=v, tracking_ms=1., load_ms=2.)
                   for i, m, v in ((0, 0, True), (1, 0, True), (2, -1, False),
                                   (3, 0, True), (4, 1, True), (5, 1, True))]
        result = report.summarize_records(records)
        self.assertEqual(result['valid_frames'], 5)
        self.assertEqual(result['longest_invalid_run'], 1)
        self.assertEqual(result['observed_map_ids'], [0, 1])
        self.assertEqual([s['frames'] for s in result['segments']], [2, 1, 2])

    def test_rigid_alignment_does_not_hide_scale_error(self):
        points = np.array([[0., 0., 0.], [1., 0., 0.], [1., 1., 0.], [0., 1., 0.]])
        scaled = 2 * points + [9., 8., 7.]
        self.assertGreater(report.alignment_metrics(points, scaled)['rigid_ate_rmse_m'], .5)
        self.assertLess(report.alignment_metrics(points, scaled)['sim3_ate_rmse_m'], 1e-10)

    def test_rigid_alignment_handles_rotation_and_translation(self):
        points = np.array([[0., 0., 0.], [1., 0., 0.], [1., 1., 0.], [0., 1., 1.]])
        rotated = points @ np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]]) + 3
        self.assertLess(report.alignment_metrics(points, rotated)['rigid_ate_rmse_m'], 1e-10)


if __name__ == '__main__':
    unittest.main()
