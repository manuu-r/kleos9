"""Explicit appearance approval and server-owned engineering design constraints.

Relative concept geometry carries shape/layout intent, never manufacturing evidence
or a source of invented millimetre dimensions. No model output can approve itself.
"""
import hashlib
import json
import math
import re
from copy import deepcopy

from .contracts import ConceptScene, DomainError

SCHEMA = 'approved-design-intent-v1'
CORNER_RADIUS_RATIO = .13  # pinned ConceptPreview rounded_box renderer semantics
INTERNAL_ROLES = {'board', 'battery', 'sensor'}


def scene_hash(scene):
    scene = _typed_scene(scene)
    return hashlib.sha256(json.dumps(scene, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _axis_z(rotation):
    return all(abs(rotation[axis] / 180 - round(rotation[axis] / 180)) < 1e-6 for axis in ('x', 'y'))


def _body_local(position, body):
    x, y, z = (position[a] - body['position'][a] for a in 'xyz')
    # Concept renderer applies X, then Y, then Z; invert in reverse order.
    for axis in ('z', 'y', 'x'):
        angle = -math.radians(body['rotation'][axis])
        c, s = math.cos(angle), math.sin(angle)
        if axis == 'z':
            x, y = c * x - s * y, s * x + c * y
        elif axis == 'y':
            x, z = c * x + s * z, -s * x + c * z
        else:
            y, z = c * y - s * z, s * y + c * z
    return dict(zip('xyz', (x, y, z)))


def _typed_scene(raw):
    try:
        return ConceptScene.model_validate(raw).model_dump(mode='json', by_alias=True)
    except (ValueError, TypeError) as exc:
        raise DomainError('The saved concept scene is invalid; review it before engineering', 409, 'design_intent_conflict') from exc


def derive_intent(approval):
    if not isinstance(approval, dict) or any(isinstance(approval.get(key), bool) or not isinstance(approval.get(key), int)
            or approval[key] < 1 for key in ('version', 'sceneVersion')):
        raise DomainError('The saved design approval metadata is invalid', 409, 'design_intent_conflict')
    scene = _typed_scene(approval.get('scene'))
    if scene_hash(scene) != approval.get('sceneHash'):
        raise DomainError('The approved design snapshot failed its integrity check', 409, 'design_intent_conflict')
    shells = [part for part in scene['parts'] if part['role'] == 'shell']
    body = max(shells, key=lambda p: p['size']['x'] * p['size']['y'] * p['size']['z']) if shells else None
    reasons = []
    if body is None:
        reasons.append('The approved scene has no explicit enclosure shell')
    elif len(shells) > 1:
        reasons.append('Multiple shell bodies need explicit engineering scope')
    if body:
        axis = 'z' if _axis_z(body['rotation']) else None
        if any(abs(v / 360 - round(v / 360)) > 1e-6 for v in body['rotation'].values()):
            reasons.append('Rotated approved-body pose needs additional qualified engineering mapping')
        if any(v < 1e-6 for v in body['size'].values()):
            raise DomainError('The approved enclosure scale is too small to map safely', 409, 'design_intent_conflict')
        if axis is None:
            reasons.append('The approved enclosure rotation is outside the supported Z-axis profile')
        profile = {'box': 'rectangle', 'rounded_box': 'rounded_rectangle', 'sphere': 'unsupported'}.get(body['shape'])
        if body['shape'] == 'cylinder':
            profile = 'circle' if math.isclose(body['size']['x'], body['size']['y'], rel_tol=1e-6) else 'ellipse'
        if profile in {'rectangle', 'rounded_rectangle'} and abs(body['rotation']['z'] / 90 - round(body['rotation']['z'] / 90)) > 1e-6:
            reasons.append('The approved enclosure yaw is outside the supported axis-aligned profile')
        if body['shape'] == 'sphere':
            reasons.append('Spherical enclosure geometry needs an additional qualified validator')
        enclosure = {'conceptPartId': body['id'], 'primitive': body['shape'], 'profile': profile,
            'axis': axis, 'size': body['size'], 'relativeSize': deepcopy(body['size']), 'position': body['position'], 'rotation': body['rotation'],
            'unsupportedReasons': reasons, **({'cornerRadiusRatio': CORNER_RADIUS_RATIO} if profile == 'rounded_rectangle' else {})}
    else:
        enclosure = {'conceptPartId': None, 'primitive': None, 'profile': 'unsupported', 'axis': None,
                     'unsupportedReasons': reasons}
    components = []
    for part in scene['parts']:
        if body and part['id'] == body['id']:
            continue
        local = _body_local(part['position'], body) if body else None
        region = ({'center': {a: local[a] / body['size'][a] for a in 'xyz'},
                   'sizeFraction': {a: part['size'][a] / body['size'][a] for a in 'xyz'}} if body else None)
        inside = bool(region and all(abs(v) <= .5 for v in region['center'].values()))
        internal = part['role'] in {'board', 'battery'} or part['role'] == 'sensor' and inside
        placement = 'internal' if internal else 'surface' if part['role'] in {'display', 'control', 'connector', 'cover'} else 'external'
        components.append({'conceptPartId': part['id'], 'label': part['label'], 'role': part['role'], 'shape': part['shape'],
            'size': part['size'], 'position': part['position'], 'rotation': part['rotation'], 'region': region,
            'placementClass': placement, 'requiredMapping': internal,
            **({'cornerRadiusRatio': CORNER_RADIUS_RATIO} if part['shape'] == 'rounded_box' else {})})
    internal = [p for p in components if p['requiredMapping']]
    return {'schemaVersion': SCHEMA, 'approvalVersion': approval['version'], 'sceneVersion': approval['sceneVersion'],
        'sceneHash': approval['sceneHash'], 'units': 'relative', 'coordinateFrame': 'concept-centered-z-up',
        'enclosure': enclosure, 'components': components, 'aspectToleranceFraction': .25, 'positionToleranceFraction': .22,
        'rotationToleranceDegrees': 15, 'stackingOrder': [p['conceptPartId'] for p in sorted(internal, key=lambda p: p['position']['z'])],
        'dimensionPolicy': 'accepted_requirements_and_interfaces_mm',
        'limitations': ['Relative component sizes are illustrative; actual component dimensions come from accepted datasheets.',
                        'Surface and external features are references, not internal clearance volumes.',
                        'Appearance approval grants no manufacturing or release approval.']}


def freeze_for_confirmation(project, actor, timestamp, force_new=False):
    concept = project.state.get('concept')
    if not isinstance(concept, dict) or not isinstance(concept.get('scene'), dict):
        if project.state.get('approvedDesign'):
            raise DomainError('Review a current concept before replacing the approved design', 409, 'concept_review_required')
        return None, None
    history = project.state.get('conceptHistory', [])
    source_record = next((item for item in reversed(history) if item.get('version') == concept.get('version')), {})
    generated = any(item.get('version') == concept.get('version') for item in history) or concept.get('explicitScene') is True
    if not generated:  # never promote a provisional seed/fallback into user-approved authority
        if project.state.get('approvedDesign'):
            raise DomainError('Review a generated concept before replacing the approved design', 409, 'concept_review_required')
        return None, None
    planning = project.state.get('planning', {})
    if concept.get('stale') or (concept.get('basedOnInputVersion', 0) != planning.get('inputVersion', 0)):
        raise DomainError('Review the updated concept before approving its design reference', 409, 'concept_review_required')
    scene = _typed_scene(concept['scene'])
    hashed = scene_hash(scene)
    previous = project.state.get('approvedDesign')
    if previous and previous.get('sceneHash') == hashed and not force_new:
        return deepcopy(previous), derive_intent(previous)
    approval = {'schemaVersion': 'approved-design-v1', 'version': (previous or {}).get('version', 0) + 1,
        'sceneVersion': concept['version'], 'sceneHash': hashed, 'scene': scene,
        'approvedAt': timestamp, 'approvedBy': actor, 'engineeringRevision': project.engineering_revision,
        'source': {'kind': 'explicit_confirmation', 'phase': concept.get('phase'),
                   'basedOnInputVersion': concept.get('basedOnInputVersion', 0),
                   **{key: source_record[key] for key in ('runId', 'attemptId') if key in source_record}}}
    return approval, derive_intent(approval)


def approved_context(project):
    intent = project.requirements.get('designIntent')
    if not intent:
        if project.state.get('approvedDesign'):
            raise DomainError('The approved design constraints are missing; review the current reference', 409, 'design_intent_conflict')
        return None
    approval = project.state.get('approvedDesign')
    if not project.state.get('confirmed') or project.state.get('approvedDesignStatus') != 'approved':
        raise DomainError('Confirm the current design before engineering or accepting a new release', 409, 'confirmation_required')
    if not isinstance(approval, dict) or derive_intent(approval) != intent:
        raise DomainError('The engineering reference no longer matches the explicitly approved design', 409, 'design_intent_conflict')
    return deepcopy(approval)


def _vector(value):
    if (not isinstance(value, dict) or set(value) != set('xyz') or any(isinstance(v, bool) or not isinstance(v, (float, int))
            or abs(v) > 1_000_000 or not math.isfinite(v) or v <= 0 for v in value.values())):
        raise DomainError('Approved design engineering requires positive x/y/z millimetre dimensions', 409, 'design_intent_conflict')
    return value


def bind_interfaces(project, proposed):
    """Overlay authoritative shape constraints; models propose only dimensions and mappings."""
    approval = approved_context(project)
    if approval is None:
        return deepcopy(proposed), {'status': 'UNKNOWN', 'details': {'reason': 'No explicitly approved structured design reference'}}
    intent = project.requirements['designIntent']
    result = deepcopy(proposed)
    mechanical = result.get('mechanical')
    if not isinstance(mechanical, dict):
        raise DomainError('Map the approved enclosure and components in mechanical interfaces', 409, 'design_intent_conflict')
    outer, inner = _vector(mechanical.get('externalMm')), _vector(mechanical.get('internalMm'))
    enclosure = intent['enclosure']
    if enclosure['profile'] == 'circle' and not enclosure['unsupportedReasons'] and any(not math.isclose(v['x'], v['y'], abs_tol=1e-6) for v in (outer, inner)):
        raise DomainError('The approved circular enclosure requires equal X/Y diameter dimensions; revise the concept explicitly to change its shape',
                          409, 'design_intent_conflict')
    relative = enclosure.get('relativeSize')
    if relative and not enclosure['unsupportedReasons']:
        tolerance = intent['aspectToleranceFraction']
        source_ratios = (relative['x'] / relative['y'], relative['z'] / max(relative['x'], relative['y']))
        engineering_ratios = (outer['x'] / outer['y'], outer['z'] / max(outer['x'], outer['y']))
        if any(abs(actual / reference - 1) > tolerance + 1e-9 for actual, reference in zip(engineering_ratios, source_ratios)):
            raise DomainError('The proposed outer dimensions change the approved design proportions; review and confirm an updated concept before engineering',
                              409, 'design_intent_conflict')
    expected = {item['conceptPartId']: item for item in intent['components']}
    mappings, seen, interface_ids = [], set(), set()
    raw_components = mechanical.get('components', [])
    if not isinstance(raw_components, list):
        raise DomainError('Provide explicit component mappings for the approved design', 409, 'design_intent_conflict')
    for component in raw_components:
        if not isinstance(component, dict):
            raise DomainError('Approved component mappings must be objects', 409, 'design_intent_conflict')
        ident = component.get('conceptPartId', component.get('id'))
        interface_id = component.get('id')
        if (not isinstance(ident, str) or not isinstance(interface_id, str) or not 1 <= len(interface_id) <= 80
                or interface_id in interface_ids):
            raise DomainError('Internal component IDs and approved identity links must be unique bounded strings', 409, 'design_intent_conflict')
        interface_ids.add(interface_id)
        source = expected.get(ident)
        if source is None or not (source['requiredMapping'] or source['role'] in {'display', 'control', 'connector'}):
            raise DomainError('Map each internal clearance component to an approved internal conceptPartId; external features are separate references',
                              409, 'design_intent_conflict')
        if ident in seen:
            raise DomainError('An approved physical component cannot be mapped twice', 409, 'design_intent_conflict')
        seen.add(ident)
        size = _vector(component.get('sizeMm'))
        position = component.get('positionMm')
        if not isinstance(position, dict) or set(position) != set('xyz') or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or abs(v) > 1_000_000 or not math.isfinite(v) for v in position.values()):
            raise DomainError('Approved component placement needs finite x/y/z millimetre minimum corners', 409, 'design_intent_conflict')
        component['conceptPartId'] = ident
        mappings.append({**deepcopy(source), 'interfaceComponentId': component['id'], 'sizeMm': size,
                         'positionMm': deepcopy(position), 'mount': 'internal'})
    if any(item['requiredMapping'] and item['conceptPartId'] not in seen for item in intent['components']):
        raise DomainError('Map every approved internal board, battery and sensor to its exact conceptPartId', 409, 'design_intent_conflict')
    physical = {'profile': enclosure['profile'], 'axis': enclosure['axis'], 'conceptPartId': enclosure['conceptPartId'],
        'externalMm': outer, 'internalMm': inner, 'centerPolicy': 'enclosure_bbox_center',
        'rotation': deepcopy(enclosure.get('rotation', {'x': 0, 'y': 0, 'z': 0})),
        'relativeSize': deepcopy(enclosure.get('relativeSize')), 'aspectToleranceFraction': intent['aspectToleranceFraction'],
        'unsupportedReasons': deepcopy(enclosure['unsupportedReasons'])}
    if enclosure.get('cornerRadiusRatio') is not None:
        wall = mechanical.get('minimumWallMm')
        if isinstance(wall, bool) or not isinstance(wall, (int, float)) or abs(wall) > 1_000_000 or not math.isfinite(wall) or wall <= 0:
            raise DomainError('Rounded enclosure mapping requires a positive wall thickness', 409, 'design_intent_conflict')
        radius = enclosure['cornerRadiusRatio'] * min(outer['x'], outer['y'])
        physical.update({'cornerRadiusRatio': enclosure['cornerRadiusRatio'], 'cornerRadiusMm': radius,
                         'internalCornerRadiusMm': max(0, radius - wall)})
    mechanical['designIntent'] = {'schemaVersion': 'engineering-design-layout-v1', 'sceneHash': intent['sceneHash'],
        'approvalVersion': intent['approvalVersion'], 'enclosure': physical, 'components': mappings,
        'features': [deepcopy(item) for item in intent['components'] if item['conceptPartId'] not in seen],
        'positionToleranceFraction': intent['positionToleranceFraction'],
        'rotationToleranceDegrees': intent['rotationToleranceDegrees'], 'stackingOrder': intent['stackingOrder']}
    electronics = result.get('electronics', {})
    if not isinstance(electronics, dict):
        raise DomainError('Electronics placement mappings must be an object', 409, 'design_intent_conflict')
    relevant = {key: value for key, value in expected.items() if value['role'] in {'sensor', 'control', 'connector'}}
    references = electronics.get('componentReferences', [])
    if not isinstance(references, list):
        raise DomainError('Provide electronics.componentReferences as conceptPartId/reference pairs', 409, 'design_intent_conflict')
    circuit = result.get('circuit', {})
    accepted_refs = {item.get('reference') for item in circuit.get('components', []) if isinstance(item, dict) and isinstance(item.get('reference'), str)} if isinstance(circuit, dict) and isinstance(circuit.get('components', []), list) else set()
    mapped, concept_ids, physical_refs = [], set(), set()
    for mapping in references:
        if not isinstance(mapping, dict):
            raise DomainError('PCB component mappings must be objects', 409, 'design_intent_conflict')
        ident, reference = mapping.get('conceptPartId'), mapping.get('reference')
        if (not isinstance(ident, str) or ident not in relevant or not isinstance(reference, str)
                or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,31}', reference) or reference not in accepted_refs
                or ident in concept_ids or reference in physical_refs):
            raise DomainError('Map approved peripheral concept IDs uniquely to actual accepted circuit references', 409, 'design_intent_conflict')
        concept_ids.add(ident)
        physical_refs.add(reference)
        source = relevant[ident]
        mapped.append({key: deepcopy(source[key]) for key in ('conceptPartId', 'role', 'region', 'shape', 'rotation', 'placementClass')} |
                      {'reference': reference})
    missing = sorted(set(relevant) - concept_ids)
    boards = [item['conceptPartId'] for item in intent['components'] if item['role'] == 'board']
    electronics['componentReferences'] = [{'conceptPartId': item['conceptPartId'], 'reference': item['reference']} for item in mapped]
    electronics['designIntent'] = {'schemaVersion': 'engineering-pcb-layout-v1', 'sceneHash': intent['sceneHash'],
        'approvalVersion': intent['approvalVersion'], 'boardConceptPartId': boards[0] if len(boards) == 1 else None,
        'componentReferences': mapped, 'unmappedConceptPartIds': missing,
        'positionToleranceFraction': intent['positionToleranceFraction']}
    result['electronics'] = electronics
    supported = enclosure['axis'] == 'z' and enclosure['profile'] in {'circle', 'rectangle', 'rounded_rectangle'} and not enclosure['unsupportedReasons'] and not missing
    return result, {'status': 'PASS' if supported else 'UNKNOWN', 'details': {'sceneHash': intent['sceneHash'],
        'approvalVersion': intent['approvalVersion'], 'unmappedPCBConceptPartIds': missing, 'scope': 'Server-derived approved profile and explicit component mapping; geometry requires independent native checks',
        'limitations': deepcopy(enclosure['unsupportedReasons']) + ([] if supported else ['The approved form needs additional qualified geometry checks'])}}
