"""ASSAY: a harness that grounds an agent's world model by checked prediction.

The agent names parts of the world as addressable states, predicts what each
action will do to them, and code grades every outcome against the world's own
response. The modules, one concept each:

    world      the registry (what may be done) and the adapter (how it is done)
    journal    the record: one event per action, hash-chained, audited
    grammar    a prediction as a sequence of outcomes
    states     addressable states: named readings of the observation
    grade      each outcome held, missed, invalid or ungradable, with the actual
    sandbox    the one place agent-written code runs
    daemon     holds the world session; the gate; the only writer of the journal
    meters     pure functions of the record
    modules    advisories: reasoning in code over the record
    show       what the agent reads: receipt, status, view
    diagnose   the record read against the five layers of reasoning
    analysis   `assay python`: offline thinking over the record
    grid       the frame-world extra, loaded only for grid observations
    cli        the command line

The kernel makes no model calls: every grade, refusal and meter is code.
"""

from __future__ import annotations

__version__ = "2.0.0"


class AssayError(Exception):
    """A refusal: a code, one line of message and, when there is one, the
    next step. Everything refused before the world is stepped costs nothing."""

    def __init__(self, message: str, *, code: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint

    def text(self) -> str:
        """The refusal as it is printed: `ERROR | CODE | message`, then the
        next step."""
        head, _, rest = self.message.partition("\n")
        lines = [f"ERROR | {self.code} | {head}"]
        if self.hint:
            lines.append(f"NEXT | {self.hint}")
        if rest:
            lines.append(rest)
        return "\n".join(lines)

    def to_json(self) -> dict[str, str | None]:
        return {"code": self.code, "message": self.message, "hint": self.hint}
