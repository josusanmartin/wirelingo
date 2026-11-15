#!/bin/bash
# LIBRELANE_CONTAINER_ENGINE wrapper for a GitHub-runner-sized harden: every
# `docker run` gets 4 CPUs (cpuset 0-3), 16 GB of memory and no swap beyond it,
# and LibreLane's core count 4 (os.cpu_count() on a 4-CPU runner); everything
# else passes through to docker unchanged.
if [ "${1:-}" = run ]; then
  shift
  echo "$(date -u +%FT%TZ) docker run --cpuset-cpus=0-3 --memory=16g --memory-swap=16g -e _OPENLANE_MAX_CORES=4 $*" >> "$HOME/protoemu-flow/aq-search/docker4.log"
  exec /usr/bin/docker run --cpuset-cpus=0-3 --memory=16g --memory-swap=16g -e _OPENLANE_MAX_CORES=4 "$@"
fi
exec /usr/bin/docker "$@"
