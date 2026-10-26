"""Read assistant text from Aider's documented ``--llm-history-file``.

Aider can print provider errors and exit successfully, so its console output
cannot establish that an assistant replied. The LLM history gives each record
a timestamped header and prefixes every response line with ``ASSISTANT ``.
Empty failure records must never cause an earlier answer to be reused.

The preset disables streaming so a failed stream cannot supply partial text.
This format comes from Aider's ``InputOutput.log_llm_history``,
``utils.format_content``, and ``Coder.send``; it has no completion-status field.
"""

from datetime import datetime
import re


_HEADER = re.compile(
    r"(TO LLM|LLM RESPONSE) (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\Z"
)
_ASSISTANT_PREFIX = "ASSISTANT "


def parse_aider_history(text: str) -> str:
    """Extract the last response, raising ValueError for missing or bad output.

    Prompt logs may include earlier assistant messages. Only text following
    the final outer ``LLM RESPONSE`` header counts as the current reply. A
    subsequent request without a response is incomplete. Assistant and prompt
    content cannot introduce record headers because Aider prefixes every line
    of that content with its role.
    """
    if not isinstance(text, str):
        raise ValueError("Aider LLM history must be text")

    final_kind = None
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith(("TO LLM", "LLM RESPONSE")):
            header = _HEADER.fullmatch(line)
            if header is None:
                raise ValueError("Aider LLM history has a malformed record header")
            try:
                datetime.fromisoformat(header[2])
            except ValueError as exc:
                raise ValueError("Aider LLM history has an invalid timestamp") from exc
            final_kind = header[1]
            body = []
        elif final_kind == "LLM RESPONSE":
            body.append(line)

    if final_kind != "LLM RESPONSE":
        raise ValueError("Aider LLM history has no final response record")

    # The writer adds a newline after each record. Accept optional trailing
    # blank lines, but genuine blank assistant lines retain their role prefix.
    while body and not body[-1].strip():
        body.pop()
    if any(not line.startswith(_ASSISTANT_PREFIX) for line in body):
        raise ValueError("Aider response contains a line without its assistant prefix")
    answer = "\n".join(line[len(_ASSISTANT_PREFIX):] for line in body)
    if not answer.strip():
        raise ValueError("Aider final response is empty")
    return answer
