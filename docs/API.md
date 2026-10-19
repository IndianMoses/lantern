# API and integration guide

Interactive OpenAPI documentation is served at `/api/docs` (Swagger UI) and `/api/redoc` while the backend is running; the schema is at `/api/openapi.json`.

## Conventions
* JSON over HTTPS; cookie session set by `POST /api/auth/login` (or `POST /api/auth/mfa/verify` when the account has MFA); every non GET request needs the header `X-CSRF-Token` with the value returned by `/api/auth/me`.
* Language: send `X-Lang: en` or `X-Lang: zh` on every request; the server localises every label it generates for that request (the signed in user's stored locale is the fallback).
* Errors are `{ "detail": "..." }` with 400 (validation or state), 401 (not signed in), 403 (wrong role or missing CSRF), 404 (not found or not yours), 409 (conflict), 429 (rate limited).
* All timestamps are ISO 8601 UTC. Money is `{amount, currency}` on the record it belongs to.
* Demo data is marked `is_demo_data` on policies, claims and documents.

## Customer endpoints

| Method and path | Purpose |
|---|---|
| `POST /api/auth/register` · `POST /api/auth/login` · `POST /api/auth/logout` · `GET /api/auth/me` · `PATCH /api/auth/me` | Accounts and sessions. Login returns `{mfa_required: true, challenge_token}` for MFA accounts (423 when the account is locked) |
| `POST /api/auth/mfa/verify` (`{challenge_token, code}`) · `POST /api/auth/mfa/setup` · `POST /api/auth/mfa/enable` (`{code}`) · `POST /api/auth/mfa/disable` (`{code}`) | TOTP multi factor authentication (any role) |
| `GET /api/auth/sessions` · `POST /api/auth/sessions/revoke` (`{all_others: true}` or `{session_id}`) · `POST /api/auth/change-password` | Session management; a password change revokes every other session |
| `POST /api/auth/reset/request` (`{email}`) · `POST /api/auth/reset/confirm` (`{token, new_password}`) | Password reset; the request always answers the same way |
| `GET /api/consents` · `POST /api/consents` (`{purpose, granted}`) | Consent purposes (locked ones cannot be withdrawn through this endpoint) |
| `GET /api/shares` · `POST /api/shares` (`{grantee_email, record_type: policy|claim, record_id, permissions?}`) · `DELETE /api/shares/{id}` | Explicit per record sharing with household members and advisers |
| `POST /api/feedback` (`{message_id, rating: 1|-1}`) | Rate an assistant answer (own messages only) |
| `GET/PUT /api/profile` | Onboarding profile (assets, housing, activities, household, preferences, consents) |
| `GET /api/overview` | Dashboard: metrics, portfolio cards, action centre, insights, recent claims |
| `GET /api/notifications` · `POST /api/notifications/{id}/read` · `POST /api/notifications/read-all` | In app notifications |
| `GET /api/policies` · `GET /api/policies/{id}` · `PATCH /api/policies/{id}` · `DELETE /api/policies/{id}` | Portfolio, clause browser, corrections, insurer linking (`insurer_org_id`), confirmation of uploaded wordings |
| `POST /api/policies/upload` (multipart) | Upload a wording → extraction, segmentation, injection scan, pending policy |
| `PATCH /api/policies/{id}/clauses/{clause_id}` | Correct an extracted clause |
| `GET /api/policies/insurers` · `GET /api/policies/insurance-types` | Reference data |
| `POST /api/gaps/analyze` (`{sync?: true}`) · `GET /api/gaps/jobs/{job_id}` · `GET /api/gaps/latest` · `GET /api/gaps/{analysis_id}` · `GET /api/gaps/history` | Coverage analysis: start a job (background by default, poll the job for the nine stage status), read a report with findings, verification, calculations, prioritisation and the graph |
| `GET /api/gaps/{analysis_id}/graph/{node_key}` · `GET /api/gaps/facts?policy_id=` | Graph node detail with provenance; typed policy facts with their status and clause |
| `POST /api/gaps/findings/{finding_id}/review` · `GET /api/gaps/reviews/open` | Ask for a human review of a finding; list open requests |
| `GET /api/claims` · `POST /api/claims` · `GET /api/claims/{id}` · `PATCH /api/claims/{id}` · `POST /api/claims/{id}/refresh` | Claims and drafts; the detail carries `checks` (integrity results for the customer audience) |
| `GET /api/claims/incident-types?insurance_type=` · `GET /api/claims/questions?insurance_type=&incident_type=` · `POST /api/claims/rank-policies` | Wizard support |
| `POST /api/claims/{id}/submit` | Prepare the submission approval (returns the approval card with its token) |
| `GET /api/claims/{id}/pack` · `POST /api/claims/{id}/pack/pdf` | Claim pack preview (JSON) and PDF for assisted submission |
| `POST /api/claims/{id}/mark-sent` | Assisted mode: record that the customer sent the pack through the insurer's own channel |
| `POST /api/claims/{id}/communications` | Create a draft message from a template kind |
| `POST /api/claims/{id}/information-requests/{rid}/respond` | Prepare the approval to answer an insurer request |
| `GET /api/documents` · `POST /api/documents/upload` · `GET /api/documents/{id}` · `GET /api/documents/{id}/download` · `DELETE /api/documents/{id}` | Evidence and correspondence |
| `GET /api/communications` · `PATCH /api/communications/{id}` · `POST /api/communications/{id}/send` · `POST /api/communications/{id}/read` · `DELETE /api/communications/{id}` | Inbox, drafts, approval to send |
| `GET /api/approvals` · `GET /api/approvals/{id}` · `POST /api/approvals/{id}/approve` · `POST /api/approvals/{id}/reject` (body `{token}`) | Human in the loop |
| `GET /api/export` · `POST /api/account/close` | Data portability and closure |

## Assistant endpoints

| Method and path | Purpose |
|---|---|
| `GET /api/assistant/conversations?surface=customer|insurer_copilot` · `POST /api/assistant/conversations` (`{context:{claim_id?, policy_id?}, surface}`) | Conversations |
| `GET /api/assistant/conversations/{id}` | Messages with structured blocks |
| `POST /api/assistant/conversations/{id}/messages` (`{text, attachment_ids?, language?, context?}`) | One agent turn; returns the assistant message, the run (intent, provider, tool executions with tiers, grounding) and any approvals created |
| `GET /api/assistant/runs/{id}` · `GET /api/assistant/runs/{id}/log` | Run transparency and the JSONL operation log |
| `GET /api/assistant/tools` · `GET /api/assistant/suggestions` | Tool catalogue for the current role and suggested prompts |

### Message blocks
The assistant message carries `blocks` the interface renders: `citations`, `checklist`, `completeness`, `coverage_view`, `gap_findings`, `claim_card`, `communication_draft`, `approval`, `insurer_request_draft`, `warning` (`unverified_citations`, `injection_flags`).

## Insurer endpoints (staff of one organisation)

| Method and path | Purpose |
|---|---|
| `GET /api/insurer/dashboard?date_from&date_to&insurance_type&handler_id` | Metrics with definitions, separated amounts, chart series |
| `GET /api/insurer/claims?status&insurance_type&handler&q&sort&page&page_size` | Queue (pre submission claims never appear) |
| `GET /api/insurer/claims/{id}` | Case workspace |
| `POST /api/insurer/claims/{id}/assign` (`{handler_id: "me" | id | null}`) · `POST .../status` · `POST .../request-information` · `POST .../decision` · `POST .../notes` | Tier 3 actions, human only |
| `GET /api/insurer/customers` · `GET /api/insurer/customers/{id}` · `GET /api/insurer/policies` · `GET /api/insurer/team` | Directory |
| `GET /api/insurer/audit` (admin) | Scoped audit events and hash chain verification |

Privileged staff endpoints return 403 with `mfa_required` when `MFA_REQUIRED_FOR_PRIVILEGED=true` and the session did not pass MFA.

## Bank endpoints (bank administrators)

| Method and path | Purpose |
|---|---|
| `GET /api/bank/overview` | Aggregate metrics with definitions over the bank's consenting customers; cells under the k anonymity threshold are `null` |
| `GET /api/bank/integration` | The eight interfaces with mode (`mock` until a real adapter is registered), call counts, consent aggregates, data boundary |
| `GET /api/bank/audit?limit=` | The bank staff's own access trail plus integration events, with chain verification |
| `GET /api/bank/customers/{id}` | Always 403: bank staff have no access to identifiable customer records |

## Development endpoints (not mounted in production)
`GET /api/dev/demo-accounts`, `POST /api/dev/reset-demo`, `GET /api/dev/audit-chain`, `GET /api/health`.

## Integration boundaries

### Insurer adapter
`backend/app/services/adapters/base.py` defines the interface:

```python
class InsurerAdapter(Protocol):
    name: str
    def submit_claim(self, claim_pack: dict, *, idempotency_key: str) -> SubmissionResult: ...
    def get_claim_status(self, insurer_reference: str) -> StatusResult: ...
    def verify_webhook(self, headers: dict, body: bytes) -> bool: ...
```

`MockInsurerAdapter` is idempotent on the key, returns `is_mock=True`, rejects packs missing required fields and raises `AdapterError` when the description contains `SIMULATE_ADAPTER_FAILURE`. To add a real provider: implement the protocol, add authentication and timeouts, keep the idempotency key, and select it per organisation in `get_adapter()` using `Organization.integration_mode`. Record `ProviderWebhookEvents` from signed webhooks to move claims to `acknowledged` or decided statuses with `status_source="adapter"`.

### Model provider (assistant)
`backend/app/agent/providers/base.py`: `complete(system, messages, tools, max_tokens, context) -> ProviderTurn`. Messages use Anthropic style content blocks. Add a provider by implementing this method; tools and tiers are unaffected. The runner only uses a hosted provider when the customer's `external_model_processing` consent is granted.

### Analysis roles (coverage pipeline)
`backend/app/agent/analysts.py`: three contracts, `analyze(package) -> AnalystOutput` (roles A and B), `challenge(package, a_output, own_output) -> ChallengeOutput` (B) and `adjudicate(package, a, b, challenge, verification) -> JudgeOutput` (C). `RuleBasedAnalyst` implements all three offline; `OpenAICompatibleAnalyst` calls any `/v1/chat/completions` endpoint with JSON mode, validates the response against the Pydantic schema, retries once with the validation error, and raises `ProviderError` so the pipeline can fall back and label the job. Models and endpoints come from `MODEL_{A,B,C}_URL` and `MODEL_{A,B,C}_NAME` (`docs/MODELS.md`).

### Bank adapters
`backend/app/services/bank.py`: eight `Protocol`s (`IdentityProvider`, `CustomerProfileSource`, `ConsentManager`, `DocumentSource`, `CommunicationsChannel`, `AuditSink`, `AnalyticsSink`, `EmbeddingContext`). Register a bank's implementation in `adapters_for(org)`; until then `MockAdapters` answers with `mock=True`. See `docs/BANK_INTEGRATION.md` for the data boundary of each interface.

### Document processing
`services/extraction.py`: `extract_text(mime, bytes)` is the place to add OCR; `segment_clauses(text)` and `classify_kind()` can be replaced by a model based extractor as long as they return the same clause dictionaries.

### Claim pack
`services/claim_pack.py` builds the JSON pack sent to adapters and renders the PDF for assisted submission. The pack contains the customer's statement, document hashes, checklist status and a clearly labelled customer side coverage reading; it never contains an admission or a coverage determination.
