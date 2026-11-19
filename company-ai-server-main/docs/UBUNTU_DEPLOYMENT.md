# Ubuntu (Proxmox VM) deployment

Layout: **llama.cpp (GPU) on the host via systemd** · **PostgreSQL + API + Caddy in Docker Compose**.
Nothing in this file has been run on a real Ubuntu/GPU machine yet: treat the first install as the test, and tick the checklist at the end.

## 0. Proxmox / VM prerequisites (your senior)
- GPU passthrough to the VM (one VM owns the GPU; GeForce cards cannot be shared between VMs). Inside the VM `nvidia-smi` must show the card.
- Check the driver/CUDA you install still supports the GPU's compute capability (GTX 1070 = 6.1, supported by CUDA 12.x). Newer CUDA releases may drop Pascal.
- VM: 8+ vCPU, 32 GB RAM, SSD (Postgres + models), Ubuntu 22.04/24.04. Snapshots are not a backup: use `pg_backup.sh` too.

## 1. Packages
```bash
sudo apt update && sudo apt install -y git build-essential cmake docker.io docker-compose-v2 ufw
# NVIDIA driver + CUDA toolkit (per NVIDIA's Ubuntu instructions), then verify:
nvidia-smi
```
## 2. Code, models, user
```bash
sudo useradd -r -m -d /opt/company-ai companyai && sudo usermod -aG docker $USER
sudo git clone <your-private-repo> /opt/company-ai && sudo chown -R companyai: /opt/company-ai
sudo -u companyai mkdir -p /opt/company-ai/models      # copy the two .gguf files here
```
## 3. Build llama.cpp (CUDA) and start the model services
```bash
sudo git clone https://github.com/ggml-org/llama.cpp /opt/llama.cpp && cd /opt/llama.cpp
sudo cmake -B build -DGGML_CUDA=ON && sudo cmake --build build --config Release -j
sudo cp /opt/company-ai/deploy/ubuntu/companyai-*.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now companyai-chat companyai-embed
curl http://172.17.0.1:8081/health          # needs the docker0 bridge (172.17.0.1); `ip addr show docker0`
```
The units bind to the docker0 address so containers can reach the models but the LAN cannot.

## 4. Configure and start the stack
```bash
cd /opt/company-ai && cp .env.production.example .env && nano .env
#   POSTGRES_PASSWORD, METRICS_TOKEN, SITE_ADDRESS, TLS_MODE
#   LLAMA_BASE_URL=http://host.docker.internal:8081   RAG_EMBED_BASE_URL=http://host.docker.internal:8082
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && python scripts/preflight.py   # no [ERROR]
docker compose up -d --build
docker compose exec api python scripts/create_company.py acme "Acme Surgical"
docker compose exec api python scripts/create_user.py root --role admin
curl -k https://localhost/health
```
## 5. Firewall, backups, secrets
```bash
sudo SSH_FROM=<your-admin-subnet> ./deploy/ubuntu/ufw.sh
./deploy/ubuntu/pg_backup.sh && ./deploy/ubuntu/pg_restore_test.sh     # must print RESTORE TEST PASS
( crontab -l 2>/dev/null; echo "0 2 * * * cd /opt/company-ai && ./deploy/ubuntu/pg_backup.sh >> logs/backup.log 2>&1" ) | crontab -
python scripts/check_secrets.py --all
```
Copy `backups/` to another machine and encrypt it (contains documents and password hashes).

## 6. Adding GPUs later
- **More VRAM / one bigger GPU**: change the model path in `companyai-chat.service`, raise `-c` and `-np` only if VRAM allows, set `INFERENCE_MAX_CONCURRENT` = `-np`, re-run `scripts/load_test.py`.
- **Second GPU**: run a second llama-server (`CUDA_VISIBLE_DEVICES=1`, port 9002) and put `deploy/ubuntu/nginx-llama-upstream.conf` in front; no application code change. Set `INFERENCE_MAX_CONCURRENT` to the total slots.
- **New-generation GPUs (compute capability 7.0+)**: vLLM becomes an option (`INFERENCE_PROVIDER=vllm`, not tested here; Pascal cards cannot run vLLM).
- **Changing the embedding model** changes vector dimensions: set `RAG_EMBED_DIM`, `TRUNCATE rag_chunks` and re-ingest all documents.

## Checklist
- [ ] `nvidia-smi` in the VM; `systemctl status companyai-chat companyai-embed` active; `sudo reboot` → both come back
- [ ] `docker compose ps` healthy; `/ready` ok; login works over HTTPS from another machine
- [ ] `ufw.sh` applied; from another machine 5432/8000/8081/8082 closed
- [ ] restore test PASS; `scripts/load_test.py` numbers recorded (see docs/ARCHITECTURE_REVIEW.md)
