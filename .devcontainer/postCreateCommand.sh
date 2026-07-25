#!/bin/bash
set -euo pipefail

uv sync --all-extras --locked

if [[ -n "${PULUMI_BUCKET:-}" && -n "${PULUMI_S3_ENDPOINT:-}" ]]; then
  pulumi login "s3://${PULUMI_BUCKET}?region=auto&endpoint=${PULUMI_S3_ENDPOINT}&s3ForcePathStyle=true"
elif [[ -n "${PULUMI_BUCKET:-}" || -n "${PULUMI_S3_ENDPOINT:-}" ]]; then
  echo "Error: set both PULUMI_BUCKET and PULUMI_S3_ENDPOINT, or neither."
  exit 1
else
  echo "No custom Pulumi backend configured; using the current Pulumi login."
fi

TASK_PULUMI_STACK="${PULUMI_STACK:-dev}"
pulumi stack select "${TASK_PULUMI_STACK}" || pulumi stack init "${TASK_PULUMI_STACK}"
