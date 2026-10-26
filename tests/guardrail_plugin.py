"""Example custom guardrails used by the test suite."""


def no_shouting(answer, context):
    return "is written in capital letters only" if answer.isupper() else None


def broken(answer, context):
    raise RuntimeError("validator bug")
