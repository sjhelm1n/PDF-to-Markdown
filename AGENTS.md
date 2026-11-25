# Codex Repository Instructions

These instructions apply to the entire repository. They are the persistent working rules for Codex and other automated contributors.

## Start Here

- Read `PROJECT.md` before proposing or making an architectural change. Treat it as the product and architecture source of truth.
- Read the relevant implementation and configuration before editing. Do not assume that planned behavior in `PROJECT.md` is already implemented.
- Preserve the distinction between **implemented**, **partial**, and **planned** functionality in documentation, issues, and code comments.
- Keep changes narrowly scoped. Do not rebuild unrelated parts of the application or silently expand the product scope.

## Repository Architecture

The repository is a small two-service application:

- `frontend/` is a React 19, TypeScript, and Vite single-page application. It is built into static files and served by Nginx in the production container. Nginx proxies `/api/` to the backend.
- `backend/` is a Python 3.12 and FastAPI service. It accepts PDFs, runs local conversion work, stores per-job files beneath `APP_DATA`, serves job artifacts, saves Markdown edits, and builds ZIP exports.
- `compose.yaml` runs the frontend and backend with named volumes for job data and the local Docling model cache.
- `README.md` contains operator-facing setup notes and current limitations.
- `PROJECT.md` defines the target product, MVP boundaries, acceptance criteria, and the audited implementation status.
- `LICENSE` and `NOTICE` establish Apache License 2.0 terms for project code. Third-party packages and model artifacts retain their own licenses.

The backend uses one API process, one conversion worker by default, and filesystem JSON state. Preserve that deployment model unless `PROJECT.md` is deliberately revised first.

## Product Boundaries

- Keep inference local. Do not add cloud inference, hosted AI APIs, telemetry that uploads document content, or a requirement for an internet connection during normal conversion.
- Do not add a database. Store job state and artifacts in the per-job filesystem workspace.
- Do not add direct Open WebUI API integration. The supported workflow is a user-reviewed ZIP export followed by manual ingestion into Open WebUI's RAG knowledge base.
- Avoid unnecessary dependencies. Prefer platform, browser, React, FastAPI, and Python standard-library capabilities already present. A new dependency needs a concrete product need, license review, maintenance justification, and documentation.
- Maintain portability. DGX Spark ARM64 through Docker Compose and Portainer is the primary deployment target, but implementation must not unnecessarily prevent compatible ARM64, AMD64, CPU-only, or CUDA-capable deployments.
- Do not make the service internet-facing by assumption. The starter is designed for a trusted local environment; security controls required for broader exposure must be explicit.

## Source Fidelity and AI Restrictions

The application converts and packages source material; it does not author new technical content.

- Preserve the PDF's technical meaning, terminology, units, warnings, identifiers, ordering, tables, captions, figure labels, and cross-references as faithfully as the selected engine permits.
- Never generate summaries, diagram explanations, inferred procedures, synthetic captions, repaired technical claims, or other AI interpretations and insert them into converted output or metadata.
- Never guess a figure's page, caption, label, purpose, or relationship. Record unknown provenance as `null` or an explicit unknown state.
- Distinguish source text, converter output, user edits, and application metadata. Metadata markers must not masquerade as source content.
- Image conversion may decode or rasterize source graphics into PNG, but it must not use generative enhancement, content-aware reconstruction, or lossy recompression. Document unavoidable source or rasterization limitations.
- Keep remote services disabled in every engine by default and in production configuration.

## Conversion Engine Architecture

All conversion engines must be modular adapters behind one stable application contract. Do not add engine-specific branching to API route handlers or React views.

An adapter must:

- expose a stable engine ID, display name, availability state, and relevant capability metadata;
- accept a local PDF path, a job workspace, and explicit conversion options;
- run without a database or cloud inference;
- return normalized document, page/provenance, image, warning, and engine-version metadata;
- write only inside the assigned job workspace;
- use deterministic artifact paths and ordering for the same input, engine version, and options;
- preserve source-grounded image references and represent unavailable metadata honestly;
- cleanly report unsupported platforms, missing models, conversion failures, and cancellation/interruption states.

Use an adapter registry to drive both the engines API and job validation. Docling is the initial adapter. Nemotron 3.5 Parse and later engines must implement the same contract rather than change the UI-to-API job model. Keep engine-native output behind the adapter boundary and normalize only what the product contract requires.

## Frontend Conventions

- Use React function components and hooks with strict TypeScript. Do not introduce `any` to bypass a known data contract.
- Keep API request and response types explicit and aligned with FastAPI/Pydantic models. Centralize API access when it is used by more than one feature.
- As `main.tsx` grows, extract cohesive components and hooks for upload, job polling, engine selection, PDF viewing, Markdown editing/rendering, pane synchronization, and export actions. Avoid a monolithic application component.
- Treat server state, editor draft state, saved state, and view state as separate concepts. Do not overwrite an active draft when polling completes or a component rerenders.
- Clean up timers, observers, object URLs, and event listeners in effect teardown.
- Protect unsaved edits across file replacement, reconversion, job changes, route changes, tab/window closure, and refresh. Do not claim full protection when only an in-app confirmation exists.
- Preserve accessibility: labeled controls, keyboard-operable resizing and navigation, visible focus, semantic status/error announcements, sufficient contrast, and a usable narrow-screen layout.
- Block remote image loading in rendered Markdown. Resolve exported images through the job asset API using validated relative paths.
- Keep the Vite development proxy and the Nginx production proxy behavior compatible. Browser-visible environment variables must use Vite's `VITE_` convention and must never contain secrets.
- Prefer derived state and targeted memoization over unnecessary effects or premature optimization. Avoid fetching the same data serially when independent work can be done together.

## Backend Conventions

- Use Python type hints for public functions and Pydantic models for request/response contracts. Prefer small route handlers that delegate conversion, storage, and export behavior to focused modules.
- Keep blocking conversion work out of async request execution. Bound concurrency and make job state transitions explicit and recoverable.
- Stream uploads in bounded chunks, enforce configured size limits, validate the PDF signature, and normalize user-facing filenames.
- Treat all job IDs and artifact paths as untrusted. Resolve paths beneath the configured job root, prevent traversal, and allow only expected artifact types.
- Use atomic file replacement for state and user edits. ZIP construction must use controlled archive names and deterministic ordering.
- Do not expose raw tracebacks, secrets, host paths, or unbounded exception text through the API. Preserve useful local diagnostics without altering source output.
- Keep converter imports and model initialization isolated from basic health/API startup where practical. Reuse expensive local model state only when lifecycle and resource limits remain safe.
- Make engine availability truthful. Health or engine discovery must not report an engine usable when required packages, models, or hardware checks fail.
- Preserve single-worker safety until concurrency has explicit resource controls and tests. Do not introduce a database or distributed queue as a shortcut.

## Testing and Validation

The repository has a backend suite and opt-in real CPU Docling and browser tests. Add or update proportionate tests for behavioral changes. See the commands in `README.md`.

Required coverage for affected behavior includes:

- unit tests for adapter contracts, deterministic image indexing, provenance normalization, path validation, job state transitions, and ZIP manifests;
- API tests for valid and invalid uploads, size limits, engine validation, polling, edits, asset access, interrupted/failed jobs, and export contents;
- frontend tests for upload/drag-and-drop, engine selection, polling states, editor/rendered preview behavior, pane resizing and synchronization, error handling, and unsaved-change protection;
- deterministic golden-fixture tests using small redistributable or generated PDFs, without committing sensitive maintenance documents;
- container smoke tests for the Compose route from Nginx through FastAPI, with ARM64/DGX Spark validation documented separately from generic local checks.

Before completing a change:

- run the narrowest relevant tests first, then the available broader suite;
- run `npm run build` for frontend TypeScript or UI changes;
- exercise FastAPI startup and relevant API tests for backend changes;
- inspect a representative ZIP and verify that Markdown references, PNG paths, metadata, and checksums agree;
- verify that no document content is sent to a remote service;
- state clearly which checks ran, which did not, and why.

Do not add test or lint dependencies during an unrelated change. When test infrastructure is intentionally introduced, keep it minimal and document the commands in `README.md`.

## Documentation Requirements

- Update `PROJECT.md` when approved product scope, architecture, data contracts, MVP boundaries, or acceptance criteria change.
- Update `README.md` when setup, configuration, deployment, supported engines, user workflow, security assumptions, or known limitations change.
- Document environment variables with defaults, allowed values, and security implications.
- Mark hardware- or engine-specific claims as verified only after they have actually been tested on that platform.
- Keep examples free of secrets, private PDFs, internal hostnames, and proprietary maintenance content.
- Explain fidelity limitations precisely. PNG is lossless after extraction or rasterization; it cannot restore information already lost in a source image, and vector graphics require a documented rasterization scale.

## Dependencies and Generated Files

- Do not hand-edit generated build output or dependency directories.
- Do not commit uploaded PDFs, job workspaces, model caches, virtual environments, `node_modules`, `dist`, or secrets.
- Keep dependency constraints intentional and reproducible. If dependencies change, update the appropriate manifest/lock data and record platform implications, especially for ARM64 and CUDA.
- Check third-party library and model licenses before redistribution. Do not imply that Apache 2.0 relicenses third-party packages or model weights.

## Licensing

- All original project source and documentation must remain compatible with Apache License 2.0.
- Preserve `LICENSE` and `NOTICE`, including required attribution.
- Add attribution for incorporated third-party code or assets when its license requires it.
- Do not copy code, model artifacts, images, or fixtures with incompatible or unclear licensing.
- Mention material third-party licenses and model terms in release and deployment documentation; they remain separate from the application's Apache 2.0 license.
