"""Local HTTPS for the phone app.

Phones only allow an installable, offline web app (service worker, WebCrypto) over HTTPS. MedPort
makes its own small certificate authority the first time it runs with --lan, and signs a certificate
for this computer's Wi-Fi address with it. The phone trusts that authority once.

The authority is name-constrained to private network addresses (192.168.x.x, 10.x.x.x, 172.16-31.x.x)
and localhost, so even if its key leaked it could not be used to impersonate any public website.
"""
import ipaddress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

PRIVATE_NETS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"]


def _now():
    return datetime.now(timezone.utc)


def _write_key(key, path: Path):
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def ensure_ca(tls_dir: Path):
    tls_dir.mkdir(parents=True, exist_ok=True)
    key_p, cert_p = tls_dir / "ca.key", tls_dir / "ca.crt"
    if key_p.exists() and cert_p.exists():
        key = serialization.load_pem_private_key(key_p.read_bytes(), None)
        return key, x509.load_pem_x509_certificate(cert_p.read_bytes())
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MedPort home Wi-Fi (this computer only)"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MedPort")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now() - timedelta(days=1)).not_valid_after(_now() + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                         content_commitment=False, key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.NameConstraints(
                permitted_subtrees=[x509.IPAddress(ipaddress.ip_network(n)) for n in PRIVATE_NETS]
                + [x509.DNSName("localhost")], excluded_subtrees=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    _write_key(key, key_p)
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key, cert


def ensure_server_cert(tls_dir: Path, ip: str) -> tuple[Path, Path]:
    """Certificate for this computer's current Wi-Fi address. Re-issued when the address changes;
    the phone keeps trusting it because the authority stays the same."""
    ca_key, ca_cert = ensure_ca(tls_dir)
    key_p, cert_p = tls_dir / "server.key", tls_dir / "server.crt"
    want = {ipaddress.ip_address(ip), ipaddress.ip_address("127.0.0.1")}
    if key_p.exists() and cert_p.exists():
        old = x509.load_pem_x509_certificate(cert_p.read_bytes())
        san = old.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if set(san.get_values_for_type(x509.IPAddress)) == want and old.not_valid_after_utc > _now() + timedelta(days=30):
            return cert_p, key_p
    if not ipaddress.ip_address(ip).is_private:
        raise ValueError(f"{ip} is not a home-network address; the phone app only works on a private Wi-Fi")
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)]))
            .issuer_name(ca_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            # phones reject server certificates valid for more than ~13 months
            .not_valid_before(_now() - timedelta(days=1)).not_valid_after(_now() + timedelta(days=395))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(a) for a in sorted(want)] + [x509.DNSName("localhost")]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write_key(key, key_p)
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(serialization.Encoding.PEM))
    return cert_p, key_p


def ca_der(tls_dir: Path) -> bytes:
    """The authority in the binary form Android and iPhone install from a download."""
    _, cert = ensure_ca(tls_dir)
    return cert.public_bytes(serialization.Encoding.DER)


def ca_fingerprint(tls_dir: Path) -> str:
    _, cert = ensure_ca(tls_dir)
    h = cert.fingerprint(hashes.SHA256()).hex().upper()
    return " ".join(h[i:i + 4] for i in range(0, 16, 4))
