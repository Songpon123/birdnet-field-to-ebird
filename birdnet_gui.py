#!/usr/bin/env python3
"""
birdnet_gui.py — Tkinter GUI (stdlib) สำหรับ field_audio_to_ebird.py
-------------------------------------------------------------------
เลือกไฟล์เดี่ยว/ทั้งโฟลเดอร์, กรอก LAT/LON/วันที่/MIN_CONF, เลือก OUTPUT,
ติ๊ก toggle (mono, spectrogram, ชนิดสำรอง), ปุ่ม Run, log + progress bar.
เรียก field_audio_to_ebird.py ผ่าน subprocess ใน thread (UI ไม่ค้าง,
และ TensorFlow รันแยก process)
"""

import os
import sys
import queue
import threading
import subprocess
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

PYTHON = sys.executable
SCRIPT = Path(__file__).resolve().parent / "field_audio_to_ebird.py"

DEF_AUDIO = ""
DEF_OUT   = str(Path.home() / "BirdNET_eBird")
DEF_LAT, DEF_LON = "12.80", "99.62"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("BirdNET → eBird clipper")
        root.geometry("860x640")
        self.q: queue.Queue = queue.Queue()
        self.proc = None

        pad = {"padx": 6, "pady": 3}
        frm = ttk.Frame(root, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        r = 0

        # ---- ไฟล์/โฟลเดอร์เข้า ----
        ttk.Label(frm, text="ไฟล์เสียง / โฟลเดอร์").grid(row=r, column=0, sticky="w", **pad)
        self.audio = tk.StringVar(value=DEF_AUDIO)
        ttk.Entry(frm, textvariable=self.audio).grid(row=r, column=1, sticky="ew", **pad)
        bf = ttk.Frame(frm); bf.grid(row=r, column=2, **pad)
        ttk.Button(bf, text="ไฟล์…", width=7, command=self.browse_file).pack(side="left")
        ttk.Button(bf, text="โฟลเดอร์…", width=9, command=self.browse_folder).pack(side="left")
        r += 1

        # ---- output ----
        ttk.Label(frm, text="โฟลเดอร์ผลลัพธ์").grid(row=r, column=0, sticky="w", **pad)
        self.out = tk.StringVar(value=DEF_OUT)
        ttk.Entry(frm, textvariable=self.out).grid(row=r, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="เลือก…", command=self.browse_output).grid(row=r, column=2, **pad)
        r += 1

        # ---- พิกัด / วัน / conf ----
        grid = ttk.Frame(frm); grid.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        self.coords = tk.StringVar(value=f"{DEF_LAT},{DEF_LON}")
        self.date = tk.StringVar(value="")
        self.place = tk.StringVar(value="")
        ttk.Label(grid, text="พิกัด lat,lon / ลิงก์ Google Maps (ว่าง=auto)").grid(row=0, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.coords, width=36).grid(row=0, column=1, padx=(2, 14))
        ttk.Label(grid, text="วันที่ (ว่าง=auto)").grid(row=0, column=2, sticky="w")
        ttk.Entry(grid, textvariable=self.date, width=14).grid(row=0, column=3, padx=2)
        r += 1

        grid2 = ttk.Frame(frm); grid2.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(grid2, text="สถานที่").grid(row=0, column=0, sticky="w")
        ttk.Entry(grid2, textvariable=self.place, width=34).grid(row=0, column=1, padx=(2, 14))
        ttk.Label(grid2, text="min_conf").grid(row=0, column=2, sticky="w")
        self.min_conf = tk.DoubleVar(value=0.5)
        ttk.Spinbox(grid2, from_=0.0, to=1.0, increment=0.05, width=6,
                    textvariable=self.min_conf).grid(row=0, column=3, padx=2)
        r += 1

        # ---- พารามิเตอร์การตัด ----
        grid3 = ttk.Frame(frm); grid3.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        self.gap = tk.DoubleVar(value=5.0)
        self.lead = tk.DoubleVar(value=3.0)
        self.tail = tk.DoubleVar(value=3.0)
        self.dbfs = tk.DoubleVar(value=-3.0)
        for i, (lab, var) in enumerate([("occurrence gap (s)", self.gap), ("lead (s)", self.lead),
                                        ("tail (s)", self.tail), ("normalize dBFS", self.dbfs)]):
            ttk.Label(grid3, text=lab).grid(row=0, column=i * 2, sticky="w")
            ttk.Spinbox(grid3, from_=-60, to=60, increment=0.5, width=7,
                        textvariable=var).grid(row=0, column=i * 2 + 1, padx=(2, 14))
        r += 1

        # ---- toggles ----
        tg = ttk.Frame(frm); tg.grid(row=r, column=0, columnspan=3, sticky="w", **pad)
        self.mono = tk.BooleanVar(value=True)
        self.spec = tk.BooleanVar(value=True)
        self.alt = tk.BooleanVar(value=True)
        self.force = tk.BooleanVar(value=False)
        self.filetime = tk.BooleanVar(value=False)
        ttk.Checkbutton(tg, text="แปลง mono", variable=self.mono).pack(side="left", padx=8)
        ttk.Checkbutton(tg, text="mel-spectrogram", variable=self.spec).pack(side="left", padx=8)
        ttk.Checkbutton(tg, text="ชนิดสำรอง", variable=self.alt).pack(side="left", padx=8)
        ttk.Checkbutton(tg, text="ทำซ้ำ (force)", variable=self.force).pack(side="left", padx=8)
        ttk.Checkbutton(tg, text="ใช้เวลาไฟล์", variable=self.filetime).pack(side="left", padx=8)
        r += 1

        # ---- ปุ่ม Run + progress ----
        run_row = ttk.Frame(frm); run_row.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        run_row.columnconfigure(1, weight=1)
        self.run_btn = ttk.Button(run_row, text="▶  เริ่มวิเคราะห์", command=self.run)
        self.run_btn.grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(run_row, mode="indeterminate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=10)
        self.status = tk.StringVar(value="พร้อม")
        ttk.Label(run_row, textvariable=self.status).grid(row=0, column=2, sticky="e")
        r += 1

        # ---- log ----
        ttk.Label(frm, text="Log").grid(row=r, column=0, sticky="w", **pad)
        r += 1
        logf = ttk.Frame(frm); logf.grid(row=r, column=0, columnspan=3, sticky="nsew", **pad)
        frm.rowconfigure(r, weight=1)
        logf.columnconfigure(0, weight=1); logf.rowconfigure(0, weight=1)
        self.log = tk.Text(logf, height=16, wrap="none", bg="#111", fg="#ddd",
                           insertbackground="#ddd", font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logf, command=self.log.yview); sb.grid(row=0, column=1, sticky="ns")
        self.log["yscrollcommand"] = sb.set

    # ---------- file pickers ----------
    def browse_file(self):
        p = filedialog.askopenfilename(
            title="เลือกไฟล์เสียง",
            filetypes=[("Audio", "*.wav *.mp3 *.flac *.m4a *.ogg *.aif *.aiff"), ("All", "*.*")])
        if p:
            self.audio.set(p)

    def browse_folder(self):
        p = filedialog.askdirectory(title="เลือกโฟลเดอร์เสียง (batch)")
        if p:
            self.audio.set(p)

    def browse_output(self):
        p = filedialog.askdirectory(title="เลือกโฟลเดอร์ผลลัพธ์")
        if p:
            self.out.set(p)

    # ---------- run ----------
    def _log(self, text):
        self.log.insert("end", text + "\n")
        self.log.see("end")

    def run(self):
        if self.proc is not None:
            messagebox.showinfo("กำลังรัน", "มีงานกำลังรันอยู่")
            return
        if not SCRIPT.exists():
            messagebox.showerror("ไม่พบสคริปต์", str(SCRIPT))
            return
        audio = self.audio.get().strip().strip('"')
        if not audio:
            messagebox.showwarning("ยังไม่เลือกไฟล์", "เลือกไฟล์หรือโฟลเดอร์ก่อน")
            return

        cmd = [PYTHON, str(SCRIPT), audio, "-o", self.out.get().strip(),
               "--min-conf", str(self.min_conf.get()),
               "--occurrence-gap", str(self.gap.get()),
               "--lead", str(self.lead.get()), "--tail", str(self.tail.get()),
               "--target-dbfs", str(self.dbfs.get()),
               "--mono" if self.mono.get() else "--no-mono",
               "--spectrogram" if self.spec.get() else "--no-spectrogram",
               "--alt-species" if self.alt.get() else "--no-alt-species"]
        if self.force.get():
            cmd += ["--force"]
        if self.filetime.get():
            cmd += ["--use-filetime"]
        if str(self.coords.get()).strip():
            cmd += ["--coords", str(self.coords.get()).strip()]
        if self.date.get().strip():
            cmd += ["--date", self.date.get().strip()]
        if self.place.get().strip():
            cmd += ["--place", self.place.get().strip()]

        self.log.delete("1.0", "end")
        self._log("คำสั่ง: " + " ".join(f'"{a}"' if " " in a else a for a in cmd))
        self.run_btn["state"] = "disabled"
        self.status.set("กำลังรัน…")
        self.progress.start(12)
        threading.Thread(target=self._worker, args=(cmd,), daemon=True).start()
        self.root.after(100, self._poll)

    def _worker(self, cmd):
        try:
            env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env, bufsize=1)
            for line in self.proc.stdout:
                self.q.put(line.rstrip())
            code = self.proc.wait()
            self.q.put(("__DONE__", code))
        except Exception as exc:  # noqa: BLE001
            self.q.put(("__DONE__", f"error: {exc}"))

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple) and item and item[0] == "__DONE__":
                    self._finish(item[1])
                    return
                self._log(item)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _finish(self, code):
        self.progress.stop()
        self.run_btn["state"] = "normal"
        self.proc = None
        if code == 0:
            self.status.set("เสร็จแล้ว ✓")
            self._log("\n=== เสร็จแล้ว ===")
        else:
            self.status.set(f"ผิดพลาด (exit {code})")
            self._log(f"\n!! จบด้วย exit {code}")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
