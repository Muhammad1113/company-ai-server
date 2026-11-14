# Deploy (Windows, single machine)

Architecture: browser -> Nginx/Caddy (:80/:443) -> FastAPI (127.0.0.1:8000) -> llama-server (:8081/:8082)

1. **Start sab kuch:** `.\scripts\start_all.ps1`  (pehle `python scripts\create_user.py admin --role admin`)
2. **Proxy (ek chuno):**
   - *Caddy (aasaan, auto-HTTPS):* caddy download -> `caddy validate --config deploy\Caddyfile` -> `caddy run --config deploy\Caddyfile`
   - *Nginx:* zip C:\nginx mein extract -> `deploy\nginx.conf` ko `C:\nginx\conf\nginx.conf` par copy -> `nginx -t` -> `start nginx`
   - HTTPS (Nginx): certs banao (Git ke saath openssl aata hai):
     `& "C:\Program Files\Git\usr\bin\openssl.exe" req -x509 -nodes -newkey rsa:2048 -days 365 -keyout C:\nginx\certs\server.key -out C:\nginx\certs\server.crt -subj "/CN=company-ai.local"`
     phir `nginx-https.conf` use karo. Self-signed cert par browser warning dega: company-wide ke liye internal CA ya Caddy `tls internal` behtar.
3. **Backup roz:** `python scripts\backup.py` ; Task Scheduler:
   `schtasks /Create /SC DAILY /ST 02:00 /TN "CompanyAI Backup" /TR "powershell -NoProfile -Command cd C:\Users\sh\company-ai-server; .\.venv\Scripts\python.exe scripts\backup.py"`
   Backups ko kisi dusri drive/machine par bhi copy karo.
4. **Firewall:** sirf proxy ka port (80/443) LAN ko kholo. 8000/8081/8082 sirf 127.0.0.1 par rehne do.
5. **Monitoring:** `/metrics` sirf localhost se; Prometheus/Grafana ke liye docker-compose.monitoring.yml.

Ye configs is sandbox mein run nahi ho sake: `nginx -t` / `caddy validate` pehle chalao.
