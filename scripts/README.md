# Scripts

Operations helpers that are not part of the runtime image.

## `bench_io.sh` - DS1522+ I/O benchmark

Measures the four numbers that drive `ffmpeg.max_workers` tuning and the
"is a hardware change worth it" decision.

### What it measures

| Test | Purpose |
|---|---|
| Sequential write (1 stream) | baseline single-stream throughput |
| Sequential read (1 stream)  | baseline + drop_caches verification |
| Parallel writes N=1,2,4,8   | scaling curve - tells us where adding workers stops helping |
| Concurrent read+write       | simulates real ffmpeg load (read inputs, write output) |

Plus an inventory snapshot (volumes, RAID, network, RAM) so we can see
whether you have one volume or several, which RAID type, etc.

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
