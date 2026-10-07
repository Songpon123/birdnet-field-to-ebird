import contextlib
import io
import json
import os
import sys
import tempfile
import time
import types
import unittest
import wave
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

# app/ first, like the real app: its tflite_runtime shim routes birdnetlib to LiteRT
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import birdnetlib  # noqa: E402
import field_audio_to_ebird as pipeline
from review_store import load_reviews, save_review


def make_wav(path: Path, seconds=1):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\0\0" * (8000 * seconds))


def fake_recording(starts, interrupt_on=None):
    """BirdNET stand-in: one 'Test bird' detection per start second."""
    class FakeRecording:
        def __init__(self, _analyzer, path, **_kwargs):
            self.path = path
            self.detections = [
                {"start_time": start, "end_time": start + 1, "common_name": "Test bird",
                 "scientific_name": "Avis testus", "confidence": 0.8}
                for start in starts
            ]

        def analyze(self):
            if Path(self.path).name == interrupt_on:
                raise KeyboardInterrupt   # what the CLI sees when the GUI window is closed

    return FakeRecording


PROCESS_ARGS = dict(lat_arg=13.8, lon_arg=100.5, cfg_lat=None, cfg_lon=None,
                    date_override=datetime(2026, 7, 15), use_meta=False, min_conf=0.5, gap=5,
                    lead=3, tail=3, target_dbfs=-3, fmt="wav", make_mono=True, incl_alt=False,
                    place_arg=None, dt_regex=pipeline.FILENAME_DATETIME_REGEX)


class WorkflowTests(unittest.TestCase):
    def test_filename_date_and_time_need_no_manual_date(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "2026-10-01 12_22.wav"
            make_wav(source)
            context = pipeline.recording_context_preview(source)
            self.assertEqual(context["datetime"], datetime(2026, 10, 1, 12, 22))
            self.assertEqual(context["datetime_source"], "filename")
            self.assertTrue(context["has_time"])
            pipeline.require_recording_context([source], None, True, 30.577, 114.393)

    def test_fill_missing_metadata_keeps_tagged_file_details(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recordings = root / "recordings"
            recordings.mkdir()
            tagged = recordings / "TAGGED.WAV"
            missing = recordings / "MISSING.WAV"
            make_wav(tagged, seconds=8)
            make_wav(missing, seconds=9)
            tagged_meta = {"datetime": datetime(2026, 10, 1, 7, 30),
                           "datetime_has_time": True,
                           "lat": 30.577, "lon": 114.393, "place": None}
            empty_meta = {"datetime": None, "datetime_has_time": False,
                          "lat": None, "lon": None, "place": None}

            def metadata(path):
                return tagged_meta if Path(path).name == tagged.name else empty_meta

            argv = ["field_audio_to_ebird.py", str(recordings), "-o", str(root / "out"),
                    "--model", "2.4", "--no-spectrogram", "--fill-missing-metadata",
                    "--date", "2026-10-02", "--coords", "31.0,115.0",
                    "--start-time", "09:00"]
            analyzer = types.ModuleType("birdnetlib.analyzer")
            analyzer.Analyzer = lambda: None
            with patch.object(sys, "argv", argv), \
                    patch.dict(sys.modules, {"birdnetlib.analyzer": analyzer}), \
                    patch.object(birdnetlib, "Recording", fake_recording((1,))), \
                    patch.object(pipeline, "read_audio_metadata", side_effect=metadata), \
                    contextlib.redirect_stdout(io.StringIO()):
                pipeline.main()
            tagged_rows = pd.read_excel(root / "out" / "2026.10.01_0730" / "summary.xlsx")
            missing_rows = pd.read_excel(root / "out" / "2026.10.02_0900" / "summary.xlsx")
            self.assertEqual((tagged_rows.iloc[0]["Latitude"], tagged_rows.iloc[0]["Longitude"]),
                             (30.577, 114.393))
            self.assertEqual((missing_rows.iloc[0]["Latitude"], missing_rows.iloc[0]["Longitude"]),
                             (31.0, 115.0))
            self.assertEqual(tagged_rows.iloc[0]["Clock time"], "07:30:01")
            self.assertEqual(missing_rows.iloc[0]["Clock time"], "09:00:01")

    def test_v3_preview_is_separate_and_uses_recording_location(self):
        class FakeV3:
            model_name = "BirdNET 3.0 preview ONNX fp16 + geo 3.0.4 (0.03)"
            direct_prediction = True

            def detections(self, _path, overlap, confidence_floor,
                           latitude, longitude, recorded_at):
                self.options = (overlap, confidence_floor,
                                latitude, longitude, recorded_at)
                return [{
                    "start_time": 3.0, "end_time": 6.0,
                    "common_name": "Light-vented Bulbul",
                    "scientific_name": "Pycnonotus sinensis",
                    "confidence": 0.608,
                    "is_predicted_for_location_and_date": True,
                    "location_filter_available": True,
                }]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "REC-001.WAV"
            make_wav(source, seconds=10)
            analyzer = FakeV3()
            rows, day = pipeline.process_file(
                analyzer, source, root / "out", **dict(PROCESS_ARGS, min_conf=0.25)
            )
            self.assertEqual(analyzer.options[:4], (1.5, 0.25, 13.8, 100.5))
            self.assertEqual(analyzer.options[4].date(), datetime(2026, 7, 15).date())
            self.assertTrue(day.name.endswith("_v3preview"))
            self.assertEqual(rows[0]["Expected by location/date"], "Yes")

    def test_v3_geo_rejects_out_of_area_false_positive_but_keeps_asian_tit(self):
        from v3_model import V3Analyzer, birdnet_week

        class FakeGeo:
            def predict(self, lat, lon, **kwargs):
                self.options = (lat, lon, kwargs)
                return types.SimpleNamespace(
                    species_list=["Vireolanius pulchellus_Green Shrike-Vireo",
                                  "Parus cinereus_Asian Tit"],
                    species_probs=[0.0, 0.916],
                )

        class FakeAcoustic:
            def predict(self, *_args, **_kwargs):
                return types.SimpleNamespace(to_structured_array=lambda: [
                    {"start_time": 1, "end_time": 4,
                     "species_name": "Vireolanius pulchellus_Green Shrike-Vireo",
                     "confidence": 0.589},
                    {"start_time": 4, "end_time": 7,
                     "species_name": "Parus cinereus_Asian Tit",
                     "confidence": 0.804},
                ])

        analyzer = V3Analyzer.__new__(V3Analyzer)
        analyzer.model = FakeAcoustic()
        analyzer.geo_model = FakeGeo()
        analyzer._geo_cache = {}
        date = datetime(2026, 10, 1, 10, 6)
        detections = analyzer.detections("sample.wav", 1.5, 0.5,
                                       30.55301882846582, 114.3630903725855, date)
        self.assertEqual(birdnet_week(date), 37)
        self.assertEqual(analyzer.geo_model.options[2]["week"], 37)
        kept = [d for d in detections if d["is_predicted_for_location_and_date"] or
                d["confidence"] >= 0.7]
        self.assertEqual([d["common_name"] for d in kept], ["Asian Tit"])

    def test_v3_uses_ebird_name_for_yellow_billed_grosbeak(self):
        from v3_model import V3Analyzer

        analyzer = V3Analyzer.__new__(V3Analyzer)
        analyzer.geo_model = types.SimpleNamespace(predict=lambda *_args, **_kwargs:
            types.SimpleNamespace(species_list=["Eophona migratoria_Chinese Grosbeak"],
                                  species_probs=[0.66]))
        analyzer.model = types.SimpleNamespace(predict=lambda *_args, **_kwargs:
            types.SimpleNamespace(to_structured_array=lambda: [{
                "start_time": 1, "end_time": 4,
                "species_name": "Eophona migratoria_Chinese Grosbeak",
                "confidence": 0.7,
            }]))
        analyzer._geo_cache = {}
        rows = analyzer.detections("sample.wav", 1.5, 0.5, 30.55, 114.36,
                                   datetime(2026, 10, 1))
        self.assertEqual(rows[0]["common_name"], "Yellow-billed Grosbeak")
        self.assertEqual(rows[0]["scientific_name"], "Eophona migratoria")

    def test_location_filter_drops_out_of_area_and_unchecked_species_by_default(self):
        class FakeV3:
            model_name = "fake v3"
            direct_prediction = True

            def detections(self, *_args):
                def det(start, common, sci, conf, predicted, available=True):
                    return {"start_time": start, "end_time": start + 3, "common_name": common,
                            "scientific_name": sci, "confidence": conf,
                            "is_predicted_for_location_and_date": predicted,
                            "location_filter_available": available}
                return [det(1, "Light-vented Bulbul", "Pycnonotus sinensis", 0.74, True),
                        det(10, "Chimpanzee", "Pan troglodytes", 0.81, False),
                        det(20, "Meimuna opalifera", "Meimuna opalifera", 0.92, True, False)]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "REC-001.WAV"
            make_wav(source, seconds=30)
            rows, _ = pipeline.process_file(FakeV3(), source, root / "out", **PROCESS_ARGS)
            self.assertEqual([r["Species (common)"] for r in rows], ["Light-vented Bulbul"])

    def test_detection_boxes_frame_each_note_inside_birdnet_windows(self):
        import numpy as np
        from pydub import AudioSegment
        sr = 32000
        t = np.arange(sr * 10) / sr
        y = 0.02 * np.random.default_rng(0).standard_normal(t.size)

        def note(start, stop, f0, f1):
            inside = (t >= start) & (t < stop)
            tt = t[inside] - start
            out = np.zeros_like(t)
            out[inside] = 0.3 * np.sin(2 * np.pi * (f0 * tt + (f1 - f0) * tt ** 2 / (2 * (stop - start))))
            out[inside] *= np.hanning(inside.sum())
            return out
        y += note(2.0, 4.0, 3000, 3000)                         # whistle
        for k in range(4):
            y += note(7.0 + k * 0.25, 7.15 + k * 0.25, 5000, 6500)   # four rising notes
        clip = AudioSegment((y * 32767).astype("<i2").tobytes(), frame_rate=sr,
                            sample_width=2, channels=1)
        windows = [(101.5, 104.5, 0.71), (103.0, 106.0, 0.66), (106.0, 109.0, 0.80)]
        boxes = pipeline.detection_boxes(windows, {"start": 101.5, "end": 109.0}, 100.0, clip)
        self.assertEqual(boxes["windows"], [[1.5, 9.0, 0.8]])
        self.assertEqual(len(boxes["sounds"]), 5)
        t0, t1, low, high = boxes["sounds"][0]
        self.assertTrue(2.0 <= t0 < t1 <= 4.0 and 2800 < low < 3000 < high < 3200)
        self.assertTrue(all(5000 <= b[2] and b[3] <= 6600 for b in boxes["sounds"][1:]))

    def test_spectrogram_redrawn_when_boxes_change(self):
        with tempfile.TemporaryDirectory() as temp:
            day = Path(temp)
            make_wav(day / "clip.wav", seconds=4)
            (day / "clip.png").write_bytes(b"old image without boxes")
            boxes = {"windows": [[0.5, 3.5, 0.9]], "sounds": []}
            pd.DataFrame([{"File": "clip.wav", "Species (common)": "A",
                           "Detection boxes": json.dumps(boxes)}]).to_excel(day / "summary.xlsx", index=False)
            with contextlib.redirect_stdout(io.StringIO()):
                pipeline.gen_spectrograms([day])
                self.assertEqual(pipeline._png_description(day / "clip.png"), pipeline._boxes_tag(boxes))
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    pipeline.gen_spectrograms([day])
            self.assertIn("created 0, skipped existing 1", output.getvalue())

    def test_xeno_canto_search_orders_by_quality_then_distance(self):
        import urllib.error
        import xeno_canto

        def rec(number, quality, lat, lon, key="lon"):
            return {"id": str(number), "q": quality, "lat": str(lat), key: str(lon), "type": "call",
                    "cnt": "China", "loc": "Wuhan", "rec": "A. Recordist", "length": "0:12",
                    "lic": "//creativecommons.org/licenses/by-nc-sa/4.0/",
                    "file": f"https://xeno-canto.org/{number}/download", "url": f"//xeno-canto.org/{number}",
                    "file-name": f"XC{number}-call.mp3"}
        nearby = {"numRecordings": "2", "recordings": [rec(1, "B", 30.6, 114.3), rec(2, "A", 31.0, 115.0)]}
        world = {"numRecordings": "900", "recordings": [rec(2, "A", 31.0, 115.0), rec(3, "A", 60.0, 30.0, "lng"),
                                                        rec(4, "C", 30.5, 114.4)]}
        queries = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        def fake_urlopen(request, timeout):
            query = request.full_url.split("query=")[1].split("&")[0]
            queries.append(query)
            return Response(json.dumps(nearby if "box" in query else world).encode())

        with patch.object(xeno_canto.urllib.request, "urlopen", fake_urlopen):
            found, total = xeno_canto.search("Phylloscopus inornatus", "user-key", "call", 30.5, 114.4,
                                             limit=10)
        self.assertEqual([r["id"] for r in found], ["XC2", "XC3", "XC1", "XC4"])   # A near, A far, B, C
        self.assertEqual(total, 900)
        self.assertIn("gen%3APhylloscopus+sp%3Ainornatus+type%3Acall+box%3A", queries[0])
        first = found[0]
        self.assertEqual((first["license"], first["page"], first["ext"]),
                         ("CC BY-NC-SA 4.0", "https://xeno-canto.org/2", ".mp3"))
        self.assertIsNotNone(found[1]["distance_km"])                    # 'lng' field also understood
        self.assertEqual(xeno_canto.build_query("Anas crecca", "flight call"),
                         'gen:Anas sp:crecca type:"flight call"')

        def rejected(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {},
                                         io.BytesIO(b'{"message": "Missing or invalid key"}'))
        with patch.object(xeno_canto.urllib.request, "urlopen", rejected):
            with self.assertRaisesRegex(xeno_canto.XenoCantoError, "Missing or invalid key"):
                xeno_canto.search("Phylloscopus inornatus", "wrong")
        with self.assertRaisesRegex(xeno_canto.XenoCantoError, "API key"):
            xeno_canto.search("Phylloscopus inornatus", "")

    def test_second_opinion_flags_clip_closer_to_another_species(self):
        import numpy as np
        import second_opinion
        import xeno_canto
        from birdnet_gui import format_second_opinion

        clip_vec, ybw_vec, hume_vec = np.eye(4)[0], np.eye(4)[1], np.eye(4)[0] * 0.9 + np.eye(4)[2] * 0.1
        labels = ["Phylloscopus inornatus_Yellow-browed Warbler", "Phylloscopus humei_Hume's Leaf Warbler"]

        class FakeModel:
            species_list = labels

            def __init__(self):
                self.ref_paths = {}

            def predict(self, paths, **kwargs):
                rows = []
                for path in paths:
                    for start in (0.0, 1.5, 3.0):
                        if kwargs.get("custom_species_list"):        # reference recording
                            rows.append({"input": path, "start_time": start, "end_time": start + 3,
                                         "species_name": kwargs["custom_species_list"][0], "confidence": 0.8})
                        else:                                         # the clip
                            rows += [{"input": path, "start_time": start, "end_time": start + 3,
                                      "species_name": labels[0], "confidence": 0.9},
                                     {"input": path, "start_time": start, "end_time": start + 3,
                                      "species_name": labels[1], "confidence": 0.3}]
                return types.SimpleNamespace(to_structured_array=lambda: rows)

            def encode(self, paths, **kwargs):
                def vector(path):
                    if "XC" not in str(path):
                        return clip_vec
                    return hume_vec if "humei" in str(path) else ybw_vec
                rows = [{"input": path, "start_time": start, "end_time": start + 3, "embedding": vector(path)}
                        for path in paths for start in (0.0, 1.5, 3.0)]
                return types.SimpleNamespace(to_structured_array=lambda: rows)

        analyzer = types.SimpleNamespace(model=FakeModel(),
                                         _geo_probabilities=lambda *_a: ({"Phylloscopus humei": 0.4}, {}))
        downloads = []

        def fake_search(scientific, key, lat=None, lon=None, limit=30, common=""):
            species = scientific.split()[1]
            return [{"id": f"XC{species}{n}", "file": "https://x", "length": "0:30", "ext": ".mp3"}
                    for n in range(2)], 2

        def fake_download(recording):
            downloads.append(recording["id"])
            path = Path(temp) / f"{recording['id']}.wav"
            make_wav(path, seconds=6)
            return path

        with tempfile.TemporaryDirectory() as temp:
            day = Path(temp)
            make_wav(day / "clip.wav", seconds=6)
            pd.DataFrame([{"File": "clip.wav", "Generated sha256": "abc123", "Species (common)": "Yellow-browed Warbler",
                           "Species (scientific)": "Phylloscopus inornatus", "Latitude": 30.5, "Longitude": 114.4,
                           "Analysis settings": json.dumps({"date": "2026-10-06"}),
                           "Detection boxes": json.dumps({"windows": [[0.0, 6.0, 0.9]], "sounds": []})}]
                         ).to_excel(day / "summary.xlsx", index=False)
            with patch.object(second_opinion, "CACHE", day / "cache"), \
                    patch.object(xeno_canto, "search", fake_search), \
                    patch.object(xeno_canto, "download", fake_download):
                result = second_opinion.run(day / "summary.xlsx", 2, "user-key", log=lambda _t: None,
                                            analyzer=analyzer)
                self.assertEqual([r["scientific"] for r in result["ranking"]],
                                 ["Phylloscopus humei", "Phylloscopus inornatus"])
                self.assertIn("⚠ Closer to Hume's Leaf Warbler", format_second_opinion(result))
                self.assertEqual(second_opinion.cached_result("abc123")["ranking"], result["ranking"])
                second_opinion.run(day / "summary.xlsx", 2, "user-key", log=lambda _t: None, analyzer=analyzer)
            self.assertEqual(len(downloads), 4)            # references reused from cache on the 2nd run

    def test_reference_files_are_made_mono_and_bad_ones_skipped(self):
        import numpy as np
        import soundfile as sf
        import second_opinion

        class Model:
            def predict(self, paths, **_kwargs):
                if any("bad" in path for path in paths):
                    raise RuntimeError("Analysis was cancelled")      # what BirdNET does on a bad file
                return types.SimpleNamespace(to_structured_array=lambda: [
                    {"input": p, "start_time": 0.0, "end_time": 3.0, "species_name": "x", "confidence": 0.5}
                    for p in paths])

            def encode(self, paths, **_kwargs):
                return types.SimpleNamespace(to_structured_array=lambda: [
                    {"input": p, "start_time": 0.0, "end_time": 3.0, "embedding": [1.0, 0.0]} for p in paths])

        scores, _encoded = second_opinion._analyze(Model(), "x", ["good1.wav", "bad.wav", "good2.wav"],
                                                   log=lambda _text: None)
        self.assertEqual(sorted(scores), ["good1.wav", "good2.wav"])
        with tempfile.TemporaryDirectory() as temp:
            stereo = Path(temp) / "XC1.wav"
            sf.write(str(stereo), np.zeros((48000 * 2, 2), dtype="float32"), 48000)
            with patch.object(second_opinion, "CACHE", Path(temp) / "cache"):
                prepared = second_opinion._prepare(stereo)
            self.assertEqual(sf.info(str(prepared)).channels, 1)

    def test_xeno_canto_falls_back_to_english_name_when_genus_differs(self):
        import xeno_canto

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        def fake_urlopen(request, timeout):
            if "en%3A" not in request.full_url:                 # gen:Periparus sp:venustulus -> nothing
                return Response(b'{"numRecordings": "0", "recordings": []}')
            return Response(json.dumps({"numRecordings": "155", "recordings": [
                {"id": "7", "gen": "Pardaliparus", "sp": "venustulus", "q": "A", "file": "https://x/7"}]}).encode())

        with patch.object(xeno_canto.urllib.request, "urlopen", fake_urlopen):
            found, total = xeno_canto.search("Periparus venustulus", "user-key", common="Yellow-bellied Tit")
            page = xeno_canto.species_page("Periparus venustulus", "user-key", "Yellow-bellied Tit")
        self.assertEqual(([r["id"] for r in found], total), (["XC7"], 155))
        self.assertEqual(page, "https://xeno-canto.org/species/Pardaliparus-venustulus")

    def test_ebird_check_region_recent_and_hotspot(self):
        from datetime import date, timedelta
        import ebird
        from birdnet_gui import format_ebird

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        replies = {
            "ref/taxonomy/ebird": [{"sciName": "Phylloscopus inornatus", "comName": "Yellow-browed Warbler",
                                    "speciesCode": "yebwar3"},
                                   {"sciName": "Mareca strepera", "comName": "Gadwall", "speciesCode": "gadwal"}],
            "ref/hotspot/geo": [{"locId": "L2", "locName": "Far", "lat": 31.0, "lng": 115.0,
                                 "subnational1Code": "CN-42", "countryCode": "CN"},
                                {"locId": "L1", "locName": "Jiufeng NFP", "lat": 30.52, "lng": 114.46,
                                 "subnational1Code": "CN-42", "countryCode": "CN"}],
            "product/spplist/CN-42": ["yebwar3"],
            "ref/region/info/CN-42": {"result": "Hubei, China"},
            "data/obs/geo/recent": [{"speciesCode": "yebwar3", "obsDt": "2026-10-05 08:30",
                                     "locName": "Jiufeng NFP"}],
        }
        keys_sent = {}

        def fake_urlopen(request, timeout):
            path = request.full_url.split("/v2/")[1].split("?")[0]
            keys_sent[path] = request.get_header("X-ebirdapitoken")
            return Response(json.dumps(replies[path]).encode())

        with tempfile.TemporaryDirectory() as temp, \
                patch.object(ebird, "CACHE", Path(temp)), \
                patch.object(ebird.urllib.request, "urlopen", fake_urlopen):
            yesterday = date.today() - timedelta(days=1)
            warbler = ebird.check("Phylloscopus inornatus", 30.5165, 114.4453, yesterday, "user-key")
            duck = ebird.check("Mareca strepera", 30.5165, 114.4453, yesterday, "user-key")
            old = ebird.check("Phylloscopus inornatus", 30.5165, 114.4453, yesterday - timedelta(days=60), "user-key")
            unknown = ebird.check("Avis inventus", 30.5, 114.4, None, "user-key")
        self.assertEqual((warbler["in_region"], warbler["region_name"], warbler["hotspot"]["name"]),
                         (True, "Hubei", "Jiufeng NFP"))
        self.assertEqual(warbler["recent"], {"date": "2026-10-05 08:30", "where": "Jiufeng NFP"})
        self.assertIs(duck["in_region"], False)
        self.assertIn("⚠ never reported in Hubei", format_ebird(duck))
        self.assertIn("not checked", format_ebird(old))
        self.assertIn("not in the eBird taxonomy", format_ebird(unknown))
        self.assertIsNone(keys_sent["ref/taxonomy/ebird"])               # taxonomy needs no key
        self.assertEqual(keys_sent["data/obs/geo/recent"], "user-key")

    def test_settings_file_is_private(self):
        from app_settings import load_settings, save_settings
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sub" / "settings.json"
            self.assertEqual(load_settings(path), {})
            save_settings({"xeno_canto_key": "abc"}, path)
            save_settings({"other": 1}, path)
            self.assertEqual(load_settings(path), {"xeno_canto_key": "abc", "other": 1})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_app_data_lives_in_program_folder(self):
        import app_settings
        import ebird
        import paths
        import second_opinion
        import xeno_canto
        data = Path(__file__).resolve().parents[1] / "data"
        self.assertEqual(app_settings.SETTINGS_FILE, data / "settings.json")
        for cache in (ebird.CACHE, xeno_canto.CACHE, second_opinion.CACHE):
            self.assertEqual(cache.parent, data / "cache")
        self.assertEqual(Path(os.environ["BIRDNET_APP_DATA"]), paths.BIRDNET_MODELS)

    def test_old_library_data_is_moved_without_overwriting(self):
        import paths
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            old = {"settings": temp / "lib/AS/BirdNET-eBird/settings.json",
                   "cache": temp / "lib/Caches/BirdNET-eBird",
                   "birdnet": temp / "lib/AS/birdnet"}
            data = temp / "program/data"
            old["settings"].parent.mkdir(parents=True)
            old["settings"].write_text('{"ebird_key": "k", "last_summary": "old"}')
            (old["birdnet"] / "acoustic-models").mkdir(parents=True)
            (old["birdnet"] / "acoustic-models/model.onnx").write_text("model")
            (old["cache"] / "xeno-canto").mkdir(parents=True)
            (old["cache"] / "xeno-canto/XC1.mp3").write_text("old copy")
            (old["cache"] / "xeno-canto/XC2.mp3").write_text("two")
            (data / "cache/xeno-canto").mkdir(parents=True)
            (data / "cache/xeno-canto/XC1.mp3").write_text("kept")
            (data / "settings.json").write_text('{"last_summary": "new"}')
            os.utime(old["settings"], (1, 1))                  # ไฟล์เดิมเก่ากว่า -> ค่าใหม่ชนะ
            with patch.object(paths, "_OLD", old), patch.object(paths, "DATA", data), \
                    patch.object(paths, "SETTINGS_FILE", data / "settings.json"), \
                    patch.object(paths, "CACHE", data / "cache"), \
                    patch.object(paths, "BIRDNET_MODELS", data / "birdnet"):
                paths.migrate_old_locations()
                paths.migrate_old_locations()                   # รันซ้ำได้
            self.assertEqual(json.loads((data / "settings.json").read_text()),
                             {"ebird_key": "k", "last_summary": "new"})
            self.assertEqual((data / "settings.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual((data / "birdnet/acoustic-models/model.onnx").read_text(), "model")
            self.assertEqual((data / "cache/xeno-canto/XC1.mp3").read_text(), "kept")
            self.assertEqual((data / "cache/xeno-canto/XC2.mp3").read_text(), "two")
            self.assertFalse(old["settings"].exists() or old["birdnet"].exists())
            self.assertFalse(old["settings"].parent.exists())

    def test_previous_results_are_listed_newest_first(self):
        from review_store import list_results
        from birdnet_gui import result_label
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder, statuses in (("2026.10.01_1105_v3preview", ["Pending"]),
                                     ("2026.10.06_0928_v3preview", ["Approved", "Rejected", "Pending"]),
                                     ("2026.09.30_0700", ["Pending", "Pending"])):
                (root / folder).mkdir()
                pd.DataFrame([{"File": f"{n}.wav", "Source file": "REC-002.WAV", "Review status": s}
                              for n, s in enumerate(statuses)]).to_excel(root / folder / "summary.xlsx", index=False)
            (root / "broken").mkdir()
            (root / "broken" / "summary.xlsx").write_text("not a workbook")
            found = list_results(root)
        self.assertEqual([info["folder"] for info in found],
                         ["2026.10.06_0928_v3preview", "2026.10.01_1105_v3preview", "2026.09.30_0700"])
        self.assertEqual(result_label(found[0]), "2026-10-06 09:28  ·  REC-002.WAV  ·  3 clips, 2 reviewed")
        self.assertTrue(result_label(found[2]).endswith("BirdNET 2.4"))

    def test_ebird_and_macaulay_links(self):
        from birdnet_gui import species_links
        links = species_links("yebwar3", "CN-42")
        self.assertEqual(links["species"], "https://ebird.org/species/yebwar3")
        self.assertIn("taxonCode=yebwar3", links["photo"])
        self.assertIn("mediaType=photo", links["photo"])
        self.assertIn("mediaType=audio&regionCode=CN-42", links["audio"])
        self.assertNotIn("regionCode", species_links("yebwar3")["audio"])

    def test_xeno_canto_species_link(self):
        from birdnet_gui import xeno_canto_url
        self.assertEqual(xeno_canto_url("Phylloscopus inornatus"),
                         "https://xeno-canto.org/species/Phylloscopus-inornatus")
        self.assertEqual(xeno_canto_url(" parus  cinereus minor "),
                         "https://xeno-canto.org/species/Parus-cinereus")   # subspecies dropped
        for name in ("", None, "_Unknown", "Acrididae", "Meimuna opalifera2"):
            self.assertIsNone(xeno_canto_url(name))

    @unittest.skipUnless(hasattr(os.stat_result, "st_birthtime"), "needs file creation times")
    def test_consistent_file_times_give_recording_start(self):
        with tempfile.TemporaryDirectory() as temp:
            recorder = Path(temp) / "REC-002.WAV"
            make_wav(recorder, seconds=40)
            started = datetime(2026, 10, 6, 9, 28).timestamp()
            os.utime(recorder, (started, started))            # birth time follows an earlier mtime
            os.utime(recorder, (started + 40, started + 40))  # closed when the recording ended
            context = pipeline.recording_context_preview(recorder, use_meta=False)
            self.assertEqual((context["datetime"], context["has_time"]), (datetime(2026, 10, 6, 9, 28), True))
            copied = Path(temp) / "REC-003.WAV"                # new file: created == modified
            make_wav(copied, seconds=40)
            self.assertIsNone(pipeline.recording_context_preview(copied, use_meta=False)["datetime"])

    def test_compact_date_and_time_entries(self):
        from entry_formats import parse_date_entry, parse_time_entry
        self.assertEqual(parse_date_entry("20261006"), datetime(2026, 10, 6))
        self.assertEqual(parse_date_entry(" 2026-10-06 "), datetime(2026, 10, 6))
        for text, expected in (("0929", "09:29:00"), ("929", "09:29:00"),
                               ("092930", "09:29:30"), ("09:29", "09:29:00")):
            self.assertEqual(parse_time_entry(text).strftime("%H:%M:%S"), expected)
        for bad in ("2026106", "20261332", "2026/10/06"):
            with self.assertRaises(ValueError):
                parse_date_entry(bad)
        for bad in ("2575", "09-29", "12345"):
            with self.assertRaises(ValueError):
                parse_time_entry(bad)

    def test_habitat_rules(self):
        from habitat import fits_site, parse_site_habitats, species_habitat
        self.assertEqual(species_habitat("Mareca strepera"), "Wetland")        # Gadwall
        self.assertEqual(species_habitat("Unknown species", "Parus cinereus"), "Woodland")
        self.assertEqual(species_habitat("Not a bird"), "")
        self.assertIs(fits_site("Wetland", ("forest",)), False)
        self.assertIs(fits_site("Wetland", ("forest", "marsh")), True)
        self.assertIs(fits_site("Marine", ("marsh", "river")), False)
        self.assertIs(fits_site("Forest", ("sea",)), True)                     # land birds fit anywhere
        self.assertIsNone(fits_site("Wetland", ()))
        self.assertEqual(parse_site_habitats(" Marsh,forest "), ("forest", "marsh"))
        with self.assertRaises(ValueError):
            parse_site_habitats("forest,jungle")

    def test_site_habitat_drops_weak_water_bird_but_keeps_strong_flyover(self):
        class FakeV3:
            model_name = "fake v3"
            direct_prediction = True

            def detections(self, *_args):
                return [{"start_time": start, "end_time": start + 3, "common_name": common,
                         "scientific_name": sci, "confidence": conf,
                         "is_predicted_for_location_and_date": True,
                         "location_filter_available": True}
                        for start, common, sci, conf in (
                            (1, "Asian Tit", "Parus cinereus", 0.82),
                            (10, "Gadwall", "Mareca strepera", 0.52),
                            (20, "Mallard", "Anas platyrhynchos", 0.85))]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "REC-001.WAV"
            make_wav(source, seconds=30)
            rows, _ = pipeline.process_file(FakeV3(), source, root / "forest",
                                            **dict(PROCESS_ARGS, site_habitats=("forest",)))
            fits = {r["Species (common)"]: r["Fits site habitat"] for r in rows}
            self.assertEqual(fits, {"Asian Tit": "Yes", "Mallard": "No — verify by ear"})
            rows, _ = pipeline.process_file(FakeV3(), source, root / "forest+marsh",
                                            **dict(PROCESS_ARGS, site_habitats=("forest", "marsh")))
            self.assertEqual({r["Species (common)"] for r in rows}, {"Asian Tit", "Gadwall", "Mallard"})
            rows, _ = pipeline.process_file(FakeV3(), source, root / "unspecified", **PROCESS_ARGS)
            self.assertEqual({r["Fits site habitat"] for r in rows}, {""})

    def test_v3_matches_geo_by_common_name_when_taxonomy_differs(self):
        from v3_model import V3Analyzer

        analyzer = V3Analyzer.__new__(V3Analyzer)
        analyzer.geo_model = types.SimpleNamespace(predict=lambda *_args, **_kwargs:
            types.SimpleNamespace(species_list=["Thinornis dubius_Little Ringed Plover",
                                                "Hesperiphona vespertina_Evening Grosbeak"],
                                  species_probs=[0.4, 0.0]))
        analyzer.model = types.SimpleNamespace(predict=lambda *_args, **_kwargs:
            types.SimpleNamespace(to_structured_array=lambda: [
                {"start_time": 1, "end_time": 4, "confidence": 0.8,
                 "species_name": "Charadrius dubius_Little Ringed Plover"},
                {"start_time": 5, "end_time": 8, "confidence": 0.74,
                 "species_name": "Coccothraustes vespertinus_Evening Grosbeak"},
            ]))
        analyzer._geo_cache = {}
        plover, grosbeak = analyzer.detections("sample.wav", 1.5, 0.5, 30.5, 114.4,
                                               datetime(2026, 10, 6))
        self.assertTrue(plover["location_filter_available"])
        self.assertTrue(plover["is_predicted_for_location_and_date"])
        self.assertTrue(grosbeak["location_filter_available"])
        self.assertFalse(grosbeak["is_predicted_for_location_and_date"])

    def test_strong_out_of_range_candidate_kept_for_review_when_opted_in(self):
        used_options = {}

        class FakeRecording:
            def __init__(self, _analyzer, _path, **kwargs):
                used_options.update(kwargs)
                self.detections = [
                    {"start_time": 1, "end_time": 2, "common_name": "Cinereous Tit",
                     "scientific_name": "Parus cinereus", "confidence": 0.81,
                     "is_predicted_for_location_and_date": False},
                    {"start_time": 5, "end_time": 6, "common_name": "Unexpected weak bird",
                     "scientific_name": "Avis weakus", "confidence": 0.4,
                     "is_predicted_for_location_and_date": False},
                    {"start_time": 10, "end_time": 11, "common_name": "Light-vented Bulbul",
                     "scientific_name": "Pycnonotus sinensis", "confidence": 0.31,
                     "is_predicted_for_location_and_date": True},
                ]

            def analyze(self):
                pass

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "REC-001.WAV"
            make_wav(source, seconds=16)
            with patch.object(birdnetlib, "Recording", FakeRecording):
                rows, _ = pipeline.process_file(
                    None, source, root / "out",
                    **dict(PROCESS_ARGS, min_conf=0.25, out_of_range_min_conf=0.7)
                )
            self.assertEqual(used_options["overlap"], 1.5)
            self.assertTrue(used_options["return_all_detections"])
            self.assertEqual({r["Species (common)"] for r in rows},
                             {"Cinereous Tit", "Light-vented Bulbul"})
            flags = {r["Species (common)"]: r["Expected by location/date"] for r in rows}
            self.assertEqual(flags["Cinereous Tit"], "No — verify by ear")
            self.assertEqual(flags["Light-vented Bulbul"], "Yes")

    def test_changed_detection_settings_reanalyze_existing_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "REC-001.WAV"
            make_wav(source, seconds=8)
            with patch.object(birdnetlib, "Recording", fake_recording((1,))):
                rows, day = pipeline.process_file(None, source, root / "out", **PROCESS_ARGS)
                pipeline.write_summary(rows, day / "summary.xlsx", {str(source.resolve())})
                skipped, _ = pipeline.process_file(None, source, root / "out", **PROCESS_ARGS)
                refreshed, _ = pipeline.process_file(
                    None, source, root / "out", **dict(PROCESS_ARGS, overlap=0)
                )
            self.assertIsNone(skipped)
            self.assertIsNotNone(refreshed)
            self.assertNotEqual(rows[0]["Analysis settings"],
                                refreshed[0]["Analysis settings"])

    def test_changed_settings_do_not_replace_approved_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "REC-001.WAV"
            make_wav(source, seconds=8)
            with patch.object(birdnetlib, "Recording", fake_recording((1,))):
                rows, day = pipeline.process_file(None, source, root / "out", **PROCESS_ARGS)
                summary = day / "summary.xlsx"
                pipeline.write_summary(rows, summary, {str(source.resolve())})
                ready = save_review(summary, 2, status="Approved", common="Test bird",
                                    scientific="Avis testus", rating=3,
                                    expected_file=rows[0]["File"])
                refreshed, _ = pipeline.process_file(
                    None, source, root / "out", **dict(PROCESS_ARGS, overlap=0)
                )
            self.assertIsNone(refreshed)
            self.assertTrue((day / ready).is_file())
            self.assertEqual(load_reviews(summary)[0]["Review status"], "Approved")

    def test_missing_metadata_requires_manual_date_and_coordinates(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "REC-001.WAV"
            make_wav(path)
            with self.assertRaisesRegex(ValueError, "Recording date missing"):
                pipeline.require_recording_context([path], None, True, None, None)
            with self.assertRaisesRegex(ValueError, "Recording location missing"):
                pipeline.require_recording_context([path], datetime(2026, 7, 15), True, None, None)
            pipeline.require_recording_context([path], datetime(2026, 7, 15), True, 13.8, 100.5)

    def test_approval_creates_ready_copy_and_rejection_removes_it(self):
        with tempfile.TemporaryDirectory() as temp:
            day = Path(temp) / "2026.07.15_1200"
            original = day / "Unknown" / "clip.wav"
            original.parent.mkdir(parents=True)
            make_wav(original)
            summary = day / "summary.xlsx"
            pd.DataFrame([{"File": "Unknown/clip.wav", "Species (common)": "Unknown",
                           "Species (scientific)": "", "Max confidence": 0.71,
                           "Review status": "Pending"}]).to_excel(summary, index=False)
            rows = load_reviews(summary)
            self.assertEqual(rows[0]["_row"], 2)
            ready = save_review(summary, 2, status="Approved", common="Asian Koel",
                                scientific="Eudynamys scolopaceus", rating=3,
                                notes="Checked by ear", expected_file="Unknown/clip.wav")
            self.assertTrue((day / ready).is_file())
            self.assertEqual(load_reviews(summary)[0]["Review status"], "Approved")
            save_review(summary, 2, status="Rejected", common="Asian Koel",
                        scientific="Eudynamys scolopaceus", notes="Different bird",
                        expected_file="Unknown/clip.wav")
            self.assertFalse((day / ready).exists())
            self.assertEqual(load_reviews(summary)[0]["Review status"], "Rejected")

    def test_rerun_replaces_summary_and_superseded_generated_clip(self):
        with tempfile.TemporaryDirectory() as temp:
            day = Path(temp)
            first = day / "old.wav"
            first.write_bytes(b"old clip")
            source = str(day / "REC-001.WAV")
            first_row = {"File": first.name, "Generated sha256": pipeline.file_sha256(first),
                         "Source path": source, "Source ID": "abc123",
                         "Species (common)": "A", "Occurrence #": 1}
            summary = day / "summary.xlsx"
            pipeline.write_summary([first_row], summary, {source})
            second = day / "new.wav"
            second.write_bytes(b"new clip")
            second_row = dict(first_row, File=second.name,
                              **{"Generated sha256": pipeline.file_sha256(second)})
            pipeline.write_summary([second_row], summary, {source})
            rows = pd.read_excel(summary)
            self.assertEqual(list(rows["File"]), ["new.wav"])
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())

    def test_continuous_mode_exports_one_original_span(self):
        class FakeRecording:
            def __init__(self, *_args, **_kwargs):
                self.detections = [
                    {"start_time": start, "end_time": start + 1,
                     "common_name": "Test bird", "scientific_name": "Avis testus",
                     "confidence": 0.8}
                    for start in (1, 10)
                ]

            def analyze(self):
                pass

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "REC-001.WAV"
            make_wav(source, seconds=16)
            with patch.object(birdnetlib, "Recording", FakeRecording):
                rows, day = pipeline.process_file(
                    None, source, root / "output", lat_arg=13.8, lon_arg=100.5,
                    cfg_lat=None, cfg_lon=None, date_override=datetime(2026, 7, 15),
                    use_meta=False, min_conf=0.5, gap=5, lead=3, tail=3,
                    target_dbfs=-3, fmt="wav", make_mono=True, incl_alt=False,
                    place_arg=None, dt_regex=pipeline.FILENAME_DATETIME_REGEX,
                    keep_continuous=True,
                )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["Start offset (s)"], 1)
            self.assertEqual(rows[0]["End offset (s)"], 11)
            clip = day / rows[0]["File"]
            self.assertTrue(clip.is_file())
            with wave.open(str(clip), "rb") as audio:
                self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), 14, delta=0.1)

    def test_failed_second_clip_does_not_publish_first_clip(self):
        class FakeRecording:
            def __init__(self, *_args, **_kwargs):
                self.detections = [
                    {"start_time": start, "end_time": start + 1,
                     "common_name": "Test bird", "scientific_name": "Avis testus",
                     "confidence": 0.8}
                    for start in (1, 10)
                ]

            def analyze(self):
                pass

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "REC-001.WAV"
            make_wav(source, seconds=16)
            original_export = pipeline._export_clip
            calls = [0]

            def fail_second(*args, **kwargs):
                calls[0] += 1
                if calls[0] == 2:
                    raise RuntimeError("simulated export failure")
                return original_export(*args, **kwargs)

            with patch.object(birdnetlib, "Recording", FakeRecording), \
                    patch.object(pipeline, "_export_clip", side_effect=fail_second):
                with self.assertRaisesRegex(RuntimeError, "simulated export failure"):
                    pipeline.process_file(
                        None, source, root / "output", lat_arg=13.8, lon_arg=100.5,
                        cfg_lat=None, cfg_lon=None, date_override=datetime(2026, 7, 15),
                        use_meta=False, min_conf=0.5, gap=5, lead=3, tail=3,
                        target_dbfs=-3, fmt="wav", make_mono=True, incl_alt=False,
                        place_arg=None, dt_regex=pipeline.FILENAME_DATETIME_REGEX,
                    )
            published = [p for p in (root / "output").rglob("*.wav")
                         if ".staging" not in p.parts]
            self.assertEqual(published, [])

    def test_rerun_without_force_keeps_reviews_and_ready_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "REC-001.WAV"
            make_wav(source, seconds=40)
            with patch.object(birdnetlib, "Recording", fake_recording((1, 30))):
                rows, day = pipeline.process_file(None, source, root / "out", **PROCESS_ARGS)
                summary = day / "summary.xlsx"
                pipeline.write_summary(rows, summary, {str(source.resolve())})
                first, second = load_reviews(summary)
                ready = save_review(summary, first["_row"], status="Approved", common="Test bird",
                                    scientific="Avis testus", rating=4, expected_file=first["File"])
                save_review(summary, second["_row"], status="Rejected", common="Test bird",
                            scientific="Avis testus", expected_file=second["File"])
                (day / first["File"]).unlink()      # clip lost -> the source is analyzed again
                rows, _ = pipeline.process_file(None, source, root / "out", **PROCESS_ARGS)
                pipeline.write_summary(rows, summary, {str(source.resolve())})
                reviews = load_reviews(summary)
                self.assertEqual([r["Review status"] for r in reviews], ["Approved", "Rejected"])
                self.assertEqual(reviews[0]["Quality rating"], 4)
                self.assertTrue((day / ready).is_file())

                # --force keeps its documented meaning: regenerate and reset reviews
                rows, _ = pipeline.process_file(None, source, root / "out", force=True, **PROCESS_ARGS)
                pipeline.write_summary(rows, summary, {str(source.resolve())}, keep_reviews=False)
                self.assertEqual({r["Review status"] for r in load_reviews(summary)}, {"Pending"})
                self.assertFalse((day / ready).exists())

    def test_deleting_rejected_clip_does_not_trigger_reanalysis(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "REC-001.WAV"
            make_wav(source, seconds=8)
            with patch.object(birdnetlib, "Recording", fake_recording((1,))):
                rows, day = pipeline.process_file(None, source, root / "out", **PROCESS_ARGS)
                summary = day / "summary.xlsx"
                pipeline.write_summary(rows, summary, {str(source.resolve())})
                row = load_reviews(summary)[0]
                save_review(summary, row["_row"], status="Rejected", common="Test bird",
                            scientific="Avis testus", expected_file=row["File"])
                (day / row["File"]).unlink()
                rows, _ = pipeline.process_file(None, source, root / "out", **PROCESS_ARGS)
            self.assertIsNone(rows)   # skipped as already processed

    def test_interrupted_batch_can_be_resumed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recordings = root / "recordings"
            recordings.mkdir()
            make_wav(recordings / "REC-001.WAV", seconds=8)
            make_wav(recordings / "REC-002.WAV", seconds=9)
            argv = ["field_audio_to_ebird.py", str(recordings), "-o", str(root / "out"),
                    "--model", "2.4",
                    "--date", "20260715", "--same-date-for-all", "--coords", "13.8,100.5",
                    "--start-time", "0600", "--no-use-metadata", "--no-spectrogram"]
            summary = root / "out" / "2026.07.15_0600" / "summary.xlsx"
            analyzer = types.ModuleType("birdnetlib.analyzer")
            analyzer.Analyzer = lambda: None

            def run(recording_class):
                output = io.StringIO()
                with patch.object(sys, "argv", argv), \
                        patch.dict(sys.modules, {"birdnetlib.analyzer": analyzer}), \
                        patch.object(birdnetlib, "Recording", recording_class), \
                        contextlib.redirect_stdout(output):
                    pipeline.main()
                return output.getvalue()

            with self.assertRaises(SystemExit) as stopped:
                run(fake_recording((1,), interrupt_on="REC-002.WAV"))
            self.assertEqual(stopped.exception.code, 130)
            self.assertEqual(set(pd.read_excel(summary)["Source file"]), {"REC-001.WAV"})

            output = run(fake_recording((1,)))
            self.assertIn("skip: already processed", output)
            self.assertEqual(set(pd.read_excel(summary)["Source file"]),
                             {"REC-001.WAV", "REC-002.WAV"})
            self.assertIn(f"summary already up to date -> {summary}", run(fake_recording((1,))))

    def test_leftover_clips_from_interrupted_run_are_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "REC-001.WAV"
            make_wav(source, seconds=8)
            with patch.object(birdnetlib, "Recording", fake_recording((1,))):
                pipeline.process_file(None, source, root / "out", **PROCESS_ARGS)
                # stopped before summary.xlsx was written; the identical clip is ours to replace
                rows, _ = pipeline.process_file(None, source, root / "out", **PROCESS_ARGS)
            self.assertEqual(len(rows), 1)

    def test_force_redo_drops_spectrogram_of_changed_clip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "REC-001.WAV"
            make_wav(source, seconds=20)
            with patch.object(birdnetlib, "Recording", fake_recording((1,))):
                def redo(**changes):
                    rows, day = pipeline.process_file(None, source, root / "out", force=True,
                                                      **dict(PROCESS_ARGS, **changes))
                    pipeline.write_summary(rows, day / "summary.xlsx", {str(source.resolve())},
                                           keep_reviews=False)
                    return rows, day

                rows, day = redo()
                png = (day / rows[0]["File"]).with_suffix(".png")
                png.write_bytes(b"spectrogram")
                redo()
                self.assertTrue(png.exists())       # same clip -> spectrogram still valid
                rows, _ = redo(tail=10)
            self.assertEqual((day / rows[0]["File"]).with_suffix(".png"), png)
            self.assertFalse(png.exists())          # regenerated by gen_spectrograms instead

    def test_file_timestamp_fallback_uses_recording_start(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "REC-001.WAV"
            make_wav(source, seconds=120)
            closed = datetime(2026, 7, 15, 6, 32).timestamp()   # recorder closes the file at the end
            os.utime(source, (closed, closed))
            with patch.object(birdnetlib, "Recording", fake_recording((1,))):
                _rows, day = pipeline.process_file(None, source, root / "out", **PROCESS_ARGS)
            self.assertEqual(day.name, "2026.07.15_0630")

    def test_timestamps_with_timezone_become_local_time(self):
        self.addCleanup(time.tzset)   # runs after patch.dict restores TZ
        with patch.dict(os.environ, {"TZ": "Asia/Bangkok"}):
            time.tzset()
            self.assertEqual(pipeline._parse_meta_datetime("2026-07-14T23:30:00.000000Z"),
                             datetime(2026, 7, 15, 6, 30))
            self.assertEqual(pipeline._parse_meta_datetime("2026-07-15 06:30:00"),
                             datetime(2026, 7, 15, 6, 30))

    def test_occurrences_sort_as_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            summary = Path(temp) / "summary.xlsx"
            rows = [{"File": f"{n}.wav", "Species (common)": "A", "Occurrence #": n,
                     "Source ID": "abc", "Source file": "REC.WAV"} for n in (10, 2, 1)]
            pipeline.write_summary(rows, summary)
            self.assertEqual(list(pd.read_excel(summary)["Occurrence #"]), [1, 2, 10])

    def test_gps_and_map_link_parsing(self):
        self.assertAlmostEqual(pipeline._gps_exif("12,48,30N"), 12 + 48 / 60 + 30 / 3600)
        self.assertAlmostEqual(pipeline._gps_exif("99,37.2W"), -99.62)
        link = ("https://www.google.com/maps/place/X/@13.80,100.50,15z/data=!3m1!4b1"
                "!4m6!3m5!1s0x0:0x0!8m2!3d13.8119502!4d100.553166")
        self.assertEqual(pipeline.parse_coords(link), (13.8119502, 100.553166))


if __name__ == "__main__":
    unittest.main()
