"""Output renderers: table/json/yaml/ndjson."""
from __future__ import annotations

import dataclasses
import json
from typing import Any, Iterable, TextIO


def default_format(stream: TextIO) -> str:
    try:
        return "table" if stream.isatty() else "json"
    except Exception:
        return "json"


def _plain(o: Any) -> Any:
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {k: _plain(v) for k, v in dataclasses.asdict(o).items()}
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_plain(v) for v in o]
    return o


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str, separators=(",", ":"))
    return str(v).replace("\n", " ")


def _table(data: Any, columns: Iterable[str] | None) -> str:
    if isinstance(data, dict):
        rows = [{"key": k, "value": v} for k, v in data.items()]
        cols = ["key", "value"]
    else:
        rows = [r if isinstance(r, dict) else {"value": r} for r in (data if isinstance(data, list) else [data])]
        cols = list(columns) if columns else list(dict.fromkeys(k for r in rows for k in r))
    if not rows:
        return ""
    grid = [[_cell(r.get(c)) for c in cols] for r in rows]
    w = [max(len(c), *(len(g[i]) for g in grid)) for i, c in enumerate(cols)]
    line = lambda cells: "  ".join(c.ljust(w[i]) for i, c in enumerate(cells)).rstrip()
    return "\n".join([line([c.upper() for c in cols])] + [line(g) for g in grid])


def _scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(str(v), ensure_ascii=False)


def _yaml(o: Any, ind: int = 0) -> list[str]:
    pad = "  " * ind
    if isinstance(o, dict) and o:
        out = []
        for k, v in o.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:")
                out += _yaml(v, ind + 1)
            else:
                out.append(f"{pad}{k}: {_yaml_empty(v)}")
        return out
    if isinstance(o, list) and o:
        out = []
        for v in o:
            if isinstance(v, (dict, list)) and v:
                sub = _yaml(v, ind + 1)
                out.append(f"{pad}- {sub[0].lstrip()}")
                out += sub[1:]
            else:
                out.append(f"{pad}- {_yaml_empty(v)}")
        return out
    return [f"{pad}{_yaml_empty(o)}"]


def _yaml_empty(v: Any) -> str:
    if isinstance(v, dict):
        return "{}"
    if isinstance(v, list):
        return "[]"
    return _scalar(v)


def to_yaml(data: Any) -> str:
    try:
        import yaml  # type: ignore
        return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True).rstrip("\n")
    except ImportError:
        return "\n".join(_yaml(data))


def render(data: Any, fmt: str, stream: TextIO, columns: Iterable[str] | None = None) -> None:
    data = _plain(data)
    if fmt == "auto":
        fmt = default_format(stream)
    if fmt == "json":
        text = json.dumps(data, indent=2, default=str)
    elif fmt == "ndjson":
        items = data if isinstance(data, list) else [data]
        text = "\n".join(json.dumps(i, default=str) for i in items)
    elif fmt == "yaml":
        text = to_yaml(data)
    elif fmt == "table":
        text = _table(data, columns)
    else:
        raise ValueError(f"unknown format {fmt!r}")
    if text:
        stream.write(text + "\n")
