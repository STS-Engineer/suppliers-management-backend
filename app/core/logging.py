"""Application logging configuration."""

import logging
import sys

LOG_LEVEL = logging.INFO

LOG_FORMAT = (
    "[%(asctime)s] %(levelname)-8s "
    "[%(name)s] %(message)s"
)

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging() -> logging.Logger:
    """Configure application logging."""

    logger = logging.getLogger("supplier_management")

    # Prevent duplicate handlers during reload/dev
    if logger.handlers:
        return logger

    logger.setLevel(LOG_LEVEL)
    logger.propagate = False

    formatter = logging.Formatter(
        fmt=LOG_FORMAT,
        datefmt=DATE_FORMAT,
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(LOG_LEVEL)
    console_handler.setFormatter(formatter)

    logger.addHandler(console_handler)

    # SQLAlchemy logging
    sqlalchemy_logger = logging.getLogger("sqlalchemy.engine")
    sqlalchemy_logger.setLevel(logging.WARNING)

    return logger


logger = setup_logging()