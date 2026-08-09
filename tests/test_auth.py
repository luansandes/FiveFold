from fivefold.auth import hash_password, verify_password


def test_password_hash_round_trip() -> None:
    encoded = hash_password("correct horse battery staple", iterations=10_000)
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong", encoded)
    assert "correct horse" not in encoded


def test_missing_password_hash_never_authenticates() -> None:
    assert not verify_password("any password", None)
