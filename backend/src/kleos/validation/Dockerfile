ARG TOOLCHAIN_IMAGE
FROM ${TOOLCHAIN_IMAGE}
USER root
WORKDIR /tmp
RUN rm -rf /app /workspace /srv/kleos /opt/kleos /opt/kleos-native \
    && find /usr/local/lib /usr/lib /opt/venv -type d \( -name kleos -o -name 'kleos-*.dist-info' \) -prune -exec rm -rf '{}' + 2>/dev/null || true
COPY native_check.py /opt/kleos-checker/native_check.py
RUN chmod 0555 /opt/kleos-checker /opt/kleos-checker/native_check.py
LABEL kleos.checker.protocol="kleos-cad-check-v1"
USER 10001:10001
ENTRYPOINT ["python", "-I", "/opt/kleos-checker/native_check.py"]
