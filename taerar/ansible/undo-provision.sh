#!/bin/bash
# Temporary testing helper. Bootstrap from master:
# /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/bnowakow/homelab/master/taerar/ansible/undo-provision.sh)"
set -euo pipefail

undo_homebrew_manifest() {
  local state_dir=$1 manifest=$2 kind=$3
  [[ -f "$state_dir/$manifest" ]] || return 0
  local brew_bin installed package
  local packages=()
  brew_bin=$(command -v brew || true)
  if [[ -z "$brew_bin" ]]; then
    if [[ -x /opt/homebrew/bin/brew ]]; then
      brew_bin=/opt/homebrew/bin/brew
    elif [[ -x /usr/local/bin/brew ]]; then
      brew_bin=/usr/local/bin/brew
    else
      echo "Homebrew is unavailable; cannot undo $manifest." >&2
      return 1
    fi
  fi
  installed=$("$brew_bin" list "--$kind")
  while IFS= read -r package; do
    [[ -n "$package" ]] || continue
    if [[ $'\n'"$installed"$'\n' == *$'\n'"$package"$'\n'* ]]; then
      packages+=("$package")
    fi
  done < "$state_dir/$manifest"
  if [[ ${#packages[@]} -gt 0 ]]; then
    echo "Removing $manifest installed by provisioning: ${packages[*]}"
    HOMEBREW_NO_AUTOREMOVE=1 "$brew_bin" uninstall "--$kind" --force "${packages[@]}"
  fi
  rm "$state_dir/$manifest"
}

main() {
  local keep_gh_preferences=false keep_onepassword=false
  local keep_json= keep_supplied=false keep_modes
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --keep)
        if [[ $# -lt 2 || "$keep_supplied" == true ]]; then
          echo '--keep requires one JSON object and may only be specified once.' >&2
          return 2
        fi
        keep_json=$2
        keep_supplied=true
        shift 2
        ;;
      --help|-h)
        echo "Usage: $0 [--keep JSON]"
        echo 'Supported: gh=preferences; 1password=preferences or app.'
        return 0
        ;;
      *) echo "Usage: $0 [--keep JSON]" >&2; return 2 ;;
    esac
  done
  if [[ "$keep_supplied" == true ]]; then
    if ! command -v python3 >/dev/null 2>&1; then
      echo 'Python 3 is required to parse --keep JSON.' >&2
      return 2
    fi
    if ! keep_modes=$(python3 - "$keep_json" <<'PY'
import json
import sys

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key: " + key)
        result[key] = value
    return result

try:
    keep = json.loads(sys.argv[1], object_pairs_hook=unique_object)
    allowed = {"gh": ("preferences",), "1password": ("preferences", "app")}
    if not isinstance(keep, dict):
        raise ValueError("expected a JSON object")
    for key, value in keep.items():
        if key not in allowed or value not in allowed[key]:
            raise ValueError("supported entries: gh=preferences; 1password=preferences or app")
    print(str(keep.get("gh") == "preferences").lower(),
          str(keep.get("1password") == "app").lower())
except (ValueError, TypeError) as error:
    print("Invalid --keep JSON: " + str(error), file=sys.stderr)
    sys.exit(2)
PY
    ); then
      return 2
    fi
    read -r keep_gh_preferences keep_onepassword <<< "$keep_modes"
  fi
  if [[ $(uname -s) != Darwin ]]; then
    echo 'This script requires macOS.' >&2
    return 1
  fi
  if [[ $EUID -eq 0 ]]; then
    echo 'Run this script as your normal user, without sudo.' >&2
    return 1
  fi

  local state_dir="$HOME/.homelab-provision-state"
  local repo_dir="$HOME/code/homelab"
  local uninstaller
  if [[ ! -d "$state_dir" ]]; then
    echo 'No recorded provisioning changes to undo. Earlier untracked runs cannot be undone automatically.'
    return 0
  fi

  # This is a manual data-loss checkpoint, before any rollback mutations.
  if [[ -e "$repo_dir/.git" ]]; then
    local checkout_changes dialog_bin terminal_name undo_tty choice
    while true; do
      checkout_changes=$(git -C "$repo_dir" status --porcelain --untracked-files=all)
      [[ -n "$checkout_changes" ]] || break
      dialog_bin=$(command -v dialog || true)
      if [[ -z "$dialog_bin" ]]; then
        for dialog_bin in /opt/homebrew/bin/dialog /usr/local/bin/dialog; do
          [[ ! -x "$dialog_bin" ]] || break
        done
      fi
      if [[ ! -x "$dialog_bin" ]]; then
        echo 'Undo stopped: uncommitted changes exist and dialog is unavailable. Install dialog or commit/back up your work first.' >&2
        return 1
      fi
      terminal_name=$(ps -p $$ -o tty=)
      terminal_name=${terminal_name//[[:space:]]/}
      undo_tty="/dev/${terminal_name#/dev/}"
      if [[ -z "$terminal_name" || "$terminal_name" == '??' || ! -c "$undo_tty" ]]; then
        echo 'Undo stopped: run from an interactive Terminal to review uncommitted changes.' >&2
        return 1
      fi
      if ! choice=$(TERM="${TERM:-xterm-256color}" "$dialog_bin" --clear --stdout \
        --title 'Uncommitted homelab changes' --default-item exit \
        --cancel-label 'Abort' --default-button cancel \
        --menu 'The checkout has uncommitted or untracked files. Undo may delete this checkout. Choose what to do before any rollback begins.' \
        18 80 3 \
        commit 'Stage ALL changes and run make codex-commit, then recheck' \
        ignore 'Ignore local changes and proceed with undo (may delete them)' \
        exit 'Abort without undoing anything' < "$undo_tty" 2> "$undo_tty"); then
        echo 'Undo cancelled; no rollback actions were run.'
        return 0
      fi
      case "$choice" in
        commit)
          (cd "$repo_dir" && git add -A && make codex-commit) < "$undo_tty" > "$undo_tty" 2>&1
          ;;
        ignore) break ;;
        exit) echo 'Undo cancelled; no rollback actions were run.'; return 0 ;;
        *) echo 'Undo stopped: invalid dialog selection.' >&2; return 1 ;;
      esac
    done
  fi

  local retaining_onepassword=false
  if [[ "$keep_onepassword" == true && (
    -f "$state_dir/onepassword-casks" ||
    -f "$state_dir/onepassword-formulae" ||
    -f "$state_dir/onepassword-formulae.tmp" ||
    -f "$state_dir/onepassword-safari-installed" ||
    -f "$state_dir/onepassword-opened"
  ) ]]; then
    retaining_onepassword=true
  fi

  # This stage was installed last. Remove casks before their formula dependencies.
  # Reverse the final provisioning step before removing its dependencies.
  rm -f -- "$repo_dir/taerar/ansible/time-machine-choices.json"

  undo_homebrew_manifest "$state_dir" make-dependencies-casks cask
  undo_homebrew_manifest "$state_dir" make-dependencies-formulae formula
  rm -f "$state_dir/make-dependencies-casks.tmp" "$state_dir/make-dependencies-formulae.tmp"

  # Restore origin before undoing login or removing the checkout.
  if [[ -d "$state_dir/repo-origin" ]]; then
    if [[ -e "$repo_dir/.git" ]]; then
      local key url status
      for key in url pushurl; do
        if git -C "$repo_dir" config --local --unset-all "remote.origin.$key"; then
          :
        else
          status=$?
          [[ $status == 5 ]] || return "$status"
        fi
        local original_urls="$state_dir/repo-origin/urls"
        if [[ "$key" == pushurl ]]; then
          original_urls="$state_dir/repo-origin/push-urls"
        fi
        if [[ -f "$original_urls" ]]; then
          while IFS= read -r url; do
            git -C "$repo_dir" config --local --add "remote.origin.$key" "$url"
          done < "$original_urls"
        fi
      done
      echo 'Restored the original homelab origin URLs.'
    else
      echo 'Homelab checkout is absent; no origin to restore.'
    fi
    rm -rf -- "$state_dir/repo-origin"
  fi
  local origin_pending
  for origin_pending in "$state_dir"/repo-origin-pending.*; do
    [[ -d "$origin_pending" ]] || continue
    rm -rf -- "$origin_pending"
  done

  local gh_restored_home_gitconfig=false

  # Undo browser login while gh is still installed. Credentials remain in the
  # macOS keychain/config directory when gh=preferences is selected in --keep.
  if [[ -d "$state_dir/gh-login" && "$keep_gh_preferences" == false ]]; then
    local gh_config_dir
    IFS= read -r gh_config_dir < "$state_dir/gh-login/config-path"
    if [[ -z "$gh_config_dir" || "$gh_config_dir" == / || "$gh_config_dir" == "$HOME" ]]; then
      echo 'Refusing to restore an unsafe GitHub CLI config path.' >&2
      return 1
    fi
    if [[ -f "$gh_config_dir/hosts.yml" ]] && grep -q '^github.com:' "$gh_config_dir/hosts.yml"; then
      local gh_bin
      gh_bin=$(command -v gh || true)
      if [[ -z "$gh_bin" ]]; then
        if [[ -x /opt/homebrew/bin/gh ]]; then
          gh_bin=/opt/homebrew/bin/gh
        elif [[ -x /usr/local/bin/gh ]]; then
          gh_bin=/usr/local/bin/gh
        else
          echo 'Reinstall gh or rerun provisioning before undoing the retained login.' >&2
          return 1
        fi
      fi
      GH_CONFIG_DIR="$gh_config_dir" "$gh_bin" auth logout --hostname github.com </dev/null
    fi
    rm -rf -- "$gh_config_dir"
    if [[ -e "$state_dir/gh-login/config" ]]; then
      mkdir -p "$(dirname "$gh_config_dir")"
      cp -pR "$state_dir/gh-login/config" "$gh_config_dir"
    fi
    if [[ -f "$state_dir/gh-login/git-config-path" ]]; then
      local git_config
      IFS= read -r git_config < "$state_dir/gh-login/git-config-path"
      if [[ -z "$git_config" || "$git_config" == / || "$git_config" == "$HOME" || -d "$git_config" ]]; then
        echo 'Refusing to restore an unsafe Git config path.' >&2
        return 1
      fi
      rm -f -- "$git_config"
      if [[ "$git_config" == "$HOME/.gitconfig" ]]; then gh_restored_home_gitconfig=true; fi
      if [[ -e "$state_dir/gh-login/git-config" || -L "$state_dir/gh-login/git-config" ]]; then
        mkdir -p "$(dirname "$git_config")"
        cp -pP "$state_dir/gh-login/git-config" "$git_config"
      fi
    fi
    rm -rf -- "$state_dir/gh-login"
  fi
  # Snapshots left before a login attempt made any changes.
  local pending
  for pending in "$state_dir"/gh-login-pending.*; do
    [[ -d "$pending" ]] || continue
    rm -rf -- "$pending"
  done

  # Prezto was installed after dotfiles; remove only resources it created.
  if [[ -d "$state_dir/prezto" ]]; then
    local prezto_marker prezto_name prezto_changes
    for prezto_marker in "$state_dir"/prezto/link-*; do
      [[ -f "$prezto_marker" ]] || continue
      prezto_name=${prezto_marker##*/link-}
      if [[ -L "$HOME/.$prezto_name" && "$(readlink "$HOME/.$prezto_name")" == "$HOME/.zprezto/runcoms/$prezto_name" ]]; then
        rm -- "$HOME/.$prezto_name"
      elif [[ -e "$HOME/.$prezto_name" || -L "$HOME/.$prezto_name" ]]; then
        echo "Undo stopped: ~/.$prezto_name was changed after Prezto setup. Back it up before retrying." >&2
        return 1
      fi
      rm "$prezto_marker"
    done
    if [[ -f "$state_dir/prezto/repo-created" ]]; then
      if [[ -e "$HOME/.zprezto/.git" ]]; then
        prezto_changes=$(git -C "$HOME/.zprezto" status --porcelain --untracked-files=all)
        if [[ -n "$prezto_changes" ]]; then
          echo 'Undo stopped: ~/.zprezto contains local changes. Back them up before retrying.' >&2
          return 1
        fi
      fi
      rm -rf -- "$HOME/.zprezto"
      rm "$state_dir/prezto/repo-created"
    fi
    rmdir "$state_dir/prezto"
  fi

  # Restore dotfiles after GitHub and Prezto rollback, before undoing 1Password.
  if [[ -d "$state_dir/dotfiles" ]]; then
    local snapshot name
    for snapshot in "$state_dir"/dotfiles/file-*; do
      [[ -d "$snapshot" ]] || continue
      name=${snapshot##*/file-}
      # GitHub rollback already restored the earlier baseline for this shared
      # file, including snapshots from the previous provisioning order.
      if [[ "$name" == .gitconfig && "$gh_restored_home_gitconfig" == true ]]; then
        rm -rf -- "$snapshot"
        continue
      fi
      rm -f -- "$HOME/$name"
      if [[ -e "$snapshot/original" || -L "$snapshot/original" ]]; then
        cp -pP "$snapshot/original" "$HOME/$name"
      fi
      rm -rf -- "$snapshot"
      echo "Restored pre-provisioning state of ~/$name"
    done
    # Incomplete snapshots were never published and never modified a dotfile.
    for snapshot in "$state_dir"/dotfiles/pending.*; do
      [[ -d "$snapshot" ]] || continue
      rm -rf -- "$snapshot"
    done
    rmdir "$state_dir/dotfiles"
  fi

  # Reverse formula installations in order, preserving pre-existing packages.
  local package brew_bin formula installed
  for package in gh onepassword terminal-ui ansible; do
    if [[ "$package" == onepassword ]]; then
      # SSH client config is outside the app and is restored in all keep modes.
      if [[ -d "$state_dir/ssh-agent-config" ]]; then
        rm -f -- "$HOME/.ssh/config"
        if [[ -f "$state_dir/ssh-agent-config/original" ]]; then
          mkdir -p "$HOME/.ssh"
          cp -p "$state_dir/ssh-agent-config/original" "$HOME/.ssh/config"
        fi
        if [[ -f "$state_dir/ssh-agent-config/directory-created" && -d "$HOME/.ssh" ]]; then
          # Preserve any unrelated keys/files the user has added since setup.
          if [[ -z "$(ls -A "$HOME/.ssh")" ]]; then rmdir "$HOME/.ssh"; fi
        fi
        rm -rf -- "$state_dir/ssh-agent-config"
      fi
      local ssh_pending
      for ssh_pending in "$state_dir"/ssh-agent-config-pending.*; do
        [[ -d "$ssh_pending" ]] || continue
        rm -rf -- "$ssh_pending"
      done
      if [[ "$keep_onepassword" == true ]]; then continue; fi
      if [[ -f "$state_dir/onepassword-opened" ]]; then
        if pgrep -x -u "$(id -u)" 1Password >/dev/null; then
          osascript -e 'tell application "1Password" to quit'
        fi
        rm "$state_dir/onepassword-opened"
      fi
      if [[ -f "$state_dir/onepassword-safari-installed" ]]; then
        local mas_bin installed_apps
        mas_bin=$(command -v mas || true)
        if [[ -z "$mas_bin" ]]; then
          if [[ -x /opt/homebrew/bin/mas ]]; then
            mas_bin=/opt/homebrew/bin/mas
          elif [[ -x /usr/local/bin/mas ]]; then
            mas_bin=/usr/local/bin/mas
          else
            echo 'Reinstall mas before undoing the Safari extension installation.' >&2
            return 1
          fi
        fi
        installed_apps=$("$mas_bin" list)
        if awk '$1 == "1569813296" { found=1 } END { exit !found }' <<< "$installed_apps"; then
          "$mas_bin" uninstall 1569813296
        elif [[ -d '/Applications/1Password for Safari.app' || -d "$HOME/Applications/1Password for Safari.app" ]]; then
          echo 'Safari app is present but not indexed by mas. Restore Spotlight indexing and rerun undo.' >&2
          return 1
        fi
        rm "$state_dir/onepassword-safari-installed"
      fi
      undo_homebrew_manifest "$state_dir" onepassword-casks cask
      undo_homebrew_manifest "$state_dir" onepassword-formulae formula
      rm -f "$state_dir/onepassword-formulae.tmp"
      continue
    fi
    if [[ "$package" == terminal-ui ]]; then
      undo_homebrew_manifest "$state_dir" terminal-ui-formulae formula
      rm -f "$state_dir/terminal-ui-formulae.tmp"
      continue
    fi
    if [[ -f "$state_dir/$package-formulae" ]]; then
      local formulae=()
      brew_bin=$(command -v brew || true)
      if [[ -z "$brew_bin" ]]; then
        if [[ -x /opt/homebrew/bin/brew ]]; then
          brew_bin=/opt/homebrew/bin/brew
        elif [[ -x /usr/local/bin/brew ]]; then
          brew_bin=/usr/local/bin/brew
        else
          echo "Homebrew is unavailable; cannot undo the recorded $package installation." >&2
          return 1
        fi
      fi
      installed=$("$brew_bin" list --formula)
      while IFS= read -r formula; do
        [[ -n "$formula" ]] || continue
        if [[ $'\n'"$installed"$'\n' == *$'\n'"$formula"$'\n'* ]]; then
          formulae+=("$formula")
        fi
      done < "$state_dir/$package-formulae"
      if [[ ${#formulae[@]} -gt 0 ]]; then
        echo "Removing $package formulae installed by provisioning: ${formulae[*]}"
        HOMEBREW_NO_AUTOREMOVE=1 "$brew_bin" uninstall --formula --force "${formulae[@]}"
      fi
      rm "$state_dir/$package-formulae"
    fi
    rm -f "$state_dir/$package-formulae.tmp"
  done

  if [[ -f "$state_dir/homebrew-installed" ]]; then
    if [[ "$retaining_onepassword" == true ]]; then
      echo 'Keeping Homebrew and its rollback marker so retained 1Password packages remain managed.'
    else
    echo 'Uninstalling Homebrew installed by provisioning...'
    uninstaller=$(/usr/bin/curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/uninstall.sh)
    NONINTERACTIVE=1 /bin/bash -c "$uninstaller"
    rm "$state_dir/homebrew-installed"
    fi
  fi

  if [[ -f "$state_dir/repo-created" ]]; then
    echo "Removing provisioning-created checkout (including any local changes): $repo_dir"
    # The running script may live inside this checkout. main is already parsed.
    cd "$HOME"
    rm -rf -- "$repo_dir"
    rm "$state_dir/repo-created"
  fi

  if [[ -f "$state_dir/code-created" ]]; then
    if [[ -d "$HOME/code" ]]; then
      # Preserve unrelated files; retry on the next undo after they are moved.
      if ! rmdir "$HOME/code"; then
        echo '~/code was retained because it is not empty. Its undo marker is preserved.' >&2
        return 1
      fi
    fi
    rm "$state_dir/code-created"
  fi

  if [[ "$keep_gh_preferences" == true && -d "$state_dir/gh-login" ]]; then
    echo 'GitHub login retained. Its rollback snapshot is kept for a later full undo.'
  fi
  if [[ "$retaining_onepassword" == true ]]; then
    echo '1Password apps, mas and their rollback records retained for a later full undo.'
  fi
  if [[ "$retaining_onepassword" == false && !( "$keep_gh_preferences" == true && -d "$state_dir/gh-login" ) ]]; then
    rmdir "$state_dir"
  fi
  # The Finder instruction dialog only copies a URL. Manual Finder favorites
  # and SMB connections remain user-managed; no clipboard contents are saved.
  # The Time Machine inventory/menu is read-only and its selection is in memory.
  echo 'Recorded provisioning changes have been undone.'
}

main "$@"
