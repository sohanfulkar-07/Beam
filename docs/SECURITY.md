# PhotoBeam — Security Model

## Threat Model

Attacker is on the same local network. PhotoBeam does NOT protect against:
- Physical access to device
- Compromised OS

## Controls

### 1. Session Token
- 32 bytes from `secrets.token_bytes(32)` (CSPRNG)
- Included in QR payload
- Validated on every connection
- Never stored to disk
- Never reused

### 2. QR Expiry
- Default 5 minutes from generation
- Receiver refuses connections after expiry
- QR display shows countdown timer

### 3. TLS 1.3
- Receiver generates ephemeral RSA-2048 self-signed cert per session
- SHA-256 fingerprint of cert is embedded in QR payload (`cert_fp`)
- Sender verifies fingerprint before trusting any data (TOFU pinning)
- Prevents MITM on local network

### 4. Receiver Approval
- Receiver UI shows sender info before accepting files
- User must explicitly accept
- Default: accept (auto-accept configurable)

### 5. Local Network Only
- Server binds only to local interface addresses (not 0.0.0.0 by default)
- If binding to all interfaces is needed (hotspot mode), token auth + TLS prevent unauthorized access

### 6. Session Invalidation
- Session invalidated after: transfer complete, cancel, expiry, or error
- Second connection attempt with same token is rejected

## What is NOT done (acceptable for v1)
- End-to-end encryption beyond TLS (content is encrypted in transit)
- Mutual TLS (sender is authenticated only by token, not cert)
- Certificate revocation
- Anti-replay for individual chunks (relying on TLS record integrity)
