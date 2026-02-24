"""Claude integration for natural language queries about job status.

Token minimization strategy:
- Pre-compute a compact text summary (~2-3K tokens) from ~370 jobs
- Send only the summary + user question to Claude
- Use a focused system prompt to keep responses concise
"""

from __future__ import annotations

import os

import anthropic

from vsphere_monitor.analyzer import JobSummary, build_compact_summary

_SYSTEM_PROMPT = """\
You are a CI monitoring assistant for OpenShift vSphere periodic Prow jobs.
You will receive a compact status report and answer questions about it.
Be concise. Use data from the report. If asked about trends, use the RECENT \
column (most recent run first, S=success, F=failure, P=pending, A=aborted).
Failure rates are computed across all runs in the current dataset window."""


def ask(summaries: list[JobSummary], question: str) -> str:
    """Send a question about job status to Claude with a pre-computed summary.

    Requires ANTHROPIC_API_KEY environment variable to be set.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return (
            "Error: ANTHROPIC_API_KEY environment variable is not set.\n"
            "Set it with: export ANTHROPIC_API_KEY=sk-ant-..."
        )

    compact = build_compact_summary(summaries)

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"<report>\n{compact}\n</report>\n\nQuestion: {question}",
            }
        ],
    )

    # Extract text from response
    return "".join(
        block.text for block in message.content if hasattr(block, "text")
    )
