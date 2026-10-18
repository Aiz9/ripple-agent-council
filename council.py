#!/usr/bin/env python3
"""Compatibility entrypoint for source checkouts; installed users run `council`."""
from agent_council.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
