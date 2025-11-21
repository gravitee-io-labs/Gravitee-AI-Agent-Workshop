#!/bin/sh
set -eu

FGA_API_URL=${FGA_API_URL:-http://openfga:8080}

echo "Installing tools (curl, jq, yq, tar)..."
apk add --no-cache curl jq yq tar >/dev/null

echo "Waiting for OpenFGA at $FGA_API_URL ..."
until curl -sf "$FGA_API_URL/healthz" >/dev/null; do sleep 1; done

STORE_NAME=$(yq -r '.name' /data/openfgastore.yaml || true)
if [ -z "$STORE_NAME" ] || [ "$STORE_NAME" = "null" ]; then STORE_NAME="Seeded Store"; fi

echo "Downloading fga CLI..."
ARCH=$(uname -m)
case "$ARCH" in
  x86_64) FGA_ASSET=linux_amd64 ;;
  aarch64) FGA_ASSET=linux_arm64 ;;
  arm64) FGA_ASSET=linux_arm64 ;;
  *) echo "Unsupported arch: $ARCH" >&2; exit 1 ;;
esac
TAG=$(curl -s https://api.github.com/repos/openfga/cli/releases/latest | jq -r .tag_name)
if [ -z "$TAG" ] || [ "$TAG" = "null" ]; then echo "Could not determine latest fga tag" >&2; exit 1; fi
VERSION=${TAG#v}
FGA_URL="https://github.com/openfga/cli/releases/download/$TAG/fga_${VERSION}_${FGA_ASSET}.tar.gz"
curl -fsSL -o /tmp/fga.tar.gz "$FGA_URL"
mkdir -p /usr/local/bin
tar -xzf /tmp/fga.tar.gz -C /usr/local/bin fga
chmod +x /usr/local/bin/fga
/usr/local/bin/fga version || true

echo "Importing via fga CLI..."
if [ -n "${FGA_STORE_ID:-}" ]; then
  IMPORT_OUTPUT=$(/usr/local/bin/fga store import --file /data/openfgastore.yaml --api-url "$FGA_API_URL" --store-id "$FGA_STORE_ID")
else
  IMPORT_OUTPUT=$(/usr/local/bin/fga store import --file /data/openfgastore.yaml --api-url "$FGA_API_URL")
fi
echo "$IMPORT_OUTPUT" > /tmp/fga_import.json
echo "Import output: $IMPORT_OUTPUT"

# Extract and persist store id
if [ -z "${FGA_STORE_ID:-}" ]; then
  FGA_STORE_ID=$(echo "$IMPORT_OUTPUT" | jq -r '.store.id // .storeId // empty')
fi
mkdir -p /data/out
if [ -n "${FGA_STORE_ID:-}" ]; then
  printf "%s" "$FGA_STORE_ID" > /data/out/store_id
  echo "Wrote store id to /data/out/store_id"
else
  echo "Warning: could not determine store id from import output" >&2
fi

echo "Import complete."
