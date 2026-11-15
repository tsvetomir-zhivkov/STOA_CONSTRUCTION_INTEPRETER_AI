"""Deterministic operations on Stoa piping-0.1 documents. No scale inference."""
from __future__ import annotations
from math import isfinite

COLLECTIONS = ('pipe_systems', 'pipe_types', 'pipe_segments', 'pipe_fittings', 'pipe_equipment', 'pipe_connections', 'texts')
FILTERS = ('id', 'service', 'system_id', 'type_id', 'material', 'kind', 'tag', 'name', 'level_id', 'room_id', 'nominal_diameter', 'is_verified')
MEASURES = ('length_m', 'nominal_diameter_m', 'outer_diameter_m', 'inner_diameter_m', 'wall_thickness_m', 'insulation_thickness_m', 'total_pipe_length_m')

def active(item):
    return item.get('status', 'active') == 'active'

def brief(item):
    keys = ('id', 'name', 'kind', 'service', 'system_id', 'type_id', 'tag', 'material', 'nominal_diameter', 'length_m', 'level_id', 'confidence', 'flags', 'is_verified', 'from_entity_id', 'to_entity_id')
    return {k: item[k] for k in keys if k in item}

def pipe_summary(data):
    return {'schema_version': data.get('schema_version'), 'drawing': data.get('drawing'), 'counts': {k: sum(active(x) for x in data.get(k, [])) for k in COLLECTIONS}, 'note': 'Indexed counts are not necessarily exhaustive. Generic graph nodes are not confirmed physical fittings.'}

def query_pipes(data, collection, field='', value='', limit=30):
    if collection not in COLLECTIONS:
        return {'error': 'Unsupported collection', 'allowed': COLLECTIONS}
    if field and field not in FILTERS:
        return {'error': 'Unsupported filter', 'allowed': FILTERS}
    matches = [x for x in data.get(collection, []) if active(x) and (not field or str(x.get(field, '')).casefold() == str(value).casefold())]
    limit = max(1, min(int(limit), 100))
    return {'match_count': len(matches), 'matches': [brief(x) for x in matches[:limit]], 'truncated': len(matches)>limit, 'note': 'Counts refer to indexed entities; do not assume complete extraction.'}

def get_pipe_entity(data, entity_id):
    for collection in COLLECTIONS:
        for item in data.get(collection, []):
            if item.get('id') == entity_id:
                return {'collection': collection, 'entity': item, 'active': active(item)}
    return {'error': 'Entity not found', 'entity_id': entity_id}

def aggregate_pipes(data, collection, operation, measure='length_m', field='', value=''):
    if collection not in COLLECTIONS or operation not in ('count','sum','min','max','average'):
        return {'error': 'Unsupported collection or operation'}
    if field and field not in FILTERS:
        return {'error': 'Unsupported filter'}
    if operation != 'count' and measure not in MEASURES:
        return {'error': 'Unsupported measurement'}
    matches = [x for x in data.get(collection, []) if active(x) and (not field or str(x.get(field, '')).casefold() == str(value).casefold())]
    if operation == 'count':
        return {'result': len(matches), 'entity_ids': [x['id'] for x in matches], 'note': 'Indexed entities only; unknown nodes are not verified physical fittings.'}
    known = [(x['id'], x.get(measure)) for x in matches if isinstance(x.get(measure), (int,float)) and not isinstance(x.get(measure),bool) and isfinite(x[measure])]
    missing = [x['id'] for x in matches if x['id'] not in {i for i,_ in known}]
    if not known:
        return {'result': None, 'missing_entity_ids': missing[:30], 'missing_count': len(missing), 'note': 'No metric values available; do not convert pixels to metres without verified scale.'}
    vals = [v for _,v in known]
    result = {'sum':sum(vals), 'min':min(vals), 'max':max(vals), 'average':sum(vals)/len(vals)}[operation]
    return {'result':result, 'measure':measure, 'entity_ids':[i for i,_ in known], 'missing_entity_ids':missing[:30], 'missing_count':len(missing), 'complete_for_indexed_matches':not missing}

def pipe_connections(data, entity_id, verified_only=False):
    connections = [x for x in data.get('pipe_connections', []) if active(x) and entity_id in (x.get('from_entity_id'),x.get('to_entity_id')) and (not verified_only or x.get('is_verified') is True)]
    return {'entity_id':entity_id, 'connections':[brief(x) for x in connections], 'count':len(connections), 'note':'Graph connections reflect indexed extraction, not independently verified physical connectivity.'}

def check_pipe_coverage(data, category):
    matches = [(i,c) for i,c in enumerate(data.get('coverage', [])) if c.get('category','').casefold()==category.casefold() and c.get('region')=='page']
    verified = bool(matches) and all(c.get('status')=='analysed' and c.get('confidence',0)>=0.8 for _,c in matches)
    return {'category':category, 'absence_verified':verified, 'coverage_references':[f'coverage[{i}]' for i,_ in matches], 'records':[{'status':c.get('status'), 'note':c.get('note'), 'confidence':c.get('confidence')} for _,c in matches], 'note':'No matching reliable coverage means absence cannot be established.'}
