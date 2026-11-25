# Local CPU validation

Tested November 25, 2026 on an Intel Mac running macOS 15.7.9. The real Docling upload, review, edit, and ZIP export workflow passed. Full local acceptance remains open because the provenance, synchronized review, and unsaved-change objectives below did not all pass. DGX Spark deployment is gated on local acceptance.

## Test environment

- Vite 6.4.6 and Node 22.23.5 on port 5173, with the Vite proxy forwarding to FastAPI on loopback port 8000. FastAPI used isolated job storage and the existing Docling adapter.
- Python 3.12.16, Docling 2.151.0, `docling-parse` 7.24.2, PyTorch 2.14.1 CPU, and torchvision 0.29.2. Docling logged `Accelerator device: 'cpu'` and ran its standard PDF pipeline. Model weights were downloaded into an isolated cache. The integration tests passed again with `HF_HUB_OFFLINE=1`.
- The sample was the original two-page USGS fact sheet *How Does a U.S. Geological Survey Streamgage Work?*, including labeled figures and technical text. Its PDF SHA-256 was `0360be009eb441bd415c46a5c5d99c647fadfac9d04e9d4191952f5feeb91584`. [USGS source PDF](https://pubs.usgs.gov/fs/2011/3001/pdf/fs2011-3001.pdf)

### Intel Mac setup

The system Python 3.13 had no compatible upstream PyTorch wheel. In an isolated Python 3.12 environment, conda-forge supplied CPU PyTorch and the Intel Mac PDF parser. Docling 2.153.0 required parser 7.28.0, for which PyPI offered Apple Silicon wheels but no Intel wheel. The attempted source build failed because AppleClang could not find `cstdint`. The tested recipe below stays within the repository's dependency range and uses a prebuilt Intel parser. [PyTorch Intel Mac build announcement](https://dev-discuss.pytorch.org/t/pytorch-macos-x86-builds-deprecation-starting-january-2024/1690) · [Conda-forge PDF parser](https://anaconda.org/conda-forge/docling-parse)

```bash
pdf2md_env=/absolute/path/to/new-test-env
micromamba create -p "$pdf2md_env" --override-channels -c conda-forge \
  python=3.12 pip pytorch=2.14.1 torchvision=0.29.2 docling-parse=7.24.2
"$pdf2md_env/bin/python" -m pip install -r backend/requirements.txt \
  'docling==2.151.0' 'docling-parse==7.24.2'
"$pdf2md_env/bin/python" -m pip check
```

## Automated results

| Check | Result |
| --- | --- |
| TypeScript `tsc --noEmit` and `npm run build` | Passed. Vite warned about the size of the main bundle. |
| Backend `unittest` suite in the CPU environment | 15 passed, including two tests through the real Docling adapter. The remaining 13 tests cover the registry and fixture-based regressions. |
| Real CPU valid-PDF integration | Passed. Verified two pages, the source label `Figure 1.`, seven decodable PNGs, matching relative Markdown links, saved Markdown in the ZIP, and matching SHA-256 for each ZIP entry and served asset. All seven hashes matched across the two test runs. |
| Real CPU corrupt-PDF integration | Passed. A file with a PDF signature but no valid document structure reached the failed state with a safe error. Save and export returned HTTP 409. |
| Frontend `npm run test:browser` | 3 passed. Covered invalid file rejection, real Docling upload and review, page navigation, image rendering, draft preview before save with linking off, reconversion cancel, saved export response, and failed conversion feedback. |

The [README](../README.md) lists the opt-in commands. The real tests require the source PDF path in `PDF2MD_TEST_PDF` and the same model cache for an offline rerun.

## Manual browser and artifact checks

| Objective | Observed result |
| --- | --- |
| Real local workflow | Passed. Viewed the original and converted document side by side, edited Markdown, saved, and clicked Download ZIP. The browser requested the ZIP successfully. `unzip -t` passed, and the archive contained the saved test edit, the original `Figure 1.` label, the image index, and all seven PNGs. |
| Relative image references | Passed for this sample. Full-document preview displayed local assets and the ZIP contained the relative paths. Linked page extraction showed a literal image placeholder at the top of the first page. |
| Page provenance and stored checksums | Partial. The source and the page extraction both placed the `Figure 1.` label on page 1. All seven image `source_page` fields were null. Full Markdown omitted source-page anchors, and the image index omitted SHA-256 values. Test-computed checksums verified bytes independently. |
| Current draft preview | Partial. Switching to Preview with linked navigation off showed the unsaved draft. With linking on, the preview kept showing the initial page extraction. The editor and rendered draft are separate views. |
| Linked navigation and fallback | Partial. Next and Previous updated the PDF page and the corresponding Markdown extraction. Scrolling the PDF pane by 120 px left the Markdown position unchanged, and scrolling Markdown by 180 px left the PDF position unchanged. In a separate browser-only check that omitted page mappings from the real completed response, the full document still rendered but the hint continued to say it was page-aligned. |
| Unsaved changes | Partial. Rejecting the reconversion and replacement confirmations retained the draft, and Download ZIP was disabled while dirty. Refresh discarded a second unsaved test edit without prompting. |

This run used one published technical document with embedded text and figures. Scanned-only documents, exhaustive visual fidelity, and target hardware were outside the executed checks. A RapidOCR empty-result warning occurred during the successful conversion.

## Draft preview regression

The updated browser suite passed all three tests with the same real Docling CPU setup and USGS PDF. It verified that Preview shows the current draft before and after saving with linked navigation selected, that the notice identifies the full-document fallback, that local images render, and that the saved Markdown and ZIP are available from the API. The TypeScript and Vite build also passed, with the existing bundle-size warning.

## Linked image preview regression

The real Docling regression reproduced the missing page image links before the fix. Afterwards, the sample's linked pages referenced four and three PNGs respectively, matching all seven paths in the full Markdown and ZIP. All seven PNG checksums matched the earlier export. The 15 backend tests, three browser tests, and TypeScript and Vite build passed. The browser test loaded a PNG with linked navigation enabled. Older conversions can show their existing PNGs in full preview or be reconverted to regenerate the page excerpts.

## Page editing and figure caption ordering

The browser test edited two pages of the real USGS conversion, switched between their draft previews, and saved both page edits. Full-document editing paused linking and kept the preceding page edits. The downloaded ZIP contained all edits and the same seven PNG paths and checksums. A separate test used Docling's serializer with known source coordinates to verify captions above and below figures in both Markdown forms. All 16 backend tests, three browser tests, and the TypeScript and Vite build passed.

## Short figure filenames

Two real CPU conversions produced the same seven sequential `fig-*.png` paths and PNG checksums. The preview, Markdown, image index, and downloaded ZIP used the shorter names. The PNG bytes matched the earlier hash-named export, and an earlier conversion's asset URL still worked. All 16 backend tests and three browser tests passed.

## Running page headers and footers

A Docling serialization regression reproduced a running header that had been classified as body text on one page. The filter removed it and the verified page counter while retaining the title, repeated safety warning, and technical footnote. A generated three-page PDF converted through the local Vite proxy and FastAPI with real CPU inference. Its per-page Markdown and ZIP omitted the running headers and counters and retained all three unique body passages. All 17 backend tests and three browser tests passed.

## Source-prefixed figure filenames

The real USGS PDF was uploaded as `Café 泵 Manual.PDF`. The browser loaded the new PNG paths, and the linked Markdown, saved Markdown, image index, and ZIP agreed on all seven filenames. All seven PNG checksums matched the earlier export. The 18 backend tests, three browser tests, and TypeScript and Vite build passed.

## Conversion and export performance

Measured on the Intel Mac with Docling 2.151.0, one CPU worker, four Docling threads, and locally cached models. Each PDF ran in a fresh process followed by five more conversions in that process. Conversion times include API submission and polling. Peak RSS is the process high-water mark across each six-job run.

| PDF | First conversion, before → after | Median repeat conversion, before → after | Median ZIP, before → after | Peak RSS, before → after |
| --- | ---: | ---: | ---: | ---: |
| USGS streamgage, 2 pages and 7 figures | 28.47 s → 22.74 s | 21.11 s → 16.91 s | 1.50 s → 0.035 s | 1,573 MiB → 1,667 MiB |
| Raster-only inspection worksheet, 3 pages | 41.49 s → 40.16 s | 34.31 s → 31.80 s | 0.008 s → 0.009 s | 2,085 MiB → 2,295 MiB |
| Native-text parts inventory with tables, 3 pages | 14.73 s → 14.49 s | 7.71 s → 6.39 s | 0.009 s → 0.013 s | 1,410 MiB → 1,407 MiB |

Repeat conversion was faster on all three samples. The scanned sample reached its peak RSS on the second job and held that high-water mark for the remaining jobs. OCR dominated the scan. The figure-rich ZIP improved when PNG entries were stored directly. Small text-only archives took a few additional milliseconds with link validation and metadata. All 36 conversions matched their sample's baseline Markdown after excluding generated source-page comments. All seven USGS PNG checksums matched. These measurements use the Mac CPU and local test fixtures.

The production build splits the PDF and Markdown renderers out of the initial JavaScript bundle. The entry bundle fell from about 785 kB to 243 kB before compression. Both renderers load when their panes first need them. Single dev-browser first-paint observations were about 9.7 and 9.8 seconds, so the bundle measurement establishes a smaller initial transfer. Startup latency still needs controlled production measurements.

## Local acceptance for the Spark build

| Check | Result |
| --- | --- |
| Automated suites | 20 backend tests, three browser tests, and the TypeScript and Vite production build passed. |
| Real CPU browser conversion | The two-page USGS document, three-page raster-only inspection worksheet, and three-page parts inventory all converted and exported. The browser run saw Markdown at about 28, 34, and 8 seconds respectively. These one-off observations include browser polling and rendering. |
| OCR and tables | The scanned worksheet's asset ID appeared in the rendered preview. The parts inventory rendered an HTML table containing its first part number. |
| Review and recovery | A pointer drag and keyboard resize both worked. Scrolling followed in both directions within a linked page and paused when unlinked. Refresh restored the original PDF and last saved Markdown. Canceling a native Chromium reload prompt retained the dirty draft. |
| Exported artifacts | Downloaded ZIPs for all three fixtures passed integrity checks. The edited USGS ZIP contained seven PNGs with known pages, dimensions, and matching hashes. A prior conversion exported its saved content with unknown edit provenance represented as `null`. |

The application was tested with offline model access on the development Mac. Native container startup, ARM64 behavior, CUDA acceleration, and resource contention with Ollama remain target checks. The fixtures verify representative text, OCR, tables, and figures. Each production manual still goes through human review before ingestion.

## Deployment gate

The development Mac checks above are complete. The next environment to validate is the Spark, starting with the native CPU stack. [DGX Spark runbook](dgx-spark.md)
