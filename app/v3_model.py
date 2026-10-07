"""BirdNET 3.0 preview acoustic and location/date model adapter."""

import paths  # noqa: F401  ตั้ง BIRDNET_APP_DATA -> data/birdnet ก่อน import birdnet


GEO_MIN_CONFIDENCE = 0.03
EBIRD_COMMON_NAMES = {
    "Eophona migratoria": "Yellow-billed Grosbeak",
}


def birdnet_week(recorded_at):
    """BirdNET geo models use four weeks per month, numbered 1 through 48."""
    return (recorded_at.month - 1) * 4 + min(4, (recorded_at.day - 1) // 7 + 1)


class V3Analyzer:
    model_name = "BirdNET 3.0 preview ONNX fp16 + geo 3.0.4 (0.03)"
    direct_prediction = True

    def __init__(self):
        try:
            import birdnet
        except ImportError as exc:
            raise RuntimeError(
                "BirdNET 3.0 preview is unavailable. Install birdnet and onnxruntime "
                "in the app's Python environment."
            ) from exc
        self.model = birdnet.load("acoustic", "3.0", "onnx", precision="fp16")
        self.geo_model = birdnet.load("geo", "3.0", "onnx", precision="fp16")
        self._geo_cache = {}

    def _geo_probabilities(self, latitude, longitude, recorded_at):
        """(by scientific name, by lower-case common name) for this place and week."""
        key = (latitude, longitude, birdnet_week(recorded_at))
        if key not in self._geo_cache:
            prediction = self.geo_model.predict(
                latitude, longitude, week=key[2], min_confidence=0.0
            )
            by_scientific, by_common = {}, {}
            for label, probability in zip(prediction.species_list, prediction.species_probs):
                scientific, _, common = str(label).partition("_")
                by_scientific[scientific] = float(probability)
                if common:
                    by_common.setdefault(common.casefold(), (float(probability), scientific))
            self._geo_cache[key] = (by_scientific, by_common)
        return self._geo_cache[key]

    def detections(self, audio_path, overlap, confidence_floor,
                   latitude, longitude, recorded_at):
        by_scientific, by_common = self._geo_probabilities(latitude, longitude, recorded_at)
        result = self.model.predict(
            audio_path,
            top_k=10,
            n_workers=1,
            overlap_duration_s=overlap,
            default_confidence_threshold=confidence_floor,
            show_stats=None,
        )
        detections = []
        for row in result.to_structured_array():
            species = str(row["species_name"])
            scientific, separator, common = species.partition("_")
            if not separator:
                common = scientific
            # The two models use different taxonomies for ~900 labels (e.g. Charadrius vs
            # Thinornis dubius), so fall back to the common name before giving up.
            geo_probability, geo_scientific = by_scientific.get(scientific), scientific
            if geo_probability is None:
                geo_probability, geo_scientific = by_common.get(common.casefold(), (None, None))
            common = EBIRD_COMMON_NAMES.get(scientific, common)
            detections.append({
                "start_time": float(row["start_time"]),
                "end_time": float(row["end_time"]),
                "common_name": common,
                "scientific_name": scientific,
                "confidence": float(row["confidence"]),
                "is_predicted_for_location_and_date": (
                    geo_probability is None or geo_probability >= GEO_MIN_CONFIDENCE
                ),
                "location_filter_available": geo_probability is not None,
                "geo_scientific_name": geo_scientific,   # other taxonomy's name, for AVONET lookup
            })
        return detections
