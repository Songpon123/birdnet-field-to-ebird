import contextlib
import io
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
                    "--date", "2026-07-15", "--same-date-for-all", "--coords", "13.8,100.5",
                    "--start-time", "06:00", "--no-use-metadata", "--no-spectrogram"]
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
