#!/usr/bin/env bash
# Read-only inspection of the existing install; backups go only in a new folder.
set -euo pipefail
root=$(realpath "$1")
out=$(realpath "$2")
mkdir "$out/preserved"
hostname > "$out/preserved/hostname.txt"
cat /etc/os-release > "$out/preserved/os-release.txt"
id -un > "$out/preserved/user.txt"
pwd > "$out/preserved/cwd.txt"
find "$root/include" "$root/src" "$root/Thirdparty/Sophus" -type f -print0 |
  sort -z | xargs -0 sha256sum > "$out/preserved/source-sha256.txt"
sha256sum "$root/lib/libORB_SLAM3.so" "$root/Examples/RGB-D/rgbd_tum" \
  "$root/Vocabulary/ORBvoc.txt" "$root/CMakeLists.txt" \
  "$root/build/CMakeCache.txt" > "$out/preserved/artifact-sha256.txt"
cp -p "$root/lib/libORB_SLAM3.so" "$root/Examples/RGB-D/rgbd_tum" \
  "$root/build/CMakeCache.txt" "$out/preserved/"
tar -czf "$out/preserved/orb-source.tar.gz" -C "$root" include src CMakeLists.txt
ldd "$root/Examples/RGB-D/rgbd_tum" > "$out/preserved/ldd.txt"
if grep -q 'not found' "$out/preserved/ldd.txt"; then exit 2; fi
set +e
"$root/Examples/RGB-D/rgbd_tum" > "$out/preserved/usage.txt" 2>&1
code=$?
set -e
echo "$code" > "$out/preserved/usage-exit-code.txt"
test "$code" -eq 1
echo 'Source has no .git; upstream commit unknown; no files modified.' > "$out/preserved/provenance.txt"
