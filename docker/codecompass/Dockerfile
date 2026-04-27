FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install build dependencies
RUN apt-get update && apt-get install -y \
    cmake make g++ git \
    llvm-15 clang-15 libclang-15-dev \
    libboost-all-dev \
    default-jdk \
    libgraphviz-dev \
    libmagic-dev \
    libgit2-dev \
    ctags \
    libgtest-dev \
    npm \
    postgresql-client libpq-dev \
    thrift-compiler libthrift-dev \
    python3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Build CodeCompass from source
WORKDIR /opt
RUN git clone --depth 1 https://github.com/Ericsson/CodeCompass.git
WORKDIR /opt/CodeCompass
RUN mkdir build && cd build && \
    cmake .. -DCMAKE_INSTALL_PREFIX=/opt/codecompass-install \
             -DDATABASE=pgsql \
             -DWITH_AUTH=plain \
             -DCMAKE_BUILD_TYPE=Release && \
    make -j$(nproc) && \
    make install

ENV PATH="/opt/codecompass-install/bin:$PATH"

# Create workspace directory
RUN mkdir -p /data/workspaces /data/repos

EXPOSE 6251

# Wrapper script: starts webserver on internal port, adds POST /api/parse
COPY wrapper.py /opt/codecompass-install/bin/wrapper.py
RUN chmod +x /opt/codecompass-install/bin/wrapper.py

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:6251/ || exit 1

ENTRYPOINT ["python3", "/opt/codecompass-install/bin/wrapper.py"]
