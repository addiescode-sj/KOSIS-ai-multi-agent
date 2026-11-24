"""Write the API document from the code, without starting a server (PR #40 review):

- docs/service/openapi.json: the OpenAPI document
- docs/service/swagger.html: Swagger UI with that document inside (open the file in a browser; read only)

    PYTHONPATH=.:src:vendor/phase2/src:vendor/phase1:vendor/phase1/src uv run python scripts/export_openapi.py

Run it after changing an endpoint or a schema; tests/test_openapi.py fails while the files are out of date. A running
server has the same document at /openapi.json and Swagger UI at /docs.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "service"
SWAGGER_UI = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14"
PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KOSIS Agent API</title>
<link rel="stylesheet" href="{ui}/swagger-ui.css">
</head>
<body>
<div id="swagger-ui"></div>
<script src="{ui}/swagger-ui-bundle.js"></script>
<script>
SwaggerUIBundle({{ dom_id: "#swagger-ui", supportedSubmitMethods: [], spec: {spec} }});
</script>
</body>
</html>
"""


def render() -> dict[str, str]:
    """File name -> content."""
    from backend.app.main import app

    spec = app.openapi()
    inline = json.dumps(spec, ensure_ascii=False).replace("</", "<\\/")  # a "</script>" in a text must not end the script
    return {"openapi.json": json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
            "swagger.html": PAGE.format(ui=SWAGGER_UI, spec=inline)}


def main() -> None:
    for name, content in render().items():
        (OUT / name).write_text(content, encoding="utf-8", newline="\n")
        print(OUT / name)


if __name__ == "__main__":
    main()
