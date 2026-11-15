/**
 * IFC element classes known to the extractor, grouped by discipline. Used by the IFC extractor (which classes to read)
 * and by the v0.3 layer (objects[].discipline). Names are upper case as in STEP files; IFC2X3, IFC4 and IFC4X3 names
 * are mixed on purpose (a class missing from one schema simply never occurs).
 */

const list = (s) => s.trim().split(/\s+/);

export const ARCHITECTURAL = list(`
  IFCWALL IFCWALLSTANDARDCASE IFCWALLELEMENTEDCASE IFCCURTAINWALL IFCDOOR IFCDOORSTANDARDCASE IFCWINDOW IFCWINDOWSTANDARDCASE
  IFCSTAIR IFCSTAIRFLIGHT IFCRAMP IFCRAMPFLIGHT IFCRAILING IFCROOF IFCCOVERING IFCCHIMNEY IFCSHADINGDEVICE
  IFCBUILDINGELEMENTPROXY IFCBUILDINGELEMENTPART IFCELEMENTASSEMBLY IFCTRANSPORTELEMENT IFCGEOGRAPHICELEMENT IFCCIVILELEMENT
  IFCBUILTELEMENT IFCSPACE`);
export const STRUCTURAL = list(`
  IFCSLAB IFCSLABSTANDARDCASE IFCSLABELEMENTEDCASE IFCBEAM IFCBEAMSTANDARDCASE IFCCOLUMN IFCCOLUMNSTANDARDCASE IFCMEMBER
  IFCMEMBERSTANDARDCASE IFCPLATE IFCPLATESTANDARDCASE IFCFOOTING IFCPILE IFCREINFORCINGBAR IFCREINFORCINGMESH IFCREINFORCINGELEMENT
  IFCTENDON IFCTENDONANCHOR IFCTENDONCONDUIT IFCDISCRETEACCESSORY IFCMECHANICALFASTENER IFCFASTENER IFCBEARING IFCCAISSONFOUNDATION
  IFCDEEPFOUNDATION`);
export const MEP = list(`
  IFCFLOWSEGMENT IFCFLOWFITTING IFCFLOWTERMINAL IFCFLOWCONTROLLER IFCFLOWMOVINGDEVICE IFCFLOWSTORAGEDEVICE IFCFLOWTREATMENTDEVICE
  IFCENERGYCONVERSIONDEVICE IFCDISTRIBUTIONELEMENT IFCDISTRIBUTIONFLOWELEMENT IFCDISTRIBUTIONCONTROLELEMENT IFCDISTRIBUTIONCHAMBERELEMENT
  IFCPIPESEGMENT IFCPIPEFITTING IFCDUCTSEGMENT IFCDUCTFITTING IFCDUCTSILENCER IFCCABLESEGMENT IFCCABLEFITTING IFCCABLECARRIERSEGMENT
  IFCCABLECARRIERFITTING IFCSANITARYTERMINAL IFCAIRTERMINAL IFCAIRTERMINALBOX IFCLIGHTFIXTURE IFCLAMP IFCOUTLET IFCSWITCHINGDEVICE
  IFCELECTRICAPPLIANCE IFCELECTRICDISTRIBUTIONBOARD IFCELECTRICFLOWSTORAGEDEVICE IFCELECTRICGENERATOR IFCELECTRICMOTOR
  IFCELECTRICTIMECONTROL IFCPROTECTIVEDEVICE IFCPROTECTIVEDEVICETRIPPINGUNIT IFCTRANSFORMER IFCMOTORCONNECTION IFCSOLARDEVICE
  IFCFIRESUPPRESSIONTERMINAL IFCALARM IFCSENSOR IFCACTUATOR IFCCONTROLLER IFCFLOWINSTRUMENT IFCFLOWMETER IFCUNITARYCONTROLELEMENT
  IFCVALVE IFCDAMPER IFCPUMP IFCFAN IFCCOMPRESSOR IFCTANK IFCFILTER IFCINTERCEPTOR IFCBOILER IFCCHILLER IFCCOIL IFCCONDENSER
  IFCCOOLEDBEAM IFCCOOLINGTOWER IFCEVAPORATIVECOOLER IFCEVAPORATOR IFCHEATEXCHANGER IFCHUMIDIFIER IFCSPACEHEATER IFCTUBEBUNDLE
  IFCUNITARYEQUIPMENT IFCAIRTOAIRHEATRECOVERY IFCBURNER IFCENGINE IFCMEDICALDEVICE IFCAUDIOVISUALAPPLIANCE IFCCOMMUNICATIONSAPPLIANCE
  IFCSTACKTERMINAL IFCWASTETERMINAL IFCJUNCTIONBOX IFCELECTRICALELEMENT IFCEQUIPMENTELEMENT IFCFIREFIGHTING`);
export const FURNISHING = list(`IFCFURNISHINGELEMENT IFCFURNITURE IFCSYSTEMFURNITUREELEMENT`);

/**
 * Products that are not building elements of a plan: they are COUNTED (coverage `ifc_classes`), never silently lost,
 * but not emitted as entities. Openings are represented by their door/window and wall_id; ports by system membership.
 */
export const NOT_EMITTED = Object.freeze({
  IFCOPENINGELEMENT: 'void cut into a host; represented by the filling door/window and its wall_id',
  IFCOPENINGSTANDARDCASE: 'void cut into a host; represented by the filling door/window and its wall_id',
  IFCVOIDINGFEATURE: 'void feature of an element',
  IFCPROJECTIONELEMENT: 'projection feature of an element',
  IFCDISTRIBUTIONPORT: 'connection port of an MEP element (no geometry of its own)',
  IFCVIRTUALELEMENT: 'virtual boundary, not a physical element',
  IFCANNOTATION: 'drawing annotation',
  IFCGRID: 'design grid',
});

export const DISCIPLINE = new Map([
  ...ARCHITECTURAL.map((c) => [c, 'architectural']),
  ...STRUCTURAL.map((c) => [c, 'structural']),
  ...MEP.map((c) => [c, 'mep']),
  ...FURNISHING.map((c) => [c, 'furnishing']),
]);

/** every element class the extractor reads */
export const ELEMENT_CLASSES = [...DISCIPLINE.keys()];

/** object.category (v0.2 fixture categories) -> discipline, for sources without an IFC class */
const CATEGORY_DISCIPLINE = {
  toilet: 'mep', sink: 'mep', shower: 'mep', bathtub: 'mep', radiator: 'mep',
  stair: 'architectural', elevator: 'architectural', sauna_bench: 'furnishing', closet: 'furnishing',
  stove: 'furnishing', fridge: 'furnishing', washing_machine: 'furnishing', other: 'other',
};

/** @param {string|null|undefined} ifcClass @param {string|null|undefined} category */
export function disciplineOf(ifcClass, category) {
  const c = ifcClass ? String(ifcClass).toUpperCase() : null;
  if (c && DISCIPLINE.has(c)) return DISCIPLINE.get(c);
  return CATEGORY_DISCIPLINE[category ?? 'other'] ?? 'other';
}

/** IFCPIPESEGMENT -> pipe_segment, IFCWALLSTANDARDCASE -> wall */
export const classNoun = (cls) => String(cls).toUpperCase().replace(/^IFC/, '').replace(/(STANDARDCASE|ELEMENTEDCASE)$/, '')
  .replace(/(SEGMENT|FITTING|TERMINAL|DEVICE|ELEMENT|FLIGHT|BOARD|APPLIANCE|CARRIER|FIXTURE)/g, '_$1').replace(/^_|_$/g, '').replace(/__+/g, '_').toLowerCase();

/** element type class -> element_types[].kind */
export function typeKind(typeClass) {
  const c = String(typeClass).toUpperCase();
  if (/^IFCDOOR(TYPE|STYLE)$/.test(c)) return 'door';
  if (/^IFCWINDOW(TYPE|STYLE)$/.test(c)) return 'window';
  if (/^IFCWALLTYPE$|^IFCCURTAINWALLTYPE$/.test(c)) return 'wall';
  if (/^IFCSLABTYPE$|^IFCROOFTYPE$/.test(c)) return 'slab';
  if (/^IFCCOLUMNTYPE$/.test(c)) return 'column';
  if (/^IFCBEAMTYPE$|^IFCMEMBERTYPE$/.test(c)) return 'beam';
  if (/^IFCCOVERINGTYPE$/.test(c)) return 'covering';
  if (/^IFC(STAIR|STAIRFLIGHT|RAMP|RAMPFLIGHT)TYPE$/.test(c)) return 'stair';
  if (/^IFCRAILINGTYPE$/.test(c)) return 'railing';
  if (/^IFC(FURNITURE|FURNISHINGELEMENT|SYSTEMFURNITUREELEMENT)TYPE$/.test(c)) return 'furniture';
  if (/^IFCSANITARYTERMINALTYPE$/.test(c)) return 'sanitary';
  const inst = c.replace(/TYPE$/, '');
  if (MEP.includes(inst)) return 'mep';
  return 'other';
}

/** is this STEP entity name an element type (IfcDoorType, IfcDoorStyle...)? */
export const isTypeClass = (t) => (t.endsWith('TYPE') && t !== 'IFCRELDEFINESBYTYPE' && !t.startsWith('IFCREL')) || t === 'IFCDOORSTYLE' || t === 'IFCWINDOWSTYLE';
