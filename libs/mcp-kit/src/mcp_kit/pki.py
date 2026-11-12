"""
pki.py
------

Certificates for mutual TLS between an MCP server and its API (Level 3, docs/adr/0025). One small certificate
authority per pair: it signs the API's server certificate (serverAuth) and the MCP server's client certificate
(clientAuth), and each side trusts only that authority. The API therefore accepts connections from its own MCP
server and nobody else, and the MCP server talks only to its own API, without any name check in application code.

Keys are EC P-256. Nothing here prints or logs a key.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

SERVER = "server"
CLIENT = "client"


@dataclass(frozen=True)
class Issued:
    """A certificate and its private key."""

    certificate: x509.Certificate
    key: ec.EllipticCurvePrivateKey

    def certificate_pem(self) -> bytes:
        return self.certificate.public_bytes(serialization.Encoding.PEM)

    def key_pem(self) -> bytes:
        return self.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )

    def expires_in(self) -> dt.timedelta:
        return self.certificate.not_valid_after_utc - dt.datetime.now(dt.UTC)


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def issue_ca(common_name: str, days: int = 1825) -> Issued:
    """A self-signed authority that may sign end-entity certificates only (path length 0)."""
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(_name(common_name))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return Issued(certificate, key)


def issue_leaf(ca: Issued, common_name: str, *, usage: str, dns_names: tuple[str, ...] = (), days: int = 90) -> Issued:
    """An end-entity certificate for one use only: `server` (serverAuth, with DNS names) or `client` (clientAuth)."""
    if usage not in (SERVER, CLIENT):
        raise ValueError("usage is 'server' or 'client'")
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(ca.certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=True,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH if usage == SERVER else ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.key.public_key()),
            critical=False,
        )
    )
    if usage == SERVER:
        names = dns_names or (common_name,)
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False)
    return Issued(builder.sign(ca.key, hashes.SHA256()), key)


def load(certificate_file: Path, key_file: Path) -> Issued:
    """A certificate and key written by `write`."""
    key = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError(f"{key_file} is not an EC private key")
    return Issued(x509.load_pem_x509_certificate(certificate_file.read_bytes()), key)


def write(issued: Issued, certificate_file: Path, key_file: Path) -> None:
    """Write PEM files; the key is readable by its owner only."""
    certificate_file.parent.mkdir(parents=True, exist_ok=True)
    certificate_file.write_bytes(issued.certificate_pem())
    key_file.touch(mode=0o600, exist_ok=True)
    key_file.chmod(0o600)
    key_file.write_bytes(issued.key_pem())
