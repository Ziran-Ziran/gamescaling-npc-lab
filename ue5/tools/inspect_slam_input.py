"""Read-only SLAM intake audit. Never opens pose/landmark truth files.

Supports image sequences, videos and map-only assets. Readiness means an input
contract check, not successful SLAM or measured end-to-end realtime operation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

import cv2
import numpy as np

IMAGES = {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'}
VIDEOS = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.m4v'}
MAPS = {'.ply', '.obj', '.glb', '.gltf', '.fbx', '.pcd', '.las', '.laz'}
OBSERVED_CLOCKS = {'sensor_capture_monotonic', 'observed_sensor_monotonic'}
FORBIDDEN = {'poses.jsonl', 'landmarks.json', 'landmarks.jsonl'}


def linked(path):
    path = Path(path).absolute()
    return any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
               for p in (path, *path.parents))


def blocked(error):
    return dict(kind='unverified', recommended_route='resolve_input_errors', errors=[error], warnings=[],
                offline_ready=False, live_timing_ready=False, ground_truth_usage='none')


def read_json(path):
    if path.name.lower() in FORBIDDEN or linked(path):
        raise ValueError('truth files are not sensor metadata')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def metadata(root, calibration_path):
    candidates = [Path(calibration_path)] if calibration_path else [root/'sensor.json', root/'meta.json']
    for path in candidates:
        if path.exists():
            data = read_json(path)
            if not isinstance(data, dict) or not isinstance(data.get('calibration', data), dict):
                raise ValueError('sensor metadata must contain a calibration object')
            # Never forward complete metadata, which may contain start_pose.
            result = {key: data.get(key) for key in ('target_hz', 'time_source', 'calibration_constant')}
            result['calibration'] = {key: data.get('calibration', data).get(key)
                                     for key in ('fx', 'fy', 'cx', 'cy', 'w', 'h')}
            depth = data.get('depth', {})
            if not isinstance(depth, dict):
                raise ValueError('depth metadata must be an object')
            result['depth_unit'] = data.get('depth_unit', depth.get('unit'))
            result['depth_meaning'] = data.get('depth_meaning', depth.get('meaning'))
            result['metadata_file'] = path.name
            return result
    return {}


def calibration_check(config, width, height, errors):
    k = config.get('calibration', {})
    values = [k.get(key) for key in ('fx', 'fy', 'cx', 'cy', 'w', 'h')]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and np.isfinite(v) for v in values):
        errors.append('missing_independent_calibration')
        return None
    if (k['fx'] <= 0 or k['fy'] <= 0 or k['w'] != width or k['h'] != height
            or k['w'] <= 0 or k['h'] <= 0 or not 0 <= k['cx'] <= width or not 0 <= k['cy'] <= height):
        errors.append('invalid_or_mismatched_calibration')
    if config.get('calibration_constant') is False:
        errors.append('changing_calibration_requires_per_frame_adapter')
    return k


def timestamps(root, ids, config, explicit_path, errors, warnings):
    path = Path(explicit_path) if explicit_path else root/'timestamps.jsonl'
    hz = config.get('target_hz')
    if hz is not None and (not isinstance(hz, (int,float)) or isinstance(hz, bool) or not np.isfinite(hz) or hz <= 0):
        errors.append('invalid_nominal_frequency')
        hz = None
    declared = config.get('time_source')
    if declared is not None and not isinstance(declared, str):
        errors.append('invalid_timestamp_clock_declaration')
        declared = None
    if path.name.lower() in FORBIDDEN:
        raise ValueError('truth files cannot supply sensor timestamps')
    if linked(path) or linked(root/'frames.tsv'):
        errors.append('unsafe_timestamp_path')
        return dict(source='unsafe_path', declared_clock=declared, nominal_hz=hz, observed_manifest=False)
    values, frame_ids = [], []
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError('timestamp row must be an object')
            except (ValueError, TypeError):
                errors.append('invalid_sensor_timestamp_manifest')
                continue
            i, t = row.get('frame_id'), row.get('t_sensor_s')
            if (not isinstance(i, int) or isinstance(i, bool) or i < 0
                    or not isinstance(t, (int,float)) or isinstance(t, bool) or not np.isfinite(t)):
                errors.append('invalid_sensor_timestamp_row')
                continue
            frame_ids.append(i)
            values.append(float(t))
        if frame_ids != sorted(set(frame_ids)):
            errors.append('duplicate_or_unordered_timestamp_frame_ids')
        if frame_ids != ids:
            errors.append('timestamp_frame_set_mismatch')
        if any(b <= a for a,b in zip(values, values[1:])):
            errors.append('timestamps_not_strictly_increasing')
        observed = declared in OBSERVED_CLOCKS
        source = 'independent_sensor_manifest' if observed else 'independent_manifest_nominal_or_unknown_clock'
        if not observed:
            warnings.append('timestamp_clock_is_nominal_or_undeclared')
    elif (root/'frames.tsv').exists():
        # Existing prepared inputs explicitly declare a nominal clock.
        for line in (root/'frames.tsv').read_text().splitlines():
            fields = line.split()
            if len(fields) != 4:
                errors.append('invalid_frames_tsv')
                continue
            try:
                frame_ids.append(int(fields[0])); values.append(float(fields[1]))
            except ValueError:
                errors.append('invalid_frames_tsv')
        if frame_ids != ids or not all(np.isfinite(t) for t in values):
            errors.append('invalid_frames_tsv')
        if any(b <= a for a,b in zip(values, values[1:])):
            errors.append('timestamps_not_strictly_increasing')
        observed = False
        source = 'frames_tsv_nominal_or_unverified'
        warnings.append('no_observed_sensor_timestamps')
    elif isinstance(hz, (int,float)) and not isinstance(hz, bool) and np.isfinite(hz) and hz > 0:
        observed = False
        source = 'nominal_frequency_only'
        warnings.append('no_observed_sensor_timestamps')
    else:
        observed = False
        source = 'missing'
        errors.append('missing_independent_timestamps_or_explicit_nominal_frequency')
    result = dict(source=source, declared_clock=declared, nominal_hz=hz, observed_manifest=observed,
                  provenance='declaration checked, physical clock synchronization not independently verified')
    if len(values) > 1:
        intervals = np.diff(values)
        result.update(interval_ms_min=float(intervals.min()*1000), interval_ms_median=float(np.median(intervals)*1000),
                      interval_ms_max=float(intervals.max()*1000))
    return result


def image_sequence(root, config, timestamps_path, max_frames):
    directory = root/'frames' if (root/'frames').is_dir() else root
    images = sorted(p for p in directory.iterdir() if p.suffix.lower() in IMAGES and p.is_file())
    errors, warnings = [], []
    rows = []
    for path in images:
        match = re.search(r'(\d+)$', path.stem)
        if match is None:
            errors.append('missing_numeric_frame_ids')
            continue
        if path.is_symlink():
            errors.append('sensor_symlinks_not_allowed')
            continue
        rows.append((int(match[1]), path))
    rows.sort()
    ids = [i for i,_ in rows]
    if ids != sorted(set(ids)):
        errors.append('duplicate_image_frame_ids')
    if not rows:
        errors.append('no_image_sequence')
    depth_dir = root/'depth'
    depths = {p.name:p for p in depth_dir.iterdir() if p.suffix.lower() in IMAGES and p.is_file()} if depth_dir.is_dir() else {}
    rgbd = depth_dir.exists()
    if rgbd and {p.name for _,p in rows} != depths.keys():
        errors.append('rgb_depth_pair_mismatch')
    if rgbd:
        valid_meanings = {'optical_axis', 'SCS_SceneDepth red channel, metric depth along the camera axis'}
        if config.get('depth_unit') != 'millimetres' or config.get('depth_meaning') not in valid_meanings:
            errors.append('unsupported_or_missing_depth_contract')
    count = min(len(rows), max_frames) if max_frames else len(rows)
    if count < len(rows):
        warnings.append('partial_image_inspection')
    shape, decoded, duplicates, previous_hash = None, 0, 0, None
    valid_depth_pixels, depth_pixels = 0, 0
    for _, path in rows[:count]:
        rgb = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if rgb is None:
            errors.append('unreadable_rgb')
            continue
        if rgb.dtype != np.uint8 or rgb.ndim not in (2,3) or (rgb.ndim == 3 and rgb.shape[2] not in (3,4)):
            errors.append('unsupported_rgb_type')
            continue
        if rgbd and (rgb.ndim != 3 or rgb.shape[2] != 3):
            errors.append('rgbd_requires_bgr_conversion')
        decoded += 1
        current = (rgb.shape[1], rgb.shape[0])
        if shape is None:
            shape = current
        elif current != shape:
            errors.append('image_dimensions_changed')
        digest = hashlib.sha256(rgb.tobytes()).digest()
        duplicates += digest == previous_hash
        previous_hash = digest
        if rgbd and path.name in depths:
            depth_path = depths[path.name]
            if depth_path.is_symlink():
                errors.append('sensor_symlinks_not_allowed')
                continue
            depth = cv2.imread(str(depth_path), cv2.IMREAD_UNCHANGED)
            if depth is None:
                errors.append('unreadable_depth')
            elif depth.dtype != np.uint16 or depth.ndim != 2:
                errors.append('unsupported_depth_type')
            elif depth.shape != rgb.shape[:2]:
                errors.append('rgb_depth_dimensions_mismatch')
            else:
                valid_depth_pixels += int(np.count_nonzero((depth>0) & (depth<65535)))
                depth_pixels += depth.size
    width, height = shape if shape else (0,0)
    k = calibration_check(config, width, height, errors)
    times = timestamps(root, ids, config, timestamps_path, errors, warnings)
    if duplicates:
        warnings.append('consecutive_identical_frames_present')
    if rgbd and depth_pixels and valid_depth_pixels == 0:
        errors.append('all_depth_pixels_invalid')
    if any(b != a+1 for a,b in zip(ids, ids[1:])):
        warnings.append('frame_id_gaps_present')
    if len(rows) < 2:
        errors.append('insufficient_frames_for_motion')
    complete = count == len(rows)
    return dict(kind='rgbd' if rgbd else 'rgb', recommended_route='rgbd_slam' if rgbd else 'monocular_slam',
                errors=sorted(set(errors)), warnings=sorted(set(warnings)), frames_found=len(images), frames_checked=count,
                frames_decoded=decoded, full_inspection=complete, width=width, height=height,
                calibration=k, timestamps=times, consecutive_identical_pairs=duplicates,
                valid_depth_fraction=valid_depth_pixels/depth_pixels if depth_pixels else None,
                offline_ready=bool(not errors and complete), live_timing_ready=bool(not errors and complete and times['observed_manifest']),
                runtime_note='contract validation only; RGB requires a separate monocular adapter; no SLAM execution or depth inference')


def video(path, config, timestamps_path, max_frames):
    errors, warnings = [], ['video_container_timing_is_not_sensor_clock', 'monocular_adapter_required']
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            return dict(kind='video', recommended_route='monocular_slam', errors=['unreadable_video'], warnings=warnings,
                        offline_ready=False, live_timing_ready=False)
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        declared = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        count, width, height, eof = 0, 0, 0, False
        while not max_frames or count < max_frames:
            ok, frame = capture.read()
            if not ok:
                eof = True
                break
            if count and frame.shape[:2] != (height,width):
                errors.append('image_dimensions_changed')
            height,width = frame.shape[:2]
            count += 1
        complete = eof or (declared > 0 and declared == count)
        if not complete:
            warnings.append('partial_video_inspection')
        if count < 2:
            errors.append('insufficient_frames_for_motion')
        if eof and declared > count:
            errors.append('video_decode_ended_before_declared_frame_count')
        # Container FPS may support nominal offline playback, never live latency.
        local = dict(config)
        if not local.get('target_hz') and np.isfinite(fps) and fps > 0:
            local['target_hz'] = fps
        k = calibration_check(config, width, height, errors)
        # A partial inspection cannot verify an entire independent frame manifest.
        times = timestamps(path.parent, list(range(declared if declared > 0 else count)), local,
                           timestamps_path, errors, warnings)
        return dict(kind='video', recommended_route='monocular_slam', errors=sorted(set(errors)), warnings=sorted(set(warnings)),
                    frames_checked=count, declared_frames=declared, container_fps=fps if np.isfinite(fps) else None,
                    width=width, height=height, full_inspection=complete, calibration=k, timestamps=times,
                    offline_ready=bool(not errors and complete), live_timing_ready=bool(not errors and complete and times['observed_manifest']),
                    runtime_note='video is monocular input; depth and metric scale are not inferred; extraction/SLAM are not performed')
    finally:
        capture.release()


def inspect(path, calibration_path=None, timestamps_path=None, max_frames=300):
    path = Path(path)
    if max_frames < 0:
        raise ValueError('max_frames must be nonnegative (0 means all)')
    if not path.exists():
        raise FileNotFoundError(path)
    if linked(path) or (path.is_dir() and (linked(path/'frames') or linked(path/'depth'))):
        return blocked('unsafe_sensor_path')
    root = path if path.is_dir() else path.parent
    if path.is_file() and path.suffix.lower() in MAPS:
        return dict(kind='map', recommended_route='map_localization_requires_images_and_map_format_adapter',
                    errors=['map_alone_has_no_temporal_image_sequence'], warnings=[], offline_ready=False,
                    live_timing_ready=False, file_bytes=path.stat().st_size, geometry_parsed=False,
                    ground_truth_usage='none')
    metadata_error = False
    try:
        config = metadata(root, calibration_path)
    except (ValueError, TypeError):
        config = {}
        metadata_error = True
    if path.is_dir():
        result = image_sequence(path, config, timestamps_path, max_frames)
    elif path.suffix.lower() in VIDEOS:
        result = video(path, config, timestamps_path, max_frames)
    else:
        raise ValueError('provide an image sequence directory, video, or supported map file')
    if metadata_error:
        result['errors'].append('invalid_sensor_metadata')
        result['offline_ready'] = result['live_timing_ready'] = False
    result['ground_truth_usage'] = 'none'
    result['readiness_scope'] = 'input contract only; no tracking/accuracy/realtime acceptance'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--calibration', type=Path)
    parser.add_argument('--timestamps', type=Path)
    parser.add_argument('--max-frames', type=int, default=300, help='decode at most N frames; 0 checks all')
    parser.add_argument('--out', type=Path, help='NEW report file; source data are never modified')
    args = parser.parse_args()
    if args.out and args.out.exists():
        raise FileExistsError(args.out)
    result = inspect(args.path, args.calibration, args.timestamps, args.max_frames)
    payload = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)
    if args.out:
        with args.out.open('x', encoding='utf-8') as output:
            output.write(payload+'\n')
    print(payload)
    return 0 if result['offline_ready'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
