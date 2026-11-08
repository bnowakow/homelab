# macOS provisioning

On a new Mac, run this command in Terminal as your normal user, without `sudo`:

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/bnowakow/homelab/master/taerar/ansible/provision.sh)" -- --unattended
```

The examples use `--unattended` to automatically proceed past installation
confirmations such as `Do you want to proceed with the installation? [y/n]`.
Omit `--unattended` to keep interactive prompts. The Homebrew installer requires
existing sudo authorization for unattended installation; if necessary, run
`sudo -v` first. Passwords and macOS permission dialogs cannot be auto-answered.
The 1Password checks skip dialogs for verified completed setup or explicitly
reused saved confirmations. Unsaved incomplete or unverified setup still prompts,
even with `--unattended`. Live agent and GitHub SSH checks still run before
switching origin. Undo offers a commit/ignore/Abort dialog for uncommitted work.

It runs the provisioning script from `master`, creates `~/code` if needed,
clones this repository into `~/code/homelab` over HTTPS if missing, and installs
Homebrew and Ansible if needed, then uses Ansible to install 1Password for Mac and
1Password for Safari, then run `make install-dotfiles` through Ansible to copy
dotfiles into `~/`. It installs Prezto and Powerlevel10k using the shared Linux/macOS
role, then installs GitHub CLI, opens browser login, switches
origin to SSH, and installs missing tools used by the root Makefile targets.
Ansible then guides Finder connection to the NAS Time Machine share and computer/
version selection. Both minor and major paths offer suggested or manual version
numbers, then ask whether to copy data from an existing version of that
computer. Backup creation, copying and restoration are still TODO.

Accepted wizard answers are saved immediately to the Git-ignored
`taerar/ansible/time-machine-choices.json`. Before the 1Password stage, reruns show
an aligned, readable summary and file path, offering reuse, new choices, or Abort.
Reuse asks only unanswered questions; selecting new choices deletes the old file.
The summary shows the literal version number once accepted. Undo removes this file.
Use `undo-provision.sh --keep '{"gh":"preferences","1password":"app"}'` to retain
GitHub authentication and 1Password apps between test runs. See [the provisioning documentation](ansible/README.md) for
details and manual prerequisites.

# IndexNow URL submission

`submit-indexnow.py` takes one URL and submits it to all discoverable IndexNow
participants plus the global endpoint. It requires Python 3, with no packages.
Endpoints are discovered from the [official registry](https://www.indexnow.org/searchengines.json)
and each participant's metadata. The [official FAQ](https://www.indexnow.org/faq)
provides fallback endpoints for Amazonbot, Bing, Naver, Seznam.cz, Yandex, and Yep.
The registry also lists the Internet Archive; its endpoint is discovered at runtime.
Discovery uses the registry's engine names and the HTTPS metadata URLs it provides;
differences in metadata `id` values do not reject otherwise valid API endpoints.

## Set up ownership once per website

Run the script with just the URL. If neither `--key` nor `INDEXNOW_KEY` is set,
it asks whether to generate a key. Answer `y` to create `<key>.txt` in the current
directory, containing a cryptographically random key. The script prints the
upload URL and a command to rerun after uploading, then exits without submitting.
If you supply `--key-location`, that is used as the upload destination.

Publish the generated UTF-8 text file at
`https://your-domain.example/<key>.txt` (or your custom key location). The key must
be 8–128 letters, digits, or dashes. You must control the submitted URL's website;
an arbitrary third-party URL cannot be submitted without its ownership key.

## Usage

From the repository root:

```sh
# First run: offer to generate the key and file for upload.
./taerar/submit-indexnow.py 'https://your-domain.example/article'

# After uploading, reuse the key printed by the script.
export INDEXNOW_KEY='your-published-key'
./taerar/submit-indexnow.py 'https://your-domain.example/article?x=1&y=2'

# Discover endpoints and preview without submitting (still needs internet).
./taerar/submit-indexnow.py --dry-run 'https://your-domain.example/article'

# Bypass a slow/unreachable registry and use the known FAQ endpoints.
./taerar/submit-indexnow.py --skip-discovery --timeout 5 \
  'https://your-domain.example/article'

# Alternatively pass the key explicitly and specify a custom key-file location.
./taerar/submit-indexnow.py --key 'your-published-key' \
  --key-location 'https://your-domain.example/catalog/ownership.txt' \
  'https://your-domain.example/catalog/article'
```

Custom key locations can also be set through `INDEXNOW_KEY_LOCATION`. A key file
in a subdirectory only authorizes URLs within that directory. Use the exact host
of the submitted URL (including `www`, if present), and percent-encode non-ASCII
characters in URLs. Quote URLs to preserve shell characters such as `&`.

Each endpoint is attempted once, with a default 15-second timeout per request
(`--timeout` overrides it). Discovery and submissions print progress before each
request. Requests are sequential, so several timeouts can add up. A registry
timeout falls back to known endpoints and does not stop submissions. Use
`--skip-discovery` to bypass registry and metadata requests; this intentionally
limits coverage to the known FAQ endpoints and does not count as a discovery
failure. Failures do not stop the other submissions. Exit code
0 means every attempted endpoint returned HTTP 200 or 202 (or a complete dry
run), or the upload file was generated; 1 means an endpoint failed, discovery was
incomplete, key generation was declined, or the file could not be written;
2 means invalid arguments or a missing key without interactive input.
HTTP 202 means ownership verification is still pending. Fix a 403 by
checking your publicly hosted key file; for 429, wait before submitting again.
DNS failures mean the advertised endpoint could not be resolved. Other engines'
successful submissions remain accepted; rerunning the whole submission is not
necessary just because one endpoint is unavailable.

Terminal output uses blue engine names, yellow successful HTTP codes (200/202)
and warnings, and red failed HTTP codes and network errors. Colors are omitted
when redirecting output or when `NO_COLOR` is set.

As the [protocol documentation](https://www.indexnow.org/documentation) explains,
participating engines share submissions, so one accepted submission already
reaches the network. This script contacts all endpoints to match the requested
behavior. Receipt does not guarantee crawling or indexing; submit URLs when
content is added, updated, or deleted.
