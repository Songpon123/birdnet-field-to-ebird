"""Coarse site-habitat check from AVONET primary habitats.

AVONET: Tobias et al. 2022, Ecology Letters, doi:10.1111/ele.13898 (data CC BY 4.0,
doi:10.6084/m9.figshare.16586228). assets/avonet_habitat.csv keeps only the name,
primary habitat and lifestyle columns, merged over the eBird, BirdLife and BirdTree sheets.
"""

import csv
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "assets" / "avonet_habitat.csv"

SITE_HABITATS = {                 # key used on the command line -> label in the app
    "forest": "Forest",
    "field": "Field / farmland",
    "marsh": "Marsh / lake",
    "river": "River / stream",
    "sea": "Sea / coast",
}
# นกบก (ป่า ทุ่ง พุ่มไม้ ฯลฯ) เข้าได้ทุกจุด เพราะเดินทาง/ร้องจากที่ใกล้ ๆ ได้
# นกน้ำต้องมีแหล่งน้ำแบบที่ใช้ได้ในจุดบันทึก
WATER_HABITATS = {
    "Wetland": {"marsh", "river", "sea"},
    "Riverine": {"river", "marsh"},
    "Coastal": {"sea", "marsh", "river"},
    "Marine": {"sea"},
}
MISMATCH_MIN_CONF = 0.8           # ไม่เข้ากับถิ่นอาศัย แต่คะแนนสูง (อาจบินผ่าน) -> เก็บไว้ให้ฟังตรวจ


@lru_cache(maxsize=1)
def _table():
    with DATA.open(encoding="utf-8", newline="") as stream:
        return {row["scientific"]: (row["habitat"], row["lifestyle"])
                for row in csv.DictReader(stream)}


def species_habitat(*scientific_names):
    """AVONET primary habitat of the first known name ("" if unknown).
    Aquatic species count as Wetland even when listed under a land habitat."""
    for name in scientific_names:
        if name and name in _table():
            habitat, lifestyle = _table()[name]
            if lifestyle == "Aquatic" and habitat not in WATER_HABITATS:
                return "Wetland"
            return habitat
    return ""


def fits_site(habitat, site_habitats):
    """True/False, or None when no site habitat was given or the species is unknown."""
    if not site_habitats or not habitat:
        return None
    needs = WATER_HABITATS.get(habitat)
    return needs is None or bool(needs & set(site_habitats))


def parse_site_habitats(text):
    """'forest,marsh' -> ('forest', 'marsh')"""
    keys = {part.strip().lower() for part in str(text or "").split(",") if part.strip()}
    unknown = sorted(keys - SITE_HABITATS.keys())
    if unknown:
        raise ValueError(f"Unknown habitat {', '.join(unknown)}; use {', '.join(SITE_HABITATS)}")
    return tuple(sorted(keys))
