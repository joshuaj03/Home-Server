-- Custom WirePlumber configuration for HomePi Smart Speaker
-- Enforces standard SBC codec (prevents SBC-XQ packet loss on Pi 5)
-- Sets 44.1 kHz default rate and disables auto-suspend for instant playback

bluez_monitor.properties = {
  ["bluez5.enable-sbc-xq"] = false,
  ["bluez5.codecs"] = "[ sbc aac ]",
  ["bluez5.default.rate"] = 44100,
}

table.insert(bluez_monitor.rules, {
  matches = {
    {
      { "device.name", "matches", "bluez_card.*" },
    },
  },
  apply_properties = {
    ["device.profile"] = "a2dp-sink",
  },
})

table.insert(bluez_monitor.rules, {
  matches = {
    {
      { "node.name", "matches", "bluez_output.*" },
    },
  },
  apply_properties = {
    ["session.suspend-timeout-seconds"] = 0,
    ["resample.quality"] = 4,
  },
})
