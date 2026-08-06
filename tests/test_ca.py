"""Tests for the vendored Zscaler CA certificate resource."""

from __future__ import annotations

import hashlib
import re
import ssl

from zscaler_fix import _ca

# Pinned SHA-256 fingerprint of the DER-encoded certificate. This is the
# same fingerprint verified against a real Zscaler-managed device and
# against alphagov/govuk-infrastructure's copy (see _ca.py docstring).
# If this test starts failing after a deliberate cert update, recompute
# with:
#   openssl x509 -in src/zscaler_fix/zscaler-root-ca.pem -outform DER \
#       | sha256sum
EXPECTED_SHA256_FINGERPRINT = "04f61f1d13aae1d16573dc2c37f796fdf4ac97713a6959ebb11d2473958b1a53"

_PEM_BLOCK_RE = re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL)


def _extract_pem_block(pem_text: str) -> str:
    match = _PEM_BLOCK_RE.search(pem_text)
    assert match, "no PEM certificate block found in vendored file"
    return match.group(0)


def test_cert_resource_loads():
    pem = _ca.load_cert_pem()
    assert "-----BEGIN CERTIFICATE-----" in pem
    assert "-----END CERTIFICATE-----" in pem


def test_cert_fingerprint_matches_pinned_value():
    """Catches accidental corruption, truncation, or an unreviewed swap."""
    pem = _ca.load_cert_pem()
    block = _extract_pem_block(pem)
    der = ssl.PEM_cert_to_DER_cert(block)
    fingerprint = hashlib.sha256(der).hexdigest()
    assert fingerprint == EXPECTED_SHA256_FINGERPRINT


def test_cert_loads_into_ssl_context():
    """Sanity check that the PEM is well-formed enough for OpenSSL itself."""
    pem = _ca.load_cert_pem()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=pem)
    certs = context.get_ca_certs()
    assert len(certs) == 1


def test_cert_common_name_is_zscaler_root_ca():
    pem = _ca.load_cert_pem()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=pem)
    (cert,) = context.get_ca_certs()
    subject = dict(kv for rdn in cert["subject"] for kv in rdn)
    assert subject.get("commonName") == "Zscaler Root CA"


def test_load_cert_pem_is_cached():
    assert _ca.load_cert_pem() is _ca.load_cert_pem()
