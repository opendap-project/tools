import subprocess
import sys
import zipfile

import pytest


@pytest.mark.slow
def test_the_wheel_carries_the_schemas(tmp_path):
    subprocess.run(["uv", "build", "--wheel", "-q", "-o", str(tmp_path)], check=True)
    (wheel,) = tmp_path.glob("odw-*.whl")
    names = zipfile.ZipFile(wheel).namelist()
    assert "odw/schemas/matrix.schema.json" in names and "odw/schemas/task.schema.json" in names


def test_schemas_are_found_inside_the_package_first(tmp_path, monkeypatch):
    from odw import schemas
    assert (schemas.SCHEMAS / "matrix.schema.json").is_file()
