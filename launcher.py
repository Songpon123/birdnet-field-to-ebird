#!/usr/bin/env python3
"""
launcher.py — ตัวเปิดโปรแกรม (freeze เป็น BirdNET-eBird.exe ด้วย PyInstaller)
----------------------------------------------------------------------------
ใช้ stdlib ล้วน -> PyInstaller freeze ผ่านเสมอ (ไม่แตะ native heavy libs).
หน้าที่: หา Python + deps ที่ bundle มาข้าง ๆ (โฟลเดอร์ python\\) แล้วรัน
app\\birdnet_app.py ด้วยตัวนั้น — เปิดไม่มี arg = GUI, มี arg = CLI.

โครงโฟลเดอร์ที่แจก:
  BirdNET-eBird\\
    BirdNET-eBird.exe   <- ไฟล์นี้
    python\\python.exe   <- Python 3.12 + deps (birdnetlib/librosa/litert/...)
    app\\birdnet_app.py  <- โค้ดโปรแกรม
    ffmpeg.exe ffprobe.exe
"""
import os
import sys
import subprocess


def base_dir():
    # โฟลเดอร์ที่ .exe อยู่ (frozen) หรือที่สคริปต์อยู่ (dev)
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def main():
    base = base_dir()
    py_dir = os.path.join(base, "python")
    py_con = os.path.join(py_dir, "python.exe")     # มี console (CLI)
    py_win = os.path.join(py_dir, "pythonw.exe")    # ไม่มี console (GUI)
    app = os.path.join(base, "app", "birdnet_app.py")

    if not os.path.isfile(py_con) or not os.path.isfile(app):
        sys.stderr.write(
            "Missing files: the 'python\\' and 'app\\' folders must sit next to the .exe\n"
            f"  looked for: {py_con}\n  looked for: {app}\n"
            "Do not separate the .exe from its folder — copy the whole BirdNET-eBird folder.\n")
        return 2

    # ให้ subprocess เห็น ffmpeg/ffprobe ที่ bundle ไว้ (field_audio_to_ebird หา via PATH)
    os.environ["PATH"] = base + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    args = sys.argv[1:]
    if args:
        # CLI: คง console ไว้ให้เห็น/redirect output ได้
        return subprocess.call([py_con, app] + args)

    # GUI: ปิดหน้าต่าง console ที่เด้งมา (launcher เป็น console exe) แล้วเปิด GUI เงียบ ๆ
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.FreeConsole()
        except Exception:
            pass
    interp = py_win if os.path.isfile(py_win) else py_con
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    return subprocess.call([interp, app], creationflags=flags)


if __name__ == "__main__":
    sys.exit(main())
