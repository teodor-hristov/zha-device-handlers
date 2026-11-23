"""Device handler for Shelly I4 and I4 Mini.

The device is a remote, every endpoint but 239 carries client clusters only:
  1..4  one per input: Scenes recall scene 1/2/3/4 = single/double/triple/long
        push, 5 = switch toggle (group id = endpoint id)
  5, 6  one per input pair (1+2, 3+4): WindowCovering, long push on the first
        input = up_open, on the second = down_close, release = stop. A short
        push also sends an OnOff toggle.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from zha.application import Platform
from zha.application.platforms import ZclPlatformEntity
from zha.application.platforms.event import BaseEvent
from zigpy.zcl import ClusterType
from zigpy.zcl.clusters.closures import WindowCovering
from zigpy.zcl.clusters.general import OnOff, Scenes

from zhaquirks.builder import ButtonEventType, EventDeviceClass, QuirkBuilder
from zhaquirks.shelly.wifi import with_wifi_setup

I4_INPUTS = 4
I4_PAIRS = 2


def pair_endpoint_id(input_number: int) -> int:
    """Return the endpoint of the input pair the input belongs to."""
    return I4_INPUTS + 1 + (input_number - 1) // 2


class _CommandListener:
    """zigpy cluster listener that forwards incoming commands to a callback."""

    def __init__(self, callback: Callable[[int, Any], None]) -> None:
        """Initialize the listener."""
        self._callback = callback

    def cluster_command(self, tsn: int, command_id: int, args: Any) -> None:
        """Forward an incoming command."""
        self._callback(command_id, args)


class _ClusterCommandEvent(BaseEvent, ZclPlatformEntity):
    """Event entity that listens to the commands of client clusters."""

    def _listen(self, cluster, callback: Callable[[int, Any], None]) -> None:
        """Forward the commands of `cluster` to `callback` until removal."""
        listener = _CommandListener(callback)
        cluster.add_listener(listener)
        self._on_remove_callbacks.append(lambda: cluster.remove_listener(listener))


class ShellyI4ButtonEvent(_ClusterCommandEvent):
    """Button event entity of one input, fed by its Scenes recall commands.

    The input endpoint does not report the release of a long push. The pair
    endpoint of the input sends WindowCovering `stop` on that release, so it
    ends the long push of whichever input of the pair started one.
    """

    _attr_device_class = EventDeviceClass.BUTTON
    # The press count is spelled out instead of `multi_press` + `press_count`, so
    # the state shows it directly. Scene 5 is the switch toggle, not a gesture.
    _SCENE_EVENTS = {
        1: ButtonEventType.SINGLE_PRESS,
        2: "double_press",
        3: "triple_press",
        4: ButtonEventType.LONG_PRESS_START,
    }
    _attr_event_types = [*_SCENE_EVENTS.values(), ButtonEventType.LONG_PRESS_END]

    def __init__(self, *args: Any, pair_endpoint_id: int, **kwargs: Any) -> None:
        """Initialize the event entity of one input."""
        super().__init__(*args, **kwargs)
        self._pair_cluster = self.device.endpoints[
            pair_endpoint_id
        ].zigpy_endpoint.out_clusters[WindowCovering.cluster_id]
        self._long_pushed = False

    def on_add(self) -> None:
        """Subscribe to the commands of the input and of its pair."""
        super().on_add()
        self._listen(self._cluster, self._handle_scenes_command)
        self._listen(self._pair_cluster, self._handle_pair_command)

    def _handle_scenes_command(self, command_id: int, args: Any) -> None:
        """Fire the gesture of a recalled scene."""
        if command_id != Scenes.ServerCommandDefs.recall.id:
            return

        event_type = self._SCENE_EVENTS.get(args.scene_id)
        if event_type is None:
            return

        self._long_pushed = event_type == ButtonEventType.LONG_PRESS_START
        self._trigger_event(event_type)

    def _handle_pair_command(self, command_id: int, args: Any) -> None:
        """End a long push on the stop its release sends."""
        if command_id != WindowCovering.ServerCommandDefs.stop.id:
            return

        if not self._long_pushed:
            return

        self._long_pushed = False
        self._trigger_event(ButtonEventType.LONG_PRESS_END)


class ShellyI4CoverEvent(_ClusterCommandEvent):
    """Event entity of one input pair, fed by its WindowCovering commands."""

    _COMMAND_EVENTS: dict[int, str] = {
        WindowCovering.ServerCommandDefs.up_open.id: "opening",
        WindowCovering.ServerCommandDefs.down_close.id: "closing",
        WindowCovering.ServerCommandDefs.stop.id: "stopped",
    }
    _attr_event_types = list(_COMMAND_EVENTS.values())

    def on_add(self) -> None:
        """Subscribe to the commands of the pair."""
        super().on_add()
        self._listen(self._cluster, self._handle_command)

    def _handle_command(self, command_id: int, args: Any) -> None:
        """Fire the movement of a WindowCovering command."""
        event_type = self._COMMAND_EVENTS.get(command_id)
        if event_type is not None:
            self._trigger_event(event_type)


_builder = with_wifi_setup(QuirkBuilder("Shelly", "I4")).applies_to("Shelly", "I4 Mini")

for _input in range(1, I4_INPUTS + 1):
    _builder = _builder.adds_entity(
        ShellyI4ButtonEvent,
        endpoint_id=_input,
        cluster_id=Scenes.cluster_id,
        cluster_type=ClusterType.Client,
        force_create=True,
        pair_endpoint_id=pair_endpoint_id(_input),
        unique_id_suffix=f"button_{_input}",
        translation_key="button_numbered",
        translation_placeholders={"number": str(_input)},
        fallback_name=f"Button {_input}",
    )

for _pair in range(1, I4_PAIRS + 1):
    _endpoint_id = I4_INPUTS + _pair
    _first = 2 * _pair - 1
    _builder = (
        _builder.adds_entity(
            ShellyI4CoverEvent,
            endpoint_id=_endpoint_id,
            cluster_id=WindowCovering.cluster_id,
            cluster_type=ClusterType.Client,
            force_create=True,
            unique_id_suffix=f"inputs_{_first}_{_first + 1}_cover",
            fallback_name=f"Cover inputs {_first}-{_first + 1}",
        )
        # The default opening sensor of the pair OnOff client only follows the
        # toggle a short push sends, which says nothing about the cover
        .prevent_default_entity_creation(
            endpoint_id=_endpoint_id,
            cluster_id=OnOff.cluster_id,
            cluster_type=ClusterType.Client,
            function=lambda entity: entity.PLATFORM == Platform.BINARY_SENSOR,
        )
    )

_builder.add_to_registry()
