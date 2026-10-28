from regresslab.core.models import Measurement


def synthetic_measurement(
    requests: int, p95: float | None, throughput: float | None, error_rate: float | None
) -> Measurement:
    """Make internally consistent inputs for CLI tests (not actual benchmark data)."""
    if error_rate is None:
        raise ValueError("Fixture needs a defined error rate")
    failed = round(requests * error_rate)
    if abs(failed / requests - error_rate) > 1e-10:
        raise ValueError("Fixture error rate cannot be represented by request count")
    success = requests - failed
    if success == 0:
        if p95 is not None or throughput != 0:
            raise ValueError("No successful requests must have no p95 and zero throughput")
        latencies = ()
        elapsed = 1.0
    else:
        if p95 is None or throughput is None or throughput <= 0:
            raise ValueError("Successful fixture requests need p95 and positive throughput")
        latencies = (float(p95),) * success
        elapsed = success / throughput
    return Measurement(
        latencies_ms=latencies,
        request_count=requests,
        successful_requests=success,
        failed_requests=failed,
        elapsed_seconds=elapsed,
    )
