# homelab

Scripts, configuration and runbooks for my home lab: a TrueNAS NAS, a Proxmox
cluster, a K3s/Rancher Kubernetes cluster, an OVH server and a few Macs.

## Layout

| Directory | What's there |
| --- | --- |
| [`rancher/`](rancher/README.md) | K3s + Rancher cluster: [Ansible bootstrap](rancher/ansible/README.md), [Fleet GitOps bundles](rancher/fleet/README.md), [Longhorn storage](rancher/longhorn/README-0-longhorn-on-single-node.md), [`helm-upgrade-apps.sh`](rancher/helm-upgrade-apps.sh), [docs](rancher/doc/) |
| [`nas/`](nas/) | TrueNAS host: [Ansible provisioning](nas/ansible/), [Zabbix agent](nas/zabbix/README.md), [Time Machine](nas/time-machine/), Plex and ZFS helpers |
| [`proxmox/`](proxmox/) | Proxmox VE: [Traefik](proxmox/traefik/README.md), [Emby](proxmox/emby/README.md), [migration monitoring](proxmox/PROXMOX-MIGRATION-MONITORING.md) |
| [`taerar/`](taerar/README.md) | macOS provisioning ([Ansible](taerar/ansible/README.md)) and photo helpers |
| [`ovh/`](ovh/) | OVH Docker host: [resource monitor](ovh/docker-resource-monitor/README.md), [dool](https://github.com/scottchiefbaker/dool), Docker maintenance |
| [`transmission/`](transmission/) | Transmission daemon behind NordVPN, with [Traefik](transmission/traefik/README.md) |
| [`home-assistant/`](home-assistant/), [`crashplan/`](crashplan/), [`kibana/`](kibana/) | Service helpers and Zabbix checks |
| [`zabbix/`](zabbix/) | Shared [Zabbix agent 2](https://www.zabbix.com/documentation/current/en/manual/concepts/agent2) config and host exports; per-service checks live in `*/zabbix/` |
| [`utilities/`](utilities/README.md) | Commit helpers (Codex/Claude) and dotfile sync |
| [`ansible/`](ansible/) | Shared Ansible roles (e.g. [prezto](ansible/roles/prezto/README.md)) |
| [`sie-pomaga/`](sie-pomaga/), [`others/`](others/), [`old/`](old/) | Side projects and archived scripts |

Top-level `is-*.sh` and `number-of-days-since-*.sh` scripts are monitoring
checks. Dotfiles (`.zshrc`, `.vimrc`, `.p10k.zsh`, …) are kept in the root.

## Getting started

```bash
git clone --recurse-submodules git@github.com:bnowakow/homelab.git
```

```bash
make install-git-hooks
```

Installs the [pre-commit hook](.githooks/pre-commit), which scans for secrets
with [ggshield](https://github.com/GitGuardian/ggshield). Run `make` to list
other targets (commit helpers, dotfiles, Rancher cron).

New Mac? See [taerar/README.md](taerar/README.md).

## Agents

[AGENTS.md](AGENTS.md) holds instructions for coding agents;
[`.claude/skills/`](.claude/skills/) has project skills such as
[truecharts-fleet-install](.claude/skills/truecharts-fleet-install/SKILL.md).

## License

[GPL-3.0](LICENSE)
