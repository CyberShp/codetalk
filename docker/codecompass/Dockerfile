FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# ── 1. System dependencies ────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake make g++ gcc-11 gcc-11-plugin-dev g++-11 git curl wget ca-certificates \
    llvm-15-dev clang-15 libclang-15-dev \
    libboost-filesystem-dev libboost-log-dev libboost-program-options-dev \
    libboost-regex-dev libboost-python-dev \
    default-jdk universal-ctags doxygen \
    libgit2-dev libgraphviz-dev libgtest-dev libmagic-dev \
    libsqlite3-dev libssl-dev \
    thrift-compiler libthrift-dev \
    postgresql-server-dev-14 postgresql-client \
    python3 graphviz \
    && rm -rf /var/lib/apt/lists/*

# Node.js 18 LTS (Ubuntu 22.04 ships v12 which is too old for CodeCompass webgui)
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── 2. Build ODB via build2 toolchain ─────────────────────────────────────
# ODB is not available as an Ubuntu 22.04 package; must be built from source.
RUN wget -q https://raw.githubusercontent.com/Ericsson/CodeCompass/master/scripts/install_latest_build2.sh \
    && bash install_latest_build2.sh /build2_install \
    && export PATH=/build2_install/bin:$PATH \
    && mkdir /odb_build && cd /odb_build \
    && bpkg create --quiet --jobs $(nproc) cc \
         config.cxx=g++ \
         config.cc.coptions=-O3 \
         config.bin.rpath=/opt/odb/lib \
         config.install.root=/opt/odb \
    && bpkg add https://pkg.cppget.org/1/beta --trust-yes \
    && bpkg fetch --trust-yes \
    && bpkg build odb --yes \
    && bpkg build libodb --yes \
    && bpkg build libodb-pgsql --yes \
    && bpkg install --all --recursive \
    && cd / && rm -rf /odb_build /build2_install install_latest_build2.sh

ENV CMAKE_PREFIX_PATH=/opt/odb
ENV PATH="/opt/odb/bin:/opt/codecompass-install/bin:$PATH"

# ── 3. Build CodeCompass from source ──────────────────────────────────────
WORKDIR /opt
RUN git clone --depth 1 https://github.com/Ericsson/CodeCompass.git
WORKDIR /opt/CodeCompass
RUN mkdir build && cd build && \
    cmake .. -DCMAKE_INSTALL_PREFIX=/opt/codecompass-install \
             -DDATABASE=pgsql \
             -DWITH_AUTH=plain \
             -DCMAKE_BUILD_TYPE=Release \
             -DLLVM_DIR=/usr/lib/llvm-15/cmake \
             -DClang_DIR=/usr/lib/cmake/clang-15 && \
    make -j$(nproc) && \
    make install

# ── 4. Runtime setup ──────────────────────────────────────────────────────
RUN mkdir -p /data/workspaces /data/repos

EXPOSE 6251

COPY wrapper.py /opt/codecompass-install/bin/wrapper.py
RUN chmod +x /opt/codecompass-install/bin/wrapper.py

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:6251/ || exit 1

ENTRYPOINT ["python3", "/opt/codecompass-install/bin/wrapper.py"]
