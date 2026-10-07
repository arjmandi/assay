"""Prompt templates: `{{NAME}}` placeholders filled from the job, nothing
else touched. An unfilled placeholder is an error, a value the template does
not use is fine.

The names a template may use: WORLD, LEVELS, BUDGET, SEED, MODEL, JOB,
RUN_DIR, REGISTRY, CONSTITUTION, CONSTITUTION_NAME (the file name alone),
ASSAY (the launcher), ADAPTER, REPO (the checkout the launcher belongs to),
STATE_DIR (the runner's state directory, where every other run of the design
lives). Nothing world-specific beyond the level count is ever inserted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from . import EvalError

PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")


def render(template: str, values: Mapping[str, str]) -> str:
    unknown = sorted(set(PLACEHOLDER.findall(template)) - set(values))
    if unknown:
        raise EvalError(
            "unfilled placeholder(s) in the prompt template: "
            + ", ".join("{{" + name + "}}" for name in unknown)
        )
    return PLACEHOLDER.sub(lambda match: values[match.group(1)], template)


def render_file(template: Path, values: Mapping[str, str]) -> str:
    try:
        text = template.read_text()
    except FileNotFoundError:
        raise EvalError(f"prompt template {template} does not exist") from None
    try:
        return render(text, values)
    except EvalError as error:
        raise EvalError(f"{template}: {error}") from None
