-- See https://wiki.hypr.land/Configuring/Basics/Monitors/
-- Managed by im0001gt.screens (Screens bar panel).

local omarchy_gdk_scale = 1
hl.env("GDK_SCALE", tostring(omarchy_gdk_scale))

-- BEGIN im0001gt.screens
hl.monitor({ output = "eDP-1", mode = "1920x1200@59.95", position = "0x0", scale = 1.25, vrr = 0 })
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = "auto" })
hl.config({ misc = { vrr = 0 }, render = { cm_auto_hdr = 0 } })
-- END im0001gt.screens

