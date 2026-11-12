"""
dpop.py
-------

crm-api's check of a DPoP-bound token at Level 3 (RFC 9449 §4.3 and §7.1, docs/adr/0025), with PyJWT only, as any
API can do it: the request carries `Authorization: DPoP <token>` and a fresh `DPoP` proof signed by the key the
token is bound to. A token copied out of a log or a proxy is useless without that key.

The proof must: be a `dpop+jwt` signed with an asymmetric algorithm by the public key in its own header; name this
request's method and URL; be fresh; never have been seen before (its `jti`); carry the hash of this very token; and
come from the key whose thumbprint the token's `cnf.jkt` names.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import jwt

ALGORITHMS = ("ES256", "ES384", "PS256", "RS256", "EdDSA")
MAX_PROOF_BYTES = 4096


class DpopRejectedError(Exception):
    """The proof is missing, malformed, for another request, stale, replayed or from another key (→ 401)."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def thumbprint(jwk: dict[str, Any]) -> str:
    """RFC 7638 SHA-256 thumbprint of a public JWK."""
    members = {"EC": ("crv", "kty", "x", "y"), "RSA": ("e", "kty", "n"), "OKP": ("crv", "kty", "x")}.get(
        str(jwk.get("kty"))
    )
    if members is None or any(not isinstance(jwk.get(m), str) for m in members):
        raise DpopRejectedError("The proof's key is not a supported public key.")
    canonical = json.dumps({m: jwk[m] for m in members}, separators=(",", ":"), sort_keys=True)
    return _b64url(hashlib.sha256(canonical.encode()).digest())


def request_htu(url: str) -> str:
    """The URL a proof must name: scheme, host and path, without query or fragment."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", "", ""))


class DpopVerifier:
    """Checks proofs and remembers each proof id for its whole validity window, so none is accepted twice."""

    def __init__(self, *, max_age_seconds: int = 60, max_remembered: int = 50_000) -> None:
        """A proof older or further in the future than `max_age_seconds` is refused; ids are kept that long."""
        self.max_age_seconds = max_age_seconds
        self.max_remembered = max_remembered
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()  # routes run in a thread pool

    def verify(self, proof: str | None, *, method: str, url: str, access_token: str, bound_jkt: str | None) -> str:
        """The proof's key thumbprint, if every check passes; DpopRejectedError otherwise."""
        if not proof or len(proof) > MAX_PROOF_BYTES:
            raise DpopRejectedError("A DPoP proof is required.")
        if not bound_jkt:
            raise DpopRejectedError("The token is not bound to a key.")
        try:
            header = jwt.get_unverified_header(proof)
            jwk = header.get("jwk")
            if header.get("typ") != "dpop+jwt" or header.get("alg") not in ALGORITHMS:
                raise DpopRejectedError("Not a DPoP proof.")
            if not isinstance(jwk, dict) or any(k in jwk for k in ("d", "p", "q", "dp", "dq", "qi")):
                raise DpopRejectedError("The proof must carry a public key only.")
            claims = jwt.decode(
                proof,
                jwt.PyJWK(jwk).key,
                algorithms=[header["alg"]],
                options={"require": ["iat", "jti", "htm", "htu"], "verify_aud": False},
            )
        except (jwt.PyJWTError, ValueError, KeyError) as exc:
            raise DpopRejectedError("Invalid DPoP proof.") from exc
        if claims["htm"] != method.upper() or claims["htu"] != request_htu(url):
            raise DpopRejectedError("The proof names another request.")
        now = time.time()
        if not isinstance(claims["iat"], int | float) or abs(now - claims["iat"]) > self.max_age_seconds:
            raise DpopRejectedError("The proof is not fresh.")
        expected_ath = _b64url(hashlib.sha256(access_token.encode("ascii")).digest())
        if claims.get("ath") != expected_ath:
            raise DpopRejectedError("The proof is for another token.")
        key_thumbprint = thumbprint(jwk)
        if key_thumbprint != bound_jkt:
            raise DpopRejectedError("The proof comes from another key than the token is bound to.")
        self._remember(str(claims["jti"]), now)
        return key_thumbprint

    def _remember(self, jti: str, now: float) -> None:
        with self._lock:
            while self._seen and next(iter(self._seen.values())) < now - 2 * self.max_age_seconds:
                self._seen.popitem(last=False)
            if jti in self._seen:
                raise DpopRejectedError("The proof was already used.")
            self._seen[jti] = now
            while len(self._seen) > self.max_remembered:
                self._seen.popitem(last=False)
