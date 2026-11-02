# Evidence & reports review (2 Nov 2026)

Review of evidence storage, export and report generation on branch `runpod-v1` (commit `3a7a21a`). No code was changed.

| Document | Read it for |
|---|---|
| [01_Report_Generation_Assessment.md](01_Report_Generation_Assessment.md) | What evidence/data DVAS stores today, what the four reports use of it, and which better reports are possible **now** vs after changes |
| [02_Evidence_and_Reporting_Implementation_Plan.md](02_Evidence_and_Reporting_Implementation_Plan.md) | Phased plan (P0 hot-fixes → evidence integrity → report rebuild → UI), in the `claude.md` plan format, with tests, risks and open questions |
| [03_UI_and_Backend_Issues.md](03_UI_and_Backend_Issues.md) | Every logical/practical defect found (B1–B30), with severity, file:line, impact and fix |

**Most urgent:** the crowd analyzer has stored nothing since commit `819ef2f` (B27). Its health check still says "ok".

**Start with** the summary table in 03, then the P0 phase in 02: it removes the issues that can produce wrong evidence or
wrong legal documents today. Items marked *(verify on pod)* need a check against live data. The pod was stopped during the review.

## Status

| Phase | State |
|---|---|
| P0 | Code done (3 Nov 2026), unit tests only. Needs an image rebuild and a pod check: crowd `/health` 200 with `model_loaded:true`, `healthcheck.sh` all OK, crowd rows in `crowd_density_readings`, ANPR `watchlist_count` after a restart. Then run `DVAS_Dashboard/backend/scripts/review_synthesized_missions.sql` and decide what to do with each row. |
| E1–U1 | Not started |

P0 differs from the plan in two places:

- **Untracked missions.** `GET /missions` still returns a plain list, so the UI is unchanged. Untracked mission ids are on a separate admin-only endpoint, `GET /api/missions/untracked`, and are no longer looked up on every list call.
- **Image lookup (B2, partly).** On the pod, evidence images were never resolved, because the hard-coded host does not exist there. Reading `ANALYTICS_DATABASE_URL` switches the lookup on, so the "latest image of any kind" guesses were removed first. Nearest-in-time matches from the same drone or mission must now be within ±10 s. E1 still replaces this with exact keys.

