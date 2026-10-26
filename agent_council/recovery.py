"""Classify CLI availability failures without mistaking normal answers for errors."""
import re

CAPACITY = re.compile(r"rate[ -]?limit|too many requests|\b429\b|resource[_ ]exhausted|insufficient[_ ]quota|"
                      r"quota.{0,40}(?:exceed|exhaust|limit)|usage limit|"
                      r"(?:hit|reached|exceeded).{0,30}(?:usage|request|token|message) limit|"
                      r"credit balance.{0,30}(?:low|insufficient|exhaust)|out of credits", re.I)
TRANSIENT = re.compile(r"timed out|timeout|temporarily unavailable|connection (?:reset|refused|aborted)|"
                       r"network (?:error|unreachable)|\b(?:502|503|504)\b|overloaded|service unavailable", re.I)
AUTH = re.compile(r"\b(?:401|403)\b|not (?:logged|signed) in|authentication (?:failed|required)|"
                  r"invalid api key|unauthorized|login required", re.I)


def failure_kind(error: str, stdout: str = "", stderr: str = "") -> str:
    text = "\n".join((error, stdout, stderr))
    if "interrupted" in error.lower():
        return "interrupted"
    if CAPACITY.search(text):
        return "capacity"
    if AUTH.search(text):
        return "authentication"
    if TRANSIENT.search(text):
        return "transient"
    return "error"


def capacity_answer(text: str) -> bool:
    """Some CLIs exit zero while printing an account-limit notice as the answer."""
    return bool(len(text) < 2000 and re.match(
        r"\s*(?:you(?:['’]ve| have)?\s+(?:hit|reached|exceeded)\s+(?:your\s+)?(?:usage|rate|request|message)\s+limit|"
        r"(?:error\s*[:\-]\s*)?(?:rate limit exceeded|quota exceeded|insufficient_quota|resource_exhausted))",
        text, re.I))
