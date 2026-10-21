FROM ubuntu:22.04

ARG SLICER_URL=https://download.slicer.org/bitstream/6911b598ac7b1c95e7934427
ARG SLICER_SHA512=2ea56b6f0c027fa73c832b23c34948e69b1b5124edf337a35f6a062f5cb78e7feb792c11bc02a4986f26e458ddfd954b00255953018bf6cc7d73834aba9f0267

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
        xvfb x11vnc novnc websockify procps \
        libgl1 libglu1-mesa libegl1 libxt6 libxrender1 libxkbcommon-x11-0 \
        libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
        libxcb-render-util0 libxcb-xinerama0 libxcb-xkb1 libnss3 \
        libxcomposite1 libxcursor1 libxi6 libxrandr2 libxtst6 libxdamage1 \
        libasound2 libpulse-mainloop-glib0 libdbus-1-3 libfontconfig1 \
        libsm6 libice6 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "$SLICER_URL" -o /tmp/slicer.tar.gz \
    && echo "$SLICER_SHA512  /tmp/slicer.tar.gz" | sha512sum -c - \
    && mkdir /opt/slicer \
    && tar -xzf /tmp/slicer.tar.gz -C /opt/slicer --strip-components=1 \
    && rm /tmp/slicer.tar.gz
ENV PATH=/opt/slicer:$PATH

# Libraries Slicer links against that the base list above does not cover
# (found with ldd over the unpacked tree). Kept in its own layer so the
# Slicer download above stays cached.
RUN apt-get update && apt-get install -y --no-install-recommends         libxcb-shape0 libhwloc15 libcups2 libpcre2-16-0 xauth     && rm -rf /var/lib/apt/lists/*

COPY MyomaReviewLite/ /opt/MyomaReviewLite/
COPY studio/slicer/start-studio.sh /usr/local/bin/start-studio.sh
RUN chmod 0755 /usr/local/bin/start-studio.sh

RUN useradd --uid 1000 --create-home --shell /bin/bash reader
USER reader
ENV HOME=/home/reader \
    DISPLAY=:1 \
    MYOMA_REVIEW_LITE_CASE=/review/queue.json \
    MYOMA_REVIEW_LITE_DATA_ROOT=/data \
    MYOMA_REVIEW_LITE_STUDIO=1

RUN xvfb-run -a Slicer --version

EXPOSE 6080
ENTRYPOINT ["/usr/local/bin/start-studio.sh"]
