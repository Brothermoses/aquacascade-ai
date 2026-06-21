"""Token helpers.

User tokens are bearer credentials. New and rotated tokens are generated
with high entropy, stored only as SHA-256 hashes, and returned once.
Legacy seeded demo tokens remain supported for local compatibility.
"""
import hashlib
import secrets


def generate_user_token(prefix="usr"):
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_token(token):
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_label(token):
    h = hash_token(token)
    return f"sha256:{h[:16]}" if h else ""
