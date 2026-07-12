#!/usr/bin/env bash
set -euo pipefail

first="${1:?first image required}"
second="${2:?second image required}"
report="${3:-reproducibility-report.json}"
first_id="$(docker image inspect "$first" --format '{{.Id}}')"
second_id="$(docker image inspect "$second" --format '{{.Id}}')"
first_layers="$(docker image inspect "$first" --format '{{json .RootFS.Layers}}')"
second_layers="$(docker image inspect "$second" --format '{{json .RootFS.Layers}}')"
status=failed
if [ "$first_id" = "$second_id" ] && [ "$first_layers" = "$second_layers" ]; then
  status=success
fi
python3 - "$report" "$first" "$second" "$first_id" "$second_id" "$first_layers" "$second_layers" "$status" <<'PY'
import json, sys
from pathlib import Path
path, first, second, first_id, second_id, first_layers, second_layers, status = sys.argv[1:]
data = {
    "schemaVersion": 1,
    "first": {"image": first, "id": first_id, "layers": json.loads(first_layers)},
    "second": {"image": second, "id": second_id, "layers": json.loads(second_layers)},
    "status": status,
}
Path(path).parent.mkdir(parents=True, exist_ok=True)
Path(path).write_text(json.dumps(data, indent=2) + "\n")
PY
[ "$status" = success ] || {
  echo "local reproducibility probe failed: image ID or layer digests differ" >&2
  exit 1
}
printf 'local_reproducibility=PASS first=%s second=%s\n' "$first" "$second"
