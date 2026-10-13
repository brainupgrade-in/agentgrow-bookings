#!/usr/bin/env bash
# Build, push and roll out to the NUC k3s cluster (context nuc-k3s). Usage: scripts/deploy-nuc.sh
# Pushes to the NUC registry NodePort (docker trusts it as insecure); k3s pulls the same image as registry.gheware-ai.com.
set -euo pipefail
cd "$(dirname "$0")/.."
CTX=nuc-k3s NS=agentgrow-bookings PUSH=192.168.1.12:30500/agentgrow-bookings
TAG="$(git rev-parse --short HEAD)$(git diff --quiet HEAD -- bookings pyproject.toml uv.lock Dockerfile || echo -dirty)"
docker build -q -t "$PUSH:$TAG" .
docker push -q "$PUSH:$TAG"
kubectl --context $CTX create namespace $NS --dry-run=client -o yaml | kubectl --context $CTX apply -f -
kubectl --context $CTX -n $NS create secret generic bookings-env --from-env-file=.env --dry-run=client -o yaml \
  | kubectl --context $CTX apply -f -
sed "s/IMAGE_TAG/$TAG/" deploy/k8s/bookings.yaml | kubectl --context $CTX apply -f -
kubectl --context $CTX -n $NS rollout restart deploy/bookings >/dev/null   # pick up secret changes too
kubectl --context $CTX -n $NS rollout status deploy/bookings --timeout=180s
echo "deployed $TAG"
