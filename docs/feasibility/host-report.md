# XR feasibility report

Read-only inventory; performance and tracking remain untested.

<pre>
{
  &quot;capabilities&quot;: {
    &quot;active_120_hz&quot;: {
      &quot;evidence&quot;: [],
      &quot;reason&quot;: &quot;Selected compositor output only; not attributed to VITURE and not measured presentation.&quot;,
      &quot;state&quot;: &quot;untested&quot;
    },
    &quot;advertised_120_hz&quot;: {
      &quot;evidence&quot;: [],
      &quot;reason&quot;: &quot;Refresh-bearing available modes missing or inconclusive; support unknown.&quot;,
      &quot;state&quot;: &quot;untested&quot;
    },
    &quot;sustained_120_hz&quot;: {
      &quot;evidence&quot;: [],
      &quot;reason&quot;: &quot;No presentation timing measurements in milestone A.&quot;,
      &quot;state&quot;: &quot;untested&quot;
    },
    &quot;tracking_quality&quot;: {
      &quot;evidence&quot;: [],
      &quot;reason&quot;: &quot;No SDK execution or tracking measurements in milestone A.&quot;,
      &quot;state&quot;: &quot;untested&quot;
    }
  },
  &quot;collected_at&quot;: &quot;2026-10-31T15:09:31.032756+00:00&quot;,
  &quot;drm&quot;: {
    &quot;data&quot;: [
      {
        &quot;gpu_device&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card0-DP-3/device&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;/sys/devices/pci0000:00/0000:00:08.1/0000:12:00.0/drm/card0&quot;
        },
        &quot;modes&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card0-DP-3/modes&quot;,
          &quot;status&quot;: &quot;unknown&quot;,
          &quot;value&quot;: null
        },
        &quot;name&quot;: &quot;card0-DP-3&quot;,
        &quot;status&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card0-DP-3/status&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;disconnected&quot;
        }
      },
      {
        &quot;gpu_device&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card0-DP-4/device&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;/sys/devices/pci0000:00/0000:00:08.1/0000:12:00.0/drm/card0&quot;
        },
        &quot;modes&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card0-DP-4/modes&quot;,
          &quot;status&quot;: &quot;unknown&quot;,
          &quot;value&quot;: null
        },
        &quot;name&quot;: &quot;card0-DP-4&quot;,
        &quot;status&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card0-DP-4/status&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;disconnected&quot;
        }
      },
      {
        &quot;gpu_device&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card0-DP-5/device&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;/sys/devices/pci0000:00/0000:00:08.1/0000:12:00.0/drm/card0&quot;
        },
        &quot;modes&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card0-DP-5/modes&quot;,
          &quot;status&quot;: &quot;unknown&quot;,
          &quot;value&quot;: null
        },
        &quot;name&quot;: &quot;card0-DP-5&quot;,
        &quot;status&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card0-DP-5/status&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;disconnected&quot;
        }
      },
      {
        &quot;gpu_device&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card0-HDMI-A-3/device&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;/sys/devices/pci0000:00/0000:00:08.1/0000:12:00.0/drm/card0&quot;
        },
        &quot;modes&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card0-HDMI-A-3/modes&quot;,
          &quot;status&quot;: &quot;unknown&quot;,
          &quot;value&quot;: null
        },
        &quot;name&quot;: &quot;card0-HDMI-A-3&quot;,
        &quot;status&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card0-HDMI-A-3/status&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;disconnected&quot;
        }
      },
      {
        &quot;gpu_device&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card0-Writeback-2/device&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;/sys/devices/pci0000:00/0000:00:08.1/0000:12:00.0/drm/card0&quot;
        },
        &quot;modes&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card0-Writeback-2/modes&quot;,
          &quot;status&quot;: &quot;unknown&quot;,
          &quot;value&quot;: null
        },
        &quot;name&quot;: &quot;card0-Writeback-2&quot;,
        &quot;status&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card0-Writeback-2/status&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;unknown&quot;
        }
      },
      {
        &quot;gpu_device&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card1-DP-1/device&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;/sys/devices/pci0000:00/0000:00:01.1/0000:01:00.0/0000:02:00.0/0000:03:00.0/drm/card1&quot;
        },
        &quot;modes&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card1-DP-1/modes&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: [
            &quot;5120x1440&quot;,
            &quot;3840x1080&quot;,
            &quot;3840x2160&quot;,
            &quot;3840x2160&quot;,
            &quot;3840x2160&quot;,
            &quot;5120x1440&quot;,
            &quot;5120x1440&quot;,
            &quot;3840x1080&quot;,
            &quot;3840x1080&quot;,
            &quot;2560x1440&quot;,
            &quot;2560x1440&quot;,
            &quot;2560x1440&quot;,
            &quot;1920x1080&quot;,
            &quot;1920x1080&quot;,
            &quot;1920x1080&quot;,
            &quot;1920x1080&quot;,
            &quot;1920x1080&quot;,
            &quot;1680x1050&quot;,
            &quot;1600x900&quot;,
            &quot;1280x1024&quot;,
            &quot;1440x900&quot;,
            &quot;1280x800&quot;,
            &quot;1280x720&quot;,
            &quot;1280x720&quot;,
            &quot;1280x720&quot;,
            &quot;1024x768&quot;,
            &quot;800x600&quot;,
            &quot;720x480&quot;,
            &quot;720x480&quot;,
            &quot;640x480&quot;,
            &quot;640x480&quot;
          ]
        },
        &quot;name&quot;: &quot;card1-DP-1&quot;,
        &quot;status&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card1-DP-1/status&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;connected&quot;
        }
      },
      {
        &quot;gpu_device&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card1-DP-2/device&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;/sys/devices/pci0000:00/0000:00:01.1/0000:01:00.0/0000:02:00.0/0000:03:00.0/drm/card1&quot;
        },
        &quot;modes&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card1-DP-2/modes&quot;,
          &quot;status&quot;: &quot;unknown&quot;,
          &quot;value&quot;: null
        },
        &quot;name&quot;: &quot;card1-DP-2&quot;,
        &quot;status&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card1-DP-2/status&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;disconnected&quot;
        }
      },
      {
        &quot;gpu_device&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card1-HDMI-A-1/device&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;/sys/devices/pci0000:00/0000:00:01.1/0000:01:00.0/0000:02:00.0/0000:03:00.0/drm/card1&quot;
        },
        &quot;modes&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card1-HDMI-A-1/modes&quot;,
          &quot;status&quot;: &quot;unknown&quot;,
          &quot;value&quot;: null
        },
        &quot;name&quot;: &quot;card1-HDMI-A-1&quot;,
        &quot;status&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card1-HDMI-A-1/status&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;disconnected&quot;
        }
      },
      {
        &quot;gpu_device&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card1-HDMI-A-2/device&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;/sys/devices/pci0000:00/0000:00:01.1/0000:01:00.0/0000:02:00.0/0000:03:00.0/drm/card1&quot;
        },
        &quot;modes&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card1-HDMI-A-2/modes&quot;,
          &quot;status&quot;: &quot;unknown&quot;,
          &quot;value&quot;: null
        },
        &quot;name&quot;: &quot;card1-HDMI-A-2&quot;,
        &quot;status&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card1-HDMI-A-2/status&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;disconnected&quot;
        }
      },
      {
        &quot;gpu_device&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card1-Writeback-1/device&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;/sys/devices/pci0000:00/0000:00:01.1/0000:01:00.0/0000:02:00.0/0000:03:00.0/drm/card1&quot;
        },
        &quot;modes&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card1-Writeback-1/modes&quot;,
          &quot;status&quot;: &quot;unknown&quot;,
          &quot;value&quot;: null
        },
        &quot;name&quot;: &quot;card1-Writeback-1&quot;,
        &quot;status&quot;: {
          &quot;source&quot;: &quot;/sys/class/drm/card1-Writeback-1/status&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;unknown&quot;
        }
      }
    ],
    &quot;detail&quot;: &quot;&quot;,
    &quot;status&quot;: &quot;ok&quot;
  },
  &quot;host&quot;: {
    &quot;architecture&quot;: {
      &quot;source&quot;: &quot;host.architecture&quot;,
      &quot;status&quot;: &quot;ok&quot;,
      &quot;value&quot;: &quot;x86_64&quot;
    },
    &quot;gpu&quot;: {
      &quot;source&quot;: &quot;host.gpu&quot;,
      &quot;status&quot;: &quot;ok&quot;,
      &quot;value&quot;: &quot;03:00.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] Navi 32 [Radeon RX 7700 XT / 7800 XT] (rev c8); 12:00.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] Granite Ridge [Radeon 610M] (rev c5)&quot;
    },
    &quot;packages&quot;: {
      &quot;hyprland&quot;: {
        &quot;source&quot;: &quot;host.packages.hyprland&quot;,
        &quot;status&quot;: &quot;ok&quot;,
        &quot;value&quot;: &quot;0.56.2&quot;
      },
      &quot;libdrm&quot;: {
        &quot;source&quot;: &quot;host.packages.libdrm&quot;,
        &quot;status&quot;: &quot;ok&quot;,
        &quot;value&quot;: &quot;2.4.134&quot;
      },
      &quot;linux&quot;: {
        &quot;source&quot;: &quot;host.packages.linux&quot;,
        &quot;status&quot;: &quot;unknown&quot;,
        &quot;value&quot;: null
      },
      &quot;mesa&quot;: {
        &quot;source&quot;: &quot;host.packages.mesa&quot;,
        &quot;status&quot;: &quot;unknown&quot;,
        &quot;value&quot;: null
      },
      &quot;nvidia-utils&quot;: {
        &quot;source&quot;: &quot;host.packages.nvidia-utils&quot;,
        &quot;status&quot;: &quot;unknown&quot;,
        &quot;value&quot;: null
      }
    }
  },
  &quot;schema_version&quot;: 1,
  &quot;session&quot;: {
    &quot;classification&quot;: &quot;physical&quot;,
    &quot;outputs&quot;: [
      {
        &quot;active_mode&quot;: {
          &quot;height&quot;: {
            &quot;source&quot;: &quot;session.data.outputs[0].height&quot;,
            &quot;status&quot;: &quot;ok&quot;,
            &quot;value&quot;: 1440
          },
          &quot;refresh_hz&quot;: {
            &quot;source&quot;: &quot;session.data.outputs[0].refreshRate&quot;,
            &quot;status&quot;: &quot;ok&quot;,
            &quot;value&quot;: 240.0
          },
          &quot;width&quot;: {
            &quot;source&quot;: &quot;session.data.outputs[0].width&quot;,
            &quot;status&quot;: &quot;ok&quot;,
            &quot;value&quot;: 5120
          }
        },
        &quot;available_modes&quot;: {
          &quot;source&quot;: &quot;session.data.outputs[0].available_modes&quot;,
          &quot;status&quot;: &quot;unknown&quot;,
          &quot;value&quot;: null
        },
        &quot;classification&quot;: &quot;physical&quot;,
        &quot;disabled&quot;: {
          &quot;source&quot;: &quot;session.data.outputs[0].disabled&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: false
        },
        &quot;make&quot;: {
          &quot;source&quot;: &quot;session.data.outputs[0].make&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;Samsung Electric Company&quot;
        },
        &quot;model&quot;: {
          &quot;source&quot;: &quot;session.data.outputs[0].model&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;Odyssey G93SD&quot;
        },
        &quot;name&quot;: {
          &quot;source&quot;: &quot;session.data.outputs[0].name&quot;,
          &quot;status&quot;: &quot;ok&quot;,
          &quot;value&quot;: &quot;DP-1&quot;
        }
      }
    ],
    &quot;status&quot;: &quot;ok&quot;
  },
  &quot;usb&quot;: {
    &quot;data&quot;: [],
    &quot;detail&quot;: &quot;&quot;,
    &quot;status&quot;: &quot;ok&quot;
  }
}
</pre>
