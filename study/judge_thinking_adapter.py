"""Local provider adapter so the correctness Judge can run against DeepSeek.

Why this exists
---------------
`tests/llm/utils/classifiers.py` builds autoevals' `LLMClassifier` with
`use_cot=True`. autoevals always forces one named tool call for the Judge:

    tool_choice={"type": "function", "function": {"name": "select_choice"}}

DeepSeek's thinking mode rejects any request that pins a specific function:

    HTTP 400 Thinking mode does not support this tool_choice

so `evaluate_correctness()` crashes before any score is recorded and the
whole eval is reported as a failure even though the investigation finished.

What this adapter changes
-------------------------
It adds the provider's documented per-request switch
`thinking={"type": "disabled"}` to autoevals' Judge request only (autoevals
builds that request through `OpenAILLMClassifier._request_args`). The Judge
prompt, the model, the expected output, the expected tool schema and the
response parsing are untouched; only the thinking mode of the Judge call
differs. The investigation model still runs in its normal thinking mode
because it goes through litellm, not through this path.

Loaded as a pytest plugin by `study/local-case.sh eval`, which puts `study/` on
PYTHONPATH and passes `-p judge_thinking_adapter`. Nothing in the repository's
eval harness is modified; to run the eval without the adapter, drop the `-p`
flag and let the Judge call fail as it did on 2026-10-06.

Remove this file (and the `-p` flag) once the Judge model accepts a forced
tool_choice again.
"""

import logging
import os

from autoevals.llm import OpenAILLMClassifier

_original_request_args = OpenAILLMClassifier._request_args
_adapter_announced = False


def _request_args_with_thinking_disabled(self, output, expected, **kwargs):
    global _adapter_announced

    request = _original_request_args(self, output, expected, **kwargs)

    # Only touch requests that pin a specific function, which is the autoevals
    # Judge shape. Anything else (plain chat, "auto") is passed through as-is.
    tool_choice = request.get("tool_choice")
    if not (isinstance(tool_choice, dict) and tool_choice.get("function")):
        return request

    extra_body = dict(request.get("extra_body") or {})
    extra_body.setdefault("thinking", {"type": "disabled"})
    request["extra_body"] = extra_body

    if not _adapter_announced:
        _adapter_announced = True
        logging.getLogger("classifier").warning(
            "judge_thinking_adapter: added thinking=disabled to the Judge request "
            f"(model={request.get('model')}) because this provider rejects a forced "
            "tool_choice in thinking mode."
        )
    return request


OpenAILLMClassifier._request_args = _request_args_with_thinking_disabled

# Also expose a marker so a human (or a log grep) can confirm the plugin loaded.
ASSERT_ADAPTER_LOADED = os.environ.get("JUDGE_THINKING_ADAPTER", "1") == "1"
