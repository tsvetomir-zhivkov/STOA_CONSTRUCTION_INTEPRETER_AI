"""Reuse the repository's source fixture builders for browser integration checks."""

import sys
from pathlib import Path

import ifcopenshell
from pytest import MonkeyPatch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))
from test_building import rich_model  # noqa: E402
from test_swiss_dxf import Plan  # noqa: E402

folder = ROOT / ".workspace-data" / "qa" / "fixtures"
folder.mkdir(parents=True, exist_ok=True)
with MonkeyPatch.context() as patch:
    rich_model.__wrapped__(folder, patch)
plan = Plan()
plan.room(0, 0, 4, 4, name="Office", number="101", area=16)
plan.save(folder / "plan.dxf")
# Extend the existing IFC fixture with a source-native, 2D-only door swing.
model = ifcopenshell.open(str(folder / "building.ifc"))
point = model.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0))
axis = model.create_entity("IfcAxis2Placement2D", Location=point)
circle = model.create_entity("IfcCircle", Position=axis, Radius=900.0)
arc = model.create_entity(
    "IfcTrimmedCurve",
    BasisCurve=circle,
    Trim1=[model.create_entity("IfcParameterValue", 0.0)],
    Trim2=[model.create_entity("IfcParameterValue", 1.5707963267948966)],
    SenseAgreement=True,
    MasterRepresentation="PARAMETER",
)
shape = model.create_entity(
    "IfcShapeRepresentation",
    ContextOfItems=model.by_type("IfcGeometricRepresentationContext")[0],
    RepresentationIdentifier="FootPrint",
    RepresentationType="Curve2D",
    Items=[arc],
)
model.by_type("IfcDoor")[0].Representation = model.create_entity(
    "IfcProductDefinitionShape", Representations=[shape]
)
model.write(str(folder / "native-plan.ifc"))
print(folder)
