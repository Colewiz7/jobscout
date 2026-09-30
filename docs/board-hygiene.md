# Board hygiene review — 2026-09-30

The two private-looking Workday site names below arrived through Simplify,
not the configured first-party board poller. JobSeer labels them **Check apply
site**. Apply is disabled for an unverified private-site URL; the original
posting remains available for inspection. No posting or application data is
deleted.

| Employer | Private-site requisition | Public-site check | JobSeer action |
| --- | --- | --- | --- |
| RTX | `01866497` on `Private_Posting_No_TMP` | The same path and requisition returned 200 from `REC_RTX_Ext_Gateway`'s Workday detail API. | Apply opens the public gateway URL. |
| GE Vernova | `R5051807-1`, `R5051647-1`, `R5051794-1` on `only_confidential_executive_recruiting` | Searching each requisition on `Vernova_ExternalSite` returned zero results. | Show the warning and disable Apply; check the public careers site manually. |
| GE Vernova | `R5051649-1` on `only_confidential_executive_recruiting` | The public site returned `R5051649-2`, not the exact requisition version. | Keep it flagged and do not silently substitute a different version. |

`testnisc` is omitted from polling and display. Existing rows remain stored for
audit. The configured `nisc` board is unchanged.
