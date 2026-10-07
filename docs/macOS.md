# BirdNET → eBird clipper (macOS)

โปรแกรมบน Mac สำหรับไฟล์เสียงภาคสนามยาว ๆ: ให้ BirdNET หาเสียงนก ตัดเป็นคลิปพร้อมอัปโหลด
eBird / Macaulay Library แล้วให้คนฟังตรวจทีละคลิปก่อนคัดลอกคลิปที่ยืนยันแล้วไปโฟลเดอร์ `Ready/`
ทุกผลจาก AI เป็น "ตัวเลือกให้ตรวจ" ไม่ใช่การยืนยันชนิด

> ฉบับย่อสำหรับเริ่มใช้ (ไทย/อังกฤษ) อยู่ที่ `อ่านก่อนใช้.txt`

---

## สารบัญ

1. [ภาพรวม](#1-ภาพรวม)
2. [เครื่องที่ใช้ได้](#2-เครื่องที่ใช้ได้)
3. [เปิดโปรแกรม และส่งให้เพื่อน](#3-เปิดโปรแกรม-และส่งให้เพื่อน)
4. [ใช้งานแบบเร็ว](#4-ใช้งานแบบเร็ว)
5. [หน้าจอแต่ละส่วน](#5-หน้าจอแต่ละส่วน)
6. [การวิเคราะห์ทำงานอย่างไร](#6-การวิเคราะห์ทำงานอย่างไร)
7. [ตัวช่วยตรวจ: Second opinion, eBird, เสียงอ้างอิง](#7-ตัวช่วยตรวจ)
8. [ผลลัพธ์และ summary.xlsx](#8-ผลลัพธ์และ-summaryxlsx)
9. [ข้อมูลของแอป: โฟลเดอร์ data/](#9-ข้อมูลของแอป-โฟลเดอร์-data)
10. [ใช้ผ่าน Command line](#10-ใช้ผ่าน-command-line)
11. [โครงสร้างโปรแกรม](#11-โครงสร้างโปรแกรม)
12. [สำหรับผู้พัฒนา](#12-สำหรับผู้พัฒนา)
13. [ข้อจำกัดที่ควรรู้](#13-ข้อจำกัดที่ควรรู้)
14. [แก้ปัญหาที่พบบ่อย](#14-แก้ปัญหาที่พบบ่อย)
15. [สัญญาอนุญาตและเครดิต](#15-สัญญาอนุญาตและเครดิต)

---

## 1. ภาพรวม

```
ไฟล์เสียงยาว (WAV/MP3/FLAC/M4A/OGG/AIFF)
  │  อ่านวันเวลา/พิกัด: metadata ในไฟล์ → ชื่อไฟล์ → เวลาไฟล์ของเครื่องอัด → กรอกเอง
  ▼
BirdNET 3.0 preview (หรือ 2.4) ฟังทีละ 3 วินาที เลื่อนทีละ 1.5 วินาที
  │  ตัวกรองพื้นที่/ฤดู (พิกัด + สัปดาห์)      ตัดชนิดที่ไม่ควรมีที่นั่นช่วงนั้น
  │  ตัวกรองถิ่นอาศัย (ถ้าติ๊ก)                 นกน้ำในจุดที่ไม่มีน้ำต้องคะแนนสูง
  ▼
รวมเสียงของชนิดเดียวกันที่ห่างกัน ≤ 5 วิ เป็น 1 occurrence → ตัดคลิปจากไฟล์ต้นฉบับ
  │  เผื่อหน้า/หลัง 3 วิ, คง sample rate/bit depth, mono, normalize peak −3 dBFS
  ▼
<โฟลเดอร์ผลลัพธ์>/<วัน_เวลา>/<ชนิด>/*.wav + spectrogram .png + summary.xlsx
  │
  ▼
แท็บ Review clips: ฟัง, ดู spectrogram (มีกรอบ), เทียบเสียงอ้างอิง, Second opinion, เช็ก eBird
  │  Approve (ให้คะแนนคุณภาพ 1–5 ด้วยคน) / Reject / Keep pending
  ▼
Ready/<ชนิด>/…_R<คะแนน>.wav   → อัปโหลด eBird / Macaulay Library
```

ไฟล์ต้นฉบับไม่ถูกแก้ไข ผลทุกอย่างอยู่ในโฟลเดอร์ผลลัพธ์ (ค่าเริ่มต้น `~/BirdNET_eBird`)

---

## 2. เครื่องที่ใช้ได้

| รายการ | ต้องการ |
|---|---|
| ชิป | Apple Silicon (M1 ขึ้นไป) — Mac ชิป Intel ใช้ไม่ได้ |
| macOS | Sequoia 15.2 ขึ้นไป (ไลบรารี LiteRT / ai-edge-litert ต้องการ) |
| พื้นที่ | โปรแกรม ~680 MB, โมเดล BirdNET 3.0 ~300 MB (`data/birdnet/`), cache อาจหลายร้อย MB |
| อินเทอร์เน็ต | Second opinion, เสียงอ้างอิง, eBird (และโหลดโมเดล 3.0 ถ้า `data/birdnet/` ยังไม่มี) |
| ติดตั้งเพิ่ม | ไม่ต้อง: Python, ffmpeg/ffprobe อยู่ในโฟลเดอร์โปรแกรมแล้ว |

---

## 3. เปิดโปรแกรม และส่งให้เพื่อน

### เปิดโปรแกรม
- **ดับเบิลคลิก `BirdNET eBird.app` บน Desktop** — เป็นตัวเปิดที่ชี้ไปยังโฟลเดอร์
  `Projects/Sound Recording/Programs/BirdNET-eBird-mac` (อย่าย้ายโฟลเดอร์นี้) log ของตัวเปิดอยู่ที่
  `~/Library/Logs/BirdNET-eBird.log`
- หรือดับเบิลคลิก **`BirdNET-eBird.command`** ในโฟลเดอร์โปรแกรม (ใช้ได้กับทุกตำแหน่งที่วางโฟลเดอร์)

### ส่งให้เพื่อน
1. **ใช้สคริปต์สร้าง zip** (แนะนำ) — ได้ `BirdNET-eBird-mac-share.zip` บน Desktop
   พร้อมโมเดล BirdNET 3.0 แต่**ไม่มี API key ของคุณ** (`data/settings.json`), cache, backup, tests:
   ```bash
   "/Users/<you>/Desktop/Projects/Sound Recording/Programs/BirdNET-eBird-mac/tools/make_share_zip.sh"
   ```
2. ถ้าคัดลอกเอง: ส่งทั้งโฟลเดอร์โปรแกรม แต่**ห้ามใส่ `data/settings.json`** (มี API key) และไม่ต้องใส่
   `data/cache/`, `_backup-before-upgrade/` (1.9 GB), `tests/`, `accuracy_cases/`,
   `__pycache__` — อย่าส่ง `BirdNET eBird.app` บน Desktop อย่างเดียว เพราะเป็นแค่ทางลัดไปยังโฟลเดอร์ในเครื่องคุณ
3. ครั้งแรกบนเครื่องเพื่อน macOS จะกันไว้ (โปรแกรมไม่ได้ลงชื่อกับ Apple):
   ดับเบิลคลิก `BirdNET-eBird.command` → ขึ้นว่าเปิดไม่ได้ → **System Settings › Privacy & Security
   › Open Anyway** (Sequoia เลิกให้คลิกขวา › Open แล้ว) ครั้งต่อไปดับเบิลคลิกได้เลย
   ตัว `.command` จะปลดการกักกัน (quarantine) ไฟล์ที่เหลือให้เอง
   หรือใช้ Terminal: `xattr -dr com.apple.quarantine /path/to/BirdNET-eBird-mac`
4. มีโมเดลใน `data/birdnet/` แล้ว เพื่อนวิเคราะห์ได้โดยไม่ต้องต่อเน็ต; xeno-canto / eBird ต้องใช้
   API key ของตัวเอง (ใส่ในแท็บ Settings)

---

## 4. ใช้งานแบบเร็ว

1. กด **Choose audio file** หรือ **Choose audio folder** (แถบบนสุด)
2. หน้าต่าง **Recording details** แสดงวันเวลา/พิกัดที่อ่านได้ กรอกเฉพาะที่ขาด
   (วันที่พิมพ์ `20261006`, เวลา `0929`, พิกัด `30.5165,114.4453` หรือวางลิงก์ Google Maps)
   ติ๊ก **Site habitat** ถ้าต้องการ (เช่น Forest หรือ Forest + Marsh) แล้วกด **Start analysis**
3. รอจนแถบล่างขึ้น Done — แอปเปิดแท็บ **Review clips** ให้เอง
4. เลือกคลิป → ฟัง (คลิกภาพหรือ ▶ Play) → ดูกรอบใน spectrogram, Second opinion, บรรทัด eBird
   → แก้ชื่อชนิดถ้าผิด → ให้ **Audio quality 1–5** → **✓ Approve → Ready** หรือ **Reject**
5. อัปโหลดไฟล์ใน `Ready/` ขึ้น eBird / Macaulay Library

เปิดโปรแกรมครั้งหน้า แท็บ Review clips จะกลับมาที่ผลชุดล่าสุดเอง ไม่ต้องวิเคราะห์ใหม่

---

## 5. หน้าจอแต่ละส่วน

### แถบด้านบน
| ปุ่ม | ทำอะไร |
|---|---|
| Choose audio file / Choose audio folder | เลือกไฟล์หรือทั้งโฟลเดอร์ แล้วเปิดหน้าต่าง Recording details |
| Analyze again | วิเคราะห์ไฟล์/โฟลเดอร์เดิมอีกครั้ง (ใช้ค่าปัจจุบันในแท็บ Settings) |
| Open output folder | เปิดโฟลเดอร์ผลลัพธ์ใน Finder |

ใต้ปุ่มแสดงไฟล์ที่เลือก, ข้อมูลที่ตรวจเจอ และโมเดลที่ใช้

### หน้าต่าง Recording details (ขึ้นทุกครั้งก่อนวิเคราะห์)
- แสดงวันเวลาพร้อมที่มา (embedded metadata / filename / file times …) และพิกัด
- ขอเฉพาะค่าที่ขาด: วันที่ (`YYYYMMDD`), พิกัด, เวลาเริ่ม (`HHMM`) ถ้าไฟล์มีแต่วันที่
- โฟลเดอร์หลายไฟล์: ค่าที่กรอกใช้เติมเฉพาะไฟล์ที่ขาด ไฟล์ที่มี metadata ใช้ของตัวเอง
- **Site habitat**: Forest, Field / farmland, Marsh / lake, River / stream, Sea / coast
  เลือกได้หลายอย่าง ไม่ติ๊ก = ไม่ตรวจถิ่นอาศัย (ดู [6.4](#64-ตัวกรองถิ่นอาศัย-avonet))

### แท็บ Settings
| ตั้งค่า | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| Audio file / folder, Output folder | – , `~/BirdNET_eBird` | ไฟล์ที่จะวิเคราะห์ และที่เก็บผล |
| Coordinates or Google Maps link | – | พิกัดจุดบันทึก |
| Date if no metadata / Start time | – | ใช้เมื่อไฟล์ไม่มีข้อมูล |
| Place | – | ชื่อสถานที่ เก็บใน summary |
| min_conf | 0.5 (3.0) / 0.25 (2.4) | คะแนน BirdNET ขั้นต่ำ |
| BirdNET model | 3.0-preview | หรือ 2.4 เพื่อเปรียบเทียบ (ผลแยกโฟลเดอร์) |
| Site habitat | ไม่ติ๊ก | เหมือนในหน้าต่าง Recording details |
| occurrence gap (s) | 5.0 | เสียงห่างกันไม่เกินนี้ = occurrence เดียวกัน |
| lead / tail (s) | 3.0 / 3.0 | เผื่อก่อนเสียงแรก/หลังเสียงสุดท้าย |
| normalize dBFS | −3.0 | ระดับ peak ของคลิป |
| analysis overlap (s) | 1.5 | หน้าต่าง 3 วิ เลื่อนทีละ (3 − overlap) วิ |
| also keep out-of-area ≥ | ปิด (0.7) | เก็บชนิดนอกพื้นที่/ฤดูที่คะแนนถึงเกณฑ์ ไว้ตรวจนกหลงถิ่น |
| to mono | เปิด | stereo → mono |
| mel-spectrogram | เปิด | สร้างภาพ .png ต่อคลิป (มีกรอบ) |
| alt species | เปิด | บันทึกชนิดอื่นที่ BirdNET เจอช่วงเดียวกัน |
| cut unknown | ปิด | ตัดเสียงที่คะแนนต่ำกว่า min_conf ไปโฟลเดอร์ `_Unknown/` |
| force redo | ปิด | วิเคราะห์ใหม่แม้เคยทำแล้ว และ**ล้างผลตรวจกลับเป็น Pending** |
| use file time | ปิด | บังคับใช้เวลาไฟล์ (ไม่ตรวจความสอดคล้อง) |
| Keep first-to-last call continuous | ปิด | ต่อเสียงของชนิดเดียวกันเป็นช่วงยาวช่วงเดียว (ใช้เมื่อมั่นใจว่าเป็นตัวเดียวกัน) |
| xeno-canto API key / eBird API key | – | key ของคุณเอง ใช้กับตัวช่วยตรวจ (ข้อ 7) |

### แท็บ Merge clips
รวมหลายคลิปของ **นกตัวเดียวกัน** (ตามแนวทาง eBird) เป็นไฟล์เดียว: ตัดเงียบหัวท้าย คั่นด้วยเงียบ 1 วิ
normalize −3 dBFS — Add…, เรียงด้วย Up/Down, Save as…, กด Merge

### แท็บ Review clips
- **Results**: เมนูรวมผลทุกชุดในโฟลเดอร์ผลลัพธ์ (ใหม่สุดก่อน: วันเวลา · ไฟล์ต้นทาง · จำนวนคลิป/ที่ตรวจแล้ว)
  **Refresh list** หลังเปลี่ยนโฟลเดอร์ผลลัพธ์, **Open other results…** สำหรับ summary.xlsx ที่อื่น
- **ตารางคลิป**: เวลา, ไฟล์, ชนิดจาก BirdNET, คะแนน, ช่อง *Check* (นอกพื้นที่หรือไม่เข้ากับถิ่นอาศัย), สถานะ
- **ช่องดูตัวอย่าง**: รายละเอียดคลิป, spectrogram (คลิกเพื่อเล่น), บรรทัด Second opinion (ฟ้า),
  บรรทัด eBird (เขียว)
- **ปุ่ม**: ▶ Play, ■ Stop, Open spectrogram full size, Reference sounds…, Second opinion
- **Look up species**: eBird ↗ (หน้าชนิด: รูป เสียง แผนที่), Photos ↗ / Sounds ↗ (Macaulay Library
  เรียงคะแนนสูงสุด; Sounds กรองตามมณฑลของจุดบันทึกเมื่อรู้), xeno-canto ↗
- **ช่องตรวจ**: Reviewed common name (เติมชื่อตาม eBird ให้), Scientific name, Audio quality (1–5), Notes
- **✓ Approve → Ready**: ต้องมีชื่อสามัญ + ชื่อวิทยาศาสตร์ + คะแนนคุณภาพ, คลิปต้องไม่เกิน 500 MB
  (ขีดจำกัด eBird) — คัดลอกไป `Ready/<ชื่อสามัญ>/`
  **Reject**: ไม่ใช้คลิปนี้ (ลบสำเนาใน Ready ถ้ามี) — **Keep pending**: เก็บไว้ตรวจทีหลัง
- ปุ่มในแท็บนี้ใช้ชื่อในช่อง Scientific name — แก้ชนิดแล้ว ตัวช่วยตรวจจะใช้ชนิดที่แก้

### แถบสถานะงาน (ล่างสุด เห็นตลอด)
- **Show details / Hide details**: ช่อง log ของงาน (ลากเส้นแบ่งปรับขนาดได้)
- แถบความคืบหน้า + สถานะ เช่น `Analyzing REC-002.WAV (file 1/2) · 05:12 · 2 queued`
- **■ Stop**: หยุดงานที่รันอยู่ ไฟล์ที่เสร็จแล้วยังอยู่ รันซ้ำจะทำต่อจากที่เหลือ
- **Jobs…**: งานที่รันอยู่ + คิว; ระหว่างงานรัน เลือกไฟล์ใหม่ได้เลย งานจะต่อคิวแล้วเริ่มเอง
  เอางานออกจากคิว / Stop all ได้ในหน้าต่างนี้
- ปิดโปรแกรมระหว่างวิเคราะห์ได้ (ถามยืนยันก่อน) — หยุดอย่างปลอดภัยเหมือนกด Stop

---

## 6. การวิเคราะห์ทำงานอย่างไร

### 6.1 วันเวลาและพิกัด
ลำดับที่ใช้ (ค่าที่กรอกเองมาก่อนเสมอ):
1. **Metadata ในไฟล์**: QuickTime creation date (มี timezone), BWF bext (เวลาเครื่องอัด),
   `creation_time` (UTC), INFO/ICRD, XMP — พิกัดจาก ISO 6709 / XMP GPS
2. **ชื่อไฟล์**: เช่น `2026-10-01 12_22.wav`, `20260608`, `YYYYMMDDHHMMSS` (ปี พ.ศ. ≥ 2400 แปลงเป็น ค.ศ.)
3. **เวลาไฟล์ของเครื่องอัด** — ใช้เฉพาะเมื่อ *เวลาสร้าง + ความยาวเสียง ≈ เวลาแก้ไข*
   (คลาดได้ 2% ของความยาว, อย่างน้อย 5 วิ ไม่เกิน 2 นาที; ไฟล์ยาว ≥ 30 วิ)
   ไฟล์ที่ถูกคัดลอกจนเวลาเปลี่ยน/ถูกแก้ไขทีหลังจะไม่ผ่าน แล้วแอปจะถาม
4. **กรอกเอง** (Recording details / Settings / CLI)

เวลาที่มี timezone (เช่น UTC จากไฟล์ m4a ของมือถือ) แปลงเป็น **time zone ของเครื่อง Mac**
ให้ตั้ง time zone ของเครื่องตรงกับจุดที่บันทึก (System Settings › General › Date & Time)

พิกัดไม่ถูกเดาเอง: ไม่มีใน metadata = ต้องกรอก ลิงก์ Google Maps ใช้ตำแหน่งหมุด (`!3d…!4d…`)
ก่อนจุดกลางแผนที่ (`@lat,lon`)

### 6.2 BirdNET
- **3.0 preview (ค่าเริ่มต้น)**: แพ็กเกจ `birdnet` (ONNX fp16) + โมเดลพื้นที่ geo 3.0 — ผลอยู่ในโฟลเดอร์
  ที่ลงท้าย `_v3preview` โมเดลอยู่ที่ `data/birdnet/` (ถ้าไม่มีจะโหลดให้ครั้งแรก)
- **2.4**: `birdnetlib` บน LiteRT (`ai-edge-litert`, ผ่าน shim `app/tflite_runtime`) — ไม่ใช้ TensorFlow
- หน้าต่าง 3 วิ เลื่อนทีละ 1.5 วิ, คะแนน ≥ min_conf

### 6.3 ตัวกรองพื้นที่/ฤดู
- โมเดล geo ให้ความน่าจะเป็นของแต่ละชนิดที่พิกัด + สัปดาห์ (48 สัปดาห์/ปี) — ผ่านเมื่อ ≥ 0.03
- **ตัดทิ้งเป็นค่าเริ่มต้น**: ชนิดนอกพื้นที่/ฤดู และเสียงที่ตรวจพื้นที่ไม่ได้ (แมลง กบ สัตว์อื่น)
- ชื่อวิทยาศาสตร์ของโมเดลเสียงกับโมเดลพื้นที่ต่างกัน ~900 ชื่อ — ถ้าไม่ตรงจะจับคู่ด้วยชื่อสามัญก่อน
- ต้องการดูนกหลงถิ่น: ติ๊ก *also keep out-of-area ≥* (เก็บพร้อมคำเตือน “No — verify by ear”)
- ตัวกรองรู้แค่พื้นที่กับฤดู ไม่รู้ถิ่นอาศัย → ข้อ 6.4

### 6.4 ตัวกรองถิ่นอาศัย (AVONET)
ใช้ถิ่นอาศัยหลักของแต่ละชนิดจาก AVONET (~13,600 ชื่อ, จับคู่กับรายชื่อนก BirdNET ได้ ~82%)
นกบกผ่านทุกจุด เพราะเดินทาง/ร้องจากที่ใกล้ ๆ ได้ — นกน้ำต้องมีแหล่งน้ำที่เข้ากัน:

| ถิ่นอาศัยของชนิด | ผ่านเมื่อจุดบันทึกมี |
|---|---|
| ป่า / ทุ่ง / พุ่มไม้ / เมือง ฯลฯ | ทุกแบบ |
| Wetland (รวมนกที่ใช้ชีวิตในน้ำ) | Marsh, River, Sea |
| Riverine | River, Marsh |
| Coastal | Sea, Marsh, River |
| Marine | Sea |

ไม่เข้ากันแต่คะแนน ≥ 0.8 → เก็บไว้ (อาจบินผ่าน) พร้อมคำเตือน *Check*
ชนิดที่ AVONET ไม่มีข้อมูล → ไม่ตรวจ

### 6.5 Occurrence และคลิป
- เสียงของชนิดเดียวกันที่ห่างกัน ≤ occurrence gap รวมเป็น 1 occurrence = 1 คลิป ช่วงต่อเนื่องจาก
  (เสียงแรก − lead) ถึง (เสียงสุดท้าย + tail) ไม่เฉือนช่วงกลาง ไม่ต่อหลายช่วง
- อ่านเฉพาะช่วงจากไฟล์ต้นฉบับ (ไม่โหลดทั้งไฟล์), คง sample rate และ bit depth (24-bit/float → 24-bit)
- mono, normalize peak −3 dBFS, วัด peak/clipping/SNR/ความถี่ก่อน normalize
- ชื่อคลิป `<วัน_เวลาเริ่ม>_<HHMM>_<Genus.species>_<SourceID>_<ลำดับ>_R0.wav` (R0 = ยังไม่ตรวจ)
- Keep continuous: รวมทุก occurrence ของชนิดเป็นช่วงเดียว (จำกัดขนาด ~350 MB ต่อช่วง)
- cut unknown: เสียงคะแนนต่ำกว่า min_conf (≥ 0.1) ที่ไม่ทับช่วงที่ระบุได้ → `_Unknown/`

### 6.6 Spectrogram และกรอบ
- mel-spectrogram ต่อคลิป สร้างใน process แยกหลังวิเคราะห์เสร็จ
- **แถบขาวด้านบน** = ช่วงที่ BirdNET เจอชนิดนี้ + คะแนน (เชื่อได้)
- **กรอบฟ้า** = เสียงที่ดังกว่าพื้นหลังชัดเจน (≥ 12 dB) ภายในช่วงนั้น — ประมาณจากภาพ อาจเป็นนก
  ชนิดอื่นหรือแมลงที่ดังพร้อมกัน (BirdNET ตัดสินทั้งหน้าต่าง 3 วิ ไม่บอกว่าเสียงไหนเป็นของชนิดนั้น)
- ข้อมูลกรอบเก็บในไฟล์ภาพด้วย ภาพที่ไม่ตรงกับคลิปปัจจุบันจะถูกสร้างใหม่เอง

### 6.7 รันซ้ำ, หยุดกลางคัน, ความปลอดภัยของไฟล์
- **รันไฟล์เดิมซ้ำ**: ถ้าไฟล์ต้นทาง การตั้งค่า และคลิปตรงกับผลเดิม → ข้าม แล้วเปิดผลเดิมให้
  ถ้าการตั้งค่าเปลี่ยน → วิเคราะห์ใหม่ และเก็บผลตรวจของคลิปที่ได้เหมือนเดิมทุกไบต์
  ถ้าโฟลเดอร์นั้นมีคลิป Approved แล้ว → ข้าม ให้เลือกโฟลเดอร์ผลลัพธ์อื่นเพื่อเปรียบเทียบ
- **force redo**: สร้างคลิปใหม่ ตั้งผลตรวจกลับเป็น Pending
- **หยุดกลางคัน** (Stop / ปิดแอป / Ctrl+C): summary บันทึกทีละไฟล์ ไฟล์ที่เสร็จแล้วไม่ต้องวิเคราะห์ใหม่
- คลิปเขียนลงพื้นที่พักก่อน (`.staging`) แล้วย้ายเข้าที่ทีเดียว ถ้าพังกลางทางไม่มีไฟล์ครึ่ง ๆ กลาง ๆ
- ทุกคลิปและสำเนาใน Ready มี sha256 — ไฟล์ที่ถูกแก้จากภายนอก (เช่นใน Audacity) จะไม่ถูกเขียนทับ/ลบ

---

## 7. ตัวช่วยตรวจ

ทุกตัวเป็น **คำใบ้** ไม่เปลี่ยนผลเอง — หูของคุณตัดสินสุดท้าย

### Reference sounds… (ต้องมี xeno-canto API key)
รายการเสียงของชนิดนั้นจาก xeno-canto เรียงคุณภาพดีและใกล้จุดบันทึกก่อน (ค้นรัศมี ~10° ก่อน
แล้วทั่วโลก) กรอง song/call/flight call/alarm call ได้ — ▶ Play reference / ▶ Play my clip สลับฟัง
แสดงผู้บันทึกและสัญญาอนุญาตของแต่ละไฟล์ ถ้าชื่อวิทยาศาสตร์ไม่มีใน xeno-canto (เช่น xeno-canto ใช้
*Pardaliparus* แต่ BirdNET ใช้ *Periparus*) จะค้นด้วยชื่ออังกฤษแทน
ขอ key ฟรีที่ https://xeno-canto.org/account

### Second opinion (ต้องมี xeno-canto API key)
1. BirdNET 3.0 ฟังคลิปอีกรอบ → ตัวเลือก = ชนิดที่ตรวจเจอ + ชนิดคะแนนรองอีก ≤ 3 ชนิด ที่ผ่านตัวกรองพื้นที่
2. แปลง 3 ช่วงที่เสียงชัดที่สุดของคลิปเป็น embedding (BirdNET 3.0, 1,280 มิติ)
3. ดึงเสียง xeno-canto คุณภาพดีใกล้จุดบันทึก ชนิดละ ≤ 6 ไฟล์ (ยาว ≤ 3 นาที) ใช้เฉพาะช่วงที่
   BirdNET ได้ยินชนิดนั้นจริง (≥ 0.2)
4. คะแนน = cosine similarity กับช่วงอ้างอิงที่ใกล้ที่สุด เฉลี่ยทั้ง 3 ช่วง → เรียงอันดับ
   ถ้าคลิปคล้ายชนิดอื่นมากกว่า ขึ้น **⚠ Closer to …**

ครั้งแรกของแต่ละชนิด 1–4 นาที (โหลดอ้างอิง) ครั้งต่อไป ~10 วิ ผลเก็บตาม sha256 ของคลิป
ส่วนต่างคะแนนมักน้อย (เช่น 0.71 vs 0.67) — ใช้เป็นคำใบ้

### บรรทัด eBird (ต้องมี eBird API key)
เมื่อเลือกคลิป: ชนิดนี้เคยมีรายงานในมณฑล/รัฐของจุดบันทึกไหม (**⚠ never reported** ถ้าไม่เคย),
รายงานล่าสุดในรัศมี 25 กม. ช่วง 30 วัน (เฉพาะไฟล์ที่บันทึกภายใน 30 วัน — ขีดจำกัด API),
และ hotspot ที่ใกล้ที่สุด — ไม่มีรายงานไม่ได้แปลว่าไม่มีนก (ข้อมูล eBird บางพื้นที่น้อย)
ขอ key ที่ https://ebird.org/api/keygen

### ชื่อตาม eBird และลิงก์ (ไม่ต้องมี key)
ระบบชื่อของ eBird (~11,000 ชนิด) ใช้เติมช่อง Reviewed common name (เช่น *Eophona migratoria*:
BirdNET เรียก Chinese Grosbeak → eBird เรียก Yellow-billed Grosbeak) และสร้างลิงก์ Look up species

---

## 8. ผลลัพธ์และ summary.xlsx

```
~/BirdNET_eBird/
└── 2026.10.06_0928_v3preview/          ← วัน_เวลาเริ่มบันทึก (+ _v3preview ถ้าใช้ 3.0)
    ├── summary.xlsx                     ← 1 แถว/คลิป
    ├── Asian Tit/
    │   ├── 2026.10.06_0928_0933_Parus.cinereus_333b43d107bf_001_R0.wav
    │   └── 2026.10.06_0928_0933_Parus.cinereus_333b43d107bf_001_R0.png
    ├── _Unknown/                        ← ถ้าเปิด cut unknown
    └── Ready/                           ← เฉพาะคลิปที่ Approve
        └── Asian Tit/2026.10.06_0928_<clipid>_Parus.cinereus_R4.wav
```

### คอลัมน์ใน summary.xlsx
| คอลัมน์ | ความหมาย |
|---|---|
| Species (common) / (scientific) | ชนิดจาก BirdNET |
| Occurrence # | ลำดับ occurrence ของชนิดนั้นในไฟล์ |
| Start / End offset (s), Clock time, Duration (s) | ตำแหน่งในไฟล์ต้นฉบับ, เวลาจริง, ความยาวคลิป |
| Max confidence | คะแนน BirdNET สูงสุดใน occurrence |
| Expected by location/date | Yes / No — verify by ear / Not checked |
| Species habitat, Fits site habitat | ถิ่นอาศัยจาก AVONET, เข้ากับจุดบันทึกไหม (ถ้าติ๊กถิ่นอาศัย) |
| Alt species 1–2, Alt1/Alt2 conf | ชนิดอื่นที่ BirdNET เจอช่วงเดียวกัน |
| Peak dBFS, Clipping, SNR, Peak freq, Freq low/high | วัดก่อน normalize (SNR/ความถี่ใช้ 15 วิแรก) |
| Detection boxes | ช่วง BirdNET + กรอบเสียง (JSON) สำหรับวาด spectrogram |
| AI confidence band (1-4) | กลุ่มคะแนน AI — **ไม่ใช่คะแนนคุณภาพเสียง** |
| Place, Latitude, Longitude, Date/time source | ข้อมูลการบันทึกและที่มาของเวลา |
| File, Generated sha256 | ตำแหน่งคลิปและลายนิ้วมือไฟล์ |
| Source file / path / ID | ไฟล์ต้นทาง (ID จากเนื้อไฟล์ ย้ายโฟลเดอร์ได้) |
| Analysis settings | การตั้งค่าที่ใช้ (JSON) ใช้ตัดสินว่าต้องวิเคราะห์ใหม่ไหม |
| Review status, Reviewed common/scientific, Quality rating, Review notes, Ready file, Ready sha256 | ผลตรวจของคน |

---

## 9. ข้อมูลของแอป: โฟลเดอร์ `data/`

ทุกอย่างที่แอปสร้างเองอยู่ใน `data/` ของโฟลเดอร์โปรแกรมที่เดียว (ย้าย/สำรองทั้งโฟลเดอร์โปรแกรมได้)

| ที่อยู่ | มีอะไร | ลบได้ไหม |
|---|---|---|
| `data/settings.json` | API key ของ xeno-canto/eBird, ผลชุดล่าสุดที่เปิด (สิทธิ์ 600) — **ส่วนตัว ห้ามส่งให้คนอื่น** | ได้ (ต้องใส่ key ใหม่) |
| `data/birdnet/` | โมเดล BirdNET 3.0 + geo + taxonomy (~300 MB) | ได้ (โหลดใหม่ตอนใช้) |
| `data/cache/xeno-canto/` | ไฟล์เสียงอ้างอิงที่เคยฟัง | ได้ |
| `data/cache/second-opinion/` | ผล Second opinion + embedding อ้างอิง | ได้ (คำนวณใหม่) |
| `data/cache/ebird/` | ระบบชื่อ eBird (60 วัน), hotspot/รายชื่อชนิด (7 วัน), รายงานล่าสุด (6 ชม.) | ได้ |
| `~/Library/Logs/BirdNET-eBird.log` | log ของตัวเปิดบน Desktop (ข้อยกเว้นเดียวที่อยู่นอก `data/`) | ได้ |

- รุ่นก่อนเก็บไว้ใน `~/Library/Application Support` และ `~/Library/Caches`
  แอปย้ายเข้า `data/` ให้เองตอนเปิด (ไม่ทับของที่มีอยู่แล้ว)
- ถ้าโฟลเดอร์โปรแกรมเขียนไม่ได้ (เช่นเปิดจากดิสก์อ่านอย่างเดียว) แอปจะกลับไปใช้ที่เดิมใน `~/Library`
- ผลวิเคราะห์ (คลิป, spectrogram, summary.xlsx) ไม่อยู่ใน `data/` แต่อยู่ในโฟลเดอร์ผลลัพธ์ที่เลือก
  (ค่าเริ่มต้น `~/BirdNET_eBird/`)
- **อย่าแก้สคริปต์ใน `BirdNET eBird.app/Contents/MacOS/`** — macOS ผูกสิทธิ์เข้าถึงโฟลเดอร์ Desktop
  ไว้กับเนื้อหาไฟล์นั้น แก้แล้วแอปจะเปิดไม่ขึ้น ("could not start" โดยไม่มีอะไรใน log)
  ถ้าเผลอแก้ไปแล้ว: คืนไฟล์เดิม หรือให้สิทธิ์ใหม่ใน System Settings › Privacy & Security ›
  Files & Folders › BirdNET eBird › Desktop Folder

---

## 10. ใช้ผ่าน Command line

```bash
cd "/Users/<you>/Desktop/Projects/Sound Recording/Programs/BirdNET-eBird-mac"
./BirdNET-eBird.command ~/Desktop/Birding/REC-002.WAV --coords 30.5165,114.4453 --habitat forest --spectrogram
./BirdNET-eBird.command ~/Desktop/Birding/Sounds --date 20261006 --same-date-for-all --coords 30.5,114.4
./BirdNET-eBird.command --help
```

| ตัวเลือก | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `audio` | – | ไฟล์หรือโฟลเดอร์ |
| `-o, --output` | `~/BirdNET_eBird` | โฟลเดอร์ผลลัพธ์ |
| `--coords LAT,LON` / `--lat` `--lon` | – | พิกัด (รับลิงก์ Google Maps) |
| `--date` | – | `YYYYMMDD` หรือ `YYYY-MM-DD` |
| `--start-time` | – | `HHMM`, `HHMMSS` หรือ `HH:MM` |
| `--same-date-for-all` | ปิด | ยืนยันว่า `--date` ใช้กับทุกไฟล์ในโฟลเดอร์ |
| `--fill-missing-metadata` | ปิด | ใช้ `--date/--coords/--start-time` เฉพาะไฟล์ที่ขาด |
| `--use-metadata / --no-use-metadata` | เปิด | อ่าน metadata ในไฟล์ |
| `--use-filetime` | ปิด | บังคับใช้เวลาไฟล์ |
| `--model {3.0-preview,2.4}` | 3.0-preview | โมเดลเสียง |
| `--min-conf` | 0.5 / 0.25 | คะแนนขั้นต่ำ |
| `--overlap` | 1.5 | ช่วงซ้อนของหน้าต่าง 3 วิ |
| `--habitat forest,marsh,…` | – | forest, field, marsh, river, sea |
| `--out-of-range-min-conf` | ปิด | เก็บชนิดนอกพื้นที่ที่คะแนนถึงค่านี้ |
| `--occurrence-gap`, `--lead`, `--tail` | 5, 3, 3 | วินาที |
| `--target-dbfs` | −3 | normalize peak |
| `--format` | wav | นามสกุลคลิป (wav, flac, mp3 …) |
| `--place` | – | ชื่อสถานที่ |
| `--datetime-regex` | (ดู --help) | regex อ่านวันเวลาจากชื่อไฟล์ |
| `--mono / --no-mono` | เปิด | |
| `--spectrogram / --no-spectrogram` | **ปิด** (GUI เปิด) | สร้างภาพ |
| `--alt-species / --no-alt-species` | เปิด | |
| `--unknown`, `--unknown-min-conf` | ปิด, 0.1 | ตัด `_Unknown/` |
| `--keep-continuous` | ปิด | ช่วงต่อเนื่องต่อชนิด |
| `--force` | ปิด | วิเคราะห์ใหม่ + ล้างผลตรวจ |

โหมดพิเศษ (GUI ใช้เรียกเบื้องหลัง):
- `--group clip1.wav clip2.wav … -o out.wav` — รวมคลิป (Merge clips)
- `--gen-spectrograms <โฟลเดอร์วัน> …` — สร้าง/ซ่อม spectrogram
- `--second-opinion <summary.xlsx> <แถว>` — คำนวณ Second opinion (อ่าน key จาก settings.json)

exit code: `0` สำเร็จ, `1` มีไฟล์ล้มเหลว, `2` ข้อมูลไม่ครบ/ค่าผิด, `130` ถูกหยุด

---

## 11. โครงสร้างโปรแกรม

```
BirdNET-eBird-mac/
├── BirdNET-eBird.command      ตัวเปิด (ไม่มีอาร์กิวเมนต์ = GUI, มี = CLI) + ปลด quarantine
├── ffmpeg, ffprobe            9.0.2 แบบเสียงอย่างเดียว ไม่พึ่ง Homebrew (tools/build_ffmpeg.sh)
├── python/                    Python 3.12.13 (python-build-standalone) + Tk 9.0.4 + แพ็กเกจทั้งหมด
├── app/
│   ├── birdnet_app.py         จุดเข้า: GUI หรือ CLI
│   ├── birdnet_gui.py         หน้าจอ Tkinter, คิวงาน, Review, Reference/Jobs windows
│   ├── field_audio_to_ebird.py  ขั้นวิเคราะห์ทั้งหมด (CLI): metadata, BirdNET, ตัวกรอง, ตัดคลิป,
│   │                            spectrogram, summary, รันซ้ำ/หยุดกลางคัน
│   ├── v3_model.py            ตัวต่อ BirdNET 3.0 preview + geo model (+ จับคู่ชื่อสามัญ)
│   ├── habitat.py             กฎถิ่นอาศัย (AVONET)
│   ├── review_store.py        อ่าน/บันทึกผลตรวจใน summary.xlsx, คัดลอก Ready, รายการผลทุกชุด
│   ├── second_opinion.py      Second opinion (embedding + xeno-canto)
│   ├── xeno_canto.py          xeno-canto API v3 (ค้น, ดาวน์โหลด, cache)
│   ├── ebird.py               eBird API 2.0 (ระบบชื่อ, รายงานใกล้ ๆ, รายชื่อชนิด, hotspot)
│   ├── entry_formats.py       แปลงวันที่ 20261006 / เวลา 0929
│   ├── paths.py               ที่อยู่ข้อมูลทั้งหมด (data/) + ย้ายจาก ~/Library รุ่นก่อน
│   ├── app_settings.py        data/settings.json (สิทธิ์ 600)
│   ├── tflite_runtime/        shim ให้ birdnetlib (2.4) ใช้ LiteRT
│   └── assets/                ไอคอน, avonet_habitat.csv
├── tests/test_workflow.py     เทสต์อัตโนมัติ
├── data/                      ข้อมูลของแอป: settings (API key — ส่วนตัว), โมเดล, cache (หัวข้อ 9)
├── tools/build_ffmpeg.sh      สร้าง ffmpeg/ffprobe ใหม่จาก source
├── tools/make_share_zip.sh    zip สำหรับส่งเพื่อน (ไม่รวม API key)
├── accuracy_cases/            บันทึกกรณีศึกษาความแม่นยำ (ไม่ต้องแจกจ่าย)
├── อ่านก่อนใช้.txt             คู่มือเริ่มต้น (ไทย/อังกฤษ)
└── README.md                  เอกสารนี้
```

GUI ไม่โหลดโมเดลเอง: การวิเคราะห์, รวมคลิป, spectrogram, Second opinion รันเป็น process แยก
(`field_audio_to_ebird.py` ด้วย Python ในโฟลเดอร์) แล้วอ่านความคืบหน้าจากข้อความที่พิมพ์ออกมา
การหยุดใช้ SIGTERM ซึ่ง CLI เปลี่ยนเป็น KeyboardInterrupt เพื่อย้อนไฟล์ที่ค้างและบันทึกงานที่เสร็จแล้ว

ส่วนประกอบหลัก: birdnet 1.1.1, onnxruntime 1.30.0, birdnetlib 0.18.1, ai-edge-litert 2.2.0,
numpy 2.4.6, pandas 3.0.3, librosa 0.11.0, soundfile 0.14.0, pydub 0.25.1, scipy 1.18.0,
openpyxl 3.1.5, matplotlib 3.11.1, Pillow 12.3.0

---

## 12. สำหรับผู้พัฒนา

```bash
cd "/Users/<you>/Desktop/Projects/Sound Recording/Programs/BirdNET-eBird-mac"
PATH="$PWD:$PATH" ./python/bin/python3.12 -m unittest discover -s tests       # เทสต์ทั้งหมด
./python/bin/python3.12 -m pip install --disable-pip-version-check <package>   # เพิ่มแพ็กเกจ
tools/build_ffmpeg.sh                                                          # สร้าง ffmpeg ใหม่
```
- เทสต์ไม่เรียก BirdNET/xeno-canto/eBird จริง (ใช้ตัวจำลอง) ยกเว้นส่วนที่ระบุ
- `tools/build_ffmpeg.sh` ใช้ source จาก Homebrew (`brew fetch --build-from-source ffmpeg lame`)
  สร้างแบบเสียงอย่างเดียว ลิงก์เฉพาะไลบรารีของระบบ + LAME แบบ static แล้วคัดลอกไปวางข้าง `app/`
- เปลี่ยนวิธีวาด spectrogram: เพิ่ม `SPECTROGRAM_STYLE` ใน `field_audio_to_ebird.py` ภาพเก่าจะสร้างใหม่เอง
- เปลี่ยนสิ่งที่มีผลต่อผลวิเคราะห์: ใส่ค่าใน `settings` ของ `process_file` เพื่อให้ผลเก่าถูกวิเคราะห์ใหม่เมื่อรันซ้ำ
- คอมเมนต์ในโค้ดเป็นภาษาไทย ข้อความในหน้าจอเป็นภาษาอังกฤษ

---

## 13. ข้อจำกัดที่ควรรู้

- BirdNET บอกได้แค่ชนิด ไม่บอกประเภทเสียง (song / call) และไม่บอกว่าเสียงไหนในหน้าต่าง 3 วิเป็นของชนิดนั้น
- ชนิดที่ BirdNET แทบไม่รู้จักจะพลาด (เช่น Yellow-billed Grosbeak ในไฟล์ 2026-10-01: คะแนน 0.005)
  การลด min_conf ทั่วไปจะเพิ่มผลผิดจำนวนมาก (ดู `accuracy_cases/`)
- ตัวกรองพื้นที่ใช้ time zone และพิกัดที่ถูกต้อง — time zone ของเครื่องผิด = เวลาจากไฟล์ UTC ผิด
- ถิ่นอาศัยจาก AVONET มีชนิดละ 1 แบบ และจับคู่ได้ ~82% ของรายชื่อ
- eBird ในหลายพื้นที่มีรายงานน้อย: "ไม่มีรายงาน" ≠ "ไม่มีนก"; รายงานล่าสุดย้อนได้ 30 วัน
- xeno-canto: เสียงอ้างอิงบางไฟล์มีนกชนิดอื่นปน, บางชนิดมีไฟล์น้อย, เว็บมีระบบกันบอต (เบราว์เซอร์ปกติใช้ได้)
- Second opinion ยังไม่ได้วัดความแม่นยำกับคลิปที่คนตรวจ — ตรวจคลิปมากขึ้นแล้วจึงประเมินได้
- เล่นเสียงใช้ `afplay` ของ macOS

---

## 14. แก้ปัญหาที่พบบ่อย

| อาการ | ทางแก้ |
|---|---|
| macOS บอกว่าเปิดไม่ได้ / damaged | System Settings › Privacy & Security › Open Anyway หรือ `xattr -dr com.apple.quarantine <โฟลเดอร์>` |
| BirdNET eBird.app บน Desktop เปิดไม่ขึ้น | โฟลเดอร์โปรแกรมถูกย้าย/เปลี่ยนชื่อ — ใช้ `BirdNET-eBird.command` แทน หรือย้ายกลับที่เดิม |
| "could not start" แต่ log ว่าง | macOS ไม่ให้เข้าถึง Desktop (สคริปต์ในตัวเปิดถูกแก้) — ดูหัวข้อ 9 |
| ถามวันที่ทั้งที่ Finder มีวันที่ | เวลาไฟล์ไม่สอดคล้อง (ถูกคัดลอก/แก้ไข) — กรอกเอง หรือใช้ use file time ถ้าแน่ใจ |
| เวลาคลาดหลายชั่วโมง | ไฟล์มีเวลา UTC → ตั้ง time zone ของ Mac ให้ตรงกับจุดบันทึก |
| ได้ชนิดแปลก ๆ จากต่างทวีป | ตรวจพิกัดในหน้าต่าง Recording details; อย่าเปิด also keep out-of-area ถ้าไม่ต้องการ |
| นกน้ำโผล่ในป่า | ติ๊ก Site habitat ให้ตรง (เช่น Forest เท่านั้น) |
| วิเคราะห์ครั้งแรกนาน/ล้ม | ไม่มี `data/birdnet/` → ต้องต่อเน็ตเพื่อโหลดโมเดล BirdNET 3.0 (~300 MB) |
| เลือกไฟล์เดิมแล้วไม่วิเคราะห์ใหม่ | ผลเดิมตรงกับการตั้งค่าแล้ว (แอปเปิดผลเดิมให้) — ติ๊ก force redo ถ้าจะทำใหม่ |
| "approved result uses older settings" | ผลชุดนั้นมีคลิป Approved — เลือกโฟลเดอร์ผลลัพธ์อื่นเพื่อเทียบ |
| Reference sounds / Second opinion ใช้ไม่ได้ | ใส่ xeno-canto API key ใน Settings แล้วกด Save key |
| บรรทัด eBird ขึ้นว่า key ไม่ถูกต้อง | ตรวจ eBird API key ใน Settings |
| cache ใหญ่ | ลบโฟลเดอร์ใน `data/cache/` ได้ |
| ไฟล์เสียงขนาด 0 ไบต์ / ปี 1980 | เครื่องอัดบันทึกไม่จบ ไม่มีเสียงในไฟล์ |

---

## 15. สัญญาอนุญาตและเครดิต

- **BirdNET** (Cornell Lab of Ornithology & Chemnitz University of Technology): โมเดลใช้สัญญา
  CC BY-NC-SA 4.0 — ใช้เพื่อการศึกษา/วิจัย ไม่ใช่เชิงพาณิชย์
- **AVONET**: Tobias et al. 2022, *Ecology Letters*, doi:10.1111/ele.13898 — ข้อมูล CC BY 4.0
  (doi:10.6084/m9.figshare.16586228)
- **FFmpeg 9.0.2** และ **LAME**: LGPL — สร้างใหม่ได้ด้วย `tools/build_ffmpeg.sh`
- **xeno-canto**: เสียงอ้างอิงเป็นของผู้บันทึกแต่ละคน ตามสัญญาอนุญาตที่แสดงในแอป
- **eBird / Macaulay Library**: ใช้ตามเงื่อนไข eBird API Terms of Use
- แพ็กเกจ Python อื่น ๆ: ดูสัญญาอนุญาตใน `python/lib/python3.12/site-packages/*.dist-info`

ขอบคุณ / Thanks
- Biopikat (developer / page: Biopikat)
- Tripitcha Wanwimolruk
- Wichyanan Limparungpatthanakij
- Utain Pummarin
- Chutinton Viriyapanon
- The eBird reviewers of Thailand
- Cornell Lab of Ornithology (BirdNET, Merlin Bird ID, eBird / Macaulay Library)
- Anthropic (Claude — helped develop and write this tool)

กรุณาอัปโหลดเสียงขึ้น eBird / Macaulay Library — ช่วยงานวิทยาศาสตร์ภาคประชาชน และช่วยให้
Merlin Bird ID แม่นขึ้น โดยเฉพาะพื้นที่ที่ยังมีข้อมูลน้อยอย่างเอเชียตะวันออกเฉียงใต้
