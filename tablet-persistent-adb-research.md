# Persistent, Restart-Safe Access to the Khan Kids Tablet

## Executive recommendation

The best wireless fit for this system is **Android 17 ADB Wi-Fi 2.0 plus repo-managed, on-demand device discovery**. Keep the tablet paired to the control computer, mark only the home Wi-Fi network as trusted for wireless debugging, use ADB 37.0.1 or later, and resolve the tablet by stable hardware identity instead of requiring a remembered `IP:port`. Live testing established one device-specific limitation: this Pixel build turns the Wireless debugging master switch off on a cold tablet restart, so wireless-only recovery from that event is not unattended.

One complication is specific to this installation: Codex runs inside an OrbStack Linux VM. The VM can make ordinary TCP connections to the tablet, but it does not receive the home LAN's link-local mDNS advertisements. The macOS host does receive them. The least-privilege solution is therefore to use macOS only to resolve the tablet's current `_adb-tls-connect._tcp` endpoint, then make the encrypted ADB connection directly from Linux. This avoids exposing the macOS ADB server to the LAN or VM.

This produces the desired workflow after normal host and network restarts. A
cold tablet restart additionally requires a physical unlock and manual
re-enable of Wireless debugging:

```text
./khan-mastery-sync --student Student A
        │
        ├─ discover current endpoint through macOS mDNS
        ├─ connect from Linux using the existing paired ADB key
        ├─ verify the tablet's stable hardware identity
        └─ run the existing fail-closed mastery workflow
```

No fixed address, fixed debugging port, or background reconnect daemon is required. A small retrying user service is optional, but discovery should also run at the start of every tablet command because that is the point at which access actually matters.

## Current-state findings

Read-only inspection on September 12, 2026 found:

| Component | Observed state | Consequence |
|---|---|---|
| Tablet | Pixel Tablet, Android 17 / API 37 | Supports ADB Wi-Fi 2.0 |
| Tablet wireless debugging | Enabled | The secure TLS ADB listener is active |
| Developer options | Enabled | Required setting is present |
| Linux ADB | 34.0.5 from the Ubuntu package | Too old for Google's current Wi-Fi 2.0 guidance |
| Linux VM network | Private OrbStack subnet behind the Mac | Does not receive LAN mDNS broadcasts |
| ADB 37.0.1 test in Linux | Runs successfully through OrbStack's x86-64 compatibility | Upgrade is technically feasible |
| Linux mDNS test with ADB 37.0.1 | No services found | Upgrade alone does not bridge the VM boundary |
| macOS mDNS test | Immediately found the tablet's `_adb-tls-connect._tcp` service | Host-side discovery is available and reliable |
| Stable tablet identity | Available through `ro.serialno` | The wrapper can prevent connecting to the wrong tablet |
| Existing ADB key | Present in the Linux account | Pairing can remain associated with this workstation account |
| Current ADB device list | One live endpoint plus one stale offline endpoint | Remembering or selecting an arbitrary `IP:port` is unsafe |
| Repo CLI | `--serial` is mandatory in `reading_workflow.py` | The repo still needs a resolver layer |

Google's current documentation says Android 17 and ADB 37.0.0 introduce ADB Wi-Fi 2.0, with automatic reconnection when a device joins a trusted wireless-debugging network. It also says a workstation remains paired until it is explicitly forgotten or debugging authorizations are revoked.^1 The July 2026 Platform Tools release is ADB 37.0.1 and removes the obsolete `openscreen` mDNS backend in favor of `libadbmdns`.^2

## What survives a restart

There are several independent pieces of state; treating “wireless ADB” as one switch obscures the real failure modes.

| Event | Pairing key | Trusted-network permission | IP address | TLS port | Active connection |
|---|---:|---:|---:|---:|---:|
| ADB client/server restart | Survives | Survives | Usually unchanged | Usually unchanged | Re-established by discovery |
| Linux VM restart | Survives on disk | Survives on tablet | May change | May change | Must be discovered again |
| Mac restart | Survives on disk | Survives on tablet | May change | May change | Must be discovered again |
| Tablet restart | Survives | Trusted record survives, but master switch turns off | May change | May change | Physical unlock and re-enable, then discovery |
| Router/DHCP restart | Survives | Survives | May change | May change | Must be discovered again |
| Wi-Fi network change | Survives | Network-specific | Changes | Changes | Disabled on an untrusted network |
| “Forget” host or revoke ADB authorizations | Removed | Unchanged | Irrelevant | Irrelevant | Re-pairing required |
| Factory reset | Removed | Removed | Irrelevant | Irrelevant | Full setup required |

The random port is intentional. AOSP documents that modern wireless ADB listens on a randomly selected TLS port, advertises it through `_adb-tls-connect._tcp`, and auto-connects only a previously paired host. The older `adb tcpip 5555` mechanism is a separate, unencrypted transport and is vulnerable to eavesdropping and man-in-the-middle attacks.^3

Therefore:

- Saving `192.168.x.x:port` in an environment variable is only a short-lived convenience.
- A DHCP reservation stabilizes the IP address but not the TLS port.
- A fixed port via `adb tcpip 5555` is less secure and normally does not provide the desired reboot persistence on an unrooted production Pixel.
- mDNS discovery is the supported mechanism for finding the changing endpoint.

## Recommended architecture

### 1. Tablet configuration

On the Pixel Tablet:

1. Leave **Developer options → Wireless debugging** enabled.
2. When Android asks whether to allow wireless debugging on the current Wi-Fi network, select **Always allow on this network** only for the trusted home network.
3. Keep the existing workstation under **Paired devices**. Pairing is a one-time action unless the host is forgotten, authorizations are revoked, the ADB key is lost, or the tablet is reset.^1
4. Keep the tablet powered or docked. Network debugging cannot provide access if the tablet is powered off, Wi-Fi is disconnected, the network is untrusted, or the router/control computer is unavailable.

ADB Wi-Fi 2.0's device daemon disables wireless ADB when it detects an untrusted network and re-enables it when the device returns to a user-approved network. Google reports that the new implementation was designed around precisely the prior failures caused by device restarts and network changes.^4

### 2. Current ADB on the Linux control environment

Install or pin official SDK Platform Tools 37.0.1 or later in a user-owned tools directory and put that directory before `/usr/bin` in the automation environment. Do not overwrite the Ubuntu-managed binary in place. Verify:

```bash
adb version
adb server-status
```

The expected status is version 37.0.0 or newer, `mdns_enabled: true`, and `mdns_backend: LIBADBMDNS`.^1 The official Linux binary is x86-64, but a direct test confirmed that it executes successfully in this ARM OrbStack VM through the enabled compatibility layer.

### 3. Bridge discovery, not control

Because mDNS is link-local and the VM is on a private subnet, use OrbStack's `mac` helper to run the macOS `dns-sd` resolver. The resolver should:

1. Browse `_adb-tls-connect._tcp.local` for a bounded period.
2. Select the service whose instance embeds the configured tablet hardware serial.
3. Resolve that service to its current hostname and port on macOS.
4. Resolve the `.local` hostname to an address on macOS.
5. Return only the resulting endpoint to Linux.
6. Run Linux `adb connect` against that endpoint.
7. Query `ro.serialno` and require an exact match before doing anything else.

This is preferable to running `adb -a` on macOS. An ADB server listening beyond loopback is effectively a control plane for every device attached to it; exposing port 5037 creates a much larger security boundary. Discovery-only bridging shares no ADB server socket and keeps the existing Linux-side device authorization model.

### 4. Repo-native defaults and fail-closed selection

Keep `tools/reading_workflow.py --serial` mandatory internally. That explicitness is a useful safety property. Change only the top-level wrappers so they can resolve the serial before invoking the workflow.

A private, Git-ignored configuration such as `private/tablet-device.local.json` should hold:

```json
{
  "student": "Student A",
  "model": "Pixel Tablet",
  "hardware_serial": "REDACTED-STABLE-DEVICE-ID"
}
```

The wrapper's selection policy should be:

1. If the caller supplies `--serial`, preserve the existing behavior.
2. Otherwise, inspect already connected, online hardware devices.
3. If exactly one matches the configured hardware serial, use it.
4. Otherwise, run bounded macOS mDNS discovery and `adb connect`.
5. Verify hardware serial, model, and `get-state=device`.
6. Reject zero matches, multiple matches, emulators, `offline`, and `unauthorized` states.
7. Pass the resolved live transport endpoint to the unchanged workflow.

With a configured default student, the everyday command can become:

```bash
./khan-mastery-sync
```

It is safer for the default to be private configuration rather than a hard-coded family name in a generic script.

### 5. Startup and retry behavior

An ADB client invocation starts its local server automatically, so a permanent Linux daemon is not necessary. The wrapper should wait up to roughly 20–30 seconds for the tablet to finish reconnecting after a restart, retry discovery with short backoff, and then fail with a precise reason.

A `systemd --user` service can optionally warm the connection after the VM's network becomes available. It should run the same resolver, use `Restart=on-failure`, and have a bounded retry interval. The mastery command must still perform its own verification because a startup service cannot guarantee that a later Wi-Fi endpoint remains current.

## Reliability choices

| Option | Restart-safe | Security | Operational burden | Recommendation |
|---|---:|---:|---:|---|
| ADB Wi-Fi 2.0 + macOS discovery bridge | Yes, under stated prerequisites | Strong TLS; narrow exposure | Moderate one-time repo work | **Best wireless option** |
| Direct USB-C data cable to always-on host | Best | Strong physical boundary | Cable/hub and physical placement | **Best absolute reliability** |
| DHCP reservation + saved TLS port | No; port still changes | TLS | Low until first restart failure | Do not use alone |
| Scan all ports on a reserved IP | Fragile and noisy | TLS after identification | High; poor failure behavior | Avoid |
| Legacy `adb tcpip 5555` | Usually not reboot-persistent | Unencrypted transport | Moderate | Avoid |
| Expose macOS ADB server on port 5037 | Can work | Broad control-plane exposure | Firewall and lifecycle complexity | Avoid unless tightly isolated |
| Router port-forward to tablet | Dangerous and unsupported | Exposes a powerful interface | High risk | Never use |

If “always” means the highest practical availability rather than cable-free convenience, a powered USB-C data connection to an always-on control host is the stronger answer. Wireless access always depends on the tablet, access point, host, and local network being awake and healthy. A powered data hub can preserve charging while providing USB ADB, but hardware compatibility should be tested before treating it as unattended infrastructure.

## Remote access beyond the home LAN

Wireless debugging is designed for a workstation and device on the same local network.^1 Do not expose the tablet's ADB port through router port-forwarding, public DNS, or a broad VPN rule.

The safer remote pattern is:

```text
remote operator → authenticated VPN/SSH → home control workstation → local TLS ADB → tablet
```

The Linux VM already has a Tailscale interface. That can secure access to the control workstation, while the tablet remains reachable only on the trusted home LAN. The ADB private key and the repo's PIN/password file should remain readable only by the local account. A compromise of either the control account or its ADB key grants extensive access to the tablet, including screenshots, input injection, app installation, and shell commands.

## Security boundaries

Persistent ADB is intentionally powerful. For this use case:

- Use a dedicated tablet and avoid unrelated personal accounts or data on it.
- Trust only the private home Wi-Fi; never approve guest, hotel, school, or public networks.
- Do not publish ADB ports or the ADB server port.
- Keep `~/.android/adbkey`, private device identity, `.secrets.json`, screenshots, reports, and student data out of Git and backups that are not equivalently protected.
- Preserve the repo's fail-closed identity and UI checks.
- Make recovery physical: if pairing is lost, require someone at the unlocked tablet to pair again rather than weakening authentication.
- Accept that no software arrangement can guarantee access during power loss, pre-boot failure, Wi-Fi/router outage, a stopped OrbStack VM, or a sleeping/off Mac.

## Implementation sequence and acceptance tests

### Phase 1 — prerequisites

1. Verify **Always allow on this network** on the tablet.
2. Install ADB 37.0.1+ in a user-owned, stable path.
3. Preserve and back up the ADB private key securely.
4. Record the stable tablet identity in a private ignored config.

### Phase 2 — repo integration

1. Add a bounded macOS DNS-SD resolver.
2. Add identity verification and stale/offline filtering.
3. Update `khan-mastery-sync` and `khan-kids-open` wrappers to resolve automatically when `--serial` is absent.
4. Keep explicit `--serial` as a diagnostic override.
5. Add unit tests with recorded discovery output; do not make CI depend on a live tablet.
6. Update the README with the zero-address workflow and recovery procedure.

### Phase 3 — failure testing

Run each test separately and confirm the wrapper either connects to the correct stable identity or fails closed:

- Restart only the Linux ADB server.
- Restart the OrbStack VM.
- Restart the Mac.
- Restart the Pixel Tablet and unlock it if Android requires first unlock.
- Restart the Wi-Fi router.
- Renew the tablet DHCP lease.
- Switch briefly to an untrusted network and return to the trusted network.
- Leave a stale offline ADB endpoint in the device list.
- Attach a second Android device or emulator.
- Revoke ADB authorization and verify that the wrapper refuses to proceed and requests physical re-pairing.

Success means no remembered IP or port is needed, the correct hardware identity is verified, the tablet returns after each supported restart case, and every unsafe ambiguity stops before UI automation begins.

## Bottom line

The current setup is close: Android 17, pairing, wireless debugging, and the repo's safe automation are already present. The missing pieces are a current ADB client and an mDNS bridge across OrbStack's VM boundary. Implementing on-demand discovery and identity verification in the repo is more robust than preserving a changing address, safer than a fixed unencrypted port, and simpler than maintaining a permanently exposed ADB server.

“Always available” should be defined operationally as: **while Wireless
debugging is enabled and the trusted home Wi-Fi, Mac, and OrbStack VM are
powered and connected, a repo command can rediscover and verify the tablet
without human entry of an IP address or port**. Live reboot testing showed that
the switch turns off on this build. A minimal Direct Boot helper with only boot
and secure-settings permissions was built, installed, granted, and cold-boot
tested; it did not restore wireless ADB before or after first unlock, so the app
and grant were removed. For unattended cold-reboot recovery, use a physical USB
data path to an always-on host and verify pre-unlock behavior on the installed
Android build.

## Sources

1. Google, “[Android Debug Bridge (adb)](https://developer.android.com/tools/adb),” updated September 2, 2026. Wireless debugging, trusted-network behavior, pairing persistence, ADB 37 diagnostics, and mDNS requirements.
2. Google, “[SDK Platform Tools release notes](https://developer.android.com/tools/releases/platform-tools),” ADB 37.0.0 and 37.0.1 releases, February and July 2026.
3. Android Open Source Project, “[Architecture of ADB Wifi](https://android.googlesource.com/platform/packages/modules/adb/+/HEAD/docs/dev/adb_wifi.md).” TLS transport, random ports, service types, auto-connect, and legacy TCP security.
4. Android Developers Blog, “[Introducing Fast and Reliable Wireless Debugging with Android Debug Bridge (ADB) Wi-Fi 2.0](https://android-developers.googleblog.com/2026/09/wireless-debugging-adb-wifi-2.html),” September 9, 2026.
5. Local system and tablet inspection, September 12, 2026. Read-only ADB settings/properties, network interfaces, macOS DNS-SD browse, ADB 37.0.1 compatibility test, and repository source inspection.
6. Repository documentation, [`README.md`](README.md), especially “Enable wireless debugging,” “Pair, then connect,” privacy limitations, and troubleshooting.
