# Shared Prezto installation

Used by the Linux Proxmox playbook and the local macOS provisioning playbook.
Paths default to `~` and are expanded on the target as the user running the role.
The role clones Prezto recursively, including its bundled Powerlevel10k theme,
and links only missing runcom files. Existing files and symlinks are preserved.

Linux installs Zsh and selects sup's login shell in its wrapper playbook. It
retains the existing update and legacy PATH-block cleanup behavior. macOS uses
its built-in `/bin/zsh`, leaves the login shell unchanged, and preserves existing
Prezto checkouts without updating them. An incomplete existing checkout fails
with instructions to initialize its submodules.

The repository's `.zpreztorc` selects Powerlevel10k and `.zshrc` loads `.p10k.zsh`.
Those files stay managed by the existing dotfile mechanisms rather than being
duplicated in this role. Choose a suitable font in your terminal if needed.

Variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `prezto_home` | `~` | Target user's configuration directory |
| `prezto_repository` | Official Prezto HTTPS repository | Clone source |
| `prezto_version` | `master` | Git revision |
| `prezto_update_existing` | `true` | Allow checkout/submodule updates |
| `prezto_remove_legacy_path_block` | `false` | Remove the old Ansible block from Prezto's tracked zshenv |
| `prezto_rollback_dir` | Empty | Optional intent records for a new checkout and runcom links |

The macOS wrapper records intent before changes. Undo removes newly created
links and the checkout, preserving pre-existing resources. It stops rather than
deleting a changed link or a checkout with local changes.
