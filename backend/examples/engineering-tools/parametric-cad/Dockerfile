# Development example: retain the installed CAD toolchain and replace app code.
# Production installations should use a pinned, reviewed base image digest.
ARG BASE_IMAGE=kleos-mechanics:development
FROM ${BASE_IMAGE}
COPY kleos /opt/kleos/kleos
COPY kleos /usr/local/lib/python3.12/site-packages/kleos
COPY --chmod=0555 examples/engineering-tools/parametric-cad/run /opt/kleos-tools/parametric-cad/run
