# Google Cloud Console runbook

Everything Sundial needs from Google, in the order it has to happen. This is
`docs/RUNBOOK-M0.md` step 1 expanded, and it is a separate file because it is
the only setup that is entirely **manual**: the publishing-status change in
step 5 has no API, so it cannot live in CDK and cannot be scripted.

Do this **before the first OAuth round-trip**. Nothing else in M0 depends on
it — `SundialCicd` and `SundialInfra` deploy fine without it — but
`SundialApp` reads two of its outputs from SSM at deploy time, and sign-in
does not work until they are real.

You do steps 1–5 once. Step 6 onwards is **per environment**: `dev` and `prod`
get separate OAuth clients in the same project (§16 decision 4).

---

## Before you start

- A Google account. The one you will actually sign in to Sundial with — its
  account id becomes the single-entry allowlist (§5.1), so using a different
  account here than you use daily means a wasted round of setup.
- The AWS CLI pointed at the Sundial account, for steps 8 and 9.

The console has been reorganised: what used to live under **APIs & Services →
OAuth consent screen** is now the **Google Auth Platform** section, with
*Branding*, *Audience*, *Clients* and *Data access* as separate pages. Both
paths are given below. If your console shows neither, navigate by the page
title rather than the breadcrumb — the names have been stable even as the
nesting moved.

---

## 1. Create or select the project

<https://console.cloud.google.com/projectcreate>

One project serves both environments. Name it something you will recognise in
a consent screen a year from now — the project name is *not* what users see,
but the app name from step 3 defaults to it.

Note the project id. Everything below assumes it is selected in the console's
project picker; picking the wrong project is the most common way to spend
twenty minutes configuring something that then does not apply.

## 2. Enable the Calendar API — and only that

**APIs & Services → Library** → search "Google Calendar API" → **Enable**.

Do **not** enable the Gmail API. Gmail's scopes are *restricted*, they arrive
with M5 when J3 lands, and requesting them now pushes the consent screen into
a verification path this project does not want yet (§16 decision 2). The same
goes for the People API — Sundial does not use it.

## 3. Consent screen: branding and audience

**Google Auth Platform → Branding** (legacy: *OAuth consent screen*).

| Field | Value |
|---|---|
| App name | `Sundial` |
| User support email | your address |
| Developer contact | the same address |
| App logo, domains | leave empty |

Then **Google Auth Platform → Audience**, user type **External**.

External is correct even though there is exactly one user. Internal requires a
Google Workspace domain you administer — that is option B in §5.3, the
fallback, not the plan.

## 4. Scopes

**Google Auth Platform → Data access** (legacy: *OAuth consent screen →
Scopes*) → **Add or remove scopes**.

Add exactly these five, and nothing else:

```
openid
email
profile
https://www.googleapis.com/auth/calendar.events
https://www.googleapis.com/auth/calendar.calendarlist.readonly
```

They match `Settings.scopes` in `backend/src/sundial/core/config.py` exactly.
If the two lists drift, consent succeeds and the first API call fails with a
403 that names an insufficient scope — a confusing failure, because the sign-in
that preceded it looked entirely healthy.

`calendar.events` is read-*and*-write across every calendar; the guarantee that
Sundial only ever writes to the `Sundial` calendar is enforced in Sundial, not
by Google (§6.1, invariant 7).

The console will not offer `openid` in the picker — it is added implicitly by
the OIDC request. Add the other four and move on.

## 5. Publish to Production ⚠️

**Google Auth Platform → Audience → Publishing status → Publish app** →
confirm.

Verify it now reads **In production**, not **Testing**.

This is the step with no API, and the one that matters most. A client left in
Testing issues refresh tokens that **expire after seven days** (§5.3). The
resulting failure — background sync dying every seventh day while everything
works perfectly for the six before it — is genuinely hard to diagnose from the
symptom. The expiry is bound to publishing status, not to scope sensitivity:
a calendar-only Sundial in Testing dies exactly as reliably as one with Gmail
scopes.

Two things this does *not* mean:

- **Published is not verified.** Production removes the token expiry
  immediately. Verification removes the *warning screen*, takes weeks, and
  needs a CASA assessment. Sundial does not pursue it — that is option A
  in §5.3, settled in §16 decision 1.
- **You will still see "Google hasn't verified this app"** on first consent.
  Click **Advanced → Go to Sundial (unsafe)**. It does not recur per session.

An unverified production app has a hard lifetime cap of 100 users. For a
single-user app this is not a constraint, but it is why the cap exists.

## 6. Create the OAuth clients

**Google Auth Platform → Clients → Create client** (legacy: *Credentials →
Create credentials → OAuth client ID*). Type **Web application**, one per
environment.

### `sundial-dev`

| Field | Value |
|---|---|
| Authorised JavaScript origins | *leave empty* |
| Authorised redirect URIs | `http://localhost:5173/api/auth/callback` |

JavaScript origins are for the implicit and JS flows. Sundial uses the
authorization code flow with PKCE and a server-side callback (§5.1), so the
field stays empty.

The redirect URI must match `SUNDIAL_GOOGLE_REDIRECT_URI` byte for byte —
Google compares the string, not the URL semantics. A trailing slash, `127.0.0.1`
for `localhost`, or `https` for `http` all produce `redirect_uri_mismatch`.
`http` is permitted here only because the host is `localhost`.

A second URI gets added to this same client after the first dev deploy, once
the CloudFront domain exists:
`https://<distribution>.cloudfront.net/api/auth/callback`
(`docs/RUNBOOK-DEPLOY.md` step 4). A client may hold several.

### `sundial-prod`

Create it now, with redirect URI
`https://sundial.mcmahongroup.org/api/auth/callback`.

Separate clients per environment so that a dev misconfiguration cannot issue
tokens that reach prod data. They share the consent screen, which is a
project-level object — that is fine and is why steps 1–5 happen only once.

**Copy the client secret when it is shown.** It is retrievable from the console
afterwards, but the download prompt is the easy moment.

## 7. Find your numeric account id

`allowed_google_account_id` is the OIDC `sub` claim — a ~21-digit number, not
your email address (§5.1). Sundial compares against `sub` because an email
address can be changed and reassigned, and the allowlist has to survive that.

There is a mild chicken-and-egg: `routes_auth.py` deliberately logs a rejected
sign-in *without* the id, so you cannot discover it by failing a login. Use the
[OAuth 2.0 Playground](https://developers.google.com/oauthplayground):

1. Authorise the scopes `openid email profile`.
2. **Exchange authorization code for tokens.**
3. Copy the `id_token` from the response and decode its payload:

```sh
python3 - <<'PY'
import base64, json
tok = input("id_token: ").split(".")[1]
print(json.loads(base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4)))["sub"])
PY
```

The Playground uses its own OAuth client, which is fine: Google's `sub` is an
identifier for the *account*, unique across all Google accounts and never
reused, so it is the same value your `sundial-dev` client will see.

## 8. Write the SSM parameters

The two values from steps 6 and 7, plus the base URL. This is
`docs/RUNBOOK-M0.md` step 2, repeated here because it is where the Console
outputs land:

```sh
env=dev
aws ssm put-parameter --name "/sundial/$env/google-client-id" --type String \
  --value "XXXXXXXX.apps.googleusercontent.com" --overwrite
aws ssm put-parameter --name "/sundial/$env/allowed-google-account-id" --type String \
  --value "1082345..." --overwrite
aws ssm put-parameter --name "/sundial/$env/app-base-url" --type String \
  --value "http://localhost:5173" --overwrite
```

`SundialApp` resolves these at **deploy** time, not synth time
(`app_stack.py`), so they must exist before that stack deploys and a change to
one needs a re-deploy to take effect. `app-base-url` becomes the CloudFront
domain in `docs/RUNBOOK-DEPLOY.md` step 4.

Repeat with `env=prod` and the prod client id once prod exists.

## 9. Store the client secret

Never in SSM, never in the repo, never in a CloudFormation template — a
template is readable by anyone holding `cloudformation:GetTemplate` (§12). CDK
creates the secret empty; you fill it:

```sh
aws secretsmanager put-secret-value --secret-id sundial/dev/google-client \
  --secret-string '{"client_secret":"GOCSPX-..."}'
```

This requires `SundialInfra-dev` to have been deployed already
(`docs/RUNBOOK-M0.md` step 3).

---

## Verification checklist

Before you conclude this is done:

- [ ] **Audience → Publishing status reads "In production".** Not Testing.
- [ ] Google Calendar API enabled; Gmail API **not** enabled.
- [ ] Exactly the five scopes from step 4, matching `core/config.py`.
- [ ] Two Web application clients, each with its own redirect URI.
- [ ] `/sundial/dev/google-client-id` and
      `/sundial/dev/allowed-google-account-id` set, the latter numeric.
- [ ] `sundial/dev/google-client` holds a real `client_secret`.

Then `docs/RUNBOOK-M0.md` step 5: `make dev-api`, `make dev-web`, sign in at
<http://localhost:5173>, and the shell should show *Google: connected*.

## When it goes wrong

| Symptom | Cause |
|---|---|
| `redirect_uri_mismatch` | The URI in the client does not match `SUNDIAL_GOOGLE_REDIRECT_URI` exactly. Compare character by character, including scheme, port and trailing slash. |
| `access_blocked` / "has not completed verification" with no *Advanced* link | The app is still in Testing and your account is not a test user. Finish step 5. |
| Sign-in works, then a `403` naming an insufficient scope | Step 4's scope list does not match `core/config.py`. Re-consent after fixing — a granted scope set is not widened by editing the console alone. |
| `502` from the callback, "no refresh token" | Google only returns one on first consent. Revoke Sundial at <https://myaccount.google.com/permissions> and sign in again. |
| Sundial `403`s your own sign-in | `allowed-google-account-id` holds an email, or the wrong `sub`. It is numeric (step 7). |
| Everything works for a week, then breaks | Testing status. This is the failure step 5 exists to prevent. |
