# Security self assessment

Scope: the Lantern insurance assistant as submitted (backend, agent, coverage pipeline, integrity ensemble, bank layer, frontend, demo data). This is a self assessment for the competition's security and compliance criterion; it is not a penetration test or a legal opinion. Every control named here is exercised by a test in `backend/tests/` unless marked *planned*.

## 0. Roles and permission tiers (people)

Seven tiers, enforced in `deps.py` and `models.Roles`. A higher tier never implies a lower tier's data: an insurer administrator does not see customer drafts and a bank administrator sees no customer record at all.

| Tier | Role | What it can reach | Notes |
|---|---|---|---|
| 1 | `customer` | own profile, policies, documents, claims, analyses, consents, shares | the only role that can grant a share or approve a submission |
| 2 | `household_member` | records explicitly shared with them, view only unless `upload` was granted | no portfolio of their own on the customer surface (`test_record_sharing_is_explicit_and_revocable`) |
| 3 | `adviser` | records explicitly shared with them | same mechanism as tier 2; intended for a broker or family adviser |
| 4 | `insurer_handler` | submitted claims of their organisation, queue, workspace, information requests | cannot record decisions |
| 5 | `insurer_reviewer` | tier 4 plus escalated claims (`human_review_required`) and decisions | |
| 6 | `insurer_admin`, `bank_admin` | insurer: team, audit; bank: aggregates, integration health, bank audit | **privileged**: MFA can be required (`MFA_REQUIRED_FOR_PRIVILEGED`) |
| 7 | `platform_admin` | operations of the platform itself | privileged; no demo account by design |

Bank staff have no route to identifiable customer data (`/api/bank/customers/{id}` is 403 by design, and the bank router imports no customer serialisers).

## 1. Permission tiers

The central safety mechanism is that the model has no privilege of its own. It can only call tools from a registry, and the registry enforces tiers in code (`backend/app/agent/tools.py`, `execute()`).

| Tier | Meaning | Tools | Enforcement |
|---|---|---|---|
| 0 | Read the signed in user's own data | `get_customer_profile`, `get_insurance_portfolio`, `get_policy_details`, `search_policy_wording`, `list_claims`, `get_claim_details`, `get_claim_timeline`, `get_claim_decision`; insurer: `insurer_get_claim`, `insurer_search_policy_wording` | Every handler scopes queries to `ctx.user` (customers) or `ctx.user.org_id` (staff). The model cannot pass a user id. Unknown ids return "not found", never another user's record |
| 1 | Prepare something that stays inside the platform | `analyze_coverage_gaps`, `assess_incident_coverage`, `create_claim_draft`, `update_claim_draft`, `generate_claim_checklist`, `check_claim_completeness`, `create_communication_draft`, `create_reminder`; insurer: `insurer_draft_information_request` | Writes drafts and analyses only; nothing leaves the platform; all recorded with the run id |
| 2 | Consequential: leaves the platform or commits the customer | `submit_claim`, `send_communication`, `respond_to_information_request` | The tool **does not act**. It creates an `ApprovalRequest` and returns `approval_required`. The interface shows the full payload and consequences; the customer approves with a one time token bound to a hash of the payload; a backend service executes exactly once |
| 3 | Reserved for authorised humans | record claim decision, request information from a customer, assign, settle, close | **No tool exists.** Insurer staff act through dedicated endpoints with role checks; the AI cannot reach them even if instructed to (see the stubbed model test `test_model_cannot_call_human_only_or_unknown_tools`) |

Tools are also scoped by *surface*: the customer assistant cannot see insurer tools and the insurer copilot cannot see customer tools (`specs_for(user, surface)`), and a role mismatch is logged as `tool.denied` in the audit log.

### Approval design
* Token: 32 random bytes; only its SHA-256 is stored; compared with `hmac.compare_digest`.
* Binding: the approval stores `payload_hash` over incident facts, document ids and hashes, recipient and cover letter. Before execution the hash is recomputed from the live claim; any change invalidates the card ("the claim changed after this approval was prepared").
* Lifetime: 15 minutes; one use; a replayed approval returns 400; preparing a new card expires older pending cards for the same claim.
* Idempotency: adapter calls carry `claim:<claim_id>:approval:<approval_id>`; the mock adapter (and a real one should) replays the prior result for a repeated key, so a retried request cannot create a second claim.
* Truthfulness: the result stored on the approval is the adapter's actual response (accepted reference, rejection or failure). Failures move the claim to `submission_failed`, notify the customer and are audit logged. The interface labels mock acknowledgements as mock.

## 2. Authentication, sessions, transport
* Passwords hashed with argon2id; login and registration are rate limited per IP; failures are audit logged without revealing which part was wrong.
* Sessions are server side rows keyed by the SHA-256 of a random token; the cookie is `HttpOnly`, `SameSite=Lax`, `Secure` in production; 12 hour expiry; logout deletes the row.
* CSRF: every state changing request must carry `X-CSRF-Token` equal to the session's token (double submit); the SPA reads it from `/api/auth/me`.
* Security headers on every response: `Content-Security-Policy` (`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-ancestors 'none'`), `Cross-Origin-Opener-Policy`, `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy` (camera limited to the app's own origin for evidence capture), `Cache-Control: no-store` on API responses (`test_security_headers_include_csp`).
* Account lockout: after `LOCKOUT_THRESHOLD` (5) failed attempts an account is locked for `LOCKOUT_MINUTES` (15); the response is 423 and does not reveal whether the password was otherwise right (`test_lockout_after_repeated_failures`).
* Password reset: a request always returns the same message whether or not the address exists; the token is random, stored hashed, expires after 30 minutes and is single use; a successful reset revokes every session (`test_password_reset_flow`). Delivery of the token by email is *planned*: in development the token is exposed only through an in process dictionary used by the tests.
* Session management: users can list their sessions (device, IP, last seen, MFA state) and sign out other devices; changing the password revokes every other session (`test_sessions_list_and_revoke`, `test_change_password_revokes_other_sessions`).
* Multi factor authentication: RFC 6238 TOTP implemented with the standard library (no third party dependency), enrolment with an `otpauth://` URI and manual key, confirmation with a live code, a two minute challenge token at login, and optional enforcement for privileged roles; sessions record whether MFA was passed (`test_totp_mfa_enrolment_and_login`, `test_mfa_required_for_privileged_when_configured`). Recovery codes are *planned*.
* Unhandled exceptions return a generic message; details go to server logs only.

## 3. Authorisation and tenant isolation
* Customers: every query filters on `user_id`; cross customer access returns 404 so identifiers cannot be probed (`test_customer_cannot_see_another_customers_records`).
* Insurers: every query filters on `insurer_org_id = staff.org_id` and excludes pre submission statuses, so an insurer never sees a draft a customer has not chosen to send (`test_insurer_tenant_isolation`, `test_pre_submission_claims_are_invisible_to_insurers`).
* Documents: customers download their own; insurer staff download only documents attached to claims in their organisation **and** already shared (submitted) by the customer.
* Roles: customer endpoints reject staff and vice versa; the audit endpoint requires the insurer admin role; bank endpoints require the bank admin role and are limited to aggregates.
* Explicit sharing: a customer can share one policy or claim at a time with a household member, adviser or other customer; the grantee sees that record only, view only unless upload was granted; the owner or the grantee can revoke; every grant and revocation is audit logged. Staff roles cannot be grantees.
* Consent: purposes are stored as append only records; two service purposes are locked (using the customer's data for the three workflows, and sharing a claim pack with the insurer when the customer approves a submission); the optional purposes (aggregate analytics, bank profile import, bank communications, external model processing, extended document retention) default to the least data use except external model processing, which the customer can withdraw at any time. The assistant runner checks `external_model_processing` before every hosted model call (`test_consent_gates_external_model_processing`); the bank's aggregates count only customers who allowed `analytics_aggregate` (`test_analytics_respect_consent`).
* Frontend visibility is never relied on; all checks are server side (`deps.py`).

## 4. Untrusted content and prompt injection
* Uploads: validated by magic bytes against the declared type and extension; size limited; file names sanitised; stored outside the web root under a random name; served only through authenticated download endpoints.
* Text extracted from uploads is scanned (`scan_for_injection`) for instruction override, persona override, action coercion, concealment, tool references, transcript spoofing and hidden characters, in English and Chinese. Flags are stored on the document, shown to the customer and to the insurer, and counted in the operation log.
* When the model sees document text it is inside an `<untrusted_document>` block with an explicit statement that it is data, and the system prompt says to ignore instructions inside it. The interface shows a warning block whenever a flagged document was attached to a turn.
* Structural defence, independent of the model: document text can never trigger a Tier 2 action because Tier 2 tools only create approvals, and approvals require a token that only the interface receives (`test_prompt_injection_in_uploaded_document_is_neutralised`).
* Tool results fed back to the model are shrunk structurally rather than cut mid JSON, so a truncated result cannot be misread.

## 5. Grounding and hallucination controls
* Citations must match clauses retrieved in the current run; unknown citations are rewritten as "unverified" and listed in a warning block; a coverage statement with no verifiable citation gets a caution banner (`agent/verifier.py`). This is enforced in code after the model answers.
* Silence detection: if the retrieved clauses do not address the question the assistant says so and offers to draft a question to the insurer.
* Status truthfulness: claim status is shown as *last known*, with source and timestamp; the assistant is instructed never to say a claim was submitted or a message sent unless the tool result says so, and the deterministic provider cannot.
* Drafts: templates insert only facts present on records; unknown facts become visible `[placeholders]`.
* Decisions: the AI cannot decide, cannot allege fraud and cannot quantify settlements; the insurer copilot labels every fact with its source.
* Coverage pipeline: every finding carries clause evidence that is re-checked in code (verification layers A to F); figures in a finding must match an extracted fact or a calculator trace; the judge can return `unresolved`, which escalates to a human instead of forcing certainty; a model that fails or returns invalid JSON falls back to the rule based role and the result is labelled `mixed`, never shown as a model output (`test_model_failure_falls_back_and_is_labelled`). Calculations are labelled illustrative and come with a trace.
* Integrity checks: seven checkers with a fixed aggregation order; warnings never become failures; only `failed_validation` blocks a submission; the customer facing wording never accuses (`backend/tests/test_integrity.py`).

## 6. Privacy and data minimisation
* Profile collects only what the three workflows need (assets, housing, activities, household). No health, biometric or special category data is requested; incident questions ask for medical documents only where the claim type needs them, and the checklist notes lawful handling.
* Personal data is masked before writing operation logs and tool execution records (`pii.py`: emails, phone numbers, UK postcodes, Chinese ID numbers, IBANs, long digit strings, sort code and account pairs; keys such as `password`, `token`, `card_number` are redacted). Tested with a card number pasted into the chat (`sensitive-info` evaluation case).
* Customers can export everything held about them (`GET /api/export`) and request account closure; documents already shared with an insurer are retained under the insurer's claim retention rather than deleted silently, and the response says so.
* Demo data is explicitly fictional and labelled as such in the interface and the API.
* Bank aggregates: counts and rates only, each with its definition; cells with fewer than three consenting customers are suppressed; the response is tested not to contain names, emails, claim references or pet names (`test_bank_admin_sees_aggregates_only`).
* Evidence packages sent to the analysis models contain typed facts, clause ids, capped clause excerpts and stated circumstances; never email addresses or document bodies (`test_challenger_is_independent_of_analyst`).
* Mobile: the service worker caches the static shell only, never `/api` responses, so no private data persists on a shared device beyond the session cookie.

## 7. Audit and operation logs
* Audit log: append only table, each event hashed with the previous hash over a canonical serialisation; `verify_chain` detects edits, deletions or reordering (tested by tampering). Recorded: logins and lockouts, MFA changes, session revocations, password changes and resets, uploads, downloads, drafts, approvals, submissions, insurer actions, decisions, exports, tool denials, agent runs, consent changes, shares, bank analytics and integration views (with the metric names requested).
* Operation logs: one JSONL file per agent run with intent, each tool call (name, tier, masked input, output summary, duration, status, approval id), grounding verification and tokens. Customers can download the log of their own runs; the evidence bundle in `evidence/` is produced by `scripts/generate_evidence.py`.

## 8. Threat model (summary)

| Threat | Mitigation | Residual risk |
|---|---|---|
| Model instructed by a document to submit or send | Tier 2 is approval only; approvals need a UI token; scanner flags; data wrapper | Model might *describe* injected content persuasively; the warning block and human approval are the backstop |
| Fabricated clauses or limits | Code level citation verifier; refusal on silence; deterministic composer quotes only retrieved text | Model prose between citations can still over interpret; the "my reading / insurer decides" framing and caution banner limit harm |
| Cross tenant leakage through the agent | Tools scoped to principal; no user id parameters; insurer tools require org match and submitted status | Shared conversation history is per user only |
| Replay or duplicate submission | One time approvals, payload hash, idempotency keys, state machine | A real provider without idempotency support would need a reconciliation job |
| Session theft / CSRF | HttpOnly cookies, CSRF header, Secure in production, rate limits, session list and revocation, TOTP MFA (optional enforcement for privileged roles) | Recovery codes and WebAuthn not implemented |
| Credential stuffing | Per IP rate limit plus per account lockout; uniform error messages | Lockout state is in the database; a distributed attacker is slowed, not stopped |
| Bank staff reading customer data | No route exists; aggregates only; k anonymity suppression; consent gated counting | Aggregates over very small banks could still be informative; raise `K_ANON` per deployment |
| Coverage model fabricates a limit | Typed facts with `not_found`; verification layer D rejects figures with no source; judge must cite; fallback labelled | Models can still phrase a qualified finding too confidently; the card shows evidence confidence separately |
| Integrity checker wrongly flags a customer | Warnings never block; only validation failures block; human review wording is neutral; reviewers see the inputs | A reviewer could over read a warning; training and the audit trail are the control |
| Malicious uploads | Magic byte checks, size limits, no execution, served with `nosniff` | No antivirus scanning in this environment |
| Denial of service on the assistant | Per user rate limit, step limit per run, tool result size limits | In memory limiter is per process |

## 9. Regulatory notes (not legal advice)
The platform is designed so that it can be operated within an insurance distribution framework but **does not claim compliance**. Before any real launch a qualified assessment is needed of, at least: UK GDPR / DPA 2018 and PIPL roles and lawful bases; FCA rules on insurance distribution, advice versus information and Consumer Duty (the gap analysis is framed as potential issues, not personalised recommendations, and never ranks or sells products); complaints handling; record keeping; automated decision making provisions (no decisions are automated; decisions are recorded by named staff with written reasons); and accessibility. Jurisdiction specific rules are kept out of the workflow engine so they can be configured rather than hard coded.

## 10. Known risks and gaps
1. The rate limiter and MFA challenge tokens are per process; the lockout table is shared. Multi process deployments need Redis (planned). Password reset email delivery and MFA recovery codes are not implemented.
2. No OCR: image only PDFs and photographs are stored but not read; the interface says so.
3. Lexical retrieval can miss paraphrases that share no tag or term; the refusal path is the safe failure, and a re-ranker can be added behind the same interface.
4. The injection scanner is pattern based; novel phrasings may pass unflagged. The structural controls (tiers, approvals) do not depend on it.
5. Clause segmentation of arbitrary uploads is heuristic; extracted clauses are marked low confidence until the customer confirms them, and answers show the confidence.
6. The audit chain is global and verified on demand, not anchored externally.
7. The mock insurer adapter and the eight mock bank adapters stand in for real integrations; webhook signature verification is implemented but no real webhook source exists. No live bank connection exists anywhere in this repository.
8. Secrets come from environment variables; no secret manager integration. No key is ever placed in frontend code.
9. Translation of clauses relies on stored translations for the seeded Chinese wordings; other translations require the Claude API.
10. The open-weight analysis models have not been run end to end on GPU hardware from this repository; the deterministic analysts are what the demo exercises (`docs/MODELS.md`).
11. `MFA_REQUIRED_FOR_PRIVILEGED` defaults to false so the demo accounts work without an authenticator; production must enable it after staff enrol.
