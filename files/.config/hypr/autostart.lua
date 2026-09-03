-- Extra autostart processes.
-- o.launch_on_start("my-service")

-- Load the GloView Hyprland plugin (managed by hyprpm) on every session start.
-- hyprpm-loaded plugins are only registered in the running compositor process,
-- so without this, hl.plugin.gloview is nil after every reboot/login until
-- `hyprpm reload -n` is run manually again.
o.exec_on_start("hyprpm reload -n")
