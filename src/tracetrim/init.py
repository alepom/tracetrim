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
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

MASK = "••••"
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
_EXPANDS = re.compile(r"\$[A-Za-z0-9_{]" + (r"|%[^%]+%" if os.name == "nt" else ""))


class Refuse(Exception):
    """This server stays in the host. The message is shown: it never holds a value."""


def _check_shape(spec) -> None:
    if not isinstance(spec, dict):
        raise Refuse("not an object")
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
        if _EXPANDS.search(rest):
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
    tmp = path.with_name(f".{path.name}.tracetrim-tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)                     # the mode asked for, whatever the umask
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


def _ours(spec) -> dict | None:
    """If this entry is one `init` writes: where its servers file and work directory are."""
    if not isinstance(spec, dict) or not isinstance(spec.get("command"), str) or not isinstance(spec.get("args"), list):
        return None
    args = [a for a in spec["args"] if isinstance(a, str)]
    stem = re.split(r"[\\/]", spec["command"])[-1].lower()
    stem = stem[:-4] if stem.endswith(".exe") else stem
    if stem != "tracetrim":
        if args[:2] != ["-m", "tracetrim.cli"]:
            return None
        args = args[2:]
    if args[:1] != ["proxy"] or "--servers" not in args[:-1]:
        return None
    servers = args[args.index("--servers") + 1]
    work = args[args.index("--auto") + 1] if "--auto" in args[:-1] else None
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


def git_problems(workdir: Path) -> tuple[str | None, list[str]]:
    """(why servers.json may not be written there, warnings)."""
    existing = next(p for p in [workdir, *workdir.parents] if p.is_dir())
    warnings = []
    for d in [existing, *existing.parents]:
        if (d / ".mcp.json").is_file() and _git(["ls-files", "--error-unmatch", ".mcp.json"], d) == 0:
            warnings.append(f"{workdir} is inside {d}, a project whose .mcp.json is under version control: "
                            "the keys in servers.json sit next to files that are shared")
            break
    repo = next((d for d in [existing, *existing.parents] if (d / ".git").exists()), None)
    if repo is None:
        return None, warnings
    if _git(["check-ignore", "-q", str(workdir / "servers.json")], existing) == 0:
        return None, warnings
    return (f"{workdir} is inside the git repository {repo} and servers.json would not be ignored there: "
            "it holds keys. Choose another --workdir, add it to .gitignore, or pass --force"), warnings


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
    if len(found) > 1:
        raise SystemExit("tracetrim init: the file has more than one tracetrim entry (" +
                         ", ".join(map(_safe_key, found)) + "): keep one")
    wd, why = default_workdir(host, scope, root)
    if workdir:
        wd, why = Path(workdir).expanduser().absolute(), "given with --workdir"
    p = Plan(host, file, scope, wd, why, name, http, raw, config)
    if found and http:
        raise SystemExit("tracetrim init: this host already goes through tracetrim over stdio (entry " +
                         ", ".join(map(_safe_key, found)) + "): undo first to move to --http")
    if found:
        (p.name, p.existing), = found.items()
        if workdir and wd != p.existing["workdir"].absolute():
            raise SystemExit(f"tracetrim init: this host already goes through tracetrim with the work directory "
                             f"{p.existing['workdir']}: leave --workdir out, or undo first")
        p.workdir, p.why_workdir = p.existing["workdir"], "the one the host's tracetrim entry already uses"
    elif name in block and not http:      # over HTTP nothing is added to the file
        raise SystemExit(f"tracetrim init: the host already has a server called {_safe_key(name)} that is not "
                         "the entry of `tracetrim init`: choose another name with --name")

    on_disk = {}
    if p.servers_path.exists():
        _, data = _read_json(p.servers_path, "the servers file")
        on_disk = data.get("mcpServers", data) if isinstance(data, dict) else None
        if not isinstance(on_disk, dict):
            raise SystemExit(f"tracetrim init: {p.servers_path} is not a servers file")

    env = block[p.name].get("env") if p.existing else None
    p.refs = dict(env) if isinstance(env, dict) else {}
    kept_refs = dict(p.refs)
    for n, spec in block.items():
        if n in found:
            continue
        if n in exclude:
            p.left[n] = "excluded with --exclude"
            continue
        try:
            _check_shape(spec)
            out, refs = _translate(host, spec, bool(http))
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
    if p.existing:
        p.entry = dict(block[p.name])
        if p.refs != kept_refs:
            p.entry["env"] = dict(p.refs)
    else:
        p.entry = {**({"type": "stdio"} if host.typed else {}), "command": (exe := executable())[0],
                   "args": [*exe[1:], "proxy", "--servers", str(p.servers_path), "--auto", str(p.workdir)],
                   **({"env": dict(p.refs)} if p.refs else {})}
    if p.leaving or http:
        p.refuse, p.warnings = git_problems(p.workdir)
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
    shown = dict(p.entry)
    if isinstance(shown.get("env"), dict):    # references are shown, a default or anything else is not
        shown["env"] = {k: v if isinstance(v, str) and _ENV.fullmatch(v) else
                        _show_ref(v) if isinstance(v, str) and _CC.fullmatch(v) else MASK
                        for k, v in shown["env"].items()}
    if p.existing:            # an entry the user may have edited
        shown["args"] = [a if a in ("proxy", "--servers", "--auto", str(p.servers_path), str(p.workdir)) or
                         re.fullmatch(r"--[a-z-]+|-m|tracetrim\.cli", a) else MASK for a in shown.get("args", [])]
    return json.dumps({p.name: shown}, indent=2, ensure_ascii=False)


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
        L += [f"  {_safe_key(n):<{width}}" + ("removed from the host" if not p.http else "") for n in p.already]
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
    if p.http:
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
    if not p.http:
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


def apply(p: Plan, force: bool) -> Path | None:
    """Write the plan. Returns the backup of the host's file, if the host was rewritten."""
    if p.refuse and not force:
        raise SystemExit("tracetrim init: " + p.refuse)
    p.workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    p.servers_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write(p.servers_path, _dump({"mcpServers": p.servers_file}), 0o600)
    if p.http:
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
        _, note = _read_json(side, "the note of the backup")
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


def run(a) -> None:
    try:
        if a.undo:
            sys.stdout.write(undo(a.undo, a.force))
            return
        if not a.host:
            raise SystemExit("tracetrim init: give --host " + "|".join(HOSTS) + " (or --undo BACKUP)")
        host = HOSTS[a.host]
        if host.name == "claude-code" and a.scope == "user" and not a.file:
            sys.stdout.write(claude_code_user(a.workdir))
            return
        file, scope, root = locate(host, a.file, a.scope)
        if not file.exists():
            raise SystemExit(f"tracetrim init: {host.title} file not found: {file}. Give it with --file")
        if file.is_symlink():
            file = file.resolve()
        p = plan(host, file, scope, root, a.workdir, a.name, a.exclude, a.http)
        write = a.write and bool(p.leaving)
        if write and p.refuse and not a.force:
            sys.stdout.write(render(p, True))
            raise SystemExit("tracetrim init: nothing was written.")
        backup = apply(p, a.force) if write else None
        sys.stdout.write(render(p, a.write))
        if p.leaving:
            sys.stdout.write(closing(p, write, backup))
    except SystemExit:
        raise
    except Exception as e:                # never a traceback or a message that could carry a value of the file
        raise SystemExit(f"tracetrim init: unexpected error ({type(e).__name__}); nothing more is shown because "
                         "the files involved hold keys") from None
