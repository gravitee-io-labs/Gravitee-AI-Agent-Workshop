"""Generate the EC keypair used by HOSTED_DELEGATED agent instances.

The private PEM lands on a shared volume that the hotel-agent container
mounts read-only. The public JWK is returned so it can be PATCHed onto
the blueprint application's `settings.oauth.jwks`.
"""

import os
from typing import Any, Dict

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey


def _int_to_base64url(value: int, length: int) -> str:
    import base64
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode("ascii")


def _public_key_to_jwk(public_key: EllipticCurvePublicKey, kid: str) -> Dict[str, Any]:
    numbers = public_key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _int_to_base64url(numbers.x, 32),
        "y": _int_to_base64url(numbers.y, 32),
        "kid": kid,
        "use": "sig",
        "alg": "ES256",
    }


def ensure_agent_keypair(private_pem_path: str, kid: str) -> Dict[str, Any]:
    """Generate an EC P-256 keypair if one doesn't already exist on disk.

    Writes the PEM to *private_pem_path* (creating parent dirs) and returns
    the matching public JWK Set ready for `settings.oauth.jwks`.
    """
    os.makedirs(os.path.dirname(private_pem_path), exist_ok=True)

    if os.path.exists(private_pem_path):
        with open(private_pem_path, "rb") as fh:
            private_key = serialization.load_pem_private_key(fh.read(), password=None)
    else:
        private_key = ec.generate_private_key(ec.SECP256R1())
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        with open(private_pem_path, "wb") as fh:
            fh.write(pem)
        os.chmod(private_pem_path, 0o644)

    return {"keys": [_public_key_to_jwk(private_key.public_key(), kid)]}
