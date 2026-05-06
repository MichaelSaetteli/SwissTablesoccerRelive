#!/usr/bin/env bash
# bench_io.sh - Synology DS1522+ I/O benchmark for the video pipeline.
#
# Measures the four numbers we actually need:
#   1. Sequential write throughput (single stream)
#   2. Sequential read throughput  (single stream)
#   3. Parallel-write scaling at N=1, 2, 4, 8 streams
#      -> directly informs ffmpeg.max_workers in config_*.json
#   4. Concurrent read+write on the same volume
#      -> simulates the real ffmpeg load (read inputs, write output)
#
# Plus a quick inventory (volumes, RAID, network) so we can decide
# whether a hardware change (separate volumes, SSD cache, RAID 10) is
# worth doing.
#
# USAGE
#   ssh admin@<NAS-IP>
#   sudo -i                              # for /proc/sys/vm/drop_caches
#   curl -O <raw-url>/bench_io.sh
#   chmod +x bench_io.sh
#   ./bench_io.sh                        # default: 4 GB per test
#   SIZE_GB=8 ./bench_io.sh              # more accurate, slower
#   WORK_DIR=/volume2 ./bench_io.sh      # benchmark a different volume
#
# OUTPUT
#   Human-readable progress on stdout. A machine-readable JSON summary
#   is appended at the very end between BEGIN_JSON / END_JSON markers
#   - copy that block back to the chat for analysis.

set -u
set -o pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WORK_DIR="${WORK_DIR:-/volume1/video-pipeline}"
SIZE_GB="${SIZE_GB:-4}"        # per-stream bench file size in GB
PARALLEL_LEVELS="${PARALLEL_LEVELS:-1 2 4 8}"
RUN_ID="bench_$$_$(date +%s)"
BENCH_DIR="$WORK_DIR/.bench_$RUN_ID"

# JSON accumulator (we'll append k=v lines and emit JSON at the end).
SUMMARY_FILE="$(mktemp -t bench_summary.XXXXXX)"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log()    { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
section() { printf '\n=== %s ===\n' "$*"; }
record()  { printf '%s=%s\n' "$1" "$2" >> "$SUMMARY_FILE"; }

cleanup() {
    log "Cleanup: removing $BENCH_DIR"
    rm -rf "$BENCH_DIR" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

require_dir_writable() {
    mkdir -p "$WORK_DIR" 2>/dev/null \
        || { echo "ERROR: cannot create $WORK_DIR" >&2; exit 1; }
    if ! touch "$WORK_DIR/.bench_write_test" 2>/dev/null; then
        echo "ERROR: $WORK_DIR is not writable by $(id -un)" >&2
        exit 1
    fi
    rm -f "$WORK_DIR/.bench_write_test"
}

human_mbps() {
    # Convert bytes/sec to MB/s with 1 decimal.
    awk -v n="$1" 'BEGIN { printf "%.1f", n / 1024 / 1024 }'
}

drop_caches() {
    if [ -w /proc/sys/vm/drop_caches ]; then
        sync
        echo 3 > /proc/sys/vm/drop_caches 2>/dev/null
        return 0
    fi
    return 1
}

# Run dd, capture wall-clock seconds in $TIME_S and bytes in $BYTES.
# Sets globals $TIME_S, $BYTES, $MBPS.
run_dd() {
    local out_path="$1"; shift
    local count_mb="$1"; shift
    local extra_args="$*"

    local t0 t1
    t0=$(date +%s.%N)
    # 1 MiB block size, $count_mb iterations.
    # conv=fdatasync forces flush so the timer covers the actual write.
    dd if=/dev/zero of="$out_path" bs=1M count="$count_mb" \
        conv=fdatasync $extra_args 2>/dev/null
    t1=$(date +%s.%N)
    BYTES=$((count_mb * 1024 * 1024))
    TIME_S=$(awk -v a="$t1" -v b="$t0" 'BEGIN { printf "%.3f", a - b }')
    MBPS=$(awk -v b="$BYTES" -v t="$TIME_S" \
        'BEGIN { if (t > 0) printf "%.1f", b / 1024 / 1024 / t; else print "0" }')
}

# Read a file straight to /dev/null after dropping caches; sets globals.
run_dd_read() {
    local in_path="$1"; shift
    drop_caches >/dev/null || true
    local t0 t1 size_b
    size_b=$(stat -c%s "$in_path")
    t0=$(date +%s.%N)
    dd if="$in_path" of=/dev/null bs=1M 2>/dev/null
    t1=$(date +%s.%N)
    BYTES=$size_b
    TIME_S=$(awk -v a="$t1" -v b="$t0" 'BEGIN { printf "%.3f", a - b }')
    MBPS=$(awk -v b="$BYTES" -v t="$TIME_S" \
        'BEGIN { if (t > 0) printf "%.1f", b / 1024 / 1024 / t; else print "0" }')
}

# ---------------------------------------------------------------------------
# 0. Header + inventory
# ---------------------------------------------------------------------------

section "Configuration"
echo "Hostname        : $(hostname)"
echo "Date            : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "WORK_DIR        : $WORK_DIR"
echo "SIZE_GB         : $SIZE_GB    (per-stream bench file size)"
echo "PARALLEL_LEVELS : $PARALLEL_LEVELS"
echo "User            : $(id -un) (uid=$(id -u))"

record hostname "$(hostname)"
record date     "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
record work_dir "$WORK_DIR"
record size_gb  "$SIZE_GB"

require_dir_writable

if drop_caches; then
    echo "Drop caches     : yes (running as root)"
    record drop_caches yes
else
    echo "Drop caches     : NO (not root) - read benchmarks may be cache-warm"
    record drop_caches no
fi

# ---- DSM / Synology model ----
section "DSM / Synology model"
if [ -r /etc.defaults/VERSION ]; then
    grep -E '^(productversion|buildnumber|smallfixnumber|os_name|builddate)=' /etc.defaults/VERSION 2>/dev/null
    record dsm_version "$(awk -F\" '/^productversion=/ {print $2}' /etc.defaults/VERSION 2>/dev/null)"
    record dsm_build   "$(awk -F\" '/^buildnumber=/ {print $2}' /etc.defaults/VERSION 2>/dev/null)"
fi
if [ -r /proc/sys/kernel/syno_hw_version ]; then
    model=$(cat /proc/sys/kernel/syno_hw_version)
    echo "Hardware model  : $model"
    record syno_hw_version "$model"
fi
echo "Kernel          : $(uname -r)"
record kernel "$(uname -r)"

# ---- Volumes (size, free, fstype, mount opts) ----
section "Volumes detail"
volumes_csv=""
for vol in $(df -P 2>/dev/null | awk '/^\/dev/ && $6 ~ /^\/volume[0-9]+$/ {print $6}' | sort -u); do
    fstype=$(df -PT "$vol" 2>/dev/null | awk 'NR==2 {print $2}')
    total_kb=$(df -P "$vol" | awk 'NR==2 {print $2}')
    used_kb=$(df -P "$vol"  | awk 'NR==2 {print $3}')
    free_kb=$(df -P "$vol"  | awk 'NR==2 {print $4}')
    total_gb=$(awk -v k="$total_kb" 'BEGIN { printf "%.1f", k/1024/1024 }')
    used_gb=$(awk -v k="$used_kb"  'BEGIN { printf "%.1f", k/1024/1024 }')
    free_gb=$(awk -v k="$free_kb"  'BEGIN { printf "%.1f", k/1024/1024 }')
    pct=$(awk -v u="$used_kb" -v t="$total_kb" \
        'BEGIN { if (t>0) printf "%.0f", u*100/t; else print "0" }')
    mount_opts=$(awk -v m="$vol" '$2==m {print $4; exit}' /proc/mounts 2>/dev/null)
    backing=$(awk -v m="$vol" '$2==m {print $1; exit}' /proc/mounts 2>/dev/null)

    printf '  %-12s fs=%-7s size=%8s GB  free=%8s GB (%s%% used)\n' \
        "$vol" "$fstype" "$total_gb" "$free_gb" "$pct"
    printf '  %-12s backing=%s\n' "" "$backing"
    printf '  %-12s mount opts=%s\n' "" "${mount_opts:-(none)}"

    key=$(echo "$vol" | tr '/' '_' | sed 's/^_//')   # /volume1 -> volume1
    record "vol_${key}_fstype"     "$fstype"
    record "vol_${key}_total_gb"   "$total_gb"
    record "vol_${key}_used_gb"    "$used_gb"
    record "vol_${key}_free_gb"    "$free_gb"
    record "vol_${key}_pct_used"   "$pct"
    record "vol_${key}_backing"    "$backing"
    record "vol_${key}_mount_opts" "$mount_opts"
    volumes_csv="${volumes_csv}${vol},"
done
record volumes "${volumes_csv%,}"

# ---- Shared folders inside the volumes (size + file count) ----
section "Shared folders (top-level dirs per volume)"
for vol in $(echo "${volumes_csv%,}" | tr ',' '\n'); do
    [ -d "$vol" ] || continue
    for entry in "$vol"/*; do
        [ -d "$entry" ] || continue
        name=$(basename "$entry")
        # Skip Synology-internal hidden folders
        case "$name" in
            \@*|@*|.*|"#recycle") continue ;;
        esac
        size_kb=$(du -sk "$entry" 2>/dev/null | awk '{print $1}')
        size_gb=$(awk -v k="${size_kb:-0}" 'BEGIN { printf "%.1f", k/1024/1024 }')
        file_count=$(find "$entry" -maxdepth 4 -type f 2>/dev/null | wc -l | tr -d ' ')
        printf '  %-50s %8s GB  %8s files\n' "$entry" "$size_gb" "$file_count"
        # Sanitize the path into a JSON-safe key
        key=$(echo "$entry" | tr '/' '_' | tr -cd 'A-Za-z0-9_')
        record "share_${key}_size_gb" "$size_gb"
        record "share_${key}_files"   "$file_count"
    done
done

# ---- RAID ----
section "RAID arrays (mdstat)"
if [ -f /proc/mdstat ]; then
    cat /proc/mdstat
    md_count=$(grep -c '^md' /proc/mdstat 2>/dev/null || echo 0)
    record md_devices "$md_count"
    # Per-array assembly (which physical drives back which md device)
    for md in $(grep '^md' /proc/mdstat | awk '{print $1}'); do
        members=$(grep "^$md" /proc/mdstat | sed -E 's/.*: active raid[0-9]+ //' \
            | awk '{$1=$1}1')
        record "md_${md}_members" "$members"
    done
else
    echo "(no /proc/mdstat)"
    record md_devices "0"
fi

# ---- Drive details (HDD vs SSD, model, size) ----
section "Physical drives"
for dev in $(ls /sys/block 2>/dev/null | grep -E '^(sd|hd|nvme|mmc)' ); do
    rota=$(cat /sys/block/$dev/queue/rotational 2>/dev/null)
    type=$([ "$rota" = "0" ] && echo "SSD" || echo "HDD")
    size_b=$(cat /sys/block/$dev/size 2>/dev/null)
    [ -z "$size_b" ] && continue
    size_gb=$(awk -v s="$size_b" 'BEGIN { printf "%.0f", s*512/1024/1024/1024 }')
    model=$(cat /sys/block/$dev/device/model 2>/dev/null | tr -s ' ' | sed 's/ *$//')
    serial=$(cat /sys/block/$dev/device/serial 2>/dev/null | tr -s ' ' | sed 's/ *$//')
    printf '  /dev/%-6s %3s  size=%6s GB  model=%-30s\n' "$dev" "$type" "$size_gb" "${model:-?}"
    record "drive_${dev}_type"   "$type"
    record "drive_${dev}_size_gb" "$size_gb"
    record "drive_${dev}_model"   "${model:-unknown}"
    record "drive_${dev}_serial"  "${serial:-unknown}"
done

# ---- SMART status (drive health) ----
if command -v smartctl >/dev/null 2>&1; then
    section "SMART health"
    for dev in /dev/sd? /dev/nvme?n? ; do
        [ -e "$dev" ] || continue
        # -H = quick health summary, fast
        out=$(smartctl -H "$dev" 2>/dev/null | tail -5)
        result=$(echo "$out" | awk -F': *' '/result|status/ {print $NF}' | head -1)
        printf '  %-12s %s\n' "$dev" "${result:-unknown}"
        devname=$(basename "$dev")
        record "smart_${devname}" "${result:-unknown}"
    done
fi

# ---- CPU + RAM + load ----
section "Compute + memory"
if [ -f /proc/cpuinfo ]; then
    cores=$(grep -c '^processor' /proc/cpuinfo)
    cpu_model=$(awk -F': ' '/model name/ {print $2; exit}' /proc/cpuinfo)
    echo "CPU              : $cpu_model ($cores cores)"
    record cpu_cores "$cores"
    record cpu_model "$cpu_model"
fi
if [ -f /proc/meminfo ]; then
    mem_total_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
    mem_avail_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
    cached_kb=$(awk '/^Cached:/ {print $2; exit}' /proc/meminfo)
    swap_total_kb=$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)
    swap_free_kb=$(awk '/^SwapFree:/ {print $2}' /proc/meminfo)
    gb() { awk -v k="$1" 'BEGIN { printf "%.1f", k/1024/1024 }'; }
    echo "RAM total        : $(gb "$mem_total_kb") GB"
    echo "RAM available    : $(gb "$mem_avail_kb") GB"
    echo "Page cache       : $(gb "$cached_kb") GB"
    echo "Swap free / total: $(gb "$swap_free_kb") / $(gb "$swap_total_kb") GB"
    record mem_total_gb     "$(gb "$mem_total_kb")"
    record mem_available_gb "$(gb "$mem_avail_kb")"
    record swap_total_gb    "$(gb "$swap_total_kb")"
fi
if [ -f /proc/loadavg ]; then
    load=$(awk '{print $1, $2, $3}' /proc/loadavg)
    echo "Load avg (1/5/15): $load"
    record loadavg "$load"
fi
if [ -f /proc/uptime ]; then
    up=$(awk '{print int($1/86400)" days, "int(($1%86400)/3600)"h"int(($1%3600)/60)"m"}' /proc/uptime)
    echo "Uptime           : $up"
    record uptime "$up"
fi

# ---- Network interfaces ----
section "Network interfaces"
if command -v ethtool >/dev/null 2>&1; then
    for iface in $(ls /sys/class/net 2>/dev/null | grep -v -E '^(lo|sit|tun|tap|veth|docker|br-|virbr)'); do
        speed=$(ethtool "$iface" 2>/dev/null | awk -F': ' '/Speed/ {print $2}')
        link=$(ethtool "$iface" 2>/dev/null | awk -F': ' '/Link detected/ {print $2}')
        mtu=$(cat /sys/class/net/$iface/mtu 2>/dev/null)
        printf '  %-10s speed=%-12s link=%-4s  mtu=%s\n' \
            "$iface" "${speed:-?}" "${link:-?}" "${mtu:-?}"
        record "iface_${iface}_speed" "${speed:-unknown}"
        record "iface_${iface}_link"  "${link:-unknown}"
        record "iface_${iface}_mtu"   "${mtu:-unknown}"
    done
else
    echo "(ethtool not installed)"
    ip link 2>/dev/null || cat /proc/net/dev
fi

# ---- Existing pipeline directories ----
section "Pipeline directories on this NAS"
found_any=0
# Check each volume + every shared folder inside it for our 7 expected dirs.
for vol in $(echo "${volumes_csv%,}" | tr ',' '\n'); do
    [ -d "$vol" ] || continue
    for parent in "$vol" "$vol"/*; do
        [ -d "$parent" ] || continue
        for sub in eingang_doppel eingang_einzel work_doppel work_einzel \
                   output_doppel output_einzel logs ; do
            path="$parent/$sub"
            if [ -d "$path" ]; then
                size_kb=$(du -sk "$path" 2>/dev/null | awk '{print $1}')
                size_gb=$(awk -v k="${size_kb:-0}" 'BEGIN { printf "%.1f", k/1024/1024 }')
                count=$(find "$path" -type f 2>/dev/null | wc -l | tr -d ' ')
                printf '  %-55s %7s GB  %6s files\n' "$path" "$size_gb" "$count"
                found_any=1
            fi
        done
    done
done
if [ "$found_any" = "0" ]; then
    echo "  (no pipeline directories found yet - first deployment)"
fi
record pipeline_dirs_present "$found_any"

# ---- Docker / container status ----
section "Docker / containers"
if command -v docker >/dev/null 2>&1; then
    if docker ps --format '{{.Names}} {{.Status}} {{.Image}}' 2>/dev/null | head -20; then
        running_count=$(docker ps -q 2>/dev/null | wc -l | tr -d ' ')
        echo
        echo "Running containers: $running_count"
        record docker_running "$running_count"
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^video-pipeline$'; then
            record video_pipeline_running yes
        else
            record video_pipeline_running no
        fi
    else
        echo "(docker CLI present but daemon not reachable)"
        record docker_running 0
    fi
else
    echo "(docker CLI not in PATH - is Container Manager installed?)"
    record docker_available no
fi

# ---- Recent disk-related kernel messages ----
section "Recent disk/RAID kernel events (last 15 lines)"
if dmesg 2>/dev/null | head -1 >/dev/null; then
    dmesg 2>/dev/null | grep -iE 'error|fail|md/raid|reset|hang|i/o error|EXT4-fs|btrfs.*error' \
        | tail -15 || true
else
    echo "  (dmesg requires root or is restricted)"
fi

# ---- Disk space sanity (specific to WORK_DIR) ----
section "Disk-space pre-check"
need_mb=$((SIZE_GB * 1024 * 12))   # ~12x size to safely run all tests
free_kb=$(df -P "$WORK_DIR" | awk 'NR==2 {print $4}')
free_mb=$((free_kb / 1024))
echo "Free in $WORK_DIR : ${free_mb} MB"
echo "Need (~12x SIZE)  : ${need_mb} MB"
if [ "$free_mb" -lt "$need_mb" ]; then
    echo "ERROR: not enough free space - reduce SIZE_GB and retry" >&2
    exit 2
fi
record free_mb_before "$free_mb"

mkdir -p "$BENCH_DIR"

# ---------------------------------------------------------------------------
# 1. Sequential write
# ---------------------------------------------------------------------------

section "Test 1: sequential write (single stream, ${SIZE_GB} GB)"
run_dd "$BENCH_DIR/seq_write.bin" $((SIZE_GB * 1024))
echo "  wrote ${BYTES} bytes in ${TIME_S}s -> ${MBPS} MB/s"
record seq_write_mbps "$MBPS"
record seq_write_seconds "$TIME_S"

# ---------------------------------------------------------------------------
# 2. Sequential read
# ---------------------------------------------------------------------------

section "Test 2: sequential read (single stream, after drop_caches)"
run_dd_read "$BENCH_DIR/seq_write.bin"
echo "  read ${BYTES} bytes in ${TIME_S}s -> ${MBPS} MB/s"
record seq_read_mbps "$MBPS"
record seq_read_seconds "$TIME_S"

# ---------------------------------------------------------------------------
# 3. Parallel write scaling
# ---------------------------------------------------------------------------

section "Test 3: parallel write scaling (N=$PARALLEL_LEVELS)"

for n in $PARALLEL_LEVELS; do
    log "  N=$n streams, ${SIZE_GB} GB each ..."
    # Spawn N parallel dd writes, time the wall-clock.
    pids=""
    t0=$(date +%s.%N)
    for i in $(seq 1 "$n"); do
        dd if=/dev/zero of="$BENCH_DIR/par_${n}_${i}.bin" \
            bs=1M count=$((SIZE_GB * 1024)) conv=fdatasync 2>/dev/null &
        pids="$pids $!"
    done
    for p in $pids; do wait "$p"; done
    t1=$(date +%s.%N)

    total_bytes=$((n * SIZE_GB * 1024 * 1024 * 1024))
    elapsed=$(awk -v a="$t1" -v b="$t0" 'BEGIN { printf "%.3f", a - b }')
    aggregate=$(awk -v b="$total_bytes" -v t="$elapsed" \
        'BEGIN { printf "%.1f", b / 1024 / 1024 / t }')
    per_stream=$(awk -v b="$total_bytes" -v t="$elapsed" -v n="$n" \
        'BEGIN { printf "%.1f", b / 1024 / 1024 / t / n }')

    echo "  N=$n  elapsed=${elapsed}s  aggregate=${aggregate} MB/s  per-stream=${per_stream} MB/s"
    record "par_n${n}_elapsed_s"       "$elapsed"
    record "par_n${n}_aggregate_mbps"  "$aggregate"
    record "par_n${n}_per_stream_mbps" "$per_stream"

    # Free space again so the next level has room.
    rm -f "$BENCH_DIR"/par_${n}_*.bin
done

# ---------------------------------------------------------------------------
# 4. Concurrent read+write (simulates ffmpeg)
# ---------------------------------------------------------------------------

section "Test 4: concurrent read+write on same volume"
log "  start: 1 reader + 1 writer in parallel, ${SIZE_GB} GB each"
drop_caches >/dev/null || true

t0=$(date +%s.%N)
dd if="$BENCH_DIR/seq_write.bin" of=/dev/null bs=1M 2>/dev/null &
RPID=$!
dd if=/dev/zero of="$BENCH_DIR/concurrent_write.bin" \
    bs=1M count=$((SIZE_GB * 1024)) conv=fdatasync 2>/dev/null &
WPID=$!
wait "$RPID" "$WPID"
t1=$(date +%s.%N)

total_bytes=$((2 * SIZE_GB * 1024 * 1024 * 1024))
elapsed=$(awk -v a="$t1" -v b="$t0" 'BEGIN { printf "%.3f", a - b }')
mbps=$(awk -v b="$total_bytes" -v t="$elapsed" \
    'BEGIN { printf "%.1f", b / 1024 / 1024 / t }')
echo "  elapsed=${elapsed}s  combined throughput=${mbps} MB/s"
record concurrent_rw_elapsed_s "$elapsed"
record concurrent_rw_mbps      "$mbps"

# ---------------------------------------------------------------------------
# 5. Recommendation heuristic
# ---------------------------------------------------------------------------

section "Recommendation"

# Pull aggregate values back out.
grep_v() { awk -F= -v k="$1" '$1==k {print $2}' "$SUMMARY_FILE"; }

n1=$(grep_v par_n1_aggregate_mbps)
n2=$(grep_v par_n2_aggregate_mbps)
n4=$(grep_v par_n4_aggregate_mbps)
n8=$(grep_v par_n8_aggregate_mbps)

# Pick the N where throughput stops improving by >10 %.
recommend_workers() {
    local prev="$n1" best=1
    for level in 2 4 8; do
        local cur
        case "$level" in
            2) cur=$n2 ;;
            4) cur=$n4 ;;
            8) cur=$n8 ;;
        esac
        if [ -z "$cur" ] || [ -z "$prev" ]; then continue; fi
        # If cur < prev * 1.10, no benefit going higher -> stop.
        cmp=$(awk -v c="$cur" -v p="$prev" 'BEGIN { print (c >= p * 1.10) ? 1 : 0 }')
        if [ "$cmp" = "1" ]; then
            best=$level
            prev=$cur
        else
            break
        fi
    done
    echo "$best"
}
recommended_workers=$(recommend_workers)
record recommended_workers "$recommended_workers"

echo "Based on the parallel-write scaling, set in config_*.json:"
echo
echo "  \"ffmpeg\": {"
echo "    \"max_workers\": $recommended_workers,"
echo "    \"max_files_per_folder\": 24"
echo "  }"
echo
echo "Aggregate throughput at each N (MB/s):"
printf '  N=1:%6s | N=2:%6s | N=4:%6s | N=8:%6s\n' "$n1" "$n2" "$n4" "$n8"
echo

# Volume-split hint
if [ "$(echo "${volumes_csv%,}" | tr ',' '\n' | grep -c .)" -lt 2 ]; then
    echo "Hint: only one /volume detected. Splitting work + output across"
    echo "      two volumes typically gives a 2-3x ffmpeg speedup."
    record hint_split_volumes yes
else
    echo "Multiple volumes detected - re-run this script against /volume2"
    echo "to see whether a volume split is worth it."
    record hint_split_volumes no
fi

# ---------------------------------------------------------------------------
# 6. Machine-readable summary
# ---------------------------------------------------------------------------

emit_json() {
    printf '{\n'
    local first=1
    while IFS='=' read -r key value; do
        if printf '%s' "$value" | grep -Eq '^[0-9]+(\.[0-9]+)?$'; then
            quoted="$value"
        else
            esc=$(printf '%s' "$value" | sed 's/\\/\\\\/g; s/"/\\"/g')
            quoted="\"$esc\""
        fi
        if [ "$first" = "1" ]; then first=0; else printf ',\n'; fi
        printf '  "%s": %s' "$key" "$quoted"
    done < "$SUMMARY_FILE"
    printf '\n}\n'
}

section "Machine-readable summary (copy block to the chat)"
echo "BEGIN_JSON"
emit_json
echo "END_JSON"

# Also save to a file in WORK_DIR so beginners can grab it via File
# Station / SMB without copy-pasting from a terminal.
JSON_FILE="$WORK_DIR/bench_result_$(date +%Y%m%d-%H%M%S).json"
if emit_json > "$JSON_FILE" 2>/dev/null; then
    echo
    echo "JSON summary also saved to: $JSON_FILE"
    echo "  (open it with File Station or copy via SMB and send it to the chat)"
fi

rm -f "$SUMMARY_FILE"
exit 0
