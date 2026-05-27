"""
Service layer for the PSUR Input Standardizer.

Thin wrapper around pipeline.run() that provides a clean programmatic API
for callers that don't use the CLI (e.g. integration tests, orchestrators).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from .pipeline import run as _pipeline_run

logger = logging.getLogger(__name__)


class StandardizerResult:
    __slots__ = ("ready", "output_dir", "run_id")

    def __init__(self, ready: bool, output_dir: str, run_id: str) -> None:
        self.ready = ready
        self.output_dir = output_dir
        self.run_id = run_id

    def __repr__(self) -> str:
        status = "READY" if self.ready else "NOT READY"
        return f"StandardizerResult(status={status}, output_dir={self.output_dir!r})"


def standardize(
    input_dir: str,
    output_dir: str,
    use_llm: bool = True,
    run_id: Optional[str] = None,
) -> StandardizerResult:
    """
    Run the full standardizer pipeline.

    Args:
        input_dir:  Directory containing raw source files.
        output_dir: Directory where the canonical package will be written.
        use_llm:    Whether to enable LLM-assisted classification and extraction.
        run_id:     Optional run identifier for reproducibility tracking.

    Returns:
        StandardizerResult with .ready, .output_dir, .run_id
    """
    if not os.path.isdir(input_dir):
        raise ValueError(f"input_dir does not exist or is not a directory: {input_dir!r}")

    os.makedirs(output_dir, exist_ok=True)

    import uuid
    effective_run_id = run_id or str(uuid.uuid4())[:8]

    ready, out_dir = _pipeline_run(
        input_dir=input_dir,
        output_dir=output_dir,
        use_llm=use_llm,
        run_id=effective_run_id,
    )

    return StandardizerResult(ready=ready, output_dir=out_dir, run_id=effective_run_id)
