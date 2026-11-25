from pathlib import Path

from .base import ConversionEngine, ConversionResult, EngineCapabilities, EngineError

_UNAVAILABLE = 'Nemotron inference is planned for a later phase.'


class NemotronAdapter(ConversionEngine):
    id = 'nemotron'
    name = 'Nemotron 3.5 Parse'
    adapter_version = '1'
    capabilities = EngineCapabilities()

    def probe(self) -> str | None:
        raise EngineError('engine_unavailable', _UNAVAILABLE)

    def convert(self, pdf_path: Path, workspace: Path, *, source_filename: str | None = None) -> ConversionResult:
        raise EngineError('engine_unavailable', _UNAVAILABLE)
