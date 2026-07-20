#!/usr/bin/env bash
# Fire N articles through the reel-af workflow sequentially.
# Async invocation → poll until terminal. Summarises script + path per article.
#
# Pre-req: af server + reel_af.app are both running.
#
# Usage:  scripts/run_batch.sh

set -euo pipefail

ARTICLES=(
  "https://www.quantamagazine.org/quantum-jamming-explores-the-truly-fundamental-principles-of-nature-20260417/|science"
  "https://stratechery.com/2026/the-data-center-veto/|business"
  "https://abyss.fish/your_dotfiles_are_not_a_distro|culture"
)

for entry in "${ARTICLES[@]}"; do
  url="${entry%%|*}"
  genre="${entry##*|}"
  out_dir="$(python3 scripts/resolve_output_dir.py batch "$genre")"
  rm -rf "$out_dir"
  mkdir -p "$out_dir"

  echo
  echo "=========================================================================="
  echo " [$genre]  $url"
  echo "=========================================================================="

  exec_id=$(/usr/bin/curl -sS -X POST "http://localhost:8080/api/v1/execute/async/reel-af.reel_article_to_reel" \
    -H "Content-Type: application/json" \
    -d "{\"input\":{\"url\":\"$url\",\"out_dir\":\"$out_dir\"}}" \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('execution_id',''))")

  if [ -z "$exec_id" ]; then
    echo "  ⚠ no execution_id returned"; continue
  fi
  echo "  execution_id=$exec_id"

  start=$(date +%s)
  while :; do
    resp=$(/usr/bin/curl -sS "http://localhost:8080/api/v1/executions/$exec_id")
    status=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','?'))")
    elapsed=$(($(date +%s) - start))
    case "$status" in
      succeeded)
        echo "  [${elapsed}s] succeeded"
        echo "$resp" | python3 -c "
import json,sys
r = json.load(sys.stdin)['result']
print(f'  script        : {r.get(\"script\",\"\")[:600]}')
print(f'  direction     : {r.get(\"direction\")}  arch={r.get(\"chosen_arch\")}  score={r.get(\"self_score\")}')
print(f'  voice         : {r.get(\"voice_id\")} ({r.get(\"voice_tone\")})')
print(f'  duration      : {r.get(\"duration_s\"):.1f}s')
print(f'  captions      : {r.get(\"captions\")}')
print(f'  motifs        : {r.get(\"motifs\")}')
print(f'  video_path    : {r.get(\"video_path\")}')
print(f'  timings       : {r.get(\"timings_s\")}')
"
        break
        ;;
      failed)
        echo "  [${elapsed}s] FAILED:"
        echo "$resp" | python3 -m json.tool | tail -15
        break
        ;;
      *)
        printf "  [%4ds] %s\n" "$elapsed" "$status"
        ;;
    esac
    sleep 20
    if [ "$elapsed" -gt 900 ]; then echo "  timeout at 15min"; break; fi
  done
done

echo
batch_parent="$(dirname "$(python3 scripts/resolve_output_dir.py batch summary)")"
echo "All outputs under $batch_parent/batch-*/"
ls -la "$batch_parent"/batch-*/reel.mp4 2>/dev/null || true
