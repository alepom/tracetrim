"""`tracetrim init`: put tracetrim in front of the MCP servers a host already uses.

  tracetrim init --host cursor                 # dry run: shows what would change, writes nothing
  tracetrim init --host cursor --write         # backup, servers moved to WORKDIR/servers.json, one entry in the host
  tracetrim init --undo BACKUP                 # the host's file as it was, byte for byte

It reads the host's MCP configuration, moves the servers tracetrim can stand
in front of to `WORKDIR/servers.json` (mode 0600) and leaves in the host one
entry that starts `tracetrim proxy --servers ... --auto WORKDIR`. A server it
cannot proxy faithfully stays in the host, untouched, and the output says why.

Those files hold keys. Nothing here prints a value: not of `env` or `headers`,
not an argument, not the path or the query of a URL, not in an error. What is
shown is names, counts and the scheme and host of a URL.

The formats are the ones each host documents (docs/hosts.md has the sources).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

MASK = "****"                  # ASCII: any terminal encoding can show it
BACKUP_MARK = ".tracetrim-backup-"
DONE_LINE = ("Last step, required if the tool tracetrim_done is available: when your final answer is ready, "
             "call tracetrim_done once with the complete answer, before you show it; then show the same answer.")


@dataclass(frozen=True)
class Host:
    name: str
    title: str
    key: str                    # the object that holds the servers
    user: bool                  # has a file for the user
    project: str | None         # the project's file, relative to the project folder
    default_scope: str
    variables: str              # "none" | "claude-code" | "env-colon": what the host resolves in a value
    typed: bool                 # entries carry "type": "stdio"

    def user_path(self) -> Path:
        home = Path.home()
        if self.name == "cursor":
            return home / ".cursor" / "mcp.json"
        if sys.platform == "darwin":
            return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        if sys.platform == "win32":
            return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "Claude" / "claude_desktop_config.json"
        return home / ".config" / "Claude" / "claude_desktop_config.json"


HOSTS = {h.name: h for h in (
    Host("claude-desktop", "Claude Desktop", "mcpServers", True, None, "user", "none", False),
    Host("claude-code", "Claude Code", "mcpServers", False, ".mcp.json", "project", "claude-code", True),
    Host("cursor", "Cursor", "mcpServers", True, ".cursor/mcp.json", "user", "env-colon", True),
    Host("vscode", "VS Code", "servers", False, ".vscode/mcp.json", "project", "env-colon", True),
)}

# What the proxy reads in a server (tracetrim.proxy.transport_for). Anything else would be
# dropped silently behind the proxy, so a server that has it stays in the host.
KNOWN = {"type", "command", "args", "env", "cwd", "url", "headers"}
# The fields in which each host documents that it resolves variables.
RESOLVED = {"none": set(),
            "claude-code": {"command", "args", "env", "url", "headers"},
            "env-colon": {"command", "args", "env", "url", "headers"}}

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_CC = re.compile(r"\$\{(" + _NAME + r")(?::-([^}]*))?\}")          # ${VAR}  ${VAR:-default}
_ENV = re.compile(r"\$\{env:(" + _NAME + r")\}")                    # ${env:NAME}
_HOSTVAR = re.compile(r"\$\{(input):[^}]*\}|\$\{(workspaceFolder|workspaceFolderBasename|userHome|pathSeparator|/)\}")
# What os.path.expandvars, which the proxy applies to servers.json, would take for a variable.
# On Windows (ntpath) also $-name, %NAME%, and $$ and %% that it always collapses to one character.
_NT = os.name == "nt"            # a flag of the module, so that the tests can try Windows' rules
_EXPANDS = re.compile(r"\$[A-Za-z0-9_{]")
_EXPANDS_NT = re.compile(r"\$[A-Za-z0-9_{-]|%[^%]+%|\$\$|%%")


class Refuse(Exception):
    """This server stays in the host. The message is shown: it never holds a value."""


def _check_shape(spec) -> None:
    if not isinstance(spec, dict):
        raise Refuse("not an object")
    nulls = sorted(k for k, v in spec.items() if v is None)
    if nulls:                  # what a host does with null is not documented: the server stays as it is
        raise Refuse("a field set to null: " + ", ".join(_safe_key(k) for k in nulls))
    unknown = sorted(k for k in spec if k not in KNOWN)
    if unknown:
        raise Refuse("field tracetrim does not know: " + ", ".join(_safe_key(k) for k in unknown))
    has_cmd, has_url = "command" in spec, "url" in spec
    if has_cmd == has_url:
        raise Refuse("needs exactly one of 'command' (stdio) and 'url' (streamable HTTP)")
    kind = spec.get("type")
    if kind == "sse":
        raise Refuse("SSE transport: tracetrim speaks streamable HTTP only")
    if kind not in (None, "stdio", "http", "streamable-http") or (kind is not None and (kind == "stdio") == has_url):
        raise Refuse("a 'type' that does not match the server")
    ok = (all(isinstance(spec.get(f, ""), str) for f in ("command", "url", "cwd"))
          and isinstance(spec.get("args") or [], list) and all(isinstance(v, str) for v in spec.get("args") or [])
          and all(isinstance(spec.get(f) or {}, dict) and all(isinstance(v, str) for v in (spec.get(f) or {}).values())
                  for f in ("env", "headers")))
    if not ok:
        raise Refuse("a value that is not text (tracetrim passes text only)")
    if has_url and any(f in spec for f in ("args", "env", "cwd")) or has_cmd and "headers" in spec:
        raise Refuse("fields of a local server and of a remote one together")


def _translate(host: Host, spec: dict, http: bool) -> tuple[dict, dict]:
    """The server as servers.json holds it, and the references it needs:
    {NAME: the expression the host resolves}. Raises Refuse when a value would
    not mean to tracetrim what it means to the host."""
    refs: dict[str, str] = {}

    def one(fld: str, s: str) -> str:
        resolved = fld in RESOLVED[host.variables]
        m = _HOSTVAR.search(s) if host.variables == "env-colon" else None
        if m:
            raise Refuse(f"variable of the host that tracetrim does not resolve: ${{{'input:…' if m.group(1) else m.group(2)}}}")

        def sub(m):
            name = m.group(1)
            expr = m.group(0)
            if host.variables == "claude-code" and m.group(2) is not None and http:
                raise Refuse(f"${{{name}:-…}}: with --http no host is there to resolve the default")
            if refs.setdefault(name, expr) != expr:
                raise Refuse(f"variable {name} is used with different defaults")
            return "${" + name + "}"

        rest = s
        if resolved and host.variables == "claude-code":
            rest, s = _CC.sub("", s), _CC.sub(sub, s)
        elif resolved and host.variables == "env-colon":
            rest, s = _ENV.sub("", s), _ENV.sub(sub, s)
        # On Windows the translated value too: ntpath collapses $$ and %% anywhere, and leaves
        # a reference between single quotes as it is.
        if (_EXPANDS_NT if _NT else _EXPANDS).search(rest) or _NT and (
                re.search(r"\$\$|%%", s) or (s != rest and "'" in s)):
            raise Refuse("a '$' that tracetrim would expand and the host leaves as it is" if host.variables == "none"
                         else "a '$' form that tracetrim would expand and the host does not document there")
        return s

    out = {}
    for k, v in spec.items():
        if k == "args":
            out[k] = [one(k, x) for x in v]
        elif k in ("env", "headers"):
            out[k] = {n: one(k, x) for n, x in v.items()}
        elif k in ("command", "url", "cwd"):
            out[k] = one(k, v)
        else:
            out[k] = v
    return out, refs


# ---------------------------------------------------------------- what may be shown

def _safe_key(k) -> str:
    """A name (of a server, a field, a variable) is shown; one that does not look like a name is not."""
    k = str(k)
    return k if re.fullmatch(r"[\w .@/:-]{1,64}", k) else "(name not shown)"


def _keys(d: dict) -> str:
    return ", ".join(f"{_safe_key(k)}={MASK}" for k in d)


def describe(spec: dict) -> str:
    """One line on a server, without any of its values."""
    if not isinstance(spec, dict):
        return ""
    if isinstance(spec.get("url"), str):
        try:
            u = urlsplit(spec["url"])
            plain = u.scheme in ("http", "https") and u.hostname and re.fullmatch(r"[\w.-]+", u.hostname)
            where = f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "") if plain else "remote"
        except ValueError:
            where = "remote"
        heads = spec.get("headers") if isinstance(spec.get("headers"), dict) else {}
        return where + (f"  headers: {_keys(heads)}" if heads else "  no headers (OAuth for tracetrim)")
    if isinstance(spec.get("command"), str):
        cmd = re.split(r"[\\/]", spec["command"])[-1]
        cmd = cmd if re.fullmatch(r"[\w.+-]{1,40}", cmd) else "command"
        n = len(spec.get("args") or []) if isinstance(spec.get("args"), list) else 0
        env = spec.get("env") if isinstance(spec.get("env"), dict) else {}
        return f"{cmd} ({n} arg{'' if n == 1 else 's'})" + (f"  env: {_keys(env)}" if env else "")
    return ""


def _show_ref(expr: str) -> str:
    return _CC.sub(lambda m: m.group(0) if m.group(2) is None else "${" + m.group(1) + ":-" + MASK + "}", expr)


# ---------------------------------------------------------------- files

def _read_json(path: Path, what: str):
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise SystemExit(f"tracetrim init: cannot read {what} {path} ({type(e).__name__})") from None
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise SystemExit(f"tracetrim init: {what} {path} is not UTF-8 text") from None
    except json.JSONDecodeError as e:
        raise SystemExit(f"tracetrim init: {what} {path} is not valid JSON (line {e.lineno}, column {e.colno}). "
                         "Nothing is rewritten; a file with comments has to be edited by hand") from None
    return raw, data


def _indent(raw: bytes):
    m = re.search(rb"\n([ \t]+)\S", raw)
    if not m:
        return 2
    return "\t" if m.group(1).startswith(b"\t") else len(m.group(1))


def _write(path: Path, data: bytes, mode: int) -> None:
    """Atomically, and with its permissions from the first byte."""
    # A name nobody can guess, created here and nowhere else: not a file or a link put there beforehand.
    tmp = path.with_name(f".{path.name}.tracetrim-tmp-{secrets.token_hex(8)}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, mode)              # outside the try: a name that exists is not ours to delete
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            if hasattr(os, "fchmod"):
                os.fchmod(f.fileno(), mode)     # the mode asked for, whatever the umask
            os.fsync(f.fileno())
        if not hasattr(os, "fchmod"):
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dump(data, indent=2, newline=True) -> bytes:
    return (json.dumps(data, indent=indent, ensure_ascii=False) + ("\n" if newline else "")).encode("utf-8")


# ---------------------------------------------------------------- the entry in the host

def executable() -> list[str]:
    """The tracetrim in use, as an absolute path: graphical hosts often do not
    have the terminal's PATH."""
    here = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if here and here.stem.lower() == "tracetrim" and here.exists():
        return [str(here.absolute())]
    for name in ("tracetrim", "tracetrim.exe"):
        beside = Path(sys.executable).parent / name
        if beside.exists():
            return [str(beside)]
    found = shutil.which("tracetrim")
    if found:
        return [str(Path(found).absolute())]
    return [str(Path(sys.executable).absolute()), "-m", "tracetrim.cli"]


def _option(args: list[str], name: str) -> str | None:
    """The value of `--name VALUE` or `--name=VALUE`, as argparse reads them."""
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a[len(name) + 1:]
    return None


def _ours(spec) -> dict | None:
    """If this entry is a tracetrim proxy: where its servers file and work directory are.
    {"servers": None} for one without --servers (--upstream-url / --upstream-cmd): it is
    tracetrim all the same, and must not be wrapped in a second one."""
    if not isinstance(spec, dict) or not isinstance(spec.get("command"), str) or not isinstance(spec.get("args"), list):
        return None
    args = [a for a in spec["args"] if isinstance(a, str)]
    stem = re.split(r"[\\/]", spec["command"])[-1].lower()
    stem = stem[:-4] if stem.endswith(".exe") else stem
    if stem != "tracetrim":
        if args[:2] != ["-m", "tracetrim.cli"]:
            return None
        args = args[2:]
    if args[:1] != ["proxy"]:
        return None
    servers, work = _option(args, "--servers"), _option(args, "--auto")
    if not servers:
        return {"servers": None, "workdir": None}
    return {"servers": Path(servers), "workdir": Path(work) if work else Path(servers).parent}


# ---------------------------------------------------------------- where things go

def _project_root(host: Host, file: Path) -> Path:
    return file.parent.parent if file.parent.name in (".cursor", ".vscode") else file.parent


def locate(host: Host, file: str | None, scope: str | None) -> tuple[Path, str, Path | None]:
    """(the host's file, "user" | "project", the project folder)."""
    if file:
        path = Path(file).expanduser().absolute()
        if host.user and (scope == "user" or not host.project or path == host.user_path().absolute()):
            return path, "user", None
        return path, "project", _project_root(host, path)
    if scope == "user" and not host.user:
        raise SystemExit(f"tracetrim init: no user file is supported for {host.title}; give the file with --file")
    if scope == "project" and not host.project:
        raise SystemExit(f"tracetrim init: {host.title} has no project file")
    scope = scope or host.default_scope
    if scope == "user":
        return host.user_path().absolute(), "user", None
    root = Path.cwd()
    return (root / host.project).absolute(), "project", root


def default_workdir(host: Host, scope: str, root: Path | None) -> tuple[Path, str]:
    base = Path.home() / ".tracetrim" / host.name
    if scope == "user" or root is None:
        return base, "the user's configuration: one work directory for the host"
    tag = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:8]
    name = re.sub(r"[^\w.-]", "_", root.name) or "project"
    return base / f"{name}-{tag}", f"one work directory per project (tracetrim learns per directory): {root}"


def _git(args: list[str], cwd: Path) -> int | None:
    try:
        return subprocess.run(["git", *args], cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              stdin=subprocess.DEVNULL, timeout=20).returncode
    except (OSError, subprocess.SubprocessError):
        return None


def _unignored_repo(file: Path) -> Path | None:
    """The git repository that holds `file` without ignoring it, if any."""
    existing = next(p for p in file.parents if p.is_dir())
    repo = next((d for d in [existing, *existing.parents] if (d / ".git").exists()), None)
    if repo is None or _git(["check-ignore", "-q", str(file)], existing) == 0:
        return None
    return repo


def git_problems(workdir: Path, servers: Path | None = None) -> tuple[str | None, list[str]]:
    """(why the servers file may not be written, warnings). `servers` is the file that is
    written: an existing entry's can be outside the work directory."""
    servers = servers or workdir / "servers.json"
    existing = next(p for p in [workdir, *workdir.parents] if p.is_dir())
    warnings = []
    for d in [existing, *existing.parents]:
        if (d / ".mcp.json").is_file() and _git(["ls-files", "--error-unmatch", ".mcp.json"], d) == 0:
            warnings.append(f"{workdir} is inside {d}, a project whose .mcp.json is under version control: "
                            "the keys in servers.json sit next to files that are shared")
            break
    if (repo := _unignored_repo(workdir / "servers.json")) is not None:
        return (f"{workdir} is inside the git repository {repo} and servers.json would not be ignored there: "
                "it holds keys. Choose another --workdir, add it to .gitignore, or pass --force"), warnings
    if servers != workdir / "servers.json" and (repo := _unignored_repo(servers)) is not None:
        return (f"the servers file {servers} is inside the git repository {repo} and would not be ignored there: "
                "it holds keys. Add it to .gitignore, or pass --force"), warnings
    return None, warnings


# ---------------------------------------------------------------- the plan

@dataclass
class Plan:
    host: Host
    file: Path
    scope: str
    workdir: Path
    why_workdir: str
    name: str
    http: int | None = None
    raw: bytes = b""
    config: dict = field(default_factory=dict)
    servers_raw: bytes | None = None                 # the servers file as read, None if there was none
    existing: dict | None = None                     # the entry already in the host, if any
    moved: dict = field(default_factory=dict)        # name -> spec for servers.json
    already: list = field(default_factory=list)      # in the host and, identical, in servers.json already
    left: dict = field(default_factory=dict)         # name -> reason
    refs: dict = field(default_factory=dict)         # NAME -> expression in the host's syntax
    translated: dict = field(default_factory=dict)   # server -> [NAME, ...]
    servers_file: dict = field(default_factory=dict)  # the whole servers.json after the change
    entry: dict = field(default_factory=dict)
    refuse: str | None = None
    warnings: list = field(default_factory=list)

    @property
    def servers_path(self) -> Path:
        return self.existing["servers"] if self.existing else self.workdir / "servers.json"

    @property
    def leaving(self) -> list:
        return [*self.moved, *self.already]


def plan(host: Host, file: Path, scope: str, root: Path | None, workdir: str | None, name: str,
         exclude: list[str], http: int | None) -> Plan:
    raw, config = _read_json(file, f"the {host.title} file")
    block = config.get(host.key, {}) if isinstance(config, dict) else None
    other = "servers" if host.key == "mcpServers" else "mcpServers"
    if not isinstance(block, dict) or (host.key not in config and other in config):
        raise SystemExit(f"tracetrim init: {file} has no '{host.key}' object: is it a {host.title} file?")
    unknown = sorted(n for n in exclude if n not in block)
    if unknown:
        raise SystemExit("tracetrim init: --exclude names no server of the file: " + ", ".join(map(_safe_key, unknown)))

    found = {n: o for n, s in block.items() if (o := _ours(s))}
    bare = [n for n in found if found[n]["servers"] is None]
    found = {n: o for n, o in found.items() if n not in bare}
    if len(found) > 1:
        raise SystemExit("tracetrim init: the file has more than one tracetrim entry (" +
                         ", ".join(map(_safe_key, found)) + "): keep one")
    wd, why = default_workdir(host, scope, root)
    if workdir:
        wd, why = Path(workdir).expanduser().absolute(), "given with --workdir"
    p = Plan(host, file, scope, wd, why, name, http, raw, config)
    if found and http is not None:
        raise SystemExit("tracetrim init: this host already goes through tracetrim over stdio (entry " +
                         ", ".join(map(_safe_key, found)) + "): undo first to move to --http")
    if found:
        (p.name, p.existing), = found.items()
        if workdir and wd != p.existing["workdir"].absolute():
            raise SystemExit(f"tracetrim init: this host already goes through tracetrim with the work directory "
                             f"{p.existing['workdir']}: leave --workdir out, or undo first")
        p.workdir, p.why_workdir = p.existing["workdir"], "the one the host's tracetrim entry already uses"
    elif name in block and http is None:      # over HTTP nothing is added to the file
        raise SystemExit(f"tracetrim init: the host already has a server called {_safe_key(name)} that is not "
                         "the entry of `tracetrim init`: choose another name with --name")

    on_disk = {}
    if p.servers_path.exists():
        p.servers_raw, data = _read_json(p.servers_path, "the servers file")
        on_disk = data.get("mcpServers", data) if isinstance(data, dict) else None
        if not isinstance(on_disk, dict):
            raise SystemExit(f"tracetrim init: {p.servers_path} is not a servers file")
        if "mcpServers" in data and len(data) > 1:     # a whole client's file: rewriting it would lose the rest
            raise SystemExit(f"tracetrim init: {p.servers_path} holds more than an 'mcpServers' object ("
                             + ", ".join(sorted(_safe_key(k) for k in data if k != "mcpServers"))
                             + "): tracetrim init only rewrites a file of servers. Nothing was written: point the "
                             "entry's --servers to a file of its own")

    env = block[p.name].get("env") if p.existing else None
    p.refs = dict(env) if isinstance(env, dict) else {}
    kept_refs = dict(p.refs)
    for n, spec in block.items():
        if n in found:
            continue
        if n in bare:
            p.left[n] = "a tracetrim proxy without --servers (--upstream-url or --upstream-cmd): left as it is"
            continue
        if n in exclude:
            p.left[n] = "excluded with --exclude"
            continue
        try:
            _check_shape(spec)
            out, refs = _translate(host, spec, http is not None)
            for var, expr in refs.items():
                if p.refs.get(var, expr) != expr:
                    raise Refuse(f"variable {var} is already passed to tracetrim with another expression")
        except Refuse as e:
            p.left[n] = str(e)
            continue
        if n in on_disk and on_disk[n] != out:
            raise SystemExit(f"tracetrim init: {p.servers_path} already has a server called {_safe_key(n)} with a "
                             "different definition. Nothing was written: rename one of the two, or --exclude it")
        p.refs.update(refs)
        if refs:
            p.translated[n] = sorted(refs)
        if n in on_disk:
            p.already.append(n)
        else:
            p.moved[n] = out

    p.servers_file = {**on_disk, **p.moved}
    stray = [n for n in on_disk if n not in p.leaving]
    if stray and not p.existing and p.leaving:
        # Left by an earlier init (then undone): with them in the file, servers the user removed
        # from the host would come back behind tracetrim.
        p.refuse = (f"{p.servers_path} already holds servers that are not among those moved now: "
                    + ", ".join(map(_safe_key, stray)) + ". They would be served again through tracetrim. "
                    "Remove them from that file, choose another --workdir, or pass --force to keep them")
    # The references of the whole servers file, not only of the servers moved now: the host passes them.
    host_expr = {"claude-code": "${{{}}}", "env-colon": "${{env:{}}}"}.get(host.variables)
    if host_expr:
        for spec in p.servers_file.values():
            for var in re.findall(r"\$\{(" + _NAME + r")\}", json.dumps(spec)):
                p.refs.setdefault(var, host_expr.format(var))
    if p.existing:
        p.entry = dict(block[p.name])
        if p.refs != kept_refs:
            p.entry["env"] = dict(p.refs)
    else:
        p.entry = {**({"type": "stdio"} if host.typed else {}), "command": (exe := executable())[0],
                   "args": [*exe[1:], "proxy", "--servers", str(p.servers_path), "--auto", str(p.workdir)],
                   **({"env": dict(p.refs)} if p.refs else {})}
    if p.leaving or http is not None:
        refuse, p.warnings = git_problems(p.workdir, p.servers_path)
        p.refuse = " ".join(r for r in (p.refuse, refuse) if r) or None
    return p


def rewritten(p: Plan) -> bytes:
    """The host's file with the servers that moved replaced, in place, by one entry."""
    block, out, placed = p.config[p.host.key], {}, False
    for n, spec in block.items():
        if n == p.name and p.existing:
            out[n], placed = p.entry, True
        elif n in p.leaving:
            if not placed and not p.existing:
                out[p.name], placed = p.entry, True
        else:
            out[n] = spec
    config = {k: (out if k == p.host.key else v) for k, v in p.config.items()}
    return _dump(config, _indent(p.raw), p.raw.endswith(b"\n"))


# ---------------------------------------------------------------- output

def _entry_text(p: Plan) -> str:
    """The host's entry. An existing one is the user's to edit (auth, envFile, cwd, ...): of it
    only what init itself writes is shown, every other field by its name alone."""
    shown, ok_args = {}, {"proxy", "--servers", "--auto", "-m", "tracetrim.cli", str(p.servers_path), str(p.workdir),
                          f"--servers={p.servers_path}", f"--auto={p.workdir}"}
    for k, v in p.entry.items():
        if k == "type":
            shown[k] = v if v == "stdio" else MASK
        elif k == "command":
            base = re.split(r"[\\/]", v)[-1] if isinstance(v, str) else ""
            shown[k] = (v if not p.existing or v == executable()[0] else
                        MASK + "/" + base if re.fullmatch(r"[\w.+-]{1,40}", base) else MASK)
        elif k == "args" and isinstance(v, list):
            shown[k] = [a if not p.existing or isinstance(a, str) and (a in ok_args or re.fullmatch(r"--[a-z-]+", a))
                        else MASK for a in v]
        elif k == "env" and isinstance(v, dict):    # references are shown, a default or anything else is not
            shown[k] = {_safe_key(n): x if isinstance(x, str) and _ENV.fullmatch(x) else
                        _show_ref(x) if isinstance(x, str) and _CC.fullmatch(x) else MASK for n, x in v.items()}
        else:
            shown[_safe_key(k)] = MASK
    return json.dumps({_safe_key(p.name): shown}, indent=2, ensure_ascii=False)


def render(p: Plan, write: bool) -> str:
    h, L = p.host, []
    n = len(p.config.get(h.key, {}))
    L.append(f"{h.title}: {p.file} ({n} server{'' if n == 1 else 's'})."
             + ("" if write else "  Dry run: nothing is written; add --write."))
    if p.existing:
        L.append(f"This host already goes through tracetrim (entry {_safe_key(p.name)}): it is not wrapped a second time.")
    width = max([len(_safe_key(x)) for x in [*p.moved, *p.already, *p.left]] or [0]) + 2
    if p.moved:
        L += ["", f"Behind tracetrim ({len(p.moved)})"]
        L += [f"  {_safe_key(n):<{width}}{describe(s)}" for n, s in p.moved.items()]
    if p.already:
        L += ["", f"Already in {p.servers_path.name}, not duplicated ({len(p.already)})"]
        L += [f"  {_safe_key(n):<{width}}" + ("removed from the host" if p.http is None else "") for n in p.already]
    if p.left:
        L += ["", f"Left in the host ({len(p.left)})"]
        L += [f"  {_safe_key(n):<{width}}{why}" for n, why in p.left.items()]
    if not p.leaving:
        L += ["", "Nothing to do: " + ("every server tracetrim can stand in front of is behind it already."
                                       if p.existing else "no server of this file can go behind tracetrim.")]
        return "\n".join(L) + "\n"
    if p.translated:
        L += ["", "References translated (the host resolves them and passes them to tracetrim; servers.json uses ${NAME})"]
        L += [f"  {_safe_key(n):<{width}}{', '.join(v)}" for n, v in p.translated.items()]

    L += ["", f"Work directory  {p.workdir}", f"                {p.why_workdir}"]
    L += [f"Servers file    {p.servers_path}  (mode 0600; {', '.join(map(_safe_key, p.servers_file))}; values as they are)"]
    if p.http is not None:
        L += _http_steps(p)
    else:
        L += ["", "Host entry" + (" (updated)" if p.existing and p.entry != p.config[h.key][p.name] else ""),
              *("  " + line for line in _entry_text(p).splitlines())]
    for w in p.warnings:
        L += ["", "Warning: " + w]
    if p.refuse:
        L += ["", ("Refused" if write else "With --write this is refused") + ": " + p.refuse]
    return "\n".join(L) + "\n"


def _http_steps(p: Plan) -> list[str]:
    cmd = " ".join(executable())
    L = ["", "Over HTTP the host's file is not rewritten. Steps:",
         "  1. choose a secret and start the proxy where the host can reach it:",
         "       export TRACETRIM_TOKEN=CHOOSE_A_SECRET",
         f"       {cmd} proxy --servers {p.servers_path} --auto {p.workdir} --http {p.http}",
         f"  2. in the host, add one MCP server: URL http://127.0.0.1:{p.http}/mcp (or your tunnel's address,",
         "     with --allowed-host), header 'Authorization: Bearer CHOOSE_A_SECRET'",
         "  3. in the host, switch off or remove: " + ", ".join(map(_safe_key, p.leaving))]
    if p.refs:
        L.append("  The servers file refers to these variables: set them in the shell that starts the proxy: "
                 + ", ".join(sorted(p.refs)))
    return L


def closing(p: Plan, written: bool, backup: Path | None) -> str:
    L = [""]
    if written and backup:
        L += [f"Backup          {backup}", f"Undo            tracetrim init --undo {backup}"]
    if p.http is None:
        L += [f"Then: restart {p.host.title}, so that it reads the file again."]
        if p.scope == "project":
            L += ["A project's file is usually shared: the entry holds paths of this machine and the backup sits "
                  "next to it. Keep both out of what you commit."]
    if any("url" in s and not s.get("headers") for s in p.moved.values()):
        L += [f"A remote server without headers uses OAuth: log in once with `tracetrim login --servers {p.servers_path}` "
              "(the login made in the host does not carry over)."]
    L += ["", "For hosts that do not load the skill, add this line to the agent's instructions:", "  " + DONE_LINE,
          "", "Learning needs tracetrim_done: without it only the telemetry is kept. Each work directory learns from "
          "what passes through it, so very different tasks in one directory make trimming less reliable.",
          f"Report: {p.workdir / 'report.html'}"]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- commands

def backup_path(file: Path) -> Path:
    base = file.with_name(file.name + BACKUP_MARK + time.strftime("%Y%m%d-%H%M%S"))
    path, i = base, 1
    while path.exists():
        i += 1
        path = base.with_name(f"{base.name}-{i}")
    return path


def _bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def apply(p: Plan, force: bool) -> Path | None:
    """Write the plan. Returns the backup of the host's file, if the host was rewritten."""
    if p.refuse and not force:
        raise SystemExit("tracetrim init: " + p.refuse)
    # The plan was made from the files as they were read: one saved since (by the host, by
    # the user) would be overwritten with content made from the old one. Checked before
    # anything is written, so that either everything is written or nothing is.
    for path, before in ((p.file, p.raw), (p.servers_path, p.servers_raw)):
        if _bytes(path) != before:
            raise SystemExit(f"tracetrim init: {path} changed while init was running. Nothing was written: "
                             "run init again")
    p.workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    p.servers_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write(p.servers_path, _dump({"mcpServers": p.servers_file}), 0o600)
    if p.http is not None:
        return None
    mode = (p.file.stat().st_mode & 0o777) or 0o600
    new = rewritten(p)
    backup = backup_path(p.file)
    _write(backup, p.raw, mode)
    # Next to the backup, what `--undo` needs to tell whether the host's file changed since: no content.
    _write(backup.with_name(backup.name + ".json"),
           _dump({"format": "tracetrim/init-backup@1", "file": str(p.file), "host": p.host.name,
                  "written_sha256": _sha(new), "backup_sha256": _sha(p.raw)}), 0o600)
    _write(p.file, new, mode)
    return backup


def undo(backup: str, force: bool) -> str:
    b = Path(backup).expanduser().absolute()
    if BACKUP_MARK not in b.name or b.name.endswith(".json"):
        raise SystemExit("tracetrim init: --undo takes the backup `init --write` made (…" + BACKUP_MARK + "DATE-TIME)")
    try:
        saved = b.read_bytes()
    except OSError as e:
        raise SystemExit(f"tracetrim init: cannot read the backup {b} ({type(e).__name__})") from None
    target, note = b.with_name(b.name.split(BACKUP_MARK)[0]), None      # the backup sits next to the original
    side = b.with_name(b.name + ".json")
    if side.exists():
        try:
            _, note = _read_json(side, "the note of the backup")
        except SystemExit:
            if not force:
                raise
            note = None                     # with --force an unreadable note counts as a missing one
    current = target.read_bytes() if target.exists() else None
    if current == saved:
        return f"{target} is already as in the backup: nothing to do.\n"
    if not force:
        if not isinstance(note, dict) or "written_sha256" not in note:
            raise SystemExit(f"tracetrim init: no note next to the backup says what `init` wrote in {target}, so it "
                             "cannot be told whether the file changed since. Pass --force to restore anyway")
        if note.get("backup_sha256") != _sha(saved):
            raise SystemExit("tracetrim init: the backup is not the one `init` made (it changed). Pass --force to restore it anyway")
        if current is None or _sha(current) != note["written_sha256"]:
            raise SystemExit(f"tracetrim init: {target} is no longer the file `init` wrote: restoring the backup would "
                             "lose the changes made since. Pass --force to restore anyway")
    _write(target, saved, (b.stat().st_mode & 0o777) or 0o600)
    return (f"{target} restored from {b.name}, byte for byte.\n"
            "The work directory is left as it is (traces, report, learning): its servers.json still holds your "
            "servers' keys; delete it if you no longer use tracetrim there.\n")


def claude_code_user(workdir: str | None) -> str:
    """Claude Code keeps the user's servers in ~/.claude.json, with the application's
    state: `init` neither writes nor reads it. These are the steps, by hand."""
    host = HOSTS["claude-code"]
    wd = Path(workdir).expanduser().absolute() if workdir else default_workdir(host, "user", None)[0]
    cmd = " ".join([*executable(), "proxy", "--servers", str(wd / "servers.json"), "--auto", str(wd)])
    return "\n".join([
        "Claude Code keeps the servers of the user (and local) scope in ~/.claude.json, together with the",
        "application's state: tracetrim init does not open that file. By hand:",
        f"  1. write your servers in {wd / 'servers.json'} as {{\"mcpServers\": {{...}}}}, readable only by you (chmod 600)",
        "  2. for each of them:   claude mcp remove NAME --scope user",
        f"  3. claude mcp add --transport stdio --scope user tracetrim -- {cmd}",
        "For a project's .mcp.json, or any file with an mcpServers object: tracetrim init --host claude-code [--file PATH]",
        ""])


def _say(text: str) -> None:
    """Print, whatever the terminal's encoding: what it cannot show is escaped, not a crash."""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(enc)
    except UnicodeEncodeError:
        text = text.encode(enc, "backslashreplace").decode(enc)
    except LookupError:
        text = text.encode("ascii", "backslashreplace").decode("ascii")
    sys.stdout.write(text)
    sys.stdout.flush()


# ---------------------------------------------------------------- the check after writing

CHECK_TIMEOUT = 120.0   # seconds; a first start on Windows, right after installing, can take more than 30


def _resolved_env(p: Plan) -> tuple[dict, list[str]]:
    """The entry's env as the host would pass it, resolved from init's own environment;
    and the names not set here (the host may set them: then the check can fail where the
    host would not)."""
    env, missing = {}, []
    for name, expr in (p.entry.get("env") or {}).items():
        m = _CC.fullmatch(expr) or _ENV.fullmatch(expr)
        if not m:
            env[name] = expr
            continue
        var, default = m.group(1), (m.group(2) if m.re is _CC else None)
        if var in os.environ:
            env[name] = os.environ[var]
        elif default is not None:
            env[name] = default
        else:
            missing.append(var)
    return env, missing


def check(p: Plan, timeout: float | None = None) -> tuple[bool, str]:
    """Start the entry just written, as the host will, and ask for its tools; no tool is
    called. (ok, a line for the user). Starting it once here also means the slow first
    start of a fresh install happens now, not inside the host."""
    try:
        import anyio
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError:
        return True, "Check           skipped: the check needs tracetrim[proxy], as the entry does"
    timeout = CHECK_TIMEOUT if timeout is None else timeout
    env, missing = _resolved_env(p)
    params = StdioServerParameters(command=p.entry["command"], args=list(p.entry.get("args") or []),
                                   env={**os.environ, **env})
    started = time.monotonic()

    async def go():
        with anyio.fail_after(timeout):
            async with stdio_client(params, errlog=open(os.devnull, "w")) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return [t.name for t in (await s.list_tools()).tools]
    try:
        tools = anyio.run(go)
    except BaseException as e:         # a timeout, a crash, a missing command: never its message (it may hold a value)
        if isinstance(e, KeyboardInterrupt):
            raise
        why = "it did not answer within %d seconds" % timeout if isinstance(e, TimeoutError) else \
              f"it could not be started or stopped answering ({type(e).__name__})"
        note = f"; not set here, so not passed: {', '.join(map(_safe_key, missing))}" if missing else ""
        return False, f"Check           FAILED: the entry was started as the host will start it, and {why}{note}"
    own = [t for t in tools if t in ("expand", "tracetrim_done")]
    return True, (f"Check           the entry starts and answers in {time.monotonic() - started:.1f} s: "
                  f"{len(tools) - len(own)} tools of your servers, plus {', '.join(own) or 'none of its own'}")


def run(a) -> None:
    try:
        if a.undo:
            _say(undo(a.undo, a.force))
            return
        if not a.host:
            raise SystemExit("tracetrim init: give --host " + "|".join(HOSTS) + " (or --undo BACKUP)")
        host = HOSTS[a.host]
        if host.name == "claude-code" and a.scope == "user" and not a.file:
            _say(claude_code_user(a.workdir))
            return
        file, scope, root = locate(host, a.file, a.scope)
        # Claude Code's own state (~/.claude.json, or under CLAUDE_CONFIG_DIR): never read, never written.
        if ".claude.json" in (file.name.lower(), file.resolve().name.lower()):
            raise SystemExit(f"tracetrim init: {file} is Claude Code's own state file, which init neither reads nor "
                             "writes. For the servers kept there, `tracetrim init --host claude-code --scope user` "
                             "prints the steps with `claude mcp`")
        if not file.exists():
            raise SystemExit(f"tracetrim init: {host.title} file not found: {file}. Give it with --file")
        if file.is_symlink():
            file = file.resolve()
        p = plan(host, file, scope, root, a.workdir, a.name, a.exclude, a.http)
        write = a.write and bool(p.leaving)
        text = render(p, a.write)       # made before anything is written
        if write and p.refuse and not a.force:
            _say(text)
            raise SystemExit("tracetrim init: nothing was written.")
        backup = apply(p, a.force) if write else None
        checked = None
        if backup is not None and p.http is None and not getattr(a, "no_check", False):
            checked = check(p)
        try:
            _say(text + (closing(p, write, backup) if p.leaving else "") + (checked[1] + "\n" if checked and checked[1] else ""))
        except Exception:
            if backup is None:
                raise
            raise SystemExit(f"tracetrim init: the files were written, but the summary could not be shown. "
                             f"Backup {backup}; undo with: tracetrim init --undo {backup}") from None
        if checked and not checked[0]:
            raise SystemExit(f"tracetrim init: the host's file is written, but the entry did not start here. "
                             f"Start the host and see whether it connects; if it does not, undo with: "
                             f"tracetrim init --undo {backup}")
    except SystemExit:
        raise
    except Exception as e:                # never a traceback or a message that could carry a value of the file
        raise SystemExit(f"tracetrim init: unexpected error ({type(e).__name__}); nothing more is shown because "
                         "the files involved hold keys") from None
