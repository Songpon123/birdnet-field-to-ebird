#!/bin/bash
# build-macos.sh — build a portable macOS bundle (runs on Macs without Python)
# ---------------------------------------------------------------------------
# MUST be run ON a Mac (it downloads a macOS Python + installs macOS wheels).
# Mirrors build-exe.ps1 for Windows, but macOS uses full TensorFlow instead of
# ai-edge-litert (which has no macOS wheel — birdnetlib falls back to TF).
#
# Run:    bash build-macos.sh
# Output: dist/BirdNET-eBird-mac/  -> a double-clickable "BirdNET-eBird.command"
#         Share it as a .zip (see the end of this script).
#
# Needs on the BUILD Mac: curl, tar, and ffmpeg+ffprobe on PATH (brew install ffmpeg).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
bundle="$here/dist/BirdNET-eBird-mac"

# --- pick a relocatable Python 3.12 (python-build-standalone) for this arch ---
arch="$(uname -m)"   # arm64 (Apple Silicon) or x86_64 (Intel)
case "$arch" in
  arm64)  triple="aarch64-apple-darwin" ;;
  x86_64) triple="x86_64-apple-darwin" ;;
  *) echo "Unsupported arch: $arch"; exit 1 ;;
esac
# Pinned python-build-standalone release (override with env if it 404s — see
# https://github.com/astral-sh/python-build-standalone/releases ). install_only
# tarballs extract to a relocatable python/ with bin/python3.
PBS_TAG="${PBS_TAG:-20241016}"
PBS_PY="${PBS_PY:-3.12.7}"
PBS_URL="${PBS_URL:-https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/cpython-${PBS_PY}+${PBS_TAG}-${triple}-install_only.tar.gz}"

echo "=== build BirdNET-eBird macOS bundle ($arch) ==="
rm -rf "$bundle"; mkdir -p "$bundle"

echo "downloading Python: $PBS_URL"
tmp="$(mktemp -d)"
curl -fL "$PBS_URL" -o "$tmp/py.tar.gz"
tar -xzf "$tmp/py.tar.gz" -C "$tmp"          # -> $tmp/python/
mv "$tmp/python" "$bundle/python"
vpy="$bundle/python/bin/python3"

# --- install deps (full TensorFlow via requirements-macos.txt) ---
echo "installing deps into the bundle python ... (first run is slow, TF is large)"
"$vpy" -m pip install --upgrade pip
"$vpy" -m pip install -r "$here/requirements-macos.txt"

# --- app code + shim ---
mkdir -p "$bundle/app/tflite_runtime"
cp "$here/birdnet_app.py" "$here/field_audio_to_ebird.py" "$here/birdnet_gui.py" "$bundle/app/"
cp "$here"/tflite_runtime/*.py "$bundle/app/tflite_runtime/"

# --- ffmpeg + ffprobe (from PATH; brew install ffmpeg) ---
for b in ffmpeg ffprobe; do
  p="$(command -v $b || true)"
  if [ -z "$p" ]; then
    echo "WARNING: $b not found on PATH — install with: brew install ffmpeg"
  else
    cp "$p" "$bundle/$b"
  fi
done

# --- macOS README (Thai/English) with Gatekeeper steps ---
cat > "$bundle/อ่านก่อนใช้.txt" <<'READMEEOF'
============================================================
  BirdNET -> eBird clipper (macOS)     อ่านก่อนใช้ / READ ME FIRST
============================================================

วิธีเปิด (macOS)
------------------------------------------------------------
1. แตกไฟล์ ZIP ทั้งอันก่อน (ดับเบิลคลิกที่ .zip)
2. เก็บทั้งโฟลเดอร์ BirdNET-eBird ไว้ด้วยกัน อย่าแยกไฟล์
3. ครั้งแรก: คลิกขวา (หรือ Control-click) ที่ BirdNET-eBird.command
   แล้วเลือก Open > Open  (จำเป็นครั้งแรกเพราะแอปยังไม่ได้ notarize)
   ครั้งต่อไปดับเบิลคลิกได้เลย
   ถ้าขึ้นว่า "damaged / cannot be opened" ให้เปิด Terminal พิมพ์:
       xattr -dr com.apple.quarantine /path/to/BirdNET-eBird
ต้องลงอะไรเพิ่มไหม? ไม่ต้อง (ยกเว้นถ้า ffmpeg ไม่ได้ถูก bundle มา ให้
   ลง: brew install ffmpeg)

ใช้งาน: เหมือนเวอร์ชัน Windows — แท็บ Analyze เลือกไฟล์/โฟลเดอร์ + พิกัด
   (เช่น 13.8119502,100.553166) แล้วกด Analyze; แท็บ Merge รวมนกตัวเดียวกัน
   ผลลัพธ์ = คลิปแยกชนิด + summary.xlsx; R0 = ยังไม่ตรวจ แก้เป็น R1-R5 เอง

------------------------------------------------------------
How to open (macOS)
------------------------------------------------------------
1. Unzip the whole archive first.
2. Keep the BirdNET-eBird folder together.
3. First run: right-click (Control-click) BirdNET-eBird.command > Open > Open
   (needed once because the app isn't notarized). After that, double-click.
   If it says "damaged / cannot be opened", open Terminal and run:
       xattr -dr com.apple.quarantine /path/to/BirdNET-eBird
Nothing to install (unless ffmpeg wasn't bundled: brew install ffmpeg).

------------------------------------------------------------
Please upload to eBird / Macaulay Library
------------------------------------------------------------
It powers global citizen science and helps train Merlin Bird ID to be
more accurate, especially for under-sampled regions like Southeast Asia.

------------------------------------------------------------
Thanks / ขอบคุณ
------------------------------------------------------------
 - Biopikat (developer / page: Biopikat)
 - Tripitcha Wanwimolruk
 - Wichyanan Limparungpatthanakij
 - Utain Pummarin
 - Chutinton Viriyapanon
 - The eBird reviewers of Thailand
 - Cornell Lab of Ornithology (BirdNET, Merlin Bird ID, eBird / Macaulay Library)
 - Anthropic (Claude — helped develop and write this tool)
READMEEOF

# --- double-click launcher: BirdNET-eBird.command ---
cat > "$bundle/BirdNET-eBird.command" <<'LAUNCH'
#!/bin/bash
# Double-click launcher. No args = GUI, args = CLI. Keeps the bundle self-contained.
dir="$(cd "$(dirname "$0")" && pwd)"
export PATH="$dir:$dir/python/bin:$PATH"     # so ffmpeg/ffprobe next to me are found
export PYTHONIOENCODING="utf-8"
# drop the download quarantine flag so Gatekeeper stops blocking (first run)
xattr -dr com.apple.quarantine "$dir" 2>/dev/null || true
exec "$dir/python/bin/python3" "$dir/app/birdnet_app.py" "$@"
LAUNCH
chmod +x "$bundle/BirdNET-eBird.command"

echo ""
echo "=== Done ==="
echo "Bundle: $bundle"
echo "Test it:  open \"$bundle/BirdNET-eBird.command\""
echo "Zip to share (preserves the +x bit):"
echo "  ditto -c -k --sequesterRsrc --keepParent \"$bundle\" \"$here/BirdNET-eBird-mac.zip\""
