"""PR #40 review: the exported API document (docs/service/openapi.json, swagger.html) is the one the code gives."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("export_openapi", ROOT / "scripts" / "export_openapi.py")
export_openapi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_openapi)


@pytest.mark.parametrize("name", ["openapi.json", "swagger.html"])
def test_the_exported_document_is_current(name):
    saved = (ROOT / "docs" / "service" / name).read_text(encoding="utf-8")
    assert saved == export_openapi.render()[name], "run scripts/export_openapi.py and commit docs/service"


def _refs(node):
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            yield node["$ref"]
        for v in node.values():
            yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)


def test_the_document_has_the_endpoints_and_no_dangling_reference():
    doc = json.loads(export_openapi.render()["openapi.json"])
    assert {"/health", "/chat/stream", "/chat/{thread_id}", "/feedback", "/events", "/dev/chat"} <= doc["paths"].keys()
    assert "ErrorResponse" in str(doc["paths"]["/feedback"]["post"]["responses"]["429"])
    assert "text/event-stream" in doc["paths"]["/chat/stream"]["post"]["responses"]["200"]["content"]
    assert {r.rsplit("/", 1)[-1] for r in _refs(doc)} <= doc["components"]["schemas"].keys()


def test_the_migrate_image_does_not_need_the_agent_code():
    """Dockerfile target `migrate` has src/kosis_store only; migrations/env.py imports the table models."""
    code = ("import sys, kosis_store.models, kosis_store.log_models, kosis_store.service_models; "
            "print([m for m in sys.modules if m.split('.')[0] in ('kosis_agent', 'kosis_rag', 'kosis_ai')])")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT / "src", capture_output=True, text=True)
    assert out.stdout.strip() == "[]", out.stderr
