#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text(encoding='utf-8').splitlines():
            if not raw or raw.lstrip().startswith('#') or '=' not in raw:
                continue
            k, v = raw.split('=', 1)
            out[k.strip()] = v.strip()
    return out


def write_env(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(f'{k}={v}' for k, v in sorted(values.items())) + '\n', encoding='utf-8')


def public_from_private(key: ec.EllipticCurvePrivateKey) -> str:
    raw = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return b64url(raw)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--private', default='/etc/bvmac/vapid_private.pem')
    ap.add_argument('--env', default='/etc/bvmac/app.env')
    ap.add_argument('--subject', default='mailto:admin@example.com')
    args = ap.parse_args()

    private_path = Path(args.private)
    env_path = Path(args.env)
    if private_path.is_file():
        key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise SystemExit('La clé VAPID existante n’est pas une clé EC valide.')
    else:
        private_path.parent.mkdir(parents=True, exist_ok=True)
        key = ec.generate_private_key(ec.SECP256R1())
        private_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        os.chmod(private_path, 0o640)

    values = read_env(env_path)
    values['BVMAC_VAPID_PRIVATE_KEY'] = str(private_path)
    values['BVMAC_VAPID_PUBLIC_KEY'] = public_from_private(key)
    values['BVMAC_VAPID_SUBJECT'] = args.subject
    write_env(env_path, values)
    print(values['BVMAC_VAPID_PUBLIC_KEY'])


if __name__ == '__main__':
    main()
