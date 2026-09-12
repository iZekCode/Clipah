"""ASGI composition root for the deployed Clipah API process."""

from clipah.api.app import create_app
from clipah.config import ProcessRole, Settings

app = create_app(Settings(process_role=ProcessRole.API))
