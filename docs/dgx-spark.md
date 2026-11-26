# DGX Spark deployment

The target is a GB10 Grace Blackwell Superchip running NVIDIA DGX OS on Linux ARM64. It has 20 Arm CPU cores, 6,144 NVIDIA Blackwell CUDA cores, and 128 GB of unified LPDDR5X memory shared by the CPU and GPU. The GB10 reports CUDA compute capability 12.1. [NVIDIA hardware overview](https://docs.nvidia.com/dgx/dgx-spark/hardware.html) · [NVIDIA DGX Spark software](https://docs.nvidia.com/dgx/dgx-spark/software.html) · [NVIDIA CUDA compute capabilities](https://developer.nvidia.com/cuda/gpus)

## Compatibility status

- The Python 3.12 Bookworm, Node 22 Alpine, and Nginx stable Alpine base images publish ARM64 and AMD64 builds. Production release images still need to be built and run for their target platform. Build natively on the Spark when practical. [Python base image](https://github.com/docker-library/official-images/blob/master/library/python) · [Node base image](https://github.com/docker-library/official-images/blob/master/library/node) · [Nginx base image](https://github.com/docker-library/official-images/blob/master/library/nginx)
- The frontend Docker context excludes host `node_modules` and build outputs so the ARM64 build uses its own native npm dependencies.
- The original API image left the PyTorch build to dependency resolution. Installing Docling or setting `DOCLING_DEVICE=cuda` alone cannot ensure suitable CUDA binaries or GPU access. The GPU build selects PyTorch `2.14.1+cu130` and torchvision `0.29.1+cu130`. The upstream indexes publish both wheels for CPython 3.12 on Linux AArch64 and x86-64. The CUDA 13.0 Blackwell build is a candidate for GB10. Use the device and kernel checks below to verify the exact build. An architecture list containing `sm_120` or `compute_120` can be compatible with a 12.1 device. [PyTorch wheels](https://download.pytorch.org/whl/cu130/torch/) · [torchvision wheels](https://download.pytorch.org/whl/cu130/torchvision/) · [NVIDIA Blackwell compatibility](https://docs.nvidia.com/cuda/blackwell-compatibility-guide/index.html)
- Docling supports CUDA in the standard PDF pipeline. OCR acceleration depends on the selected OCR engine and its runtime, so record which stages use the GPU. Each application worker initializes its Docling converter on the first successful job and reuses it. [Docling GPU guide](https://docling-project.github.io/docling/usage/gpu/)
- **Target validation is pending.** Upstream manifests, wheel listings, and fixture tests establish a candidate configuration. Release qualification requires native image builds and a real PDF conversion on the Spark.

## Before deploying

The development Mac passed the CPU checks for text, scanned, and table-heavy PDFs. The tested workflow includes page-level navigation and scrolling, saved-draft restoration, and ZIP manifest validation. Target hardware and container checks remain pending. [Local CPU validation report](local-validation.md)

On the Spark, build both images natively and deploy the CPU stack through Portainer on a free port. Repeat the test PDF uploads, review, edit, and ZIP inspection to establish an ARM64 CPU baseline. Then build the CUDA API image and apply the GPU reservation. Check container GPU access, PyTorch and torchvision kernels, and Docling inference using the same PDFs. Record cold and repeat job time, export time, and peak memory on both stacks. Keep the CPU image available for rollback and repeat the resource check with Ollama active before increasing CPU thread limits.

### Task 2 worker and container hardening

Before processing untrusted PDFs on the Spark, move PDF preflight and Docling conversion to a terminable local worker. Enforce hard per-job time, memory and scratch limits and verify that a killed worker releases its job reservation while the API stays responsive. Run the worker with only its job workspace and approved model cache. Apply and verify least-privilege container settings, process and storage limits, and native dependency provenance on the actual ARM64 images. The current application limits retained job files and leaves temporary parsing, inference and export storage to these runtime controls.

## Host preflight

Run on the actual Spark. Check published Docker ports and host listeners before selecting the web bind address and port. Keep the existing Portainer, Open WebUI, and Ollama services on their own networks and ports.

```bash
uname -m
cat /etc/os-release
nvidia-smi
nvidia-ctk --version
docker version
docker compose version
docker ps --format '{{.Names}}	{{.Ports}}'
sudo ss -ltnp
```

Expect `aarch64` and an NVIDIA GB10. Compare the installed driver against the container runtime requirements. The CUDA version in `nvidia-smi` describes driver support. Use `torch.version.cuda` later to identify the build in the API image. [NVIDIA Spark release notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html) · [CUDA driver compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/latest/minor-version-compatibility.html)

DGX Spark ships with NVIDIA Container Toolkit configured. Verify passthrough before changing the daemon. NVIDIA documents this smoke test.

```bash
docker run --rm --platform linux/arm64 --gpus all \
  nvcr.io/nvidia/cuda:13.0.1-devel-ubuntu24.04 nvidia-smi
```

If the toolkit is installed but its rootful Docker runtime is missing, prepare a maintenance window for Portainer, Open WebUI, and Ollama before applying NVIDIA's configuration and restarting Docker. Follow NVIDIA's separate instructions if Docker is rootless.

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Repeat the container smoke test after any runtime change. [DGX Spark container runtime](https://docs.nvidia.com/dgx/dgx-spark/nvidia-container-runtime-for-docker.html) · [NVIDIA Container Toolkit setup](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)

## Build the images and deploy from Portainer

From the repository on the Spark, build the two ARM64 images. The API tag below is a local candidate for GPU testing.

```bash
docker buildx build --platform linux/arm64 --load -t pdf2md-web:arm64 ./frontend
docker buildx build --platform linux/arm64 --load \
  --build-arg PDF2MD_TORCH_BUILD=cu130 -t pdf2md-api:arm64-cu130 ./backend
docker image inspect --format '{{.Os}}/{{.Architecture}} {{.Id}}' \
  pdf2md-web:arm64 pdf2md-api:arm64-cu130
```

For a CPU API image, omit the build argument and use a distinct tag such as `pdf2md-api:arm64-cpu`. Both commands should produce `linux/arm64` images. For registry deployment, publish versioned tags and check the platform with `docker buildx imagetools inspect IMAGE_REF`. Use verified image digests for repeatable production updates. Where CPU dependencies resolve, an AMD64 build uses `--platform linux/amd64`. A registry can hold both platforms under one tag when the builder supports both. [Docker multi-platform builds](https://docs.docker.com/build/building/multi-platform/)

In Portainer, select the Spark's Docker Standalone endpoint and open **Stacks → Add stack → Web editor**. Use `pdf2md` as the stack name for a new deployment, or keep the existing stack name to preserve its volume names.

1. Paste `compose.yaml`. Replace each service's `build:` entry with its `image:` reference. For images loaded on this Docker daemon, set `pull_policy: never` on both services. For registry images, use the published references and configure registry access in Portainer.
2. For CUDA, use the API's `arm64-cu130` image and merge `environment` and `deploy.resources.reservations.devices` for `api` from `compose.gpu.yaml`. Retain the base API settings and volumes. For CPU, select the CPU API image and use the base `DOCLING_DEVICE: cpu` settings. The images select separate hash-pinned Python locks. Only `api` requests the NVIDIA GPU.
3. Set the stack's environment variables as needed, then deploy. Publish only the web service. Nginx reaches `api:8000` on the stack network, so existing software can keep host port 8000.

The local CLI equivalent for validating and building the GPU configuration is below. Use either Portainer or the CLI to manage a given stack. [Portainer stack setup](https://docs.portainer.io/user/docker/stacks/add) · [Docker Compose GPU reservations](https://docs.docker.com/compose/how-tos/gpu-support/)

```bash
docker compose -p pdf2md -f compose.yaml -f compose.gpu.yaml config
docker compose -p pdf2md -f compose.yaml -f compose.gpu.yaml up -d --build
```

### Ports, persistence, and resources

| Stack setting | Default | Effect |
| --- | --- | --- |
| `PDF2MD_WEB_BIND` | `0.0.0.0` | IPv4 host bind address. Use the Spark's LAN IP or `127.0.0.1` as appropriate. |
| `PDF2MD_WEB_PORT` | `8085` | Host port for Nginx. Choose an unused port verified by the preflight. |
| `PDF2MD_API_CPUS` | `4` | API CPU limit in cores. |
| `PDF2MD_API_MEM_LIMIT` | `16g` | API container memory limit. Adjust for representative documents and host capacity. |
| `PDF2MD_DOCLING_THREADS` | `4` | Sets Docling's `DOCLING_NUM_THREADS` for CPU inference work. |
| `PDF2MD_MAX_PENDING_JOBS` | `2` | Waiting admission capacity alongside the one conversion worker. |
| `PDF2MD_MAX_PDF_PAGES` | `25` | Inclusive page limit before Docling inference. |
| `PDF2MD_MAX_STORAGE_BYTES` | `2147483648` | Retained-job storage budget in bytes. |

There is one conversion worker. Use the same upload and edit request limits for the API and Nginx, and set retention for the planned operating window. On the Spark, exercise 25- and 26-page uploads and oversized upload and edit requests through Nginx. Watch `docker stats --no-stream`, host memory with `free -h`, and `nvidia-smi` while running representative jobs with Ollama active. Reserving one GPU grants device access without allocating a dedicated share of GPU compute or unified memory. [Application settings](../README.md#upload-and-job-limits).

The `pdf2md-data` volume backs `/data` for uploads, job state, Markdown, and assets. The separate `docling-model-cache` volume backs `/root/.cache` for downloaded model artifacts. Keep the project name and volume declarations stable across redeploys. Inspect the mounts and recreate a container to check data and cached models persist. Avoid volume deletion when removing or recreating containers. If operating offline, prepopulate the cache using the same pipeline before disconnecting. [Docling model preparation](https://docling-project.github.io/docling/usage/advanced_options/)

## Verify the deployed GPU stack

Use the actual container name if the API was deployed under a different stack name. Verify architecture, the NVIDIA device request, and storage on the deployed container.

```bash
pdf2md_api=$(docker ps -q --filter label=com.docker.compose.project=pdf2md \
  --filter label=com.docker.compose.service=api)
docker inspect --format '{{json .HostConfig.DeviceRequests}}' "$pdf2md_api"
docker inspect --format '{{range .Mounts}}{{println .Name .Destination}}{{end}}' "$pdf2md_api"
docker exec "$pdf2md_api" uname -m
docker exec "$pdf2md_api" nvidia-smi
docker exec "$pdf2md_api" python -m pip check
docker exec -i "$pdf2md_api" python - <<'PY'
import os
from importlib.metadata import version

import torch
import torchvision

print("Docling:", version("docling"), "torch:", torch.__version__, "torchvision:", version("torchvision"))
print("CUDA build:", torch.version.cuda, "requested device:", os.environ["DOCLING_DEVICE"])
assert torch.version.cuda == "13.0", "Expected the CUDA 13.0 API image"
assert torch.cuda.is_available(), "CUDA cannot be used inside the API container"
print(torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0), torch.cuda.get_arch_list())
assert torch.cuda.get_device_capability(0) == (12, 1), "Expected DGX Spark GB10"
x = torch.ones((128, 128), device="cuda")
result = x @ x
boxes = torch.tensor([[0., 0., 1., 1.], [0., 0., 1., 1.]], device="cuda")
scores = torch.tensor([0.9, 0.8], device="cuda")
kept = torchvision.ops.nms(boxes, scores, 0.5)
torch.cuda.synchronize()
assert result[0, 0].item() == 128 and kept.tolist() == [0]
print("CUDA PyTorch and torchvision kernels passed")
PY
```

Submit a small, non-sensitive PDF with extractable text through the published web endpoint. Use the configured bind address and port. Poll the job until `complete`, then verify the ZIP. If running on a different machine, use the Spark's reachable address in place of loopback.

```bash
pdf2md_url=http://127.0.0.1:8085
pdf2md_fixture=/path/to/test-document.pdf
pdf2md_job=$(curl -fsS -F "file=@${pdf2md_fixture};type=application/pdf" \
  "${pdf2md_url}/api/jobs?engine=docling" | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
curl -fsS "${pdf2md_url}/api/jobs/${pdf2md_job}" |
  python3 -c 'import json, sys; job=json.load(sys.stdin); print(job["state"], job.get("error"))'
# Repeat the status check until completion. Review container logs on failure.
curl -fsS "${pdf2md_url}/api/jobs/${pdf2md_job}/download" -o spark-smoke.zip
unzip -t spark-smoke.zip
```

Run the same adapter on the uploaded fixture inside the API container and check its CUDA allocator. This diagnostic creates a separate converter alongside any cached worker models, so run it with an idle queue and monitor shared memory. Record the reported device, GPU allocation, and Docling stage logs together with the successful API conversion. Repeat with a scanned and table-heavy fixture if those paths are part of production.

```bash
docker exec -i "$pdf2md_api" python - "$pdf2md_job" <<'PY'
import logging
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import torch
from docling.datamodel.pipeline_options import PdfPipelineOptions

from app.engines.base import EngineConfig
from app.engines.docling import DoclingAdapter

logging.basicConfig(level=logging.INFO)
assert torch.cuda.is_available()
assert str(PdfPipelineOptions().accelerator_options.device).split(".")[-1].lower() == "cuda"
source = Path(os.environ["APP_DATA"]) / sys.argv[1] / "input.pdf"
torch.cuda.reset_peak_memory_stats()
with TemporaryDirectory() as workspace:
    result = DoclingAdapter(EngineConfig()).convert(source, Path(workspace))
    torch.cuda.synchronize()
    peak = torch.cuda.max_memory_allocated()
    assert result.pages and result.markdown.strip()
    assert peak > 0, "Docling did not allocate CUDA tensors in this process"
    print(f"Converted {len(result.pages)} page(s), peak PyTorch CUDA allocation {peak} bytes")
PY
```

Recreate the API through Portainer under the same stack name. Run the mount inspection again, download the original job's ZIP, and verify that model artifacts remain in `/root/.cache` before submitting another conversion. Record the DGX OS and driver versions, image IDs or digests, Torch/CUDA and Docling versions, test PDF type, observed accelerator, and memory behavior with Ollama running. If the CUDA checks fail, choose the CPU image and base Compose settings while investigating the driver, Torch build, or failing Docling stage.
