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
    # HL-71: one odw package from the domain folders, only the schemas themselves (no tests or fixtures), and
    # every folder's tools.toml for the --help summaries.
    assert all(n.endswith(".schema.json") for n in names if n.startswith("odw/schemas/"))
    assert {"odw/__init__.py", "odw/clikit.py", "odw/plan.py", "odw/tools/core.toml", "odw/tools/documentation.toml", "odw/tools/host.toml", "odw/docs.py"} <= set(names)


def test_schemas_are_found_inside_the_package_first(tmp_path, monkeypatch):
    from odw import schemas
    assert (schemas.SCHEMAS / "matrix.schema.json").is_file()
