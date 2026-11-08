"""UV texturing with OpenMVS and textured GLB export (Blender when available, otherwise trimesh).

COLMAP does not texture meshes. OpenMVS reads COLMAP's undistorted dense workspace, projects the
photos onto a mesh in the same world coordinates, and writes an OBJ with UVs and a texture atlas.
Every step checks the installed tool's options first and validates its output.
"""
from pathlib import Path
import os
import shutil
import subprocess

from .config import ROOT, Settings, resolve_tool

INTERFACE_FLAGS = ("--image-folder",)
TEXTURE_FLAGS = ("--mesh-file", "--export-type")


def openmvs_tool(settings: Settings, name: str) -> str | None:
    if settings.openmvs_bin:
        folder = Path(settings.openmvs_bin)
        folder = folder if folder.is_absolute() else ROOT / folder
        for candidate in (folder / f"{name}.exe", folder / name):
            if candidate.is_file():
                return str(candidate.resolve())
        return None
    return shutil.which(name)


def tool_help(executable: str, flags) -> str:
    result = subprocess.run([executable, "--help"], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=20)
    output = result.stdout + result.stderr
    missing = [flag for flag in flags if flag not in output]
    if missing:
        raise RuntimeError(f"{Path(executable).name} lacks {', '.join(missing)}")
    return output


def texturing_tools(settings: Settings):
    """Return (interface, texture_mesh, problem); problem is None when both tools expose the needed options."""
    interface, texture = openmvs_tool(settings, "InterfaceCOLMAP"), openmvs_tool(settings, "TextureMesh")
    if not interface or not texture:
        return interface, texture, "OpenMVS InterfaceCOLMAP/TextureMesh not found; set openmvs_bin in config.local.toml."
    try:
        tool_help(interface, INTERFACE_FLAGS)
        tool_help(texture, TEXTURE_FLAGS)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        return interface, texture, f"OpenMVS check failed: {error}"
    return interface, texture, None


def clean_mesh(mesh):
    """UV-safe cleanup before texturing: drop degenerate/duplicate faces and unused vertices."""
    mesh = mesh.copy()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    return mesh


def textured_obj(colmap: str, dense: Path, mesh_ply: Path, work: Path, settings: Settings, run, log_dir: Path) -> Path:
    """Run OpenMVS on a COLMAP dense workspace; return the textured OBJ path."""
    interface, texture, problem = texturing_tools(settings)
    if problem:
        raise RuntimeError(problem)
    work.mkdir(parents=True, exist_ok=True)
    # Older OpenMVS builds only read COLMAP's text model; writing it beside the binary one is harmless.
    steps = [
        ([colmap, "model_converter", "--input_path", str(dense / "sparse"), "--output_path", str(dense / "sparse"),
          "--output_type", "TXT"], "model_converter.log"),
        ([interface, "-i", str(dense), "-o", str(work / "scene.mvs"), "--image-folder", str(dense / "images"),
          "-w", str(work)], "openmvs_interface.log"),
        ([texture, str(work / "scene.mvs"), "--mesh-file", str(mesh_ply), "-o", str(work / "textured.mvs"),
          "--export-type", "obj", "-w", str(work)], "openmvs_texture.log"),
    ]
    for command, log in steps:
        code = run(command, log_dir / log)
        if code:
            raise RuntimeError(f"{Path(command[0]).name} exited with code {code}; see {log}")
    obj = work / "textured.obj"
    if not obj.is_file():
        raise RuntimeError("TextureMesh produced no textured.obj; see openmvs_texture.log")
    return obj


def validate_textured_glb(path: Path) -> dict:
    import trimesh
    scene = trimesh.load(path)
    geometries = list(scene.geometry.values()) if isinstance(scene, trimesh.Scene) else [scene]
    textured = [g for g in geometries if isinstance(g.visual, trimesh.visual.TextureVisuals)
                and g.visual.uv is not None and len(g.visual.uv)
                and getattr(g.visual.material, "baseColorTexture", None) is not None]
    if not textured:
        raise ValueError("GLB has no UV-mapped base colour texture")
    faces = sum(len(g.faces) for g in textured)
    image = textured[0].visual.material.baseColorTexture
    return dict(faces=int(faces), texture_size=list(image.size))


def export_textured_glb(obj: Path, glb: Path, settings: Settings, run, log_dir: Path) -> dict:
    """Convert a textured OBJ to GLB with embedded texture, validating the result."""
    blender = resolve_tool(settings.blender) if settings.glb_exporter in ("auto", "blender") else None
    warning = ""
    if blender:
        script = ROOT / "scripts" / "blender_export_glb.py"
        code = run([blender, "-b", "--factory-startup", "--python-exit-code", "1", "-P", str(script), "--", str(obj), str(glb)],
                   log_dir / "blender_export.log")
        try:
            if code:
                raise RuntimeError(f"Blender exited with code {code}")
            return dict(exporter="blender", **validate_textured_glb(glb))
        except (RuntimeError, ValueError, OSError) as error:
            if settings.glb_exporter == "blender":
                raise
            warning = f"Blender export failed ({error}); used trimesh instead."
    elif settings.glb_exporter == "blender":
        raise RuntimeError("glb_exporter is blender, but Blender was not found")
    import trimesh
    mesh = trimesh.load(obj, force="mesh")
    if os.path.exists(glb):
        os.remove(glb)
    mesh.export(glb)
    return dict(exporter="trimesh", warning=warning, **validate_textured_glb(glb))
