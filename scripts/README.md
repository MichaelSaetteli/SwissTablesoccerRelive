# Scripts

Operations helpers that are not part of the runtime image.

## `bench_io.sh` - DS1522+ I/O benchmark

Measures the four numbers that drive `ffmpeg.max_workers` tuning and the
"is a hardware change worth it" decision.

### What it measures

**I/O benchmarks**

| Test | Purpose |
|---|---|
| Sequential write (1 stream) | baseline single-stream throughput |
| Sequential read (1 stream)  | baseline + drop_caches verification |
| Parallel writes N=1,2,4,8   | scaling curve - tells us where adding workers stops helping |
| Concurrent read+write       | simulates real ffmpeg load (read inputs, write output) |

**System inventory** (no measurements - read-only checks)

| Section | What we learn |
|---|---|
| DSM / Synology model       | DSM version + hardware revision, kernel |
| Volumes detail             | `/volume*` size + free + filesystem (ext4 vs btrfs) + mount opts |
| Shared folders             | top-level dirs per volume with size + file count |
| RAID arrays                | type per md device + which physical drives back it |
| Physical drives            | per-disk: HDD vs SSD, size, model |
| SMART health               | drive-by-drive PASSED/FAILED status |
| Compute + memory           | CPU model + cores, RAM total/available, load avg, uptime |
| Network interfaces         | per-NIC speed (10 GbE check), link state, MTU |
| Pipeline directories       | which `eingang_*`, `work_*`, `output_*`, `logs/` already exist + how full |
| Docker / containers        | running containers, whether `video-pipeline` is up |
| Recent kernel events       | last 15 disk/RAID errors from dmesg (if accessible) |

Together this gives a full picture: where you have free space, whether
your 10 GbE link is actually negotiating 10 Gbit/s, how the volumes are
laid out, whether a previous pipeline run left data behind, etc.

### Usage on the NAS

```bash
ssh admin@<NAS-IP>
sudo -i                                     # for /proc/sys/vm/drop_caches
cd /tmp
curl -O https://raw.githubusercontent.com/MichaelSaetteli/SwissTablesoccerRelive/claude/read-briefing-start-build-2wOz7/scripts/bench_io.sh
chmod +x bench_io.sh

# default: 4 GB per stream, ~3-5 minutes total runtime
./bench_io.sh

# more accurate, ~10 minutes
SIZE_GB=8 ./bench_io.sh

# benchmark a different volume
WORK_DIR=/volume2/video-pipeline ./bench_io.sh
```

The script:

* writes everything under `<WORK_DIR>/.bench_<pid>_<timestamp>/`
* removes that directory on exit (even on Ctrl-C)
* needs ~12x SIZE_GB of free space - it pre-checks and bails if too tight
* drops the page cache between tests when run as root

### Output

Two parts:

1. **Human-readable** progress + a printed recommendation block:
   ```
   "ffmpeg": { "max_workers": 4, "max_files_per_folder": 24 }
   ```

2. **Machine-readable** JSON between `BEGIN_JSON` / `END_JSON` markers
   at the very end. Copy that block back to the chat - I'll use it to:

   * pick the actual `max_workers` value for your setup (not just the
     heuristic the script applies),
   * decide whether splitting `work` and `output` onto two volumes is
     worth doing on your specific hardware,
   * adjust `quiet_seconds` if your network shows pauses,
   * spot anything weird (10 GbE not negotiated, RAID degraded, etc.).

### Safety

* No write outside `WORK_DIR`. No `rm -rf` on user data.
* All test files have an unmistakable `.bench_<id>` prefix.
* Trap on EXIT/INT/TERM ensures cleanup even if you Ctrl-C the run.
* Reading `/dev/zero` and writing to disk costs only the time you let
  it run; safe to abort at any point.
