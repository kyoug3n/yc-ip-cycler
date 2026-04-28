# yc-ip-cycler

Repeatedly allocate Yandex Cloud static external IPs until one lands inside a desired subnet range, then save it to `result.json`.

**Why this works for anti-censorship:** Certain Yandex Cloud IP ranges (ASN 13238 and the Cloud-specific allocations) are whitelisted by ISPs in restrictive countries — traffic to/from those IPs is not filtered. This tool automates the tedious process of requesting IPs until you get one in such a range.

---

## Prerequisites

- Python 3.10+
- A Yandex Cloud account with billing enabled
- A folder where you have `vpc.addresses.create` and `vpc.addresses.delete` permissions (the `editor` or `vpc.admin` role on the folder is sufficient)

---

## Installation

```bash
cd yc-ip-cycler
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## Authentication

You must set **one** of the following environment variables before running:

### Option A — IAM token (recommended, short-lived)

```bash
# Install the yc CLI: https://yandex.cloud/en/docs/cli/quickstart
yc init                          # one-time setup
export YC_TOKEN=$(yc iam create-token)
```

An IAM token is valid for **12 hours**. Re-export it before it expires.

### Option B — API key (long-lived)

1. In the [Yandex Cloud console](https://console.yandex.cloud/), go to **Service accounts**.
2. Create a service account and assign it the `vpc.admin` role on your folder.
3. Under the service account, create an **API key** and copy the secret value.

```bash
export YC_API_KEY="AQVN..."
```

---

## Getting your Folder ID

```bash
yc resource-manager folder list
```

Or open the console → select your cloud → click the folder → the ID is shown in the URL and the **Overview** tab.

---

## Configuration

```bash
cp config.yaml.example config.yaml
```

Edit `config.yaml`:

```yaml
folder_id: "b1g0000000000000000"   # your folder ID
zone: "ru-central1-a"              # availability zone

desired_subnets:
  - "5.45.192.0/18"
  - "77.88.0.0/18"
  - "213.180.192.0/19"

max_attempts: 50
release_on_exit: true
```

### Finding relevant Yandex subnets

The prefixes most commonly whitelisted by ISPs belong to **AS13238** (Yandex) and Yandex Cloud allocations.  Current prefix lists:

- https://bgp.he.net/AS13238#_prefixes
- https://ipinfo.io/AS13238

`config.yaml.example` already includes the major prefixes as of mid-2025.

---

## Usage

```bash
# Normal run
python main.py

# Custom config path
python main.py --config /path/to/config.yaml

# Dry run — shows what would happen, makes no API calls
python main.py --dry-run

# Verbose — show DEBUG output on stdout (always written to cycle.log)
python main.py --verbose
```

### Example output

```
2025-07-14 11:03:01  INFO     [1/50] Allocating IP (name=ipcycler-20250714110301-a3f9c1, zone=ru-central1-a)…
✗ Attempt 1: 84.201.144.73 — not in desired range, releasing…
2025-07-14 11:03:06  INFO     [2/50] Allocating IP (name=ipcycler-20250714110305-b7d2e4, zone=ru-central1-a)…
✗ Attempt 2: 130.193.50.12 — not in desired range, releasing…
2025-07-14 11:03:11  INFO     [3/50] Allocating IP (name=ipcycler-20250714110310-c1a8f3, zone=ru-central1-a)…
✓ Found matching IP: 77.88.17.203 (subnet: 77.88.0.0/18) after 3 attempt(s)
  Resource ID: e9b30e4sd3ee4abc123
  Result saved to result.json
```

### result.json (written on success)

```json
{
  "ip_address": "77.88.17.203",
  "resource_id": "e9b30e4sd3ee4abc123",
  "matched_subnet": "77.88.0.0/18",
  "attempts": 3,
  "zone": "ru-central1-a"
}
```

**Idempotency:** If `result.json` already exists, the tool prints the saved IP and exits without making any API calls.  Delete `result.json` to run a fresh search.

---

## Running the tests

```bash
pytest tests/ -v
```

The test suite covers CIDR boundary conditions, multiple subnets, `/32` and `/0` edge cases, and bitmask correctness (ensuring the tool doesn't fall back to string-prefix matching).

---

## What to do after finding a matching IP

Once `result.json` is written, the IP is **reserved** in your Yandex Cloud folder but not attached to anything yet.  You should:

1. **Create a VM** in the same zone and availability zone as the IP, or use an existing one.
2. **Attach the reserved IP** to the VM's network interface:
   ```bash
   yc compute instance add-one-to-one-nat <instance-id> \
       --network-interface-index 0 \
       --external-ip-address-id <resource_id from result.json>
   ```
3. **Install proxy software** on the VM.  Popular choices:
   - **3proxy** — lightweight, TCP/UDP, minimal config
   - **Xray-core** (VLESS/XTLS) — censorship-resistant transport
   - **Outline** (Shadowsocks) — easy key management via the Outline Manager app
   - **sing-box** — multi-protocol, actively maintained

4. Point your client at `<ip_address>:<proxy_port>`.

> **Note:** This tool only handles the IP selection step.  VM provisioning, proxy installation, and client configuration are outside its scope.

---

## Cost notes

- Yandex Cloud charges for **idle static IPs** (not attached to a running resource).
- Each failed attempt: allocate → release ≈ a few seconds of idle billing, negligible.
- The matched IP accrues the idle fee until you attach it to a VM.
- See current pricing at https://yandex.cloud/en/docs/vpc/pricing

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Auth error: No credentials found` | Export `YC_TOKEN` or `YC_API_KEY` |
| `HTTP 403` | The service account / IAM token lacks `vpc.addresses.create` on the folder |
| `HTTP 429` | You're hitting rate limits; the tool retries automatically with backoff |
| `Config file not found` | Run `cp config.yaml.example config.yaml` and fill in values |
| Operation never completes | Check Yandex Cloud status page; operations time out after 30 s by default |
