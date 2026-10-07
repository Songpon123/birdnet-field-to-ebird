#!/usr/bin/env python3
"""
birdnet_app.py — จุดเข้าเดียวสำหรับไฟล์ .exe (PyInstaller)
----------------------------------------------------------
- เปิดโดยไม่มี argument  -> เปิดหน้าต่างโปรแกรม (Tkinter GUI)
- มี argument ใด ๆ       -> โหมด CLI (เหมือนเรียก field_audio_to_ebird.py)

GUI จะ spawn ตัว .exe เองซ้ำพร้อม argument เพื่อรัน CLI ใน process แยก
(TensorFlow แยก process, UI ไม่ค้าง) — ดู birdnet_gui.py / field_audio_to_ebird.py
ที่เช็ค getattr(sys, "frozen", False) เพื่อ re-invoke ตัว exe แทน python+สคริปต์
"""
import sys
import multiprocessing


def _log_without_console():
    """pythonw.exe (ตัวเปิดบน Windows) ไม่มี console: stdout/stderr เป็น None -> เขียนลง data/logs"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    import paths
    try:
        paths.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        log = open(paths.LOG_FILE, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def main():
    multiprocessing.freeze_support()  # กัน child process วน spawn ตอน frozen
    _log_without_console()
    argv = sys.argv[1:]
    want_gui = (not argv) or argv[0] == "--gui"
    if want_gui:
        from birdnet_gui import main as gui_main
        gui_main()
    else:
        from field_audio_to_ebird import main as cli_main
        cli_main()


if __name__ == "__main__":
    main()
