-- Keep only your personal keybinding overrides here. Add new bindings or
-- unbind defaults before replacing them.

-- See current bindings and descriptions:
--   omarchy menu keybindings --print

-- To disable every Omarchy default binding, set this in
-- ~/.config/hypr/hyprland.lua before require("default.hypr.omarchy"), then add
-- only the bindings you want below:
--   omarchy_default_bindings = false

-- To disable all preinstalled app/webapp bindings, set:
--   omarchy_preinstalled_bindings = false

-- Add a new binding.
-- o.bind("SUPER + SHIFT + R", "SSH", "alacritty -e ssh your-server")

-- Confirm before closing the active window (was: Close window, hl.dsp.window.close()).
-- The confirm dialog + close lives in a script so focus/timing and quoting are
-- handled outside the Lua string: it captures the focused window's address
-- before zenity steals focus, then closes that address on Yes.
hl.unbind("SUPER + W")
o.bind("SUPER + W", "Close window (confirm)", "/home/david/.local/bin/omarchy-confirm-close")

-- Lock the screen (mirrors the default SUPER + CTRL + L).
o.bind("SUPER + ALT + L", "Lock system", "omarchy-system-lock")

-- Region screenshot, macOS-style (Cmd+Shift+4 equivalent).
-- Unbind existing SUPER + ALT + 4 (was: Switch to group window 4, bound by keycode).
hl.unbind("SUPER + ALT + code:13")
o.bind("SUPER + ALT + 4", "Screenshot region", "omarchy-capture-screenshot region")

-- Workspace Switcher (io.github.woogy7.workspaces): hold ALT, tap TAB to cycle
-- workspaces, release ALT to switch (was: App switcher next/prev).
local ws_switcher = { id = "io.github.woogy7.workspaces", timer = nil, held = false }
local ws_hold_keys = { "Alt_L", "Alt_R" }

local function ws_switcher_send(action)
  hl.exec_cmd("omarchy-shell shell summon " .. ws_switcher.id
    .. " '{\"action\":\"" .. action .. "\",\"modifier\":\"alt\"}'")
end

local function ws_switcher_watch_release()
  if ws_switcher.held then return end
  ws_switcher.held = true
  if ws_switcher.timer then ws_switcher.timer:set_enabled(false) end
  ws_switcher.timer = hl.timer(function()
    if not ws_switcher.held then return end
    local down = false
    for _, k in ipairs(ws_hold_keys) do if hl.is_key_down(k) then down = true end end
    if not down then
      ws_switcher.held = false
      if ws_switcher.timer then ws_switcher.timer:set_enabled(false) end
      ws_switcher_send("commit")
    end
  end, { timeout = 25, type = "repeat" })
end

hl.unbind("ALT + TAB")
hl.unbind("ALT + SHIFT + TAB")
o.bind("ALT + TAB", "Workspace switcher (next)", function()
  ws_switcher_send(ws_switcher.held and "next" or "open-next")
  ws_switcher_watch_release()
end)
o.bind("ALT + SHIFT + TAB", "Workspace switcher (previous)", function()
  ws_switcher_send(ws_switcher.held and "prev" or "open-prev")
  ws_switcher_watch_release()
end)

-- GloView: cycle the displayed/live workspace (was: Next/Previous workspace).
-- Registered only when the GloView plugin is loaded (see hyprland.lua); when it's
-- absent the default SUPER+TAB / SUPER+SHIFT+TAB workspace bindings stay active.
if hl.plugin.gloview then
  hl.unbind("SUPER + TAB")
  hl.unbind("SUPER + SHIFT + TAB")
  hl.bind("SUPER + TAB", hl.plugin.gloview.next)
  hl.bind("SUPER + SHIFT + TAB", hl.plugin.gloview.prev)
  hl.bind("SUPER + GRAVE", hl.plugin.gloview.desktop)
end

o.bind("SUPER + SHIFT + L", "Lock screen explorer", "omarchy-shell lock explore")

-- Change an existing binding by unbinding it first, then binding the key again.
-- This example changes SUPER+SPACE from the launcher to the Omarchy root menu.
-- hl.unbind("SUPER + SPACE")
-- o.bind("SUPER + SPACE", "Omarchy menu", "omarchy-menu toggle root")

-- Disable a default binding without replacing it.
-- hl.unbind("SUPER + SHIFT + B")

-- Logitech MX Keys examples:
-- o.bind("SUPER + SHIFT + S", nil, "omarchy-capture-screenshot")
-- o.bind("SUPER + H", nil, "voxtype record toggle")
-- o.bind("SUPER + PERIOD", nil, "omarchy-shell shell toggle omarchy.emojis")

-- Side mouse buttons (thumb cluster) switch workspaces, like the 3-finger swipe.
-- Bare binds (no modifier): just press the side button to switch workspace.
-- NOTE: this overrides the side buttons' usual browser back/forward navigation.
-- Codes cover both reporting styles:
--   275=BTN_SIDE / 278=BTN_BACK  -> previous workspace
--   276=BTN_EXTRA / 277=BTN_FORWARD -> next workspace
o.bind("mouse:275", "Previous workspace (side button)", hl.dsp.focus({ workspace = "e-1" }), { mouse = true })
o.bind("mouse:278", "Previous workspace (side button)", hl.dsp.focus({ workspace = "e-1" }), { mouse = true })
o.bind("mouse:276", "Next workspace (side button)",     hl.dsp.focus({ workspace = "e+1" }), { mouse = true })
o.bind("mouse:277", "Next workspace (side button)",     hl.dsp.focus({ workspace = "e+1" }), { mouse = true })

-- BEGIN im0001gt.screens
hl.unbind("SUPER + SLASH")
hl.unbind("SUPER + ALT + SLASH")
o.bind("SUPER + SLASH", "Monitor scaling up", "/home/david/.config/omarchy/plugins/im0001gt.screens/scripts/display-ctl scale up")
o.bind("SUPER + ALT + SLASH", "Monitor scaling down", "/home/david/.config/omarchy/plugins/im0001gt.screens/scripts/display-ctl scale down")
-- END im0001gt.screens
