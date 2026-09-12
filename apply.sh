#!/usr/bin/env bash
# Reapplies this machine's Omarchy customizations to a fresh Omarchy install.
#
# Usage:
#   ./apply.sh                 launch the setup wizard GUI (interactive)
#   ./apply.sh --cli           interactive yes/no per category (terminal)
#   ./apply.sh --all           applies every category, no prompts
#   ./apply.sh --only a,b      applies only the named categories
#   ./apply.sh --myconfig <f>  applies from a JSON config file (see GUI)
#   ./apply.sh --list          lists category names and exits
#
# Categories (always applied in this order, regardless of selection order,
# since later ones depend on earlier ones - e.g. bar config references
# plugin IDs that must already be installed):
#   packages     remove default packages this machine doesn't have
#   install      install this machine's added packages (official + AUR)
#   webapps      remove Omarchy web app launcher entries
#   plugins      install third-party omarchy shell plugins
#   theme        install + activate custom themes
#   gloview      install the GloView Hyprland overview plugin (AUR)
#   hyprland     Hyprland config overrides (excludes keybindings)
#   keybindings  ~/.config/hypr/bindings.lua
#   dotfiles     .bashrc, git config, starship, btop, mise
#   barconfig    ~/.config/omarchy/shell.json (bar layout/plugin state)
#   gpu          install Ollama (CUDA/NVIDIA) + a small GPU model
#   hermes       install Hermes Agent (CLI + TUI) + optional desktop / web /
#                AI-provider (default OpenRouter) + model selection
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
FILES_DIR="$SCRIPT_DIR/files"

CATEGORIES=(packages install webapps plugins theme gloview hyprland keybindings dotfiles barconfig gpu hermes)

info() { printf '\033[36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$1" >&2; }

describe() {
  case "$1" in
    packages)    echo "Remove default packages you removed (14 packages)" ;;
    install)     echo "Install added packages (11 official + 11 AUR)" ;;
    webapps)     echo "Remove Omarchy web app launcher entries (Discord, YouTube, WhatsApp, etc.)" ;;
    plugins)     echo "Install third-party shell plugins (15 plugins)" ;;
    theme)       echo "Install and activate your custom themes" ;;
    gloview)     echo "Install the GloView Hyprland overview plugin (AUR: gloview-git)" ;;
    hyprland)    echo "Apply Hyprland config overrides (input/looknfeel/autostart/monitors/etc.)" ;;
    keybindings) echo "Apply custom keybindings (bindings.lua)" ;;
    dotfiles)    echo "Apply dotfiles (.bashrc, git config, starship, btop, mise)" ;;
    barconfig)   echo "Apply bar/plugin layout (shell.json)" ;;
    gpu)         echo "Install Ollama (CUDA/NVIDIA) + a small GPU model" ;;
    hermes)      echo "Install Hermes Agent (CLI + TUI) + optional desktop/web/provider/model" ;;
  esac
}

contains() {
  local needle="$1"; shift
  local x
  for x in "$@"; do [[ "$x" == "$needle" ]] && return 0; done
  return 1
}

# backup_and_copy_file <path-relative-to-HOME>
backup_and_copy_file() {
  local rel="$1"
  local src="$FILES_DIR/$rel"
  local dest="$HOME/$rel"
  mkdir -p "$(dirname "$dest")"
  if [[ -e "$dest" ]]; then
    cp -a "$dest" "$dest.bak.$(date +%s)"
  fi
  cp -a "$src" "$dest"
  echo "  wrote $dest"
}

# backup_and_copy_dir <path-relative-to-HOME>
backup_and_copy_dir() {
  local rel="$1"
  local src="${FILES_DIR:?}/$rel"
  local dest="$HOME/$rel"
  mkdir -p "$(dirname "$dest")"
  if [[ -e "$dest" ]]; then
    mv "$dest" "$dest.bak.$(date +%s)"
  fi
  cp -a "$src" "$dest"
  echo "  wrote $dest/"
}

# ensure_flag_file <dest-file> <flag-line> — make sure <flag-line> is present in
# the flags file, creating it if missing. Preserves any existing flags; backs up
# the file once before first modifying it. Idempotent (no-op if flag present).
# Used for Electron --password-store flags read on every app launch.
ensure_flag_file() {
  local dest="$1" flag="$2"
  mkdir -p "$(dirname "$dest")"
  if [[ ! -e "$dest" ]]; then
    printf '%s\n' "$flag" > "$dest"
    echo "  wrote $dest"
    return
  fi
  if grep -qxF -- "$flag" "$dest"; then
    echo "  $dest already has flag, skipping"
    return
  fi
  cp -a "$dest" "$dest.bak.$(date +%s)"
  printf '%s\n' "$flag" >> "$dest"
  echo "  added flag to $dest (backed up original)"
}

reload_hyprland() {
  if command -v hyprctl >/dev/null 2>&1 && hyprctl monitors >/dev/null 2>&1; then
    hyprctl reload
    hyprctl configerrors
  fi
}

# ---------------------------------------------------------------- categories --

apply_packages() {
  info "Removing packages to match this setup"
  local pkg
  local removed_source="${CONFIG_REMOVED_FILE:-$SCRIPT_DIR/packages-removed.txt}"
  while IFS= read -r pkg; do
    [[ -z "$pkg" || "$pkg" == \#* ]] && continue
    if pacman -Qq "$pkg" &>/dev/null; then
      sudo pacman -R --noconfirm "$pkg" || warn "failed to remove $pkg"
    else
      echo "  $pkg already absent, skipping"
    fi
  done < "$removed_source"
  echo "Leftover now-unused dependencies (if any) can be reviewed with: pacman -Qtdq"
}

apply_webapps() {
  info "Removing Omarchy web app launcher entries"
  local desktop_dir="$HOME/.local/share/applications"
  local icon_dir="$HOME/.local/share/icons/hicolor/256x256/apps"
  local old_icon_dir="$HOME/.local/share/applications/icons"
  if [[ ! -d $desktop_dir ]]; then
    echo "  no $desktop_dir directory, nothing to remove"
    return
  fi
  local count=0 file name icon_name
  while IFS= read -r -d '' file; do
    if grep -q '^Exec=.*\(omarchy-launch-webapp\|omarchy-webapp-handler\).*' "$file"; then
      name="$(basename "${file%.desktop}")"
      icon_name="$(printf '%s\n' "$name" | tr '[:upper:]' '[:lower:]' | sed 's/[^[:alnum:]]\+/-/g; s/^-//; s/-$//')"
      rm -f "$file" "$icon_dir/$icon_name.png" "$icon_dir/$name.png" "$old_icon_dir/$name.png"
      echo "  removed $name"
      count=$((count + 1))
    fi
  done < <(find "$desktop_dir" -name '*.desktop' -print0 2>/dev/null)
  if (( count == 0 )); then
    echo "  no web app launchers found, nothing to remove"
  else
    echo "  removed $count web app launcher(s)"
    if command -v update-desktop-database >/dev/null 2>&1; then
      update-desktop-database "$desktop_dir" &>/dev/null || true
    fi
  fi
}

apply_install() {
  info "Installing added packages (official repos + AUR)"
  local pkg official=() aur=()
  local added_source="${CONFIG_ADDED_FILE:-$SCRIPT_DIR/packages-added.txt}"
  local aur_source="${CONFIG_AUR_FILE:-$SCRIPT_DIR/packages-aur.txt}"
  if [[ -f "$added_source" ]]; then
    while IFS= read -r pkg; do
      [[ -z "$pkg" || "$pkg" == \#* ]] && continue
      official+=("$pkg")
    done < "$added_source"
  fi
  if [[ -f "$aur_source" ]]; then
    while IFS= read -r pkg; do
      [[ -z "$pkg" || "$pkg" == \#* ]] && continue
      aur+=("$pkg")
    done < "$aur_source"
  fi
  if ((${#official[@]})); then
    echo "  official repos: ${official[*]}"
    sudo pacman -S --needed --noconfirm "${official[@]}" || warn "pacman install reported failures"
  fi
  if ((${#aur[@]})); then
    if ! command -v yay >/dev/null 2>&1; then
      warn "yay AUR helper not found - skipping AUR installs: ${aur[*]}"
    else
      echo "  AUR: ${aur[*]}"
      yay -S --needed --noconfirm --removemake "${aur[@]}" || warn "yay install reported failures"
    fi
  fi
  # Docker: add the current user to the docker group and enable+start the
  # service so non-root `docker` works. Idempotent: usermod -aG is additive,
  # and systemctl enable --now is a no-op once enabled+active.
  if pacman -Qq docker >/dev/null 2>&1; then
    local user="$(id -un)"
    if ! id -nG "$user" | tr ' ' '\n' | grep -qx docker; then
      sudo usermod -aG docker "$user" || warn "failed to add $user to docker group"
      echo "  added $user to docker group (log out/in for it to take effect)"
    else
      echo "  $user already in docker group"
    fi
    sudo systemctl enable --now docker || warn "failed to enable+start docker service"
  fi
  # Enable+start the systemd service for packages that need a daemon to
  # function. Idempotent: systemctl enable --now is a no-op once active, and
  # each entry is guarded on its owning package being installed.
  # "<service>:<package>" per line.
  local svc pkg_for_svc
  for entry in "tailscaled:tailscale" "displaylink:displaylink"; do
    svc="${entry%%:*}"
    pkg_for_svc="${entry#*:}"
    if pacman -Qq "$pkg_for_svc" >/dev/null 2>&1 && systemctl list-unit-files "$svc.service" >/dev/null 2>&1; then
      sudo systemctl enable --now "$svc" || warn "failed to enable+start $svc"
    fi
  done
  # Electron app keyring fix (see apply_electron_keyring_fix below).
  apply_electron_keyring_fix
}

# apply_electron_keyring_fix — write Electron --password-store=gnome-libsecret
# flags for VS Code and Element so their safeStorage stores secrets in the GNOME
# keyring via libsecret. On Hyprland, XDG_CURRENT_DESKTOP is not recognized by
# Chromium's password-store auto-detection, so without this both apps report
# "An os keyring couldn't be identified for storing the encryption related data".
# The flag is written only for an app that is in the selected AUR install set
# (so toggling the app off on the wizard's Packages page skips its flag).
# Gated by CONFIG_ELECTRON_KEYRING_FIX (default true; set via --myconfig features).
apply_electron_keyring_fix() {
  if [[ "${CONFIG_ELECTRON_KEYRING_FIX:-true}" == "false" ]]; then
    echo "  electron keyring fix disabled in config, skipping"
    return
  fi
  local aur_source="${CONFIG_AUR_FILE:-$SCRIPT_DIR/packages-aur.txt}"
  [[ -f "$aur_source" ]] || return
  local flag="--password-store=gnome-libsecret"
  # VS Code: /usr/bin/code reads ~/.config/code-flags.conf on every launch.
  if grep -qxF -- "visual-studio-code-bin" "$aur_source" 2>/dev/null; then
    ensure_flag_file "$HOME/.config/code-flags.conf" "$flag"
  fi
  # Element: /usr/bin/electron43 reads <name>-flags.conf (then electron-flags.conf)
  # on every launch; element-desktop launches via electron43.
  if grep -qxF -- "element-desktop" "$aur_source" 2>/dev/null; then
    ensure_flag_file "$HOME/.config/electron43-flags.conf" "$flag"
  fi
}

apply_plugins() {
  info "Installing third-party shell plugins"
  if ! command -v omarchy >/dev/null 2>&1; then
    warn "omarchy CLI not found - skipping plugin install"
    return
  fi
  local id url enable
  while IFS=$'\t' read -r id url enable; do
    [[ -z "$id" || "$id" == \#* ]] && continue
    if [[ -d "$HOME/.config/omarchy/plugins/$id" ]]; then
      echo "  $id already installed, skipping"
      continue
    fi
    if [[ "$enable" == "true" ]]; then
      omarchy plugin add "$url" --enable --yes || warn "failed to install $id"
    else
      omarchy plugin add "$url" --yes || warn "failed to install $id"
    fi
  done < "$SCRIPT_DIR/plugins.txt"
}

apply_theme() {
  info "Installing custom themes"
  local t
  for t in omarchy-wallpaper pexels-jplenio-2080960 pexels-simon73-1323550; do
    backup_and_copy_dir ".config/omarchy/themes/$t"
  done
  if command -v omarchy >/dev/null 2>&1; then
    omarchy theme set omarchy-wallpaper || warn "omarchy theme set failed - run it manually"
  fi
}

apply_gloview() {
  info "Installing GloView Hyprland plugin (AUR: gloview-git)"
  if [[ -f /usr/lib/gloview.so ]]; then
    echo "  gloview already installed (/usr/lib/gloview.so present), skipping"
    return
  fi
  if ! command -v yay >/dev/null 2>&1; then
    warn "yay AUR helper not found - skipping gloview install (run manually: yay -S gloview-git)"
    return
  fi
  yay -S gloview-git --noconfirm --removemake || warn "failed to install gloview-git"
  if [[ -f /usr/lib/gloview.so ]]; then
    echo "  gloview installed at /usr/lib/gloview.so"
    reload_hyprland
  else
    warn "gloview-git install reported success but /usr/lib/gloview.so is missing"
  fi
}

apply_hyprland() {
  info "Applying Hyprland config overrides (excludes keybindings - see that category)"
  local f
  for f in hyprland.lua input.lua looknfeel.lua autostart.lua hyprsunset.conf xdph.conf .luarc.json; do
    backup_and_copy_file ".config/hypr/$f"
  done
  warn "monitors.lua names specific display outputs - verify with 'hyprctl monitors all' after applying"
  backup_and_copy_file ".config/hypr/monitors.lua"
  reload_hyprland
}

apply_keybindings() {
  info "Applying keybindings"
  # SUPER+W confirm-close helper (referenced by bindings.lua). Copied first so
  # the binding resolves on first press; chmod in case git lost the exec bit.
  # Skipped when CONFIG_CONFIRM_CLOSE=false (set via --myconfig features).
  if [[ "${CONFIG_CONFIRM_CLOSE:-true}" != "false" ]]; then
    backup_and_copy_file ".local/bin/omarchy-confirm-close"
    chmod +x "$HOME/.local/bin/omarchy-confirm-close"
  else
    echo "  confirm-close disabled in config, skipping omarchy-confirm-close"
  fi
  # omarchy-plugin-vet: standalone plugin security vetting helper. Not
  # feature-gated; chmod in case git lost the exec bit on clone.
  backup_and_copy_file ".local/bin/omarchy-plugin-vet"
  chmod +x "$HOME/.local/bin/omarchy-plugin-vet"
  # omarchy-tdl-workspace: SUPER+H launcher (VSCode + foot/tmux with btop +
  # tdl a). Copied before bindings.lua so the binding resolves on first press;
  # chmod in case git lost the exec bit.
  backup_and_copy_file ".local/bin/omarchy-tdl-workspace"
  chmod +x "$HOME/.local/bin/omarchy-tdl-workspace"
  backup_and_copy_file ".config/hypr/bindings.lua"
  reload_hyprland
}

apply_dotfiles() {
  info "Applying dotfiles"
  backup_and_copy_file ".bashrc"
  backup_and_copy_file ".config/git/config"
  backup_and_copy_file ".config/starship.toml"
  backup_and_copy_file ".config/btop/btop.conf"
  backup_and_copy_file ".config/mise/config.toml"
  echo "Note: .bashrc sources /usr/share/aur-scan/integration.bash if present (aur-scanner package, optional)."
}

apply_barconfig() {
  info "Applying bar/plugin layout (shell.json)"
  backup_and_copy_file ".config/omarchy/shell.json"
  if command -v omarchy >/dev/null 2>&1; then
    omarchy restart shell || warn "omarchy restart shell failed - restart it manually"
  fi
}

apply_gpu() {
  info "Installing Ollama (CUDA/NVIDIA) + GPU models"
  # Skipped entirely when CONFIG_OLLAMA_INSTALL=false (set via --myconfig).
  if [[ "${CONFIG_OLLAMA_INSTALL:-true}" == "false" ]]; then
    echo "  ollama disabled in config, skipping GPU setup"
    return
  fi
  # This targets the NVIDIA Quadro T1000 (4GB VRAM, Turing / compute 7.5).
  # The official installer ships a prebuilt CUDA-enabled binary that bundles
  # its own CUDA libraries and only needs the NVIDIA driver, so we don't pull
  # the ~4GB CUDA toolkit via pacman. Idempotent: skips if ollama is present.
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    warn "nvidia-smi not found - no NVIDIA GPU/driver detected, skipping GPU setup"
    return
  fi
  if command -v ollama >/dev/null 2>&1; then
    echo "  ollama already installed ($(ollama --version 2>/dev/null || echo present)), skipping"
  else
    curl -fsSL https://ollama.com/install.sh | sudo sh || warn "ollama installer reported a failure"
  fi
  # enable+start is a no-op once active; safe to always run.
  sudo systemctl enable --now ollama || warn "failed to enable+start ollama service"
  # Pull models. Default to qwen2.5:3b; --myconfig can override via
  # CONFIG_OLLAMA_MODELS (comma-separated tags). `ollama pull` is idempotent.
  local models="${CONFIG_OLLAMA_MODELS:-qwen2.5:3b}"
  local model
  IFS=',' read -ra model_list <<< "$models"
  for model in "${model_list[@]}"; do
    [[ -z "$model" ]] && continue
    ollama pull "$model" || warn "failed to pull $model (run 'ollama pull $model' manually)"
  done
}

# ------------------------------------------------------------------ hermes --
# Hermes Agent helpers (https://hermes-agent.nousresearch.com). The official
# installer (curl ... | bash) clones the repo into ~/.hermes/hermes-agent,
# builds a Python 3.11 venv + the Ink TUI, and links `hermes` into
# ~/.local/bin. These functions install it and wire up the optional pieces the
# installer does not own on an Omarchy box: the AUR desktop app, a systemd
# user unit for the web dashboard, and provider/API-key/model configuration.

# hermes_env_set KEY VALUE — write/update KEY=VALUE in ~/.hermes/.env (0600).
hermes_env_set() {
  local key="$1" val="$2" env_file="$HOME/.hermes/.env" esc
  mkdir -p "$HOME/.hermes"
  [[ -f "$env_file" ]] || { touch "$env_file"; chmod 600 "$env_file"; }
  esc=$(printf '%s' "$val" | sed -e 's/[\/&|]/\\&/g')
  if grep -qE "^${key}=" "$env_file"; then
    sed -i -E "s|^${key}=.*|${key}=${esc}|" "$env_file"
  else
    printf '%s=%s\n' "$key" "$val" >> "$env_file"
  fi
}

# hermes_install_web_service PORT — write + enable+start hermes-studio.service
# (dashboard on 127.0.0.1:PORT). Idempotent.
hermes_install_web_service() {
  local port="$1" unit="$HOME/.config/systemd/user/hermes-studio.service"
  echo "  enabling hermes-studio.service (dashboard on 127.0.0.1:$port)"
  mkdir -p "$(dirname "$unit")"
  cat > "$unit" <<EOF
[Unit]
Description=Hermes Studio (hermes dashboard web UI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$HOME
Environment=PATH=$HOME/.local/bin:$HOME/.local/share/mise/shims:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
ExecStart=$HOME/.local/bin/hermes dashboard --no-open --port $port --host 127.0.0.1
ExecStop=$HOME/.local/bin/hermes dashboard --stop
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now hermes-studio.service || warn "failed to enable+start hermes-studio.service"
}

# hermes_configure_provider — pick a provider (default OpenRouter), write the
# API key to ~/.hermes/.env, and set model.provider/model.base_url in
# config.yaml. Providers outside the curated list delegate to `hermes setup
# model`, which knows the full catalog and per-provider base URLs.
hermes_configure_provider() {
  local provider="${CONFIG_HERMES_PROVIDER:-}" api_key="${CONFIG_HERMES_API_KEY:-}" base_url="${CONFIG_HERMES_BASE_URL:-}"
  local keyvar choice
  [[ "$provider" == "true" || -z "$provider" ]] && provider=openrouter
  if [[ "$interactive" == "true" ]]; then
    cat <<EOF
  Common providers:
    1) OpenRouter   (default; 300+ models, pay-per-use)
    2) OpenAI API
    3) Anthropic
    4) Google AI Studio (Gemini)
    5) DeepSeek
    6) xAI (Grok)
    7) Other / use the interactive wizard (full catalog)
EOF
    printf "  Choose a provider [1]: "
    read -r choice; choice="${choice:-1}"
    case "$choice" in
      1) provider=openrouter; base_url="https://openrouter.ai/api/v1" ;;
      2) provider=openai-api; base_url="https://api.openai.com/v1" ;;
      3) provider=anthropic;  base_url="https://api.anthropic.com" ;;
      4) provider=gemini;     base_url="https://generativelanguage.googleapis.com/v1beta" ;;
      5) provider=deepseek;   base_url="https://api.deepseek.com/v1" ;;
      6) provider=xai;        base_url="https://api.x.ai/v1" ;;
      7) provider=wizard ;;
      *) provider=openrouter; base_url="https://openrouter.ai/api/v1" ;;
    esac
  fi
  case "$provider" in
    openrouter|openai-api|anthropic|gemini|deepseek|xai) ;;
    *)
      echo "  launching 'hermes setup model' (interactive provider + model wizard)..."
      hermes setup model || warn "hermes setup model did not complete"
      return ;;
  esac
  case "$provider" in
    openrouter) keyvar=OPENROUTER_API_KEY ;;
    openai-api) keyvar=OPENAI_API_KEY ;;
    anthropic)  keyvar=ANTHROPIC_API_KEY ;;
    gemini)     keyvar=GEMINI_API_KEY ;;
    deepseek)   keyvar=DEEPSEEK_API_KEY ;;
    xai)        keyvar=XAI_API_KEY ;;
  esac
  if [[ "$interactive" == "true" && -z "$api_key" ]]; then
    printf "  Enter %s (input hidden, blank to skip): " "$keyvar"
    read -rs api_key; echo
  fi
  if [[ -z "$api_key" ]]; then
    warn "no API key provided — set $keyvar in ~/.hermes/.env or run 'hermes setup model' later"
  else
    hermes_env_set "$keyvar" "$api_key"
    echo "  wrote $keyvar to ~/.hermes/.env"
  fi
  hermes config set model.provider "$provider" >/dev/null 2>&1 || warn "failed to set model.provider"
  [[ -n "$base_url" ]] && hermes config set model.base_url "$base_url" >/dev/null 2>&1 || true
  echo "  provider set to '$provider'${base_url:+ ($base_url)}"
}

# hermes_select_model [PRESET] — set model.default directly from PRESET, or
# launch `hermes model` (interactive picker; --refresh re-fetches /v1/models so
# you can search and select). Skipped in non-interactive mode without a preset.
hermes_select_model() {
  local preset="${1:-}"
  if [[ -n "$preset" && "$preset" != "true" ]]; then
    hermes config set model.default "$preset" >/dev/null 2>&1 || warn "failed to set model.default"
    echo "  default model set to '$preset'"
    return
  fi
  if [[ "$interactive" != "true" ]]; then
    echo "  skipping model picker (non-interactive) — run 'hermes model' later to choose"
    return
  fi
  echo "  launching 'hermes model' (search/select; --refresh re-fetches /v1/models)..."
  hermes model --refresh || warn "hermes model picker did not complete"
}

apply_hermes() {
  info "Installing Hermes Agent (CLI + TUI)"
  if [[ "${CONFIG_HERMES_INSTALL:-true}" == "false" ]]; then
    echo "  hermes disabled in config, skipping"
    return
  fi
  # 1. Install the agent (CLI + TUI). --skip-setup defers provider/key/model
  #    configuration to the options below; --non-interactive avoids any prompt
  #    from the installer itself (system packages, wizard). Idempotent.
  export PATH="$HOME/.local/bin:$PATH"
  if command -v hermes >/dev/null 2>&1; then
    echo "  hermes already installed ($(hermes --version 2>/dev/null | head -1)), skipping"
  else
    echo "  running official installer (first run builds a venv + TUI; this can take several minutes)..."
    if ! curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup --non-interactive; then
      warn "hermes installer reported a failure"
      return
    fi
    if ! command -v hermes >/dev/null 2>&1; then
      warn "hermes command not found after install — ensure ~/.local/bin is on PATH and re-run"
      return
    fi
  fi
  # Sub-options honour --myconfig overrides (CONFIG_HERMES_*) and prompt in
  # interactive/--only mode; --all and --myconfig apply defaults without prompts.
  local interactive=false
  if [[ -t 0 ]] && [[ "${mode:-}" != "all" && "${mode:-}" != "myconfig" ]]; then
    interactive=true
  fi
  local reply dflt
  # 2. Desktop application (AUR: hermes-desktop, from the omarchy repo).
  local enable_desktop="${CONFIG_HERMES_DESKTOP:-false}"
  if [[ "$interactive" == "true" ]]; then
    dflt=n; [[ "$enable_desktop" == "true" ]] && dflt=y
    printf "Enable the Hermes desktop application? [y/N]: "
    read -r reply; reply="${reply:-$dflt}"
    [[ "$reply" =~ ^[Yy]$ ]] && enable_desktop=true || enable_desktop=false
  fi
  if [[ "$enable_desktop" == "true" ]]; then
    echo "  installing hermes-desktop (AUR)"
    if command -v yay >/dev/null 2>&1; then
      yay -S --needed --noconfirm --removemake hermes-desktop || warn "failed to install hermes-desktop"
    else
      warn "yay not found — install hermes-desktop manually: yay -S hermes-desktop"
    fi
  fi
  # 3. Web server interface (Hermes Studio dashboard, systemd user service).
  local enable_web="${CONFIG_HERMES_WEB:-false}"
  local web_port="${CONFIG_HERMES_WEB_PORT:-9119}"
  if [[ "$interactive" == "true" ]]; then
    dflt=n; [[ "$enable_web" == "true" ]] && dflt=y
    printf "Enable the Hermes web interface (dashboard on 127.0.0.1:%s)? [y/N]: " "$web_port"
    read -r reply; reply="${reply:-$dflt}"
    [[ "$reply" =~ ^[Yy]$ ]] && enable_web=true || enable_web=false
  fi
  [[ "$enable_web" == "true" ]] && hermes_install_web_service "$web_port"
  # 4. AI provider + API key (default OpenRouter).
  local do_provider=true
  [[ "${CONFIG_HERMES_PROVIDER:-}" == "false" ]] && do_provider=false
  if [[ "$interactive" == "true" ]]; then
    printf "Add an AI provider + API key (default OpenRouter)? [Y/n]: "
    read -r reply; [[ "${reply:-y}" =~ ^[Nn]$ ]] && do_provider=false
  fi
  [[ "$do_provider" == "true" ]] && hermes_configure_provider
  # 5. Select / search for a default model.
  local do_model=true
  if [[ "$interactive" == "true" ]]; then
    printf "Select / search for a default model now (interactive picker)? [Y/n]: "
    read -r reply; [[ "${reply:-y}" =~ ^[Nn]$ ]] && do_model=false
  fi
  [[ "$do_model" == "true" ]] && hermes_select_model "${CONFIG_HERMES_MODEL:-}"
  echo "  Hermes Agent ready — run 'hermes chat' for the TUI"
  [[ "$enable_web" == "true" ]] && echo "  Web UI: http://127.0.0.1:$web_port/  (systemctl --user status hermes-studio)"
}

run_category() {
  case "$1" in
    packages)    apply_packages ;;
    install)     apply_install ;;
    webapps)     apply_webapps ;;
    plugins)     apply_plugins ;;
    theme)       apply_theme ;;
    gloview)     apply_gloview ;;
    hyprland)    apply_hyprland ;;
    keybindings) apply_keybindings ;;
    dotfiles)    apply_dotfiles ;;
    barconfig)   apply_barconfig ;;
    gpu)         apply_gpu ;;
    hermes)      apply_hermes ;;
    *) warn "unknown category: $1" ;;
  esac
}

# Apply plugin deltas from a --myconfig JSON file.
apply_config_plugins() {
  local config="$1"
  if ! command -v omarchy >/dev/null 2>&1; then
    warn "omarchy CLI not found - skipping plugin operations from config"
    return
  fi
  local id url enable
  while IFS=$'\t' read -r id url enable; do
    [[ -z "$url" ]] && continue
    if [[ "$enable" == "true" ]]; then
      omarchy plugin add "$url" --enable --yes || warn "failed to add plugin: $url"
    else
      omarchy plugin add "$url" --yes || warn "failed to add plugin: $url"
    fi
  done < <(jq -r '.plugins.add[]? | [.id, .url, (.enable|tostring)] | @tsv' "$config")
  while IFS= read -r id; do
    [[ -z "$id" ]] && continue
    omarchy plugin enable "$id" || warn "failed to enable plugin: $id"
  done < <(jq -r '.plugins.enable[]?' "$config")
  while IFS= read -r id; do
    [[ -z "$id" ]] && continue
    omarchy plugin disable "$id" || warn "failed to disable plugin: $id"
  done < <(jq -r '.plugins.disable[]?' "$config")
}

# Apply extra packages from a --myconfig JSON file.
apply_config_packages() {
  local config="$1"
  local pkgs
  mapfile -t pkgs < <(jq -r '.packages.pacman[]?' "$config")
  if ((${#pkgs[@]})); then
    info "Installing extra pacman packages: ${pkgs[*]}"
    sudo pacman -S --needed --noconfirm "${pkgs[@]}" || warn "pacman install reported failures"
  fi
  mapfile -t pkgs < <(jq -r '.packages.aur[]?' "$config")
  if ((${#pkgs[@]})); then
    if ! command -v yay >/dev/null 2>&1; then
      warn "yay not found - skipping AUR packages: ${pkgs[*]}"
    else
      info "Installing extra AUR packages: ${pkgs[*]}"
      yay -S --needed --noconfirm --removemake "${pkgs[@]}" || warn "yay install reported failures"
    fi
  fi
}

# ------------------------------------------------------------------- CLI --

selected=()
mode="interactive"
config_file=""

case "${1:-}" in
  --all)
    mode="all"
    ;;
  --only)
    mode="only"
    IFS=',' read -r -a selected <<< "${2:-}"
    for c in "${selected[@]}"; do
      contains "$c" "${CATEGORIES[@]}" || { echo "Unknown category: $c" >&2; exit 1; }
    done
    ;;
  --cli)
    mode="interactive"
    ;;
  --myconfig)
    config_file="${2:-}"
    [[ -n "$config_file" ]] || { echo "--myconfig requires a file path" >&2; exit 1; }
    [[ -f "$config_file" ]] || { echo "Config file not found: $config_file" >&2; exit 1; }
    jq -e . "$config_file" >/dev/null 2>&1 || { echo "Invalid JSON: $config_file" >&2; exit 1; }
    mode="myconfig"
    ;;
  --list)
    for c in "${CATEGORIES[@]}"; do printf '%-12s %s\n' "$c" "$(describe "$c")"; done
    exit 0
    ;;
  -h|--help)
    sed -n '2,27p' "$0"
    exit 0
    ;;
  "")
    # No args: launch the GUI wizard if we have a display + python3.
    # Falls back to the interactive terminal prompt otherwise.
    if [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && command -v python3 >/dev/null 2>&1; then
      "$SCRIPT_DIR/gui/run.sh"
      exit 0
    fi
    ;;
  *)
    echo "Unknown argument: $1" >&2
    echo "Run with --help for usage." >&2
    exit 1
    ;;
esac

if [[ "$mode" == "myconfig" ]]; then
  info "Applying config from $config_file"
  # If configured_packages is present, extract the toggled-on lists to temp
  # files so apply_packages/apply_install use those instead of the text files.
  # This honours per-package opt-outs from the wizard.
  if jq -e '.configured_packages' "$config_file" >/dev/null 2>&1; then
    tmpdir="$(mktemp -d)"
    trap 'rm -rf "$tmpdir"' EXIT
    jq -r '.configured_packages.added[]?'    "$config_file" > "$tmpdir/added.txt"
    jq -r '.configured_packages.aur[]?'      "$config_file" > "$tmpdir/aur.txt"
    jq -r '.configured_packages.removed[]?'  "$config_file" > "$tmpdir/removed.txt"
    export CONFIG_ADDED_FILE="$tmpdir/added.txt"
    export CONFIG_AUR_FILE="$tmpdir/aur.txt"
    export CONFIG_REMOVED_FILE="$tmpdir/removed.txt"
  fi
  # Feature toggles (features object) + ollama model list.
  if jq -e '.features' "$config_file" >/dev/null 2>&1; then
    # NOTE: jq's `//` falls through on `false` as well as null, so
    # `.x // true` returns "true" even when a toggle is explicitly false. Use a
    # null-only fallback so an OFF toggle actually propagates as "false".
    export CONFIG_CONFIRM_CLOSE="$(jq -r 'if .features.confirm_close == null then "true" else (.features.confirm_close|tostring) end' "$config_file")"
    export CONFIG_OLLAMA_INSTALL="$(jq -r 'if .features.ollama_install == null then "true" else (.features.ollama_install|tostring) end' "$config_file")"
    export CONFIG_ELECTRON_KEYRING_FIX="$(jq -r 'if .features.electron_keyring_fix == null then "true" else (.features.electron_keyring_fix|tostring) end' "$config_file")"
  fi
  if jq -e '.ollama_models' "$config_file" >/dev/null 2>&1; then
    export CONFIG_OLLAMA_MODELS="$(jq -r '.ollama_models | join(",")' "$config_file")"
  fi
  # Hermes Agent toggles (.hermes object). Defaults: install on, desktop/web
  # off, provider openrouter, port 9119. provider/api_key/base_url/model are
  # empty unless set — apply_hermes fills the OpenRouter defaults at runtime.
  if jq -e '.hermes' "$config_file" >/dev/null 2>&1; then
    export CONFIG_HERMES_INSTALL="$(jq -r '.hermes.install // true' "$config_file")"
    export CONFIG_HERMES_DESKTOP="$(jq -r '.hermes.desktop // false' "$config_file")"
    export CONFIG_HERMES_WEB="$(jq -r '.hermes.web // false' "$config_file")"
    export CONFIG_HERMES_WEB_PORT="$(jq -r '.hermes.web_port // 9119' "$config_file")"
    export CONFIG_HERMES_PROVIDER="$(jq -r '.hermes.provider // ""' "$config_file")"
    export CONFIG_HERMES_API_KEY="$(jq -r '.hermes.api_key // ""' "$config_file")"
    export CONFIG_HERMES_BASE_URL="$(jq -r '.hermes.base_url // ""' "$config_file")"
    export CONFIG_HERMES_MODEL="$(jq -r '.hermes.model // ""' "$config_file")"
  fi
  # Read selected categories from JSON, run them in fixed order.
  mapfile -t selected < <(jq -r '.categories[]?' "$config_file")
  for c in "${selected[@]}"; do
    contains "$c" "${CATEGORIES[@]}" || { echo "Unknown category in config: $c" >&2; exit 1; }
  done
  for c in "${CATEGORIES[@]}"; do
    if contains "$c" "${selected[@]}"; then
      run_category "$c"
      echo
    fi
  done
  apply_config_plugins "$config_file"
  echo
  apply_config_packages "$config_file"
  echo
  info "Done."
  exit 0
fi

if [[ "$mode" == "all" ]]; then
  selected=("${CATEGORIES[@]}")
elif [[ "$mode" == "interactive" ]]; then
  echo "Select which customizations to apply (Enter = yes)."
  echo
  for c in "${CATEGORIES[@]}"; do
    read -r -p "$(describe "$c") [Y/n]: " reply
    reply="${reply:-y}"
    [[ "$reply" =~ ^[Yy] ]] && selected+=("$c")
  done
  echo
fi

if [[ ${#selected[@]} -eq 0 ]]; then
  echo "Nothing selected - exiting."
  exit 0
fi

for c in "${CATEGORIES[@]}"; do
  if contains "$c" "${selected[@]}"; then
    run_category "$c"
    echo
  fi
done

info "Done."
