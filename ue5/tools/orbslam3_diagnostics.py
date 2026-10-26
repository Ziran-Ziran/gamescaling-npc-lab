"""Instrument a LOCAL copy of ORB sources; summarize events without truth.

No upstream file or class layout is modified. Unknown source anchors fail before
creating output. The optional native build reuses copied existing object files.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def replace_once(source, anchor, replacement):
    if source.count(anchor) != 1:
        raise ValueError(f'expected anchor exactly once, found {source.count(anchor)}: {anchor[:90]}')
    return source.replace(anchor, replacement, 1)


def patch_function(source, signature, transform):
    if source.count(signature) != 1:
        raise ValueError(f'expected function exactly once: {signature}')
    start = source.index(signature)
    end = source.index('\n}', start) + 2
    return source[:start] + transform(source[start:end]) + source[end:]


def emit(event, fields, indent='    '):
    values = ', '.join('{"' + k + '", double(' + v + ')}' for k, v in fields.items())
    return indent + 'gsvdiag::emit("' + event + '", {' + values + '});\n'


def validate_local_thresholds(body):
    for condition in ('if(mCurrentFrame.mnId<mnLastRelocFrameId+mMaxFrames && mnMatchesInliers<50)',
                      'if((mnMatchesInliers>10)&&(mState==RECENTLY_LOST))',
                      'if(mnMatchesInliers<30)'):
        replace_once(body, condition, condition)


def tracking_source(source):
    source = replace_once(source, '#include "Tracking.h"', '#include "Tracking.h"\n#include "diagnostics.h"')

    def grab(body):
        extra = ('    int gsvDepth = 0, gsvClose = 0;\n'
                 '    for (float z : mCurrentFrame.mvDepth) {\n'
                 '        if (z > 0 && std::isfinite(z)) { ++gsvDepth; if (z < mThDepth) ++gsvClose; }\n'
                 '    }\n')
        extra += emit('rgbd_features', dict(features='mCurrentFrame.N', valid_depth_features='gsvDepth',
                       close_depth_features='gsvClose', close_threshold_m='mThDepth', orb_frame_id='mCurrentFrame.mnId'))
        return replace_once(body, '    Track();', extra+'    Track();')
    source = patch_function(source, 'Sophus::SE3f Tracking::GrabImageRGBD(', grab)

    def reference(body):
        anchor = '    if(nmatches<15)'
        body = replace_once(body, anchor, emit('reference_matches', dict(matches='nmatches',
                    keyframe_id='mpReferenceKF->mnId')) + anchor)
        anchor = '    if (mSensor == System::IMU_MONOCULAR || mSensor == System::IMU_STEREO || mSensor == System::IMU_RGBD)'
        return replace_once(body, anchor, emit('reference_inliers', dict(matches_after_optimization='nmatches',
                             map_inliers='nmatchesMap', required_inliers='10')) + anchor)
    source = patch_function(source, 'bool Tracking::TrackReferenceKeyFrame()', reference)

    def motion(body):
        anchor = '    // If few matches, uses a wider window search'
        body = replace_once(body, anchor, emit('motion_projection', dict(matches='nmatches', window_px='th')) + anchor)
        anchor = '        Verbose::PrintMess("Matches with wider search: " + to_string(nmatches), Verbose::VERBOSITY_NORMAL);'
        body = replace_once(body, anchor, anchor+'\n'+emit('motion_projection_wide', dict(matches='nmatches', window_px='2*th'), '        '))
        anchor = '    if(mbOnlyTracking)'
        return replace_once(body, anchor, emit('motion_inliers', dict(matches_after_optimization='nmatches',
                               map_inliers='nmatchesMap', required_inliers='10')) + anchor)
    source = patch_function(source, 'bool Tracking::TrackWithMotionModel()', motion)

    def local(body):
        validate_local_thresholds(body)
        anchor = '    // Decide if the tracking was succesful'
        values = dict(inliers='mnMatchesInliers', local_map_points='mvpLocalMapPoints.size()',
                      previous_state='mState', queue_size='mpLocalMapper->KeyframesInQueue()',
                      required_inliers='(mCurrentFrame.mnId<mnLastRelocFrameId+mMaxFrames ? 50 : (mState==RECENTLY_LOST ? 11 : 30))')
        return replace_once(body, anchor, emit('local_map_result', values) + anchor)
    source = patch_function(source, 'bool Tracking::TrackLocalMap()', local)

    def need(body):
        anchor = '    if(((c1a||c1b||c1c) && c2)||c3 ||c4)'
        values = dict(c1a='c1a', c1b='c1b', c1c='c1c', c2='c2',
                      close_tracked='nTrackedClose', close_untracked='nNonTrackedClose',
                      reference_matches='nRefMatches', inliers='mnMatchesInliers',
                      mapper_idle='bLocalMappingIdle', queue_size='mpLocalMapper->KeyframesInQueue()')
        return replace_once(body, anchor, emit('keyframe_gates', values) + anchor)
    source = patch_function(source, 'bool Tracking::NeedNewKeyFrame()', need)

    def create(body):
        at = body.index('{') + 1
        body = body[:at] + '\n    int gsvNewDepthPoints = 0;' + body[at:]
        anchor = '                    mCurrentFrame.mvpMapPoints[i]=pNewMP;'
        body = replace_once(body, anchor, anchor+'\n                    ++gsvNewDepthPoints;')
        anchor = '    mpLocalMapper->InsertKeyFrame(pKF);'
        return replace_once(body, anchor, emit('keyframe_created', dict(keyframe_id='pKF->mnId',
                    keyframe_time_s='pKF->mTimeStamp', new_depth_points='gsvNewDepthPoints',
                    map_points='pKF->GetMap()->MapPointsInMap()')) + anchor)
    source = patch_function(source, 'void Tracking::CreateNewKeyFrame()', create)
    def relocalization(body):
        anchor = '    if(vpCandidateKFs.empty()) {'
        body = replace_once(body, anchor, emit('relocalization_candidates',
                            dict(candidates='vpCandidateKFs.size()')) + anchor)
        anchor = '    if(!bMatch)'
        return replace_once(body, anchor, emit('relocalization_result', dict(matched='bMatch')) + anchor)
    source = patch_function(source, 'bool Tracking::Relocalization()', relocalization)
    for signature, event in (('void Tracking::Reset(bool bLocMap)', 'tracking_reset_all'),
                             ('void Tracking::ResetActiveMap(bool bLocMap)', 'tracking_reset_active'),
                             ('void Tracking::CreateMapInAtlas()', 'tracking_create_map')):
        def entry(body, name=event):
            at = body.index('{') + 1
            return body[:at] + '\n' + emit(name, {}) + body[at:]
        source = patch_function(source, signature, entry)
    return source


def mapping_source(source):
    source = replace_once(source, '#include "LocalMapping.h"', '#include "LocalMapping.h"\n#include "diagnostics.h"')
    def enqueue(body):
        anchor = '    mlNewKeyFrames.push_back(pKF);'
        return replace_once(body, anchor, anchor+'\n'+emit('mapping_enqueue', dict(
            keyframe_id='pKF->mnId', keyframe_time_s='pKF->mTimeStamp', queue_size='mlNewKeyFrames.size()')))
    source = patch_function(source, 'void LocalMapping::InsertKeyFrame(', enqueue)
    def begin(body):
        anchor = '        mlNewKeyFrames.pop_front();'
        return replace_once(body, anchor, anchor+'\n'+emit('mapping_begin', dict(
            keyframe_id='mpCurrentKeyFrame->mnId', keyframe_time_s='mpCurrentKeyFrame->mTimeStamp',
            queue_size='mlNewKeyFrames.size()'), '        '))
    source = patch_function(source, 'void LocalMapping::ProcessNewKeyFrame()', begin)
    def run(body):
        anchor = '                        Optimizer::LocalBundleAdjustment(mpCurrentKeyFrame,&mbAbortBA, mpCurrentKeyFrame->GetMap(),num_FixedKF_BA,num_OptKF_BA,num_MPs_BA,num_edges_BA);'
        values = dict(keyframe_id='mpCurrentKeyFrame->mnId', keyframe_time_s='mpCurrentKeyFrame->mTimeStamp')
        body = replace_once(body, anchor, emit('bundle_begin', values, '                        ')+anchor+'\n'+
                            emit('bundle_end', dict(values, optimized_points='num_MPs_BA', edges='num_edges_BA'), '                        '))
        anchor = '            mpLoopCloser->InsertKeyFrame(mpCurrentKeyFrame);'
        return replace_once(body, anchor, anchor+'\n'+emit('mapping_end', dict(values,
            queue_size='KeyframesInQueue()', map_points='mpCurrentKeyFrame->GetMap()->MapPointsInMap()',
            map_keyframes='mpCurrentKeyFrame->GetMap()->KeyFramesInMap()'), '            '))
    return patch_function(source, 'void LocalMapping::Run()', run)


def prepare(root, out):
    root, out = Path(root).resolve(), Path(out).resolve()
    if out.exists():
        raise FileExistsError(out)
    # Validate all patches and all required objects BEFORE any writes.
    sources = {name: (root/'src'/name).read_text(encoding='utf-8')
               for name in ('Tracking.cc', 'LocalMapping.cc')}
    patched = dict(Tracking=tracking_source(sources['Tracking.cc']),
                   LocalMapping=mapping_source(sources['LocalMapping.cc']))
    objects = sorted((root/'build/CMakeFiles/ORB_SLAM3.dir/src').rglob('*.o'))
    if len(objects) < 25 or not any(p.name == 'System.cc.o' for p in objects):
        raise ValueError('complete existing ORB object build required; no full rebuild is attempted')
    out.mkdir()
    (out/'objects').mkdir()
    source_hashes = {}
    for name, text in patched.items():
        (out/(name+'.cc')).write_text(text, encoding='utf-8')
        source_hashes[name+'.cc'] = hashlib.sha256(sources[name+'.cc'].encode()).hexdigest()
    object_hashes = {}
    for path in objects:
        relative = path.relative_to(root/'build/CMakeFiles/ORB_SLAM3.dir/src')
        if relative.name in ('Tracking.cc.o', 'LocalMapping.cc.o'):
            continue
        destination = out/'objects'/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        object_hashes[relative.as_posix()] = hashlib.sha256(destination.read_bytes()).hexdigest()
    provenance = dict(source_root=str(root), original_source_sha256=source_hashes,
                      reused_object_sha256=object_hashes, upstream_commit='unknown: no .git evidence',
                      policy='local generated upstream copies retain upstream notices; no class layout or algorithm threshold changes')
    (out/'provenance.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    return provenance


def summarize(events, records):
    previous = -1
    by_frame, pending, waits, queue_sizes = {}, {}, [], []
    counts = {}
    for event in events:
        if event['seq'] <= previous:
            raise ValueError('non-increasing diagnostic event sequence')
        previous = event['seq']
        name = event['event']
        counts[name] = counts.get(name, 0) + 1
        frame = event.get('frame_id')
        if frame is not None:
            by_frame.setdefault(frame, []).append(event)
        if 'queue_size' in event:
            queue_sizes.append(event['queue_size'])
        if name == 'mapping_enqueue':
            pending[event['keyframe_id']] = event['monotonic_s']
        elif name == 'mapping_begin' and event['keyframe_id'] in pending:
            waits.append(1000*(event['monotonic_s']-pending.pop(event['keyframe_id'])))
    failures = []
    for record in records:
        if record['valid']:
            continue
        own = by_frame.get(record['frame_id'], [])
        def last(name):
            return next((e for e in reversed(own) if e['event'] == name), {})
        features = last('rgbd_features')
        local = last('local_map_result')
        reference = last('reference_matches')
        motion = last('motion_projection_wide') or last('motion_projection')
        relocation = last('relocalization_candidates')
        relocation_result = last('relocalization_result')
        reason = 'insufficient_diagnostic_evidence'
        if local and local['inliers'] < local['required_inliers']:
            reason = 'local_map_inliers_below_threshold'
        elif relocation and relocation['candidates'] == 0:
            reason = 'no_relocalization_candidates'
        elif relocation_result and not relocation_result['matched']:
            reason = 'relocalization_candidates_rejected'
        elif reference and reference['matches'] < 15 and (not motion or motion['matches'] < 20):
            reason = 'insufficient_initial_correspondences'
        elif own:
            reason = 'state_transition_or_unobserved_branch'
        failures.append(dict(frame_id=record['frame_id'], state=record['state'], reason=reason,
                             features=features.get('features'), valid_depth_features=features.get('valid_depth_features'),
                             local_inliers=local.get('inliers'), required_inliers=local.get('required_inliers'),
                             local_map_points=local.get('local_map_points'),
                             relocalization_candidates=relocation.get('candidates'),
                             reference_matches=reference.get('matches'), projection_matches=motion.get('matches')))
    return dict(ground_truth_usage='none', diagnostic_timing='instrumented execution only; not a realtime benchmark',
                event_counts=counts, failures=failures,
                keyframe_queue=dict(paired_keyframes=len(waits), not_started_in_log=len(pending),
                    wait_ms_max=max(waits) if waits else None, queue_size_max=max(queue_sizes) if queue_sizes else None))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('prepare')
    build.add_argument('root', type=Path)
    build.add_argument('out', type=Path)
    report = commands.add_parser('report')
    report.add_argument('run', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        data = prepare(args.root, args.out)
    else:
        data = summarize([json.loads(s) for s in (args.run/'diagnostics.jsonl').read_text().splitlines()],
                         [json.loads(s) for s in (args.run/'tracking.jsonl').read_text().splitlines()])
        (args.run/'diagnostic_report.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
    print(json.dumps(data, indent=2))


if __name__ == '__main__':
    main()
