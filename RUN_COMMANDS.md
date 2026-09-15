# mn-data-prepare images: how to run them

This folder has the three image tarballs — `docker load` them on each machine
(no internet/registry credentials needed, which matters since these are
also published privately on GHCR and copying by USB is meant to avoid that
dependency entirely):

| File | Image | Size |
|---|---|---|
| `coordinator.tar` | `mn-dataprep-coordinator:latest` | 154MB |
| `worker.tar` | `mn-dataprep-worker:latest` | 369MB |
| `ollama.tar` | `mn-dataprep-ollama:latest` | 3.72GB (model weights download separately on first container start, not baked in) |

(Also on `ghcr.io/neith-san/mn-dataprep-{coordinator,worker,ollama}:latest` if
you ever want to `docker pull` instead — those packages are private, so
that path needs `docker login ghcr.io` with a `read:packages` token.)

No Docker Swarm needed — plain `docker run` works fine, including `--gpus all` for
the worker's GPU access (that's the whole point of avoiding Swarm here: Swarm's
GPU generic-resource scheduling doesn't work reliably under Docker Desktop, but
plain `docker run --gpus all` does).

## Manager (172.16.153.161 — already done, images loaded and confirmed present)

```
docker load -i E:\undesnii\images\coordinator.tar

docker run -d --name coordinator --restart=always -p 8000:8000 ^
  -v E:\undesnii\data:/data:ro ^
  -v E:\undesnii\undesnii:/data_prepare ^
  -e PIPELINE_CONFIG=/app/config/pipeline.yaml ^
  mn-dataprep-coordinator:latest
```

`/data` is read-only here on purpose -- that's where `scripts/fetch_corpus.sh` /
manual file-copying write to, from the host side, not through this container.
`/data_prepare` is read-write (state + output land there).

Check it's up: `curl http://127.0.0.1:8000/healthz`

## Each worker PC

Copy this whole `images` folder over (USB is fine), then:

```
docker load -i ollama.tar
docker load -i worker.tar

docker network create genai-net

docker run -d --name ollama --restart=always --gpus all --network genai-net -p 11434:11434 ^
  -v ollama_cache:/root/.ollama ^
  -e OLLAMA_HOST=0.0.0.0:11434 ^
  mn-dataprep-ollama:latest

docker run -d --name worker --restart=always --network genai-net ^
  -e COORDINATOR_URL=http://172.16.153.161:8000 ^
  -e OLLAMA_HOST=http://ollama:11434 ^
  -v <path-to-shared-data>:/data:ro ^
  -v <path-to-shared-data_prepare>:/data_prepare ^
  mn-dataprep-worker:latest
```

**Not resolved yet, blocks real work:** `<path-to-shared-data>` /
`<path-to-shared-data_prepare>` must point at the same content as the
manager's `E:\undesnii\data` and `E:\undesnii\undesnii`. Workers will start
fine without this but every job will fail (can't read corpus chunks, can't
write output). This needs a containerized NFS server on the manager
(exports those two folders over the network) -- ask your Claude session to
start it once you're ready for workers to actually do real work.

## Verify a worker is leasing work

```
curl http://172.16.153.161:8000/status
```
Counts should show items moving from `pending` -> `leased` -> `done` once
workers are running and the shared data mount is in place.

## Load the corpus once the coordinator is up

Run on the manager host (not through a container):
```
CORPUS_API_URL=http://127.0.0.1:8420 CORPUS_API_TOKEN=<token> bash scripts/fetch_corpus.sh
make scan
```
(needs the SSH reverse tunnel from the HPC corpus host to be up, and a bash
shell -- WSL/Ubuntu, or Git Bash works for this specific script since it's
pure Python/venv, no apt-get involved)

## Collecting the dataset later

```
make merge   # dedups + writes hf_dataset/data/{train,validation,test}-*.parquet
make push    # optional, needs HF_TOKEN, pushes to the Hub
```
