import pytest

EXPECTED_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'"
    ),
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "x-frame-options": "DENY",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "cross-origin-opener-policy": "same-origin",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/static/app.js", "/non-existent-route-for-404"])
async def test_security_headers_present_on_all_responses(client, path):
    response = await client.get(path)
    for header, expected_val in EXPECTED_HEADERS.items():
        actual_val = response.headers.get(header)
        assert actual_val is not None, f"Missing header: {header} on {path}"
        assert actual_val == expected_val, (
            f"Header {header} mismatch on {path}: expected '{expected_val}', got '{actual_val}'"
        )
    # HSTS should not be set (the reverse proxy does that at deploy)
    assert "strict-transport-security" not in response.headers
