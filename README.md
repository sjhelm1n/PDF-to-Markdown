# PDF to Markdown — Docling starter

Apache-2.0-licensed starter for a self-hosted PDF-to-Markdown review workspace, designed for preparing maintenance manuals for **manual** upload to Open WebUI.

## Included

- React/Vite SPA: drag-and-drop PDF, Docling convert, source PDF preview, Markdown review/edit, save, ZIP download.
- FastAPI: file uploads, single-process background conversion, disk-based JSON job state, export API.
- Docling: locally executed conversion, referenced PNG files, a separate image index, per-page Markdown extraction for linked **page navigation**.
- No database, no cloud AI, no image descriptions, no Open WebUI integration.
- The engine selector uses `GET /api/engines` for capabilities and availability. Docling is the default. Nemotron 3.5 Parse appears as an unavailable placeholder.

## Local development

Use Python 3.12 where possible. Intel Mac users can reproduce the tested conda-forge CPU setup in the [local validation report](docs/local-validation.md#intel-mac-setup).

```bash
cd backend
python -m venv .venv
# Activate the virtual environment for your OS
pip install -r requirements.txt
APP_DATA=./data DOCLING_DEVICE=cpu uvicorn app.main:app --reload
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Browse `http://localhost:5173`. The Vite development proxy forwards `/api` to FastAPI on port 8000. On Windows PowerShell, set `$env:APP_DATA='./data'` and `$env:DOCLING_DEVICE='cpu'` before starting Uvicorn.

If Vite logs `ECONNREFUSED` for `/api/engines`, check that the FastAPI terminal is still running. Open `http://127.0.0.1:8000/api/engines` and confirm it returns JSON, then reload the Vite page.

### Engine configuration and checks

Set `PDF2MD_ENGINES` on the API service to a comma-separated list of engine IDs. Unset allows both registered IDs, `docling` and `nemotron`. An empty value disables both. Discovery checks Docling's local runtime imports. Model initialization remains part of conversion.

From the repository root, after installing the existing backend and frontend requirements:

```bash
(cd backend && python -m unittest discover -s tests -v)
(cd frontend && npm run build)
```

The default backend suite uses fixture adapters for fast coverage. Set `PDF2MD_TEST_PDF` to an absolute path to a real multipage PDF containing figures to include the CPU Docling integration tests. Install Docling and provision its model artifacts first. Optionally set `PDF2MD_TEST_FIGURE_REFERENCE` to a label found in the chosen source PDF.

```bash
(cd backend && PDF2MD_TEST_PDF=/absolute/path/to/technical.pdf python -m unittest discover -s tests -v)
```

With Vite and a CPU FastAPI backend running, use the browser suite to verify the same workflow through the UI. It requires [agent-browser](https://agent-browser.dev/) with Chromium. Set `AGENT_BROWSER_BIN` if the executable is outside your PATH. Setting `PDF2MD_BROWSER_URL` overrides the default of `http://127.0.0.1:5173`.

```bash
PDF2MD_TEST_PDF=/absolute/path/to/technical.pdf npm --prefix frontend run test:browser
```

Without `PDF2MD_TEST_PDF`, the real conversion tests are explicitly skipped.

### Conversion benchmarks

Run each representative PDF in a separate process to measure one cold conversion and five subsequent conversions with the same worker. Use the same document, options, and model cache for comparisons.

```bash
(cd backend && PYTHONPATH=. DOCLING_DEVICE=cpu DOCLING_NUM_THREADS=4 \
  python tests/benchmark_conversion.py /absolute/path/to/technical.pdf)
```

The benchmark reports stage timings, process peak RSS, ZIP time, and output checksums. The browser test reports time until the Markdown preview appears. See the [local validation report](docs/local-validation.md) for measurements and the [DGX Spark runbook](docs/dgx-spark.md) for target checks.

## Docker and DGX Spark

For a local Docker Compose deployment in CPU mode, run from the repository root.

```bash
docker compose up --build -d
```

The web service defaults to port `8085`. Set `PDF2MD_WEB_PORT` to a free host port and `PDF2MD_WEB_BIND` to the appropriate LAN IP or `127.0.0.1` for local-only access. The API stays on the stack's internal network.

The [DGX Spark deployment runbook](docs/dgx-spark.md) records the local acceptance gate and the subsequent ARM64, Portainer, and CUDA checks.

## Important limitations for the first iteration

- Resize the review panes by dragging the divider or focusing it and pressing the arrow keys. With **Linked page navigation** enabled, either pane can switch the current page. Scrolling is approximate within the linked page. Precise alignment of individual elements is planned.
- With linked navigation on, **Edit Markdown** follows the current PDF page when the page excerpts reproduce the full Markdown exactly. **Preview** shows that page's current draft. Save and ZIP export include edits from every page. Turn off linked navigation to edit the full document. Full-document edits pause linking because their page boundaries can move.
- Refresh restores the active PDF and last saved Markdown in the same tab. A browser exit warning protects unsaved or in-progress changes. For a restored draft without a verified page mapping, the app opens the full Markdown.
- New Docling conversions place associated captions before or after their figures using source PDF positions. Captions that Docling cannot associate or position retain the converter's ordering.
- New Docling conversions omit recognized page headers and footers, matching copies in the same margin on other pages, and standalone `Page X of Y` counters that match the PDF pagination. Ambiguous margin text and technical footnotes stay available for review.
- The `image-index.json` records figure IDs, PNG sizes and checksums, and available source pages, bounding boxes, and caption text. Unknown provenance is represented by `null`.
- PNG export is lossless *after extraction/rasterization*. Original JPEG pixels may already be lossy; vector schematics are rasterized at configurable resolution (`images_scale=3.0`).
- This is a **single-user / trusted-LAN starter**, not an internet-facing service. Add authentication, malware scanning, request rate limits, job retention, resource limits, and HTTPS before wider exposure. Never publish the API/port to the public internet as-is.
- Each worker initializes its Docling converter on demand and reuses it for subsequent jobs. ZIP generation verifies the PNG bytes and stores them without a second compression pass. Archives spill to disk above the in-memory spool threshold.
- Dependency constraints use ranges for a starter. For reproducible builds, lock exact versions after verification on the Spark.

## Export content

```
converted-document.zip
├── manual.md          # edited full document
├── metadata.json      # source, engine, edit state, and checksums
├── image-index.json   # filenames and image inventory
└── images/
    └── Pump-Service-Manual-fig-*.png
```

For `Pump Service Manual.pdf`, new Docling conversions name figures `images/Pump-Service-Manual-fig-000001.png`, `images/Pump-Service-Manual-fig-000002.png`, and so on in source traversal order. The prefix preserves letters, digits, and case, replaces spaces and unsafe punctuation with hyphens, and is limited to 100 UTF-8 bytes. If the name has no usable letters or digits, the prefix is `document`. Markdown uses the same relative paths as the ZIP. Reconversion applies the current naming convention.

**Uploading Markdown to Open WebUI does not automatically guarantee image resolution or image routing.** Preserve the ZIP and arrange image serving separately as part of the Maintenance Copilot's retrieval architecture.

## Licensing

Application source: Apache License 2.0. Docling and model weights maintain their own licenses; these are not redistributed in this source archive.
