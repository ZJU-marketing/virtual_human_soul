"""检查 API 路由与 checkpoint."""

from vh_api.main import app
from vh_api.checkpointer import make_thread_id

routes = []
for r in app.routes:
    methods = getattr(r, "methods", None)
    if methods:
        routes.append(f"{sorted(methods)[0]:6s} {r.path}")

print("API 路由:")
for r in routes:
    print(" ", r)
print("thread_id 示例:", make_thread_id("abc12345"))
