"""Run 3 random articles end-to-end through reel-af in parallel.

Fires async executions via the AgentField workflow. Polls each until done.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import aiohttp

CONTROL_PLANE = "http://localhost:8080"
TIMEOUT_S = 1200

ARTICLES = [
    ("space",      "https://www.newscientist.com/article/2527597-mercury-may-have-gained-all-of-its-unexpected-water-in-a-single-day/"),
    ("ai_hardware", "https://epoch.ai/data-insights/ai-chip-component-cost-shares"),
    ("psychology", "https://www.newscientist.com/article/2527614-political-anger-affects-the-body-differently-to-other-forms-of-anger/"),
]


async def _kick_off(session, genre, url):
    out_dir = f"output/random3/{genre}"
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
        return genre, url, exec_id


async def _poll(session, genre, exec_id):
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
            print(f"[{genre}] [{elapsed:4d}s] {status}")
            last_status = status
        if status in ("succeeded", "failed"):
            return {"genre": genre, "exec_id": exec_id, "data": data, "wall_s": elapsed}
        if elapsed > TIMEOUT_S:
            return {"genre": genre, "exec_id": exec_id, "data": data, "wall_s": elapsed, "timed_out": True}
        await asyncio.sleep(15)


async def _run_one(session, genre, url):
    genre, url, exec_id = await _kick_off(session, genre, url)
    if not exec_id:
        return {"genre": genre, "error": "no exec_id"}
    print(f"[{genre}] ✓ kicked off exec_id={exec_id}")
    result = await _poll(session, genre, exec_id)
    result["url"] = url
    return result


def _summary(results):
    print()
    print("=" * 80)
    print(" BATCH SUMMARY")
    print("=" * 80)
    for r in results:
        genre = r["genre"]
        url = r.get("url", "?")
        data = r.get("data", {})
        status = data.get("status", "?")
        print()
        print(f"── [{genre}] ──────────────────────────────────")
        print(f"  url    : {url}")
        print(f"  wall   : {r.get('wall_s', '?')}s")
        print(f"  status : {status}")
        if status == "succeeded":
            res = data.get("result", {})
            print(f"  script  : {res.get('script', '')[:600]}")
            print(f"  dir={res.get('direction')} arch={res.get('chosen_arch')} score={res.get('self_score')}")
            print(f"  voice   : {res.get('voice_id')} ({res.get('voice_tone')})")
            print(f"  dur     : {res.get('duration_s', 0):.1f}s")
            print(f"  captions: {res.get('captions')}")
            print(f"  motifs  : {res.get('motifs')}")
            print(f"  output  : {res.get('video_path')}")
            print(f"  timings : {res.get('timings_s')}")
        elif status == "failed":
            print(f"  err     : {data}")


async def main():
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as session:
        results = await asyncio.gather(*(_run_one(session, g, u) for g, u in ARTICLES))
    _summary(results)


if __name__ == "__main__":
    asyncio.run(main())
