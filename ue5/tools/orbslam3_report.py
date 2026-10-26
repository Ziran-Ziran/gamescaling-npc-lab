"""Native replay accounting, then optional separate post-run truth scoring.

The tracker never imports this module. GT alignment is written only in this
evaluation report. Native poses, map snapshots and their hashes are immutable.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def summarize_records(records):
    if not records:
        raise ValueError('no tracking records')
    segments, current = [], None
    longest, lost, valid = 0, 0, 0
    maps = set()
    previous = -1
    states = {}
    for record in records:
        i = record['frame_id']
        if i <= previous:
            raise ValueError('non-causal or duplicate tracking records')
        previous = i
        state = str(record.get('state', 'unknown'))
        states[state] = states.get(state, 0) + 1
        if not record['valid']:
            lost += 1
            longest = max(longest, lost)
            current = None
            continue
        if record['map_id'] < 0:
            raise ValueError('valid pose without an observed map identity')
        valid += 1
        lost = 0
        maps.add(record['map_id'])
        if (current is None or current['map_id'] != record['map_id']
                or i != current['last_frame_id'] + 1):
            current = dict(map_id=record['map_id'], first_frame_id=i, last_frame_id=i, frames=0)
            segments.append(current)
        current['last_frame_id'] = i
        current['frames'] += 1
    timing = {}
    for key in ('tracking_ms', 'load_ms'):
        values = [r[key] for r in records]
        timing[key] = dict(p50=float(np.percentile(values, 50)),
                           p95=float(np.percentile(values, 95)), max=float(max(values)))
    return dict(total_frames=len(records), valid_frames=valid, invalid_frames=len(records)-valid,
                coverage=valid / len(records), longest_invalid_run=longest,
                observed_map_ids=sorted(maps), segments=segments, states=states, timing=timing)


def alignment_metrics(source, target):
    source, target = np.asarray(source, float), np.asarray(target, float)
    if len(source) < 3 or source.shape != target.shape:
        raise ValueError('at least three paired camera centres required')
    a, b = source-source.mean(axis=0), target-target.mean(axis=0)
    if np.linalg.matrix_rank(a, tol=1e-7) < 2 or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError('degenerate or non-finite trajectory')
    u, singular, vt = np.linalg.svd(b.T @ a / len(a))
    correction = np.eye(3)
    correction[2, 2] = 1 if np.linalg.det(u @ vt) > 0 else -1
    rotation = u @ correction @ vt
    scale = float((singular * np.diag(correction)).sum() / np.mean(np.sum(a*a, axis=1)))
    rigid = a @ rotation.T - b
    similarity = scale * a @ rotation.T - b
    return dict(rigid_ate_rmse_m=float(np.sqrt(np.mean(np.sum(rigid*rigid, axis=1)))),
                sim3_ate_rmse_m=float(np.sqrt(np.mean(np.sum(similarity*similarity, axis=1)))),
                sim3_scale=scale)


def hashes(directory):
    names = ('tracking.jsonl', 'run.json', 'sparse_snapshot.jsonl',
             'keyframes_snapshot.jsonl', 'trajectory_async_snapshot.tum', 'diagnostics.jsonl')
    return {name: hashlib.sha256((directory/name).read_bytes()).hexdigest()
            for name in names if (directory/name).exists()}


def report(directory, session=None):
    directory = Path(directory)
    if not (directory / 'run.json').exists():
        raise ValueError('native run incomplete; refusing evaluation')
    native_hashes = hashes(directory)
    manifest = directory / 'native-sha256.json'
    if manifest.exists() and json.loads(manifest.read_text()) != native_hashes:
        raise RuntimeError('native data changed since previous export audit')
    if not manifest.exists():
        manifest.write_text(json.dumps(native_hashes, indent=2), encoding='utf-8')
    records = [json.loads(line) for line in (directory/'tracking.jsonl').read_text().splitlines()]
    run = json.loads((directory/'run.json').read_text())
    result = summarize_records(records)
    if result['total_frames'] != run['frames']:
        raise ValueError('incomplete native frame accounting')
    result.update(run=directory.name, native=run, ground_truth_usage='none',
                  processing_fps=len(records) / run['processing_wall_s'], native_sha256=native_hashes)
    if run['pace_hz'] > 0:
        # Nominal file-replay schedule only, NEVER physical acquisition latency.
        lateness = [r['wall_elapsed_ms'] - (n+1)*1000/run['pace_hz']
                    for n, r in enumerate(records)]
        result['nominal_schedule_overrun'] = dict(
            late_frames=sum(t > 0 for t in lateness),
            p95_ms=float(np.percentile(lateness, 95)), max_ms=float(max(lateness)),
            measurement='file replay finish minus nominal scheduled deadline; not live latency')
    log = directory.parent / (directory.name + '.log')
    if log.exists():
        text = log.read_text(encoding='utf-8', errors='replace')
        result['reset_log_occurrences'] = dict(active=text.count('Active map Reseting'),
                                               system=text.count('System Reseting'))
    result['active_map_resets'] = result['system_resets'] = None
    result['reset_count_source'] = 'unavailable: native verbosity can suppress reset messages'
    if (directory/'diagnostics.jsonl').exists():
        events = [json.loads(line) for line in (directory/'diagnostics.jsonl').read_text().splitlines()]
        # Older instrumented logs did not cover resets; absence is not zero.
        if run.get('diagnostic_schema', 0) >= 2:
            result['active_map_resets'] = sum(e['event']=='tracking_reset_active' for e in events)
            result['system_resets'] = sum(e['event']=='tracking_reset_all' for e in events)
            result['reset_count_source'] = 'instrumented_tracking_entry_events'
    with (directory/'sparse_snapshot.jsonl').open(encoding='utf-8') as points:
        result['sparse_snapshot_points'] = sum(1 for _ in points)
    result['asynchronous_export_is_quiescent'] = False
    if session is not None:
        # This is the first and only read of truth in the new pipeline, AFTER
        # completion and native hashes. Each continuous segment is scored alone.
        session = Path(session)
        truth = {p['i']: np.array(p['loc'], dtype=float) * [1., -1., 1.] / 100
                 for p in map(json.loads, (session/'poses.jsonl').read_text().splitlines())}
        result.update(session=session.name, truth_frames=len(truth),
                      ground_truth_usage='post-native-export evaluation only')
        result['valid_of_truth'] = f"{result['valid_frames']}/{len(truth)}"
        for segment in result['segments']:
            subset = [r for r in records if r['valid'] and r['map_id'] == segment['map_id']
                      and segment['first_frame_id'] <= r['frame_id'] <= segment['last_frame_id']]
            paired = [r for r in subset if r['frame_id'] in truth]
            segment['paired_truth_frames'] = len(paired)
            segment['coverage_of_truth'] = len(paired) / len(truth)
            try:
                segment.update(alignment_metrics(
                    [np.array(r['Twc']).reshape(4, 4)[:3, 3] for r in paired],
                    [truth[r['frame_id']] for r in paired]))
            except ValueError as error:
                segment['evaluation_unavailable'] = str(error)
        if len(result['segments']) == 1:
            for key in ('rigid_ate_rmse_m', 'sim3_ate_rmse_m', 'sim3_scale'):
                result[key] = result['segments'][0].get(key)
    if native_hashes != hashes(directory):
        raise RuntimeError('native data changed during evaluation')
    (directory/'orbslam3_eval.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--session', type=Path)
    args = parser.parse_args()
    print(json.dumps(report(args.directory, args.session), indent=2))
