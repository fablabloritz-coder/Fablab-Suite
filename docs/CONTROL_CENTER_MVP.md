# Control Center MVP - FabLab Suite

## Goal
Build a single operator application that can:
- install/update the suite locally
- install/update the suite on Ubuntu servers
- reuse current proven scripts and safety checks

This document defines a pragmatic path, without rewriting the full stack.

## Principles
- One source of truth for code updates: monorepo
- One operator entry point: Control Center UI
- One workflow engine for local and remote modes
- Keep existing helper logic as backend executor

## Existing assets to reuse
- `fabsuite-ubuntu.sh`: server lifecycle and data safety logic
- `fabsuite_ssh_gui.py`: remote connectivity and UX patterns
- Root `docker-compose.yml`: local full-suite run

## Target architecture

### Layer 1 - Workflow core
A Python module builds deterministic workflows as ordered steps.
- Inputs: target mode, operation, options
- Output: list of steps with commands + stop policy

### Layer 2 - Execution adapters
- Local adapter: execute shell commands on workstation
- Remote adapter: execute commands over SSH (through current paramiko layer)

### Layer 3 - UI
Single app with two modes:
- Local mode
- Ubuntu server mode

The UI does not own deployment logic. It only triggers workflows and renders logs.

## MVP scope

### Supported operations
- install
- update
- audit
- status
- logs

### Local mode behavior
- install/update: `docker compose up -d --build`
- status: `docker compose ps`
- logs: `docker compose logs`
- audit: local diagnostics preset (basic in MVP)

### Ubuntu mode behavior
Use helper actions as canonical backend:
- install: `repair-env`, `check-data-safety`, `install`
- update: `repair-env`, `check-data-safety`, `update`
- audit: `audit`, then post `check-data-safety`
- status/logs: helper direct actions

## Why this approach
- Lowest migration risk
- Preserves tested production behavior
- Enables one-click global updates from one UI
- Lets us refactor incrementally

## Data safety policy
For install/update on Ubuntu:
- always run pre-check safety
- block operation on risk code
- surface warning as first-class UI event

## Config model
Single profile object per environment:
- mode: local or ssh
- repo_url
- branch
- install_dir
- ports
- data paths
- ssh connection fields (if remote)

## Delivery phases

### Phase 1 (now)
- Create `deploy_core` scaffold module
- Encode workflows and adapters
- Keep existing GUI behavior unchanged

### Phase 2
- Plug GUI actions into `deploy_core` service
- Keep helper and existing logs

### Phase 3
- Add Local mode screens in same GUI
- Add profile manager and run history

### Phase 4
- Package as a standalone desktop app

## Success criteria
- One-click update works in both modes
- Same safety behavior in both modes
- No duplicated deployment logic between UI and scripts
- Clear logs for each step with exit status

## Current progress snapshot
- Done: `deploy_core` scaffold (models, workflows, service, adapters)
- Done: GUI bridge using `deploy_core` for `Status suite`, `Audit serveur`, `Install suite`, `Mettre à jour la suite`
- Done: GUI Local mode selector (`Serveur SSH` / `Local Docker`) with shared workflows for audit/install/update/status/logs
- Done: séparation UX des actions `communes` vs `SSH serveur uniquement` avec désactivation automatique selon mode
- Next: brancher les actions de scan/archivage/suppression dossier serveur sur un sous-module `server_maintenance` (même style que `deploy_core`)
