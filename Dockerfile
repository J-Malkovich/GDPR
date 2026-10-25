# bw CLI: pinned (2026.9.1+ fails login against our Vaultwarden) version, checksum verified against the official GitHub release asset
FROM debian:bookworm-slim AS bw
ARG TARGETARCH
ARG BW_VERSION=2026.8.0
ARG BW_SHA256_AMD64=367f618e9fcccaac4980ec12c7bafd01df739b5f3cb1af31bc9045cf75eea1d6
ARG BW_SHA256_ARM64=74d822a5dceda5896ed8fc07bc61925b29afd98d96a6a3e9e525ae556c3083a8
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates \
 && case "$TARGETARCH" in amd64) A=linux; S=$BW_SHA256_AMD64;; arm64) A=linux-arm64; S=$BW_SHA256_ARM64;; *) echo "unsupported arch $TARGETARCH"; exit 1;; esac \
 && curl -fsSL -o /bw.zip "https://github.com/bitwarden/clients/releases/download/cli-v${BW_VERSION}/bw-${A}-${BW_VERSION}.zip" \
 && echo "$S  /bw.zip" | sha256sum -c - \
 && unzip /bw.zip bw -d /out && chmod 755 /out/bw

FROM python:3.12-slim
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY --from=bw /out/bw /usr/local/bin/bw
WORKDIR /app
COPY gdpr.py test_gdpr.py ./
# a failing test fails the build
RUN python -m unittest test_gdpr
RUN useradd -r -u 10001 gdpr
USER gdpr
CMD ["python","-u","gdpr.py"]
