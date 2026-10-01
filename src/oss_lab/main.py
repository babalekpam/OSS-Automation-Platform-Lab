"""ASGI entrypoint: ``uvicorn oss_lab.main:app``. The release label comes from
``OSS_LAB_RELEASE`` (``n`` = current, ``n1`` = candidate)."""

import os

from .core.api import create_app
from .core.stack import Stack

app = create_app(Stack(version=os.environ.get("OSS_LAB_RELEASE", "n")))
