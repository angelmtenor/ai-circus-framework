"""
dpop.py
-------

Proof of possession for the per-API token at Level 3 (RFC 9449, docs/adr/0025). The MCP server holds a private
key that never leaves its memory. It signs a proof when it exchanges the user's token, so the identity provider
binds the per-API token (T2) to that key (`cnf.jkt`), and it signs a fresh proof for every call to the API, which
checks both. A T2 copied out of a log or a proxy is then useless: nobody else can sign its proofs (rule R10).

The user's token (T1) stays a bearer token: Keycloak refuses to exchange a sender-constrained token for any client
other than the one it was issued to, so binding T1 to the chat UI would forbid the per-API exchange. T1 is checked
live on every call instead (`introspection.py`).

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

DPOP_SCHEME = "DPoP"
PROOF_TYPE = "dpop+jwt"
ALGORITHM = "ES256"


def b64url(data: bytes) -> str:
    """Base64url without padding, as JOSE uses it."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwk_thumbprint(jwk: dict[str, Any]) -> str:
    """The RFC 7638 SHA-256 thumbprint of a public JWK (EC, RSA or OKP): what `cnf.jkt` carries."""
    required = {"EC": ("crv", "kty", "x", "y"), "RSA": ("e", "kty", "n"), "OKP": ("crv", "kty", "x")}
    members = required.get(str(jwk.get("kty")))
    if members is None or any(not isinstance(jwk.get(m), str) for m in members):
        raise ValueError("Unsupported or incomplete public JWK.")
    canonical = json.dumps({m: jwk[m] for m in members}, separators=(",", ":"), sort_keys=True)
    return b64url(hashlib.sha256(canonical.encode()).digest())


def access_token_hash(access_token: str) -> str:
    """The proof's `ath`: the access token's SHA-256, base64url."""
    return b64url(hashlib.sha256(access_token.encode("ascii")).digest())


def htu(url: str) -> str:
    """The URL a proof names: scheme, host and path, without query or fragment (RFC 9449 §4.2)."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", "", ""))


class DpopKey:
    """An EC P-256 key pair held in memory only. A new process gets a new key and simply exchanges again."""

    def __init__(self, private_key: ec.EllipticCurvePrivateKey | None = None) -> None:
        """A fresh key unless one is given (tests)."""
        self._private = private_key or ec.generate_private_key(ec.SECP256R1())
        numbers = self._private.public_key().public_numbers()
        self.public_jwk: dict[str, str] = {
            "kty": "EC",
            "crv": "P-256",
            "x": b64url(numbers.x.to_bytes(32, "big")),
            "y": b64url(numbers.y.to_bytes(32, "big")),
        }
        self.thumbprint = jwk_thumbprint(self.public_jwk)

    def proof(self, method: str, url: str, access_token: str | None = None) -> str:
        """A one-time proof for one request: method, URL, time, a unique id and, for a resource call, the token hash."""
        claims: dict[str, Any] = {
            "htm": method.upper(),
            "htu": htu(url),
            "iat": int(time.time()),
            "jti": str(uuid.uuid4()),
        }
        if access_token is not None:
            claims["ath"] = access_token_hash(access_token)
        return jwt.encode(
            claims, self._private, algorithm=ALGORITHM, headers={"typ": PROOF_TYPE, "jwk": self.public_jwk}
        )


def bound_thumbprint(access_token: str) -> str | None:
    """The `cnf.jkt` a token says it is bound to (read without verifying: the token's own signature is checked
    by whoever accepts it; this only confirms the identity provider bound it to the key that asked)."""
    try:
        claims = jwt.decode(access_token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return None
    jkt = (claims.get("cnf") or {}).get("jkt")
    return jkt if isinstance(jkt, str) else None
