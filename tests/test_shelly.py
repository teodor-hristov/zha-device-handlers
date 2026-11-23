"""Tests for Shelly quirks."""

import asyncio

import pytest
from zha.application import Platform
from zha.quirks import DEVICE_REGISTRY
from zigpy.profiles import zha
import zigpy.types as t
from zigpy.zcl import ClusterType, foundation
from zigpy.zcl.clusters.closures import WindowCovering
from zigpy.zcl.clusters.general import Basic, Scenes
from zigpy.zcl.foundation import ZCLAttributeAccess

from tests.zha_helpers import join_device_from_diagnostics, zha_gateway
import zhaquirks
from zhaquirks.shelly import SHELLY_MANUFACTURER_CODE, ShellyLightLevelCluster
from zhaquirks.shelly.i4 import ShellyI4ButtonEvent, ShellyI4CoverEvent
from zhaquirks.shelly.wifi import (
    SHELLY_WIFI_SETUP_CLUSTER_ID,
    SHELLY_WIFI_SETUP_ENDPOINT_ID,
    SHELLY_WIFI_SETUP_MODELS,
    SHELLY_WIFI_SETUP_PROFILE_ID,
    ShellyCustomProfileDevice,
    ShellyWiFiSetupCluster,
)

zhaquirks.setup()


def _attribute_report_data(
    attribute: foundation.ZCLAttributeDef, value: object
) -> bytes:
    """Build a typed ZCL attribute report payload for Shelly WiFi attributes."""

    header = foundation.ZCLHeader.general(
        tsn=1,
        command_id=foundation.GeneralCommand.Report_Attributes,
        manufacturer=SHELLY_MANUFACTURER_CODE,
        direction=foundation.Direction.Server_to_Client,
    ).serialize()
    response = foundation.Attribute(
        attrid=attribute.id,
        value=foundation.TypeValue(type=attribute.zcl_type, value=value),
    )
    command = (
        foundation.GENERAL_COMMANDS[foundation.GeneralCommand.Report_Attributes]
        .schema([response])
        .serialize()
    )
    return t.SerializableBytes(header + command).serialize()


@pytest.mark.parametrize("model", SHELLY_WIFI_SETUP_MODELS)
def test_shelly_wifi_setup_cluster_replaced(zigpy_device_from_v2_quirk, model) -> None:
    """Ensure Shelly relay devices expose a typed WiFi setup cluster."""

    quirked = zigpy_device_from_v2_quirk(
        "Shelly",
        model,
        endpoint_ids=[1, SHELLY_WIFI_SETUP_ENDPOINT_ID],
        cluster_ids={
            SHELLY_WIFI_SETUP_ENDPOINT_ID: {
                SHELLY_WIFI_SETUP_CLUSTER_ID: ClusterType.Server,
            }
        },
    )

    cluster = quirked.endpoints[SHELLY_WIFI_SETUP_ENDPOINT_ID].in_clusters[
        SHELLY_WIFI_SETUP_CLUSTER_ID
    ]

    assert isinstance(quirked, ShellyCustomProfileDevice)
    assert isinstance(cluster, ShellyWiFiSetupCluster)
    assert cluster.ep_attribute == "shelly_wifi_setup"
    assert cluster.find_attribute("status") == cluster.AttributeDefs.status
    assert cluster.find_attribute("ssid") == cluster.AttributeDefs.ssid
    assert cluster.AttributeDefs.status.manufacturer_code == SHELLY_MANUFACTURER_CODE
    assert cluster.AttributeDefs.dhcp.access == ZCLAttributeAccess.Read
    assert cluster.AttributeDefs.enable.access == (
        ZCLAttributeAccess.Read | ZCLAttributeAccess.Write
    )
    assert cluster.AttributeDefs.action.access == ZCLAttributeAccess.Write
    assert cluster.AttributeDefs.action.type is t.uint8_t


@pytest.mark.parametrize("model", SHELLY_WIFI_SETUP_MODELS)
def test_shelly_wifi_custom_profile_packet_processed(
    zigpy_device_from_v2_quirk, model
) -> None:
    """Ensure Shelly custom profile packets are processed as ZCL for WiFi reads."""

    quirked = zigpy_device_from_v2_quirk(
        "Shelly",
        model,
        endpoint_ids=[1, SHELLY_WIFI_SETUP_ENDPOINT_ID],
        cluster_ids={
            SHELLY_WIFI_SETUP_ENDPOINT_ID: {
                SHELLY_WIFI_SETUP_CLUSTER_ID: ClusterType.Server,
            }
        },
    )

    cluster = quirked.endpoints[SHELLY_WIFI_SETUP_ENDPOINT_ID].in_clusters[
        SHELLY_WIFI_SETUP_CLUSTER_ID
    ]

    async def deliver_packet() -> None:
        quirked.packet_received(
            t.ZigbeePacket(
                profile_id=SHELLY_WIFI_SETUP_PROFILE_ID,
                cluster_id=SHELLY_WIFI_SETUP_CLUSTER_ID,
                src_ep=SHELLY_WIFI_SETUP_ENDPOINT_ID,
                dst_ep=SHELLY_WIFI_SETUP_ENDPOINT_ID,
                data=t.SerializableBytes(
                    _attribute_report_data(
                        cluster.AttributeDefs.status,
                        t.CharacterString("got ip"),
                    )
                ),
            )
        )

    asyncio.run(deliver_packet())

    assert cluster.get("status") == "got ip"


@pytest.mark.parametrize("model", SHELLY_WIFI_SETUP_MODELS)
def test_shelly_wifi_standard_profile_packet_delegated(
    zigpy_device_from_v2_quirk, model
) -> None:
    """Ensure standard ZHA profile packets fall through to normal zigpy parsing."""

    quirked = zigpy_device_from_v2_quirk(
        "Shelly",
        model,
        endpoint_ids=[1, SHELLY_WIFI_SETUP_ENDPOINT_ID],
        cluster_ids={
            SHELLY_WIFI_SETUP_ENDPOINT_ID: {
                SHELLY_WIFI_SETUP_CLUSTER_ID: ClusterType.Server,
            }
        },
    )

    # Standard ZHA profile packet should delegate to super()
    zha_packet = t.ZigbeePacket(
        profile_id=zha.PROFILE_ID,
        cluster_id=Basic.cluster_id,
        src_ep=1,
        dst_ep=1,
        data=t.SerializableBytes(
            _attribute_report_data(
                Basic.AttributeDefs.model,
                t.CharacterString("1PM"),
            )
        ),
    )

    hdr, rsp_key = quirked._parse_packet_header(zha_packet)

    assert hdr is not None
    assert rsp_key is not None
    assert rsp_key.endpoint_id == 1
    assert rsp_key.cluster_id == Basic.cluster_id


def test_shelly_presence_wifi_setup_and_light_level(
    zigpy_device_from_v2_quirk,
) -> None:
    """Ensure Presence has the WiFi setup cluster and keeps its light level."""

    quirked = zigpy_device_from_v2_quirk(
        "Shelly",
        "Presence",
        endpoint_ids=[1, SHELLY_WIFI_SETUP_ENDPOINT_ID],
        cluster_ids={
            1: {ShellyLightLevelCluster.cluster_id: ClusterType.Server},
            SHELLY_WIFI_SETUP_ENDPOINT_ID: {
                SHELLY_WIFI_SETUP_CLUSTER_ID: ClusterType.Server,
            },
        },
    )

    wifi = quirked.endpoints[SHELLY_WIFI_SETUP_ENDPOINT_ID].in_clusters[
        SHELLY_WIFI_SETUP_CLUSTER_ID
    ]
    light_level = quirked.endpoints[1].in_clusters[ShellyLightLevelCluster.cluster_id]

    assert isinstance(quirked, ShellyCustomProfileDevice)
    assert isinstance(wifi, ShellyWiFiSetupCluster)
    assert isinstance(light_level, ShellyLightLevelCluster)


I4_DIAGNOSTICS = "shelly-i4-0x02036300.json"
I4_IEEE = "ab:cd:ef:12:fe:f9:00:b4"


def _i4_events(entity) -> list[str]:
    """Collect the event types the entity fires."""
    events: list[str] = []
    entity.on_event(
        "event_triggered", lambda event: events.append(event.triggered.event_type)
    )
    return events


async def _i4_command(cluster, command, *args) -> None:
    """Deliver a command the I4 sends from one of its client clusters."""
    hdr = foundation.ZCLHeader.cluster(
        tsn=1,
        command_id=command.id,
        direction=foundation.Direction.Client_to_Server,
    )
    cluster.handle_message(hdr, command.schema(*args))


async def test_shelly_i4_entities() -> None:
    """Ensure the I4 has an event entity per input and per input pair."""
    async with zha_gateway() as gateway:
        device = await join_device_from_diagnostics(
            gateway, I4_DIAGNOSTICS, DEVICE_REGISTRY
        )

        for number in range(1, 5):
            entity = device.get_platform_entity(
                Platform.EVENT, unique_id=f"{I4_IEEE}-{number}-button_{number}"
            )
            assert isinstance(entity, ShellyI4ButtonEvent)

        for endpoint_id, inputs in ((5, "1_2"), (6, "3_4")):
            entity = device.get_platform_entity(
                Platform.EVENT,
                unique_id=f"{I4_IEEE}-{endpoint_id}-inputs_{inputs}_cover",
            )
            assert isinstance(entity, ShellyI4CoverEvent)

        assert not [
            entity
            for entity in device.platform_entities.values()
            if entity.PLATFORM == Platform.BINARY_SENSOR
        ]


async def test_shelly_i4_button_events() -> None:
    """Ensure scene recalls and the pair stop map to button events."""
    async with zha_gateway() as gateway:
        device = await join_device_from_diagnostics(
            gateway, I4_DIAGNOSTICS, DEVICE_REGISTRY
        )
        button_1 = device.get_platform_entity(
            Platform.EVENT, unique_id=f"{I4_IEEE}-1-button_1"
        )
        button_2 = device.get_platform_entity(
            Platform.EVENT, unique_id=f"{I4_IEEE}-2-button_2"
        )
        events_1 = _i4_events(button_1)
        events_2 = _i4_events(button_2)

        scenes = device.device.endpoints[1].out_clusters[Scenes.cluster_id]
        pair = device.device.endpoints[5].out_clusters[WindowCovering.cluster_id]
        recall = Scenes.ServerCommandDefs.recall
        stop = WindowCovering.ServerCommandDefs.stop

        # A stop without a long push before it ends nothing
        await _i4_command(pair, stop)
        for scene_id in (1, 2, 3, 5, 4):
            await _i4_command(scenes, recall, 1, scene_id)
        await _i4_command(pair, stop)
        await _i4_command(pair, stop)

        assert events_1 == [
            "single_press",
            "double_press",
            "triple_press",
            "long_press_start",
            "long_press_end",
        ]
        assert events_2 == []


async def test_shelly_i4_cover_events() -> None:
    """Ensure the WindowCovering commands of a pair map to cover events."""
    async with zha_gateway() as gateway:
        device = await join_device_from_diagnostics(
            gateway, I4_DIAGNOSTICS, DEVICE_REGISTRY
        )
        entity = device.get_platform_entity(
            Platform.EVENT, unique_id=f"{I4_IEEE}-6-inputs_3_4_cover"
        )
        events = _i4_events(entity)

        pair = device.device.endpoints[6].out_clusters[WindowCovering.cluster_id]
        for command in (
            WindowCovering.ServerCommandDefs.up_open,
            WindowCovering.ServerCommandDefs.stop,
            WindowCovering.ServerCommandDefs.down_close,
        ):
            await _i4_command(pair, command)

        assert events == ["opening", "stopped", "closing"]
