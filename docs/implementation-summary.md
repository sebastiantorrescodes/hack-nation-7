# What we built

October 4, 2026 — extension version 0.1.4.

## A. Reliable website capture and voice context

We restored the conversational capture interface because the replacement disconnected
recording from skill review and teaching. Build Work Map still calls `onBuilt`, creates
draft skills in Supabase and feeds the existing review and trainee screens.

We added automatic event-logger attachment, typing/Stop flush, durable retries, richer
frame and shadow-root snapshots, and a Check screen preview to make capture easier to
verify. The picture stays visible when AI interpretation fails. Observed actions,
page text and workflow context reach the ElevenLabs interviewer directly; extra
automatic model analysis is optional. Packaged AudioWorklets fix extension startup
without weakening its script security policy. The interface uses SF typography,
thin borders, one accent and touch targets of at least 44 pixels.

## B. Gemini through one model API interface

We connected Gemini to the existing backend model adapter because local model downloads
and free OpenRouter capacity were slowing the demo. With `LLM_PROVIDER=auto`, adding
`GEMINI_API_KEY` selects Gemini; installations without that key retain OpenRouter.
Explicit provider selection remains available. All backend reasoning uses the same
schema validation and error handling. The ElevenLabs voice conversation keeps its
own configured model.

We selected `gemini-3.5-flash-lite` as the default after it passed screenshot reading
and the real Work Map build. Gemini 3.8 Flash remains an explicit option; it returned
HTTP 503 twice during the build test. Requests never silently switch providers/models.
Keys stay backend-only and are excluded from Git.

Gemini requires an AI Studio project marked Free tier with billing disabled for
no-cost use. Its API key cannot enforce a zero-dollar spending cap on a billing-enabled
project. Free quotas and availability limits still apply; free-tier demonstrations
use synthetic records because Google may use submitted content to improve products.

## C. One evidence-to-teaching pipeline

We unified observed events, expert explanations, bounded interview checkpoints,
Work Maps and tutoring because separate stores left proposals disconnected from
teachable skills. Models propose; exact expert quotes and same-session observed
events support drafts. Experts review and confirm teach-back before teaching.

Migrations 003 and 004 add durable checkpoints, idempotent capture, atomic Work Map
builds, versioned review and pinned tutoring snapshots. Guardrails and mastery are
deterministic. Local access is loopback-only; public deployment requires configured
Supabase authentication, membership and ownership checks.

## Verification and remaining limits

- 103 backend tests and 35 extension tests passed; the production extension build passed.
- Actual Flash-Lite read four fields from a generated screenshot in 1.2 seconds.
- Actual Flash-Lite built evidence-backed draft skills in a disposable PostgreSQL
  fixture. Review, teach-back, pinned tutoring, guardrails and mastery then passed;
  other model boundaries in that fixture used synthetic responses.
- Running backend, migrated Supabase schema and ElevenLabs signed-URL checks passed.
- Live-model tests used synthetic data and did not write to the team database.
- The complete record-specific Chrome interview remains a hands-on acceptance check.
  Chrome site permissions still apply; protected pages and inaccessible frames may
  remain unreadable. DOM guardrails do not intercept every possible application write.
- Cloning the repository does not supply credentials, apply database migrations or
  install the extension. Public deployment and a one-click judge link are separate work.

See [architecture](agentic-backend.md), [UI setup](ui-capture-setup.md),
[capture tradeoffs](capture-reliability.md) and [repeatable checks](functional-verification.md).
