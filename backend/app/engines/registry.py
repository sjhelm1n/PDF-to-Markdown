import logging
import os
from collections.abc import Iterable

from .base import ConversionEngine, EngineConfig, EngineError, EngineInfo
from .docling import DoclingAdapter
from .nemotron import NemotronAdapter

logger = logging.getLogger(__name__)


class EngineRegistry:
    def __init__(self, engines: Iterable[ConversionEngine]):
        self._engines: dict[str, ConversionEngine] = {}
        for engine in engines:
            if engine.id in self._engines:
                raise ValueError(f'Duplicate conversion engine ID: {engine.id}')
            self._engines[engine.id] = engine

    def describe(self) -> list[EngineInfo]:
        return [self._describe(engine) for engine in self._engines.values()]

    def select(self, engine_id: str) -> ConversionEngine:
        engine = self._engines.get(engine_id)
        if engine is None:
            raise EngineError('unknown_engine', 'Unknown conversion engine.')
        info = self._describe(engine)
        if info.unavailable_reason is not None:
            raise EngineError('engine_unavailable', info.unavailable_reason)
        return engine

    @staticmethod
    def _describe(engine: ConversionEngine) -> EngineInfo:
        engine_version = None
        reason = None
        if not engine.config.enabled:
            reason = 'Disabled by server configuration.'
        else:
            try:
                engine_version = engine.probe()
            except EngineError as exc:
                if exc.__cause__ is not None:
                    logger.warning('Engine %s failed its runtime check', engine.id, exc_info=True)
                reason = str(exc)
            except Exception:
                logger.exception('Engine %s failed its runtime check', engine.id)
                reason = 'Local runtime check failed. See server logs.'
        return EngineInfo(
            id=engine.id,
            name=engine.name,
            adapter_version=engine.adapter_version,
            engine_version=engine_version,
            enabled=engine.config.enabled,
            available=reason is None,
            unavailable_reason=reason,
            capabilities=engine.capabilities,
        )


def configured_registry() -> EngineRegistry:
    adapters = (
        (DoclingAdapter, {'images_scale': 3.0}),
        (NemotronAdapter, {}),
    )
    configured = os.getenv('PDF2MD_ENGINES')
    known = {adapter.id for adapter, _ in adapters}
    enabled = known if configured is None else {
        engine_id.strip() for engine_id in configured.split(',') if engine_id.strip()
    }
    unknown = enabled - known
    if unknown:
        raise ValueError(f'Unknown IDs in PDF2MD_ENGINES: {", ".join(sorted(unknown))}')
    return EngineRegistry(
        adapter(EngineConfig(enabled=adapter.id in enabled, options=options))
        for adapter, options in adapters
    )
