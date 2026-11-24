"""诊断: 自己创建 SelectorEventLoop 后交给 uvicorn 的 Server.run."""

import asyncio
import sys
from contextlib import asynccontextmanager

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI

app = FastAPI()


@app.get("/debug/loop")
async def debug_loop():
    return {
        "loop": type(asyncio.get_running_loop()).__name__,
        "policy": type(asyncio.get_event_loop_policy()).__name__,
    }


def main() -> None:
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=8302, log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)
    # 关键: 自己建 loop 并 set 为当前 loop, uvicorn.Server.run 会复用它
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server.serve())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
