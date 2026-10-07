"""ตรวจรุ่น Windows (รันบน Mac): ทุก .dll/.pyd/.exe ต้องหา DLL ที่ import ได้ — มีในโฟลเดอร์ หรือเป็นของ Windows
ถ้าขาด (เช่น VC++ runtime) เครื่องเพื่อนจะขึ้น "DLL load failed" ตอนเปิด
ใช้: uv run --no-project --with pefile python check_dlls.py <โฟลเดอร์รุ่น Windows>"""
import sys
from collections import defaultdict
from pathlib import Path

import pefile

root = Path(sys.argv[1])
SYSTEM = {
    "kernel32", "user32", "gdi32", "advapi32", "shell32", "ole32", "oleaut32", "ws2_32", "msvcrt",
    "ntdll", "comdlg32", "comctl32", "winmm", "imm32", "version", "shlwapi", "crypt32", "bcrypt",
    "secur32", "setupapi", "cfgmgr32", "dbghelp", "psapi", "iphlpapi", "rpcrt4", "userenv", "winhttp",
    "wininet", "dwmapi", "uxtheme", "d3d11", "dxgi", "d3d9", "d3d12", "dxcore", "opengl32", "glu32",
    "mfplat", "mfreadwrite", "mf", "mfuuid", "powrprof", "normaliz", "wldap32", "netapi32", "mpr",
    "avrt", "winspool.drv", "propsys", "dnsapi", "hid", "imagehlp", "msimg32", "shcore", "ncrypt",
    "wtsapi32", "ucrtbase", "usp10", "gdiplus", "mswsock", "dbgeng", "oleacc", "credui", "wintrust",
    "msacm32", "ksuser", "strmiids", "vfw32", "avicap32", "msvfw32", "dsound", "dinput8", "xinput1_4",
    "winusb", "sspicli", "cryptbase", "bcryptprimitives", "kernelbase", "dwrite", "d2d1",
    "windowscodecs", "mmdevapi", "ntdsapi", "pdh", "wevtapi", "tdh", "authz", "netutils", "oledlg",
    "urlmon", "wer", "evr", "dcomp", "d3dcompiler_47", "comsvcs", "dxva2", "nvcuda", "cuda",
    "wsock32", "mfcore", "bthprops.cpl", "cabinet", "msi",
}
# ปลั๊กอินเสริมที่โหลดเฉพาะเมื่อมีฮาร์ดแวร์/ไลบรารีนั้น ไม่กระทบการทำงานปกติ
OPTIONAL = {"openvino.dll", "openvino_tensorflow_lite_frontend.dll", "tbb12.dll"}

present = {p.name.lower() for p in root.rglob("*") if p.suffix.lower() in (".dll", ".pyd", ".exe")}
missing = defaultdict(set)
checked = 0
for path in root.rglob("*"):
    if path.suffix.lower() not in (".dll", ".pyd", ".exe"):
        continue
    try:
        pe = pefile.PE(str(path), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    except pefile.PEFormatError:
        continue
    checked += 1
    if pe.FILE_HEADER.Machine != 0x8664:
        if "distlib" not in path.parts:                 # ตัวต้นแบบ launcher ของ pip สำหรับเครื่องอื่น ไม่ถูกรัน
            missing["<not x64>"].add(str(path.relative_to(root)))
        continue
    for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        name = entry.dll.decode().lower()
        stem = name[:-4] if name.endswith(".dll") else name
        if name in present or stem in SYSTEM or name.startswith(("api-ms-win-", "ext-ms-")):
            continue
        missing[name].add(str(path.relative_to(root)))

problems = {name: users for name, users in missing.items() if name not in OPTIONAL}
print(f"checked {checked} PE files; optional plugins skipped: {sorted(set(missing) & OPTIONAL)}")
for name, users in sorted(problems.items()):
    print(f"MISSING {name}: {len(users)} -> {sorted(users)[:4]}")
sys.exit(1 if problems else 0)
