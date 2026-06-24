#!/usr/bin/env python3
"""
field_audio_to_ebird.py
------------------------
รับไฟล์เสียงสนามยาว ๆ (หรือทั้งโฟลเดอร์) แล้วเตรียมคลิปสำหรับอัป eBird / Macaulay Library
ตามแนวทางคู่มือ "Audio Editing in Audacity for eBird" (Cornell Lab / Macaulay Library)

ลำดับงาน:
  1) ระบุชนิดนกด้วย BirdNET (กรองตามพิกัด + วันที่ ลด false positive)
  2) แบ่ง detection ของแต่ละชนิดเป็น "occurrence" (การพบแต่ละครั้ง):
       detection ที่ห่างกัน <= OCCURRENCE_GAP_SEC = ครั้งเดียวกัน, เกินกว่านั้น = คนละครั้ง
     แต่ละ occurrence ตัดเป็น "ช่วงต่อเนื่องช่วงเดียว" จากไฟล์ต้นฉบับ
       ตั้งแต่ (เสียงแรก - LEAD_SEC) ถึง (เสียงสุดท้าย + TAIL_SEC), clamp ขอบไฟล์
     *ไม่* คั่นความเงียบ, *ไม่* concat หลาย detection, *ไม่* เฉือนช่วงกลาง (ระยะห่าง = ข้อมูลวิจัย)
  3) ตัดจากไฟล์ต้นฉบับเต็มคุณภาพ (คง sample rate + bit depth), แปลง mono ถ้าตั้ง MAKE_MONO,
     normalize peak ไปที่ TARGET_DBFS (-3 dB) ต่อไฟล์
  4) ตั้งชื่อตามมาตรฐาน ML: YYYY.MM.DD_HHMM_Genus.species_R0.wav
     โฟลเดอร์: OUTPUT\\<YYYY.MM.DD>\\<ชื่อสามัญ>\\<ไฟล์>
  5) summary.xlsx (1 แถว/คลิป) ไว้ให้คนตรวจก่อนสรุป ID
  6) (ออปชัน) mel-spectrogram .png ต่อ occurrence ไว้รีวิวด้วยตา

หมายเหตุ:
  - rating ในชื่อไฟล์ตั้งเป็น R0 (auto/ยังไม่ตรวจ) เสมอ ให้คนมาแก้เป็น R1-R5 เอง
    ส่วน provisional rating (เดาจาก confidence) อยู่ในคอลัมน์ของ summary.xlsx
  - เสียงประกาศ (voice notes) คู่มือใช้ -10 dB แต่ตรวจอัตโนมัติยาก จึง normalize -3 ทั้งหมด
  - โมเดล BirdNET เป็น CC BY-NC-SA 4.0 (ใช้เพื่อการศึกษา/วิจัย = non-commercial)

ติดตั้ง: pip install birdnetlib pydub pandas openpyxl librosa matplotlib soundfile numpy ; ต้องมี ffmpeg
"""

import argparse
import gc
import json
import math
import os
import re
import struct
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# กัน OpenMP double-init crash (tensorflow + librosa/numba โหลด libiomp ตัวเดียวกัน)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import numpy as np
from pydub import AudioSegment
import pandas as pd

# birdnetlib (ดึง TensorFlow มาด้วย) import แบบ lazy ใน main()/process_file()
# เพื่อให้โหมด --gen-spectrograms รันเป็น subprocess "ที่ไม่มี TensorFlow" ได้
# (TensorFlow + librosa/matplotlib ใน process เดียวกัน = native crash 0xC0000005)

# ======================== ตั้งค่า (ปรับตรงนี้ หรือ override ด้วย argument) ========================
AUDIO_FILE       = ""
OUTPUT_DIR       = str(Path.home() / "BirdNET_eBird")     # โฟลเดอร์ผลลัพธ์
LAT, LON         = 12.80, 99.62                   # พิกัด fallback (ใช้เมื่อไม่ได้ระบุ + ไม่มีใน metadata)
REC_DATE         = None                           # YYYY-MM-DD override วันกรอง (None = เดาจากชื่อ/metadata/mtime)
MIN_CONF         = 0.5                            # ความมั่นใจขั้นต่ำ 0-1
USE_METADATA     = True                           # อ่านวัน/พิกัดจาก metadata ไฟล์ (ffprobe + BWF bext + XMP)

OCCURRENCE_GAP_SEC = 5.0                          # ห่างกัน <= ค่านี้ = ครั้งเดียวกัน, เกิน = คนละครั้ง=คนละไฟล์
LEAD_SEC         = 3.0                            # เผื่อก่อนเสียงแรก (วินาที) ~3 วิ ตามคู่มือ
TAIL_SEC         = 3.0                            # เผื่อหลังเสียงสุดท้าย (วินาที)
TARGET_DBFS      = -3.0                           # normalize peak (Macaulay)
MAKE_MONO        = True                           # stereo -> mono
EXPORT_SPECTROGRAM = False                        # สร้าง mel-spectrogram .png ต่อคลิป
INCLUDE_ALT_SPECIES = True                        # ใส่ชนิดสำรองอันดับ 2-3 ใน summary

# regex พาร์สวันเวลาเริ่มอัดจากชื่อไฟล์ (group: ปี เดือน วัน [ชม.] [นาที] [วินาที])
# รองรับ separator หลายแบบ: '25681111 1336', '2026-05-22 08_41', '20260608', 'YYYYMMDDHHMMSS'
FILENAME_DATETIME_REGEX = r"(\d{4})[-_.: ]?(\d{2})[-_.: ]?(\d{2})(?:[-_.T ]*(\d{2})[-_.: ]?(\d{2})[-_.: ]?(\d{2})?)?"

EXPORT_FORMAT    = "wav"                          # eBird แนะนำ .wav
DEFAULT_RATING   = "R0"                           # provisional rating ในชื่อไฟล์ (auto = ยังไม่ตรวจ)
AUDIO_EXTS       = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aif", ".aiff"}
# =============================================================================================


def _configure_runtime():
    """UTF-8 stdout (กัน UnicodeEncodeError ไทย) + หา ffmpeg ให้ pydub เอง"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / r"Microsoft\WinGet\Links\ffmpeg.exe",
            Path(os.environ.get("USERPROFILE", "")) / r"scoop\shims\ffmpeg.exe",
            Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
            Path(getattr(sys, "_MEIPASS", "")) / "ffmpeg.exe",   # เผื่อ bundle ใน exe
        ]
        ffmpeg = next((str(p) for p in candidates if p.is_file()), None)
    if ffmpeg:
        ffmpeg_dir = str(Path(ffmpeg).parent)
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
        AudioSegment.converter = ffmpeg
        AudioSegment.ffmpeg = ffmpeg
        ffprobe = shutil.which("ffprobe") or str(Path(ffmpeg).with_name("ffprobe.exe"))
        if Path(ffprobe).is_file():
            AudioSegment.ffprobe = ffprobe
    else:
        print("เตือน: หา ffmpeg ไม่เจอ — การอ่าน/เขียนไฟล์เสียงอาจล้มเหลว")


_configure_runtime()


# ----------------------------- helpers -----------------------------
def sanitize(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in " -_." else "_" for c in str(name)).strip()
    return cleaned or "unknown"


def hms(seconds: float) -> str:
    """offset วินาที -> 'HH:MM:SS'"""
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def normalize_to(seg: AudioSegment, target_dbfs: float) -> AudioSegment:
    if seg.max_dBFS == float("-inf"):
        return seg
    return seg.apply_gain(target_dbfs - seg.max_dBFS)


def _dt_from_text(text: str, regex: str):
    """ดึง datetime จากสตริงด้วย regex (ปี พ.ศ. -> ค.ศ. อัตโนมัติ) คืน None ถ้าไม่ได้"""
    for m in re.finditer(regex, text):
        g = list(m.groups()) + [None] * (6 - len(m.groups()))
        try:
            year, month, day = int(g[0]), int(g[1]), int(g[2])
            if year >= 2400:           # พ.ศ. -> ค.ศ.
                year -= 543
            hh = int(g[3]) if g[3] else 0
            mm = int(g[4]) if g[4] else 0
            ss = int(g[5]) if g[5] else 0
            if 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
                return datetime(year, month, day, hh, mm, ss)
        except (ValueError, TypeError):
            continue
    return None


def filename_datetime(name: str, regex: str):
    """เดาวันเวลาจาก 'ชื่อไฟล์' เท่านั้น (ไม่อ่านชื่อโฟลเดอร์ใน path เพราะมักเป็นรหัส
    ของเครื่องอัด เช่น 2026061310 ที่ไม่ใช่วันที่อัดจริง ทำให้เพี้ยน)"""
    return _dt_from_text(name, regex)


def _parse_meta_datetime(s: str):
    s = str(s).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d", "%Y:%m:%d", "%Y%m%dT%H%M%S"):
        try:
            return datetime.strptime(s[:len(datetime.now().strftime(fmt)) + 2].strip(), fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _parse_iso6709(s: str):
    """'+12.80-099.62/' หรือ '+12.8000+099.6200+010/' -> (lat, lon)"""
    m = re.match(r"\s*([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", str(s))
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            return None
    return None


def _read_wav_meta(path: Path, meta: dict):
    """อ่าน BWF bext (OriginationDate/Time) + XMP (_PMX) จาก WAV — best effort"""
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"RIFF":
                return
            f.read(8)  # size + 'WAVE'
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                cid = hdr[:4]
                sz = struct.unpack("<I", hdr[4:])[0]
                if cid in (b"bext", b"_PMX", b"iXML"):
                    data = f.read(sz)
                else:
                    f.seek(sz, 1)
                    if sz % 2:
                        f.read(1)
                    continue
                if sz % 2:
                    f.read(1)
                if cid == b"bext" and meta.get("datetime") is None:
                    d = data[320:330].decode("latin1", "replace").strip("\x00 ").replace(":", "-")
                    t = data[330:338].decode("latin1", "replace").strip("\x00 ")
                    dt = _parse_meta_datetime(f"{d} {t}".strip())
                    if dt:
                        meta["datetime"] = dt
                elif cid in (b"_PMX", b"iXML"):
                    txt = data.decode("utf-8", "replace")
                    if meta.get("datetime") is None:
                        mm = re.search(r"(?:xmp:CreateDate|exif:DateTimeOriginal|BWFOriginationDate)"
                                       r'[>"]\s*([0-9:\-T ]{8,25})', txt)
                        if mm:
                            meta["datetime"] = _parse_meta_datetime(mm.group(1))
                    if meta.get("lat") is None:
                        mlat = re.search(r"exif:GPSLatitude[>\"]([^<\"]+)", txt)
                        mlon = re.search(r"exif:GPSLongitude[>\"]([^<\"]+)", txt)
                        if mlat and mlon:
                            la, lo = _gps_exif(mlat.group(1)), _gps_exif(mlon.group(1))
                            if la is not None and lo is not None:
                                meta["lat"], meta["lon"] = la, lo
    except Exception:  # noqa: BLE001
        pass


def _gps_exif(s: str):
    """'12,48.0N' / '12.8' -> ทศนิยม (รองรับ exif GPS แบบองศา,ลิปดา + ทิศ)"""
    s = str(s).strip()
    m = re.match(r"(\d+),([\d.]+)([NSEW]?)", s)
    if m:
        deg = float(m.group(1)) + float(m.group(2)) / 60.0
        if m.group(3) in ("S", "W"):
            deg = -deg
        return deg
    try:
        return float(s)
    except ValueError:
        return None


def parse_coords(text):
    """รับพิกัดช่องเดียว: 'lat,lon', 'lat lon', หรือลิงก์ Google Maps -> (lat, lon) | None
    เช่น '14.4272132,101.4011014' หรือ 'https://www.google.com/maps/@14.42,101.40,2949m/...'"""
    if not text:
        return None
    t = str(text).strip()
    pats = [
        r"@(-?\d+\.\d+),\s*(-?\d+\.\d+)",        # .../@lat,lon,zoom
        r"[?&]q=(-?\d+\.\d+),\s*(-?\d+\.\d+)",    # ...?q=lat,lon
        r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)",        # place URL !3dlat!4dlon
        r"(-?\d+\.\d+)\s*[, ]\s*(-?\d+\.\d+)",    # lat,lon หรือ lat lon
    ]
    for p in pats:
        m = re.search(p, t)
        if m:
            try:
                lat, lon = float(m.group(1)), float(m.group(2))
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return lat, lon
            except ValueError:
                pass
    return None


def read_audio_metadata(path: Path) -> dict:
    """ดึง datetime / lat / lon / place จาก metadata ไฟล์ (ffprobe + BWF/XMP) — คืน dict ที่ไม่มี = None"""
    meta = {"datetime": None, "lat": None, "lon": None, "place": None}
    # ffprobe tags (ครอบคลุม mp4/m4a/flac/ogg/mp3 และ wav บางส่วน)
    ffprobe = getattr(AudioSegment, "ffprobe", None)
    if ffprobe and Path(ffprobe).is_file():
        try:
            r = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json",
                                "-show_format", "-show_streams", str(path)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=30)
            data = json.loads(r.stdout or "{}")
            tags = {}
            for blk in [data.get("format", {})] + data.get("streams", []):
                for k, v in (blk.get("tags") or {}).items():
                    tags[k.lower()] = v
            for k in ("creation_time", "date", "date_recorded", "icrd",
                      "com.apple.quicktime.creationdate", "originationdate"):
                if k in tags:
                    meta["datetime"] = _parse_meta_datetime(tags[k])
                    if meta["datetime"]:
                        break
            for k in ("com.apple.quicktime.location.iso6709", "location",
                      "location-eng", "ixml_location"):
                if k in tags:
                    ll = _parse_iso6709(tags[k])
                    if ll:
                        meta["lat"], meta["lon"] = ll
                        break
            for k in ("com.apple.quicktime.location.name", "location_name", "place"):
                if k in tags and str(tags[k]).strip():
                    meta["place"] = str(tags[k]).strip()
                    break
        except Exception:  # noqa: BLE001
            pass
    # WAV BWF/XMP (ffprobe ไม่ดึง bext/_PMX)
    if path.suffix.lower() == ".wav":
        _read_wav_meta(path, meta)
    return meta


def merge_occurrences(items, gap_sec):
    """items: list (start, end, conf) -> list dict {start,end,max_conf,n_det} เรียงเวลา"""
    occ = []
    for s, e, c in sorted(items):
        if occ and s <= occ[-1]["end"] + gap_sec:
            occ[-1]["end"] = max(occ[-1]["end"], e)
            occ[-1]["max_conf"] = max(occ[-1]["max_conf"], c)
            occ[-1]["n_det"] += 1
        else:
            occ.append({"start": s, "end": e, "max_conf": c, "n_det": 1})
    return occ


def alt_species_for(start, end, all_dets, primary_common, topn=2):
    """ชนิดสำรอง: detection ของชนิดอื่นที่ทับช่วงเวลาเดียวกัน เอา conf สูงสุดต่อชนิด"""
    cand = {}
    for s, e, common, _sci, conf in all_dets:
        if common == primary_common:
            continue
        if e >= start and s <= end:            # overlap
            if common not in cand or conf > cand[common]:
                cand[common] = conf
    return sorted(cand.items(), key=lambda x: -x[1])[:topn]


def estimate_snr(seg: AudioSegment):
    """ค่าประมาณ SNR แบบหยาบ: 90th vs 10th percentile ของ RMS ราย frame 50ms (dB)"""
    samples = np.array(seg.get_array_of_samples()).astype(np.float64)
    if seg.channels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1)
    if samples.size == 0:
        return None
    peak = float(1 << (8 * seg.sample_width - 1))
    if peak > 0:
        samples /= peak
    fr = max(1, int(seg.frame_rate * 0.05))
    n = len(samples) // fr
    if n < 3:
        return None
    rms = np.sqrt(np.mean(samples[:n * fr].reshape(n, fr) ** 2, axis=1))
    rms = rms[rms > 0]
    if rms.size < 3:
        return None
    sig = np.percentile(rms, 90)
    noise = np.percentile(rms, 10)
    if noise <= 0:
        return None
    return round(20 * math.log10(sig / noise), 1)


def conf_to_stars(c: float) -> int:
    """provisional rating หยาบ ๆ จาก confidence (1-4, ไม่ให้ 5 เพราะเป็น auto)"""
    if c >= 0.9:
        return 4
    if c >= 0.75:
        return 3
    if c >= 0.6:
        return 2
    return 1


def unique_path(folder: Path, base: str, fmt: str) -> Path:
    """กันชื่อไฟล์ชนกัน (occurrence คนละครั้งในนาทีเดียวกัน)"""
    p = folder / f"{base}.{fmt}"
    i = 2
    while p.exists():
        p = folder / f"{base}-{i}.{fmt}"
        i += 1
    return p


def save_mel_spectrogram(seg: AudioSegment, png_path: Path, title: str) -> bool:
    """mel-spectrogram .png (เรียกเฉพาะใน subprocess ที่ไม่มี TensorFlow)"""
    try:
        import librosa
        import librosa.display
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        print(f"    (ข้าม spectrogram: import ไม่ได้ -> {exc})")
        return False

    samples = np.array(seg.get_array_of_samples()).astype(np.float32)
    if seg.channels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1)
    peak = float(1 << (8 * seg.sample_width - 1))
    if peak > 0:
        samples /= peak
    sr = seg.frame_rate
    fmax = min(12000, sr / 2)

    S = librosa.feature.melspectrogram(y=samples, sr=sr, n_mels=128, fmax=fmax)
    S_db = librosa.power_to_db(S, ref=np.max)

    fig, ax = plt.subplots(figsize=(11, 4))
    img = librosa.display.specshow(
        S_db, sr=sr, x_axis="time", y_axis="mel", fmax=fmax, ax=ax, cmap="magma"
    )
    ax.set_title(title, fontsize=9)
    fig.colorbar(img, ax=ax, format="%+2.0f dB")
    fig.tight_layout()
    fig.savefig(png_path, dpi=110)
    plt.close(fig)
    return True


# ----------------------------- spectrogram subprocess mode -----------------------------
def _spectrogram_title(row, wav: Path) -> str:
    if row is None:
        return wav.stem.replace("_", "  ")

    def cell(key):
        v = row.get(key)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        return str(v).strip()

    sp = cell("Species (common)") or wav.stem
    conf = cell("Max confidence")
    conf_txt = ""
    if conf:
        try:
            conf_txt = f"conf {float(conf):.2f}"
        except ValueError:
            conf_txt = f"conf {conf}"
    occ = cell("Occurrence #")
    clock = cell("Clock time")

    parts = [sp]
    if occ:
        parts.append(f"#{occ}")
    if clock:
        parts.append(clock)
    if conf_txt:
        parts.append(conf_txt)
    return "  ".join(parts)


def gen_spectrograms(dirs):
    """โหมด subprocess (--gen-spectrograms): วน *.wav สร้าง mel .png — ไม่โหลด TensorFlow"""
    made = skipped = failed = 0
    for d in dirs:
        if not d.is_dir():
            continue
        meta = {}
        summ = d / "summary.xlsx"
        if summ.exists():
            try:
                sdf = pd.read_excel(summ)
                for _, r in sdf.iterrows():
                    meta[Path(str(r["File"])).name] = r
            except Exception as exc:  # noqa: BLE001
                print(f"  (อ่าน summary.xlsx ไม่ได้ ใช้ชื่อไฟล์แทน: {exc})")
        for wav in sorted(d.rglob("*.wav")):
            png = wav.with_suffix(".png")
            if png.exists():
                skipped += 1
                continue
            title = _spectrogram_title(meta.get(wav.name), wav)
            try:
                seg = AudioSegment.from_file(str(wav))
                if save_mel_spectrogram(seg, png, title):
                    made += 1
                    print(f"  spec -> {wav.name}")
                else:
                    failed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"  !! spectrogram {wav.name}: {exc}")
    print(f"spectrogram เสร็จ: สร้าง {made}, ข้ามที่มีแล้ว {skipped}, ล้มเหลว {failed}")


def run_spectrogram_subprocess(date_dirs):
    """เรียกตัวเองเป็น subprocess โหมด --gen-spectrograms (process สะอาด ไม่มี TF)"""
    cmd = [sys.executable]
    if not getattr(sys, "frozen", False):
        cmd.append(os.path.abspath(__file__))
    cmd.append("--gen-spectrograms")
    cmd += [str(d) for d in date_dirs]
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env, bufsize=1,
    )
    for line in proc.stdout:
        print(line.rstrip())
    return proc.wait()


# ----------------------------- core -----------------------------
def _trim_edges(seg: AudioSegment, thresh_db: float = -50.0) -> AudioSegment:
    """ตัดความเงียบหัว-ท้ายของคลิป (ไม่ตัดช่วงกลาง = เก็บจังหวะการร้องไว้)"""
    from pydub.silence import detect_leading_silence
    start = detect_leading_silence(seg, silence_threshold=thresh_db)
    end = detect_leading_silence(seg.reverse(), silence_threshold=thresh_db)
    trimmed = seg[start:len(seg) - end]
    return trimmed if len(trimmed) > 0 else seg


def _load_clip(path: Path):
    """โหลดคลิป (เล็ก) ด้วย soundfile -> AudioSegment คงบิต; fallback pydub"""
    try:
        import soundfile as sf
        info = sf.info(str(path))
        dtype, sw, sub = _depth(info.subtype)
        data, _ = sf.read(str(path), dtype=dtype, always_2d=True)
        seg = AudioSegment(data.tobytes(), frame_rate=info.samplerate,
                           sample_width=sw, channels=data.shape[1])
        return seg, sub
    except Exception:  # noqa: BLE001
        return AudioSegment.from_file(str(path)), None


def group_clips(wav_paths, out_path: Path, silence_ms=1000, target_dbfs=-3.0, trim=True):
    """
    รวมหลายคลิป 'นกตัวเดียวกัน' เป็นไฟล์เดียว — ตัดเงียบหัวท้ายแต่ละคลิป แล้วต่อ
    คั่นด้วยความเงียบ silence_ms (ตามคู่มือ ML ข้อ 5) normalize รวมอีกครั้ง
    *ใช้เฉพาะเมื่อมั่นใจว่าเป็นนกตัวเดียวกันเท่านั้น*
    """
    segs, subtype = [], None
    for w in wav_paths:
        seg, sub = _load_clip(Path(w))
        subtype = subtype or sub
        if trim:
            seg = _trim_edges(seg)
        segs.append(seg)
    if not segs:
        print("group: ไม่มีคลิป")
        return
    sr, ch, sw = segs[0].frame_rate, segs[0].channels, segs[0].sample_width
    gap = AudioSegment.silent(duration=silence_ms, frame_rate=sr).set_channels(ch).set_sample_width(sw)
    out = segs[0]
    for s in segs[1:]:
        s = s.set_frame_rate(sr).set_channels(ch).set_sample_width(sw)
        out = out + gap + s
    out = normalize_to(out, target_dbfs)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _export_clip(out, out_path, out_path.suffix.lstrip(".") or "wav", subtype)
    print(f"group: รวม {len(segs)} คลิป -> {out_path} ({len(out)/1000:.0f}s)")


def _depth(subtype):
    """คง bit depth เดิม -> (read_dtype, sample_width, export_subtype). pydub ไม่รับ 3-byte
    จึงอ่าน 24/32-bit เป็น int32 แล้ว export กลับเป็น PCM_24/PCM_32 ด้วย soundfile"""
    s = (subtype or "").upper()
    if "PCM_24" in s:
        return "int32", 4, "PCM_24"
    if "PCM_32" in s:
        return "int32", 4, "PCM_32"
    if "FLOAT" in s or "DOUBLE" in s:
        return "int32", 4, "PCM_24"        # float -> 24-bit
    return "int16", 2, "PCM_16"            # 16-bit, mp3, ogg ฯลฯ


def _open_source(path: Path):
    """เปิดไฟล์: ถ้า libsndfile อ่านได้ (WAV/FLAC/OGG/MP3) คืน ('sf', info) -> อ่านทีละช่วง
    (RAM ต่ำ, รองรับ 24-bit ที่ pydub โหลดทั้งก้อนแล้ว crash, และ export คง bit depth ได้)
    ไม่งั้น (m4a/aac) คืน ('full', AudioSegment ทั้งไฟล์)"""
    try:
        import soundfile as sf
        info = sf.info(str(path))
        if info.frames > 0:
            return "sf", info
    except Exception:  # noqa: BLE001
        pass
    return "full", AudioSegment.from_file(str(path))


def _read_segment(path: Path, info, start_s: float, end_s: float):
    """อ่านเฉพาะช่วง [start_s, end_s] ด้วย soundfile -> AudioSegment"""
    import soundfile as sf
    sr = info.samplerate
    s = max(0, int(start_s * sr))
    e = min(info.frames, int(end_s * sr))
    if e <= s:
        return None
    dtype, sw, _ = _depth(info.subtype)
    data, _meta = sf.read(str(path), start=s, stop=e, dtype=dtype, always_2d=True)
    return AudioSegment(data.tobytes(), frame_rate=sr,
                        sample_width=sw, channels=data.shape[1])


def _export_clip(clip: AudioSegment, out_path: Path, fmt: str, subtype):
    """export คง bit depth ด้วย soundfile (รองรับ PCM_24); ไม่งั้น fallback pydub"""
    if subtype and fmt.lower() in ("wav", "flac", "aiff", "aif"):
        import soundfile as sf
        arr = np.array(clip.get_array_of_samples())
        if clip.channels > 1:
            arr = arr.reshape((-1, clip.channels))
        sf.write(str(out_path), arr, clip.frame_rate, subtype=subtype)
    else:
        clip.export(out_path, format=fmt)


def _already_processed(date_folder: Path, source_name: str) -> bool:
    """ไฟล์นี้เคยตัดแล้วหรือยัง — ดูจากคอลัมน์ 'Source file' ใน summary.xlsx ของวันนั้น"""
    summ = date_folder / "summary.xlsx"
    if not summ.exists():
        return False
    try:
        df = pd.read_excel(summ)
        return "Source file" in df.columns and (df["Source file"].astype(str) == source_name).any()
    except Exception:  # noqa: BLE001
        return False


def process_file(analyzer, audio_path: Path, out_root: Path, *, lat_arg, lon_arg,
                 cfg_lat, cfg_lon, date_override, use_meta, min_conf, gap, lead, tail,
                 target_dbfs, fmt, make_mono, incl_alt, place_arg, dt_regex, force=False,
                 use_filetime=False):
    """
    วิเคราะห์ + ตัดคลิป 1 ไฟล์ คืน (rows, date_folder)
    วัน/พิกัด/สถานที่: argument > ชื่อไฟล์ > metadata ไฟล์ > default
    spectrogram สร้างทีหลังใน subprocess แยก (กัน native crash)
    """
    from birdnetlib import Recording  # lazy: เลี่ยงโหลด TensorFlow ในโหมด gen-spectrograms

    meta = read_audio_metadata(audio_path) if (use_meta and not use_filetime) else {}

    # ---- วันเวลาเริ่มอัด ----  ลำดับ: --date > (ชื่อไฟล์ > metadata ถ้าไม่บังคับ filetime) > เวลาไฟล์
    fn_dt = None if use_filetime else filename_datetime(audio_path.name, dt_regex)
    if fn_dt:
        rec_dt, dt_src = fn_dt, "ชื่อไฟล์"
    elif meta.get("datetime"):
        rec_dt, dt_src = meta["datetime"], "metadata"
    else:
        # ใช้เวลาไฟล์ที่ "เก่าสุด" = เวลาเริ่มบันทึก (ctime มัก = ตอนสร้างไฟล์/เริ่มอัด,
        # mtime = เขียนจบ; แต่ถ้า copy มา ctime จะใหม่ -> min() เลือกอันที่ใกล้วันอัดสุด)
        stt = audio_path.stat()
        rec_dt = datetime.fromtimestamp(min(stt.st_mtime, stt.st_ctime))
        dt_src = "เวลาไฟล์ (เริ่มบันทึก)"
    if date_override:
        rec_dt = rec_dt.replace(year=date_override.year, month=date_override.month,
                                day=date_override.day)
        dt_src = "--date"

    # ---- พิกัด ----
    def _pick(av, mv, cv):
        if av is not None:
            return av, "argument"
        if mv is not None:
            return mv, "metadata"
        return cv, "default"
    lat, lat_src = _pick(lat_arg, meta.get("lat"), cfg_lat)
    lon, _ = _pick(lon_arg, meta.get("lon"), cfg_lon)

    # ---- สถานที่ ----
    place = place_arg or meta.get("place") or ""

    filter_date = rec_dt
    # โฟลเดอร์มีเวลาเริ่มบันทึกด้วย (YYYY.MM.DD_HHMM) เพื่อแยกไฟล์คนละ session ในวันเดียวกัน
    date_tag = rec_dt.strftime("%Y.%m.%d_%H%M")
    date_folder = out_root / date_tag

    # ข้ามถ้าไฟล์นี้เคยตัดแล้ว (เช็คก่อน analyze เพื่อไม่เสียเวลา)
    if not force and _already_processed(date_folder, audio_path.name):
        print(f"\n=== {audio_path.name} ===  ข้าม: เคยตัดแล้วใน {date_tag}/ (ใส่ --force เพื่อทำซ้ำ)")
        return [], None

    print(f"\n=== {audio_path.name} ===")
    print(f"  เริ่มอัด {rec_dt:%Y-%m-%d %H:%M:%S} (จาก {dt_src}) | "
          f"พิกัด {lat},{lon} ({lat_src})"
          + (f" | สถานที่ {place}" if place else "") + f" | min_conf {min_conf}")

    recording = Recording(analyzer, str(audio_path),
                          lat=lat, lon=lon, date=filter_date, min_conf=min_conf)
    print("  กำลังวิเคราะห์เสียง (ไฟล์ยาวอาจใช้เวลาหลายนาที) ...")
    recording.analyze()
    dets = recording.detections
    print(f"  พบ detection {len(dets)} ช่วง")
    if not dets:
        print("  ไม่พบเสียงนกที่มั่นใจพอ — ข้ามไฟล์นี้ (ลองลด --min-conf)")
        return [], None

    print("  กำลังโหลดไฟล์เสียงต้นฉบับ (เต็มคุณภาพ) ...")
    src_mode, src = _open_source(audio_path)
    if src_mode == "sf":
        total_ms = int(src.frames / src.samplerate * 1000)
        export_subtype = _depth(src.subtype)[2]
        print(f"    (stream ทีละช่วง: {src.samplerate}Hz {src.channels}ch {src.subtype} -> {export_subtype})")
    else:
        total_ms = len(src)
        export_subtype = None

    all_dets = [(d["start_time"], d["end_time"], d["common_name"],
                 d["scientific_name"], d["confidence"]) for d in dets]
    by_species = defaultdict(list)
    for d in dets:
        by_species[(d["common_name"], d["scientific_name"])].append(
            (d["start_time"], d["end_time"], d["confidence"]))

    rows = []
    for (common, sci), items in sorted(by_species.items()):
        occ = merge_occurrences(items, gap)
        sp_dir = date_folder / sanitize(common)
        sp_dir.mkdir(parents=True, exist_ok=True)

        for idx, o in enumerate(occ, start=1):
            start_s = max(0.0, o["start"] - lead)
            end_s = min(total_ms / 1000.0, o["end"] + tail)
            if end_s <= start_s:
                continue
            if src_mode == "sf":
                clip = _read_segment(audio_path, src, start_s, end_s)
            else:
                clip = src[int(start_s * 1000):int(end_s * 1000)]
            if clip is None or len(clip) == 0:
                continue
            if make_mono and clip.channels > 1:
                clip = clip.set_channels(1)

            # วัดคุณภาพ "ก่อน" normalize (peak จริงของเสียง)
            peak_dbfs = clip.max_dBFS
            clipping = peak_dbfs >= -0.1
            snr = estimate_snr(clip)

            clip = normalize_to(clip, target_dbfs)

            occ_clock = rec_dt + timedelta(seconds=o["start"])
            hhmm = occ_clock.strftime("%H%M")
            genus_sp = sanitize(sci).replace(" ", ".")
            base = f"{date_tag}_{hhmm}_{genus_sp}_{DEFAULT_RATING}"
            out_path = unique_path(sp_dir, base, fmt)
            _export_clip(clip, out_path, fmt, export_subtype)

            alts = alt_species_for(o["start"], o["end"], all_dets, common) if incl_alt else []
            alt1 = alts[0] if len(alts) >= 1 else ("", "")
            alt2 = alts[1] if len(alts) >= 2 else ("", "")

            start_clock = occ_clock.strftime("%H:%M:%S")
            dur = round(len(clip) / 1000.0, 1)
            rows.append({
                "Species (common)": common,
                "Species (scientific)": sci,
                "Occurrence #": idx,
                "Start offset (s)": round(o["start"], 1),
                "Clock time": start_clock,
                "End offset (s)": round(o["end"], 1),
                "Duration (s)": dur,
                "Max confidence": round(o["max_conf"], 3),
                "Alt species 1": alt1[0],
                "Alt1 conf": round(alt1[1], 3) if alt1[1] != "" else "",
                "Alt species 2": alt2[0],
                "Alt2 conf": round(alt2[1], 3) if alt2[1] != "" else "",
                "Peak dBFS": round(peak_dbfs, 1),
                "Clipping": "YES" if clipping else "",
                "SNR (dB approx)": snr if snr is not None else "",
                "Provisional rating": conf_to_stars(o["max_conf"]),
                "Place": place or "",
                "File": str(out_path.relative_to(date_folder)),
            })
            print(f"    -> {common:<28} #{idx:02d} {start_clock} {dur:4.0f}s "
                  f"conf {o['max_conf']:.2f}  peak {peak_dbfs:4.1f}dB"
                  + ("  CLIP!" if clipping else ""))

    del src
    gc.collect()
    return rows, date_folder


def write_summary(rows, xlsx_path: Path):
    df = pd.DataFrame(rows)
    # merge กับ summary เดิมของวันนั้น (กันไฟล์ที่ถูก skip หายไปจากสรุป)
    if xlsx_path.exists():
        try:
            df = pd.concat([pd.read_excel(xlsx_path), df], ignore_index=True)
        except Exception:  # noqa: BLE001
            pass
    if "File" in df.columns:
        df = df.drop_duplicates(subset=["File"], keep="last")
    sort_cols = [c for c in ("Species (common)", "Occurrence #") if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols, key=lambda c: c.astype(str))
    df.to_excel(xlsx_path, index=False)


def collect_inputs(input_path: Path):
    if input_path.is_dir():
        return sorted(p for p in input_path.iterdir()
                      if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    if input_path.is_file():
        return [input_path]
    return []


def build_parser():
    p = argparse.ArgumentParser(
        description="ตัดคลิปนกจากเสียงสนามด้วย BirdNET เตรียมอัป eBird/Macaulay (ตามคู่มือ ML)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("audio", nargs="?", default=AUDIO_FILE, help="ไฟล์เสียง หรือโฟลเดอร์ (batch)")
    p.add_argument("-o", "--output", default=OUTPUT_DIR, help="โฟลเดอร์ผลลัพธ์")
    p.add_argument("--lat", type=float, default=None, help="latitude (ไม่ใส่ = metadata/ค่า default)")
    p.add_argument("--lon", type=float, default=None, help="longitude (ไม่ใส่ = metadata/ค่า default)")
    p.add_argument("--coords", default=None,
                   help="พิกัดช่องเดียว 'lat,lon' หรือลิงก์ Google Maps (override --lat/--lon)")
    p.add_argument("--date", default=REC_DATE, help="override วัน YYYY-MM-DD (ไม่ใส่ = ชื่อไฟล์/metadata/mtime)")
    p.add_argument("--use-metadata", action=argparse.BooleanOptionalAction, default=USE_METADATA,
                   help="อ่านวัน/พิกัดจาก metadata ไฟล์ (ffprobe + BWF bext + XMP)")
    p.add_argument("--use-filetime", action="store_true",
                   help="บังคับใช้เวลาไฟล์ (เริ่มบันทึก) เป็นวันเวลาอัด ข้ามชื่อไฟล์/metadata")
    p.add_argument("--min-conf", type=float, default=MIN_CONF, help="ความมั่นใจขั้นต่ำ 0-1")
    p.add_argument("--occurrence-gap", type=float, default=OCCURRENCE_GAP_SEC,
                   help="ห่างกัน <= ค่านี้ (วินาที) = ครั้งเดียวกัน")
    p.add_argument("--lead", type=float, default=LEAD_SEC, help="เผื่อก่อนเสียงแรก (วินาที)")
    p.add_argument("--tail", type=float, default=TAIL_SEC, help="เผื่อหลังเสียงสุดท้าย (วินาที)")
    p.add_argument("--target-dbfs", type=float, default=TARGET_DBFS, help="ระดับ normalize peak")
    p.add_argument("--format", default=EXPORT_FORMAT, help="นามสกุล export")
    p.add_argument("--place", default=None, help="ชื่อสถานที่ (เก็บลง summary)")
    p.add_argument("--datetime-regex", default=FILENAME_DATETIME_REGEX,
                   help="regex พาร์สวันเวลาจากชื่อไฟล์")
    p.add_argument("--mono", action=argparse.BooleanOptionalAction, default=MAKE_MONO,
                   help="แปลง stereo -> mono")
    p.add_argument("--spectrogram", action=argparse.BooleanOptionalAction, default=EXPORT_SPECTROGRAM,
                   help="สร้าง mel-spectrogram .png ต่อคลิป")
    p.add_argument("--alt-species", action=argparse.BooleanOptionalAction, default=INCLUDE_ALT_SPECIES,
                   help="ใส่ชนิดสำรองอันดับ 2-3 ใน summary")
    p.add_argument("--force", action="store_true",
                   help="ทำซ้ำแม้ไฟล์เคยตัดแล้ว (ปกติจะข้ามไฟล์ที่มีใน summary ของวันนั้น)")
    return p


def main():
    # โหมดสร้าง spectrogram แยก (subprocess — ไม่โหลด TensorFlow)
    if len(sys.argv) >= 2 and sys.argv[1] == "--gen-spectrograms":
        gen_spectrograms([Path(d) for d in sys.argv[2:]])
        return

    # โหมดรวมคลิปนกตัวเดียวกัน (ML guide #5): --group w1 w2 ... -o out.wav
    if len(sys.argv) >= 2 and sys.argv[1] == "--group":
        rest = sys.argv[2:]
        if "-o" in rest:
            i = rest.index("-o")
            group_clips(rest[:i], Path(rest[i + 1]))
        else:
            print("group: ต้องระบุ -o <ไฟล์ออก>")
        return

    args = build_parser().parse_args()
    if args.coords:
        cc = parse_coords(args.coords)
        if cc:
            args.lat, args.lon = cc
        else:
            print(f"เตือน: อ่านพิกัดจาก '{args.coords}' ไม่ได้ — ใช้ค่าอื่นแทน")
    input_path = Path(args.audio)
    root_out = Path(args.output)
    root_out.mkdir(parents=True, exist_ok=True)

    date_override = datetime.strptime(args.date, "%Y-%m-%d") if args.date else None

    files = collect_inputs(input_path)
    if not files:
        print(f"ไม่พบไฟล์เสียงที่: {input_path}")
        sys.exit(1)

    print(f"จะประมวลผล {len(files)} ไฟล์ | output: {root_out}")
    print("กำลังโหลดโมเดล BirdNET (ครั้งเดียว) ...")
    from birdnetlib.analyzer import Analyzer  # lazy: โหลด TensorFlow ตรงนี้
    analyzer = Analyzer()

    rows_by_date = defaultdict(list)        # date_folder -> rows (รวมหลายไฟล์วันเดียวกัน)
    total_clips = 0
    for audio_path in files:
        try:
            rows, date_folder = process_file(
                analyzer, audio_path, root_out,
                lat_arg=args.lat, lon_arg=args.lon, cfg_lat=LAT, cfg_lon=LON,
                date_override=date_override, use_meta=args.use_metadata,
                min_conf=args.min_conf, gap=args.occurrence_gap,
                lead=args.lead, tail=args.tail, target_dbfs=args.target_dbfs,
                fmt=args.format, make_mono=args.mono, incl_alt=args.alt_species,
                place_arg=args.place, dt_regex=args.datetime_regex, force=args.force,
                use_filetime=args.use_filetime,
            )
        except Exception as exc:  # noqa: BLE001  ไฟล์เดียวพังไม่ควรล้มทั้ง batch
            print(f"  !! ผิดพลาดกับ {audio_path.name}: {exc}")
            continue
        if rows and date_folder is not None:
            for r in rows:
                r["Source file"] = audio_path.name
            rows_by_date[date_folder].extend(rows)
            total_clips += len(rows)

    # เขียน summary.xlsx ต่อโฟลเดอร์วัน
    for date_folder, rows in rows_by_date.items():
        write_summary(rows, date_folder / "summary.xlsx")
        print(f"  สรุป {len(rows)} คลิป -> {date_folder / 'summary.xlsx'}")

    # สร้าง spectrogram ใน process แยก (กัน TensorFlow ชน native crash)
    if args.spectrogram and rows_by_date:
        print("\nกำลังสร้าง mel-spectrogram ใน process แยก (กัน TensorFlow ชน) ...")
        run_spectrogram_subprocess(sorted(rows_by_date.keys(), key=str))

    print(f"\nเสร็จแล้ว: {total_clips} คลิป จาก {len(files)} ไฟล์ -> {root_out}")


if __name__ == "__main__":
    main()
