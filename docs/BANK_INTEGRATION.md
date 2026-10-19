# Bank integration and security checklist

How a bank embeds Lantern without replacing its own systems, what the platform
needs from the bank, what it refuses to ingest, and what must be true before a
pilot. Nothing in this document describes a live connection: every interface
below ships as a labelled mock (`backend/app/services/bank.py`, `MockAdapters`),
and the integration page in the bank surface reports mode `mock` until a real
adapter is registered. No bank's API is assumed or invented.

## 1. Integration boundaries

```
Bank systems                         Lantern (bank hosted)
-----------------------------        -----------------------------------------
Identity provider (SSO)      ──────▶ IdentityProvider.verify_assertion
Customer master / CRM        ──────▶ CustomerProfileSource.fetch_profile   (consent: bank_profile_import)
Consent management           ◀─────▶ ConsentManager.sync                  (mirror of the customer's decisions)
Document vault               ──────▶ DocumentSource.list_documents         (planned use; mock returns [])
Secure messaging / push      ◀────── CommunicationsChannel.send            (consent: bank_communications)
SIEM / audit warehouse       ◀────── AuditSink.deliver                     (hash chained events)
Reporting / BI               ◀────── AnalyticsSink.publish                 (aggregates only, k anonymity 3)
Mobile banking app           ◀─────▶ EmbeddingContext.deep_link            (signed path, no customer data in the URL)
```

What crosses each boundary, and what never does:

| Interface | Direction | Data in | Data out | Never |
|---|---|---|---|---|
| Identity | bank → Lantern | a signed assertion | subject, assurance level | passwords; Lantern keeps its own session and MFA state |
| Profile | bank → Lantern | customer reference, purpose | `full_name`, `country`, `city`, `currency`, insurance relevant `products` | balances, transactions, credit data, income |
| Consent | both | purpose, decision | acknowledgement | free text about the customer |
| Documents | bank → Lantern | customer reference | document metadata for the customer to choose from | bulk document import |
| Communications | Lantern → bank | subject and body of an insurance message | delivery reference | marketing, product offers |
| Audit sink | Lantern → bank | audit events (`seq`, `prev_hash`, `hash`, action, actor type, target type, outcome) | count delivered | document contents, conversation text |
| Analytics sink | Lantern → bank | the same aggregate the bank dashboard shows | acknowledgement | any per customer figure |
| Embedding | both | a path and the customer reference | a deep link with an HMAC signature | tokens or personal data in query strings |

Everything else stays inside Lantern: conversations, uploaded documents,
claims, analyses, the knowledge graph and the operation logs. The bank
administrator role has no route to any of it.

## 2. Replacing a mock

1. Implement the `Protocol` for the interface in a module under
   `backend/app/services/bank_adapters/<bank>.py`. Keep the return
   dataclasses (`ExternalIdentity`, `ProfileImport`, `Delivery`) and set
   `mock=False` only when the call really reached the bank.
2. Register it in `adapters_for(org)` keyed by the bank's `Organization.id`.
   Unregistered interfaces keep the mock, so a bank can go live one interface
   at a time; `integration_health` reports each interface's mode separately.
3. Add a contract test next to `backend/tests/test_bank_and_security.py`
   that runs the adapter against the bank's sandbox with recorded fixtures.
4. Keep every secret (client ids, signing keys, sink credentials) in the
   server environment or a secret manager. Nothing goes in the frontend or
   in source control.

## 3. Tenant isolation

- Customers: every record carries `user_id`; every query filters on it; cross
  customer access is 404. Shares are explicit per record and revocable.
- Insurers: `insurer_org_id` on policies and claims; pre submission claims are
  invisible to insurers.
- Bank: `CustomerProfile.bank_org_id` ties a customer to the bank; the bank
  surface aggregates over that set only and suppresses cells under
  `K_ANON = 3`. Bank staff are `Organization.kind = "bank"` users and are
  rejected by every customer and insurer dependency.
- A multi bank deployment keeps one database per bank (recommended) or relies
  on `bank_org_id` scoping; the code supports both, the former is simpler to
  audit.

## 4. Pre pilot checklist

Verified in this repository (tests named in brackets):

- [x] Bank role sees aggregates only; no identifiable field in the response; no route to customer records (`test_bank_admin_sees_aggregates_only`)
- [x] Aggregates count only customers who consented to `analytics_aggregate`; consent records are append only; locked purposes cannot be withdrawn through the toggle (`test_analytics_respect_consent`)
- [x] Hosted model calls are gated by `external_model_processing` consent (`test_consent_gates_external_model_processing`)
- [x] Record sharing is explicit, view only by default, revocable, and household accounts see only what was shared (`test_record_sharing_is_explicit_and_revocable`)
- [x] Session list and revocation; password change revokes other sessions (`test_sessions_list_and_revoke`, `test_change_password_revokes_other_sessions`)
- [x] Login lockout after repeated failures (`test_lockout_after_repeated_failures`)
- [x] Password reset tokens: no enumeration, hashed, single use (`test_password_reset_flow`)
- [x] TOTP enrolment, challenge at login, optional enforcement for privileged roles (`test_totp_mfa_enrolment_and_login`, `test_mfa_required_for_privileged_when_configured`)
- [x] CSP, frame denial and no store caching on API responses (`test_security_headers_include_csp`)
- [x] Mock adapters label every result and report mode `mock` (`test_mock_adapters_are_labelled`)
- [x] Audit chain verification on the bank audit view (`test_bank_admin_sees_aggregates_only`)
- [x] Insurer tenant isolation and pre submission invisibility (`test_insurer_tenant_isolation`, `test_pre_submission_claims_are_invisible_to_insurers`)
- [x] Submission approvals are bound to the integrity inputs: evidence added after the card invalidates it and a fresh card carries the new hash (`test_submission_approval_is_bound_to_integrity_inputs`)

Required before a pilot, not done here:

- [ ] Real `IdentityProvider` against the bank's SSO (OIDC or SAML) with assurance level mapping; decide whether Lantern passwords remain for staff only
- [ ] Consent manager mapping between Lantern purposes and the bank's consent taxonomy, with the bank as the system of record if its policy requires it
- [ ] Audit sink delivery schedule and retention; external anchoring of the chain head (for example a daily hash published to the bank's SIEM)
- [ ] Email or push delivery of password reset links; MFA recovery codes; WebAuthn for staff if the bank's policy prefers it
- [ ] `MFA_REQUIRED_FOR_PRIVILEGED=true`, `COOKIE_SECURE=true`, a generated `SECRET_KEY`, Postgres, Redis backed rate limits and challenge tokens, secret manager
- [ ] Data protection impact assessment covering the purposes above, retention (`document_retention_extended` default off), and the k anonymity threshold for the bank's customer base size
- [ ] Penetration test of the deployed instance; this document is a self assessment
- [ ] GPU serving of the three open-weight models inside the bank's network (`MODELS.md`) and an evaluation run in model mode
- [ ] Legal review of the gap analysis wording against local insurance distribution rules (information versus advice)

## 5. Operating model

- Deployment: one container (`DEPLOYMENT.md`) behind the bank's gateway;
  the SPA and the API share an origin, which is what the CSP assumes.
- Monitoring: `/api/health` (audit chain state, provider mode), the bank's
  audit view, and the operation logs per agent run.
- Support: customers can export their data; insurers act through their own
  surface; the bank sees aggregates. A customer complaint is handled on the
  insurer side with the audit trail as evidence.
- Change control: pipeline version (`PIPELINE_VERSION`), prompt versions and
  extractor versions are written on every analysis, so a change in output can
  be traced to a change in code or model.
