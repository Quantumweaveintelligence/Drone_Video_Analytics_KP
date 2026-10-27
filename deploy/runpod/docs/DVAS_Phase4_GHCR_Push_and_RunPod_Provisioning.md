# DVAS — Phase 4: Push to GHCR & provision RunPod

> Branch `runpod-v1` · Plan: `DVAS_RunPod_Deployment_Plan.md` Phase 4 · Written 27 Oct 2026 · **Part A rewritten 27 Oct
> for your build laptop (`user-Nitro-AN515-57`, Docker root on `/`)** · **A0 added 27 Oct (clean restart + safe cleanup)**
> RunPod console steps checked against docs.runpod.io (Credentials, Storage, Templates,
> Expose ports, Environment variables pages) on 27 Oct 2026. RunPod renames menus from
> time to time: if a label differs, look for the closest match.
>
> **Part A** (💻 your laptop, done by you) builds, tests and pushes the image — about 2–3 hours, mostly waiting.
> **Part B** (🌐 RunPod console, done by you) creates the volume, template and pod.
> **Part C** is troubleshooting. **Part D** lists what to send back before Phase 5.

---

## 0. Where you are

| Item | State (checked 27 Oct 2026, 10:45) |
|---|---|
| `runpod-v1` committed | ✅ last commits: *model weight manifest* (10:06) and *MinIO via source build, phase 4 fixes* (10:34) |
| `deploy/runpod/models.sha256` + `/mnt/NewVolume/dvas_models_stage/` | ✅ done (A4) — re-running A4 is harmless |
| MinIO build mode | ✅ `MINIO_FROM=source` written to the Dockerfile (A6) |
| Image built | ✅ `ghcr.io/quantumweaveintelligence/dvas-runpod:20261027-e5d7ef3` (commit `e5d7ef3`), 23.9 GB, 20 GB disk left — build 3 at 16:37 |
| Image tested / pushed | ❌ → steps A7–A9 (check A2d GPU runtime first) |
| Baseline DB dumps, realm template | ➖ skipped on purpose (see A5) — the pod has tested fallbacks |

**How much disk?** With the classic `overlay2` image store and the default `docker` builder (A2), peak use is about
**38 GB** from an empty Docker: base image ~20 GB unpacked + DVAS layers ~8–10 GB + frontend, MinIO source build and
cache ~5–7 GB. With the base image already pulled the build itself needs about **18 GB** more.
`build_image.sh` now checks this and stops before it can fill your disk. The model staging folder is on
`/mnt/NewVolume`, so it doesn't count. Don't build or pull anything else meanwhile.

> ⚠️ **Do not run `docker system prune --volumes` or `docker volume prune`.** The 23 volumes are the data of your
> old local compose stacks (Postgres, MinIO, Keycloak). They're only 5.3 GB; leave them alone.

---

# PART A — Build, test, push (💻 your laptop)

Do the steps in order and run every command **from the repo root** (`/mnt/NewVolume/DVAS_RUNPODV1`) unless the
step says otherwise. Text in `<angle brackets>` is a placeholder: replace it **including the brackets**
(e.g. `GHCR_USER=octocat`, not `GHCR_USER=<octocat>`). Each step ends with **✔ You should see** — don't go on until you do. If a step fails, stop
and send me the output (Part C lists the usual causes).

### A0. Starting over (clean restart) — do this first if an earlier attempt ran

You can always restart Part A from the top: every step is safe to repeat (git steps just say *nothing to commit*,
A4 rewrites the same checksums, the resolver writes the same Dockerfile lines). What must be clean is the
**terminal** and the **disk**.

> **Where did the 24 GB go?** Almost certainly not junk: the build pulled the ~10 GB base image (≈20 GB unpacked),
> the Go image and MinIO sources, and started the DVAS layers. That is the expected cost of a build — deleting it
> only means downloading it again. The real questions are whether it sits in the right image store (overlay2) and
> whether your build stopped half-way. a)–c) answer that.

**a) Stop the old run, then use a new terminal.** In the old terminal press **Ctrl+C** (once; wait for the prompt),
then close it. Open a **new terminal** — this drops anything exported earlier (`MINIO_IMAGE`, `MC_IMAGE`, `PY`, …),
which `build_image.sh` would otherwise pass into the build.

```bash
cd /mnt/NewVolume/DVAS_RUNPODV1
pgrep -af 'build_image|buildx|docker pull' || echo "nothing running"     # must print: nothing running
mkdir -p deploy/runpod/.logs
mv ~/dvas-build.log deploy/runpod/.logs/build-attempt1.log 2>/dev/null; ls deploy/runpod/.logs
```

`deploy/runpod/.logs/` is git-ignored and not sent into the build. Logs there are **readable by me directly** —
you don't have to paste them.

**b) Take a disk report** (read-only, deletes nothing; asks for your sudo password once for folder sizes):

```bash
chmod +x deploy/runpod/tools/*
deploy/runpod/tools/docker_disk_report.sh
```

It saves `deploy/runpod/.logs/disk-report-<time>.txt`. If anything in it is unclear, just tell me *"report saved"*.

**c) Free space — pick the row that matches the report:**

| The report shows | Do this | Free afterwards |
|---|---|---|
| `image store driver : overlay2` and `runpod/pytorch:2.8.0-…` in **Images** | keep the base image (saves a 10 GB download); drop only the half-finished build: `docker builder prune -af` | ≈ 25–28 GB → enough (needs 18) |
| `image store driver : overlayfs` | full clean + switch store: do **A2b** exactly as written (prunes images + cache, keeps volumes), then A3 pulls the base again | ≈ 45–47 GB |
| `overlay2`, **no** base image, but free space is still far below 45 GB | images from the *old* store are hidden but still on disk — see **d)** | ≈ 45–47 GB |

Always safe on top of that (none of it is needed by DVAS):

```bash
docker container prune -f                 # stopped containers only — volumes are kept
sudo apt clean                            # downloaded .deb packages
sudo journalctl --vacuum-size=200M        # old system logs
rm -rf ~/.cache/pip                       # pip download cache (rebuilt on demand)
df -h / | tail -1
```

> ⚠️ **Never delete:** Docker **volumes** (`docker volume prune`, `docker system prune --volumes`) — your old local
> stacks' data; `/mnt/NewVolume/dvas_models_stage/` — the weights every build needs; anything in the repo.

**d) Only if c) said so — clear the hidden old image store:**

```bash
echo '{ "features": { "containerd-snapshotter": true } }'  | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker
docker image prune -af && docker builder prune -af       # cleans the old store (volumes kept)
echo '{ "features": { "containerd-snapshotter": false } }' | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker
docker info --format '{{.Driver}}'                       # → overlay2
docker volume ls | wc -l                                 # still 24
df -h / | tail -1
```

(If `/etc/docker/daemon.json` had other settings, keep them and only change the `"features"` line.)

✔ You should see: *nothing running*, the store is `overlay2`, and free space as in the table. Then continue with
**A1** and run **one block at a time** — wait for each ✔ before the next. Pasting all commands at once hides the
first error under the output of the later ones.

### A1. Get the latest code onto `runpod-v1`

```bash
cd /mnt/NewVolume/DVAS_RUNPODV1
git switch runpod-v1
git pull
git status
```

If `git status` lists Phase 4 files as modified/untracked (`deploy/runpod/Dockerfile`, `tools/build_image.sh`,
`tools/push_ghcr.sh`, `tools/phase4_preflight.sh`, `tools/check_build_host.sh`, `RELEASES.md`, `README.md`, this
guide, `context_runpodv1.md`, `.gitignore`), commit them first — the image tag is the commit hash, so the image
must be built from a commit:

```bash
git add deploy/runpod context_runpodv1.md .gitignore
git commit -m "runpod-v1: phase 4 build/push tooling"
git push
```

Make the scripts executable (your NTFS drive has no real exec bits; `fileMode=false` means this won't show in git):

```bash
chmod +x deploy/runpod/bin/* deploy/runpod/tools/*
```

✔ You should see: `git status` → *nothing to commit, working tree clean* (untracked `.env`/`users.local.json` are fine).

### A2. Prepare Docker (once)

**a) Docker and buildx versions, and no sudo needed:**
```bash
docker version --format 'client {{.Client.Version}} / server {{.Server.Version}}'
docker buildx version
```
If `docker` needs `sudo`: `sudo usermod -aG docker $USER`, then log out and back in.

**b) Image store must be `overlay2`** (the newer containerd store keeps a compressed *and* an unpacked copy of every
layer — about +10 GB here, which you don't have):
```bash
docker info --format '{{.Driver}}'
```
- `overlay2` → good, go to c).
- `overlayfs` → switch to overlay2. Images pulled *before* the switch become invisible but keep using disk, so
  remove them first (this deletes **images and build cache only — your volumes are kept**):
  ```bash
  docker image ls                                   # e.g. the base image, if you already did A3
  docker image prune -af                            # remove all images
  docker builder prune -af                          # remove all build cache
  sudo cat /etc/docker/daemon.json 2>/dev/null      # if it exists, ADD the "features" key by hand instead
  echo '{ "features": { "containerd-snapshotter": false } }' | sudo tee /etc/docker/daemon.json
  sudo systemctl restart docker
  docker info --format '{{.Driver}}'                # → overlay2
  docker volume ls | wc -l                           # volumes are still there (24 lines = 23 volumes + header)
  df -h / | tail -1
  ```
  Then (re)do **A3** to pull the base image into the new store.

**c) Use the default builder** (other builder drivers write a second full copy of the image on `--load`):
```bash
docker buildx use default
docker buildx ls          # the line marked * must say driver "docker"
```

**d) GPU in containers** (only needed for the local test in A7):
```bash
docker info | grep -i runtimes        # must list nvidia
nvidia-smi                            # your laptop GPU
```
If `nvidia` is missing, install the NVIDIA Container Toolkit (`sudo apt install nvidia-container-toolkit &&
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`). You can still build and
push without it; you just can't do A7.

✔ You should see: `overlay2`, builder `default` with driver `docker`, `nvidia` in the runtimes.

### A3. Choose and pull the base image

The RunPod PyTorch 2.8 image (checked on Docker Hub 27 Oct 2026; amd64, ~10 GB compressed):

```bash
export BASE_IMAGE=runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04
echo "export BASE_IMAGE=$BASE_IMAGE" >> ~/.bashrc      # so every new terminal has it
docker pull "$BASE_IMAGE"                               # 10–30 min depending on your line
docker image ls "$BASE_IMAGE"
df -h / | tail -1
```

(If RunPod's console shows a different image for its official *PyTorch 2.8* template — **Templates → Explore** —
you may use that instead; the Dockerfile handles Ubuntu 22.04 and 24.04 bases.)

✔ You should see: the image listed at roughly 20 GB, and about **27 GB still free** on `/`.

### A4. Stage the model weights (required — the build checks them)

```bash
deploy/runpod/tools/stage_models.sh
```

This copies every `*.pt`, `*.pth`, `*.onnx`, `*.pb` under `dvas_analytics/` (except the unused RF-DETR
`checkpoint_best_total.pth` files) into `/mnt/NewVolume/dvas_models_stage/analytics/` and writes
`deploy/runpod/models.sha256`.

✔ You should see: `staged 15 files …` (13 weights + vehicle `.pb` files) and **`all expected weights present`**.
If it prints `MISSING: …`, that weight isn't in your working copy — copy it from `/mnt/NewVolume/POLICE_DRONE/dvas_analytics/…`
to the same path in `DVAS_RUNPODV1` and run the script again.

Commit the manifest (only the checksums — the weights stay out of git):

```bash
git add deploy/runpod/models.sha256
git commit -m "runpod-v1: model weight manifest"
git push
```

Back up the staging folder somewhere safe (NAS / external disk): every future build needs it.

### A5. Skip the optional Phase 1 extras (for now)

`dump_baseline_schema.sh`, `export_realm.sh` and `collect_versions.sh` need your **local compose stacks running**.
Your Docker has no images any more, so starting them would rebuild ~30 GB of images — you don't have the space.
**Skip them.** The pod then builds its schema from the migrations (tested on PostgreSQL 16) and creates the Keycloak
realm, clients, roles and users with `setup_keycloak.py`. You can add the dumps in a later image.

### A6. Preflight, then build

```bash
deploy/runpod/tools/phase4_preflight.sh
```

✔ You should see: `Preflight passed (N warning(s))`. These **WARN**s are expected and fine:
- `no analytics baseline dump`, `no dashboard baseline dump`, `no realm template` (A5)
- `47 GB free … enough with overlay2` (disk)
- `not logged in to ghcr.io` (you log in during A8)

Any **FAIL** must be fixed first.

**MinIO (server + `mc` client).** MinIO stopped publishing community Docker images in 2025, so by default the
Dockerfile now **compiles MinIO and mc from their GitHub release tags** inside the build (`ARG MINIO_FROM=source`,
using `golang:1-bookworm`). Nothing to type by hand — run the helper once; it uses a published image if your machine
can pull one, otherwise it keeps the source build and records the newest release tags:

```bash
unset MINIO_IMAGE MC_IMAGE                                   # clear any earlier manual attempt
deploy/runpod/tools/resolve_minio_images.sh --write
git add deploy/runpod/Dockerfile
git commit -m "runpod-v1: MinIO build mode"
git push
deploy/runpod/tools/phase4_preflight.sh                      # again → no FAIL
```

✔ Expected (what your machine showed last time — no image pullable), ending with:
```
No usable MinIO image pair → building MinIO + mc from source in the Docker build.
MINIO_FROM=source
MINIO_GIT_TAG=RELEASE.2025-10-15T17-29-55Z      (or newer)
MC_GIT_TAG=RELEASE.2025-08-13T08-35-41Z         (or newer)
GO_IMAGE=golang:1-bookworm
Written to deploy/runpod/Dockerfile: …
```
The `skip … reason: …` lines above it are normal. If an image *is* pullable you get `MINIO_FROM=image` and two
`…@sha256:…` lines instead — also fine. The source build adds ~5–10 min and ~2 GB of temporary cache to A6.
If it ends with `FAIL: …`, send me the output.

Preflight then shows `OK  MinIO source build: github.com/minio/minio tag … exists` (and the same for `mc`).

**WARN `uncommitted changes`**: run `git status`, commit what's listed (`git add -A && git commit -m "…" && git push`,
never `.env` or `users.local.json` — those are git-ignored anyway), then run the preflight again. The image tag is the
commit hash, so build only from a clean tree.

**WARN `image store: containerd snapshotter (overlayfs)`**: do A2b before building (≈10 GB saved — you need it with 47 GB).

**Build** (30–60 min the first time; keep the laptop on mains power and stop it from sleeping):

```bash
deploy/runpod/tools/build_image.sh
```

It writes its own log to `deploy/runpod/.logs/build-<time>.log` (I can read it there) and first prints
`disk: … GB free …; need about … GB`. If it prints **STOP: not enough free space**, go back to A0 c) — don't force it.

Optional, in a second terminal, to watch the disk:
```bash
watch -n 30 'df -h / | tail -1; docker system df'
```

✔ You should see, at the end:
```
built ghcr.io/quantumweaveintelligence/dvas-runpod:20261027-<sha>
size: …GB
```
and `cat deploy/runpod/.last_build_tag` prints that tag.

If the build fails: don't retry blindly — tell me *"build failed"*; I read the newest log in `deploy/runpod/.logs/` myself. If it failed with
*no space left on device*: `docker builder prune -f` (removes only build cache), check `df -h /`, and rerun the
build command — finished stages are reused.

### A7. Local smoke test (~15 min)

Make sure nothing else uses port 8088 (`ss -ltnp | grep 8088` prints nothing). Then:

```bash
deploy/runpod/tools/run_local_test.sh ghcr.io/quantumweaveintelligence/dvas-runpod:$(cat deploy/runpod/.last_build_tag)
docker logs -f dvas-pod-test
```

The first boot runs in two parts. First `dvas-init` creates the secrets and the Postgres cluster, and you watch it in
`docker logs` (lines start with `[dvas-init]`). It ends with `starting supervisord`. Only then does
`/var/log/dvas/bootstrap.log` exist; a `tail` before that fails with *No such file or directory*, which is harmless.
Press Ctrl-C (the container keeps running), then follow the bootstrap. `tail -F` waits until the file appears:

```bash
docker ps --filter name=dvas-pod-test        # STATUS must be "Up …" (if "Exited": docker logs dvas-pod-test | tail -50)
docker exec -it dvas-pod-test tail -F /var/log/dvas/bootstrap.log
```

Wait until it says **`bootstrap complete`** (first boot: 5–10 min), press Ctrl-C, then:

```bash
docker exec -it dvas-pod-test dvas-ctl status       # every program RUNNING, bootstrap EXITED
docker exec -it dvas-pod-test dvas-ctl health
docker exec -it dvas-pod-test cat /opt/dvas-os-packages.txt
sudo cat ~/dvas-pod-test-volume/dvas/secrets/users.json   # test passwords (files are root-owned)
```

1. Browser → http://localhost:8088 → log in as `control@dvas.kp` with the `control_001` password from `users.json`.
   You should land on the command dashboard.
2. Restart test (`dvas-ctl` only exists *inside* the container — always via `docker exec`):
   ```bash
   docker restart dvas-pod-test && sleep 180
   docker exec -it dvas-pod-test dvas-ctl status          # backend RUNNING, bootstrap EXITED
   docker logs dvas-pod-test 2>&1 | grep -c "first boot: creating roles"     # must print 1
   ```
   If `backend` is FATAL: save `docker exec dvas-pod-test tail -300 /var/log/dvas/backend.log >
   deploy/runpod/.logs/restart-backend.log` and tell me.

✔ Pass = login works, data survives the restart, and every program except perhaps some analyzers is RUNNING.

Your laptop GPU has far less memory than the L40S (48 GB): one or more analyzers may show CUDA *out of memory* in
`docker exec dvas-pod-test dvas-ctl logs <name>` or restart a few times. That is expected here and is **not** a
reason to stop.

Clean up the test (the image stays):

```bash
docker rm -f dvas-pod-test
sudo rm -rf ~/dvas-pod-test-volume          # test data + test secrets only
```

### A8. Push to GHCR

You need your GitHub **username** and the classic PAT with `write:packages`.

```bash
GHCR_USER=<your-github-username> deploy/runpod/tools/push_ghcr.sh
#   GHCR personal access token (write:packages):   ← paste the PAT (nothing is shown), press Enter
```

The first push uploads ~12–15 GB (roughly 40 min at 50 Mbit/s upload). If the connection drops, run the same
command again — finished layers aren't re-sent.

✔ You should see, at the end:
```
pushed:  ghcr.io/quantumweaveintelligence/dvas-runpod:20261027-<sha>
digest:  sha256:…
RunPod template → Container Image:  ghcr.io/quantumweaveintelligence/dvas-runpod:20261027-<sha>
```

Note that line — it's what you paste into the RunPod template (B5). Then record the release:

```bash
git add deploy/runpod/RELEASES.md
git commit -m "release $(cat deploy/runpod/.last_build_tag)"
git push
docker logout ghcr.io            # docker login stored the PAT base64-encoded in ~/.docker/config.json
```

Rules: never deploy `latest` (the scripts refuse it); never put the PAT on the command line.

### A9. Check the package on GitHub

1. Open https://github.com/orgs/Quantumweaveintelligence/packages → **dvas-runpod**.
2. **Visibility: Private.** (Package settings → *Danger Zone* shows the current visibility.)
3. It should show *"Connected to Drone_Video_Analytics_KP"* (the image carries the repo label).
4. If A8 failed with `permission_denied: create_package` / `write_package`: an org owner must allow members to create
   private packages (**Organization → Settings → Packages**), and the PAT must belong to an org member.

### A10. Free the space again (optional)

```bash
docker builder prune -f       # build cache (a few GB); the next build is then slower
df -h / | tail -1
```

Keep the DVAS image and the base image if you'll rebuild soon: the next build then only uploads the changed layers.

### ✅ Part A done when
- [ ] Docker: overlay2, default builder, NVIDIA runtime
- [ ] `models.sha256` committed; staging folder backed up
- [ ] Image built; local test passed (login + restart)
- [ ] Image pushed; tag + digest in `RELEASES.md`, committed; package private
- [ ] You have the image line for the RunPod template → go to Part B

---

# PART B — Provision RunPod (🌐 console, done by you)

### B1. Account basics (once)

1. Turn on **2FA** and add credit.
2. Add your SSH key:
   - Go to **Credentials**, then the **SSH Public Keys** tab, then **Add SSH Key**.
   - Paste the single line from `~/.ssh/id_ed25519.pub`. If you don't have a key yet: `ssh-keygen -t ed25519 -C dvas-runpod`.
   - RunPod injects every account key into pods as `PUBLIC_KEY`. Our `dvas-init.sh` writes it to `authorized_keys`, and SSH accepts keys only (no passwords).

### B2. Registry credential for GHCR

Go to **Credentials**, then the **Container Registry Auth** tab, then **Add Credential**:

| Field | Value |
|---|---|
| Name | `ghcr-dvas` |
| Registry URL | `ghcr.io` |
| Username | your GitHub username |
| Password / access token | a PAT with **`read:packages`** |

Then click **Save**.

> **Recommended:** create a second classic PAT with only `read:packages` for RunPod, and keep the `write:packages` one on the build machine. Otherwise RunPod holds a token that can overwrite your images. Rotate both every 90 days (plan S9).

### B3. Pick the data centre (plan 0.2)

1. Go to **Pods → Deploy → Secure Cloud** and filter on **L40S**.
2. Note which data centres show L40S **and** offer network volumes.
3. Prefer the one with the lowest latency from Kerala. You'll measure it from the field site in Phase 8.

A network volume is tied to one data centre, and a pod can only use a volume in its own data centre. That's why you choose here, before B4.

### B4. Network volume

Go to **Storage**, then **New Network Volume**:

| Field | Value |
|---|---|
| Data center | the one from B3 |
| Name | `dvas-vol-test` |
| Size | **30 GB** (you can grow it later, never shrink it) |
| Storage tier | **Standard** (~$0.07/GB-month → ≈ $2.10/month). Choose High-Performance only if Postgres turns out slow (risk R3) |

Then click **Create Network Volume**.

**Important:** a network volume can only be attached when a pod is *deployed*. You can't add it to an existing pod.

### B5. Template

Go to **Templates** (My Templates), then **New Template**:

| Field | Value |
|---|---|
| Name | `dvas-runpod-test` |
| Visibility | **Private** |
| Compute type | NVIDIA GPU |
| Container image | `ghcr.io/quantumweaveintelligence/dvas-runpod:<TAG>`, the **exact** tag printed by `push_ghcr.sh` |
| Registry credentials | `ghcr-dvas` |
| Container start command | **leave empty**: the image's own entrypoint (`tini → dvas-init.sh`) must run |
| Container disk | **50 GB** (raise to 80 GB if `docker image ls` shows the image above ~30 GB) |
| Volume disk / Volume mount path | mount path **`/workspace`** (the network volume replaces the volume disk) |
| HTTP ports | **80** (label `dvas-web`). Our nginx is the only public entry point |
| TCP ports | **22** (SSH) |
| Environment variables | `TZ` = `Asia/Kolkata`, `DVAS_DEV_MODE` = `0` |

Then click **Save Template**.

- **Don't add secrets.** The pod generates them on the volume at first boot (`/workspace/dvas/secrets/`).
- Optional overrides, all documented in `deploy/runpod/env/dvas.env.example`: `CROWD_MAX_FPS`, `QUEUE_MAX_LENGTH`, `LOG_LEVEL`, and `PUBLIC_BASE_URL` (only for a custom domain, Phase 12.8).
- **Changing environment variables later restarts the pod.** That's harmless here because all state is on `/workspace`.

### B6. Deploy the pod

1. Go to **Pods**, then **Deploy**.
2. Choose **Secure Cloud**.
3. Under **Network Volume**, choose `dvas-vol-test`. This locks the data centre.
4. Select GPU **L40S** with count **1**.
5. Choose template `dvas-runpod-test` (**Change Template** / **Edit Template**). Check that the image tag, ports 80/22, container disk and `/workspace` are all there.
6. Check the summary:
   - 1 × L40S, 48 GB VRAM
   - vCPU / RAM: the plan assumes **16 vCPU / 62 GB**. Write down what you actually get
   - the on-demand price per hour
7. Click **Deploy On-Demand**.

### B7. Watch the first boot

- **"Running" doesn't mean ready.** The first pull of the image takes about 10–20 minutes.
- Open the pod's **Logs** (container logs). You should see:
  ```
  [dvas-init] … initialising Postgres cluster in /workspace/dvas/pgdata
  [db-bootstrap] … database bootstrap complete
  [dvas-init] … starting supervisord (public URL: https://<POD_ID>-80.proxy.runpod.net)
  ```
- First start of Keycloak, model loading and the bootstrap then take another **5–10 minutes**. Until nginx is up, the proxy URL returns 502/404.
- If the log says `WARNING: /workspace is not a mounted volume`, the network volume isn't attached. **Terminate the pod and deploy again** (B6 step 3).

### B8. Connect

Open the **Connect** menu on the pod:

- **HTTP Service, port 80**: `https://<POD_ID>-80.proxy.runpod.net`. This is the DVAS login page.
- **Direct TCP Ports**: it shows something like `213.173.x.x:NNNNN -> :22`. Connect with:
  ```bash
  ssh root@<IP> -p <PORT> -i ~/.ssh/id_ed25519
  ```
  Use the **TCP** route. The "basic SSH" proxy (`ssh.runpod.io`) is designed around RunPod's own images.

### B9. Checks on the pod (Phase 4 "Done when")

```bash
df -h /workspace                      # ~30 GB network volume
dvas-ctl status                       # every program RUNNING, bootstrap EXITED (status 0)
cat /workspace/dvas/state/bootstrap_ok
cat /opt/dvas-os-packages.txt         # which Postgres/Java/Valkey variant the image used
nvidia-smi                            # L40S, analyzer processes listed
curl -s localhost/healthz             # ok
```

Then open the proxy URL in a browser. The DVAS login page should appear.

**Secure the generated credentials now.** Phase 5.2 says the same, but don't wait:

```bash
# from your machine
mkdir -p ~/dvas-runpod-secrets && chmod 700 ~/dvas-runpod-secrets
scp -P <PORT> root@<IP>:/workspace/dvas/secrets/{dvas.env,users.json} ~/dvas-runpod-secrets/
```

Put both files in the team password manager and keep an offline copy. Losing `FACE_VECTOR_ENCRYPTION_KEY` makes stored face embeddings unreadable.

### B10. Stop vs terminate (plan 4.7): record the answer

1. Open the pod's menu and check whether **Stop** is offered for a pod with a network volume.
2. If it is: Stop the pod, then Start it.
   - The **pod ID, and so the URL, stays the same.**
   - The GPU is released while stopped. On Start you may get "Zero GPU Pods" if no L40S is free in that data centre. Retry later.
3. **Terminate** deletes the pod but not the volume.
   - A new deploy with the same volume gets a **new pod ID / URL**.
   - Our bootstrap updates Keycloak's `frontendUrl` automatically.
   - Tell the edge team the new URL.
4. Write down what you saw. It goes into the runbook (Phase 12.1) and into `context_runpodv1.md`.

### B11. Updating to a new image later

1. Build and push a new tag (A6–A8).
2. Change **Container image** in the template (or the pod's **Edit Pod**) to that tag. The pod restarts; the volume, database and secrets stay.
3. **Rollback:** set the previous tag from `RELEASES.md`.

### B12. Cost control

- The GPU is billed per second while the pod runs. Stop or terminate it outside test windows.
- The volume costs ≈ $2.10/month on its own.
- Check the billing page weekly.

---

# PART C — Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| `push_ghcr.sh`: `denied: permission_denied: create_package/write_package` | PAT lacks `write:packages`, or the org doesn't let members create private packages (A9) |
| `push_ghcr.sh`: `unauthorized` | stale login → `FORCE_LOGIN=1 GHCR_USER=… deploy/runpod/tools/push_ghcr.sh` |
| Push keeps restarting on big layers | slow or unstable upload. `docker push` resumes per layer, so just run it again |
| Build: `STOP: not enough free space` | the guard in `build_image.sh` — free space per A0 c)/d), then rerun. Only override (`FORCE_LOW_DISK=1`) if you know the cache already holds most layers |
| Build: `no space left on device` | `docker builder prune -f`, check `df -h /`, rerun `build_image.sh` (A6). Make sure the image store is `overlay2` (A2b) |
| Build fails in stage `minio-source` / `mc-source` (`git clone`, `go: downloading`, `go build`) | network hiccup → rerun `build_image.sh` (finished stages are cached). Still failing → tell me; I read `deploy/runpod/.logs/` |
| Build: `failed to resolve source metadata for docker.io/minio/…` | the Dockerfile still names a dead MinIO tag → rerun `resolve_minio_images.sh --write` (A6), commit, build again |
| Build: `sha256sum: WARNING … did NOT match` | weights changed after staging → rerun `stage_models.sh` (A4), commit `models.sha256`, build again |
| Build: pip can't find `paddlepaddle==2.6.2` | see the note at the top of `deploy/runpod/requirements/anpr.txt`; send me the log tail |
| Build: `No interpreter with torch+torchvision` | the base image isn't a PyTorch image, or torch lives elsewhere → `PY=/path/to/python deploy/runpod/tools/build_image.sh` |
| RunPod: image pull fails, 401/403/"not found" | template has no `ghcr-dvas` credential, the tag has a typo, or the PAT lacks `read:packages` / the user can't see the private package |
| RunPod: pull fails with a disk error | raise **Container disk** (B5) to 80 GB |
| Pod restarts in a loop | open **Logs**. `dvas-init.sh` failed: usually the volume isn't mounted, or Postgres can't `chmod`/`fsync` on the volume (risk R3, fallback in the plan) |
| Proxy URL gives **502** | nginx or the backend isn't up yet → SSH in, run `dvas-ctl status` and `tail /var/log/dvas/supervisord.log` |
| Proxy URL gives **524** | a request ran longer than Cloudflare's **100 s** limit (PDF/65B exports, Phase 6) |
| Login page loads but login fails | `tail -50 /var/log/dvas/bootstrap.log /var/log/dvas/keycloak.log`. Bootstrap must say "bootstrap complete" |
| An analyzer is FATAL | `dvas-ctl logs <name>`: missing weight (re-check A4) or CUDA error |

---

# PART D — Send me this before Phase 5

1. The image tag and digest (the new row in `RELEASES.md`).
2. Pod ID / proxy URL, data centre, and the actual vCPU / RAM.
3. Output of `dvas-ctl status`, `cat /opt/dvas-os-packages.txt` and `df -h / /workspace`.
4. `tail -100 /var/log/dvas/bootstrap.log` (**contains no secrets**; don't send `dvas.env` or `users.json`).
5. The stop-vs-terminate answer (B10).

## ✅ Phase 4 done when

- [ ] Image pushed with an immutable tag; row in `RELEASES.md`; package private
- [ ] RunPod: 2FA, SSH key, `ghcr-dvas` credential (read-only PAT)
- [ ] Network volume `dvas-vol-test` (30 GB) in the chosen data centre
- [ ] Template `dvas-runpod-test` as in B5
- [ ] Pod running on 1 × L40S, Secure Cloud, `df -h /workspace` ≈ 30 GB
- [ ] Proxy URL shows the DVAS login page; SSH over TCP works
- [ ] `dvas.env` + `users.json` copied off the pod into the password manager
- [ ] Stop-vs-terminate behaviour recorded

**↩ Rollback.** Terminate the pod; the network volume keeps the data. Redeploy with the previous tag from `RELEASES.md`.
