import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"

# Versioned API contract: exported from the handlers by `litestar assets
# generate-types`, consumed by the frontend without Python. See the README.
OPENAPI_SCHEMA = PROJECT_ROOT / "openapi.json"

# Serving the OpenAPI docs publishes a full inventory of the routes, so production
# turns them off (see compose.prod.yml). Kept on by default: they are useful in
# development, and `litestar assets generate-types` needs the schema in memory.
DOCS_ENABLED = os.getenv("ENABLE_DOCS", "true").lower() in {"1", "true", "yes"}
