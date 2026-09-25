#!/usr/bin/env python3
"""Génère/réinitialise le mot de passe administrateur /stat avec Argon2id.

Le script peut être lancé avec le Python système : si Argon2 n'y est pas
installé, il se relance automatiquement avec le venv BVMAC voisin.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path


def _ensure_venv() -> None:
    try:
        import argon2  # noqa: F401
        return
    except ModuleNotFoundError:
        root = Path(__file__).resolve().parents[1]
        vpy = root / ".venv" / "bin" / "python"
        if vpy.is_file() and Path(sys.executable).resolve() != vpy.resolve():
            os.execv(str(vpy), [str(vpy), str(Path(__file__).resolve()), *sys.argv[1:]])
        raise SystemExit(
            "Argon2 est absent. Lance le script avec le Python du venv BVMAC : "
            f"{vpy}"
        )


_ensure_venv()
from argon2 import PasswordHasher, Type  # noqa: E402

PH = PasswordHasher(
    time_cost=2,
    memory_cost=19456,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env", default="/etc/bvmac/stat.env")
    p.add_argument("--password")
    args = p.parse_args()

    password = args.password or secrets.token_urlsafe(18) + "!7"
    path = Path(args.env)
    existing: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                existing[k] = v

    existing["BVMAC_STAT_PASSWORD_HASH"] = PH.hash(password)
    existing.setdefault("BVMAC_STAT_SESSION_HOURS", "12")
    existing["BVMAC_COOKIE_SECURE"] = "1"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(f"{k}={v}" for k, v in sorted(existing.items())) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o640)
    print(password)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
