# Lantern — an insurance assistant that reads policies by the clause

Lantern is an AI agent for **FinTechathon 2026, International Track, Topic D (Insurance Assistant)**. It answers policy questions, finds coverage gaps and guides claims from first report to the insurer's decision, in **English and 中文**, and every statement it makes about cover is tied to a specific clause of the customer's own policy wording.

Four things make it different from a chatbot bolted onto a FAQ:

1. **Grounded by construction.** Clause level retrieval, inline citations like `[NGM-HC-204481 §2.6]` that open the clause, a code level verifier that rejects citations the model did not retrieve, and explicit refusal when the wording is silent.
2. **Coverage analysis that shows its work.** Typed policy facts with an explicit `not_found` state, a three role analysis (analyst, independent challenger, judge) designed for open-weight models run inside the bank, six verification layers, deterministic illustrative calculations with traces, and a risk graph with provenance on every edge. Risk priority and evidence confidence are shown separately.
3. **Permission tiers enforced in the backend.** Seven tiers for people, four for the agent's tools. Tier 2 tools need the customer's approval through a one time token bound to the exact payload; claim decisions have no tool at all. Documents are untrusted data; a prompt injection in an upload is flagged and cannot trigger an action. Seven integrity checkers warn but never accuse.
4. **A real workflow on three sides.** The customer builds a claim pack against a checklist generated from the policy's own claims conditions; the insurer gets a case workspace, a queue, a copilot that summarises but never decides, and a hash chained audit log; the bank gets aggregate analytics and an integration layer with consent controls, and never sees a customer record.

Everything in the demo is fictional: insurers, the bank, policies, customers, claims. No live bank or insurer connection exists; every integration is a labelled mock.

---

## Quick start

Requirements: Python 3.11+, Node 20+ (only to build the interface).

```bash
git clone https://github.com/IndianMoses/lantern.git && cd lantern
make install            # pip install backend, npm install frontend
make build              # builds the single page app into frontend/dist
make backend            # http://127.0.0.1:8000  (seeds the demo database on first start)
```

Or with Docker: `docker compose up --build` and open http://localhost:8000.

Open the app and sign in with a demo account (listed on the login page in development, or `GET /api/dev/demo-accounts`):

| Role | Account | Password | Try |
|---|---|---|---|
| Customer (UK, English) | `alex.tan@demo.example` | `alex-demo-2026` | "Is my bike covered at work?" · "Find gaps in my cover" · "My road bike was stolen from outside the office yesterday…" |
| Customer (深圳, 中文) | `li.wen@demo.example` | `liwen-demo-2026` | "充电起火算不算？" · "分析我的保障缺口" · "我的电动车昨晚在楼下被偷了，已经报警" |
| Customer with a declined claim | `priya.nair@demo.example` | `priya-demo-2026` | "Why was my claim for Biscuit declined? Help me challenge it" |
| Claims handler, Northgate Mutual | `jordan.reyes@northgate.example` | `jordan-demo-2026` | Queue → case workspace (integrity checks) → copilot → decision |
| Insurer admin, Northgate Mutual | `morgan.clarke@northgate.example` | `morgan-demo-2026` | Team & audit → hash chain; Security → enrol TOTP |
| Claims handler, 鹏城安保 | `wang.lei@pengcheng.example` | `wanglei-demo-2026` | Tenant isolation: cannot see Northgate claims |
| Bank administrator, Harbourline Bank | `dana.whitfield@harbourline.example` | `dana-demo-2026` | Aggregates with definitions and suppression → Integration (eight mock interfaces) → Audit |

Customers: open **Coverage gaps** to watch the nine stage analysis run and explore the risk graph; **Settings** holds consent, sharing and account security (sessions, password, MFA). On a phone (or a 390 px window) the app uses a bottom navigation and can be installed from the browser menu.

### Modes

Two independent switches, both read from the server environment (`.env`, see `.env.example`; nothing is ever placed in frontend code).

**Conversational assistant**

| Mode | How | What you get |
|---|---|---|
| **Claude API** | `ANTHROPIC_API_KEY=... LLM_PROVIDER=auto` | Natural conversation; the model chooses tools, the backend enforces tiers and verifies citations. Only used for customers who keep the `external_model_processing` consent on |
| **Offline (default when no key)** | `LLM_PROVIDER=deterministic` | A rule based planner that calls the same tools and composes answers only from tool output. Used by the tests, the evaluation suite and the evidence bundle so everything is reproducible without credentials. If the API fails mid run, the agent falls back to this mode and says so in the log |

**Coverage analysis (Model A analyst, Model B challenger, Model C judge)**

| Mode | How | What you get |
|---|---|---|
| **Open-weight models** | `MODEL_A_URL`, `MODEL_B_URL`, `MODEL_C_URL` pointing at OpenAI compatible endpoints (vLLM, SGLang, Ollama) serving the models in [docs/MODELS.md](docs/MODELS.md); `ANALYSIS_MODE=auto` | Three independent model roles with schema validated JSON, retries and timeouts. A role that fails falls back to the rule based role and the job is labelled `mixed`; a fallback is never shown as a model result |
| **Deterministic (default)** | no model URLs, or `ANALYSIS_MODE=deterministic` | Rule based implementations of the same three roles. This is what the demo, the tests and the evidence bundle run; the analysis page states the mode on every job |

The model never talks to the database. It calls typed tools (`backend/app/agent/tools.py`); every call is validated, authorised against the signed in user, logged with masked inputs, and recorded in the run's operation log.

---

## What it does

### 1. Policy questions with citations
Upload a wording (PDF or text; two English and two Chinese fictional wordings are pre loaded) → clauses are segmented, classified (coverage, exclusion, condition, limit, definition, claims procedure), tagged and indexed. Ask in either language; retrieval is bilingual (BM25 over stemmed English plus Chinese bigrams, with a shared tag lexicon so "手机被偷" finds a theft exclusion and "e-bike battery" finds 第三条). The answer separates *what the wording states*, *my reading* and *what only the insurer decides*; when nothing relevant is found it says so and offers to draft a question to the insurer instead of guessing.

### 2. Coverage analysis
Nine stages with live status: customer context → fact extraction (typed facts with known / unknown / not_found / ambiguous / conflicting states, each tied to a clause) → knowledge graph (provenance on every edge, policy versions kept apart) → Model A analyst → Model B challenger (assesses independently, then challenges A) → Model C judge (accepted / rejected / revise / unresolved, with reasons) → verification layers A to F in code → deterministic calculator (illustrative figures with a trace, never a guess) → report. Every finding cites its evidence, e.g. "your £1,200 bike is covered to £350 unless specified [NGM-HC-204481 §2.6]", "Meridian excludes the USA, the bank's packaged policy includes it". Findings carry a risk priority (critical to investigate / high / medium / low, weights published) and a separate evidence confidence, and a customer can ask for a human review from any card. The right hand panel is an interactive risk graph (layers, focus mode, verified only), replaced by a list on phones.

### 3. Claims guidance, end to end
Report what happened (chat or wizard) → the agent ranks the customer's policies for the incident, reads the applicable clauses (cover, possible exclusions, conditions, excess), opens a draft and generates a **checklist derived from the policy's own claims conditions** (each item says why it is needed, how to get it, and cites the clause) → evidence uploads satisfy items → a rule based completeness check (administrative only, explicitly not a coverage view) → **approval card** showing exactly what will be shared → submission through an insurer adapter (mock, idempotent, can be made to fail) or, for policies not connected to an insurer, an assisted pack (PDF) → tracking with *last known* status and source → insurer requests, customer responses (approval gated) → the handler's decision → the assistant explains the decision from the clauses relied on and drafts a factual request for clarification or review, without promising success.

### 4. Integrity checks (never accusations)
Seven checkers run on every claim change: dates, document integrity, duplicate evidence, amount plausibility, prompt injection, cross document consistency, evidence provenance. Each returns passed / warning / failed_validation / inconclusive / human_review_required with a plain reason; aggregation order is fixed and warnings never become failures. Only a validation failure blocks a submission; a claim that needs a second look goes to an insurer reviewer with neutral wording for the customer. The approval card is bound to the integrity inputs, so evidence added after the card invalidates it.

### Insurer workspace
Operations dashboard (metrics with definitions, amounts kept separate: submitted / approved / settled / outstanding, SLA breaches, workload), filterable queue with check summaries, case workspace with facts labelled by source, documents with injection flags, integrity results, request information, record decision (human only, written reason required, audit logged), internal notes, a tier ≤ 1 copilot, and an admin audit view that verifies the hash chain.

### Bank operations
A bank administrator sees engagement, insurance outcome and AI quality metrics for the bank's own customers, each with its definition, only over customers who consented to aggregate analytics, with cells under three suppressed. The integration page lists the eight provider agnostic interfaces (identity, profile, consent, documents, communications, audit sink, analytics sink, embedding), all currently mock adapters, and the data boundary (never transactions, balances or credit data). See [docs/BANK_INTEGRATION.md](docs/BANK_INTEGRATION.md).

---

## Architecture in one picture

```
 Browser / PWA (React SPA, EN/中文)        Insurer staff            Bank operations
   │ cookie session + CSRF + X-Lang            │                        │ aggregates only
   ▼                                           ▼                        ▼
 FastAPI ──── routers (auth · account · customer · claims · documents · gaps · approvals · assistant · insurer · bank)
   │            │ server side authorisation on every endpoint, seven role tiers, tenant scoping, consent checks
   │            ▼
   │   Agent runner ── intent → system prompt v → provider loop → verifier → blocks
   │            │                        ▲
   │            │   typed tools (tier 0/1/2) │ Claude API (consent gated)  or  deterministic planner
   │            ▼
   │   Coverage pipeline ── facts → graph → A analyst → B challenger → C judge → verification A–F → calculator → report
   │            │                        ▲ open-weight models on OpenAI compatible endpoints, or rule based roles
   │            ▼
   │   Services: retrieval (BM25 EN/中文) · coverage view · checklist rules · completeness · integrity ensemble
   │             claims lifecycle (state machine) · approvals · insurer adapters · bank adapters (8 mocks) · claim pack
   │            │
   ▼            ▼
 SQLAlchemy (SQLite dev / Postgres) · private file storage · JSONL operation logs · hash chained audit log
```

Details, the data model and the core algorithms are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Security and compliance summary

See [docs/SECURITY_SELF_ASSESSMENT.md](docs/SECURITY_SELF_ASSESSMENT.md) for the full assessment, threat model and known risks.

| Control | Where |
|---|---|
| Permission tiers 0–3, enforced in code, not prompts | `app/agent/tools.py` (`execute`), `services/approvals.py` |
| Approval tokens: one time, payload hashed, expiring, owner bound | `services/approvals.py`, `security.py` |
| Tenant isolation: customer records by `user_id`, insurer records by `insurer_org_id`; pre submission claims invisible to insurers; 404 not 403 | `deps.py`, `routers/insurer.py` |
| Untrusted documents: validation by magic bytes, injection scanner (EN/中文), wrapped as data for the model, flags surfaced to both sides | `services/extraction.py`, `agent/runner.py` |
| Grounding verifier: unknown citations marked, coverage claims without citations get a caution banner | `agent/verifier.py` |
| PII masking in tool records and operation logs | `pii.py`, `agent/oplog.py` |
| Hash chained, append only audit log with verification endpoint | `audit.py` |
| Sessions: argon2id passwords, HttpOnly cookies, CSRF double submit, rate limits, lockout, session revocation, TOTP MFA (optional enforcement for privileged roles), CSP and other headers | `security.py`, `routers/auth.py`, `main.py` |
| Idempotent submissions (adapter keys), duplicate approval replay rejected, approval bound to integrity inputs | `services/claims.py`, `services/adapters/` |
| Consent purposes (append only), consent aware model routing, explicit record sharing | `services/bank.py`, `routers/account.py`, `agent/runner.py` |
| Bank surface: aggregates with k anonymity, no route to customer records, labelled mock adapters | `routers/bank.py`, `services/bank.py` |
| Integrity ensemble: fixed aggregation, warnings never block, neutral wording | `services/integrity.py` |

---

## Tests, evaluation, evidence

```bash
make test        # 105 tests: rules, transitions, verifier, scanner, tenant isolation, CSRF, approvals,
                 #            full claim journey, duplicate submission, adapter failure, model loop with a stubbed API,
                 #            fact extraction, calculator, prioritisation, verification, pipeline and fallback labelling,
                 #            integrity checkers and gates, bank aggregates, consent, sharing, lockout, reset, MFA, CSP
make evals       # 22 behavioural cases (retrieval, refusal, injection, unauthorised, bilingual, deadlines...) -> backend/evals/results-*.json
make evidence    # runs a 27 step customer + insurer journey and writes ./evidence (operation logs, audit chain, journey)
```

The evidence bundle in `evidence/` is the sandbox execution record for the submission; regenerate it with the Claude API by setting `LLM_PROVIDER=anthropic`.

---

## Repository layout

```
backend/app/
  main.py            FastAPI app, static SPA serving, security headers
  config.py          settings (env), models.py (schema), db.py, security.py, deps.py, audit.py, pii.py
  routers/           auth, account (consents, shares, feedback), profile, policies, documents, gaps, claims,
                     communications, approvals, assistant, insurer, bank, dev
  services/          retrieval, extraction, taxonomy, coverage, checklist, completeness, gap_engine,
                     facts, graph, verification, calculator, prioritisation, analysis (pipeline), integrity,
                     bank (interfaces, mocks, consent), claims (lifecycle), approvals, communications, claim_pack, policies, adapters/
  agent/             tools (typed, tiered), runner, intent, prompts (versioned), analysts (A/B/C), verifier, oplog, providers/
  seed/              fictional policies (EN, 中文), customers, insurers, a bank, claims, generated PDFs
  migrations.py      additive column upgrades for existing SQLite databases
backend/tests/       pytest suite          backend/evals/   evaluation cases and runner
frontend/src/        React + TypeScript SPA (customer app, insurer workspace, bank operations, EN/中文), PWA shell
scripts/             generate_evidence.py, make_icons.py, screenshots.mjs, screens_gaps.mjs, screens_bank.mjs, dev_server.sh
docs/                architecture, models, design system, bank integration, security self assessment, API, evaluation,
                     demo script, limitations, deployment
evidence/            generated execution evidence and screenshots
```

---

## Documentation

- [Architecture, data model and core algorithms](docs/ARCHITECTURE.md)
- [Models: the open-weight selection, licences, inference requirements, fallback](docs/MODELS.md)
- [Design system: ClaimMate tokens, type, the mark](docs/DESIGN_SYSTEM.md)
- [Bank integration and security checklist](docs/BANK_INTEGRATION.md)
- [Security self assessment: permission tiers, threat model, known risks](docs/SECURITY_SELF_ASSESSMENT.md)
- [API and integration guide](docs/API.md) (interactive docs at `/api/docs` when running)
- [Evaluation and testing](docs/EVALUATION.md)
- [Demo script and presentation outline](docs/DEMO_SCRIPT.md)
- [Implemented features, mock integrations, limitations and future work](docs/FEATURES_AND_LIMITATIONS.md)
- [Deployment](docs/DEPLOYMENT.md)

## Licence

MIT. All insurers, products, wordings, people and claims in this repository are fictional and must not be used as real insurance information.
