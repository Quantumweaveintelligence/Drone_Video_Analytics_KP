#!/usr/bin/env python3
"""
sanitize_realm.py — RunPod plan Phase 1.5 / 2.8.

Turns a raw Keycloak realm export (kc.sh export --users realm_file) into a
template that is safe to commit and to import on the pod:

  * removes users (and their credentials / password hashes) and federated identities
  * replaces every client secret with "**********" (setup_keycloak.py sets the
    real, freshly generated secrets on the pod)
  * removes realm signing/encryption key providers (private keys) — Keycloak
    generates new keys on import
  * blanks SMTP / identity-provider secrets
  * keeps roles, clients, protocol mappers, scopes, flows and realm settings

Usage:  sanitize_realm.py <raw-export.json> <realm-template.json>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

MASK = "**********"
KEY_PROVIDER = "org.keycloak.keys.KeyProvider"


def sanitize(realm: dict[str, Any]) -> dict[str, Any]:
    out = dict(realm)
    for key in ("users", "federatedUsers"):
        out.pop(key, None)

    clients = []
    for client in out.get("clients", []) or []:
        c = dict(client)
        if "secret" in c:
            c["secret"] = MASK
        attrs = dict(c.get("attributes") or {})
        for a in list(attrs):
            if "secret" in a.lower():
                attrs[a] = MASK
        if attrs:
            c["attributes"] = attrs
        clients.append(c)
    if clients:
        out["clients"] = clients

    components = dict(out.get("components") or {})
    if KEY_PROVIDER in components:
        components.pop(KEY_PROVIDER)
    if components or "components" in out:
        out["components"] = components

    smtp = dict(out.get("smtpServer") or {})
    if "password" in smtp:
        smtp["password"] = MASK
        out["smtpServer"] = smtp

    idps = []
    for idp in out.get("identityProviders", []) or []:
        i = dict(idp)
        cfg = dict(i.get("config") or {})
        if "clientSecret" in cfg:
            cfg["clientSecret"] = MASK
        i["config"] = cfg
        idps.append(i)
    if idps:
        out["identityProviders"] = idps
    return out


def find_leaks(obj: Any, path: str = "") -> list[str]:
    """Paths of values that still look like secrets (defence in depth)."""
    leaks: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if k in ("secret", "hashedSaltedValue", "privateKey", "password", "clientSecret") \
                    and isinstance(v, str) and v and v != MASK:
                leaks.append(p)
            leaks.extend(find_leaks(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            leaks.extend(find_leaks(v, f"{path}[{i}]"))
    return leaks


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    raw = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    clean = sanitize(raw)
    leaks = find_leaks(clean)
    if leaks:
        print("ERROR: values that look like secrets remain:\n  " + "\n  ".join(leaks), file=sys.stderr)
        return 1
    Path(argv[2]).parent.mkdir(parents=True, exist_ok=True)
    Path(argv[2]).write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"sanitised realm '{clean.get('realm')}' → {argv[2]} "
          f"({len(clean.get('clients', []))} clients, users removed, key providers removed)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
