# Build Undernet ircu2 from upstream for the pybot test harness.
ARG IRCU_REF=main

FROM debian:trixie-slim AS builder

ARG IRCU_REF=main

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    gcc \
    make \
    bison \
    flex \
    autoconf \
    automake \
    autoconf-archive \
    libc6-dev \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN git clone --depth 1 --branch "${IRCU_REF}" https://github.com/UndernetIRC/ircu2.git ircu2
WORKDIR /build/ircu2

RUN ./autogen.sh \
    && ./configure --prefix=/opt/ircu --with-maxcon=512 --with-tls=openssl \
    && make -j"$(nproc)"

FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl3t64 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -m -d /opt/ircu ircu \
    && mkdir -p /opt/ircu/bin /opt/ircu/lib

COPY --from=builder /build/ircu2/ircd/ircd /opt/ircu/bin/ircd
COPY ircd.conf /opt/ircu/lib/ircd.conf
RUN touch /opt/ircu/lib/ircd.motd \
    && chown -R ircu:ircu /opt/ircu

USER ircu
WORKDIR /opt/ircu
EXPOSE 6667

# -n = no fork (foreground for Docker)
CMD ["/opt/ircu/bin/ircd", "-f", "/opt/ircu/lib/ircd.conf", "-n"]
