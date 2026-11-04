# Build from a qualified toolchain-only image by immutable digest. No default parent.
ARG TOOLCHAIN_IMAGE
FROM ${TOOLCHAIN_IMAGE}
USER root
# Old application code must never be callable in rewritten native images.
RUN set -eu; rm -rf /app /workspace /srv/kleos /opt/kleos /opt/kleos-native /opt/qualified-companion /opt/qualified-android \
    /opt/qualify_android_cache.py /opt/kleos-companion-runtime.json \
    /opt/gradle-cache/init.d /opt/gradle-cache/kotlin-profile /opt/gradle-cache/gradle.properties \
    /opt/pub-cache/active_roots; \
    for directory in /usr/local/lib /usr/lib /opt/venv; do \
      if [ -d "$directory" ]; then find "$directory" -type d \( -name kleos -o -name 'kleos*.dist-info' -o -name 'kleos*.egg-info' \) -prune -exec rm -rf '{}' +; fi; \
    done; \
    find /usr/local/bin /usr/bin -maxdepth 1 -type f -name 'kleos*' -delete; \
    for directory in /opt/gradle-cache/caches/*; do \
      if [ -d "$directory" ] && [ "$(basename "$directory")" != modules-2 ]; then rm -rf "$directory"; fi; \
    done
WORKDIR /opt/kleos-native
RUN mkdir -p kleos/execution && touch kleos/__init__.py kleos/execution/__init__.py
COPY src/kleos/execution/protocol.py src/kleos/execution/native_runner.py src/kleos/execution/pcb_contract.py kleos/execution/
COPY src/kleos/execution/web_bundle.py kleos/execution/
COPY src/kleos/execution/bootstrap.py src/kleos/execution/result_reader.py src/kleos/execution/pcb_runner.py src/kleos/execution/pcb_generate.py /opt/kleos-native/
# libseccomp is mandatory, not an optional weakened local mode.
RUN python -c "import ctypes; ctypes.CDLL('libseccomp.so.2')" \
    && test ! -e /app/kleos && test ! -e /workspace/kleos
LABEL kleos.native.protocol="kleos-native-session-v2"
ENTRYPOINT ["python", "-I", "/opt/kleos-native/bootstrap.py"]
