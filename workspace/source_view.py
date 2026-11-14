"""Faithful source previews through existing CAD/PDF libraries, with traceable handles."""
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def dxf_preview(source: Path, destination: Path):
    import ezdxf
    from ezdxf.addons.drawing import Frontend, RenderContext, layout, svg
    from ezdxf.addons.drawing.config import ColorPolicy, Configuration

    document = ezdxf.readfile(source)
    entities = {}
    for entity in document.modelspace():
        entities[entity.dxf.handle] = {
            "handle": entity.dxf.handle, "type": entity.dxftype(), "layer": entity.dxf.layer,
            "attributes": entity.dxf.all_existing_dxf_attribs(),
        }

    class TaggedRenderer(svg.SVGRenderBackend):
        def tag(self, before, properties):
            for element in list(self.entities)[before:]:
                element.set("data-source-handle", properties.handle)
                element.set("data-layer", properties.layer)
                element.set("data-source-type", entities.get(properties.handle, {}).get("type", "BLOCK_CONTENT"))

        def add_strokes(self, path, properties):
            before = len(self.entities)
            super().add_strokes(path, properties)
            self.tag(before, properties)

        def add_filling(self, path, properties):
            before = len(self.entities)
            super().add_filling(path, properties)
            self.tag(before, properties)

    class TaggedBackend(svg.SVGBackend):
        @staticmethod
        def make_backend(page, settings):
            return TaggedRenderer(page, settings)

    context = RenderContext(document)
    backend = TaggedBackend()
    Frontend(context, backend, config=Configuration(color_policy=ColorPolicy.BLACK)).draw_layout(document.modelspace())
    root = backend.get_xml_root_element(layout.Page(0, 0), settings=layout.Settings(output_coordinate_space=10000))
    destination.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    manifest = {"format": "dxf", "units": document.units, "entities": entities,
                "source_to_preview": list(backend.transformation_matrix),
                "limitations": ["External image references require their original assets; unsupported CAD entities are not inferred."]}
    destination.with_suffix(".json").write_text(json.dumps(manifest, default=str), encoding="utf-8")


def make_preview(source: Path, folder: Path):
    if source.suffix.lower() == ".dxf":
        dxf_preview(source, folder / "preview.svg")
    elif source.suffix.lower() == ".pdf":
        import pymupdf
        with pymupdf.open(source) as document:
            if len(document):
                document[0].get_pixmap(matrix=pymupdf.Matrix(0.6, 0.6)).save(folder / "thumbnail.png")


def building_preview(building, destination):
    """Thumbnail from actual stored plan geometry; never an invented illustration."""
    polygons = []
    levels = building.get("levels", [])
    level = levels[0]["id"] if levels else None
    for collection in ("rooms", "walls", "openings", "objects"):
        for entity in building.get(collection, []):
            if level and entity.get("level_id") != level:
                continue
            geometry = entity.get("geometry") or {}
            if geometry.get("coordinate_system") == "world" and geometry.get("footprints"):
                polygons.extend(p["exterior"] for p in geometry["footprints"] if p.get("exterior"))
            elif entity.get("polygon_px"):
                polygons.append(entity["polygon_px"])
    points = [p for polygon in polygons for p in polygon]
    if not points:
        return
    x, y = min(p[0] for p in points), min(p[1] for p in points)
    width, height = max(p[0] for p in points) - x, max(p[1] for p in points) - y
    pad = max(width, height) * .05 or 1
    root = ET.Element("svg", xmlns="http://www.w3.org/2000/svg", viewBox=f"{x-pad} {y-pad} {width+2*pad} {height+2*pad}")
    for polygon in polygons:
        ET.SubElement(root, "polygon", points=" ".join(f"{p[0]},{p[1]}" for p in polygon), fill="none", stroke="#253e50", **{"stroke-width": "1", "vector-effect": "non-scaling-stroke"})
    destination.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
