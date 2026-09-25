# Example Jobs by AHJ x Roof System — LOCAL FILE, NOT IN GIT

The calibration matrix that used to live here listed, for every AHJ x roof-system
combination, a **real customer**: surname, job number and street address, pulled from
1,723 live AccuLynx jobs. That is customer PII and this repository is published, so the
content has been moved out of version control.

**Where the real matrix is now:** `EXAMPLE_JOBS_BY_AHJ_SYSTEM.local.md`, in this same
folder. It is untracked and `.gitignore`d. Nothing was deleted — open that file for the
full table, the "Known gaps" list, the pulled-documents inventory and the audit
corrections, exactly as before.

**To regenerate it:** run `find_example_jobs.py` from this folder (it pulls fresh from
the AccuLynx API; the key is read from `whitelabel-crm/data/crm_migration.db` and is
never printed). Write the result to the `.local.md` name — never back to this file.

**If you need a shareable version:** keep the AHJ x system grid and the job numbers, and
drop the customer name and street address columns. A job number alone is not PII.

**Note for the operator:** the previous, PII-bearing revisions of this file are still in
this repository's git history. Removing them there is a history rewrite (e.g.
`git filter-repo`) plus a force-push and a re-clone by everyone — an owner decision, not
something to do mid-task.
