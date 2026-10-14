## Career Connect ingest credentials

The Cloudflare Worker writes alerts to the `jobscout-careerconnect` R2 bucket
and jobscout drains it. Four values, out of band like every other credential
here, and an R2 token scoped to this bucket alone rather than the CNPG backup
credentials:

```bash
ssh k3s-01 'sudo k3s kubectl -n jobscout create secret generic jobscout-r2 \
  --from-literal=endpoint=https://<account>.r2.cloudflarestorage.com \
  --from-literal=bucket=jobscout-careerconnect \
  --from-literal=access_key_id=REPLACE_ME \
  --from-literal=secret_access_key=REPLACE_ME'
```

The CronJob reads them as `R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID` and
`R2_SECRET_ACCESS_KEY`. All four unset means the source is skipped rather than
failing, so the boards keep running if the bucket is ever unavailable.

Objects are deleted only after their postings are committed, so a crash
between the two costs a repeated parse rather than a lost posting. A message
that parses to nothing is moved to `careerconnect/failed/` instead: the three
Google forwarding confirmations already in the bucket are that case, real mail
that is not a job alert.

## Tagline prompt secret

The tagline prompt describes the candidate and the repository is public, so the
prompt lives only in the cluster. Create it by hand, as with `db-backup-r2`, from
a local file that is never committed:

```bash
kubectl -n jobscout create secret generic jobscout-tagline-prompt \
  --from-file=prompt.md=./tagline-prompt.md
```

The `jobseer-overviews` CronJob mounts it at `/etc/jobscout/prompt.md` and the
step is skipped when the secret is absent. Edit the secret and the next run
regenerates every tagline, a few at a time. Do not backfill with one large job:
an unthrottled run of 400 pushed the k3s VM to a load average of 260. Let the
morning window do it, or run `process-taglines --now --limit 10` by hand.
