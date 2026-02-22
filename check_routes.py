"""Quick route check script."""
import logging
for name in ['httpx', 'github_mcp_server', 'root', '']:
    logging.getLogger(name).setLevel(logging.CRITICAL)
logging.basicConfig(level=logging.CRITICAL, force=True)

import sys
sys.path.insert(0, '.')
from main import app

with open('_routes.txt', 'w') as f:
    routes = [r.path for r in app.routes if hasattr(r, 'methods')]
    f.write(f"Total routes: {len(routes)}\n\n")
    for p in sorted(routes):
        f.write(p + "\n")
    f.write("\n--- New endpoints ---\n")
    for p in sorted(routes):
        if any(kw in p for kw in ['meetings', 'developers', 'assign-nl', 'progress-report']):
            f.write(p + "\n")

print("Routes written to _routes.txt")
