"""Tests for zscaler_fix's activate/deactivate/is_active and _prepare_context.

These tests patch and restore the real ssl.SSLContext class methods, so
the _reset_state fixture is essential to avoid leaking patched state
between tests (or into other test files run in the same process).
"""

from __future__ import annotations

import sys

import pytest

import zscaler_fix


@pytest.fixture(autouse=True)
def _reset_state():
    """Ensure a clean, deactivated slate before and after every test."""
    zscaler_fix.deactivate()
    yield
    zscaler_fix.deactivate()


def test_starts_inactive():
    assert zscaler_fix.is_active() is False


def test_activate_sets_active():
    zscaler_fix.activate()
    assert zscaler_fix.is_active() is True


def test_activate_patches_wrap_socket_and_wrap_bio():
    import ssl

    original_wrap_socket = ssl.SSLContext.wrap_socket
    original_wrap_bio = ssl.SSLContext.wrap_bio

    zscaler_fix.activate()

    assert ssl.SSLContext.wrap_socket is not original_wrap_socket
    assert ssl.SSLContext.wrap_bio is not original_wrap_bio


def test_activate_is_idempotent():
    import ssl

    zscaler_fix.activate()
    patched_wrap_socket = ssl.SSLContext.wrap_socket

    zscaler_fix.activate()

    assert ssl.SSLContext.wrap_socket is patched_wrap_socket


def test_deactivate_restores_original_methods():
    import ssl

    original_wrap_socket = ssl.SSLContext.wrap_socket
    original_wrap_bio = ssl.SSLContext.wrap_bio

    zscaler_fix.activate()
    zscaler_fix.deactivate()

    assert ssl.SSLContext.wrap_socket is original_wrap_socket
    assert ssl.SSLContext.wrap_bio is original_wrap_bio
    assert zscaler_fix.is_active() is False


def test_deactivate_without_activate_is_safe():
    zscaler_fix.deactivate()  # must not raise
    assert zscaler_fix.is_active() is False


class TestPrepareContext:
    """Tests for the per-handshake context preparation logic."""

    def test_clears_strict_flag(self):
        import ssl

        # Note: VERIFY_X509_STRICT is only on by default since Python
        # 3.13 - a bare ssl.SSLContext(...) never sets it, and older
        # create_default_context() doesn't either. requests/urllib3 set
        # the flag themselves regardless of Python version, independent
        # of this default; create_default_context() is enough to exercise
        # the same code path this function must fix.
        context = ssl.create_default_context()
        if sys.version_info >= (3, 13):
            assert context.verify_flags & ssl.VERIFY_X509_STRICT  # sanity: on by default (3.13+)

        zscaler_fix._prepare_context(context)

        assert not (context.verify_flags & ssl.VERIFY_X509_STRICT)

    def test_adds_ca_without_removing_existing_roots(self):
        import ssl

        context = ssl.create_default_context()
        certs_before = len(context.get_ca_certs())

        zscaler_fix._prepare_context(context)

        certs_after = context.get_ca_certs()
        # Additive, never destructive - the exact delta isn't portable. The
        # OS default cert bundle depends on which OpenSSL a given Python
        # *distribution* links against, not the language version: on one
        # test machine, Homebrew's Python 3.14 linked Homebrew's OpenSSL
        # bundle (195 certs, already including a corporate CA pushed by
        # MDM into a Homebrew-managed store), while uv-managed 3.11/3.12/
        # 3.13 builds all pointed at macOS's own 128-cert bundle, which did
        # not include it. So the CA can already be present before this
        # function runs, in which case adding it again is a no-op rather
        # than +1 - hence checking >= and presence, not a fixed delta.
        assert len(certs_after) >= certs_before

        common_names = [dict(kv for rdn in cert["subject"] for kv in rdn).get("commonName") for cert in certs_after]
        assert "Zscaler Root CA" in common_names

    def test_preserves_verification_settings(self):
        """Guardrail: must never weaken these, whatever else changes.

        This is the property that keeps a previously-valid connection
        (e.g. one reached via a tunnel that never touches Zscaler) working
        exactly as before.
        """
        import ssl

        context = ssl.create_default_context()

        zscaler_fix._prepare_context(context)

        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True

    def test_never_raises_on_unparseable_cert(self, monkeypatch):
        import ssl

        monkeypatch.setattr(zscaler_fix._ca, "load_cert_pem", lambda: "not a real certificate")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

        zscaler_fix._prepare_context(context)  # must not raise

    def test_never_raises_when_cert_loading_errors(self, monkeypatch):
        import ssl

        def _raise():
            raise FileNotFoundError("simulated missing resource")

        monkeypatch.setattr(zscaler_fix._ca, "load_cert_pem", _raise)
        context = ssl.create_default_context()
        flags_before = context.verify_flags

        zscaler_fix._prepare_context(context)  # must not raise

        # Nothing should have changed since the CA never loaded (compared
        # against the actual starting state, since the default verify_flags
        # value itself differs by Python version - see test_clears_strict_flag).
        assert context.verify_flags == flags_before


class TestDisableEnvVar:
    def test_disable_env_var_prevents_activation(self, monkeypatch):
        import ssl

        monkeypatch.setenv(zscaler_fix.DISABLE_ENV_VAR, "1")
        original_wrap_socket = ssl.SSLContext.wrap_socket

        zscaler_fix.activate()

        assert zscaler_fix.is_active() is False
        assert ssl.SSLContext.wrap_socket is original_wrap_socket

    @pytest.mark.parametrize("value", ["1", "true", "True", "yes", "on"])
    def test_disable_env_var_accepts_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv(zscaler_fix.DISABLE_ENV_VAR, value)
        zscaler_fix.activate()
        assert zscaler_fix.is_active() is False

    def test_unset_env_var_allows_activation(self, monkeypatch):
        monkeypatch.delenv(zscaler_fix.DISABLE_ENV_VAR, raising=False)
        zscaler_fix.activate()
        assert zscaler_fix.is_active() is True


class TestSecurityGuardrails:
    """Regression guards for the specific failure mode this package must avoid.

    See __init__.py docstring: "Why this does NOT use truststore /
    pip-system-certs" - truststore repoints validation at the OS-native
    validator for every connection, which broke an otherwise-valid,
    unrelated certificate in practice. These tests fail loudly if a future
    change reintroduces that approach.
    """

    def test_truststore_is_never_imported(self):
        zscaler_fix.activate()
        assert "truststore" not in sys.modules

    def test_ssl_context_remains_the_stdlib_class(self):
        import ssl

        zscaler_fix.activate()
        assert ssl.SSLContext.__module__ == "ssl"
        assert ssl.SSLContext.__name__ == "SSLContext"
