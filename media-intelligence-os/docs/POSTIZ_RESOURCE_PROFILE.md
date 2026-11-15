# Postiz Resource Profile & Hardware Headroom (`16 GB` Laptop)

**Measured Date:** 2026-11-10
**Host Hardware:** Intel Core i5-13420H (12 logical CPUs), `16 GB DDR5` (`TotalVisibleMemorySize: 16,462,712 KB` ≈ `15.70 GiB`), Integrated Intel Graphics
**Docker Desktop / Engine:** Docker Desktop `4.99.0`, Engine `29.9.3`, WSL2 Linux Engine (`MemTotal: 8,166,977,536` bytes ≈ `7.61 GiB`, `Swap: 2.0 GiB`)

---

## 1. Phase-by-Phase Container & System Measurements

All container values below were captured via `docker stats --no-stream`, `wsl -d docker-desktop -e free -h`, `Get-CimInstance Win32_OperatingSystem`, `Get-Process vmmemWSL`, and `docker system df`:

| Measurement Point | `postiz-runtime-app` RAM (CPU) | `temporal-es` RAM | `temporal` RAM | `temporal-pg` RAM | `postiz-pg` RAM | `postiz-redis` RAM | `temporal-ui` + `admin-tools` RAM | WSL Used / Avail (`7.6G` total) | Host `vmmemWSL` WS / Host Free RAM |
|---|---|---|---|---|---|---|---|---|---|
| **1. Pre-Postiz Baseline** | — | — | — | — | — | — | — | `484.7M` / `7.0G` | — / `2,470,212 KB` (`~2.36 GiB`) |
| **2. Initial Startup Peak** | `2.111 GiB` (`130.35%`) | `625.8 MiB` (`11.30%`) | `354.5 MiB` (`1.47%`) | `131.7 MiB` | `65.52 MiB` | `14.19 MiB` | `10.17 MiB` | `3.2G` / `4.2G` | — |
| **3. Initial Idle Stabilized** | `1.508 GiB` (`3.57%`) | `636.5 MiB` (`0.45%`) | `368.9 MiB` (`1.07%`) | `133.9 MiB` | `68.92 MiB` | `16.23 MiB` | `10.23 MiB` | `2.7G` / `4.7G` | — |
| **4. Postiz UI Open (Playwright)** | `1.520 GiB` (`3.56%`) | `640.7 MiB` (`3.69%`) | `374.6 MiB` (`1.10%`) | `135.7 MiB` | `70.75 MiB` | `16.73 MiB` | `13.79 MiB` | — | `7,007.5 MB` / `680,240 KB` (`~664 MB`) |
| **5. API & MCP Discovery Load** | `1.466 GiB` (`3.72%`) | `595.6 MiB` (`3.11%`) | `267.2 MiB` (`4.05%`) | `98.63 MiB` | `64.59 MiB` | `13.80 MiB` | `15.88 MiB` | — | — |
| **6. UI Open After Config Restart** | `1.503 GiB` (`4.36%`) | `595.6 MiB` (`3.28%`) | `256.4 MiB` (`1.48%`) | `99.16 MiB` | `55.62 MiB` | `14.03 MiB` | `15.65 MiB` | — | — |
| **7. Full `down`/`up` Cold Restart** | `1.858 GiB` (`201.32%`) | `608.1 MiB` (`1.30%`) | `63.19 MiB` (`12.00%`) | `48.64 MiB` | `24.69 MiB` | `4.92 MiB` | `5.94 MiB` | `3.2G` / `4.2G` | `5,084.0 MB` / `2,260,536 KB` (`~2.16 GiB`) |
| **8. Post-Restart Stabilized Idle** | `1.227 GiB` (`4.81%`) | `617.5 MiB` (`4.51%`) | `133.7 MiB` (`7.75%`) | `83.30 MiB` | `24.92 MiB` | `4.89 MiB` | `6.84 MiB` | `2.8G` / `4.5G` | `4,431.2 MB` / `1,928,248 KB` (`~1.84 GiB`) |

*(Draft creation attempts returned `HTTP 400` validation immediately with negligible resource overhead because no social integration is connected in Phase 2.)*

---

## 2. Disk Space & Stability Checks

- **Host Drive `C:\`:**
  - Pre-Postiz image pull: `341.50 GB` free (`610.14 GB` used)
  - Post-Postiz pull & deployment: `325.95 GB` free (`625.70 GB` used; `~15.55 GB` consumed)
- **Docker Disk Footprint (`docker system df`):**
  - Images: `12` total (`8` active), **`11.34 GB`**
  - Containers: `8` total (`8` active), **`206.3 MB`**
  - Local Volumes: `6` total (`6` active), **`169.4 MB`**
  - Build Cache: `74` entries, **`765.8 MB`**
- **Container OOM & Unexpected Restarts:** Verified via `docker inspect` across all 8 containers: `OOMKilled=false`, `RestartCount=0`, `ExitCode=0`. Windows remained responsive throughout startup, UI tests, API/MCP suites, and 5 restart cycles.

---

## 3. Operational Risks & Phase 3+ Budgeting Recommendations

1. **Windows Host Physical RAM Pressure (`vmmemWSL`):** While WSL internals settled at `~2.8 GiB` used out of `7.6 GiB`, Windows `vmmemWSL` working set peaked at `7,007.5 MB` (~`6.84 GiB`) during active UI/browser testing (leaving `~664 MB` free physical RAM on the `16 GB` host with Windows Memory Compression active at `358.6 MB`).
2. **Startup CPU/RAM Burst:** `postiz-runtime-app` spikes to `~1.86–2.11 GiB` and `130–201%` CPU during cold boot/warmup before settling to `1.23–1.51 GiB`.
3. **Phase 3+ Design Impact:**
   - Do **not** blindly cap Postiz containers or remove Temporal/Elasticsearch before verifying compatibility—all 8 containers ran reliably with zero OOMs.
   - Keep Media Intelligence OS services (`postgres`, `redis`, `api`, `worker`, `creative`, `web`, `mcp`) slim, pin `creative-worker` concurrency strictly to `1`, and avoid running heavy concurrent browser/video workloads while Postiz is booting.
   - Re-measure combined `media-intelligence-os` + `postiz-runtime` `docker stats` and `vmmemWSL` in Phase 3.
