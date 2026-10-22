# syntax=docker/dockerfile:1.7

# Build on the host architecture; release binaries are cross-compiled without QEMU.
FROM --platform=$BUILDPLATFORM golang:1.27.3-alpine AS deps
WORKDIR /app
ENV GOTOOLCHAIN=local
# Changing application code keeps the downloaded dependency layer intact.
COPY go.mod go.sum ./
RUN go mod download

# Compiler tooling is installed before application sources, so edits reuse this layer.
FROM deps AS test
RUN apk add --no-cache build-base
COPY cmd/ ./cmd/
COPY internal/ ./internal/
COPY testdata/ ./testdata/
CMD ["go", "test", "-mod=readonly", "-race", "-count=1", "./..."]

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

FROM scratch AS runtime
ENV FSBOT_STATE_DIR=/data
COPY --from=assets /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=assets /rootfs/ /
WORKDIR /data
# The application changes most often, so its layer is last. No sources, SDK or shell.
COPY --from=build /out/fsbot /fsbot
# Bootstrap fixes an existing volume's ownership, then drops to uid/gid 10001.
ENTRYPOINT ["/fsbot"]
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=2 \
  CMD ["/fsbot", "healthcheck"]
