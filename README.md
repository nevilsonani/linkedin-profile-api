# LinkedIn Profile API

Give it a LinkedIn profile URL, get back the profile as structured JSON.

```bash
curl "$BASE_URL/api/v1/profile?url=https://www.linkedin.com/in/williamhgates" \
  -H "X-API-Key: $API_KEY"
```

```jsonc
{
  "success": true,
  "data": {
    "full_name": "Ada Lovelace",
    "headline": "Mathematician | First Computer Programmer",
    "location": { "text": "London, England, United Kingdom", "city": "London", "country": "United Kingdom" },
    "about": "I write algorithms for the Analytical Engine.\n\nInterested in poetical science.",
    "profile_picture": { "url": "https://media.licdn.com/...400_400...", "width": 400, "variants": [ /* 3 sizes */ ] },
    "experience":     [ { "title": "Principal Analyst", "company": { "name": "…", "logo": { /* … */ } },
                          "date_range": { "text": "Jun 1842 - Present", "is_current": true, "duration_months": 2199 } } ],
    "education":      [ { "school_name": "…", "degree_name": "…", "field_of_study": "…" } ],
    "skills":         [ { "name": "Algorithms", "endorsement_count": 99 } ],
    "certifications": [ { "name": "…", "authority": "Royal Society", "url": "…" } ],
    "languages":      [ { "name": "English", "proficiency": "Native or bilingual proficiency" } ]
    // …plus projects, publications, honors, volunteering, courses, patents,
    //    test scores, organizations, contact_info, network_info
  },
  "meta": {
    "source": "voyager",
    "duration_ms": 1840,
    "endpoints_succeeded": ["profileView", "contactInfo", "networkInfo", "skills", "dashProfile"],
    "endpoints_failed": [],
    "warnings": []
  }
}
```

A complete, unabridged response is in [`docs/example-response.json`](docs/example-response.json).

**Interactive docs:** `/docs` (Swagger UI) · `/redoc` · `/openapi.json`

---

## Contents

- [Approach](#approach) — how the reverse engineering works
- [Quick start](#quick-start) — running it locally
- [Getting your LinkedIn cookie](#getting-your-linkedin-cookie)
- [Deployment](#deployment) — Render, Docker, anywhere
- [API reference](#api-reference)
- [Response schema](#response-schema)
- [Error handling](#error-handling)
- [Known limitations](#known-limitations) — please read this section
- [Testing](#testing)

---

## Approach

### Finding the API

LinkedIn's web app is a single-page application. Loading a profile page doesn't
ship you HTML with the data in it — the page boots, then fetches the profile
over XHR from a private REST layer at `linkedin.com/voyager/api/*`. Watching the
Network tab while a profile loads reveals the whole surface.

That's a much better target than parsing rendered HTML. It returns clean JSON,
the field names are stable and self-describing, and it doesn't break when
LinkedIn reskins the UI. The trade-off is that it's undocumented and can change
without notice — which is why the parsers here are written to degrade rather
than crash (more on that below).

### Getting Voyager to answer

Three things must be right or the API refuses:

| Requirement | Detail |
|---|---|
| **Session cookie** | `li_at` — this is what actually authenticates you. Long-lived (~12 months). |
| **CSRF token** | The `csrf-token` header must equal the `JSESSIONID` cookie value. LinkedIn stores that cookie *quoted* (`"ajax:123…"`) but wants the header *unquoted*. Get this wrong and every call is a 403. |
| **Protocol version** | `x-restli-protocol-version: 2.0.0`. Omit it and several endpoints silently return a different, older payload shape. |

Plus a browser-shaped `User-Agent` and the `x-li-track` client-metadata header
that the real front-end sends. See [`app/linkedin/client.py`](app/linkedin/client.py).

### Which endpoints, and why

The workhorse is **`/identity/profiles/{id}/profileView`**. It returns the entire
profile inlined in one document — header block plus `positionView`,
`educationView`, `skillView`, `certificationView`, `languageView`, `projectView`,
`publicationView`, `honorView`, `volunteerExperienceView`, `courseView`,
`patentView`, `testScoreView`. One round trip covers roughly 95% of the profile.

Four smaller calls fill the gaps, fired **concurrently** after `profileView`
returns:

| Endpoint | Adds |
|---|---|
| `/profileContactInfo` | email, phone, websites, Twitter, birthday |
| `/networkinfo` | follower count, connection count, degree of separation |
| `/skills?count=100` | the full skill list (`profileView` truncates it) |
| `/identity/dash/profiles` | premium / influencer / open-to-work badges |

Only `profileView` is required. **Every other call is allowed to fail** — a
403 on contact info just means that person hides their email, which is normal
and shouldn't fail the request. Failures land in `meta.endpoints_failed` and
`meta.warnings` instead.

### Two response encodings

Voyager speaks two dialects depending on the `Accept` header:

- **Inlined** (`application/json`) — a fully-nested tree. Easy to parse.
- **Normalised** (`application/vnd.linkedin.normalized+json+2.1`) — a small
  `data` object of URN pointers plus a flat `included[]` array of every entity,
  each tagged with `$type`. Compact, but you resolve pointers yourself.

This service asks for inlined everywhere except the dash endpoint, which only
speaks normalised.

### Parsing the awkward parts

Three Voyager conventions need real handling rather than a naive `dict.get`:

**Images are split in half.** A `VectorImage` gives you a `rootUrl` and a list
of per-size `artifacts`, each with a `fileIdentifyingUrlPathSegment` carrying
the signature and expiry. Neither half is a usable URL alone — you concatenate
them. The parser resolves every size, sorts largest-first, and surfaces the
signed URL's expiry as `expires_at` so callers know when links go stale.

**Dates are genuinely partial.** LinkedIn lets people enter a year with no
month. Emitting `"2021-01-01"` for what someone typed as `2021` would be
inventing data, so dates are modelled as `{year, month, day, text}` with
whatever is actually known, plus a rendered `text` for display.

**Rest.li unions are single-key envelopes.** Polymorphic values arrive wrapped
as `{"com.linkedin.common.VectorImage": {…}}`. A shared `unwrap_union` helper
peels these.

One derived field worth calling out: `total_experience_months` computes the
**union** of all position date ranges rather than the sum, so two concurrent
one-year roles count as 12 months of experience, not 24.

### Designing for schema drift

Voyager is a private API with no compatibility guarantee. The realistic failure
mode isn't "LinkedIn blocks us" — it's "LinkedIn renames a field and one section
starts coming back empty." So:

- **Each section parses independently.** A section that throws is caught,
  degraded to an empty list, and recorded in `meta.warnings`. A change to how
  certifications are shaped doesn't cost you the other twelve sections.
- **Types are checked, not assumed.** Every parser tolerates a list arriving as
  a string, `null` where an object was expected, and `None` items inside lists.
  There's a test that feeds deliberately wrong types through and asserts the
  parse still succeeds.
- **`meta` tells you what happened.** `endpoints_succeeded`, `endpoints_failed`,
  and `warnings` let you distinguish *"this person has no certifications"* from
  *"we couldn't read the certifications"* — a distinction a bare JSON body
  can't make.
- **`GET /api/v1/profile/raw`** returns unparsed upstream payloads, which is how
  you diagnose drift when a section does go empty.

### Fallback path

If Voyager returns a security challenge or refuses a profile outright, the
service fetches the rendered page and extracts the `schema.org/Person` JSON-LD
block, falling back again to Open Graph meta tags. That yields materially less
(no skills, certifications, languages, or dates) so it's clearly marked with
`meta.source: "public_html"` and a warning. It is a degraded result, not a
silent substitute.

### Protecting the upstream account

The scraping account is the single point of failure for the whole service, so
the design spends effort keeping it healthy:

- **Successful scrapes are cached** in-process (TTL + LRU). Re-requesting a
  profile within the window costs zero Voyager calls.
- **Randomised jitter** between sequential calls, so a burst doesn't look
  machine-timed.
- **Per-IP rate limiting** on the public endpoint.
- **Bounded retries** with exponential backoff — and only on transient errors
  (5xx, network). A 429 or an auth failure is *never* retried, because
  retrying those is exactly what escalates a throttle into a ban.

---

## Quick start

Requires Python 3.11+.

```bash
git clone https://github.com/nevilsonani/linkedin-profile-api.git
cd linkedin-profile-api

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env             # then fill in LINKEDIN_LI_AT + LINKEDIN_JSESSIONID

uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/docs>.

Verify the session is live before anything else:

```bash
curl http://127.0.0.1:8000/health/linkedin
# {"status":"ok","authenticated":true,"as_public_id":"your-account"}
```

### Configuration

Every setting is an environment variable; all have defaults except the cookies.

| Variable | Default | Purpose |
|---|---|---|
| `LINKEDIN_LI_AT` | — | **Required.** Session cookie. |
| `LINKEDIN_JSESSIONID` | — | **Required.** Doubles as the CSRF token. |
| `API_KEYS` | *(empty)* | Comma-separated keys for `X-API-Key`. Empty ⇒ open API. |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins. |
| `CACHE_TTL_SECONDS` | `900` | Profile cache lifetime. `0` disables. |
| `CACHE_MAX_ENTRIES` | `512` | LRU capacity. |
| `RATE_LIMIT` | `30/minute` | Per-IP limit. |
| `REQUEST_TIMEOUT_SECONDS` | `25` | Per-call timeout. |
| `MAX_RETRIES` | `3` | Retries for transient upstream failures only. |
| `MIN_REQUEST_DELAY` / `MAX_REQUEST_DELAY` | `0.4` / `1.2` | Jitter between calls. |
| `ENABLE_DOCS` | `true` | Set `false` to hide `/docs` in production. |
| `LOG_LEVEL` | `INFO` | |

---

## Getting your LinkedIn cookie

1. Log in to <https://www.linkedin.com> in Chrome or Firefox.
2. Open DevTools → **Application** (Chrome) / **Storage** (Firefox) → Cookies →
   `https://www.linkedin.com`.
3. Copy the **value** of `li_at` → `LINKEDIN_LI_AT`.
4. Copy the **value** of `JSESSIONID` (looks like `ajax:1234567890123456789`) →
   `LINKEDIN_JSESSIONID`. Surrounding quotes are stripped automatically.

**Treat `li_at` as a password.** Anyone holding it has full access to that
LinkedIn account. It is never logged (the logger redacts it), never committed
(`.env` is gitignored), and on Render it lives in the platform's secret store.

The cookie lasts roughly a year but is invalidated early if you log out, change
your password, or LinkedIn forces a re-auth. When that happens
`/health/linkedin` reports `degraded` and scrapes return `LINKEDIN_AUTH_FAILED`
— re-issue the cookie and redeploy.

> **Use a throwaway account.** Automated access violates LinkedIn's User
> Agreement and accounts do get restricted. Don't point this at an account you
> care about. See [Known limitations](#known-limitations).

---

## Deployment

### Render (blueprint included)

1. Push the repo to GitHub.
2. Render → **New** → **Blueprint** → select the repo. It reads
   [`render.yaml`](render.yaml).
3. When prompted, supply the three secrets marked `sync: false` —
   `LINKEDIN_LI_AT`, `LINKEDIN_JSESSIONID`, `API_KEYS`. These are stored by
   Render, never in git.
4. Deploy. HTTPS and the certificate are automatic.

Health check path is `/health`, which never touches LinkedIn — so the platform
won't burn Voyager calls probing liveness.

> On Render's free tier the instance sleeps after inactivity; the first request
> after a sleep takes ~30s and starts with a cold cache.

### Docker

```bash
docker build -t linkedin-profile-api .
docker run -p 8000:8000 --env-file .env linkedin-profile-api
```

Multi-stage build, non-root user, built-in `HEALTHCHECK`. Honours `$PORT`, so it
drops onto Fly.io, Cloud Run, Railway, or ECS unchanged.

---

## API reference

All responses are JSON. Every response carries an `x-request-id` header (echoed
from your `X-Request-ID` if you send one) — quote it when reporting problems.

### `GET /api/v1/profile`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `url` | string | **required** | Profile URL or bare username. |
| `include_contact_info` | bool | `true` | Fetch email / websites / phone. |
| `include_network_info` | bool | `true` | Fetch followers / connections. |
| `include_skills` | bool | `true` | Fetch the full skill list. |
| `use_cache` | bool | `true` | `false` forces a live scrape. |

`url` is forgiving. All of these work:

```
https://www.linkedin.com/in/williamhgates/
https://www.linkedin.com/in/williamhgates?trk=nav
http://linkedin.com/in/williamhgates
linkedin.com/in/williamhgates
https://in.linkedin.com/in/some-person-1a2b3c4     # country subdomains
https://www.linkedin.com/en/in/jane-doe            # locale prefixes
https://www.linkedin.com/pub/jane-doe/1/2/3        # legacy /pub/ URLs
https://www.linkedin.com/in/ACoAAA1234567890       # obfuscated member ids
https://www.linkedin.com/in/jos%C3%A9-garcia       # percent-encoded
williamhgates                                      # bare username
```

### `POST /api/v1/profile`

Same operation, JSON body:

```bash
curl -X POST "$BASE_URL/api/v1/profile" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"url": "https://www.linkedin.com/in/williamhgates", "include_contact_info": false}'
```

### Other endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | no | Liveness. Never calls LinkedIn. |
| `GET /health/linkedin` | yes | Session validity. Returns **200** with `status: "degraded"` when the cookie is dead, so a monitor can tell a dead session from a dead process. |
| `GET /health/cache` | yes | Cache hit/miss statistics. |
| `GET /api/v1/profile/raw` | yes | Unparsed upstream payloads. Debug aid for schema drift; disabled when `API_KEYS` is unset, since raw payloads can contain contact details. |
| `DELETE /api/v1/cache` | yes | Flush the cache (e.g. after refreshing the cookie). |

### Authentication

If `API_KEYS` is set, every endpoint except `/health` requires `X-API-Key`.
Keys are compared in constant time. If `API_KEYS` is empty the API is open —
fine locally, not for a public deployment.

---

## Response schema

Full JSON Schema is at `/openapi.json`. Two rules govern the whole thing:

1. **Every field is present; any field may be `null`.** Lists are `[]`, never
   `null`. You never need to guard for a missing key.
2. **Absent and hidden are different.** An empty `certifications` list plus an
   empty `meta.warnings` means the person has none. An empty list plus a warning
   means we couldn't read them.

### Top level

| Field | Type | Notes |
|---|---|---|
| `public_id` | string | Vanity slug. |
| `profile_url` | string | Canonicalised. |
| `urn`, `member_id` | string? | Stable LinkedIn identifiers — use these as keys, not names. |
| `first_name`, `last_name`, `full_name`, `maiden_name` | string? | |
| `headline`, `about`, `industry` | string? | `about` preserves newlines. |
| `location` | object? | `text`, `city`, `country`, `country_code`, `postal_code` |
| `is_student`, `is_influencer`, `is_premium`, `is_open_to_work`, `is_hiring` | bool? | `null` = unknown, not false. |
| `profile_picture`, `background_image` | object? | See below. |
| `experience` | array | Flat list of positions. |
| `experience_grouped` | array | Same roles grouped by company, as the UI shows them. |
| `education`, `skills`, `certifications`, `languages` | array | |
| `projects`, `publications`, `honors`, `volunteer_experience` | array | |
| `courses`, `patents`, `test_scores`, `organizations` | array | |
| `contact_info`, `network_info` | object? | |
| `current_position` | object? | Most recent role with no end date. |
| `total_experience_months` | int? | Union of ranges — overlaps counted once. |

### Recurring objects

**`DateParts`** — `{ year, month, day, text }`. Any component may be `null`;
`text` renders what's known (`"Jun 2021"`, `"2021"`).

**`DateRange`** — `{ start, end, is_current, duration_months, text }`.
`end: null` with `is_current: true` means present.

**`Image`** — `{ url, width, height, variants[], expires_at }`. `url` is the
largest variant; `variants` lists every size LinkedIn offered, largest first.

> ⚠️ **Image URLs are signed and expire** (`expires_at`, typically weeks). If
> you're storing profile pictures, download the bytes — don't persist the URL.

---

## Error handling

Every failure — validation, auth, upstream, unhandled — returns the same shape:

```json
{
  "success": false,
  "error": { "code": "PROFILE_NOT_FOUND", "message": "…", "hint": "…" },
  "request_id": "7a7673fe9cc94db4"
}
```

Branch on `code`, never on `message`.

| Code | HTTP | Meaning & what to do |
|---|---|---|
| `INVALID_PROFILE_URL` | 400 | Not a LinkedIn profile URL. Fix the input. |
| `VALIDATION_ERROR` | 422 | Malformed request. See `/docs`. |
| `MISSING_API_KEY` / `INVALID_API_KEY` | 401 / 403 | Check `X-API-Key`. |
| `PROFILE_NOT_FOUND` | 404 | Deleted, renamed, or unreachable for this account. |
| `PROFILE_UNAVAILABLE` | 403 | Exists but privacy/network-distance blocks it. Not retryable. |
| `LINKEDIN_AUTH_FAILED` | 503 | **Cookie expired.** Re-issue `li_at` and redeploy. |
| `LINKEDIN_CHALLENGE` | 503 | CAPTCHA/checkpoint. Log in via browser, clear it, re-issue the cookie. |
| `LINKEDIN_RATE_LIMITED` | 429 | LinkedIn is throttling. **Back off for minutes** — retrying hard risks a ban. |
| `RATE_LIMITED` | 429 | *This* API's per-IP limit. Retry shortly. |
| `LINKEDIN_NOT_CONFIGURED` | 503 | No cookie set on the server. |
| `PARSE_ERROR` | 502 | Voyager's shape changed. Inspect `/api/v1/profile/raw`. |
| `LINKEDIN_UPSTREAM_ERROR` | 502 | Network failure or LinkedIn 5xx. Retryable. |
| `INTERNAL_ERROR` | 500 | Bug. Report with the `request_id`. |

Internal errors are deliberately opaque — a leaked traceback could expose cookie
material. Details go to the (redacting) server log.

---

## Known limitations

**Be honest about these before relying on the service.**

### Terms of service and account risk

Automated scraping violates LinkedIn's User Agreement, regardless of the
technique. The account whose cookie you supply **can be restricted or
permanently banned**. Use a throwaway account, keep volume low, and don't build
anything business-critical on top of this without accepting that risk. The
built-in caching, jitter, and rate limiting reduce exposure; they don't
eliminate it.

*(For context: `hiQ v. LinkedIn` held that scraping **public** data isn't a
Computer Fraud and Abuse Act violation. That is a narrow US-law finding about
criminal liability — it does not make the contractual ToS breach go away, and it
says nothing about authenticated scraping like this, which is what a session
cookie makes it. Not legal advice.)*

### Undocumented, unstable upstream

Voyager can change without warning. The architecture degrades gracefully
(section-level isolation, `meta.warnings`, the raw endpoint) but a determined
schema change **will** eventually need parser updates. Treat this as code
requiring maintenance, not a stable vendor SDK.

### Coverage bounded by what your account can see

You get what your logged-in account gets. Specifically:

- **Contact info** (email, phone) only for 1st-degree connections, typically.
- **Connection counts** cap at `500` — that's LinkedIn's display cap, not a bug.
- **Out-of-network profiles** may return `PROFILE_UNAVAILABLE`.
- **Recommendations, endorsement details, activity/posts, and "People also
  viewed"** are not implemented. They live on separate paginated endpoints and
  each would multiply the call count per scrape.
- **Endorsement counts** are populated only when the skills endpoint supplies
  them; often `null`.

### Other constraints

- **Image URLs expire** (see `expires_at`). Download bytes, don't store URLs.
- **`/in/` member profiles only.** Company and school pages are explicitly
  rejected with an explanatory error.
- **Cache is per-process.** Multiple instances don't share it, and a restart
  starts cold. Swap `TTLCache` for Redis behind the same interface if you scale
  out.
- **Rate limiting is per-process too** (in-memory), so it's per-instance rather
  than global.
- **No pagination.** Profiles with very large sections may be truncated by
  LinkedIn's own defaults; skills are explicitly fetched up to 100.
- **Single session.** No cookie-pool rotation — one account, one throughput
  ceiling.
- **English output.** Requests pin `en_US`, so enum labels are English
  regardless of the profile's language.

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest                # 99 tests
ruff check .
```

The suite runs entirely offline. `respx` intercepts traffic at the HTTP
boundary, so the real client code — header construction, cookie formatting,
status handling, retries, fallback — is exercised against realistic fixture
payloads; only the network is faked.

| File | Covers |
|---|---|
| `tests/test_url_parsing.py` | URL normalisation, including lookalike-domain rejection |
| `tests/test_parsers.py` | Every section, partial dates, image URL assembly, deliberately malformed payloads |
| `tests/test_api.py` | Endpoints, auth, error envelopes, graceful degradation, HTML fallback, and that the CSRF header matches the cookie |
| `tests/test_cache.py` | TTL expiry, LRU eviction, concurrency, key derivation |

Worth highlighting: `test_optional_endpoint_failure_is_a_warning_not_an_error`
asserts that a 404 on contact info and a 500 on skills still produce a usable
200 response with the failures recorded in `meta` — the degradation behaviour
this design depends on.

---

## Project layout

```
app/
├── main.py                     FastAPI app, middleware, error translation
├── config.py                   Settings (pydantic-settings)
├── api/
│   ├── deps.py                 API-key auth, service injection
│   └── routes/                 profile.py, health.py
├── linkedin/
│   ├── client.py               Voyager HTTP client — auth, retries, status mapping
│   ├── exceptions.py           Typed errors carrying HTTP status + code
│   └── parsers/
│       ├── common.py           Dates, images, companies, URNs
│       ├── profile_view.py     The main document
│       ├── supplementary.py    Contact / network / skills / dash
│       └── public_html.py      JSON-LD + Open Graph fallback
├── models/profile.py           Response schema (Pydantic)
├── services/
│   ├── profile_service.py      Orchestration and merging
│   └── cache.py                TTL + LRU
└── utils/
    ├── url.py                  URL normalisation
    └── logging.py              Structured logs with secret redaction
```

## Security

- Secrets come from the environment only. `.env` is gitignored; `.env.example`
  holds no real values.
- The logger redacts `li_at`, `JSESSIONID`, `csrf-token`, and `X-API-Key`
  patterns from every string it emits.
- API keys are compared with `secrets.compare_digest`.
- Internal errors return an opaque message; tracebacks stay server-side.
- The Docker image runs as a non-root user.
- The raw-payload endpoint is disabled unless API keys are configured.
