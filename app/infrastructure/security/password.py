"""
Password hashing.

Uses the `bcrypt` library directly rather than passlib: passlib (last
released 1.7.4) has a well-known compatibility wrinkle with bcrypt>=4.1
(it probes `bcrypt.__about__.__version__`, which newer bcrypt releases
removed). Calling bcrypt directly avoids that dependency entirely while
keeping the same security properties.
"""
import bcrypt

_BCRYPT_MAX_BYTES = 72  # bcrypt silently truncates beyond this; we reject instead.


class PasswordTooLongError(ValueError):
    pass


def hash_password(plain_password: str) -> str:
    password_bytes = plain_password.encode("utf-8")
    if len(password_bytes) > _BCRYPT_MAX_BYTES:
        raise PasswordTooLongError(
            f"Password must be at most {_BCRYPT_MAX_BYTES} bytes when UTF-8 encoded."
        )
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed hash (e.g. legacy/corrupt data) — never raise on auth path.
        return False
