# Models used by the coverage analysis

Lantern's coverage analysis is a three role pipeline (Model A analyst, Model B
independent challenger, Model C judge) wrapped in deterministic extraction,
verification and calculation stages (see `ARCHITECTURE.md`). The roles are
designed for open-weight models served **inside the bank's own environment**
through any OpenAI compatible chat completions endpoint (vLLM, SGLang, Ollama,
TGI). No policy text leaves the bank's infrastructure when the models run there.

The selection below was checked against the official model repositories on
19 October 2026. Licence terms can change; re-verify before deployment.

## Selected models

| Role | Model | Publisher | Licence (as stated on the repository) | Parameters | Context |
|---|---|---|---|---|---|
| A, primary analyst | `Qwen/Qwen3-32B` | Qwen team, Alibaba Cloud | Apache 2.0 (`license: apache-2.0`, LICENSE file 11.3 kB in the repo) | 32.8B total, 31.2B non embedding, 64 layers, dense | 32,768 natively; 131,072 with YaRN |
| B, independent challenger | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | Mistral AI | Apache 2.0 (`License: apache-2.0`) | 24B, dense | long context (the card's examples use a 131,072 token generation limit; the card itself does not state a context figure) |
| C, judge | `zai-org/GLM-4.5-Air` | Z.ai (Zhipu) | MIT (`License: mit`) | 106B total, 12B active (mixture of experts) | not stated on the card |

Official repositories (weights source):

- https://huggingface.co/Qwen/Qwen3-32B
- https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506
- https://huggingface.co/zai-org/GLM-4.5-Air

### Why these three

- **Different families, different training data.** The challenger's value comes
  from independence. A, B and C are from three unrelated publishers so a
  shared blind spot is less likely than with three sizes of one family.
- **Permissive licences.** Apache 2.0 and MIT permit commercial use, on premise
  deployment and modification without a separate agreement, which matters for a
  bank that must host the weights itself.
- **Bilingual strength.** All three are trained with substantial Chinese and
  English data; the platform serves both languages and policy wordings arrive
  in either.
- **Serving maturity.** All three are supported by vLLM and SGLang out of the
  box, with JSON mode (`response_format`) and tool calling, which the pipeline
  relies on for schema validated output.
- **Judge with a different shape.** GLM-4.5-Air is a mixture of experts model
  with a hybrid thinking mode; the judge's job (weigh two assessments against
  deterministic verification results) benefits from the larger total capacity
  while the 12B active parameters keep latency acceptable.

### Why not others (considered)

- `Qwen/Qwen3-235B-A22B` for the judge: stronger, but roughly 470 GB in bf16
  and operationally heavy for a pilot. Reasonable upgrade path.
- `meta-llama/Llama-3.3-70B-Instruct`: capable, but the Llama community licence
  carries use restrictions and an acceptable use policy; the bank's legal team
  would need to review it, whereas Apache 2.0 and MIT need no extra terms.
- `deepseek-ai/DeepSeek-V3` (MIT weights): very strong, but 671B total
  parameters is beyond the pilot's hardware budget.
- Hosted proprietary APIs: excluded for the coverage pipeline because the
  evidence package contains policy wording and customer circumstances. (The
  conversational assistant can optionally use a hosted provider, gated by the
  customer's `external_model_processing` consent; see `SECURITY_SELF_ASSESSMENT.md`.)

## Inference requirements (from the model cards and publisher guidance)

| Model | Serving guidance | Memory (bf16/fp16, approximate) | Notes |
|---|---|---|---|
| Qwen3-32B | `vllm>=0.8.5` or `sglang>=0.4.6.post1`; Ollama supported | about 66 GB weights (65.5 GB of safetensors on the Hub) plus KV cache; one 80 GB GPU or two 48 GB GPUs | Thinking is on by default. Lantern sends `chat_template_kwargs: {"enable_thinking": false}` and strips any `<think>` block. Publisher's non thinking sampling guidance: temperature 0.7, top_p 0.8, top_k 20, min_p 0 (Lantern uses temperature 0.1 for extraction stability). |
| Mistral-Small-3.2-24B-Instruct-2506 | `vLLM >= 0.9.1` (pulls `mistral_common >= 1.6.2`); Transformers also documented | "~55 GB of GPU RAM in bf16 or fp16" per the model card | Publisher recommends vLLM; strong function calling. FP8 variants exist from third parties; use the official repository for the pilot. |
| GLM-4.5-Air | `vllm serve "zai-org/GLM-4.5-Air"` or SGLang (`lmsysorg/sglang:latest` image); no versions stated on the card | about 220 GB total weights in bf16 (106B parameters); an FP8 repository `zai-org/GLM-4.5-Air-FP8` halves that | Hybrid reasoning (thinking / non thinking). Lantern requests non thinking mode. |

A minimal pilot is therefore three to four 80 GB GPUs (or FP8 variants on
fewer), all inside the bank's network. Smaller fallbacks for a laptop demo:
`Qwen/Qwen3-8B` (A), `mistralai/Mistral-Small-3.2-24B-Instruct-2506` quantised
(B) and `Qwen/Qwen3-14B` (C), accepting less independence between A and C.

## Version pinning

The configuration in this repository selects models **by name only**
(`MODEL_A_NAME` etc.). For a deployment, pin a revision: download with a
specific commit hash from the Hub's "Files and versions" tab and start the
server with `--revision <hash>` (vLLM) or from a local directory. Record the
hash, the download date and the SHA-256 of `model.safetensors.index.json` in
the deployment change log. Lantern writes the model identifier reported by the
endpoint into every analysis job (`models` on `GapAnalysis` and
`CoverageAnalysisJob`), so results can be traced to the serving configuration.

## How the pipeline uses them

- Every role receives the same **evidence package**: typed policy facts with
  clause ids and excerpts, the customer's stated circumstances, and the
  prioritisation inputs. Prompts (`backend/app/agent/analysts.py`) require
  clause ids for every claim and strict JSON.
- **Independence.** B produces its own assessment without seeing A, then
  challenges A in a second call. C sees A, B, the challenges and the
  deterministic verification results, and must give a verdict per finding with
  a reason that refers to clauses or checks.
- **Validation.** Every response is parsed and validated against a Pydantic
  schema. A response that does not validate is sent back once with the error;
  a second failure is a provider error.
- **Timeouts, retries, fallback.** `MODEL_TIMEOUT_SECONDS` (default 90),
  `MODEL_MAX_RETRIES` (default 1). When a role fails, the deterministic rule
  based implementation of that role runs instead and the job is labelled
  `mixed`; the stage status and the finding cards show which role fell back.
  A fallback is never presented as a model output.
- **Modes.** `ANALYSIS_MODE=auto` uses the models when all three URLs are set,
  otherwise `deterministic`. The deterministic analysts are complete
  implementations of the same contracts (the demo ships with them), so the
  product works without any GPU.

## Configuration

```
ANALYSIS_MODE=auto
MODEL_A_URL=http://vllm-a.internal:8000/v1
MODEL_A_NAME=Qwen/Qwen3-32B
MODEL_A_API_KEY=            # only if the serving layer requires one; never in frontend code
MODEL_B_URL=http://vllm-b.internal:8000/v1
MODEL_B_NAME=mistralai/Mistral-Small-3.2-24B-Instruct-2506
MODEL_C_URL=http://vllm-c.internal:8000/v1
MODEL_C_NAME=zai-org/GLM-4.5-Air
```

Keys and URLs live in the server environment (`.env`, not committed). The
frontend never holds a model key; it only polls `/api/gaps/jobs/{id}`.

## Status

- Verified in this repository: the deterministic implementations of A, B and C,
  the validation, fallback and labelling logic, and the OpenAI compatible
  client (unit tested with a fake server; see `backend/tests/test_coverage_intelligence.py`).
- Not verified in this repository: end to end runs against the three models on
  GPU hardware. That requires the bank's inference environment and is listed as
  planned work in the engineering report.
