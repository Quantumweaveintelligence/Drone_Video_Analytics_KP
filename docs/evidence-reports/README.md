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
