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


def main():
    multiprocessing.freeze_support()  # กัน child process วน spawn ตอน frozen
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
