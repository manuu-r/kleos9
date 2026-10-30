import json
import logging

import pytest

from kleos.telemetry import SafeAccessFormatter


@pytest.mark.parametrize("target", [
    "/api/v1/auth/callback?code=private-oauth-code&state=private-state",
    "/api/v1/artifacts/file/content?X-Amz-Signature=private-signature",
    "/api/v1/projects?after=private-query#private-fragment",
])
def test_access_logs_drop_query_values_and_client_address(target):
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
                               '%s - "%s %s HTTP/%s" %d',
                               ("private-client-address", "GET", target, "1.1", 200), None)
    result = SafeAccessFormatter().format(record)
    assert "private-" not in result
    parsed = json.loads(result)
    assert parsed["method"] == "GET" and parsed["status"] == 200
    assert parsed["path"] == target.split("?", 1)[0]


def test_unexpected_access_log_shape_never_falls_back_to_raw_interpolation():
    record = logging.LogRecord("uvicorn.access", logging.WARNING, "", 0, "private-message %s",
                               ("private-argument",), None)
    assert "private-" not in SafeAccessFormatter().format(record)
