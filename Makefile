SHELL := /bin/bash

.PHONY: setup-manager setup-worker label-workers deploy deploy-no-build status scan merge push logs-worker logs-coordinator

setup-manager:   ## run once on the manager box
	bash scripts/setup_manager.sh

setup-worker:    ## run once on EACH gpu worker box (needs MANAGER_IP, JOIN_TOKEN)
	bash scripts/setup_worker.sh

label-workers:   ## run on the manager after all workers have joined
	bash scripts/label_workers.sh

deploy:          ## build+push images and (re)deploy the stack
	bash scripts/deploy_stack.sh

deploy-no-build: ## redeploy using already-pushed images
	bash scripts/deploy_stack.sh --no-build

status:          ## service placement + job-queue counts
	bash scripts/status.sh

scan:            ## rescan ./data for newly copied-in files (also runs on coordinator startup)
	curl -fsS -X POST "http://$${MANAGER_IP}:8000/rescan" | python3 -m json.tool

merge:           ## merge all worker shards into hf_dataset/ (run inside the coordinator container)
	docker exec -it $$(docker ps -q -f name=mn-data-prepare_coordinator) python -m pipeline.dedup_merge

push:            ## push hf_dataset/ to the Hub (needs HF_TOKEN; run inside the coordinator container)
	docker exec -it -e HF_TOKEN=$$HF_TOKEN $$(docker ps -q -f name=mn-data-prepare_coordinator) python -m pipeline.push_to_hub

logs-worker:
	docker service logs -f mn-data-prepare_worker

logs-coordinator:
	docker service logs -f mn-data-prepare_coordinator
