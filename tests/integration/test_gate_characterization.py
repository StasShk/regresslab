"""A small, real-HTTP smoke run of the manual statistical-gate experiment."""

import csv
import json
import os
import subprocess
import sys
from pathlib import Path


def test_characterization_generates_gate_reports(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "experiment"
    env = os.environ.copy()
    # Local services must not be routed through developer or CI HTTP proxies.
    for key in list(env):
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}:
            del env[key]
    env["NO_PROXY"] = "127.0.0.1,localhost"
    command = [
        sys.executable,
        "-m",
        "experiments.gate_characterization",
        "--output-dir",
        str(output),
        "--trials",
        "1",
        "--pairs",
        "8",
        "--requests",
        "2",
        "--delay-ms",
        "1",
        "--noise",
        "0",
        "--effects",
        "0,25",
    ]
    completed = subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True, timeout=40, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    with (output / "summary.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2  # A/A and A/B
    for row in rows:
        assert row["pairs"] == "8"
        assert (
            sum(int(row[f"{status}_count"]) for status in ("pass", "regression", "inconclusive"))
            == 1
        )
    for effect in (0, 25):
        report = output / f"noise-0/effect-{effect}/trial-001/results/gate-8.json"
        result = json.loads(report.read_text(encoding="utf-8"))
        assert result["gate"]["pair_count"] == 8
        assert result["gate"]["status"] in ("PASS", "REGRESSION", "INCONCLUSIVE")
