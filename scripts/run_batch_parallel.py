"""Run N articles through reel-af in PARALLEL via the AgentField workflow.

Fires async executions concurrently and polls each until terminal. Shows
per-article timing + script + output path side-by-side.

Pre-req:
  - `af server` running on :8080
  - `python -m reel_af.app` running on :8002
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import aiohttp

from resolve_output_dir import resolve_output_dir

CONTROL_PLANE = "http://localhost:8080"
TIMEOUT_S = 900

ARTICLES: list[tuple[str, str]] = [
    ("science",  "https://www.quantamagazine.org/quantum-jamming-explores-the-truly-fundamental-principles-of-nature-20260417/"),
    ("business", "https://stratechery.com/2026/the-data-center-veto/"),
    ("culture",  "https://abyss.fish/your_dotfiles_are_not_a_distro"),
]


async def _kick_off(session: aiohttp.ClientSession, genre: str, url: str) -> tuple[str, str, str]:
    out_dir = resolve_output_dir("batch", genre)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    payload = {"input": {"url": url, "out_dir": out_dir}}
    async with session.post(
        f"{CONTROL_PLANE}/api/v1/execute/async/reel-af.reel_article_to_reel",
        json=payload,
    ) as resp:
        body = await resp.text()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {}
        exec_id = data.get("execution_id", "")
        if not exec_id:
            print(f"[{genre}] ⚠ no exec_id  body={body[:200]}")
        return genre, url, exec_id


async def _poll(session: aiohttp.ClientSession, genre: str, exec_id: str) -> dict[str, Any]:
    start = time.time()
    last_status = ""
    while True:
        try:
            async with session.get(f"{CONTROL_PLANE}/api/v1/executions/{exec_id}") as resp:
                data = await resp.json()
        except Exception as e:
            print(f"[{genre}] poll error: {e}")
            await asyncio.sleep(15)
            continue
        status = data.get("status", "?")
        elapsed = int(time.time() - start)
        if status != last_status or elapsed % 60 == 0:
            print(f"[{genre}] [{elapsed:4d}s] status={status}")
            last_status = status
        if status in ("succeeded", "failed"):
            return {"genre": genre, "exec_id": exec_id, "data": data, "wall_s": elapsed}
        if elapsed > TIMEOUT_S:
            return {"genre": genre, "exec_id": exec_id, "data": data, "wall_s": elapsed,
                    "timed_out": True}
        await asyncio.sleep(15)


async def _run_one(session: aiohttp.ClientSession, genre: str, url: str) -> dict[str, Any]:
    genre, url, exec_id = await _kick_off(session, genre, url)
    if not exec_id:
        return {"genre": genre, "error": "no exec_id"}
    print(f"[{genre}] ✓ kicked off exec_id={exec_id}")
    result = await _poll(session, genre, exec_id)
    result["url"] = url
    return result


def _print_summary(results: list[dict[str, Any]]) -> None:
    print()
    print("=" * 72)
    print(" BATCH SUMMARY")
    print("=" * 72)
    for r in results:
        genre = r["genre"]
        url = r.get("url", "?")
        data = r.get("data", {})
        status = data.get("status", "?")
        print()
        print(f"── [{genre}] ────────────────────────────────────────")
        print(f"  url    : {url}")
        print(f"  wall   : {r.get('wall_s', '?')}s")
        print(f"  status : {status}")
        if status == "succeeded":
            res = data.get("result", {})
            print(f"  script : {res.get('script', '')[:500]}")
            print(f"  direction={res.get('direction')}  arch={res.get('chosen_arch')}  score={res.get('self_score')}")
            print(f"  voice  : {res.get('voice_id')} ({res.get('voice_tone')})")
            print(f"  dur    : {res.get('duration_s', 0):.1f}s")
            print(f"  caps   : {res.get('captions')}")
            print(f"  motifs : {res.get('motifs')}")
            print(f"  output : {res.get('video_path')}")
            print(f"  timings: {res.get('timings_s')}")
        elif status == "failed":
            print(f"  error  : {data.get('error', data)[:400] if isinstance(data.get('error'), str) else data}")


async def main() -> None:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as session:
        # Fire all 3 in parallel via asyncio.gather.
        results = await asyncio.gather(*(_run_one(session, g, u) for g, u in ARTICLES))
    _print_summary(results)


if __name__ == "__main__":
    asyncio.run(main())
