# WConfd performance benchmark image (standalone dev tooling, not part of the build)
#
# Builds Ganeti from the repository this file lives in, on top of the CI image
# used by .devcontainer and .github/workflows/ci.yml. The build is cached in
# the image, so benchmark runs start fast.
#
# Build  (from repo root):
#   docker build -f tools/wconfd-bench/Dockerfile -t wconfd-bench .
#
# Run a benchmark tier (results land in ./results/):
#   docker run --rm --ulimit nofile=1024:4096 --privileged \
#     -v "$PWD/results:/out" wconfd-bench smoke
#   docker run --rm --ulimit nofile=1024:4096 --privileged \
#     -v "$PWD/results:/out" wconfd-bench 500kb
#
# Interactive shell instead (for debugging inside the container):
#   docker run --rm -it --ulimit nofile=1024:4096 --privileged \
#     -v "$PWD/results:/out" --entrypoint bash wconfd-bench
#
# --privileged is needed because vcluster-setup uses `ip addr add` to give
# each virtual node its own IP inside the container.

FROM docker.io/ganeti/ci:trixie-py3

WORKDIR /src
COPY . .

RUN ./autogen.sh \
 && ./configure \
 && make -j "$(nproc)" \
 && cp tools/vcluster-setup /usr/local/bin/vcluster-setup

WORKDIR /opt/wconfd-bench
ENTRYPOINT ["/src/tools/wconfd-bench/bench.sh"]
CMD ["smoke"]
