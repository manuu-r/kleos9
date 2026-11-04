"""Printer connection DTOs; observations are never physical acceptance."""
import ipaddress
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, SecretStr, StrictBool, field_validator, model_validator

from kleos.contracts.models import Actor, WireModel

MODELS = {'N1': 'Bambu Lab A1 mini', 'N2S': 'Bambu Lab A1', 'C12': 'Bambu Lab P1S',
          'BL-P001': 'Bambu Lab X1 Carbon'}


def private_address(value: str) -> str:
    address = ipaddress.ip_address(value)
    if address.version != 4 or not any(address in ipaddress.ip_network(network)
        for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
        raise ValueError('Use a literal private IPv4 printer address')
    return str(address)


@dataclass(frozen=True)
class LocalAccess:
    """Issued by the API only after local-development, loopback and admin checks."""
    actor: Actor
    local_admin: bool = False


class ProbeRequest(WireModel):
    address: str = Field(max_length=15)
    service: Literal['mqtt', 'ftps'] = 'mqtt'

    _address = field_validator('address')(private_address)


class PairRequest(ProbeRequest):
    serial: str = Field(pattern=r'^[A-Za-z0-9_-]{6,64}$')
    access_code: SecretStr = Field(min_length=8, max_length=64, exclude=True)
    model_id: str
    firmware_version: str | None = Field(default=None, min_length=1, max_length=80)
    certificate_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    certificate_verified: StrictBool = False
    lan_developer_mode: StrictBool = False
    ftps_certificate_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    ftps_certificate_verified: StrictBool = False

    @model_validator(mode='after')
    def validate_identity(self):
        if self.model_id not in MODELS:
            raise ValueError('Select a supported printer model')
        if not re.fullmatch(r'[A-Za-z0-9]{8,64}', self.access_code.get_secret_value()):
            raise ValueError('Invalid access code')
        if bool(self.certificate_sha256) != self.certificate_verified:
            raise ValueError('A manual certificate pin requires explicit verification')
        if bool(self.ftps_certificate_sha256) != self.ftps_certificate_verified:
            raise ValueError('A manual transfer certificate pin requires explicit verification')
        if self.lan_developer_mode and not self.firmware_version:
            raise ValueError('Provide firmware version when selecting Developer Mode')
        return self


class CertificateView(WireModel):
    address: str
    certificate_sha256: str
    verified: bool
    service: Literal['mqtt', 'ftps'] = 'mqtt'


class DiscoveredPrinter(WireModel):
    address: str
    serial: str
    name: str
    model_id: str | None = None
    firmware_version: str | None = None


class DiscoveryView(WireModel):
    devices: list[DiscoveredPrinter]
    qualification: Literal['unverified_lan_advertisement'] = 'unverified_lan_advertisement'


class ConnectionState(WireModel):
    enabled: bool
    paired: bool = False
    connected: bool = False
    status: str = 'not_connected'
    message: str = 'Local read-only connection is not configured'
    address: str | None = None
    serial: str | None = None
    model_id: str | None = None
    firmware_version: str | None = None
    direct_printing: bool = False
    credentials_stored: bool = False
    expires_after_seconds: int = 0
    error_code: str | None = None
    observation: dict = Field(default_factory=dict)
