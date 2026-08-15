"""Compatibility helpers for the installed RAGAS release.

RAGAS 0.4.3 imports the removed legacy module
``langchain_community.chat_models.vertexai``. The current integration lives in
``langchain_google_vertexai``. Install a process-local alias before importing
RAGAS rather than modifying site-packages.
"""

from __future__ import annotations

import sys
import types


def install_vertexai_import_compatibility() -> None:
    try:
        __import__("langchain_community.chat_models.vertexai")
        return
    except ModuleNotFoundError:
        pass

    from langchain_google_vertexai import ChatVertexAI, VertexAI

    module_name = "langchain_community.chat_models.vertexai"
    compatibility_module = types.ModuleType(module_name)
    compatibility_module.ChatVertexAI = ChatVertexAI
    compatibility_module.VertexAI = VertexAI
    sys.modules[module_name] = compatibility_module
