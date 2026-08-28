# Sundial

A single-user day-management application: one screen that merges a calendar, a
todo list, and an email-derived task inbox — and then decides *when* you are
actually going to do the work, defending that decision on your real calendar.

Deployed to AWS, used from a browser on desktop and phone, reads and writes
Google Calendar and Gmail.

**Status: M0a — foundations.** Auth, storage, and the deploy pipeline exist;
no calendar or tasks yet.

## Read this first

- [`docs/SPEC.md`](docs/SPEC.md) — the full technical specification.
- [`docs/RUNBOOK-M0.md`](docs/RUNBOOK-M0.md) — first-run setup, including the
  two Google Cloud Console steps that have no API and cannot live in CDK.
- [`docs/RUNBOOK-DEPLOY.md`](docs/RUNBOOK-DEPLOY.md) — one-time setup for the
  deploy pipeline: the OIDC roles, the GitHub environments, and the
  certificate.
- [`CLAUDE.md`](CLAUDE.md) — implementation guidance and the invariants that
  are easy to get wrong.

## Running it

```sh
make install    # uv ×2, npm
make check      # ruff, mypy, pytest, eslint, tsc
make dev-api    # :8000
make dev-web    # :5173
```

Local development needs AWS credentials for the `dev` environment — the table,
the KMS key, and two secrets are real. The test suite does not; it runs against
moto.

## What is left in M0

The first real deploy. `dev` needs no DNS and no certificate — it serves from
its own CloudFront domain — so it can go out immediately. `prod` additionally
needs the certificate for `sundial.mcmahongroup.org` and a DNS record at the
registrar (§15.2). M0 is done when you can sign in on your phone and see
"connected".

## Shape of the thing

| | |
|---|---|
| Front end | React + TypeScript + Vite, PWA (installable on phone) |
| Back end | Python 3.13 on AWS Lambda, FastAPI + Mangum |
| Data | DynamoDB, single table |
| Infra | AWS CDK (Python) — CloudFront, S3, API Gateway HTTP API, EventBridge Scheduler, SQS, SES, KMS |
| External | Google Calendar v3, Gmail v1, Anthropic API (Claude) |
| Cost | ~$8–14/month, single user |
