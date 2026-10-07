"""สิ่งที่ต่างกันระหว่าง macOS กับ Windows: เปิดไฟล์, เล่นเสียง, หา ffmpeg, รัน/หยุดงานเบื้องหลัง"""

import _thread
import os
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path

PROGRAM = Path(__file__).resolve().parent.parent
IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # Windows: ไม่ให้หน้าต่าง console เด้ง
GUI_CHILD_ENV = "BIRDNET_GUI_CHILD"
STOP_LINE = "STOP"


def bundled_tool(name):
    """ffmpeg/ffprobe/ffplay ที่แนบมา: Windows = <โปรแกรม>/bin/name.exe, mac = <โปรแกรม>/name"""
    for candidate in (PROGRAM / "bin" / f"{name}.exe", PROGRAM / name):
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def child_python():
    """python สำหรับงานเบื้องหลัง: GUI บน Windows รันด้วย pythonw.exe (ไม่มี console) ใช้ python.exe ข้าง ๆ"""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").is_file():
        return str(exe.with_name("python.exe"))
    return sys.executable


def child_env():
    """env ของงานเบื้องหลัง: UTF-8 (ชื่อไทย), ไม่ buffer (GUI อ่านความคืบหน้าทีละบรรทัด)"""
    return dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
                **{GUI_CHILD_ENV: "1"})


def start_child(cmd):
    """เริ่มงานเบื้องหลังที่ GUI อ่าน stdout ได้; Windows เปิด stdin ไว้ส่งคำสั่งหยุด"""
    return subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if IS_WINDOWS else None,
        text=True, encoding="utf-8", errors="replace", env=child_env(), bufsize=1,
        creationflags=NO_WINDOW)


def request_stop(proc):
    """ขอให้งานหยุดเอง (ย้อนไฟล์ที่ค้าง แล้วบันทึกส่วนที่เสร็จ): mac/Linux ส่ง SIGTERM
    Windows ไม่มี SIGTERM จริง (terminate = ฆ่าทันที) จึงส่งบรรทัด STOP ทาง stdin"""
    if IS_WINDOWS and proc.stdin is not None:
        try:
            proc.stdin.write(STOP_LINE + "\n")
            proc.stdin.flush()
            return
        except (OSError, ValueError):
            pass
    proc.terminate()


def watch_for_stop():
    """CLI ที่ GUI เปิดบน Windows: ได้ STOP ทาง stdin -> KeyboardInterrupt ใน main thread (เหมือน SIGTERM บน mac)"""
    if not (IS_WINDOWS and os.environ.get(GUI_CHILD_ENV) and sys.stdin is not None):
        return
    signal.signal(signal.SIGINT, signal.default_int_handler)

    def watch():
        try:
            for line in sys.stdin:
                if line.strip() == STOP_LINE:
                    if os.name == "nt":
                        signal.raise_signal(signal.SIGINT)   # ปลุก main thread ที่รออยู่ด้วย (เหมือน Ctrl+C)
                    else:
                        _thread.interrupt_main()
                    return
        except (OSError, ValueError):
            pass
    threading.Thread(target=watch, daemon=True).start()


def open_path(path):
    """เปิดไฟล์/โฟลเดอร์ด้วยโปรแกรมของระบบ"""
    if IS_MAC:
        subprocess.Popen(["open", str(path)])
    elif IS_WINDOWS:
        os.startfile(str(path))
    else:
        subprocess.Popen(["xdg-open", str(path)])


def play_audio(path):
    """เล่นเสียงเบื้องหลัง -> Popen (terminate() เพื่อหยุด): mac = afplay, Windows = ffplay ที่แนบมา"""
    if IS_MAC:
        return subprocess.Popen(["afplay", str(path)])
    player = bundled_tool("ffplay")
    if not player:
        raise RuntimeError("ffplay not found (it ships in the bin folder of the Windows version)")
    return subprocess.Popen([player, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)


def enable_high_dpi():
    """Windows: ตัวหนังสือคมบนจอความละเอียดสูง (ไม่งั้นระบบขยายภาพจนเบลอ) — เรียกก่อนสร้างหน้าต่าง"""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
