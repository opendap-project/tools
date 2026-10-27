"""S-expressions of KiCad files for the schematic craft check (tools/sch-craft-check.py): parse, child
lookups, properties, text sizes and the grid. bind(names) gives it the check script's constants. Python 3.9."""
import json
import os
import re
import subprocess
import sys

GRID = None  # bound from the check script (bind)


def bind(names):
    """Take the check script's upper-case constants (its limits and sets, read from tools/craft/rules.toml)."""
    globals().update({k: v for k, v in names.items() if k.isupper()})


# ---------------------------------------------------------------- S-expressions

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse(text):
    """Parse one S-expression into nested lists of strings."""
    stack, cur, pos = [], [], 0
    while True:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip():
                raise ValueError(f"unreadable S-expression at offset {pos}")
            break
        pos = m.end()
        if m.group(1):
            stack.append(cur)
            cur = []
        elif m.group(2):
            done, cur = cur, stack.pop()
            cur.append(done)
        elif m.group(3) is not None:
            s = m.group(3)
            cur.append(re.sub(r'\\(.)', lambda x: {"n": "\n", "t": "\t"}.get(x.group(1), x.group(1)), s))
        else:
            cur.append(m.group(4))
    return cur[0] if cur else []


def kids(node, name):
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == name]


def kid(node, name):
    for c in node[1:]:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def val(node, name, default=None):
    k = kid(node, name)
    return k[1] if k is not None and len(k) > 1 else default


def xy(node):
    at = kid(node, "at")
    if at is None:
        return None
    return float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0


def flag(node, name):
    """True for (name yes), a bare `name` token, or (name); (power global) and (power local) also
    count for `power` — KiCad writes all of these across versions."""
    for c in node[1:]:
        if c == name:
            return True
        if isinstance(c, list) and c and c[0] == name:
            return len(c) == 1 or c[1] in ("yes", "global", "local")
    return False


def props(node):
    out = {}
    for p in kids(node, "property"):
        out[p[1]] = p
    return out


def prop_text(node, name, default=None):
    p = props(node).get(name)
    return p[2] if p is not None and len(p) > 2 else default


def hidden(prop):
    eff = kid(prop, "effects")
    return flag(prop, "hide") or (eff is not None and flag(eff, "hide"))


def font_of(node):
    eff = kid(node, "effects")
    return kid(eff, "font") if eff is not None else None


def text_size(node):
    f = font_of(node)
    size = kid(f, "size") if f is not None else None
    return float(size[1]) if size is not None else None


def on_grid(v, g=None):
    g = GRID if g is None else g
    return abs(v / g - round(v / g)) < 1e-3


def fmt(x, y):
    return f"({x:g}, {y:g})"


def load_toml(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        import tomllib  # Python 3.11+
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except ImportError:
        py = os.environ.get("ODAPKIT_PYTHON3", "python3")
        res = subprocess.run([py, "-c", "import json,sys,tomllib;print(json.dumps(tomllib.load(open(sys.argv[1],'rb'))))", path],
                             capture_output=True, text=True)
        if res.returncode:
            sys.exit(f"sch-craft-check: cannot read {path}: {res.stderr.strip()}")
        return json.loads(res.stdout)
