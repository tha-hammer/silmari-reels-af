"""Run one arxiv paper through reel-af to validate scientific mode.

Fires async, polls until done. Reports timings + the propagated content_mode
so we can verify the scientific branch actually activated.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import aiohttp

from resolve_output_dir import resolve_output_dir

CONTROL_PLANE = "http://localhost:8080"
TIMEOUT_S = 1800  # papers take longer (heavier video, longer script)

# Override via argv[1] if you want a different paper. Default: DeepSeek-R1.
DEFAULT_URL = "https://arxiv.org/abs/2501.12948"


async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    label = sys.argv[2] if len(sys.argv) > 2 else "arxiv_one"
    out_dir = resolve_output_dir("scientific", label)
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as s:
        # Kick off
        payload = {"input": {"url": url, "out_dir": out_dir}}
        async with s.post(
            f"{CONTROL_PLANE}/api/v1/execute/async/reel-af.reel_article_to_reel",
            json=payload,
        ) as resp:
            body = await resp.text()
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                print("Kick-off response was not JSON:", body[:400])
                return
            exec_id = data.get("execution_id", "")
            if not exec_id:
                print("No execution_id returned:", data)
                return
            print(f"[{label}] kicked off exec_id={exec_id}")
            print(f"[{label}] url={url}")
            print(f"[{label}] out_dir={out_dir}")

        # Poll
        start = time.time()
        last_status = ""
        while True:
            try:
                async with s.get(
                    f"{CONTROL_PLANE}/api/v1/executions/{exec_id}"
                ) as resp:
                    state = await resp.json()
            except Exception as e:
                print(f"[{label}] poll error: {e}")
                await asyncio.sleep(15)
                continue
            status = state.get("status", "?")
            elapsed = int(time.time() - start)
            if status != last_status or elapsed % 60 == 0:
                print(f"[{label}] [{elapsed:4d}s] {status}")
                last_status = status
            if status in ("succeeded", "failed"):
                break
            if elapsed > TIMEOUT_S:
                print(f"[{label}] TIMEOUT after {elapsed}s")
                break
            await asyncio.sleep(15)

    print()
    print("=" * 80)
    print(f" RESULT [{label}]")
    print("=" * 80)
    print(f"  status      : {state.get('status')}")
    if state.get("status") == "succeeded":
        r = state.get("result", {})
        print(f"  content_mode    : {r.get('content_mode')}")
        print(f"  topic_familiarity: {r.get('topic_familiarity')}")
        print(f"  audience_level   : {r.get('audience_level')}")
        print(f"  chosen_arch      : {r.get('chosen_arch')}")
        print(f"  direction        : {r.get('direction')}")
        print(f"  self_score       : {r.get('self_score')}")
        print(f"  voice            : {r.get('voice_id')} ({r.get('voice_tone')})")
        print(f"  duration_s       : {r.get('duration_s')}")
        print(f"  captions         : {r.get('captions')}")
        print(f"  motifs           : {r.get('motifs')}")
        print(f"  video_path       : {r.get('video_path')}")
        print(f"  timings_s        : {r.get('timings_s')}")
        print()
        print("  SCRIPT:")
        print("  " + r.get("script", "").replace("\n", "\n  "))
    else:
        print(json.dumps(state, indent=2)[:2000])


if __name__ == "__main__":
    asyncio.run(main())
