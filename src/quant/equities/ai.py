"""Optional AI interpreter. It proposes a schema; the UI requires approval to run it."""
from __future__ import annotations
import json
import os
from .schema import Hypothesis

SYSTEM = """Translate a user's U.S. equities research idea into the supplied schema.
Only use the supported reversal, momentum, or consecutive-down-session condition. Never claim an expected result,
invent unsupported fundamentals, or produce executable code. Use the provided defaults
when the idea is ambiguous and let the user review the resulting hypothesis."""


def interpret(text: str, defaults: dict) -> Hypothesis:
    """Call Structured Outputs, then validate again at the deterministic boundary."""
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Set OPENAI_API_KEY to use AI interpretation, or edit the form directly.")
    from openai import OpenAI
    schema = Hypothesis.model_json_schema()
    prompt = f"Idea: {text}\nDefaults: {json.dumps(defaults, default=str)}"
    response = OpenAI().responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        instructions=SYSTEM,
        input=prompt,
        text={"format": {"type": "json_schema", "name": "equity_hypothesis", "strict": True, "schema": schema}},
    )
    return Hypothesis.model_validate_json(response.output_text)
