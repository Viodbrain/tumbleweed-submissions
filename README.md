# Tumbleweed submissions

The current focus is the **employee website and shared-account login** for
[Tumbleweed](https://github.com/Viodbrain/tumbleweed). The default app opens the
employee sign-in page. Employees can review, edit, approve and reject pending
resources after database setup. Login uses salted Argon2id password hashes and
short-lived sessions.

The public directory/submission page is saved for a later phase. Its source is
preserved, but its routes are disabled in the default moderator service. This version runs locally. Atlas connections and the manual approval/rejection
workflow have been verified; hosting has not been deployed. Connection secrets
and employee credentials are local configuration and are not included here.

## Current starting point: employee login

Use `.env.example` with `SERVICE_ROLE=moderator`. Configure the separate
submissions database connection, initialize its collections, and run the
interactive shared-credential setup below. For login alone, the approved reader
and publisher connections can remain unset until review/publication is enabled.

Start the maintainer website with `uvicorn app.main:app --port 8012 --reload`
after installing the project. Open `http://127.0.0.1:8012/maintain` or `/`.
Sign-in reports a clear setup message until its database connection is configured.

## Database and service separation

Use the existing Atlas cluster with two **different databases**:

- `tumbleweed`: approved `places`, `events`, and `zips`. Existing user and messaging
  data stays in the agent project and is not used by this website.
- `tumbleweed_submissions`: `pending_resources`, review history, salted maintainer
  credential hashes, hashed sessions, and expiring request counters.

Run the same source as **two separate processes/services**, with separate secrets:

- `SERVICE_ROLE=public`: directory + submission endpoints only. Give it a
  submissions database credential and an approved-data **read-only** credential.
- `SERVICE_ROLE=moderator`: sign-in + review/approval endpoints only. Give it the
  submissions credential, approved read credential, and a separate approved
  publisher credential. This is the only service with approved write access.

Set Atlas database/collection roles accordingly. The application cannot narrow
permissions granted by an overly broad Atlas user. Restrict the approved reader to
`places`, `events`, `zips`; restrict the publisher to reading/inserting `places`
and `events`, using Atlas custom roles where available. Neither needs user or
message collections. Public configuration rejects an approved-write URI. No
MongoDB URI or shared password is sent to the browser.

## Install and configure locally

Python 3.11 or newer:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

Fill in `.env` privately. Process environment overrides `.env`. Use this one file
only; it is ignored by Git. Do not copy the main project's `.env` here: these
connections need different privileges. Leave `APPROVED_WRITE_MONGODB_URI` absent
whenever using a public service. The default role is now `moderator`.

The submissions Atlas user needs `readWrite` on `tumbleweed_submissions`. Initialize
its collections and TTL indexes, then set the shared credential interactively:

```sh
python -m scripts.setup_directory             # dry run
python -m scripts.setup_directory --apply
python -m scripts.setup_maintainer
```

The credential command prompts for a username and a password of 16–256 characters.
It stores an Argon2id hash with a random salt; no plaintext password is saved.
Running it again rotates the credential and revokes prior sessions. Share the
password through a password manager. There is no public registration.

For the later public-site phase, explicitly set `SERVICE_ROLE=public` and remove
the publisher URI from that process before starting:

```sh
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open `http://127.0.0.1:8000/directory`.

For the moderator process, configure `SERVICE_ROLE=moderator` and
`APPROVED_WRITE_MONGODB_URI` **only in that process's environment/secret store**,
then run a second instance on another port:

```sh
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

Open `http://127.0.0.1:8001/maintain`. Restart a process after changing its
configuration; settings are cached. The public process must remain configured
without the writer secret.

## How publication works

Submission writes only to the pending database. Public browsing reads only the
approved database. Sources, addresses, coordinates, opening hours, and event dates
must be checked by a maintainer. The server does not fetch source URLs or geocode.

For events, choose **Add an event here** on an approved location. This fills the
venue reference, name, address and coordinates. The event's `place_id` references
`places._id`; approval checks the venue snapshot. For a new venue, submit and
approve the location first. Event titles stay in `title`, separate from compact
14-character IDs such as `e_d8571b271f83`. Place IDs use the same pattern with `p_`.
Event times are timezone-aware UTC BSON dates; the form identifies the browser's
local timezone. An optional event end time is supported by the API.

Revision checks reject stale edits/decisions with 409. Approval freezes the reviewed
revision as `approving`, inserts a deterministic destination record, then marks it
`approved`. If interrupted between database writes, retry the Publishing queue;
the same submission ID prevents duplicate publication. The frozen submission stays
in the pending database as an audit record. Rejection never publishes. This is a
retryable sequence, not a transaction spanning separate connections.

The shared credential provides a shared audit identity. It does not identify which
individual maintainer made a decision. Employees can also search and edit existing approved records from the Approved
directory tab.

## Deployment preparation

Host the two Python services on separate HTTPS origins. Each can serve its own
HTML/assets directly, with no frontend build step. Never expose a development
server as a production deployment. Configure Atlas network access for the chosen
backend host. Keep secrets in the hosting provider's secret store.

For separate static hosting, export the two frontends with their respective
reachable HTTPS API origins:

```sh
python -m scripts.export_directory \
  --public-api-base https://public-api.example.org \
  --moderator-api-base https://moderator-api.example.org \
  --output /tmp/tumbleweed-sites
```

Set each service's `ALLOWED_ORIGINS` to its exact frontend origin; leave it empty
for same-origin hosting. Configure trusted proxy IPs narrowly so the client address
used for rate limits is correct. Session tokens stay in JavaScript memory, expire
after two hours, and are checked on every admin request. Refreshing requires sign-in.

Requests have a 16 KiB body limit. Per-IP hourly limits apply to submissions (5),
login (20), directory browsing (600), and authenticated admin requests (300).
Stored IPs are hashed, which is not complete anonymization. TTL indexes remove
expired counters and sessions. Pending/rejected audit records have no automatic
retention policy yet. Add an edge rate limit or challenge before a broad public
launch. Do not publish fictional demo data as verified food-service information.

Approved collections must already have `places.location` and `events.location`
2dsphere indexes; the agent repository's index setup supplies these. This website's
setup command only prepares the submissions database and does not alter approved
data or indexes.

## Verification

```sh
python -m pytest -q
ruff check backend scripts
```

Tests use separate mocked databases and do not contact Atlas. They check public and
moderator route separation, pending isolation, venue links, revision conflicts,
retryable approval, rejection, input validation, password salts, session expiry,
credential rotation, logout, and configuration boundaries.


Local validation also exercised login, pending isolation, linked event approval,
retry-safe approval, rejection and logout against fresh temporary Atlas databases,
which were removed afterward. Manual employee review confirmed rejection without
publication and approval creating one destination record. Rejected submissions
and review histories are retained indefinitely for now.


## Editing approved resources

After employee sign-in, select **Approved directory**. Search locations or events
by name, address, reference or ZIP (including ZIPs embedded in legacy addresses).
Results are paginated in groups of 30. Select a record, edit its details, add a
reason and confirm before saving. Saving immediately updates the approved database.

IDs and original provenance/custom fields are preserved. ZIP and source URL remain
optional for older records. Event start/end times retain their stored precision
when unchanged. A full-document version digest and atomic MongoDB comparison reject
concurrent edits with 409, including changes made outside this application. Reload
the record and reapply edits after a conflict. A timestamped edit reason is stored
in the approved record, attributed to the shared employee account; the latest ten
entries are displayed and the stored history is not automatically removed.

Location edits do not cascade to linked events' saved venue snapshots. Review those
events afterward; **Use current venue details** copies the current location name,
address, area and coordinates before saving an event. Event saves validate their
venue reference and snapshot. New approval rules still require future event dates;
existing approved events may have historical dates so employees can correct them.

Authenticated endpoints:

- `GET /api/moderation/resources?resource_type=place&q=pantry&zip=99344&offset=0`
- `GET /api/moderation/resources/{place|event}/{id}`
- `PUT /api/moderation/resources/{place|event}/{id}` with `version`, `resource`, `note`.

The publisher credential now needs update permission on approved `places` and
`events` in addition to read/insert. No delete endpoint is provided. Public services
have none of these routes. The public submission page remains deferred.
