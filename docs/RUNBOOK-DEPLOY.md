# Deploy runbook

The pipeline is `.github/workflows/deploy.yml`. It runs only after a successful
`CI` on `main`, or on manual dispatch.

Everything here is one-time setup. Steps 1–3 must happen before the first
deploy; steps 4–6 are only needed for prod.

---

## 1. Create the deploy roles

`SundialCicd` creates one role per environment. It cannot be deployed by the
pipeline whose roles it creates, so it goes on by hand, once, from an admin
session.

```sh
cd infra
npx aws-cdk@2 deploy SundialCicd
```

It **references** the account's existing GitHub OIDC provider rather than
creating one. An issuer URL is unique per AWS account, and this account already
has `token.actions.githubusercontent.com` serving other repositories — creating
a second would fail the deploy.

The roles hold no service permissions of their own. They may assume the four
CDK bootstrap roles, read Sundial stack outputs, write their own environment's
site bucket, and invalidate CloudFront. Deploys happen *through* the bootstrap
roles, which is what keeps this far narrower than attaching `PowerUserAccess`.

## 2. GitHub environments

Four, because the durable stack is gated separately from the application stack.

| Environment | Required reviewers | Deploys |
|---|---|---|
| `dev` | no | `SundialApp-dev`, the front end |
| `dev-infra` | **yes** | `SundialInfra-dev` |
| `prod` | **yes** | `SundialApp-prod`, the front end |
| `prod-infra` | **yes** | `SundialInfra-prod` |

The role trust policies name these environments directly, so the approval is
enforced by AWS when the token is exchanged — not merely by GitHub's UI.
Removing a reviewer requirement is not by itself enough to reach the account.

The `-infra` jobs only run when `cdk diff` reports an actual change to the
durable stack, so an unchanged stack never asks for an approval nobody needs
to give.

## 3. Repository secrets and variables

```sh
gh secret set AWS_ACCOUNT_ID          # the 12-digit account id
```

It is a secret rather than a variable only because this repository is public
and an account id is a small piece of reconnaissance. It is not a credential.

Variables are needed for prod only, and must be left **empty** until the
certificate exists (step 5):

```sh
gh variable set SITE_DOMAIN_NAME      --body sundial.mcmahongroup.org
gh variable set SITE_CERTIFICATE_ARN  --body arn:aws:acm:us-east-1:<account>:certificate/<id>
```

## 4. Deploy dev

Dev needs **no DNS and no certificate**. It serves from the distribution's own
`*.cloudfront.net` domain, which is still a single origin, so §5.1's
first-party session cookie works exactly as it does in prod.

Push to `main`, or dispatch the workflow. The first run also creates the
DynamoDB table and KMS key, so `dev-infra` will ask for approval.

Afterwards, take the `SiteUrl` from the workflow summary and finish the wiring:

```sh
aws ssm put-parameter --name /sundial/dev/app-base-url --type String \
  --value "https://d111111abcdef8.cloudfront.net" --overwrite
```

then add `https://<that host>/api/auth/callback` as an authorised redirect URI
on the **dev** Google OAuth client, and re-run the deploy so the Lambdas pick
up the new value. This two-step exists because the distribution's domain is not
knowable until it has been created.

## 5. The certificate (prod only)

Request it in `us-east-1` — CloudFront accepts certificates from nowhere else.

```sh
aws acm request-certificate --region us-east-1 \
  --domain-name sundial.mcmahongroup.org \
  --validation-method DNS \
  --query CertificateArn --output text

aws acm describe-certificate --region us-east-1 --certificate-arn <arn> \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord'
```

Add that `CNAME` at the registrar, wait for `Status: ISSUED`, then set
`SITE_CERTIFICATE_ARN` and `SITE_DOMAIN_NAME` per step 3.

**CDK references this certificate; it never creates one.** A DNS-validated
certificate created inside a stack blocks that stack until its validation
record resolves, and with the zone outside AWS nothing in the deployment can
write it — so the deploy would hang for the validation timeout and then roll
back (§15.2).

## 6. Deploy prod, then point DNS at it

Dispatch the workflow with `environment: prod` and approve the gates. Take
`DistributionId`'s domain from the outputs and add the record at the registrar:

```
sundial.mcmahongroup.org.   CNAME   d222222abcdef8.cloudfront.net.
```

CloudFront serves the alias only once the certificate covers it *and* DNS
resolves; until the record propagates the distribution answers on its own
domain. Then set `/sundial/prod/app-base-url` to
`https://sundial.mcmahongroup.org`, add the matching redirect URI to the
**prod** Google OAuth client, and re-deploy.

---

## What the pipeline will not do

- **Write secret values.** The session keypair and the Google client secret are
  populated by `docs/RUNBOOK-M0.md` steps 3–4. The pipeline never touches them.
- **Write SSM parameters.** Configuration is deliberately outside the deploy.
- **Publish the OAuth clients.** A Cloud Console step with no API
  (`docs/RUNBOOK-M0.md` step 1), and the reason background sync survives past
  seven days.
- **Delete anything durable.** `SundialInfra` retains its table, key, and
  secrets. Destroying `SundialApp` does not take data with it (§13).
