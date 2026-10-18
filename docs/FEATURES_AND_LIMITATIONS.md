# Implemented features, mock integrations, known limitations, future work

## Implemented

### Customer application
* Account registration and login; bilingual interface (English, 中文) switchable at any time; profile with assets, housing, activities, household, consents; data export; account closure request.
* Overview dashboard: active policies, annualised premium, open claims, renewals within 60 days, action centre (insurer requests, pending approvals, unfinished claims, renewals, unread messages), insights from the latest gap analysis, portfolio cards, recent claims.
* Policies: portfolio, clause browser filtered by kind, translation toggle for clauses with stored translations, timeline, renewal reminders at 60/30/14/7 days, upload of a wording (PDF or text) with extraction, clause segmentation, confidence flags, correction and confirmation, insurer linking.
* Assistant: persistent conversations, suggested prompts, attachments, structured blocks (citations, checklist, completeness, coverage reading, gap findings, claim card, drafts, approval cards, warnings), "why this answer" transparency panel (intent, provider, prompt version, tools with tiers and outcomes, citation verification), downloadable operation log per run.
* Coverage gap analysis across profile and all active policies with evidence and a stored history.
* Claims: incident wizard with dynamic questions per incident type, policy ranking, draft creation, coverage reading, checklist cross referenced to clauses, per item uploads, completeness with conflicts, approval gated submission through an adapter, assisted mode (pack PDF and "I sent it myself"), status tracking with source and timestamp, insurer requests and approval gated responses, message drafts (templates for notification, progress update, missing evidence, clarification, reconsideration, complaint, response), decision view, explanation and review request flow.
* Documents: list, upload, download, delete (unless shared), injection flags shown; messages inbox with drafts, sent and received.

### Insurer workspace
* Operations dashboard with defined metrics, separate amounts, SLA breaches, charts (status, product, weekly received, processing days by product, outcomes by month, workload, document processing) and filters (product, handler, dates).
* Claims queue with search, filters, sorting and pagination; pre submission claims never visible.
* Case workspace: facts by source, policy and clauses, documents with hashes and injection flags, checklist and completeness, coverage reading labelled as the customer side view, information requests and responses, communications, decisions, internal notes, timeline, audit history, allowed transitions.
* Actions: assign, status changes, request information (notifies the customer), record decision (written reason, clause references, amount), internal notes.
* Copilot (Tier ≤ 1): case summary with labelled sources, missing items, draft information request pre filled into the form.
* Customer directory scoped to the organisation; team list; admin audit view with hash chain verification.

### Platform
* Permission tiers enforced in code, approvals with one time tokens and payload hashing, idempotent adapter submissions, hash chained audit log, PII masked operation logs, prompt injection scanner, grounding verifier, rate limiting, CSRF, security headers, tenant isolation, notifications.
* Seed data: 7 fictional insurers, 7 staff accounts, 10 customers, 17 policies (6 English and 2 Chinese wordings as templates, 264 clauses), 11 claims across the lifecycle, documents, messages, decisions, generated PDFs.
* Tests (64), behavioural evaluation suite (22 cases), evidence generator, Docker image and compose file.

## Mock integrations and how to replace them
| Mock | Interface | Replace with |
|---|---|---|
| `MockInsurerAdapter` | `InsurerAdapter` protocol (`submit_claim`, `get_claim_status`, `verify_webhook`) | Provider REST adapter selected per organisation in `get_adapter()`; keep idempotency keys; record webhook events |
| Deterministic model provider | `LLMProvider.complete` | Already shipped alongside the Claude API provider; used offline and as fallback |
| In process rate limiter and session table | `SlidingWindowLimiter`, `UserSession` | Redis backed limiter and session store for multi process deployments |
| Text extraction without OCR | `extract_text` | OCR service for images and scanned PDFs |
| Stored clause translations | `PolicyClause.translations` | Model translation on demand (the API provider can translate inline) |
| Notifications in app only | `Notification` rows | Email or SMS channel behind the same table |

## Known limitations
1. No MFA, password reset or account lockout.
2. Retrieval is lexical; paraphrases without shared terms or tags can be missed (safe failure is a refusal, not a guess).
3. Clause segmentation of arbitrary wordings is heuristic; extracted clauses are low confidence until confirmed.
4. The coverage reading is tag based; it reads "battery theft is excluded inside a theft clause" only through the quoted text, not through negation handling.
5. Streaming responses are not implemented; a turn returns when the run completes (typically under a second offline, a few seconds with the API).
6. Household and broker roles from the wider product vision are not implemented; roles are customer, insurer handler, insurer admin.
7. Policy discovery, quotes, comparison of market products and applications are out of scope for this build (deliberately, to stay within Topic D).
8. The audit chain is verified on demand and not anchored externally.
9. Demo wordings are synthetic and short compared with real policies; real wordings will need the OCR and re-ranking work above.

## Future work (in priority order)
1. Real insurer adapter with webhook driven status updates and reconciliation.
2. OCR and layout aware clause extraction; model assisted clause classification with the same confidence workflow.
3. Embedding re-ranker behind `ClauseIndex.search`, evaluated against the same case suite.
4. MFA for staff, password reset, lockout, Redis backed sessions and limits, secret manager.
5. Renewal comparison across the customer's own policies (already partially in the gap engine) and a regulated advice boundary review before any product comparison feature.
6. Accessibility audit against WCAG 2.2 AA and a formal privacy impact assessment.
