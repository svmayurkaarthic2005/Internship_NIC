"""
Digital Signature Certificate blobs for the sample database.

The DSC columns in the extracts hold base64-encoded PKCS#7 signedData, wrapped
at 76 characters -- the detached signature a Tahsildar applies to a patta
transfer order (workflow_guide.txt step 6). This module produces the real
thing: an actual X.509 certificate and an actual PKCS#7 SignedData structure,
DER-encoded and base64-wrapped, so the column parses with any PKCS#7 reader.

The certificates are self-signed by a clearly-labelled sample CA. The subject
follows the shape used by revenue-department signing certs (CN = officer,
O = REVENUE DEPARTMENT, ST = Tamil Nadu), but the issuer is *not* a real
certifying authority -- these must never be presentable as genuine credentials.
"""
from __future__ import annotations

import base64
import datetime as _dt

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, pkcs7
from cryptography.x509.oid import NameOID

# Deliberately not the name of any real licensed CA.
SAMPLE_CA_NAME = "SIS Sample Data Sub-CA (NOT A REAL CA)"

_KEY_SIZE = 2048
_signers: list[tuple[str, x509.Certificate, rsa.RSAPrivateKey]] = []


def _build_signer(officer: str, serial: int) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=_KEY_SIZE)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "REVENUE DEPARTMENT"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Tamil Nadu"),
        x509.NameAttribute(NameOID.POSTAL_CODE, "628001"),
        x509.NameAttribute(NameOID.COMMON_NAME, officer.upper()),
    ])
    issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SIS Chatbot Sample Data"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Sub-CA"),
        x509.NameAttribute(NameOID.COMMON_NAME, SAMPLE_CA_NAME),
    ])
    not_before = _dt.datetime(2022, 1, 1, tzinfo=_dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(serial)
        .not_valid_before(not_before)
        .not_valid_after(not_before + _dt.timedelta(days=1461))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=True,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.RFC822Name("sis.sample@localhost")]),
            critical=False)
        .sign(key, hashes.SHA256())
    )
    return cert, key


def init_signers(officers: list[str]) -> None:
    """Generate one signing certificate per officer, in the order given.

    Order is preserved so a caller indexing by position gets the certificate
    belonging to the officer it credits in the username column.
    """
    global _signers
    unique = list(dict.fromkeys(officers))
    _signers = [(o, *_build_signer(o, 0x21A6430000000000 + i))
                for i, o in enumerate(unique, start=1)]


def signer_count() -> int:
    return len(_signers)


def _wrap(b64: str, width: int = 76) -> str:
    return "\n".join(b64[i:i + width] for i in range(0, len(b64), width))


def sign(payload: str, signer_index: int) -> tuple[str, str]:
    """PKCS#7-sign `payload`.

    Returns (sha256_hex_of_payload, base64_pkcs7_wrapped_at_76_chars).
    """
    if not _signers:
        raise RuntimeError("call init_signers() first")
    _name, cert, key = _signers[signer_index % len(_signers)]
    data = payload.encode("utf-8")
    der = (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(data)
        .add_signer(cert, key, hashes.SHA256())
        .sign(Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature,
                             pkcs7.PKCS7Options.Binary])
    )
    digest = hashes.Hash(hashes.SHA256())
    digest.update(data)
    return digest.finalize().hex(), _wrap(base64.b64encode(der).decode("ascii"))


def signer_name(signer_index: int) -> str:
    """The officer whose certificate signs at this index."""
    return _signers[signer_index % len(_signers)][0]


def certificate_pem(signer_index: int) -> str:
    _name, cert, _key = _signers[signer_index % len(_signers)]
    return cert.public_bytes(serialization.Encoding.PEM).decode()


if __name__ == "__main__":
    import time
    init_signers([f"officer_{i}" for i in range(6)])
    t0 = time.time()
    for i in range(200):
        h, blob = sign(f"patta|7585|{i}", i)
    print(f"200 signatures in {time.time() - t0:.2f}s")
    h, blob = sign("patta|7585|demo", 0)
    print("sha256:", h)
    print("blob first 3 lines:")
    print("\n".join(blob.splitlines()[:3]))
    print("lines:", len(blob.splitlines()), "chars:", len(blob))
    # prove it parses back as PKCS#7
    der = base64.b64decode("".join(blob.splitlines()))
    certs = pkcs7.load_der_pkcs7_certificates(der)
    print("parsed PKCS#7, certs inside:", len(certs))
    print("subject:", certs[0].subject.rfc4514_string())
    print("issuer :", certs[0].issuer.rfc4514_string())
