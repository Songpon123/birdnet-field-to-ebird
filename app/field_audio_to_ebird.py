#!/usr/bin/env python3
"""
field_audio_to_ebird.py
------------------------
รับไฟล์เสียงสนามยาว ๆ (หรือทั้งโฟลเดอร์) แล้วเตรียมคลิปสำหรับอัป eBird / Macaulay Library
ตามแนวทางคู่มือ "Audio Editing in Audacity for eBird" (Cornell Lab / Macaulay Library)

ลำดับงาน:
  1) เสนอชนิดนกด้วย BirdNET 3.0 preview เป็นค่าเริ่มต้น; ผู้ใช้ฟังตรวจทุกชนิด
     ทั้งสองรุ่นใช้ตัวกรองพิกัด + วันที่ และเลือก BirdNET 2.4 เพื่อเทียบผลได้
  2) แบ่ง detection ของแต่ละชนิดเป็น "occurrence" (การพบแต่ละครั้ง):
       detection ที่ห่างกัน <= OCCURRENCE_GAP_SEC = ครั้งเดียวกัน, เกินกว่านั้น = คนละครั้ง
     แต่ละ occurrence ตัดเป็น "ช่วงต่อเนื่องช่วงเดียว" จากไฟล์ต้นฉบับ
       ตั้งแต่ (เสียงแรก - LEAD_SEC) ถึง (เสียงสุดท้าย + TAIL_SEC), clamp ขอบไฟล์
     *ไม่* คั่นความเงียบ, *ไม่* concat หลาย detection, *ไม่* เฉือนช่วงกลาง (ระยะห่าง = ข้อมูลวิจัย)
  3) ตัดจากไฟล์ต้นฉบับเต็มคุณภาพ (คง sample rate + bit depth), แปลง mono ถ้าตั้ง MAKE_MONO,
     normalize peak ไปที่ TARGET_DBFS (-3 dB) ต่อไฟล์
  4) ตั้งชื่อคลิปที่ยังไม่ตรวจด้วย R0 และเก็บแยกตามวัน/เวลาและชนิด
  5) summary.xlsx (1 แถว/คลิป) พร้อมสถานะ Pending ให้คนตรวจ ID และคุณภาพ
  6) (ออปชัน) mel-spectrogram .png ต่อ occurrence ไว้รีวิวด้วยตา

หมายเหตุ:
  - R0 = ยังไม่ตรวจ; หลังตรวจด้วยคน GUI คัดลอกคลิปที่อนุมัติไป Ready/ พร้อม R1-R5
    ค่า AI confidence band ใน summary.xlsx ไม่ใช่คะแนนคุณภาพเสียง
  - เสียงประกาศ (voice notes) คู่มือใช้ -10 dB แต่ตรวจอัตโนมัติยาก จึง normalize -3 ทั้งหมด
  - โมเดล BirdNET เป็น CC BY-NC-SA 4.0 (ใช้เพื่อการศึกษา/วิจัย = non-commercial)

ติดตั้ง: pip install birdnet birdnetlib onnxruntime pydub pandas openpyxl librosa
         matplotlib soundfile numpy ; ต้องมี ffmpeg (แพ็กเกจที่เหลือตาม dependencies)
"""

import argparse
import gc
import hashlib
import json
import math
import os
import re
import shutil
import signal
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

import paths  # ตั้ง BIRDNET_APP_DATA -> data/birdnet ก่อนโหลด BirdNET 3.0
from platform_tools import NO_WINDOW, bundled_tool, watch_for_stop
from entry_formats import DATE_HINT, TIME_HINT, parse_date_entry, parse_time_entry
from review_store import REVIEW_COLUMNS
from habitat import (MISMATCH_MIN_CONF as HABITAT_MISMATCH_MIN_CONF, SITE_HABITATS,
                     fits_site, parse_site_habitats, species_habitat)

# birdnetlib (ดึง TensorFlow มาด้วย) import แบบ lazy ใน main()/process_file()
# เพื่อให้โหมด --gen-spectrograms รันเป็น subprocess "ที่ไม่มี TensorFlow" ได้
# (TensorFlow + librosa/matplotlib ใน process เดียวกัน = native crash 0xC0000005)

# ======================== ตั้งค่า (ปรับตรงนี้ หรือ override ด้วย argument) ========================
AUDIO_FILE       = ""
OUTPUT_DIR       = str(Path.home() / "BirdNET_eBird")     # โฟลเดอร์ผลลัพธ์
LAT, LON         = None, None                     # never silently invent a recording location
REC_DATE         = None                           # YYYYMMDD override วันกรอง (None = เดาจากชื่อ/metadata/mtime)
MIN_CONF         = 0.5                            # V3 preview: เก็บตัวเลือกให้คนตรวจ; ไม่ใช่การยืนยัน
V2_MIN_CONF      = 0.25                           # V2.4: ค่าที่ใช้กู้เสียงเบาที่เกณฑ์เดิม 0.5 พลาด
DEFAULT_MODEL    = "3.0-preview"                  # recall ดีกว่า V2.4 ในไฟล์เปรียบเทียบ; ผลยังต้องตรวจด้วยคน
OVERLAP_SEC      = 1.5                            # เลื่อนหน้าต่าง 3 วินาทีทีละครึ่งช่วง
OUT_OF_RANGE_MIN_CONF = None                      # None = ตัดชนิดนอกพื้นที่/ฤดู และชนิดที่ตรวจพื้นที่ไม่ได้ทิ้ง
                                                  # (ตั้งค่า เช่น 0.7 = เก็บไว้ตรวจนกหลงถิ่นด้วยหู)
USE_METADATA     = True                           # อ่านวัน/พิกัดจาก metadata ไฟล์ (ffprobe + BWF bext + XMP)

OCCURRENCE_GAP_SEC = 5.0                          # ห่างกัน <= ค่านี้ = ครั้งเดียวกัน, เกิน = คนละครั้ง=คนละไฟล์
LEAD_SEC         = 3.0                            # เผื่อก่อนเสียงแรก (วินาที) ~3 วิ ตามคู่มือ
TAIL_SEC         = 3.0                            # เผื่อหลังเสียงสุดท้าย (วินาที)
TARGET_DBFS      = -3.0                           # normalize peak (Macaulay)
MAKE_MONO        = True                           # stereo -> mono
EXPORT_SPECTROGRAM = False                        # สร้าง mel-spectrogram .png ต่อคลิป
INCLUDE_ALT_SPECIES = True                        # ใส่ชนิดสำรองอันดับ 2-3 ใน summary
CUT_UNKNOWN      = False                          # ตัดเสียงที่ BirdNET มั่นใจไม่พอ ไปเก็บ _Unknown/
UNKNOWN_MIN_CONF = 0.1                            # floor: conf อยู่ [floor, min_conf) = unknown (ต่ำกว่า floor = ทิ้ง=noise)

# regex พาร์สวันเวลาเริ่มอัดจากชื่อไฟล์ (group: ปี เดือน วัน [ชม.] [นาที] [วินาที])
# รองรับ separator หลายแบบ: '25681111 1336', '2026-05-22 08_41', '20260608', 'YYYYMMDDHHMMSS'
FILENAME_DATETIME_REGEX = r"(\d{4})[-_.: ]?(\d{2})[-_.: ]?(\d{2})(?:[-_.T ]*(\d{2})[-_.: ]?(\d{2})[-_.: ]?(\d{2})?)?"

EXPORT_FORMAT    = "wav"                          # eBird แนะนำ .wav
DEFAULT_RATING   = "R0"                           # provisional rating ในชื่อไฟล์ (auto = ยังไม่ตรวจ)
AUDIO_EXTS       = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aif", ".aiff"}
MAX_RAW_SEGMENT_BYTES = 350_000_000         # avoid exhausting RAM on long continuous spans
# =============================================================================================


def _configure_runtime():
    """UTF-8 stdout (กัน UnicodeEncodeError ไทย) + หา ffmpeg ให้ pydub เอง"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    import shutil

    def working_binary(*candidates):
        for candidate in dict.fromkeys(str(p) for p in candidates if p):
            try:
                result = subprocess.run([candidate, "-version"], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=5, creationflags=NO_WINDOW)
                if result.returncode == 0:
                    return candidate
            except (OSError, subprocess.TimeoutExpired):
                continue
        return None

    # ffmpeg/ffprobe ที่แนบมากับโปรแกรม (mac: ข้าง app/, Windows: bin\) มาก่อน ไม่ขึ้นกับ PATH ของตัวเปิดแอป
    ffmpeg = working_binary(bundled_tool("ffmpeg"), shutil.which("ffmpeg"),
                            "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg")
    if not ffmpeg:
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / r"Microsoft\WinGet\Links\ffmpeg.exe",
            Path(os.environ.get("USERPROFILE", "")) / r"scoop\shims\ffmpeg.exe",
            Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
            Path(getattr(sys, "_MEIPASS", "")) / "ffmpeg.exe",   # เผื่อ bundle ใน exe
        ]
        ffmpeg = working_binary(*[p for p in candidates if p.is_file()])
    if ffmpeg:
        ffmpeg_dir = str(Path(ffmpeg).parent)
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
        AudioSegment.converter = ffmpeg
        AudioSegment.ffmpeg = ffmpeg
        ffprobe = working_binary(Path(ffmpeg).with_name("ffprobe"), Path(ffmpeg).with_name("ffprobe.exe"),
                                 shutil.which("ffprobe"),
                                 "/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe")
        if ffprobe:
            AudioSegment.ffprobe = ffprobe
    else:
        print("Warning: ffmpeg not found — reading/writing audio may fail")


_configure_runtime()


# ----------------------------- helpers -----------------------------
def sanitize(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in " -_." else "_" for c in str(name)).strip()
    return cleaned or "unknown"


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


def filename_has_time(name: str, regex: str) -> bool:
    """Whether the filename contains an hour and minute, not just a date."""
    return any(len(m.groups()) >= 5 and m.group(4) and m.group(5)
               for m in re.finditer(regex, name))


def _metadata_has_time(value: str) -> bool:
    return bool(re.search(r"(?:T|\s)\d{1,2}:\d{2}|\d{8}T\d{4}", str(value)))


def _parse_meta_datetime(s: str):
    """เวลาที่ไม่มี timezone = เวลาท้องถิ่นของเครื่องอัด; เวลาที่มี timezone (เช่น MP4/M4A
    creation_time ...Z = UTC) แปลงเป็นเวลาของเครื่องนี้ ไม่งั้นในไทยจะคลาด 7 ชม."""
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
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


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
                        meta["datetime_has_time"] = bool(t)
                elif cid in (b"_PMX", b"iXML"):
                    txt = data.decode("utf-8", "replace")
                    if meta.get("datetime") is None:
                        # รับทั้งแบบ element <xmp:CreateDate>...</> และ attribute xmp:CreateDate="..."
                        mm = re.search(r"(?:xmp:CreateDate|exif:DateTimeOriginal|BWFOriginationDate)"
                                       r"""(?:>|=\s*["'])\s*([0-9:\-T ]{8,25})""", txt)
                        if mm:
                            value = mm.group(1)
                            meta["datetime"] = _parse_meta_datetime(value)
                            meta["datetime_has_time"] = _metadata_has_time(value)
                    if meta.get("lat") is None:
                        mlat = re.search(r"""exif:GPSLatitude(?:>|=\s*["'])([^<"']+)""", txt)
                        mlon = re.search(r"""exif:GPSLongitude(?:>|=\s*["'])([^<"']+)""", txt)
                        if mlat and mlon:
                            la, lo = _gps_exif(mlat.group(1)), _gps_exif(mlon.group(1))
                            if la is not None and lo is not None:
                                meta["lat"], meta["lon"] = la, lo
    except Exception:  # noqa: BLE001
        pass


def _gps_exif(s: str):
    """'12,48.0N' / '12,48,30N' / '12.8' -> ทศนิยม (exif GPS แบบองศา,ลิปดา[,ฟิลิปดา] + ทิศ)"""
    s = str(s).strip()
    m = re.match(r"(\d+),(\d+(?:\.\d+)?)(?:,(\d+(?:\.\d+)?))?\s*([NSEW]?)", s)
    if m:
        deg = float(m.group(1)) + float(m.group(2)) / 60.0 + float(m.group(3) or 0) / 3600.0
        if m.group(4) in ("S", "W"):
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
        r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)",        # place URL !3dlat!4dlon = หมุด (ก่อน @ ที่เป็นกลางจอแผนที่)
        r"@(-?\d+\.\d+),\s*(-?\d+\.\d+)",        # .../@lat,lon,zoom
        r"[?&]q=(-?\d+\.\d+),\s*(-?\d+\.\d+)",    # ...?q=lat,lon
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
    meta = {"datetime": None, "datetime_has_time": False,
            "lat": None, "lon": None, "place": None}
    # ffprobe tags (ครอบคลุม mp4/m4a/flac/ogg/mp3 และ wav บางส่วน)
    ffprobe = getattr(AudioSegment, "ffprobe", None)
    if ffprobe and Path(ffprobe).is_file():
        try:
            r = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json",
                                "-show_format", "-show_streams", str(path)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=30, creationflags=NO_WINDOW)
            data = json.loads(r.stdout or "{}")
            tags = {}
            for blk in [data.get("format", {})] + data.get("streams", []):
                for k, v in (blk.get("tags") or {}).items():
                    tags[k.lower()] = v
            if "origination_date" in tags:        # BWF bext ที่ ffprobe แยกเป็นวันที่ + เวลา
                tags["originationdate"] = f"{tags['origination_date']} {tags.get('origination_time', '')}"
            # เรียงจากแม่นสุด: quicktime มี offset ท้องถิ่น, bext = เวลาเครื่องอัด,
            # creation_time = UTC, date/icrd มักมีแต่วันที่ (จะได้เวลา 00:00)
            for k in ("com.apple.quicktime.creationdate", "originationdate", "creation_time",
                      "date_recorded", "date", "icrd"):
                if k in tags:
                    meta["datetime"] = _parse_meta_datetime(tags[k])
                    if meta["datetime"]:
                        meta["datetime_has_time"] = _metadata_has_time(tags[k])
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
        except Exception as exc:
            print(f"Warning: could not read ffprobe metadata from {path}: {exc}")
    # WAV BWF/XMP (ffprobe ไม่ดึง bext/_PMX)
    if path.suffix.lower() == ".wav":
        _read_wav_meta(path, meta)
    return meta


def recording_context_preview(path: Path, use_meta=True, dt_regex=FILENAME_DATETIME_REGEX):
    """What date/time and location will be used before manual overrides."""
    meta = read_audio_metadata(path) if use_meta else {}
    embedded_dt = meta.get("datetime")
    filename_dt = filename_datetime(path.name, dt_regex)
    filename_time = filename_has_time(path.name, dt_regex)
    if embedded_dt:
        dt = embedded_dt
        source = "embedded metadata"
        has_time = meta.get("datetime_has_time", True)
        if not has_time and filename_dt and filename_time and filename_dt.date() == dt.date():
            dt = filename_dt
            source = "metadata date + filename time"
            has_time = True
    else:
        dt = filename_dt
        source = "filename" if dt else None
        has_time = filename_time if dt else False
    if not has_time:
        started = consistent_file_start(path)
        if started and (dt is None or started.date() == dt.date()):
            dt, has_time = started, True
            source = f"{source} date + file times" if source else "file times (created + length = modified)"
    return {"datetime": dt, "datetime_source": source, "has_time": has_time,
            "lat": meta.get("lat"), "lon": meta.get("lon"), "place": meta.get("place"),
            "metadata": meta}


def consistent_file_start(path: Path):
    """เวลาเริ่มบันทึกจากระบบไฟล์ เฉพาะเมื่อ 'เวลาสร้าง + ความยาวเสียง ≈ เวลาแก้ไขล่าสุด'
    (เครื่องอัดสร้างไฟล์ตอนเริ่ม ปิดไฟล์ตอนจบ) ไฟล์ที่ถูกคัดลอกจนเวลาเปลี่ยนหรือถูกแก้ทีหลังจะไม่ผ่าน"""
    try:
        stat = path.stat()
    except OSError:
        return None
    created = getattr(stat, "st_birthtime", None)
    if created is None:
        return None
    duration = _audio_duration(path)
    if duration < 30:
        return None
    tolerance = max(5.0, min(120.0, duration * 0.02))
    if abs(created + duration - stat.st_mtime) > tolerance:
        return None
    return datetime.fromtimestamp(created).replace(microsecond=0)


def effective_overrides(context, date_override, lat, lon, start_time_override,
                        fill_missing=False):
    """Keep file metadata when GUI supplies fallback details for a mixed folder."""
    if not fill_missing:
        return date_override, lat, lon, start_time_override
    return (
        date_override if context["datetime"] is None else None,
        lat if context["lat"] is None else None,
        lon if context["lon"] is None else None,
        start_time_override if not context["has_time"] else None,
    )


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


def freq_stats(seg: AudioSegment, call_start_s=None, call_end_s=None,
               fmin: float = 150.0, fmax: float = 12000.0):
    """ความถี่เสียงนก (Hz) แบบเน้นถูกต้อง: คืน (peak, low, high) หรือ (None,None,None).
    เพื่อให้ตรงเสียงนกจริง ไม่ใช่ noise:
      1) วิเคราะห์เฉพาะช่วงที่นกร้องจริง [call_start_s, call_end_s] (ถ้าให้มา) ตัด lead/tail
      2) เอาเฉพาะ frame ที่ "ดัง" (พลังงาน >= 75th percentile) = ตัวเสียงร้อง ไม่ใช่ช่วงเงียบ
      3) spectral subtraction: ลบ spectrum พื้นหลัง (frame เงียบ/ช่วง padding) ออก = ตัดลม/noise นิ่ง
    peak = argmax หลังลบ noise, low/high = ช่วง -15 dB รอบ peak. gain ไม่กระทบ"""
    samples = np.array(seg.get_array_of_samples()).astype(np.float64)
    if seg.channels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1)
    n = samples.size
    sr = seg.frame_rate
    win = 2048
    if n < win or sr <= 0:
        return None, None, None
    hop = win // 4
    w = np.hanning(win)
    starts = list(range(0, n - win + 1, hop))
    mags = np.stack([np.abs(np.fft.rfft(samples[i:i + win] * w)) for i in starts])
    centers = (np.array(starts) + win / 2.0) / sr        # เวลากลาง frame (วินาที)
    energy = mags.sum(axis=1)

    # frame ที่อยู่ในช่วงนกร้องจริง vs พื้นหลัง (padding นอกช่วง)
    if call_start_s is not None and call_end_s is not None and call_end_s > call_start_s:
        in_call = (centers >= call_start_s) & (centers <= call_end_s)
        bg = ~in_call
    else:
        in_call = np.ones(len(starts), dtype=bool)
        bg = np.zeros(len(starts), dtype=bool)

    # ในช่วงนก เอาเฉพาะ frame ดัง (>=75th pct) = ตัวเสียงร้อง
    if in_call.sum() >= 4:
        thr = np.percentile(energy[in_call], 75)
        loud = in_call & (energy >= thr)
    else:
        loud = in_call
    if loud.sum() == 0:
        loud = in_call
    sig = mags[loud].mean(axis=0)

    # noise reference: frame padding (ถ้ามี) ไม่งั้นใช้ frame เงียบสุด 25%
    if bg.sum() >= 2:
        noise = mags[bg].mean(axis=0)
    else:
        q = np.percentile(energy, 25)
        quiet = energy <= q
        noise = mags[quiet].mean(axis=0) if quiet.sum() >= 2 else np.zeros_like(sig)
    denoised = np.clip(sig - noise, 0.0, None)

    freqs = np.fft.rfftfreq(win, 1.0 / sr)
    band = (freqs >= fmin) & (freqs <= min(fmax, sr / 2.0))
    fb = freqs[band]
    spec = denoised[band]
    if fb.size == 0 or spec.max() <= 0:
        spec = sig[band]                      # fallback ถ้า subtract แล้วว่าง
        if fb.size == 0 or spec.max() <= 0:
            return None, None, None

    # เลือก peak ที่ "โดดชัด" (tonal = เสียงนก) ไม่ใช่แค่พลังงานสูงสุด (อาจเป็นลม broadband)
    peak_i = int(np.argmax(spec))
    spec_db = 20.0 * np.log10(spec / spec.max() + 1e-9)
    try:
        from scipy.signal import find_peaks, peak_prominences
        pk, _ = find_peaks(spec_db, prominence=6.0, distance=3)
        pk = pk[(spec_db[pk] > -20.0)]        # ต้องดังพอ (ไม่ใช่ยอดจิ๋วใน noise floor)
        if pk.size:
            prom = peak_prominences(spec_db, pk)[0]
            peak_i = int(pk[int(np.argmax(prom))])   # ยอดที่ prominence สูงสุด = tonal สุด
    except Exception:  # noqa: BLE001  ไม่มี scipy -> ใช้ argmax
        pass
    peak_hz = float(fb[peak_i])

    # low/high = ขอบรอบ peak ที่ยังดังกว่า peak - 15 dB (เดินซ้าย/ขวาจนหลุด)
    thr_db = spec_db[peak_i] - 15.0
    lo = peak_i
    while lo > 0 and spec_db[lo - 1] >= thr_db:
        lo -= 1
    hi = peak_i
    while hi < len(spec_db) - 1 and spec_db[hi + 1] >= thr_db:
        hi += 1
    return round(peak_hz), round(float(fb[lo])), round(float(fb[hi]))


def conf_to_stars(c: float) -> int:
    """provisional rating หยาบ ๆ จาก confidence (1-4, ไม่ให้ 5 เพราะเป็น auto)"""
    if c >= 0.9:
        return 4
    if c >= 0.75:
        return 3
    if c >= 0.6:
        return 2
    return 1


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detection_boxes(items, occurrence, clip_start_s, clip: AudioSegment):
    """สิ่งที่วาดบน spectrogram ของคลิป (วินาทีนับจากต้นคลิป):
      windows = ช่วงที่ BirdNET เจอชนิดนี้ [[t0, t1, max_conf], ...] (หน้าต่าง 3 วิที่ซ้อนกันรวมเป็นช่วงเดียว)
      sounds  = เสียงที่ดังเกินพื้นหลังชัดเจนภายในช่วงนั้น [[t0, t1, f_low, f_high], ...]
    BirdNET ไม่บอกว่าเสียงอยู่ตรงไหนในหน้าต่าง/ความถี่ใด กรอบ sounds จึงเป็นค่าประมาณ
    และอาจรวมเสียงชนิดอื่นที่ดังในช่วงเดียวกัน"""
    windows = []
    for s, e, c in sorted(items):
        if s < occurrence["start"] - 1e-6 or e > occurrence["end"] + 1e-6:
            continue
        if windows and s <= windows[-1][1]:
            windows[-1][1], windows[-1][2] = max(windows[-1][1], e), max(windows[-1][2], c)
        else:
            windows.append([s, e, c])
    length = len(clip) / 1000.0
    windows = [[round(max(0.0, s - clip_start_s), 2), round(min(length, e - clip_start_s), 2), round(c, 3)]
               for s, e, c in windows]
    sounds = []
    for t0, t1, _conf in windows:
        pad = 3.0                         # วิเคราะห์เฉพาะรอบ ๆ ช่วง (คลิปต่อเนื่องอาจยาวหลายนาที)
        part_start = max(0.0, t0 - pad)
        part = clip[int(part_start * 1000):int((t1 + pad) * 1000)]
        sounds += [[round(a + part_start, 2), round(b + part_start, 2), lo, hi]
                   for a, b, lo, hi in _sound_events(part, t0 - part_start, t1 - part_start,
                                                     max_boxes=12 + int(t1 - t0))]
    return {"windows": windows, "sounds": sounds}


def _sound_events(seg: AudioSegment, t_from, t_to, fmin=150.0, fmax=12000.0,
                  excess_db=12.0, max_boxes=12):
    """จุดใน spectrogram ที่ดังกว่าพื้นหลัง (median) ของความถี่นั้นเกิน excess_db -> กรอบ [t0, t1, f_low, f_high]"""
    from scipy import ndimage, signal
    samples = np.array(seg.get_array_of_samples()).astype(np.float32)
    if seg.channels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1)
    sr = seg.frame_rate
    n_fft = 1024 if sr <= 32000 else 2048
    if samples.size < n_fft * 4:
        return []
    freqs, times, z = signal.stft(samples, fs=sr, nperseg=n_fft, noverlap=n_fft * 3 // 4,
                                  boundary=None, padded=False)
    # เฉลี่ยจุดข้างเคียงก่อน กันเม็ดสุ่มของเสียงรบกวนเกินเกณฑ์
    power = 10 * np.log10(ndimage.uniform_filter(np.abs(z) ** 2, size=(3, 3)) + 1e-12)
    floor = np.median(power, axis=1, keepdims=True)                 # พื้นหลังของแต่ละความถี่
    band = (freqs >= fmin) & (freqs <= min(fmax, sr / 2))
    inside = (times >= t_from) & (times <= t_to)
    mask = (power - floor > excess_db) & band[:, None] & inside[None, :]
    mask = ndimage.binary_closing(mask, structure=np.ones((3, 5)))   # ต่อชิ้นส่วนของโน้ตเดียวกัน
    mask = ndimage.binary_opening(mask, structure=np.ones((2, 3)))   # ตัดจุดรบกวนเล็ก ๆ
    labels, _count = ndimage.label(mask)
    found = []
    for index, region in enumerate(ndimage.find_objects(labels), start=1):
        rows, cols = region
        t0, t1 = float(times[cols.start]), float(times[cols.stop - 1])
        if t1 - t0 < 0.03 or (labels[region] == index).sum() < 20:
            continue
        strength = float((power[region] - floor[rows])[labels[region] == index].max())
        found.append([strength, t0, t1, float(freqs[rows.start]), float(freqs[rows.stop - 1])])
    strongest = sorted(_merge_touching(found), key=lambda item: -item[0])[:max_boxes]
    return sorted([t0, t1, round(low), round(high)] for _strength, t0, t1, low, high in strongest)


def _merge_touching(found, gap_s=0.03, freq_slack=0.1):
    """รวมชิ้นส่วนของโน้ตเดียวกัน (ซ้อนกันทั้งเวลาและความถี่) เป็นกรอบเดียว: [strength, t0, t1, low, high]"""
    boxes = [list(item) for item in found]
    merged = True
    while merged:
        merged = False
        kept = []
        for box in sorted(boxes, key=lambda item: item[1]):
            for other in kept:
                if (box[1] <= other[2] + gap_s and other[1] <= box[2] + gap_s and
                        box[3] <= other[4] * (1 + freq_slack) and other[3] <= box[4] * (1 + freq_slack)):
                    other[:] = [max(other[0], box[0]), min(other[1], box[1]), max(other[2], box[2]),
                                min(other[3], box[3]), max(other[4], box[4])]
                    merged = True
                    break
            else:
                kept.append(box)
        boxes = kept
    return boxes


def save_mel_spectrogram(seg: AudioSegment, png_path: Path, title: str, boxes=None) -> bool:
    """mel-spectrogram .png (เรียกเฉพาะใน subprocess ที่ไม่มี TensorFlow)"""
    try:
        import librosa
        import librosa.display
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        print(f"    (skipping spectrogram: import failed -> {exc})")
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
    # แถบบน = ช่วงที่ BirdNET เจอชนิดนี้, กรอบฟ้า = เสียงที่ดังเด่นในช่วงนั้น
    # (แกนเป็นวินาที/Hz จึงวาดด้วยค่าจริงได้แม้แกนตั้งเป็น mel)
    if boxes:
        from matplotlib.patches import Rectangle
        bar_low = fmax * 0.86
        for t0, t1, conf in boxes.get("windows", ()):
            ax.add_patch(Rectangle((t0, bar_low), t1 - t0, fmax - bar_low, color="white",
                                   alpha=0.35, linewidth=0))
            ax.text(t0, bar_low, f" BirdNET {conf:.2f}", color="white", fontsize=8,
                    va="bottom", ha="left", clip_on=True)
        for t0, t1, low, high in boxes.get("sounds", ()):
            low, high = low * 0.9, min(bar_low, high * 1.1)
            ax.add_patch(Rectangle((t0, low), max(t1 - t0, 0.02), high - low, fill=False,
                                   edgecolor="#00e5ff", linewidth=1.4))
        if boxes.get("sounds"):
            # BirdNET ตัดสินทั้งหน้าต่าง 3 วิ ไม่บอกว่าเสียงไหนเป็นของชนิดนี้ (ทดลองลบเสียงทีละกลุ่มแล้วแยกไม่ได้)
            ax.text(0.005, 0.015, "cyan = loud sounds in the BirdNET span; may be other birds or insects",
                    transform=ax.transAxes, color="#00e5ff", fontsize=7, va="bottom", ha="left",
                    bbox={"facecolor": "black", "alpha": 0.6, "pad": 2, "linewidth": 0})
    ax.set_title(title, fontsize=9)
    fig.colorbar(img, ax=ax, format="%+2.0f dB")
    fig.tight_layout()
    # เก็บกรอบไว้ในไฟล์ภาพ: ถ้าไม่ตรงกับคลิปปัจจุบัน gen_spectrograms จะสร้างใหม่
    fig.savefig(png_path, dpi=110, metadata={"Description": _boxes_tag(boxes)})
    plt.close(fig)
    return True


SPECTROGRAM_STYLE = 3    # เพิ่มเมื่อเปลี่ยนวิธีวาด -> ภาพเก่าถูกสร้างใหม่


def _boxes_tag(boxes) -> str:
    return f"boxes v{SPECTROGRAM_STYLE} " + json.dumps(boxes, sort_keys=True, separators=(",", ":"))


def _png_description(png: Path) -> str:
    try:
        from PIL import Image
        with Image.open(png) as image:
            return str(image.text.get("Description", ""))
    except Exception:  # noqa: BLE001  ภาพเสีย/อ่านไม่ได้ = สร้างใหม่
        return ""


def _row_boxes(row):
    """'Detection boxes' จาก summary -> dict (None ถ้าไม่มีข้อมูล เช่น summary รุ่นเก่า)"""
    value = None if row is None else row.get("Detection boxes")
    if value is None or (isinstance(value, float) and pd.isna(value)) or not str(value).strip():
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


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
        wav_paths = []
        summ = d / "summary.xlsx"
        fallback_scan = not summ.exists()
        if summ.exists():
            try:
                sdf = pd.read_excel(summ)
                for _, r in sdf.iterrows():
                    wav = _safe_generated_path(d, str(r["File"]))
                    if wav.is_file() and wav.suffix.lower() == ".wav":
                        wav_paths.append(wav)
                        meta[wav] = r
            except Exception as exc:  # noqa: BLE001
                print(f"  (could not read summary.xlsx, using filenames: {exc})")
                wav_paths = []
                fallback_scan = True
        if fallback_scan:
            wav_paths = [wav for wav in d.rglob("*.wav")
                         if "Ready" not in wav.relative_to(d).parts]
        for wav in sorted(set(wav_paths)):
            png = wav.with_suffix(".png")
            boxes = _row_boxes(meta.get(wav))
            if png.exists() and (boxes is None or _png_description(png) == _boxes_tag(boxes)):
                skipped += 1
                continue
            title = _spectrogram_title(meta.get(wav), wav)
            try:
                seg = AudioSegment.from_file(str(wav))
                if save_mel_spectrogram(seg, png, title, boxes):
                    made += 1
                    print(f"  spec -> {wav.name}")
                else:
                    failed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"  !! spectrogram {wav.name}: {exc}")
    print(f"spectrograms done: created {made}, skipped existing {skipped}, failed {failed}")
    return failed


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
        return _seg_from_sf(data, info.samplerate, sw), sub
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
        print("group: no clips")
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
    print(f"group: merged {len(segs)} clips -> {out_path} ({len(out)/1000:.0f}s)")


def _depth(subtype):
    """คง bit depth เดิม -> (read_dtype, sample_width, export_subtype). pydub ไม่รับ 3-byte
    จึงอ่าน 24/32-bit เป็น int32 แล้ว export กลับเป็น PCM_24/PCM_32 ด้วย soundfile.
    FLOAT/DOUBLE ต้องอ่านเป็น float32 แล้ว scale เป็น int32 เอง — soundfile ไม่ scale
    float->int ให้ (มันปัดค่า float [-1,1] เป็น 0 = คลิปเงียบสนิท) ดู _seg_from_sf"""
    s = (subtype or "").upper()
    if "PCM_24" in s:
        return "int32", 4, "PCM_24"
    if "PCM_32" in s:
        return "int32", 4, "PCM_32"
    if "FLOAT" in s or "DOUBLE" in s:
        return "float32", 4, "PCM_24"      # อ่าน float แล้ว scale -> 24-bit
    return "int16", 2, "PCM_16"            # 16-bit, mp3, ogg ฯลฯ


def _seg_from_sf(data, sr, sw):
    """สร้าง AudioSegment จาก array ที่ soundfile อ่านมา — ถ้าเป็น float [-1,1] scale เป็น
    int32 ก่อน (pydub รับแต่ int; soundfile ไม่ scale float->int ให้)"""
    if str(data.dtype).startswith("float"):
        data = (np.clip(data, -1.0, 1.0) * 2147483647.0).astype("<i4")
        sw = 4
    return AudioSegment(np.ascontiguousarray(data).tobytes(), frame_rate=sr,
                        sample_width=sw, channels=data.shape[1])


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
    estimated_bytes = (e - s) * info.channels * sw
    if estimated_bytes > MAX_RAW_SEGMENT_BYTES:
        raise ValueError(
            f"Continuous span needs about {estimated_bytes / 1e6:.0f} MB of raw audio. "
            "Use a shorter span or prepare this long continuous recording in an audio editor."
        )
    data, _meta = sf.read(str(path), start=s, stop=e, dtype=dtype, always_2d=True)
    return _seg_from_sf(data, sr, sw)


def _export_clip(clip: AudioSegment, out_path: Path, fmt: str, subtype):
    """export คง bit depth ด้วย soundfile (รองรับ PCM_24); ไม่งั้น fallback pydub"""
    temporary = out_path.with_name(out_path.stem + ".tmp" + out_path.suffix)
    try:
        if subtype and fmt.lower() in ("wav", "flac", "aiff", "aif"):
            import soundfile as sf
            arr = np.array(clip.get_array_of_samples())
            if clip.channels > 1:
                arr = arr.reshape((-1, clip.channels))
            sf.write(str(temporary), arr, clip.frame_rate, subtype=subtype)
        else:
            clip.export(temporary, format=fmt)
        os.replace(temporary, out_path)
    finally:
        temporary.unlink(missing_ok=True)


def _already_processed(date_folder: Path, sid: str, settings: str) -> bool:
    """Skip only when this source, analysis settings, and generated clips match."""
    summ = date_folder / "summary.xlsx"
    if not summ.exists():
        return False
    try:
        df = pd.read_excel(summ, dtype={"Source ID": str})
        if not {"Source ID", "File", "Generated sha256", "Analysis settings"}.issubset(df.columns):
            return False
        matching = df[df["Source ID"].astype(str) == sid]
        if matching.empty or not matching["Analysis settings"].eq(settings).all():
            return False

        def intact(row):
            clip = date_folder / str(row["File"])
            if clip.is_file():
                return file_sha256(clip) == str(row["Generated sha256"])
            return row.get("Review status") == "Rejected"   # ผู้ตรวจลบคลิปที่ไม่ใช้ทิ้งเอง
        return all(intact(row) for _, row in matching.iterrows())
    except Exception:  # noqa: BLE001
        return False


def _approved_under_other_settings(date_folder: Path, sid: str, settings: str) -> bool:
    """Do not silently replace a reviewed result when analysis defaults change."""
    summary = date_folder / "summary.xlsx"
    if not summary.exists():
        return False
    df = pd.read_excel(summary, dtype={"Source ID": str})
    if not {"Source ID", "Review status"}.issubset(df.columns):
        return False
    matching = df[df["Source ID"].astype(str) == sid]
    if matching.empty or not matching["Review status"].eq("Approved").any():
        return False
    return "Analysis settings" not in matching.columns or not matching["Analysis settings"].eq(settings).all()


def _previous_hashes(date_folder: Path, sid: str) -> dict:
    summary = date_folder / "summary.xlsx"
    if not summary.exists():
        return {}
    df = pd.read_excel(summary, dtype={"Source ID": str})
    if not {"Source ID", "File", "Generated sha256"}.issubset(df.columns):
        return {}
    return {str(row["File"]): str(row["Generated sha256"])
            for _, row in df[df["Source ID"] == sid].iterrows()}


def _safe_generated_path(date_folder: Path, relative: str) -> Path:
    candidate = (date_folder / relative).resolve()
    if not candidate.is_relative_to(date_folder.resolve()):
        raise ValueError("Generated clip path is outside output folder")
    return candidate


def _check_generated_collision(date_folder: Path, out_path: Path, previous: dict, new_hash=None):
    """ทับได้เฉพาะไฟล์ของโปรแกรมเอง: summary บันทึก hash ไว้ หรือเหมือนคลิปใหม่ทุกไบต์
    (เหลือจากรอบที่ถูกหยุดกลางคัน ก่อนเขียน summary)"""
    if not out_path.exists():
        return
    relative = str(out_path.relative_to(date_folder))
    current = file_sha256(out_path)
    if current != previous.get(relative) and current != new_hash:
        raise FileExistsError(f"Existing clip was changed or is not tracked: {out_path}")


def _audio_duration(path: Path) -> float:
    """ความยาวไฟล์ (วินาที), 0 ถ้าอ่านไม่ได้"""
    try:
        import soundfile as sf
        return float(sf.info(str(path)).duration)
    except Exception:  # noqa: BLE001
        pass
    ffprobe = getattr(AudioSegment, "ffprobe", None)
    if ffprobe:
        try:
            r = subprocess.run([ffprobe, "-v", "quiet", "-show_entries", "format=duration",
                                "-of", "default=nw=1:nk=1", str(path)],
                               capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
            return float(r.stdout.strip())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return 0.0


def source_id(path: Path) -> str:
    """Fast content fingerprint stable when a recording folder is moved."""
    stat = path.stat()
    digest = hashlib.sha256(str(stat.st_size).encode("ascii"))
    with path.open("rb") as source:
        digest.update(source.read(128 * 1024))
        if stat.st_size > 128 * 1024:
            source.seek(max(0, stat.st_size - 128 * 1024))
            digest.update(source.read(128 * 1024))
    return digest.hexdigest()[:12]


def require_recording_context(files, date_override, use_meta, lat_arg, lon_arg):
    """Fail before loading BirdNET when date or location was not supplied."""
    if date_override and lat_arg is not None and lon_arg is not None:
        return
    missing_dates, missing_coords = [], []
    for path in files:
        context = recording_context_preview(path, use_meta)
        if not date_override and context["datetime"] is None:
            missing_dates.append(str(path))
        if ((lat_arg is None and context["lat"] is None) or
                (lon_arg is None and context["lon"] is None)):
            missing_coords.append(str(path))
    messages = []
    if missing_dates:
        messages.append("Recording date missing from audio metadata and filename. Enter the date "
                        "in the Date field (YYYYMMDD), or use --date YYYYMMDD:\n" +
                        "\n".join(f"  - {name}" for name in missing_dates))
    if missing_coords:
        messages.append("Recording location missing from audio metadata. Enter "
                        "coordinates in the GUI, or use --coords LAT,LON:\n" +
                        "\n".join(f"  - {name}" for name in missing_coords))
    if messages:
        raise ValueError("\n\n".join(messages))
def process_file(analyzer, audio_path: Path, out_root: Path, *, lat_arg, lon_arg,
                 cfg_lat, cfg_lon, date_override, use_meta, min_conf, gap, lead, tail,
                 target_dbfs, fmt, make_mono, incl_alt, place_arg, dt_regex, force=False,
                 use_filetime=False, cut_unknown=False, unknown_floor=UNKNOWN_MIN_CONF,
                 keep_continuous=False, start_time_override=None,
                 overlap=OVERLAP_SEC, out_of_range_min_conf=OUT_OF_RANGE_MIN_CONF,
                 site_habitats=()):
    """
    วิเคราะห์ + ตัดคลิป 1 ไฟล์ คืน (rows, date_folder); rows = None เมื่อข้ามเพราะเคยทำแล้ว
    วัน/พิกัด/สถานที่: argument > metadata ไฟล์ > ชื่อไฟล์ > เวลาไฟล์ > default
    spectrogram สร้างทีหลังใน subprocess แยก (กัน native crash)
    """
    context = recording_context_preview(audio_path, use_meta, dt_regex)
    meta = context["metadata"]
    if date_override is None and context["datetime"] is None:
        raise ValueError("Recording date missing from metadata and filename; enter --date YYYYMMDD")
    if ((lat_arg is None and meta.get("lat") is None) or
            (lon_arg is None and meta.get("lon") is None)):
        raise ValueError("Recording location missing from metadata; enter --coords LAT,LON")
    sid = source_id(audio_path)

    # ---- วันเวลาเริ่มอัด ----  ลำดับ: --date > (metadata > ชื่อไฟล์ ถ้าไม่บังคับ filetime) > เวลาไฟล์
    fn_dt = None if use_filetime else filename_datetime(audio_path.name, dt_regex)
    if context["datetime"] and not use_filetime:   # metadata / ชื่อไฟล์ / เวลาไฟล์ที่สอดคล้องกัน
        rec_dt, dt_src = context["datetime"], context["datetime_source"]
    elif fn_dt:
        rec_dt, dt_src = fn_dt, "filename time"
    else:
        # mtime = ตอนเครื่องอัดปิดไฟล์ = "จบ" การบันทึก -> ลบความยาวไฟล์ออก
        # เวลาสร้างไฟล์ (st_birthtime บน mac; st_ctime บน Windows รุ่นเก่า — บน mac ctime
        # คือเวลาแก้ metadata ไม่ใช่เวลาสร้าง) ใช้ถ้าเก่ากว่า; copy มาใหม่ค่านี้จะใหม่กว่าเลยไม่ถูกเลือก
        stt = audio_path.stat()
        starts = [stt.st_mtime - _audio_duration(audio_path)]
        created = getattr(stt, "st_birthtime", stt.st_ctime if os.name == "nt" else None)
        if created is not None:
            starts.append(created)
        rec_dt = datetime.fromtimestamp(min(starts)).replace(microsecond=0)
        dt_src = "file timestamp (unverified recording time)"
    if date_override:
        rec_dt = rec_dt.replace(year=date_override.year, month=date_override.month,
                                day=date_override.day)
        dt_src = f"manual date; {dt_src}"
    if start_time_override:
        rec_dt = rec_dt.replace(hour=start_time_override.hour, minute=start_time_override.minute,
                                second=start_time_override.second)
        dt_src = f"{dt_src}; manual start time"

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
    date_folder = out_root / (date_tag + ("_v3preview" if getattr(analyzer, "direct_prediction", False) else ""))

    settings = {
        "model": getattr(analyzer, "model_name", ""), "date": rec_dt.date().isoformat(),
        "lat": lat, "lon": lon, "min_conf": min_conf, "overlap": overlap,
        "out_of_range_min_conf": out_of_range_min_conf, "gap": gap,
        "lead": lead, "tail": tail, "target_dbfs": target_dbfs,
        "format": fmt, "mono": make_mono, "alt_species": incl_alt,
        "cut_unknown": cut_unknown, "unknown_floor": unknown_floor,
        "keep_continuous": keep_continuous,
        "detection_boxes": True,   # ผลเก่าไม่มีกรอบ -> รันซ้ำจะวิเคราะห์ใหม่ (ผลตรวจเดิมยังอยู่)
    }
    if site_habitats:                     # เพิ่มเฉพาะเมื่อระบุ ผลเดิมที่ไม่ได้ระบุจะได้ไม่ต้องวิเคราะห์ใหม่
        settings["habitat"] = list(site_habitats)
    settings = json.dumps(settings, sort_keys=True, separators=(",", ":"))
    # ข้ามเฉพาะเมื่อข้อมูลต้นทาง การตั้งค่า และคลิปที่สร้างไว้ตรงกัน
    if not force and _already_processed(date_folder, sid, settings):
        print(f"\n=== {audio_path.name} ===  skip: already processed in {date_tag}/ (use --force to redo)")
        return None, date_folder
    if not force and _approved_under_other_settings(date_folder, sid, settings):
        print(f"\n=== {audio_path.name} ===  skip: approved result uses older settings; "
              "choose another output folder or use --force to replace it")
        return None, date_folder
    previous_hashes = _previous_hashes(date_folder, sid)
    staging_dir = date_folder / ".staging" / sid
    shutil.rmtree(staging_dir, ignore_errors=True)
    staged_files = []

    def stage_clip(clip, out_path):
        staged = staging_dir / out_path.relative_to(date_folder)
        staged.parent.mkdir(parents=True, exist_ok=True)
        _export_clip(clip, staged, fmt, export_subtype)
        digest = file_sha256(staged)
        _check_generated_collision(date_folder, out_path, previous_hashes, digest)
        staged_files.append((staged, out_path, digest))
        return digest

    print(f"\n=== {audio_path.name} ===")
    print(f"  recorded {rec_dt:%Y-%m-%d %H:%M:%S} (from {dt_src}) | "
          f"coords {lat},{lon} ({lat_src})"
          + (f" | place {place}" if place else "") + f" | min_conf {min_conf}")

    # เปิด cut_unknown -> analyze ที่ threshold ต่ำ (floor) เพื่อเก็บเสียงที่ BirdNET
    # มั่นใจไม่พอด้วย แล้วค่อยแยก: conf >= min_conf = ID ได้, [floor, min_conf) = unknown
    analysis_min = min(min_conf, unknown_floor) if cut_unknown else min_conf
    print("  analyzing audio (long files may take several minutes) ...")
    if getattr(analyzer, "direct_prediction", False):
        raw = analyzer.detections(audio_path, overlap, analysis_min,
                                  lat, lon, filter_date)
    else:
        from birdnetlib import Recording  # lazy: no BirdNET dependency in spectrogram mode
        recording = Recording(analyzer, str(audio_path),
                              lat=lat, lon=lon, date=filter_date, min_conf=analysis_min,
                              overlap=overlap, return_all_detections=True)
        recording.analyze()
        raw = recording.detections
    def in_area(d):
        # ชนิดที่โมเดลพื้นที่ไม่รู้จัก (แมลง กบ สัตว์อื่น ชื่อไม่ตรง) ถือว่าไม่ผ่าน
        return (d.get("location_filter_available", True) and
                d.get("is_predicted_for_location_and_date", True))

    def kept(d):
        return in_area(d) or (out_of_range_min_conf is not None and
                              d["confidence"] >= out_of_range_min_conf)

    dets = [d for d in raw if d["confidence"] >= min_conf and kept(d)]
    filtered_out = [d for d in raw if d["confidence"] >= min_conf and not kept(d)]
    low_dets = [d for d in raw if d["confidence"] < min_conf and in_area(d)] if cut_unknown else []
    unexpected = sum(not in_area(d) for d in dets)
    print(f"  found {len(dets)} detections"
          + (f" ({unexpected} outside the location/date filter; review carefully)" if unexpected else "")
          + (f" + {len(low_dets)} low-conf -> _Unknown" if cut_unknown else ""))
    if filtered_out:
        names = sorted({d["common_name"] for d in filtered_out})
        shown = ", ".join(names[:5]) + (" ..." if len(names) > 5 else "")
        print(f"  location/date filter excluded {len(filtered_out)} candidate(s): {shown}")

    # ---- ถิ่นอาศัยของจุดบันทึก (AVONET) ---- นกน้ำในจุดที่ไม่มีน้ำต้องคะแนนสูงพอจะเป็นนกบินผ่าน
    def habitat_of(d):
        return species_habitat(d["scientific_name"], d.get("geo_scientific_name"))

    if site_habitats:
        habitat_out = [d for d in dets if fits_site(habitat_of(d), site_habitats) is False and
                       d["confidence"] < HABITAT_MISMATCH_MIN_CONF]
        if habitat_out:
            dets = [d for d in dets if d not in habitat_out]
            names = sorted({f"{d['common_name']} ({habitat_of(d)})" for d in habitat_out})
            shown = ", ".join(names[:5]) + (" ..." if len(names) > 5 else "")
            print(f"  site habitat ({'+'.join(site_habitats)}) excluded {len(habitat_out)} "
                  f"candidate(s): {shown}")
    if not dets and not low_dets:
        print("  no confident bird sounds — skipping this file (try lowering --min-conf)")
        return [], date_folder

    print("  loading source audio (full quality) ...")
    src_mode, src = _open_source(audio_path)
    if src_mode == "sf":
        total_ms = int(src.frames / src.samplerate * 1000)
        export_subtype = _depth(src.subtype)[2]
        print(f"    (streaming per segment: {src.samplerate}Hz {src.channels}ch {src.subtype} -> {export_subtype})")
    else:
        total_ms = len(src)
        export_subtype = None

    all_dets = [(d["start_time"], d["end_time"], d["common_name"],
                 d["scientific_name"], d["confidence"]) for d in dets]
    by_species = defaultdict(list)
    species_expected = {}
    species_habitats = {}
    for d in dets:
        key = (d["common_name"], d["scientific_name"])
        by_species[key].append(
            (d["start_time"], d["end_time"], d["confidence"]))
        species_expected[key] = (d.get("is_predicted_for_location_and_date", True)
                                 if d.get("location_filter_available", True) else None)
        species_habitats[key] = habitat_of(d)

    rows = []
    for (common, sci), items in sorted(by_species.items()):
        occ = merge_occurrences(items, gap)
        if keep_continuous and len(occ) > 1:
            print(f"  preserving continuous recording of {common}; confirm it is one individual")
            occ = [{"start": occ[0]["start"], "end": occ[-1]["end"],
                    "max_conf": max(o["max_conf"] for o in occ),
                    "n_det": sum(o["n_det"] for o in occ)}]
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
            stats_clip = clip[:15000]
            snr = estimate_snr(stats_clip)
            peak_hz, flo, fhi = freq_stats(stats_clip, o["start"] - start_s, o["end"] - start_s)
            boxes = detection_boxes(items, o, start_s, clip)

            clip = normalize_to(clip, target_dbfs)

            occ_clock = rec_dt + timedelta(seconds=o["start"])
            hhmm = occ_clock.strftime("%H%M")
            genus_sp = sanitize(sci).replace(" ", ".")
            base = f"{date_tag}_{hhmm}_{genus_sp}_{sid}_{idx:03d}_{DEFAULT_RATING}"
            out_path = sp_dir / f"{base}.{fmt}"
            generated_hash = stage_clip(clip, out_path)

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
                "Expected by location/date": (
                    "Not checked — verify by ear" if species_expected[(common, sci)] is None
                    else "Yes" if species_expected[(common, sci)] else "No — verify by ear"
                ),
                "Species habitat": species_habitats[(common, sci)],
                "Fits site habitat": {True: "Yes", False: "No — verify by ear", None: ""}[
                    fits_site(species_habitats[(common, sci)], site_habitats)],
                "Alt species 1": alt1[0],
                "Alt1 conf": round(alt1[1], 3) if alt1[1] != "" else "",
                "Alt species 2": alt2[0],
                "Alt2 conf": round(alt2[1], 3) if alt2[1] != "" else "",
                "Peak dBFS": round(peak_dbfs, 1),
                "Clipping": "YES" if clipping else "",
                "SNR (dB approx, first 15s)": snr if snr is not None else "",
                "Peak freq (Hz)": peak_hz if peak_hz is not None else "",
                "Freq low (Hz)": flo if flo is not None else "",
                "Freq high (Hz)": fhi if fhi is not None else "",
                "Detection boxes": json.dumps(boxes),
                "AI confidence band (1-4)": conf_to_stars(o["max_conf"]),
                "Place": place or "",
                "File": str(out_path.relative_to(date_folder)),
                "Generated sha256": generated_hash,
                "Source file": audio_path.name,
                "Source path": str(audio_path.resolve()),
                "Source ID": sid,
                "Analysis settings": settings,
                "Date/time source": dt_src,
                "Latitude": lat,
                "Longitude": lon,
                "Review status": "Pending",
            })
            print(f"    -> {common:<28} #{idx:02d} {start_clock} {dur:4.0f}s "
                  f"conf {o['max_conf']:.2f}  peak {peak_dbfs:4.1f}dB"
                  + ("  CLIP!" if clipping else ""))

    # ---- เสียงที่ BirdNET มั่นใจไม่พอ -> _Unknown/ (ไว้ฟัง/ให้ผู้เชี่ยวชาญเทียบเอง) ----
    if cut_unknown and low_dets:
        conf_windows = [(d["start_time"], d["end_time"]) for d in dets]
        unk_items = [(d["start_time"], d["end_time"], d["confidence"]) for d in low_dets]
        unk_dir = date_folder / "_Unknown"
        made = 0
        for o in merge_occurrences(unk_items, gap):
            # ข้ามถ้าทับช่วง detection ที่มั่นใจแล้ว (น่าจะตัวเดียวกัน/ชนิดสำรอง)
            if any(o["start"] <= we and o["end"] >= ws for ws, we in conf_windows):
                continue
            start_s = max(0.0, o["start"] - lead)
            end_s = min(total_ms / 1000.0, o["end"] + tail)
            if end_s <= start_s:
                continue
            clip = (_read_segment(audio_path, src, start_s, end_s) if src_mode == "sf"
                    else src[int(start_s * 1000):int(end_s * 1000)])
            if clip is None or len(clip) == 0:
                continue
            if make_mono and clip.channels > 1:
                clip = clip.set_channels(1)
            peak_dbfs = clip.max_dBFS
            clipping = peak_dbfs >= -0.1
            stats_clip = clip[:15000]
            snr = estimate_snr(stats_clip)
            peak_hz, flo, fhi = freq_stats(stats_clip, o["start"] - start_s, o["end"] - start_s)
            boxes = detection_boxes(unk_items, o, start_s, clip)
            clip = normalize_to(clip, target_dbfs)
            # BirdNET เดาชนิด conf สูงสุดในช่วงนี้ (แค่ใบ้ ไม่ยืนยัน)
            guess = max((d for d in low_dets
                         if d["end_time"] >= o["start"] and d["start_time"] <= o["end"]),
                        key=lambda d: d["confidence"], default=None)
            made += 1
            unk_dir.mkdir(parents=True, exist_ok=True)
            occ_clock = rec_dt + timedelta(seconds=o["start"])
            hhmm = occ_clock.strftime("%H%M")
            base = f"{date_tag}_{hhmm}_UNKNOWN_{sid}_{made:03d}_{DEFAULT_RATING}"
            out_path = unk_dir / f"{base}.{fmt}"
            generated_hash = stage_clip(clip, out_path)
            g_common = guess["common_name"] if guess else ""
            g_conf = round(guess["confidence"], 3) if guess else ""
            start_clock = occ_clock.strftime("%H:%M:%S")
            dur = round(len(clip) / 1000.0, 1)
            rows.append({
                "Species (common)": "_Unknown",
                "Species (scientific)": "",
                "Occurrence #": made,
                "Start offset (s)": round(o["start"], 1),
                "Clock time": start_clock,
                "End offset (s)": round(o["end"], 1),
                "Duration (s)": dur,
                "Max confidence": round(o["max_conf"], 3),
                "Expected by location/date": "",
                "Alt species 1": g_common,       # BirdNET เดา (conf ต่ำ ไม่ยืนยัน)
                "Alt1 conf": g_conf,
                "Alt species 2": "",
                "Alt2 conf": "",
                "Peak dBFS": round(peak_dbfs, 1),
                "Clipping": "YES" if clipping else "",
                "SNR (dB approx, first 15s)": snr if snr is not None else "",
                "Peak freq (Hz)": peak_hz if peak_hz is not None else "",
                "Freq low (Hz)": flo if flo is not None else "",
                "Freq high (Hz)": fhi if fhi is not None else "",
                "Detection boxes": json.dumps(boxes),
                "AI confidence band (1-4)": "",
                "Place": place or "",
                "File": str(out_path.relative_to(date_folder)),
                "Generated sha256": generated_hash,
                "Source file": audio_path.name,
                "Source path": str(audio_path.resolve()),
                "Source ID": sid,
                "Analysis settings": settings,
                "Date/time source": dt_src,
                "Latitude": lat,
                "Longitude": lon,
                "Review status": "Pending",
            })
            print(f"    -> {'_Unknown':<28} #{made:02d} {start_clock} {dur:4.0f}s "
                  f"maybe {g_common or '?'} (conf {o['max_conf']:.2f})")
        if made:
            print(f"  _Unknown: {made} clips -> {unk_dir}")

    backups = []
    committed = []
    try:
        for staged, final, digest in staged_files:
            # spectrogram เดิมใช้ต่อได้เฉพาะเมื่อคลิปเหมือนเดิมทุกไบต์ (gen_spectrograms ข้าม .png ที่มีอยู่)
            png = final.with_suffix(".png")
            replaced = [final] if final.exists() else []
            if png.exists() and (not replaced or file_sha256(final) != digest):
                replaced.append(png)
            for old in replaced:
                backup = staging_dir / "_backup" / old.relative_to(date_folder)
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(old, backup)
                backups.append((backup, old))
            os.replace(staged, final)
            committed.append(final)
    except BaseException:   # รวม KeyboardInterrupt (ปิดแอป/Ctrl+C) ไม่งั้น finally ลบ backup ทิ้ง
        for final in committed:
            final.unlink(missing_ok=True)
        for backup, final in reversed(backups):
            os.replace(backup, final)
        raise
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
        try:
            staging_dir.parent.rmdir()          # ลบ .staging/ ถ้าว่างแล้ว
        except OSError:
            pass

    del src
    gc.collect()
    return rows, date_folder


def _carry_over_reviews(new, superseded):
    """คลิปที่สร้างใหม่ได้เหมือนเดิมทุกไบต์ (ชื่อ + sha256 เดิม) เก็บผลตรวจของคนไว้"""
    if new.empty or superseded.empty or not {"File", "Generated sha256"}.issubset(superseded.columns):
        return new
    reviewed = {(str(r["File"]), str(r["Generated sha256"])): r for _, r in superseded.iterrows()}
    new = new.copy()
    for index, row in new.iterrows():
        old = reviewed.get((str(row["File"]), str(row["Generated sha256"])))
        if old is None:
            continue
        for column in REVIEW_COLUMNS:
            if column in old.index and not pd.isna(old[column]):
                new.loc[index, column] = old[column]
    return new


def write_summary(rows, xlsx_path: Path, processed_sources=(), processed_ids=(), keep_reviews=True):
    """keep_reviews=False (--force) = ตั้งผลตรวจกลับเป็น Pending ตามที่แจ้งผู้ใช้ไว้"""
    df = pd.DataFrame(rows)
    superseded = pd.DataFrame()
    processed_sources = set(processed_sources)
    processed_ids = set(processed_ids) | {r["Source ID"] for r in rows}
    # merge กับ summary เดิมของวันนั้น (กันไฟล์ที่ถูก skip หายไปจากสรุป)
    if xlsx_path.exists():
        try:
            old = pd.read_excel(xlsx_path, dtype={"Source ID": str})
            if "Provisional rating" in old.columns:
                legacy = old.pop("Provisional rating")
                if "AI confidence band (1-4)" in old.columns:
                    old["AI confidence band (1-4)"] = old["AI confidence band (1-4)"].fillna(legacy)
                else:
                    old["AI confidence band (1-4)"] = legacy
            if "Source path" in old.columns:
                replace = old["Source path"].isin(processed_sources)
                if "Source ID" in old.columns:
                    replace = replace | old["Source ID"].isin(processed_ids)
            elif "Source ID" in old.columns:
                replace = old["Source ID"].isin(processed_ids)
            elif "Source file" in old.columns:
                replace = old["Source file"].isin({r["Source file"] for r in rows})
            else:
                replace = pd.Series(False, index=old.index)
            superseded = old[replace]
            kept = old[~replace]
        except Exception as exc:
            raise RuntimeError(f"Cannot read existing summary; refusing to overwrite {xlsx_path}: {exc}") from exc
        if keep_reviews:
            df = _carry_over_reviews(df, superseded)
        df = pd.concat([kept, df], ignore_index=True)
    if "File" in df.columns:
        df = df.drop_duplicates(subset=["File"], keep="last")
    sort_cols = [c for c in ("Species (common)", "Occurrence #") if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols, key=lambda c: (pd.to_numeric(c, errors="coerce")
                                                      if c.name == "Occurrence #" else c.astype(str)))
    if "Quality rating" in df.columns:   # กันกลายเป็น 3.0 เมื่อมีช่องว่างปน
        df["Quality rating"] = pd.to_numeric(df["Quality rating"], errors="coerce").astype("Int64")
    if df.empty and not xlsx_path.exists():
        return
    temporary = xlsx_path.with_name(xlsx_path.stem + ".tmp.xlsx")
    try:
        df.to_excel(temporary, index=False)
        os.replace(temporary, xlsx_path)
    finally:
        temporary.unlink(missing_ok=True)
    current_files = set(df["File"].astype(str)) if "File" in df else set()
    current_ready = set(df["Ready file"].dropna().astype(str)) if "Ready file" in df else set()
    for _, old_row in superseded.iterrows():
        for column, hash_column, expected_parent, still_used in (
            ("File", "Generated sha256", xlsx_path.parent, current_files),
            ("Ready file", "Ready sha256", xlsx_path.parent / "Ready", current_ready),
        ):
            relative = old_row.get(column)
            old_hash = old_row.get(hash_column)
            if (pd.isna(relative) or pd.isna(old_hash) or not relative or not old_hash or
                    str(relative) in still_used):
                continue
            try:
                old_path = _safe_generated_path(xlsx_path.parent, str(relative))
                if not old_path.is_relative_to(expected_parent.resolve()):
                    continue
                if old_path.is_file():
                    if file_sha256(old_path) == str(old_hash):
                        old_path.unlink()
                        if column == "File":
                            old_path.with_suffix(".png").unlink(missing_ok=True)
                            try:
                                old_path.parent.rmdir()  # remove an obsolete, now-empty species folder
                            except OSError:
                                pass
                    else:
                        print(f"  warning: superseded copy was edited; kept {old_path}")
            except (OSError, ValueError) as exc:
                print(f"  warning: could not remove superseded copy {relative}: {exc}")


def collect_inputs(input_path: Path):
    if input_path.is_dir():
        return sorted(p for p in input_path.iterdir()
                      if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    if input_path.is_file():
        return [input_path]
    return []


def build_parser():
    p = argparse.ArgumentParser(
        description="Cut bird clips from long field recordings with BirdNET, ready for eBird/Macaulay",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("audio", nargs="?", default=AUDIO_FILE, help="audio file or folder (batch)")
    p.add_argument("-o", "--output", default=OUTPUT_DIR, help="output folder")
    p.add_argument("--lat", type=float, default=None, help="latitude (blank = metadata/default)")
    p.add_argument("--lon", type=float, default=None, help="longitude (blank = metadata/default)")
    p.add_argument("--coords", default=None,
                   help="single field 'lat,lon' e.g. 13.8119502,100.553166 or a Google Maps link (overrides --lat/--lon)")
    p.add_argument("--date", default=REC_DATE,
                   help="recording date YYYYMMDD (or YYYY-MM-DD); required only when metadata and filename have no date")
    p.add_argument("--fill-missing-metadata", action="store_true",
                   help="apply --date, --coords and --start-time only to files missing those details")
    p.add_argument("--same-date-for-all", action="store_true",
                   help="confirm that --date applies to every file in a folder")
    p.add_argument("--start-time", default=None, help="actual recording start time HHMM or HHMMSS (HH:MM also works)")
    p.add_argument("--use-metadata", action=argparse.BooleanOptionalAction, default=USE_METADATA,
                   help="read date/coords from file metadata (ffprobe + BWF bext + XMP)")
    p.add_argument("--use-filetime", action="store_true",
                   help="use the file timestamp as unverified recording time")
    p.add_argument("--min-conf", type=float, default=None,
                   help="minimum confidence 0-1 (default: 0.5 for 3.0-preview; 0.25 for 2.4)")
    p.add_argument("--model", choices=("2.4", "3.0-preview"), default=DEFAULT_MODEL,
                   help="acoustic model; 3.0-preview is the default. Both use the location/date species filter")
    p.add_argument("--overlap", type=float, default=OVERLAP_SEC,
                   help="overlap between 3-second BirdNET analysis windows; 0 <= overlap < 3")
    p.add_argument("--out-of-range-min-conf", type=float, default=OUT_OF_RANGE_MIN_CONF,
                   help="also keep candidates outside the location/date species list (or not covered "
                        "by it) at this confidence, for vagrant review; off by default")
    p.add_argument("--habitat", default="",
                   help=f"recording site habitat(s), comma-separated: {', '.join(SITE_HABITATS)}. "
                        "Water birds then need a matching water habitat or confidence >= "
                        f"{HABITAT_MISMATCH_MIN_CONF}; blank = no habitat check")
    p.add_argument("--occurrence-gap", type=float, default=OCCURRENCE_GAP_SEC,
                   help="gap <= this (seconds) = same occurrence")
    p.add_argument("--lead", type=float, default=LEAD_SEC, help="padding before first sound (seconds)")
    p.add_argument("--tail", type=float, default=TAIL_SEC, help="padding after last sound (seconds)")
    p.add_argument("--target-dbfs", type=float, default=TARGET_DBFS, help="normalize peak level")
    p.add_argument("--format", default=EXPORT_FORMAT, help="export extension")
    p.add_argument("--place", default=None, help="place name (stored in summary)")
    p.add_argument("--datetime-regex", default=FILENAME_DATETIME_REGEX,
                   help="regex to parse datetime from the filename")
    p.add_argument("--mono", action=argparse.BooleanOptionalAction, default=MAKE_MONO,
                   help="convert stereo -> mono")
    p.add_argument("--spectrogram", action=argparse.BooleanOptionalAction, default=EXPORT_SPECTROGRAM,
                   help="generate a mel-spectrogram .png per clip")
    p.add_argument("--alt-species", action=argparse.BooleanOptionalAction, default=INCLUDE_ALT_SPECIES,
                   help="include alternate species 2-3 in the summary")
    p.add_argument("--unknown", action=argparse.BooleanOptionalAction, default=CUT_UNKNOWN,
                   help="also cut sounds BirdNET can't confidently ID into an _Unknown/ folder")
    p.add_argument("--unknown-min-conf", type=float, default=UNKNOWN_MIN_CONF,
                   help="confidence floor for _Unknown (below this = ignored as noise)")
    p.add_argument("--keep-continuous", action="store_true",
                   help="one continuous span from first to last detection per species in each source; use only after confirming the same individual")
    p.add_argument("--force", action="store_true",
                   help="redo even if the file was already processed (normally skipped)")
    return p


def main():
    # GUI หยุดงานด้วย SIGTERM -> ให้เป็น KeyboardInterrupt เพื่อ rollback/ลบ staging ได้เรียบร้อย
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    watch_for_stop()   # Windows: GUI ส่ง STOP ทาง stdin แทน SIGTERM

    # ความเห็นที่สองจาก xeno-canto สำหรับคลิปเดียว (GUI เรียกเป็น subprocess)
    if len(sys.argv) >= 2 and sys.argv[1] == "--second-opinion":
        from second_opinion import main as second_opinion_main
        sys.exit(second_opinion_main(sys.argv[2:]))

    # โหมดสร้าง spectrogram แยก (subprocess — ไม่โหลด TensorFlow)
    if len(sys.argv) >= 2 and sys.argv[1] == "--gen-spectrograms":
        if gen_spectrograms([Path(d) for d in sys.argv[2:]]):
            sys.exit(1)
        return

    # โหมดรวมคลิปนกตัวเดียวกัน (ML guide #5): --group w1 w2 ... -o out.wav
    if len(sys.argv) >= 2 and sys.argv[1] == "--group":
        rest = sys.argv[2:]
        if "-o" in rest:
            i = rest.index("-o")
            if i + 1 >= len(rest):
                print("group: must specify an output file after -o")
                sys.exit(2)
            group_clips(rest[:i], Path(rest[i + 1]))
        else:
            print("group: must specify -o <output file>")
            sys.exit(2)
        return

    args = build_parser().parse_args()
    if args.min_conf is None:
        args.min_conf = MIN_CONF if args.model == "3.0-preview" else V2_MIN_CONF
    if args.coords:
        cc = parse_coords(args.coords)
        if cc:
            args.lat, args.lon = cc
        else:
            print(f"Invalid coordinates: {args.coords}")
            sys.exit(2)
    if ((args.lat is not None and not -90 <= args.lat <= 90) or
            (args.lon is not None and not -180 <= args.lon <= 180)):
        print("Coordinates are outside valid latitude/longitude ranges")
        sys.exit(2)
    if (not 0 <= args.min_conf <= 1 or not 0 <= args.unknown_min_conf <= 1 or
            (args.out_of_range_min_conf is not None and not 0 <= args.out_of_range_min_conf <= 1)):
        print("Confidence thresholds must be between 0 and 1")
        sys.exit(2)
    try:
        site_habitats = parse_site_habitats(args.habitat)
    except ValueError as exc:
        print(exc)
        sys.exit(2)
    if not 0 <= args.overlap < 3:
        print("Overlap must be at least 0 and less than 3 seconds")
        sys.exit(2)
    if min(args.occurrence_gap, args.lead, args.tail) < 0:
        print("Occurrence gap, lead, and tail cannot be negative")
        sys.exit(2)
    input_path = Path(args.audio)
    root_out = Path(args.output)
    try:
        date_override = parse_date_entry(args.date) if args.date else None
    except ValueError:
        print(f"Date must be {DATE_HINT}")
        sys.exit(2)
    try:
        start_time_override = parse_time_entry(args.start_time) if args.start_time else None
    except ValueError:
        print(f"Start time must be {TIME_HINT}")
        sys.exit(2)

    files = collect_inputs(input_path)
    if not files:
        print(f"No audio file found at: {input_path}")
        sys.exit(1)
    if len(files) > 1 and date_override and not args.same_date_for_all and not args.fill_missing_metadata:
        print("Date applies to every file in this folder. Verify all recordings share "
              "the same date and add --same-date-for-all, or process each date separately.")
        sys.exit(2)
    try:
        require_recording_context(files, date_override, args.use_metadata,
                                  args.lat, args.lon)
    except ValueError as exc:
        print(f"Input required before analysis:\n{exc}")
        sys.exit(2)
    root_out.mkdir(parents=True, exist_ok=True)

    print(f"Processing {len(files)} file(s) | output: {root_out}")
    print(f"Loading BirdNET {args.model} model (once) ...")
    if args.model == "3.0-preview":
        from v3_model import V3Analyzer
        analyzer = V3Analyzer()
        print("  experimental acoustic model; location/date species filter is active; verify every ID by ear")
    else:
        from birdnetlib.analyzer import Analyzer  # lazy: โหลด LiteRT ตรงนี้
        analyzer = Analyzer()

    new_clips_by_date = defaultdict(int)    # date_folder -> คลิปใหม่รอบนี้ (รวมหลายไฟล์วันเดียวกัน)
    skipped_dates = set()                   # date_folder ที่มีไฟล์ถูกข้ามเพราะทำไว้แล้ว
    total_clips = 0
    failed_files = []
    seen_sources = set()
    try:
        for audio_path in files:
            sid = source_id(audio_path)
            if sid in seen_sources:
                print(f"  duplicate recording skipped: {audio_path}")
                continue
            try:
                context = recording_context_preview(audio_path, args.use_metadata,
                                                    args.datetime_regex)
                effective_date, effective_lat, effective_lon, effective_time = effective_overrides(
                    context, date_override, args.lat, args.lon, start_time_override,
                    args.fill_missing_metadata)
                rows, date_folder = process_file(
                    analyzer, audio_path, root_out,
                    lat_arg=effective_lat, lon_arg=effective_lon, cfg_lat=LAT, cfg_lon=LON,
                    date_override=effective_date, use_meta=args.use_metadata,
                    min_conf=args.min_conf, gap=args.occurrence_gap,
                    lead=args.lead, tail=args.tail, target_dbfs=args.target_dbfs,
                    fmt=args.format, make_mono=args.mono, incl_alt=args.alt_species,
                    place_arg=args.place, dt_regex=args.datetime_regex, force=args.force,
                    use_filetime=args.use_filetime,
                    cut_unknown=args.unknown, unknown_floor=args.unknown_min_conf,
                    keep_continuous=args.keep_continuous,
                    start_time_override=effective_time,
                    overlap=args.overlap, out_of_range_min_conf=args.out_of_range_min_conf,
                    site_habitats=site_habitats,
                )
                if rows is not None:
                    # เขียน summary ทันทีทีละไฟล์: ถ้าหยุดกลางคัน ไฟล์ที่เสร็จแล้วไม่ต้องวิเคราะห์ใหม่
                    write_summary(rows, date_folder / "summary.xlsx",
                                  {str(audio_path.resolve())}, {sid}, keep_reviews=not args.force)
            except Exception as exc:  # noqa: BLE001  ไฟล์เดียวพังไม่ควรล้มทั้ง batch
                print(f"  !! error with {audio_path.name}: {exc}")
                for staging in root_out.glob(f"*/.staging/{sid}"):
                    shutil.rmtree(staging, ignore_errors=True)
                failed_files.append(str(audio_path))
                continue
            seen_sources.add(sid)
            if rows is None:
                skipped_dates.add(date_folder)
            else:
                new_clips_by_date[date_folder] += len(rows)
                total_clips += len(rows)
    except KeyboardInterrupt:
        print("\nStopped. Finished files are saved; run again to continue with the rest.")
        sys.exit(130)

    # GUI อ่านบรรทัด "summary ... -> path" เพื่อเปิดแท็บตรวจคลิป
    for date_folder in sorted(set(new_clips_by_date) | skipped_dates, key=str):
        summary = date_folder / "summary.xlsx"
        if not summary.exists():
            continue
        if date_folder in new_clips_by_date:
            print(f"  summary {new_clips_by_date[date_folder]} new clips -> {summary}")
        else:
            print(f"  summary already up to date -> {summary}")

    # สร้าง spectrogram ใน process แยก (กัน TensorFlow ชน native crash)
    if args.spectrogram and total_clips:
        print("\nGenerating mel-spectrograms in a separate process ...")
        dates_with_clips = [d for d, count in new_clips_by_date.items() if count]
        if run_spectrogram_subprocess(sorted(dates_with_clips, key=str)) != 0:
            print("  !! spectrogram generation failed")
            failed_files.append("spectrogram generation")

    if failed_files:
        print(f"\nIncomplete: {len(failed_files)} failure(s), {total_clips} clips -> {root_out}")
        for failed in failed_files:
            print(f"  failed: {failed}")
        sys.exit(1)
    print(f"\nDone: {total_clips} clips from {len(files)} file(s) -> {root_out}")


if __name__ == "__main__":
    paths.migrate_old_locations()   # กันโหลดโมเดลซ้ำถ้า GUI รุ่นก่อนยังเปิดอยู่
    main()
