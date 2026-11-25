"""Profile one PDF with the real local API worker and inspect each exported ZIP."""

import argparse
import hashlib
import io
import json
import os
import resource
import sys
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, sleep
from unittest.mock import patch
from zipfile import ZipFile

from docling.datamodel.settings import settings
from docling.document_converter import DocumentConverter
from fastapi.testclient import TestClient


def benchmark(pdf: Path, runs: int) -> None:
    native_convert = DocumentConverter.convert
    stage_seconds = {}

    def profile(converter, *args, **kwargs):
        result = native_convert(converter, *args, **kwargs)
        stage_seconds.clear()
        stage_seconds.update({name: round(sum(item.times), 3) for name, item in result.timings.items()})
        return result

    settings.debug.profile_pipeline_timings = True
    with (
        TemporaryDirectory() as directory,
        patch.dict(os.environ, {'APP_DATA': directory, 'PDF2MD_ENGINES': 'docling', 'PDF2MD_CONVERSION_WORKERS': '1'}),
        patch.object(DocumentConverter, 'convert', profile),
    ):
        from app.main import app

        with TestClient(app) as client:
            for run in range(1, runs + 1):
                start = perf_counter()
                with pdf.open('rb') as source:
                    submitted = client.post('/api/jobs', files={'file': (pdf.name, source, 'application/pdf')})
                submitted.raise_for_status()
                job_url = '/api/jobs/' + submitted.json()['id']
                deadline = start + 300
                while perf_counter() < deadline:
                    response = client.get(job_url)
                    response.raise_for_status()
                    job = response.json()
                    if job['state'] in {'complete', 'failed', 'interrupted'}:
                        break
                    sleep(0.1)
                else:
                    raise TimeoutError('The conversion exceeded five minutes.')
                if job['state'] != 'complete':
                    raise RuntimeError(job.get('error') or job['state'])

                converted = perf_counter()
                response = client.get(job_url + '/download')
                response.raise_for_status()
                downloaded = perf_counter()
                with ZipFile(io.BytesIO(response.content)) as archive:
                    if archive.testzip() is not None:
                        raise RuntimeError('The ZIP failed its integrity check.')
                    png_sha256 = {name: hashlib.sha256(archive.read(name)).hexdigest()
                                  for name in archive.namelist() if name.endswith('.png')}
                peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                print(json.dumps({
                    'run': run, 'pages': len(job['pages']), 'seconds': round(converted - start, 3),
                    'zip_seconds': round(downloaded - converted, 4), 'zip_bytes': len(response.content),
                    'peak_mib': round(peak / (1024**2 if sys.platform == 'darwin' else 1024)),
                    'stage_seconds': stage_seconds, 'docling': version('docling'),
                    'markdown_sha256': hashlib.sha256(job['markdown'].encode()).hexdigest(),
                    'png_sha256': png_sha256,
                }), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--runs', type=int, default=6)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error('--runs must be positive')
    benchmark(args.pdf, args.runs)
