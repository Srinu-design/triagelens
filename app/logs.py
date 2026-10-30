import logging
import sys

logger = logging.getLogger("triagelens.access")


def setup_logging() -> None:
    # DECISION: Disable uvicorn.access logger so uvicorn's access log doesn't double every request.
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.handlers.clear()
    uvicorn_access.propagate = False
    uvicorn_access.disabled = True

    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
