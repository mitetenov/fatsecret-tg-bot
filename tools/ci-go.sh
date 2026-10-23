#!/bin/sh
# Run in the native test environment, with persistent GOCACHE and writable /out.
set -eu
: "${VERSION:?VERSION must identify the tested commit}"

unformatted=$(gofmt -l cmd internal)
if [ -n "$unformatted" ]; then
    printf 'Run gofmt on:\n%s\n' "$unformatted" >&2
    exit 1
fi
CGO_ENABLED=0 go vet -mod=readonly -trimpath -buildvcs=false ./...
# vet already ran above. Always execute tests, even when compilation is cached.
go test -mod=readonly -vet=off -count=1 -timeout=5m ./...
go test -mod=readonly -vet=off -race -count=1 -timeout=5m ./...
# Reuse non-race dependencies compiled by vet; never ship the race binary.
CGO_ENABLED=0 go build -mod=readonly -trimpath -buildvcs=false \
    -ldflags="-s -w -buildid= -X main.version=$VERSION" \
    -o /out/fsbot ./cmd/fsbot
