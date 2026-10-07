"""eBird API 2.0, read-only. The taxonomy needs no key; nearby sightings, regional species
lists and hotspots use the user's own key (Settings tab). Results are cached on this Mac."""

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

import paths

API = "https://api.ebird.org/v2"
USER_AGENT = "BirdNET-eBird-mac (bird recording review app)"
CACHE = paths.CACHE / "ebird"
NEARBY_KM = 25                      # รายงานล่าสุดใกล้จุดบันทึก (API ให้สูงสุด 50 กม.)
RECENT_DAYS = 30                    # API ย้อนหลังได้สูงสุด 30 วัน
HOTSPOT_KM = 50
DAY = 24 * 3600


class EBirdError(RuntimeError):
    pass


def _get(path, params=None, key=None, timeout=30):
    url = f"{API}/{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    headers = {"User-Agent": USER_AGENT}
    if key:
        headers["X-eBirdApiToken"] = key
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise EBirdError("eBird did not accept the API key; check it in the Settings tab") from exc
        raise EBirdError(f"eBird returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise EBirdError(f"Cannot reach eBird: {exc}") from exc


def _cached(name, max_age_s, fetch):
    path = CACHE / f"{name}.json"
    try:
        if time.time() - path.stat().st_mtime < max_age_s:
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    data = fetch()
    CACHE.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(path)
    return data


def taxonomy():
    """{'by_sci': {sciName: {code, common}}, 'by_common': {comName lower: ...}} (no key needed)"""
    rows = _cached("taxonomy", 60 * DAY,
                   lambda: _get("ref/taxonomy/ebird", {"fmt": "json", "cat": "species", "locale": "en"}))
    by_sci, by_common = {}, {}
    for row in rows:
        entry = {"code": row["speciesCode"], "common": row["comName"], "scientific": row["sciName"]}
        by_sci[row["sciName"]] = entry
        by_common.setdefault(row["comName"].casefold(), entry)
    return {"by_sci": by_sci, "by_common": by_common}


def species(scientific, common=""):
    tax = taxonomy()
    return tax["by_sci"].get(" ".join(str(scientific).split()[:2])) or tax["by_common"].get(str(common).casefold())


def _spot(lat, lon):
    return f"{lat:.2f}_{lon:.2f}"


def nearest_hotspots(lat, lon, key):
    rows = _cached(f"hotspots_{_spot(lat, lon)}", 7 * DAY,
                   lambda: _get("ref/hotspot/geo", {"lat": f"{lat:.4f}", "lng": f"{lon:.4f}",
                                                    "dist": HOTSPOT_KM, "fmt": "json"}, key))
    for row in rows:
        row["km"] = round(_distance_km(lat, lon, float(row["lat"]), float(row["lng"])), 1)
    return sorted(rows, key=lambda row: row["km"])


def region_species(region, key):
    return set(_cached(f"spplist_{region}", 7 * DAY, lambda: _get(f"product/spplist/{region}", key=key)))


def region_name(region, key):
    try:
        info = _cached(f"region_{region}", 60 * DAY, lambda: _get(f"ref/region/info/{region}", key=key))
        return str(info.get("result") or region).split(",")[0]
    except EBirdError:
        return region


def recent_nearby(lat, lon, key):
    """ล่าสุดของแต่ละชนิดภายใน NEARBY_KM ในช่วง RECENT_DAYS วันก่อนวันนี้"""
    return _cached(f"recent_{_spot(lat, lon)}_{date.today().isoformat()}", 6 * 3600,
                   lambda: _get("data/obs/geo/recent", {"lat": f"{lat:.4f}", "lng": f"{lon:.4f}",
                                                        "dist": NEARBY_KM, "back": RECENT_DAYS,
                                                        "includeProvisional": "true"}, key))


def check(scientific, lat, lon, recorded, key, common=""):
    """ชนิดนี้สอดคล้องกับข้อมูล eBird ใกล้จุดบันทึกไหม -> dict (recorded = datetime/date หรือ None)"""
    entry = species(scientific, common)
    if entry is None:
        return {"known": False, "scientific": scientific}
    result = {"known": True, **entry, "region": None, "region_name": None, "in_region": None,
              "recent_checked": False, "recent": None, "hotspot": None}
    if lat is None or lon is None or not key:
        return result
    hotspots = nearest_hotspots(lat, lon, key)
    if hotspots:
        nearest = hotspots[0]
        result["hotspot"] = {"name": nearest["locName"], "km": nearest["km"], "id": nearest["locId"]}
        result["region"] = nearest.get("subnational1Code") or nearest.get("countryCode")
    if result["region"]:
        result["region_name"] = region_name(result["region"], key)
        result["in_region"] = entry["code"] in region_species(result["region"], key)
    when = getattr(recorded, "date", lambda: recorded)() if recorded else None
    if when is not None and 0 <= (date.today() - when).days <= RECENT_DAYS:
        result["recent_checked"] = True
        sightings = [row for row in recent_nearby(lat, lon, key) if row.get("speciesCode") == entry["code"]]
        if sightings:
            latest = max(sightings, key=lambda row: row.get("obsDt", ""))
            result["recent"] = {"date": latest.get("obsDt", ""), "where": latest.get("locName", "")}
    return result


def _distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))
