# Tigersecu DVR Integration for Home Assistant

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bwarden&repository=hacs-tigersecu-dvr&category=integration)

This custom component integrates [Tigersecu](https://www.tigersecu.com/) Digital Video Recorders (DVRs) into Home Assistant. It connects to the DVR's websocket interface to provide real-time status updates and camera streams.

## Supported Hardware

[Tigersecu DVR models](https://www.tigersecu.com/firmware-upgrading/) I have only tested this successfully on my model 16CH (E_IV_HB) DVR. It *might* work on other Nova Super HD DVRs, *maybe* on H.264 Super HD DVRs, but will almost certainly **not** support the Tuya-based models.

## Features

*   **Camera Streams**: Creates a camera entity for each channel on the DVR, providing a live RTSP stream.
*   **Motion Detection**: A binary sensor for each channel that indicates when motion is detected.
*   **Video Loss Detection**: A binary sensor for each channel to report video signal loss.
*   **Recording Status**: The camera entity will show if it is currently recording.
*   **External Alarm Sensors**: Dynamically creates binary sensors for any connected external alarm inputs.
*   **Diagnostic Sensors**: Provides various sensors for system health and status, including:
    *   Network details (IP, MAC, Gateway, etc.).
    *   Disk status, capacity, and model information.
    *   Disk S.M.A.R.T. attributes (disabled by default).
    *   Last login information.

## Installation

### HACS (Recommended)

1.  This integration is available in the [Home Assistant Community Store (HACS)](https://hacs.xyz/).
2.  Use the "Open in HACS" button above to add the repository.
3.  Search for "Tigersecu DVR" and install it.
4.  Restart Home Assistant.

### Manual Installation

1.  Copy the `custom_components/tigersecu_dvr` directory into your Home Assistant `custom_components` directory.
2.  Restart Home Assistant.

## Configuration

Configuration is done via the UI.

1.  Go to **Settings** -> **Devices & Services**.
2.  Click **Add Integration** and search for **Tigersecu DVR**.
3.  Enter the required information:
    *   **Host**: The IP address or hostname of your DVR.
    *   **Username**: The username for your DVR.
    *   **Password**: The password for your DVR.
4.  Click **Submit**. The integration will connect to the DVR and automatically discover all available channels and sensors.

## Entities Provided

This integration will create a device in Home Assistant representing your DVR. The following entities will be associated with that device. The entity IDs will be based on the DVR's host name and the entity name (e.g., `camera.my_dvr_ch01`).

### Camera

*   **Channel Camera**: A camera entity for each channel (e.g., `CH01`, `CH02`), providing a live RTSP stream.
    *   **Attributes**: `chip`, `format` (from `VideoInput` event).

### Binary Sensors

*   **Motion**: A binary sensor for each channel that turns `on` when motion is detected.
*   **Video Loss**: A binary sensor for each channel that turns `on` when the video signal is lost. This will also mark the corresponding camera entity as unavailable.
*   **External Alarm**: (Optional) A binary sensor for each external alarm input that turns `on` when triggered. These are created dynamically if detected.
*   **Time Sync Problem**: (Diagnostic, disabled by default) A binary sensor that turns `on` if the DVR's clock is out of sync with Home Assistant's clock.

### Sensors

The following sensors are created to provide diagnostic information.

*   **Network**:
    *   IP Address
    *   MAC Address
    *   Gateway
    *   External IP
    *   Link Speed
*   **System**:
    *   **Last Login**: Shows the user and source of the last login event.
    *   **Disk Scheme**: The current recording mode (e.g., Continuous, Scheduled/Motion).
*   **Disk (per disk)**:
    *   Model
    *   Status
    *   Capacity
    *   Available Space
*   **S.M.A.R.T. (per disk, per attribute, disabled by default)**:
    *   The value of a specific S.M.A.R.T. attribute. Additional details are available as state attributes. You can enable these sensors from the entity settings if needed.

### Update

*   **DVR Update**: An update entity that reports the progress of a firmware update initiated on the DVR.

## Troubleshooting

If you have trouble connecting, ensure:
*   Your DVR is on the same network as your Home Assistant instance.
*   The username and password are correct.
*   There are no firewalls blocking the connection to the DVR's web port (usually port 80).

If channels are not discovered, the integration will attempt to reconnect. Check the Home Assistant logs for any error messages from the `pytigersecu` library or the `tigersecu_dvr` integration.

## DVR Events

The integration listens for the following event types from the DVR's websocket.

| Event Type           | Handled | Associated Entities / Purpose                                                              |
|----------------------|---------|--------------------------------------------------------------------------------------------|
| `DateTime`           | Yes     | `binary_sensor.time_sync_problem`                                                          |
| `Disk`               | Yes     | `sensor.disk_X_model`, `sensor.disk_X_status`, `sensor.disk_X_capacity`, `sensor.disk_X_available` |
| `Login`              | Yes     | `sensor.last_login`                                                                        |
| `Motion`             | Yes     | `binary_sensor.motion_chXX`                                                                |
| `Network`            | Yes     | `sensor.ip_address`, `sensor.mac_address`, `sensor.gateway`, `sensor.external_ip`, `sensor.link_speed` |
| `Record`             | Yes     | `camera.chXX` (is_recording attribute)                                                     |
| `Scheme`             | Yes     | `sensor.disk_scheme`                                                                       |
| `Sensor`             | Yes     | `binary_sensor.sensor_X` (dynamically created)                                             |
| `SMART`              | Yes     | `sensor.disk_X_smart_Y`                                                                    |
| `UpgradeProgress`    | Yes     | `update.dvr_update` (progress)                                                             |
| `VideoInput`         | Yes     | `camera.chXX` (discovery, attributes)                                                      |
| `VLOSS`              | Yes     | `binary_sensor.video_loss_chXX`, `camera.chXX` (availability)                              |
| `ConfigChange`       | No      | -                                                                                          |
| `ErrorAuthorization` | No      | -                                                                                          |
| `Logout`             | No      | -                                                                                          |
| `SendRemote`         | No      | -                                                                                          |
| `SrvFd`              | No      | -                                                                                          |
