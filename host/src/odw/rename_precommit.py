"""One pre-commit configuration for a cut root, from the configurations its folders brought (RFC 0017 §7 step 4).

The configurations follow the workspace's layout (`repos:`, `- repo:` blocks, `- id:` hooks, one `key: value` per
line), so they are read and written as text, comments kept, with no YAML library. Rules (docs/specs/rename.md):

- a remote hook every folder pins identically is kept once, unscoped; one only some folders pin is scoped to them;
- a remote hook served by one of the root's own sources is dropped: the folder that provides it runs its local copy
  for the whole root, with its script entry under the folder;
- every other local hook is scoped to its folder (`files: ^<folder>/…`) and runs inside it (`sh -c 'cd <folder> &&
  …'`); an id two folders share gets the folder as a suffix.
"""
import dataclasses
import re

PLAIN_START = set("-?:,[]{}#&*!|>'\"%@`") | {" ", "\t"}
YAML_WORDS = {"true", "false", "yes", "no", "on", "off", "null", "~", ""}


def scalar(value):
    """A YAML scalar for `value`: plain when that reads back the same, else single-quoted."""
    v = str(value)
    unsafe = (v.lower() in YAML_WORDS or v[0] in PLAIN_START or v[-1] in " \t:" or ": " in v or " #" in v
              or re.fullmatch(r"[-+]?[0-9][0-9_.eE+-]*", v))
    return "'" + v.replace("'", "''") + "'" if unsafe else v


def _unquote(raw):
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("''", "'")
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return raw


def _split_comment(raw):
    """(value, trailing comment) of a raw `key: value  # comment` value."""
    if raw[:1] in "'\"":
        end = raw.find(raw[0], 1)
        while raw[0] == "'" and end != -1 and raw[end + 1:end + 2] == "'":
            end = raw.find("'", end + 2)
        if end != -1:
            return raw[:end + 1], raw[end + 1:].strip()
    m = re.search(r"\s+#", raw)
    return (raw[:m.start()], raw[m.start():].strip()) if m else (raw, "")


@dataclasses.dataclass
class Hook:
    comments: list  # comment lines before the hook, without indentation
    items: list  # [key, raw value, trailing comment, continuation lines (dedented)]

    def get(self, key):
        for k, raw, _, more in self.items:
            if k == key:
                return None if more and not raw else (raw if raw[:1] in "[{" else _unquote(raw))
        return None

    def set(self, key, value):
        for item in self.items:
            if item[0] == key:
                item[1], item[3] = scalar(value), []
                return
        self.items.append([key, scalar(value), "", []])

    def signature(self):
        return tuple((k, raw, tuple(more)) for k, raw, _, more in self.items)

    def render(self, indent):
        pad = " " * indent
        out = [f"{pad}{c}" for c in self.comments]
        for n, (k, raw, comment, more) in enumerate(self.items):
            lead = f"{pad}- " if n == 0 else f"{pad}  "
            out.append(f"{lead}{k}:" + (f" {raw}" if raw else "") + (f"  {comment}" if comment else ""))
            out += [f"{pad}  {line}" for line in more]
        return out


@dataclasses.dataclass
class Block:
    comments: list
    repo: str
    rev: str | None
    hooks: list


@dataclasses.dataclass
class Config:
    header: list
    blocks: list
    other: list  # top-level lines this module does not merge (reported)

    def hooks(self):
        return [h for b in self.blocks for h in b.hooks]

    def hook(self, id_):
        return next((h for h in self.hooks() if h.get("id") == id_), None)


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _hooks(lines):
    """Hooks of a `- id:` list (any indentation), comments before a hook attached to it."""
    hooks, comments, base, key_at = [], [], None, None
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("#") and (base is None or _indent(line) <= base):
            comments.append(s)
            continue
        if s.startswith("- ") and (base is None or _indent(line) == base):
            base, key_at = _indent(line), _indent(line) + 2
            k, _, v = s[2:].partition(":")
            hooks.append(Hook(comments, [[k.strip(), *_split_comment(v.strip()), []]]))
            comments = []
        elif hooks and _indent(line) == key_at and not s.startswith("#") and ":" in s and not s.startswith("- "):
            k, _, v = s.partition(":")
            hooks[-1].items.append([k.strip(), *_split_comment(v.strip()), []])
        elif hooks:
            hooks[-1].items[-1][3].append(line[key_at:] if _indent(line) >= key_at else s)
    return hooks, comments


def parse(text):
    lines = text.split("\n")
    header, blocks, other, i = [], [], [], 0
    while i < len(lines) and lines[i].strip() != "repos:":
        (header if lines[i].startswith("#") or not lines[i].strip() else other).append(lines[i])
        i += 1
    body, pending, cur, in_hooks = lines[i + 1:], [], None, False
    for line in body:
        s = line.strip()
        if s.startswith("- repo:"):
            cur, in_hooks = [pending, _unquote(_split_comment(s[7:].strip())[0]), None, []], False
            blocks.append(cur)
            pending = []
        elif cur is not None and not in_hooks and s.startswith("rev:"):
            cur[2] = _unquote(_split_comment(s[4:].strip())[0])
        elif cur is not None and s == "hooks:":
            in_hooks = True
        elif line and _indent(line) == 0 and not s.startswith("#"):
            other.append(line)
            cur = None
        elif cur is not None and in_hooks:
            cur[3].append(line)
        elif s.startswith("#"):
            pending.append(s)
    out, carry = [], []
    for comments, repo, rev, hook_lines in blocks:
        hooks, trailing = _hooks(hook_lines)
        out.append(Block(carry + comments, repo, rev, hooks))
        carry = trailing  # comments after a block's last hook introduce the next block
    return Config([h for h in header if h.strip()], out, [o for o in other if o.strip()])


def _alternatives(rx):
    out, cur, depth, cls, esc = [], "", 0, False, False
    for ch in rx:
        if esc:
            cur, esc = cur + ch, False
            continue
        if ch == "\\":
            cur, esc = cur + ch, True
            continue
        if cls:
            cls = ch != "]"
        elif ch == "[":
            cls = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "|" and depth == 0:
            out.append(cur)
            cur = ""
            continue
        cur += ch
    return out + [cur]


def _folder_rx(folders):
    names = [f.replace(".", r"\.") for f in ([folders] if isinstance(folders, str) else folders)]
    return names[0] if len(names) == 1 else f"(?:{'|'.join(names)})"


def scope(rx, folders):
    """The `files`/`exclude` pattern `rx` (searched from the folder) as a pattern of the root, under `folders`."""
    prefix = f"^{_folder_rx(folders)}/"
    if rx is None:
        return prefix
    parts = [a[1:] if a.startswith("^") else f".*(?:{a})" for a in _alternatives(rx)]
    return prefix + parts[0] if len(parts) == 1 else f"{prefix}(?:{'|'.join(parts)})"


def _in_folder(entry, folder):
    """`entry` run from inside `folder`: `sh -c 'cd <folder> && <entry>'`."""
    m = re.fullmatch(r"sh -c '(.*)'", entry, re.S)
    inner = m.group(1) if m and "'" not in m.group(1) else entry.replace("'", "'\\''")
    return f"sh -c 'cd {folder} && {inner}'"


def _repo_name(url):
    return url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]


def _scope_hook(h, folders):
    h.set("files", scope(h.get("files"), folders))
    if h.get("exclude") is not None:
        h.set("exclude", scope(h.get("exclude"), folders))


def _local(h, folder, suffix):
    """A folder's own local hook, scoped to it and run inside it."""
    h = Hook(list(h.comments), [list(i[:3]) + [list(i[3])] for i in h.items])
    if suffix:
        h.set("id", f"{h.get('id')}-{folder.rsplit('/', 1)[-1]}")
    h.set("name", f"{folder} {h.get('name') or h.get('id')}")
    entry, lang = h.get("entry") or "", h.get("language")
    if lang == "script":
        h.set("entry", f"{folder}/{entry}")
    elif str(h.get("pass_filenames")).lower() == "false":
        h.set("entry", _in_folder(entry, folder))
    elif "/" in entry.split(" ")[0] and not entry.startswith("/"):
        h.set("entry", f"{folder}/{entry}")
    _scope_hook(h, folder)
    return h


def merge(configs, own):
    """(text, report) of one configuration for `configs` (folder → its config text); `own` is the set of the
    root's source repository names (their remote hooks are served by the root itself)."""
    parsed = {f: parse(t) for f, t in sorted(configs.items())}
    report = {"shared": 0, "scoped": 0, "provided": 0, "dropped": [], "warnings": []}
    remote, dropped = {}, set()  # (repo, rev) -> {signature-key -> (hook, [folders])}
    for f, cfg in parsed.items():
        if cfg.other:
            report["warnings"].append({"code": "config.top-level", "message": f"{f}: top-level keys not merged: "
                                       + "; ".join(o.strip() for o in cfg.other)})
        for b in cfg.blocks:
            if b.repo == "local":
                continue
            if _repo_name(b.repo) in own:
                for h in b.hooks:
                    dropped.add(h.get("id"))
                    report["dropped"].append(f"{_repo_name(b.repo)} {h.get('id')}")
                continue
            group = remote.setdefault((b.repo, b.rev), {"comments": b.comments, "hooks": {}})
            for h in b.hooks:
                group["hooks"].setdefault((h.get("id"), h.signature()), (h, []))[1].append(f)
    report["dropped"] = sorted(set(report["dropped"]))
    revs = {}
    for repo, rev in remote:
        revs.setdefault(repo, []).append(rev)
    for repo, rs in revs.items():
        if len(rs) > 1:
            report["warnings"].append({"code": "config.rev-differs", "message": f"{repo} pinned at {', '.join(rs)}"})
    local = {f: [h for b in cfg.blocks if b.repo == "local" for h in _with_block_comments(b)]
             for f, cfg in parsed.items()}
    seen = {}
    for f, hooks in local.items():
        for h in hooks:
            if h.get("id") not in dropped:
                seen.setdefault(h.get("id"), set()).add(f)
    out = list(next(iter(parsed.values())).header) if parsed else []
    out.append("repos:")
    for (repo, rev), group in remote.items():
        out += [f"  {c}" for c in group["comments"]] + [f"  - repo: {repo}"]
        out += [f"    rev: {scalar(rev)}"] if rev else []
        out.append("    hooks:")
        for h, fs in group["hooks"].values():
            if len(fs) < len(parsed):
                h = Hook(list(h.comments), [list(i[:3]) + [list(i[3])] for i in h.items])
                _scope_hook(h, fs)
                report["scoped"] += 1
            else:
                report["shared"] += 1
            out += h.render(6)
    provided = [(f, h) for f, hooks in local.items() for h in hooks if h.get("id") in dropped]
    if provided:
        out += ["  # Hooks this repository provides, run from its own tree for every folder.", "  - repo: local",
                "    hooks:"]
        for f, h in provided:
            h = Hook(list(h.comments), [list(i[:3]) + [list(i[3])] for i in h.items])
            if h.get("language") == "script":
                h.set("entry", f"{f}/{h.get('entry')}")
            out += h.render(6)
            report["provided"] += 1
    for f, hooks in local.items():
        mine = [h for h in hooks if h.get("id") not in dropped]
        if not mine:
            continue
        out += [f"  # {f}/", "  - repo: local", "    hooks:"]
        for h in mine:
            out += _local(h, f, len(seen[h.get("id")]) > 1).render(6)
            report["scoped"] += 1
    return "\n".join(out) + "\n", report


def _with_block_comments(block):
    """A local block's hooks, the block's own comment carried by its first hook."""
    if not block.hooks:
        return []
    first = block.hooks[0]
    return [Hook(block.comments + first.comments, first.items), *block.hooks[1:]]


def manifest(text, folder):
    """A folder's `.pre-commit-hooks.yaml` as the root's: script entries are relative to the repository root."""
    lines = text.split("\n")
    first = next((n for n, line in enumerate(lines) if line.startswith("- ")), len(lines))
    head = [line for line in lines[:first] if line.startswith("#")]
    hooks, _ = _hooks(lines[first:])
    for h in hooks:
        if h.get("language") == "script":
            h.set("entry", f"{folder}/{h.get('entry')}")
    return head, hooks


def render_manifest(parts):
    """One manifest from [(head comments, hooks)] of every folder that provides hooks."""
    out = list(parts[0][0]) if parts else []
    for _, hooks in parts:
        for h in hooks:
            out += h.render(0)
    return "\n".join(out) + "\n"
