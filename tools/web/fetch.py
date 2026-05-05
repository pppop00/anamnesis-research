"""Fetch a URL's text content. Thin wrapper around `requests` with a sane User-Agent.

Headless mode for backfilling / cron jobs. For interactive runs in Claude Code or Cursor,
prefer the host's WebFetch tool — it handles redirects, JS, and rate limits better.
"""
from __future__ import annotations

import argparse
import sys

DEFAULT_UA = "AnamnesisResearch/1.0 (https://example.invalid/anamnesis-research)"


def fetch(url: str, user_agent: str = DEFAULT_UA, timeout: float = 20.0) -> str:
    try:
        import requests
    except ImportError:
        print("error: pip install requests", file=sys.stderr)
        sys.exit(3)
    r = requests.get(url, headers={"User-Agent": user_agent}, timeout=timeout)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return r.text


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--url", required=True)
    p.add_argument("--user-agent", default=DEFAULT_UA)
    p.add_argument("--timeout", type=float, default=20.0)
    args = p.parse_args(argv)
    print(fetch(args.url, args.user_agent, args.timeout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
