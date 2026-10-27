# deploy/runpod/keycloak

`realm-template.json` is the **sanitised** export of realm `kerala-police-drone`
(no users, no password hashes, client secrets masked, signing keys removed):

```bash
deploy/runpod/tools/export_realm.sh      # raw export → ../dvas_secrets_local/ (outside git)
                                          # sanitised  → deploy/runpod/keycloak/realm-template.json
```

On the pod, `bin/keycloak-start.sh` imports it once (if present), then
`bin/dvas-bootstrap.sh` runs `setup_keycloak.py` on every boot to set the generated
client secrets, the users from `/workspace/dvas/secrets/users.json` and the realm
`frontendUrl` (= current pod URL). Without a template, `setup_keycloak.py` creates
the realm, clients, roles and users from scratch.
