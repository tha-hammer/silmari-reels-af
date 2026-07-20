"""Repeatable A1 source ingestion + producer driver (AF-u77).

Why this exists
---------------
In production the reel-af agent's yt-dlp download hits ``YTDLP_PROXY_URL``
(BrightData data-center IP) and YouTube returns ``403 Forbidden`` *before any
planning*, so every URL-mode reel dies at source download. A presigned bucket
``.mp4`` URL is classified as direct-media by
``reel_af.render.hooks._is_direct_media_url()`` and streamed with **no proxy**,
which sidesteps the 403.

Separately, the improved A1 producer (script quality / content-driven length /
script-coherence) lives only on the ``transcript_to_plan -> dsl_hooks_to_reels``
path. The reel-af-ui exposes only composite presets, so the browser cannot reach
the A1 producer at all. This CLI closes both gaps in one repeatable command.

What it does
------------
1. Obtains the source ``.mp4`` locally -- ``yt-dlp`` download of a URL on your
   (residential) IP, or a local file you pass. This is the download prod cannot do.
2. Uploads it to the ``reel-uploads`` bucket and presigns a GET URL (direct-media).
3. Dispatches the deployed A1 producer against that presigned URL, in two stages,
   via the PUBLIC control-plane, so rendering happens on Railway (not locally):
       reel-af.reel_transcript_to_plan -> publishes composite_ref/words_ref/hook_ref
       reel-af.reel_dsl_hooks_to_reels -> renders + returns download_url
4. Prints the final A1 ``download_url``.

Usage
-----
    uv run python scripts/ingest_source.py https://youtu.be/wPcKNuUG3NM
    uv run python scripts/ingest_source.py /path/to/local.mp4 --register educational
    uv run python scripts/ingest_source.py <url> --upload-only        # presign + print URL
    uv run python scripts/ingest_source.py --source-url <presigned> --dispatch-only

Config (auto-pulled from ``railway variables`` when not already in env):
    REEL_BUCKET_NAME / REEL_BUCKET_ENDPOINT / REEL_BUCKET_REGION /
    REEL_BUCKET_ACCESS_KEY_ID / REEL_BUCKET_SECRET_ACCESS_KEY   (service reel-af)
    AGENTFIELD_API_KEY                                          (service control-plane)
Public control-plane URL: --control-plane / AGENTFIELD_PUBLIC_SERVER
    (default https://control-plane-production-fa62.up.railway.app)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import boto3
import httpx

# Railway coordinates for the silmari-deep-research / production deployment.
RAILWAY_PROJECT = "5dcbd074-f4f2-4284-b355-3e332d4538a5"
RAILWAY_ENV = "production"
DEFAULT_CONTROL_PLANE = "https://control-plane-production-fa62.up.railway.app"

# Must match reel_af.render.hooks._DIRECT_MEDIA_EXTENSIONS so the presigned URL is
# classified direct-media (no proxy) by the agent.
DIRECT_MEDIA_EXTENSIONS = (".mp4", ".mkv", ".webm", ".mov", ".m4v")

TRANSCRIPT_TO_PLAN = "reel-af.reel_transcript_to_plan"
DSL_HOOKS_TO_REELS = "reel-af.reel_dsl_hooks_to_reels"

_BUCKET_KEYS = (
    "REEL_BUCKET_NAME",
    "REEL_BUCKET_ENDPOINT",
    "REEL_BUCKET_REGION",
    "REEL_BUCKET_ACCESS_KEY_ID",
    "REEL_BUCKET_SECRET_ACCESS_KEY",
)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fail(msg: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


# ── config resolution ────────────────────────────────────────────────────────
def pull_railway_kv(service: str) -> dict[str, str]:
    """Return REEL_BUCKET-relevant KV from `railway variables` for a service.

    Mirrors the working scratchpad workaround: the bucket creds live only on
    Railway, never in the local .env.
    """
    proc = subprocess.run(
        ["railway", "variables", "-s", service, "-p", RAILWAY_PROJECT,
         "-e", RAILWAY_ENV, "--kv"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        _fail(f"`railway variables -s {service}` failed: {proc.stderr.strip()[:300]}")
    kv: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()
    return kv


def resolve_bucket_cfg() -> dict[str, str]:
    if all(os.getenv(k) for k in _BUCKET_KEYS if k != "REEL_BUCKET_REGION"):
        cfg = {k: os.getenv(k, "") for k in _BUCKET_KEYS}
    else:
        _log("• pulling REEL_BUCKET_* from railway (service reel-af)…")
        kv = pull_railway_kv("reel-af")
        cfg = {k: os.getenv(k) or kv.get(k, "") for k in _BUCKET_KEYS}
    cfg.setdefault("REEL_BUCKET_REGION", "auto")
    if not cfg.get("REEL_BUCKET_REGION"):
        cfg["REEL_BUCKET_REGION"] = "auto"
    missing = [k for k in _BUCKET_KEYS if k != "REEL_BUCKET_REGION" and not cfg.get(k)]
    if missing:
        _fail(f"missing bucket config: {', '.join(missing)}")
    return cfg


def resolve_api_key(cli_value: str | None) -> str:
    key = cli_value or os.getenv("AGENTFIELD_API_KEY")
    if key:
        return key
    _log("• pulling AGENTFIELD_API_KEY from railway (service control-plane)…")
    proc = subprocess.run(
        ["railway", "variables", "-s", "control-plane", "-p", RAILWAY_PROJECT,
         "-e", RAILWAY_ENV, "--kv"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        _fail(f"`railway variables -s control-plane` failed: {proc.stderr.strip()[:300]}")
    for line in proc.stdout.splitlines():
        if line.startswith("AGENTFIELD_API_KEY="):
            return line.split("=", 1)[1].strip()
    _fail("AGENTFIELD_API_KEY not found (pass --api-key or set the env var)")


# ── source acquisition ───────────────────────────────────────────────────────
def download_source(url: str, cookies: str | None) -> Path:
    """yt-dlp the URL into a fresh temp dir, remuxed to mp4; return the file path."""
    dl_dir = Path(tempfile.mkdtemp(prefix="ingest-src-"))
    cmd = [
        "yt-dlp",
        "-f", "bestvideo*+bestaudio/best",
        "--merge-output-format", "mp4",
        "-o", str(dl_dir / "%(id)s.%(ext)s"),
    ]
    if cookies:
        cmd += ["--cookies", cookies]
    cmd.append(url)
    _log(f"• yt-dlp downloading {url} …")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        _fail(f"yt-dlp failed: {proc.stderr.strip()[-500:]}")
    files = sorted(dl_dir.glob("*.mp4")) or sorted(p for p in dl_dir.iterdir() if p.is_file())
    if not files:
        _fail(f"yt-dlp produced no file in {dl_dir}")
    return files[0]


def derive_key(source: str, local_path: Path, override: str | None) -> str:
    if override:
        key = override
    else:
        m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{6,})", source)
        stem = m.group(1) if m else local_path.stem
        stem = re.sub(r"[^A-Za-z0-9_-]", "-", stem) or "source"
        key = f"uploads/manual/{stem}.mp4"
    if not key.lower().endswith(DIRECT_MEDIA_EXTENSIONS):
        _fail(f"object key {key!r} must end in one of {DIRECT_MEDIA_EXTENSIONS} "
              "so the agent classifies it direct-media (no proxy)")
    return key


def upload_and_presign(cfg: dict[str, str], local_path: Path, key: str,
                       ttl: int, force: bool) -> str:
    s3 = boto3.client(
        "s3",
        endpoint_url=cfg["REEL_BUCKET_ENDPOINT"],
        region_name=cfg.get("REEL_BUCKET_REGION", "auto"),
        aws_access_key_id=cfg["REEL_BUCKET_ACCESS_KEY_ID"],
        aws_secret_access_key=cfg["REEL_BUCKET_SECRET_ACCESS_KEY"],
    )
    bucket = cfg["REEL_BUCKET_NAME"]
    exists = False
    if not force:
        try:
            s3.head_object(Bucket=bucket, Key=key)
            exists = True
        except Exception:
            exists = False
    if exists:
        _log(f"• object already present, reusing {bucket}/{key} (use --force to re-upload)")
    else:
        _log(f"• uploading {local_path} -> {bucket}/{key} ({local_path.stat().st_size / 1e6:.1f} MB)…")
        s3.upload_file(str(local_path), bucket, key,
                       ExtraArgs={"ContentType": "video/mp4"})
    url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl)
    return url


# ── control-plane dispatch ───────────────────────────────────────────────────
def _client(cp_url: str, api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=cp_url.rstrip("/"),
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        timeout=httpx.Timeout(120.0, read=120.0),
    )


def dispatch(client: httpx.Client, target: str, cp_input: dict) -> str:
    resp = client.post(f"/api/v1/execute/async/{target}", json={"input": cp_input})
    if resp.status_code >= 400:
        _fail(f"dispatch {target} -> HTTP {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    exec_id = data.get("execution_id", "")
    if not exec_id:
        _fail(f"dispatch {target}: no execution_id in response: {data}")
    return exec_id


def poll(client: httpx.Client, exec_id: str, label: str, timeout_s: int) -> dict:
    start = time.time()
    last = ""
    while True:
        try:
            state = client.get(f"/api/v1/executions/{exec_id}").json()
        except Exception as e:  # transient poll failure
            _log(f"  [{label}] poll error: {e}")
            time.sleep(15)
            continue
        status = state.get("status", "?")
        elapsed = int(time.time() - start)
        if status != last or elapsed % 60 == 0:
            _log(f"  [{label}] [{elapsed:4d}s] {status}")
            last = status
        if status in ("succeeded", "failed"):
            return state
        if elapsed > timeout_s:
            _fail(f"[{label}] TIMEOUT after {elapsed}s (exec_id={exec_id})")
        time.sleep(15)


def run_a1(source_url: str, register: str, clip_idx: int,
           cp_url: str, api_key: str, timeout_s: int) -> str:
    with _client(cp_url, api_key) as client:
        _log(f"• stage 1: {TRANSCRIPT_TO_PLAN}")
        s1_id = dispatch(client, TRANSCRIPT_TO_PLAN,
                         {"source_url": source_url, "register": register})
        s1 = poll(client, s1_id, "plan", timeout_s)
        if s1.get("status") != "succeeded":
            _fail(f"stage 1 failed: {json.dumps(s1)[:600]}")
        r1 = s1.get("result", {})
        if r1.get("error"):
            _fail(f"stage 1 producer error: {r1.get('error')} :: {json.dumps(r1)[:400]}")
        refs = {k: r1.get(k) for k in ("composite_ref", "words_ref", "hook_ref")}
        if not all(refs.values()):
            _fail(f"stage 1 missing artifact refs: {refs}")
        _log(f"  artifacts: {json.dumps(refs)[:300]}")

        _log(f"• stage 2: {DSL_HOOKS_TO_REELS}")
        s2_id = dispatch(client, DSL_HOOKS_TO_REELS,
                         {"source_url": source_url, "clip_idx": clip_idx, **refs})
        s2 = poll(client, s2_id, "render", timeout_s)
        if s2.get("status") != "succeeded":
            _fail(f"stage 2 failed: {json.dumps(s2)[:600]}")
        r2 = s2.get("result", {})
        if r2.get("error") or not r2.get("download_url"):
            _fail(f"stage 2 delivery error: {json.dumps(r2)[:400]}")
        return r2["download_url"]


# ── entrypoint ───────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Ingest a source (URL or local file) and drive the deployed A1 "
                    "producer, bypassing the yt-dlp proxy 403.")
    p.add_argument("source", nargs="?",
                   help="YouTube (or other) URL to download, or a local video file path.")
    p.add_argument("--source-url",
                   help="Skip acquisition/upload; dispatch this already-presigned URL.")
    p.add_argument("--register", default="educational",
                   help="A1 register passed to transcript_to_plan (default: educational).")
    p.add_argument("--clip-idx", type=int, default=1)
    p.add_argument("--ttl", type=int, default=21600,
                   help="Presigned URL TTL in seconds (default 21600 = 6h).")
    p.add_argument("--cookies", help="Cookies file passed to yt-dlp.")
    p.add_argument("--bucket-key", help="Override the destination object key (must end .mp4).")
    p.add_argument("--force", action="store_true", help="Re-upload even if the key exists.")
    p.add_argument("--upload-only", action="store_true",
                   help="Acquire + upload + presign, print the URL, and stop.")
    p.add_argument("--dispatch-only", action="store_true",
                   help="Skip acquisition/upload; requires --source-url.")
    p.add_argument("--control-plane",
                   default=os.getenv("AGENTFIELD_PUBLIC_SERVER", DEFAULT_CONTROL_PLANE),
                   help="Public control-plane base URL.")
    p.add_argument("--api-key", help="AGENTFIELD_API_KEY (else env, else railway).")
    p.add_argument("--timeout", type=int, default=1800,
                   help="Per-stage poll timeout in seconds (default 1800).")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    # Resolve the source URL (either given, or acquired + uploaded + presigned).
    if args.dispatch_only or args.source_url:
        if not args.source_url:
            _fail("--dispatch-only requires --source-url")
        source_url = args.source_url
    else:
        if not args.source:
            _fail("provide a source URL/file, or --source-url with --dispatch-only")
        cfg = resolve_bucket_cfg()
        local = Path(args.source)
        if local.exists() and local.is_file():
            _log(f"• using local file {local}")
        else:
            local = download_source(args.source, args.cookies)
        key = derive_key(args.source, local, args.bucket_key)
        source_url = upload_and_presign(cfg, local, key, args.ttl, args.force)
        _log(f"SOURCE_URL={source_url}")

    if args.upload_only:
        _log("• --upload-only: stopping before dispatch.")
        return

    api_key = resolve_api_key(args.api_key)
    _log(f"• control-plane: {args.control_plane}")
    download_url = run_a1(source_url, args.register, args.clip_idx,
                          args.control_plane, api_key, args.timeout)
    _log("")
    _log("=" * 72)
    _log(f"A1 DOWNLOAD_URL={download_url}")
    _log("=" * 72)


if __name__ == "__main__":
    main()
