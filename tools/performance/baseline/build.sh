#!/usr/bin/env bash
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_dir=$(cd -- "$script_dir/../../.." && pwd)
export FIREFOX_BASELINE_ROOT=${FIREFOX_BASELINE_ROOT:?Set FIREFOX_BASELINE_ROOT on a disk with at least 40 GiB free}
mkdir -p "$FIREFOX_BASELINE_ROOT/tmp"
export MOZBUILD_STATE_PATH="$FIREFOX_BASELINE_ROOT/state"
export TMPDIR="$FIREFOX_BASELINE_ROOT/tmp"
export MOZCONFIG="$script_dir/mozconfig"
exec 9>"$FIREFOX_BASELINE_ROOT/resource.lock"
flock -n 9
available=$(df -PB1 "$FIREFOX_BASELINE_ROOT" | awk 'NR == 2 {print $4}')
if (( available < 10 * 1024 * 1024 * 1024 )); then
    echo "Less than 10 GiB disk reserve" >&2
    exit 1
fi
cd "$source_dir"
exec ./mach --no-interactive build
