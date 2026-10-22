# syntax=docker/dockerfile:1.7

# Build on the host architecture; release binaries are cross-compiled without QEMU.
FROM --platform=$BUILDPLATFORM golang:1.27.3-alpine AS deps
WORKDIR /app
ENV GOTOOLCHAIN=local
# Changing application code keeps the downloaded dependency layer intact.
COPY go.mod go.sum ./
# Sources and checksum metadata are enough for offline builds; do not keep ZIP
# duplicates in the dependency layer transferred to each native CI builder.
RUN go mod download \
 && find /go/pkg/mod/cache/download -type f -name '*.zip' -delete

# Compiler tooling is installed before application sources, so edits reuse this layer.
FROM deps AS test
RUN apk add --no-cache gcc musl-dev
COPY cmd/ ./cmd/
COPY internal/ ./internal/
COPY testdata/ ./testdata/
CMD ["go", "test", "-mod=readonly", "-race", "-count=1", "./..."]

# CI runs in BuildKit and exports only the release binary, never the SDK image.
FROM test AS ci-test
ENV GOCACHE=/go-build GOPROXY=off GOSUMDB=off
COPY tools/ci-go.sh /ci-go.sh
RUN mkdir -p /out && chown 10001:10001 /out
USER 10001:10001
ARG VERSION=dev
ARG CI_TEST_RUN
ARG TARGETARCH
# A unique run/attempt forces real tests even when the source layers are cached.
RUN --network=none \
    --mount=type=cache,target=/go-build,uid=10001,gid=10001 \
    test "$(go env GOARCH)" = "$TARGETARCH" \
    && test -n "$CI_TEST_RUN" && sh /ci-go.sh

FROM scratch AS ci-artifacts
COPY --from=ci-test /out/fsbot /fsbot

FROM deps AS build
COPY cmd/ ./cmd/
COPY internal/ ./internal/
ARG TARGETOS
ARG TARGETARCH
ARG VERSION=dev
RUN --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -mod=readonly -trimpath -buildvcs=false \
      -ldflags="-s -w -buildid= -X main.version=$VERSION" -o /out/fsbot ./cmd/fsbot

FROM --platform=$BUILDPLATFORM alpine:3.23 AS assets
RUN apk add --no-cache ca-certificates \
 && mkdir -p /rootfs/data /rootfs/etc \
 && chown 10001:10001 /rootfs/data \
 && chmod 700 /rootfs/data \
 && printf 'fsbot:x:10001:10001:fsbot:/data:/sbin/nologin\n' > /rootfs/etc/passwd \
 && printf 'fsbot:x:10001:\n' > /rootfs/etc/group

# SQLite smoke tests only need a shell and their fixture, not the Go SDK.
FROM assets AS smoke-fixture
COPY testdata/python-state.sqlite /app/testdata/python-state.sqlite

FROM scratch AS runtime-base
ENV FSBOT_STATE_DIR=/data
COPY --from=assets /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=assets /rootfs/ /
WORKDIR /data
# Bootstrap fixes an existing volume's ownership, then drops to uid/gid 10001.
ENTRYPOINT ["/fsbot"]
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=2 \
  CMD ["/fsbot", "healthcheck"]

# CI compiles once in the tested native container and only packages its binary.
# Keep the rootfs identical to a regular source build.
FROM runtime-base AS ci-runtime
COPY .ci-bin/fsbot /fsbot

# Default target: standalone source builds keep working without CI artifacts.
FROM runtime-base AS runtime
# The application changes most often, so its layer is last. No sources, SDK or shell.
COPY --from=build /out/fsbot /fsbot
