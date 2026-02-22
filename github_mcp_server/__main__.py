"""Entry-point for the GitHub MCP Server."""
import argparse
import logging
import threading
import uvicorn

from .config import settings


def _run_mcp_stdio():
    from .server import mcp
    logging.info("Starting GitHub MCP Server (stdio transport)")
    mcp.run(transport="stdio")


def _run_mcp_sse():
    from .server import mcp
    logging.info(
        "Starting GitHub MCP Server (SSE) on %s:%s",
        settings.mcp_server_host, settings.mcp_server_port,
    )
    mcp.run(
        transport="sse",
        host=settings.mcp_server_host,
        port=settings.mcp_server_port,
    )


def _run_rest_api():
    """Start the REST API bridge (passes the app object directly)."""
    from .api import app as rest_app

    logging.info("Starting REST API on 0.0.0.0:%s", settings.mcp_server_port)
    uvicorn.run(
        rest_app,
        host="0.0.0.0",
        port=settings.mcp_server_port,
        log_level="info",
    )


def main():
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(description="GitHub MCP Server")
    parser.add_argument(
        "--mode",
        choices=["mcp-stdio", "mcp-sse", "rest", "all"],
        default="mcp-stdio",
        help="mcp-stdio | mcp-sse | rest | all",
    )
    args = parser.parse_args()

    if args.mode == "mcp-stdio":
        _run_mcp_stdio()
    elif args.mode == "mcp-sse":
        _run_mcp_sse()
    elif args.mode == "rest":
        _run_rest_api()
    elif args.mode == "all":
        rest_thread = threading.Thread(target=_run_rest_api, daemon=True)
        rest_thread.start()
        logging.info("REST API started in background thread")
        _run_mcp_sse()


if __name__ == "__main__":
    main()
