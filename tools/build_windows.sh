#!/bin/zsh
# สร้างรุ่น Windows (64-bit x64) บน Mac -> ~/Desktop/BirdNET-eBird-win.zip
# โค้ด app/ และโมเดล data/birdnet ใช้ร่วมกับรุ่น mac; Python, แพ็กเกจ และ ffmpeg เป็นของ Windows
# ต้องมี uv; ครั้งแรกดาวน์โหลด ~300 MB (Python, ffmpeg เก็บใน build/windows-cache; แพ็กเกจใน cache ของ uv)
# ไม่ใส่ data/settings.json (API key ของคุณ) และ cache
set -euo pipefail
program="$(cd "$(dirname "$0")/.." && pwd)"
cache="$program/build/windows-cache"
stage_root="$program/build/windows"
stage="$stage_root/BirdNET-eBird-win"
out="${1:-$HOME/Desktop/BirdNET-eBird-win.zip}"

py_tag=20260807
py_file="cpython-3.12.13+$py_tag-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
py_base="https://github.com/astral-sh/python-build-standalone/releases/download/$py_tag"
py_mirror="https://releases.astral.sh/github/python-build-standalone/releases/download/$py_tag"   # mirror ของ uv (เร็วกว่า)
ff_file="ffmpeg-n9.0-latest-win64-lgpl-shared-9.0.zip"            # LGPL เหมือนรุ่น mac, มี ffplay
ff_base="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"

fetch() {   # fetch <url> <file in cache>: ต่อจากที่ค้าง; ช้าจนแทบหยุด (GitHub บางเครือข่าย) ตัดแล้วต่อใหม่
    [[ -s "$cache/$2" ]] && return
    echo "Downloading $2 ..."
    local attempt
    for attempt in {1..40}; do
        if curl -fsSL --retry 3 -C - --speed-limit 20000 --speed-time 30 -o "$cache/$2.part" "$1"; then
            mv "$cache/$2.part" "$cache/$2"
            return
        fi
        echo "  slow or interrupted; resuming ($attempt, $(du -h "$cache/$2.part" 2>/dev/null | cut -f1) so far)"
        sleep 3
    done
    echo "Download failed: $1" >&2
    exit 1
}

verify() {  # verify <sums file> <file>: sha256 ต้องตรงกับที่ผู้สร้างประกาศ
    local expected actual
    expected="$(awk -v f="$2" '{n = $2; sub(/^\*/, "", n); if (n == f) {print $1; exit}}' "$cache/$1")"
    actual="$(shasum -a 256 "$cache/$2" | awk '{print $1}')"
    if [[ -z "$expected" || "$expected" != "$actual" ]]; then
        echo "Checksum mismatch for $2; deleted, run again" >&2
        rm -f "$cache/$2" "$cache/$1"
        exit 1
    fi
}

mkdir -p "$cache"
fetch "$py_mirror/${py_file//+/%2B}" "$py_file"
fetch "$py_base/SHA256SUMS" "python-$py_tag-SHA256SUMS"
verify "python-$py_tag-SHA256SUMS" "$py_file"
fetch "$ff_base/$ff_file" "$ff_file"
fetch "$ff_base/checksums.sha256" "ffmpeg-checksums.sha256"       # ไฟล์ latest เปลี่ยนได้: ไม่ตรง = โหลดใหม่ทั้งคู่
verify "ffmpeg-checksums.sha256" "$ff_file"

echo "Assembling $stage ..."
rm -rf "$stage_root"
mkdir -p "$stage"
tar -xzf "$cache/$py_file" -C "$stage"                          # -> python\python.exe, pythonw.exe, Lib\ ...

UV_CONCURRENT_DOWNLOADS=3 UV_HTTP_RETRIES=5 UV_HTTP_TIMEOUT=120 \
uv pip install --quiet --target "$stage/python/Lib/site-packages" \
    --python-platform x86_64-pc-windows-msvc --python-version 3.12 \
    --only-binary :all: --no-deps -r "$program/tools/windows/requirements-windows.txt"
rm -rf "$stage/python/Lib/site-packages/bin"                    # ตัวเรียกคำสั่งของ mac ไม่ใช้

# VC++ runtime ของ Microsoft (onnxruntime ต้องการ msvcp140_1, LiteRT ต้องการ vcruntime140_threads)
# วางข้าง python.exe (app-local ตาม REDIST list ของ Visual Studio) เพื่อนไม่ต้องติดตั้ง VC++ Redistributable
UV_CONCURRENT_DOWNLOADS=3 UV_HTTP_RETRIES=5 \
uv pip install --quiet --target "$stage_root/msvc-tmp" --python-platform x86_64-pc-windows-msvc \
    --python-version 3.12 --only-binary :all: --no-deps msvc-runtime==14.44.35112
cp "$stage_root"/msvc-tmp/Scripts/*.dll "$stage/python/"
cp "$stage_root"/msvc-tmp/msvc_runtime-*.dist-info/licenses/LICENSE "$stage/python/MSVC-RUNTIME-LICENSE.txt"
rm -rf "$stage_root/msvc-tmp"

unzip -q "$cache/$ff_file" -d "$stage_root/ffmpeg-tmp"
mkdir -p "$stage/bin"
cp "$stage_root"/ffmpeg-tmp/*/bin/*.exe "$stage_root"/ffmpeg-tmp/*/bin/*.dll "$stage/bin/"
cp "$stage_root"/ffmpeg-tmp/*/LICENSE.txt "$stage/bin/FFMPEG-LICENSE.txt"
rm -rf "$stage_root/ffmpeg-tmp"

rsync -a --exclude "__pycache__" --exclude ".DS_Store" --exclude "AppIcon.icns" "$program/app/" "$stage/app/"
mkdir -p "$stage/data" "$stage/tools"
if [[ -d "$program/data/birdnet" ]]; then
    rsync -a --exclude "*.lock" "$program/data/birdnet/" "$stage/data/birdnet/"
else
    echo "Note: no data/birdnet on this Mac; the Windows app downloads the BirdNET 3.0 model on first use"
fi
cp "$program/tools/windows/BirdNET eBird.bat" "$program/tools/windows/Create desktop shortcut.bat" "$stage/"
cp "$program/tools/windows/create_shortcut.ps1" "$stage/tools/"
cp "$program/tools/windows/README-Windows.txt" "$program/README.md" "$stage/"
cp "$program/MODEL_CREDITS.txt" "$stage/"
cp -R "$program/LICENSES" "$stage/"

echo "Checking DLL dependencies ..."
uv run --quiet --no-project --with pefile python "$program/tools/windows/check_dlls.py" "$stage"

# zip ด้วย Python: ชื่อไฟล์เป็น UTF-8 ที่ Windows อ่านถูก; กัน settings.json หลุดเข้าไป
python3 - "$stage" "$out" <<'EOF'
import os, sys, zipfile
from pathlib import Path
stage, out = Path(sys.argv[1]), Path(sys.argv[2])
files = sorted(p for p in stage.rglob("*") if p.is_file() and "__pycache__" not in p.parts
               and p.name != ".DS_Store")
assert not any(p.name == "settings.json" for p in files), "settings.json must not be shipped"
longest = max(len(str(p.relative_to(stage.parent))) for p in files)
out.unlink(missing_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in files:
        archive.write(path, path.relative_to(stage.parent))
print(f"Created {out} ({out.stat().st_size / 1e6:.0f} MB, {len(files)} files, longest path {longest} chars)")
EOF
rm -rf "$stage_root"                                              # เหลือแค่ zip (~1.2 GB ที่พักไฟล์)
echo "API keys not included. Test it on Windows (see README-Windows.txt)."
