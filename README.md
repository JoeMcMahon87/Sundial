# Sundial

A single-user day-management application: one screen that merges a calendar, a
todo list, and an email-derived task inbox — and then decides *when* you are
actually going to do the work, defending that decision on your real calendar.

Deployed to AWS, used from a browser on desktop and phone, reads and writes
Google Calendar and Gmail.

**Status: specification. No code yet.**

## Read this first

- [`docs/SPEC.md`](docs/SPEC.md) — the full technical specification.

Two sections need your attention before implementation starts:

- **§5.3 — the Google OAuth verification problem.** The one external
  constraint that can't be engineered around, with three options and a
  recommendation.
- **§16 — open questions.** Six decisions still outstanding.

## Shape of the thing

| | |
|---|---|
| Front end | React + TypeScript + Vite, PWA (installable on phone) |
| Back end | Python 3.13 on AWS Lambda, FastAPI + Mangum |
| Data | DynamoDB, single table |
| Infra | AWS CDK (Python) — CloudFront, S3, API Gateway HTTP API, EventBridge Scheduler, SQS, SES, KMS |
| External | Google Calendar v3, Gmail v1, Anthropic API (Claude) |
| Cost | ~$7–13/month, single user |
