"""GET requests must not silently accept a body."""

import pytest
from pydantic import ValidationError

from regresslab.core.config import ExperimentSpec


def test_get_rejects_json_body() -> None:
    with pytest.raises(ValidationError, match="json_body is not supported for GET"):
        ExperimentSpec.model_validate(
            {
                "name": "get-with-body",
                "url": "http://localhost:8000/health",
                "requests": 1,
                "json_body": {"value": 1},
            }
        )
