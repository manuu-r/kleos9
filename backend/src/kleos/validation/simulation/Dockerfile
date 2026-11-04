# Isolated circuit-simulation image. Runs one compiler-generated deck; no model, network or secrets.
# Debian bookworm pins ngspice 39.3, which reports itself as ngspice-39 (the version the parser trusts).
#
# Optional build-time inputs for restricted networks (nothing is retained in the image):
#   --build-arg APT_SCHEME=https --build-context extra-ca=<directory of *.crt to trust for apt>
ARG PYTHON_IMAGE=docker.io/library/python:3.12.16-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
FROM scratch AS extra-ca
FROM ${PYTHON_IMAGE}
ARG NGSPICE_VERSION=39.3+ds-1
ARG APT_SCHEME=http
COPY --from=extra-ca / /tmp/extra-ca/
RUN set -eux; \
    if ls /tmp/extra-ca/*.crt >/dev/null 2>&1; then cp /tmp/extra-ca/*.crt /usr/local/share/ca-certificates/ && update-ca-certificates; fi; \
    sed -i "s#http://deb.debian.org#${APT_SCHEME}://deb.debian.org#g" /etc/apt/sources.list.d/debian.sources; \
    apt-get update; \
    apt-get install -y --no-install-recommends "ngspice=${NGSPICE_VERSION}"; \
    rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man /tmp/extra-ca /usr/local/share/ca-certificates/*.crt; \
    update-ca-certificates --fresh >/dev/null; \
    test "$(ngspice --version | grep -o 'ngspice-[0-9.]*' | head -1)" = "ngspice-39"
COPY ngspice_runner.py /opt/kleos-sim/ngspice_runner.py
RUN chmod 0555 /opt/kleos-sim /opt/kleos-sim/ngspice_runner.py
LABEL kleos.checker.protocol="kleos-circuit-sim-v1"
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["/usr/bin/timeout", "--signal=KILL", "20", "/usr/local/bin/python3", "-I", "/opt/kleos-sim/ngspice_runner.py"]
