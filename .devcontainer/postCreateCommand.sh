#!/bin/bash
set -euo pipefail

uv sync --all-extras

if [[ -z "${PULUMI_BUCKET:-}" || -z "${PULUMI_S3_ENDPOINT:-}" ]]; then
  echo "Error: PULUMI_BUCKET and PULUMI_S3_ENDPOINT must be set in your environment."
  exit 1
fi

pulumi login "s3://${PULUMI_BUCKET}?region=auto&endpoint=${PULUMI_S3_ENDPOINT}&s3ForcePathStyle=true"

pulumi stack select dev || pulumi stack init dev
