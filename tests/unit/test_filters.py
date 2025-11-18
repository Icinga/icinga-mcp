import pytest
from icinga_mcp.filters import sanitize_query


def test_sanitize_query_allows_operator_filters():
    params = {
        "name~": "*vm*",
        "name_ci~": "*VM*",
        "page": 1,
        "limit": 50,
        "foo": "bar",
    }
    result = sanitize_query(params)

    # allowed keys and dotted fields are kept and stringified
    assert result["name~"] == "*vm*"
    assert result["name_ci~"] == "*VM*"
    assert result["page"] == "1"
    assert result["limit"] == "50"
    # disallowed keys are dropped
    assert "foo" not in result