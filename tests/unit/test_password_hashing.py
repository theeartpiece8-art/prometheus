from app.infrastructure.security.password import PasswordTooLongError, hash_password, verify_password


class TestPasswordHashing:
    def test_hash_and_verify_roundtrip(self):
        h = hash_password("S3cur3P@ssw0rd!")
        assert verify_password("S3cur3P@ssw0rd!", h)

    def test_wrong_password_fails(self):
        h = hash_password("correct-password")
        assert not verify_password("incorrect-password", h)

    def test_hash_is_not_the_plaintext(self):
        h = hash_password("hunter2")
        assert h != "hunter2"

    def test_two_hashes_of_same_password_differ(self):
        # bcrypt uses a random salt per call — hashes of the same password
        # must never be identical (otherwise identical passwords would be
        # detectable by comparing hashes, leaking information).
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        assert h1 != h2
        assert verify_password("same-password", h1)
        assert verify_password("same-password", h2)

    def test_overlong_password_is_rejected(self):
        too_long = "x" * 100
        try:
            hash_password(too_long)
            assert False, "expected PasswordTooLongError"
        except PasswordTooLongError:
            pass

    def test_malformed_hash_returns_false_not_exception(self):
        assert verify_password("anything", "not-a-real-bcrypt-hash") is False
