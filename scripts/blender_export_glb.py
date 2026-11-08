"""Blender background script: textured OBJ -> GLB with the texture embedded.

Run: blender -b --factory-startup --python-exit-code 1 -P scripts/blender_export_glb.py -- input.obj output.glb
Cleanup is limited to UV-preserving operations (loose geometry and degenerate faces).
"""
import sys

import bpy

source, destination = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.ops.wm.read_factory_settings(use_empty=True)
if hasattr(bpy.ops.wm, "obj_import"):  # Blender 3.2+
    bpy.ops.wm.obj_import(filepath=source)
else:
    bpy.ops.import_scene.obj(filepath=source)
meshes = [item for item in bpy.context.scene.objects if item.type == "MESH"]
if not meshes:
    raise SystemExit("No mesh imported")
for item in meshes:
    bpy.ops.object.select_all(action="DESELECT")
    item.select_set(True)
    bpy.context.view_layer.objects.active = item
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.delete_loose()
    bpy.ops.mesh.dissolve_degenerate()
    bpy.ops.object.mode_set(mode="OBJECT")
bpy.ops.export_scene.gltf(filepath=destination, export_format="GLB")
