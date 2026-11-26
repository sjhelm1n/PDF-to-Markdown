# PDF to Markdown Product Specification

## Document Status

This file is the project-level product and architecture specification. It records Phase 1, the application-level P1 controls, and the remaining MVP requirements.

Status terms used below:

- **Implemented**: present in the inspected repository.
- **Partial**: a useful subset exists, but it does not meet the complete requirement.
- **Planned**: not present in the inspected repository.

An item marked implemented is based on source and configuration inspection, not proof that it has been exercised on every target platform.

## Product Summary

PDF to Markdown is an open-source, self-hosted application for converting PDF maintenance documents into reviewable Markdown and lossless PNG figure assets. Its primary workflow prepares a faithful, human-reviewed export for manual ingestion into an Open WebUI RAG knowledge base.

The application is licensed under Apache License 2.0. It operates without a database, without cloud inference, and without direct Open WebUI integration. It must preserve original technical content and provenance rather than generate new interpretations, summaries, captions, or procedural guidance.

The primary deployment target is Docker Standalone on NVIDIA DGX OS, managed through Portainer on DGX Spark. The Spark has a GB10 Grace Blackwell Superchip, a 20-core Arm CPU, a Blackwell GPU with 6,144 CUDA cores, and 128 GB of unified LPDDR5X memory. Linux reports the architecture as AArch64 and Docker uses `linux/arm64`. The design retains a CPU path for compatible ARM64 and AMD64 hosts. [NVIDIA DGX Spark hardware](https://docs.nvidia.com/dgx/dgx-spark/hardware.html) · [NVIDIA DGX Spark software](https://docs.nvidia.com/dgx/dgx-spark/software.html)

## Product Principles

1. **Source fidelity over enrichment.** Preserve source text, structure, figures, labels, warnings, identifiers, and provenance. Unknown information stays unknown.
2. **Human review before ingestion.** The user can compare the PDF and Markdown, edit the Markdown, render the current draft, and explicitly export it.
3. **Local and private operation.** Conversion engines run locally and remote services are disabled. Document content is not sent to cloud inference or telemetry services.
4. **Pluggable conversion.** Docling is the initial engine. Nemotron 3.5 Parse and later engines use adapters behind a stable job and artifact contract.
5. **Portable, filesystem-based deployment.** Per-job files and JSON metadata replace a database. Containers avoid unnecessary hardware coupling.
6. **Deterministic artifacts.** Given the same source PDF and upload filename, engine/version, configuration, and application version, artifact naming, ordering, references, and metadata are reproducible.
7. **Minimal dependencies.** Add software only when it supplies a necessary capability that is not reasonably provided by the existing stack.

## Current Implementation

### Repository Map

```text
.
├── frontend/               React/TypeScript/Vite SPA and Nginx image
│   ├── src/                Application UI, engine discovery hook, and stylesheet
│   ├── Dockerfile          Node build stage and Nginx runtime stage
│   ├── nginx.conf          SPA fallback and /api reverse proxy
│   └── package.json        Frontend scripts and dependencies
├── backend/                Python/FastAPI conversion service
│   ├── app/main.py         API, filesystem jobs, worker, and ZIP export
│   ├── app/engines/        Shared contract, engine registry, and adapters
│   ├── tests/              Engine, Docling adapter, and FastAPI regression tests
│   ├── Dockerfile          Python 3.12 service image
│   └── requirements.txt    FastAPI, Uvicorn, multipart, and Docling constraints
├── compose.yaml            CPU deployment, resource settings, and persistent volumes
├── compose.gpu.yaml        Optional CUDA build and API GPU reservation
├── docs/dgx-spark.md       Portainer deployment and on-device validation runbook
├── README.md               Setup, deployment guidance, and current limitations
├── LICENSE                 Apache License 2.0
└── NOTICE                  Project and third-party licensing notice
```

This is a two-service application, not a monorepo with independently versioned packages. The frontend and backend communicate through `/api`. Nginx proxies production traffic to FastAPI; Vite supplies an equivalent development proxy.

### Current Runtime Flow

1. The browser accepts a PDF by file picker or drag-and-drop and creates a local object URL for the PDF preview.
2. The SPA queries `GET /api/engines`, selects an available engine, posts the PDF to `POST /api/jobs?engine=<id>`, and polls the returned job ID.
3. FastAPI admits a bounded number of uploads and queued jobs, checks the streamed HTTP body before parsing, and saves the PDF under a UUID directory in `APP_DATA`. It validates the signature, file size, encryption state and page count before scheduling work on the single conversion worker.
4. The worker invokes the selected adapter. Docling uses local conversion with remote services disabled, referenced picture images enabled, and an image scale of `3.0`.
5. The worker stores full Markdown, per-page Markdown, PNG files, a basic image index, and JSON job status on disk.
6. The SPA shows a paged PDF and per-page or full Markdown. The source editor follows the PDF page when the page excerpts reconstruct the full Markdown exactly.
7. The user can save edits, then download a ZIP containing `manual.md`, `image-index.json`, and `images/*.png`.

### Implementation Status

| Capability | Status | Repository evidence and gap |
| --- | --- | --- |
| Apache 2.0 open-source project | Implemented | `LICENSE` contains Apache 2.0 and `NOTICE` identifies the project. Third-party/model licenses remain separate. |
| React/Vite SPA | Implemented | React 19, strict TypeScript, Vite, production Nginx container, and SPA fallback are configured. |
| Drag-and-drop PDF upload | Implemented | File picker and drop target exist. The API bounds the multipart body and permits up to 100 MiB and 25 pages per PDF by default. Invalid or encrypted PDFs are rejected before inference. |
| Admission, storage and retention | Implemented at application level | One conversion at a time, a bounded pending queue, a retained-file budget, expiry of terminal jobs and interrupted-state recovery. Native parser isolation and hard runtime limits are pending. |
| Selectable conversion engines | Implemented | The SPA populates its selector from `GET /api/engines` and disables unavailable options. FastAPI checks availability again at submission. |
| Docling conversion | Implemented | Docling 2.151.0 converted a real USGS PDF on the Intel Mac in CPU mode. Automated checks verified ZIP image links and PNG bytes. Markdown omits recognized running headers and footers, matching margin copies, and page counters verified against the PDF pagination. |
| Modular adapter layer | Implemented | `backend/app/engines/` defines the conversion result, capabilities, configuration, typed errors, and registry. Job routes and the worker use the shared contract. |
| Nemotron 3.5 Parse | Partial | The registry advertises a disabled placeholder. Docling has a built-in local VLM profile. Runtime compatibility and export fidelity are unqualified. See [the architecture decision](#nemotron-35-parse-architecture-decision). |
| Side-by-side PDF and Markdown | Implemented | The divider supports pointer and keyboard resizing, remembers the split in the tab, and is hidden when the panes stack. |
| Synchronized scrolling | Implemented at page level | Both panes switch the current source page. Linked previews synchronize approximate positions within the page, with a full-document fallback when the page mapping is unavailable. Element alignment is planned. |
| Editable and rendered Markdown | Partial | Linked navigation drives the editor and current draft preview when the page excerpts reconstruct the original full Markdown exactly. Saves include the full draft. When the mapping cannot support page edits, the editor opens the full document and Preview shows the full draft after changes. |
| Unsaved-change protection | Implemented with browser limits | Replacement and reconversion confirm before discarding work. The browser exit guard runs while a draft is dirty or saving. The active tab restores the PDF and last saved Markdown. |
| Referenced PNG extraction | Implemented with limitations | Docling writes referenced PNG assets once and uses the same paths in full and per-page Markdown. PNG encoding preserves extracted pixels. Source JPEG loss and vector rasterization cannot be reversed. |
| Original figure references | Implemented with limitations | Docling places associated captions from same-page positions where available. Figure metadata records the associated source caption and page when verified. Unknown associations are kept as `null`. |
| Source-page metadata | Implemented at page level | Full Markdown includes source-page anchors when the ordered page excerpts reproduce the full document. The figure index uses verified one-based pages and bounding boxes where available. |
| Deterministic image indexing | Implemented | Source-prefixed PNG paths carry stable sequential figure IDs. The image index records source order, image dimensions, byte counts, and SHA-256. |
| ZIP export with Markdown, PNG, and metadata | Implemented | ZIPs contain saved Markdown, a manifest, an image index, and its referenced PNG files. The exporter verifies local image paths and checksums before writing the archive. |
| Manual Open WebUI RAG workflow | Implemented by boundary | Export is manual and there is no Open WebUI API integration. The starter correctly warns that Markdown upload alone does not guarantee image resolution. |
| No database | Implemented | Job state and artifacts are filesystem files and JSON. |
| No cloud inference | Implemented in Docling configuration | `enable_remote_services` is false. Initial model artifact download may still require network preparation and is not remote inference. |
| Docker Compose deployment | Partial | CPU Compose and an optional NVIDIA GPU override provide persistent job/cache volumes, a configurable web port, and API resource settings. Native production image builds, CUDA-backed Docling, and Portainer operation on DGX Spark require target validation. |
| Automated tests | Partial | Backend and browser suites cover the real CPU Docling workflow and corrupt input, alongside fixture-based engine regressions. Deployment verification and coverage for remaining MVP review behavior are pending. |

### Phase 1 Engine Discovery and Compatibility

- `GET /api/engines` returns `default_engine` and `engines`. Each engine entry includes its ID, display name, adapter and runtime versions, enablement, availability, unavailable reason, and capabilities for Markdown, page Markdown, PNG output, and figure provenance.
- The SPA prefers the API default when available and falls back to the first available engine. Existing API callers may omit the `engine` query parameter to request Docling.
- `PDF2MD_ENGINES` is an optional comma-separated allowlist with registered IDs `docling` and `nemotron`. It defaults to both IDs when unset. An empty value disables both, and an unknown ID fails startup. A disabled entry stays visible in the catalog with a reason.
- Discovery checks local runtime imports without loading models or downloading weights. Model initialization occurs during conversion and can fail if the required assets or hardware are unavailable. Nemotron reports unavailable even when its ID is enabled.
- Job submission returns HTTP 422 with `unknown_engine` for an unknown ID and HTTP 503 with `engine_unavailable` for an unavailable adapter. Background conversion errors include a stable `error_code` and a client-safe message.
- Jobs persist the selected engine ID, effective options, and adapter version. Successful jobs include runtime version and warnings. The existing Markdown, page preview, asset API, and ZIP layout remain compatible.
- Phase 1 passed 11 automated tests and the TypeScript/Vite production build. A browser smoke test exercised discovery, unavailable options, PDF upload and preview, fixture conversion, and Markdown edits. The export endpoint returned a valid ZIP through the Vite proxy with the saved Markdown, image index, and referenced PNG. The existing Docling dependency could not resolve a compatible PyTorch distribution on the Intel macOS/Python 3.13 test host. Real model conversion and DGX Spark/Portainer verification remain pending.

### Local CPU validation

The subsequent Intel Mac validation passed 15 backend tests, including two real CPU Docling tests, and 3 browser tests. TypeScript and the production build passed. The core upload-to-ZIP workflow passed, with seven PNGs extracted from a public two-page PDF. Provenance, synchronized review, and draft protection leave local acceptance open. [Local validation report](docs/local-validation.md)

## Users and Primary Workflow

The primary user is a maintainer, technical writer, or RAG curator preparing maintenance manuals for controlled ingestion.

1. Open the local application.
2. Drop or select one PDF.
3. Select an available local conversion engine and review its availability/capabilities.
4. Start conversion and observe queued, running, complete, interrupted, or failed status.
5. Compare the original PDF with converted Markdown and source figures using resizable panes and synchronized navigation/scrolling.
6. Edit the Markdown while viewing a rendering of the current draft.
7. Review fidelity, source-page markers, images, and warnings. The application does not supply technical interpretations.
8. Save the draft and download a deterministic ZIP.
9. Manually ingest the reviewed files into the operator's Open WebUI RAG workflow outside this application.

## MVP Scope

### Included

- Single-PDF upload by picker and drag-and-drop, with bounded streaming validation.
- Runtime discovery and selection of locally available engines.
- A reusable adapter contract and registry, with Docling as the first production adapter.
- Local, source-faithful Markdown conversion with page/provenance metadata.
- Referenced PNG extraction with deterministic IDs, filenames, order, and integrity metadata.
- Resizable side-by-side PDF and Markdown review panes.
- Toggleable synchronized scrolling/navigation based on source provenance, with a safe fallback to page-level alignment.
- Full Markdown editing plus a rendered view of the current draft.
- Persistent draft save and comprehensive unsaved-change protection in the SPA.
- Deterministic ZIP export containing Markdown, PNG images, an image index, and document metadata.
- Filesystem job storage, bounded local background conversion, clear status/error states, and recovery marking after service restart.
- Docker Compose deployment suitable for Portainer and validated on DGX Spark ARM64, while retaining compatible CPU and other-host operation.
- User and developer documentation, automated tests, and Apache 2.0 compliance.

### Excluded

- Databases, distributed queues, or a multi-node control plane.
- Direct Open WebUI upload, indexing, API calls, or knowledge-base management.
- Cloud conversion or cloud/hosted AI inference.
- AI-generated summaries, captions, descriptions, repair, translation, question generation, or interpretation of technical content.
- Multi-document batch orchestration for the MVP.
- Collaborative editing, accounts, roles, long-term document library management, or general internet-facing hosting.
- Guaranteed pixel equivalence to lossy source images or non-raster PDF graphics; the guarantee is no additional lossy PNG encoding after extraction/rasterization.

Nemotron 3.5 Parse is the next candidate engine. Its implementation is outside the initial Docling MVP. The same registry and adapter contract must support both.

## Target Architecture

### Frontend

The SPA remains React, strict TypeScript, and Vite. It should be decomposed by responsibility as behavior grows:

- upload/file selection and validation;
- engine discovery and selection;
- job submission, polling, and failure recovery;
- PDF viewer and source-position reporting;
- resizable review workspace and synchronization controller;
- Markdown draft editor and renderer;
- save/export actions and unsaved-change guard.

API types and transport logic should be separated from view components. Server state must not overwrite an active draft. PDF object URLs, timers, observers, and navigation guards require explicit cleanup.

### Backend

FastAPI remains the HTTP boundary, with route handlers delegating to focused modules:

```text
API routes
  -> job/conversion service
      -> engine registry
          -> Docling adapter
          -> Nemotron 3.5 Parse adapter (disabled, planned local Docling VLM profile)
          -> future adapters
      -> filesystem job store
      -> artifact normalizer/validator
      -> ZIP exporter
```

The service remains database-free. A bounded local worker may be in-process for the MVP. State writes are atomic, job IDs are UUIDs, and all resolved paths must remain under the job root.

### Engine Adapter Contract

Each adapter supplies:

- stable `id`, display name, adapter version, engine version, and availability detail;
- supported input/output capabilities and local hardware requirements;
- a conversion method accepting a local input PDF, isolated output workspace, optional source basename, and explicit options;
- normalized Markdown, source-page/provenance records, image records, warnings, and reproducibility metadata;
- deterministic source traversal order and artifact naming;
- local-only execution with remote services disabled;
- typed failure states that do not leak sensitive source content.

API routes validate engines through a registry. The frontend populates the selector from registry output and disables unavailable engines with an explanation. Engine-specific options, if added, are namespaced and recorded in export metadata.

### Nemotron 3.5 Parse architecture decision

**Use two selectable profiles in the existing worker.** Keep `docling` as the default and leave its standard `PdfPipelineOptions` path intact. The future `nemotron` adapter should select Docling's `VlmPipeline` and `nemotron_parse_v3_5` preset with in-process Transformers. This uses one FastAPI service and the same job submission, polling, editor, asset routes, and ZIP export. The installed Docling 2.151.0 package contains the preset, which establishes configuration support. The only model-backed result verified in this project is the standard CPU workflow. Keep the Nemotron choice disabled until qualified. [Docling model catalog](https://docling-project.github.io/docling/usage/model_catalog/#vlm-convert-stage) · [Local validation report](docs/local-validation.md)

Keep both profiles behind `ConversionEngine`. Share the Docling-specific mapping from `DoclingDocument` to `ConversionResult` where it preserves each pipeline's output. The worker already accepts full Markdown, numbered page Markdown, relative PNG asset records, warnings, and an engine version. Full block provenance, richer figure records, and model identity need additive, framework-neutral metadata under the existing target export contract. Record the adapter and Docling versions, the preset, model repository and revision, and effective runtime and conversion options. Future frameworks can implement the same contract with their own internal document type. A request for an unavailable Nemotron profile should return the existing unavailable error so the user can choose standard Docling explicitly.

#### Output fidelity and the shared result

| Output | Standard Docling | Nemotron through Docling |
| --- | --- | --- |
| Markdown and page structure | Uses PDF text, OCR, layout detection, and table structure recognition. Full and per-page Markdown passed local testing. | Parses page images into predicted text, classes, and boxes. Docling maps native output into document items such as headings, lists, and tables. Validate reading order, table spans, cross-page structure, values, units, and warnings against the source. |
| PNGs and relative links | The current adapter uses referenced export and prefers embedded images where supported. The local test verified seven PNGs and their links. | The VLM pipeline can crop predicted picture regions from rendered source pages. Use referenced export after checking that each crop and link is valid. PNG encoding preserves the crop's pixels. Original embedded-image fidelity and complete figure detection need separate tests. |
| Source-page and figure provenance | The adapter retains per-page Markdown but leaves figure provenance null. | The native mapper attaches page numbers and predicted boxes to document items. Validate coordinates against PDF page frames, including rotation and scaling. Retain only reliable associations and use null where a figure or caption cannot be verified. |
| Metadata and completeness | Job status records the engine version and warnings. The current ZIP index lacks the full provenance and checksum fields. | Docling documents page-level parsing diagnostics, including request counts. Carry useful diagnostics into normalized metadata. Detect partial or truncated conversion and withhold normal success and export when required pages are incomplete. |

References for the comparison include the upstream model's native block format, Docling's preset behaviors, and the VLM options for rendering picture assets. [NVIDIA model card](https://huggingface.co/nvidia/Nemotron-3.5-Parse#tables-and-postprocessing) · [Docling VLM options](https://docling-project.github.io/docling/reference/pipeline_options/#docling.datamodel.pipeline_options.VlmPipelineOptions) · [Docling model catalog](https://docling-project.github.io/docling/usage/model_catalog/#vlm-convert-stage)

Treat generated text, caption associations, and chart values as extraction candidates. Preserve visible source labels and captions, and keep source image crops for charts. Keep picture description and generated interpretations out of the export. Check whether the preset's chart data or picture text enters Markdown by default and exclude unverified fields. Start with single-pass parsing. Additional passes can add table and chart crop requests, and their merge behavior and JPEG inference inputs need separate fidelity and cost checks. [Docling model catalog](https://docling-project.github.io/docling/usage/model_catalog/#vlm-convert-stage) · [NVIDIA parsing modes](https://huggingface.co/nvidia/Nemotron-3.5-Parse#single-double-or-triple-pass)

#### Runtime on DGX Spark

- **Packages and device.** Standard Docling already uses the PDF parser and its OCR, layout, and table models. The optional profile adds the Nemotron model and a compatible inline VLM stack, including Transformers, torchvision, and timm as required by NVIDIA. The locally inspected Docling 2.151.0 preset specifies Transformers 5.14.1 or newer and BF16, subject to the selected Docling release's dependency constraints. Check the exact installed preset and resolve the packages together in a candidate `linux/arm64` API image. Start with the existing CUDA 13.0 PyTorch and torchvision pins, NVIDIA Container Toolkit, and explicit CUDA device selection. Verify real BF16 inference on GB10 compute capability 12.1. NVIDIA's published H100 and H200 results do not establish Spark compatibility or throughput. [NVIDIA Transformers example and hardware](https://huggingface.co/nvidia/Nemotron-3.5-Parse#transformers) · [NVIDIA compute capabilities](https://developer.nvidia.com/cuda/gpus) · [DGX Spark runbook](docs/dgx-spark.md)
- **Custom model code and offline operation.** Transformers requires explicit `trust_remote_code` for this preset. It executes the checkpoint's Python code inside the worker. Review the exact approved snapshot before allowing it, stage its files in the existing model cache, keep `enable_remote_services` false, and prove the conversion can run with network egress blocked. The inspected preset points to a pinned NVIDIA snapshot. Its model weights are under OpenMDW 1.1, and the tokenizer has separate MIT terms and attribution. [Docling model catalog](https://docling-project.github.io/docling/usage/model_catalog/#vlm-convert-stage) · [Pinned NVIDIA snapshot](https://huggingface.co/nvidia/Nemotron-3.5-Parse/tree/e7ea79242a87ea0ad3919b672bfedb429a683206) · [NVIDIA license terms](https://huggingface.co/nvidia/Nemotron-3.5-Parse#licenseterms-of-use)
- **Capacity.** The model is about 0.6B parameters. Weight size alone excludes page rasters, activations, decoder cache, and temporary copies. Standard Docling and Nemotron use different inference workloads, so measure cold start, warm time per page, peak process and CUDA memory, and sustained jobs with Ollama active. Start with one conversion worker and test whether the current API container limit and shared-memory headroom are sufficient before raising limits or concurrency. Keep the CPU bundle usable without the optional model. [NVIDIA model card](https://huggingface.co/nvidia/Nemotron-3.5-Parse#model-architecture) · [DGX Spark hardware](https://docs.nvidia.com/dgx/dgx-spark/hardware.html)

#### When a dedicated service would help

A dedicated vLLM runtime could keep the model loaded, batch concurrent requests, use NVIDIA's optional speculative decoder, and isolate model dependencies or crashes from the API process. Those gains are unmeasured on the Spark. A service would add image transfer over an API, separate lifecycle and resource controls, and another ARM64/CUDA compatibility check. Docling documents an API-backed route for this preset and excludes in-process vLLM. NVIDIA's supported serving path would need separate validation for the chosen package versions. For the current single-worker application, defer that option unless measurements or a code-isolation requirement justify it. It would still need the same source-fidelity and export validation. [Docling model catalog](https://docling-project.github.io/docling/usage/model_catalog/#vlm-convert-stage) · [NVIDIA serving and decoding notes](https://huggingface.co/nvidia/Nemotron-3.5-Parse#vllm)

#### Qualification gates and open questions

1. Validate standard Docling with the same representative PDFs on the Spark after local end-to-end acceptance. Record native CPU and CUDA behavior separately. [Local validation report](docs/local-validation.md) · [Spark runbook](docs/dgx-spark.md)
2. In a separate future qualification, determine which exact Docling, Transformers, Torch, torchvision, and model snapshot work together with native ARM64 and CUDA BF16. Check that the approved snapshot runs offline, that missing prerequisites produce a clear unavailable reason, and that standard Docling still works in both API builds.
3. Compare both profiles on the same native and scanned PDFs with dense technical text, tables, line drawings, and raster figures. Check Markdown fidelity, page mappings, crop accuracy, figure links and hashes, repeatability, and behavior on partial or malformed inputs. Decide whether parity is good enough for the same advertised capabilities. Report measured latency and memory before enabling Nemotron or reconsidering a dedicated service.

### Per-Job Filesystem Contract

A completed job should have a normalized layout equivalent to:

```text
<job-id>/
├── input.pdf                 retained in the private job workspace, not exported by default
├── status.json               job state and timestamps
├── output.md                 current saved Markdown draft
├── pages.json                normalized page/provenance anchors
├── metadata.json             original Markdown checksum and save baseline used by the ZIP manifest
├── image-index.json          deterministic figure inventory and provenance
└── images/
    ├── Pump-Service-Manual-fig-000001.png
    └── Pump-Service-Manual-fig-000002.png
```

Engine-native temporary output may exist in an isolated subdirectory, but the API and ZIP use only normalized artifacts.

### Provenance and Image Contract

- Markdown contains non-rendering source-page anchors such as `<!-- source-page: 12 -->` at verified page transitions. More precise block mappings live in `pages.json` rather than inventing visible content.
- Each normalized block mapping identifies its source page and, when supplied by the engine, its PDF bounding box and reading order. Unavailable fields are `null`.
- Figures receive sequential IDs (`fig-000001`, `fig-000002`, and so on) based on stable source traversal order. PNG filenames combine the normalized source PDF stem and the figure ID, for example `Pump-Service-Manual-fig-000001.png`. The prefix preserves case and Unicode letters and digits, replaces spaces and unsafe punctuation with hyphens, and fits within 100 UTF-8 bytes. The fallback prefix is `document`.
- `image-index.json` records ID, relative path, source page, source order, verified source label/caption when present, bounding box when available, dimensions, byte size, and SHA-256 checksum.
- Markdown image references use the exact normalized relative paths. Original source labels, captions, and cross-references are preserved when extracted; none are inferred.
- The same PDF, upload filename, engine/version, adapter/version, and options must produce the same normalized order, paths, and metadata except for explicitly documented volatile timestamps/job IDs.
- PNG output uses lossless encoding. Embedded lossy pixels remain inherently lossy, and vector/composite figures are rasterized at a documented scale. No generative enhancement is allowed.

### Export Contract

The downloaded archive is named `converted-document.zip` and contains at least:

```text
converted-document.zip
├── manual.md
├── metadata.json
├── image-index.json
└── images/
    └── Pump-Service-Manual-fig-*.png
```

Archive entries are emitted in deterministic lexical order with fixed safe relative paths. `metadata.json` records the source filename, source PDF checksum, conversion and save timestamps, selected engine and versions, normalized options, application version, page/image counts, warnings, whether the Markdown was user-edited, and artifact checksums. It must not contain host paths, secrets, generated interpretations, or the source PDF unless a later explicit product decision adds it.

## Functional Requirements

### FR-1: Upload and Validation

- The SPA accepts one PDF through drag-and-drop or a labeled file picker.
- Replacing an active document requires confirmation when a draft is unsaved.
- The API streams the upload, applies a configurable byte limit, validates expected PDF characteristics, sanitizes the display filename, and cleans up rejected jobs.
- Invalid input, oversized files, and service errors produce actionable messages without exposing tracebacks or host paths.

### FR-2: Engine Discovery and Selection

- The backend lists registered engines with stable IDs, names, versions, capability flags, availability, and unavailable reasons.
- The UI lets the user select any available engine and never presents an unavailable engine as usable.
- The submitted engine and options are stored with the job and exported metadata.
- Docling is the default initial engine. Nemotron 3.5 Parse and future engines use the same endpoints and normalized result shape.

### FR-3: Conversion Jobs

- Job states include at least queued, running, complete, failed, and interrupted.
- Conversion work is bounded and does not block the async request loop.
- A restart marks unfinished in-process jobs interrupted unless explicit safe resumption exists.
- Failures retain enough local diagnostic context for an operator while returning concise safe errors to the browser.
- Conversion never calls a remote inference service.

### FR-4: Faithful Markdown

- Output preserves source technical text, headings, lists, tables, warnings, identifiers, units, captions, and references as the engine can extract them.
- Exclude verified running headers and footers from Markdown. Preserve technical footnotes and body text when the margin classification is uncertain.
- Verified source-page anchors and normalized provenance are included without changing visible technical meaning.
- The application does not summarize, interpret, translate, explain, or invent content.
- Converter uncertainty or missing provenance is represented as metadata/warnings, not repaired with generated claims.

### FR-5: Figure Extraction

- All figures referenced by normalized Markdown are available as PNG assets through safe relative paths.
- PNG files use lossless encoding after extraction/rasterization and deterministic IDs/names.
- Original figure labels, captions, and references are retained only when source-grounded.
- The image index contains provenance and integrity fields and explicitly uses `null` when an engine cannot verify a value.
- Asset serving blocks traversal, unsupported extensions, and remote-image passthrough.

### FR-6: Review Workspace

- PDF and Markdown appear side by side on suitable screens and stack accessibly on narrow screens.
- A visible divider resizes the panes by pointer and keyboard, enforces usable minimum sizes, and preserves the current session's preference.
- A synchronization toggle links PDF and Markdown positions bidirectionally using the best verified provenance available.
- Synchronization falls back to source-page alignment when element coordinates are unavailable and communicates that reduced precision.
- Manual page controls remain available and do not destroy scroll position or draft content.

### FR-7: Editing, Rendering, and Draft Safety

- The user edits the complete Markdown source and can inspect a rendered view of the current draft without saving first.
- Rendered local image references resolve through the validated asset endpoint; remote and unsafe image sources are blocked.
- Save writes the complete draft atomically and reports success or failure.
- The application tracks clean, dirty, and saving/error states accurately.
- Dirty drafts are protected during replacement, reconversion, job changes, app navigation, refresh, and tab/window close.
- Provenance invalidated by user edits is not silently regenerated or guessed; retained anchors and edit status are reflected in metadata.

### FR-8: Export

- Export is enabled only for a complete job with a saved draft.
- The ZIP contains `manual.md`, `metadata.json`, `image-index.json`, and all referenced normalized PNG files.
- The exporter verifies that local Markdown image references have corresponding safe archive entries and reports integrity problems.
- Archive ordering, filenames, image indexing, and checksums are deterministic under the reproducibility contract.

### FR-9: Local Deployment and Operations

- Docker Compose starts the frontend, backend, persistent job volume, and local model cache needed by the enabled adapter.
- Nginx serves the SPA, applies the upload limit, preserves SPA fallback, and proxies API requests with a conversion-appropriate timeout.
- Both production images must build and run natively as `linux/arm64`. Publish or retain an AMD64 CPU build where dependencies allow.
- Portainer deploys a dedicated Docker Standalone stack using prebuilt images. Keep the API internal and select a free web host port on the DGX Spark already running Portainer, Open WebUI, and Ollama.
- Preserve per-stack application data and model cache volumes across container recreation.
- The optional GPU deployment uses NVIDIA Container Toolkit and an explicit CUDA PyTorch build. The GB10 reports compute capability 12.1. Qualify the exact image, host driver, and Docling model workload on the Spark before declaring GPU support. [NVIDIA compute capability table](https://developer.nvidia.com/cuda/gpus)
- Keep CPU mode usable without NVIDIA device access. Follow the [DGX Spark deployment runbook](docs/dgx-spark.md) for the configuration and validation commands.

## Nonfunctional Requirements

### Fidelity and Determinism

- No application feature may introduce AI-authored technical interpretation.
- Normalized output is reproducible for fixed input, versions, and options.
- Source and artifact checksums make integrity review possible.
- User edits are preserved byte-for-byte as UTF-8 Markdown except for an explicitly documented newline policy.

### Privacy and Security

- Documents and outputs remain on the deployed host and in the user's browser session.
- Remote inference and remote Markdown images are disabled.
- Inputs, IDs, paths, sizes, and archive names are validated; writes are confined to the job workspace and performed atomically.
- The MVP is for a trusted local network. Broader exposure requires authentication, rate limiting, malware scanning, HTTPS termination, and hardened isolation. Job retention and application-level request and storage budgets are in place.

### Performance and Reliability

- Upload and download handling is streamed or bounded to avoid unbounded memory use. ZIP generation should become streaming if realistic document sizes make the current in-memory approach unsafe.
- Use one API process and one simultaneous conversion. Pending-job count, Docling CPU threads, and existing Compose CPU and memory settings are configurable. CPU and GPU draw from the Spark's shared memory, and container memory settings do not establish a separate GPU memory budget.
- Expensive engine/model initialization may be reused safely, without contaminating output across jobs.
- Polling, restart recovery, and failures converge on clear terminal or recoverable states.
- Large documents remain reviewable through pagination/virtualization or equivalent bounded rendering.

### Portability

- Container images and dependencies support Linux ARM64 for DGX Spark and should support Linux AMD64 where upstream engines permit.
- GPU configuration is optional and isolated from the generic Compose contract.
- Hardware-specific paths, secrets, and hostnames are configuration, never source constants.
- A missing accelerator produces a clear availability/fallback state rather than corrupt output.

### Accessibility and Usability

- Core upload, conversion, review, editing, resizing, save, and export flows are keyboard accessible.
- Controls have programmatic labels; status and errors are announced appropriately; focus and contrast are visible.
- The UI communicates linked-scroll precision, unsaved state, engine availability, conversion progress, and fidelity warnings without requiring developer tools.

### Maintainability and Licensing

- Engine-specific logic is contained in adapters and exercised by contract tests.
- Frontend, backend, and export contracts use explicit types/schemas and compatible versioning.
- Dependencies are minimal, justified, license-reviewed, and version-controlled reproducibly.
- Original project work remains Apache 2.0; third-party packages and model weights retain and document their own licenses.
- Architecture, operations, configuration, known limitations, and verification results stay current in `PROJECT.md` and `README.md`.

## Validation Strategy

The target test suite includes:

- unit tests for adapter normalization, stable figure IDs, checksums, page anchors, state transitions, path confinement, and deterministic archives;
- FastAPI tests for upload constraints, engine validation, job polling, edits, asset responses, failures/interruption, and exact ZIP inventory;
- frontend tests for drag-and-drop, runtime engine options, status polling, source/render modes, current-draft rendering, pane resizing, scroll synchronization, and every dirty-draft exit path;
- golden conversion fixtures that are small, redistributable, and cover text, headings, tables, warnings, multi-page structure, embedded raster images, and vector figures;
- end-to-end tests from browser upload through archive inspection, asserting no unexpected network destination is contacted;
- container smoke tests for reverse proxy, persistence, restart recovery, and CPU mode;
- a documented DGX Spark ARM64/Portainer run that checks image architecture, host and container device access, a PyTorch CUDA computation, Docling conversion on CUDA, and persistence across recreation. Record CPU and AMD64 verification separately when claimed.

Tests must compare source-grounded structure and artifact integrity. They must not use generated semantic interpretations as expected output.

## MVP Acceptance Criteria

The MVP is accepted when all of the following are demonstrated:

1. The repository retains Apache 2.0 `LICENSE` and compliant `NOTICE`; redistributed third-party/model terms are documented separately.
2. A valid PDF can be dropped or selected, uploaded within the configured limit, converted locally with Docling, and rejected safely when invalid or oversized.
3. Engine choices come from the backend registry. Docling reports truthful availability, and a contract-test adapter can be added without changing job routes or core UI data shapes.
4. Conversion performs no remote inference and the deployed application requires no database or Open WebUI connection.
5. The review screen provides resizable PDF/Markdown panes and toggleable, bidirectional synchronization using verified block coordinates or an explicitly identified page-level fallback.
6. The complete Markdown draft can be edited and rendered before save. Saved content survives polling and refresh, and every exit from a dirty draft is guarded.
7. The converted Markdown contains source-grounded page anchors and preserves technical content without inserted summaries, explanations, captions, or inferred facts.
8. Every exported figure is a losslessly encoded PNG after extraction/rasterization, has a deterministic ID/path and checksum, remains correctly referenced from Markdown, and uses only verified or `null` provenance.
9. Repeating conversion with identical input, upload filename, engine/adapter versions, and options yields identical normalized Markdown and artifact checksums aside from documented volatile metadata.
10. The ZIP contains exactly the documented safe artifact classes, including `manual.md`, `metadata.json`, `image-index.json`, and referenced PNGs, with no source PDF, host path, secret, or temporary engine output by default.
11. The reviewed ZIP is suitable for the documented manual Open WebUI RAG ingestion workflow; the application makes no claim that Open WebUI will automatically serve or understand relative image assets.
12. Relevant automated unit, API, frontend, golden, end-to-end, and container tests pass, and their commands are documented.
13. The Compose stack runs through Portainer on DGX Spark with native ARM64 images, logs and exports from a verified CUDA-backed Docling conversion, and persistent job and cache volumes. Verify that the chosen limits leave enough capacity for Open WebUI and Ollama. Record a compatible CPU configuration where upstream dependencies permit.
14. Known fidelity, security, resource, and model-license limitations are visible in operator documentation.

## Future Enhancements

- Qualify the planned local Docling Nemotron profile against the [architecture decision](#nemotron-35-parse-architecture-decision) before enabling it.
- Add other local engines through the same registry and adapter conformance suite.
- Improve coordinate-level alignment for complex layouts, tables, repeated headers, footnotes, and figures while retaining page-level fallback.
- Isolate PDF preflight and conversion in a terminable local worker with hard time, memory, process and scratch-disk limits. Add running-job cancellation and check recovery from native parser failure.
- Add configurable OCR and rasterization profiles whose exact values are recorded in export metadata.
- Add batch conversion only after single-document determinism, resource controls, and review safety are established.
- Add optional local authentication, audit events, malware scanning, and HTTPS deployment guidance for environments beyond a trusted LAN.
- Add richer archive validation and a standalone local pre-ingestion report for Open WebUI operators without creating direct Open WebUI integration.

The following remain non-goals unless this specification is explicitly revised: a database, cloud inference, AI-authored technical interpretation, and direct Open WebUI integration.
