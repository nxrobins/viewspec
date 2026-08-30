# Private Studio Review Deployment Contract

Status: SDK boundary and replay-safe API bridge deployed; separate worker/service isolation is
proved locally in a draft backend PR. Production review deployment, signing and canary approval
remain pending. Account-aware readiness is implemented for review, not yet deployed.

This is the minimum acceptance contract for adding private Studio review beside the existing
`viewspec-api` service. It is intentionally narrower than a collaboration platform. The first
release creates an unlisted, expiring link for one immutable checked revision, lets a reviewer
leave source-bound comments, and preserves exact owner approval.

## Mount boundary

The SDK provides a dependency-free ASGI wrapper for the dedicated review service. It must not be
mounted in the hosted compiler process because the hosted compiler and public SDK intentionally
carry different protobuf descriptor sets. A minimal review-service app can use:

```python
health_app = FastAPI()

review_service = StudioReviewService(
    "/data/studio-review",
    signing_key=load_stable_capability_key(),
    receipt_signing_key=load_active_receipt_key(),
    receipt_verification_keys=load_previous_receipt_keys(),
    key_id=active_receipt_key_id(),
    verifier=run_verified_review_rebuild,
)
review_http = StudioReviewHTTPAdapter(
    review_service,
    public_origin="https://review.viewspec.dev",
    authorize_upload=authorize_internal_studio_review_upload,
)
internal_auth = StudioReviewInternalAuth(
    load_api_to_review_hmac_key(),
    nonce_store=StudioReviewInternalNonceStore("/data/studio-review/internal-nonces.sqlite3"),
)
app = StudioReviewASGIApp(
    review_http,
    downstream=health_app,
    internal_auth=internal_auth,
    allow_direct_create=False,
)
```

The compiler API owns a small provider-specific `/v1/reviews` upload bridge. It authenticates an
active paid key, bounds the archive, forwards only the review media type, disclosure, expiry, and
idempotency headers, then calls a private `/internal/v1/reviews` ingress with mutual request and
response authentication. The raw paid key never crosses that boundary.

The review wrapper intercepts the signed `/internal/v1/reviews` ingress plus `/review` and
`/review/...`; health and unrelated protocols continue downstream. Production composition closes
direct `/v1/reviews` creation with `allow_direct_create=False`. The internal verifier binds the
protocol version, direction, method, path, exact allowlisted creation headers, archive SHA-256,
timestamp, and nonce before the adapter or storage sees the request. It forwards only those four
creation headers plus a server-created authentication marker. The raw paid key and every internal
authentication header are discarded at the boundary.

Request nonces are admitted to a bounded SQLite store on the durable review volume, so a process
restart does not reopen the active request replay window. Response nonces are admitted by the API
bridge's own durable verifier store when it checks the review service response. The response
signature binds the status, ingress path, content type, body SHA-256, timestamp, response nonce,
and originating request nonce. The API bridge must verify that response before returning its body.
Bodies, paths, and headers are bounded while streaming; ambiguous duplicate headers fail closed.
Browser review routes still trust only the ASGI scope's `scheme` for HTTPS identity. An incoming
`X-Forwarded-Proto` header is never sufficient by itself. The wrapper strips reserved
`x-viewspec-internal-*` headers from every non-internal review route, so ordinary traffic cannot
mint the server-only adapter authentication marker.

The wrapper sends blocking review storage and verifier work to a worker thread after it has bounded
the asynchronous request stream. A remote verifier therefore cannot freeze the review-service
event loop.

Production configuration must provide:

- durable private storage at `/data/studio-review` on a dedicated review-service volume;
- a stable capability-derivation key of at least 32 random bytes, plus an independently rotatable
  active receipt-signing key, explicit key id, and previous receipt verification keyring;
- the canonical review origin `https://review.viewspec.dev`, provisioned and tested before enablement;
- upload authorization in `viewspec-api` using the existing paid API-key identity before any archive
  reaches the review service;
- request logging disabled for `/review/...`, with capability fragments never reaching the server;
  and
- retention, expiry, deletion, backup, and signing-key rotation procedures.

## Rebuild worker boundary

Session creation must not trust an internally consistent uploaded artifact set. The review-service
process first calls `make_studio_review_rebuild_request(package, envelope)`. This revalidates every package
byte and creates a bounded request containing only the exact semantic source, optional `DESIGN.md`,
and the hash-bound envelope. Uploaded checked artifacts are not transported to or executed by the
worker. The isolated worker calls `python -m viewspec.studio_review_worker`; it rebuilds the checked
static/React comparison and requires the complete rebuilt inventory to equal every expected upload
path, size, and SHA-256.

The worker must set:

```text
VIEWSPEC_STUDIO_REVIEW_NODE_MODULES_DIR=/opt/viewspec-host-verify/node_modules
```

The directory must be part of the immutable image, contain `node_modules/.package-lock.json`, and
match the pinned ViewSpec React package versions. The rebuild path has no install fallback. It
invokes the trusted Vite binary directly, so npm dependency installation and package lifecycle
hooks are not run. Uploaded checked artifacts are compared as inert bytes and never executed.

The SDK rebuild evidence deliberately does not claim process isolation. The hosted worker must run
the rebuild inside infrastructure that actually enforces:

- outbound network denied;
- dependency and project lifecycle hooks disabled;
- arbitrary uploaded commands disabled;
- CPU at most 30 seconds;
- memory at most 512 MiB;
- wall time at most 120 seconds; and
- file and byte limits covering the exact package without exceeding the SDK maxima.

The runner binds that enforcement record to the canonical rebuild-evidence SHA-256 and passes it to
`bind_studio_review_sandbox_attestation`. Only the resulting service-shaped verification may be
returned to `StudioReviewService`. A plain dictionary assembled by the review-service process is not
an isolation proof; the deployment must retain the runner's authoritative execution receipt.

### Recommended Fly topology

Use three Fly apps in the same private organization network, built from the same reviewed source.
A single app with several process groups is not an adequate secret boundary: Fly injects an app's
secrets into every Machine belonging to that app. The public SDK and hosted compiler also own
different protobuf descriptors and must not be loaded into the same Python process. A narrow upload
bridge therefore connects the compiler API to a dedicated review service, which connects to a
dedicated rebuild worker.

| App | VM boundary | Responsibility |
| --- | --- | --- |
| `viewspec-api` | Existing 1 GiB machine with the compiler `/data` volume and public HTTPS service | Authenticate paid uploads, bound the archive, and forward it over one mutually authenticated private ingress. It does not import the Studio review SDK runtime. |
| `viewspec-review` | Dedicated 512 MiB machine with a separate review volume and `review.viewspec.dev` HTTPS service | Persist review state, serve immutable review artifacts, enforce browser capabilities, and call the private worker. It receives no billing, Stripe, compiler-receipt, or compiler-database secret. |
| `viewspec-review-worker` | Dedicated 512 MiB machine, one concurrent rebuild, no public service, no volume, and no secret except the worker HMAC key | Authenticate an internal rebuild request, run the fixed SDK worker inside Bubblewrap, and return signed rebuild plus sandbox evidence. |

The API reaches `viewspec-review.internal:8080`; that ingress accepts only a hash-, timestamp-,
nonce-, and protocol-bound HMAC request, then removes the API credential before storage. The
review service reaches `viewspec-review-worker.internal:9090`. The worker binds only to
`fly-local-6pn:9090`, has no public service, and receives no volume. The three apps share no single
secret: API↔review and review↔worker use independent HMAC keys. See Fly's
[private-DNS](https://fly.io/docs/networking/private-networking/),
[internal service](https://fly.io/docs/networking/app-services/), and
[app-secret](https://fly.io/docs/apps/secrets/) contracts.

Each internal hop has its own dedicated HMAC secret. The SDK now implements this complete contract
for API→review requests and responses; an independent review→worker hop is implemented and locally
proved in the draft backend runtime, but not deployed. Stale, replayed, unsigned,
or differently hashed API→review messages fail before state creation. The review service never
receives the raw paid API key. The worker receives no billing database, signing key, review volume,
API key, receipt secret, Stripe secret, or Fly API token.

Inside the worker Machine, Bubblewrap runs the fixed SDK module with a read-only Python/Node runtime,
read-only pinned dependency seed, empty temporary workspace, cleared environment, new PID/IPC/UTS
namespaces, and `--unshare-net`. Bubblewrap documents that the new network namespace cannot see the
host network. CPU and wall time are bounded by the runner; the dedicated Machine's cgroup is the
hard 512 MiB memory boundary. The worker admits one rebuild at a time, samples aggregate cgroup CPU
usage against a 30-second ceiling, and kills the sandbox at either the CPU or 120-second wall
boundary. The canary must inspect the actual cgroup limit and prove the network namespace and an
egress canary rather than trusting configuration text. See the
[Bubblewrap namespace contract](https://github.com/containers/bubblewrap#sandboxing).

## Persistence and operations

The reference service keeps immutable package objects plus SQLite state. On the existing single
region deployment, the database and objects must share the durable volume and be backed up as one
consistency unit. Before enabling Share:

1. Exercise restore into a clean canary volume and verify signed receipts and object hashes.
2. Run the expiry/deletion sweeper with a dry-run mode, bounded batches, and retained aggregate
   counts that contain no capability, cookie, comment body, or artifact content.
3. Prove signing-key rotation retains verification for existing receipts while all new receipts use
   the active key id.
4. Prove restart recovery between object persistence and database commit leaves no usable partial
   session and that idempotent retry creates exactly one session.
5. Alert on aggregate create, verification, exchange, comment, approval, revoke, expiry, and delete
   outcomes by stable code. Never record request URLs, authorization headers, cookies, fragments,
   semantic source, fixture values, or comment bodies.

The SDK service core now implements the local operator primitives for this gate:

- startup and explicit `reconcile_storage(...)` recover an interrupted delete, remove an
  uncommitted object or staging directory, and reject ambiguous filesystem state;
- `run_retention(dry_run=..., limit=...)` records aggregate-only counts and expires a bounded batch;
- `verify_storage()` checks SQLite integrity and foreign keys, revalidates every retained package
  byte, verifies all stored receipts through the configured keyring, rejects unexpected objects,
  and returns only counts plus an aggregate object-set hash; and
- `aggregate_telemetry(...)` returns bounded lifecycle and maintenance counts without review-level
  identifiers or content.

Receipt rotation must not rotate the stable capability key. Start the replacement process with the
new active receipt key and key id while retaining every still-valid old receipt key in
`receipt_verification_keys`. A clean restored volume passes only when `verify_storage()` can verify
both old and new receipts. Once the product's receipt-retention horizon has elapsed, the retired key
may be removed in a separately audited change.

## Release gate

Studio is network-free by default. `viewspec studio --compare --install --share` is an explicit
private-review opt-in, but it still renders no Share control unless the canonical API returns a
current Ed25519-signed `studio_share_release` receipt for an eligible caller. Readiness and upload
apply the same current durable paid entitlement and beta-account allowlist; legacy, revoked,
inactive, free and out-of-cohort identities cannot obtain readiness. Readiness consumes no compile
quota, invokes no rebuild, and returns `no-store` with credential-aware `Vary` headers. Upload
rechecks eligibility; a previously obtained receipt does not override later revocation.

The local daemon fetches the public receipt key without credentials, then authenticates only its
readiness request using the same daemon-held credential used for upload. Its HTTP transport refuses
redirects and environment proxies, and bounds responses while streaming. It verifies the exact
origins, deployment/report/run hashes, bounded lifetime, and all nine canary checks. Neither the
receipt, API key, nor local paths reach browser HTML, status JSON or daemon metadata. A denied
account receives a content-free explanation and can continue locally without `--share`.

The Share control remains absent until one production canary proves all of the following against
the deployed HTTPS origin:

| Gate | Required proof |
| --- | --- |
| Exact ingress | The deterministic archive, package id, source hash, root manifest hash, inspection hash, and both target artifact-set hashes agree locally and remotely. |
| Independent rebuild | A pinned, install-free source rebuild reproduces every checked artifact byte; one deliberate mismatch is rejected before session creation. |
| Real isolation | The retained runner receipt proves denied egress and enforced CPU, memory, wall, file, and byte limits; egress and lifecycle-hook canaries fail closed. |
| Private authority | Anonymous access fails generically; fragment exchange is one-time; cookies are `Secure`, `HttpOnly`, `SameSite=Strict`; reviewer approval is forbidden. |
| Complete journey | Chromium, Firefox, and WebKit each complete create → exchange → static/React review → semantic comment → owner approval in under five minutes. |
| No leaks | External requests, capability leaks, CSP violations, console errors, and sensitive log fields are all zero. |
| Recovery | Restart, retry, expiry, reviewer rotation, revocation, deletion, backup restore, and key rotation pass without duplicate or orphaned usable sessions. |

### Retained canary interface

Freeze the immutable production deployment and deployment-owned stage driver before the first
probe. The public SDK runner then invokes exactly nine stages in canonical order and promotes a
stage only after its closed schema and product semantics pass:

```bash
python scripts/run_studio_production_canary.py init \
  --out <canary-directory> \
  --driver <deployment-owned-stage-driver.py> \
  --deployment-sha256 <immutable-deployment-sha256>
python scripts/run_studio_production_canary.py run --root <canary-directory>
```

The stages are `deployment`, `ingress`, `rebuild`, `isolation`, `browser-chromium`,
`browser-firefox`, `browser-webkit`, `recovery`, and `leak-audit`. Each writes one hash-bound JSON
artifact beneath `stages/`. The final report retains one redacted command receipt per stage with
argv, stdout, and stderr hashes, bounded byte counts, exit status, and elapsed time; it never
retains the streams themselves. `checkpoint.json` is replaced atomically after every promoted
stage. Resume first revalidates the plan, driver, runner, verifier, every prior stage hash, and
every prior stage's semantics.

Run the independent verifier before treating the result as production evidence:

```bash
python scripts/check_studio_production_canary.py \
  <canary-directory>/production-canary-evidence.json \
  --out <canary-directory>/verified-result.json \
  --share-release-payload-out <canary-directory>/studio-share-release-payload.json
```

The second output is deliberately unsigned. Only the production API's existing Ed25519 receipt
signer may sign that exact closed payload and publish it from
authenticated `GET https://api.viewspec.dev/v1/studio-share-readiness` as
`{"schema_version":1,"release":<signed-receipt>}`. The API continues publishing its public key at
`GET /v1/receipt-key`. Release lifetime is at most one hour; the recommended beta window is 15
minutes. Failed, incomplete, expired, differently originated, or differently signed evidence
keeps Share absent. The verifier never receives a signing key.

With a verified release, Studio's first Share click only prepares the exact local package and
shows the structured disclosure. The browser must then confirm that exact package and choose a
bounded expiry before the daemon uploads it. The daemon validates returned ingress bytes, package,
source, root-manifest, inspection, and both target artifact identities before exposing owner and
reviewer fragment-capability links. A failure leaves the local package available for inspection
and retry.

The deployment repository owns the stage driver because only it can inspect the actual Fly app,
secret, volume, worker, backup, and log boundaries. The SDK runner and verifier cannot manufacture
those observations. That collector is implemented locally with active ingress/rebuild mismatch
probes, three-engine browser journeys, an authorization-gated restart and restored-volume drill,
multi-key receipt verification, aggregate telemetry checks, and exact-value log scanning. It has
not been installed as a production workflow or run against the canonical origin. The API bridge
and fail-closed readiness endpoint are deployed, while account-aware readiness is pending review;
the separate review/worker runtime is locally proved in a draft deployment-repository PR. No
signed production release is installed, so the production gate remains open and current public
installs still show no Share control.

After this gate passes, expose Share to a bounded beta cohort first. Public galleries, mutable hosted
source, live presence, arbitrary project execution, and production-data connectors remain out of
scope.
