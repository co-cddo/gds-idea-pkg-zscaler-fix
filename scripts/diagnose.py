#!/usr/bin/env python3
"""Diagnose why Zscaler-related Python HTTPS failures differ between machines.

Zero required dependencies for the baseline checks (stdlib only). Pass
--with httpx --with requests to `uv run` for the fuller picture -- each
library has different trust-store behaviour, and this reports on whichever
are actually importable rather than requiring all of them.

Run it via the raw URL, not a local checkout -- IMPORTANT:

    uv run --with httpx --with requests \\
        https://raw.githubusercontent.com/co-cddo/gds-idea-pkg-zscaler-fix/main/scripts/diagnose.py

`uv run` auto-installs whatever project it finds above the current
directory. Running `scripts/diagnose.py` by a relative/absolute path from
inside a clone of *this* repo installs zscaler_fix itself as a side
effect (it's the project you're standing in) -- `--isolated`/`--no-project`
do not prevent this for a script path under a project directory, only the
URL form does. That would silently show "zscaler_fix: installed" for
someone who never intentionally installed it, defeating the point of a
clean diagnostic. Verified: running the identical script via URL from an
unrelated directory correctly reports "not installed".

To compare across Python versions (isolates whether strict X.509
validation, which only defaults to on for 3.13+, is the deciding factor):

    for v in 3.11 3.12 3.13; do
        echo "=== Python $v ==="
        uv run --python "$v" --with httpx --with requests \\
            https://raw.githubusercontent.com/co-cddo/gds-idea-pkg-zscaler-fix/main/scripts/diagnose.py
    done

Copy the full output and send it to whoever's trying to help diagnose the
issue -- that's the point of this script, not to fix anything itself.
"""

from __future__ import annotations

import importlib.util
import os
import platform
import ssl
import sys
from collections.abc import Callable

# Per the README's own measurement from a Zscaler-managed device: these are
# expected to be clean (never intercepted) almost everywhere in the org --
# PyPI, all GitHub hosts, ghcr.io, Docker Hub, quay.io, npm, Terraform,
# HashiCorp, HuggingFace, Bedrock, and regional *.amazonaws.com. If one of
# these fails for someone, that's a stronger signal something unusual is
# going on for them specifically, not just normal Zscaler behaviour.
CLEAN_HOSTS = [
    "https://pypi.org",
    "https://api.github.com",
    "https://ghcr.io",
    "https://auth.docker.io",
    "https://quay.io",
]

# public.ecr.aws is confirmed Zscaler-intercepted -- the one AWS domain that
# usually isn't exempted, unlike most *.amazonaws.com hosts.
INTERCEPTED_HOSTS = [
    "https://public.ecr.aws",
]

TIMEOUT = 10


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _zscaler_fix_active() -> bool | None:
    """True/False if zscaler_fix is installed, None if it isn't."""
    if not _has("zscaler_fix"):
        return None
    import zscaler_fix

    return zscaler_fix.is_active()


def _print_environment() -> None:
    print("=" * 70)
    print("Zscaler/TLS diagnostic")
    print("=" * 70)
    print(f"python:          {sys.version.split()[0]}")
    print(f"executable:      {sys.executable}")
    print(f"platform:        {platform.platform()}")

    zf = _zscaler_fix_active()
    print(f"zscaler_fix:     {'not installed' if zf is None else f'installed, active={zf}'}")
    print(f"pip_system_certs installed: {_has('pip_system_certs')}")
    print(f"httpx installed:            {_has('httpx')}")
    print(f"requests installed:         {_has('requests')}")

    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "PIP_CERT"):
        print(f"{var}: {os.environ.get(var, '(not set)')}")


def _print_ssl_context_check() -> None:
    """The exact check from the README's "Am I affected?" section.

    Two independent factors determine what error (if any) you see -- see
    the README's table. This reports both directly instead of inferring
    them from a live connection's error message.
    """
    print()
    print("-- Default SSL context --")
    ctx = ssl.create_default_context()
    common_names = [
        dict(kv for rdn in cert["subject"] for kv in rdn).get("commonName", "") for cert in ctx.get_ca_certs()
    ]
    zscaler_trusted = any("zscaler" in cn.lower() for cn in common_names)
    strict = bool(ctx.verify_flags & ssl.VERIFY_X509_STRICT)
    print(f"Zscaler CA trusted by default context: {zscaler_trusted}")
    print(f"Strict X.509 validation on:             {strict}")
    if not zscaler_trusted and not strict:
        print("  -> expect: unable to get local issuer certificate")
    elif not zscaler_trusted and strict:
        print("  -> expect: unable to get local issuer certificate")
    elif zscaler_trusted and not strict:
        print("  -> expect: works fine, no error")
    else:
        print("  -> expect: Basic Constraints of CA cert not marked critical")


def _try_urllib(url: str) -> None:
    import urllib.error
    import urllib.request

    try:
        urllib.request.urlopen(url, timeout=TIMEOUT)  # noqa: S310
    except urllib.error.HTTPError:
        # Any HTTP response (even 4xx/5xx) proves the TLS handshake
        # succeeded -- only a genuine connection/SSL failure (URLError)
        # should count as a failure here.
        pass


def _try_httpx(url: str) -> None:
    import httpx

    httpx.get(url, timeout=TIMEOUT, follow_redirects=True)


def _try_requests(url: str) -> None:
    import requests

    requests.get(url, timeout=TIMEOUT)


def _run_live_tests() -> None:
    print()
    print("-- Live connection tests --")

    libraries: list[tuple[str, Callable[[str], None], bool]] = [
        ("urllib", _try_urllib, True),
        ("httpx", _try_httpx, _has("httpx")),
        ("requests", _try_requests, _has("requests")),
    ]

    for label, hosts in (
        ("clean (should always work)", CLEAN_HOSTS),
        ("intercepted (needs the fix)", INTERCEPTED_HOSTS),
    ):
        print(f"\n[{label}]")
        for url in hosts:
            results = []
            for name, fn, available in libraries:
                if not available:
                    results.append(f"{name}=skipped (not installed)")
                    continue
                try:
                    fn(url)
                    results.append(f"{name}=OK")
                except Exception as e:  # noqa: BLE001 - deliberately broad, this is a diagnostic
                    results.append(f"{name}=FAIL({type(e).__name__}: {e})")
            print(f"  {url}")
            for result in results:
                print(f"    {result}")


def main() -> None:
    _print_environment()
    _print_ssl_context_check()
    _run_live_tests()
    print()
    print("=" * 70)
    print("Copy everything above and send it to whoever's helping diagnose this.")
    print("=" * 70)


if __name__ == "__main__":
    main()
