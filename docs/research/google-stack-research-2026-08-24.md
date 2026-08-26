# Google Stack Research — verified 2026-08-24

Four research briefs gathered via live web research (official Google docs prioritized) on 2026-08-24 for the All Things Agentic Hackathon. These are the fact-base behind PLAN.md. Note: `cloud.google.com/...` docs 301 to `docs.cloud.google.com/...`.

---

## BRIEF 1 — Gemini Enterprise Agent Platform / ADK / Model Armor

**Meta-finding:** At Google Cloud Next '26 (Apr 22, 2026), Vertex AI was rebranded to the **Gemini Enterprise Agent Platform**. Docs moved to `docs.cloud.google.com/gemini-enterprise-agent-platform/...`; ADK docs moved to **adk.dev**. "Vertex AI Agent Engine" is now **Agent Runtime** (API still `aiplatform.googleapis.com` `reasoningEngines`).

### ADK
- Languages: Python, TypeScript, Go, Java, Kotlin. Python `google-adk` latest **v2.7.1 (Aug 17, 2026)**; 2.0 GA May 19, 2026. https://adk.dev/
- v2 = **graph-based execution engine** (nodes = agents/tools/functions): Graph / Dynamic / Collaborative workflows + experimental Agent Routing. `BaseAgent` subclasses `BaseNode`. https://adk.dev/2.0/ , https://adk.dev/workflows/
- Core: `LlmAgent` (instruction templating `{state_var}`, `output_key`, Pydantic `output_schema`, planners, code executor); workflow agents `SequentialAgent`/`LoopAgent`/`ParallelAgent`. https://adk.dev/agents/llm-agents/
- Guardrails: docs recommend **Plugins** over raw callbacks (`LoggingPlugin`, `GlobalInstructionPlugin`). 6 callback hooks exist. https://adk.dev/callbacks/
- Env: Gemini API key OR `GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION` + **`GOOGLE_GENAI_USE_ENTERPRISE=True`** (replaced `GOOGLE_GENAI_USE_VERTEXAI`). https://adk.dev/agents/models/google-gemini/
- Deploy targets: Agent Runtime (`adk deploy agent_engine`), Cloud Run (`adk deploy cloud_run` or `gcloud run deploy` + `get_fast_api_app()`), GKE, any container. https://adk.dev/deploy/
- A2A protocol: Linux Foundation, spec v1.0.0 (Mar 2026) / v1.0.1 (May 2026); ADK support Experimental. https://a2a-protocol.org/latest/

### Agent Runtime (ex-Agent Engine) — GA
- Managed deploy/scale for agents; frameworks: google-adk, langchain, langgraph, ag2, llama-index, custom. Scaling 0–1000 instances; default 4 vCPU/4Gi. July 2026 blog: Runtime + **Agent Identity** "available for everyone."
- **Sessions** (events + per-conversation state, auto-managed for ADK) and **Memory Bank** (LLM-extracted long-term memories, similarity retrieval, TTL, `VertexAiMemoryBankService`). https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank
- Bidi streaming Preview, **10-min query timeout** — long jobs must chunk state via Sessions/Memory. Pricing: per vCPU-hr + GiB-hr, "no-cost access tier" (per adk.dev); historical ~$0.0994/vCPU-hr — verify in console.

### GEAP governance components (all REAL, announced Next '26)
- **Agent Registry**: central inventory — agents (auto-registered from supported runtimes), MCP servers/tools, endpoints; auth bindings. https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/agent-registry
- **Agent Gateway**: HTTP/MCP/A2A proxy, mTLS + DPoP, MCP-aware policy, Model Armor via Service Extensions; `gcloud network-services agent-gateways`. **Heavy to provision — 1–2 days; skip/mention only.**
- **Agent Identity** (GA): SPIFFE IDs `spiffe://TRUST_DOMAIN/resources/...`, auto X.509 24h rotation, agent as IAM principal, auth manager credentials vault. **Auto-assigned on Agent Runtime deploy — zero extra work.**
- **Agent Observability**: per-agent dashboards (sessions, tokens, p50/p95/p99, errors), online eval monitors (hallucination/safety/tool-use), Traces span DAGs, Topology; OTel GenAI semconv. Enable on Runtime via `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true`, `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`, `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY`. https://docs.cloud.google.com/gemini-enterprise-agent-platform/optimize/observability/overview

### Model Armor (GA) — https://docs.cloud.google.com/security-command-center/docs/model-armor-overview
- Inspects prompts AND responses; documents (PDF/Office/CSV/TXT ≤4MB); **images = Preview: JPEG/PNG/BMP, ≤4MB, ONE image per request, us/eu multi-regions only**.
- Filters: prompt injection + jailbreak; SDP basic/advanced (PII, credentials); malicious URLs; RAI (hate/harassment/sexual/dangerous); CSAM always-on. Single-turn only; no audio/video.
- **Verdict: NOT suitable as the photo-moderation pipeline.** Use for text surfaces (`SanitizeUserPrompt`/`SanitizeModelResponse` on `modelarmor.googleapis.com`). For images: Cloud Vision SafeSearch (GA) + Gemini rubric.
- Floor settings = org/project minimum filter requirements. Historical free tier ~2M tokens/mo (verify).

---

## BRIEF 2 — Gemini models & generative media (pricing per 1M tokens unless noted)

**Meta:** New **Interactions API** is the primary Gemini API surface (Lyria 3, Omni, structured output); `generateContent` still required for Batch + explicit caching. `temperature/top_p/top_k` deprecated Jul 21, 2026.

### Text/multimodal lineup (all 1M ctx in / 65,536 out) — https://ai.google.dev/gemini-api/docs/models , /pricing
| Model | Status | In | Out |
|---|---|---|---|
| `gemini-3.7-flash` | GA Aug 13, 2026 | $0.75 intro (→$1.50 Jan 2027) | $3.75 intro |
| `gemini-3.6-flash` | GA Jul 21, 2026 | $0.75 intro | $3.75 intro |
| `gemini-3.5-flash` | GA May 19, 2026 | $1.50 | $9.00 |
| `gemini-3.5-flash-lite` | GA Jul 21, 2026 | $0.30 | $2.50 |
| `gemini-3.1-flash-lite` | GA | $0.25 | $1.50 (but version <3.5 — likely fails rule) |
| `gemini-3.1-pro-preview` | Preview | $2.00/$4.00 | $12/$18 |

- Free tier includes 3.7/3.6/3.5 flash, 3.5-flash-lite (incl. **free Batch**), Gemma 4. NOT free: Nano Banana, Veo, Lyria, Omni. **Free-tier data used to improve Google products — route guest media through billed tier.**
- Image tokens: ≤384px both dims = 258 flat; else 768×768 tiles @258 (960×540 → 1,548). `media_resolution` param on Gemini 3. **Classify thumbnails.**
- 2D bounding boxes (`box_2d` [ymin,xmin,ymax,xmax] /1000) + segmentation masks supported; structured output via JSON schema; Batch API = 50% off, Tier-1 enqueue 10M tokens (flash-lite).
- Context caching: implicit auto on 2.5+; cached input 3.5-flash-lite $0.03/1M.
- Rate limits no longer published — per-project, spend-based rolling caps (Tier 1 $10/10-min); check aistudio.google.com/rate-limit.

### Veo 3.1 (latest; Veo 2/3 shut down Jun 30, 2026) — https://ai.google.dev/gemini-api/docs/veo
- `veo-3.1-generate-preview`, `veo-3.1-fast-generate-preview`, Lite; Vertex GA IDs `veo-3.1-generate-001`/`-fast-`; text/image/video-to-video, native audio, 4/6/8s, 720p/1080p/4K, 9:16 supported, extension to 148s.
- Pricing per second: 3.1 $0.40; **Fast $0.10**; Lite $0.05. No free tier. Vertex region us-central1 only. Person-generation: image-to-video `allow_adult` only (EU/UK/CH/MENA locked).
- Alt: `gemini-omni-flash-preview` ~$0.10/s conversational video gen/edit; "recognizable people" images unsupported.

### Lyria 3 (Preview, open, no allowlist) — https://ai.google.dev/gemini-api/docs/music-generation
- `lyria-3-clip-preview` (30s clips, **$0.04/song**), `lyria-3-pro-preview` (full songs, $0.08). Text- AND image-to-music (≤10 images), 44.1kHz stereo MP3, vocals + [Verse]/[Chorus] lyrics, instrumental via prompt, Hindi among languages. SynthID watermark. Commercial use permitted under Pre-GA terms. Lyria RealTime (`lyria-realtime-exp`) still exists — WebSocket, instrumental-only.

### Imagen — DEAD. Shut down on Gemini API Aug 17, 2026; Vertex pages deprecated. Migrate to Nano Banana.

### Nano Banana (Gemini native image gen/edit, all GA) — https://ai.google.dev/gemini-api/docs/image-generation
- `gemini-3.1-flash-image` (NB2, workhorse: $0.045@0.5K/$0.067@1K/$0.101@2K/$0.151@4K), `gemini-3-pro-image` (NB Pro, $0.134@1K), `gemini-3.1-flash-lite-image` (~$0.0336@1K). Editing real photos: inpaint, style, multi-image composition, ≤14 refs, character consistency, multi-turn via `previous_interaction_id`. No free tier; SynthID; resolution strings UPPERCASE ("1K"). **Test person-edit policy day 1.**

### Gemma 4 (Apr 2, 2026) — free on Gemini API
- Variants E2B/E4B (edge), 12B, **26B-A4B MoE**, 31B; image input all, audio on small ones; `gemma-4-26b-a4b-it` / `gemma-4-31b-it` via Gemini API marked "Free of charge" (free-only). Cloud Run GPU serving via Ollama tutorial if needed.

---

## BRIEF 3 — Face identity & reel production

### Face identity
- **Cloud Vision** `FACE_DETECTION`: alive (2026-08-11 update) — boxes, 34 landmarks, head pose, joy/sorrow/anger/surprise, underExposed/blurred/headwear. **"Specific individual Facial Recognition is not supported."** Celebrity Recognition shut down Sep 2025. Google sells zero face-identity API.
- **MediaPipe**: NO face-identity embedder (only generic Image Embedder — MobileNet-class semantic, unsuitable). Face Detector = BlazeFace (boxes + 6 keypoints only).
- **Vertex `multimodalembedding@001`**: semantic embeddings (scene/clothing), NOT identity-discriminative — don't build "find me" on it.
- **Gemini as matcher: NO** — undocumented capability, nondeterministic, refusal-prone, O(N) cost, biometric-policy risk.
- **The pipeline (industry standard — Premagic/Kwikpic pattern):** selfie (consent artifact) → detect+align (SCRFD or YuNet 5-landmark 112×112 warp) → ArcFace 512-d L2-normalized → Firestore per-face docs → `findNearest`.
- **InsightFace** (`pip`, ONNX Runtime CPU): `buffalo_l` 326MB (SCRFD-10GF + ArcFace R50, LFW 99.83), `buffalo_sc` 16MB. **License: lib MIT, pretrained models non-commercial research only** — hackathon OK, production swap to AuraFace/SFace. Alt: OpenCV YuNet + SFace (permissive, tiny; what PhotoPrism ships; DBSCAN clustering, sface match dist 0.35).
- **Firestore vector search: GA since Sep 5, 2024.** Max 2048 dims; `findNearest` ≤1000 results; **flat index only (exact KNN)**; EUCLIDEAN/COSINE/DOT_PRODUCT (DOT_PRODUCT on unit vectors recommended); pre-filtering supported; no realtime listeners on vector queries; needs CLI-created index. Perfect at 10k faces. Vertex AI Vector Search = ~$65–100+/mo idle, overkill. ArcFace same-person cosine sim threshold ~0.4–0.5 (calibrate).
- Consent/compliance: Google GenAI Prohibited Use Policy bars biometrics without consent; GDPR Art. 9 explicit consent; selfie upload = consent artifact; per-event scoping + delete button.

### Reel production
- **ffmpeg (subprocess, Python-assembled filtergraphs)**: `zoompan` (Ken Burns), `xfade` (~50–60 transitions, ≥4.3, inputs must share res/format/fps), `drawtext`/ASS captions, `acrossfade`. Fastest, free.
- **MoviePy 2.2.1** (May 2025, MIT, v2 API break, Pillow-based TextClip): easier DX, slower (numpy frames). Fallback if filtergraphs get fiddly.
- **Remotion**: `@remotion/cloudrun` is Alpha/unmaintained; company licensing. Skip. **editly**: dormant. Skip.
- Hosted escape hatches: Shotstack ($0.30/min PAYG, 10 free credits), Creatomate, JSON2Video (watermarked free tier).
- **librosa** `beat_track`: tempo + beat times, CPU, seconds per song. **librosa 1.0.0 (Aug 11, 2026) requires Python ≥3.12 — pin `librosa<1.0` on 3.11.** MP3→WAV via ffmpeg first, load with soundfile.
- **Transcoder API**: concat/trim/overlays only — NO transitions, NO text, NO Ken Burns. Skip. ($0.03/min HD.)
- **Cloud Run 2026**: services 60-min max timeout; **Jobs 168h** (GPU jobs 1h); 8 vCPU/32GiB max; **L4 GPU GA** no-quota (~$0.67–0.84/h, scale-to-zero) — unnecessary for our renders. 30–60s 1080×1920 H.264 on 8 vCPU = single-digit minutes.
- Collages: custom Pillow (`Image.new` + `ImageOps.fit` + paste grid), crops anchored on face boxes. No maintained lib exists.

---

## BRIEF 4 — Scale architecture & hackathon meta

### Ingestion at scale (canonical 2026 patterns)
- **Signed V4 PUT URL** per photo (15-min expiry, content-type pinned; XML API endpoints only — JSON endpoints ignore CORS config). Signed **POST policy** if size caps needed (`content-length-range`). **Videos: server-initiated resumable session** → hand session URI to browser (it's a bearer token; 1-week validity; 256KiB chunk multiples).
- CORS pitfalls: include `Content-Type`, `x-goog-resumable` in `responseHeader`; exact origin scheme match; never list OPTIONS. `gcloud storage buckets update --cors-file`.
- **Eventarc Standard** = the clean default: `google.cloud.storage.object.v1.finalized` → CloudEvents POST → Cloud Run. At-least-once; backoff 10s→600s; retention 24h; **DLQ configured on the underlying `eventarc-REGION-...` Pub/Sub subscription**. Idempotency: dedupe on CloudEvents source+id. **Write derived outputs to a separate bucket (retrigger-loop warning from official tutorial).**
- **Cloud Tasks over Pub/Sub for Gemini metering**: server-side rate controls (`max-dispatches-per-second`, `max-concurrent-dispatches`, retry knobs); Pub/Sub has no server-side rate cap. Pattern: Eventarc → intake → Cloud Tasks queue (5–10 dispatch/s) → classifier worker.
- **Firestore realtime**: built for this scale; many small writes > batches; **500 writes/s cap on sequentially-increasing indexed fields (`createdAt`) → index exemption**; auto-IDs; kiosk queries `limit(50)`. Cost at 5k photos × 500 listeners: <$5.
- **iOS Safari Web Push: only for home-screen-installed PWAs (16.4+), permission needs user gesture.** Primary bounty channel = Firestore-listener in-app banners; FCM = progressive enhancement. FCM JS: `firebase-messaging-sw.js` at root, VAPID key, note `getToken()` deprecated → `register()`/FID.
- Thumbnails: in our own Eventarc worker (sharp/Pillow) — Firebase Extensions program **sunsets Mar 31, 2027**.
- Hosting: **Firebase App Hosting** (GA) = GitHub → Cloud Build → Cloud Run + CDN for Next.js SSR; classic Hosting for static. Firebase **Anonymous Auth** for zero-friction guest uid.
- Cost totals: Gemini classification <$5 (thumbnails), GCS ~$0.40 + 100GB/mo free NA egress, Cloud Run free tier covers a 3-day event, Firestore <$5. **Realistic total $10–40 vs $150 hackathon credits (+$300 trial).**

### Hackathon meta
- **ADK Hackathon 2025**: 476 submissions, 8 winners (~1.7%). Grand Prize **SalesShortcut**: 34 agents, patterns named explicitly, 5 Cloud Run microservices, real-time dashboard, Medium articles + YouTube bonus. Honorable mentions included solo devs (Bleach, TradeSage — the latter won partly on an honest write-up of Agent Engine failures + fallback).
- **All Things Agentic judging**: Innovation & Operational Utility 40% ("autonomous, high-value action over simple chat") / **Architectural Discipline 30%** (state/memory, credential security, failure handling) / Demo & Production Readiness 30% (reproducible setup, visible GCP deployment). Devpost lists deadline **Aug 31, 2026 5pm PDT** (= Sep 1, 05:30 IST).
- Partner-hackathon rubric: services must be "imported and called in code, not just named in the README." Service count never scored.
- Demo video: ~4 min; must show problem → value prop → live demo → **GCP console proof**; organizer tip: capture proof, then shut down services. Winner formula (7-time winner): scripted VO, problem-with-data → solution → user walkthrough → brief tech → impact; **budget a full day for video + docs**; submit ≥1 week early if possible.
