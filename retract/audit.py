"""`retract audit`: flag likely memory writes that bypass the Retract SDK.

Best effort only (L3 is not established): dynamic dispatch, clients passed in from other
modules, and external services are not seen. A flagged call is a likely bypass, not a proof.
"""

from __future__ import annotations

import ast
import fnmatch
import os
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

WRITES = frozenset({"add", "upsert", "put", "add_texts", "add_documents"})
ADAPTER_WRITES = WRITES | {"write"}  # Retract adapters: write() skips lineage and the guard

# Fully qualified names whose instances, or return values, are memory clients.
CLIENTS = frozenset(
    {
        "mem0.Memory",
        "mem0.AsyncMemory",
        "mem0.MemoryClient",
        "mem0.AsyncMemoryClient",
        "mem0.memory.main.Memory",
        "mem0.memory.main.AsyncMemory",
        "langgraph.store.base.BaseStore",
        "langgraph.store.memory.InMemoryStore",
        "langgraph.store.postgres.PostgresStore",
        "langgraph.store.postgres.AsyncPostgresStore",
        "langgraph.store.sqlite.SqliteStore",
        "langgraph.store.sqlite.AsyncSqliteStore",
        "langgraph.config.get_store",
        "langchain_core.vectorstores.VectorStore",
        "langchain_core.vectorstores.InMemoryVectorStore",
        "qdrant_client.QdrantClient",
        "qdrant_client.AsyncQdrantClient",
        "chromadb.Client",
        "chromadb.PersistentClient",
        "chromadb.HttpClient",
        "chromadb.EphemeralClient",
        "pinecone.Pinecone",
        "retract.adapters.SimpleVectorStore",
        "retract.adapters.Mem0Adapter",
        "retract.adapters.simple.SimpleVectorStore",
        "retract.adapters.mem0.Mem0Adapter",
        "retract.core.interfaces.MemoryAdapter",
    }
)
CLIENT_PREFIXES = (
    "langchain_community.vectorstores.",
    "langchain_chroma.",
    "langchain_qdrant.",
    "langchain_pinecone.",
    "langchain_postgres.",
)
# Methods on a client that return another writable client (a collection or index).
FACTORIES = frozenset({"get_or_create_collection", "create_collection", "get_collection", "Index"})
SKIP_DIRS = frozenset({"__pycache__", "node_modules"})


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    col: int
    call: str
    client: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.col}: likely bypass: {self.call}() on {self.client}"


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _client_name(qualname: str | None) -> str | None:
    if qualname is None:
        return None
    if qualname in CLIENTS or qualname.startswith(CLIENT_PREFIXES):
        return qualname
    owner, _, attr = qualname.rpartition(".")
    if attr.startswith("from_") and _client_name(owner):  # Memory.from_config, Chroma.from_texts
        return owner
    return None


class _Module:
    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.aliases: dict[str, str] = {}
        for node in ast.walk(tree):  # imports anywhere, including inside functions
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        self.aliases[a.asname] = a.name
                    else:
                        head = a.name.split(".")[0]
                        self.aliases[head] = head
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for a in node.names:
                    self.aliases[a.asname or a.name] = f"{node.module}.{a.name}"
        self.tracked: dict[str, str] = {}  # bound name or self.attr -> client qualname
        while True:  # fixpoint, so aliases like `m2 = m1` resolve in any order
            new = {n: c for n, c in self._bindings() if n not in self.tracked}
            if not new:
                break
            self.tracked.update(new)

    def _resolve(self, node: ast.AST) -> str | None:
        dotted = _dotted(node)
        if dotted is None:
            return None
        head, _, rest = dotted.partition(".")
        if head not in self.aliases:
            return None
        return self.aliases[head] + (f".{rest}" if rest else "")

    def client_of(self, expr: ast.AST | None) -> str | None:
        if expr is None:
            return None
        dotted = _dotted(expr)
        if dotted in self.tracked:
            return self.tracked[dotted]
        if isinstance(expr, ast.Call):
            client = _client_name(self._resolve(expr.func))
            if client:
                return client
            if isinstance(expr.func, ast.Attribute) and expr.func.attr in FACTORIES:
                return self.client_of(expr.func.value)
        if isinstance(expr, ast.Await):
            return self.client_of(expr.value)
        return None

    def _annotation_client(self, ann: ast.AST | None) -> str | None:
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            try:
                ann = ast.parse(ann.value, mode="eval")
            except SyntaxError:
                return None
        for node in ast.walk(ann) if ann is not None else ():
            if isinstance(node, ast.Name | ast.Attribute):
                client = _client_name(self._resolve(node))
                if client:
                    return client
        return None

    def _bindings(self) -> Iterator[tuple[str, str]]:
        for node in ast.walk(self.tree):
            pairs: list[tuple[ast.AST, str | None]] = []
            if isinstance(node, ast.Assign):
                client = self.client_of(node.value)
                pairs = [(t, client) for t in node.targets]
            elif isinstance(node, ast.AnnAssign):
                client = self._annotation_client(node.annotation) or self.client_of(node.value)
                pairs = [(node.target, client)]
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                pairs = [(node.optional_vars, self.client_of(node.context_expr))]
            elif isinstance(node, ast.arg):
                client = self._annotation_client(node.annotation)
                if client:
                    yield node.arg, client
            for target, client in pairs:
                name = _dotted(target)
                if name and client:
                    yield name, client

    def findings(self, path: Path) -> Iterator[Finding]:
        for node in ast.walk(self.tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            method = node.func.attr
            if method not in ADAPTER_WRITES:
                continue
            client = self.client_of(node.func.value)
            if client is None:
                continue
            writes = ADAPTER_WRITES if client.startswith("retract.") else WRITES
            if method in writes:
                call = f"{ast.unparse(node.func.value)}.{method}"
                yield Finding(path, node.lineno, node.col_offset + 1, call, client)


def audit_source(source: bytes, path: Path) -> list[Finding]:
    """Findings for one module; raises SyntaxError if it does not parse."""
    # shortcut: intra-module dataflow only, upgrade if real code passes clients across modules.
    # Bindings are also scope-insensitive: a name bound to a client anywhere counts everywhere.
    found = _Module(ast.parse(source, filename=str(path))).findings(path)
    return sorted(found, key=lambda f: (f.line, f.col))


def _config_root_and_allow(start: Path) -> tuple[Path | None, list[str]]:
    start = start.resolve()
    for d in [start, *start.parents] if start.is_dir() else start.parents:
        pyproject = d / "pyproject.toml"
        if pyproject.is_file():
            with pyproject.open("rb") as f:
                conf = tomllib.load(f).get("tool", {}).get("retract", {}).get("audit", {})
            allow = conf.get("allow", [])
            if not (isinstance(allow, list) and all(isinstance(p, str) for p in allow)):
                raise ValueError(f"{pyproject}: [tool.retract.audit] allow must be a list of str")
            return d, allow
    return None, []


def _python_files(path: Path) -> Iterator[Path]:
    if path.is_file():
        yield path
        return
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield Path(dirpath, name)


def audit(path: Path) -> tuple[list[Finding], list[str]]:
    """(findings, errors) for a file or directory tree, minus `[tool.retract.audit] allow`.

    `allow` holds fnmatch patterns on paths relative to the nearest pyproject.toml.
    Files that do not parse are errors, so a gate never passes them silently.
    """
    if not path.exists():
        raise FileNotFoundError(f"no such file or directory: {path}")
    root, allow = _config_root_and_allow(path)
    findings: list[Finding] = []
    errors: list[str] = []
    for file in _python_files(path):
        if root is not None and allow:
            try:
                rel = file.resolve().relative_to(root).as_posix()
            except ValueError:
                rel = file.as_posix()
            if any(fnmatch.fnmatchcase(rel, pat) for pat in allow):
                continue
        try:
            findings += audit_source(file.read_bytes(), file)
        except (SyntaxError, ValueError) as e:  # ValueError: null bytes in source
            errors.append(f"{file}: cannot parse: {e}")
    return findings, errors
