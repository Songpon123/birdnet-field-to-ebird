#!/bin/bash
# Double-click launcher. No args = GUI, args = CLI. Keeps the bundle self-contained.
dir="$(cd "$(dirname "$0")" && pwd)"
export PATH="$dir:$dir/python/bin:$PATH"     # so ffmpeg/ffprobe next to me are found
export PYTHONIOENCODING="utf-8"
# drop the download quarantine flag so Gatekeeper stops blocking (first run)
xattr -dr com.apple.quarantine "$dir" 2>/dev/null || true
# ใช้ python3.12 (binary จริง) ไม่ใช่ python3 (symlink ที่พังตอนแตก zip -> ไปรัน python ระบบ)
py="$dir/python/bin/python3.12"
[ -x "$py" ] || py="$dir/python/bin/python3"
exec "$py" "$dir/app/birdnet_app.py" "$@"
