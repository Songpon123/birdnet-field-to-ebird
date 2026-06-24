#!/usr/bin/env python3
"""
birdnet_ui.py — Streamlit UI สำหรับ field_audio_to_ebird.py (เวอร์ชันตามสเปก ML/eBird)
-----------------------------------------------------------------------------------
รัน:
    C:\\Users\\songp\\birdnet-env\\Scripts\\streamlit.exe run C:\\Users\\songp\\Downloads\\birdnet_ui.py

2 แท็บ:
  ▶️ Run     — เลือก/อัปโหลดไฟล์ + กรอกพิกัด/วัน(ออปชัน)/สถานที่ + พารามิเตอร์
               แล้วเรียก field_audio_to_ebird.py ผ่าน subprocess พร้อม stream log
  🎧 Review  — เปิดผลตามโฟลเดอร์วัน (YYYY.MM.DD) ฟังเสียง + ดู mel-spectrogram
               ทีละคลิป ติ๊กเลือกแล้วคัดลอกไป _to_upload เตรียมอัป eBird
"""

import os
import sys
import shutil
import tempfile
import subprocess
from pathlib import Path

import pandas as pd
import streamlit as st

SCRIPT = Path(__file__).resolve().parent / "field_audio_to_ebird.py"
PYTHON = sys.executable
DEF_AUDIO = ""
DEF_OUT = str(Path.home() / "BirdNET_eBird")
DEF_LAT, DEF_LON = 12.80, 99.62

def native_pick(folder: bool) -> str:
    """เปิด native file/folder dialog บนเครื่อง (Streamlit server = เครื่อง local) คืน path
    ใช้แทนการอัปโหลด เพื่อให้ได้ path จริง -> อ่านวันเวลา (mtime) ของไฟล์ได้"""
    fn = "askdirectory" if folder else "askopenfilename"
    code = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
        f"print(filedialog.{fn}(title='เลือกไฟล์เสียง'))\n"
        "r.destroy()\n"
    )
    try:
        out = subprocess.run([PYTHON, "-c", code], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=300)
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


st.set_page_config(page_title="BirdNET → eBird", page_icon="🐦", layout="wide")
st.title("🐦 BirdNET → eBird clipper")

tab_run, tab_review = st.tabs(["▶️ Run", "🎧 Review & เลือกอัป"])

# ============================== RUN ==============================
with tab_run:
    if not SCRIPT.exists():
        st.error(f"หาสคริปต์ไม่เจอ: {SCRIPT}")

    st.subheader("1) ไฟล์เสียง")
    src = st.radio("แหล่งไฟล์",
                   ["เลือกไฟล์ในเครื่อง (แนะนำ — ได้วันเวลาจริงจากไฟล์)", "อัปโหลด (เวลาไฟล์หาย)"],
                   horizontal=True)
    audio_arg, is_dir = None, False
    if src.startswith("เลือก"):
        if "audio_path" not in st.session_state:
            st.session_state.audio_path = DEF_AUDIO
        bc = st.columns([5, 1, 1])
        if bc[1].button("📂 ไฟล์…"):
            p = native_pick(False)
            if p:
                st.session_state.audio_path = p
        if bc[2].button("📁 โฟลเดอร์…"):
            p = native_pick(True)
            if p:
                st.session_state.audio_path = p
        audio_arg = bc[0].text_input("path ไฟล์ หรือโฟลเดอร์ (batch)",
                                     key="audio_path").strip().strip('"')
        is_dir = bool(audio_arg) and Path(audio_arg).is_dir()
    else:
        up = st.file_uploader("อัปโหลดไฟล์เสียง", type=["wav", "mp3", "flac", "m4a", "ogg", "aif", "aiff"])
        st.warning("⚠️ การอัปโหลดทำให้ **เวลาไฟล์ (วันอัด) หาย** — เบราว์เซอร์ไม่ส่ง timestamp มา "
                   "ถ้าไฟล์ไม่มีวันในชื่อ/ใน metadata ระบบจะใช้ 'วันที่อัปโหลด' แทน "
                   "→ สำหรับไฟล์ในเครื่องแนะนำใช้ 'path ในเครื่อง' หรือกรอก 'วันที่ override'")
        if up is not None:
            tmpdir = Path(tempfile.gettempdir()) / "birdnet_uploads"
            tmpdir.mkdir(parents=True, exist_ok=True)
            dest = tmpdir / up.name
            with open(dest, "wb") as f:
                f.write(up.getbuffer())
            audio_arg = str(dest)
            st.caption(f"บันทึกชั่วคราว: {dest}")

    st.subheader("2) จุดสำรวจ")
    st.caption("เว้นว่าง = ดึงจาก metadata / ชื่อไฟล์ อัตโนมัติ · พิกัดวาง 'lat,lon' หรือลิงก์ Google Maps ทั้งอันได้")
    g = st.columns([2, 1, 1])
    coords = g[0].text_input("พิกัด — lat,lon หรือลิงก์ Google Maps (ว่าง=auto)", f"{DEF_LAT},{DEF_LON}")
    date = g[1].text_input("วันที่ override (ว่าง=auto)", "")
    place = g[2].text_input("สถานที่ (ว่าง=metadata)", "")

    st.subheader("3) พารามิเตอร์การตัด (ตามคู่มือ ML)")
    min_conf = st.slider("min_conf", 0.0, 1.0, 0.5, 0.05)
    g2 = st.columns(4)
    gap = g2[0].number_input("occurrence gap (วิ) — ห่างเกินนี้ = คนละครั้ง", value=5.0, step=0.5)
    lead = g2[1].number_input("lead ก่อนเสียงแรก (วิ)", value=3.0, step=0.5)
    tail = g2[2].number_input("tail หลังเสียงสุดท้าย (วิ)", value=3.0, step=0.5)
    dbfs = g2[3].number_input("normalize (dBFS)", value=-3.0, step=0.5)
    g3 = st.columns(5)
    mono = g3[0].checkbox("แปลง mono", True)
    spec = g3[1].checkbox("mel-spectrogram", True)
    alt = g3[2].checkbox("ชนิดสำรอง", True)
    force = g3[3].checkbox("ทำซ้ำ (force)", False, help="ปกติข้ามไฟล์ที่เคยตัดแล้ว — ติ๊กเพื่อทำซ้ำ")
    filetime = g3[4].checkbox("ใช้เวลาไฟล์", False,
                              help="บังคับใช้เวลาไฟล์ (เริ่มบันทึก) ข้ามชื่อ/metadata — เหมาะ recorder ที่ชื่อ/folder ไม่ใช่วันจริง")
    out = st.text_input("โฟลเดอร์ผลลัพธ์", DEF_OUT)

    ready = bool(audio_arg)
    if not ready:
        st.warning("เลือกหรืออัปโหลดไฟล์ก่อน")

    if st.button("▶️ เริ่มวิเคราะห์", type="primary", disabled=not ready):
        cmd = [
            PYTHON, str(SCRIPT), audio_arg, "-o", out,
            "--min-conf", str(min_conf), "--occurrence-gap", str(gap),
            "--lead", str(lead), "--tail", str(tail), "--target-dbfs", str(dbfs),
            "--mono" if mono else "--no-mono",
            "--spectrogram" if spec else "--no-spectrogram",
            "--alt-species" if alt else "--no-alt-species",
        ]
        if force:
            cmd += ["--force"]
        if filetime:
            cmd += ["--use-filetime"]
        if coords.strip():
            cmd += ["--coords", coords.strip()]
        if date.strip():
            cmd += ["--date", date.strip()]
        if place.strip():
            cmd += ["--place", place.strip()]

        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        st.caption("คำสั่ง: " + " ".join(f'"{a}"' if " " in a else a for a in cmd))
        st.info("กำลังรัน… โหลดโมเดล + analyze ไฟล์ยาวใช้เวลาหลายนาที (ดู log ด้านล่าง)")
        log_box = st.empty()
        lines = []
        with st.spinner("กำลังประมวลผล…"):
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env, bufsize=1)
            for line in proc.stdout:
                lines.append(line.rstrip())
                log_box.code("\n".join(lines[-300:]))
            code = proc.wait()
        if code == 0:
            st.success("เสร็จแล้ว ✅ ไปแท็บ '🎧 Review & เลือกอัป'")
        else:
            st.error(f"ผิดพลาด (exit {code}) — ดู log ด้านบน")


# ============================== REVIEW ==============================
def find_sessions(root: Path):
    """โฟลเดอร์ที่มี summary.xlsx = 1 ชุด (โครงใหม่ = โฟลเดอร์วัน YYYY.MM.DD)"""
    found = []
    if (root / "summary.xlsx").exists():
        found.append(root)
    for d in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        if (d / "summary.xlsx").exists():
            found.append(d)
    return found


def fmt_cell(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return v


with tab_review:
    rev_out = st.text_input("โฟลเดอร์ผลลัพธ์ที่จะรีวิว", DEF_OUT, key="rev_out")
    root = Path(rev_out)

    if not root.is_dir():
        st.warning("ยังไม่มีโฟลเดอร์นี้ — รันที่แท็บ Run ก่อน")
    else:
        sessions = find_sessions(root)
        if not sessions:
            st.warning("ไม่พบ summary.xlsx (ยังไม่มีผลลัพธ์ หรือ path ผิด)")
        else:
            sess = st.selectbox("วัน (โฟลเดอร์ผล)", sessions, format_func=lambda p: p.name)
            try:
                df = pd.read_excel(sess / "summary.xlsx")
            except Exception as exc:  # noqa: BLE001
                st.error(f"อ่าน summary.xlsx ไม่ได้: {exc}")
                df = None

            if df is not None and len(df):
                m = st.columns(4)
                m[0].metric("คลิปทั้งหมด", len(df))
                m[1].metric("ชนิด", df["Species (common)"].nunique())
                m[2].metric("conf เฉลี่ย", f'{df["Max confidence"].mean():.2f}')
                if "Clipping" in df:
                    m[3].metric("คลิปที่ clip", int((df["Clipping"].astype(str) == "YES").sum()))

                with st.expander("ตาราง summary", expanded=False):
                    st.dataframe(df, width="stretch", hide_index=True)

                species = ["(ทั้งหมด)"] + sorted(df["Species (common)"].astype(str).unique())
                pick = st.selectbox("กรองชนิด", species)
                view = df if pick == "(ทั้งหมด)" else df[df["Species (common)"].astype(str) == pick]

                st.caption("ติ๊ก ✅ คลิปที่ผ่าน แล้วกดปุ่มล่างสุดเพื่อคัดลอกไป _to_upload")
                selected = []
                for _, r in view.iterrows():
                    wav = sess / str(r["File"])
                    bits = [f'**{r["Species (common)"]}** _({r["Species (scientific)"]})_',
                            f'#{r["Occurrence #"]}', str(fmt_cell(r.get("Clock time"))),
                            f'{r["Duration (s)"]}s', f'conf **{r["Max confidence"]}**',
                            f'rating {fmt_cell(r.get("Provisional rating"))}',
                            f'peak {fmt_cell(r.get("Peak dBFS"))}dB']
                    if str(fmt_cell(r.get("Clipping"))) == "YES":
                        bits.append("⚠️ CLIP")
                    alt1 = fmt_cell(r.get("Alt species 1"))
                    if alt1:
                        bits.append(f'alt: {alt1} ({fmt_cell(r.get("Alt1 conf"))})')
                    st.markdown("  ·  ".join(str(b) for b in bits))

                    col1, col2 = st.columns([1, 2])
                    with col1:
                        if wav.exists():
                            col1.audio(str(wav))
                            if st.checkbox("✅ เลือกอัป", key=f"sel::{wav}"):
                                selected.append(wav)
                        else:
                            col1.error("ไม่พบไฟล์ wav")
                    with col2:
                        png = wav.with_suffix(".png")
                        if png.exists():
                            col2.image(str(png), width="stretch")
                        else:
                            col2.caption("(ไม่มี spectrogram — รันด้วย --spectrogram)")
                    st.divider()

                st.caption("🔗 **รวม** = เฉพาะคลิปที่เป็น **นกตัวเดียวกัน** (ฟังยืนยันก่อน) "
                           "→ ตัดเงียบหัวท้าย ต่อคั่น 1 วิ เป็นไฟล์เดียว (ตามคู่มือ ML ข้อ 5)")
                bcol = st.columns(2)
                if bcol[0].button(f"📤 คัดลอก {len(selected)} ไฟล์ ไป _to_upload",
                                  type="primary", disabled=not selected):
                    dest = sess / "_to_upload"
                    dest.mkdir(parents=True, exist_ok=True)
                    n = 0
                    for w in selected:
                        try:
                            shutil.copy2(w, dest / w.name)
                            png = w.with_suffix(".png")
                            if png.exists():
                                shutil.copy2(png, dest / png.name)
                            n += 1
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"คัดลอก {w.name} ไม่ได้: {exc}")
                    st.success(f"คัดลอก {n} คลิป → {dest}")

                if bcol[1].button(f"🔗 รวม {len(selected)} คลิป (นกตัวเดียวกัน)",
                                  disabled=len(selected) < 2):
                    dest = sess / "_to_upload"
                    dest.mkdir(parents=True, exist_ok=True)
                    out = dest / f"GROUPED_{Path(selected[0]).stem}.wav"
                    cmd = [PYTHON, str(SCRIPT), "--group"] + [str(w) for w in selected] + ["-o", str(out)]
                    env = dict(os.environ, PYTHONIOENCODING="utf-8")
                    r = subprocess.run(cmd, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", env=env)
                    if r.returncode == 0 and out.exists():
                        st.success(f"รวมแล้ว → {out.name}")
                        st.audio(str(out))
                    else:
                        st.error(f"รวมไม่สำเร็จ: {(r.stdout or '') + (r.stderr or '')}")
