"""Opt-in, loopback-only characterization of RegressLab's existing statistical gate.

Run from the repository root: uv run python -m experiments.gate_characterization ...
Uses only the existing RegressLab dependencies and the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from time import sleep

import yaml

from regresslab.cli import main as regresslab_main

STATUSES = ("PASS", "REGRESSION", "INCONCLUSIVE")


@contextmanager
def delayed_server(delay_ms: float, noise_pct: float, seed: int) -> Iterator[str]:
    """Serve GET /health on loopback with seeded uniform per-request jitter.

    An independent RNG is used for each side; OS scheduling and network timing
    remain real and are not controlled by the seed.
    """
    rng = random.Random(seed)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True

        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_error(404)
                return
            jitter = rng.uniform(-noise_pct, noise_pct) / 100.0
            sleep(max(0.0, delay_ms * (1.0 + jitter) / 1000.0))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            try:
                self.wfile.write(b"ok")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/health"
        finally:
            server.shutdown()
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError("Test server did not stop")


def _cli(*args: str, allowed: tuple[int, ...]) -> int:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = regresslab_main(list(args))
    if code not in allowed:
        raise RuntimeError(
            f"regresslab {' '.join(args[:2])} returned {code}: "
            f"{stderr.getvalue() or stdout.getvalue()}"
        )
    return code


def _numbers(value: str) -> list[float]:
    try:
        values = [float(part.strip()) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated numbers") from exc
    if not values or any(not 0 <= x <= 100 for x in values):
        raise argparse.ArgumentTypeError("values must be between 0 and 100")
    if len(set(values)) != len(values):
        raise argparse.ArgumentTypeError("values must be unique")
    return values


def _pair_counts(value: str) -> list[int]:
    try:
        values = [int(part.strip()) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated positive integers") from exc
    if not values or sorted(set(values)) != values or values[0] < 1:
        raise argparse.ArgumentTypeError("pair counts must be positive, unique and increasing")
    return values


def _write_config(path: Path, url: str, requests: int) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "name": "controlled-health",
                "url": url,
                "requests": requests,
                "concurrency": 1,
                "warmup_requests": 3,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _evaluate_prefixes(directory: Path, counts: list[int]) -> dict[int, str]:
    full = yaml.safe_load((directory / "pairs.yaml").read_text(encoding="utf-8"))
    pairs = full["pairs"]
    answers: dict[int, str] = {}
    for n in counts:
        subset = directory / f"pairs-{n}.yaml"
        subset.write_text(yaml.safe_dump({"pairs": pairs[:n]}, sort_keys=False), encoding="utf-8")
        output = directory / f"gate-{n}.json"
        code = _cli(
            "compare-series",
            str(subset),
            "--gate",
            "--output",
            str(output),
            allowed=(0, 1, 3),
        )
        report = json.loads(output.read_text(encoding="utf-8"))
        status = report["gate"]["status"]
        expected_code = {"PASS": 0, "REGRESSION": 1, "INCONCLUSIVE": 3}.get(status)
        if code != expected_code:
            raise RuntimeError(f"Unexpected gate status in {output}")
        answers[n] = status
    return answers


def run_characterization(
    output_dir: Path,
    *,
    trials: int,
    pairs: list[int],
    requests: int,
    delay_ms: float,
    noise_levels: list[float],
    effects: list[float],
    seed: int,
) -> list[dict[str, object]]:
    """Collect independent trials and evaluate each at several nested sample sizes."""
    if output_dir.exists():
        raise FileExistsError(f"Output directory exists: {output_dir}")
    output_dir.mkdir(parents=True)
    settings = {
        "trials": trials,
        "pairs": pairs,
        "requests_per_run": requests,
        "baseline_delay_ms": delay_ms,
        "noise_pct": noise_levels,
        "candidate_increase_pct": effects,
        "seed": seed,
        "p95_threshold_pct": 10,
        "throughput_threshold_pct": 10,
        "error_rate_threshold_points": 1,
        "confidence": 0.95,
        "min_pairs": 8,
    }
    (output_dir / "settings.json").write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )
    counts: dict[tuple[float, float, int], Counter[str]] = {}
    details: list[dict[str, object]] = []
    conditions = [
        (noise_index, noise, effect_index, effect)
        for noise_index, noise in enumerate(noise_levels)
        for effect_index, effect in enumerate(effects)
    ]
    for trial in range(trials):
        order = conditions.copy()
        random.Random(seed + trial).shuffle(order)
        for noise_index, noise, effect_index, effect in order:
            directory = (
                output_dir / f"noise-{noise:g}" / f"effect-{effect:g}" / f"trial-{trial + 1:03d}"
            )
            directory.mkdir(parents=True)
            # Independent seeded streams for the two servers and each trial.
            run_seed = seed + (noise_index * len(effects) + effect_index) * trials * 2 + trial * 2
            with (
                delayed_server(delay_ms, noise, run_seed) as baseline_url,
                delayed_server(
                    delay_ms * (1 + effect / 100), noise / (1 + effect / 100), run_seed + 1
                ) as candidate_url,
            ):
                baseline = directory / "baseline.yaml"
                candidate = directory / "candidate.yaml"
                _write_config(baseline, baseline_url, requests)
                _write_config(candidate, candidate_url, requests)
                _cli(
                    "run-series",
                    str(baseline),
                    str(candidate),
                    "--repeats",
                    str(pairs[-1]),
                    "--output-dir",
                    str(directory / "results"),
                    allowed=(0,),
                )
            results = directory / "results"
            statuses = _evaluate_prefixes(results, pairs)
            for n, status in statuses.items():
                counts.setdefault((noise, effect, n), Counter())[status] += 1
                details.append(
                    {
                        "noise_pct": noise,
                        "effect_pct": effect,
                        "trial": trial + 1,
                        "pairs": n,
                        "status": status,
                        "seed": run_seed,
                    }
                )
            print(
                f"noise={noise:g}% effect={effect:g}% trial={trial + 1}/{trials}: "
                + ", ".join(f"{n}={statuses[n]}" for n in pairs),
                flush=True,
            )
    summary: list[dict[str, object]] = []
    for (noise, effect, n), c in sorted(counts.items()):
        row: dict[str, object] = {
            "noise_pct": noise,
            "effect_pct": effect,
            "pairs": n,
            "trials": trials,
        }
        for status in STATUSES:
            row[status.lower() + "_count"] = c[status]
            row[status.lower() + "_rate"] = c[status] / trials
        summary.append(row)
    for filename, rows in (("summary.csv", summary), ("trials.csv", details)):
        with (output_dir / filename).open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New directory for raw measurements and reports",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=3,
        help="Independent complete series per condition (default: 3)",
    )
    parser.add_argument(
        "--pairs",
        type=_pair_counts,
        default=[8, 12, 20, 40],
        help="Series sizes (default: 8,12,20,40)",
    )
    parser.add_argument(
        "--requests", type=int, default=20, help="HTTP requests per run (default: 20)"
    )
    parser.add_argument(
        "--delay-ms",
        type=float,
        default=10.0,
        help="Baseline delay in milliseconds (default: 10)",
    )
    parser.add_argument(
        "--noise",
        type=_numbers,
        default=[0.0, 10.0],
        help="Uniform jitter ± percent (default: 0,10)",
    )
    parser.add_argument(
        "--effects",
        type=_numbers,
        default=[0.0, 5.0, 10.0, 25.0],
        help="Candidate delay increase percent (default: 0,5,10,25)",
    )
    parser.add_argument("--seed", type=int, default=12345)
    args = parser.parse_args(argv)
    if args.trials < 1 or args.requests < 1 or not 0 < args.delay_ms <= 10000:
        parser.error("trials and requests must be positive; delay-ms must be in (0, 10000]")
    run_characterization(
        args.output_dir,
        trials=args.trials,
        pairs=args.pairs,
        requests=args.requests,
        delay_ms=args.delay_ms,
        noise_levels=args.noise,
        effects=args.effects,
        seed=args.seed,
    )
    print(f"Summary: {args.output_dir / 'summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
