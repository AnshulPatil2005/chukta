"""Start the decision inspector.

    python serve.py

Equivalent to `uvicorn web.app:app --port 8000`, but it checks the optional
dependencies first and says something useful if they are missing, rather than
failing with a traceback about starlette.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--reload", action="store_true",
                    help="restart on file changes (development)")
    ap.add_argument("--no-browser", action="store_true",
                    help="do not open a browser window")
    args = ap.parse_args()

    try:
        import uvicorn  # noqa: F401
        import fastapi  # noqa: F401
    except ImportError:
        print("The dashboard needs FastAPI and uvicorn:")
        print("    pip install -r requirements.txt")
        print()
        print("Everything else - the sim, the eval pipeline, the trace - runs")
        print("without them: python -m chukta.trace")
        return 1

    import uvicorn

    url = f"http://{args.host}:{args.port}"
    print(f"  decision inspector -> {url}")
    print("  dry run: requests are rendered, never sent.")
    print()
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
