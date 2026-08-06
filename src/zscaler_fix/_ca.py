"""Loads the vendored Zscaler root CA certificate.

The certificate is deliberately vendored rather than fetched at runtime:

- Fetching a trust anchor over the network on every process start creates a
  bootstrap problem - you'd need working TLS to download the certificate
  that fixes TLS.
- It also means anyone who can tamper with the source URL or the connection
  used to fetch it could install an arbitrary root CA into every Python
  process using this package. Vendoring means the certificate is reviewed
  once, pinned, and visible in this repo's git history.

Source of truth: alphagov/govuk-infrastructure, cacerts/zscaler-root-ca.pem,
commit f8bfa874279d (2026-06-16). Verified byte-identical (SHA-256
fingerprint of the DER encoding) to the certificate installed by Zscaler on
a real managed device - this is Zscaler's vendor-default root CA, not an
organisation-specific certificate.

A scheduled workflow (.github/workflows/cert-drift.yml) checks weekly
whether alphagov's copy has changed, so drift is noticed without any
runtime network dependency.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

_CERT_RESOURCE = "zscaler-root-ca.pem"


@lru_cache(maxsize=1)
def load_cert_pem() -> str:
    """Return the vendored Zscaler root CA certificate as PEM text.

    Cached after the first call, since this is invoked on every TLS
    handshake once the fix is active and repeated disk I/O would be
    wasteful.

    Raises whatever the underlying resource loader raises (e.g.
    FileNotFoundError) if the packaged certificate is missing - this
    function does not swallow errors itself. Callers that need fail-safe
    behaviour at runtime (see zscaler_fix._prepare_context) are responsible
    for catching and handling that.
    """
    resource = files("zscaler_fix").joinpath(_CERT_RESOURCE)
    return resource.read_text(encoding="ascii")
