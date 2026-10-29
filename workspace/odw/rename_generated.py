"""Generated files in a root's cut: a rendered file is never rewritten; its source is, and the file is rendered again
from the rewritten source, so the generated-file check (`odw req render --check`) still passes after the cut.

Today one generator: a requirements README rendered from the `requirements.toml` beside it (odw/requirements.py).
Spec: docs/specs/rename.md.
"""
import posixpath
import tomllib

from odw import requirements

GENERATORS = [("docs/requirements/README.md", "requirements.toml", requirements.render)]


def outputs(files):
    """Generated file → (its source, the renderer), for every generated file whose source is tracked too."""
    out = {}
    for path in files:
        for tail, source, render in GENERATORS:
            if f"/{path}".endswith(f"/{tail}"):
                src = posixpath.join(posixpath.dirname(path), source)
                if src in files:
                    out[path] = (src, render)
    return out


def render(found, text_of, dst_of):
    """[(generated path after the moves, its source after the moves, the rendered text)] for every generated file
    whose rendering from its (possibly rewritten) source differs from the file. `text_of(path)` reads a file as the
    cut leaves it; a source that does not parse is left for its own check."""
    out = []
    for path, (src, renderer) in sorted(found.items()):
        source, current = text_of(src), text_of(path)
        if source is None:
            continue
        try:
            new = renderer(tomllib.loads(source))
        except (tomllib.TOMLDecodeError, KeyError, TypeError):
            continue
        if new != current:
            out.append((dst_of(path), dst_of(src), new))
    return out
