"""Manual, deliberate step -- never run automatically by anything else in
this pipeline. Pushes hf_dataset/ to the Hugging Face Hub.

Requires HF_TOKEN in the environment (see .env.example). Publishing is an
outward-facing, hard-to-reverse action, so this script only ever runs when
a human explicitly invokes it:

    HF_TOKEN=hf_xxx python -m pipeline.push_to_hub --repo-id toorgil/my-dataset
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from . import config as cfgmod


def main():
    cfg = cfgmod.load()
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-id", default=cfg["huggingface"]["repo_id"])
    ap.add_argument("--private", action="store_true", default=cfg["huggingface"]["private"])
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN is not set -- refusing to push without explicit auth")

    from huggingface_hub import HfApi

    hf_dir = Path(cfg["paths"]["hf_dataset_dir"])
    if not (hf_dir / "data").exists():
        raise SystemExit(f"{hf_dir}/data not found -- run `python -m pipeline.dedup_merge` first")

    api = HfApi(token=token)
    api.create_repo(repo_id=args.repo_id, repo_type="dataset", private=args.private, exist_ok=True)
    api.upload_folder(repo_id=args.repo_id, repo_type="dataset", folder_path=str(hf_dir))
    print(f"pushed {hf_dir} -> https://huggingface.co/datasets/{args.repo_id}")


if __name__ == "__main__":
    main()
