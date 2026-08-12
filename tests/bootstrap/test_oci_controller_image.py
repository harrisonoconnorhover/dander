"""Source-free OCI Functions controller image contract."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[2]


def test_controller_image_is_python_312_x86_digest_pinned_and_wheel_only() -> None:
    dockerfile = (_ROOT / "infra/oci/controller/Dockerfile").read_text(encoding="utf-8")

    assert "fnproject/python:3.12-dev@sha256:" in dockerfile
    assert "fnproject/python:3.12@sha256:" in dockerfile
    assert "ARG DANDER_WHEEL" in dockerfile
    assert "pip3 install --target /python --no-cache-dir --no-deps" in dockerfile
    assert "COPY src/" not in dockerfile
    assert "COPY . " not in dockerfile
    assert 'ENTRYPOINT ["/python/bin/fdk", "/function/func.py", "handler"]' in dockerfile


def test_controller_dependencies_and_shim_are_exact_and_secret_free() -> None:
    requirements = (_ROOT / "infra/oci/controller/requirements.txt").read_text(encoding="utf-8")
    shim = (_ROOT / "infra/oci/controller/func.py").read_text(encoding="utf-8")

    assert requirements.splitlines() == [
        "fdk==0.1.117",
        "oci==2.184.1",
        "pydantic==2.13.4",
    ]
    assert "function_handler import handler" in shim
    assert "token" not in requirements.lower()
    assert "password" not in shim.lower()


def test_oci_function_import_does_not_load_other_provider_operations() -> None:
    script = """
import sys
from dander.providers.oci_container_instances.function_handler import handler
assert callable(handler)
assert 'dander.providers.azure_container_apps.operations' not in sys.modules
assert 'dander.providers.fargate.operations' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
