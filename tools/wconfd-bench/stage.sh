#!/usr/bin/env bash
# stage.sh: stage the Ganeti install tree under /usr/local from the source
# build (/src), so the stock init script + daemon-util + ensure-dirs work.
# Idempotent.
set -e -u

if [ -x /usr/local/etc/init.d/ganeti ]; then
  exit 0
fi

mkdir -p /usr/local/etc/init.d /usr/local/lib/ganeti /usr/local/sbin /usr/local/bin
cp /src/doc/examples/ganeti.initd /usr/local/etc/init.d/ganeti
chmod +x /usr/local/etc/init.d/ganeti
cp /src/daemons/daemon-util /usr/local/lib/ganeti/daemon-util

# ensure-dirs needs the ganeti python package; wrap it with PYTHONPATH.
cat > /usr/local/lib/ganeti/ensure-dirs <<'EOF'
#!/bin/sh
export PYTHONPATH=/src
exec /usr/bin/python3 /src/tools/ensure-dirs "$@"
EOF
chmod +x /usr/local/lib/ganeti/ensure-dirs

# Haskell daemons -> built binaries
for d in ganeti-confd ganeti-kvmd ganeti-metad ganeti-mond ganeti-wconfd ganeti-luxid; do
  if [ -x "/src/dist/build/$d/$d" ]; then
    ln -sf "/src/dist/build/$d/$d" "/usr/local/sbin/$d"
  fi
done

# Python daemons + gnt commands -> source scripts
ln -sf /src/daemons/ganeti-noded /usr/local/sbin/ganeti-noded
ln -sf /src/daemons/ganeti-rapi /usr/local/sbin/ganeti-rapi
for s in /src/scripts/gnt-*; do
  ln -sf "$s" "/usr/local/sbin/$(basename "$s")"
done

# luxid spawns jobs as <versionedsharedir>/ganeti/jqueue/exec.py and sets
# PYTHONPATH=<versionedsharedir>. /src/lib IS the ganeti package; link it in.
# The link target of jqueueExecutorPy is versioned (3.2 here; from AutoConf.hs).
VERSIONEDSHAREDIR=$(grep -m1 '^versionedsharedir =' /src/src/AutoConf.hs | cut -d'"' -f2)
mkdir -p "$VERSIONEDSHAREDIR"
ln -sfn /src/lib "$VERSIONEDSHAREDIR/ganeti"

# ganeti python package importable everywhere (gnt-*, ensure-dirs, etc.)
# Write the .pth unconditionally: `import ganeti` may succeed in this shell
# only because the container WORKDIR is /src; gnt-* run from elsewhere.
echo "/src" > "$(python3 -c 'import site; print(site.getsitepackages()[0])')/ganeti-dev.pth"
