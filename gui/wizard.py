#!/usr/bin/env python3
"""Omarchy setup wizard — a GTK4 + libadwaita GUI for configuring apply.sh.

Builds a JSON config from user selections:
  - Opinionated category defaults (which apply.sh categories to run)
  - Plugin browser (enable/disable catalog plugins, add new ones by git URL)
  - Package search (pacman repos + optional AUR, with repo badges)
  - Review screen that saves the JSON and optionally applies it

Usage:
    wizard.py <setup-dir>                Launch the GUI wizard.
    wizard.py <setup-dir> --print-json   Print a default config JSON to stdout
                                         (no window; for headless validation).
"""

import sys
import os
import json
import re
import subprocess
import shutil
import shlex
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, GLib, Gdk


# ---------------------------------------------------------------------------
# Data layer — pure functions, no GUI dependencies.
# ---------------------------------------------------------------------------


def run_cmd(args, timeout=20):
    """Run a command, return (stdout, returncode). Errors are non-fatal."""
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.stdout, r.returncode
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "", 1


def parse_apply_list(setup_dir):
    """Parse `apply.sh --list` output → list of (name, description)."""
    out, _ = run_cmd(["bash", str(Path(setup_dir) / "apply.sh"), "--list"])
    cats = []
    for line in out.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        name = parts[0]
        desc = parts[1] if len(parts) > 1 else ""
        cats.append((name, desc))
    return cats


def read_packages_files(setup_dir):
    """Read the three package lists → dict with 'added', 'aur', 'removed'."""
    sd = Path(setup_dir)
    result = {"added": [], "aur": [], "removed": []}
    for key, fname in [("added", "packages-added.txt"),
                       ("aur", "packages-aur.txt"),
                       ("removed", "packages-removed.txt")]:
        path = sd / fname
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            result[key].append(line)
    return result


def read_plugins_txt(setup_dir):
    """Read plugins.txt → list of {id, url, enable}."""
    path = Path(setup_dir) / "plugins.txt"
    plugins = []
    if not path.exists():
        return plugins
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        plugins.append({
            "id": parts[0],
            "url": parts[1],
            "enable": parts[2].strip().lower() == "true" if len(parts) > 2 else False,
        })
    return plugins


def get_plugin_catalog():
    """Run omarchy-plugin-catalog → list of plugin manifest dicts."""
    omarchy_path = os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")
    catalog_bin = Path(omarchy_path) / "bin" / "omarchy-plugin-catalog"
    if not catalog_bin.exists():
        catalog_bin = Path(shutil.which("omarchy-plugin-catalog") or "")
    if not catalog_bin.exists():
        return []
    out, rc = run_cmd([str(catalog_bin)], timeout=15)
    if rc != 0 or not out.strip():
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def get_plugin_states():
    """Run `omarchy plugin list --json` → dict of id → state dict."""
    out, rc = run_cmd(["omarchy", "plugin", "list", "--json"], timeout=15)
    if rc != 0 or not out.strip():
        return {}
    try:
        items = json.loads(out)
    except json.JSONDecodeError:
        return {}
    return {item["id"]: item for item in items if "id" in item}


def get_theme_colors():
    """Read the active Omarchy theme's colors.toml → dict of name → hex string.

    Falls back to the omarchy-wallpaper palette if anything is missing.
    """
    fallback = {
        "accent": "#895cf2",
        "selection": "#241b38",
        "muted": "#616066",
        "background": "#0C0222",
        "dark_background": "#09021a",
        "lighter_background": "#241b38",
        "foreground": "#F9D740",
        "light_foreground": "#fadd5d",
        "bright_foreground": "#fbe170",
        "red": "#dd4b40",
        "green": "#a27820",
        "bright_green": "#cb9d20",
        "blue": "#895cf2",
        "magenta": "#c037e6",
    }

    # Find the current theme name.
    out, rc = run_cmd(["omarchy", "theme", "current"], timeout=8)
    theme_name = out.strip() if rc == 0 else ""
    if not theme_name:
        return fallback

    # Find its directory (prefer user-installed copy).
    out, rc = run_cmd(["omarchy", "theme", "dir", theme_name], timeout=8)
    theme_dir = Path(out.strip()) if rc == 0 and out.strip() else None
    if not theme_dir or not theme_dir.exists():
        theme_dir = Path.home() / ".config/omarchy/themes" / theme_name

    colors_path = theme_dir / "colors.toml"
    if not colors_path.exists():
        return fallback

    colors = dict(fallback)
    for line in colors_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        m = re.match(r'^(\w+)\s*=\s*"?(#[0-9a-fA-F]+)"?\s*$', line)
        if m:
            colors[m.group(1)] = m.group(2)
    return colors


def generate_css_string(colors):
    """Build the full CSS: @define-color rules from theme + base omarchy.css."""
    # Map theme color names to our CSS variable names.
    var_map = {
        "om_bg": colors.get("background", "#0C0222"),
        "om_dark_bg": colors.get("dark_background", "#09021a"),
        "om_lighter_bg": colors.get("lighter_background", "#241b38"),
        "om_fg": colors.get("foreground", "#F9D740"),
        "om_light_fg": colors.get("light_foreground", "#fadd5d"),
        "om_bright_fg": colors.get("bright_foreground", "#fbe170"),
        "om_accent": colors.get("accent", "#895cf2"),
        "om_bright_accent": colors.get("bright_blue", "#b176ff"),
        "om_muted": colors.get("muted", "#616066"),
        "om_green": colors.get("green", "#a27820"),
        "om_bright_green": colors.get("bright_green", "#cb9d20"),
        "om_red": colors.get("red", "#dd4b40"),
    }
    defines = "\n".join(f"@define-color {k} {v};" for k, v in var_map.items())

    css_path = Path(__file__).parent / "omarchy.css"
    base_css = css_path.read_text() if css_path.exists() else ""
    return defines + "\n\n" + base_css


def search_pacman(query):
    """Search official repos via `pacman -Ss` → list of (repo, name, desc)."""
    if not query or not query.strip():
        return []
    out, _ = run_cmd(["pacman", "-Ss", query], timeout=15)
    results = []
    lines = out.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\w+)/(\S+)\s+(\S+)", line)
        if m:
            repo, name, version = m.group(1), m.group(2), m.group(3)
            desc = lines[i + 1].strip() if i + 1 < len(lines) else ""
            results.append((repo, name, version, desc))
    return results


def search_aur(query):
    """Search AUR via the JSON RPC API (~0.2s) → list of (repo, name, version, desc).

    Uses the AUR's public RPC v5 search endpoint directly instead of `yay -Sa`,
    which can take over a minute. Results are sorted by vote count (most
    popular first) for relevance.
    """
    if not query or not query.strip():
        return []
    import urllib.request
    import urllib.parse
    import urllib.error
    url = ("https://aur.archlinux.org/rpc/v5/search/"
           + urllib.parse.quote(query.strip()))
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, OSError):
        return []
    results = []
    for item in data.get("results", []):
        results.append((
            "aur",
            item.get("Name", ""),
            item.get("Version", ""),
            item.get("Description", ""),
            item.get("NumVotes", 0),
        ))
    # Sort by votes descending (most popular first).
    results.sort(key=lambda r: r[4], reverse=True)
    # Drop the votes field, return (repo, name, version, desc).
    return [(r[0], r[1], r[2], r[3]) for r in results]


def get_gpu_vram():
    """Detect NVIDIA GPU name and VRAM via nvidia-smi.

    Returns (gpu_name, vram_mib) or (None, None) if no NVIDIA GPU.
    """
    out, rc = run_cmd([
        "nvidia-smi", "--query-gpu=name,memory.total",
        "--format=csv,noheader,nounits"
    ], timeout=8)
    if rc != 0 or not out.strip():
        return None, None
    parts = out.strip().split(", ")
    if len(parts) >= 2:
        return parts[0].strip(), int(parts[1].strip())
    return None, None


def parse_param_sizes(size_tags):
    """Convert param-size tags like ['0.5b', '3b', '70b'] → [0.5, 3.0, 70.0]."""
    sizes = []
    for tag in size_tags:
        m = re.match(r'^([0-9.]+)[bB]$', tag.strip())
        if m:
            sizes.append(float(m.group(1)))
    return sorted(sizes)


def vram_fit_status(sizes, vram_mib):
    """Determine VRAM fit for a model's smallest available param size.

    Q4_K_M quantization needs ~0.7 GiB per billion params. We leave ~1 GiB
    for KV cache / context, so the practical limit is (vram_gib - 1) / 0.7.
    Returns one of: 'fits', 'tight', 'toobig'.
    """
    if not sizes or not vram_mib:
        return 'toobig'
    vram_gib = vram_mib / 1024.0
    max_params = (vram_gib - 1.0) / 0.7  # rough Q4 estimate
    smallest = sizes[0]
    if smallest <= max_params:
        return 'fits'
    elif smallest <= max_params * 1.5:
        return 'tight'  # may need partial CPU offload
    else:
        return 'toobig'


def search_ollama_models(query, vram_mib=None):
    """Search ollama.com/library for models matching the query.

    Scrapes the search results page HTML (no public JSON API exists) and
    returns a list of dicts: {name, sizes, caps, pulls, desc, fit}.
    The 'fit' field is 'fits'/'tight'/'toobig' if vram_mib is provided, else None.
    """
    if not query or not query.strip():
        return []
    import urllib.request
    import urllib.parse
    import html as html_mod
    url = ("https://ollama.com/search?q="
           + urllib.parse.quote(query.strip()))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "omarchy-setup-wizard"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8")
    except Exception:
        return []

    # Split into model blocks at each /library/ link.
    blocks = re.split(r'href="/library/', raw)[1:]
    models = []
    seen = set()
    for block in blocks:
        name = block.split('"')[0]
        if name in seen or not name:
            continue
        seen.add(name)
        # Param-size badges (blue bg-[#ddf4ff] spans).
        size_tags = re.findall(r'bg-\[#ddf4ff\][^>]*>([0-9.]+[bB])<', block)
        sizes = parse_param_sizes(size_tags)
        # Capability badges (indigo bg-indigo-50 spans).
        caps = re.findall(r'bg-indigo-50[^>]*>([a-z]+)<', block)
        # Pulls count.
        pulls_match = re.search(
            r'<span\s*>([0-9.]+[KMk]?)</span>\s*'
            r'<span class="hidden sm:flex">&nbsp;Pulls', block)
        pulls = pulls_match.group(1) if pulls_match else ""
        # Description (first <p> with meaningful text).
        desc_match = re.search(r'<p[^>]*>([^<]{10,})</p>', block)
        desc = html_mod.unescape(desc_match.group(1).strip()) if desc_match else ""
        # VRAM fit.
        fit = vram_fit_status(sizes, vram_mib) if vram_mib else None
        models.append({
            "name": name,
            "sizes": sizes,
            "caps": caps,
            "pulls": pulls,
            "desc": desc,
            "fit": fit,
        })
    return models


# ---------------------------------------------------------------------------
# JSON config generation.
# ---------------------------------------------------------------------------


def build_config(categories, plugin_states, plugin_toggles, plugins_add,
                 extra_pacman, extra_aur, category_order,
                 configured_pkg_toggles=None, packages_data=None,
                 confirm_close=None, ollama_install=None, ollama_models=None):
    """Build the JSON config dict from wizard state.

    categories       — dict {name: bool}
    plugin_states    — dict {id: {enabled: bool, ...}} (current state)
    plugin_toggles   — dict {id: bool} (desired enabled state)
    plugins_add      — list of {id, url, enable}
    extra_pacman     — set of package names (from search)
    extra_aur        — set of package names (from search)
    category_order   — list of category names in fixed order
    configured_pkg_toggles — dict {pkg_name: bool} per-package opt-in/out
    packages_data    — dict with 'added', 'aur', 'removed' lists
    confirm_close    — bool or None (SUPER+W confirm-close feature)
    ollama_install   — bool or None (Ollama + GPU models feature)
    ollama_models    — list of model tags to pull, or None
    """
    selected = [c for c in category_order if categories.get(c, False)]

    enable = []
    disable = []
    for pid, desired in plugin_toggles.items():
        current = plugin_states.get(pid, {}).get("enabled", False)
        if desired and not current:
            enable.append(pid)
        elif not desired and current:
            disable.append(pid)

    result = {
        "categories": selected,
        "plugins": {
            "enable": sorted(enable),
            "disable": sorted(disable),
            "add": plugins_add,
        },
        "packages": {
            "pacman": sorted(extra_pacman),
            "aur": sorted(extra_aur),
        },
    }

    # Include the toggled-on configured packages so apply.sh --myconfig can
    # honour per-package opt-outs instead of always reading the text files.
    if configured_pkg_toggles is not None and packages_data is not None:
        configured = {}
        for key in ("added", "aur", "removed"):
            configured[key] = sorted(
                name for name in packages_data.get(key, [])
                if configured_pkg_toggles.get(name, True)
            )
        result["configured_packages"] = configured

    # Feature toggles.
    features = {}
    if confirm_close is not None:
        features["confirm_close"] = confirm_close
    if ollama_install is not None:
        features["ollama_install"] = ollama_install
    if features:
        result["features"] = features

    # Ollama models to pull.
    if ollama_install and ollama_models is not None:
        result["ollama_models"] = sorted(ollama_models)

    return result


def default_config(setup_dir):
    """Build a default config (all categories, default plugins per plugins.txt,
    no extra packages) — used by --print-json."""
    cats = parse_apply_list(setup_dir)
    category_order = [c[0] for c in cats]
    categories = {c: True for c in category_order}

    defaults = read_plugins_txt(setup_dir)
    states = get_plugin_states()

    # Default plugin toggles: current state, with plugins.txt defaults set.
    toggles = {pid: s.get("enabled", False) for pid, s in states.items()}
    for p in defaults:
        toggles[p["id"]] = p["enable"]

    plugins_add = []
    pkgs = read_packages_files(setup_dir)
    pkg_toggles = {name: True for key in ("added", "aur", "removed")
                   for name in pkgs.get(key, [])}
    return build_config(
        categories, states, toggles, plugins_add, set(), set(),
        category_order, pkg_toggles, pkgs,
        confirm_close=True, ollama_install=True, ollama_models=["qwen2.5:3b"]
    )


# ---------------------------------------------------------------------------
# The wizard application.
# ---------------------------------------------------------------------------


class WizardApp(Adw.Application):
    """The main wizard application with a 4-page Gtk.Stack."""

    PAGE_TITLES = [
        "My Defaults",
        "Plugins",
        "Packages",
        "GPU Models",
        "Review & Save",
    ]

    def __init__(self, setup_dir):
        super().__init__(application_id="dev.omarchy.setup-wizard",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.setup_dir = setup_dir
        self.current_page = 0
        self.win = None

        # --- load data ---
        self.categories_info = parse_apply_list(setup_dir)
        self.category_order = [c[0] for c in self.categories_info]
        self.category_descs = dict(self.categories_info)
        self.packages = read_packages_files(setup_dir)
        self.default_plugins = read_plugins_txt(setup_dir)
        self.plugin_catalog = get_plugin_catalog()
        self.plugin_states = get_plugin_states()
        self.theme_colors = get_theme_colors()

        # --- wizard state ---
        self.selected_categories = {c: True for c in self.category_order}
        self.plugin_toggles = {}
        self.plugins_to_add = []
        self.extra_pacman = set()
        self.extra_aur = set()
        self.saved_config_path = None

        # Per-package toggle state for the configured-package lists.
        # Defaults to True (install/remove as configured); False skips it.
        self.configured_pkg_toggles = {}
        for key in ("added", "aur", "removed"):
            for name in self.packages.get(key, []):
                self.configured_pkg_toggles[name] = True

        # Async package-search state (background thread + progress bar).
        self._search_serial = 0
        self._search_timeout_id = 0
        self._search_pulse_id = 0
        self._search_progress = None

        # Feature toggles (shown on the defaults page).
        self.confirm_close_enabled = True
        self.ollama_enabled = True

        # GPU + ollama model state.
        self.gpu_name, self.gpu_vram = get_gpu_vram()
        self.ollama_models_to_pull = ["qwen2.5:3b"]  # default pre-selected
        self._model_search_serial = 0
        self._model_search_timeout_id = 0
        self._model_search_pulse_id = 0
        self._model_search_progress = None

        # Initialize plugin toggles from current state.
        for pid, state in self.plugin_states.items():
            self.plugin_toggles[pid] = state.get("enabled", False)
        # Apply plugins.txt defaults.
        for p in self.default_plugins:
            self.plugin_toggles[p["id"]] = p["enable"]

    def do_activate(self):
        if self.win is not None:
            self.win.present()
            return

        colors = self.theme_colors
        css_text = generate_css_string(colors)
        provider = Gtk.CssProvider()
        provider.load_from_data(css_text.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        win = Adw.ApplicationWindow(application=self)
        win.set_title("Omarchy Setup Wizard")
        win.set_default_size(860, 640)
        win.add_css_class("om-wizard")

        # Header bar.
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        win.set_content(self._build_root(header))

        self.win = win
        win.present()

    # --- layout ---

    def _build_root(self, header):
        """Build the main vertical box: header + stack + nav bar."""
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(header)

        # Stack with the 4 pages.
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_vexpand(True)

        self.pages = [
            self._build_defaults_page(),
            self._build_plugins_page(),
            self._build_packages_page(),
            self._build_gpu_models_page(),
            self._build_review_page(),
        ]
        for i, page in enumerate(self.pages):
            self.stack.add_named(page, f"page-{i}")

        outer.append(self.stack)

        # Navigation bar.
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                      halign=Gtk.Align.FILL, margin_top=8,
                      margin_bottom=12, margin_start=16, margin_end=16,
                      spacing=10)
        nav.add_css_class("om-page")
        nav.set_spacing(10)

        self.step_label = Gtk.Label(label=f"Step 1 of {len(self.PAGE_TITLES)}")
        self.step_label.add_css_class("om-step")
        self.step_label.set_hexpand(True)
        self.step_label.set_xalign(0)

        self.back_btn = Gtk.Button(label="Back")
        self.back_btn.add_css_class("om-nav-btn")
        self.back_btn.connect("clicked", self._on_back)

        self.next_btn = Gtk.Button(label="Next")
        self.next_btn.add_css_class("om-nav-btn")
        self.next_btn.add_css_class("om-nav-btn-primary")
        self.next_btn.connect("clicked", self._on_next)

        # Apply button — shown only on the last (review) page. Red until a
        # config is saved, then green.
        self.nav_apply_btn = Gtk.Button(label="Apply now")
        self.nav_apply_btn.add_css_class("om-nav-btn")
        self.nav_apply_btn.add_css_class("om-nav-btn-danger")
        self.nav_apply_btn.connect("clicked", self._on_apply_now)
        self.nav_apply_btn.set_sensitive(False)
        self.nav_apply_btn.set_visible(False)

        nav.append(self.step_label)
        nav.append(self.back_btn)
        nav.append(self.next_btn)
        nav.append(self.nav_apply_btn)
        outer.append(nav)

        self._update_nav()
        return outer

    def _update_nav(self):
        """Update back/next buttons and step label for the current page."""
        total = len(self.PAGE_TITLES)
        self.step_label.set_text(
            f"Step {self.current_page + 1} of {total} — {self.PAGE_TITLES[self.current_page]}"
        )
        self.back_btn.set_visible(self.current_page > 0)
        if self.current_page == total - 1:
            self.next_btn.set_label("Save config…")
            self.nav_apply_btn.set_visible(True)
        else:
            self.next_btn.set_label("Next")
            self.nav_apply_btn.set_visible(False)
        self.stack.set_visible_child_name(f"page-{self.current_page}")
        # Sync the ollama-off notice on the GPU Models page.
        gpu_page_idx = total - 2  # page before review
        if self.current_page == gpu_page_idx and hasattr(self, "_gpu_ollama_notice"):
            self._gpu_ollama_notice.set_visible(not self.ollama_enabled)
        # Refresh review page when entering it.
        if self.current_page == total - 1:
            self._refresh_review()

    def _on_back(self, _btn):
        if self.current_page > 0:
            self.current_page -= 1
            self._update_nav()

    def _on_next(self, _btn):
        total = len(self.PAGE_TITLES)
        if self.current_page < total - 1:
            self.current_page += 1
            self._update_nav()
        else:
            self._on_save_config()

    # --- page 1: defaults ---

    def _build_defaults_page(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.add_css_class("om-scrolled")

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                       valign=Gtk.Align.START)
        page.add_css_class("om-page")

        title = Gtk.Label(label="My Opinionated Defaults")
        title.add_css_class("om-title")
        title.set_xalign(0)
        page.append(title)

        subtitle = Gtk.Label(
            label="Select which setup categories to apply. These are your "
                  "pre-configured defaults from this machine."
        )
        subtitle.add_css_class("om-subtitle")
        subtitle.set_xalign(0)
        subtitle.set_wrap(True)
        page.append(subtitle)

        # Select all / none helpers.
        helpers = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          margin_bottom=8)
        all_btn = Gtk.Button(label="Select all")
        all_btn.add_css_class("om-helper-btn")
        all_btn.connect("clicked", lambda *_: self._set_all_categories(True))
        none_btn = Gtk.Button(label="Select none")
        none_btn.add_css_class("om-helper-btn")
        none_btn.connect("clicked", lambda *_: self._set_all_categories(False))
        helpers.append(all_btn)
        helpers.append(none_btn)
        page.append(helpers)

        # Category toggles in a card.
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("om-card")

        self.category_switches = {}
        for name in self.category_order:
            desc = self.category_descs.get(name, "")
            row = self._make_toggle_row(name, desc,
                                        self.selected_categories.get(name, True))
            switch = row.switch_widget
            switch.connect("notify::active", self._on_category_toggle, name)
            self.category_switches[name] = switch
            card.append(row)

        page.append(card)

        # Show the package counts.
        pkgs = self.packages
        info = Gtk.Label(
            label=f"Included: {len(pkgs['added'])} official packages, "
                  f"{len(pkgs['aur'])} AUR packages, {len(pkgs['removed'])} "
                  f"to remove, {len(self.default_plugins)} plugins."
        )
        info.add_css_class("om-subtitle")
        info.set_xalign(0)
        info.set_wrap(True)
        page.append(info)

        # Feature toggles card.
        page.append(self._build_features_toggles())

        scroll.set_child(page)
        return scroll

    def _build_features_toggles(self):
        """Card with install/don't-install toggles for notable features."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("om-card")
        card.set_margin_top(12)

        header = Gtk.Label(label="Features")
        header.add_css_class("om-group-header")
        header.set_xalign(0)
        card.append(header)

        # SUPER+W confirm-before-close toggle.
        cc_row = self._make_toggle_row(
            "SUPER+W confirm before close",
            "A zenity dialog asks before closing the focused window.",
            self.confirm_close_enabled)
        cc_switch = cc_row.switch_widget
        cc_switch.connect("notify::active", self._on_confirm_close_toggle)
        self._cc_switch = cc_switch
        card.append(cc_row)

        # Ollama + GPU models toggle.
        ol_row = self._make_toggle_row(
            "Ollama + GPU models",
            "Installs Ollama (CUDA/NVIDIA) and pulls your selected models. "
            "Search for compatible models on the GPU Models page.",
            self.ollama_enabled)
        ol_switch = ol_row.switch_widget
        ol_switch.connect("notify::active", self._on_ollama_toggle)
        self._ol_switch = ol_switch
        card.append(ol_row)

        return card

    def _on_confirm_close_toggle(self, switch, _pspec):
        self.confirm_close_enabled = switch.get_active()

    def _on_ollama_toggle(self, switch, _pspec):
        self.ollama_enabled = switch.get_active()

    def _set_all_categories(self, val):
        for name, sw in self.category_switches.items():
            sw.set_active(val)

    def _on_category_toggle(self, switch, _pspec, name):
        self.selected_categories[name] = switch.get_active()

    # --- page 2: plugins ---

    def _build_plugins_page(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.add_css_class("om-scrolled")

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                       valign=Gtk.Align.START)
        page.add_css_class("om-page")

        title = Gtk.Label(label="Shell Plugins")
        title.add_css_class("om-title")
        title.set_xalign(0)
        page.append(title)

        subtitle = Gtk.Label(
            label="Browse and toggle Omarchy shell plugins. The defaults from "
                  "plugins.txt are at the top. Search the full catalog below, "
                  "or add a new plugin by git URL."
        )
        subtitle.add_css_class("om-subtitle")
        subtitle.set_xalign(0)
        subtitle.set_wrap(True)
        page.append(subtitle)

        # Search entry.
        search = Gtk.SearchEntry(placeholder_text="Search plugins by name, id, or description…")
        search.add_css_class("om-search")
        search.connect("search-changed", self._on_plugin_search)
        self.plugin_search = search
        page.append(search)

        # Plugin list in a card.
        self.plugin_listbox = Gtk.ListBox()
        self.plugin_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.plugin_listbox.add_css_class("om-card")
        page.append(self.plugin_listbox)

        # Add-by-URL row.
        add_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                          spacing=8, margin_top=12)
        add_label = Gtk.Label(label="Add a new plugin by git URL:")
        add_label.add_css_class("om-subtitle")
        add_label.set_xalign(0)
        add_box.append(add_label)

        input_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.plugin_url_entry = Gtk.Entry(placeholder_text="https://github.com/…/plugin.git")
        self.plugin_url_entry.add_css_class("om-add-entry")
        self.plugin_url_entry.set_hexpand(True)

        add_btn = Gtk.Button(label="Add")
        add_btn.add_css_class("om-add-btn")
        add_btn.connect("clicked", self._on_add_plugin)

        input_row.append(self.plugin_url_entry)
        input_row.append(add_btn)
        add_box.append(input_row)
        page.append(add_box)

        # List of added plugins (for display).
        self.added_plugins_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                         spacing=4, margin_top=8)
        page.append(self.added_plugins_box)

        # Populate the list.
        self._populate_plugin_list("")
        scroll.set_child(page)
        return scroll

    def _on_plugin_search(self, entry):
        self._populate_plugin_list(entry.get_text().strip().lower())

    def _populate_plugin_list(self, query):
        """Rebuild the plugin listbox, filtered by query."""
        # Clear existing rows.
        while True:
            row = self.plugin_listbox.get_first_child()
            if row is None:
                break
            self.plugin_listbox.remove(row)

        # Merge catalog + states into a unified list.
        catalog_by_id = {p["id"]: p for p in self.plugin_catalog}
        all_ids = set(catalog_by_id.keys()) | set(self.plugin_states.keys())
        default_ids = {p["id"] for p in self.default_plugins}

        # Build entries: (id, name, desc, kinds, first_party, is_default)
        entries = []
        for pid in all_ids:
            cat = catalog_by_id.get(pid, {})
            state = self.plugin_states.get(pid, {})
            name = cat.get("name") or state.get("name") or pid
            desc = cat.get("description") or ""
            kinds = cat.get("kinds") or state.get("kinds") or []
            first_party = cat.get("firstParty", state.get("firstParty", False))
            is_default = pid in default_ids
            entries.append((pid, name, desc, kinds, first_party, is_default))

        # Sort: defaults first (in plugins.txt order), then alpha.
        default_order = {p["id"]: i for i, p in enumerate(self.default_plugins)}
        entries.sort(key=lambda e: (
            0 if e[5] else 1,
            default_order.get(e[0], 999),
            e[1].lower(),
        ))

        shown = 0
        for pid, name, desc, kinds, first_party, is_default in entries:
            if query:
                searchable = f"{name} {pid} {desc}".lower()
                if query not in searchable:
                    continue
            row = self._make_plugin_row(pid, name, desc, kinds,
                                        first_party, is_default)
            self.plugin_listbox.append(row)
            shown += 1

        if shown == 0:
            empty = Gtk.Label(label="No plugins match your search.")
            empty.add_css_class("om-subtitle")
            self.plugin_listbox.append(empty)

    def _make_plugin_row(self, pid, name, desc, kinds, first_party, is_default):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("om-row")

        # Left: name + badges + description.
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        left.set_hexpand(True)

        name_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name_lbl = Gtk.Label(label=name)
        name_lbl.add_css_class("om-row-name")
        name_lbl.set_xalign(0)
        name_box.append(name_lbl)

        if is_default:
            badge = Gtk.Label(label="default")
            badge.add_css_class("om-badge")
            badge.add_css_class("om-badge-firstparty")
            name_box.append(badge)
        if first_party:
            fp = Gtk.Label(label="omarchy")
            fp.add_css_class("om-badge")
            fp.add_css_class("om-badge-firstparty")
            name_box.append(fp)

        for kind in kinds[:3]:
            kb = Gtk.Label(label=kind)
            kb.add_css_class("om-badge-kind")
            name_box.append(kb)

        left.append(name_box)

        id_lbl = Gtk.Label(label=pid)
        id_lbl.add_css_class("om-row-id")
        id_lbl.set_xalign(0)
        left.append(id_lbl)

        if desc:
            desc_lbl = Gtk.Label(label=desc)
            desc_lbl.add_css_class("om-row-desc")
            desc_lbl.set_xalign(0)
            desc_lbl.set_wrap(True)
            desc_lbl.set_max_width_chars(70)
            left.append(desc_lbl)

        row.append(left)

        # Right: enable toggle.
        switch = Gtk.Switch()
        switch.set_active(self.plugin_toggles.get(pid, False))
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("notify::active", self._on_plugin_toggle, pid)
        row.append(switch)

        return row

    def _on_plugin_toggle(self, switch, _pspec, pid):
        self.plugin_toggles[pid] = switch.get_active()

    def _on_add_plugin(self, _btn):
        url = self.plugin_url_entry.get_text().strip()
        if not url:
            return
        entry = {"id": "", "url": url, "enable": True}
        self.plugins_to_add.append(entry)
        self.plugin_url_entry.set_text("")

        # Show the added plugin in the list.
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("om-row")
        lbl = Gtk.Label(label=f"+ {url}")
        lbl.add_css_class("om-row-name")
        lbl.set_xalign(0)
        lbl.set_hexpand(True)
        lbl.set_wrap(True)
        rm = Gtk.Button(label="Remove")
        rm.add_css_class("om-helper-btn")
        rm.connect("clicked", self._on_remove_added_plugin, entry, row)
        row.append(lbl)
        row.append(rm)
        self.added_plugins_box.append(row)

    def _on_remove_added_plugin(self, _btn, entry, row):
        if entry in self.plugins_to_add:
            self.plugins_to_add.remove(entry)
        self.added_plugins_box.remove(row)

    # --- page 3: packages ---

    def _build_packages_page(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.add_css_class("om-scrolled")

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                       valign=Gtk.Align.START)
        page.add_css_class("om-page")

        title = Gtk.Label(label="Search & Add Packages")
        title.add_css_class("om-title")
        title.set_xalign(0)
        page.append(title)

        subtitle = Gtk.Label(
            label="Your opinionated package defaults are listed below with "
                  "pacman / AUR / removed badges. Search underneath to add more."
        )
        subtitle.add_css_class("om-subtitle")
        subtitle.set_xalign(0)
        subtitle.set_wrap(True)
        page.append(subtitle)

        # Configured packages from the three text files.
        page.append(self._build_configured_packages_section())

        # Search header.
        search_header = Gtk.Label(label="Add more packages")
        search_header.add_css_class("om-summary-label")
        search_header.set_xalign(0)
        search_header.set_margin_top(6)
        page.append(search_header)

        # Search row with AUR toggle.
        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        search = Gtk.SearchEntry(placeholder_text="Search packages…")
        search.add_css_class("om-search")
        search.set_hexpand(True)
        search.connect("activate", self._on_pkg_search)
        self.pkg_search = search

        aur_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        aur_box.set_valign(Gtk.Align.CENTER)
        aur_lbl = Gtk.Label(label="Include AUR")
        self.aur_switch = Gtk.Switch()
        self.aur_switch.set_active(True)
        aur_box.append(aur_lbl)
        aur_box.append(self.aur_switch)

        search_btn = Gtk.Button(label="Search")
        search_btn.add_css_class("om-nav-btn")
        search_btn.connect("clicked", self._on_pkg_search)

        search_row.append(search)
        search_row.append(aur_box)
        search_row.append(search_btn)
        page.append(search_row)

        # Results card.
        self.pkg_listbox = Gtk.ListBox()
        self.pkg_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.pkg_listbox.add_css_class("om-card")
        page.append(self.pkg_listbox)

        # Selected packages display.
        sel_label = Gtk.Label(label="Selected packages:")
        sel_label.add_css_class("om-summary-label")
        sel_label.set_xalign(0)
        sel_label.set_margin_top(12)
        page.append(sel_label)

        self.selected_pkgs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                         spacing=4)
        page.append(self.selected_pkgs_box)

        scroll.set_child(page)
        return scroll

    def _build_configured_packages_section(self):
        """Card showing the packages already in the three text files, grouped
        and badged: official (pacman), AUR, and removed."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("om-card")

        pkgs = self.packages
        groups = [
            ("Official (pacman)", pkgs.get("added", []), "pacman"),
            ("AUR", pkgs.get("aur", []), "aur"),
            ("Removed", pkgs.get("removed", []), "removed"),
        ]
        for label, names, badge_kind in groups:
            # Group header.
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            header.add_css_class("om-group-header")
            h_lbl = Gtk.Label(label=label)
            h_lbl.add_css_class("om-group-header")
            h_lbl.set_xalign(0)
            count = Gtk.Label(label=f"({len(names)})")
            count.add_css_class("om-group-count")
            header.append(h_lbl)
            header.append(count)
            card.append(header)

            if not names:
                empty = Gtk.Label(label="(none)")
                empty.add_css_class("om-summary-item")
                empty.set_xalign(0)
                empty.set_margin_start(12)
                empty.set_margin_bottom(4)
                card.append(empty)
                continue

            for name in names:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row.add_css_class("om-row")
                badge = Gtk.Label(label=badge_kind)
                badge.add_css_class("om-badge")
                badge.add_css_class(f"om-badge-{badge_kind}")
                name_lbl = Gtk.Label(label=name)
                name_lbl.add_css_class("om-row-name")
                name_lbl.set_xalign(0)
                name_lbl.set_hexpand(True)
                switch = Gtk.Switch()
                switch.set_active(self.configured_pkg_toggles.get(name, True))
                switch.set_valign(Gtk.Align.CENTER)
                switch.connect("notify::active",
                               self._on_configured_pkg_toggle, name)
                row.append(badge)
                row.append(name_lbl)
                row.append(switch)
                card.append(row)

        return card

    def _on_configured_pkg_toggle(self, switch, _pspec, name):
        self.configured_pkg_toggles[name] = switch.get_active()

    def _on_pkg_search(self, _widget):
        query = self.pkg_search.get_text().strip()
        if not query:
            return
        include_aur = self.aur_switch.get_active()

        # Cancel any in-flight search's timeout + pulse, and bump the serial so
        # a stale background thread's results are discarded.
        self._search_serial += 1
        serial = self._search_serial
        self._cancel_search_timers()

        # Clear results, show a loading label.
        self._clear_pkg_children(self.pkg_listbox)
        loading = Gtk.Label(label="Searching…")
        loading.add_css_class("om-subtitle")
        self.pkg_listbox.append(loading)

        # Start a 3-second timer; if results haven't arrived by then, show a
        # pulsing progress bar so the user knows something is happening.
        self._search_timeout_id = GLib.timeout_add(
            3000, self._on_search_timeout, serial)

        # Run the search in a background thread so the GUI stays responsive.
        threading.Thread(
            target=self._search_worker,
            args=(query, include_aur, serial),
            daemon=True,
        ).start()

    def _search_worker(self, query, include_aur, serial):
        """Background thread: run pacman + AUR search without blocking the UI."""
        pacman_results = search_pacman(query)
        aur_results = search_aur(query) if include_aur else []
        # Hand results back to the main thread via idle_add.
        GLib.idle_add(self._on_search_complete, pacman_results, aur_results, serial)

    def _on_search_timeout(self, serial):
        """Fires 3s after a search starts. If still waiting, show a progress bar."""
        self._search_timeout_id = 0
        if serial != self._search_serial:
            return False  # A newer search superseded this one.
        progress = Gtk.ProgressBar(pulse_step=0.3)
        progress.add_css_class("om-search-progress")
        progress.set_text("Still searching — AUR queries can take a few seconds…")
        progress.set_show_text(True)
        progress.pulse()
        self.pkg_listbox.append(progress)
        self._search_progress = progress
        # Pulse every 400ms until results arrive.
        self._search_pulse_id = GLib.timeout_add(400, self._pulse_progress, serial)
        return False  # Don't repeat this one-shot timeout.

    def _pulse_progress(self, serial):
        if serial != self._search_serial or self._search_progress is None:
            self._search_pulse_id = 0
            return False  # Stop pulsing.
        self._search_progress.pulse()
        return True  # Continue.

    def _on_search_complete(self, pacman_results, aur_results, serial):
        """Main-thread callback when the background search finishes."""
        # Discard stale results from a superseded search.
        if serial != self._search_serial:
            return False
        self._cancel_search_timers()

        # Clear the loading label / progress bar / old results.
        self._clear_pkg_children(self.pkg_listbox)
        self._search_progress = None

        if not pacman_results and not aur_results:
            empty = Gtk.Label(label="No packages found.")
            empty.add_css_class("om-subtitle")
            self.pkg_listbox.append(empty)
            return False

        for repo, name, version, desc in pacman_results:
            row = self._make_pkg_row(repo, name, version, desc, is_aur=False)
            self.pkg_listbox.append(row)

        for repo, name, version, desc in aur_results:
            row = self._make_pkg_row("aur", name, version, desc, is_aur=True)
            self.pkg_listbox.append(row)

        return False  # Don't call idle_add again.

    def _cancel_search_timers(self):
        """Remove the 3s timeout and the pulse timer if active."""
        if self._search_timeout_id:
            GLib.source_remove(self._search_timeout_id)
            self._search_timeout_id = 0
        if self._search_pulse_id:
            GLib.source_remove(self._search_pulse_id)
            self._search_pulse_id = 0

    def _clear_pkg_children(self, container):
        """Remove all children from a Gtk container."""
        while True:
            child = container.get_first_child()
            if child is None:
                break
            container.remove(child)

    def _make_pkg_row(self, repo, name, version, desc, is_aur):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("om-row")

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        left.set_hexpand(True)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        badge = Gtk.Label(label=repo)
        badge.add_css_class("om-badge")
        if is_aur:
            badge.add_css_class("om-badge-aur")
        top.append(badge)

        name_lbl = Gtk.Label(label=name)
        name_lbl.add_css_class("om-row-name")
        name_lbl.set_xalign(0)
        top.append(name_lbl)

        ver_lbl = Gtk.Label(label=version)
        ver_lbl.add_css_class("om-row-id")
        top.append(ver_lbl)
        left.append(top)

        if desc:
            d = Gtk.Label(label=desc)
            d.add_css_class("om-row-desc")
            d.set_xalign(0)
            d.set_wrap(True)
            d.set_max_width_chars(70)
            left.append(d)

        row.append(left)

        # Add button or "added" indicator.
        already = name in self.extra_pacman or name in self.extra_aur
        if already:
            check = Gtk.Label(label="✓ added")
            check.add_css_class("om-success")
            check.set_valign(Gtk.Align.CENTER)
            row.append(check)
        else:
            add = Gtk.Button(label="Add")
            add.add_css_class("om-helper-btn")
            add.set_valign(Gtk.Align.CENTER)
            add.connect("clicked", self._on_add_pkg, name, is_aur, row)
            row.append(add)

        return row

    def _on_add_pkg(self, _btn, name, is_aur, row):
        if is_aur:
            self.extra_aur.add(name)
        else:
            self.extra_pacman.add(name)
        # Replace the Add button with a checkmark.
        # Find and remove the last child (the button), add a checkmark.
        last = row.get_last_child()
        if last:
            row.remove(last)
        check = Gtk.Label(label="✓ added")
        check.add_css_class("om-success")
        check.set_valign(Gtk.Align.CENTER)
        row.append(check)
        self._refresh_selected_pkgs()

    def _refresh_selected_pkgs(self):
        """Update the selected-packages display below the search."""
        while True:
            child = self.selected_pkgs_box.get_first_child()
            if child is None:
                break
            self.selected_pkgs_box.remove(child)

        all_sel = sorted(
            [(n, "aur") for n in self.extra_aur] +
            [(n, "pacman") for n in self.extra_pacman]
        )
        if not all_sel:
            empty = Gtk.Label(label="(none yet)")
            empty.add_css_class("om-summary-item")
            self.selected_pkgs_box.append(empty)
            return

        for name, source in all_sel:
            chip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            chip = Gtk.Label(label=source.upper())
            chip.add_css_class("om-badge" if source == "pacman" else "om-badge-aur")
            chip_box.append(chip)
            lbl = Gtk.Label(label=name)
            lbl.add_css_class("om-summary-item")
            lbl.set_xalign(0)
            chip_box.append(lbl)
            self.selected_pkgs_box.append(chip_box)

    # --- page 4: GPU models ---

    def _build_gpu_models_page(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.add_css_class("om-scrolled")

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                       valign=Gtk.Align.START)
        page.add_css_class("om-page")

        title = Gtk.Label(label="GPU Models")
        title.add_css_class("om-title")
        title.set_xalign(0)
        page.append(title)

        # GPU info or "no GPU" warning.
        if self.gpu_name:
            vram_gb = self.gpu_vram / 1024.0 if self.gpu_vram else 0
            gpu_info = Gtk.Label(
                label=f"GPU: {self.gpu_name} — {self.gpu_vram} MiB ({vram_gb:.1f} GB) VRAM\n"
                      f"Models up to ~{int((vram_gb - 1) / 0.7)}B params fit fully in VRAM at Q4."
            )
        else:
            gpu_info = Gtk.Label(
                label="No NVIDIA GPU detected (nvidia-smi not found).\n"
                      "You can still browse models, but none will be highlighted as fitting."
            )
        gpu_info.add_css_class("om-gpu-info")
        gpu_info.set_xalign(0)
        gpu_info.set_wrap(True)
        page.append(gpu_info)

        # If ollama is toggled off, show a notice.
        self._gpu_ollama_notice = Gtk.Label(
            label="⚠ Ollama is toggled off on the Defaults page. "
                  "Enable it there to install Ollama and pull models."
        )
        self._gpu_ollama_notice.add_css_class("om-subtitle")
        self._gpu_ollama_notice.set_xalign(0)
        self._gpu_ollama_notice.set_wrap(True)
        self._gpu_ollama_notice.set_visible(not self.ollama_enabled)
        page.append(self._gpu_ollama_notice)

        # Search row.
        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        search = Gtk.SearchEntry(placeholder_text="Search ollama models…")
        search.add_css_class("om-search")
        search.set_hexpand(True)
        search.connect("activate", self._on_model_search)
        self.model_search = search

        search_btn = Gtk.Button(label="Search")
        search_btn.add_css_class("om-nav-btn")
        search_btn.connect("clicked", self._on_model_search)

        search_row.append(search)
        search_row.append(search_btn)
        page.append(search_row)

        # Results card.
        self.model_listbox = Gtk.ListBox()
        self.model_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.model_listbox.add_css_class("om-card")
        page.append(self.model_listbox)

        # Selected models display.
        sel_label = Gtk.Label(label="Models to pull:")
        sel_label.add_css_class("om-summary-label")
        sel_label.set_xalign(0)
        sel_label.set_margin_top(12)
        page.append(sel_label)

        self.selected_models_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4)
        page.append(self.selected_models_box)
        self._refresh_selected_models()

        scroll.set_child(page)
        return scroll

    def _on_model_search(self, _widget):
        query = self.model_search.get_text().strip()
        if not query:
            return
        self._model_search_serial += 1
        serial = self._model_search_serial
        self._cancel_model_search_timers()

        self._clear_pkg_children(self.model_listbox)
        loading = Gtk.Label(label="Searching…")
        loading.add_css_class("om-subtitle")
        self.model_listbox.append(loading)

        self._model_search_timeout_id = GLib.timeout_add(
            3000, self._on_model_search_timeout, serial)

        threading.Thread(
            target=self._model_search_worker,
            args=(query, serial),
            daemon=True,
        ).start()

    def _model_search_worker(self, query, serial):
        results = search_ollama_models(query, self.gpu_vram)
        GLib.idle_add(self._on_model_search_complete, results, serial)

    def _on_model_search_timeout(self, serial):
        self._model_search_timeout_id = 0
        if serial != self._model_search_serial:
            return False
        progress = Gtk.ProgressBar(pulse_step=0.3)
        progress.add_css_class("om-search-progress")
        progress.set_text("Still searching ollama.com…")
        progress.set_show_text(True)
        progress.pulse()
        self.model_listbox.append(progress)
        self._model_search_progress = progress
        self._model_search_pulse_id = GLib.timeout_add(
            400, self._pulse_model_progress, serial)
        return False

    def _pulse_model_progress(self, serial):
        if serial != self._model_search_serial or self._model_search_progress is None:
            self._model_search_pulse_id = 0
            return False
        self._model_search_progress.pulse()
        return True

    def _on_model_search_complete(self, results, serial):
        if serial != self._model_search_serial:
            return False
        self._cancel_model_search_timers()
        self._clear_pkg_children(self.model_listbox)
        self._model_search_progress = None

        if not results:
            empty = Gtk.Label(label="No models found.")
            empty.add_css_class("om-subtitle")
            self.model_listbox.append(empty)
            return False

        for model in results:
            row = self._make_model_row(model)
            self.model_listbox.append(row)
        return False

    def _cancel_model_search_timers(self):
        if self._model_search_timeout_id:
            GLib.source_remove(self._model_search_timeout_id)
            self._model_search_timeout_id = 0
        if self._model_search_pulse_id:
            GLib.source_remove(self._model_search_pulse_id)
            self._model_search_pulse_id = 0

    def _make_model_row(self, model):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("om-row")

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        left.set_hexpand(True)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name_lbl = Gtk.Label(label=model["name"])
        name_lbl.add_css_class("om-row-name")
        name_lbl.set_xalign(0)
        top.append(name_lbl)

        for s in model["sizes"]:
            sb = Gtk.Label(label=f"{s}b")
            sb.add_css_class("om-badge-kind")
            top.append(sb)

        for cap in model["caps"][:3]:
            cb = Gtk.Label(label=cap)
            cb.add_css_class("om-badge-kind")
            top.append(cb)

        fit = model.get("fit")
        if fit == "fits":
            fb = Gtk.Label(label="✓ fits VRAM")
            fb.add_css_class("om-badge")
            fb.add_css_class("om-badge-fits")
            top.append(fb)
        elif fit == "tight":
            fb = Gtk.Label(label="⚠ tight")
            fb.add_css_class("om-badge")
            fb.add_css_class("om-badge-tight")
            top.append(fb)
        elif fit == "toobig":
            fb = Gtk.Label(label="✗ too large")
            fb.add_css_class("om-badge")
            fb.add_css_class("om-badge-toobig")
            top.append(fb)

        left.append(top)

        meta_parts = []
        if model["desc"]:
            meta_parts.append(model["desc"][:80])
        if model["pulls"]:
            meta_parts.append(f"{model['pulls']} pulls")
        if meta_parts:
            meta = Gtk.Label(label=" · ".join(meta_parts))
            meta.add_css_class("om-row-desc")
            meta.set_xalign(0)
            meta.set_wrap(True)
            meta.set_max_width_chars(70)
            left.append(meta)

        row.append(left)

        # Default tag: use the smallest available size.
        if model["sizes"]:
            smallest = model["sizes"][0]
            tag_str = f"{int(smallest)}b" if smallest == int(smallest) else f"{smallest}b"
            model_tag = f"{model['name']}:{tag_str}"
        else:
            model_tag = model["name"]

        if model_tag in self.ollama_models_to_pull:
            check = Gtk.Label(label="✓ selected")
            check.add_css_class("om-success")
            check.set_valign(Gtk.Align.CENTER)
            row.append(check)
        else:
            add = Gtk.Button(label="Pull")
            add.add_css_class("om-helper-btn")
            add.set_valign(Gtk.Align.CENTER)
            add.connect("clicked", self._on_add_model, model_tag, row)
            row.append(add)

        return row

    def _on_add_model(self, _btn, model_tag, row):
        if model_tag not in self.ollama_models_to_pull:
            self.ollama_models_to_pull.append(model_tag)
        last = row.get_last_child()
        if last:
            row.remove(last)
        check = Gtk.Label(label="✓ selected")
        check.add_css_class("om-success")
        check.set_valign(Gtk.Align.CENTER)
        row.append(check)
        self._refresh_selected_models()

    def _refresh_selected_models(self):
        while True:
            child = self.selected_models_box.get_first_child()
            if child is None:
                break
            self.selected_models_box.remove(child)

        if not self.ollama_models_to_pull:
            empty = Gtk.Label(label="(none yet)")
            empty.add_css_class("om-summary-item")
            self.selected_models_box.append(empty)
            return

        for tag in sorted(self.ollama_models_to_pull):
            chip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            lbl = Gtk.Label(label=tag)
            lbl.add_css_class("om-summary-item")
            lbl.set_xalign(0)
            chip_box.append(lbl)
            rm = Gtk.Button(label="Remove")
            rm.add_css_class("om-helper-btn")
            rm.connect("clicked", self._on_remove_model, tag, chip_box)
            chip_box.append(rm)
            self.selected_models_box.append(chip_box)

    def _on_remove_model(self, _btn, tag, chip_box):
        if tag in self.ollama_models_to_pull:
            self.ollama_models_to_pull.remove(tag)
        self.selected_models_box.remove(chip_box)

    # --- page 5: review ---

    def _build_review_page(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.add_css_class("om-scrolled")

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                       valign=Gtk.Align.START)
        page.add_css_class("om-page")

        title = Gtk.Label(label="Review & Save")
        title.add_css_class("om-title")
        title.set_xalign(0)
        page.append(title)

        subtitle = Gtk.Label(
            label="Review your selections below. Save the config to a JSON "
                  "file, then optionally apply it with apply.sh --myconfig."
        )
        subtitle.add_css_class("om-subtitle")
        subtitle.set_xalign(0)
        subtitle.set_wrap(True)
        page.append(subtitle)

        self.review_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                      spacing=6)
        page.append(self.review_content)

        self.save_status = Gtk.Label(label="")
        self.save_status.set_xalign(0)
        self.save_status.set_margin_top(8)
        self.save_status.set_wrap(True)
        page.append(self.save_status)

        scroll.set_child(page)
        return scroll

    def _refresh_review(self):
        """Rebuild the review summary from current state."""
        while True:
            child = self.review_content.get_first_child()
            if child is None:
                break
            self.review_content.remove(child)

        config = build_config(
            self.selected_categories,
            self.plugin_states,
            self.plugin_toggles,
            self.plugins_to_add,
            self.extra_pacman,
            self.extra_aur,
            self.category_order,
            self.configured_pkg_toggles,
            self.packages,
            confirm_close=self.confirm_close_enabled,
            ollama_install=self.ollama_enabled,
            ollama_models=self.ollama_models_to_pull,
        )

        # Categories section.
        self._add_review_label("Categories", bold=True)
        for cat in config["categories"]:
            self._add_review_item(f"✓ {cat} — {self.category_descs.get(cat, '')}")
        if not config["categories"]:
            self._add_review_item("(none selected)")

        # Features section.
        if "features" in config:
            self._add_review_label("Features", bold=True, margin_top=12)
            feats = config["features"]
            self._add_review_item(
                f"SUPER+W confirm close: {'✓ enabled' if feats.get('confirm_close') else '✗ disabled'}"
            )
            self._add_review_item(
                f"Ollama + GPU models: {'✓ enabled' if feats.get('ollama_install') else '✗ disabled'}"
            )

        # Ollama models section.
        if config.get("ollama_models"):
            self._add_review_label("Ollama models to pull", bold=True, margin_top=12)
            for tag in config["ollama_models"]:
                self._add_review_item(f"  {tag}")

        # Plugins section.
        self._add_review_label("Plugin changes", bold=True, margin_top=12)
        if config["plugins"]["enable"]:
            self._add_review_item(f"Enable: {', '.join(config['plugins']['enable'])}")
        if config["plugins"]["disable"]:
            self._add_review_item(f"Disable: {', '.join(config['plugins']['disable'])}")
        if config["plugins"]["add"]:
            for p in config["plugins"]["add"]:
                self._add_review_item(f"+ Add: {p['url']}")
        if not (config["plugins"]["enable"] or config["plugins"]["disable"]
                or config["plugins"]["add"]):
            self._add_review_item("(no changes)")

        # Extra packages (from search).
        self._add_review_label("Extra packages", bold=True, margin_top=12)
        if config["packages"]["pacman"]:
            self._add_review_item(f"Pacman: {', '.join(config['packages']['pacman'])}")
        if config["packages"]["aur"]:
            self._add_review_item(f"AUR: {', '.join(config['packages']['aur'])}")
        if not (config["packages"]["pacman"] or config["packages"]["aur"]):
            self._add_review_item("(none)")

        # Configured packages (from the text files) — show counts and any
        # packages the user toggled off (skipped).
        if "configured_packages" in config:
            cp = config["configured_packages"]
            self._add_review_label("Configured packages", bold=True, margin_top=12)
            added_total = len(self.packages.get("added", []))
            aur_total = len(self.packages.get("aur", []))
            removed_total = len(self.packages.get("removed", []))
            self._add_review_item(
                f"Install (pacman): {len(cp['added'])}/{added_total}"
            )
            self._add_review_item(f"Install (AUR): {len(cp['aur'])}/{aur_total}")
            self._add_review_item(f"Remove: {len(cp['removed'])}/{removed_total}")
            # List any packages that were toggled off.
            skipped = []
            for key, total_list in [("added", "added"), ("aur", "aur"),
                                     ("removed", "removed")]:
                for name in self.packages.get(total_list, []):
                    if name not in cp[key]:
                        skipped.append(name)
            if skipped:
                self._add_review_item(f"Skipped (toggled off): {', '.join(skipped)}")

    def _add_review_label(self, text, bold=False, margin_top=0):
        lbl = Gtk.Label(label=text)
        lbl.add_css_class("om-summary-label")
        lbl.set_xalign(0)
        if margin_top:
            lbl.set_margin_top(margin_top)
        self.review_content.append(lbl)

    def _add_review_item(self, text):
        lbl = Gtk.Label(label=text)
        lbl.add_css_class("om-summary-item")
        lbl.set_xalign(0)
        lbl.set_wrap(True)
        lbl.set_max_width_chars(80)
        self.review_content.append(lbl)

    # --- save & apply ---

    def _on_save_config(self, _btn=None):
        config = build_config(
            self.selected_categories,
            self.plugin_states,
            self.plugin_toggles,
            self.plugins_to_add,
            self.extra_pacman,
            self.extra_aur,
            self.category_order,
            self.configured_pkg_toggles,
            self.packages,
            confirm_close=self.confirm_close_enabled,
            ollama_install=self.ollama_enabled,
            ollama_models=self.ollama_models_to_pull,
        )

        dialog = Gtk.FileDialog()
        dialog.set_title("Save Omarchy config")
        dialog.set_initial_name("omarchy-config.json")
        dialog.set_initial_folder(Gio.File.new_for_path(str(Path.home())))

        def on_save(_dialog, result):
            try:
                file = dialog.save_finish(result)
            except GLib.Error:
                return  # User cancelled.
            path = file.get_path()
            Path(path).write_text(
                json.dumps(config, indent=2) + "\n",
                encoding="utf-8",
            )
            self.saved_config_path = path
            self.nav_apply_btn.set_sensitive(True)
            self.nav_apply_btn.remove_css_class("om-nav-btn-danger")
            self.nav_apply_btn.add_css_class("om-nav-btn-primary")
            self.save_status.set_text(
                f"Saved to: {path}\n\nApply with:\n"
                f"  {self.setup_dir}/apply.sh --myconfig {shlex.quote(path)}"
            )
            self.save_status.remove_css_class("om-subtitle")
            self.save_status.add_css_class("om-success")

        dialog.save(self.win, None, on_save)

    def _on_apply_now(self, _btn):
        if not self.saved_config_path:
            return
        path = self.saved_config_path
        apply_cmd = (
            f"{shlex.quote(self.setup_dir + '/apply.sh')} "
            f"--myconfig {shlex.quote(path)}"
        )

        # Try to launch in a terminal so output is visible.
        launched = False
        for term in ("xdg-terminal-exec", "alacritty", "foot", "kitty", "ghostty"):
            if not shutil.which(term):
                continue
            if term == "xdg-terminal-exec":
                subprocess.Popen([term, apply_cmd])
            elif term in ("foot",):
                subprocess.Popen([term, "-e", "bash", "-c",
                                  apply_cmd + "; echo; read -rp 'Press Enter to close…'"])
            else:
                subprocess.Popen([term, "-e", "bash", "-c",
                                  apply_cmd + "; echo; read -rp 'Press Enter to close…'"])
            launched = True
            break

        if not launched:
            # No terminal — show the command to run manually.
            self.save_status.set_text(
                f"No terminal found. Run in a terminal:\n  {apply_cmd}"
            )
        else:
            self.save_status.set_text(
                f"Applying from {path} — see the terminal window for output."
            )

    # --- shared row builder ---

    def _make_toggle_row(self, name, description, active):
        """Build a horizontal row: label+desc on the left, a Switch on the right."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("om-row")

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        left.set_hexpand(True)

        name_lbl = Gtk.Label(label=name)
        name_lbl.add_css_class("om-row-name")
        name_lbl.set_xalign(0)
        left.append(name_lbl)

        if description:
            desc_lbl = Gtk.Label(label=description)
            desc_lbl.add_css_class("om-row-desc")
            desc_lbl.set_xalign(0)
            desc_lbl.set_wrap(True)
            desc_lbl.set_max_width_chars(60)
            left.append(desc_lbl)

        row.append(left)

        switch = Gtk.Switch()
        switch.set_active(active)
        switch.set_valign(Gtk.Align.CENTER)
        row.append(switch)
        row.switch_widget = switch
        return row


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def main():
    if len(sys.argv) < 2:
        print("Usage: wizard.py <setup-dir> [--print-json]", file=sys.stderr)
        return 1

    setup_dir = sys.argv[1]
    extra_args = sys.argv[2:]

    if "--print-json" in extra_args:
        config = default_config(setup_dir)
        print(json.dumps(config, indent=2))
        return 0

    app = WizardApp(setup_dir)
    # Pass only the program name to app.run() — the setup-dir path in sys.argv
    # would be interpreted by GApplication as a file to open, triggering
    # "This application can not open files" criticals since we don't set
    # G_APPLICATION_HANDLES_OPEN. setup_dir is already stored on the instance.
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
