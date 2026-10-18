# API and integration guide

Interactive OpenAPI documentation is served at `/api/docs` (Swagger UI) and `/api/redoc` while the backend is running; the schema is at `/api/openapi.json`.

## Conventions
* JSON over HTTPS; cookie session set by `POST /api/auth/login`; every non GET request needs the header `X-CSRF-Token` with the value returned by `/api/auth/me`.
* Errors are `{ "detail": "..." }` with 400 (validation or state), 401 (not signed in), 403 (wrong role or missing CSRF), 404 (not found or not yours), 409 (conflict), 429 (rate limited).
* All timestamps are ISO 8601 UTC. Money is `{amount, currency}` on the record it belongs to.
* Demo data is marked `is_demo_data` on policies, claims and documents.

## Customer endpoints

| Method and path | Purpose |
|---|---|
| `POST /api/auth/register` · `POST /api/auth/login` · `POST /api/auth/logout` · `GET /api/auth/me` · `PATCH /api/auth/me` | Accounts and sessions |
| `GET/PUT /api/profile` | Onboarding profile (assets, housing, activities, household, preferences, consents) |
| `GET /api/overview` | Dashboard: metrics, portfolio cards, action centre, insights, recent claims |
| `GET /api/notifications` · `POST /api/notifications/{id}/read` · `POST /api/notifications/read-all` | In app notifications |
| `GET /api/policies` · `GET /api/policies/{id}` · `PATCH /api/policies/{id}` · `DELETE /api/policies/{id}` | Portfolio, clause browser, corrections, insurer linking (`insurer_org_id`), confirmation of uploaded wordings |
| `POST /api/policies/upload` (multipart) | Upload a wording → extraction, segmentation, injection scan, pending policy |
| `PATCH /api/policies/{id}/clauses/{clause_id}` | Correct an extracted clause |
| `GET /api/policies/insurers` · `GET /api/policies/insurance-types` | Reference data |
| `POST /api/gaps/analyze` · `GET /api/gaps/latest` | Coverage gap analysis |
| `GET /api/claims` · `POST /api/claims` · `GET /api/claims/{id}` · `PATCH /api/claims/{id}` · `POST /api/claims/{id}/refresh` | Claims and drafts |
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

### Model provider
`backend/app/agent/providers/base.py`: `complete(system, messages, tools, max_tokens, context) -> ProviderTurn`. Messages use Anthropic style content blocks. Add a provider by implementing this method; tools and tiers are unaffected.

### Document processing
`services/extraction.py`: `extract_text(mime, bytes)` is the place to add OCR; `segment_clauses(text)` and `classify_kind()` can be replaced by a model based extractor as long as they return the same clause dictionaries.

### Claim pack
`services/claim_pack.py` builds the JSON pack sent to adapters and renders the PDF for assisted submission. The pack contains the customer's statement, document hashes, checklist status and a clearly labelled customer side coverage reading; it never contains an admission or a coverage determination.
