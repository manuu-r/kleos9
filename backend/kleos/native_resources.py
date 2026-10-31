"""Operator-qualified resource profiles shared with Terraform and pinned per job."""
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NativeResources(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    cpuUnits: int
    memoryMiB: int
    tmpMiB: int = Field(ge=64)
    pidsLimit: int = Field(ge=32, le=1024)
    ephemeralStorageGiB: int = Field(ge=21, le=200)

    @model_validator(mode='after')
    def qualified_pair(self):
        ranges = {256: (512, 2048, 512), 512: (1024, 4096, 1024), 1024: (2048, 8192, 1024),
                  2048: (4096, 16384, 1024), 4096: (8192, 30720, 1024),
                  8192: (16384, 61440, 4096), 16384: (32768, 122880, 8192)}
        low, high, step = ranges.get(self.cpuUnits, (0, -1, 1))
        if ((self.cpuUnits == 256 and self.memoryMiB not in {512, 1024, 2048})
                or not low <= self.memoryMiB <= high or (self.memoryMiB-low) % step
                or self.tmpMiB > self.memoryMiB):
            raise ValueError('Native resources must use a qualified Fargate CPU/memory pair and bounded scratch')
        return self


def default_profiles():
    return json.loads(Path(__file__).with_name('native-resources.json').read_text())
