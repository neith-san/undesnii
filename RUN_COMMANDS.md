# mn-data-prepare images: how to run them

## Get the images (pick one)

**Option A — download script (recommended, no USB needed):** the three
images are published publicly on GHCR. On each machine, just run:

Run from inside the folder containing the script (or give the full path),
in an ordinary cmd or PowerShell prompt:

```powershell
powershell -ExecutionPolicy Bypass -File download_images.ps1
```

That's it — no `docker login`, no token, no file transfer. Just share
`RUN_COMMANDS.md` and `download_images.ps1` (this folder, minus the
tarballs) with whoever's setting up a machine, and they're self-sufficient.
Takes a few minutes (mostly the ~3.7GB ollama image); once it finishes,
continue with the "Manager" or "Each worker PC" commands below.

*(If a `docker pull` in that script fails with "unauthorized"/"denied", the
GHCR packages have gone back to private — fix via GitHub profile → Packages
→ each package → Package settings → Change visibility → Public.)*

**Option B — tarballs (works with zero internet access):** this folder also
has the raw image tarballs — `docker load` them on each machine:

| File | Image | Size |
|---|---|---|
| `coordinator.tar` | `mn-dataprep-coordinator:latest` | 154MB |
| `worker.tar` | `mn-dataprep-worker:latest` | 369MB |
| `ollama.tar` | `mn-dataprep-ollama:latest` | 3.72GB (model weights download separately on first container start, not baked in) |

```
docker load -i coordinator.tar
docker load -i worker.tar
docker load -i ollama.tar
```

Either option lands the same three local image tags
(`mn-dataprep-{coordinator,worker,ollama}:latest`) — everything below is
identical either way.

No Docker Swarm needed — plain `docker run` works fine, including `--gpus all`
for the worker's GPU access.

**No NFS, no shared filesystem at all.** Workers fetch corpus source text
from the coordinator over plain HTTP (`GET /file`) instead of a shared
`/data` mount, and keep their generated output on their own local disk
instead of a shared `/data_prepare` mount — collect each worker's output
folder back to the manager later (USB, scp, whatever), then run the merge
step. This sidesteps the whole NFS/virtiofs/Windows-networking mess
entirely: workers only ever need plain network reachability to the
coordinator's port 8000, nothing else.

**Every command below is single-line on purpose.** `^` (cmd.exe) and `` ` ``
(PowerShell) line-continuation characters don't work the same across shells,
and mixing them up silently breaks multi-line commands. Single-line avoids
the problem regardless of which shell you're in.

## Manager (172.16.153.161 — already done)

```
docker run -d --name coordinator --restart=always -p 8000:8000 -v genai-data:/data:ro -v genai-data-prepare:/data_prepare -e PIPELINE_CONFIG=/app/config/pipeline.yaml mn-dataprep-coordinator:latest
```

Check it's up: `curl http://127.0.0.1:8000/healthz`. `/data` and `/data_prepare`
here are local Docker volumes, used only by the coordinator itself -- nothing
else needs to reach them directly anymore.

## Each worker PC

After getting the images (Option A or B above) — plain `docker` commands,
no `sudo`, no WSL, no volume mounts to a shared path at all (`worker_output`
below is a private local volume, just for this machine's own generated
rows):

```
docker network create genai-net
docker run -d --name ollama --restart=always --gpus all --network genai-net -p 11434:11434 -v ollama_cache:/root/.ollama -e OLLAMA_HOST=0.0.0.0:11434 mn-dataprep-ollama:latest
docker run -d --name worker --restart=always --network genai-net -e COORDINATOR_URL=http://172.16.153.161:8000 -e OLLAMA_HOST=http://ollama:11434 -v worker_output:/data_prepare mn-dataprep-worker:latest
```

`docker network create genai-net` will error "already exists" if you're
re-running after a partial failure — harmless, ignore it.

### The ollama image now self-heals and reports real health

Earlier `ollama.tar` builds had a bootstrap script that gave up permanently
on the first transient `/api/pull`/`/api/create` failure, with no way to
tell from the outside -- this is what caused a run where most nodes had a
missing model and dead-lettered almost their entire share of the corpus
before anyone noticed. The current image fixes this:

- `docker ps` now shows real health for the `ollama` container --
  `(health: starting)` while it's still pulling/building (up to 40 minutes
  for a fresh ~18GB pull), `(unhealthy)` if bootstrap is genuinely stuck,
  `(healthy)` once the model is ready. Check any worker at a glance with
  just `docker ps`, no curl/exec needed.
- If bootstrap fails, it retries with backoff internally, and then keeps
  retrying the whole thing every 30 minutes in the background, forever --
  no manual intervention needed for a transient failure to self-heal.
- The `worker` container now waits for its local model to actually be
  ready (not just for the ollama server to be reachable) before it leases
  **any** work -- so a node stuck on `(unhealthy)` simply sits idle instead
  of dead-lettering corpus items. Its logs show a "waiting for model ...
  not leasing any work until it's ready" heartbeat while this is happening.

**Reloading this image on a worker that already has the 18GB base model
cached is cheap and safe** -- `docker rm -f ollama` does not touch the
`ollama_cache` named volume, so `docker run` with the new image reuses the
already-downloaded weights and only re-runs the fast `/api/create` step,
no re-download.

## Verify a worker is leasing work

```
curl http://172.16.153.161:8000/status
```
Counts should show items moving from `pending` -> `leased` -> `done`.

## Stopping, pausing, and resuming generation

**Pause one worker** (e.g. for GPU maintenance, a student needing the machine, etc.):
```
docker stop worker ollama
```
Safe, no data loss -- any items that worker was leasing expire server-side after
`leasing.lease_ttl_s` (1200s / 20min) and go back to `pending` for another worker to pick up.
Resume with:
```
docker start ollama worker
```
The worker's model-readiness gate means it correctly waits for ollama to report healthy again
before it leases anything -- no manual coordination needed between the two containers.

**Stop the whole fleet**: there's no single stop-everything command (this is plain
`docker run` per machine, not Swarm) -- run `docker stop worker ollama` on each worker, and
`docker stop coordinator` on the manager if you need that down too. **Watch out**: every
container here runs `--restart=always`, which brings containers back after the *Docker
daemon* restarts (e.g. a Windows reboot) even if you'd manually stopped them beforehand --
so a reboot during planned downtime can silently un-pause generation. If you need downtime to
survive a reboot, `docker stop` right before shutdown isn't enough by itself; recreate without
`--restart=always`, or just remember to re-stop after the machine comes back up.

**"Fresh start" (destructive, rare)** -- this is not the same as a normal stop/resume, it
throws away real state:
```
docker volume rm worker_output              # on a worker: discards any generated rows not yet collected
docker volume rm genai-data genai-data-prepare   # on the manager: discards the whole corpus load + job-queue history
```
Only do this after collecting whatever output matters (see "Collecting the dataset later"
below) and only deliberately -- never as a first troubleshooting step for something that
looks stuck. A stuck-looking node almost always just needs `docker logs` and the health-check
guidance above, not a wipe.

## Load the corpus into the shared volume

Run **on the manager** (already done once — 2,939 pending items loaded from
an initial 4,000-row test batch). This runs `fetch_corpus.py` inside a
throwaway container using the coordinator image (already has the right
Python deps), writing straight into the `genai-data` volume — no host path,
no WSL, no separate venv needed:

```
docker run --rm -v genai-data:/data -e PIPELINE_CORPUS__API_URL=http://host.docker.internal:8420 -e CORPUS_API_TOKEN=<token> mn-dataprep-coordinator:latest python -m pipeline.fetch_corpus
```

`host.docker.internal` is Docker Desktop's route from inside a container
back to the Windows host, where the SSH reverse tunnel from the HPC corpus
host lands `127.0.0.1:8420`. Then pick up what it wrote:

```
curl -s -X POST http://127.0.0.1:8000/rescan
```

## Collecting the dataset later

Each worker's generated rows live in its own local `worker_output` volume,
under `output/<worker_id>/part-*.jsonl`. Pull that out to a real path so you
can copy it to the manager:

```
docker run --rm -v worker_output:/d -v <a local path>:/out alpine cp -r /d/output /out
```

Copy each worker's `output/<worker_id>/` folder into the manager's
`genai-data-prepare` volume's `output/` (same trick, reversed -- mount
`genai-data-prepare` and copy in), then, on the manager, run inside the
coordinator container:

```
docker exec -it coordinator python -m pipeline.dedup_merge
```

Writes `hf_dataset/data/{train,validation,test}-*.parquet`. This is safe to
run against a partial or re-copied set of shards -- it dedupes by
content-hash `id`, so copying the same worker's output twice (or collecting
mid-run and again later) never produces duplicate rows.

## Extending to a new dataset (e.g. Wikipedia MN)

**No new worker images needed.** Workers are dataset-agnostic -- they
process whatever text chunk the coordinator hands them via `/lease` +
`/file`, regardless of where it came from. Extending to a new source is
purely a data-ingestion task:

1. Get the new source into plain text or the same
   `{"text": ..., "source": ..., ...}` JSONL shape `fetch_corpus.py`
   produces (e.g. for Wikipedia MN: the `wikimedia/wikipedia` HF dataset's
   `20231101.mn` config, or a WikiExtractor pass over the raw XML dump).
2. Land it in the `genai-data` volume under its own subdirectory (keep
   sources separate for provenance), same throwaway-container-copy trick
   used everywhere else in this doc:
   ```
   docker run --rm -v genai-data:/data -v <host path with source text>:/src alpine cp -r /src/. /data/text/wikipedia/
   ```
3. Tell the coordinator to pick it up -- no restarts needed anywhere:
   ```
   curl -s -X POST http://172.16.153.161:8000/rescan
   ```
   New items appear as `pending` and every already-healthy worker starts
   leasing them automatically.

This only stops being true if a new source needs a genuinely different
**modality** (the pipeline already has image/audio processors defined,
just unused so far) or custom parsing beyond what `read_text_file`/
`chunk_text` already handle -- that would need an actual worker/coordinator
code change, not just new data.

## Updating the manager (coordinator)

Low-risk: **all coordinator state lives in the `genai-data-prepare` Docker
volume** (`coordinator_state.json` checkpoint + `manifest.jsonl`), not in
the container itself. Verified directly this session -- recreating the
coordinator container with a new image preserves done/dead/pending counts
intact. To roll out a new coordinator image:

```
docker pull ghcr.io/neith-san/mn-dataprep-coordinator:latest
docker rm -f coordinator
docker run -d --name coordinator --restart=always -p 8000:8000 -v genai-data:/data:ro -v genai-data-prepare:/data_prepare -e PIPELINE_CONFIG=/app/config/pipeline.yaml ghcr.io/neith-san/mn-dataprep-coordinator:latest
```

Workers don't need to know or care -- they just keep hitting the same
`COORDINATOR_URL`, no changes needed on their end. **One caveat**: if an
update ever changes the `/lease` or `/complete` JSON shape (an API
contract change, not just internal logic), workers would need the
matching new worker image rolled out at the same time -- hasn't happened
yet, but worth checking before assuming a coordinator-only update is safe.

## Project status / background (for picking this back up later)

### Topology
- **HPC box**: hosts the CulturaX Mongolian corpus + a custom Arrow-batch
  HTTP API (`dataset_api/`, port 8420) serving it. Builds/pushes all three
  images to GHCR.
- **Manager** (`172.16.153.161`, Windows + Docker Desktop, user
  `undesnii`): runs the `coordinator` container. Reached from the HPC box
  via a persistent **SSH reverse tunnel** (`ssh -R 8420:127.0.0.1:8420
  undesnii@172.16.153.161`, keypair `~/.ssh/dataset_tunnel_ed25519`,
  script `dataset_api/tunnel.sh` on the HPC box, retry loop) because the
  manager's network blocks *inbound* connections from the HPC box's
  subnet on every port -- the HPC box has to connect *out* instead. The
  manager reaches the HPC box's corpus API at its own `127.0.0.1:8420`
  once that tunnel is up.
- **Workers**: ~20 lab PCs (Windows + Docker Desktop). Distributed
  incrementally so far, not all confirmed healthy yet.

### Why the architecture looks the way it does
- **No NFS/shared filesystem** -- tried first, hit real confirmed
  blockers (Windows paths can't be re-exported over NFS from Docker
  Desktop's VM; NFSv4 only lets a client reach one `fsid=0` export
  directly; the manager's Docker Desktop/WSL2 backend was independently
  unstable). Replaced with the `GET /file` + local-output design described
  above -- workers need zero shared-storage mounts.
- **No Docker Swarm** -- Swarm's GPU scheduling needs a real Docker Engine
  (not Docker Desktop) with `nvidia-ctk --set-as-default` on every single
  worker, which needs working Ubuntu WSL2 everywhere. Plain
  `docker run --gpus all` sidesteps this and is what's actually deployed.
- **GHCR images are public** by choice, specifically so this file +
  `download_images.ps1` is enough to self-serve a new machine.
- **Ollama bootstrap is self-healing** after an incident where 2,768/2,939
  corpus items (94.8%) dead-lettered because most freshly-provisioned
  nodes' bootstrap failed once, silently, permanently, and nothing
  anywhere checked "does the model actually exist" vs. "is the ollama
  server reachable" -- see the section above for the fix.

### Known open issues
- **Manager's WSL2/Ubuntu distro is broken**, suspected domain
  GPO/endpoint-security policy on the `lab317` domain -- not fixable over
  SSH, IT ticket was opened. This is why the deployment avoids needing
  WSL anywhere it can.
- The HPC box's root disk hit 0 bytes free once mid-build for reasons
  unrelated to this project (not Docker, not this repo's scratch files) --
  check `df -h /` there before large builds/pushes if things start failing
  mysteriously.
- Corpus loaded so far is small (one ~4,000-row test batch, 2,939 chunks)
  -- run `fetch_corpus.py` again for a real production-scale batch once
  the worker fleet is confirmed healthy.
- Fleet-scale output collection (copying every worker's `worker_output`
  back to the manager for `dedup_merge.py`) is documented above but not
  yet exercised for real.
