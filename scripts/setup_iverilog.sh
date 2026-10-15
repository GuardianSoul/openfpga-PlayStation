#!/usr/bin/env bash
# Optional project-local simulator; no sudo or system installation.
set -euo pipefail
task_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
task_tools="$task_root/build/tools"
task_source="$task_tools/iverilog-src"
task_commit=4fd5291632232fbe1ba49b2c26bb6b2bf1c6c9cf
for task_dependency in git make g++ gcc autoconf gperf bison flex; do
    command -v "$task_dependency" >/dev/null || { echo "Missing tool: $task_dependency" >&2; exit 1; }
done
mkdir -p "$task_tools"
if [[ ! -d "$task_source" ]]; then
    git clone --depth 1 --branch v12_0 https://github.com/steveicarus/iverilog.git "$task_source"
fi
[[ $(git -C "$task_source" rev-parse HEAD) == "$task_commit" ]] || {
    echo "Local Icarus source does not match the pinned v12_0 commit." >&2; exit 1;
}
cd "$task_source"
sh autoconf.sh
./configure --prefix="$task_tools/iverilog"
make -j"${PSX_BUILD_JOBS:-4}"
make install
