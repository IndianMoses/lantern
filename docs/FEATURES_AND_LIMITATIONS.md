# Implemented features, mock integrations, known limitations, future work

## Implemented

### Customer application
* Account registration and login; bilingual interface (English, 中文) switchable at any time with one authoritative locale (server payloads follow the `X-Lang` header); profile with assets, housing, activities, household; data export; account closure request.
* Settings: consent controls per purpose (locked service purposes, optional analytics, profile import, bank communications, external model processing, extended retention), explicit per record sharing with household members and advisers (view only by default, revocable), account security (active sessions with sign out of other devices, password change, TOTP enrolment).
* Mobile: installable PWA at 390 px with bottom navigation and a "More" sheet, camera capture for evidence (`capture="environment"`), no horizontal overflow (checked with Playwright on every captured page), service worker that caches the shell only and never API responses.
* Overview dashboard: active policies, annualised premium, open claims, renewals within 60 days, action centre (insurer requests, pending approvals, unfinished claims, renewals, unread messages), insights from the latest gap analysis, portfolio cards, recent claims.
* Policies: portfolio, clause browser filtered by kind, translation toggle for clauses with stored translations, timeline, renewal reminders at 60/30/14/7 days, upload of a wording (PDF or text) with extraction, clause segmentation, confidence flags, correction and confirmation, insurer linking.
* Assistant: persistent conversations, suggested prompts, attachments, structured blocks (citations, checklist, completeness, coverage reading, gap findings, claim card, drafts, approval cards, warnings), "why this answer" transparency panel (intent, provider, prompt version, tools with tiers and outcomes, citation verification), downloadable operation log per run.
* Coverage analysis workspace: nine stage pipeline with live stage status (customer context, extraction, graph, analysts, challenge, judge, verification, calculation, report); typed policy facts with known / unknown / not_found / ambiguous / conflicting states; three role analysis (A analyst, B independent challenger, C judge) with rule based implementations shipped and open-weight model implementations behind OpenAI compatible endpoints; verification layers A to F shown as badges on every finding; deterministic illustrative calculations with step traces; risk priority (critical to investigate / high / medium / low) kept separate from evidence confidence, with the weighting shown; interactive risk graph (pan, zoom, keyboard, layer filters, verified only toggle, focus mode) with a list view under 900 px; human review requests; stored history; pending policies excluded and reported.
* Claims: incident wizard with dynamic questions per incident type, policy ranking, draft creation, coverage reading, checklist cross referenced to clauses, per item uploads (file or camera), completeness with conflicts, integrity checks panel (seven checkers, customer audience wording), approval gated submission through an adapter with the approval bound to the integrity inputs, assisted mode (pack PDF and "I sent it myself"), status tracking with source and timestamp, insurer requests and approval gated responses, message drafts (templates for notification, progress update, missing evidence, clarification, reconsideration, complaint, response), decision view, explanation and review request flow.
* Assistant answer feedback (helpful / not helpful) feeding only an aggregate satisfaction rate.
* Documents: list, upload, download, delete (unless shared), injection flags shown; messages inbox with drafts, sent and received.

### Insurer workspace
* Operations dashboard with defined metrics, separate amounts, SLA breaches, charts (status, product, weekly received, processing days by product, outcomes by month, workload, document processing) and filters (product, handler, dates).
* Claims queue with search, filters, sorting and pagination; pre submission claims never visible.
* Case workspace: facts by source, policy and clauses, documents with hashes and injection flags, checklist and completeness, integrity check results with the insurer audience detail and escalation reasons, coverage reading labelled as the customer side view, information requests and responses, communications, decisions, internal notes, timeline, audit history, allowed transitions. Queue rows carry a check summary.
* Actions: assign, status changes, request information (notifies the customer), record decision (written reason, clause references, amount), internal notes.
* Copilot (Tier ≤ 1): case summary with labelled sources, missing items, draft information request pre filled into the form.
* Customer directory scoped to the organisation; team list; admin audit view with hash chain verification; account security page (sessions, password, MFA).

### Bank operations surface
* Bank administrator role with aggregate analytics only (engagement, insurance outcomes, AI quality and verification), every metric with a definition, k anonymity suppression (cells under 3), consent respected (only customers who allowed aggregate analytics are counted), no route to identifiable records.
* Integration page: the eight provider agnostic interfaces with mode, call counts and the data boundary (ingested / never ingested); bank audit trail with chain verification; account security page.

### Platform
* Seven permission tiers for people and four tool tiers for the agent, enforced in code; approvals with one time tokens and payload hashing; idempotent adapter submissions; hash chained audit log; PII masked operation logs; prompt injection scanner; grounding verifier; rate limiting and account lockout; sessions with revocation; password change and hashed single use reset tokens; TOTP MFA with optional enforcement for privileged roles; CSRF; CSP and other security headers; tenant isolation; consent aware model routing; notifications.
* Design system: ClaimMate 60/30/10 brown tokens applied through CSS custom properties, system font stack and type scale, 12/8/6 px radii, tabular numerals, reduced motion support. The brief asked for the existing logo to be recoloured without changing its shape; the repository had no logo asset (the previous build drew a plain orange rounded tile with CSS and no lantern shape), so an inline SVG lantern mark was drawn in the token colours. It is a new mark, not a recoloured one; `DESIGN_SYSTEM.md` says so and explains how to drop in the real ClaimMate logo if one exists.
* Seed data: 7 fictional insurers, 1 fictional bank (Harbourline Bank) with a bank administrator, 7 insurer staff accounts, 10 customers (7 of them bank customers), 17 policies with distinct coherent periods including a renewal that supersedes its earlier version, 11 claims across the lifecycle, documents, messages, decisions, generated PDFs, default consents, and a baseline deterministic coverage analysis per customer produced by the pipeline at seed time.
* Tests (104), behavioural evaluation suite (22 cases), evidence generator, Playwright screenshot scripts, Docker image and compose file.

## Mock integrations and how to replace them
| Mock | Interface | Replace with |
|---|---|---|
| `MockInsurerAdapter` | `InsurerAdapter` protocol (`submit_claim`, `get_claim_status`, `verify_webhook`) | Provider REST adapter selected per organisation in `get_adapter()`; keep idempotency keys; record webhook events |
| Deterministic model provider | `LLMProvider.complete` | Already shipped alongside the Claude API provider; used offline and as fallback |
| In process rate limiter and session table | `SlidingWindowLimiter`, `UserSession` | Redis backed limiter and session store for multi process deployments |
| Text extraction without OCR | `extract_text` | OCR service for images and scanned PDFs |
| Stored clause translations | `PolicyClause.translations` | Model translation on demand (the API provider can translate inline) |
| Notifications in app only | `Notification` rows | Email or SMS channel behind the same table |
| `MockAdapters` (bank) | eight `Protocol`s in `services/bank.py` (`IdentityProvider`, `CustomerProfileSource`, `ConsentManager`, `DocumentSource`, `CommunicationsChannel`, `AuditSink`, `AnalyticsSink`, `EmbeddingContext`) | The bank's implementations registered in `adapters_for()`; every mock result carries `mock=True` and `integration_health` reports mode `mock` until then. No invented bank API is assumed (`docs/BANK_INTEGRATION.md`) |
| Rule based analysts A/B/C | `analyze`, `challenge`, `adjudicate` contracts in `agent/analysts.py` | `OpenAICompatibleAnalyst` against vLLM/SGLang/Ollama serving the models in `docs/MODELS.md`; selected automatically when `MODEL_A_URL`, `MODEL_B_URL`, `MODEL_C_URL` are set |
| Development reset token exposure | `_dev_reset_tokens` in `routers/auth.py` | Email delivery of the reset link; the dictionary is only used by tests |

## Known limitations
1. Password reset email delivery, MFA recovery codes and WebAuthn are not implemented; the rate limiter and MFA challenge tokens are per process.
2. Retrieval is lexical; paraphrases without shared terms or tags can be missed (safe failure is a refusal, not a guess).
3. Clause segmentation of arbitrary wordings is heuristic; extracted clauses are low confidence until confirmed.
4. The coverage reading is tag based; it reads "battery theft is excluded inside a theft clause" only through the quoted text, not through negation handling.
5. Streaming responses are not implemented; a turn returns when the run completes (typically under a second offline, a few seconds with the API).
6. Household member and adviser roles exist and can receive explicit shares, but there is no invitation flow yet: an account must be created first and the role set by an administrator.
7. Policy discovery, quotes, comparison of market products and applications are out of scope for this build (deliberately, to stay within Topic D).
8. The audit chain is verified on demand and not anchored externally.
9. Demo wordings are synthetic and short compared with real policies; real wordings will need the OCR and re-ranking work above.
10. The open-weight analysis models have not been exercised end to end on GPU hardware from this repository; the demo runs the rule based analysts and labels the mode on every job.
11. Fact extraction is pattern based on clause text; figures written in unusual forms can land in `ambiguous` or `not_found` (never guessed), which the report shows.
12. The knowledge graph is rebuilt per analysis from the current records rather than maintained incrementally.
13. Integrity checkers are heuristics with explicit thresholds; they are designed to warn, not to adjudicate, and the documentation says so.

## Future work (in priority order)
1. Real insurer adapter with webhook driven status updates and reconciliation.
2. OCR and layout aware clause extraction; model assisted clause classification with the same confidence workflow.
3. Embedding re-ranker behind `ClauseIndex.search`, evaluated against the same case suite.
4. Redis backed sessions, limits and MFA challenges; secret manager; reset email delivery; MFA recovery codes.
4a. Run the three open-weight models on the bank's GPU environment and extend the evaluation suite with model mode cases.
5. Renewal comparison across the customer's own policies (already partially in the gap engine) and a regulated advice boundary review before any product comparison feature.
6. Accessibility audit against WCAG 2.2 AA and a formal privacy impact assessment.
