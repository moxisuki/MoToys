"""Kimi 会话索引：直接读 <sessions>/<workspace>/<session>/state.json，
用 sessions_cache.json 按 mtime+size 增量解析；正文只做有上限的浅层深搜。"""

import json
import os
import time

from .base import CACHE_PATH, log


def _sessions_root(cfg):
    km = cfg.get("kimi") or {}
    root = str(km.get("sessions_dir") or "").strip()
    if not root:
        root = os.path.join(os.path.expanduser("~"), ".kimi-code", "sessions")
    return os.path.expandvars(os.path.expanduser(root))


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return None


def _load_cache():
    cache = _read_json(CACHE_PATH)
    return cache if isinstance(cache, dict) else {}


def _save_cache(cache):
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
    except Exception as exc:
        log("写 sessions_cache.json 失败：%r" % (exc,))


def load_sessions(cfg):
    """列出会话（按 updatedAt 倒序）。

    稳态只做「scandir + stat + 读缓存」，不重新解析没变过的 state.json
    （本机全量解析 63 个要 341 ms，缓存后 ≈ 10 ms）。
    """
    root = _sessions_root(cfg)
    km = cfg.get("kimi") or {}
    include_archived = bool(km.get("include_archived", False))

    entries = []
    try:
        workspaces = list(os.scandir(root))
    except OSError as exc:
        log("会话目录不可读：%r（%r）" % (root, exc))
        return []

    for ws in workspaces:
        if not ws.is_dir():
            continue
        try:
            session_dirs = list(os.scandir(ws.path))
        except OSError:
            continue
        for sd in session_dirs:
            if not sd.is_dir():
                continue
            state_path = os.path.join(sd.path, "state.json")
            try:
                stat = os.stat(state_path)
            except OSError:
                continue
            entries.append((state_path, sd.path, stat.st_mtime, stat.st_size))

    cache = _load_cache()
    files = cache.get("files") if isinstance(cache.get("files"), dict) else {}
    changed = False
    out = []

    for state_path, session_dir, mtime, size in entries:
        record = files.get(state_path)
        if (isinstance(record, dict) and record.get("size") == size
                and abs(float(record.get("mtime") or 0) - mtime) < 1e-6):
            data = record.get("data")
        else:
            data = _read_json(state_path)
            files[state_path] = {"mtime": mtime, "size": size, "data": data}
            changed = True
        if not isinstance(data, dict):
            continue
        if data.get("archived") and not include_archived:
            continue
        if not (data.get("title") or data.get("lastPrompt") or data.get("cwd")):
            continue
        item = dict(data)
        item["_path"] = session_dir
        out.append(item)

    live = {e[0] for e in entries}
    for gone in [key for key in files if key not in live]:
        files.pop(gone, None)
        changed = True

    if changed:
        _save_cache({"version": 1, "files": files})

    out.sort(key=lambda r: r.get("updatedAt") or 0, reverse=True)
    return out


def _one_line(text, limit=90):
    text = " ".join(str(text or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _relative_time(ms):
    try:
        stamp = float(ms) / 1000.0
    except (TypeError, ValueError):
        return "时间未知"
    if stamp <= 0:
        return "时间未知"
    delta = time.time() - stamp
    if delta < 0:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return "%d 分钟前" % (delta // 60)
    if delta < 86400:
        return "%d 小时前" % (delta // 3600)
    if delta < 86400 * 7:
        return "%d 天前" % (delta // 86400)
    return time.strftime("%Y-%m-%d", time.localtime(stamp))


def _session_blob(session):
    return " ".join(str(session.get(key) or "") for key in
                    ("title", "lastPrompt", "cwd", "id", "titleKind")).lower()


def _search_sessions_meta(sessions, text, limit):
    needle = text.lower()
    return [s for s in sessions if needle in _session_blob(s)]


def _search_sessions_deep(sessions, text, limit, km, already):
    """有上限的正文深搜：只看最近 N 个会话的 wire.jsonl 末尾若干字节。"""
    needle = text.lower()
    if len(needle) < int(km.get("deep_min_query") or 2):
        return []
    max_files = int(km.get("deep_files") or 10)
    max_bytes = int(km.get("deep_bytes") or 262144)
    seen = {s.get("_path") for s in already}
    hits = []
    for session in sessions:
        if len(hits) + len(already) >= limit:
            break
        if session.get("_path") in seen:
            continue
        wire = os.path.join(str(session.get("_path") or ""), "agents", "main", "wire.jsonl")
        try:
            size = os.path.getsize(wire)
            with open(wire, "rb") as fh:
                if size > max_bytes:
                    fh.seek(size - max_bytes)
                blob = fh.read(max_bytes)
        except OSError:
            continue
        if needle in blob.decode("utf-8", "replace").lower():
            hits.append(session)
        max_files -= 1
        if max_files <= 0:
            break
    return hits
