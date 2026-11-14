# Databricks notebook source
# MAGIC %pip install -q anthropic shapely

# COMMAND ----------

dbutils.library.restartPython()  # noqa: F821

# COMMAND ----------

# Verifies that serverless compute can read the Claude key from the secret scope and
# complete one model call. Reports booleans and model metadata only; the key is never
# printed, returned or written anywhere.
import json

import anthropic

out = {}
try:
    key = dbutils.secrets.get(scope="lantern", key="anthropic_api_key")  # noqa: F821
    out["secret_read"] = bool(key)
except Exception as exc:  # noqa: BLE001
    out["secret_read"] = False
    out["secret_error"] = type(exc).__name__
    key = None

def _attempt(k, label):
    res = {}
    try:
        client = anthropic.Anthropic(api_key=k, max_retries=1, timeout=60.0)
        r = client.beta.messages.create(
            model="claude-opus-5-5",
            max_tokens=1000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        )
        text = next((b.text for b in r.content if b.type == "text"), "")
        res.update(ok=True, model=r.model, stop_reason=r.stop_reason, reply=text.strip()[:40],
                   usage={"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens})
    except Exception as exc:  # noqa: BLE001
        cause = exc.__cause__
        res.update(ok=False, error=type(exc).__name__,
                   cause=type(cause).__name__ if cause else None,
                   # Header-format errors mention only the header name, never its value.
                   cause_kind=("illegal_header" if cause and "header" in str(cause).lower() else
                               "tls" if cause and ("ssl" in str(cause).lower() or "certificate" in str(cause).lower()) else
                               "other"))
    out[label] = res
    return res.get("ok")


if key:
    import os

    import re

    out["key_has_control_chars"] = any(ord(c) < 32 or ord(c) == 127 for c in key)
    out["key_has_non_ascii"] = any(ord(c) > 126 for c in key)
    out["key_starts_with_expected_prefix"] = key.startswith("sk-ant-")
    m = re.search(r"sk-ant-[A-Za-z0-9_\-]+", key)
    out["key_pattern_found_inside"] = bool(m)
    out["extra_chars_around_key"] = (len(key) - len(m.group(0))) if m else None
    if m:
        _attempt(m.group(0), "call_with_extracted_key")

import shapely  # noqa: E402

out["anthropic_sdk"] = anthropic.__version__
out["shapely"] = shapely.__version__
dbutils.notebook.exit(json.dumps(out))  # noqa: F821
