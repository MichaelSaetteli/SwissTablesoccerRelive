# STS-Upload Server Contract — How to test (Slice 1)

This is the **server-side** of the operator upload tool (Issue #15).
A polished cross-platform GUI is the next slice; for now you can drive
the contract end-to-end via the included demo CLI or via plain `curl`.

What this slice gives you:

* Per-card upload state machine (uploading -> verified -> released, with
  failed/cancelled/interrupted recovery paths).
* HTTP endpoints under `/api/upload/*` for the future client tool.
* Atomic handoff: the watcher only sees a folder in `eingang_<disc>/ETxx/`
  AFTER manifest verification + the release step succeed.
* Auto-release option (per upload) for operators who do not want the
  manual checkpoint.
* `prepare_card.py` admin CLI to label SD cards with `.sts-card.json`.
* `sts_upload_demo.py` demo client that exercises the full flow.

What this slice does NOT give you yet:

* No GUI, no installer, no card auto-detection, no hardware-eject
  monitoring. Those are slice 2.

---

## Step 1 — Admin: prepare one SD card

On any machine with this repo checked out:

```bash
python -m scripts.prepare_card \
    --card /path/to/sdcard/root \
    --tournament-id 42 \
    --tournament-name "Seetal 2026 STS2" \
    --discipline Einzel \
    --table ET01
```

This writes `.sts-card.json` to the card root. The marker is the source
of truth for which tournament / discipline / table the card belongs to.

## Step 2 — Operator: run the demo CLI client

From a machine that can reach the NAS:

```bash
pip install requests
python -m scripts.sts_upload_demo \
    --card /path/to/sdcard/root \
    --server http://192.168.1.159:8080 \
    --username admin \
    --password "<your-web-password>"
```

Add `--auto-release` if you want the upload to also release atomically
into the watcher's `eingang_<disc>/ETxx/` without a separate operator
release step.

What you should see:

1. Marker is read and reported (table / discipline / tournament).
2. Files are walked and counted.
3. Session login.
4. `POST /api/upload/start` -> server reserves a staging dir.
5. One `POST /api/upload/<card_id>/chunk` per file.
6. `POST /api/upload/<card_id>/finish` -> manifest verification.
7. If `--auto-release`: state goes straight to `released`.
   Otherwise: `POST /api/upload/release` for the explicit release.

After the release the folder appears in `eingang_<disc>/ETxx/`, the
watcher quiescence timer fires, and the existing pipeline (move /
organize / rename / merge) runs as usual.

## Step 3 — Probe the contract with curl (optional)

If you want to test without the demo client:

```bash
# login
curl -c cookies.txt -X POST \
    -d "username=admin&password=<pw>" \
    http://192.168.1.159:8080/login

# active tournament + expected card counts
curl -b cookies.txt \
    http://192.168.1.159:8080/api/upload/active-tournament

# start one upload session
curl -b cookies.txt -X POST \
    -H "Content-Type: application/json" \
    -d '{"tournament_id": 42, "discipline": "Einzel",
         "table_name": "ET01", "expected_files": 14,
         "expected_bytes": 1932735283}' \
    http://192.168.1.159:8080/api/upload/start

# returns {"id": N, ...}; use N below
curl -b cookies.txt -X POST \
    -F "relative_name=video_001.mp4" \
    -F "file=@/path/to/video_001.mp4" \
    http://192.168.1.159:8080/api/upload/N/chunk

# ...repeat for every file, then:
curl -b cookies.txt -X POST \
    http://192.168.1.159:8080/api/upload/N/finish

# explicit release
curl -b cookies.txt -X POST \
    -H "Content-Type: application/json" \
    -d '{"card_ids": [N]}' \
    http://192.168.1.159:8080/api/upload/release
```

## Endpoint summary

| Method | Path | Purpose |
|---|---|---|
| GET    | `/api/upload/active-tournament`     | Tournament discovery + expected card counts |
| POST   | `/api/upload/start`                 | Reserve staging dir + DB row |
| POST   | `/api/upload/<card_id>/chunk`       | Multipart upload of one file (form: `relative_name`, `file`) |
| POST   | `/api/upload/<card_id>/finish`      | Manifest verification; -> verified or failed |
| POST   | `/api/upload/release`               | Atomic rename for one or many `card_ids` |
| POST   | `/api/upload/<card_id>/cancel`      | Wipe staging + mark cancelled |
| GET    | `/api/upload/status`                | List cards (optional `?discipline=`, `?tournament_id=`) |

## State machine

```
uploading ─→ verified  ─→ released   (happy path)
    │            │           ▲
    │            │           │ (release, atomic rename)
    │            │
    │            └─→ failed  (manifest mismatch or release IO error)
    │                  │
    │                  └─→ uploading  (operator retries)
    │
    └─→ interrupted ─→ uploading  (resume after card pull / network drop)
    └─→ cancelled                 (terminal)
```

`released` is terminal: the folder is now the watcher's responsibility,
not ours.

## Volume layout (INV-1)

* `/volume1/SDD/staging_<disc>/<card_uuid>/`  — server-managed, auto-created
* `/volume1/SDD/eingang_<disc>/ETxx/`         — atomic handoff target
* Staging root is derived from `eingang` (sibling on the same volume) so
  there is no separate config knob that could drift to HDD. See
  `docs/INVARIANTS.md` INV-1.

## What is testable today

* Full lifecycle via the demo CLI against a real card.
* Auto-release on/off.
* Failure handling: corrupt a chunk, manifest mismatch surfaces.
* Concurrent uploads (different card_uuids): two demo clients in
  parallel against the same tournament.
* Cancel + retry.

## What is next (slice 2)

* PySide6 GUI matching the wireframe in Issue #15.
* SD card auto-detection (mount listing, marker scan).
* Hardware-eject monitoring (mount/unmount events -> interrupted state).
* Resume-after-tool-restart (local state file).
* Cross-platform .exe / installer.
* Operator + admin docs.
