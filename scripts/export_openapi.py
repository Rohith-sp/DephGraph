"""Print the API's OpenAPI document (no server, no database needed); feeds `npm run gen:api`."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
from app.main import app  # noqa: E402

print(json.dumps(app.openapi(), indent=2, sort_keys=True))
