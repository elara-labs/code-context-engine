"""Provider-neutral structural context models and providers."""

from context_engine.structural.base import BaseStructuralProvider, ProviderStatus
from context_engine.structural.codegraph import CodeGraphBaseProvider, CodeGraphClient
from context_engine.structural.merge import merge_structural_contexts
from context_engine.structural.models import Relationship, SourceRange, StructuralContext, SymbolKey

__all__ = [
    "BaseStructuralProvider",
    "CodeGraphBaseProvider",
    "CodeGraphClient",
    "ProviderStatus",
    "Relationship",
    "SourceRange",
    "StructuralContext",
    "SymbolKey",
    "merge_structural_contexts",
]
