"""zscaler_fix - trust the corporate Zscaler TLS-inspecting proxy CA in Python.

**Must never reach production.** See "Why this must never reach
production" below, and in particular: do not depend on this from a `dev`
dependency group - see the README's Usage section for why.

## The problem

On a Zscaler-managed device, outbound HTTPS is intercepted and re-signed
with Zscaler's own root CA. The OS is told to trust that CA (via MDM), so
`curl`, `apt`, and browsers work fine. Python's HTTP clients do not consult
the OS trust store, though:

- `urllib`/`ssl` use the OS store via ``ssl.create_default_context()``, but
  Python 3.13+ enables strict X.509 validation by default
  (``ssl.VERIFY_X509_STRICT``), which rejects Zscaler's root CA because its
  ``basicConstraints`` extension is not marked critical - a spec deviation
  that strict mode specifically checks for.
- `requests`/`urllib3` don't use the OS store at all by default - they ship
  their own CA bundle (``certifi``) and, independently of Python version,
  apply the same strict check themselves.

The result is ``CERTIFICATE_VERIFY_FAILED`` (or, once you've tried adding
the cert the "obvious" way, ``Basic Constraints of CA cert not marked
critical``) on any Python HTTPS call Zscaler intercepts.

## What this package does

Wraps ``ssl.SSLContext.wrap_socket``/``wrap_bio`` - the point every Python
HTTP client eventually calls to perform a TLS handshake, regardless of how
it built its context - so that, right before each handshake:

1. The vendored Zscaler CA is added via ``load_verify_locations(cadata=...)``.
   This is *additive*: existing trusted roots (from certifi or the OS
   store) are left in place.
2. ``ssl.VERIFY_X509_STRICT`` is cleared, which is the only way to stop
   Python 3.13+ rejecting Zscaler's malformed CA. This is a no-op on
   Python <3.13, where the flag isn't set by default anyway.

Both steps only happen if the CA actually loads; a context that can't be
prepared is left completely untouched.

## Why this must never reach production

Clearing ``VERIFY_X509_STRICT`` weakens certificate validation for *every*
TLS connection the process makes, not just ones Zscaler intercepts -
production workloads are usually not behind Zscaler at all, so this has
pure downside there. Depend on this package only from a dedicated,
non-default dependency group (e.g. `zscaler`) - **not** `dev`, which uv
syncs by default and which the `gds-idea-app-kit` web templates install in
a Docker stage that production is built from. See the README's Usage
section for the full explanation.

## Why this does NOT use `truststore` / `pip-system-certs`

Those repoint Python's validator at the OS-native trust store wholesale.
On macOS that routes verification through Apple's stricter validator for
*every* connection, including ones that never touch Zscaler - which has
been observed to reject otherwise-valid, unrelated certificates. This
package only ever adds a CA and relaxes one specific check; it never
replaces the validator, so certificates that already verified successfully
continue to do so.

## Usage

Installing the package is enough - a ``.pth`` file activates it
automatically on interpreter startup, in every process, with no code
changes required. To disable at runtime (e.g. while debugging an unrelated
TLS issue), set ``ZSCALER_FIX_DISABLE=1``.
"""

from __future__ import annotations

import logging
import os
import ssl
import threading

from zscaler_fix import _ca

__all__ = ["activate", "deactivate", "is_active"]

logger = logging.getLogger(__name__)

DISABLE_ENV_VAR = "ZSCALER_FIX_DISABLE"

_lock = threading.Lock()
_active = False
_originals: dict[str, object] = {}
_logged_once = False


def _is_disabled() -> bool:
    return os.environ.get(DISABLE_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


def _prepare_context(context: ssl.SSLContext) -> None:
    """Best-effort: trust the Zscaler CA and relax strict validation for it.

    Never raises. Only clears VERIFY_X509_STRICT if the CA actually loaded,
    so a context is left completely untouched when no cert is available.
    """
    global _logged_once

    try:
        pem = _ca.load_cert_pem()
    except Exception:
        logger.debug("zscaler_fix: could not load bundled CA certificate", exc_info=True)
        return

    try:
        context.load_verify_locations(cadata=pem)
    except Exception:
        logger.debug("zscaler_fix: could not load CA into SSLContext", exc_info=True)
        return

    try:
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    except Exception:
        logger.debug("zscaler_fix: could not clear VERIFY_X509_STRICT", exc_info=True)
        return

    if not _logged_once:
        logger.info(
            "zscaler_fix is active: trusting the corporate Zscaler proxy CA for outbound "
            "HTTPS and relaxing strict X.509 validation for it. This should be a "
            "development-only dependency - see the zscaler-fix README. "
            "Set %s=1 to disable.",
            DISABLE_ENV_VAR,
        )
        _logged_once = True


def activate() -> None:
    """Patch ssl.SSLContext so Python HTTPS clients trust the Zscaler proxy CA.

    Idempotent and safe to call multiple times. No-op if ZSCALER_FIX_DISABLE
    is set. Never raises - if anything goes wrong, the process is left
    exactly as it would have been without this package installed.
    """
    global _active

    try:
        if _is_disabled():
            logger.debug("zscaler_fix: disabled via %s", DISABLE_ENV_VAR)
            return

        with _lock:
            if _active:
                return

            original_wrap_socket = ssl.SSLContext.wrap_socket
            original_wrap_bio = ssl.SSLContext.wrap_bio

            def _patched_wrap_socket(self, *args, **kwargs):
                _prepare_context(self)
                return original_wrap_socket(self, *args, **kwargs)

            def _patched_wrap_bio(self, *args, **kwargs):
                _prepare_context(self)
                return original_wrap_bio(self, *args, **kwargs)

            ssl.SSLContext.wrap_socket = _patched_wrap_socket
            ssl.SSLContext.wrap_bio = _patched_wrap_bio

            _originals["wrap_socket"] = original_wrap_socket
            _originals["wrap_bio"] = original_wrap_bio
            _active = True
    except Exception:
        logger.debug("zscaler_fix: activate() failed, leaving ssl module untouched", exc_info=True)


def deactivate() -> None:
    """Undo activate(), restoring the original ssl.SSLContext methods.

    Safe to call even if activate() was never called.
    """
    global _active

    with _lock:
        if not _active:
            return

        if "wrap_socket" in _originals:
            ssl.SSLContext.wrap_socket = _originals.pop("wrap_socket")
        if "wrap_bio" in _originals:
            ssl.SSLContext.wrap_bio = _originals.pop("wrap_bio")

        _active = False


def is_active() -> bool:
    """Return whether the patch is currently applied."""
    return _active
