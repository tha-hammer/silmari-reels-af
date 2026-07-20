"""Run N arxiv papers through reel-af in parallel.

Fires async, polls each, prints a single summary at the end. Useful for
sanity-checking scientific mode across domains.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import aiohttp

from resolve_output_dir import resolve_output_dir

CONTROL_PLANE = "http://localhost:8080"
TIMEOUT_S = 1800

PAPERS = [
    ("meta_llama2",          "https://arxiv.org/abs/2307.09288"),  # Meta Llama 2
    ("meta_code_llama",      "https://arxiv.org/abs/2308.12950"),  # Meta Code Llama
    ("apple_intelligence_v2","https://arxiv.org/abs/2407.21075"),  # Apple Intelligence (retry)
]


async def _kick(session, label, url):
    out = resolve_output_dir("scientific", label)
    Path(out).mkdir(parents=True, exist_ok=True)
    async with session.post(
        f"{CONTROL_PLANE}/api/v1/execute/async/reel-af.reel_article_to_reel",
        json={"input": {"url": url, "out_dir": out}},
    ) as resp:
        data = json.loads(await resp.text() or "{}")
        return label, url, data.get("execution_id", ""), out


async def _poll(session, label, exec_id):
    start = time.time()
    last = ""
    while True:
        try:
            async with session.get(
                f"{CONTROL_PLANE}/api/v1/executions/{exec_id}"
            ) as resp:
                data = await resp.json()
        except Exception as e:
            print(f"[{label}] poll err: {e}")
            await asyncio.sleep(15)
            continue
        status = data.get("status", "?")
        elapsed = int(time.time() - start)
        if status != last or elapsed % 60 == 0:
            print(f"[{label}] [{elapsed:4d}s] {status}", flush=True)
            last = status
        if status in ("succeeded", "failed"):
            return {"label": label, "exec_id": exec_id, "data": data, "wall_s": elapsed}
        if elapsed > TIMEOUT_S:
            return {"label": label, "exec_id": exec_id, "data": data, "wall_s": elapsed, "timed_out": True}
        await asyncio.sleep(15)


async def _run_one(session, label, url):
    label, url, exec_id, out = await _kick(session, label, url)
    if not exec_id:
        return {"label": label, "error": "no exec_id"}
    print(f"[{label}] kicked off exec_id={exec_id} url={url}", flush=True)
    result = await _poll(session, label, exec_id)
    result["url"] = url
    result["out_dir"] = out
    return result


def _summary(results):
    print()
    print("=" * 80)
    print(" BATCH SUMMARY")
    print("=" * 80)
    for r in results:
        label = r["label"]
        data = r.get("data", {})
        status = data.get("status", "?")
        print()
        print(f"── [{label}] ──────────────────────────────────")
        print(f"  url    : {r.get('url')}")
        print(f"  wall   : {r.get('wall_s')}s")
        print(f"  status : {status}")
        if status == "succeeded":
            res = data.get("result", {})
            print(f"  content_mode    : {res.get('content_mode')}")
            print(f"  topic_familiarity: {res.get('topic_familiarity')}")
            print(f"  audience_level   : {res.get('audience_level')}")
            print(f"  arch={res.get('chosen_arch')} dir={res.get('direction')}")
            print(f"  voice            : {res.get('voice_id')} ({res.get('voice_tone')})")
            print(f"  duration_s       : {res.get('duration_s')}")
            print(f"  video            : {res.get('video_path')}")
            print(f"  timings_s        : {res.get('timings_s')}")
            script = (res.get('script') or '').replace('\n', '\n  ')
            print(f"  script:\n  {script}")
        elif status == "failed":
            print(f"  err     : {data.get('error', data)[:400]}")


async def main():
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as s:
        results = await asyncio.gather(*(_run_one(s, label, url) for label, url in PAPERS))
    _summary(results)


if __name__ == "__main__":
    asyncio.run(main())
