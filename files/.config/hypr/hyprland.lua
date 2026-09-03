-- Learn how to configure Hyprland: https://wiki.hypr.land/Configuring/Start/

-- Omarchy's bootstrap keeps path setup out of this user config.
dofile((os.getenv("OMARCHY_PATH") or "/usr/share/omarchy") .. "/default/hypr/bootstrap.lua")

-- Load the GloView Hyprland plugin at parse time, before hypr/input.lua and
-- hypr/bindings.lua reference hl.plugin.gloview. hyprpm-based autostart
-- loading (see hypr/autostart.lua) runs on the `hyprland.start` event, which
-- fires AFTER config parse -- too late: the gloview binds/gestures would
-- silently fail to register on login. Loading here makes hl.plugin.gloview
-- available while the config is being parsed.
local gloview_so = "/usr/lib/gloview.so"
local gloview_file = io.open(gloview_so, "r")
if gloview_file then
  gloview_file:close()
  hl.plugin.load(gloview_so)
end

-- Disable all Omarchy default bindings. Add your own in hypr/bindings.lua.
-- omarchy_default_bindings = false
--
-- Or disable only bindings for Omarchy's preinstalled apps/web apps while
-- keeping core window-manager bindings:
-- omarchy_preinstalled_bindings = false

-- Load Omarchy defaults.
require("default.hypr.omarchy")

-- Hybrid graphics override: render the desktop on the Intel iGPU and
-- DisplayLink (evdi) devices only, so the NVIDIA Quadro dGPU can enter RTD3
-- (D3cold) when no app is explicitly offloading to it. This overrides
-- Omarchy's default nvidia.lua, which sets __GLX_VENDOR_LIBRARY_NAME=nvidia
-- globally and pins the dGPU awake, defeating dynamic power management.
-- Monitors: i915 (card2) drives eDP-1; evdi (card0/card3) drive the two
-- external Samsung displays; the NVIDIA dGPU (card1) drives nothing.
-- To run a specific app on the dGPU, use: prime-run <app>
hl.env("WLR_DRM_DEVICES", "/dev/dri/by-path/pci-0000:00:02.0-card,/dev/dri/by-path/platform-evdi.0-card,/dev/dri/by-path/platform-evdi.1-card")
-- Counter Omarchy nvidia.lua (which sets these to nvidia during config parse)
-- so child apps render on the Intel iGPU. prime-run overrides per-app.
hl.env("__GLX_VENDOR_LIBRARY_NAME", "mesa")
hl.env("LIBVA_DRIVER_NAME", "iHD")
hl.env("NVD_BACKEND", "")

-- Put your personal overrides in these files.
-- in ~/.local/share/applications/io.element.Element.desktop. We intentionally
-- do NOT append GNOME to XDG_CURRENT_DESKTOP here: doing so broke
-- xdg-desktop-portal-hyprland's desktop detection and can nudge Electron/GTK
-- apps onto slower rendering paths (felt as sluggishness/tearing).

-- Put your personal overrides in these files. They're loaded after Omarchy's
-- defaults so package updates can improve the defaults without rewriting your
-- ~/.config/hypr files.
require("hypr.monitors")
require("hypr.input")
require("hypr.bindings")
require("hypr.looknfeel")
require("hypr.autostart")

-- Toggle config flags dynamically.
require("default.hypr.toggles")

-- Add any other personal Hyprland configuration below.
-- o.window("qemu", { workspace = "5" })
