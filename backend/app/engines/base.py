from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal, Mapping

from pydantic import BaseModel

OptionValue = str | int | float | bool
ErrorCode = Literal['unknown_engine', 'engine_unavailable', 'conversion_failed']


class EngineError(Exception):
    """An engine failure with a message safe to expose to API clients."""

    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EngineConfig:
    enabled: bool = True
    options: Mapping[str, OptionValue] = field(default_factory=dict)


class EngineCapabilities(BaseModel, frozen=True):
    markdown: bool = False
    page_markdown: bool = False
    png_images: bool = False
    figure_provenance: bool = False


class EngineInfo(BaseModel, frozen=True):
    id: str
    name: str
    adapter_version: str
    engine_version: str | None
    enabled: bool
    available: bool
    unavailable_reason: str | None = None
    capabilities: EngineCapabilities


@dataclass(frozen=True)
class PageResult:
    source_page: int
    markdown: str


@dataclass(frozen=True)
class ImageResult:
    path: str
    source_page: int | None = None
    caption: str | None = None
    source_bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class ConversionResult:
    markdown: str
    pages: tuple[PageResult, ...] = ()
    images: tuple[ImageResult, ...] = ()
    warnings: tuple[str, ...] = ()
    engine_version: str | None = None


class ConversionEngine(ABC):
    id: ClassVar[str]
    name: ClassVar[str]
    adapter_version: ClassVar[str]
    capabilities: ClassVar[EngineCapabilities]

    def __init__(self, config: EngineConfig):
        self.config = config

    @abstractmethod
    def probe(self) -> str | None:
        """Check local runtime availability and return the runtime version if known."""

    @abstractmethod
    def convert(self, pdf_path: Path, workspace: Path, *, source_filename: str | None = None) -> ConversionResult:
        """Convert locally with the optional upload basename for artifact naming."""
