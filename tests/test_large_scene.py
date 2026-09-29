"""Isolate native command-buffer regressions so an abort cannot kill the test runner."""

import copy
import ctypes
from pathlib import Path
import struct
import subprocess
import sys

import numpy as np
import pytest

import filly
from test_features import pack, unpack


def many_meshes(triangle_glb, count=10000):
    doc, _ = unpack(triangle_glb)
    material = doc["materials"][0]
    doc.update(nodes=[], meshes=[], materials=[], accessors=[], bufferViews=[])
    binary = bytearray()
    # Separate accessors force gltfio to create and upload separate geometry buffers,
    # as NodePerformanceTest does. Repeated references would only test node count.
    streams = (("POSITION", "VEC3", 5126, struct.pack("<9f", -0.8,-0.8,0, 0.8,-0.8,0, 0,0.8,0)),
               ("NORMAL", "VEC3", 5126, struct.pack("<9f", *([0,0,1]*3))),
               ("TEXCOORD_0", "VEC2", 5126, struct.pack("<6f", 0,0, 1,0, 0.5,1)),
               ("indices", "SCALAR", 5123, struct.pack("<3H", 0,1,2)))
    for i in range(count):
        attributes = {}
        for semantic, kind, component, data in streams:
            binary += b"\0" * (-len(binary) % 4)
            index = len(doc["accessors"])
            doc["bufferViews"].append({"buffer":0,"byteOffset":len(binary),"byteLength":len(data)})
            entry = {"bufferView":index,"componentType":component,"count":3,"type":kind}
            if semantic == "POSITION":
                entry.update(min=[-0.8,-0.8,0],max=[0.8,0.8,0])
            doc["accessors"].append(entry)
            binary += data
            attributes[semantic] = index
        indices = attributes.pop("indices")
        doc["meshes"].append({"primitives":[{"attributes":attributes,"indices":indices,"material":i}]})
        doc["materials"].append(copy.deepcopy(material))
        # Keep one triangle visible for a deterministic pixel assertion. The others
        # still exercise allocation, resource upload, and destruction at full scale.
        doc["nodes"].append({"mesh":i,"name":f"node{i}","translation":[0 if i==0 else 10+i,0,0]})
    doc["scenes"][0]["nodes"] = list(range(count))
    return pack(doc, binary)


@pytest.mark.gpu
@pytest.mark.parametrize("shared", [False, pytest.param(True, marks=pytest.mark.interop)])
def test_large_asset_load_render_and_close_in_child(tmp_path, triangle_glb, shared):
    if shared:
        if sys.platform not in ("win32", "linux"):
            pytest.skip("WGL/GLX implementation")
        pytest.importorskip("pyglet")
    asset = tmp_path / "many-meshes.glb"
    asset.write_bytes(many_meshes(triangle_glb))
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(asset), str(int(shared))],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "large scene passed" in result.stdout
    # The engine configuration sizes the handle arena for this workload.
    assert "arena is full" not in result.stdout + result.stderr


def worker(asset, shared):
    window = None
    texture = None
    if shared:
        import pyglet
        from pyglet import gl
        window = pyglet.window.Window(width=32, height=32, visible=False)
        texture = gl.GLuint()
        gl.glGenTextures(1, ctypes.byref(texture))
        gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
        gl.glTexStorage2D(gl.GL_TEXTURE_2D, 1, gl.GL_RGBA8, 32, 32)
    try:
        options = {"shared_context":filly.current_gl_context()} if shared else {}
        with filly.Renderer(**options) as renderer:
            scene = renderer.create_scene()
            camera = scene.create_camera()
            camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
            camera.position = (0,0,3)
            camera.look_at((0,0,0))
            scene.camera = camera
            target = (renderer.import_gl_texture(texture.value, width=32, height=32) if shared
                      else renderer.create_render_target(width=32, height=32))

            def center():
                renderer.render(scene,target)
                if not shared:
                    return target.read()[16,16]
                pixels = np.empty((32,32,4),dtype=np.uint8)
                with target.acquire():
                    gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
                    gl.glGetTexImage(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE,
                                    pixels.ctypes.data_as(ctypes.c_void_p))
                return pixels[16,16]

            for cycle in range(2):
                print(f"load {cycle}", flush=True)
                model = scene.load(asset)
                assert len(model.node_names) == 10000
                np.testing.assert_array_equal(center(), [255,0,0,255])
                model.node("node0").material().base_color = (0,1,0,1)
                np.testing.assert_array_equal(center(), [0,255,0,255])
                model.close()
                assert renderer.stats.live_models == 0
                np.testing.assert_array_equal(center(), [0,0,0,255])
            target.close()
        print("large scene passed", flush=True)
    finally:
        if window:
            window.switch_to()
            gl.glDeleteTextures(1, ctypes.byref(texture))
            window.close()


if __name__ == "__main__":
    worker(Path(sys.argv[1]), bool(int(sys.argv[2])))
