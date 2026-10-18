# Lantern — an insurance assistant that reads policies by the clause

Lantern is an AI agent for **FinTechathon 2026, International Track, Topic D (Insurance Assistant)**. It answers policy questions, finds coverage gaps and guides claims from first report to the insurer's decision, in **English and 中文**, and every statement it makes about cover is tied to a specific clause of the customer's own policy wording.

Three things make it different from a chatbot bolted onto a FAQ:

1. **Grounded by construction.** Clause level retrieval, inline citations like `[NGM-HC-204481 §2.6]` that open the clause, a code level verifier that rejects citations the model did not retrieve, and explicit refusal when the wording is silent.
2. **Permission tiers enforced in the backend.** Tier 0 reads, Tier 1 prepares, Tier 2 needs the customer's approval through a one time token bound to the exact payload, Tier 3 (claim decisions) has no tool at all. Documents are untrusted data; a prompt injection in an upload is flagged and cannot trigger an action.
3. **A real workflow on both sides.** The customer builds a claim pack against a checklist generated from the policy's own claims conditions; the insurer gets a case workspace, a queue, a copilot that summarises but never decides, and a hash chained audit log.

Everything in the demo is fictional: insurers, policies, customers, claims.

---

## Quick start

Requirements: Python 3.11+, Node 20+ (only to build the interface).

```bash
git clone <this repository> lantern && cd lantern
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
| Claims handler, Northgate Mutual | `jordan.reyes@northgate.example` | `jordan-demo-2026` | Queue → case workspace → copilot → decision |
| Insurer admin, Northgate Mutual | `morgan.clarke@northgate.example` | `morgan-demo-2026` | Team & audit → hash chain |
| Claims handler, 鹏城安保 | `wang.lei@pengcheng.example` | `wanglei-demo-2026` | Tenant isolation: cannot see Northgate claims |

### With or without the Claude API

| Mode | How | What you get |
|---|---|---|
| **Claude API** | `ANTHROPIC_API_KEY=... LLM_PROVIDER=auto` | Natural conversation; the model chooses tools, the backend enforces tiers and verifies citations |
| **Offline (default when no key)** | `LLM_PROVIDER=deterministic` | A rule based planner that calls the same tools and composes answers only from tool output. Used by the tests, the evaluation suite and the evidence bundle so everything is reproducible without credentials. If the API fails mid run, the agent falls back to this mode and says so in the log |

The model never talks to the database. It calls typed tools (`backend/app/agent/tools.py`); every call is validated, authorised against the signed in user, logged with masked inputs, and recorded in the run's operation log.

---

## What it does

### 1. Policy questions with citations
Upload a wording (PDF or text; two English and two Chinese fictional wordings are pre loaded) → clauses are segmented, classified (coverage, exclusion, condition, limit, definition, claims procedure), tagged and indexed. Ask in either language; retrieval is bilingual (BM25 over stemmed English plus Chinese bigrams, with a shared tag lexicon so "手机被偷" finds a theft exclusion and "e-bike battery" finds 第三条). The answer separates *what the wording states*, *my reading* and *what only the insurer decides*; when nothing relevant is found it says so and offers to draft a question to the insurer instead of guessing.

### 2. Coverage gap analysis
Needs are derived from the customer's own profile (assets, housing, activities, household) and checked against the clauses of every active policy: covered, partial (a limit below the declared value, an exclusion that bites, an extension not selected), gap, duplicate (two travel policies that both cover medical and baggage) and notes. Every finding cites its evidence, e.g. "your £1,200 bike is covered to £350 unless specified [NGM-HC-204481 §2.6]", "Meridian excludes the USA, the bank's packaged policy includes it".

### 3. Claims guidance, end to end
Report what happened (chat or wizard) → the agent ranks the customer's policies for the incident, reads the applicable clauses (cover, possible exclusions, conditions, excess), opens a draft and generates a **checklist derived from the policy's own claims conditions** (each item says why it is needed, how to get it, and cites the clause) → evidence uploads satisfy items → a rule based completeness check (administrative only, explicitly not a coverage view) → **approval card** showing exactly what will be shared → submission through an insurer adapter (mock, idempotent, can be made to fail) or, for policies not connected to an insurer, an assisted pack (PDF) → tracking with *last known* status and source → insurer requests, customer responses (approval gated) → the handler's decision → the assistant explains the decision from the clauses relied on and drafts a factual request for clarification or review, without promising success.

### Insurer workspace
Operations dashboard (metrics with definitions, amounts kept separate: submitted / approved / settled / outstanding, SLA breaches, workload), filterable queue, case workspace with facts labelled by source, documents with injection flags, request information, record decision (human only, written reason required, audit logged), internal notes, a tier ≤ 1 copilot, and an admin audit view that verifies the hash chain.

---

## Architecture in one picture

```
 Browser (React SPA, EN/中文)                     Insurer staff
   │ cookie session + CSRF                           │
   ▼                                                 ▼
 FastAPI ──── routers (customer / claims / documents / approvals / assistant / insurer)
   │            │ server side authorisation on every endpoint, tenant scoping
   │            ▼
   │   Agent runner ── intent → system prompt v → provider loop → verifier → blocks
   │            │                        ▲
   │            │   typed tools (tier 0/1/2) │ Claude API  or  deterministic planner
   │            ▼                        │
   │   Services: retrieval (BM25 EN/中文) · coverage view · checklist rules · completeness
   │             gap engine · claims lifecycle (state machine) · approvals · adapters · claim pack
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
| Sessions: argon2 passwords, HttpOnly cookies, CSRF double submit, rate limits, security headers | `security.py`, `main.py` |
| Idempotent submissions (adapter keys), duplicate approval replay rejected | `services/claims.py`, `services/adapters/` |

---

## Tests, evaluation, evidence

```bash
make test        # 64 tests: rules, transitions, verifier, scanner, tenant isolation, CSRF, approvals,
                 #           full claim journey, duplicate submission, adapter failure, model loop with a stubbed API
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
  routers/           auth, profile, policies, documents, gaps, claims, communications, approvals, assistant, insurer, dev
  services/          retrieval, extraction, taxonomy, coverage, checklist, completeness, gap_engine,
                     claims (lifecycle), approvals, communications, claim_pack, policies, adapters/
  agent/             tools (typed, tiered), runner, intent, prompts (versioned), verifier, oplog, providers/
  seed/              fictional policies (EN, 中文), customers, insurers, claims, generated PDFs
backend/tests/       pytest suite          backend/evals/   evaluation cases and runner
frontend/src/        React + TypeScript SPA (customer app and insurer workspace, EN/中文)
scripts/             generate_evidence.py
docs/                architecture, security self assessment, API, evaluation, demo script, limitations, deployment
evidence/            generated execution evidence
```

---

## Documentation

- [Architecture, data model and core algorithms](docs/ARCHITECTURE.md)
- [Security self assessment: permission tiers, threat model, known risks](docs/SECURITY_SELF_ASSESSMENT.md)
- [API and integration guide](docs/API.md) (interactive docs at `/api/docs` when running)
- [Evaluation and testing](docs/EVALUATION.md)
- [Demo script and presentation outline](docs/DEMO_SCRIPT.md)
- [Implemented features, mock integrations, limitations and future work](docs/FEATURES_AND_LIMITATIONS.md)
- [Deployment](docs/DEPLOYMENT.md)

## Licence

MIT. All insurers, products, wordings, people and claims in this repository are fictional and must not be used as real insurance information.
