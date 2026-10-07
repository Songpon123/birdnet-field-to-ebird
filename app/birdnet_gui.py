#!/usr/bin/env python3
"""Simple desktop UI for the BirdNET to eBird workflow."""

import sys
import queue
import re
import threading
import json
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
from entry_formats import DATE_HINT, TIME_HINT, parse_date_entry, parse_time_entry
from habitat import MISMATCH_MIN_CONF, SITE_HABITATS
import paths
import platform_tools
from platform_tools import open_path, play_audio, request_stop
from app_settings import load_settings, save_settings
from review_store import list_results, load_reviews, save_review
import ebird
import second_opinion
import xeno_canto

PYTHON = platform_tools.child_python()   # Windows: python.exe แทน pythonw.exe (อ่าน stdout ได้)
SCRIPT = Path(__file__).resolve().parent / "field_audio_to_ebird.py"
ICON = Path(__file__).resolve().parent / "assets" / "app_icon.png"


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def result_label(info):
    """ผลวิเคราะห์หนึ่งชุด -> ข้อความในเมนู Results"""
    match = re.match(r"(\d{4})\.(\d{2})\.(\d{2})_(\d{2})(\d{2})", info["folder"])
    when = f"{match[1]}-{match[2]}-{match[3]} {match[4]}:{match[5]}" if match else info["folder"]
    sources = ", ".join(info["sources"][:2]) + (f" +{len(info['sources']) - 2}" if len(info["sources"]) > 2 else "")
    reviewed = info["approved"] + info["rejected"]
    model = "" if info["folder"].endswith("_v3preview") else "  ·  BirdNET 2.4"
    return f"{when}  ·  {sources or 'unknown source'}  ·  {info['clips']} clips, {reviewed} reviewed{model}"


def species_links(code, region=None):
    """หน้าของชนิดใน eBird และ Macaulay Library (รูป/เสียงเรียงคะแนนสูงสุด; เสียงกรองตามมณฑลถ้ารู้)"""
    library = f"https://search.macaulaylibrary.org/catalog?taxonCode={quote(code)}&sort=rating_rank_desc"
    return {"species": f"https://ebird.org/species/{quote(code)}",
            "photo": library + "&mediaType=photo",
            "audio": library + "&mediaType=audio" + (f"&regionCode={quote(region)}" if region else "")}


def format_ebird(result):
    """ผลเช็ก eBird -> ข้อความสั้นใต้ภาพ spectrogram"""
    if not result.get("known"):
        return f"eBird: {result.get('scientific', '')} is not in the eBird taxonomy (check the name)"
    parts = [f"eBird: {result['common']}"]
    region = result.get("region_name") or result.get("region")
    if result.get("in_region") is True:
        parts.append(f"reported in {region} ✓")
    elif result.get("in_region") is False:
        parts.append(f"⚠ never reported in {region} on eBird — check carefully")
    if result.get("recent"):
        recent = result["recent"]
        parts.append(f"seen within {ebird.NEARBY_KM} km in the last {ebird.RECENT_DAYS} days "
                     f"(latest {recent['date']}{', ' + recent['where'] if recent['where'] else ''})")
    elif result.get("recent_checked"):
        parts.append(f"no report within {ebird.NEARBY_KM} km in the last {ebird.RECENT_DAYS} days")
    elif result.get("region"):
        parts.append("recent sightings not checked (recording older than 30 days)")
    if result.get("hotspot"):
        parts.append(f"nearest hotspot: {result['hotspot']['name']} ({result['hotspot']['km']} km)")
    return "  ·  ".join(parts)


def format_second_opinion(result):
    """ผลความเห็นที่สอง -> ข้อความสั้นใต้ภาพ spectrogram"""
    ranking = [r for r in result.get("ranking", []) if r.get("similarity") is not None]
    if not ranking:
        return "Second opinion: no usable xeno-canto references for these species"
    text = ("Second opinion (similarity to nearby xeno-canto recordings): " +
            "  ·  ".join(f"{r['common']} {r['similarity']:.2f}" for r in ranking))
    current = next((r for r in ranking if r["scientific"] == result.get("current")), None)
    top = ranking[0]
    if current is None:
        text += "\nNo usable references for the current species"
    elif top["scientific"] != current["scientific"]:
        text += (f"\n⚠ Closer to {top['common']} than to {current['common']} "
                 "— listen with Reference sounds… before approving")
    elif len(ranking) > 1:
        text += f"   ✓ agrees with {current['common']}"
    missing = [r["common"] for r in result.get("ranking", []) if r.get("similarity") is None]
    if missing:
        text += f"   (no references: {', '.join(missing)})"
    return text


def xeno_canto_url(scientific):
    """'Phylloscopus inornatus [ssp]' -> xeno-canto species page (None ถ้าไม่ใช่ชื่อสองคำ)"""
    parts = str(scientific or "").split()
    if len(parts) < 2 or not (parts[0].isalpha() and parts[1].isalpha()):
        return None
    return "https://xeno-canto.org/species/" + quote(f"{parts[0].capitalize()}-{parts[1].lower()}")

DEF_AUDIO = ""
DEF_OUT   = str(Path.home() / "BirdNET_eBird")
COORD_HINT = "e.g. 13.81195,100.55317"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("BirdNET → eBird clipper")
        root.geometry("960x780")
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

        # ---- แถบปุ่มเลือกไฟล์ (บน) / แถบสถานะงาน (ล่าง เห็นตลอด) / แท็บ+รายละเอียด (กลาง ลากเส้นแบ่งได้) ----
        toolbar = ttk.Frame(outer)
        toolbar.pack(side="top", fill="x", pady=(0, 8))
        run_row = ttk.Frame(outer)
        run_row.pack(side="bottom", fill="x", pady=(6, 0))
        self.paned = ttk.Panedwindow(outer, orient="vertical")
        self.paned.pack(fill="both", expand=True)
        self.nb = ttk.Notebook(self.paned)
        self.paned.add(self.nb, weight=3)
        analyze = ttk.Frame(self.nb, padding=8)
        merge = ttk.Frame(self.nb, padding=8)
        review = ttk.Frame(self.nb, padding=8)
        self.nb.add(analyze, text="Settings")
        self.nb.add(merge, text="Merge clips")
        self.nb.add(review, text="Review clips")
        self._build_analyze(analyze)
        self._build_merge(merge)
        self._build_review(review)
        self._build_toolbar(toolbar)
        self.review_tab = review
        self.nb.select(analyze)

        # ---- แถบสถานะงาน: รายละเอียด / progress / สถานะ / Stop / Jobs ----
        run_row.columnconfigure(1, weight=1)
        # รายละเอียดถูกพับไว้ก่อน เพื่อให้หน้าเริ่มต้นอ่านง่าย
        self.log_visible = False
        self.log_toggle = ttk.Button(run_row, text="Show details ▴", command=self._toggle_log)
        self.log_toggle.grid(row=0, column=0, padx=(0, 10))
        self.progress = ttk.Progressbar(run_row, mode="indeterminate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.status = tk.StringVar(value="Ready")
        ttk.Label(run_row, textvariable=self.status).grid(row=0, column=2, sticky="e")
        # งานที่รันอยู่ + คิว: หยุดได้ (ไฟล์ที่เสร็จแล้วยังอยู่) ดูรายการได้ที่ Jobs…
        self.stop_btn = ttk.Button(run_row, text="■ Stop", command=self.stop_job, state="disabled")
        self.stop_btn.grid(row=0, column=3, padx=(10, 0))
        self.jobs_btn = ttk.Button(run_row, text="Jobs…", command=self.show_jobs)
        self.jobs_btn.grid(row=0, column=4, padx=(6, 0))
        self.job_queue = []                   # งานที่รอ (dict: title, cmd, button, is_analyze, added)
        self.current_job = None               # งานที่กำลังรัน
        self._jobs_window = None

        logf = ttk.Frame(self.paned)          # ใส่ใน paned เมื่อกด Show details
        self.log_frame = logf
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(0, weight=1)
        self.log = tk.Text(logf, height=6, wrap="none", bg="#111", fg="#ddd",
                           insertbackground="#ddd", font=("Menlo" if sys.platform == "darwin" else "Consolas", 10))
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log["yscrollcommand"] = sb.set

        # Ctrl+C/V/X/A + คลิกขวา ให้ทำงานทุก keyboard layout (เช่น ไทย)
        self._install_clipboard_fix()
        # โหลด pandas/numpy ล่วงหน้าตอนผู้ใช้ยังไม่กดอะไร -> กดเลือกไฟล์ครั้งแรกไม่ต้องรอ import
        root.after(500, lambda: threading.Thread(target=self._preload_pipeline, daemon=True).start())
        # ผลที่วิเคราะห์ไว้แล้ว: ใส่ในเมนู Results และเปิดชุดล่าสุดที่ดูค้างไว้ (ไม่ต้องวิเคราะห์ใหม่)
        root.after(300, lambda: self.refresh_results(auto_load=True))

    @staticmethod
    def _preload_pipeline():
        try:
            import field_audio_to_ebird  # noqa: F401
        except Exception:  # noqa: BLE001  ถ้าพังจะแจ้งตอนใช้งานจริง
            pass

    def _on_close(self):
        if self.job_running:
            waiting = (f"\n{len(self.job_queue)} queued job(s) will not run." if self.job_queue else "")
            if not messagebox.askokcancel("Stop the current job?",
                                          "Closing now stops the analysis.\n"
                                          "Finished files are saved; the next run continues with the remaining files."
                                          + waiting):
                return
            self.job_queue.clear()
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
                request_stop(proc)   # CLI เปลี่ยนเป็น KeyboardInterrupt แล้ว rollback เอง
                self._stop_sent = True
            self.root.after(100, self._stop_job_then_close)
            return
        if proc is not None and proc.poll() is None:
            proc.kill()
        self._close_now()

    def _close_now(self):
        self.review_stop()
        self.root.destroy()

    def _build_toolbar(self, frm):
        style = ttk.Style(self.root)
        style.configure("Quick.TButton", font=("Helvetica", 14, "bold"), padding=(16, 10))
        style.configure("QuickAction.TButton", font=("Helvetica", 12), padding=(12, 10))

        buttons = ttk.Frame(frm)
        buttons.pack(fill="x")
        self.quick_file_btn = ttk.Button(buttons, text="Choose audio file",
                                         style="Quick.TButton", command=lambda: self._quick_choose(False))
        self.quick_file_btn.pack(side="left", padx=(0, 8))
        self.quick_folder_btn = ttk.Button(buttons, text="Choose audio folder",
                                           style="Quick.TButton", command=lambda: self._quick_choose(True))
        self.quick_folder_btn.pack(side="left", padx=(0, 8))
        self.quick_start_btn = ttk.Button(buttons, text="Analyze again",
                                          style="QuickAction.TButton", state="disabled",
                                          command=self._quick_start_selected)
        self.quick_start_btn.pack(side="left")
        ttk.Button(buttons, text="Open output folder", style="QuickAction.TButton",
                   command=self._open_output_folder).pack(side="right")

        info = ttk.Frame(frm)
        info.pack(fill="x", pady=(6, 0))
        info.columnconfigure(0, weight=1)
        self.quick_selected = tk.StringVar(value="No audio selected yet")
        ttk.Label(info, textvariable=self.quick_selected, foreground="#555",
                  wraplength=600, justify="left").grid(row=0, column=0, sticky="w")
        self.quick_model_hint = tk.StringVar()
        self._update_quick_model_hint()
        ttk.Label(info, textvariable=self.quick_model_hint, foreground="#a26316",
                  wraplength=330, justify="right").grid(row=0, column=1, sticky="e")
        self.quick_metadata = tk.StringVar(value="")
        ttk.Label(info, textvariable=self.quick_metadata,
                  wraplength=900, justify="left").grid(row=1, column=0, columnspan=2, sticky="w")

    def _toggle_log(self):
        if self.log_visible:
            self.paned.forget(self.log_frame)
            self.log_toggle.configure(text="Show details ▴")
        else:
            self.paned.add(self.log_frame, weight=1)
            self.log_toggle.configure(text="Hide details ▾")
            # เริ่มที่ราว 40% ของพื้นที่ ลากเส้นแบ่งปรับเองได้
            self.root.update_idletasks()
            self.paned.sashpos(0, int(self.paned.winfo_height() * 0.6))
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
            open_path(path)
        except Exception as exc:
            messagebox.showerror("Cannot open folder", str(exc))

    def _quick_choose(self, folder):
        if self.quick_busy:              # งานวิเคราะห์ที่รันอยู่ไม่บล็อก: งานใหม่ต่อคิว
            return
        path = self.browse_folder() if folder else self.browse_file()
        if not path:
            return
        self.date.set("")
        self.coords.set("")
        self.start_time.set("")
        self.quick_selected.set(path)
        self.quick_metadata.set("")
        self.quick_start_btn.configure(state="normal")
        self._quick_start_selected()

    def _quick_start_selected(self):
        if self.quick_busy:              # งานวิเคราะห์ที่รันอยู่ไม่บล็อก: งานใหม่ต่อคิว
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
            from field_audio_to_ebird import collect_inputs, recording_context_preview
            files = collect_inputs(path)
            if not files:
                raise ValueError("No audio files found at the selected location")
            missing_date = []
            missing_coords = []
            contexts = []
            for audio_file in files:
                context = recording_context_preview(audio_file)
                contexts.append((audio_file, context))
                if context["datetime"] is None:
                    missing_date.append(audio_file)
                if context["lat"] is None or context["lon"] is None:
                    missing_coords.append(audio_file)
            self.quick_q.put(("ready", files, contexts, missing_date, missing_coords))
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
        _, self.quick_files, contexts, missing_date, missing_coords = item
        if len(contexts) == 1:
            _, context = contexts[0]
            dt = context["datetime"]
            when = dt.strftime("%Y-%m-%d %H:%M") if dt and context["has_time"] else (
                dt.strftime("%Y-%m-%d (time missing)") if dt else "date/time missing")
            where = (f"{context['lat']:.6f}, {context['lon']:.6f}"
                     if context["lat"] is not None and context["lon"] is not None
                     else "location missing")
            self.quick_metadata.set(f"Detected: {when}  •  {where}")
        else:
            self.quick_metadata.set(f"Detected details for {len(contexts)} files; review each file before analysis")
        self.status.set("Review recording details")
        self._quick_details(contexts, missing_date, missing_coords)

    def _quick_details(self, contexts, missing_date, missing_coords):
        dialog = tk.Toplevel(self.root)
        dialog.title("Recording details")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Review recording details",
                  font=("Helvetica", 17, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(body, text="Details found in the audio file, its filename or the recorder's file times are used automatically. "
                  "Enter only what is missing.", wraplength=690).pack(anchor="w", pady=(0, 12))

        def date_text(context):
            dt = context["datetime"]
            if not dt:
                return "Missing"
            value = dt.strftime("%Y-%m-%d %H:%M") if context["has_time"] else dt.strftime("%Y-%m-%d; time missing")
            return f"{value} ({context['datetime_source']})"

        def coords_text(context):
            if context["lat"] is None or context["lon"] is None:
                return "Missing"
            return f"{context['lat']:.6f}, {context['lon']:.6f} (embedded metadata)"

        if len(contexts) == 1:
            path, context = contexts[0]
            ttk.Label(body, text=path.name, font=("Helvetica", 13, "bold"),
                      wraplength=690).pack(anchor="w", pady=(0, 7))
            ttk.Label(body, text="Date/time: " + date_text(context),
                      wraplength=690).pack(anchor="w", pady=2)
            ttk.Label(body, text="Location: " + coords_text(context),
                      wraplength=690).pack(anchor="w", pady=2)
            if context["place"]:
                ttk.Label(body, text="Place: " + str(context["place"]),
                          wraplength=690).pack(anchor="w", pady=2)
        else:
            columns = ("file", "date", "location")
            table_frame = ttk.Frame(body)
            table_frame.pack(fill="x", pady=(0, 12))
            table = ttk.Treeview(table_frame, columns=columns, show="headings",
                                 height=min(8, len(contexts)))
            for key, label, width in (("file", "File", 190),
                                      ("date", "Date/time and source", 285),
                                      ("location", "Location", 250)):
                table.heading(key, text=label)
                table.column(key, width=width, stretch=False)
            table.pack(side="left")
            bar = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
            bar.pack(side="right", fill="y")
            table.configure(yscrollcommand=bar.set)
            for path, context in contexts:
                table.insert("", "end", values=(path.name, date_text(context), coords_text(context)))

        first_entry = None
        if missing_date:
            ttk.Label(body, text=f"Recording date for {len(missing_date)} file(s) without one ({DATE_HINT}) *").pack(anchor="w")
            entry = ttk.Entry(body, textvariable=self.date, width=30)
            entry.pack(fill="x", pady=(3, 10))
            first_entry = entry
        if missing_coords:
            ttk.Label(body, text=f"Location for {len(missing_coords)} file(s) without geotag (latitude,longitude) *").pack(anchor="w")
            entry = ttk.Entry(body, textvariable=self.coords, width=42)
            entry.pack(fill="x", pady=(3, 4))
            ttk.Label(body, text="e.g. 13.81195,100.55317, or paste a Google Maps link",
                      foreground="#555").pack(anchor="w", pady=(0, 10))
            if first_entry is None:
                first_entry = entry
        if len(contexts) == 1 and not contexts[0][1]["has_time"]:
            ttk.Label(body, text=f"Recording start time, if known ({TIME_HINT})").pack(anchor="w")
            ttk.Entry(body, textvariable=self.start_time, width=20).pack(anchor="w", pady=(3, 4))
            ttk.Label(body, text="Enter it if known so the clip clock times are correct.",
                      foreground="#555").pack(anchor="w", pady=(0, 9))
        elif len(contexts) > 1 and any(not context["has_time"] for _, context in contexts):
            ttk.Label(body, text="Some files have no start time. Analyze them individually if exact clock times matter.",
                      foreground="#8a5100", wraplength=690).pack(anchor="w", pady=(0, 9))
        if len(contexts) > 1 and (missing_date or missing_coords):
            ttk.Label(body, text="Values entered below fill missing details only. Embedded details in other files stay in use.",
                      foreground="#8a5100", wraplength=690).pack(anchor="w", pady=(0, 12))

        ttk.Label(body, text="Site habitat (optional; tick all that apply)").pack(anchor="w", pady=(4, 0))
        self._habitat_checkboxes(body).pack(anchor="w", pady=(3, 2))
        ttk.Label(body, text="Water birds then need a matching water habitat, or a score of at least "
                  f"{MISMATCH_MIN_CONF} in case they flew over. Leave all unticked to skip this check.",
                  foreground="#555", wraplength=690).pack(anchor="w", pady=(0, 10))

        def submit(_event=None):
            from field_audio_to_ebird import parse_coords
            if missing_date:
                value = self.date.get().strip()
                try:
                    parse_date_entry(value)
                except ValueError:
                    messagebox.showwarning("Invalid date", f"Enter the date as {DATE_HINT}", parent=dialog)
                    return
            if missing_coords and parse_coords(self.coords.get()) is None:
                messagebox.showwarning("Invalid location", "Enter coordinates, e.g. 13.81195,100.55317", parent=dialog)
                return
            if len(self.quick_files) > 1 and (missing_date or missing_coords):
                count = len(set(missing_date) | set(missing_coords))
                if not messagebox.askokcancel("Use for all files?",
                                              f"Fill the missing details for {count} file(s)? Existing metadata stays in use.",
                                              parent=dialog):
                    return
            dialog.destroy()
            self.run(confirmed_batch=True, button=self.quick_start_btn, fill_missing=True)

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

    def _habitat_checkboxes(self, parent, label=None):
        frame = ttk.Frame(parent)
        column = 0
        if label:
            ttk.Label(frame, text=label).grid(row=0, column=0, sticky="w", padx=(0, 8))
            column = 1
        for key, text in SITE_HABITATS.items():
            ttk.Checkbutton(frame, text=text, variable=self.habitat_vars[key]).grid(
                row=0, column=column, sticky="w", padx=(0, 10))
            column += 1
        return frame

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
        ttk.Label(grid, text="Date if no metadata (YYYYMMDD)").grid(row=1, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.date, width=14).grid(row=1, column=1, sticky="w", padx=2)
        ttk.Label(grid, text="Start time (HHMM)").grid(row=1, column=2, sticky="w")
        ttk.Entry(grid, textvariable=self.start_time, width=12).grid(row=1, column=3, padx=2)
        r += 1

        grid2 = ttk.Frame(frm); grid2.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(grid2, text="Place").grid(row=0, column=0, sticky="w")
        ttk.Entry(grid2, textvariable=self.place, width=34).grid(row=0, column=1, padx=(2, 14))
        ttk.Label(grid2, text="min_conf").grid(row=0, column=2, sticky="w")
        self.min_conf = tk.DoubleVar(value=0.5)
        ttk.Spinbox(grid2, from_=0.0, to=1.0, increment=0.05, width=6,
                    textvariable=self.min_conf).grid(row=0, column=3, padx=2)
        ttk.Label(grid2, text="BirdNET model").grid(row=1, column=0, sticky="w")
        self.model = tk.StringVar(value="3.0-preview")
        model_box = ttk.Combobox(grid2, textvariable=self.model,
                                 values=("3.0-preview", "2.4"),
                                 state="readonly", width=13)
        model_box.grid(row=1, column=1, sticky="w", padx=(2, 14))
        model_box.bind("<<ComboboxSelected>>", self._on_model_changed)
        ttk.Label(grid2, text="3.0 preview now checks location and date; verify every ID by ear.",
                  foreground="#a26316", wraplength=310,
                  justify="left").grid(row=1, column=2, columnspan=2, sticky="w")
        r += 1

        # ถิ่นอาศัยของจุดบันทึก (เลือกได้หลายแบบ เช่น ป่า+บึง) ใช้ร่วมกับหน้าต่าง Recording details
        self.habitat_vars = {key: tk.BooleanVar(value=False) for key in SITE_HABITATS}
        self._habitat_checkboxes(frm, label="Site habitat").grid(
            row=r, column=0, columnspan=3, sticky="w", **pad)
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
        self.overlap = tk.DoubleVar(value=1.5)
        self.keep_out_of_range = tk.BooleanVar(value=False)   # ปกติตัดชนิดนอกพื้นที่/ฤดูทิ้ง
        self.out_of_range_min_conf = tk.DoubleVar(value=0.7)
        ttk.Label(grid3, text="analysis overlap (s)").grid(row=1, column=0, sticky="w")
        ttk.Spinbox(grid3, from_=0, to=2.5, increment=0.5, width=7,
                    textvariable=self.overlap).grid(row=1, column=1, padx=(2, 14))
        ttk.Checkbutton(grid3, text="also keep out-of-area ≥", variable=self.keep_out_of_range).grid(
            row=1, column=2, sticky="w")
        ttk.Spinbox(grid3, from_=0, to=1, increment=0.05, width=7,
                    textvariable=self.out_of_range_min_conf).grid(row=1, column=3, padx=(2, 14))
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
        r += 1

        # key ของผู้ใช้เอง (ใช้กับ Reference sounds ในแท็บ Review) เก็บในเครื่อง อ่านได้เฉพาะบัญชีนี้
        ttk.Separator(frm).grid(row=r, column=0, columnspan=3, sticky="ew", pady=(10, 6))
        r += 1
        keyf = ttk.Frame(frm); keyf.grid(row=r, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(keyf, text="xeno-canto API key").pack(side="left")
        self.xc_key = tk.StringVar(value=load_settings().get("xeno_canto_key", ""))
        ttk.Entry(keyf, textvariable=self.xc_key, width=44, show="•").pack(side="left", padx=6)
        ttk.Button(keyf, text="Save key", command=self._save_xc_key).pack(side="left")
        ttk.Label(keyf, text="  Needed only for Reference sounds; get it at xeno-canto.org/account",
                  foreground="#888").pack(side="left")
        r += 1
        ebf = ttk.Frame(frm); ebf.grid(row=r, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(ebf, text="eBird API key").pack(side="left")
        self.ebird_key = tk.StringVar(value=load_settings().get("ebird_key", ""))
        ttk.Entry(ebf, textvariable=self.ebird_key, width=44, show="•").pack(side="left", padx=(31, 6))
        ttk.Button(ebf, text="Save key", command=self._save_ebird_key).pack(side="left")
        ttk.Label(ebf, text="  For the eBird check in Review clips; get it at ebird.org/api/keygen",
                  foreground="#888").pack(side="left")

    def _on_model_changed(self, _event=None):
        # Update the suggested threshold only while it still equals the last
        # model's default; keep a threshold the user entered themselves.
        if self.model.get() == "2.4" and self.min_conf.get() == 0.5:
            self.min_conf.set(0.25)
        elif self.model.get() == "3.0-preview" and self.min_conf.get() == 0.25:
            self.min_conf.set(0.5)
        if hasattr(self, "quick_model_hint"):
            self._update_quick_model_hint()

    def _update_quick_model_hint(self):
        name = "BirdNET 3.0 preview" if self.model.get() == "3.0-preview" else "BirdNET 2.4"
        self.quick_model_hint.set(f"Using {name}. Listen to each candidate before approving it.")

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
        frm.rowconfigure(4, weight=1)
        ttk.Label(frm, text="Review clips", font=("Helvetica", 17, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(frm, text="Select a clip → play it → check the species → approve",
                  font=("Helvetica", 12)).grid(row=1, column=0, sticky="w", pady=(0, 8))
        top = ttk.Frame(frm)
        top.grid(row=2, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        self.review_summary = tk.StringVar()
        # ผลที่วิเคราะห์ไว้แล้วทุกชุดในโฟลเดอร์ผลลัพธ์ เลือกแล้วโหลดทันที (ไม่ต้องวิเคราะห์ใหม่)
        ttk.Label(top, text="Results").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.results_choice = tk.StringVar()
        self.results_box = ttk.Combobox(top, textvariable=self.results_choice, state="readonly")
        self.results_box.grid(row=0, column=1, sticky="ew")
        self.results_box.bind("<<ComboboxSelected>>", self._on_result_selected)
        ttk.Button(top, text="Refresh list", command=self.refresh_results).grid(row=0, column=2, padx=4)
        ttk.Button(top, text="Open other results…", command=self.review_browse).grid(row=0, column=3)
        self._results = {}                    # ข้อความในเมนู -> path ของ summary.xlsx
        self._results_q = queue.Queue()

        columns = ("time", "file", "species", "confidence", "location", "status")
        self.review_tree = ttk.Treeview(frm, columns=columns, show="headings", height=6)
        for key, label, width in (("time", "Time", 78), ("file", "Clip", 275),
                                  ("species", "BirdNET species", 190),
                                  ("confidence", "Confidence", 85),
                                  ("location", "Area/date", 85), ("status", "Status", 120)):
            self.review_tree.heading(key, text=label)
            self.review_tree.column(key, width=width, stretch=(key == "file"))
        self.review_tree.grid(row=3, column=0, sticky="ew", pady=5)
        self.review_tree.bind("<<TreeviewSelect>>", self._review_select)
        self.review_rows = {}

        preview = ttk.Frame(frm)
        preview.grid(row=4, column=0, sticky="nsew", pady=(3, 7))
        preview.columnconfigure(0, weight=1)
        self.review_context = tk.StringVar(value="Select a clip to see its sound and recording details")
        ttk.Label(preview, textvariable=self.review_context, font=("Helvetica", 11),
                  wraplength=840).grid(
            row=0, column=0, sticky="w", pady=(0, 5))
        self.review_spec_label = ttk.Label(preview, text="Spectrogram appears here", anchor="center")
        self.review_spec_label.grid(row=1, column=0, sticky="nsew")
        self.review_spec_label.bind("<Button-1>", self._review_preview_play)
        self.review_spec_image = None
        self.second_opinion = tk.StringVar()
        ttk.Label(preview, textvariable=self.second_opinion, foreground="#1f5f8b",
                  wraplength=880, justify="left").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self._second_opinion_q = queue.Queue()
        self._second_opinion_busy = False
        self.ebird_info = tk.StringVar()
        ttk.Label(preview, textvariable=self.ebird_info, foreground="#2e6b30",
                  wraplength=880, justify="left").grid(row=3, column=0, sticky="w", pady=(2, 0))
        self._ebird_q = queue.Queue()
        self._ebird_request = self._ebird_answered = 0
        self._ebird_polling = False
        self._ebird_region = None              # มณฑล/รัฐของจุดบันทึก (จากผลเช็ก eBird ล่าสุด)
        self._lookup_q = queue.Queue()

        controls = ttk.Frame(frm)
        controls.grid(row=5, column=0, sticky="w", pady=(4, 7))
        buttons = ttk.Frame(controls)
        buttons.pack(anchor="w")
        ttk.Button(buttons, text="▶ Play", style="QuickAction.TButton",
                   command=self.review_play).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="■ Stop", style="QuickAction.TButton",
                   command=self.review_stop).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Open spectrogram full size", style="QuickAction.TButton",
                   command=self.review_spectrogram).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Reference sounds…", style="QuickAction.TButton",
                   command=self.review_references).pack(side="left", padx=(0, 8))
        self.second_opinion_btn = ttk.Button(buttons, text="Second opinion", style="QuickAction.TButton",
                                             command=self.review_second_opinion)
        self.second_opinion_btn.pack(side="left")
        # หน้าของชนิดนี้ (ชื่อในช่อง Scientific name): รูป/เสียง/แผนที่ ใน eBird, Macaulay Library, xeno-canto
        lookups = ttk.Frame(controls)
        lookups.pack(anchor="w", pady=(6, 0))
        ttk.Label(lookups, text="Look up species:").pack(side="left", padx=(0, 8))
        for text, kind in (("eBird ↗", "species"), ("Photos ↗", "photo"), ("Sounds ↗", "audio")):
            ttk.Button(lookups, text=text, command=lambda kind=kind: self.review_lookup(kind)).pack(
                side="left", padx=(0, 6))
        ttk.Button(lookups, text="xeno-canto ↗", command=self.review_xeno_canto).pack(side="left")

        fields = ttk.Frame(frm)
        fields.grid(row=6, column=0, sticky="ew", pady=5)
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
        actions.grid(row=7, column=0, sticky="w", pady=(8, 4))
        ttk.Button(actions, text="✓ Approve → Ready", style="QuickAction.TButton",
                   command=lambda: self.review_save("Approved")).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Reject", style="QuickAction.TButton",
                   command=lambda: self.review_save("Rejected")).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Keep pending", style="QuickAction.TButton",
                   command=lambda: self.review_save("Pending")).pack(side="left")
        ttk.Label(frm, text="Only approved clips are copied to Ready/  •  Audio quality must be rated by a person",
                  foreground="#666").grid(row=8, column=0, sticky="w", pady=(4, 0))

    def refresh_results(self, auto_load=False):
        """ค้นผลวิเคราะห์ทุกชุดในโฟลเดอร์ผลลัพธ์ (เบื้องหลัง) แล้วใส่ในเมนู Results"""
        root = self.out.get().strip() or DEF_OUT

        def work():
            try:
                self._results_q.put((list_results(root), auto_load))
            except OSError:
                self._results_q.put(([], auto_load))
        threading.Thread(target=work, daemon=True).start()
        self.root.after(150, self._results_poll)

    def _results_poll(self):
        try:
            found, auto_load = self._results_q.get_nowait()
        except queue.Empty:
            self.root.after(150, self._results_poll)
            return
        self._results = {}
        for info in found:
            label = result_label(info)
            if label in self._results:        # ชื่อซ้ำ (นาทีเดียวกัน) -> ต่อชื่อโฟลเดอร์
                label += f" · {info['folder']}"
            self._results[label] = info["path"]
        self.results_box.configure(values=list(self._results))
        if auto_load and not self.review_summary.get():
            # เปิดแอปใหม่: กลับไปที่ผลชุดล่าสุดที่เปิดดู (หรือชุดใหม่สุด)
            last = Path(load_settings().get("last_summary") or "")
            target = last if last.is_file() else (found[0]["path"] if found else None)
            if target is not None:
                self.review_summary.set(str(target))
                self.review_load(quiet=True)
                if self.review_rows:
                    self.nb.select(self.review_tab)
        self._select_result_label()

    def _select_result_label(self):
        current = Path(self.review_summary.get()).expanduser()
        label = next((text for text, path in self._results.items() if Path(path) == current), "")
        self.results_choice.set(label or (str(current) if self.review_summary.get() else ""))

    def _on_result_selected(self, _event=None):
        path = self._results.get(self.results_choice.get())
        if path:
            self.review_summary.set(str(path))
            self.review_load()

    def review_browse(self):
        path = filedialog.askopenfilename(title="Choose summary.xlsx", filetypes=[("Excel", "*.xlsx")])
        if path:
            self.review_summary.set(path)
            self.review_load()

    def review_load(self, quiet=False):
        try:
            summary = Path(self.review_summary.get()).expanduser()
            rows = load_reviews(summary)
            try:
                save_settings({"last_summary": str(summary.resolve())})   # เปิดแอปครั้งหน้าจะกลับมาที่นี่
            except OSError:
                pass
            if summary.resolve() not in {Path(p).resolve() for p in self._results.values()}:
                self.refresh_results()            # ผลชุดใหม่ (เพิ่งวิเคราะห์เสร็จ) -> เพิ่มในเมนู
            else:
                self._select_result_label()
            self.review_rows = {str(row["_row"]): row for row in rows}
            self.review_tree.delete(*self.review_tree.get_children())
            self._clear_review_preview()
            for key, row in self.review_rows.items():
                status = row.get("Review status") or "Pending"
                ready = row.get("Ready file")
                if status == "Approved" and (not ready or
                    not (summary.parent / str(ready)).is_file()):
                    status = "Missing Ready"
                display_status = "Ready file missing" if status == "Missing Ready" else status
                self.review_tree.insert("", "end", iid=key,
                                        values=(row.get("Clock time") or "", row.get("File") or "",
                                                row.get("Species (common)") or "",
                                                row.get("Max confidence") or "",
                                                "Check" if (str(row.get("Expected by location/date") or "") not in ("", "Yes") or
                                                            str(row.get("Fits site habitat") or "").startswith("No")) else "",
                                                display_status))
        except Exception as exc:
            if not quiet:
                messagebox.showerror("Cannot open summary", str(exc))

    def _selected_review(self):
        selected = self.review_tree.selection()
        return (selected[0], self.review_rows[selected[0]]) if selected else (None, None)

    def _review_select(self, _event=None):
        _key, row = self._selected_review()
        if row is None:
            self._clear_review_preview()
            return
        self.review_common.set(row.get("Reviewed common") or row.get("Species (common)") or "")
        self.review_scientific.set(row.get("Reviewed scientific") or row.get("Species (scientific)") or "")
        rating = row.get("Quality rating")   # summary เก่าอาจเก็บเป็น 3.0
        self.review_rating.set(str(int(rating)) if isinstance(rating, (int, float)) and rating == rating else "")
        self.review_notes.set(row.get("Review notes") or "")
        self._show_review_preview(row)
        if not self._second_opinion_busy:     # ผลที่เคยคำนวณไว้ (เก็บตาม sha256 ของคลิป)
            cached = second_opinion.cached_result(row.get("Generated sha256"))
            self.second_opinion.set(format_second_opinion(cached) if cached else "")
        self._start_ebird_check(row)

    def _start_ebird_check(self, row):
        """เช็กชนิดนี้กับ eBird รอบจุดบันทึก (เบื้องหลัง, มี cache) + เติมชื่อตาม eBird ถ้ายังไม่ได้ตรวจ"""
        self._ebird_request += 1
        request = self._ebird_request
        scientific = self.review_scientific.get().strip()
        common = self.review_common.get().strip()
        if xeno_canto_url(scientific) is None:
            self._ebird_answered = request
            self.ebird_info.set("")
            return
        lat, lon = _number(row.get("Latitude")), _number(row.get("Longitude"))
        try:
            recorded = datetime.fromisoformat(json.loads(row.get("Analysis settings") or "{}").get("date"))
        except (TypeError, ValueError):
            recorded = None
        key = self.ebird_key.get().strip()
        self._ebird_region = None
        self.ebird_info.set("eBird: checking …" if key else "")
        prefill = not row.get("Reviewed common")

        def work():
            try:
                result = ebird.check(scientific, lat, lon, recorded, key, common)
                self._ebird_q.put((request, result, None, prefill, common))
            except ebird.EBirdError as exc:
                self._ebird_q.put((request, None, str(exc), prefill, common))
        threading.Thread(target=work, daemon=True).start()
        if not self._ebird_polling:
            self._ebird_polling = True
            self.root.after(150, self._ebird_poll)

    def _ebird_poll(self):
        """ตัวรับผลตัวเดียว: แสดงเฉพาะผลของคลิปล่าสุด แล้วหยุดเมื่อได้ผลนั้น"""
        try:
            while True:
                request, result, error, prefill, common = self._ebird_q.get_nowait()
                if request == self._ebird_request:
                    self._ebird_answered = request
                    self._show_ebird(result, error, prefill, common)
        except queue.Empty:
            pass
        if self._ebird_answered != self._ebird_request:
            self.root.after(150, self._ebird_poll)
        else:
            self._ebird_polling = False

    def _show_ebird(self, result, error, prefill, common):
        if result:
            self._ebird_region = result.get("region")
        if error:
            self.ebird_info.set(f"eBird: {error}" if self.ebird_key.get().strip() else "")
            return
        # ชื่อตามระบบ eBird (เติมเฉพาะเมื่อยังไม่เคยตรวจ และผู้ใช้ยังไม่ได้แก้ช่องนี้)
        if prefill and result.get("known") and self.review_common.get().strip() == common:
            self.review_common.set(result["common"])
        self.ebird_info.set(format_ebird(result) if self.ebird_key.get().strip() else "")

    def _clear_review_preview(self):
        self.review_spec_image = None
        self.review_context.set("Select a clip to see its sound and recording details")
        self.review_spec_label.configure(image="", text="Spectrogram appears here")
        if not self._second_opinion_busy:
            self.second_opinion.set("")
        self._ebird_request += 1                  # ทิ้งผลเช็ก eBird ที่ยังค้างอยู่
        self._ebird_answered = self._ebird_request
        self.ebird_info.set("")

    def _show_review_preview(self, row):
        parts = []
        if row.get("Source file"):
            parts.append(str(row["Source file"]))
        if row.get("Clock time"):
            parts.append(str(row["Clock time"]))
        if row.get("Start offset (s)") is not None:
            parts.append(f"{row['Start offset (s)']}s into recording")
        if row.get("Duration (s)") is not None:
            parts.append(f"{row['Duration (s)']}s clip")
        area_status = str(row.get("Expected by location/date") or "")
        if area_status.startswith("No"):
            parts.append("Outside expected species list — verify by ear")
        elif area_status.startswith("Not checked"):
            parts.append("Location/date not checked — verify by ear")
        if str(row.get("Fits site habitat") or "").startswith("No"):
            parts.append(f"{row.get('Species habitat') or 'Water'} bird at this site habitat — "
                         "may be a flyover; verify by ear")
        self.review_context.set("  •  ".join(parts) or "Selected clip")
        self.review_spec_image = None
        try:
            path = self._review_audio_path().with_suffix(".png")
            if not path.is_file():
                self.review_spec_label.configure(image="", text="No spectrogram for this clip")
                return
            with Image.open(path) as source:
                source.thumbnail((840, 205), Image.Resampling.LANCZOS)
                self.review_spec_image = ImageTk.PhotoImage(source.copy(), master=self.root)
            self.review_spec_label.configure(image=self.review_spec_image, text="")
        except Exception as exc:
            self.review_spec_label.configure(image="", text=f"Cannot show spectrogram: {exc}")

    def _review_preview_play(self, _event=None):
        if self._selected_review()[0] is not None:
            self.review_play()

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
            self.audio_player = play_audio(path)
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
            open_path(path)
        except Exception as exc:
            messagebox.showerror("Cannot open spectrogram", str(exc))

    def review_xeno_canto(self):
        _key, row = self._selected_review()
        if row is None:
            messagebox.showwarning("No clip", "Select a clip first")
            return
        # ใช้ชื่อในช่อง Scientific name (ถ้าผู้ตรวจแก้ชนิด จะเทียบกับชนิดที่แก้)
        scientific = self.review_scientific.get().strip() or str(row.get("Species (scientific)") or "")
        url = xeno_canto_url(scientific)
        if url is None:
            messagebox.showinfo("No scientific name",
                                "Enter the scientific name first, e.g. Phylloscopus inornatus")
            return
        key = self.xc_key.get().strip()
        if not key:
            webbrowser.open(url)
            return
        common = self.review_common.get().strip() or str(row.get("Species (common)") or "")

        def work():   # xeno-canto อาจใช้สกุลอื่น (เช่น Pardaliparus): ถามหน้าที่ถูกจาก API ก่อน
            try:
                page = xeno_canto.species_page(scientific, key, common) or url
            except xeno_canto.XenoCantoError:
                page = url
            webbrowser.open(page)
        threading.Thread(target=work, daemon=True).start()

    def review_second_opinion(self):
        key, row = self._selected_review()
        if row is None:
            messagebox.showwarning("No clip", "Select a clip first")
            return
        if self._second_opinion_busy:
            return
        if not self.xc_key.get().strip():
            messagebox.showinfo("xeno-canto API key needed",
                                "Second opinion compares the clip with xeno-canto recordings. "
                                "Paste your own API key (free, from xeno-canto.org/account) in the Settings tab.")
            return
        if self.xc_key.get().strip() != load_settings().get("xeno_canto_key", ""):
            self._save_xc_key()               # subprocess อ่าน key จากไฟล์ตั้งค่า ไม่ส่งผ่าน command line
        self._second_opinion_busy = True
        self.second_opinion_btn.configure(state="disabled")
        self.second_opinion.set("Second opinion: starting … (first run for a species downloads references)")
        cmd = self._launch() + ["--second-opinion", str(Path(self.review_summary.get()).expanduser()), str(key)]

        def work():
            try:
                proc = platform_tools.start_child(cmd)
                for line in proc.stdout:
                    self._second_opinion_q.put(line.rstrip())
                proc.wait()
            except OSError as exc:
                self._second_opinion_q.put(f"ERROR {exc}")
            self._second_opinion_q.put(None)
        threading.Thread(target=work, daemon=True).start()
        self.root.after(200, self._second_opinion_poll)

    def _second_opinion_poll(self):
        try:
            while True:
                line = self._second_opinion_q.get_nowait()
                if line is None:
                    self._second_opinion_busy = False
                    self.second_opinion_btn.configure(state="normal")
                    return
                if line.startswith("RESULT "):
                    result = json.loads(line[7:])
                    _key, row = self._selected_review()
                    # ผู้ใช้อาจเลือกคลิปอื่นระหว่างรอ: แสดงเฉพาะเมื่อยังเป็นคลิปเดิม (ผลถูกเก็บไว้แล้ว)
                    same = row is not None and str(row.get("Generated sha256") or result["clip_sha"]) == result["clip_sha"]
                    self.second_opinion.set(format_second_opinion(result) if same else "")
                elif line.startswith("ERROR "):
                    self.second_opinion.set("Second opinion failed: " + line[6:])
                elif line and not line.startswith(("INFO", "WARNING")) and "Model loaded" not in line:
                    self.second_opinion.set("Second opinion: " + line.strip())
        except queue.Empty:
            pass
        self.root.after(200, self._second_opinion_poll)

    def review_lookup(self, kind):
        """เปิดหน้าชนิดนี้: eBird (รูป เสียง แผนที่) หรือ Macaulay Library เฉพาะรูป/เสียง"""
        _key, row = self._selected_review()
        if row is None:
            messagebox.showwarning("No clip", "Select a clip first")
            return
        scientific = self.review_scientific.get().strip() or str(row.get("Species (scientific)") or "")
        common = self.review_common.get().strip() or str(row.get("Species (common)") or "")
        if xeno_canto_url(scientific) is None:
            messagebox.showinfo("No scientific name",
                                "Enter the scientific name first, e.g. Phylloscopus inornatus")
            return
        region = self._ebird_region
        self.status.set("Looking up the eBird species …")

        def work():   # ครั้งแรกต้องโหลดระบบชื่อ eBird (~15 วินาที) หลังจากนั้นเก็บไว้ในเครื่อง
            try:
                entry = ebird.species(scientific, common)
            except ebird.EBirdError as exc:
                self._lookup_q.put(("lookup-error", str(exc)))
                return
            if entry is None:
                self._lookup_q.put(("lookup-error", f"{scientific} is not in the eBird taxonomy"))
                return
            webbrowser.open(species_links(entry["code"], region)[kind])
            self._lookup_q.put(("lookup-done", entry["common"]))
        threading.Thread(target=work, daemon=True).start()
        self.root.after(200, self._lookup_poll)

    def _lookup_poll(self):
        try:
            kind, text = self._lookup_q.get_nowait()
        except queue.Empty:
            self.root.after(200, self._lookup_poll)
            return
        if kind == "lookup-error":
            self.status.set("")
            messagebox.showinfo("eBird lookup", text)
        else:
            self.status.set(f"Opened {text} in your browser")

    def _save_ebird_key(self):
        try:
            save_settings({"ebird_key": self.ebird_key.get().strip()})
            self.status.set("eBird key saved")
        except OSError as exc:
            messagebox.showerror("Cannot save key", str(exc))

    def _save_xc_key(self):
        try:
            save_settings({"xeno_canto_key": self.xc_key.get().strip()})
            self.status.set("xeno-canto key saved")
        except OSError as exc:
            messagebox.showerror("Cannot save key", str(exc))

    def review_references(self):
        """หน้าต่างเสียงตัวอย่างจาก xeno-canto ของชนิดในช่อง Scientific name: ฟังเทียบในแอป"""
        _key, row = self._selected_review()
        if row is None:
            messagebox.showwarning("No clip", "Select a clip first")
            return
        scientific = (self.review_scientific.get().strip() or str(row.get("Species (scientific)") or "")).strip()
        if xeno_canto_url(scientific) is None:
            messagebox.showinfo("No scientific name",
                                "Enter the scientific name first, e.g. Phylloscopus inornatus")
            return
        if not self.xc_key.get().strip():
            if messagebox.askyesno("xeno-canto API key needed",
                                   "Reference sounds need your own xeno-canto API key (free, from "
                                   "xeno-canto.org/account). Paste it in the Settings tab.\n\n"
                                   "Open the species page in your browser instead?"):
                webbrowser.open(xeno_canto_url(scientific))
            return

        def number(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        lat, lon = number(row.get("Latitude")), number(row.get("Longitude"))
        common = self.review_common.get().strip() or str(row.get("Species (common)") or "")
        ReferenceWindow(self, scientific, common, lat if lat == lat else None, lon if lon == lon else None)

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
            self._review_select()
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

    def _start(self, cmd, btn, title, is_analyze=True):
        """เริ่มงาน หรือต่อคิวถ้ามีงานรันอยู่"""
        if not SCRIPT.exists():
            messagebox.showerror("Script not found", str(SCRIPT))
            return
        job = {"title": title, "cmd": cmd, "button": btn, "is_analyze": is_analyze,
               "added": datetime.now()}
        if self.job_running:
            self.job_queue.append(job)
            self.status.set(f"Queued: {title} ({len(self.job_queue)} waiting)")
            self._update_job_controls()
            return
        self._launch_job(job)

    def _launch_job(self, job):
        self.log.delete("1.0", "end")
        self._log("command: " + " ".join(f'"{a}"' if " " in a else a for a in job["cmd"]))
        self._active_btn = job["button"]
        self._active_is_analyze = job["is_analyze"]
        self._clip_count = None
        self._generated_summaries = []
        self._already_done = False
        job.update(started=time.monotonic(), total=None, index=0, file="", stopping=False)
        self.current_job = job
        self.job_running = True
        self.progress.start(12)
        self._update_job_controls()
        threading.Thread(target=self._worker, args=(job["cmd"],), daemon=True).start()
        self.root.after(100, self._poll)

    def _job_progress_text(self):
        job = self.current_job
        if job is None:
            return ""
        elapsed = int(time.monotonic() - job["started"])
        text = ("Analyzing " if job["is_analyze"] else "") + (job["file"] or job["title"])
        if job["total"] and job["total"] > 1:
            text += f" (file {job['index']}/{job['total']})"
        text += f"  ·  {elapsed // 60:02d}:{elapsed % 60:02d}"
        if job["stopping"]:
            text = "Stopping … " + text
        if self.job_queue:
            text += f"  ·  {len(self.job_queue)} queued"
        return text

    def _update_job_controls(self):
        self.stop_btn.configure(state="normal" if self.job_running else "disabled")
        self.jobs_btn.configure(text=f"Jobs ({len(self.job_queue)} queued)…" if self.job_queue else "Jobs…")
        if self._jobs_window is not None:
            self._jobs_window.refresh()

    def _start_next_job(self):
        if self.job_queue and not self.job_running and not self._closing:
            self._launch_job(self.job_queue.pop(0))

    def stop_job(self):
        job, proc = self.current_job, self.proc
        if job is None or proc is None or proc.poll() is not None:
            return
        if not messagebox.askokcancel("Stop this job?", f"Stop “{job['title']}”?\n"
                                      "Finished files are kept; run it again later to continue."):
            return
        job["stopping"] = True
        request_stop(proc)                # CLI เปลี่ยนเป็น KeyboardInterrupt แล้ว rollback เอง
        self.status.set(self._job_progress_text())

    def show_jobs(self):
        if self._jobs_window is not None and self._jobs_window.win.winfo_exists():
            self._jobs_window.win.lift()
            return
        self._jobs_window = JobsWindow(self)

    def run(self, confirmed_batch=False, button=None, fill_missing=False):
        audio = self.audio.get().strip().strip('"')
        if not audio:
            messagebox.showwarning("No audio selected", "Choose an audio file or folder first.")
            return
        if self.date.get().strip():
            try:
                parse_date_entry(self.date.get())
            except ValueError:
                messagebox.showwarning("Invalid date", f"Enter the date as {DATE_HINT}")
                return
        if self.start_time.get().strip():
            value = self.start_time.get().strip()
            try:
                parse_time_entry(value)
            except ValueError:
                messagebox.showwarning("Invalid time", f"Enter the recording start time as {TIME_HINT}")
                return
        same_date_for_all = False
        if self.date.get().strip() and Path(audio).is_dir() and not fill_missing:
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
               "--model", self.model.get(),
               "--min-conf", str(self.min_conf.get()),
               "--overlap", str(self.overlap.get()),
               "--occurrence-gap", str(self.gap.get()),
               "--lead", str(self.lead.get()), "--tail", str(self.tail.get()),
               "--target-dbfs", str(self.dbfs.get()),
               "--mono" if self.mono.get() else "--no-mono",
               "--spectrogram" if self.spec.get() else "--no-spectrogram",
               "--alt-species" if self.alt.get() else "--no-alt-species",
               "--unknown" if self.unknown.get() else "--no-unknown"]
        if self.keep_out_of_range.get():
            cmd += ["--out-of-range-min-conf", str(self.out_of_range_min_conf.get())]
        if fill_missing:
            cmd += ["--fill-missing-metadata"]
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
        habitats = [key for key, var in self.habitat_vars.items() if var.get()]
        if habitats:
            cmd += ["--habitat", ",".join(habitats)]
        name = Path(audio).name + (" (folder)" if Path(audio).is_dir() else "")
        self._start(cmd, button or self.run_btn, f"Analyze {name}", is_analyze=True)

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
        self._start(cmd, self.merge_btn, f"Merge {len(files)} clips → {Path(out).name}", is_analyze=False)

    def _worker(self, cmd):
        try:
            self.proc = platform_tools.start_child(cmd)   # Windows: ไม่มี console เด้ง, หยุดผ่าน stdin
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
                if isinstance(item, str) and self.current_job is not None:
                    job = self.current_job        # ความคืบหน้าจากข้อความของ CLI
                    total = re.match(r"Processing (\d+) file", item)
                    if total:
                        job["total"] = int(total.group(1))
                    current = re.match(r"\s*=== (.+?) ===", item)
                    if current:
                        job["index"] += 1
                        job["file"] = current.group(1)
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
        if self.current_job is not None:
            self.status.set(self._job_progress_text())
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
        job, self.current_job = self.current_job, None
        if self._closing:
            return
        # งานถัดไปในคิว (เว้นช่วงให้เห็นผลของงานที่เพิ่งจบ)
        if self.job_queue:
            self.root.after(1500, self._start_next_job)
        self._update_job_controls()
        if job is not None and job.get("stopping"):
            self.status.set(f"Stopped: {job['title']} — finished files are kept; run it again to continue")
            self._log("\n=== Stopped by you ===")
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


class JobsWindow:
    """งานที่กำลังรันและงานที่รอคิว: หยุดงาน / เอางานออกจากคิว"""

    def __init__(self, app):
        self.app = app
        win = self.win = tk.Toplevel(app.root)
        win.title("Jobs")
        win.geometry("640x280")
        body = ttk.Frame(win, padding=12)
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, columns=("state", "job", "time"), show="headings", height=8)
        for key, label, width in (("state", "State", 110), ("job", "Job", 380), ("time", "Time", 110)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, stretch=(key == "job"))
        self.tree.pack(fill="both", expand=True)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="■ Stop running job", command=app.stop_job).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Remove from queue", command=self.remove).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Stop all", command=self.stop_all).pack(side="left")
        ttk.Button(actions, text="Close", command=win.destroy).pack(side="right")
        self.refresh()
        self._tick()

    def refresh(self):
        if not self.win.winfo_exists():
            return
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        job = self.app.current_job
        if job is not None:
            elapsed = int(time.monotonic() - job["started"])
            state = "Stopping" if job["stopping"] else "Running"
            detail = job["title"] + (f"  —  {job['file']} ({job['index']}/{job['total']})"
                                     if job["file"] and job["total"] and job["total"] > 1 else "")
            self.tree.insert("", "end", iid="running", values=(state, detail,
                                                               f"{elapsed // 60:02d}:{elapsed % 60:02d}"))
        for number, waiting in enumerate(self.app.job_queue, start=1):
            iid = f"queued{id(waiting)}"
            self.tree.insert("", "end", iid=iid, values=(f"Queued #{number}", waiting["title"],
                                                         "added " + waiting["added"].strftime("%H:%M")))
            if iid in selected:
                self.tree.selection_add(iid)
        if job is None and not self.app.job_queue:
            self.tree.insert("", "end", iid="idle", values=("—", "No jobs running or waiting", ""))

    def _tick(self):                       # เวลาที่ผ่านไปของงานที่รันอยู่
        if self.win.winfo_exists():
            self.refresh()
            self.win.after(1000, self._tick)

    def remove(self):
        chosen = set(self.tree.selection())
        self.app.job_queue[:] = [job for job in self.app.job_queue if f"queued{id(job)}" not in chosen]
        self.app._update_job_controls()

    def stop_all(self):
        if not self.app.job_queue and self.app.current_job is None:
            return
        self.app.job_queue.clear()
        self.app._update_job_controls()
        self.app.stop_job()


class ReferenceWindow:
    """เสียงตัวอย่างจาก xeno-canto ของชนิดหนึ่ง เล่นสลับกับคลิปที่กำลังตรวจได้"""
    TYPES = ("Any", "song", "call", "flight call", "alarm call")

    def __init__(self, app, scientific, common, lat, lon):
        self.app, self.scientific, self.common, self.lat, self.lon = app, scientific, common, lat, lon
        self.recordings = {}
        self.q = queue.Queue()
        self._search_id = 0
        win = self.win = tk.Toplevel(app.root)
        win.title(f"Reference sounds — {common} ({scientific})" if common else f"Reference sounds — {scientific}")
        win.geometry("940x430")
        body = ttk.Frame(win, padding=12)
        body.pack(fill="both", expand=True)
        top = ttk.Frame(body)
        top.pack(fill="x")
        ttk.Label(top, text="Sound type").pack(side="left")
        self.sound_type = tk.StringVar(value="Any")
        chooser = ttk.Combobox(top, textvariable=self.sound_type, values=self.TYPES, state="readonly", width=12)
        chooser.pack(side="left", padx=6)
        chooser.bind("<<ComboboxSelected>>", lambda _event: self.search())
        self.state = tk.StringVar()
        ttk.Label(top, textvariable=self.state, foreground="#555").pack(side="left", padx=10)

        columns = ("id", "type", "q", "length", "distance", "where", "recordist", "license")
        self.tree = ttk.Treeview(body, columns=columns, show="headings", height=12)
        for key, label, width in (("id", "XC", 85), ("type", "Type", 140), ("q", "Q", 30),
                                  ("length", "Length", 60), ("distance", "Distance", 80),
                                  ("where", "Location", 230), ("recordist", "Recordist", 130),
                                  ("license", "License", 110)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, stretch=(key == "where"))
        self.tree.pack(fill="both", expand=True, pady=8)
        self.tree.bind("<Double-1>", lambda _event: self.play())

        actions = ttk.Frame(body)
        actions.pack(fill="x")
        ttk.Button(actions, text="▶ Play reference", command=self.play).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="▶ Play my clip", command=app.review_play).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="■ Stop", command=app.review_stop).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Open on xeno-canto ↗", command=self.open_page).pack(side="left")
        ttk.Label(body, text="Best quality and nearest first. Recordings © their recordists, "
                  "shared on xeno-canto.org under the license shown.", foreground="#888").pack(anchor="w", pady=(6, 0))
        self.search()
        self.win.after(100, self._poll)

    def search(self):
        self._search_id += 1
        search_id, sound_type = self._search_id, self.sound_type.get()
        self.state.set("Searching xeno-canto…")
        self.tree.delete(*self.tree.get_children())

        def work():
            try:
                found, total = xeno_canto.search(self.scientific, self.app.xc_key.get().strip(),
                                                 "" if sound_type == "Any" else sound_type,
                                                 self.lat, self.lon, common=self.common)
                self.q.put(("results", search_id, found, total))
            except xeno_canto.XenoCantoError as exc:
                self.q.put(("error", search_id, str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _selected(self):
        selected = self.tree.selection()
        return self.recordings.get(selected[0]) if selected else None

    def play(self):
        recording = self._selected()
        if recording is None:
            self.state.set("Select a recording first")
            return
        self.state.set(f"Downloading {recording['id']}…")

        def work():
            try:
                self.q.put(("play", None, xeno_canto.download(recording)))
            except xeno_canto.XenoCantoError as exc:
                self.q.put(("error", None, str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def open_page(self):
        recording = self._selected()
        if recording:
            webbrowser.open(recording["page"])
        else:
            self.app.review_xeno_canto()

    def _poll(self):
        if not self.win.winfo_exists():
            return
        try:
            while True:
                kind, search_id, *payload = self.q.get_nowait()
                if search_id is not None and search_id != self._search_id:
                    continue                          # ผลค้นเก่าหลังเปลี่ยนประเภทเสียง
                if kind == "results":
                    self._show(*payload)
                elif kind == "play":
                    self.app.review_stop()
                    try:
                        self.app.audio_player = play_audio(payload[0])
                    except (OSError, RuntimeError) as exc:
                        self.state.set(f"Cannot play: {exc}")
                        continue
                    self.state.set(f"Playing {payload[0].stem}")
                else:
                    self.state.set(payload[0])
        except queue.Empty:
            pass
        self.win.after(100, self._poll)

    def _show(self, found, total):
        self.recordings = {}
        for recording in found:
            self.recordings[recording["id"]] = recording
            distance = "" if recording["distance_km"] is None else f"{recording['distance_km']:,} km"
            where = ": ".join(part for part in (recording["country"], recording["location"]) if part)
            self.tree.insert("", "end", iid=recording["id"], values=(
                recording["id"], recording["type"], recording["quality"], recording["length"],
                distance, where, recording["recordist"], recording["license"]))
        self.state.set(f"Showing {len(found)} of {total:,} recordings" if found
                       else "No recordings found for this species and sound type")


def main():
    paths.migrate_old_locations()   # ข้อมูลจาก ~/Library (รุ่นก่อน) -> data/
    platform_tools.enable_high_dpi()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
