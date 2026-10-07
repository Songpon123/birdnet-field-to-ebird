"""Reference recordings from xeno-canto (API v3; needs the user's own key from
https://xeno-canto.org/account). Recordings belong to their recordists and are shared
under the Creative Commons license shown with each one."""

import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request

import paths

API = "https://xeno-canto.org/api/3/recordings"
USER_AGENT = "BirdNET-eBird-mac (bird recording review app)"
CACHE = paths.CACHE / "xeno-canto"
QUALITY_RANK = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}
NEARBY_DEGREES = 10                 # ค้นรอบจุดบันทึกก่อน (เสียงท้องถิ่น) แล้วค่อยทั่วโลก


class XenoCantoError(RuntimeError):
    pass


def build_query(scientific, sound_type="", box=None, common=""):
    """common = ค้นด้วยชื่ออังกฤษแทน (xeno-canto ใช้ระบบชื่อ IOC สกุลอาจไม่ตรงกับ BirdNET
    เช่น Yellow-bellied Tit เป็น Pardaliparus ไม่ใช่ Periparus)"""
    if common:
        parts = [f'en:"{common}"']
    else:
        genus, species = str(scientific).split()[:2]
        parts = [f"gen:{genus}", f"sp:{species.lower()}"]
    if sound_type:
        parts.append(f'type:"{sound_type}"' if " " in sound_type else f"type:{sound_type}")
    if box:
        parts.append("box:" + ",".join(f"{value:.3f}" for value in box))
    return " ".join(parts)


def search(scientific, key, sound_type="", lat=None, lon=None, limit=30, timeout=20, common=""):
    """-> (recordings, total). เรียงคุณภาพดีก่อน แล้วใกล้จุดบันทึกก่อน
    ถ้าชื่อวิทยาศาสตร์ไม่มีใน xeno-canto (สกุลต่างกัน) จะค้นด้วยชื่ออังกฤษ common แทน"""
    if not key:
        raise XenoCantoError("Add your xeno-canto API key in the Settings tab first")
    found, total = _search(scientific, key, sound_type, lat, lon, limit, "")
    if not found and not total and common:
        found, total = _search(scientific, key, sound_type, lat, lon, limit, common)
    return found, total


def species_page(scientific, key, common=""):
    """หน้าชนิดของ xeno-canto ตามชื่อที่ xeno-canto ใช้จริง (None ถ้าหาไม่เจอ)"""
    for name in ("", common) if common else ("",):
        data = _get_json({"query": build_query(scientific, common=name), "key": key, "per_page": 50})
        for item in data.get("recordings") or []:
            if item.get("gen") and item.get("sp"):
                return "https://xeno-canto.org/species/" + urllib.parse.quote(f"{item['gen']}-{item['sp']}")
    return None


def _search(scientific, key, sound_type, lat, lon, limit, common):
    found, total = {}, 0
    queries = []
    if lat is not None and lon is not None:
        queries.append((build_query(scientific, sound_type,
                                    (max(-90, lat - NEARBY_DEGREES), max(-180, lon - NEARBY_DEGREES),
                                     min(90, lat + NEARBY_DEGREES), min(180, lon + NEARBY_DEGREES)),
                                    common), True))
    queries.append((build_query(scientific, sound_type, common=common), False))
    for query, optional in queries:
        try:
            data = _get_json({"query": query, "key": key, "per_page": 100})
        except XenoCantoError:
            if optional:          # ถ้าค้นรอบพื้นที่ไม่ได้ ยังค้นทั่วโลกต่อ
                continue
            raise
        total = max(total, int(data.get("numRecordings") or 0))
        for item in data.get("recordings") or []:
            recording = simplify(item, lat, lon)
            found.setdefault(recording["id"], recording)
        if len(found) >= limit:
            break
    ordered = sorted(found.values(), key=lambda r: (QUALITY_RANK.get(r["quality"], 9),
                                                    r["distance_km"] if r["distance_km"] is not None else 1e9))
    return ordered[:limit], total


def _get_json(params, timeout=20):
    request = urllib.request.Request(API + "?" + urllib.parse.urlencode(params),
                                     headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            message = json.load(exc).get("message")
        except (ValueError, AttributeError):
            message = None
        raise XenoCantoError(message or f"xeno-canto returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise XenoCantoError(f"Cannot reach xeno-canto: {exc}") from exc


def simplify(item, lat=None, lon=None):
    rec_lat = _number(item.get("lat"))
    rec_lon = _number(item.get("lon", item.get("lng")))
    distance = (_distance_km(lat, lon, rec_lat, rec_lon)
                if None not in (lat, lon, rec_lat, rec_lon) else None)
    number = str(item.get("id", "")).strip()
    return {
        "id": f"XC{number}",
        "type": str(item.get("type") or ""),
        "quality": str(item.get("q") or "").strip().upper()[:1],
        "length": str(item.get("length") or ""),
        "country": str(item.get("cnt") or ""),
        "location": str(item.get("loc") or ""),
        "recordist": str(item.get("rec") or ""),
        "date": str(item.get("date") or ""),
        "license": license_label(item.get("lic")),
        "file": _https(item.get("file")),
        "page": _https(item.get("url")) or f"https://xeno-canto.org/{number}",
        "ext": (os.path.splitext(str(item.get("file-name") or ""))[1] or ".mp3").lower(),
        "distance_km": None if distance is None else round(distance),
    }


def license_label(url):
    """'//creativecommons.org/licenses/by-nc-sa/4.0/' -> 'CC BY-NC-SA 4.0'"""
    match = re.search(r"licenses/([a-z-]+)/([\d.]+)", str(url or ""))
    return f"CC {match.group(1).upper()} {match.group(2)}" if match else str(url or "")


def download(recording, cache=CACHE, timeout=60):
    """mp3/wav ของรายการนี้ (เก็บ cache ไว้ฟังซ้ำ) -> Path"""
    if not recording.get("file"):
        raise XenoCantoError(f"{recording['id']} has no downloadable file")
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{recording['id']}{recording.get('ext') or '.mp3'}"
    if target.is_file() and target.stat().st_size > 0:
        return target
    temporary = target.with_name(target.name + ".part")
    request = urllib.request.Request(recording["file"], headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open("wb") as out:
            while chunk := response.read(1 << 16):
                out.write(chunk)
        os.replace(temporary, target)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise XenoCantoError(f"Cannot download {recording['id']}: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _https(url):
    url = str(url or "").strip()
    return "https:" + url if url.startswith("//") else url


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))
