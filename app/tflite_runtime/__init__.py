"""
tflite_runtime shim -> ai_edge_litert (LiteRT)
----------------------------------------------
birdnetlib ลอง`import tflite_runtime.interpreter` ก่อน full tensorflow.
shim นี้ทำให้ birdnetlib ใช้ ai-edge-litert (เบา ~16MB) แทน TensorFlow (~1.5GB)
เวลาสร้าง .exe จะได้ไม่ต้อง bundle full TF (ซึ่ง crash ตอน PyInstaller analyze).

ถ้าไม่ได้ลง ai-edge-litert -> import ล้ม -> birdnetlib fallback ไป tensorflow.lite เอง.
"""
