"""Railway-safe entrypoint: reads PORT from env (no shell expansion needed)."""

from __future__ import annotations

import os


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    print(f"pdf-extract-api binding host={host} port={port}", flush=True)

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
    )


if __name__ == "__main__":
    main()
