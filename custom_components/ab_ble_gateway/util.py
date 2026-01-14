import logging
from uuid import UUID
_LOGGER = logging.getLogger(__name__)


def to_unformatted_mac(addr: int):
    """Return unformatted MAC address"""
    return ''.join(f'{i:02X}' for i in addr[:])


def to_mac(addr: str) -> str:
    """Return formatted MAC address"""
    return ':'.join(f'{i:02X}' for i in addr)


def parse_ap_ble_devices_data(devices_data):
    """ Converts the April Brother BLE Gateway Data Format into Raw HCI Packets """
    # See  https://wiki.aprbrother.com/en/User_Guide_For_AB_BLE_Gateway_V4.html#data-format
    d = devices_data
    data = bytearray(bytearray(6))  # prepend 6 bytes
    data.extend(d)
    data.append(d[7])  # append rrsid at the end
    data[2] = len(data) - 3  # set size field
    data[7 + 6] = len(data) - 14 - 1  # set adpayload_size (where the rrsid was)
    data[7:13] = data[7:13][::-1]  # reverse mac address
    return data


def parse_raw_data(data: bytearray):
    """ Converts RAW HCI Packets info BLE advertisments as Bleak would generate them"""
    # This is partly from https://github.com/Ernst79/bleparser/blob/ecd3c596760aab3ec4bf7ba30515831024fc47d3/package/bleparser/__init__.py#L82

    # check if packet is Extended scan result
    is_ext_packet = True if data[3] == 0x0D else False
    # check for no BR/EDR + LE General discoverable mode flags
    adpayload_start = 29 if is_ext_packet else 14
    # https://www.silabs.com/community/wireless/bluetooth/knowledge-base.entry.html/2017/02/10/bluetooth_advertisin-hGsf
    try:
        adpayload_size = data[adpayload_start - 1]
    except IndexError:
        return None, None
    # check for BTLE msg size
    msg_length = data[2] + 3
    if (
        msg_length <= adpayload_start or msg_length != len(data) or msg_length != (
            adpayload_start + adpayload_size + (0 if is_ext_packet else 1)
        )
    ):
        return None, None
    # extract RSSI byte
    rssi_index = 18 if is_ext_packet else msg_length - 1
    rssi = data[rssi_index]
    # strange positive RSSI workaround
    if rssi > 127:
        rssi = rssi - 256
    # MAC address
    mac = (data[8 if is_ext_packet else 7:14 if is_ext_packet else 13])[::-1]
    complete_local_name = ""
    shortened_local_name = ""
    service_class_uuid16 = None
    service_class_uuid128 = None
    service_data_list = []
    man_spec_data_list = []

    while adpayload_size > 1:
        adstuct_size = data[adpayload_start] + 1
        if adstuct_size > 1 and adstuct_size <= adpayload_size:
            adstruct = data[adpayload_start:adpayload_start + adstuct_size]
            # https://www.bluetooth.com/specifications/assigned-numbers/generic-access-profile/
            adstuct_type = adstruct[1]
            if adstuct_type == 0x02:
                # AD type 'Incomplete List of 16-bit Service Class UUIDs'
                service_class_uuid16 = (adstruct[2] << 8) | adstruct[3]
            elif adstuct_type == 0x03:
                # AD type 'Complete List of 16-bit Service Class UUIDs'
                service_class_uuid16 = (adstruct[2] << 8) | adstruct[3]
            elif adstuct_type == 0x06:
                # AD type '128-bit Service Class UUIDs'
                service_class_uuid128 = adstruct[2:]
            elif adstuct_type == 0x08:
                # AD type 'shortened local name'
                shortened_local_name = adstruct[2:].decode("utf-8")
            elif adstuct_type == 0x09:
                # AD type 'complete local name'
                complete_local_name = adstruct[2:].decode("utf-8")
            elif adstuct_type == 0x16 and adstuct_size > 4:
                # AD type 'Service Data - 16-bit UUID'
                service_data_list.append(adstruct)
            elif adstuct_type == 0xFF:
                # AD type 'Manufacturer Specific Data'
                man_spec_data_list.append(adstruct)
                # https://www.bluetooth.com/specifications/assigned-numbers/company-identifiers/
        adpayload_size -= adstuct_size
        adpayload_start += adstuct_size

    if complete_local_name:
        local_name = complete_local_name
    else:
        local_name = shortened_local_name

    service_uuids = []
    if (service_class_uuid128 is not None):
        service_uuids.append(UUID(bytes=bytes(service_class_uuid128)).hex)

    if (service_class_uuid16 is not None):
        service_uuids.append("0000{:04x}-0000-1000-8000-00805f9b34fb".format(service_class_uuid16))

    # https://github.com/hbldh/bleak/blob/c5cbb8485741331d03a3ac151e98f45edb560938/bleak/backends/corebluetooth/scanner.py#L82
    # https://github.com/hbldh/bleak/blob/60aa4aa23a97bda075770fec43202295602f1a9d/bleak/backends/winrt/scanner.py#L159
    service_data = {}
    if len(service_data_list) > 0:
        for service_data_elem in service_data_list:
            service_data_uuid = "0000{:04x}-0000-1000-8000-00805f9b34fb".format((service_data_elem[3] << 8) | service_data_elem[2])
            service_data[service_data_uuid] = bytes(service_data_elem[4:])
            service_uuids.append(service_data_uuid)

    manufacturer_data = {}

    if (len(man_spec_data_list) > 1):
        raise "Multiple Manufacturer Data Fields is not supported"

    if len(man_spec_data_list) > 0:
        manufacturer_id = int.from_bytes(
            man_spec_data_list[0][2:4], byteorder="little"
        )
        manufacturer_value = bytes(man_spec_data_list[0][4:])
        manufacturer_data[manufacturer_id] = manufacturer_value

    return {
        "address": to_mac(mac),
        "rssi": rssi,
        "service_uuids": service_uuids,
        "local_name": local_name,
        "service_data": service_data,
        'manufacturer_data': manufacturer_data
    }
