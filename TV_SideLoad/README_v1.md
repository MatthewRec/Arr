# Sideloading Jellyfin onto a Samsung Tizen TV

The exact sequence that worked: build a certificate tied to your TV, sign the prebuilt Jellyfin package, and push it over the network with the Tizen CLI.

| | |
|---|---|
| **Install method** | Prebuilt `.wgt` |
| **Sign & install from** | `C:\tizen-studio\tools\ide\bin` (or the portable path below) |
| **Shell** | Windows PowerShell |
| **TVs done so far** | 75" M70H and 85" QN85B — see [Devices and profiles](#devices-and-profiles) |

> [!important]
> **The distributor certificate is bound to a specific TV.** Each TV has its own Device Unique ID (DUID), and the DUID is baked into the distributor certificate as a Subject Alternative Name. A certificate made for one TV **will not install on another** — see [Adding another TV](#adding-another-tv). This is the single most common reason a sideload that "worked last time" fails.

## Contents

- [Devices and profiles](#devices-and-profiles)
- [Prerequisites](#prerequisites)
- [1. Create the certificate](#1-create-the-certificate)
- [2. Enable Developer Mode on the TV](#2-enable-developer-mode-on-the-tv)
- [3. Connect to the TV](#3-connect-to-the-tv)
- [4. Get the Jellyfin package in place](#4-get-the-jellyfin-package-in-place)
- [5. Sign the package](#5-sign-the-package)
- [6. Install to the TV](#6-install-to-the-tv)
- [Adding another TV](#adding-another-tv)
- [Certificate expiry](#certificate-expiry)
- [Portable toolchain (no admin rights)](#portable-toolchain-no-admin-rights)
- [Troubleshooting](#troubleshooting)
- [Quick reference](#quick-reference)

## Devices and profiles

Certificates live in `C:\Users\matth\SamsungCertificate\<profile>\`. Profiles are registered in
`<tizen-studio-data>\profile\profiles.xml`.

| Profile | Distributor DUID(s) | Distributor expires | Status |
|---|---|---|---|
| `Parents_tv` | `EXCPHAB7ET6MU` (85" QN85B, `QN85QN85BDFXZA`) | 2027-10-11 | **Working.** Installed 2026-10-10 |
| `Jellyfin` | `7XCNQVDG2M7YE`, `10.23.2.4` | 2027-08-28 | Working for the 75" TV |
| `75in` | `10.23.2.4` only | 2027-08-27 | **Dead** — an IP address was typed into the DUID field, so it matches no TV. Superseded by `Jellyfin` |

All three share **one author certificate** (`SamsungCertificate\Jellyfin\author.p12`, `CN=Matthew`, expires **2027-08-28**). Keep it that way — see [Adding another TV](#adding-another-tv).

> The `10.23.2.4` entries are the original mistake preserved for the record: that is a homelab IP, not a DUID. A DUID is a 13-character alphanumeric string like `EXCPHAB7ET6MU`. Get it from the TV with `sdb shell 0 getduid` (step 3) rather than typing it by hand.

## Prerequisites

- [ ] Tizen Studio with the **Samsung Certificate Extension**. Two routes:
  - **Standard:** install Tizen Studio, then add the extension via Package Manager → Extension SDK. The installer **requires administrator rights**.
  - **Portable, no admin:** see [Portable toolchain](#portable-toolchain-no-admin-rights). This is what the 85" install actually used.
- [ ] A free **Samsung Developer account** — needed to generate the distributor certificate.
- [ ] Computer and TV on the **same local network** (same subnet, not a guest network).
- [ ] The **Jellyfin `.wgt`** you want to install. `TV_SideLoad/Jellyfin-10.10.z.wgt` in this repo is ready to go.

## 1. Create the certificate

A working Tizen certificate **profile** has two halves: an **author certificate** that identifies you, and a **distributor certificate** tied to your specific TV's DUID. Current Tizen firmware checks the distributor half — this is the part that actually has to match your TV.

1. Get the TV's DUID. The reliable way is over `sdb` once Developer Mode is on (step 3): `sdb shell 0 getduid`. On the TV itself it is under **Menu → Support → Contact Samsung → Unique Device ID** (sometimes **Settings → Support → About This TV**).
2. In Tizen Studio: **Tools → Certificate Manager**. (Portable route: run `tools\certificate-manager\eclipse.exe`.)
3. Click **+** to start a new profile. Cancel the migration dialog if it appears.
4. Choose **Samsung** as the certificate type, then **TV** as the device type.
5. Name the profile something you'll remember — you'll reference this name every time you sign a build.
6. **Author certificate:** if you already have one, choose *Select an existing author certificate* and point it at `SamsungCertificate\Jellyfin\author.p12`. Only create a new one if you have none — and if you do, back up the `.p12`.
7. **Distributor certificate:** sign in with your Samsung account, then add the TV's DUID from step 1. With the TV connected over `sdb`, Certificate Manager can read it for you — prefer that over typing.
8. Click **Finish**.

> **Keep the backup.** Future Jellyfin updates must be signed with the *same author certificate* — a different one is treated as a different publisher and won't be allowed to overwrite the existing install.

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
sdb shell 0 getduid
```

- `sdb devices` lists the TV and its **target name** (e.g. `QN85QN85BDFXZA`) — you need this for step 6.
- `sdb shell 0 getduid` prints the **DUID** (e.g. `EXCPHAB7ET6MU`) — you need this for step 1. Plain `sdb shell getduid` without the `0` returns nothing.

A quick sanity check that Developer Mode is actually live, before anything else:

```powershell
Test-NetConnection -ComputerName TV_IP_ADDRESS -Port 26101 -InformationLevel Quiet   # must be True
```

## 4. Get the Jellyfin package in place

1. Use `TV_SideLoad/Jellyfin-10.10.z.wgt` from this repo, or download a release from the [jellyfin-tizen releases page](https://github.com/jellyfin/jellyfin-tizen/releases).
2. Copy it to wherever you'll sign from. **Sign a fresh copy each time** — signing rewrites the `.wgt` in place, so keep the pristine original untouched.

## 5. Sign the package

```powershell
cd C:\tizen-studio\tools\ide\bin
.\tizen package -t wgt -s YOUR_PROFILE_NAME -- Jellyfin.wgt
```

Replace `YOUR_PROFILE_NAME` with the profile from step 1. This re-signs the file with your certificate in place. The app id (`AprZAARz4r.Jellyfin`) comes from the package's `config.xml` and does **not** change with the certificate.

## 6. Install to the TV

```powershell
.\tizen install -n Jellyfin.wgt -t QN85QN85BDFXZA
```

Use the target name from step 3. A successful install reports `Installed the package: Id(AprZAARz4r.Jellyfin)` and counts up to `installing[100]`. Find it on the TV under **Apps → Downloaded**.

Confirm it registered:

```powershell
sdb shell 0 applist | Select-String Jellyfin
#   'Jellyfin'   'AprZAARz4r.Jellyfin'
```

## Adding another TV

You do **not** need a whole new identity for a second TV — you need a new *distributor* certificate and the *same* author certificate.

1. Get the new TV's DUID (`sdb shell 0 getduid`).
2. Certificate Manager → **+** → Samsung → TV → new profile name.
3. Author: **reuse** `SamsungCertificate\Jellyfin\author.p12`.
4. Distributor: sign in, add the new DUID.
5. Sign and install with the new profile name.

A single distributor certificate can hold **up to 10 DUIDs**, so you can also add a TV to an existing profile instead of making a new one. Reusing the author certificate keeps the publisher identity stable, which is what lets you update the app on *any* of these TVs later without uninstalling first.

## Certificate expiry

Validity is stamped by Samsung's CA at issuance and is part of the signed certificate — it **cannot be extended**. These are issued for one year. When they lapse you re-issue in Certificate Manager with the same Samsung account.

The binding deadline is the **earliest** cert in the chain you sign with. Right now that's the shared author certificate: **2027-08-28**.

Expiry definitely blocks *signing and installing*. Whether an already-installed app keeps launching past expiry is not something to rely on — there are consistent reports of dev-signed Tizen apps refusing to start once the signing certificate lapses. Plan on a re-sign.

## Portable toolchain (no admin rights)

The official Tizen Studio installer needs elevation. If you can't (or don't want to) run it, the SDK can be assembled from the standalone package zips into a user-writable folder. This is how the 85" TV was done.

Current layout on this machine:

| Path | What |
|---|---|
| `C:\Users\matth\tizen-studio` | The assembled SDK |
| `C:\Users\matth\tizen-jdk\jdk-17.0.20.1+1` | Portable Temurin 17 for the CLI |
| `C:\Users\matth\tizen-studio-data` | `profile\profiles.xml` and CLI logs |
| `C:\Users\matth\tizen-work\sideload-jellyfin.ps1` | One-shot sign + install script |

Built by downloading from `https://download.tizen.org/sdk/tizenstudio/official/binary/` and merging each zip's `data/` directory into `tizen-studio`:

```
new-common-cli   new-web-cli   tizen-core   sdb
certificate-encryptor   certificate-generator
embedded-java   certificate-manager-product
```

plus the Samsung Certificate Extension from `https://download.tizen.org/sdk/extensions/tizen-certificate-extension_<ver>.zip`
(unzip, then merge `binary/cert-add-on_<ver>_windows-64.zip`'s `data/`).

`COMMON-CLI` and `WebCLI` are meta-packages containing only a manifest — the real jars are in `new-common-cli` / `new-web-cli`.

Then hand-write `tizen-studio\sdk.info`:

```
TIZEN_SDK_INSTALLED_PATH=C:/Users/matth/tizen-studio/
TIZEN_SDK_DATA_PATH=C:/Users/matth/tizen-studio-data/
JDK_PATH=C:/Users/matth/tizen-jdk/jdk-17.0.20.1+1
```

To register a profile from certificates you already have, without the GUI:

```powershell
tizen security-profiles add -n NAME -a <author.p12> -p <pw> -d <distributor.p12> -dp <pw> -f -A
```

The `.p12` files already contain their Samsung intermediate CA, so `-c` / `-dc` are not needed — Certificate Manager itself leaves `ca=""` in `profiles.xml`.

## Troubleshooting

<details>
<summary><code>Check certificate error : :Invalid format of certificate in signature.:&lt;-2&gt;</code> — install fails around 26%</summary>

**This almost always means the TV's DUID is not in your distributor certificate.** The message mentions "format", which sends you hunting for a malformed certificate chain — ignore that. It is a device-binding failure.

Check what your certificate actually permits:

```powershell
openssl x509 -in C:\Users\matth\SamsungCertificate\<profile>\distributor.crt -noout -ext subjectAltName
# X509v3 Subject Alternative Name:
#     URI:URN:tizen:packageid=, URI:URN:tizen:deviceid=EXCPHAB7ET6MU
```

Compare against `sdb shell 0 getduid`. If the DUID isn't listed, no amount of re-signing will help — issue a distributor certificate that includes it ([Adding another TV](#adding-another-tv)).

For the record, this error is *not* fixed by exporting the intermediate CAs out of the `.p12` and passing `-c` / `-dc`. That was tried; the chain was already correct and the error was identical.
</details>

<details>
<summary><code>Invalid file or password</code> when registering a profile, with a password you know is right</summary>

`tizen.bat` routes its arguments through `cmd.exe`, which mangles `!`, `&`, `^`, `%`, `<`, `>` and `|`. A password containing any of those never arrives intact.

Call the CLI's Java entry point directly instead:

```powershell
$jars = (Get-ChildItem "$tz\tools\ide\lib-ncli\*.jar" | ForEach-Object { $_.FullName }) -join ';'
Push-Location "$tz\tools\ide\bin"
& $java "-Dlog4j.configurationFile=log4j-progress.xml" `
        -cp "$tz\tools\ide\conf-ncli;$jars" org.tizen.ncli.ide.shell.Main <args>
Pop-Location
```

`sideload-jellyfin.ps1` already does this.
</details>

<details>
<summary>Lost the author certificate password</summary>

It's recoverable on the machine that created it. `SamsungCertificate\<profile>\author.pwd` is a 4-byte little-endian length followed by a **Windows DPAPI** blob, encrypted for your user account. The payload is the password plus a trailing NUL:

```powershell
Add-Type -AssemblyName System.Security
$all = [System.IO.File]::ReadAllBytes("$certDir\author.pwd")
$len = [BitConverter]::ToInt32($all, 0)
$dec = [System.Security.Cryptography.ProtectedData]::Unprotect(
         $all[4..(4+$len-1)], $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
[System.Text.Encoding]::UTF8.GetString($dec).TrimEnd([char]0)
```

Note that `profiles.xml` written by Certificate Manager puts the **path to the `.pwd` file** in the `password` attribute, not the password itself. A profile added via `security-profiles add` stores the encrypted value inline instead. Both work.
</details>

<details>
<summary>Certificate Manager opens and immediately closes (portable install)</summary>

Run `tools\certificate-manager\eclipse.exe`, not `certificate-manager.exe`. The latter looks for a `certificate-manager.ini` that the package doesn't ship, so it exits 0 with no window and no log. Only `eclipse.ini` exists, and the `eclipse.exe` launcher matches it.
</details>

<details>
<summary>"tizen : The term 'tizen' is not recognized..."</summary>

PowerShell — unlike `cmd.exe` — won't run an executable from the current folder unless it's prefixed with `.\`. From inside the `bin` folder, run `.\tizen`. To drop the prefix permanently:

```powershell
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";C:\tizen-studio\tools\ide\bin", "User")
```

Then open a **new** PowerShell window.
</details>

<details>
<summary>Certificate / signature mismatch on install</summary>

The `.wgt` was signed with a different **author** certificate than the one already on the TV. Re-sign with the original author certificate, or uninstall the app on the TV first.
</details>

<details>
<summary>An install that used to work suddenly fails</summary>

Check the certificate dates — these are one-year certificates:

```powershell
openssl x509 -in <cert>.crt -noout -dates
```

See [Certificate expiry](#certificate-expiry). (An earlier version of this note claimed 3-month author certificates; the ones actually in use are issued for a year.)
</details>

<details>
<summary>"Permit to install applications" errors</summary>

Only affects older-style DUIDs (the ones starting with `1.0#`). In Device Manager, right-click the connected TV in the file explorer pane and choose **Permit to install applications**, then retry.
</details>

<details>
<summary><code>sdb connect</code> hangs or refuses</summary>

Confirm the TV and computer are genuinely on the same subnet — corporate and guest Wi-Fi commonly isolate clients from each other, which blocks this entirely. Then confirm Developer Mode is listening:

```powershell
Test-NetConnection -ComputerName TV_IP_ADDRESS -Port 26101 -InformationLevel Quiet
```

Also check the **Host PC IP** set on the TV in Developer Mode still matches this machine's current IP — it changes with DHCP.
</details>

## Quick reference

Once a profile exists for the target TV and Developer Mode is on:

```powershell
sdb connect TV_IP_ADDRESS
cd C:\tizen-studio\tools\ide\bin
.\tizen package -t wgt -s YOUR_PROFILE_NAME -- Jellyfin.wgt
.\tizen install -n Jellyfin.wgt -t TARGET_NAME
```

Or, with the portable toolchain, the whole round trip including a DUID pre-check:

```powershell
powershell -ExecutionPolicy Bypass -File C:\Users\matth\tizen-work\sideload-jellyfin.ps1 -ProfileName Parents_tv
```

---

Sources: [jellyfin/jellyfin-tizen](https://github.com/jellyfin/jellyfin-tizen) · [Samsung Developer — Creating Certificates](https://developer.samsung.com/smarttv/develop/getting-started/setting-up-sdk/creating-certificates.html) · [Samsung Developer — Permitting Device Installs](https://developer.samsung.com/galaxy-watch-tizen/getting-certificates/permit.html)

---

## Prebuilt package in this repo

`Jellyfin-10.10.z.wgt` (11 MB) is the package used for both TVs, so step 4 can use it instead of
downloading a release. App id `AprZAARz4r.Jellyfin`.

It was built from the unmodified official repos at these commits:

| Repo | Branch | Commit |
| --- | --- | --- |
| [jellyfin/jellyfin-web](https://github.com/jellyfin/jellyfin-web) | `release-10.10.z` | `2efac6f67a` (2026-02-19) |
| [jellyfin/jellyfin-tizen](https://github.com/jellyfin/jellyfin-tizen) | `master` | `31408c1` (2025-10-24) |

> **Open item:** the client is 10.10.z. Match it to the Jellyfin *server* version when the server is
> upgraded — the server is `10.23.200.101:8096`, which is not reachable from the house Wi-Fi the TVs
> are on, so this is still unverified.
