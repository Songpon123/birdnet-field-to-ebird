#!/usr/bin/env python3
"""
birdnet_gui.py — Tkinter GUI (stdlib) for field_audio_to_ebird.py
-----------------------------------------------------------------
Two tabs:
  - Analyze: pick a file/folder, set lat/lon/date/min_conf, toggles, Run.
  - Merge:   combine several clips of the SAME individual bird into one file
             (eBird guideline: 1 s silence between, trim edges, re-normalize).
Runs field_audio_to_ebird.py via subprocess in a thread (UI stays responsive,
and TensorFlow/LiteRT runs in a separate process).
UI text is English so it is usable anywhere; code comments stay Thai.
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
COORD_HINT = "e.g. 13.8119502,100.553166  (or paste a Google Maps link)"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("BirdNET → eBird clipper")
        root.geometry("880x680")
        self.q: queue.Queue = queue.Queue()
        self.proc = None

        outer = ttk.Frame(root, padding=8)
        outer.pack(fill="both", expand=True)

        # ---- แท็บ Analyze / Merge ----
        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill="x")
        analyze = ttk.Frame(self.nb, padding=8)
        merge = ttk.Frame(self.nb, padding=8)
        self.nb.add(analyze, text="Analyze")
        self.nb.add(merge, text="Merge (same individual)")
        self._build_analyze(analyze)
        self._build_merge(merge)

        # ---- แถบ progress + status ร่วม ----
        run_row = ttk.Frame(outer)
        run_row.pack(fill="x", pady=(8, 4))
        run_row.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(run_row, mode="indeterminate")
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.status = tk.StringVar(value="Ready")
        ttk.Label(run_row, textvariable=self.status).grid(row=0, column=1, sticky="e")

        # ---- log ร่วม ----
        ttk.Label(outer, text="Log").pack(anchor="w")
        logf = ttk.Frame(outer)
        logf.pack(fill="both", expand=True)
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(0, weight=1)
        self.log = tk.Text(logf, height=14, wrap="none", bg="#111", fg="#ddd",
                           insertbackground="#ddd", font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log["yscrollcommand"] = sb.set

        # Ctrl+C/V/X/A + คลิกขวา ให้ทำงานทุก keyboard layout (เช่น ไทย)
        self._install_clipboard_fix()

    def _install_clipboard_fix(self):
        # ปัญหา: Tk ผูก Ctrl+V กับ keysym "v" — พอ layout เป็นไทย keysym เปลี่ยน เลย paste ไม่ได้
        # แก้: ผูกตาม keycode ปุ่มจริง (V=86 C=67 X=88 A=65) ซึ่งไม่ขึ้นกับ layout + เมนูคลิกขวา
        def on_key(e):
            if not (e.state & 0x0004):        # 0x0004 = Control
                return None
            w, kc = e.widget, e.keycode
            if kc == 86:                      # V -> paste
                w.event_generate("<<Paste>>"); return "break"
            if kc == 67:                      # C -> copy
                w.event_generate("<<Copy>>"); return "break"
            if kc == 88:                      # X -> cut
                w.event_generate("<<Cut>>"); return "break"
            if kc == 65:                      # A -> select all
                try:
                    w.select_range(0, "end"); w.icursor("end")
                except Exception:  # noqa: BLE001
                    pass
                return "break"
            return None

        self._menu_target = None
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Cut",
                         command=lambda: self._menu_gen("<<Cut>>"))
        menu.add_command(label="Copy",
                         command=lambda: self._menu_gen("<<Copy>>"))
        menu.add_command(label="Paste",
                         command=lambda: self._menu_gen("<<Paste>>"))

        def popup(e):
            self._menu_target = e.widget
            e.widget.focus_set()
            try:
                menu.tk_popup(e.x_root, e.y_root)
            finally:
                menu.grab_release()

        def apply(w):
            if isinstance(w, (ttk.Entry, tk.Entry)):
                w.bind("<Control-KeyPress>", on_key)   # ผูกที่ตัว widget -> ทำงานก่อน class binding
                w.bind("<Button-3>", popup)
            for c in w.winfo_children():
                apply(c)
        apply(self.root)

    def _menu_gen(self, ev):
        if self._menu_target is not None:
            self._menu_target.event_generate(ev)

    # =========================== แท็บ Analyze ===========================
    def _build_analyze(self, frm):
        pad = {"padx": 6, "pady": 3}
        frm.columnconfigure(1, weight=1)
        r = 0

        ttk.Label(frm, text="Audio file / folder").grid(row=r, column=0, sticky="w", **pad)
        self.audio = tk.StringVar(value=DEF_AUDIO)
        ttk.Entry(frm, textvariable=self.audio).grid(row=r, column=1, sticky="ew", **pad)
        bf = ttk.Frame(frm); bf.grid(row=r, column=2, **pad)
        ttk.Button(bf, text="File…", width=7, command=self.browse_file).pack(side="left")
        ttk.Button(bf, text="Folder…", width=9, command=self.browse_folder).pack(side="left")
        r += 1

        ttk.Label(frm, text="Output folder").grid(row=r, column=0, sticky="w", **pad)
        self.out = tk.StringVar(value=DEF_OUT)
        ttk.Entry(frm, textvariable=self.out).grid(row=r, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Choose…", command=self.browse_output).grid(row=r, column=2, **pad)
        r += 1

        grid = ttk.Frame(frm); grid.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        self.coords = tk.StringVar(value=f"{DEF_LAT},{DEF_LON}")
        self.date = tk.StringVar(value="")
        self.place = tk.StringVar(value="")
        ttk.Label(grid, text="Coordinates lat,lon / Google Maps link (blank = auto)").grid(
            row=0, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.coords, width=36).grid(row=0, column=1, padx=(2, 14))
        ttk.Label(grid, text="Date (blank = auto)").grid(row=0, column=2, sticky="w")
        ttk.Entry(grid, textvariable=self.date, width=14).grid(row=0, column=3, padx=2)
        # ตัวอย่างพิกัด
        ttk.Label(grid, text=COORD_HINT, foreground="#888").grid(
            row=1, column=1, sticky="w", padx=(2, 0))
        r += 1

        grid2 = ttk.Frame(frm); grid2.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(grid2, text="Place").grid(row=0, column=0, sticky="w")
        ttk.Entry(grid2, textvariable=self.place, width=34).grid(row=0, column=1, padx=(2, 14))
        ttk.Label(grid2, text="min_conf").grid(row=0, column=2, sticky="w")
        self.min_conf = tk.DoubleVar(value=0.5)
        ttk.Spinbox(grid2, from_=0.0, to=1.0, increment=0.05, width=6,
                    textvariable=self.min_conf).grid(row=0, column=3, padx=(2, 14))
        ttk.Label(grid2, text="high-pass Hz (0=off)").grid(row=0, column=4, sticky="w")
        self.highpass = tk.DoubleVar(value=0)
        ttk.Spinbox(grid2, from_=0, to=2000, increment=50, width=7,
                    textvariable=self.highpass).grid(row=0, column=5, padx=2)
        r += 1

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

        tg = ttk.Frame(frm); tg.grid(row=r, column=0, columnspan=3, sticky="w", **pad)
        self.mono = tk.BooleanVar(value=True)
        self.spec = tk.BooleanVar(value=True)
        self.alt = tk.BooleanVar(value=True)
        self.unknown = tk.BooleanVar(value=False)
        self.force = tk.BooleanVar(value=False)
        self.filetime = tk.BooleanVar(value=False)
        ttk.Checkbutton(tg, text="to mono", variable=self.mono).pack(side="left", padx=8)
        ttk.Checkbutton(tg, text="mel-spectrogram", variable=self.spec).pack(side="left", padx=8)
        ttk.Checkbutton(tg, text="alt species", variable=self.alt).pack(side="left", padx=8)
        ttk.Checkbutton(tg, text="cut unknown", variable=self.unknown).pack(side="left", padx=8)
        ttk.Checkbutton(tg, text="force redo", variable=self.force).pack(side="left", padx=8)
        ttk.Checkbutton(tg, text="use file time", variable=self.filetime).pack(side="left", padx=8)
        r += 1

        # ---- Xeno-Canto reference download (optional) ----
        xcf = ttk.Frame(frm); xcf.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        self.xc_key = tk.StringVar(value="")
        self.xc_country = tk.StringVar(value="")
        ttk.Label(xcf, text="Xeno-Canto key (blank = off, link only)").grid(row=0, column=0, sticky="w")
        ttk.Entry(xcf, textvariable=self.xc_key, width=32, show="•").grid(row=0, column=1, padx=(2, 14))
        ttk.Label(xcf, text="country (e.g. thailand)").grid(row=0, column=2, sticky="w")
        ttk.Entry(xcf, textvariable=self.xc_country, width=14).grid(row=0, column=3, padx=2)
        r += 1

        self.run_btn = ttk.Button(frm, text="▶  Analyze", command=self.run)
        self.run_btn.grid(row=r, column=0, sticky="w", **pad)

    # ============================ แท็บ Merge ============================
    def _build_merge(self, frm):
        pad = {"padx": 6, "pady": 3}
        frm.columnconfigure(0, weight=1)

        warn = ("⚠  Only merge recordings of the SAME individual bird "
                "(eBird guideline).\n"
                "BirdNET identifies species, not individuals — confirm by ear first.")
        ttk.Label(frm, text=warn, foreground="#b36b00", justify="left").grid(
            row=0, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(frm, text="Clips to merge (top → bottom order)").grid(
            row=1, column=0, sticky="w", **pad)

        listf = ttk.Frame(frm); listf.grid(row=2, column=0, sticky="nsew", **pad)
        frm.rowconfigure(2, weight=1)
        listf.columnconfigure(0, weight=1)
        self.merge_list = tk.Listbox(listf, height=6, selectmode="extended")
        self.merge_list.grid(row=0, column=0, sticky="nsew")
        lsb = ttk.Scrollbar(listf, command=self.merge_list.yview)
        lsb.grid(row=0, column=1, sticky="ns")
        self.merge_list["yscrollcommand"] = lsb.set

        btns = ttk.Frame(frm); btns.grid(row=2, column=1, sticky="n", **pad)
        ttk.Button(btns, text="Add…", width=10, command=self.merge_add).pack(pady=2)
        ttk.Button(btns, text="Remove", width=10, command=self.merge_remove).pack(pady=2)
        ttk.Button(btns, text="Up", width=10, command=lambda: self.merge_move(-1)).pack(pady=2)
        ttk.Button(btns, text="Down", width=10, command=lambda: self.merge_move(1)).pack(pady=2)
        ttk.Button(btns, text="Clear", width=10, command=self.merge_clear).pack(pady=2)

        outf = ttk.Frame(frm); outf.grid(row=3, column=0, columnspan=2, sticky="ew", **pad)
        outf.columnconfigure(1, weight=1)
        ttk.Label(outf, text="Output file").grid(row=0, column=0, sticky="w")
        self.merge_out = tk.StringVar(value="")
        ttk.Entry(outf, textvariable=self.merge_out).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(outf, text="Save as…", command=self.merge_save_as).grid(row=0, column=2)

        self.merge_btn = ttk.Button(frm, text="🔗  Merge", command=self.merge_run)
        self.merge_btn.grid(row=4, column=0, sticky="w", **pad)

    # ---------- Analyze pickers ----------
    def browse_file(self):
        p = filedialog.askopenfilename(
            title="Choose an audio file",
            filetypes=[("Audio", "*.wav *.mp3 *.flac *.m4a *.ogg *.aif *.aiff"), ("All", "*.*")])
        if p:
            self.audio.set(p)

    def browse_folder(self):
        p = filedialog.askdirectory(title="Choose an audio folder (batch)")
        if p:
            self.audio.set(p)

    def browse_output(self):
        p = filedialog.askdirectory(title="Choose output folder")
        if p:
            self.out.set(p)

    # ---------- Merge list ops ----------
    def merge_add(self):
        ps = filedialog.askopenfilenames(
            title="Choose clips of the same individual",
            filetypes=[("Audio", "*.wav *.mp3 *.flac *.m4a *.ogg *.aif *.aiff"), ("All", "*.*")])
        for p in ps:
            self.merge_list.insert("end", p)

    def merge_remove(self):
        for i in reversed(self.merge_list.curselection()):
            self.merge_list.delete(i)

    def merge_clear(self):
        self.merge_list.delete(0, "end")

    def merge_move(self, delta):
        sel = self.merge_list.curselection()
        if not sel:
            return
        i = sel[0]
        j = i + delta
        if j < 0 or j >= self.merge_list.size():
            return
        text = self.merge_list.get(i)
        self.merge_list.delete(i)
        self.merge_list.insert(j, text)
        self.merge_list.selection_set(j)

    def merge_save_as(self):
        p = filedialog.asksaveasfilename(
            title="Save merged file as", defaultextension=".wav",
            initialfile="GROUPED.wav", filetypes=[("WAV", "*.wav")])
        if p:
            self.merge_out.set(p)

    # ---------- run helpers ----------
    def _log(self, text):
        self.log.insert("end", text + "\n")
        self.log.see("end")

    def _launch(self):
        # frozen (.exe): เรียกตัว exe เองซ้ำ; dev/bundle: python + สคริปต์
        return [sys.executable] if getattr(sys, "frozen", False) else [PYTHON, str(SCRIPT)]

    def _busy(self):
        return self.proc is not None

    def _start(self, cmd, btn):
        if self._busy():
            messagebox.showinfo("Busy", "A job is already running")
            return
        if not SCRIPT.exists():
            messagebox.showerror("Script not found", str(SCRIPT))
            return
        self.log.delete("1.0", "end")
        self._log("command: " + " ".join(f'"{a}"' if " " in a else a for a in cmd))
        self._active_btn = btn
        btn["state"] = "disabled"
        self.status.set("Running…")
        self.progress.start(12)
        threading.Thread(target=self._worker, args=(cmd,), daemon=True).start()
        self.root.after(100, self._poll)

    def run(self):
        audio = self.audio.get().strip().strip('"')
        if not audio:
            messagebox.showwarning("No input", "Choose a file or folder first")
            return
        cmd = self._launch() + [audio, "-o", self.out.get().strip(),
               "--min-conf", str(self.min_conf.get()),
               "--occurrence-gap", str(self.gap.get()),
               "--lead", str(self.lead.get()), "--tail", str(self.tail.get()),
               "--target-dbfs", str(self.dbfs.get()),
               "--mono" if self.mono.get() else "--no-mono",
               "--spectrogram" if self.spec.get() else "--no-spectrogram",
               "--alt-species" if self.alt.get() else "--no-alt-species",
               "--unknown" if self.unknown.get() else "--no-unknown",
               "--highpass", str(self.highpass.get())]
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
        if str(self.xc_key.get()).strip():
            cmd += ["--xc-key", str(self.xc_key.get()).strip()]
            if str(self.xc_country.get()).strip():
                cmd += ["--xc-country", str(self.xc_country.get()).strip()]
        self._start(cmd, self.run_btn)

    def merge_run(self):
        files = list(self.merge_list.get(0, "end"))
        if len(files) < 2:
            messagebox.showwarning("Need clips", "Add at least 2 clips to merge")
            return
        out = self.merge_out.get().strip()
        if not out:
            messagebox.showwarning("No output", "Choose an output file (Save as…)")
            return
        cmd = self._launch() + ["--group"] + files + ["-o", out]
        self._start(cmd, self.merge_btn)

    def _worker(self, cmd):
        try:
            env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
            # กันหน้าต่าง console เด้งตอน GUI spawn python.exe (Windows)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env, bufsize=1,
                creationflags=flags)
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
        getattr(self, "_active_btn", self.run_btn)["state"] = "normal"
        self.proc = None
        if code == 0:
            self.status.set("Done ✓")
            self._log("\n=== Done ===")
        else:
            self.status.set(f"Error (exit {code})")
            self._log(f"\n!! finished with exit {code}")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
