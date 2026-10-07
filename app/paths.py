r"""ข้อมูลของแอปทั้งหมดอยู่ในโฟลเดอร์ data/ ของโฟลเดอร์โปรแกรม (ย้ายทั้งโฟลเดอร์ได้):

  data/settings.json   API key ของผู้ใช้ — ส่วนตัว อย่าส่งให้คนอื่น
  data/birdnet/        โมเดล BirdNET 3.0 (โหลดครั้งแรก ~280 MB)
  data/cache/          xeno-canto, second-opinion, ebird (ลบได้ จะโหลด/คำนวณใหม่)
  data/logs/           log ของรุ่น Windows (เปิดด้วย pythonw ไม่มี console)

log ของตัวเปิดบน Desktop ยังอยู่ ~/Library/Logs/BirdNET-eBird.log: ห้ามแก้สคริปต์ในตัวเปิด
(macOS ผูกสิทธิ์เข้าถึง Desktop ไว้กับเนื้อหาไฟล์นั้น แก้แล้วแอปจะเปิดไม่ขึ้น)

ถ้าโฟลเดอร์โปรแกรมเขียนไม่ได้ (เช่นดิสก์อ่านอย่างเดียว หรือ C:\Program Files) ใช้ ~/Library
(mac) หรือ %LOCALAPPDATA%\BirdNET-eBird (Windows) แทน
"""

import os
import shutil
from pathlib import Path

PROGRAM = Path(__file__).resolve().parent.parent
DATA = PROGRAM / "data"
LIBRARY = Path.home() / "Library"
_OLD = {                                   # ที่เก็บเดิมก่อนรวมเข้า data/
    "settings": LIBRARY / "Application Support" / "BirdNET-eBird" / "settings.json",
    "cache": LIBRARY / "Caches" / "BirdNET-eBird",
    "birdnet": LIBRARY / "Application Support" / "birdnet",
}


def _writable(folder):
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


if _writable(DATA):
    SETTINGS_FILE = DATA / "settings.json"
    CACHE = DATA / "cache"
    BIRDNET_MODELS = DATA / "birdnet"
    LOGS = DATA / "logs"
elif os.name == "nt":
    _local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "BirdNET-eBird"
    SETTINGS_FILE, CACHE, BIRDNET_MODELS = _local / "settings.json", _local / "cache", _local / "birdnet"
    LOGS = _local / "logs"
else:
    SETTINGS_FILE, CACHE, BIRDNET_MODELS = _OLD["settings"], _OLD["cache"], _OLD["birdnet"]
    LOGS = LIBRARY / "Logs"
LOG_FILE = LOGS / "BirdNET-eBird.log"

# แพ็กเกจ birdnet อ่านตัวแปรนี้ตอน import: ต้องตั้งก่อนโหลดโมเดล 3.0 (process ลูกได้ค่านี้ด้วย)
os.environ.setdefault("BIRDNET_APP_DATA", str(BIRDNET_MODELS))


def migrate_old_locations(log=print):
    """ย้ายข้อมูลจาก ~/Library (รุ่นก่อน) เข้า data/ — รันซ้ำได้ ไม่ทับของที่มีอยู่แล้ว"""
    if not SETTINGS_FILE.is_relative_to(DATA) or os.name == "nt":   # รุ่นก่อนมีแต่ mac
        return []
    moved = []

    def merge_move(old, new):
        if not old.exists():
            return
        if not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(old), str(new))
                moved.append((old, new))
            except OSError as exc:
                log(f"Could not move {old} -> {new}: {exc}")
        elif old.is_dir() and new.is_dir():          # มีทั้งสองที่: ย้ายเฉพาะไฟล์ที่ยังไม่มี
            for child in list(old.iterdir()):
                merge_move(child, new / child.name)
            try:
                old.rmdir()
            except OSError:
                pass

    _merge_settings(_OLD["settings"], SETTINGS_FILE)
    merge_move(_OLD["settings"], SETTINGS_FILE)
    merge_move(_OLD["birdnet"], BIRDNET_MODELS)
    merge_move(_OLD["cache"], CACHE)
    try:
        _OLD["settings"].parent.rmdir()               # ลบโฟลเดอร์เดิมถ้าว่างแล้ว
    except OSError:
        pass
    return moved


def _merge_settings(old, new):
    """settings มีทั้งสองที่ (เช่นแอปรุ่นเก่ายังเปิดอยู่ตอนย้าย): รวมกัน ไฟล์ที่ใหม่กว่าชนะ"""
    if not (old.is_file() and new.is_file()):
        return
    import json
    try:
        a, b = (json.loads(f.read_text(encoding="utf-8")) for f in (old, new))
    except (OSError, ValueError):
        return
    merged = dict(b, **a) if old.stat().st_mtime > new.stat().st_mtime else dict(a, **b)
    temporary = new.with_name(new.name + ".tmp")
    temporary.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, new)
    old.unlink()
