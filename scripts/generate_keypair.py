"""Generate an RSA key pair for Snowflake key-pair authentication.

Writes the private key to .secrets/datamind_rsa_key.p8 (git-ignored) and prints the
public key body to paste into `ALTER USER ... SET RSA_PUBLIC_KEY = '...'`.
"""

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

out_dir = Path(__file__).resolve().parent.parent / ".secrets"
out_dir.mkdir(exist_ok=True)
key_path = out_dir / "datamind_rsa_key.p8"
if key_path.exists():
    raise SystemExit(f"{key_path} already exists; delete it first if you really want a new key.")

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
body = "".join(line for line in pub.splitlines() if "-----" not in line)
print(f"Private key written to {key_path}\nSet in .env:  SNOWFLAKE_PRIVATE_KEY_FILE=.secrets/datamind_rsa_key.p8\n")
print("Public key (paste into RSA_PUBLIC_KEY):\n" + body)
