"""Prepare a new, truth-free RGB-D input directory for the native runner.

Only this preprocessing step reads capture metadata. No truth file is opened.
Old captures have no independent sensor timestamps: frame_id / target_hz is
explicitly nominal, and cannot establish acquisition or live latency.
"""
import argparse
import json
from pathlib import Path
import re
import shutil

import cv2
import numpy as np


def prepare(session, out):
    session, out = Path(session), Path(out)
    if out.exists():
        raise FileExistsError(out)
    meta = json.loads((session / 'meta.json').read_text(encoding='utf-8'))
    if meta.get('calibration_constant') is not True or meta.get('flip_vertical') is not False:
        raise ValueError('constant independent calibration and unflipped images required')
    k = {name: meta['calibration'][name] for name in ('fx', 'fy', 'cx', 'cy', 'w', 'h')}
    hz = float(meta['target_hz'])
    if (not all(np.isfinite(v) for v in (*k.values(), hz)) or min(k['fx'], k['fy'], hz) <= 0
            or k['w'] != meta['width'] or k['h'] != meta['height']
            or min(k['w'], k['h']) <= 0):
        raise ValueError('invalid calibration or nominal frequency')
    if meta['depth']['unit'] != 'millimetres' or meta['depth']['format'] != 'png16_gray':
        raise ValueError('uint16 millimetre optical-axis depth required')
    frames = {p.name: p for p in (session / 'frames').glob('*.png')}
    depths = {p.name: p for p in (session / 'depth').glob('*.png')}
    if not frames or frames.keys() != depths.keys():
        raise ValueError('RGB/depth pair set mismatch or empty capture')
    entries = []
    for name in frames:
        match = re.fullmatch(r'(?:frame_)?(\d+)\.png', name)
        if not match:
            raise ValueError(f'unrecognized frame name: {name}')
        entries.append((int(match[1]), name))
    entries.sort()
    if len({i for i, _ in entries}) != len(entries):
        raise ValueError('duplicate frame index')
    total_bytes = sum(p.stat().st_size for p in (*frames.values(), *depths.values()))
    if total_bytes > 10 * 1024**3 or shutil.disk_usage(out.parent).free - total_bytes < 40 * 1024**3:
        raise ValueError('input copy exceeds disk budget')
    # Validate every image before creating any output; never guess an image type.
    for _, name in entries:
        rgb = cv2.imread(str(frames[name]), cv2.IMREAD_UNCHANGED)
        depth = cv2.imread(str(depths[name]), cv2.IMREAD_UNCHANGED)
        if (rgb is None or rgb.dtype != np.uint8 or
                rgb.shape not in ((k['h'], k['w'], 3), (k['h'], k['w'], 4))):
            raise ValueError(f'invalid BGR8 image: {name}')
        if depth is None or depth.dtype != np.uint16 or depth.shape != (k['h'], k['w']):
            raise ValueError(f'invalid uint16 depth: {name}')
    out.mkdir()
    (out / 'frames').mkdir()
    (out / 'depth').mkdir()
    for _, name in entries:
        rgb = cv2.imread(str(frames[name]), cv2.IMREAD_UNCHANGED)
        if rgb.shape[2] == 4:
            rgb = cv2.cvtColor(rgb, cv2.COLOR_BGRA2BGR)
        depth = cv2.imread(str(depths[name]), cv2.IMREAD_UNCHANGED)
        depth[depth == 65535] = 0
        if (not cv2.imwrite(str(out / 'frames' / name), rgb) or
                not cv2.imwrite(str(out / 'depth' / name), depth)):
            raise OSError('failed to encode sensor input')
    config = dict(calibration=k, target_hz=hz, time_source='nominal_frame_id_over_target_hz',
                  rgb_order='BGR8 via OpenCV imread', depth_unit='millimetres',
                  depth_meaning='optical_axis', invalid_depth=0,
                  preprocessing='BGRA to BGR, no flip; saturated 65535 mm to invalid zero',
                  frames=len(entries))
    (out / 'sensor.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    (out / 'frames.tsv').write_text(''.join(
        f'{i}\t{i / hz:.9f}\tframes/{name}\tdepth/{name}\n' for i, name in entries), encoding='utf-8')
    settings = ['%YAML:1.0', 'File.version: "1.0"', 'Camera.type: "PinHole"']
    settings += [f'Camera1.{key}: {float(k[key])}' for key in ('fx', 'fy', 'cx', 'cy')]
    settings += [f'Camera1.{key}: 0.0' for key in ('k1', 'k2', 'p1', 'p2', 'k3')]
    settings += [f'Camera.width: {k["w"]}', f'Camera.height: {k["h"]}',
                 f'Camera.fps: {max(1, round(hz))}', 'Camera.RGB: 0', 'Stereo.ThDepth: 40.0',
                 'Stereo.b: 0.1', 'RGBD.DepthMapFactor: 1000.0', 'loopClosing: 0',
                 'ORBextractor.nFeatures: 2000', 'ORBextractor.scaleFactor: 1.2',
                 'ORBextractor.nLevels: 8', 'ORBextractor.iniThFAST: 20',
                 'ORBextractor.minThFAST: 7']
    settings += [f'Viewer.{key}: {value}' for key, value in dict(
        KeyFrameSize=.05, KeyFrameLineWidth=1., GraphLineWidth=.9, PointSize=2.,
        CameraSize=.08, CameraLineWidth=3., ViewpointX=0., ViewpointY=-.7,
        ViewpointZ=-1.8, ViewpointF=500.).items()]
    (out / 'settings.yaml').write_text('\n'.join(settings) + '\n', encoding='utf-8')
    return config


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session', type=Path)
    parser.add_argument('out', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.session, args.out), indent=2))
