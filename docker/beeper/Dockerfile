# Beeper Server: Beeper's headless client, closed source and unsigned.
# The build is pinned to one update-feed release and checked against its sha512:
# https://api.beeper.com/desktop/update-feed.json?bundleID=com.automattic.beeper.server&platform=linux&channel=stable&arch={x64,arm64}
FROM debian:bookworm-slim

ARG TARGETARCH
ARG BEEPER_SERVER_VERSION=4.3.284
ARG BEEPER_SERVER_SHA512_amd64=6wR1h4W9mnuMxC28FckeQOGMzXMePblzbypCxcMIKmcgyJ50pEmIeYHehb4epB0Tfzyd630neeVcGYAIDIepCA==
ARG BEEPER_SERVER_SHA512_arm64=7eFmHSvG0+rbRWKkhb5oOiUgPKDIsL0wFgCLSVUaMGHPSu82tKiu6UE8m9iPcM0+8Ucm8Aiy9ATz6xyttsz/Qw==

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl openssl libsecret-1-0 libglib2.0-0 tini \
 && rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) arch=x64; sha512=$BEEPER_SERVER_SHA512_amd64 ;; \
      arm64) arch=arm64; sha512=$BEEPER_SERVER_SHA512_arm64 ;; \
      *) echo "unsupported architecture $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/server.tgz \
      "https://beeper-desktop.download.beeper.com/builds/beeper-server-${BEEPER_SERVER_VERSION}-linux-${arch}.tar.gz"; \
    actual=$(openssl dgst -sha512 -binary /tmp/server.tgz | base64 -w0); \
    if [ "$actual" != "$sha512" ]; then echo "beeper-server sha512 mismatch" >&2; exit 1; fi; \
    mkdir -p /opt/beeper; \
    tar -xzf /tmp/server.tgz -C /opt/beeper --strip-components=1 --no-same-owner; \
    rm /tmp/server.tgz

RUN useradd --create-home --home-dir /data --uid 1000 beeper
USER beeper
ENV HOME=/data XDG_CACHE_HOME=/data/cache
WORKDIR /data
VOLUME /data

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
  CMD curl -fs http://127.0.0.1:23373/v1/info >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/beeper/beeper-server", "--host=0.0.0.0", "--port=23373", "--data-dir=/data/server"]
