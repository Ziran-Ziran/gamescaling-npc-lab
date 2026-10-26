"""Collect every finished evaluation into one comparison table.

    python ue5/tools/summarize.py

Reads every eval.json under output/ue5-eval/ -- both the `colmap-<session>` runs
(features, matching and mapping together) and the `map-<session>-<arm>` runs (only
the mapper, on a filtered match graph) -- and prints a markdown table plus a JSON
copy, so the numbers quoted in documents come from files rather than memory.

The `arm` column matters: the same session appears several times with different
match graphs, and comparing them is the whole point. Numbers are only ever attached
to the directory they were written in, never matched across arms by session name.
"""

import json
from pathlib import Path

COLUMNS = [
    ("session", "会话"),
    ("arm", "匹配图"),
    ("registered_of_truth", "注册/真值"),
    ("coverage", "覆盖率"),
    ("ate_rmse_m", "ATE RMSE (m)"),
    ("ate_relative_percent", "相对误差 (%)"),
    ("points3d", "三维点"),
    ("label_recall", "标牌重建率"),
    ("label_plane_median_m", "地图误差中位 (m)"),
    ("depth_ratio", "深度/轴距"),
    ("vo_ate_m", "VO ATE (m)"),
    ("vo_rigid_ate_m", "VO 米制 ATE (m)"),
]

SIDECARS = [
    ("landmark_eval.json", "label_recall"),
    ("depth_points_check.json", "depth_ratio"),
    ("vo_eval.json", "vo_ate_m"),
]


def sidecar(directory, name):
    for candidate in (directory / name, directory / "best" / name):
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def enrich(row, directory, session):
    landmarks = sidecar(directory, "landmark_eval.json")
    if landmarks:
        row["label_recall"] = landmarks.get("label_recall")
        row["label_plane_median_m"] = landmarks.get(
            "perpendicular_to_label_plane_m", {}).get("median")

    depth = sidecar(directory, "depth_points_check.json")
    if depth:
        row["depth_ratio"] = depth.get("measured_over_axis_median")

    vo = sidecar(directory, "vo_eval.json")
    if vo:
        row["vo_ate_m"] = vo.get("ate_rmse_m")
        row["vo_rigid_ate_m"] = vo.get("ate_rigid_rmse_m")
    return row


def arm_of(directory):
    """Describe the matching graph a run used, from its directory name."""
    name = directory.name
    if name.startswith("map-"):
        # map-<session>-<arm>; the session itself may contain dashes. A mapper run may
        # record a clearer label of its own.
        summary = directory / "summary.json"
        if summary.exists():
            label = json.loads(summary.read_text(encoding="utf-8")).get("arm")
            if label:
                return label
        return name.rsplit("-", 1)[-1]
    if name.startswith("colmap-"):
        # Older runs predate the "matching" field of summary.json; the field was
        # backfilled from the database itself (exhaustive writes a row for every pair).
        summary = directory / "summary.json"
        if summary.exists():
            matching = json.loads(summary.read_text(encoding="utf-8")).get("matching")
            if matching:
                return matching
        return "colmap (未记录)"
    return name


def main() -> int:
    root = Path("output/ue5-eval")
    candidates = []
    for pattern in ("colmap-*/eval.json", "map-*/eval.json"):
        candidates += sorted(root.glob(pattern))

    rows = []
    for eval_path in candidates:
        data = json.loads(eval_path.read_text(encoding="utf-8"))
        directory = eval_path.parent
        row = dict(data)
        row["arm"] = arm_of(directory)
        row["eval_path"] = str(eval_path)
        rows.append(enrich(row, directory, data.get("session")))

    if not rows:
        print("no eval.json files yet; run run_colmap.py and evaluate_slam.py first")
        return 1

    order = {"city-04": 0, "city-02": 1, "city-03": 2, "city-lap2": 3}
    rows.sort(key=lambda row: (order.get(row.get("session"), 99), row.get("arm", "")))

    header = "| " + " | ".join(label for _, label in COLUMNS) + " |"
    divider = "|" + "|".join("---" for _ in COLUMNS) + "|"
    lines = [header, divider]
    for row in rows:
        cells = []
        for key, _ in COLUMNS:
            value = row.get(key, "")
            if value is None:
                value = "-"
            if isinstance(value, float):
                value = f"{value:.4g}"
            cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")

    table = "\n".join(lines)
    print(table)

    dense = dense_table(root)
    payload = table + "\n"
    if dense:
        payload += "\n## 稠密地图（深度融合）\n\n" + dense + "\n"
        payload += ("\ncolmap 的厚度在原生米制地图上计算；标牌评分在融合完成后做 Sim(3) 对齐，"
                    "米制刚体对齐结果另见 dense_eval.json。真值融合行是算法上限对照。\n"
                    "同帧 gt 对照只使用 COLMAP 实际融合的帧；旧版 VO 的真值对齐发生在融合前，已单独标注。\n")
        print("\n" + dense)

    orb = orbslam3_table(root)
    if orb:
        payload += "\n## ORB-SLAM3 因果 RGB-D 回放\n\n" + orb + "\n"
        payload += ("\n时间为帧号/名义频率，离线节流不代表 UE 端到端延迟。"
                    "ATE 为原生即时 Twc 落盘后按连续段评分；多图/多段不拼接，详见各运行 orbslam3_eval.json。"
                    "地图为异步稀疏快照，不代表重载定位验收。内部诊断行的开销和调度会改变，不能作为实时性能成绩；"
                    "无重置入口事件时重置次数为未知，文字日志无消息不能当成零次。\n")
        print("\n" + orb)

    out = root / "comparison.md"
    out.write_text(payload, encoding="utf-8")
    (root / "comparison.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\n{len(rows)} runs written to {out}")
    return 0


DENSE_COLUMNS = [
    ("session", "会话"),
    ("run", "运行"),
    ("pose_source", "位姿来源"),
    ("frames", "融合帧/真值"),
    ("coverage", "覆盖率"),
    ("voxel_m", "体素边长 (m)"),
    ("voxels", "体素数"),
    ("observations_per_voxel", "每体素观测"),
    ("thickness", "表面厚度中位 (m)"),
    ("label_recall", "标牌命中 (0.5 m)"),
    ("plane", "标牌平面误差中位 (m)"),
    ("alignment", "位姿对齐 RMSE (m)"),
    ("truth_usage", "真值使用范围"),
]


def dense_table(root):
    """Dense maps are scored by their own tool, so they get their own table."""
    rows = []
    paths = sorted(root.glob("dense-*/dense_eval.json"))
    control = root / 'regression/city-lap2-gt-matched/dense_eval.json'
    if control.exists():
        paths.append(control)
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        frames = data.get('frames_of_truth')
        coverage = data.get('coverage')
        if frames is None:
            # Historical reports predate coverage fields. Only backfill counts,
            # never infer accuracy or associate results across matching arms.
            truth_path = Path('output/ue5-datasets') / data['session'] / 'poses.jsonl'
            if truth_path.exists():
                total = sum(bool(line.strip()) for line in truth_path.read_text(encoding='utf-8').splitlines())
                frames = f"{data['frames_used']}/{total}"
                coverage = round(data['frames_used'] / total, 4) if total else None
        usage = data.get('ground_truth_usage')
        if usage is None:
            usage = 'oracle fusion reference' if data.get('pose_source') == 'gt' else 'legacy: truth alignment before fusion'
        usage = {
            'oracle fusion reference': '真值融合上限',
            'post-fusion evaluation only': '仅融合后评测',
            'legacy: truth alignment before fusion': '旧版：融合前真值对齐',
        }.get(usage, usage)
        matched = data.get('matched_frame_control')
        matched_source = matched.get('pose_source', 'colmap') if isinstance(matched, dict) else 'colmap'
        rows.append({
            "session": data.get("session"),
            "run": path.parent.name.removeprefix('dense-' + str(data.get('session')) + '-'),
            "pose_source": (f'gt（{matched_source.upper()} 同帧）' if matched
                            else data.get("pose_source")),
            "voxels": data.get("voxels"),
            "voxel_m": data.get('voxel_m'),
            "frames": frames,
            "coverage": coverage,
            "truth_usage": usage,
            "observations_per_voxel": data.get("observations_per_voxel"),
            "thickness": (data.get("surface_thickness_m") or {}).get("median"),
            "label_recall": data.get("label_recall_0_5_m"),
            "plane": (data.get("perpendicular_to_label_plane_m") or {}).get("median"),
            "alignment": (data.get("pose_alignment") or {}).get("rmse_m"),
        })
    if not rows:
        return ""
    rows.sort(key=lambda row: (row["session"] or "", row["pose_source"] or ""))
    header = "| " + " | ".join(label for _, label in DENSE_COLUMNS) + " |"
    divider = "|" + "|".join("---" for _ in DENSE_COLUMNS) + "|"
    lines = [header, divider]
    for row in rows:
        cells = []
        for key, _ in DENSE_COLUMNS:
            value = row.get(key, "")
            if value is None:
                value = "-"
            cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def orbslam3_table(root):
    columns = [('session', '会话'), ('run', '运行'), ('pace', '节流 Hz'),
               ('frames', '有效/输入'), ('coverage', '覆盖率'), ('maps', '图数'),
               ('segments', '连续段'), ('longest_invalid_run', '最长失效帧段'),
               ('resets', '活动图/全局重置'), ('p50', 'tracking p50 ms'),
               ('p95', 'tracking p95 ms'), ('processing_fps', '含读图吞吐 FPS'),
               ('rigid', '刚体 ATE m'), ('sim3', 'Sim(3) ATE m'),
               ('sparse_snapshot_points', '稀疏快照点数')]
    rows = []
    for path in sorted(root.glob('orbslam3-*/replay-*/orbslam3_eval.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        row = dict(data)
        row['session'] = data.get('session') or '独立输入（未评分）'
        row.update(run=path.parent.parent.name + '/' + path.parent.name,
                   pace=data['native']['pace_hz'],
                   frames=f"{data['valid_frames']}/{data['total_frames']}",
                   maps=len(data['observed_map_ids']), segments=len(data['segments']),
                   resets=(f"{data['active_map_resets']}/{data['system_resets']}"
                           if data.get('active_map_resets') is not None and data.get('system_resets') is not None else '未知'),
                   p50=data['timing']['tracking_ms']['p50'],
                   p95=data['timing']['tracking_ms']['p95'],
                   rigid=data.get('rigid_ate_rmse_m', '逐段见报告'),
                   sim3=data.get('sim3_ate_rmse_m', '逐段见报告'))
        if data['native'].get('instrumented'):
            row['run'] += '（内部诊断）'
        if data.get('ground_truth_usage') == 'none':
            row['rigid'] = row['sim3'] = '未做真值评分'
        rows.append(row)
    if not rows:
        return ''
    lines = ['| ' + ' | '.join(label for _, label in columns) + ' |',
             '|' + '|'.join('---' for _ in columns) + '|']
    for row in rows:
        values = [row.get(key, '-') for key, _ in columns]
        lines.append('| ' + ' | '.join(f'{v:.4g}' if isinstance(v, float) else str(v)
                                       for v in values) + ' |')
    return '\n'.join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
