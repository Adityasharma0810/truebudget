"""Parsers for real DHCP lease formats written by hotspot software."""

from __future__ import annotations

from eb_agent.core import _parse_dhcp_lease_file


def test_dnsmasq_lease(tmp_path) -> None:
    path = tmp_path / "dnsmasq.leases"
    path.write_text(
        "1790000000 aa:bb:cc:dd:ee:01 10.42.0.23 phone *\n"
        "1790000000 00:00:00:00:00:00 10.42.0.24 ignored *\n",
        encoding="utf-8",
    )
    leases = _parse_dhcp_lease_file(str(path))
    assert leases == [
        {"mac": "aa:bb:cc:dd:ee:01", "ip": "10.42.0.23", "hostname": "phone"}
    ]


def test_networkmanager_key_value_lease(tmp_path) -> None:
    path = tmp_path / "internal-wlan0.lease"
    path.write_text(
        "# This is private data. Do not parse.\n"
        "ADDRESS=10.42.0.31\n"
        "PREFIX=24\n"
        "HOSTNAME=laptop\n"
        "CLIENTID=01:aa:bb:cc:dd:ee:02\n",
        encoding="utf-8",
    )
    leases = _parse_dhcp_lease_file(str(path))
    assert leases == [
        {"mac": "aa:bb:cc:dd:ee:02", "ip": "10.42.0.31", "hostname": "laptop"}
    ]


def test_systemd_networkd_lease(tmp_path) -> None:
    path = tmp_path / "lease"
    path.write_text(
        "ADDRESS=192.168.12.44\n"
        "CLIENTID=01:aa:bb:cc:dd:ee:03\n"
        "HOSTNAME=tablet\n",
        encoding="utf-8",
    )
    leases = _parse_dhcp_lease_file(str(path))
    assert leases[0]["mac"] == "aa:bb:cc:dd:ee:03"
    assert leases[0]["ip"] == "192.168.12.44"


def test_kea_csv_lease(tmp_path) -> None:
    path = tmp_path / "kea-leases4.csv"
    path.write_text(
        "address,hwaddr,client_id,valid_lifetime,expire,subnet_id,"
        "fqdn_fwd,fqdn_rev,hostname,state,user_context,pool_id\n"
        "10.42.0.50,aa:bb:cc:dd:ee:04,,3600,1790003600,1,0,0,tv,0,,0\n",
        encoding="utf-8",
    )
    leases = _parse_dhcp_lease_file(str(path))
    assert leases == [
        {"mac": "aa:bb:cc:dd:ee:04", "ip": "10.42.0.50", "hostname": "tv"}
    ]


def test_missing_lease_file_is_empty() -> None:
    assert _parse_dhcp_lease_file("/nonexistent/lease") == []
