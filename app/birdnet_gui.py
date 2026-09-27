#!/usr/bin/env python3
"""Simple desktop UI for the BirdNET to eBird workflow."""

import os
import sys
import queue
import re
import threading
import subprocess
import time
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from review_store import load_reviews, save_review

PYTHON = sys.executable
SCRIPT = Path(__file__).resolve().parent / "field_audio_to_ebird.py"
ICON = Path(__file__).resolve().parent / "assets" / "app_icon.png"

DEF_AUDIO = ""
DEF_OUT   = str(Path.home() / "BirdNET_eBird")
COORD_HINT = "e.g. 13.81195,100.55317"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("BirdNET → eBird clipper")
        root.geometry("960x720")
        try:   # ไอคอนหน้าต่าง/Dock (บน mac แทนไอคอน Python)
            self._icon = tk.PhotoImage(file=str(ICON))
            root.iconphoto(True, self._icon)
        except tk.TclError:
            pass
        self.q: queue.Queue = queue.Queue()
        self.proc = None
        self.job_running = False
        self.quick_busy = False
        self.quick_q: queue.Queue = queue.Queue()
        self.quick_files = []
        self.audio_player = None
        self._closing = False
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        outer = ttk.Frame(root, padding=8)
        outer.pack(fill="both", expand=True)

        # ---- หน้าเริ่มต้นและแท็บสำหรับงานละเอียด ----
        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill="x")
        quick = ttk.Frame(self.nb, padding=18)
        analyze = ttk.Frame(self.nb, padding=8)
        merge = ttk.Frame(self.nb, padding=8)
        review = ttk.Frame(self.nb, padding=8)
        self.nb.add(quick, text="Start")
        self.nb.add(analyze, text="Advanced settings")
        self.nb.add(merge, text="Merge clips")
        self.nb.add(review, text="Review clips")
        self._build_analyze(analyze)
        self._build_merge(merge)
        self._build_review(review)
        self._build_quick(quick)
        self.review_tab = review
        self.nb.select(quick)

        # ---- แถบ progress + status ร่วม ----
        run_row = ttk.Frame(outer)
        run_row.pack(fill="x", pady=(8, 4))
        run_row.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(run_row, mode="indeterminate")
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.status = tk.StringVar(value="Ready")
        ttk.Label(run_row, textvariable=self.status).grid(row=0, column=1, sticky="e")

        # รายละเอียดถูกพับไว้ก่อน เพื่อให้หน้าเริ่มต้นอ่านง่าย
        self.log_visible = False
        self.log_toggle = ttk.Button(outer, text="Show details ▾", command=self._toggle_log)
        self.log_toggle.pack(anchor="w")
        logf = ttk.Frame(outer)
        self.log_frame = logf
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(0, weight=1)
        self.log = tk.Text(logf, height=14, wrap="none", bg="#111", fg="#ddd",
                           insertbackground="#ddd", font=("Menlo" if sys.platform == "darwin" else "Consolas", 10))
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log["yscrollcommand"] = sb.set

        # Ctrl+C/V/X/A + คลิกขวา ให้ทำงานทุก keyboard layout (เช่น ไทย)
        self._install_clipboard_fix()
        # โหลด pandas/numpy ล่วงหน้าตอนผู้ใช้ยังไม่กดอะไร -> กดเลือกไฟล์ครั้งแรกไม่ต้องรอ import
        root.after(500, lambda: threading.Thread(target=self._preload_pipeline, daemon=True).start())

    @staticmethod
    def _preload_pipeline():
        try:
            import field_audio_to_ebird  # noqa: F401
        except Exception:  # noqa: BLE001  ถ้าพังจะแจ้งตอนใช้งานจริง
            pass

    def _on_close(self):
        if self.job_running:
            if not messagebox.askokcancel("Stop the current job?",
                                          "Closing now stops the analysis.\n"
                                          "Finished files are saved; the next run continues with the remaining files."):
                return
            self._closing = True
            self._stop_sent = False
            self._close_deadline = time.monotonic() + 15
            self.status.set("Stopping…")
            self._stop_job_then_close()
            return
        self._close_now()

    def _stop_job_then_close(self):
        proc = self.proc
        if self.job_running and time.monotonic() < self._close_deadline:
            if proc is not None and proc.poll() is None and not self._stop_sent:
                proc.terminate()   # CLI เปลี่ยน SIGTERM เป็น KeyboardInterrupt แล้ว rollback เอง
                self._stop_sent = True
            self.root.after(100, self._stop_job_then_close)
            return
        if proc is not None and proc.poll() is None:
            proc.kill()
        self._close_now()

    def _close_now(self):
        self.review_stop()
        self.root.destroy()

    def _build_quick(self, frm):
        style = ttk.Style(self.root)
        style.configure("Quick.TButton", font=("Helvetica", 16, "bold"), padding=(18, 16))
        style.configure("QuickAction.TButton", font=("Helvetica", 12), padding=(12, 10))
        ttk.Label(frm, text="Analyze bird recordings", font=("Helvetica", 23, "bold")).pack(anchor="w", pady=(0, 7))
        ttk.Label(frm, text="Choose an audio file or a folder — analysis starts right away.",
                  font=("Helvetica", 13)).pack(anchor="w", pady=(0, 16))

        buttons = ttk.Frame(frm)
        buttons.pack(fill="x", pady=(0, 10))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        self.quick_file_btn = ttk.Button(buttons, text="Choose file and start",
                                         style="Quick.TButton", command=lambda: self._quick_choose(False))
        self.quick_file_btn.grid(row=0, column=0, sticky="ew", padx=(0, 7))
        self.quick_folder_btn = ttk.Button(buttons, text="Choose folder and start",
                                           style="Quick.TButton", command=lambda: self._quick_choose(True))
        self.quick_folder_btn.grid(row=0, column=1, sticky="ew", padx=(7, 0))

        self.quick_selected = tk.StringVar(value="No audio selected yet")
        ttk.Label(frm, textvariable=self.quick_selected, foreground="#555",
                  wraplength=840, justify="left").pack(anchor="w", pady=(3, 14))

        actions = ttk.Frame(frm)
        actions.pack(anchor="w", pady=(0, 18))
        self.quick_start_btn = ttk.Button(actions, text="Analyze again",
                                          style="QuickAction.TButton", state="disabled",
                                          command=self._quick_start_selected)
        self.quick_start_btn.pack(side="left", padx=(0, 10))
        ttk.Button(actions, text="Open output folder", style="QuickAction.TButton",
                   command=self._open_output_folder).pack(side="left")

        ttk.Separator(frm).pack(fill="x", pady=(0, 15))
        ttk.Label(frm, text="How to use", font=("Helvetica", 14, "bold")).pack(anchor="w", pady=(0, 6))
        for step in (
            "1. Click a button above to choose a file or folder",
            "2. If the recording has no date or location, the app asks before starting",
            "3. When it finishes, listen to and review the clips in the ‘Review clips’ tab",
        ):
            ttk.Label(frm, text=step, font=("Helvetica", 12)).pack(anchor="w", pady=2)

    def _toggle_log(self):
        if self.log_visible:
            self.log_frame.pack_forget()
            self.log_toggle.configure(text="Show details ▾")
        else:
            self.log_frame.pack(fill="both", expand=True, pady=(5, 0))
            self.log_toggle.configure(text="Hide details ▴")
        self.log_visible = not self.log_visible

    def _show_log(self):
        if not self.log_visible:
            self._toggle_log()

    def _open_output_folder(self):
        path = Path(self.out.get().strip()).expanduser()
        if not path.is_dir():
            messagebox.showinfo("No results yet", "The output folder is created after the first successful analysis.")
            return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif os.name == "nt":
                os.startfile(str(path))
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("Cannot open folder", str(exc))

    def _quick_choose(self, folder):
        if self._busy() or self.quick_busy:
            return
        path = self.browse_folder() if folder else self.browse_file()
        if not path:
            return
        self.date.set("")
        self.coords.set("")
        self.start_time.set("")
        self.quick_selected.set(path)
        self.quick_start_btn.configure(state="normal")
        self._quick_start_selected()

    def _quick_start_selected(self):
        if self._busy() or self.quick_busy:
            return
        path = Path(self.audio.get().strip()).expanduser()
        if not path.exists():
            messagebox.showwarning("File not found", "Choose the audio file or folder again.")
            return
        self.quick_busy = True
        self.quick_file_btn.configure(state="disabled")
        self.quick_folder_btn.configure(state="disabled")
        self.quick_start_btn.configure(state="disabled")
        self.status.set("Checking file details…")
        self.progress.start(12)
        threading.Thread(target=self._quick_probe_worker, args=(path,), daemon=True).start()
        self.root.after(100, self._quick_poll)

    def _quick_probe_worker(self, path):
        try:
            from field_audio_to_ebird import collect_inputs, read_audio_metadata
            files = collect_inputs(path)
            if not files:
                raise ValueError("No audio files found at the selected location")
            missing_date = []
            missing_coords = []
            for audio_file in files:
                metadata = read_audio_metadata(audio_file)
                if metadata.get("datetime") is None:
                    missing_date.append(audio_file)
                if metadata.get("lat") is None or metadata.get("lon") is None:
                    missing_coords.append(audio_file)
            self.quick_q.put(("ready", files, missing_date, missing_coords))
        except Exception as exc:
            self.quick_q.put(("error", str(exc)))

    def _quick_poll(self):
        try:
            item = self.quick_q.get_nowait()
        except queue.Empty:
            self.root.after(100, self._quick_poll)
            return
        self.progress.stop()
        self.quick_busy = False
        self.quick_file_btn.configure(state="normal")
        self.quick_folder_btn.configure(state="normal")
        self.quick_start_btn.configure(state="normal")
        if item[0] == "error":
            self.status.set("Could not check the files")
            messagebox.showerror("Cannot open file", item[1])
            return
        _, self.quick_files, missing_date, missing_coords = item
        if (not missing_date or self.date.get().strip()) and (not missing_coords or self.coords.get().strip()):
            self.run(confirmed_batch=True, button=self.quick_start_btn)
        else:
            self.status.set("Waiting for recording details")
            self._quick_details(missing_date, missing_coords)

    def _quick_details(self, missing_date, missing_coords):
        dialog = tk.Toplevel(self.root)
        dialog.title("Recording details")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Add details before analysis",
                  font=("Helvetica", 17, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(body, text="The audio file is missing the details below. Enter the real values from the recording session.",
                  wraplength=450).pack(anchor="w", pady=(0, 12))
        first_entry = None
        if missing_date:
            ttk.Label(body, text="Recording date (YYYY-MM-DD) *").pack(anchor="w")
            entry = ttk.Entry(body, textvariable=self.date, width=30)
            entry.pack(fill="x", pady=(3, 10))
            first_entry = entry
        if missing_coords:
            ttk.Label(body, text="Recording location (latitude,longitude) *").pack(anchor="w")
            entry = ttk.Entry(body, textvariable=self.coords, width=42)
            entry.pack(fill="x", pady=(3, 4))
            ttk.Label(body, text="e.g. 13.81195,100.55317, or paste a Google Maps link",
                      foreground="#555").pack(anchor="w", pady=(0, 10))
            if first_entry is None:
                first_entry = entry
        ttk.Label(body, text="Recording start time (if known)").pack(anchor="w")
        ttk.Entry(body, textvariable=self.start_time, width=20).pack(anchor="w", pady=(3, 4))
        ttk.Label(body, text="e.g. 06:30, so each clip gets the correct clock time",
                  foreground="#555").pack(anchor="w", pady=(0, 9))
        if len(self.quick_files) > 1:
            supplied = " and ".join(label for needed, label in
                                    ((missing_date, "date"), (missing_coords, "location")) if needed)
            ttk.Label(body, text=f"{len(self.quick_files)} files selected: the {supplied} you enter "
                                 "will be used for every file.\n"
                                 "If the files differ, analyze each set separately.",
                      foreground="#8a5100", wraplength=450, justify="left").pack(anchor="w", pady=(0, 12))

        def submit(_event=None):
            from field_audio_to_ebird import parse_coords
            if missing_date:
                value = self.date.get().strip()
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError(value)
                except ValueError:
                    messagebox.showwarning("Invalid date", "Enter the date as YYYY-MM-DD, e.g. 2026-09-27", parent=dialog)
                    return
            if missing_coords and parse_coords(self.coords.get()) is None:
                messagebox.showwarning("Invalid location", "Enter coordinates, e.g. 13.81195,100.55317", parent=dialog)
                return
            if len(self.quick_files) > 1 and (missing_date or missing_coords):
                if not messagebox.askokcancel("Use for all files?",
                                              f"Confirm that all {len(self.quick_files)} files share the details you entered.",
                                              parent=dialog):
                    return
            dialog.destroy()
            self.run(confirmed_batch=True, button=self.quick_start_btn)

        action = ttk.Frame(body)
        action.pack(fill="x", pady=(6, 0))
        ttk.Button(action, text="Start analysis", style="QuickAction.TButton",
                   command=submit).pack(side="right", padx=(8, 0))
        ttk.Button(action, text="Cancel", command=dialog.destroy).pack(side="right")
        dialog.bind("<Return>", submit)
        dialog.grab_set()
        self._install_clipboard_fix()
        if first_entry is not None:
            first_entry.focus_set()

    def _install_clipboard_fix(self):
        # ปัญหา (Win/Linux, Thai layout): Tk ผูก Ctrl+V กับ keysym "v" — พอ layout เป็นไทย keysym
        #   เปลี่ยน เลย paste ไม่ได้
        # ปัญหา (macOS): ต้องผูก Cmd แยกจาก Ctrl (คนละ modifier/state bit) ไม่งั้น Cmd+V ไม่ทำงาน
        #   และ trackpad "คลิกขวา" (two-finger / control-click) ส่ง <Button-2> ไม่ใช่ <Button-3>
        # แก้: ดู keysym ก่อน (layout อังกฤษ) แล้วค่อยดูปุ่มจริงซึ่งไม่ขึ้นกับ layout:
        #   Windows = virtual-key code (V=86 C=67 X=88 A=65); macOS Tk เก็บ Mac virtual key code
        #   ไว้ใน byte บนสุดของ keycode (V=9 C=8 X=7 A=0; 0xff = event จำลอง)
        #   ผูก Ctrl (Win/Linux) หรือ Command (mac) — Tk ยิง binding นี้เมื่อกด modifier นั้นอยู่แล้ว
        is_mac = sys.platform == "darwin"
        physical = {9: "v", 8: "c", 7: "x", 0: "a"} if is_mac else {86: "v", 67: "c", 88: "x", 65: "a"}

        def on_key(e):
            w = e.widget
            letter = e.keysym.lower()
            if letter not in ("v", "c", "x", "a"):
                letter = physical.get(e.keycode >> 24 if is_mac else e.keycode)
            if letter in ("v", "c", "x"):
                w.event_generate({"v": "<<Paste>>", "c": "<<Copy>>", "x": "<<Cut>>"}[letter])
                return "break"
            if letter == "a":                 # select all
                try:
                    w.select_range(0, "end"); w.icursor("end")
                except Exception:  # noqa: BLE001
                    pass
                return "break"
            return None

        self._menu_target = None
        menu = getattr(self, "_edit_menu", None)
        if menu is None:                      # สร้างครั้งเดียว (ฟังก์ชันนี้ถูกเรียกซ้ำตอนเปิด dialog)
            menu = self._edit_menu = tk.Menu(self.root, tearoff=0)
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
                # ผูกที่ตัว widget -> ทำงานก่อน class binding; บน mac Ctrl+A/… เป็นปุ่มแก้ไขแบบ emacs
                w.bind("<Command-KeyPress>" if is_mac else "<Control-KeyPress>", on_key)
                if is_mac:
                    w.bind("<Button-2>", popup)            # mac secondary-click บน Tk 8.6
                w.bind("<Button-3>", popup)                # right-click (Win/Linux และ mac บน Tk 9)
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
        self.coords = tk.StringVar(value="")
        self.date = tk.StringVar(value="")
        self.start_time = tk.StringVar(value="")
        self.place = tk.StringVar(value="")
        ttk.Label(grid, text="Coordinates or Google Maps link").grid(
            row=0, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.coords, width=36).grid(row=0, column=1, padx=(2, 14))
        ttk.Label(grid, text=COORD_HINT, foreground="#888").grid(
            row=0, column=2, sticky="w", padx=(2, 0))
        ttk.Label(grid, text="Date if no metadata (YYYY-MM-DD)").grid(row=1, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.date, width=14).grid(row=1, column=1, sticky="w", padx=2)
        ttk.Label(grid, text="Start time (HH:MM)").grid(row=1, column=2, sticky="w")
        ttk.Entry(grid, textvariable=self.start_time, width=12).grid(row=1, column=3, padx=2)
        r += 1

        grid2 = ttk.Frame(frm); grid2.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(grid2, text="Place").grid(row=0, column=0, sticky="w")
        ttk.Entry(grid2, textvariable=self.place, width=34).grid(row=0, column=1, padx=(2, 14))
        ttk.Label(grid2, text="min_conf").grid(row=0, column=2, sticky="w")
        self.min_conf = tk.DoubleVar(value=0.5)
        ttk.Spinbox(grid2, from_=0.0, to=1.0, increment=0.05, width=6,
                    textvariable=self.min_conf).grid(row=0, column=3, padx=2)
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
        self.continuous = tk.BooleanVar(value=False)
        ttk.Checkbutton(tg, text="to mono", variable=self.mono).pack(side="left", padx=8)
        ttk.Checkbutton(tg, text="mel-spectrogram", variable=self.spec).pack(side="left", padx=8)
        ttk.Checkbutton(tg, text="alt species", variable=self.alt).pack(side="left", padx=8)
        ttk.Checkbutton(tg, text="cut unknown", variable=self.unknown).pack(side="left", padx=8)
        ttk.Checkbutton(tg, text="force redo", variable=self.force).pack(side="left", padx=8)
        ttk.Checkbutton(tg, text="use file time", variable=self.filetime).pack(side="left", padx=8)
        r += 1

        ttk.Checkbutton(frm, text="Keep first-to-last call continuous for each species (same individual only)",
                        variable=self.continuous).grid(row=r, column=0, columnspan=3, sticky="w", **pad)
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

    def _build_review(self, frm):
        frm.columnconfigure(0, weight=1)
        ttk.Label(frm, text="Review clips", font=("Helvetica", 17, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(frm, text="Select a clip → play it → check the species → approve",
                  font=("Helvetica", 12)).grid(row=1, column=0, sticky="w", pady=(0, 8))
        top = ttk.Frame(frm)
        top.grid(row=2, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        self.review_summary = tk.StringVar()
        ttk.Entry(top, textvariable=self.review_summary).grid(row=0, column=0, sticky="ew")
        ttk.Button(top, text="Open other results…", command=self.review_browse).grid(row=0, column=1, padx=4)
        ttk.Button(top, text="Reload", command=self.review_load).grid(row=0, column=2)

        columns = ("file", "species", "confidence", "status")
        self.review_tree = ttk.Treeview(frm, columns=columns, show="headings", height=8)
        for key, label, width in (("file", "Clip", 310), ("species", "BirdNET species", 190),
                                  ("confidence", "Confidence", 85), ("status", "Status", 120)):
            self.review_tree.heading(key, text=label)
            self.review_tree.column(key, width=width, stretch=(key == "file"))
        self.review_tree.grid(row=3, column=0, sticky="ew", pady=5)
        self.review_tree.bind("<<TreeviewSelect>>", self._review_select)
        self.review_rows = {}

        controls = ttk.Frame(frm)
        controls.grid(row=4, column=0, sticky="w", pady=(4, 7))
        ttk.Button(controls, text="▶ Play", style="QuickAction.TButton",
                   command=self.review_play).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="■ Stop", style="QuickAction.TButton",
                   command=self.review_stop).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Spectrogram", style="QuickAction.TButton",
                   command=self.review_spectrogram).pack(side="left")

        fields = ttk.Frame(frm)
        fields.grid(row=5, column=0, sticky="ew", pady=5)
        fields.columnconfigure(1, weight=1)
        self.review_common = tk.StringVar()
        self.review_scientific = tk.StringVar()
        self.review_rating = tk.StringVar()
        self.review_notes = tk.StringVar()
        ttk.Label(fields, text="Reviewed common name").grid(row=0, column=0, sticky="w")
        ttk.Entry(fields, textvariable=self.review_common).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Label(fields, text="Scientific name").grid(row=0, column=2, sticky="w")
        ttk.Entry(fields, textvariable=self.review_scientific, width=24).grid(row=0, column=3, padx=5)
        ttk.Label(fields, text="Audio quality (1–5)").grid(row=1, column=0, sticky="w")
        ttk.Combobox(fields, textvariable=self.review_rating, values=("1", "2", "3", "4", "5"),
                     state="readonly", width=5).grid(row=1, column=1, sticky="w", padx=5)
        ttk.Label(fields, text="Notes").grid(row=1, column=2, sticky="w")
        ttk.Entry(fields, textvariable=self.review_notes, width=24).grid(row=1, column=3, padx=5)

        actions = ttk.Frame(frm)
        actions.grid(row=6, column=0, sticky="w", pady=(8, 4))
        ttk.Button(actions, text="✓ Approve → Ready", style="QuickAction.TButton",
                   command=lambda: self.review_save("Approved")).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Reject", style="QuickAction.TButton",
                   command=lambda: self.review_save("Rejected")).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Keep pending", style="QuickAction.TButton",
                   command=lambda: self.review_save("Pending")).pack(side="left")
        ttk.Label(frm, text="Only approved clips are copied to Ready/  •  Audio quality must be rated by a person",
                  foreground="#666").grid(row=7, column=0, sticky="w", pady=(4, 0))

    def review_browse(self):
        path = filedialog.askopenfilename(title="Choose summary.xlsx", filetypes=[("Excel", "*.xlsx")])
        if path:
            self.review_summary.set(path)
            self.review_load()

    def review_load(self):
        try:
            summary = Path(self.review_summary.get()).expanduser()
            rows = load_reviews(summary)
            self.review_rows = {str(row["_row"]): row for row in rows}
            self.review_tree.delete(*self.review_tree.get_children())
            for key, row in self.review_rows.items():
                status = row.get("Review status") or "Pending"
                ready = row.get("Ready file")
                if status == "Approved" and (not ready or
                    not (summary.parent / str(ready)).is_file()):
                    status = "Missing Ready"
                display_status = "Ready file missing" if status == "Missing Ready" else status
                self.review_tree.insert("", "end", iid=key,
                                        values=(row.get("File") or "", row.get("Species (common)") or "",
                                                row.get("Max confidence") or "", display_status))
        except Exception as exc:
            messagebox.showerror("Cannot open summary", str(exc))

    def _selected_review(self):
        selected = self.review_tree.selection()
        return (selected[0], self.review_rows[selected[0]]) if selected else (None, None)

    def _review_select(self, _event=None):
        _key, row = self._selected_review()
        if row is None:
            return
        self.review_common.set(row.get("Reviewed common") or row.get("Species (common)") or "")
        self.review_scientific.set(row.get("Reviewed scientific") or row.get("Species (scientific)") or "")
        rating = row.get("Quality rating")   # summary เก่าอาจเก็บเป็น 3.0
        self.review_rating.set(str(int(rating)) if isinstance(rating, (int, float)) and rating == rating else "")
        self.review_notes.set(row.get("Review notes") or "")

    def _review_audio_path(self):
        _key, row = self._selected_review()
        if row is None:
            raise ValueError("Select a clip first")
        parent = Path(self.review_summary.get()).expanduser().resolve().parent
        path = (parent / str(row["File"])).resolve()
        if not path.is_relative_to(parent):
            raise ValueError("Clip path is outside the output folder")
        return path

    def review_play(self):
        try:
            self.review_stop()
            path = self._review_audio_path()
            if not path.is_file():
                raise FileNotFoundError(path)
            if sys.platform != "darwin":
                raise RuntimeError("Preview uses macOS afplay")
            self.audio_player = subprocess.Popen(["afplay", str(path)])
        except Exception as exc:
            messagebox.showerror("Cannot play clip", str(exc))

    def review_stop(self):
        if self.audio_player is not None and self.audio_player.poll() is None:
            self.audio_player.terminate()
        self.audio_player = None

    def review_spectrogram(self):
        try:
            path = self._review_audio_path().with_suffix(".png")
            if not path.is_file():
                raise FileNotFoundError(f"Spectrogram not found: {path}")
            subprocess.Popen(["open", str(path)])
        except Exception as exc:
            messagebox.showerror("Cannot open spectrogram", str(exc))

    def review_save(self, status):
        key, row = self._selected_review()
        if key is None:
            messagebox.showwarning("No clip", "Select a clip first")
            return
        try:
            rating = int(self.review_rating.get()) if self.review_rating.get() else None
            ready = save_review(Path(self.review_summary.get()), int(key), status=status,
                                common=self.review_common.get(), scientific=self.review_scientific.get(),
                                rating=rating, notes=self.review_notes.get(),
                                expected_file=str(row["File"]))
            self.review_load()
            self.review_tree.selection_set(key)
            self.review_tree.see(key)
            self._log(f"Review {status}: {ready or key}")
        except Exception as exc:
            messagebox.showerror("Could not save review", str(exc))

    # ---------- Analyze pickers ----------
    def browse_file(self):
        p = filedialog.askopenfilename(
            title="Choose an audio file",
            filetypes=[("Audio", "*.wav *.mp3 *.flac *.m4a *.ogg *.aif *.aiff"), ("All", "*.*")])
        if p:
            self.audio.set(p)
        return p

    def browse_folder(self):
        p = filedialog.askdirectory(title="Choose a folder of recordings")
        if p:
            self.audio.set(p)
        return p

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
        return self.job_running or self.quick_busy

    def _start(self, cmd, btn):
        if self._busy():
            messagebox.showinfo("Busy", "Wait for the current job to finish.")
            return
        if not SCRIPT.exists():
            messagebox.showerror("Script not found", str(SCRIPT))
            return
        self.log.delete("1.0", "end")
        self._log("command: " + " ".join(f'"{a}"' if " " in a else a for a in cmd))
        self._active_btn = btn
        self._clip_count = None
        self._generated_summaries = []
        self._already_done = False
        btn["state"] = "disabled"
        self.quick_file_btn.configure(state="disabled")
        self.quick_folder_btn.configure(state="disabled")
        self.quick_start_btn.configure(state="disabled")
        self.job_running = True
        self.status.set("Analyzing…" if self._active_is_analyze else "Merging clips…")
        self.progress.start(12)
        threading.Thread(target=self._worker, args=(cmd,), daemon=True).start()
        self.root.after(100, self._poll)

    def run(self, confirmed_batch=False, button=None):
        audio = self.audio.get().strip().strip('"')
        if not audio:
            messagebox.showwarning("No audio selected", "Choose an audio file or folder first.")
            return
        if self.date.get().strip():
            try:
                datetime.strptime(self.date.get().strip(), "%Y-%m-%d")
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.date.get().strip()):
                    raise ValueError("CLI requires YYYY-MM-DD")
            except ValueError:
                messagebox.showwarning("Invalid date", "Enter the date as YYYY-MM-DD, e.g. 2026-09-27")
                return
        if self.start_time.get().strip():
            value = self.start_time.get().strip()
            try:
                datetime.strptime(value, "%H:%M:%S" if value.count(":") == 2 else "%H:%M")
            except ValueError:
                messagebox.showwarning("Invalid time", "Enter the recording start time, e.g. 06:30")
                return
        same_date_for_all = False
        if self.date.get().strip() and Path(audio).is_dir():
            audio_files = [p for p in Path(audio).iterdir()
                           if p.is_file() and p.suffix.lower() in
                           (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aif", ".aiff")]
            if len(audio_files) > 1:
                if not confirmed_batch and not messagebox.askokcancel(
                        "One date for every file?",
                        f"The entered date will be used for all {len(audio_files)} audio files. "
                        "Confirm they were recorded on the same date. Otherwise, process each date separately."):
                    return
                same_date_for_all = True
        if self.continuous.get() and not messagebox.askokcancel(
                "Continuous recording",
                "Keep one continuous span from the first to last detection of each species? "
                "Use this only when you have checked the recording is of the same individual."):
            return
        cmd = self._launch() + [audio, "-o", self.out.get().strip(),
               "--min-conf", str(self.min_conf.get()),
               "--occurrence-gap", str(self.gap.get()),
               "--lead", str(self.lead.get()), "--tail", str(self.tail.get()),
               "--target-dbfs", str(self.dbfs.get()),
               "--mono" if self.mono.get() else "--no-mono",
               "--spectrogram" if self.spec.get() else "--no-spectrogram",
               "--alt-species" if self.alt.get() else "--no-alt-species",
               "--unknown" if self.unknown.get() else "--no-unknown"]
        if self.force.get():
            if not messagebox.askokcancel("Redo analysis", "Redo will replace generated clips for this source "
                                            "and reset their review status to Pending. Continue?"):
                return
            cmd += ["--force"]
        if self.continuous.get():
            cmd += ["--keep-continuous"]
        if self.filetime.get():
            cmd += ["--use-filetime"]
        if str(self.coords.get()).strip():
            cmd += ["--coords", str(self.coords.get()).strip()]
        if self.date.get().strip():
            cmd += ["--date", self.date.get().strip()]
        if same_date_for_all:
            cmd += ["--same-date-for-all"]
        if self.start_time.get().strip():
            cmd += ["--start-time", self.start_time.get().strip()]
        if self.place.get().strip():
            cmd += ["--place", self.place.get().strip()]
        self._active_is_analyze = True
        self._start(cmd, button or self.run_btn)

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
        self._active_is_analyze = False
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
                if isinstance(item, str):
                    match = re.search(r"\bDone: (\d+) clips\b", item)
                    if match:
                        self._clip_count = int(match.group(1))
                    if " -> " in item and "summary.xlsx" in item:
                        candidate = Path(item.split(" -> ", 1)[1].strip())
                        if candidate.is_file() and candidate not in self._generated_summaries:
                            self._generated_summaries.append(candidate)
                        if "already up to date" in item:
                            self._already_done = True
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _finish(self, code):
        self.progress.stop()
        getattr(self, "_active_btn", self.run_btn)["state"] = "normal"
        self.quick_file_btn.configure(state="normal")
        self.quick_folder_btn.configure(state="normal")
        if self.audio.get().strip():
            self.quick_start_btn.configure(state="normal")
        self.proc = None
        self.job_running = False
        if self._closing:
            return
        if code == 0:
            self.status.set("Done ✓")
            self._log("\n=== Done ===")
            if self._active_is_analyze:
                summaries = self._generated_summaries
                if len(summaries) == 1:
                    self.review_summary.set(str(summaries[0]))
                    self.review_load()
                    if self.review_rows:
                        self.nb.select(self.review_tab)
                        self.status.set("Already analyzed — showing the existing results (tick “force redo” to analyze again)"
                                        if self._already_done and not self._clip_count
                                        else "Done — select a clip to listen and review ✓")
                elif len(summaries) > 1:
                    self.status.set("Done — results span several dates; open the output folder to choose one")
                elif self._clip_count == 0:
                    self.status.set("Done — no bird sounds met the current settings")
        else:
            self.status.set("Analysis failed — see the details below")
            self._log(f"\n!! finished with exit {code}")
            self._show_log()
            if code == 2:
                messagebox.showwarning("Recording details incomplete",
                                       "Enter the real recording date and location, then try again.")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
