"""Accepted circuit authority shared by the control plane and isolated PCB adapter."""
import copy
import math
import re


def circuit_contract(value, peripheral_ids):
    if (not isinstance(peripheral_ids, list) or len(peripheral_ids) > 128
            or any(not isinstance(item, str) or not 1 <= len(item) <= 100 for item in peripheral_ids)
            or len(set(peripheral_ids)) != len(peripheral_ids)):
        raise ValueError('An authoritative approved peripheral ID list is required')
    if not isinstance(value, dict) or set(value) != {'references', 'conceptReferences', 'constraints'}:
        raise ValueError('An accepted circuit contract is required')
    references = value['references']
    if not isinstance(references, list) or not 1 <= len(references) <= 128:
        raise ValueError('Accepted circuit reference count is invalid')
    seen = set()
    for item in references:
        if not isinstance(item, dict) or set(item) != {'reference', 'value', 'footprint', 'pins'}:
            raise ValueError('Accepted circuit reference fields are invalid')
        reference = item['reference']
        if not isinstance(reference, str) or not re.fullmatch(r'[A-Z]+[1-9][0-9]{0,5}', reference) or reference in seen:
            raise ValueError('Accepted circuit references must be unique annotated identifiers')
        seen.add(reference)
        if not isinstance(item['value'], str) or not 1 <= len(item['value']) <= 128:
            raise ValueError('Accepted circuit component value is invalid')
        if not isinstance(item['footprint'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+', item['footprint']):
            raise ValueError('Accepted circuit footprint identity is invalid')
        if not isinstance(item['pins'], dict) or not 1 <= len(item['pins']) <= 128:
            raise ValueError('Accepted circuit pin map is invalid')
        for pin, net in item['pins'].items():
            if (not isinstance(pin, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,31}', pin)
                    or not isinstance(net, str) or not 1 <= len(net) <= 100
                    or any(ord(c) < 32 for c in net)):
                raise ValueError('Accepted circuit pin or net identifier is invalid')
    mapping = value['conceptReferences']
    if (not isinstance(mapping, dict) or set(mapping) != set(peripheral_ids)
            or any(not isinstance(ref, str) or ref not in seen for ref in mapping.values())
            or len(set(mapping.values())) != len(mapping)):
        raise ValueError('Every approved peripheral must map uniquely to an accepted circuit reference')
    constraints = value['constraints']
    if not isinstance(constraints, dict) or set(constraints) != {
        'maxWidthMm', 'maxHeightMm', 'minTrackWidthMm', 'minClearanceMm', 'copperLayers'
    }:
        raise ValueError('Accepted PCB constraints are incomplete')
    for key in ('maxWidthMm', 'maxHeightMm', 'minTrackWidthMm', 'minClearanceMm'):
        number = constraints[key]
        low, high = (.5, 500) if key.startswith('max') else (.05, 10)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not low <= number <= high:
            raise ValueError('Accepted PCB dimensions and clearances must be finite and bounded')
    if constraints['copperLayers'] != ['F.Cu', 'B.Cu']:
        raise ValueError('Only explicit two-copper-layer PCB routing is currently supported')
    return copy.deepcopy(value)
