/** Normalize STEP records using IFC schema metadata; retain the existing parser and geometry engine. */
import fs from 'node:fs';
import { emptyBuilding, stableId } from '../../storage/building.js';
import { datasetPath } from '../../mappers/documentBuilder.js';
import { lookupRoomName, OUTDOOR_TYPES } from '../../mappers/glossary.js';
import { unwrap, isRef } from './stepParser.js';
import { polygonArea } from '../../geometry/geom.js';

const SCHEMAS = JSON.parse(fs.readFileSync(new URL('../../../schema/ifc-entities.json', import.meta.url), 'utf8'));
const PREFIX = { EXA: 1e18, PETA: 1e15, TERA: 1e12, GIGA: 1e9, MEGA: 1e6, KILO: 1e3, HECTO: 1e2, DECA: 10, DECI: .1, CENTI: .01, MILLI: .001, MICRO: 1e-6, NANO: 1e-9 };
const CATEGORY = { IFCCOLUMN: 'column', IFCBEAM: 'beam', IFCSLAB: 'slab', IFCROOF: 'roof', IFCFOOTING: 'footing', IFCPILE: 'pile', IFCMEMBER: 'structural_member', IFCPLATE: 'structural_member', IFCMECHANICALFASTENER: 'structural_connection', IFCRAILING: 'railing', IFCSTAIR: 'stair', IFCFURNITURE: 'furniture', IFCPIPESEGMENT: 'pipe', IFCPIPEFITTING: 'pipe', IFCDUCTSEGMENT: 'duct', IFCDUCTFITTING: 'duct', IFCCABLESEGMENT: 'cable', IFCCABLECARRIERSEGMENT: 'cable_tray', IFCELECTRICDISTRIBUTIONBOARD: 'distribution_board', IFCLIGHTFIXTURE: 'light_fixture', IFCPUMP: 'pump', IFCVALVE: 'valve', IFCFAN: 'ventilation_equipment', IFCAIRTERMINAL: 'ventilation_equipment', IFCBOILER: 'heating_equipment', IFCSPACEHEATER: 'radiator', IFCCHILLER: 'cooling_equipment', IFCFIRESUPPRESSIONTERMINAL: 'fire_protection_equipment', IFCSENSOR: 'sensor' };
const SYSTEM = { HEATING: 'heating', COOLING: 'cooling', VENTILATION: 'ventilation', AIRCONDITIONING: 'ventilation', DOMESTICCOLDWATER: 'plumbing', DOMESTICHOTWATER: 'plumbing', DRAINAGE: 'drainage', SEWAGE: 'drainage', ELECTRICAL: 'electrical', LIGHTING: 'lighting', FIREPROTECTION: 'fire_protection', COMMUNICATION: 'communication' };

export class StepFacts {
  constructor(model) {
    this.model = model; this.entities = model.e;
    this.schema = SCHEMAS[model.schema];
    if (!this.schema) throw new Error(`Unsupported IFC schema metadata: ${model.schema}`);
    this.parent = new Map(); this.types = new Map(); this.psets = new Map(); this.materials = new Map();
    this.units = new Map();
    const project = [...this.entities.values()].find(e => this.isA(e, 'IFCPROJECT'));
    const assignment = this.get(this.attr(project, 'UnitsInContext'));
    for (const ref of this.attr(assignment, 'Units') ?? []) {
      const unit = this.get(ref); this.units.set(this.attr(unit, 'UnitType'), unit);
    }
    for (const [id, e] of this.entities) {
      if (this.isA(e, 'IFCRELAGGREGATES') || this.isA(e, 'IFCRELNESTS')) for (const child of this.attr(e, 'RelatedObjects') ?? []) this.parent.set(child.ref, this.attr(e, 'RelatingObject')?.ref);
      if (this.isA(e, 'IFCRELCONTAINEDINSPATIALSTRUCTURE')) for (const child of this.attr(e, 'RelatedElements') ?? []) this.parent.set(child.ref, this.attr(e, 'RelatingStructure')?.ref);
      if (this.isA(e, 'IFCRELDEFINESBYTYPE')) for (const child of this.attr(e, 'RelatedObjects') ?? []) this.types.set(child.ref, this.attr(e, 'RelatingType')?.ref);
      if (this.isA(e, 'IFCRELDEFINESBYPROPERTIES')) for (const child of this.attr(e, 'RelatedObjects') ?? []) {
        if (!this.psets.has(child.ref)) this.psets.set(child.ref, []);
        this.psets.get(child.ref).push(this.attr(e, 'RelatingPropertyDefinition'));
      }
      if (this.isA(e, 'IFCRELASSOCIATESMATERIAL')) for (const child of this.attr(e, 'RelatedObjects') ?? []) this.materials.set(child.ref, this.attr(e, 'RelatingMaterial'));
    }
  }
  get(ref) { return isRef(ref) ? this.entities.get(ref.ref) : null; }
  attr(e, name) { if (!e) return null; const idx = this.schema[e.type]?.attributes.indexOf(name) ?? -1; return idx >= 0 ? unwrap(e.args[idx]) : null; }
  isA(e, type) { let cls = e?.type; while (cls) { if (cls === type) return true; cls = this.schema[cls]?.parent; } return false; }
  id(e) { const guid = this.attr(e, 'GlobalId'); return guid ? `ifc:${guid}` : null; }
  ancestor(id, type) { const seen = new Set(); while (id != null && !seen.has(id)) { seen.add(id); if (this.isA(this.entities.get(id), type)) return id; id = this.parent.get(id); } return null; }
  factor(unit) {
    if (!unit) return null;
    if (unit.type === 'IFCSIUNIT') {
      const p = PREFIX[this.attr(unit, 'Prefix')] ?? 1;
      return p ** ({ AREAUNIT: 2, VOLUMEUNIT: 3 }[this.attr(unit, 'UnitType')] ?? 1);
    }
    if (this.isA(unit, 'IFCCONVERSIONBASEDUNIT')) {
      const measure = this.get(this.attr(unit, 'ConversionFactor'));
      const f = this.factor(this.get(this.attr(measure, 'UnitComponent'))), value = this.attr(measure, 'ValueComponent');
      return f != null && typeof value === 'number' ? value * f : null;
    }
    return null;
  }
  property(ref, seen = new Set()) {
    if (!isRef(ref)) return Array.isArray(ref) ? ref.map(x => this.property(x, seen)) : unwrap(ref);
    if (seen.has(ref.ref)) return { step_id: ref.ref };
    const e = this.get(ref); if (!e) return { step_id: ref.ref };
    seen = new Set([...seen, ref.ref]);
    if (e.type === 'IFCPROPERTYSINGLEVALUE') return this.attr(e, 'NominalValue');
    if (e.type === 'IFCPROPERTYENUMERATEDVALUE') return (this.attr(e, 'EnumerationValues') ?? []).map(unwrap);
    if (e.type === 'IFCPROPERTYLISTVALUE') return (this.attr(e, 'ListValues') ?? []).map(unwrap);
    if (e.type === 'IFCCOMPLEXPROPERTY') return Object.fromEntries((this.attr(e, 'HasProperties') ?? []).map(r => [this.attr(this.get(r), 'Name'), this.property(r, seen)]));
    return Object.fromEntries((this.schema[e.type]?.attributes ?? []).map((name, i) => [name, this.property(e.args[i], seen)]));
  }
  properties(id) {
    const e = this.entities.get(id), typeId = this.types.get(id), type = this.entities.get(typeId);
    const refs = [...(this.attr(type, 'HasPropertySets') ?? []), ...(this.attr(e, 'HasPropertySets') ?? []), ...(this.psets.get(id) ?? [])];
    const psets = {}, quantities = [];
    for (const r of refs) {
      const set = this.get(r); if (!set) continue;
      const name = this.attr(set, 'Name') ?? `#${r.ref}`;
      if (set.type === 'IFCPROPERTYSET') psets[name] = Object.fromEntries((this.attr(set, 'HasProperties') ?? []).map(p => [this.attr(this.get(p), 'Name'), this.property(p)]));
      else if (set.type === 'IFCELEMENTQUANTITY') {
        psets[name] = this.property(r);
        for (const qr of this.attr(set, 'Quantities') ?? []) {
          const q = this.get(qr), spec = { IFCQUANTITYLENGTH: ['LengthValue', 'LENGTHUNIT', 'm'], IFCQUANTITYAREA: ['AreaValue', 'AREAUNIT', 'm2'], IFCQUANTITYVOLUME: ['VolumeValue', 'VOLUMEUNIT', 'm3'], IFCQUANTITYCOUNT: ['CountValue', null, 'count'] }[q?.type];
          if (!spec) continue;
          const original = this.attr(q, spec[0]), explicit = this.get(this.attr(q, 'Unit'));
          const factor = spec[1] ? this.factor(explicit ?? this.units.get(spec[1])) : 1;
          quantities.push({ name: this.attr(q, 'Name'), value: typeof original === 'number' && factor != null ? original * factor : null, unit: spec[2], origin: 'source', method: `${name}.${this.attr(q, 'Name')}`, source_refs: [], original_value: original, original_unit: explicit ? JSON.stringify(explicit) : spec[1] });
        }
      }
    }
    return { psets, quantities, type };
  }
  materialData(id, length) {
    let ref = this.materials.get(id) ?? this.materials.get(this.types.get(id)), mat = this.get(ref);
    const one = m => ({ name: this.attr(m, 'Name'), category: this.attr(m, 'Category'), description: this.attr(m, 'Description') });
    if (mat?.type === 'IFCMATERIALLAYERSETUSAGE') mat = this.get(this.attr(mat, 'ForLayerSet'));
    if (!mat) return { materials: [], material_layers: [] };
    if (mat.type === 'IFCMATERIALLAYERSET') {
      const layers = (this.attr(mat, 'MaterialLayers') ?? []).map(r => this.get(r));
      return { materials: layers.map(l => one(this.get(this.attr(l, 'Material')))), material_layers: layers.map(l => ({ material: one(this.get(this.attr(l, 'Material'))), thickness_m: this.attr(l, 'LayerThickness') == null ? null : this.attr(l, 'LayerThickness') * length, function: this.attr(l, 'Category'), is_structural: null, is_insulation: null, is_ventilated: typeof this.attr(l, 'IsVentilated') === 'boolean' ? this.attr(l, 'IsVentilated') : null })) };
    }
    if (mat.type === 'IFCMATERIALLIST') return { materials: (this.attr(mat, 'Materials') ?? []).map(r => one(this.get(r))), material_layers: [] };
    return { materials: [one(mat)], material_layers: [] };
  }
}

export function normalizeIfc(model, filePath, opts, footprint) {
  const f = new StepFacts(model), length = f.factor(f.units.get('LENGTHUNIT'));
  if (length == null) throw new Error('IFC project length unit is missing or unsupported; cannot normalize geometry');
  model.lenM = length;
  const ds = datasetPath(filePath, { datasetRoot: opts.config.datasetRoot });
  const file = opts.file ?? ds.file, source = opts.source ?? ds.source;
  const sid = stableId('source', file), did = stableId('drawing', file, null, null);
  const sourceRef = guid => ({ source_document_id: sid, drawing_id: did, source_ref: guid, extractor: 'vector_ifc' });
  const allBuildings = [...model.e].filter(([, e]) => f.isA(e, 'IFCBUILDING'));
  const physical = [...model.e].filter(([, e]) => f.isA(e, 'IFCELEMENT') || f.isA(e, 'IFCSPACE') || f.isA(e, 'IFCBUILDINGSTOREY'));
  const groups = allBuildings.length ? [...allBuildings] : [[null, null]];
  if (groups.length > 1 && physical.some(([id]) => f.ancestor(id, 'IFCBUILDING') == null)) groups.push([null, null]);
  return groups.map(([buildingStep, building]) => {
    const out = emptyBuilding(f.id(building) ?? stableId('building', file));
    const project = [...model.e.values()].find(e => f.isA(e, 'IFCPROJECT'));
    const site = buildingStep == null ? null : model.e.get(f.ancestor(buildingStep, 'IFCSITE'));
    out.project = { name: f.attr(project, 'Name'), description: f.attr(project, 'Description'), building_name: f.attr(building, 'Name'), building_type: f.attr(building, 'ObjectType'), site_name: f.attr(site, 'Name'), ifc_schema: model.schema, ifc_project_global_id: f.attr(project, 'GlobalId'), ifc_building_global_id: f.attr(building, 'GlobalId'), ifc_site_global_id: f.attr(site, 'GlobalId'), source_refs: [sourceRef(f.attr(project, 'GlobalId') ?? file)] };
    out.source_documents = [{ id: sid, file, format: 'ifc', source, ifc_schema: model.schema, units: { to_si: Object.fromEntries([...f.units].map(([kind, u]) => [kind, f.factor(u)])), original: [...f.units.values()] }, properties: { relationships: [...model.e].filter(([, e]) => f.isA(e, 'IFCRELATIONSHIP')).map(([step_id, e]) => ({ step_id, ...e })), applications: [...model.e.values()].filter(e => e.type === 'IFCAPPLICATION') } }];
    out.drawings = [{ id: did, source_document_id: sid, level_ids: [], file, source, read_from: file, route: 'ifc', page_index: null, image: null, transforms: [], translator_version: 'blueprint-ingest@0.3.0', created_at: opts.now ?? new Date().toISOString() }];
    function base(id, e) {
      const guid = f.attr(e, 'GlobalId'), props = f.properties(id), type = props.type;
      const flat = Object.assign({}, ...Object.values(props.psets));
      const entity = { id: f.id(e), ifc_global_id: guid, ifc_class: f.schema[e.type]?.name ?? e.type, name: f.attr(e, 'Name'), description: f.attr(e, 'Description'), predefined_type: f.attr(e, 'PredefinedType'), type_id: f.id(type), type_name: f.attr(type, 'Name'), type_mark: f.attr(type, 'Tag'), level_id: f.id(model.e.get(f.ancestor(id, 'IFCBUILDINGSTOREY'))), confidence: 1, status: 'active', provenance: { extractor: 'vector_ifc', source_ref: guid, file }, source_refs: [sourceRef(guid)], source_drawing_id: did, properties: { psets: props.psets, source_attributes: Object.fromEntries((f.schema[e.type]?.attributes ?? []).map((n, i) => [n, e.args[i]])), type_properties: type ? f.property({ ref: f.types.get(id) }) : null }, quantities: props.quantities, ...f.materialData(id, length), fire_rating: flat.FireRating ?? null, acoustic_rating: flat.AcousticRating ?? null, is_load_bearing: typeof flat.LoadBearing === 'boolean' ? flat.LoadBearing : null, classifications: [] };
      for (const q of entity.quantities) q.source_refs = entity.source_refs;
      for (const layer of entity.material_layers) layer.source_refs = entity.source_refs;
      entity.properties.material_association = f.property(f.materials.get(id) ?? f.materials.get(f.types.get(id)) ?? null);
      for (const rel of model.e.values()) if (f.isA(rel, 'IFCRELASSOCIATESCLASSIFICATION') && (f.attr(rel, 'RelatedObjects') ?? []).some(r => r.ref === id)) {
        const classification = f.get(f.attr(rel, 'RelatingClassification'));
        const system = f.get(f.attr(classification, 'ReferencedSource'));
        entity.classifications.push({ system: f.attr(system, 'Name'), code: f.attr(classification, 'Identification') ?? f.attr(classification, 'ItemReference'), name: f.attr(classification, 'Name'), location: f.attr(classification, 'Location') });
      }
      return { entity, flat };
    }
    for (const [id, e] of physical) {
      if (groups.length > 1 && f.ancestor(id, 'IFCBUILDING') !== buildingStep) continue;
      if (f.isA(e, 'IFCVIRTUALELEMENT')) continue;
      const { entity: item, flat } = base(id, e);
      if (f.isA(e, 'IFCBUILDINGSTOREY')) {
        item.level_id = null; item.elevation_m = f.attr(e, 'Elevation') == null ? null : f.attr(e, 'Elevation') * length;
        item.height_m = null; item.storey_identifier = f.attr(e, 'GlobalId'); out.levels.push(item); continue;
      }
      const bucket = f.isA(e, 'IFCSPACE') ? 'rooms' : f.isA(e, 'IFCWALL') ? 'walls' : f.isA(e, 'IFCDOOR') || f.isA(e, 'IFCWINDOW') || f.isA(e, 'IFCOPENINGELEMENT') ? 'openings' : 'objects';
      const fp = footprint(id, e, length);
      item.polygon_m = fp?.ring ?? null; item.polygon_px = null;
      const placement = isRef(f.attr(e, 'ObjectPlacement')) ? model.placement(f.attr(e, 'ObjectPlacement')) : null;
      item.geometry = fp || placement ? { coordinate_system: 'world', unit: 'm', footprints: fp ? [{ exterior: fp.ring, holes: fp.holes ?? [] }] : [], bbox: fp?.bbox ?? null, placement: placement ? [...[0, 1, 2].map(i => [placement.x[i], placement.y[i], placement.z[i], placement.t[i] * length]), [0, 0, 0, 1]] : null, source_refs: item.source_refs } : null;
      const q = name => item.quantities.find(q => q.name === name)?.value ?? null;
      if (bucket === 'rooms') {
        item.number = f.attr(e, 'Name'); item.name = f.attr(e, 'LongName') ?? item.number;
        item.label = item.name && item.number && item.name !== item.number ? `${item.name} (${item.number})` : item.name;
        item.type = lookupRoomName(item.name)?.type ?? 'unknown'; item.subtype = f.attr(e, 'ObjectType'); item.is_outdoor = item.type === 'unknown' ? null : OUTDOOR_TYPES.has(item.type);
        item.holes_m = fp?.holes ?? [];
        item.area_written_m2 = q('NetFloorArea'); item.area_computed_m2 = fp?.ring ? polygonArea(fp.ring) - item.holes_m.reduce((a, h) => a + polygonArea(h), 0) : null;
        item.area_m2 = item.area_written_m2 ?? item.area_computed_m2; item.height_m = q('Height') ?? fp?.depthM ?? null; item.volume_m3 = q('NetVolume');
        if (item.area_computed_m2 != null) item.quantities.push({ name: 'area_computed_m2', value: item.area_computed_m2, unit: 'm2', origin: fp.confidence < .9 ? 'estimated' : 'calculated', method: `geometry:${fp.method}`, source_refs: item.source_refs });
        item.finishes = [['floor', 'FloorCovering'], ['wall', 'WallCovering'], ['ceiling', 'CeilingCovering']].filter(([, key]) => typeof flat[key] === 'string').map(([surface, key]) => ({ surface, description: flat[key], source_refs: item.source_refs }));
      } else if (bucket === 'walls') {
        Object.assign(item, { length_m: q('Length'), thickness_m: q('Width'), height_m: q('Height') ?? fp?.depthM ?? null, net_side_area_m2: q('NetSideArea'), gross_area_m2: q('GrossSideArea'), net_volume_m3: q('NetVolume'), gross_volume_m3: q('GrossVolume'), is_external: typeof flat.IsExternal === 'boolean' ? flat.IsExternal : null, room_ids: [] });
        if(item.thickness_m==null && item.material_layers.length && item.material_layers.every(l=>l.thickness_m!=null)){
          item.thickness_m=item.material_layers.reduce((n,l)=>n+l.thickness_m,0);
          item.quantities.push({name:'thickness_m',value:item.thickness_m,unit:'m',origin:'calculated',method:'sum of IfcMaterialLayer.LayerThickness',source_refs:item.source_refs});
        }
      } else if (bucket === 'openings') {
        item.kind = f.isA(e, 'IFCDOOR') ? 'door' : f.isA(e, 'IFCWINDOW') ? 'window' : 'void'; item.is_physical = item.kind !== 'void';
        item.width_m = f.attr(e, 'OverallWidth') == null ? null : f.attr(e, 'OverallWidth') * length;
        item.height_m = f.attr(e, 'OverallHeight') == null ? null : f.attr(e, 'OverallHeight') * length;
        item.operation = f.attr(e, 'OperationType') ?? f.attr(f.types.get(id) ? model.e.get(f.types.get(id)) : null, 'OperationType');
        item.wall_id = null; item.room_ids = [];
      } else {
        item.category = Object.keys(CATEGORY).find(cls => f.isA(e, cls)); item.category = CATEGORY[item.category] ?? 'other';
        const sanitary = String(item.predefined_type ?? '').toUpperCase();
        if (f.isA(e, 'IFCSANITARYTERMINAL')) item.category = { WCSEAT: 'toilet', TOILETPAN: 'toilet', SINK: 'sink', WASHHANDBASIN: 'sink', SHOWER: 'shower', BATH: 'bathtub' }[sanitary] ?? 'other';
        item.subtype = item.ifc_class.replace(/^Ifc/, '').toLowerCase(); item.net_volume_m3 = q('NetVolume'); item.length_m = q('Length'); item.height_m = q('Height') ?? fp?.depthM ?? null;
        if (item.category === 'stair' && f.isA(model.e.get(f.parent.get(id)), 'IFCSTAIR')) item.category = 'other';
      }
      out[bucket].push(item);
    }
    const indexed = new Map(['levels', 'rooms', 'walls', 'openings', 'objects'].flatMap(c => out[c].map(e => [e.id, e])));
    for (const [id, e] of model.e) {
      if (f.isA(e, 'IFCTYPEOBJECT') && (groups.length === 1 || [...indexed.values()].some(x => x.type_id === f.id(e)))) {
        const { entity } = base(id, e); entity.level_id = null; entity.type_id = null;
        entity.entity_kind = e.type.startsWith('IFCDOOR') ? 'door' : e.type.startsWith('IFCWINDOW') ? 'window' : e.type.startsWith('IFCWALL') ? 'wall' : 'other';
        entity.instance_ids = [...indexed.values()].filter(x => x.type_id === entity.id).map(x => x.id); out.types.push(entity);
      }
      if (f.isA(e, 'IFCSYSTEM')) {
        const { entity } = base(id, e); entity.level_id = null;
        entity.system_type = SYSTEM[f.attr(e, 'PredefinedType')] ?? 'unknown'; entity.component_ids = [];
        for (const rel of model.e.values()) if (f.isA(rel, 'IFCRELASSIGNSTOGROUP') && f.attr(rel, 'RelatingGroup')?.ref === id) {
          entity.component_ids.push(...(f.attr(rel, 'RelatedObjects') ?? []).map(r => f.id(f.get(r))).filter(key => indexed.has(key)));
        }
        if (entity.component_ids.length || groups.length === 1) {
          entity.level_ids = [...new Set(entity.component_ids.map(i => indexed.get(i).level_id).filter(Boolean))];
          out.systems.push(entity); for (const i of entity.component_ids) (indexed.get(i).system_ids ??= []).push(entity.id);
        }
      }
    }
    const known = new Set([out.building_id, ...indexed.keys(), ...out.types.map(t => t.id), ...out.systems.map(s => s.id)]);
    const unresolved=[];
    for(const rel of model.e.values())if(f.isA(rel,'IFCRELSPACEBOUNDARY')){
      const room=indexed.get(f.id(f.get(f.attr(rel,'RelatingSpace')))),target=f.attr(rel,'RelatedBuildingElement'),entity=f.get(target);
      if(room && !known.has(f.id(entity))){
        const record={field:'boundary_ids',global_id:f.attr(entity,'GlobalId'),ifc_class:entity?.type??null,express_id:target?.ref??null,reason:!entity?'entity_not_in_file':f.isA(entity,'IFCVIRTUALELEMENT')?'not_emitted_class':'outside_document'};
        (room.properties.unresolved_references??=[]).push(record);unresolved.push(record);
      }
    }
    const add = (e, kind, a, b) => { const from = f.id(f.get(a)), to = f.id(f.get(b)); if (known.has(from) && known.has(to)) out.relationships.push({ id: `ifc-rel:${f.attr(e, 'GlobalId')}:${from}:${to}`, kind, from_id: from, to_id: to, source_refs: [sourceRef(f.attr(e, 'GlobalId'))] }); };
    for (const e of model.e.values()) {
      if (f.isA(e, 'IFCRELAGGREGATES') || f.isA(e, 'IFCRELNESTS')) for (const child of f.attr(e, 'RelatedObjects') ?? []) add(e, 'aggregates', f.attr(e, 'RelatingObject'), child);
      if (f.isA(e, 'IFCRELCONTAINEDINSPATIALSTRUCTURE')) for (const child of f.attr(e, 'RelatedElements') ?? []) add(e, 'contains', f.attr(e, 'RelatingStructure'), child);
      for (const [cls, kind, a, b] of [['IFCRELVOIDSELEMENT', 'voids', 'RelatedOpeningElement', 'RelatingBuildingElement'], ['IFCRELFILLSELEMENT', 'fills', 'RelatedBuildingElement', 'RelatingOpeningElement'], ['IFCRELSPACEBOUNDARY', 'bounds', 'RelatingSpace', 'RelatedBuildingElement'], ['IFCRELCONNECTSELEMENTS', 'connects', 'RelatingElement', 'RelatedElement']]) if (f.isA(e, cls)) add(e, kind, f.attr(e, a), f.attr(e, b));
      if (f.isA(e, 'IFCRELCONNECTSPORTS')) {
        const owner = port => {
          if (!port) return null;
          const parent = f.parent.get(port.ref);
          if (parent != null) return { ref: parent };
          const rel = [...model.e.values()].find(r => f.isA(r, 'IFCRELCONNECTSPORTTOELEMENT') && f.attr(r, 'RelatingPort')?.ref === port.ref);
          return f.attr(rel, 'RelatedElement');
        };
        add(e, 'connects', owner(f.attr(e, 'RelatingPort')), owner(f.attr(e, 'RelatedPort')));
      }
    }
    // Resolve host walls before fills; relationship order in STEP is not significant.
    for (const rel of out.relationships) {
      const a = indexed.get(rel.from_id), b = indexed.get(rel.to_id);
      if (['contains', 'aggregates'].includes(rel.kind) && b) b.parent_id = rel.from_id;
      if (rel.kind === 'voids' && a && b && out.walls.includes(b)) { a.wall_id = b.id; a.level_id ??= b.level_id; }
      if (rel.kind === 'bounds' && a && b) { (a.boundary_ids ??= []).push(b.id); (b.room_ids ??= []).push(a.id); }
    }
    for (const rel of out.relationships.filter(r => r.kind === 'fills')) { const a = indexed.get(rel.from_id), b = indexed.get(rel.to_id); if (a && b) a.wall_id = b.wall_id; }
    for (const [step, table] of model.e) if (table.type === 'IFCTABLE') {
      const rows = [...(f.attr(table, 'Rows') ?? [])];
      let headers = (f.attr(table, 'Columns') ?? []).map(r => f.attr(f.get(r), 'Name') ?? f.attr(f.get(r), 'Identifier') ?? '');
      if (!headers.length && rows.length && f.attr(f.get(rows[0]), 'IsHeading') === true) headers = (f.attr(f.get(rows.shift()), 'RowCells') ?? []).map(c => String(unwrap(c)));
      const id = `${sid}:table:${step}`, name = f.attr(table, 'Name'), ref = sourceRef(`#${step}`);
      out.schedules.push({ id, name, schedule_type: ['door', 'window', 'material', 'equipment'].find(k => (name ?? '').toLowerCase().includes(k)) ?? 'other', source_document_id: sid, source_sheet_id: null, headers,
        rows: rows.map((r, n) => ({ id: `${id}:row:${n}`, cells: (f.attr(f.get(r), 'RowCells') ?? []).map(unwrap), entity_ids: [], source_refs: [ref] })), confidence: 1, provenance: { extractor: 'vector_ifc', source_ref: `#${step}` }, source_refs: [ref] });
    }
    out.drawings[0].level_ids = out.levels.map(l => l.id);
    out.coverage = ['rooms', 'walls', 'doors', 'windows', 'fixtures'].map(category => ({ category, drawing_id: did, region: 'page', status: 'analysed', extractor: 'vector_ifc', confidence: 1, note: 'All source instances retained, including those without geometry' }));
    if(unresolved.length)out.coverage.push({category:'relationships',drawing_id:did,region:'page',status:'partial',extractor:'vector_ifc',confidence:1,note:`${unresolved.length} space-boundary reference(s) unresolved: ${[...new Set(unresolved.map(u=>u.ifc_class??'unknown'))].map(c=>`${c} ${unresolved.filter(u=>(u.ifc_class??'unknown')===c).length}`).join(', ')}`});
    out.history = [{ extractor: 'vector_ifc', action: 'extract', note: `${model.schema}; complete building; source units normalized to SI` }];
    return out;
  });
}
