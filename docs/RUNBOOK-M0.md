# M0 runbook

Two of these steps have no API and cannot live in CDK. They are the reason this
file exists rather than being a `make` target.

## 1. Google Cloud Console — manual, before anything else

**Full walkthrough: [`RUNBOOK-GOOGLE.md`](RUNBOOK-GOOGLE.md).** The summary below
is the shape of it; that file has the console navigation, the verification
checklist, and what each failure mode looks like.

Per §16 decision 1 the publishing path is **option A: Production, unverified**.
Do this for **both** the `dev` and the `prod` OAuth client. A client left in
"Testing" issues refresh tokens that expire after **7 days**, and the resulting
failure — background sync dying every seventh day — is genuinely confusing to
debug because everything works fine for a week.

1. Create (or select) the Google Cloud project.
2. Enable the **Google Calendar API**. *Not* the Gmail API — that arrives with
   M5 (§16 decision 1).
3. OAuth consent screen: user type **External**.
4. Scopes: `openid`, `email`, `profile`,
   `.../auth/calendar.events`, `.../auth/calendar.calendarlist.readonly`.
   No `gmail.*` scopes.
5. **Publishing status → "Publish app" → Production.** Confirm the status reads
   *In production*, not *Testing*. This is the step with no API.
6. Create two OAuth client IDs of type *Web application*:
   - `sundial-dev` — authorised redirect URI `http://localhost:5173/api/auth/callback`
   - `sundial-prod` — redirect URI added in M0b, once the domain exists
7. Note the numeric account id you will sign in with. It is the `sub` claim, not
   the email address; the allowlist compares against `sub` (§5.1).

The consent screen will show an "unverified app" interstitial the first time.
That is expected under option A and does not recur per session.

## 2. SSM parameters

CDK reads configuration from Parameter Store at deploy time (§12). One AWS
account, two environments, distinguished by path (§16 decision 3).

```sh
env=dev
aws ssm put-parameter --name "/sundial/$env/google-client-id" --type String \
  --value "XXXXXXXX.apps.googleusercontent.com" --overwrite
aws ssm put-parameter --name "/sundial/$env/allowed-google-account-id" --type String \
  --value "1082345..." --overwrite
aws ssm put-parameter --name "/sundial/$env/app-base-url" --type String \
  --value "http://localhost:5173" --overwrite
```

`app-base-url` becomes the real hostname in M0b. Nothing in the CDK code has a
hostname in it (§15.1).

## 3. Deploy the durable stack

```sh
make build
cd infra && npx aws-cdk@2 deploy SundialInfra-dev
```

`SundialInfra` retains its table, key, and secrets on delete (§13). If you ever
need to remove it you will have to detach those by hand — that is deliberate.

## 4. Populate the secrets

Both are created empty by CDK, because a CloudFormation template is readable by
anyone holding `cloudformation:GetTemplate` (§12).

```sh
python3 scripts/rotate_session_key.py --env dev

aws secretsmanager put-secret-value --secret-id sundial/dev/google-client \
  --secret-string '{"client_secret":"GOCSPX-..."}'
```

## 5. Run it locally

```sh
cp .env.example .env && $EDITOR .env   # fill in the ARNs printed by step 3
source .env
make dev-api    # :8000
make dev-web    # :5173, proxying /api to :8000
```

Open <http://localhost:5173>, click **Sign in with Google**, accept the
unverified-app interstitial, and the shell should show *Google: connected*.

Signing in with any account other than the one in
`SUNDIAL_ALLOWED_GOOGLE_ACCOUNT_ID` returns `403` and creates no user record.

## 6. Deploy the app stack

```sh
cd infra && npx aws-cdk@2 deploy SundialApp-dev
```

## What is still open

**M0b, blocked on the domain (§15.1).** Route 53 hosted zone, ACM certificate
in `us-east-1`, the CloudFront distribution with S3 and the HTTP API as a second
origin under `/api/*`, the production redirect URI, and the GitHub Actions OIDC
deploy role. Until then the session cookie is first-party only by virtue of the
Vite proxy, which is a different mechanism to the deployed one.

M0 is done when you can sign in on your phone and see "connected" — which needs
M0b.
