"""Keep the typed runtime Problem vocabulary equal to the published OpenAPI enum."""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import yaml

from easysynq_api.problems import ProblemCode

ROOT = Path(__file__).resolve().parents[4]
OPENAPI = ROOT / "packages/contracts/openapi.yaml"


def test_problem_schema_equals_runtime_problem_code_vocabulary() -> None:
    schema = yaml.safe_load(OPENAPI.read_text())
    published = set(schema["components"]["schemas"]["Problem"]["properties"]["code"]["enum"])
    runtime = set(get_args(ProblemCode.__value__))

    assert published == runtime, (
        f"OpenAPI is missing {sorted(runtime - published)}; "
        f"OpenAPI-only codes are {sorted(published - runtime)}"
    )
