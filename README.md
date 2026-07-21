# BirdNET field-to-eBird

Cut bird clips from long field recordings with **BirdNET**, ready to upload to
**eBird / Macaulay Library**, following the Cornell Lab
*Audio Editing in Audacity for eBird* guide.

- Automatic species ID (filtered by location + date)
- Splits clips **per occurrence** — one continuous cut with lead/tail padding,
  no silence joins, no concatenation
- Standard ML filenames: `YYYY.MM.DD_HHMM_Genus.species_R0.wav`, foldered by day / species
- **Preserves quality** (sample rate + bit depth: 24-bit stays 24-bit), downmix to mono, normalize −3 dB
- `summary.xlsx`, one row per clip (confidence, alternate species, peak dBFS, clipping flag, rough SNR,
  **call frequency in Hz** — peak / low / high — plus a **Xeno-Canto link** to that species'
  reference recordings so you can recheck the ID by ear, …)
- Optional mel-spectrograms for visual review

Three ways to run: **Tkinter GUI** (desktop window), **Streamlit** (browser), **CLI**.

---

## Requirements

1. **Python 3.12** (important — TensorFlow does not support 3.13+)
   ```powershell
   winget install Python.Python.3.12
   ```
2. **ffmpeg**
   ```powershell
   winget install Gyan.FFmpeg
   ```
3. **git** (to clone)

> Recommended: RAM ≥ 8 GB, free disk ~3 GB (TensorFlow + model).

## Install

```powershell
git clone <repo-url> birdnet-field-to-ebird
cd birdnet-field-to-ebird
powershell -ExecutionPolicy Bypass -File setup.ps1
```

`setup.ps1` creates a virtual env (`.venv`) and installs dependencies
(first run is slow — TensorFlow is large).

## Usage

```powershell
.\run-gui.ps1     # Tkinter — pick a file via dialog (recommended: reads date/time from the file)
.\run-web.ps1     # Streamlit — open http://localhost:8501 in a browser
```

The GUI has two tabs: **Analyze** (detect + cut clips) and **Merge (same individual)**
— pick several clips of the *same individual bird*, set an output file, and it joins
them with 1 s of silence between (per the eBird guideline), trimming and re-normalizing.
Coordinates accept `lat,lon` (e.g. `13.8119502,100.553166`) or a pasted Google Maps link.

Or the CLI directly:

```powershell
.\.venv\Scripts\python.exe field_audio_to_ebird.py "recording.wav" -o "output_dir" `
    --coords 13.8119502,100.553166 --place "Site name" --spectrogram
# Whole folder (batch):
.\.venv\Scripts\python.exe field_audio_to_ebird.py "audio_folder" -o "output_dir"
# Merge clips of the same individual into one file:
.\.venv\Scripts\python.exe field_audio_to_ebird.py --group clip1.wav clip2.wav -o merged.wav
```

### Key options (CLI; also in both GUIs)
| Option | Default | Meaning |
|---|---|---|
| `--lat` `--lon` | (empty = read metadata / config default) | survey coordinates |
| `--date` | (empty = guess from filename / metadata / file time) | override date YYYY-MM-DD |
| `--min-conf` | 0.5 | minimum confidence 0–1 |
| `--occurrence-gap` | 5 | gap larger than this (s) = separate occurrence = separate file |
| `--lead` `--tail` | 3 / 3 | padding before / after (s) |
| `--spectrogram` | off | generate mel-spectrograms |
| `--unknown` | off | also cut sounds BirdNET can't confidently ID into an `_Unknown/` folder (for manual / expert review) |
| `--unknown-min-conf` | 0.25 | confidence floor for `_Unknown` (below this = ignored as noise) |
| `--highpass` | 0 (off) | high-pass filter cutoff Hz — cut low rumble/wind/hum; use sparingly (eBird suggests ≤250) |
| `--force` | off | re-process even if the file was already cut (normally skipped) |

> **`_Unknown/` folder:** with `--unknown` (GUI: **cut unknown** checkbox), any sound BirdNET
> detects at `[0.25, min-conf)` confidence — and that doesn't overlap a confident detection —
> is cut into `<date>/_Unknown/`. Filenames are `<date>_<time>_UNKNOWN_R0.wav`; the summary's
> *Alt species 1* column holds BirdNET's low-confidence guess (a hint, not an ID). Listen and
> compare yourself, or send them to an expert.

## Standalone .exe (run on machines without Python)

Build a self-contained folder that runs on any 64-bit Windows PC — **no Python,
no pip, no ffmpeg install** needed on the target machine. It bundles a real
Python 3.12, all dependencies, the BirdNET model, and ffmpeg next to a small
`BirdNET-eBird.exe` launcher.

On your build machine you need Python 3.12 (`winget install Python.Python.3.12`)
and ffmpeg (`winget install Gyan.FFmpeg`) on PATH, then:

```powershell
powershell -ExecutionPolicy Bypass -File build-exe.ps1
```

Output: `dist\BirdNET-eBird\` — **zip this whole folder** and copy it to the other PC.
There, unzip and run `BirdNET-eBird.exe` (double-click = GUI window).

```
BirdNET-eBird\
  BirdNET-eBird.exe   ← run this (no args = GUI, args = CLI)
  python\             ← bundled Python 3.12 + all deps (don't remove)
  app\                ← program code
  ffmpeg.exe  ffprobe.exe
```

### For the people you share it with (no install needed)

**Nothing to install.** Python, ffmpeg, the BirdNET model, and the Microsoft VC++
runtime (`msvcp140.dll`, `vcomp140.dll`, …) are all bundled. Just:

1. **Extract the whole ZIP first** (right-click → Extract All). Don't run the `.exe`
   from inside the ZIP preview — it won't find `python\` / `app\`.
2. **Keep the whole folder together.** Don't move `BirdNET-eBird.exe` out on its own.
3. Double-click `BirdNET-eBird.exe`.
4. First time, Windows SmartScreen may say *"Windows protected your PC"* (because the
   `.exe` isn't code-signed). Click **More info → Run anyway**. This is normal for
   unsigned apps; the file is safe.

Requirements on their PC: **64-bit Windows 10 or 11**. That's it.

- Large (~1.1 GB). First launch is slow (loads the model).
- Also works as a CLI: `BirdNET-eBird.exe "recording.wav" -o out --lat 13.75 --lon 100.5`
- Uses **ai-edge-litert (LiteRT)** instead of full TensorFlow, so it's much smaller
  and needs no GPU/CUDA. Detection results are identical.

> Why a bundled Python instead of a single frozen `.exe`? PyInstaller crashes while
> analyzing this project's native libraries (TensorFlow / scipy / numba) on Windows.
> Bundling a real Python sidesteps that and is rock-solid.
>
> The Streamlit browser UI is **not** in the `.exe` (it ships the Tkinter GUI + CLI).
> For the browser UI, use the clone + `setup.ps1` route above.

### macOS bundle

Same idea for macOS, built with `build-macos.sh` — **must be run on a Mac** (it
downloads a macOS Python and macOS wheels; it can't be cross-built from Windows).

```bash
brew install ffmpeg           # provides ffmpeg + ffprobe
bash build-macos.sh           # -> dist/BirdNET-eBird-mac/BirdNET-eBird.command
# zip to share (keeps the +x bit):
ditto -c -k --sequesterRsrc --keepParent dist/BirdNET-eBird-mac BirdNET-eBird-mac.zip
```

- macOS uses **full TensorFlow** (Apple-Silicon `tensorflow` 2.21 / Intel 2.16) because
  `ai-edge-litert` has no macOS wheel; birdnetlib falls back to `tensorflow.lite`
  automatically. Detection results are the same.
- The launcher is a double-clickable `BirdNET-eBird.command` (opens a Terminal window +
  the GUI). No args = GUI, args = CLI.
- **Gatekeeper (first run):** the app isn't notarized, so right-click (Control-click)
  `BirdNET-eBird.command` → **Open → Open** the first time. If macOS says it's "damaged",
  run `xattr -dr com.apple.quarantine /path/to/BirdNET-eBird`.
- Works on Apple Silicon (arm64) and Intel (x86_64); build on the same kind of Mac you
  want to run it on (or on Apple Silicon, build both via Rosetta for the Intel one).
- **ffmpeg on macOS:** Homebrew's `ffmpeg` links external dylibs, so the copied binary
  may not run on a *clean* Mac without ffmpeg. That's usually fine — WAV/FLAC/MP3 are read
  directly via `soundfile` and don't need ffmpeg; ffmpeg is only used for `.m4a`/`.aac`
  and metadata probing. If a target Mac needs it, `brew install ffmpeg` there, or swap in
  a static ffmpeg build before zipping.

## Notes / tips

- **Clip date/time** comes from: filename (`20260613 0830`, `2026-06-13 08_30`) → file metadata → file mtime.
  Files without a date in the name are most reliable if you name them with the date/time.
- **Don't use "Upload" in Streamlit for files that have no date in the name** — the browser drops the
  file timestamp, so you get the upload date instead of the recording date. Use the "pick local file"
  button or the Tkinter GUI instead.
- 24-bit beats 16-bit for field audio (wider dynamic range); the tool keeps 24-bit.
- `R0` in the filename = unreviewed (auto). Listen / check the spectrogram, then edit it to R1–R5 before uploading.

## Why upload to eBird / Macaulay Library

Please don't let your recordings sit on a hard drive. When you review, rate, and upload
your clips to **eBird / Macaulay Library**:

- You add to a **global citizen-science** archive that researchers and conservationists
  rely on — every record helps track distributions, seasonality, and population change.
- You help **train Merlin Bird ID** to recognize species more accurately, especially for
  under-sampled regions like Southeast Asia. More high-quality, well-labeled recordings =
  a smarter Merlin for everyone.

Your recordings matter. Cut them, check them, and send them in. 🐦

## Acknowledgements

This tool exists thanks to the knowledge, feedback, and field expertise of:

- **Biopikat** — creator of this tool (Facebook page: *Biopikat*)
- **Tripitcha Wanwimolruk**
- **Wichyanan Limparungpatthanakij**
- **Utain Pummarin**
- **Chutinton Viriyapanon**
- **The eBird reviewers of Thailand** — for their volunteer work reviewing records and
  safeguarding data quality
- **The Cornell Lab of Ornithology** — for BirdNET, Merlin Bird ID, and the
  eBird / Macaulay Library that make all of this possible
- **Anthropic** — Claude helped develop and write the code for this tool

Thank you for sharing your ears, your data, and your time.

## License / credits

- This code: use and modify freely.
- **BirdNET** model: CC BY-NC-SA 4.0 (Cornell Lab / Stefan Kahl et al.) — for education/research
  (non-commercial); give credit and share-alike.
- Follow eBird / Macaulay Library upload guidelines.
