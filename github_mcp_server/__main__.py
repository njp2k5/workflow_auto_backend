"""Entry-point for the GitHub MCP Server."""
import logging
import uvicorn
from .config import settings


def main():
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    from .api import app

    logging.info("Starting GitHub MCP Server on 0.0.0.0:%s", settings.mcp_server_port)
    logging.info("  MCP SSE endpoint : /mcp/sse")
    logging.info("  REST API         : /api/*")
    logging.info("  Dashboard stream : /api/stream/dashboard")
    logging.info("  Swagger docs     : /docs")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.mcp_server_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
