"""Unit test for `GET /health` (`app/main.py`).

No depende de la base de datos ni de ningún servicio externo -- el propio
docstring del endpoint lo garantiza.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.unit

client = TestClient(app)


def test_health_check_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
