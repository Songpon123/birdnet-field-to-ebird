# BirdNET field-to-eBird

Turn field recordings into reviewable bird clips for eBird / Macaulay Library. BirdNET suggests species; a person must listen, confirm the identification, and rate audio quality before uploading.

## What the current app does

- **Start tab:** choose an audio file or folder and analysis starts. If the recording date or location is missing from metadata, the app asks for the real values first. It never substitutes a file's modification date for a missing recording date.
- **Analysis:** BirdNET detects calls and exports a WAV per occurrence, preserving the source sample rate and bit depth where possible. Optional spectrograms and alternate candidate species help with review.
- **Review clips tab:** listen, inspect spectrograms, correct names, and assign a human quality rating. Only approved clips are copied to `Ready/`. Reviews are kept when an unchanged source is selected again.
- **Advanced settings:** adjust confidence, padding, normalization, unknown detections, and continuous spans.
- **Merge clips:** combine clips only after confirming they are from the same individual bird.

Outputs go to `~/BirdNET_eBird` by default, grouped by recording date and start time. Each session has `summary.xlsx`. The original recordings are left untouched. If a run is interrupted, completed files remain available and a later run can continue.

## macOS: build a desktop bundle

This GitHub repository contains **source code**, not the large Python runtime, model, or ffmpeg binaries. Build a portable bundle on an Apple Silicon Mac with Python 3.12 dependencies and ffmpeg/ffprobe:

```bash
brew install ffmpeg
bash build-macos.sh
open "dist/BirdNET-eBird-mac/BirdNET eBird.app"
```

The build produces `dist/BirdNET-eBird-mac/` with `BirdNET eBird.app`, a `.command` fallback, Python, and the application code. Keep the `.app` **inside that folder**; it uses the sibling `python/` and `app/` directories. To share it, zip the entire folder:

```bash
ditto -c -k --sequesterRsrc --keepParent dist/BirdNET-eBird-mac BirdNET-eBird-mac.zip
```

For a bundle that runs without Homebrew ffmpeg on another Mac, build self-contained audio binaries using `tools/build_ffmpeg.sh` and set `FFMPEG_DIR` to its `prefix/bin` when running `build-macos.sh`. The checked-in Mac dependency versions match the working Apple Silicon package; an Intel build has not been verified.

Because the app is not notarized, macOS may require opening it with **Control-click → Open** on first launch. See [อ่านก่อนใช้.txt](%E0%B8%AD%E0%B9%88%E0%B8%B2%E0%B8%99%E0%B8%81%E0%B9%88%E0%B8%AD%E0%B8%99%E0%B9%83%E0%B8%8A%E0%B9%89.txt) for the Thai and English packaged guide.

## Run from source

The source layout has one application copy in `app/`. On macOS, use Python 3.12, install `requirements-macos.txt`, and run:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-macos.txt
.venv/bin/python app/birdnet_app.py
```

Install ffmpeg/ffprobe separately if working with M4A/MP3 and for complete metadata probing. The GUI uses the local machine only; it does not upload your audio to eBird.

On Windows, `setup.ps1` and `run-gui.ps1` remain available. `build-exe.ps1` assembles a portable Windows folder with the current `app/` code. Windows packaging has not been retested with this update.

The optional Streamlit interface remains in `birdnet_ui.py`; run it with `run-web.ps1` after Windows setup, or `streamlit run birdnet_ui.py` in a configured source environment. Its review view is separate from the desktop app's approval workflow.

## Command line

```bash
# A recording whose metadata contains date and coordinates
.venv/bin/python app/field_audio_to_ebird.py recording.wav -o output

# A recording without those metadata fields: provide the real values
.venv/bin/python app/field_audio_to_ebird.py recording.wav -o output \
  --date 2026-09-27 --coords 13.81195,100.55317 --start-time 06:30

# Several files recorded on the same date; confirm the shared manual date
.venv/bin/python app/field_audio_to_ebird.py audio_folder -o output \
  --date 2026-09-27 --same-date-for-all --coords 13.81195,100.55317

# Merge clips only after confirming they are the same individual
.venv/bin/python app/field_audio_to_ebird.py --group clip1.wav clip2.wav -o merged.wav
```

For files without a date in metadata, `--date` is required even if the filename or file modification time looks like a date. A location is also required if absent from metadata. `--coords` accepts latitude/longitude or a Google Maps link. For a folder with a manual date, `--same-date-for-all` confirms that it applies to every file.

## Checks

The repository includes workflow tests for required metadata, review approvals, safe reruns, interruption, and continuous spans:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Credits and use

This project builds on BirdNET and the Cornell Lab's eBird / Macaulay Library guidance. Thanks to Biopikat, Tripitcha Wanwimolruk, Wichyanan Limparungpatthanakij, Utain Pummarin, Chutinton Viriyapanon, and the eBird reviewers of Thailand for field expertise and feedback. BirdNET model licensing and eBird upload guidelines still apply; see the model's CC BY-NC-SA 4.0 terms for non-commercial use.
