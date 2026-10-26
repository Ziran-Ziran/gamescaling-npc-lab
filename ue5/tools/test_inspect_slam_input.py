"""Read-only intake contracts for future image/video/map inputs."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

import inspect_slam_input as intake


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'frames').mkdir()
        self.k = dict(fx=20., fy=20., cx=20., cy=15., w=40, h=30)
        (self.root/'sensor.json').write_text(json.dumps(dict(calibration=self.k, target_hz=10,
            time_source='nominal_frame_id_over_target_hz', depth_unit='millimetres',
            depth_meaning='optical_axis')))
        for i in range(3):
            image = np.zeros((30, 40, 3), np.uint8)
            image[:, :, i] = 100
            cv2.imwrite(str(self.root/'frames'/f'frame_{i:06}.png'), image)

    def depth(self):
        (self.root/'depth').mkdir()
        for i in range(3):
            cv2.imwrite(str(self.root/'depth'/f'frame_{i:06}.png'),
                        np.full((30, 40), 3000, np.uint16))

    def test_rgb_without_depth_routes_to_monocular_and_does_not_read_truth(self):
        (self.root/'poses.jsonl').write_bytes(b'INVALID TRUTH MUST NOT BE PARSED')
        before = hashlib.sha256((self.root/'poses.jsonl').read_bytes()).hexdigest()
        result = intake.inspect(self.root)
        self.assertEqual(result['kind'], 'rgb')
        self.assertEqual(result['recommended_route'], 'monocular_slam')
        self.assertTrue(result['offline_ready'])
        self.assertFalse(result['live_timing_ready'])
        self.assertEqual(hashlib.sha256((self.root/'poses.jsonl').read_bytes()).hexdigest(), before)

    def test_missing_depth_pair_is_reported(self):
        self.depth()
        (self.root/'depth'/'frame_000001.png').unlink()
        result = intake.inspect(self.root)
        self.assertEqual(result['kind'], 'rgbd')
        self.assertFalse(result['offline_ready'])
        self.assertIn('rgb_depth_pair_mismatch', result['errors'])

    def test_correct_rgbd_with_observed_timestamps_is_ready(self):
        self.depth()
        config = json.loads((self.root/'sensor.json').read_text())
        config['time_source'] = 'sensor_capture_monotonic'
        (self.root/'sensor.json').write_text(json.dumps(config))
        (self.root/'timestamps.jsonl').write_text('\n'.join(json.dumps(dict(
            frame_id=i, t_sensor_s=20.+i*.1)) for i in range(3)))
        result = intake.inspect(self.root)
        self.assertEqual(result['recommended_route'], 'rgbd_slam')
        self.assertTrue(result['offline_ready'])
        self.assertTrue(result['live_timing_ready'])
        self.assertEqual(result['timestamps']['source'], 'independent_sensor_manifest')

    def test_manifest_does_not_promote_nominal_time_to_observed(self):
        (self.root/'timestamps.jsonl').write_text('\n'.join(json.dumps(dict(
            frame_id=i, t_sensor_s=i*.1)) for i in range(3)))
        result = intake.inspect(self.root)
        self.assertTrue(result['offline_ready'])
        self.assertFalse(result['live_timing_ready'])
        self.assertIn('nominal', result['timestamps']['source'])

    def test_reversed_timestamps_are_rejected(self):
        (self.root/'timestamps.jsonl').write_text('\n'.join(json.dumps(dict(
            frame_id=i, t_sensor_s=t)) for i,t in enumerate((0., .2, .1))))
        result = intake.inspect(self.root)
        self.assertIn('timestamps_not_strictly_increasing', result['errors'])

    def test_changed_dimensions_and_corrupt_image_are_reported(self):
        cv2.imwrite(str(self.root/'frames'/'frame_000001.png'), np.zeros((20, 40, 3), np.uint8))
        (self.root/'frames'/'frame_000002.png').write_bytes(b'bad png')
        result = intake.inspect(self.root)
        self.assertIn('image_dimensions_changed', result['errors'])
        self.assertIn('unreadable_rgb', result['errors'])

    def test_wrong_depth_unit_is_not_guessed(self):
        self.depth()
        config = json.loads((self.root/'sensor.json').read_text())
        config['depth_unit'] = 'unknown'
        (self.root/'sensor.json').write_text(json.dumps(config))
        self.assertIn('unsupported_or_missing_depth_contract', intake.inspect(self.root)['errors'])

    def test_map_alone_is_not_a_trackable_sequence(self):
        path = self.root/'scene.obj'
        path.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
        result = intake.inspect(path)
        self.assertEqual(result['kind'], 'map')
        self.assertFalse(result['offline_ready'])
        self.assertEqual(result['recommended_route'], 'map_localization_requires_images_and_map_format_adapter')

    def test_calibration_must_not_be_fetched_from_truth(self):
        (self.root/'sensor.json').unlink()
        (self.root/'poses.jsonl').write_text(json.dumps(dict(i=0, **self.k)))
        result = intake.inspect(self.root)
        self.assertIn('missing_independent_calibration', result['errors'])

    def test_video_is_monocular_and_container_fps_is_only_nominal(self):
        path = self.root/'capture.avi'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10., (40,30))
        if not writer.isOpened():
            self.skipTest('MJPG video encoder unavailable')
        try:
            for i in range(5):
                writer.write(np.full((30,40,3),i*30,np.uint8))
        finally:
            writer.release()
        result = intake.inspect(path, max_frames=0)
        self.assertEqual(result['kind'], 'video')
        self.assertEqual(result['frames_checked'], 5)
        self.assertTrue(result['offline_ready'])
        self.assertFalse(result['live_timing_ready'])
        self.assertFalse(intake.inspect(path, max_frames=2)['offline_ready'])

    def test_partial_image_check_does_not_claim_full_readiness(self):
        result = intake.inspect(self.root, max_frames=1)
        self.assertFalse(result['offline_ready'])
        self.assertFalse(result['full_inspection'])

    def test_malformed_metadata_is_reported_as_an_input_error(self):
        (self.root/'sensor.json').write_text('{bad json')
        self.assertIn('invalid_sensor_metadata', intake.inspect(self.root)['errors'])

    def test_invalid_nominal_frequency_does_not_escape_as_json_nan(self):
        data = json.loads((self.root/'sensor.json').read_text())
        data['target_hz'] = float('nan')
        (self.root/'sensor.json').write_text(json.dumps(data))
        result = intake.inspect(self.root)
        self.assertFalse(result['offline_ready'])
        json.dumps(result, allow_nan=False)

    def test_bad_timestamp_manifests_are_reported_without_crashing(self):
        for payload in ('{broken', '[]', '{"frame_id":"zero","t_sensor_s":0}'):
            with self.subTest(payload=payload):
                (self.root/'timestamps.jsonl').write_text(payload)
                result = intake.inspect(self.root)
                self.assertFalse(result['offline_ready'])
                self.assertTrue(any('timestamp' in e for e in result['errors']))

    def test_null_depth_metadata_is_reported(self):
        (self.root/'sensor.json').write_text(json.dumps(dict(calibration=self.k, target_hz=10, depth=None)))
        self.assertIn('invalid_sensor_metadata', intake.inspect(self.root)['errors'])

    def test_rgbd_requires_bgr_conversion_for_grayscale_or_bgra(self):
        self.depth()
        for shape in ((30,40), (30,40,4)):
            with self.subTest(shape=shape):
                cv2.imwrite(str(self.root/'frames'/'frame_000000.png'),np.zeros(shape,np.uint8))
                result = intake.inspect(self.root)
                self.assertFalse(result['offline_ready'])
                self.assertIn('rgbd_requires_bgr_conversion', result['errors'])

    def test_linked_timestamp_manifest_never_opens_truth(self):
        truth = self.root/'poses.jsonl'
        truth.write_text('SHOULD NEVER BE READ')
        link = self.root/'timestamps.jsonl'
        try:
            link.symlink_to(truth)
        except (OSError, NotImplementedError):
            self.skipTest('symlink creation unavailable on this host')
        result = intake.inspect(self.root)
        self.assertIn('unsafe_timestamp_path', result['errors'])

    def test_linked_image_directory_is_rejected(self):
        frames = self.root/'frames'
        frames.rename(self.root/'real-frames')
        try:
            frames.symlink_to(self.root/'real-frames',target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('directory symlink creation unavailable on this host')
        self.assertIn('unsafe_sensor_path', intake.inspect(self.root)['errors'])


if __name__ == '__main__':
    unittest.main()
