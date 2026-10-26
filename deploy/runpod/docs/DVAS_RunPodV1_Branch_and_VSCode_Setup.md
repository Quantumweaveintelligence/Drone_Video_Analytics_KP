# DVAS — `runpod-v1` Branch Setup & VS Code Connection Guide

> **Repository:** https://github.com/Quantumweaveintelligence/Drone_Video_Analytics_KP
> **Base branch:** `testing` → **new branch:** `runpod-v1`
> **Working copy:** `/mnt/NewVolume/DVAS_RUNPODV1` (duplicate of the app; the original stays in `/mnt/NewVolume/POLICE_DRONE`)
> **Machine:** Ubuntu workstation (`user-nitro-an515-57`) · Prepared 26 Oct 2026
> **Used by:** Phase 1.1 of `DVAS_RunPod_Deployment_Plan.md`. The edge teammate can follow the same steps with branch `edge-exe-v1`.

**Branch name.** Git branch names can't contain spaces, so "runpod vi / runpod v1" becomes **`runpod-v1`**. Creating it from `testing` gives you everything that's on `testing` — the same result as "a new branch with testing merged into it". Later updates from `testing` are merged with one command (Step 7).

---

## 0. Check of your duplicate folder — what's missing

I compared `/mnt/NewVolume/DVAS_RUNPODV1` with the original folder and with the list of files git tracks on `testing` (read from the repository index). The four app folders were copied completely, including model weights and `.env` files. But:

| # | Finding | Why it matters | Fixed in |
|---|---|---|---|
| M1 | 🔴 **No `.git` folder.** The duplicate isn't a git repository. | You can't create a branch, commit or push from it. | Step 2 |
| M2 | 🔴 **Root `.gitignore` not copied.** | If you ran `git init` + `git add .` you would commit the 5.7 GB of drone videos (`dvas_ingest/DJI_0110.MP4`, `DJI_0111.MP4`), the Windows `.venv`, `backend/venv`, `node_modules`, every `.env` and ~1.4 GB of model weights. GitHub rejects files > 100 MB, and secrets would leak. | Step 2 (comes back with git) |
| M3 | 🔴 **`dvas_analytics/crowd/main.py` is committed as 0 bytes on `testing`.** Your restored 21 KB version exists only on disk (in both folders). | Anyone cloning `testing` — including the edge teammate — still gets a crowd service that can't start. | Steps 1 and 4 |
| M4 | 🟠 **Possibly unpushed work in the original repo.** The last pull in `POLICE_DRONE` produced a merge (`ORIG_HEAD`/`AUTO_MERGE` are present). | If that merge or other commits weren't pushed, a fresh `runpod-v1` from GitHub won't contain them. | Step 1 |
| M5 | 🟠 **The original repo uses `core.fileMode=false`.** `/mnt/NewVolume` is likely an NTFS/exFAT drive, where Linux file permissions aren't real. | Without the same setting in the new copy, git may show **every file as modified** (mode changes). | Step 3 |
| M6 | 🟠 **Running two copies with Docker clashes.** Compose project names come from the folder names (`dvas_ingest`, `DVAS_Dashboard`), and every service has a fixed `container_name` (`drone_postgres`, `dvas_backend`, …). | `docker compose up` in the duplicate would **replace the original's running containers and reuse the same database volumes**. Run only one copy's compose stack at a time. The RunPod image test (`dvas-pod-test` on port 8088) does not clash. | Rule in Step 8 |
| M7 | 🟡 Tracked files not copied: `.gitignore`, `logs.txt`, `security_diff.diff`, `security_diff_utf8.diff`. | They return with git. The last three are old logs/diffs (≈ 700 KB) and can be deleted on `runpod-v1`. | Step 2 / optional cleanup |
| M8 | 🟡 Local-only files that **were** copied and must stay out of git: `context.md`, `claude.md`, `geofence_context.md`, `PERFORMANCE REPORT/` (untracked today); `.env` files and model weights (already git-ignored). | Keep them for your own reference; Step 2 adds the docs to a local-only ignore. | Step 2 |
| M9 | 🟡 Not copied, and only needed for the **local docker-compose** stack: `_dvas_temp_raw_upload_9x8a72#data/` (mounted by the dashboard compose `edge` container) and `videos/`. | Not needed for RunPod work. Copy them only if you plan to run the old compose stack from the duplicate. | — |
| M10 | 🟡 The deployment docs aren't in the duplicate yet: `DVAS_RunPod_Deployment_Plan.md`, `DVAS_Edge_EXE_Build_Plan.md`, this guide. | You'll want them next to the code. | Step 4 |
| M11 | 🟡 Secrets committed in the repo: `dvas_edge/token.txt`, `dvas_edge/config/wireguard/wg0-client.conf`, `dvas_ingest/config/wireguard/wg0-server.conf`, `DVAS_Dashboard/cookies.txt`, `dvas_edge/config/settings.yaml` (client secret + operator password). | Already handled by the RunPod plan (Phase 1.7, Phase 13). Edge files are coordinated with the edge teammate. | RunPod plan |

---

## Step 0 — Prerequisites

| Need | Check / install |
|---|---|
| Git ≥ 2.30 | `git --version` · `sudo apt install git` |
| Write access to the repo | You can see *Settings → Collaborators* or push to `testing` today |
| One authentication method | **GitHub CLI** (recommended) or an **SSH key** — Step 3 |
| VS Code | `code --version` · `sudo snap install code --classic` (or the `.deb` from code.visualstudio.com) |
| Disk space | Clone ≈ 100 MB + models (1.4 GB) + optional test video (2.8 GB) |

---

## Step 1 — Finish and push pending work in the original repo

Do this in the **original** folder, so `testing` on GitHub is complete before you branch from it.

```bash
cd /mnt/NewVolume/POLICE_DRONE
git status                                   # review every modified/untracked file listed
git fetch origin
git log --oneline origin/testing..testing    # commits you have that GitHub doesn't
git log --oneline testing..origin/testing    # commits on GitHub you don't have
```

1. **Commit the restored crowd service to `testing`** (fixes M3 for everyone):
   ```bash
   git add dvas_analytics/crowd/main.py
   git commit -m "fix(crowd): restore main.py emptied by merge"
   ```
2. Commit any other changes from `git status` that belong on `testing` (review each one). Leave the rest uncommitted or stash them (`git stash push -m "local wip"`).
3. Push:
   ```bash
   git pull --rebase origin testing     # only if the second log above showed commits
   git push origin testing
   git log --oneline -3 origin/testing  # your commit is now on GitHub
   ```

If you'd rather not touch `testing` yet, skip item 1 — Step 4 commits the file on `runpod-v1` instead. Do tell the edge teammate, because their branch would still have the empty file.

---

## Step 2 — Turn `DVAS_RUNPODV1` into a git working copy

### Option A (recommended) — fresh clone, then bring over the untracked assets

A clean clone guarantees the working copy matches GitHub exactly; you then copy back only what git doesn't track.

```bash
cd /mnt/NewVolume
mv DVAS_RUNPODV1 DVAS_RUNPODV1_copy                     # keep the copy until Step 9 is verified
git clone https://github.com/Quantumweaveintelligence/Drone_Video_Analytics_KP.git DVAS_RUNPODV1
cd DVAS_RUNPODV1
git config core.fileMode false                          # M5 — same as the original repo
git switch testing                                      # creates local testing tracking origin/testing

# Model weights (git-ignored) — only weight files are copied
rsync -a --prune-empty-dirs --include='*/' \
  --include='*.pt' --include='*.pth' --include='*.onnx' --exclude='*' \
  ../DVAS_RUNPODV1_copy/dvas_analytics/ dvas_analytics/

# Local environment files (git-ignored)
cp ../DVAS_RUNPODV1_copy/dvas_ingest/.env                 dvas_ingest/.env
cp ../DVAS_RUNPODV1_copy/DVAS_Dashboard/.env              DVAS_Dashboard/.env
cp ../DVAS_RUNPODV1_copy/DVAS_Dashboard/backend/.env      DVAS_Dashboard/backend/.env

# Restored crowd service — only needed if you skipped Step 1 item 1
cp ../DVAS_RUNPODV1_copy/dvas_analytics/crowd/main.py     dvas_analytics/crowd/main.py

# Your local context docs (kept out of git, see below)
cp ../DVAS_RUNPODV1_copy/{context.md,claude.md,geofence_context.md} .
cp -r "../DVAS_RUNPODV1_copy/PERFORMANCE REPORT" .

# Optional: one drone video for the Phase 8 load test (2.8 GB, git-ignored)
cp ../DVAS_RUNPODV1_copy/dvas_ingest/DJI_0110.MP4 dvas_ingest/
```

Keep the local docs out of git without changing the shared `.gitignore`:
```bash
printf 'context.md\nclaude.md\ngeofence_context.md\nPERFORMANCE REPORT/\n' >> .git/info/exclude
```

Check:
```bash
git status --short
```
Expected: nothing listed (or only `dvas_analytics/crowd/main.py` if you skipped Step 1). **No** `.MP4`, `.pt`, `.env`, `venv` or context docs may appear. If they do, stop and check that `.gitignore` exists at the repo root.

### Option B — keep the copied files and add git to them

Use this only if you deliberately want the exact files from the copy, including any local edits that never reached GitHub.

```bash
cd /mnt/NewVolume/DVAS_RUNPODV1
git clone --no-checkout https://github.com/Quantumweaveintelligence/Drone_Video_Analytics_KP.git /tmp/dvas_git
mv /tmp/dvas_git/.git . && rmdir /tmp/dvas_git
git config core.fileMode false
git fetch origin
git reset --mixed origin/testing              # index = testing, working files untouched
git checkout -- .gitignore logs.txt security_diff.diff security_diff_utf8.diff   # restore files not copied (M7)
printf 'context.md\nclaude.md\ngeofence_context.md\nPERFORMANCE REPORT/\n' >> .git/info/exclude
git status --short                            # every line is a real difference vs testing — review them
```

---

## Step 3 — Git identity and GitHub authentication

```bash
cd /mnt/NewVolume/DVAS_RUNPODV1
git config user.name  "Your Name"
git config user.email "your.name@quantumweaveintelligence.com"   # the email on your GitHub account
```

Pick **one** authentication method.

**(a) GitHub CLI — recommended (HTTPS, no password prompts)**
```bash
sudo apt install gh            # or see https://cli.github.com
gh auth login                  # GitHub.com → HTTPS → "Login with a web browser"
gh auth setup-git              # makes git use gh for credentials
git ls-remote origin -h refs/heads/testing   # works without asking for a password
```

**(b) SSH key**
```bash
ssh-keygen -t ed25519 -C "your.name@quantumweaveintelligence.com"
cat ~/.ssh/id_ed25519.pub      # add it on GitHub: Settings → SSH and GPG keys → New SSH key
ssh -T git@github.com          # "Hi <user>! You've successfully authenticated"
git remote set-url origin git@github.com:Quantumweaveintelligence/Drone_Video_Analytics_KP.git
```

If the organisation uses **SAML SSO**, authorise the token/key for `Quantumweaveintelligence` on GitHub (*Settings → SSH keys / Tokens → Configure SSO*). Otherwise pushes fail with 403.

Don't store a personal access token in plain text (`credential.helper store`); use `gh` or SSH.

---

## Step 4 — Create `runpod-v1` from `testing` and push it

```bash
cd /mnt/NewVolume/DVAS_RUNPODV1
git fetch origin
git switch -c runpod-v1 origin/testing        # new branch = everything on testing
git push -u origin runpod-v1                  # creates it on GitHub and links upstream

# Safety tag marking where RunPod work started
git tag -a pre-runpod-2026-10-26 -m "testing at the start of RunPod work" origin/testing
git push origin pre-runpod-2026-10-26
```

If the branch was already created on the GitHub website:
```bash
git fetch origin
git switch runpod-v1                          # picks up origin/runpod-v1
git merge origin/testing                      # bring in anything newer from testing
```

**First commits on the branch**
```bash
# 1. Crowd fix (skip if Step 1 already put it on testing)
git add dvas_analytics/crowd/main.py
git commit -m "fix(crowd): restore main.py emptied by merge"

# 2. Deployment docs next to the code (M10)
mkdir -p deploy/runpod/docs
cp /mnt/NewVolume/POLICE_DRONE/DVAS_RunPod_Deployment_Plan.md \
   /mnt/NewVolume/POLICE_DRONE/DVAS_Edge_EXE_Build_Plan.md \
   /mnt/NewVolume/POLICE_DRONE/DVAS_RunPodV1_Branch_and_VSCode_Setup.md deploy/runpod/docs/
git add deploy/runpod/docs
git commit -m "docs: RunPod deployment plan, edge exe plan, branch setup guide"

# 3. Optional cleanup of old logs/diffs (M7)
git rm logs.txt security_diff.diff security_diff_utf8.diff
git commit -m "chore: remove stale logs and diff dumps"

git push
```

---

## Step 5 — Protect the branch on GitHub (repo admin)

*Repository → Settings → Branches → Add branch ruleset* (or *Add rule*):
- **Target:** `runpod-v1`
- ✅ Block force pushes · ✅ Restrict deletions
- Optional: require a pull request for merges into `testing`

If you don't have admin rights, ask a repo admin to set this up.

---

## Step 6 — Connect VS Code

### 6.1 Open the working copy
```bash
code /mnt/NewVolume/DVAS_RUNPODV1
```
or *File → Open Folder…*. Choose **"Yes, I trust the authors"**.

### 6.2 Sign in and check the branch
- **Accounts** icon (bottom-left) → **Sign in with GitHub to use GitHub Pull Requests**. VS Code can then push/pull and show PRs. With `gh` or SSH from Step 3, git already works without it.
- The **status bar** (bottom-left) must show **`runpod-v1`**. Click it to switch branches; pick `origin/...` entries to check out remote branches.
- **Source Control** view (`Ctrl+Shift+G`):
  - stage changes (`+`)
  - type a message and **Commit**
  - **Sync Changes** (push/pull)
- Turn on auto-fetch so VS Code shows when `testing` moves: *Settings → search `git.autofetch` → true*.

### 6.3 Recommended extensions
| Extension | Why |
|---|---|
| Python + Pylance (Microsoft) | analytics, backend, ingest code |
| ESLint | React frontend |
| Container Tools / Docker (Microsoft) | Dockerfile, image build in Phase 3 |
| YAML | supervisord/compose/settings files |
| GitHub Pull Requests | PRs to `testing` from inside VS Code |
| GitLens (optional) | blame/history — useful to trace who emptied a file |
| **Remote - SSH** (Microsoft) | edit and debug directly on the RunPod pod (6.5) |

### 6.4 Workspace settings
Keep VS Code fast by excluding the big folders. Create `.vscode/settings.json` (local; add `.vscode/` to `.git/info/exclude` if you don't want to commit it):
```json
{
  "files.watcherExclude": {
    "**/node_modules/**": true, "**/venv/**": true, "**/.venv/**": true,
    "**/*.MP4": true, "**/*.mp4": true, "**/models/**": true, "**/build/**": true
  },
  "search.exclude": {
    "**/node_modules": true, "**/venv": true, "**/.venv": true, "**/build": true,
    "context.md": true
  },
  "git.autofetch": true,
  "files.eol": "\n",
  "python.analysis.extraPaths": ["./dvas_analytics", "./DVAS_Dashboard/backend", "./dvas_ingest/ingest/api"]
}
```

### 6.5 Connect VS Code to the RunPod pod (once Phase 4 of the RunPod plan is done)
1. RunPod console → pod → **Connect → Direct TCP Ports** → note `IP` and the port mapped to `:22`.
2. Add to `~/.ssh/config`:
   ```
   Host dvas-pod
       HostName <IP>
       Port <PORT>
       User root
       IdentityFile ~/.ssh/id_ed25519
   ```
3. VS Code → `F1` → **Remote-SSH: Connect to Host…** → `dvas-pod`.
4. Open `/opt/dvas` (baked code) or `/workspace/dvas` (volume).
   - The VS Code server installs into the container disk and is re-installed after a pod stop; that's expected.
   - Make code changes in your local working copy and ship them via git/image (or `DVAS_DEV_MODE`, RunPod plan 12.2), not by editing the pod directly.

---

## Step 7 — Day-to-day workflow

| Task | Terminal | VS Code |
|---|---|---|
| Get the latest of your branch | `git pull` | Sync Changes |
| Commit work | `git add -p` → `git commit -m "…"` | Stage → message → Commit |
| Push | `git push` | Sync Changes |
| Bring in new work from `testing` | `git fetch origin && git merge origin/testing` | F1 → **Git: Merge Branch…** → `origin/testing` |
| Resolve merge conflicts | edit → `git add` → `git commit` | Merge editor (Accept Current/Incoming/Both) |
| Send a cloud-safe fix back to `testing` | `git switch -c fix/xyz origin/testing` → cherry-pick → push → open PR | GitHub Pull Requests view |
| See what you'd commit | `git status`, `git diff --staged` | Source Control list |

Rules:
- **Never** commit `.env`, model weights, videos, `venv`/`node_modules`, `users.json`/`dvas.env` from the pod, or anything from `/workspace`. Run `git status` before every commit.
- Merge `testing` into `runpod-v1` at least weekly, and always before building an image you'll deploy.
- Shared frontend files (`App.js`, `Login.js`, `AuthContext.js`, `useJpegPoller.js`, `src/lib/deployment.js`): change them through a PR to `testing` (RunPod plan Phase 7.2), so the edge teammate's branch gets the same change.
- Keep commits small and descriptive (`fix(crowd): …`, `feat(runpod): …`, `docs: …`).

---

## Step 8 — Rules for having two copies on one machine

| Do | Don't |
|---|---|
| Edit RunPod code only in `/mnt/NewVolume/DVAS_RUNPODV1` on `runpod-v1` | Edit the same feature in both folders |
| Keep using `/mnt/NewVolume/POLICE_DRONE` (on `testing`) for the existing local compose stack | Run `docker compose up` in **both** folders (M6 — same container names and volumes) |
| If you need the old stack from the duplicate: `docker compose down` in the original first, and know it will use the **same** databases | Copy files between the folders by hand once both are under git — use `git merge`/cherry-pick |
| Build and test the RunPod image from the duplicate (RunPod plan Phase 3; container `dvas-pod-test`, port 8088) | Delete `DVAS_RUNPODV1_copy` before Step 9 is verified |

---

## Step 9 — Verification checklist

- [ ] `git -C /mnt/NewVolume/DVAS_RUNPODV1 branch --show-current` → `runpod-v1`
- [ ] `git status` is clean (no `.MP4`, `.pt`, `.env`, `venv`, context docs listed)
- [ ] `git log --oneline -1 origin/testing` is an ancestor of `runpod-v1`: `git merge-base --is-ancestor origin/testing runpod-v1 && echo OK`
- [ ] `wc -c dvas_analytics/crowd/main.py` → ~21004 bytes, and it's committed (`git log --oneline -- dvas_analytics/crowd/main.py | head -1`)
- [ ] Model weights present: `ls dvas_analytics/*/models | head`
- [ ] Branch and tag visible on GitHub; branch protection on
- [ ] VS Code opens the folder, shows `runpod-v1`, and **Sync Changes** works
- [ ] (After Phase 4) VS Code Remote-SSH connects to `dvas-pod`
- [ ] Then delete the old copy to free ~10 GB: `rm -rf /mnt/NewVolume/DVAS_RUNPODV1_copy`

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Every file shows as modified right after cloning | `git config core.fileMode false` (M5); if it's line endings: `git config core.autocrlf input` |
| `remote: Permission denied` / 403 on push | Check repo write access; authorise the token/key for SSO (Step 3); `gh auth status` |
| `File … exceeds GitHub's file size limit of 100 MB` | You staged a video or model: `git rm --cached <file>`, then `git commit --amend`. Check `.gitignore` is present. |
| `fatal: 'origin/testing' is not a commit` | `git fetch origin` first |
| Merge from `testing` conflicts in shared frontend files | Take `testing`'s version of the shared build-target code, then re-apply your cloud-only changes |
| VS Code shows no Git / Source Control | Open the **repo root** (`DVAS_RUNPODV1`), not a subfolder; check `git --version` |
| Docker containers of the original stack disappeared | You ran compose from the duplicate (M6). `docker compose down` there, then `docker compose up -d` in `POLICE_DRONE` |