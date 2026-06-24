# BirdNET field-to-eBird

Cut bird clips from long field recordings with **BirdNET**, ready to upload to
**eBird / Macaulay Library**, following the Cornell Lab
*Audio Editing in Audacity for eBird* guide.

- Automatic species ID (filtered by location + date)
- Splits clips **per occurrence** — one continuous cut with lead/tail padding,
  no silence joins, no concatenation
- Standard ML filenames: `YYYY.MM.DD_HHMM_Genus.species_R0.wav`, foldered by day / species
- **Preserves quality** (sample rate + bit depth: 24-bit stays 24-bit), downmix to mono, normalize −3 dB
- `summary.xlsx`, one row per clip (confidence, alternate species, peak dBFS, clipping flag, rough SNR, …)
- Optional mel-spectrograms for visual review

Three ways to run: **Tkinter GUI** (desktop window), **Streamlit** (browser), **CLI**.

🇹🇭 ภาษาไทย: [README.th.md](README.th.md)

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

Or the CLI directly:

```powershell
.\.venv\Scripts\python.exe field_audio_to_ebird.py "recording.wav" -o "output_dir" `
    --lat 13.75 --lon 100.5 --place "Site name" --spectrogram
# Whole folder (batch):
.\.venv\Scripts\python.exe field_audio_to_ebird.py "audio_folder" -o "output_dir"
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
| `--force` | off | re-process even if the file was already cut (normally skipped) |

## Notes / tips

- **Clip date/time** comes from: filename (`20260613 0830`, `2026-06-13 08_30`) → file metadata → file mtime.
  Files without a date in the name are most reliable if you name them with the date/time.
- **Don't use "Upload" in Streamlit for files that have no date in the name** — the browser drops the
  file timestamp, so you get the upload date instead of the recording date. Use the "pick local file"
  button or the Tkinter GUI instead.
- 24-bit beats 16-bit for field audio (wider dynamic range); the tool keeps 24-bit.
- `R0` in the filename = unreviewed (auto). Listen / check the spectrogram, then edit it to R1–R5 before uploading.

## License / credits

- This code: use and modify freely.
- **BirdNET** model: CC BY-NC-SA 4.0 (Cornell Lab / Stefan Kahl et al.) — for education/research
  (non-commercial); give credit and share-alike.
- Follow eBird / Macaulay Library upload guidelines.
