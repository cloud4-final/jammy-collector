#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JSONL_FILE="${SCRIPT_DIR}/events.jsonl"

if [[ ! -f "$JSONL_FILE" ]]; then
    echo "Error: ${JSONL_FILE} not found." >&2
    exit 1
fi

echo "Sending events to ${BASE_URL}/api/v1/events..."
count=0
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    resp=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST "${BASE_URL}/api/v1/events" \
        -H "Content-Type: application/json" \
        -d "$line")
    status=$(echo "$resp" | grep "HTTP_STATUS:" | cut -d':' -f2)
    body=$(echo "$resp" | grep -v "HTTP_STATUS:")
    count=$((count + 1))
    echo "[${count}] Status: ${status} | Response: ${body}"
done < "$JSONL_FILE"

echo "Finished sending ${count} events."
