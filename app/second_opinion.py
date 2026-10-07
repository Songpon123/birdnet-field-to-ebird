"""Second opinion for one reviewed clip: is it closer to xeno-canto recordings of the
detected species or of BirdNET's other plausible candidates?

The clip's loudest BirdNET 3.0 segments and segments of nearby, good-quality xeno-canto
recordings (only where BirdNET also hears that species) are turned into BirdNET 3.0
embeddings; each candidate's score is the cosine similarity to its nearest reference
segments, averaged over the clip segments. A hint for the reviewer, not an identification.

Run through the CLI: field_audio_to_ebird.py --second-opinion SUMMARY.xlsx ROW
"""

import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

import paths
import xeno_canto
from app_settings import load_settings
from review_store import load_reviews

CACHE = paths.CACHE / "second-opinion"
MAX_CANDIDATES = 4             # ชนิดที่ตรวจเจอ + ตัวเลือกรองอีก 3
CANDIDATE_MIN_SCORE = 0.02     # คะแนน BirdNET ขั้นต่ำของตัวเลือกรองในคลิป
QUERY_SEGMENTS = 3             # ช่วง 3 วิของคลิปที่ใช้เทียบ (ที่ BirdNET ให้คะแนนสูงสุด)
REFS_PER_SPECIES = 6
MAX_REF_SECONDS = 180          # ข้ามไฟล์อ้างอิงยาวเกิน (โหลดนาน)
REF_MIN_SCORE = 0.2            # ใช้เฉพาะช่วงที่ BirdNET ได้ยินชนิดนั้นในไฟล์อ้างอิงจริง
REF_SEGMENTS = 3
OVERLAP = 1.5


def result_path(clip_sha):
    return CACHE / f"{clip_sha}.json"


def cached_result(clip_sha):
    try:
        return json.loads(result_path(clip_sha).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def run(summary, row_number, key, log=print, analyzer=None):
    summary = Path(summary)
    row = {r["_row"]: r for r in load_reviews(summary)}.get(int(row_number))
    if row is None:
        raise ValueError("Selected clip is no longer in the summary")
    clip = (summary.parent / str(row["File"])).resolve()
    if not clip.is_file():
        raise FileNotFoundError(clip)
    clip_sha = str(row.get("Generated sha256") or "") or _sha256(clip)

    if analyzer is None:
        log("Loading BirdNET 3.0 …")
        from v3_model import V3Analyzer
        analyzer = V3Analyzer()
    model = analyzer.model
    labels = {str(label).partition("_")[0]: str(label) for label in model.species_list}
    spans = _spans(row)

    log("Scoring the clip …")
    scores = defaultdict(dict)                    # segment start -> {scientific: score}
    for r in model.predict([str(clip)], top_k=10, n_workers=1, overlap_duration_s=OVERLAP,
                           default_confidence_threshold=0.01, show_stats=None).to_structured_array():
        start, end = float(r["start_time"]), float(r["end_time"])
        if spans is None or any(start < b and end > a for a, b in spans):
            scores[round(start, 2)][str(r["species_name"]).partition("_")[0]] = float(r["confidence"])
    embeddings = {round(float(r["start_time"]), 2): np.asarray(r["embedding"], dtype=np.float32)
                  for r in model.encode([str(clip)], n_workers=1, overlap_duration_s=OVERLAP,
                                        show_stats=None).to_structured_array()}

    current = str(row.get("Reviewed scientific") or row.get("Species (scientific)") or "").strip()
    current = " ".join(current.split()[:2])
    candidates = _candidates(scores, current, labels, analyzer, row)
    if not candidates:
        raise ValueError("BirdNET found no candidate species in this clip")
    top_segments = sorted(scores, key=lambda s: -max(scores[s].get(c, 0.0) for c in candidates))
    query = [_unit(embeddings[s]) for s in top_segments[:QUERY_SEGMENTS] if s in embeddings]
    if not query:
        raise ValueError("No BirdNET segments found in this clip")

    lat, lon = _number(row.get("Latitude")), _number(row.get("Longitude"))
    ranking = []
    for scientific in candidates:
        log(f"References for {scientific} …")
        refs, used = reference_embeddings(model, scientific, labels.get(scientific, scientific),
                                          key, lat, lon, log)
        best = max((s.get(scientific, 0.0) for s in scores.values()), default=0.0)
        similarity = (float(np.mean([float((refs @ q).max()) for q in query])) if len(refs) else None)
        ranking.append({"scientific": scientific,
                        "common": _common(labels.get(scientific, scientific)),
                        "similarity": None if similarity is None else round(similarity, 3),
                        "birdnet": round(best, 3), "references": used})
    ranking.sort(key=lambda item: -(item["similarity"] if item["similarity"] is not None else -1))
    result = {"clip_sha": clip_sha, "current": current, "ranking": ranking,
              "query_segments": len(query), "created": datetime.now().isoformat(timespec="seconds")}
    CACHE.mkdir(parents=True, exist_ok=True)
    result_path(clip_sha).write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def _candidates(scores, current, labels, analyzer, row):
    best = defaultdict(float)
    for segment in scores.values():
        for scientific, score in segment.items():
            best[scientific] = max(best[scientific], score)
    plausible = _area_filter(analyzer, row, labels)
    others = [s for s, score in sorted(best.items(), key=lambda kv: -kv[1])
              if s != current and score >= CANDIDATE_MIN_SCORE and plausible(s)]
    picks = ([current] if current in labels else []) + others
    return picks[:MAX_CANDIDATES]


def _area_filter(analyzer, row, labels):
    """ตัวเลือกรองต้องผ่านตัวกรองพื้นที่/ฤดูเดียวกับตอนวิเคราะห์ (ถ้ารู้พิกัดและวันที่)"""
    lat, lon = _number(row.get("Latitude")), _number(row.get("Longitude"))
    try:
        date = datetime.fromisoformat(json.loads(row.get("Analysis settings") or "{}").get("date"))
    except (TypeError, ValueError):
        date = None
    if lat is None or lon is None or date is None:
        return lambda _scientific: True
    from v3_model import GEO_MIN_CONFIDENCE
    by_scientific, by_common = analyzer._geo_probabilities(lat, lon, date)

    def plausible(scientific):
        probability = by_scientific.get(scientific)
        if probability is None:
            probability = by_common.get(_common(labels.get(scientific, "")).casefold(), (None,))[0]
        return probability is not None and probability >= GEO_MIN_CONFIDENCE
    return plausible


def reference_embeddings(model, scientific, label, key, lat, lon, log=print):
    """เวกเตอร์ (n, dim) ของช่วงที่ BirdNET ได้ยินชนิดนี้ในไฟล์ xeno-canto ใกล้จุดบันทึก"""
    recordings, _total = xeno_canto.search(scientific, key, lat=lat, lon=lon, limit=30,
                                           common=str(label).partition("_")[2])
    chosen = [r for r in recordings if r["file"] and _seconds(r["length"]) <= MAX_REF_SECONDS]
    chosen = chosen[:REFS_PER_SPECIES]
    vectors, used, pending = [], 0, []
    for recording in chosen:
        cached = _ref_cache(recording["id"], scientific)
        if cached.is_file():
            stored = np.load(cached)
            vectors += list(stored)
            used += bool(len(stored))
        else:
            try:
                pending.append((recording, _prepare(xeno_canto.download(recording))))
            except (xeno_canto.XenoCantoError, RuntimeError, OSError) as exc:
                log(f"  skipped {recording['id']}: {exc}")
    if pending:
        log(f"  analyzing {len(pending)} new reference recording(s) …")
        scores, encoded = _analyze(model, label, [str(path) for _recording, path in pending], log)
        for recording, path in pending:
            file_scores = scores.get(str(path), {})
            best = sorted((s for s, c in file_scores.items() if c >= REF_MIN_SCORE),
                          key=lambda s: -file_scores[s])[:REF_SEGMENTS]
            picked = np.array([_unit(encoded[str(path)][s]) for s in best if s in encoded.get(str(path), {})],
                              dtype=np.float32).reshape(-1, model_dim(encoded))
            cache = _ref_cache(recording["id"], scientific)
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.save(cache, picked)                     # ว่าง = ไฟล์นี้ใช้ไม่ได้ (จำไว้ ไม่ต้องลองซ้ำ)
            vectors += list(picked)
            used += bool(len(picked))
    return (np.array(vectors, dtype=np.float32) if vectors else np.zeros((0, 1), np.float32)), used


def _prepare(path):
    """WAV โมโนสำหรับ BirdNET (ตัวโหลดของ BirdNET 3.0 ล้มกับ MP3 สเตอริโอบางไฟล์)"""
    import soundfile as sf
    target = CACHE / "prepared" / (Path(path).stem + ".wav")
    if target.is_file():
        return target
    try:
        data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001  ไฟล์เสีย/อ่านไม่ได้
        raise RuntimeError(f"cannot read audio: {exc}") from exc
    if len(data) < rate:                     # สั้นกว่า 1 วินาที
        raise RuntimeError("recording too short")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.stem + ".tmp.wav")
    sf.write(str(temporary), data.mean(axis=1), rate, subtype="PCM_16")
    temporary.replace(target)
    return target


def _analyze(model, label, paths, log=print):
    """คะแนนของชนิดนี้ + เวกเตอร์ ทุกช่วง 3 วิ ของแต่ละไฟล์; ถ้าทั้งชุดล้ม ลองทีละไฟล์แล้วข้ามไฟล์ที่เสีย"""
    scores, encoded = defaultdict(dict), defaultdict(dict)

    def one_batch(batch):
        for r in model.predict(batch, top_k=None, n_workers=1, overlap_duration_s=OVERLAP,
                               default_confidence_threshold=0.0, custom_species_list=[label],
                               show_stats=None).to_structured_array():
            scores[str(r["input"])][round(float(r["start_time"]), 2)] = float(r["confidence"])
        for r in model.encode(batch, n_workers=1, overlap_duration_s=OVERLAP,
                              show_stats=None).to_structured_array():
            encoded[str(r["input"])][round(float(r["start_time"]), 2)] = np.asarray(r["embedding"], np.float32)

    try:
        one_batch(paths)
    except RuntimeError:
        for path in paths:
            try:
                one_batch([path])
            except RuntimeError:
                log(f"  skipped {Path(path).stem}: BirdNET could not read it")
                scores.pop(path, None)
                encoded.pop(path, None)
    return scores, encoded


def model_dim(encoded):
    for segments in encoded.values():
        for vector in segments.values():
            return len(vector)
    return 1


def _ref_cache(xc_id, scientific):
    return CACHE / "references" / f"{xc_id}_{scientific.replace(' ', '_')}.npy"


def _spans(row):
    try:
        windows = json.loads(row.get("Detection boxes") or "")["windows"]
        return [(float(a), float(b)) for a, b, *_rest in windows] or None
    except (TypeError, ValueError, KeyError):
        return None                                   # summary รุ่นเก่า: ใช้ทั้งคลิป


def _unit(vector):
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


def _common(label):
    from v3_model import EBIRD_COMMON_NAMES
    scientific, _, common = str(label).partition("_")
    return EBIRD_COMMON_NAMES.get(scientific, common or scientific)


def _seconds(length):
    try:
        parts = [int(p) for p in str(length).split(":")]
    except ValueError:
        return 0
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv):
    if len(argv) != 2:
        print("usage: --second-opinion SUMMARY.xlsx ROW")
        return 2
    key = load_settings().get("xeno_canto_key", "")
    try:
        result = run(argv[0], argv[1], key, log=lambda text: print(text, flush=True))
    except (xeno_canto.XenoCantoError, ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}", flush=True)
        return 1
    print("RESULT " + json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
