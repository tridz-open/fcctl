"""Extract the Pilot admin dashboard's API contract from its public JS bundle.

usage: python -I extract_pilot_contract.py ASSETS_DIR > pilot_dashboard_contract.json
       (ASSETS_DIR holds index-*.js and every lazy chunk; fetch them with --fetch BASE_URL OUT_DIR)
Output: sorted list of {method, path, body_keys, query_keys, fn}. Paths use {} for each variable segment.
"""
import json
import pathlib
import re
import sys
import urllib.request

def fetch(base, out):
    out = pathlib.Path(out); out.mkdir(parents=True, exist_ok=True)
    html = urllib.request.urlopen(base.rstrip("/") + "/login").read().decode()
    todo = set(re.findall(r"assets/(index-[\w-]+\.js)", html)); seen = set()
    while todo:
        n = todo.pop(); seen.add(n)
        s = urllib.request.urlopen(f"{base.rstrip('/')}/assets/{n}").read().decode()
        (out / n).write_text(s)
        todo |= {m for m in re.findall(r'(?:assets/|"\./)([\w.-]+\.js)', s)} - seen

def top_keys(s):
    """Top-level keys of the object literal starting at s[0]=='{'."""
    depth, tok, q = 0, "", None
    for ch in s:
        if q:
            q = None if ch == q else q; continue
        if ch in "`\"'": q = ch
        if ch in "{[(":
            depth += 1
            if depth == 1: continue
        elif ch in "}])":
            depth -= 1
            if depth == 0: break
        if depth == 1: tok += ch
    keys = set()
    for part in tok.split(","):
        part = part.strip()
        m = re.match(r'"?([A-Za-z_][\w-]*)"?\s*:', part) or re.match(r"\.\.\.\w+\?\{(\w+)", part)
        if m: keys.add(m.group(1))
    return sorted(keys)

def opt_keys(opts, label):
    m = re.search(label + r":\s*(\{)?", opts)
    if not m: return None
    return top_keys(opts[m.start(1):]) if m.group(1) else ["<dynamic>"]

def main(d):
    d = pathlib.Path(d); out = {}
    for f in sorted(d.glob("*.js")):
        s = f.read_text()
        recv = set(re.findall(r"\bi as (\w+)\b[^;]*?from\"\./client-", s))   # the ky client export
        recv |= set(re.findall(r"(\w+)=\w+\.create\(\{prefix:\w+", s))      # defined in this chunk
        recv |= {"X"} if f.name.startswith("index-") else set()
        imp = re.search(r"import\{([^}]*)\}from\"\./client-", s)
        names = dict((a.split(" as ") + [a])[:2] for a in imp.group(1).split(",")) if imp else {}
        urlb = ({names["n"]} if "n" in names else set()) | ({"Vt"} if f.name.startswith("index-") else set())
        if not recv: continue
        alt = "|".join(map(re.escape, recv))
        for m in re.finditer(rf"\b(?:{alt})\.(get|post|put|patch|delete)\(`([^`]*)`", s):
            rest, opts = s[m.end():], ""
            if rest.startswith(","):
                k = 1
                if rest[k] == "{":
                    depth = 0
                    for j, ch in enumerate(rest[k:]):
                        depth += ch == "{"; depth -= ch == "}"
                        if depth == 0: opts = rest[k:k + j + 1]; break
            ctx = s[max(0, m.start() - 200):m.start()]
            fn = (re.findall(r"(\w+):(?:async)?\s*(?:\([^()]*\)|\w+)=>", ctx) or [""])[-1]
            add(out, m.group(1).upper(), m.group(2), opt_keys(opts, "json"), opt_keys(opts, "searchParams"), fn)
        ualt = "|".join(map(re.escape, urlb))
        for m in re.finditer(rf"\b(?:{ualt})\(`([a-z][^`]*)`", s):   # URL builder: EventSource / download / XHR
            add(out, "GET*", m.group(1), None, None, "url")
    # URL-builder hits are noisy (single-letter aliases): keep only API roots seen on real client calls, plus health
    roots = {e["path"].split("/")[1] for e in out.values() if e["method"] != "GET*"} | {"health"}
    out = {k: e for k, e in out.items() if e["method"] != "GET*" or
           (e["path"].split("/")[1] in roots and re.fullmatch(r"/[a-z][a-z0-9/{}_-]*", e["path"]))}
    seen = {e["path"] for e in out.values() if e["method"] != "GET*"}
    out = {k: e for k, e in out.items() if e["method"] != "GET*" or e["path"] not in seen or e["path"].endswith("/output/content")}
    res = sorted(out.values(), key=lambda e: (e["path"], e["method"]))
    for e in res: e["fn"] = sorted(e["fn"])
    json.dump(res, sys.stdout, indent=1); print()

def add(out, method, raw, body, query, fn):
    raw = re.sub(r"\$\{[^}]*$", "", raw)  # nested template cut at the inner backtick (e.g. tasks/{}/debug)
    path = "/" + re.sub(r"\?.*$", "", re.sub(r"\$\{(?:[^{}]|\{[^}]*\})*\}", "{}", raw))
    e = out.setdefault((method, path), {"method": method, "path": path, "body_keys": None, "query_keys": None, "fn": set()})
    if body: e["body_keys"] = sorted(set(e["body_keys"] or []) | set(body))
    if query: e["query_keys"] = sorted(set(e["query_keys"] or []) | set(query))
    if fn: e["fn"].add(fn)

if __name__ == "__main__":
    if sys.argv[1] == "--fetch": fetch(sys.argv[2], sys.argv[3])
    else: main(sys.argv[1])
