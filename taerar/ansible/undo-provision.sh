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
  local keep_gh_login=false argument
  for argument in "$@"; do
    case "$argument" in
      --keep-gh-login) keep_gh_login=true ;;
      --help|-h) echo "Usage: $0 [--keep-gh-login]"; return 0 ;;
      *) echo "Usage: $0 [--keep-gh-login]" >&2; return 2 ;;
    esac
  done
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

  # This stage was installed last. Remove casks before their formula dependencies.
  undo_homebrew_manifest "$state_dir" make-dependencies-casks cask
  undo_homebrew_manifest "$state_dir" make-dependencies-formulae formula
  rm -f "$state_dir/make-dependencies-casks.tmp" "$state_dir/make-dependencies-formulae.tmp"

  # Restore dotfiles before restoring any pre-login Git configuration.
  if [[ -d "$state_dir/dotfiles" ]]; then
    local snapshot name
    for snapshot in "$state_dir"/dotfiles/file-*; do
      [[ -d "$snapshot" ]] || continue
      name=${snapshot##*/file-}
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

  # Undo browser login while gh is still installed. Credentials remain in the
  # macOS keychain/config directory when --keep-gh-login is selected.
  if [[ -d "$state_dir/gh-login" && "$keep_gh_login" == false ]]; then
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

  # Reverse formula installations in order, preserving pre-existing packages.
  local package brew_bin formula installed
  for package in gh onepassword ansible; do
    if [[ "$package" == onepassword ]]; then
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
    echo 'Uninstalling Homebrew installed by provisioning...'
    uninstaller=$(/usr/bin/curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/uninstall.sh)
    NONINTERACTIVE=1 /bin/bash -c "$uninstaller"
    rm "$state_dir/homebrew-installed"
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

  if [[ "$keep_gh_login" == true && -d "$state_dir/gh-login" ]]; then
    echo 'GitHub login retained. Its rollback snapshot is kept for a later full undo.'
  else
    rmdir "$state_dir"
  fi
  echo 'Recorded provisioning changes have been undone.'
}

main "$@"
