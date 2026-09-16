# mn-data-prepare images: how to run them

## Get the images (pick one)

**Option A — download script (recommended, no USB needed):** the three
images are published publicly on GHCR. On each machine, just run:

```powershell
powershell -ExecutionPolicy Bypass -File download_images.ps1
```

That's it — no `docker login`, no token, no file transfer. Just share
`RUN_COMMANDS.md` and `download_images.ps1` (this folder, minus the
tarballs) with whoever's setting up a machine, and they're self-sufficient.

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
