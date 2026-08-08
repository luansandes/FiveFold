from fivefold.auth import hash_password, verify_password


def test_password_hash_round_trip() -> None:
    encoded = hash_password("correct horse battery staple", iterations=10_000)
    assert verify_password("correct horse battery staple", encoded, "production")
    assert not verify_password("wrong", encoded, "production")
    assert "correct horse" not in encoded


def test_development_default_is_never_valid_in_production() -> None:
    assert verify_password("fivefold-demo", None, "development")
    assert not verify_password("fivefold-demo", None, "production")

