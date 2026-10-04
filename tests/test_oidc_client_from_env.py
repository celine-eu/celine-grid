"""The OIDC client is taken from the environment when it is set (NIS2 R24).

Literal constructor arguments used to override `CELINE_OIDC_*`, so a deployment could not
name its client and a prod-like run could not use a client with a real secret. The defaults
are read at import, so each case runs a fresh interpreter.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

PROBE = (
    "import json; from celine.grid.settings import Settings; o = Settings().oidc; "
    "print(json.dumps([o.client_id, o.client_secret, o.audience]))"
)


def _oidc(**env: str) -> list[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("CELINE_OIDC_")}
    out = subprocess.run(
        [sys.executable, "-c", PROBE], env={**clean, "CELINE_ENV": "dev", **env},
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()[-1]
    return json.loads(out)


def test_the_environment_names_the_client():
    assert _oidc(
        CELINE_OIDC_CLIENT_ID="svc-grid-example",
        CELINE_OIDC_CLIENT_SECRET="a-real-secret",
        CELINE_OIDC_AUDIENCE="svc-grid-example",
    ) == ["svc-grid-example", "a-real-secret", "svc-grid-example"]


def test_the_local_client_is_the_default():
    assert _oidc() == ["svc-grid", "svc-grid", "svc-grid"]
