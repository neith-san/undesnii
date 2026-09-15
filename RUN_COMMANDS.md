# mn-data-prepare images: how to run them

Published on GitHub Container Registry — pull instead of loading a tarball
(tarballs are also still in this folder if you'd rather use those):

| Image | Size | Pull |
|---|---|---|
| `mn-dataprep-coordinator` | 154MB | `docker pull ghcr.io/neith-san/mn-dataprep-coordinator:latest` |
| `mn-dataprep-worker` | 369MB | `docker pull ghcr.io/neith-san/mn-dataprep-worker:latest` |
| `mn-dataprep-ollama` | 3.72GB | `docker pull ghcr.io/neith-san/mn-dataprep-ollama:latest` (model weights download separately on first container start, not baked in) |

**These packages are private by default on GHCR.** Before pulling on a worker
PC, either make them public (GitHub -> your profile -> Packages -> each
package -> Package settings -> Change visibility), or `docker login ghcr.io`
with a token that has `read:packages` scope on every machine that needs to
pull.

No Docker Swarm needed — plain `docker run` works fine, including `--gpus all` for
the worker's GPU access (that's the whole point of avoiding Swarm here: Swarm's
GPU generic-resource scheduling doesn't work reliably under Docker Desktop, but
plain `docker run --gpus all` does).

## Manager (this machine, 172.16.153.161)

```
docker pull ghcr.io/neith-san/mn-dataprep-coordinator:latest
docker tag ghcr.io/neith-san/mn-dataprep-coordinator:latest mn-dataprep-coordinator:latest

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

```
docker pull ghcr.io/neith-san/mn-dataprep-ollama:latest
docker pull ghcr.io/neith-san/mn-dataprep-worker:latest
docker tag ghcr.io/neith-san/mn-dataprep-ollama:latest mn-dataprep-ollama:latest
docker tag ghcr.io/neith-san/mn-dataprep-worker:latest mn-dataprep-worker:latest

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

`<path-to-shared-data>` / `<path-to-shared-data_prepare>` must point at the same
content as the manager's `E:\undesnii\data` and `E:\undesnii\undesnii` --
shared storage across all machines is not wired up yet (was going to be an
NFS-in-a-container on the manager, still pending confirmation since it
publishes port 2049). Without this, workers can't read corpus chunks or write
output. Resolve this before starting workers for real.

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
