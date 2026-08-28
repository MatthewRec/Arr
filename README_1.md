# Sideloading Jellyfin onto a Samsung Tizen TV

The exact sequence that worked: build a certificate tied to your TV, sign the prebuilt Jellyfin package, and push it over the network with the Tizen CLI.

| | |
|---|---|
| **Install method** | Prebuilt `.wgt` |
| **Sign & install from** | `C:\tizen-studio\tools\ide\bin` |
| **Example TV device name** | `UN75M70HDFXZA` |
| **Shell** | Windows PowerShell |

## Contents

- [Prerequisites](#prerequisites)
- [1. Create the certificate](#1-create-the-certificate)
- [2. Enable Developer Mode on the TV](#2-enable-developer-mode-on-the-tv)
- [3. Connect to the TV](#3-connect-to-the-tv)
- [4. Get the Jellyfin package in place](#4-get-the-jellyfin-package-in-place)
- [5. Sign the package](#5-sign-the-package)
- [6. Install to the TV](#6-install-to-the-tv)
- [Troubleshooting](#troubleshooting)
- [Quick reference](#quick-reference)

## Prerequisites

- [ ] Tizen Studio installed, with the **Samsung Certificate Extension** added via Tizen Studio's Package Manager → Extension SDK.
- [ ] A free **Samsung Developer account** — needed to generate the distributor certificate.
- [ ] Computer and TV on the **same local network** (same subnet — not a guest network).
- [ ] The **Jellyfin.wgt** release you want to install, downloaded ahead of time.

## 1. Create the certificate

A working Tizen certificate **profile** has two halves: an **author certificate** that identifies you, and a **distributor certificate** tied to your specific TV's Device Unique ID (DUID). Current Tizen firmware checks the distributor half — this is the part that actually has to match your TV.

1. On the TV, find its DUID: **Menu → Support → Contact Samsung → Unique Device ID** (path varies slightly by firmware/region — sometimes under **Settings → Support → About This TV**).
2. In Tizen Studio: **Tools → Certificate Manager**.
3. Click **+** to start a new profile. Cancel the migration dialog if it appears.
4. Choose **Samsung** as the certificate type, then **TV** as the device type.
5. Name the profile something you'll remember — you'll reference this name every time you sign a build.
6. Create the **author certificate**: fill in your details, sign in with your Samsung Developer account, and save a backup of the resulting `.p12` file somewhere safe.
7. Create the **distributor certificate**: sign in with the same Samsung account, then add the TV's DUID from step 1 (or connect the TV directly on the network and let Tizen Studio read it for you).
8. Click **Finish**.

> **Keep the backup.** Future Jellyfin updates must be signed with this *same* certificate — a different one is treated as a different publisher and won't be allowed to overwrite the existing install.

## 2. Enable Developer Mode on the TV

1. On the TV, open the **Apps** screen.
2. Type `1` `2` `3` `4` `5` on the on-screen keypad (or long-press the **Home** icon on some models) to reveal the hidden Developer options.
3. Switch **Developer mode** to **On**.
4. When prompted, enter your **computer's** IP address (not the TV's).
5. Let the TV restart.
6. After it reboots, find the **TV's** IP address under **Settings → General → Network → Network Status**.

## 3. Connect to the TV

```powershell
sdb connect TV_IP_ADDRESS
sdb devices
```

`sdb devices` lists the connected TV and its device name (e.g. `UN75M70HDFXZA`) — note it down, you'll need it in step 6. The same connection can be made from Tizen Studio's GUI via **Window → Device Manager → +**.

## 4. Get the Jellyfin package in place

1. Download the `Jellyfin.wgt` release you want from the [jellyfin-tizen releases page](https://github.com/jellyfin/jellyfin-tizen/releases).
2. Move that `.wgt` file directly into `C:\tizen-studio\tools\ide\bin` — signing and installing both happen from this folder, so keeping the file here avoids typing out full paths later.

## 5. Sign the package

```powershell
cd C:\tizen-studio\tools\ide\bin
.\tizen package -t wgt -s YOUR_PROFILE_NAME -- Jellyfin.wgt
```

Replace `YOUR_PROFILE_NAME` with the profile created in step 1. This re-signs the file with your certificate and drops a freshly signed `.wgt` in the same folder (Tizen may rename it, e.g. to something like `Jellyfin-10.11.z-secondary.wgt`).

## 6. Install to the TV

```powershell
.\tizen install -n Jellyfin-10.11.z-secondary.wgt -t UN75M70HDFXZA
```

Use the signed filename from step 5 and the device name from step 3. A successful install reports something like `Installed the package: Id(AprZAARz4r.Jellyfin)`. Find it on the TV under **Apps → Downloaded**.

## Troubleshooting

<details>
<summary>"tizen : The term 'tizen' is not recognized..."</summary>

PowerShell — unlike cmd.exe — won't run an executable from the current folder unless it's prefixed with `.\`. From inside `C:\tizen-studio\tools\ide\bin`, run `.\tizen` instead of `tizen`. To drop the prefix permanently, add that folder to your PATH:

```powershell
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";C:\tizen-studio\tools\ide\bin", "User")
```

Then open a **new** PowerShell window.
</details>

<details>
<summary>"npm is not recognized"</summary>

Node.js isn't installed, or PowerShell was opened before it finished installing. Install the LTS build from [nodejs.org](https://nodejs.org) (npm ships with it), then close and reopen PowerShell completely before retrying.
</details>

<details>
<summary>Certificate / signature mismatch on install</summary>

Almost always means the `.wgt` was signed with a different certificate than what's already on the TV. Reinstall using the same profile that installed the app originally, or uninstall the existing app on the TV first.
</details>

<details>
<summary>An install that used to work suddenly fails</summary>

Personal-use author certificates expire every 3 months by default. Open Certificate Manager, regenerate the author certificate on the same profile, and re-sign.
</details>

<details>
<summary>"Permit to install applications" errors</summary>

Only affects older-style DUIDs (the ones starting with `1.0#`). In Tizen Studio's Device Manager, right-click the connected TV in the file explorer pane and choose **Permit to install applications**, then retry the install.
</details>

<details>
<summary><code>sdb connect</code> hangs or refuses</summary>

Confirm the TV and computer are genuinely on the same subnet — corporate and guest Wi-Fi networks commonly isolate clients from each other, which blocks this entirely.
</details>

## Quick reference

Once the certificate exists and Developer Mode is already on, this is the whole round trip for a new build:

```powershell
sdb connect TV_IP_ADDRESS
cd C:\tizen-studio\tools\ide\bin
.\tizen package -t wgt -s YOUR_PROFILE_NAME -- Jellyfin.wgt
.\tizen install -n SIGNED_FILE.wgt -t UN75M70HDFXZA
```

---

Sources: [jellyfin/jellyfin-tizen](https://github.com/jellyfin/jellyfin-tizen) · [Samsung Developer — Creating Certificates](https://developer.samsung.com/smarttv/develop/getting-started/setting-up-sdk/creating-certificates.html) · [Samsung Developer — Permitting Device Installs](https://developer.samsung.com/galaxy-watch-tizen/getting-certificates/permit.html)
