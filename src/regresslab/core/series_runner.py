"""Collect matched HTTP benchmark pairs for offline series comparison."""

from pathlib import Path

import yaml

from regresslab.core import http_runner
from regresslab.core.auth import bearer_headers
from regresslab.core.config import ExperimentSpec
from regresslab.core.metrics import calculate_metrics
from regresslab.core.saved_results import (
    _safe_experiment_document,
    save_result,
    validate_experiment_compatibility,
)
from regresslab.core.series_manifest import SavedResultPair, SeriesManifest


async def run_series(
    baseline: ExperimentSpec,
    candidate: ExperimentSpec,
    *,
    repeats: int,
    output_dir: Path,
) -> Path:
    """Save alternating, sequential pairs and return the completed manifest path.

    The output directory must not exist. Completed measurements survive errors
    and cancellation; pairs.yaml is published only after all pairs finish.
    Request failures remain measurement data, just as in run_experiment.
    """
    if type(repeats) is not int:
        raise TypeError("repeats must be a positive integer")
    if repeats < 1:
        raise ValueError("repeats must be a positive integer")
    validate_experiment_compatibility(baseline, candidate)
    _safe_experiment_document(baseline)
    _safe_experiment_document(candidate)
    # Check both credentials before collecting even the first baseline.
    bearer_headers(baseline)
    bearer_headers(candidate)
    # This is also the exclusive reservation: even an empty directory is rejected.
    output_dir.mkdir(parents=True, exist_ok=False)
    pairs: list[SavedResultPair] = []
    for index in range(1, repeats + 1):
        pair_id = f"pair-{index:04d}"
        pair = SavedResultPair(
            id=pair_id,
            baseline=f"{pair_id}-baseline.json",
            candidate=f"{pair_id}-candidate.json",
        )
        legs = ((baseline, pair.baseline), (candidate, pair.candidate))
        if index % 2 == 0:
            legs = (legs[1], legs[0])
        for spec, filename in legs:
            measurement = await http_runner.run_experiment(spec)
            metrics = calculate_metrics(measurement)
            target = output_dir / filename
            temporary = target.with_suffix(".json.tmp")
            save_result(temporary, spec, measurement, metrics)
            temporary.replace(target)
        pairs.append(pair)

    manifest = SeriesManifest(pairs=tuple(pairs))
    manifest_path = output_dir / "pairs.yaml"
    temporary_manifest = output_dir / "pairs.yaml.tmp"
    temporary_manifest.write_text(
        yaml.safe_dump(manifest.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )
    temporary_manifest.replace(manifest_path)
    return manifest_path
