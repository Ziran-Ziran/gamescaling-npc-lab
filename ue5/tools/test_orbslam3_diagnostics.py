"""Diagnostics must preserve algorithm code and distinguish background work."""
import unittest

import orbslam3_diagnostics as diag


class PatchTests(unittest.TestCase):
    def test_changed_local_threshold_cannot_be_misreported(self):
        body = ('if(mCurrentFrame.mnId<mnLastRelocFrameId+mMaxFrames && mnMatchesInliers<50)\n'
                'if((mnMatchesInliers>10)&&(mState==RECENTLY_LOST))\nif(mnMatchesInliers<30)')
        diag.validate_local_thresholds(body)
        with self.assertRaisesRegex(ValueError, 'exactly once'):
            diag.validate_local_thresholds(body.replace('mnMatchesInliers<30', 'mnMatchesInliers<32'))

    def test_duplicate_or_missing_anchor_refuses_patch(self):
        for source in ('missing', 'anchor anchor'):
            with self.assertRaisesRegex(ValueError, 'exactly once'):
                diag.replace_once(source, 'anchor', 'instrumented')

    def test_patch_is_limited_to_the_selected_function(self):
        source = 'void A::first()\n{\n    return;\n}\n\nvoid A::second()\n{\n    return;\n}\n'
        changed = diag.patch_function(source, 'void A::first()',
                                     lambda body: diag.replace_once(body, '    return;', '    log();\n    return;'))
        self.assertEqual(changed.count('log();'), 1)
        self.assertTrue(changed.endswith('void A::second()\n{\n    return;\n}\n'))


class AnalysisTests(unittest.TestCase):
    def test_many_features_can_still_have_too_few_map_inliers(self):
        events = [dict(seq=0, event='rgbd_features', frame_id=12, monotonic_s=1.,
                       features=1900, valid_depth_features=1200),
                  dict(seq=1, event='motion_projection', frame_id=12, monotonic_s=1.01, matches=150),
                  dict(seq=2, event='local_map_result', frame_id=12, monotonic_s=1.02,
                       inliers=18, required_inliers=30, local_map_points=400)]
        result = diag.summarize(events, [dict(frame_id=12, valid=False, state=3)])
        failure = result['failures'][0]
        self.assertEqual(failure['reason'], 'local_map_inliers_below_threshold')
        self.assertEqual(failure['features'], 1900)
        self.assertEqual(failure['local_inliers'], 18)

    def test_background_events_are_joined_by_keyframe_not_current_frame(self):
        events = [dict(seq=0, event='mapping_enqueue', frame_id=40, monotonic_s=1., keyframe_id=7, queue_size=1),
                  dict(seq=1, event='rgbd_features', frame_id=41, monotonic_s=1.1, features=1000),
                  dict(seq=2, event='mapping_begin', frame_id=None, monotonic_s=1.25, keyframe_id=7, queue_size=0)]
        result = diag.summarize(events, [dict(frame_id=41, valid=True, state=2)])
        self.assertEqual(result['keyframe_queue']['paired_keyframes'], 1)
        self.assertAlmostEqual(result['keyframe_queue']['wait_ms_max'], 250.)
        self.assertEqual(result['failures'], [])

    def test_missing_internal_events_do_not_invent_a_reason(self):
        result = diag.summarize([], [dict(frame_id=4, valid=False, state=3)])
        self.assertEqual(result['failures'][0]['reason'], 'insufficient_diagnostic_evidence')

    def test_relocalization_without_candidates_has_an_explicit_reason(self):
        result = diag.summarize([dict(seq=0, event='relocalization_candidates', frame_id=8,
                                     monotonic_s=1., candidates=0)],
                                [dict(frame_id=8, valid=False, state=3)])
        self.assertEqual(result['failures'][0]['reason'], 'no_relocalization_candidates')

    def test_reversed_event_sequence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'sequence'):
            diag.summarize([dict(seq=2, event='x'), dict(seq=1, event='y')], [])


if __name__ == '__main__':
    unittest.main()
