#!/usr/bin/env bash
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_dir=$(cd -- "$script_dir/../../.." && pwd)
export FIREFOX_BASELINE_ROOT=${FIREFOX_BASELINE_ROOT:?Set FIREFOX_BASELINE_ROOT}
export MOZBUILD_STATE_PATH="$FIREFOX_BASELINE_ROOT/state"
export TMPDIR="$FIREFOX_BASELINE_ROOT/tmp"
export MOZCONFIG="$script_dir/mozconfig"
exec 9>"$FIREFOX_BASELINE_ROOT/resource.lock"
flock -n 9
cd "$source_dir"
revision=$(git rev-parse FIREFOX_159_0_1_RELEASE)
if ! git diff --quiet "$revision" -- . ':(exclude)tools/performance/baseline'; then
    echo "Stock preservation requires source unchanged from the pinned release" >&2
    exit 1
fi
./mach --no-interactive package
destination="$FIREFOX_BASELINE_ROOT/binaries/source-stock-${revision:0:8}"
if [[ -e "$destination" ]]; then
    echo "Preserved runtime already exists: $destination" >&2
    exit 1
fi
shopt -s nullglob
archives=("$FIREFOX_BASELINE_ROOT"/obj-release/dist/*.tar.xz)
if (( ${#archives[@]} != 1 )); then
    echo "Expected exactly one packaged Linux archive" >&2
    exit 1
fi
mkdir -p "$destination/symbols"
cp --reflink=auto "${archives[0]}" "$destination/firefox.tar.xz"
tar -xf "$destination/firefox.tar.xz" -C "$destination"
test -x "$destination/firefox/firefox"
for library in "$FIREFOX_BASELINE_ROOT"/obj-release/dist/bin/*.so; do
    cp --reflink=auto "$library" "$destination/symbols/"
done
cp "$MOZCONFIG" "$destination/mozconfig"
printf '%s\n' "$revision" > "$destination/source-revision.txt"
git rev-parse HEAD > "$destination/checkout-revision.txt"
git diff --stat "$revision" -- . ':(exclude)tools/performance/baseline' > "$destination/source-diff-stat.txt"
{
    "$MOZBUILD_STATE_PATH/clang/bin/clang" --version
    "$MOZBUILD_STATE_PATH/rustc/bin/rustc" -Vv
    "$MOZBUILD_STATE_PATH/rustc/bin/cargo" --version
} > "$destination/toolchain.txt"
cd "$destination"
sha256sum firefox.tar.xz mozconfig source-revision.txt checkout-revision.txt toolchain.txt firefox/firefox firefox/firefox-bin firefox/libxul.so symbols/*.so > SHA256SUMS
printf '%s\n' "$destination/firefox/firefox"
