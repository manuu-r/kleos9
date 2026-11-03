# Pinned engineering toolchain parents for the native execution images.
#
# These stages install third-party tools only; they contain no Kleos code. Build a stage, record its image
# ID, then build ../Dockerfile with `--build-arg TOOLCHAIN_IMAGE=<that image ID>`; qualify that child image.
# Targets: mechanics-toolchain (build123d CAD), electronics-toolchain (KiCad, Freerouting, atopile),
# firmware-toolchain (Zephyr 4.2.0, ARM GCC).
# Others are added only once built and qualified here.
#
# Optional inputs for restricted networks (nothing is retained in the image):
#   --build-arg APT_SCHEME=https --build-arg https_proxy=... --build-context extra-ca=<dir of *.crt>
ARG PYTHON_IMAGE=docker.io/library/python:3.12.16-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
FROM scratch AS extra-ca

FROM ${PYTHON_IMAGE} AS base
ARG APT_SCHEME=http
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 KLEOS_NATIVE_SANDBOX=1 PIP_NO_CACHE_DIR=1
COPY kleos-net.sh /usr/local/sbin/kleos-net
RUN --mount=type=bind,from=extra-ca,target=/extra-ca \
    set -eux; chmod 0755 /usr/local/sbin/kleos-net; kleos-net begin; \
    pip install "pydantic==2.14.2" "httpx==0.28.1"; \
    useradd --uid 10001 --create-home builder; \
    apt-get update; apt-get install -y --no-install-recommends binutils libseccomp2; \
    kleos-net end

FROM base AS mechanics-toolchain
RUN --mount=type=bind,from=extra-ca,target=/extra-ca \
    set -eux; kleos-net begin; \
    pip install "build123d==0.10.0" "ocp-gordon==0.1.18"; \
    apt-get update; apt-get install -y --no-install-recommends libgl1 libglu1-mesa libxrender1 libxext6 libsm6; \
    kleos-net end

# KiCad and Freerouting for PCB generation, routing, ERC/DRC and manufacturing exports. Versions and checksums
# are the ones the qualified electronics evidence was recorded against.
FROM mechanics-toolchain AS electronics-toolchain
ARG KICAD_VERSION=9.0.8+dfsg-1~bpo12+1
ARG KICAD_LIBRARIES_VERSION=9.0.8-1~bpo12+1
ARG FREEROUTING_VERSION=2.1.0
ARG FREEROUTING_SHA256=2c07d58f75dac03782664081e7a58b41c25400d871a9fcf166a2ea6fe60d5def
ARG TEMURIN_JRE_URL=https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/OpenJDK21U-jre_x64_linux_hotspot_21.0.13_9.tar.gz
ARG TEMURIN_JRE_SHA256=a535a1df9c6284d9618872984e026ae0352d1f7e9e0605425c0e6114f47351dd
# atopile 0.12.5 needs CPython 3.13; keep it in an isolated tool environment away from the supervisor's Python.
ENV UV_PYTHON_INSTALL_DIR=/opt/ato-python UV_TOOL_DIR=/opt/ato-tools UV_TOOL_BIN_DIR=/opt/toolchains/bin
RUN --mount=type=bind,from=extra-ca,target=/extra-ca \
    set -eux; kleos-net begin; \
    echo 'deb https://deb.debian.org/debian bookworm-backports main' > /etc/apt/sources.list.d/backports.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends -t bookworm-backports \
        "kicad=${KICAD_VERSION}" "kicad-symbols=${KICAD_LIBRARIES_VERSION}" \
        "kicad-footprints=${KICAD_LIBRARIES_VERSION}" gcc libc6-dev curl ca-certificates; \
    mkdir -p /opt/freerouting /opt/java21; \
    curl --fail --location --proto '=https' --tlsv1.2 "$TEMURIN_JRE_URL" --output /tmp/java.tar.gz; \
    echo "$TEMURIN_JRE_SHA256  /tmp/java.tar.gz" | sha256sum --check --strict; \
    tar -xzf /tmp/java.tar.gz --strip-components=1 -C /opt/java21; rm /tmp/java.tar.gz; \
    ln -s /opt/java21/bin/java /usr/local/bin/java; \
    curl --fail --location --proto '=https' --tlsv1.2 \
        "https://github.com/freerouting/freerouting/releases/download/v${FREEROUTING_VERSION}/freerouting-${FREEROUTING_VERSION}.jar" \
        --output /opt/freerouting/freerouting.jar; \
    echo "$FREEROUTING_SHA256  /opt/freerouting/freerouting.jar" | sha256sum --check --strict; \
    pip install "uv==0.9.8"; \
    export UV_NATIVE_TLS=1; uv python install 3.13.7; \
    uv tool install --python 3.13.7 "atopile==0.12.5"; \
    chmod -R a+rX /opt/ato-python /opt/ato-tools; \
    echo 'deb-removed' >/dev/null; rm -f /etc/apt/sources.list.d/backports.list; \
    kleos-net end

# Zephyr RTOS and ARM GCC for explicitly qualified starter boards only (nRF52840 DK, native_sim). A new
# HAL or board requires a new baked layer and qualification; nothing is fetched at job time.
FROM base AS firmware-toolchain
ARG ZEPHYR_VERSION=v4.2.0
RUN --mount=type=bind,from=extra-ca,target=/extra-ca \
    set -eux; kleos-net begin; \
    apt-get update; \
    apt-get install -y --no-install-recommends git cmake ninja-build gperf ccache device-tree-compiler \
        gcc-arm-none-eabi libnewlib-arm-none-eabi make gcc gcc-multilib libc6-dev; \
    pip install "west==1.5.0"; \
    git clone --depth 1 --branch "${ZEPHYR_VERSION}" https://github.com/zephyrproject-rtos/zephyr /opt/zephyrproject/zephyr; \
    west init -l /opt/zephyrproject/zephyr; \
    cd /opt/zephyrproject; west config zephyr.base zephyr; \
    west update --narrow --fetch-opt=--depth=1 cmsis cmsis_6 hal_stm32; \
    pip install -r zephyr/scripts/requirements-base.txt; \
    kleos-net end
# Nordic starter-board support is pinned by the Zephyr release manifest; a separate layer avoids recloning Zephyr.
RUN --mount=type=bind,from=extra-ca,target=/extra-ca \
    set -eux; kleos-net begin; \
    cd /opt/zephyrproject; west update --narrow --fetch-opt=--depth=1 hal_nordic; \
    kleos-net end
