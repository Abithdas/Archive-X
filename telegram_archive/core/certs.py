"""Local HTTPS certificates that phones will actually accept.

v16 and earlier used one self-signed certificate. Research showed that
iOS Safari rejects self-signed certificates outright (and media often
fails even after a warning is accepted), and Apple additionally requires
TLS server certificates to have:  a SAN, the serverAuth extended key
usage, and a validity of at most 825 days.

v17 does what the `mkcert` tool does:
  * data/cert/ca.pem      — a private root CA, created ONCE, valid 10 years.
                            Install/trust it on your phone once
                            (download from https://<pc>:8443/ca.crt).
  * data/cert/cert.pem    — the server certificate, signed by that CA,
                            valid 800 days, serverAuth EKU, SAN = localhost,
                            127.0.0.1 and the PC's current LAN IP.
The server certificate is re-issued automatically (same CA, so the phone
keeps trusting it) when the LAN IP changes or it is within 30 days of
expiry. The CA private key never leaves data/cert/.
"""
from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import config

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    CRYPTOGRAPHY_AVAILABLE = False

CA_PATH = config.CERT_DIR / "ca.pem"
CA_KEY_PATH = config.CERT_DIR / "ca-key.pem"
LEAF_DAYS = 800          # Apple limit: 825
RENEW_BEFORE_DAYS = 30


def _now():
    return datetime.now(timezone.utc)


def _write_key(path, key):
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.TraditionalOpenSSL,
                                       serialization.NoEncryption()))


def _load_or_create_ca():
    if CA_PATH.exists() and CA_KEY_PATH.exists():
        ca_cert = x509.load_pem_x509_certificate(CA_PATH.read_bytes())
        ca_key = serialization.load_pem_private_key(CA_KEY_PATH.read_bytes(), password=None)
        return ca_cert, ca_key
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Archive X (personal)"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Archive X Local Root CA"),
    ])
    ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    ca_cert = (x509.CertificateBuilder()
               .subject_name(name).issuer_name(name)
               .public_key(ca_key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(_now() - timedelta(days=1))
               .not_valid_after(_now() + timedelta(days=3650))
               .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
               .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                            content_commitment=False, key_encipherment=False,
                                            data_encipherment=False, key_agreement=False,
                                            encipher_only=False, decipher_only=False), critical=True)
               .add_extension(ski, critical=False)
               .sign(ca_key, hashes.SHA256()))
    config.CERT_DIR.mkdir(parents=True, exist_ok=True)
    _write_key(CA_KEY_PATH, ca_key)
    CA_PATH.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    return ca_cert, ca_key


def _wanted_ips() -> set[str]:
    ips = {"127.0.0.1"}
    lan = config.local_ip()
    try:
        ipaddress.ip_address(lan)
        ips.add(lan)
    except ValueError:
        pass
    return ips


def _leaf_ok(ca_cert) -> bool:
    if not (config.CERT_PATH.exists() and config.KEY_PATH.exists()):
        return False
    try:
        leaf = x509.load_pem_x509_certificate(config.CERT_PATH.read_bytes())
    except Exception:
        return False
    if leaf.issuer != ca_cert.subject:                       # old v14-v16 self-signed cert
        return False
    expires = getattr(leaf, "not_valid_after_utc", None) or leaf.not_valid_after.replace(tzinfo=timezone.utc)
    if expires - _now() < timedelta(days=RENEW_BEFORE_DAYS):
        return False
    try:
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
    except x509.ExtensionNotFound:
        return False
    return _wanted_ips() <= have                             # LAN IP changed -> reissue


def ensure_certificate() -> tuple[str, str]:
    if not CRYPTOGRAPHY_AVAILABLE:
        raise SystemExit("Missing HTTPS dependency. Run: pip install -r requirements.txt")
    config.CERT_DIR.mkdir(parents=True, exist_ok=True)
    ca_cert, ca_key = _load_or_create_ca()
    if _leaf_ok(ca_cert):
        return str(config.CERT_PATH), str(config.KEY_PATH)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    san: list[Any] = [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(i)) for i in sorted(_wanted_ips())]
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "telegram-archive.local")]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now() - timedelta(days=1))
            .not_valid_after(_now() + timedelta(days=LEAF_DAYS))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=True,
                                         content_commitment=False, data_encipherment=False, key_agreement=False,
                                         key_cert_sign=False, crl_sign=False, encipher_only=False,
                                         decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write_key(config.KEY_PATH, key)
    # Serve the full chain so clients can build the path to the root.
    config.CERT_PATH.write_bytes(leaf.public_bytes(serialization.Encoding.PEM)
                                 + ca_cert.public_bytes(serialization.Encoding.PEM))
    return str(config.CERT_PATH), str(config.KEY_PATH)


def ca_certificate_der() -> bytes:
    """Root CA in DER form, the format iOS/Android install most reliably."""
    ca_cert, _ = _load_or_create_ca()
    return ca_cert.public_bytes(serialization.Encoding.DER)
