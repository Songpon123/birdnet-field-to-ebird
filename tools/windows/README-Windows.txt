============================================================
  BirdNET -> eBird clipper  (Windows)    อ่านก่อนใช้ / READ ME FIRST
============================================================
รายละเอียดทั้งระบบ / Full documentation: README.md

------------------------------------------------------------
ภาษาไทย
------------------------------------------------------------
เครื่องที่ใช้ได้
  - Windows 10 หรือ 11 แบบ 64-bit (x64) — ไม่ต้องติดตั้งอะไรเพิ่ม
    (มี Python, ffmpeg และโมเดล BirdNET 3.0 อยู่ในโฟลเดอร์แล้ว วิเคราะห์ได้โดยไม่ต้องต่อเน็ต)

ติดตั้ง
  1. คลิกขวาที่ไฟล์ zip > Properties > ติ๊ก Unblock > OK  (ก่อนแตกไฟล์ กันคำเตือนทุกไฟล์)
  2. แตก zip ไปไว้ที่สั้น ๆ เช่น C:\BirdNET-eBird-win
     (อย่าไว้ใน C:\Program Files และอย่าเปิดจากในไฟล์ zip โดยตรง)
  3. ดับเบิลคลิก "Create desktop shortcut.bat" ครั้งเดียว จะได้ไอคอน BirdNET eBird บน Desktop
     หรือดับเบิลคลิก "BirdNET eBird.bat" เพื่อเปิดเลย
  4. ถ้าขึ้น "Windows protected your PC": More info > Run anyway (โปรแกรมไม่ได้ลงชื่อกับ Microsoft)

ใช้งาน (เหมือนรุ่น Mac)
  1. Choose audio file หรือ Choose audio folder (แถบบนสุด)
  2. หน้าต่าง Recording details: กรอกเฉพาะที่ขาด  วันที่ 20261006, เวลา 0929,
     พิกัด 30.5165,114.4453 หรือวางลิงก์ Google Maps; ติ๊ก Site habitat ถ้าต้องการ > Start analysis
  3. ผลอยู่ในแท็บ Review clips (ผลเก่าทุกชุดอยู่ในเมนู Results)
  4. ฟัง (คลิกภาพหรือ Play) ดูกรอบใน spectrogram แก้ชื่อ ให้คะแนน 1-5 แล้ว Approve หรือ Reject
  5. ใส่ API key ของตัวเอง (xeno-canto / eBird) ในแท็บ Settings แล้วกด Save key

ควรรู้
  - ข้อมูลของแอปอยู่ในโฟลเดอร์ data\  (settings.json มี API key — ห้ามส่งให้คนอื่น)
  - เปิดไม่ขึ้น: ดู data\logs\BirdNET-eBird.log
  - ผลวิเคราะห์อยู่ที่ C:\Users\<ชื่อคุณ>\BirdNET_eBird\ (เปลี่ยนได้ในแท็บ Settings)
  - เวลาในไฟล์แบบ UTC (เช่น m4a จากมือถือ) แปลงตาม time zone ของเครื่อง ตั้งให้ตรงกับจุดที่บันทึก
  - Windows ตั้งเวลาสร้างไฟล์ใหม่ตอนคัดลอก แอปจึงอาจถามเวลาเริ่มบันทึกถ้าไม่มีในไฟล์
  - ■ Stop หยุดงานได้ ไฟล์ที่เสร็จแล้วยังอยู่ รันใหม่จะทำต่อ

------------------------------------------------------------
English
------------------------------------------------------------
Requirements
  - Windows 10 or 11, 64-bit (x64). Nothing to install: Python, ffmpeg and the
    BirdNET 3.0 model are included, so analysis works offline.

Install
  1. Right-click the zip > Properties > tick Unblock > OK (before extracting)
  2. Extract to a short path such as C:\BirdNET-eBird-win
     (not C:\Program Files, and do not run it from inside the zip)
  3. Double-click "Create desktop shortcut.bat" once for a Desktop icon,
     or double-click "BirdNET eBird.bat" to open the app
  4. If "Windows protected your PC" appears: More info > Run anyway (the app is unsigned)

Using it (same as the Mac version)
  1. Choose audio file or Choose audio folder (top bar)
  2. Recording details: fill in only what is missing (date 20261006, time 0929,
     coordinates or a Google Maps link), optionally a Site habitat, then Start analysis
  3. Results open in Review clips; earlier results are in the Results menu
  4. Listen, check the spectrogram, correct the species, rate 1-5, Approve or Reject
  5. Paste your own xeno-canto / eBird API keys in Settings and click Save key

Good to know
  - App data lives in the data\ folder; data\settings.json holds your API keys: never share it
  - If the app does not open, see data\logs\BirdNET-eBird.log
  - Results go to C:\Users\<you>\BirdNET_eBird\ (change it in Settings)
  - UTC times in files (e.g. phone m4a) use this PC's time zone; set it to where you recorded
  - Copying a file on Windows resets its creation time, so the app may ask for the start time
  - Stop keeps finished files; run again to continue

------------------------------------------------------------
Please upload to eBird / Macaulay Library. Thanks: see README.md
