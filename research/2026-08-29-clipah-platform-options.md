# Clipah Platform Options

Research date: 2026-08-29

Scope: yt-dlp authentication, transcription and highlight-analysis providers, open-source web editor foundations, and automatic B-roll retrieval/generation. All external references below are first-party documentation or the project's official repository.

## Executive recommendation

- Keep Python for media/AI workers. The immediate problem is provider and job lifecycle design, not the language.
- Keep AssemblyAI initially, but migrate to its current API and benchmark it against Deepgram Nova-3 on a small Indonesian/English corpus. Do not switch providers based on marketing claims alone.
- Replace Clipah's retired Groq model immediately. Use Groq's native SDK, strict JSON Schema output, a configurable model alias, and a provider adapter.
- Treat YouTube-cookie import as an optional, high-risk connector for authorized content. Default production ingestion should be direct upload or an owner-authorized source integration.
- Do not build a timeline/rendering engine entirely from scratch. Run a short technical spike with Elah and OpenReel/Mediabunny, then own Clipah's product-specific UI and composition schema. Retain FFmpeg as the authoritative server renderer.
- Build B-roll as an editable suggestion layer. Start with licensed stock retrieval and semantic reranking; use generative video only as a user-approved fallback when retrieval confidence is low.

## 1. yt-dlp cookies and hosted-product constraints

### Supported authentication mechanisms

yt-dlp officially supports:

1. `--cookies-from-browser BROWSER` to read a local browser profile.
2. `--cookies /path/to/cookies.txt` to read a Mozilla/Netscape-format cookie jar.
3. Combining browser extraction with `--cookies` to export a jar, although yt-dlp warns that this can export cookies for **all sites**, not only the requested URL. The cookie file therefore has the power of the user's logged-in browser session and must be treated as a secret. See the official [yt-dlp cookie FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp).

For YouTube specifically, yt-dlp documents several additional constraints:

- YouTube frequently rotates account cookies. yt-dlp recommends exporting a dedicated incognito/private session that is never reopened, and warns that using an account with yt-dlp can cause temporary or permanent account bans.
- Account cookies should be used only when content actually requires authentication, such as private, age-restricted, members-only, or private-playlist content.
- OAuth login no longer works with yt-dlp.
- YouTube increasingly requires Proof of Origin (PO) Tokens. The current yt-dlp recommendation is an external PO Token provider plugin for the `mweb` client; manually extracted tokens are no longer practical because tokens can be bound to individual video IDs.
- Rate limiting is session/account sensitive, so a single shared service account is a reliability and blast-radius risk.

Sources: [yt-dlp YouTube extractor guidance](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube), [yt-dlp PO Token guide](https://github.com/yt-dlp/yt-dlp/wiki/Po-Token-Guide).

Current yt-dlp also strongly recommends `yt-dlp-ejs`, FFmpeg/ffprobe, and a supported JavaScript runtime (Deno is the documented recommendation) for full YouTube support. Its maintainers recommend the `nightly` channel for regular users because site changes can break the monthly stable release. The current PyPI release is `2026.8.19`, while Clipah pins `2025.10.22`. See the official [yt-dlp package documentation](https://pypi.org/project/yt-dlp/).

### Recommended Clipah operating model

`--cookies-from-browser` is appropriate for local/self-hosted operation where the worker runs on the same trusted device as the user's browser. It is not a viable mechanism for a normal cloud worker.

For a hosted product, if cookie import is retained:

- accept only a per-user, domain-scoped Netscape cookie file through a dedicated consent screen;
- never accept a shared global `cookies.txt` and never bake cookies into an image or repository;
- encrypt the file at rest with an envelope-encryption key, isolate access to the ingestion worker, never include its contents in logs/errors, and delete it after a short explicit retention period;
- bind the secret to `user_id`/`connection_id`, not to a process-global path;
- execute each download in a fresh job directory/container with resource, URL, duration, and playlist limits;
- provide revoke/delete and “connection expired” states rather than silently retrying with another user's credentials;
- avoid a shared YouTube account; show the account-ban risk in product copy;
- keep PO Token provider plugins and yt-dlp on an independently deployable, frequently updated ingestion image;
- validate that the user owns or is authorized to process the source.

This is an architectural inference from yt-dlp's documented cookie/session behavior. It does not remove platform-policy or copyright risk. YouTube's API developer policies explicitly prohibit API clients from downloading, caching, storing, or modifying YouTube audiovisual content without prior written approval, and prohibit offline-download functionality outside YouTube Premium. See [YouTube API developer policies](https://developers.google.com/youtube/terms/developer-policies#e-handling-youtube-data-and-content) and the [official compliance guide](https://developers.google.com/youtube/terms/developer-policies-guide). Obtain product-specific legal review before making URL download a public SaaS feature.

## 2. Transcription and highlight-analysis APIs

### Current Clipah state

Clipah pins `assemblyai==0.42.0` and calls the older `SpeechModel.universal` interface. AssemblyAI's current Python SDK is `1.0.0`, released 2026-08-14. A controlled migration is required rather than a blind version bump. See the official [AssemblyAI PyPI package](https://pypi.org/project/assemblyai/).

Clipah sends highlight-analysis requests through OpenAI's SDK and Groq-compatible base URL using `meta-llama/llama-4-scout-17b-16e-instruct`. Groq shut that model down on 2026-07-17 and recommends `openai/gpt-oss-120b` or `qwen/qwen3.6-27b`. See [Groq model deprecations](https://console.groq.com/docs/deprecations).

### AssemblyAI: best migration path

AssemblyAI's current pre-recorded models are:

- `universal-3-pro`: highest-accuracy tier, native prompting/keyterms/code switching, but currently limited to English, Spanish, German, French, Portuguese, and Italian.
- `universal-2`: broad coverage across 99 languages, including Indonesian.

The API accepts `speech_models` as an ordered fallback list; AssemblyAI's own examples use `['universal-3-pro', 'universal-2']`. For Indonesian, Universal-2 will be the applicable model. Current prices published by AssemblyAI are $0.21/hour for Universal-3 Pro and $0.15/hour for Universal-2. See [AssemblyAI model selection](https://www.assemblyai.com/docs/getting-started/models).

Speaker diarization is supported on both models. Its result includes utterances plus per-word `start`, `end`, `confidence`, and `speaker`, which directly supports editable karaoke captions without retranscribing each generated clip. See [AssemblyAI speaker diarization](https://www.assemblyai.com/docs/pre-recorded-audio/label-speakers).

Recommended use:

- transcribe the source asset once;
- persist words, speaker labels, utterances, language, confidence, and provider/model version;
- slice this canonical transcript by timestamps for each clip;
- use a webhook or queue completion event rather than keeping a web request open;
- keep raw provider output for reproducibility while exposing a provider-neutral transcript schema internally.

### Groq: best migration path

Use Groq's native Python SDK rather than the OpenAI SDK with a substituted base URL. The current official `groq` SDK is `1.6.0` and is typed for Groq's API. See the official [Groq Python package](https://pypi.org/project/groq/).

For highlight extraction:

- default to `openai/gpt-oss-20b` for lower-cost candidate extraction and evaluate `openai/gpt-oss-120b` for final reranking/quality;
- request `response_format.type = 'json_schema'` with `strict: true`;
- make every schema field required and set `additionalProperties: false` as Groq requires;
- validate the response again with Pydantic and validate clip-domain invariants (`0 <= start < end <= duration`, overlap limits, minimum/maximum clip length);
- note that Groq strict structured output currently supports only `openai/gpt-oss-20b` and `openai/gpt-oss-120b`, and cannot be combined with streaming or tool use;
- store the model ID in configuration/database, not inline in `app.py`;
- maintain an eval set and subscribe/check the deprecation page before changing aliases.

Sources: [Groq structured outputs](https://console.groq.com/docs/structured-outputs), [Groq supported models](https://console.groq.com/docs/models), [Groq deprecation lifecycle](https://console.groq.com/docs/deprecations).

### Credible transcription alternatives

| Option | Relevant official capability | Clipah fit / constraint |
|---|---|---|
| Deepgram Nova-3 | Supports Indonesian, word times, batch/streaming, and speaker diarization. [Models/languages](https://developers.deepgram.com/docs/models-languages-overview/), [diarization](https://developers.deepgram.com/docs/diarization) | Strongest managed alternative to benchmark for Indonesian clips and caption timing. |
| OpenAI `gpt-transcribe` | Current high-accuracy file/Realtime transcription model with keyword hints, multiple language hints, and code-switching support; official pricing is $0.0045/minute. [Model page](https://developers.openai.com/api/docs/models/gpt-transcribe) | Current OpenAI non-diarized candidate to benchmark, especially for domain terms and multilingual input. |
| OpenAI `gpt-4o-transcribe` | OpenAI documents better WER/language recognition than original Whisper. [Model page](https://developers.openai.com/api/docs/models/gpt-4o-transcribe) | Credible high-accuracy managed transcription option. |
| OpenAI `gpt-4o-transcribe-diarize` | Produces speaker-labelled timestamped segments in the Transcription API. [Model page](https://developers.openai.com/api/docs/models/gpt-4o-transcribe-diarize) | Its diarized response does **not** support word timestamp granularities, so it is insufficient by itself for word-highlight/karaoke caption editing. See the [Audio API reference](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create). |
| WhisperX | BSD-2-Clause self-hosted pipeline using faster-whisper, wav2vec2 forced alignment, and pyannote speaker diarization. It provides word timestamps and can run on CPU or GPU. [Official repository](https://github.com/m-bain/whisperX) | Best for data control or sufficient sustained volume, but Clipah owns GPU scheduling, model downloads, observability, language-specific aligners, and quality. WhisperX itself warns that overlap and diarization remain imperfect; pyannote diarization requires a Hugging Face token and acceptance of its model agreement. |

Recommendation: create a 2–5 hour labelled evaluation corpus representing Indonesian, English, code-switching, music/noise, one/two/many speakers, and vertical-video source quality. Compare word error rate, word-timestamp drift, diarization error, p95 completion time, failure rate, and total cost. Retain AssemblyAI unless Deepgram or WhisperX shows a material measured advantage.

## 3. Open-source web video editor foundations

The relevant projects fall into different categories and should not be compared as interchangeable products.

| Project | Category | License / official facts | Clipah assessment |
|---|---|---|---|
| [OpenReel Video](https://github.com/Augani/openreel-video) | Full browser editor application | MIT. React/TypeScript, WebCodecs/WebGPU, Mediabunny, multi-track timeline, captions, undo/redo, local export. | Best full-app reference and possible source donor. A wholesale fork would import a very large product surface and local-first assumptions that differ from Clipah's server project/job model. |
| [Diffusion Studio Editor](https://github.com/diffusionstudio/editor) | Full web/desktop app plus AI-editing CLI | MPL-2.0. Solid/Vite UI, Electron app, CLI, JSX composition layer. | Interesting for agent-driven editing and generative compositions, but its UI stack does not align with Clipah's Next.js/React frontend. |
| [Elah](https://github.com/elahlabs/elah) | Reusable editor engine/SDK plus React bindings and headless CLI | Apache-2.0. Frame-based timeline, deterministic resolver, WebGL2/WebCodecs preview, React timeline/editor packages, server render CLI. | Architecturally the closest foundation for a custom Clipah editor. It is young, so validate seeking, mobile behavior, captions, long projects, proxy media, export parity, and maintainer risk in a spike. |
| [OpenVideo Editor](https://github.com/openvideodev/react-video-editor) | Next.js starter app/showcase and engine | Next.js, PixiJS/WebCodecs, timeline, canvas and local MP4 export. Dual license: free only for individuals, nonprofits, and organizations up to three employees; company license otherwise. | Fastest visual/product prototype, but licensing must be resolved before adoption. |
| [Remotion](https://github.com/remotion-dev/remotion) | Programmatic React renderer/player, not a complete NLE editor | Source available under a special license; a company license is required in some cases. Supports programmatic compositions, player, Node/Lambda/Vercel render paths. | Good renderer for template-heavy generated video, but it does not remove the need to build timeline state/UX, and licensing must be budgeted. |
| [Mediabunny](https://github.com/Vanilagy/mediabunny) | Low-level media toolkit | MPL-2.0. Pure TypeScript toolkit for demux/mux, trimming, conversion, WebCodecs, streaming I/O, browser and server. | Useful underneath preview/proxy/local export, but it is not an editor or timeline. |
| [ffmpeg.wasm](https://github.com/ffmpegwasm/ffmpeg.wasm) | Browser port of FFmpeg | JavaScript wrapper is MIT; underlying FFmpeg build has LGPL/GPL considerations. | Useful for small fallback transforms, but avoid making large/long video export depend on browser WASM. Native FFmpeg workers remain more predictable. |

### Build-versus-adopt recommendation

Do **not** build decoding, seeking, waveform, frame scheduling, timeline hit-testing, and muxing from zero. Also do **not** immediately fork an entire editor application.

Use a two-week proof of concept:

1. Define Clipah's own versioned composition/EDL JSON first: sources, tracks, trims, transforms/keyframes, caption words/styles, B-roll, overlays, audio, and render settings.
2. Test Elah as the embeddable timeline/preview foundation.
3. Test OpenReel's core/Mediabunny approach as the fallback/reference.
4. Require the same fixture composition to render both in browser preview and via Clipah's native FFmpeg worker; measure frame/timing parity.
5. Choose only after verifying Safari/mobile degradation, 30–60 minute proxy sources, memory use, waveform generation, undo/redo, autosave, caption editing, and license obligations.

The likely long-term shape is: Clipah-owned Next.js editor UI and composition schema, an adopted browser media/timeline engine, and a Clipah-owned FFmpeg render compiler. This preserves product differentiation without rebuilding commodity media primitives.

## 4. Automatic B-roll retrieval, generation, and placement

Automatic B-roll is a strong differentiator if it behaves like an assistant rather than silently rewriting the user's video. It should create editable timeline suggestions with provenance, confidence, and a one-click remove/replace action.

### Retrieval-first sources

- Pexels exposes topic, orientation, size, and locale filters through `GET /v1/videos/search`. It requires a prominent Pexels link, asks for photographer attribution where possible, and defaults to 200 requests/hour and 20,000/month. See the official [Pexels API documentation](https://www.pexels.com/api/documentation/).
- Pixabay exposes royalty-free video search with type/category/resolution/safesearch filters. Its API requires 24-hour result caching, disallows systematic mass downloads, and recommends storing selected videos on the application's server rather than permanent image hotlinking. See the official [Pixabay API documentation](https://pixabay.com/api/docs/).

Preserve provider, asset ID, source URL, author, license/terms snapshot, retrieval date, and attribution text on every imported B-roll asset. Do not assume “free” means provenance can be discarded.

### Generation options

- Runway's API supports asynchronous text-to-video, image-to-video, and video-to-video tasks through official Python/Node SDKs. See [Runway API reference](https://docs.dev.runwayml.com/api/) and [SDK task model](https://docs.dev.runwayml.com/api-details/sdks/).
- A provider aggregator such as fal exposes a uniform queued API across many video models; long-running jobs are designed for status checks/webhooks. See [fal video-generation API](https://fal.ai/docs/model-api-reference/video-generation-api/overview).
- Wan2.1 is an Apache-2.0 self-hosted suite supporting text-to-video, image-to-video, video editing, and related tasks. Its smaller T2V model is documented as requiring roughly 8.19 GB VRAM and taking about four minutes for a five-second 480p clip on an RTX 4090. See the official [Wan2.1 repository](https://github.com/Wan-Video/Wan2.1).
- Do not start a new integration with the OpenAI Sora API: official OpenAI documentation marks the API deprecated and scheduled to shut down permanently on 2026-09-24. See the [OpenAI video API reference](https://developers.openai.com/api/reference/typescript/resources/videos/methods/create).

### Recommended B-roll pipeline

1. Segment the canonical transcript into semantic beats, preserving exact word timestamps and speaker turns.
2. Ask the highlight model for a concrete visual intent, search terms, entities, setting, mood, and exclusions for each eligible beat using strict structured output.
3. Retrieve portrait-compatible stock candidates first. Apply safe-search, duration, source, and licensing filters.
4. Sample thumbnails/keyframes and rerank candidates against the visual intent using a vision model or a text-image embedding model. OpenAI's CLIP reference implementation uses aligned text/image encoders, though its own model card advises deployment-specific evaluation. See the [CLIP model card](https://github.com/openai/CLIP/blob/main/model-card.md).
5. Apply deterministic placement rules: avoid the hook's first face reveal, never cut across a critical on-screen demonstration, respect sentence/scene boundaries, use minimum/maximum shot durations, cap total B-roll coverage, and prefer gaps in speaker emphasis. PySceneDetect can provide content/adaptive scene boundaries and is BSD-3-Clause. See [PySceneDetect's official detection guide](https://github.com/Breakthrough/PySceneDetect/blob/main/website/pages/cli.md).
6. Insert selected media as B-roll track items in the composition JSON. Keep original dialogue audio unless explicitly changed; support crop, motion, transitions, and ducking as separate editable properties.
7. Show confidence, provenance, and alternatives in the editor. Let the user accept all, accept one, replace, regenerate, or remove.
8. Use generation only when stock retrieval is below a configured relevance threshold or the user requests a custom visual. Put generation behind budget, concurrency, moderation, timeout, and per-project quota controls.
9. Normalize/proxy accepted assets once, then render them with the same immutable edit version as the main clip. FFmpeg's filter graph supports normal overlay/compositing for the final worker render. See [FFmpeg's official overlay example](https://ffmpeg.org/ffmpeg.html#Complex-filtergraphs).

### Product risks and quality gates

- Generated B-roll can introduce factual hallucinations, inconsistent people/brands, misleading reenactments, visual artifacts, and much higher latency/cost than stock retrieval.
- Stock results can be semantically generic, repetitive, culturally mismatched, or poorly framed for 9:16.
- Automatic placement can reduce retention when it interrupts a face, punchline, demonstration, or emotionally important pause.

Therefore store `source_type`, provider/model/version, prompt/query, seed when available, provenance, moderation result, relevance score, placement reason, and user decision. Measure acceptance/removal rate, replacement rate, watch-through impact, generation cost per exported minute, and render failures. The best first release is “Suggest B-roll” with a coverage slider (`minimal`, `balanced`, `dynamic`), not unconditional automatic insertion.

## 5. Immediate migration priorities

1. Replace the retired Groq Llama 4 Scout ID and add strict schema output plus Pydantic/domain validation.
2. Migrate AssemblyAI SDK/configuration, enable real diarization, and persist the single canonical word-level transcript.
3. Update yt-dlp into its own frequently deployable ingestion worker with EJS/Deno and optional PO Token provider support; remove the global cookie file.
4. Add a direct-upload-first ingestion policy and a deliberate authorization/legal gate around YouTube import.
5. Run the Elah/OpenReel editor spike before committing to an editor engine.
6. Ship stock B-roll suggestions before generative B-roll; make all placements editable and provenance-aware.
