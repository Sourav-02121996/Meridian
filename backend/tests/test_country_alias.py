"""Pure-Python tests for canonicalize_country — no browser needed."""

from app.apply_adapters.fields import canonicalize_country


def test_common_aliases_map_to_the_full_name():
    assert canonicalize_country("USA") == "United States"
    assert canonicalize_country("usa") == "United States"
    assert canonicalize_country("US") == "United States"
    assert canonicalize_country("U.S.A.") == "United States"
    assert canonicalize_country("America") == "United States"
    assert canonicalize_country("UK") == "United Kingdom"


def test_already_canonical_name_is_unchanged():
    assert canonicalize_country("United States") == "United States"
    assert canonicalize_country("Germany") == "Germany"


def test_unknown_value_is_returned_unchanged_not_guessed():
    assert canonicalize_country("Atlantis") == "Atlantis"


def test_blank_value_is_returned_unchanged():
    assert canonicalize_country("") == ""


def test_whitespace_is_tolerated():
    assert canonicalize_country("  usa  ") == "United States"
