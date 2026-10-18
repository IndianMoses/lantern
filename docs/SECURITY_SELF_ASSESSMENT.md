# Security self assessment

Scope: the Lantern insurance assistant as submitted (backend, agent, frontend, demo data). This is a self assessment for the competition's security and compliance criterion; it is not a penetration test or a legal opinion.

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
* Security headers on every response (`X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`), `Cache-Control: no-store` on API responses.
* Unhandled exceptions return a generic message; details go to server logs only.
* Not implemented: MFA for privileged roles, password reset, account lockout policy (see known risks).

## 3. Authorisation and tenant isolation
* Customers: every query filters on `user_id`; cross customer access returns 404 so identifiers cannot be probed (`test_customer_cannot_see_another_customers_records`).
* Insurers: every query filters on `insurer_org_id = staff.org_id` and excludes pre submission statuses, so an insurer never sees a draft a customer has not chosen to send (`test_insurer_tenant_isolation`, `test_pre_submission_claims_are_invisible_to_insurers`).
* Documents: customers download their own; insurer staff download only documents attached to claims in their organisation **and** already shared (submitted) by the customer.
* Roles: customer endpoints reject staff and vice versa; the audit endpoint requires the insurer admin role.
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

## 6. Privacy and data minimisation
* Profile collects only what the three workflows need (assets, housing, activities, household). No health, biometric or special category data is requested; incident questions ask for medical documents only where the claim type needs them, and the checklist notes lawful handling.
* Personal data is masked before writing operation logs and tool execution records (`pii.py`: emails, phone numbers, UK postcodes, Chinese ID numbers, IBANs, long digit strings, sort code and account pairs; keys such as `password`, `token`, `card_number` are redacted). Tested with a card number pasted into the chat (`sensitive-info` evaluation case).
* Customers can export everything held about them (`GET /api/export`) and request account closure; documents already shared with an insurer are retained under the insurer's claim retention rather than deleted silently, and the response says so.
* Demo data is explicitly fictional and labelled as such in the interface and the API.

## 7. Audit and operation logs
* Audit log: append only table, each event hashed with the previous hash over a canonical serialisation; `verify_chain` detects edits, deletions or reordering (tested by tampering). Recorded: logins, uploads, downloads, drafts, approvals, submissions, insurer actions, decisions, exports, tool denials, agent runs.
* Operation logs: one JSONL file per agent run with intent, each tool call (name, tier, masked input, output summary, duration, status, approval id), grounding verification and tokens. Customers can download the log of their own runs; the evidence bundle in `evidence/` is produced by `scripts/generate_evidence.py`.

## 8. Threat model (summary)

| Threat | Mitigation | Residual risk |
|---|---|---|
| Model instructed by a document to submit or send | Tier 2 is approval only; approvals need a UI token; scanner flags; data wrapper | Model might *describe* injected content persuasively; the warning block and human approval are the backstop |
| Fabricated clauses or limits | Code level citation verifier; refusal on silence; deterministic composer quotes only retrieved text | Model prose between citations can still over interpret; the "my reading / insurer decides" framing and caution banner limit harm |
| Cross tenant leakage through the agent | Tools scoped to principal; no user id parameters; insurer tools require org match and submitted status | Shared conversation history is per user only |
| Replay or duplicate submission | One time approvals, payload hash, idempotency keys, state machine | A real provider without idempotency support would need a reconciliation job |
| Session theft / CSRF | HttpOnly cookies, CSRF header, Secure in production, rate limits | No MFA yet |
| Malicious uploads | Magic byte checks, size limits, no execution, served with `nosniff` | No antivirus scanning in this environment |
| Denial of service on the assistant | Per user rate limit, step limit per run, tool result size limits | In memory limiter is per process |

## 9. Regulatory notes (not legal advice)
The platform is designed so that it can be operated within an insurance distribution framework but **does not claim compliance**. Before any real launch a qualified assessment is needed of, at least: UK GDPR / DPA 2018 and PIPL roles and lawful bases; FCA rules on insurance distribution, advice versus information and Consumer Duty (the gap analysis is framed as potential issues, not personalised recommendations, and never ranks or sells products); complaints handling; record keeping; automated decision making provisions (no decisions are automated; decisions are recorded by named staff with written reasons); and accessibility. Jurisdiction specific rules are kept out of the workflow engine so they can be configured rather than hard coded.

## 10. Known risks and gaps
1. No MFA, password reset or lockout; sessions and rate limiter are single process.
2. No OCR: image only PDFs and photographs are stored but not read; the interface says so.
3. Lexical retrieval can miss paraphrases that share no tag or term; the refusal path is the safe failure, and a re-ranker can be added behind the same interface.
4. The injection scanner is pattern based; novel phrasings may pass unflagged. The structural controls (tiers, approvals) do not depend on it.
5. Clause segmentation of arbitrary uploads is heuristic; extracted clauses are marked low confidence until the customer confirms them, and answers show the confidence.
6. The audit chain is global and verified on demand, not anchored externally.
7. The mock insurer adapter stands in for real integrations; webhook signature verification is implemented but no real webhook source exists.
8. Secrets come from environment variables; no secret manager integration.
9. Translation of clauses relies on stored translations for the seeded Chinese wordings; other translations require the Claude API.
