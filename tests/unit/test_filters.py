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


@pytest.mark.parametrize(
    "params,expected",
    [
        ({"page": 2, "limit": 25}, {"page": "2", "limit": "25"}),
        ({"name": "web01"}, {"name": "web01"}),
        ({"host.name": "web01"}, {"host.name": "web01"}),
        ({"service.name": "PING"}, {"service.name": "PING"}),
    ],
)
def test_sanitize_query_basic_stringification_and_dotted_passthrough(params, expected):
    result = sanitize_query(params)
    # only expected keys should appear
    assert set(result.keys()) == set(expected.keys())
    for k, v in expected.items():
        assert result[k] == v


def test_sanitize_query_drops_none_values_and_unknown_keys():
    params = {
        "page": None,
        "limit": 10,
        "host.name": None,
        "unknown": "x",
    }
    result = sanitize_query(params)

    # None values are removed
    assert "page" not in result
    assert "host.name" not in result
    # known non-None values are kept and stringified
    assert result["limit"] == "10"
    # completely unknown keys are dropped
    assert "unknown" not in result
