#!/bin/bash
# build-macos.sh — build a portable macOS bundle (runs on Macs without Python)
# ---------------------------------------------------------------------------
# MUST be run ON a Mac (it downloads a macOS Python + installs macOS wheels).
# Mirrors build-exe.ps1 for Windows. The working Apple Silicon package uses
# ai-edge-litert through the tflite_runtime shim in app/.
#
# Run:    bash build-macos.sh
# Output: dist/BirdNET-eBird-mac/ with a Finder app and a .command fallback
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

# --- install dependencies via requirements-macos.txt ---
echo "installing deps into the bundle python ..."
"$vpy" -m pip install --upgrade pip
"$vpy" -m pip install -r "$here/requirements-macos.txt"

# --- app code, review workflow, icon, and LiteRT shim ---
mkdir -p "$bundle/app/tflite_runtime" "$bundle/app/assets"
cp "$here"/app/*.py "$bundle/app/"
cp "$here"/app/tflite_runtime/*.py "$bundle/app/tflite_runtime/"
cp "$here"/app/assets/app_icon.png "$bundle/app/assets/"

# --- ffmpeg + ffprobe (set FFMPEG_DIR to self-contained builds when distributing) ---
for b in ffmpeg ffprobe; do
  p="${FFMPEG_DIR:-}/$b"
  [ -x "$p" ] || p="$(command -v "$b" || true)"
  if [ -z "$p" ]; then
    echo "WARNING: $b not found on PATH — install with: brew install ffmpeg"
  else
    cp "$p" "$bundle/$b"
  fi
done

# --- packaged guide (same text maintained in the repository) ---
cp "$here/อ่านก่อนใช้.txt" "$bundle/อ่านก่อนใช้.txt"

# --- double-click launcher: BirdNET-eBird.command ---
cat > "$bundle/BirdNET-eBird.command" <<'LAUNCH'
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
LAUNCH
chmod +x "$bundle/BirdNET-eBird.command"

# Finder app: shares the bundle's Python and source, and opens without Terminal.
desktop_app="$bundle/BirdNET eBird.app"
mkdir -p "$desktop_app/Contents/MacOS" "$desktop_app/Contents/Resources"
cp "$here/macos/Info.plist" "$desktop_app/Contents/Info.plist"
cp "$here/macos/BirdNET-eBird" "$desktop_app/Contents/MacOS/BirdNET-eBird"
cp "$here/app/assets/AppIcon.icns" "$desktop_app/Contents/Resources/AppIcon.icns"
chmod +x "$desktop_app/Contents/MacOS/BirdNET-eBird"

echo ""
echo "=== Done ==="
echo "Bundle: $bundle"
echo "Test it:  open \"$desktop_app\""
echo "Zip to share (preserves the +x bit):"
echo "  ditto -c -k --sequesterRsrc --keepParent \"$bundle\" \"$here/BirdNET-eBird-mac.zip\""
