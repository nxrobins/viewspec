# ViewSpec Studio Product Goal

Status: Active

Date: 2026-08-14

## North star

ViewSpec should be the fastest way for a person and an agent to create a beautiful, trustworthy
application together.

The product promise is:

> Point at the product. Ask for the outcome. Approve the change.

The compiler, semantic source, provenance, replay, responsive anchors, Freerange, Pretext,
verification, and receipts remain the reason this experience can be trusted. They are not the
experience a new user must learn first.

## Ruthless priorities

1. **One unforgettable loop before more capabilities.** Preview, Comment, Approve must feel
   complete before natural-language generation, hosted collaboration, or deployment are added.
2. **Show the product before explaining the machinery.** The live interface is primary. Proof is a
   calm confidence state that expands when requested.
3. **Semantic direct manipulation is the differentiation.** A comment on pixels must arrive at the
   agent with exact semantic identity. A proposed repair must show meaningful before and after.
4. **More compute must buy more product.** ViewSpec earns a premium through fidelity, continuity,
   isolated repair, and consistent targets—not by claiming the fewest tokens.
5. **No dishonest magic.** Fixed demos, incomplete verification, and bounded integrations retain
   explicit boundaries. Generated output is never presented as editable source.

## Milestone 1: the ViewSpec Moment

Given one valid local `viewspec.intent.json` or `viewspec.app.json`, one command opens a checked
creation canvas with one obvious loop:

```bash
viewspec studio
```

The milestone does not generate semantic source from natural language, deploy an application, or
host a collaborative session. Those are later layers. It makes the already-implemented local
compiler, Review, Converge, and proof substrate feel like one product.

## Proved success criteria

The milestone passes only when every criterion below is proved on the same revision.

| Promise | Pass condition | Authoritative evidence |
| --- | --- | --- |
| One entry point | `viewspec studio [SOURCE]` opens either source kind; omission discovers exactly one canonical source and fails clearly for zero or two. | CLI and source-discovery tests. |
| Fast first result | A clean local starter reaches the ready response in less than 60 seconds, and the response records measured `ready_ms`. | Isolated journey test and retained test timing. |
| Checked before shown | Studio returns ready only after compile and artifact check pass; its confidence contract reports checked or verified without overstating scope. | Runtime integration test and exact readiness schema assertions. |
| Obvious human loop | The first screen names Preview, Comment, and Approve; Comment is the only primary action; the canvas offers 390, 768, and 1440 widths. | Generated-chrome contract test and real Chromium interaction test. |
| Semantic feedback | A browser comment resolves through the checked manifest and the agent receives the exact revision, viewport, and semantic target with acknowledgement semantics. | Existing Review server tests plus a Studio journey test. |
| Understandable change | A pending repair displays bounded semantic before/after values and a plain-language progress result; no authority token reaches the browser. | Converge projection and browser-chrome tests. |
| Quiet trust | The default chrome says Checked and Local; detailed proof scope is available on demand; hashes and compiler vocabulary do not dominate the primary path. | UI contract assertions and visual review at desktop and mobile sizes. |
| Existing guarantees hold | Loopback-only, no-network defaults, immutable generated artifacts, exact approval, fail-closed identities, and the full existing suite remain green. | Security tests, full pytest, Ruff, syntax checks, and CI. |

## Product-level success criteria

The full product objective remains open after Milestone 1. ViewSpec becomes the intended product
only when controlled studies prove all of the following:

- a new user reaches a desirable working interface within one minute;
- the user completes three meaningful visual-to-semantic changes without editing generated code;
- each change remains coherent across mobile, tablet, desktop, static, and React targets;
- repair is localized and introduces no stable-criterion regression;
- the user can understand health without reading a report and can inspect exact evidence on demand;
- a bounded project can be shared or deployed within five minutes; and
- blinded reviewers prefer the delivered product often enough to justify ViewSpec's compute premium.

### Product evidence ledger

The same-revision automated shakedown now proves the mechanical center of this objective. Starting
from a brief, it creates a checked AppBundle, opens Studio, submits and acknowledges exact Review
events, approves three independently meaningful semantic changes, and verifies every resulting
revision at 390, 768, and 1440 pixels in both static and React. It also rechecks route continuity,
declared interaction behavior, replay, runtime cleanliness, source hashes, and the prohibition on
generated-output edits. CI evaluates and retains this evidence under
`viewspec-studio-product-v1`; a shape-valid report cannot promote itself to a full product pass.

| Product criterion | Current status | What closes it |
| --- | --- | --- |
| Checked first value in under one minute | Mechanically proved in the isolated browser journey. | Meet the preregistered rate with new users in both study arms. |
| Three visual-to-semantic changes without generated edits | Mechanically proved across four exact source revisions. | Meet the preregistered completion and comprehension rates with new users. |
| Responsive static/React coherence after every change | Mechanically proved for stable interactions at 390, 768, and 1440; Studio now observes exact semantic pairs and routes one prioritized mismatch into the normal review loop. | Add the preregistered blinded fidelity judgment and meet the parity gate. |
| Localized repair without stable regression | Covered separately by the V2 value-premium mutation trials. | Meet the pooled repair and zero-regression assurance gates on eligible sessions. |
| Understandable health and inspectable evidence | Product surface and proof projection implemented. | Meet the preregistered new-user health-comprehension rate. |
| Private handoff in under five minutes | Local multi-browser HTTPS contract proved; not production. | Pass the authorized production canary and the human handoff-rate gate. |
| Preference worth the compute premium | Not measured. | Run at least 18 blinded sessions per arm and clear both the 65% preference rate and confidence interval above chance. |

Therefore the mechanical journey is green, while the full product goal remains open. Automated
acceptance is not evidence of desirability, and the local private-review service is not a deployed
sharing product.

The remaining human gate is now executable rather than aspirational. The
[Studio Human-Value Study V1](../conformance/studio-product-v1/HUMAN_STUDY.md) preregisters balanced
between-subjects allocation, reserve activation, the same three-change task, anonymous records,
artifact-bound blinded packets, production-only handoff evidence, conservative tie handling, and a
95% Wilson confidence interval. Its runner refuses optional stopping, changed packets, invalid
hashes, unknown claims, or a missing production canary. No participant data has been collected.

The production gate is likewise mechanically specified. A resumable canary runner now hash-binds
the immutable deployment, deployment-owned collector, runner, verifier, nine canonical stages,
redacted command receipts, and every promoted artifact. The independent checker rejects missing,
reordered, changed, or semantically weak evidence, and the human-study analyzer calls that checker
directly. The deployment-owned stage collector is also implemented locally, including deliberate
ingress and rebuild mismatches, three real browser engines, recovery and restored-volume drills,
receipt-key rotation proof, and exact-value leak scanning. Review into the deployment repository,
an explicitly authorized production workflow, and the production run remain pending; therefore
this is executable evidence infrastructure, not a production-sharing claim.

The V2 value-premium evaluator remains the measurement foundation. It must evolve from artifact
comparison into this end-to-end human-and-agent journey; token count is cost accounting, not the
primary quality objective.

## Milestone 2: first creation without the machinery

Status: implemented; acceptance evidence is required on every change.

A person can give their coding agent a product brief and optional local reference image, ask it to
open Studio, and receive checked canonical semantic source without manually editing a starter or
learning the schema. ViewSpec remains the deterministic contract and proof layer; it does not hide
a model client inside the SDK.

| Promise | Pass condition | Authoritative evidence |
| --- | --- | --- |
| Exact request | The normalized brief and optional reference identity are hash-bound into one deterministic task. | Task determinism and tamper tests. |
| Local by construction | Brief and reference remain under the workspace; PNG, JPEG, or WebP is capped at 10 MiB; no SDK network call or upload occurs. | Path, media, size, and metadata tests. |
| No template theater | Preparing a task creates no semantic source, and an unchanged ViewSpec starter is ineligible for acceptance. | Blank-workspace and starter-rejection tests. |
| Checked before canonical | Candidate validation, compilation, and artifact check all pass before `viewspec.app.json` or `viewspec.intent.json` appears. A failure publishes nothing. | Both-source acceptance and negative proof tests. |
| Durable evidence | The exact task, candidate, proof report, checked artifact, and canonical source remain available after acceptance. | Retained-path and hash assertions. |
| One human journey | The managed agent prepares, authors, accepts, then opens the result directly in Preview → Comment → Approve. | Managed-instruction contract and real Chromium journey. |
| Honest reference scope | Reference identity is proved; visual fidelity is explicitly `not_proven` until reviewed or evaluated separately. | Exact result-schema assertions and product copy. |

## Milestone 3: one product, static and React side by side

Status: implemented; acceptance evidence is required on every change.

An AppBundle can open one immutable Studio revision containing checked static and React products.
The comparison is a shared semantic review surface, not two unrelated sessions and not a visual
equivalence claim.

| Promise | Pass condition | Authoritative evidence |
| --- | --- | --- |
| One exact source | Static and React builds, retained artifacts, and every screen identity are bound to the same captured AppBundle hash. | Comparison-manifest and reload tests. |
| Both or neither | Both production targets build and check before promotion; route or semantic-identity drift fails closed and leaves no candidate revision. | Failure-injection and atomic-promotion tests. |
| One responsive canvas | One 390, 768, or 1440 selection sets both canvases to the exact same dimensions. | Real Chromium journey. |
| Synchronized navigation | Navigation from either static or React moves the other target to the same declared route without stale restore loops. | Bidirectional browser navigation assertions. |
| Same semantic target | A click in either canvas visibly highlights the exact matching semantic element in both Static and React, presents a human resource/field summary, and resolves the comment through the target's checked manifest to the same AppBundle screen and source identity. Exact implementation trace remains available on demand. | Browser-to-agent feedback assertion and synchronized-selection Chromium assertion. |
| Coherent approval | An approved AppBundle change shows its exact nested before/after field, rebuilds both targets, and retains a valid route across the revision. | App diff, identity-continuity, and end-to-end approval tests. |
| Honest scope | Locked dependency installation is explicit, runtime SDK network calls are absent, and visual parity remains `not_proven`. | Exact response and retained policy assertions. |

## Milestone 4: inspect the product in motion

Status: implemented; acceptance evidence is required on every change.

State and data evidence belong beside the product, not in a detached proof dashboard. AppBundle
comparison revisions expose those capabilities only when their exact source declares them.

| Promise | Pass condition | Authoritative evidence |
| --- | --- | --- |
| Checked replay timeline | Static and React state contract hashes, reducer conformance, and replay reports agree before Studio shows a checkpoint. | Comparison build and retained inspection-manifest tests. |
| Exact browser replay | Selecting a checkpoint cleanly reloads both artifacts without invalidating the checked browser handshake, drives only uniquely mapped declared actions with matching rendered payload bindings, and displays a compact human summary of the proved final state; ambiguity fails closed as `not_replayable`. | Real Chromium action replay, repeated-handshake regression coverage, and negative projection tests. |
| Visible state truth | One bounded scalar state projection updates the same exactly-once semantic text binding in Static and React; replay proves the final visible string and presents it before internal state metadata. | State-text schema negatives, reducer conformance, target builds, generated browser assertion, and Studio replay journey. |
| Honest target behavior | Studio can expose different current static/React results without calling them visual parity or hiding the distinction. | Chromium assertion for static fixture text versus React live rebinding. |
| Resource identity at the point of use | A selected field shows its checked resource → record → field identity, binding, fixture value, and current target text. | Resource projection and browser selection assertions. |
| State-aware feedback | The agent receives an allowlisted current-revision replay checkpoint plus server-derived resource evidence with the semantic target. | Browser-to-agent event assertion and forged-evidence rejection. |
| Honest data scope | The UI and machine contract say fixture scope and `production_data: not_claimed`; no persistence or backend truth is implied. | Exact readiness schema, copy, and policy tests. |

## Milestone 4.5: turn target drift into one exact change

Status: implemented as browser-observed semantic geometry; acceptance evidence is required on
every change.

Static and React should not merely sit beside one another. Studio observes the visible leaf nodes
that share an exact checked DOM/IR identity, compares their text, geometry, type, and color at the
current canonical viewport, and presents one prioritized discrepancy in human language. The
result remains an observation from the current browser, not a pixel-parity proof.

| Promise | Pass condition | Authoritative evidence |
| --- | --- | --- |
| Exact comparison set | Only visible leaf nodes with the same checked DOM and IR identity in the current static/React revision are compared. Missing counterparts remain explicit mismatches. | Frame-SDK contract tests and manifest-bound comparison assertions. |
| One actionable result | Studio deterministically prioritizes missing targets, text differences, then threshold-exceeding position, size, type, and color differences, and shows one plain-language result. | Generated-chrome tests and deliberate browser mutations. |
| Source-bound review | **Review this** selects that exact semantic element through the existing server-resolved Review path and pre-fills a human-readable Static/React correction request. | Chromium negative test and exact `binding_id`/`ir_id` event assertions. |
| Closed correction loop | A detected mismatch can become one bounded proposal, receive explicit human approval, rebuild both targets, and return the comparison to aligned without editing generated output. | End-to-end Studio journey across the approved revision boundary. |
| Responsive continuity | Initial and rebuilt targets stay inside the checked geometry thresholds at 390, 768, and 1440 pixels. Viewport changes and replay checkpoints trigger a fresh observation. | Dedicated state-text journey and three-change product journey. |
| Honest claim | The UI says the result is browser-observed geometry and typography, retains `visual_parity: not_proven`, and does not infer screenshot fidelity or production behavior. | Exact policy/readiness assertions and visible Studio copy. |

The implementation also closes the shared causes exposed by this proof: Static and React now use
the same app-shell markup and sizing contract, React screen roots retain their declared route
identity, and shared button, badge, shell-kicker, and route-button typography has explicit metrics
instead of target-specific browser defaults.

## Milestone 4.6: exact targets, effortless canvas

Status: implemented; acceptance evidence is required on every change.

Studio must preserve the exact checked product viewport without making the reviewer operate a
developer-tool viewport. Mobile comparisons remain side by side when both fit at readable scale.
Tablet and Desktop present one canonical Live surface at the available canvas width while the
paired Static target remains mounted and selectable from Details. The presentation transform never
changes the inner target viewport used for layout, interaction, replay, or Target Coherence.

| Promise | Pass condition | Authoritative evidence |
| --- | --- | --- |
| Whole product first | Selecting Tablet or Desktop shows the complete width of one target with no horizontal canvas overflow. | Real-browser `clientWidth`/`scrollWidth` assertion and 1280-pixel visual audit. |
| Exactness is untouched | The inner targets remain exactly 390×844, 768×1024, and 1440×1000 while Studio fits their outer presentation. | Frame-viewport assertions at every canonical size. |
| Comparison stays available | Mobile remains side by side; larger viewports show Live by default and expose the paired Static target inside Details without reloading either product. | Surface-selection, mounted-frame, and replay browser assertions. |
| Proof survives presentation | Target Coherence remains aligned, a scaled target can still produce an exact matched semantic selection, and replay still applies to both targets. | Browser-observed coherence, comment-selection, and replay journey. |

This is deliberately not a zoom preference or a new fidelity claim. It removes navigation tax
from the default experience while keeping every proof input exact.

## Milestone 4.7: product first, context when needed

Status: implemented; acceptance evidence is required on every change.

The checked interface—not Studio explanation—is the default product. Proof, data, conversation,
requests, and decisions live in one contextual rail. It stays outside the layout until requested,
appears automatically for an exact mismatch, semantic selection, or approval, and leaves through
one explicit Close action or Escape.

| Promise | Pass condition | Authoritative evidence |
| --- | --- | --- |
| Canvas first | At a 1280×720 Studio viewport, Desktop has zero horizontal overflow and fits at no less than 84% while retaining an exact 1440×1000 inner viewport. | Real Chromium width, scroll, scale, and frame measurements. |
| Quiet by default | The rail starts `aria-hidden` and inert; **Details** reports `aria-expanded=false`; redundant three-step cards are absent. | Generated-chrome and accessibility-tree assertions. |
| Context appears at the moment of intent | Selecting an exact semantic element opens the rail at **Ask for one change** and focuses the feedback field; a detected mismatch opens its proof; a pending proposal opens its exact decision. | End-to-end mismatch → request → proposal → approval journey. |
| Exit restores the product | Close or Escape removes the rail from focus order, Escape restores focus to **Details**, and returning to Preview clears stale selection context. | Keyboard and state-reset browser assertions. |
| One product, two checked targets | Live is the default larger-viewport surface; Static is available only inside Details. The inactive target is `aria-hidden`, inert, and removed from sequential focus while remaining mounted for coherence and replay. Mobile keeps both targets visible. | Surface-control, accessibility-state, exact-frame, coherence, and replay assertions. |

The rail is non-modal: it does not trap focus or resize the product. It is contextual product
chrome, not a second application beside the application under review.

## Milestone 4.8: the agent handoff is visible

Status: implemented; acceptance evidence is required on every change.

An exact semantic comment is only a product interaction when the reviewer can tell whether an
agent is listening, whether the request is merely saved, and whether it has been delivered. Studio
must derive that truth from the authenticated Review lease and durable queue. It must never infer
agent presence from an open browser, an optimistic counter, or elapsed animation.

| Promise | Pass condition | Authoritative evidence |
| --- | --- | --- |
| Truthful presence | Studio says **Agent ready** only while one authenticated long poll owns the Review delivery lease. With no lease and no delivered batch it says **Agent not connected**. | Server projection tests using a controlled clock and a real concurrent poll. |
| Durable handoff | A comment accepted without an agent remains queued and its Conversation entry says **Waiting for agent**. Starting the agent later delivers that exact event without resubmission. | Browser submission plus delayed authenticated poll and exact event assertion. |
| Visible work | Once a batch is delivered but not acknowledged, Studio says **Agent working** even when the delivering request has returned. | At-least-once batch test and browser session projection. |
| Server-owned queue | The visible request count always comes from the durable session cursor, falls to zero only after exact batch acknowledgement, and survives page reload. | Queue recovery, reload, and acknowledgement assertions. |
| Calm first-use language | Presence and request state are visible beside the product; proof details and protocol vocabulary stay out of the primary message. The status is available to assistive technology without stealing focus. | Generated-chrome accessibility assertions and current-run visual review. |
| No weakened boundary | Presence reveals no agent capability, batch id, token, filesystem path, or source content; one delivery lease and existing acknowledgement semantics remain unchanged. | Projection allowlist and Review security regression suite. |

This milestone deliberately does not start or impersonate an agent. It makes the existing local
human-agent contract legible so the reviewer never has to guess whether a request is waiting,
being worked, or acknowledged.

## Milestone 4.9: brief to checked product in one room

Status: implemented, locally proved, and promoted through repository CI.

The first ViewSpec experience must not begin with a missing-file error or a three-command protocol.
In an empty workspace, the coding agent preserves the person's brief and optional local reference,
opens Studio once, authors only the task-bound candidate, and leaves the person in the same browser
tab while ViewSpec moves from **Waiting for agent** to **Checking candidate** to the existing checked
Preview → Comment → Approve product. ViewSpec remains the deterministic local contract and proof
layer; it does not add a hidden model client or pretend that reading a task proves agent presence.

| Promise | Pass condition | Authoritative evidence |
| --- | --- | --- |
| One opening command | `viewspec studio --brief-file BRIEF [--reference IMAGE] [--kind app\|view]` prepares the exact existing creation contract and opens its creation room. A canonical source keeps the current Studio path; creation arguments and an existing source fail closed. | CLI routing, conflict, and source-discovery tests. |
| Exact, local handoff | The normalized brief, optional bounded reference identity, candidate path, acceptance contract, and task id are the same deterministic values produced by `studio-create`; the browser reveals no absolute path, capability, schema, source, or candidate bytes. No network request occurs. | Task-equivalence, browser-projection allowlist, loopback, CSP, and network-capture assertions. |
| Truthful visible states | Before a candidate exists the room says **Waiting for agent**. A stable candidate produces **Checking candidate**. A failed proof says **Candidate needs one fix** with the exact bounded error and recovery action; changing that candidate retries without publishing canonical source. | State-machine unit tests and a real Chromium failure → retry journey. |
| Checked before handoff | Only the existing task-bound acceptance path may publish canonical source. The room records candidate validation and artifact check as passed before it exposes a handoff; unchanged starters, task/reference drift, malformed candidates, and failed proof publish nothing. | Existing creation negatives plus room acceptance and tamper tests. |
| Same-tab first value | A healthy candidate starts the normal checked Studio configuration and replaces the creation room in the same browser tab—no second command, tab, or generated-output edit. The mechanical candidate-to-checked transition completes in less than 60 seconds and retains task, candidate, proof, source, build, and measured timing evidence. | Isolated real-Chromium journey and retained transition receipt. |
| Resumable, not theatrical | Reloading the room or rerunning the same invocation recovers the exact task and latest durable state. An externally accepted matching source also hands off safely; a conflicting source fails closed. | Reload, daemon restart, duplicate invocation, external-acceptance, and conflict tests. |
| Existing product remains whole | Once checked, Preview, Comment, Approve, agent presence, static/React comparison, replay/resource evidence, and responsive target coherence behave exactly as before. | Existing Studio browser suites plus one creation-to-comment regression journey. |

This milestone removes ceremony, not safeguards. `studio-create` and `studio-accept` remain explicit
diagnostic interfaces, while managed-agent instructions prefer the single-room journey. The browser
never authors semantic source and a candidate's existence is not described as proof that an agent is
connected.

## Milestone 4.10: the request never disappears

Status: implemented and locally proved; repository CI promotion is pending.

Studio promises one continuous loop, so the human request must remain visible from submission
through acknowledgement. The Conversation rail is a browser-safe projection of the durable Review
journal: it pairs the last four human turns with their replies, reports each request's current
delivery state, survives reload, and exposes none of the machinery that makes delivery exact.

| Promise | Pass condition | Authoritative evidence |
| --- | --- | --- |
| Immediate continuity | The exact human request appears in Conversation as soon as submission succeeds and remains after the input clears. | Real-browser submission assertion before any agent poll. |
| One coherent delivery truth | The message moves from **Waiting for agent** to **Agent working** to **Acknowledged**, while the header independently reports only authenticated agent presence. No surface simultaneously makes contradictory lifecycle claims. | Browser journey across queued, delivered, and acknowledged states. |
| Paired history | An acknowledgement reply appears directly after its human request; reload reconstructs the same bounded order from durable session state. | Server restart/reload projection tests and real-browser history assertion. |
| Browser-safe boundary | Each projected message contains only role, body, and status. Tokens, capabilities, batch and event identities, source hashes, paths, targets, and protocol objects remain absent. | Projection allowlist and serialized negative assertions. |
| Calm and accessible | Conversation is an ordered list with visible text states and live-region updates. Long request text wraps without horizontal product or rail overflow at 390, 768, and 1440 pixels. | Generated-chrome assertions plus current-run desktop and mobile visual review. |

This milestone changes visibility, not delivery semantics. The durable journal, authenticated lease,
at-least-once batch, exact acknowledgement, proposal, and approval contracts remain unchanged.
The next unmet promise after this continuity repair is one-click private sharing and bounded hosted
execution.

### Current product checkpoint

The real first-use audit now proves the intended moment at a 1280-pixel Studio viewport: the
comparison opens at the 390-pixel product width with both targets visible from the canvas origin;
React mounts on the first declared route; the ready state waits for both semantic screens; mobile
navigation preserves complete words in the shared Static/React presentation plan; one click
highlights the same semantic field in both products; and replay can reload the same immutable
revision and report its proved outcome without a second, expired handshake. The first visible-state
gap is now closed through bounded `state_text`: `reviewed_count` drives the same exact semantic
binding in Static and React, two replayed actions visibly produce `Review count: 2`, and malformed,
ambiguous, non-scalar, or cross-screen projections fail before build. The contract intentionally
stops at one literal `{value}` placeholder—no renderer heuristics, HTML, expressions, paths,
localization, or formatter functions.

## Milestone 5: one-click private review

Status: local preparation, provider-independent service core, framework-neutral HTTPS/ASGI
contract, replay-safe API→review ingress, deterministic remote rebuild proof, local
Chromium/Firefox/WebKit journey, and the fail-closed Studio Share release gate implemented; a
separate-app production topology is specified, with API bridge wiring, worker isolation,
signed-release publication, deployment, and canary approval pending.

A person can turn the exact checked Studio revision in front of them into a private review link.
The reviewer sees the same immutable product, routes, viewports, state/replay evidence, resource
fixtures, and semantic targets without installing ViewSpec. They may comment, but they may not
change source, run arbitrary code, or approve on the owner's behalf.

ViewSpec must not expose a Share control until the service behind it satisfies every contract below.
The existing hosted compile and verification APIs are useful execution substrate, but they do not
currently provide review-session creation, capability exchange, comments, revocation, expiry, or
owner approval.

That absence is now enforced by product code rather than release convention. Studio stays
network-free by default. An explicit `--share` invocation requires the checked comparison flow,
fetches the canonical receipt key and short-lived release, and exposes the single Share action only
after the Ed25519 signature, production origins, deployment/report/run hashes, and all nine canary
checks validate. The first click prepares and displays the exact disclosure without uploading.
Only a separate checked confirmation and bounded expiry can create the link. The paid API key stays
inside the daemon; browser code receives neither credential nor release receipt.

The local SDK now implements the safe first half as
`viewspec studio-share-prepare viewspec.app.json`. It resumes and revalidates the exact checked
static/React revision, captures a bounded content-addressed payload, writes the complete human
disclosure and machine inventory, emits one deterministic `.vsreview` transport archive, and then
stops. The archive uses canonical paths, ordering, timestamps, permissions, and uncompressed bytes;
strict ingress rejects traversal, links, duplicates, compression ambiguity, tampering, and excess
members or bytes before materialization. Preparation performs no upload and creates no link,
capability, or confirmation authority. This proves the upload boundary before transport exists.

The internal `StudioReviewService` now implements the six-operation durable trust core without
opening a socket or creating a public command. It refuses session creation until an injected
bounded verifier proves the exact package and no-network sandbox policy; persists only hashes of
capabilities and browser sessions; revalidates routes, screens, semantic targets, replay evidence,
and server-derived resource evidence; separates reviewer comments from owner approval; signs
receipts; and implements shortening, rotation, revocation, expiry, deletion, audit, and
idempotency. It also separates stable capability authority from rotatable receipt keys, performs
bounded aggregate-only retention, repairs only mechanically provable interrupted storage
transitions on restart, and verifies a restored SQLite/object consistency unit down to package and
receipt hashes. The SQLite/object-store reference implementation proves the domain and recovery
state machines. It is not the encrypted production storage, scheduled operations, or deployed
production sandbox runner required to expose Share.

The internal `StudioReviewHTTPAdapter` now proves the transport contract without choosing a cloud
provider or opening a production socket. It accepts only the deterministic media type after upload
authorization and explicit disclosure, requires one canonical HTTPS origin, exchanges a one-time
URL-fragment capability for a scoped `Secure`/`HttpOnly`/`SameSite=Strict` cookie, serves a generic
non-discoverable shell, and exposes only allowlisted checked artifacts. Its hosted presentation
derivation is separately hashed and identifies the exact source artifact hash while applying a
no-network sandbox. A test-only self-signed HTTPS bridge proves the reviewer and owner journey in
separate browser contexts across Chromium, Firefox, and WebKit: fragment removal, responsive
static/React canvases, synchronized navigation, semantic selection, replay, fixture evidence,
acknowledged comment, and exact owner approval. Each engine retains a redacted evidence record
covering elapsed time, revision and receipt identities, request paths, external egress, capability
leaks, console errors, and CSP violations. The latest isolated acceptance run completed every
engine in under three seconds, against a five-minute ceiling, with zero egress, leaks, console
errors, or CSP violations. CI reruns and uploads these records; it
does not create a public command or imply that a durable service has been deployed.

The internal `StudioReviewASGIApp` now supplies the bounded production mount seam without adding a
framework dependency: it intercepts only the private-review routes, streams request bodies within
their exact limits, preserves repeated response headers, delegates existing API routes and
lifespan events, and derives HTTPS identity from the trusted ASGI scope rather than forwarding
headers. Its internal creation ingress authenticates the exact allowlisted headers and archive
hash before adapter dispatch, durably rejects request replay across restarts, strips paid and
internal credentials, and authenticates the exact response back to the originating request nonce.
Production mode closes direct creation. Blocking review persistence and verifier calls run outside
the API event-loop thread. The independent rebuild verifier now recompiles packaged semantic source using an
operator-pinned prebuilt dependency seed, invokes Vite directly without install or lifecycle-hook
fallback, and compares the complete static/React artifact inventory byte-for-byte. It emits rebuild
evidence without claiming network isolation; the hosted worker must bind its own real sandbox
attestation before the service will create a session. Its artifact-free wire contract sends only
hash-bound semantic source, optional `DESIGN.md`, and the expected inventory to the fixed isolated
SDK worker; uploaded artifact bytes are never executed or needed outside trusted ingress. The exact mount, worker, persistence,
operations, and canary gate are defined in
[Private Studio Review Deployment Contract](studio-review-deployment.md).
The deployment topology deliberately separates the compiler API, public review service, and
private rebuild worker. Fly app secrets are injected into every Machine in an app, and the hosted
compiler and public SDK carry different protobuf descriptor sets. Separate apps therefore keep
billing, receipt, Stripe, review-signing, persistence, and rebuild authority out of the wrong trust
zones while avoiding an invalid shared Python runtime.

The current production-ingress goal is precise: one paid, disclosure-accepted API request may
create exactly one private review session through a narrow service boundary, while alteration,
replay, direct bypass, credential propagation, and response substitution all fail closed. It is
proved when all of these criteria hold:

| Production ingress criterion | Pass condition | Authoritative evidence |
| --- | --- | --- |
| Exact request binding | Method, internal path, protocol, direction, the four creation headers, archive SHA-256, timestamp, and nonce are authenticated together. | Request round-trip plus body, header, method, path, secret, signature, and staleness negatives. |
| Pre-storage rejection | A bad signature, altered body/header, stale timestamp, or reused nonce reaches neither the HTTP adapter nor review storage. | Adapter-dispatch absence assertions and durable nonce replay test across authenticator restart. |
| No direct bypass | Production composition does not accept session creation at public `/v1/reviews`. | Direct-create 404 assertion with `allow_direct_create=False`. |
| Least-authority forwarding | The adapter receives only the four signed creation headers and one server-generated authentication marker; no paid key or internal authentication header survives. | Exact forwarded-header equality and response/session leakage assertions. |
| Bound response identity | Status, internal path, content type, body SHA-256, timestamp, response nonce, and originating request nonce are authenticated before API return. | Response round-trip plus body, request-nonce, signature, and replay negatives. |
| Whole journey remains intact | The signed ingress still creates the immutable revision, exchanges separate capabilities, records a reviewer comment, and permits only exact owner approval. | Production-like ASGI integration test over the real service, adapter, package, and rebuild verifier. |

| Promise | Pass condition | Authoritative evidence |
| --- | --- | --- |
| Informed upload | Before the first upload, Studio shows one explicit confirmation naming the exact source, optional `DESIGN.md`, optional reference image, declared fixture values, checked static/React artifacts and manifests, and future review comments that will leave the machine. Absolute local paths, environment values, secrets, unrelated files, and production data are excluded. | Request-body allowlist tests, disclosure snapshot, and network-capture assertion. |
| Canary-gated appearance | Share is absent by default and whenever the signed release is missing, invalid, expired, incomplete, or bound to another origin or deployment. The explicit opt-in and API credential alone cannot expose it. | Signed-release negatives, default-chrome absence assertion, and attested-chrome contract test. |
| Private by default | A session is unlisted, sends `noindex`/`noarchive`, has an expiry, and cannot be opened without a high-entropy reviewer capability. No public mode exists in this milestone. | Anonymous/invalid-capability denial tests, response-header assertions, and search metadata test. |
| Authority is separated | Owner and reviewer capabilities are distinct. A capability arrives in the URL fragment, is exchanged once for a `Secure`, `HttpOnly`, `SameSite=Strict` cookie, and is immediately removed from browser history. Capabilities never appear in server logs, analytics, referrers, or artifact URLs. | Browser network/log inspection, cookie assertions, rotation test, and leak canary. |
| Exact immutable revision | The service accepts only a locally checked Studio comparison revision and records its source hash, artifact hashes, manifest hashes, routes, semantic identity hash, inspection hash, and compiler/version lock. The hosted result must match those identities before a link is returned. | Upload-integrity tests, signed receipt verification, and local-versus-hosted hash assertions. |
| Bounded execution | Hosted verification runs in a no-network sandbox with pinned dependencies and explicit CPU, memory, wall-time, file-count, and byte limits. It cannot execute lifecycle hooks or arbitrary project commands. | Adversarial fixture suite, egress denial test, resource-limit tests, and retained execution receipt. |
| Same review semantics | Viewport and route selection, semantic selection, declared replay checkpoints, fixture-resource inspection, and source-bound comments behave the same as local Studio. Unsupported evidence is visibly unavailable, never inferred. | Shared contract fixtures plus Chromium, Firefox, and WebKit end-to-end journeys. |
| Comments are evidence, not edits | Reviewers submit bounded plain-text comments attached to the exact revision, viewport, route, target, semantic identity, and allowlisted replay/resource context. The server revalidates every identity; browser-supplied proof is never trusted. | Forgery, stale-revision, duplicate-identity, payload-limit, and cross-session isolation tests. |
| Approval remains human and exact | Reviewer comments cannot approve. Only the owner may approve the exact current revision after seeing any agent proposal and semantic before/after. A model response, successful build, or reviewer action never implies approval. | Role matrix, stale-approval rejection, proposal/approval separation, and audit-event assertions. |
| Lifecycle is reversible | The owner can set or shorten expiry, rotate the reviewer link, revoke access immediately, and delete the session and its artifacts. Expired, revoked, or deleted links fail closed without revealing session metadata. | Expiry-boundary, rotation, revocation, deletion, and post-delete object-store tests. |
| Calm, observable failure | Upload, verification, comment delivery, and approval each have durable idempotency keys and auditable status. Partial uploads never create a usable link; retry never duplicates a comment or approval. | Fault-injection tests, idempotency tests, metrics/log schema assertions, and recovery drill. |
| Five-minute outcome | From a checked local revision, a new owner creates a link, a second browser comments on a semantic target, and the owner receives that exact comment in under five minutes. | Timed, isolated, multi-browser end-to-end test with retained request, receipt, and event evidence. |

### Minimal service boundary

The first hosted review service needs only six bounded operations:

1. Create a session from one allowlisted, hash-bound Studio revision after disclosure acceptance.
2. Exchange a one-time owner or reviewer fragment capability for a scoped browser session.
3. Read the immutable revision and its allowlisted inspection projection.
4. Append and acknowledge a source-bound reviewer comment.
5. Record an owner-only exact-revision approval.
6. Rotate, revoke, expire, or delete the session.

It does not need presence, arbitrary uploads, mutable generated code, team workspaces, CRDTs,
deployment, production-data connectors, or public galleries. Those wait until the private review
loop is proven excellent.

### Authority boundary

The local SDK now defines and verifies the upload envelope, deterministic source rebuild, ASGI
mount, receipts, capability scopes, client behavior, and the cryptographic release gate. It cannot
honestly ship this milestone without mounting them inside the authorized persistent HTTPS service
and proving its actual network isolation, encrypted storage, signing keys, logs, retention jobs,
recovery, and deployment configuration. Until that backend is in scope, passes the production
canary, and publishes the short-lived signed release, Studio omits Share even when the user passes
`--share`; it never substitutes a loopback URL or nonfunctional control.

## Recommended delivery order

1. ViewSpec Studio: Preview, Comment, Approve.
2. Agent-assisted source creation from a brief or reference image. **Implemented locally.**
3. Synchronized responsive and static/React comparison. **Implemented locally.**
4. State replay and resource inspection inside the same canvas. **Implemented locally.**
5. One-click private sharing and bounded hosted execution. **Local product, service, isolation,
   recovery, operator boundaries, fail-closed canary runner/verifier/collector, and signed-release
   client gate implemented locally; production workflow authorization, readiness signing endpoint,
   production run, and public Share remain intentionally pending.**
6. Deployment only after the created product, correction loop, and proof layer are excellent.

Work that does not make the next unmet item observably better waits.
