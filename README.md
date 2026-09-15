# data_prepare — distributed multimodal dataset generation for Gemma 4 E4B (mn)

Turns raw text/image/audio you drop into `../data/` into a bilingual
(Mongolian + English) instruction/thinking dataset, in the same column
layout as [`toorgil/mongolian-llm-benchmark`](https://huggingface.co/datasets/toorgil/mongolian-llm-benchmark),
using a Docker Swarm cluster of 1 manager + ~20 dual-RTX-A4000 worker nodes
running [muse-glimmer](https://ollama.com/blog/muse-glimmer) (Meta
Superintelligence Labs' 30B open agentic/vision model, served through
Ollama) as the generation engine.

This complements the existing P0–P8 text-only tokenizer/CPT/SFT pipeline in
the repo root (see [`../PLAN.md`](../PLAN.md)): its output is meant to feed
P8 SFT (and, via the English-paired rows, act as English replay data
addressing the "English PPL must not regress" risk called out for P7 CPT).

## Why these specific choices

- **muse-glimmer, not a random Ollama model** — it's Apache-2.0, 30B with a
  dedicated 1.8B vision encoder (so it can look at your `data/images/`
  directly), 128K context, and — the important part — **controllable
  reasoning strength** (`low`/`medium`/`high`/`xhigh`) surfaced through
  Ollama's `think` API field, which returns the reasoning trace separately
  in `message.thinking`. That maps directly onto the benchmark's `thinking`
  column with no scraping/parsing hacks. It does **not** support audio
  input, so audio goes through a transcription step first (see below).
- **A custom `muse-glimmer-mn` tag, not the bare model** — `ollama/Modelfile.muse-glimmer`
  layers a system prompt on top instructing careful bilingual behavior
  (Mongolian output stays in Mongolian, thinks in the language it's
  answering in, never fabricates facts it isn't sure of) without retraining
  any weights. If you already have your own `muse-glimmer` build with
  different instructions, `bootstrap_muse_glimmer.sh` detects that and
  leaves it alone.
- **Docker Swarm with host networking, not overlay** — a worker MUST talk to
  the Ollama instance on its *own* node (that's the whole point of pairing
  one Ollama process with that node's two A4000s), not whichever replica
  Swarm's overlay VIP happens to route to. Host networking sidesteps that;
  see the comment block at the top of `docker/docker-compose.swarm.yml`.
- **NFS for shared storage** — the simplest thing that actually satisfies
  "workers can share data and prepared data" across ~21 machines without a
  new storage system. Swap it for a real NAS/Ceph/GlusterFS mount at the
  same paths if you have one; nothing else in the stack cares.
- **GPU scheduling via Swarm generic resources** — Swarm's stock
  `deploy.resources.reservations.devices` GPU block (the thing that works in
  plain `docker compose up`) is silently ignored under `docker stack
  deploy`. `scripts/node_gpu_generic_resources.sh` wires up the supported
  workaround (NVIDIA's `swarm-resource` + Docker's `node-generic-resources`)
  so the scheduler genuinely reserves both A4000s per node for Ollama.

## Architecture

```
                         ┌───────────────────────────┐
                         │  manager node              │
                         │  - swarm manager           │
                         │  - local image registry    │
                         │  - NFS server (data, data_prepare)
                         │  - coordinator (job queue) │
                         └─────────────┬─────────────┘
                                       │ overlay-free: fixed MANAGER_IP:8000
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
      ┌───────┴───────┐        ┌───────┴───────┐        ┌───────┴───────┐
      │ worker node 1  │        │ worker node 2  │  ...   │ worker node 20│
      │ 2x RTX A4000   │        │ 2x RTX A4000   │        │ 2x RTX A4000  │
      │ ollama (muse-  │        │ ollama         │        │ ollama        │
      │  glimmer-mn)   │        │                │        │               │
      │ worker process │        │ worker process │        │ worker process│
      │  <-127.0.0.1-> │        │                │        │               │
      └───────┬───────┘        └───────┬───────┘        └───────┬───────┘
              └────────────── NFS mount: /mnt/genai/{data,data_prepare} ─┘
```

Each worker node leases a small batch of work items from the coordinator,
generates rows by calling its own local Ollama, writes them to a shard file
only it owns (`data_prepare/output/<worker_id>/part-*.jsonl`), and reports
completion. Nothing about this requires cross-node coordination beyond the
lease/complete HTTP calls, so it scales roughly linearly with node count.

## Quickstart

```bash
# 1) On the manager:
cd data_prepare
cp .env.example .env        # fill in MANAGER_IP at minimum
MANAGER_IP=10.0.0.10 bash scripts/setup_manager.sh
#   -> prints a `docker swarm join --token ...` command

# 2) On each of the ~20 GPU boxes:
MANAGER_IP=10.0.0.10 JOIN_TOKEN=SWMTKN-... bash scripts/setup_worker.sh

# 3) Back on the manager, once all 20 have joined:
bash scripts/label_workers.sh
bash scripts/deploy_stack.sh

# 4) Copy your source material into ../data/{text,images,audio}/ (see
#    ../data/README.md), then:
bash scripts/status.sh
```

First boot on each GPU node pulls muse-glimmer:30b (~18GB) once into
`/var/lib/genai/ollama` on that node's local disk — expect the first
`docker stack deploy` to take a while across 20 nodes pulling in parallel;
`docker service logs -f mn-data-prepare_ollama` shows pull progress.

## Resuming / adding more data later

Nothing here needs to be told to resume — it's the default behavior:

- `data_prepare/state/manifest.jsonl` is the append-only list of every
  source file + chunk ever discovered (content-hash IDs, so re-scanning
  never duplicates an entry).
- `data_prepare/state/coordinator_state.json` is the coordinator's
  checkpoint of what's pending/leased/done/dead, saved every 30s, every 200
  completions, and on graceful shutdown (SIGTERM) — and reloaded on boot.
  Redeploying the stack, rebooting the manager, or losing a worker mid-item
  (its lease just expires and the item goes back to `pending`) all resume
  cleanly with no manual steps.
- Copying more files into `../data/` at any time and running `make scan`
  (or `POST /rescan` on the coordinator) picks them up without touching
  anything already processed.
- The only way to actually start over is to delete
  `data_prepare/state/manifest.jsonl` and `coordinator_state.json` — do
  that deliberately, never as a side effect.

## Dataset schema

Base columns are identical to `toorgil/mongolian-llm-benchmark`: `id`,
`source`, `category`, `type` (`qa`/`mcq`/`problem_solving`/
`instruction_following`/`code`/`dpo`), `instruction`, `input`, `choices`,
`answer`, `thinking`, `difficulty`, `language`.

Added for this project: `modality` (`text`/`image`/`audio`), `media_path`
(relative path back into `../data/`, for image/audio rows), and `pair_id`
(shared by the Mongolian row and the English row generated from the same
source item — this is the "share English knowledge with Mongolian" part:
every fact/task exists in both languages, at matching difficulty, so the
data doubles as English-retention replay material and as a
Mongolian<->English knowledge bridge).

`python -m pipeline.dedup_merge` (run inside the coordinator container, or
via `make merge`) merges every worker's shards, dedups by `id`, and writes
`hf_dataset/data/{train,validation,test}-00000-of-00001.parquet` plus a
dataset card — loadable with
`datasets.load_dataset("parquet", data_dir="data_prepare/hf_dataset/data")`,
or pushable with `make push` (`HF_TOKEN` required; never runs on its own).

## Tuning

- `config/pipeline.yaml` — task-type mix, difficulty levels, chunking
  sizes, Ollama `think` level (drop to `medium` for throughput over trace
  depth), lease TTL/retries, split sizes. Override any key at deploy time
  with `PIPELINE_<SECTION>__<KEY>=value` env vars (see `pipeline/config.py`).
- `ollama/Modelfile.muse-glimmer` — the bilingual/thinking system prompt.
  Edit it, then redeploy; nodes with an already-built `muse-glimmer-mn`
  won't auto-update (bootstrap is deliberately idempotent) — bump the tag
  name if you need a clean rebuild everywhere, e.g. `muse-glimmer-mn-v2` in
  `.env`'s `OLLAMA_INSTRUCTED_TAG`.
- `config/asr.*` — audio transcription runs on CPU by default so it never
  competes with muse-glimmer for the node's two A4000s; switch
  `asr.device: cuda` only if a node has real VRAM headroom to spare.

## Known limits / things to watch

- Swarm generic-resource GPU scheduling is an older, less-maintained Docker
  feature than Kubernetes device plugins — verify it actually took effect
  per node with `docker node inspect <node> --format '{{ .Description.Resources.GenericResources }}'`
  before assuming all 20 are running.
- The coordinator holds all item state in one process's memory + a single
  JSON checkpoint file. This is fine well past hundreds of thousands of
  items; if a corpus grows into the tens of millions of chunks, swap the
  checkpoint for a small database before it becomes a bottleneck.
- The default NFS export in `docker/nfs/export-data.sh` allows any host
  (`*`) — tighten that to your cluster's subnet before running this beyond
  a trusted lab network.
- muse-glimmer's per-node VRAM footprint (18GB weights + KV cache at
  `num_ctx: 16384`) is sized for two A4000s (32GB) together; don't schedule
  other GPU work on these nodes at the same time.
