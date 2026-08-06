"""Integration tests against real Zscaler-intercepted and clean endpoints.

Marked `integration` and excluded from CI (GitHub-hosted runners are not
behind Zscaler, so these would either fail for the wrong reason or pass
vacuously there). Run manually on a Zscaler-managed machine:

    uv run pytest tests/test_integration.py -m integration -v

`public.ecr.aws` is verified Zscaler-intercepted; `pypi.org` is a clean
control used to prove normal TLS is untouched.
"""

from __future__ import annotations

import ssl

import pytest

import zscaler_fix

INTERCEPTED_HOST = "https://public.ecr.aws"
CLEAN_HOST = "https://pypi.org"

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_state():
    zscaler_fix.deactivate()
    yield
    zscaler_fix.deactivate()


class TestUrllib:
    def test_intercepted_host_fails_without_fix(self):
        import urllib.request

        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(INTERCEPTED_HOST, timeout=15)

    def test_intercepted_host_succeeds_with_fix(self):
        import urllib.request

        zscaler_fix.activate()
        response = urllib.request.urlopen(INTERCEPTED_HOST, timeout=15)
        assert response.status == 200

    def test_clean_host_succeeds_with_or_without_fix(self):
        import urllib.request

        response = urllib.request.urlopen(CLEAN_HOST, timeout=15)
        assert response.status == 200

        zscaler_fix.activate()
        response = urllib.request.urlopen(CLEAN_HOST, timeout=15)
        assert response.status == 200


class TestRequests:
    def test_intercepted_host_fails_without_fix(self):
        requests = pytest.importorskip("requests")

        with pytest.raises(requests.exceptions.SSLError):
            requests.get(INTERCEPTED_HOST, timeout=15)

    def test_intercepted_host_succeeds_with_fix(self):
        requests = pytest.importorskip("requests")

        zscaler_fix.activate()
        response = requests.get(INTERCEPTED_HOST, timeout=15)
        assert response.status_code == 200

    def test_clean_host_succeeds_with_or_without_fix(self):
        requests = pytest.importorskip("requests")

        response = requests.get(CLEAN_HOST, timeout=15)
        assert response.status_code == 200

        zscaler_fix.activate()
        response = requests.get(CLEAN_HOST, timeout=15)
        assert response.status_code == 200


class TestHttpx:
    def test_intercepted_host_succeeds_with_fix(self):
        httpx = pytest.importorskip("httpx")

        zscaler_fix.activate()
        response = httpx.get(INTERCEPTED_HOST, timeout=15, follow_redirects=True)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_async_intercepted_host_succeeds_with_fix(self):
        httpx = pytest.importorskip("httpx")

        zscaler_fix.activate()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(INTERCEPTED_HOST, timeout=15)
        assert response.status_code == 200


class TestBoto3:
    def test_ecr_public_call_reaches_server_over_tls_with_fix(self):
        """No real AWS credentials in this environment, so we can't assert a
        successful API response - but any error response *from AWS*, as
        opposed to a connection-level TLS failure, proves the handshake
        itself succeeded, which is what this test checks.
        """
        boto3 = pytest.importorskip("boto3")
        from botocore.exceptions import ClientError, SSLError
        from botocore.exceptions import ConnectionError as BotoConnectionError

        zscaler_fix.activate()
        client = boto3.client("ecr-public", region_name="us-east-1")
        try:
            client.describe_registries()
        except ClientError:
            pass  # reached AWS and got a proper (auth) error response - TLS succeeded
        except (BotoConnectionError, SSLError) as e:
            pytest.fail(f"TLS-level failure reaching public.ecr.aws: {e}")


def test_ssl_context_default_still_strict_when_fix_inactive():
    """Sanity check that we're testing something real, not a tautology."""
    context = ssl.create_default_context()
    assert context.verify_flags & ssl.VERIFY_X509_STRICT
