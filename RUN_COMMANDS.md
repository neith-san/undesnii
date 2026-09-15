# mn-data-prepare images: how to run them

This folder has the three image tarballs — `docker load` them on each machine
(no internet/registry credentials needed):

| File | Image | Size |
|---|---|---|
| `coordinator.tar` | `mn-dataprep-coordinator:latest` | 154MB |
| `worker.tar` | `mn-dataprep-worker:latest` | 369MB |
| `ollama.tar` | `mn-dataprep-ollama:latest` | 3.72GB (model weights download separately on first container start, not baked in) |

(Also on `ghcr.io/neith-san/mn-dataprep-{coordinator,worker,ollama}:latest` if
you'd rather `docker pull` — those packages are private, needs
`docker login ghcr.io` with a `read:packages` token.)

No Docker Swarm needed — plain `docker run` works fine, including `--gpus all`
for the worker's GPU access.

**Every command below is single-line on purpose.** `^` (cmd.exe) and `` ` ``
(PowerShell) line-continuation characters don't work the same across shells,
and mixing them up silently breaks multi-line commands (seen already on one
worker PC). Single-line avoids the whole problem regardless of which shell
you're in.

## Manager (172.16.153.161 — already done)

Images loaded, and running against two Docker named volumes (`genai-data`,
`genai-data-prepare`) rather than direct Windows-path bind mounts — Windows
drive paths are mounted into Docker Desktop's Linux VM via virtiofs/9p,
which can't be re-exported over NFS, so the volumes are the real shared
storage and `E:\undesnii\data` / `E:\undesnii\undesnii` are no longer what
workers (or the coordinator) actually read from.

```
docker run -d --name coordinator --restart=always -p 8000:8000 -v genai-data:/data:ro -v genai-data-prepare:/data_prepare -e PIPELINE_CONFIG=/app/config/pipeline.yaml mn-dataprep-coordinator:latest
```

Check it's up: `curl http://127.0.0.1:8000/healthz`

## Shared storage (manager -> every worker)

Exports the two Docker volumes above -- `/srv/data` over NFSv4, `/srv/data_prepare`
over NFSv3. **Both are needed on one export, not split across two servers**:
NFSv4 only lets a client reach one `fsid=0` export directly (a second,
independent export isn't reachable the normal way), and a single shared
parent directory doesn't work either (`/srv` itself isn't a real distinct
filesystem, so it can't be exported -- only the two volume-backed
subdirectories under it can). NFSv3 has no such single-root restriction, so
`data_prepare` uses that instead -- which is why this needs the extra
rpcbind/mountd/statd ports (111, 32765, 32767) published alongside 2049:

```
docker run -d --name genai-nfs --restart=always --cap-add SYS_ADMIN -p 2049:2049 -p 2049:2049/udp -p 111:111 -p 111:111/udp -p 32765:32765 -p 32765:32765/udp -p 32767:32767 -p 32767:32767/udp -v genai-data:/srv/data -v genai-data-prepare:/srv/data_prepare -e NFS_EXPORT_0="/srv/data *(ro,fsid=0,no_subtree_check,insecure,no_root_squash)" -e NFS_EXPORT_1="/srv/data_prepare *(rw,fsid=1,no_subtree_check,insecure,no_root_squash)" erichough/nfs-server
```

## Each worker PC

Copy this whole `images` folder over (USB is fine), then — plain `docker`
commands, no `sudo`, no WSL needed (Docker Desktop's daemon mounts the NFS
share itself, inside its own Linux VM). Note **`/data` is NFSv4 but
`/data_prepare` is NFSv3** (`type=nfs`, not `type=nfs4`, plus `nfsvers=3`) --
see the note above for why:

```
docker load -i ollama.tar
docker load -i worker.tar
docker network create genai-net
docker volume create --driver local --opt type=nfs4 --opt o=addr=172.16.153.161,ro --opt device=:/srv/data genai-data
docker volume create --driver local --opt type=nfs --opt o=addr=172.16.153.161,rw,nfsvers=3 --opt device=:/srv/data_prepare genai-data-prepare
docker run -d --name ollama --restart=always --gpus all --network genai-net -p 11434:11434 -v ollama_cache:/root/.ollama -e OLLAMA_HOST=0.0.0.0:11434 mn-dataprep-ollama:latest
docker run -d --name worker --restart=always --network genai-net -e COORDINATOR_URL=http://172.16.153.161:8000 -e OLLAMA_HOST=http://ollama:11434 -v genai-data:/data:ro -v genai-data-prepare:/data_prepare mn-dataprep-worker:latest
```

`docker network create genai-net` will error "already exists" if you're
re-running after a partial failure — harmless, ignore it. If a previous
attempt already created broken `genai-data`/`genai-data-prepare` volumes on
this machine, remove them first: `docker volume rm genai-data genai-data-prepare`.

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

Run inside the coordinator container (has the volume mounted already):
```
docker exec -it coordinator python -m pipeline.dedup_merge
```
Writes `hf_dataset/data/{train,validation,test}-*.parquet` into
`genai-data-prepare`. To pull that out to a real filesystem path for
inspection or pushing to the Hub:
```
docker run --rm -v genai-data-prepare:/d -v <a Windows path>:/out alpine cp -r /d/hf_dataset /out
```
